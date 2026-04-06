from __future__ import annotations

"""Conservative capture-the-flag strategy.

This strategy is tuned to beat ordinary opponents by avoiding unnecessary
cross-map risks. It prefers stable defense, safe flag returns, and only pushes
deep when home side pressure is under control.
"""

import time
from dataclasses import dataclass

from lib.actions import Chat, DashTo, MoveTo
from lib.observation import BlockState, GridPosition, Observation, PlayerState, TeamName

PRISON_GATES = {
    "L": GridPosition(x=-16, z=24),
    "R": GridPosition(x=16, z=24),
}
TREE_KEYWORDS = ("leaves", "vine", "pitcher_plant")
HOME_FALLBACK_X = {"L": -18, "R": 18}
HOME_HOLD_X = {"L": -12, "R": 12}
HOME_ENTRY_X = {"L": -4, "R": 4}
MID_HOLD_X = {"L": -7, "R": 7}
ENEMY_STAGE_X = {"L": 8, "R": -8}
ENEMY_FLAG_X = {"L": 18, "R": -18}
LANE_Z_POINTS = (-18, -6, 6, 18)
HOME_PATROL_Z = (-20, -10, 0, 10, 20)
TREE_CLEARANCE_RADIUS = 1
TREE_SEARCH_RADIUS = 4
THREAT_RANGE = 12
SAFE_PUSH_PRESSURE = 6
RETREAT_PRESSURE = 12
INTERACT_RADIUS = 1


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


def _is_enemy_side(position: GridPosition, team: TeamName) -> bool:
    return not _is_home_side(position, team)


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


def _closest_player(origin: GridPosition, players: tuple[PlayerState, ...]) -> PlayerState | None:
    if not players:
        return None
    return min(
        players,
        key=lambda player: (
            _distance(origin, player.position),
            player.position.x,
            player.position.z,
        ),
    )


def _role_for_bot(obs: Observation, bot_name: str) -> str:
    ordered_names = sorted(player.name for player in obs.myteam_players)
    if bot_name not in ordered_names:
        return "runner"
    return "anchor" if ordered_names.index(bot_name) % 2 else "runner"


def _lane_for_bot(obs: Observation, bot_name: str) -> int:
    ordered_names = sorted(player.name for player in obs.myteam_players)
    if bot_name not in ordered_names:
        return 0
    return LANE_Z_POINTS[ordered_names.index(bot_name) % len(LANE_Z_POINTS)]


def _enemy_pressure(position: GridPosition, enemies: tuple[PlayerState, ...]) -> int:
    total = 0
    for enemy in enemies:
        if enemy.in_prison:
            continue
        distance = _distance(position, enemy.position)
        if distance <= THREAT_RANGE:
            total += THREAT_RANGE - distance
    return total


def _is_tree_block(block: BlockState) -> bool:
    return any(keyword in block.name for keyword in TREE_KEYWORDS)


def _tree_cells(obs: Observation) -> set[tuple[int, int]]:
    return {
        (block.grid_position.x, block.grid_position.z)
        for block in obs.blocks
        if _is_tree_block(block)
    }


def _has_tree_clearance(tree_cells: set[tuple[int, int]], position: GridPosition, radius: int) -> bool:
    for dx in range(-radius, radius + 1):
        for dz in range(-radius, radius + 1):
            if (position.x + dx, position.z + dz) in tree_cells:
                return False
    return True


def _nearest_clear_target(obs: Observation, origin: GridPosition, desired: GridPosition) -> GridPosition:
    tree_cells = _tree_cells(obs)
    if not tree_cells or _has_tree_clearance(tree_cells, desired, TREE_CLEARANCE_RADIUS):
        return desired

    best = desired
    best_score: tuple[int, int, int, int] | None = None
    for radius in range(1, TREE_SEARCH_RADIUS + 1):
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                if abs(dx) != radius and abs(dz) != radius:
                    continue
                candidate = _clamp_to_map(obs, desired.x + dx, desired.z + dz)
                if not _has_tree_clearance(tree_cells, candidate, TREE_CLEARANCE_RADIUS):
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


def _home_score_target(obs: Observation, origin: GridPosition) -> GridPosition:
    scoring_block = _closest_block(origin, obs.my_targets)
    if scoring_block is not None:
        return scoring_block.grid_position
    fallback_z = 0 if abs(origin.z) <= 6 else origin.z
    return _clamp_to_map(obs, HOME_FALLBACK_X[obs.my_team], fallback_z)


def _enemy_flag_carrier(obs: Observation) -> PlayerState | None:
    return next((enemy for enemy in obs.enemies if enemy.has_flag), None)


