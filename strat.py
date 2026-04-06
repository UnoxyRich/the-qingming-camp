from __future__ import annotations

"""Fast dash-only flag runner.

This strategy avoids the slow world pathfinder, but it still plans around
observed blockers so it does not keep dashing straight into trees and fences.
"""

import time
from dataclasses import dataclass
from heapq import heappop, heappush

from lib.actions import Chat, DashTo
from lib.observation import BlockState, GridPosition, Observation, TeamName

PRISON_GATES = {
    "L": GridPosition(x=-16, z=24),
    "R": GridPosition(x=16, z=24),
}
NON_BLOCKING_FLOOR_BLOCKS = {
    "blue_banner",
    "red_banner",
    "redstone_wire",
    "stone_pressure_plate",
}
HOME_ENTRY_X = {"L": -5, "R": 5}
ENEMY_ENTRY_X = {"L": 5, "R": -5}
HOME_SETTLE_X = {"L": -14, "R": 14}
INTERACT_RADIUS = 1
LANE_Z_POINTS = (-18, -6, 6, 18)
OBSTACLE_CLEARANCE_RADIUS = 1
OBSTACLE_SEARCH_RADIUS = 5
WAYPOINT_LOOKAHEAD = 8


def _distance(left: GridPosition, right: GridPosition) -> int:
    return abs(left.x - right.x) + abs(left.z - right.z)


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def _clamp_to_map(obs: Observation, x: int, z: int) -> GridPosition:
    return GridPosition(
        x=_clamp(x, obs.map.min_x, obs.map.max_x),
        z=_clamp(z, obs.map.min_z, obs.map.max_z),
    )


def _is_home_side(position: GridPosition, team: TeamName) -> bool:
    return position.x < 0 if team == "L" else position.x > 0


def _lane_for_bot(obs: Observation, bot_name: str) -> int:
    ordered_names = sorted(player.name for player in obs.myteam_players)
    if bot_name not in ordered_names:
        return 0
    return LANE_Z_POINTS[ordered_names.index(bot_name) % len(LANE_Z_POINTS)]


def _closest_block(origin: GridPosition, blocks: tuple[BlockState, ...]) -> BlockState | None:
    if not blocks:
        return None
    return min(
        blocks,
        key=lambda block: (
            _distance(origin, block.grid_position),
            block.grid_position.x,
            block.grid_position.z,
        ),
    )


def _goal_cells(obs: Observation) -> set[tuple[int, int]]:
    cells = {
        (block.grid_position.x, block.grid_position.z)
        for block in obs.flags_to_capture + obs.flags_to_protect + obs.my_targets
    }
    prison_gate = PRISON_GATES[obs.my_team]
    cells.add((prison_gate.x, prison_gate.z))
    return cells


def _is_walk_blocker(block: BlockState, *, plane_y: int) -> bool:
    block_y = int(block.position.y)
    if block_y not in {plane_y, plane_y + 1}:
        return False
    return block.bounding_box == "block" and block.name not in NON_BLOCKING_FLOOR_BLOCKS


def _obstacle_cells(obs: Observation) -> set[tuple[int, int]]:
    goal_cells = _goal_cells(obs)
    blocked: set[tuple[int, int]] = set()
    for block in obs.blocks:
        if not _is_walk_blocker(block, plane_y=obs.map.plane_y):
            continue
        cell = (block.grid_position.x, block.grid_position.z)
        if cell in goal_cells:
            continue
        blocked.add(cell)
    return blocked


def _has_clearance(obstacles: set[tuple[int, int]], position: GridPosition, radius: int) -> bool:
    for dx in range(-radius, radius + 1):
        for dz in range(-radius, radius + 1):
            if (position.x + dx, position.z + dz) in obstacles:
                return False
    return True


