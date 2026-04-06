from __future__ import annotations

"""Utility-scored capture-the-flag strategy.

Unlike the hybrid strategy, this bot does not follow a fixed stage machine.
Each tick it scores a set of candidate objectives and moves toward the highest
utility option for its current role.
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
MID_GUARD_X = {"L": -8, "R": 8}
HOME_GUARD_X = {"L": -14, "R": 14}
SWEEP_X = {"L": 18, "R": -18}
LANE_Z_POINTS = (-18, -6, 6, 18)
HOME_Z_POINTS = (-20, -8, 0, 8, 20)
SWEEP_Z_POINTS = (-24, -12, 0, 12, 24)
TREE_CLEARANCE_RADIUS = 1
TREE_SEARCH_RADIUS = 4
THREAT_RANGE = 12


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


def _role_for_bot(obs: Observation, bot_name: str) -> str:
    ordered_names = sorted(player.name for player in obs.myteam_players)
    if bot_name not in ordered_names:
        return "runner"
    return "runner" if ordered_names.index(bot_name) == 0 else "anchor"


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


def _enemy_pressure(position: GridPosition, enemies: tuple[PlayerState, ...]) -> int:
    pressure = 0
    for enemy in enemies:
        if enemy.in_prison:
            continue
        distance = _distance(position, enemy.position)
        if distance <= THREAT_RANGE:
            pressure += THREAT_RANGE - distance
    return pressure


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


def _enemy_flag_carrier(obs: Observation) -> PlayerState | None:
    return next((enemy for enemy in obs.enemies if enemy.has_flag), None)


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


def _crossing_target(obs: Observation, lane_z: int, *, toward_enemy: bool) -> GridPosition:
    x = ENEMY_ENTRY_X[obs.my_team] if toward_enemy else HOME_ENTRY_X[obs.my_team]
    return _clamp_to_map(obs, x, lane_z)


def _home_score_target(obs: Observation, origin: GridPosition) -> GridPosition:
    scoring_block = _closest_block(origin, obs.my_targets)
    if scoring_block is not None:
        return scoring_block.grid_position
    fallback_z = 0 if abs(origin.z) <= 6 else origin.z
    return _clamp_to_map(obs, HOME_FALLBACK_X[obs.my_team], fallback_z)


def _candidate_score(
    origin: GridPosition,
    candidate: GridPosition,
    *,
    base: int,
    enemies: tuple[PlayerState, ...],
    role: str,
) -> int:
    distance_penalty = _distance(origin, candidate) * 3
    pressure_penalty = _enemy_pressure(candidate, enemies) * (5 if role == "runner" else 3)
    return base - distance_penalty - pressure_penalty


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
class PressureStrategy:
    chat_cooldown: float = 5.0
    last_chat_at: float = 0.0
    last_intent: tuple[str, int, int] | None = None
    role: str = "runner"
    lane_z: int = 0
    sweep_index: int = 0
    committed_flag: GridPosition | None = None

    def on_game_start(self, obs: Observation) -> None:
        self.last_chat_at = 0.0
        self.last_intent = None
        self.role = _role_for_bot(obs, obs.bot_name)
        self.lane_z = _lane_for_bot(obs, obs.bot_name)
        self.sweep_index = 0
        self.committed_flag = None

    def compute_next_action(self, obs: Observation) -> list[MoveTo | Chat]:
        self.role = _role_for_bot(obs, obs.bot_name)
        self.lane_z = _lane_for_bot(obs, obs.bot_name)

        me = obs.self_player
        enemies = tuple(enemy for enemy in obs.enemies if not enemy.in_prison)

        if me.in_prison:
            self.committed_flag = None
            return self._issue(obs, me.position, "Break out", PRISON_GATES[obs.my_team], radius=0)

        if me.has_flag:
            self.committed_flag = None
            return self._carry_plan(obs, me.position, enemies)

        enemy_carrier = _enemy_flag_carrier(obs)
        if enemy_carrier is not None and _is_home_side(enemy_carrier.position, obs.my_team):
            return self._intercept_plan(obs, me.position, enemy_carrier, enemies)

        if self.role == "anchor" and self._should_guard(obs, enemies):
            return self._guard_plan(obs, me.position, enemies)

        return self._offense_plan(obs, me.position, enemies)

    def _should_guard(self, obs: Observation, enemies: tuple[PlayerState, ...]) -> bool:
        if any(enemy.has_flag for enemy in enemies):
            return True
        home_intruders = [enemy for enemy in enemies if _is_home_side(enemy.position, obs.my_team)]
        return len(home_intruders) > 0

    def _carry_plan(
        self,
        obs: Observation,
        origin: GridPosition,
        enemies: tuple[PlayerState, ...],
    ) -> list[MoveTo | Chat]:
        candidates: list[tuple[str, GridPosition, int, bool, int]] = []
        home_target = _home_score_target(obs, origin)
        candidates.append(("Score flag", home_target, 0, True, 150))
        candidates.append(("Return home", _crossing_target(obs, self.lane_z, toward_enemy=False), 1, True, 120))
        for z_value in HOME_Z_POINTS:
            candidates.append(("Retreat lane", _clamp_to_map(obs, MID_GUARD_X[obs.my_team], z_value), 1, True, 80))
        return self._best_plan(obs, origin, candidates, enemies)

    def _intercept_plan(
        self,
        obs: Observation,
        origin: GridPosition,
        carrier: PlayerState,
        enemies: tuple[PlayerState, ...],
    ) -> list[MoveTo | Chat]:
        candidates: list[tuple[str, GridPosition, int, bool, int]] = []
        candidates.append(("Chase carrier", carrier.position, 1, True, 140))
        candidates.append(("Cut off carrier", _clamp_to_map(obs, HOME_ENTRY_X[obs.my_team], carrier.position.z), 1, True, 120))
        candidates.append(("Defend home", _clamp_to_map(obs, HOME_GUARD_X[obs.my_team], carrier.position.z), 1, True, 100))
        return self._best_plan(obs, origin, candidates, enemies)

    def _guard_plan(
        self,
        obs: Observation,
        origin: GridPosition,
        enemies: tuple[PlayerState, ...],
    ) -> list[MoveTo | Chat]:
        candidates: list[tuple[str, GridPosition, int, bool, int]] = []
        for enemy in enemies:
            if _is_home_side(enemy.position, obs.my_team):
                candidates.append(("Guard intruder", enemy.position, 1, True, 120))
        for z_value in HOME_Z_POINTS:
            candidates.append(("Hold home", _clamp_to_map(obs, MID_GUARD_X[obs.my_team], z_value), 1, True, 70))
        home_target = _home_score_target(obs, origin)
        candidates.append(("Cover score", home_target, 1, True, 90))
        return self._best_plan(obs, origin, candidates, enemies)

    def _offense_plan(
        self,
        obs: Observation,
        origin: GridPosition,
        enemies: tuple[PlayerState, ...],
    ) -> list[MoveTo | Chat]:
        candidates: list[tuple[str, GridPosition, int, bool, int]] = []

        assigned_flag = self._refresh_committed_flag(obs) or _assigned_flag(obs, obs.bot_name)
        if assigned_flag is not None:
            self.committed_flag = assigned_flag
            candidates.append(("Capture flag", assigned_flag, 0, True, 150 if self.role == "runner" else 120))

        candidates.append(("Cross midfield", _crossing_target(obs, self.lane_z, toward_enemy=True), 1, False, 90))
        candidates.append(("Press lane", _clamp_to_map(obs, SWEEP_X[obs.my_team], self.lane_z), 1, False, 80))

        for z_value in SWEEP_Z_POINTS:
            candidates.append(("Sweep", _clamp_to_map(obs, SWEEP_X[obs.my_team], z_value), 1, False, 60))

        return self._best_plan(obs, origin, candidates, enemies)

    def _refresh_committed_flag(self, obs: Observation) -> GridPosition | None:
        if self.committed_flag is None:
            return None
        visible_flags = {(flag.grid_position.x, flag.grid_position.z) for flag in obs.flags_to_capture}
        if (self.committed_flag.x, self.committed_flag.z) in visible_flags:
            return self.committed_flag
        self.committed_flag = None
        return None

    def _best_plan(
        self,
        obs: Observation,
        origin: GridPosition,
        candidates: list[tuple[str, GridPosition, int, bool, int]],
        enemies: tuple[PlayerState, ...],
    ) -> list[MoveTo | Chat]:
        ranked = []
        for intent, raw_target, radius, avoid_entities, base in candidates:
            safe_target = _nearest_clear_target(obs, origin, raw_target)
            score = _candidate_score(origin, safe_target, base=base, enemies=enemies, role=self.role)
            ranked.append((score, intent, safe_target, radius, avoid_entities))
        ranked.sort(key=lambda item: item[0], reverse=True)
        _, intent, target, radius, avoid_entities = ranked[0]
        return self._issue(obs, origin, intent, target, radius=radius, avoid_entities=avoid_entities)

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
            actions.append(Chat(message=f"[PRS] {intent} -> ({target.x}, {target.z})"))
            self.last_chat_at = now
        self.last_intent = signature


__all__ = ["PressureStrategy"]