def _friendly_flag_carrier(obs: Observation) -> PlayerState | None:
    return next((player for player in obs.myteam_players if player.has_flag), None)


def _assigned_flag(obs: Observation, bot_name: str) -> GridPosition | None:
    flags = list(sorted(obs.flags_to_capture, key=lambda block: (block.grid_position.x, block.grid_position.z)))
    if not flags:
        return None
    workers = [player for player in sorted(obs.myteam_players, key=lambda current: current.name) if not player.in_prison and not player.has_flag]
    assignments: dict[str, GridPosition] = {}
    for worker in workers:
        if not flags:
            break
        chosen = min(
            flags,
            key=lambda flag: (
                _distance(worker.position, flag.grid_position),
                flag.grid_position.x,
                flag.grid_position.z,
            ),
        )
        assignments[worker.name] = chosen.grid_position
        flags.remove(chosen)
    return assignments.get(bot_name)


def _move(target: GridPosition, *, radius: int = 1, avoid_entities: bool = False) -> MoveTo:
    return MoveTo(
        x=target.x,
        z=target.z,
        radius=radius,
        sprint=True,
        jump=False,
        avoid_entities=avoid_entities,
    )


def _score_move(target: GridPosition) -> DashTo:
    return DashTo(x=target.x, z=target.z, radius=0, sprint=True, jump=True)