def _nearest_clear_target(obs: Observation, origin: GridPosition, desired: GridPosition) -> GridPosition:
    obstacles = _obstacle_cells(obs)
    if not obstacles or _has_clearance(obstacles, desired, OBSTACLE_CLEARANCE_RADIUS):
        return desired

    best = desired
    best_score: tuple[int, int, int, int] | None = None
    for radius in range(1, OBSTACLE_SEARCH_RADIUS + 1):
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                if abs(dx) != radius and abs(dz) != radius:
                    continue
                candidate = _clamp_to_map(obs, desired.x + dx, desired.z + dz)
                if not _has_clearance(obstacles, candidate, OBSTACLE_CLEARANCE_RADIUS):
                    continue
                score = (
                    _distance(desired, candidate),
                    _distance(origin, candidate),
                    abs(candidate.z - desired.z),
                    abs(candidate.x - desired.x),
                )
                if best_score is None or score < best_score:
                    best = candidate
                    best_score = score
        if best_score is not None:
            return best
    return best


def _line_is_clear(
    start: GridPosition,
    goal: GridPosition,
    obstacles: set[tuple[int, int]],
    *,
    clearance: int,
) -> bool:
    dx = goal.x - start.x
    dz = goal.z - start.z
    steps = max(abs(dx), abs(dz), 1) * 2
    for step in range(steps + 1):
        ratio = step / steps
        sample = GridPosition(
            x=round(start.x + (dx * ratio)),
            z=round(start.z + (dz * ratio)),
        )
        if not _has_clearance(obstacles, sample, clearance):
            return False
    return True


def _grid_neighbors(obs: Observation, current: GridPosition) -> tuple[GridPosition, ...]:
    neighbors: list[GridPosition] = []
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        next_x = current.x + dx
        next_z = current.z + dz
        if obs.map.min_x <= next_x <= obs.map.max_x and obs.map.min_z <= next_z <= obs.map.max_z:
            neighbors.append(GridPosition(x=next_x, z=next_z))
    return tuple(neighbors)


def _find_path(
    obs: Observation,
    start: GridPosition,
    goal: GridPosition,
    obstacles: set[tuple[int, int]],
) -> list[GridPosition] | None:
    frontier: list[tuple[int, int, GridPosition]] = []
    heappush(frontier, (0, 0, start))
    came_from: dict[tuple[int, int], tuple[int, int] | None] = {(start.x, start.z): None}
    cost_so_far: dict[tuple[int, int], int] = {(start.x, start.z): 0}
    sequence = 1

    while frontier:
        _, _, current = heappop(frontier)
        if current == goal:
            break

        for neighbor in _grid_neighbors(obs, current):
            cell = (neighbor.x, neighbor.z)
            if cell != (goal.x, goal.z) and not _has_clearance(obstacles, neighbor, 0):
                continue
            new_cost = cost_so_far[(current.x, current.z)] + 1
            if cell in cost_so_far and new_cost >= cost_so_far[cell]:
                continue
            cost_so_far[cell] = new_cost
            priority = new_cost + _distance(neighbor, goal)
            heappush(frontier, (priority, sequence, neighbor))
            sequence += 1
            came_from[cell] = (current.x, current.z)

    if (goal.x, goal.z) not in came_from:
        return None

    path: list[GridPosition] = []
    current_cell: tuple[int, int] | None = (goal.x, goal.z)
    while current_cell is not None:
        path.append(GridPosition(x=current_cell[0], z=current_cell[1]))
        current_cell = came_from[current_cell]
    path.reverse()
    return path


def _planned_target(obs: Observation, origin: GridPosition, desired: GridPosition) -> GridPosition:
    obstacles = _obstacle_cells(obs)
    safe_goal = _nearest_clear_target(obs, origin, desired)
    if not obstacles or _line_is_clear(origin, safe_goal, obstacles, clearance=OBSTACLE_CLEARANCE_RADIUS):
        return safe_goal

    path = _find_path(obs, origin, safe_goal, obstacles)
    if not path:
        return safe_goal

    waypoint = path[min(len(path) - 1, 1)]
    furthest_index = min(len(path) - 1, WAYPOINT_LOOKAHEAD)
    for index in range(1, furthest_index + 1):
        candidate = path[index]
        if not _line_is_clear(origin, candidate, obstacles, clearance=OBSTACLE_CLEARANCE_RADIUS):
            break
        waypoint = candidate
    return waypoint


