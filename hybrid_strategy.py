from __future__ import annotations

"""Fresh capture-the-flag strategy with immediate movement decisions.

This implementation does not reuse the previous route-memory logic. The bot
always produces a movement target from the current observation instead of
parking in place while waiting for a staged plan to settle.
"""

import time
from dataclasses import dataclass

from lib.actions import Chat, MoveTo
from lib.observation import BlockState, GridPosition, Observation, PlayerState, TeamName

PRISON_GATES = {
    "L": GridPosition(x=-16, z=24),
    "R": GridPosition(x=16, z=24),
}
TREE_KEYWORDS = ("leaves", "vine", "pitcher_plant")
HOME_FALLBACK_X = {"L": -18, "R": 18}
ENEMY_ENTRY_X = {"L": 4, "R": -4}
HOME_ENTRY_X = {"L": -4, "R": 4}
ENEMY_RALLY_X = {"L": 10, "R": -10}
ENEMY_SWEEP_X = {"L": 18, "R": -18}
LANE_Z_POINTS = (-18, -6, 6, 18)
SWEEP_Z_POINTS = (-24, -12, 0, 12, 24)
INTERCEPT_RANGE = 14
TARGET_REACHED_RADIUS = 2
TREE_CLEARANCE_RADIUS = 1
TREE_SEARCH_RADIUS = 4


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
    if team == "L":
        return position.x < 0
    return position.x > 0


def _is_enemy_side(position: GridPosition, team: TeamName) -> bool:
    return not _is_home_side(position, team)


def _active_teammates(obs: Observation) -> tuple[PlayerState, ...]:
    return tuple(player for player in obs.myteam_players if not player.in_prison)


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


def _lane_for_bot(obs: Observation, bot_name: str) -> int:
    ordered_names = sorted(player.name for player in obs.myteam_players)
    if bot_name not in ordered_names:
        return 0
    index = ordered_names.index(bot_name)
    return LANE_Z_POINTS[index % len(LANE_Z_POINTS)]


def _crossing_point(obs: Observation, team: TeamName, lane_z: int, *, toward_enemy: bool) -> GridPosition:
    x = ENEMY_ENTRY_X[team] if toward_enemy else HOME_ENTRY_X[team]
    return _clamp_to_map(obs, x, lane_z)


def _home_score_target(obs: Observation, origin: GridPosition) -> GridPosition:
    scoring_block = _closest_block(origin, obs.my_targets)
    if scoring_block is not None:
        return scoring_block.grid_position
    fallback_z = 0 if abs(origin.z) <= 6 else origin.z
    return _clamp_to_map(obs, HOME_FALLBACK_X[obs.my_team], fallback_z)


def _enemy_flag_blocks(obs: Observation) -> tuple[BlockState, ...]:
    return tuple(sorted(obs.flags_to_capture, key=lambda block: (block.grid_position.x, block.grid_position.z)))


def _assigned_flag(obs: Observation, bot_name: str) -> GridPosition | None:
    flags = list(_enemy_flag_blocks(obs))
    if not flags:
        return None

    workers = [player for player in sorted(_active_teammates(obs), key=lambda current: current.name) if not player.has_flag]
    if not workers:
        return None

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


def _enemy_flag_carrier(obs: Observation) -> PlayerState | None:
    for enemy in obs.enemies:
        if enemy.has_flag:
            return enemy
    return None


def _sweep_target(obs: Observation, lane_z: int, sweep_index: int) -> GridPosition:
    target_z = SWEEP_Z_POINTS[sweep_index % len(SWEEP_Z_POINTS)]
    if lane_z < 0 and target_z > 0:
        target_z = -target_z
    if lane_z > 0 and target_z < 0:
        target_z = -target_z
    return _clamp_to_map(obs, ENEMY_SWEEP_X[obs.my_team], target_z)


def _move(target: GridPosition, *, radius: int = 1, avoid_entities: bool = False) -> MoveTo:
    return MoveTo(
        x=target.x,
        z=target.z,
        radius=radius,
        sprint=True,
        jump=False,
        avoid_entities=avoid_entities,
    )