@dataclass
class SafeStrategy:
    chat_cooldown: float = 5.0
    last_chat_at: float = 0.0
    last_intent: tuple[str, int, int] | None = None
    lane_z: int = 0
    role: str = "runner"

    def on_game_start(self, obs: Observation) -> None:
        self.last_chat_at = 0.0
        self.last_intent = None
        self.lane_z = _lane_for_bot(obs, obs.bot_name)
        self.role = _role_for_bot(obs, obs.bot_name)

    def compute_next_action(self, obs: Observation) -> list[MoveTo | DashTo | Chat]:
        self.lane_z = _lane_for_bot(obs, obs.bot_name)
        self.role = _role_for_bot(obs, obs.bot_name)

        me = obs.self_player
        active_enemies = tuple(enemy for enemy in obs.enemies if not enemy.in_prison)
        home_intruders = tuple(enemy for enemy in active_enemies if _is_home_side(enemy.position, obs.my_team))
        friendly_carrier = _friendly_flag_carrier(obs)
        enemy_carrier = _enemy_flag_carrier(obs)

        if me.in_prison:
            return self._issue(obs, me.position, "Break out", PRISON_GATES[obs.my_team], radius=INTERACT_RADIUS)

        if me.has_flag:
            return self._carry_flag(obs, me.position, active_enemies)

        if enemy_carrier is not None and _is_home_side(enemy_carrier.position, obs.my_team):
            return self._issue(obs, me.position, "Stop carrier", enemy_carrier.position, radius=1, avoid_entities=True)

        if friendly_carrier is not None and friendly_carrier.name != me.name:
            return self._escort_carrier(obs, me.position, friendly_carrier, active_enemies)

        if self.role == "anchor":
            return self._anchor_plan(obs, me.position, home_intruders, active_enemies)

        return self._runner_plan(obs, me.position, home_intruders, active_enemies)

    def _carry_flag(
        self,
        obs: Observation,
        origin: GridPosition,
        active_enemies: tuple[PlayerState, ...],
    ) -> list[MoveTo | DashTo | Chat]:
        if _is_enemy_side(origin, obs.my_team):
            crossing = _clamp_to_map(obs, HOME_ENTRY_X[obs.my_team], self.lane_z)
            return self._issue(obs, origin, "Retreat with flag", crossing, radius=1, avoid_entities=True)

        home_target = _home_score_target(obs, origin)
        if _enemy_pressure(home_target, active_enemies) > RETREAT_PRESSURE:
            safer_hold = _clamp_to_map(obs, HOME_HOLD_X[obs.my_team], origin.z)
            return self._issue(obs, origin, "Stabilize home", safer_hold, radius=1, avoid_entities=True)
        return self._issue_score(obs, "Score flag", home_target)

    def _escort_carrier(
        self,
        obs: Observation,
        origin: GridPosition,
        carrier: PlayerState,
        active_enemies: tuple[PlayerState, ...],
    ) -> list[MoveTo | DashTo | Chat]:
        if _is_enemy_side(carrier.position, obs.my_team):
            escort_target = _clamp_to_map(obs, HOME_ENTRY_X[obs.my_team], carrier.position.z)
            return self._issue(obs, origin, "Escort return", escort_target, radius=1, avoid_entities=True)

        nearest_enemy = _closest_player(carrier.position, active_enemies)
        if nearest_enemy is not None and _distance(carrier.position, nearest_enemy.position) <= 10:
            return self._issue(obs, origin, "Screen carrier", nearest_enemy.position, radius=1, avoid_entities=True)

        cover_target = _home_score_target(obs, carrier.position)
        return self._issue(obs, origin, "Cover score", cover_target, radius=1, avoid_entities=True)

    def _anchor_plan(
        self,
        obs: Observation,
        origin: GridPosition,
        home_intruders: tuple[PlayerState, ...],
        active_enemies: tuple[PlayerState, ...],
    ) -> list[MoveTo | DashTo | Chat]:
        intruder = _closest_player(origin, home_intruders)
        if intruder is not None:
            return self._issue(obs, origin, "Guard home", intruder.position, radius=1, avoid_entities=True)

        if any(_is_enemy_side(enemy.position, obs.my_team) for enemy in active_enemies):
            hold = _clamp_to_map(obs, HOME_HOLD_X[obs.my_team], self.lane_z)
            return self._issue(obs, origin, "Hold line", hold, radius=1, avoid_entities=True)

        patrol_z = min(HOME_PATROL_Z, key=lambda value: abs(value - self.lane_z))
        patrol = _clamp_to_map(obs, MID_HOLD_X[obs.my_team], patrol_z)
        return self._issue(obs, origin, "Mid cover", patrol, radius=1, avoid_entities=True)

    def _runner_plan(
        self,
        obs: Observation,
        origin: GridPosition,
        home_intruders: tuple[PlayerState, ...],
        active_enemies: tuple[PlayerState, ...],
    ) -> list[MoveTo | DashTo | Chat]:
        if home_intruders:
            intruder = _closest_player(origin, home_intruders)
            if intruder is not None:
                return self._issue(obs, origin, "Recover home", intruder.position, radius=1, avoid_entities=True)

        assigned_flag = _assigned_flag(obs, obs.bot_name)
        if assigned_flag is None:
            stage = _clamp_to_map(obs, MID_HOLD_X[obs.my_team], self.lane_z)
            return self._issue(obs, origin, "Stage midfield", stage, radius=1, avoid_entities=True)

        stage_target = _clamp_to_map(obs, ENEMY_STAGE_X[obs.my_team], assigned_flag.z)
        flag_pressure = _enemy_pressure(assigned_flag, active_enemies)
        stage_pressure = _enemy_pressure(stage_target, active_enemies)

        if flag_pressure <= SAFE_PUSH_PRESSURE and stage_pressure <= RETREAT_PRESSURE:
            return self._issue(obs, origin, "Safe capture", assigned_flag, radius=INTERACT_RADIUS, avoid_entities=True)

        if _is_enemy_side(origin, obs.my_team) and _enemy_pressure(origin, active_enemies) >= RETREAT_PRESSURE:
            fallback = _clamp_to_map(obs, MID_HOLD_X[obs.my_team], self.lane_z)
            return self._issue(obs, origin, "Fall back", fallback, radius=1, avoid_entities=True)

        if _is_home_side(origin, obs.my_team):
            return self._issue(obs, origin, "Probe lane", stage_target, radius=1, avoid_entities=True)

        safer_enemy_hold = _clamp_to_map(obs, ENEMY_STAGE_X[obs.my_team], self.lane_z)
        return self._issue(obs, origin, "Wait for opening", safer_enemy_hold, radius=1, avoid_entities=True)

    def _issue(
        self,
        obs: Observation,
        origin: GridPosition,
        intent: str,
        target: GridPosition,
        *,
        radius: int,
        avoid_entities: bool = False,
    ) -> list[MoveTo | DashTo | Chat]:
        actions: list[MoveTo | DashTo | Chat] = []
        safe_target = _nearest_clear_target(obs, origin, target)
        self._announce(actions, intent, safe_target)
        actions.append(_move(safe_target, radius=radius, avoid_entities=avoid_entities))
        return actions

    def _issue_score(
        self,
        obs: Observation,
        intent: str,
        target: GridPosition,
    ) -> list[DashTo | Chat]:
        actions: list[DashTo | Chat] = []
        self._announce(actions, intent, target)
        actions.append(_score_move(target))
        return actions

    def _announce(self, actions: list[MoveTo | DashTo | Chat], intent: str, target: GridPosition) -> None:
        signature = (intent, target.x, target.z)
        if signature == self.last_intent:
            return
        now = time.monotonic()
        if now - self.last_chat_at >= self.chat_cooldown:
            actions.append(Chat(message=f"[SAFE] {intent} -> ({target.x}, {target.z})"))
            self.last_chat_at = now
        self.last_intent = signature


__all__ = ["SafeStrategy"]