def _home_target(obs: Observation, origin: GridPosition) -> GridPosition:
    open_target = _closest_block(origin, obs.my_targets)
    if open_target is not None:
        return open_target.grid_position
    return _clamp_to_map(obs, HOME_SETTLE_X[obs.my_team], origin.z)


def _enemy_flag(obs: Observation, origin: GridPosition) -> GridPosition | None:
    flag = _closest_block(origin, obs.flags_to_capture)
    if flag is None:
        return None
    return flag.grid_position


def _dash(target: GridPosition, *, radius: int = 1) -> DashTo:
    return DashTo(x=target.x, z=target.z, radius=radius, sprint=True, jump=True)


def _score_dash(target: GridPosition) -> DashTo:
    return DashTo(x=target.x, z=target.z, radius=0, sprint=True, jump=True)


@dataclass
class FlagDashStrategy:
    chat_cooldown: float = 5.0
    last_chat_at: float = 0.0
    last_intent: tuple[str, int, int] | None = None
    lane_z: int = 0
    current_observation: Observation | None = None

    def on_game_start(self, obs: Observation) -> None:
        self.last_chat_at = 0.0
        self.last_intent = None
        self.lane_z = _lane_for_bot(obs, obs.bot_name)
        self.current_observation = obs

    def compute_next_action(self, obs: Observation) -> list[DashTo | Chat]:
        self.current_observation = obs
        self.lane_z = _lane_for_bot(obs, obs.bot_name)
        me = obs.self_player

        if me.in_prison:
            return self._issue("Break out", me.position, PRISON_GATES[obs.my_team], radius=INTERACT_RADIUS)

        if me.has_flag:
            return self._return_home(obs)

        return self._grab_flag(obs)

    def _return_home(self, obs: Observation) -> list[DashTo | Chat]:
        me = obs.self_player
        if not _is_home_side(me.position, obs.my_team):
            entry = _clamp_to_map(obs, HOME_ENTRY_X[obs.my_team], self.lane_z)
            return self._issue("Return home", me.position, entry, radius=1)

        target = _home_target(obs, me.position)
        actions: list[DashTo | Chat] = []
        self._announce(actions, "Score flag", target)
        actions.append(_score_dash(target))
        return actions

    def _grab_flag(self, obs: Observation) -> list[DashTo | Chat]:
        me = obs.self_player
        flag = _enemy_flag(obs, me.position)

        if flag is None:
            fallback = _clamp_to_map(obs, ENEMY_ENTRY_X[obs.my_team], self.lane_z)
            return self._issue("Push lane", me.position, fallback, radius=1)

        if _is_home_side(me.position, obs.my_team):
            entry = _clamp_to_map(obs, ENEMY_ENTRY_X[obs.my_team], flag.z)
            if _distance(me.position, entry) > 2:
                return self._issue("Enter enemy side", me.position, entry, radius=1)

        return self._issue("Take flag", me.position, flag, radius=INTERACT_RADIUS)

    def _issue(self, intent: str, origin: GridPosition, target: GridPosition, *, radius: int) -> list[DashTo | Chat]:
        routed = target
        if self.current_observation is not None:
            routed = _planned_target(self.current_observation, origin, target)
        actions: list[DashTo | Chat] = []
        self._announce(actions, intent, routed)
        actions.append(_dash(routed, radius=radius))
        return actions

    def _announce(self, actions: list[DashTo | Chat], intent: str, target: GridPosition) -> None:
        signature = (intent, target.x, target.z)
        if signature == self.last_intent:
            return
        now = time.monotonic()
        if now - self.last_chat_at >= self.chat_cooldown:
            actions.append(Chat(message=f"[DASH] {intent} -> ({target.x}, {target.z})"))
            self.last_chat_at = now
        self.last_intent = signature


class Strat(FlagDashStrategy):
    pass


__all__ = ["FlagDashStrategy", "Strat"]