@dataclass
class HybridStrategy:
    chat_cooldown: float = 5.0
    last_chat_at: float = 0.0
    last_intent: tuple[str, int, int] | None = None
    attack_lane_z: int = 0
    committed_flag: GridPosition | None = None
    sweep_index: int = 0

    def on_game_start(self, obs: Observation) -> None:
        self.last_chat_at = 0.0
        self.last_intent = None
        self.attack_lane_z = _lane_for_bot(obs, obs.bot_name)
        self.committed_flag = None
        self.sweep_index = 0

    def compute_next_action(self, obs: Observation) -> list[MoveTo | Chat]:
        self.attack_lane_z = _lane_for_bot(obs, obs.bot_name)

        me = obs.self_player

        if me.in_prison:
            self.committed_flag = None
            return self._issue(obs, me.position, "Break out", PRISON_GATES[obs.my_team], radius=0)

        if me.has_flag:
            self.committed_flag = None
            return self._issue_carrier(obs)

        carrier = _enemy_flag_carrier(obs)
        if carrier is not None and _is_home_side(carrier.position, obs.my_team):
            if _is_home_side(me.position, obs.my_team) or _distance(me.position, carrier.position) <= INTERCEPT_RANGE:
                return self._issue(
                    obs,
                    me.position,
                    "Chase carrier",
                    carrier.position,
                    radius=1,
                    avoid_entities=True,
                )

        return self._issue_offense(obs)

    def _issue_carrier(self, obs: Observation) -> list[MoveTo | Chat]:
        me = obs.self_player
        if _is_enemy_side(me.position, obs.my_team):
            return self._issue(
                obs,
                me.position,
                "Return home",
                _crossing_point(obs, obs.my_team, self.attack_lane_z, toward_enemy=False),
                radius=1,
                avoid_entities=True,
            )
        return self._issue(
            obs,
            me.position,
            "Score flag",
            _home_score_target(obs, me.position),
            radius=0,
            avoid_entities=True,
        )

    def _issue_offense(self, obs: Observation) -> list[MoveTo | Chat]:
        me = obs.self_player

        assigned_flag = self._refresh_committed_flag(obs) or _assigned_flag(obs, obs.bot_name)
        if assigned_flag is not None:
            self.committed_flag = assigned_flag
            if _is_home_side(me.position, obs.my_team):
                entry = _crossing_point(obs, obs.my_team, self.attack_lane_z, toward_enemy=True)
                if _distance(me.position, entry) > TARGET_REACHED_RADIUS:
                    return self._issue(obs, me.position, "Enter enemy side", entry, radius=1)
            return self._issue(obs, me.position, "Capture flag", assigned_flag, radius=0)

        # When the enemy flag is not currently visible, keep advancing instead of
        # stopping in place and waiting for a better calculation.
        if _is_home_side(me.position, obs.my_team):
            rally = _clamp_to_map(obs, ENEMY_RALLY_X[obs.my_team], self.attack_lane_z)
            return self._issue(obs, me.position, "Advance", rally, radius=1)

        sweep_target = _sweep_target(obs, self.attack_lane_z, self.sweep_index)
        if _distance(me.position, sweep_target) <= TARGET_REACHED_RADIUS:
            self.sweep_index += 1
            sweep_target = _sweep_target(obs, self.attack_lane_z, self.sweep_index)
        return self._issue(obs, me.position, "Sweep", sweep_target, radius=1)

    def _refresh_committed_flag(self, obs: Observation) -> GridPosition | None:
        if self.committed_flag is None:
            return None
        visible_flags = {(flag.grid_position.x, flag.grid_position.z) for flag in _enemy_flag_blocks(obs)}
        if (self.committed_flag.x, self.committed_flag.z) in visible_flags:
            return self.committed_flag
        self.committed_flag = None
        return None

    def _issue(
        self,
        obs: Observation,
        origin: GridPosition,
        intent: str,
        target: GridPosition,
        *,
        radius: int,
        avoid_entities: bool = False,
    ) -> list[MoveTo | Chat]:
        actions: list[MoveTo | Chat] = []
        safe_target = _nearest_clear_target(obs, origin, target)
        self._announce(actions, intent, safe_target)
        actions.append(_move(safe_target, radius=radius, avoid_entities=avoid_entities))
        return actions

    def _announce(self, actions: list[MoveTo | Chat], intent: str, target: GridPosition) -> None:
        signature = (intent, target.x, target.z)
        if signature == self.last_intent:
            return
        now = time.monotonic()
        if now - self.last_chat_at >= self.chat_cooldown:
            actions.append(Chat(message=f"[HYB] {intent} -> ({target.x}, {target.z})"))
            self.last_chat_at = now
        self.last_intent = signature


__all__ = ["HybridStrategy"]
