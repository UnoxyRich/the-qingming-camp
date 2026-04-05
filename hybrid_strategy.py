from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from lib.actions import Chat, MoveTo
from lib.observation import BlockState, GridPosition, Observation, PlayerState, TeamName

PRISON_GATES = {
    "L": GridPosition(x=-16, z=24),
    "R": GridPosition(x=16, z=24),
}
HOME_FALLBACK_X = {"L": -18, "R": 18}
ENTRY_X = {"L": 6, "R": -6}
HOME_ENTRY_X = {"L": -6, "R": 6}
PATROL_X = {"L": 14, "R": -14}
PATROL_Z_POINTS = (-24, -12, -2, 8, 18, 28)
HOME_DEFENSE_BUFFER = 2
IDLE_THRESHOLD_TICKS = 10
NEAR_DEFENSE_DISTANCE = 10
ENEMY_THREAT_RANGE = 10
RESCUE_ADVANTAGE = 4
PATROL_REACHED_RADIUS = 3
ENTRY_REACHED_RADIUS = 3
FORWARD_PROGRESS_SLACK = 1
INTERCEPT_LOOKAHEAD_STEPS = 2
MAX_INTERCEPT_DELTA = 2
OFFENSE_DETOUR_FORWARD_STEPS = (0, 2, 4)
OFFENSE_DETOUR_Z_OFFSETS = (0, -6, 6, -10, 10, -14, 14)
RETREAT_DETOUR_FORWARD_STEPS = (0, 2, 4)
RETREAT_DETOUR_Z_OFFSETS = (0, -5, 5, -9, 9)


def _manhattan(left: GridPosition, right: GridPosition) -> int:
    return abs(left.x - right.x) + abs(left.z - right.z)


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def _clamp_to_map(obs: Observation, x: int, z: int) -> GridPosition:
    return GridPosition(
        x=_clamp(x, obs.map.min_x, obs.map.max_x),
        z=_clamp(z, obs.map.min_z, obs.map.max_z),
    )


def _travel_sign(team: TeamName, *, toward_enemy: bool) -> int:
    base = 1 if team == "L" else -1
    return base if toward_enemy else -base


def _progress_value(x: int, sign: int) -> int:
    return sign * x


def _lock_forward_progress(
    obs: Observation,
    origin: GridPosition,
    candidate: GridPosition,
    sign: int,
    *,
    slack: int = FORWARD_PROGRESS_SLACK,
) -> GridPosition:
    minimum_progress = _progress_value(origin.x, sign) - slack
    candidate_progress = _progress_value(candidate.x, sign)
    if candidate_progress >= minimum_progress:
        return candidate
    locked_x = _clamp(origin.x - (sign * slack), obs.map.min_x, obs.map.max_x)
    return GridPosition(x=locked_x, z=candidate.z)


def _active_enemies(obs: Observation) -> tuple[PlayerState, ...]:
    return tuple(enemy for enemy in obs.enemies if not enemy.in_prison)


def _is_true_home_territory(position: GridPosition, team: TeamName) -> bool:
    if team == "L":
        return position.x < 0
    return position.x > 0


def _is_enemy_territory(position: GridPosition, team: TeamName) -> bool:
    return not _is_true_home_territory(position, team)


def _is_home_defense_zone(position: GridPosition, team: TeamName) -> bool:
    if team == "L":
        return position.x <= HOME_DEFENSE_BUFFER
    return position.x >= -HOME_DEFENSE_BUFFER


def _closest_block(origin: GridPosition, blocks: tuple[BlockState, ...]) -> BlockState | None:
    if not blocks:
        return None
    return min(
        blocks,
        key=lambda block: (
            _manhattan(origin, block.grid_position),
            block.grid_position.x,
            block.grid_position.z,
        ),
    )


def _unplaced_flags(obs: Observation) -> tuple[BlockState, ...]:
    occupied = {(position.x, position.z) for position in obs.gold_block_positions}
    return tuple(
        flag
        for flag in obs.flags_to_capture
        if (flag.grid_position.x, flag.grid_position.z) not in occupied
    )


def _enemy_pressure(position: GridPosition, enemies: tuple[PlayerState, ...]) -> float:
    pressure = 0.0
    for enemy in enemies:
        distance = _manhattan(position, enemy.position)
        if distance <= ENEMY_THREAT_RANGE:
            pressure += (ENEMY_THREAT_RANGE - distance) * 2.5
    return pressure


def _is_on_line_segment(
    start: GridPosition,
    end: GridPosition,
    point: GridPosition,
    *,
    tolerance: float = 3.0,
) -> bool:
    segment_dx = end.x - start.x
    segment_dz = end.z - start.z
    segment_length_squared = (segment_dx * segment_dx) + (segment_dz * segment_dz)
    if segment_length_squared == 0:
        return _manhattan(start, point) <= tolerance

    projection = (
        ((point.x - start.x) * segment_dx) + ((point.z - start.z) * segment_dz)
    ) / segment_length_squared
    clamped_projection = max(0.0, min(1.0, projection))
    closest_x = start.x + (segment_dx * clamped_projection)
    closest_z = start.z + (segment_dz * clamped_projection)
    return math.hypot(point.x - closest_x, point.z - closest_z) <= tolerance


def _path_blocked_by_enemy(
    origin: GridPosition,
    target: GridPosition,
    enemies: tuple[PlayerState, ...],
    *,
    tolerance: float = 3.0,
) -> bool:
    return any(
        _is_on_line_segment(origin, target, enemy.position, tolerance=tolerance)
        for enemy in enemies
    )


def _line_detour_target(
    obs: Observation,
    origin: GridPosition,
    target: GridPosition,
    enemies: tuple[PlayerState, ...],
    *,
    toward_enemy: bool,
) -> GridPosition:
    if not _path_blocked_by_enemy(origin, target, enemies):
        return target

    offset = 5 if origin.z < target.z else -5
    sign = _travel_sign(obs.my_team, toward_enemy=toward_enemy)
    alternative = GridPosition(
        x=int(round((origin.x + target.x) / 2)),
        z=target.z + offset,
    )
    return _lock_forward_progress(
        obs,
        origin,
        _clamp_to_map(obs, alternative.x, alternative.z),
        sign,
    )


def _build_crossing_waypoint(
    obs: Observation,
    origin: GridPosition,
    target: GridPosition,
    enemies: tuple[PlayerState, ...],
    *,
    toward_enemy: bool,
) -> GridPosition:
    if toward_enemy:
        if _is_enemy_territory(origin, obs.my_team) or not _is_enemy_territory(target, obs.my_team):
            return target
        waypoint_x = ENTRY_X[obs.my_team]
    else:
        if _is_true_home_territory(origin, obs.my_team):
            return target
        waypoint_x = HOME_ENTRY_X[obs.my_team]

    sign = _travel_sign(obs.my_team, toward_enemy=toward_enemy)
    candidates = tuple(
        _lock_forward_progress(
            obs,
            origin,
            _clamp_to_map(obs, waypoint_x, target.z + offset),
            sign,
        )
        for offset in (-10, -6, -2, 2, 6, 10)
    )
    return min(
        candidates,
        key=lambda candidate: (
            _enemy_pressure(candidate, enemies),
            _manhattan(origin, candidate) + _manhattan(candidate, target),
            abs(candidate.z - target.z),
        ),
    )


def _detour_target(
    obs: Observation,
    origin: GridPosition,
    target: GridPosition,
    enemies: tuple[PlayerState, ...],
    *,
    toward_enemy: bool,
    forward_steps: tuple[int, ...],
    z_offsets: tuple[int, ...],
) -> GridPosition:
    direct_detour = _line_detour_target(
        obs,
        origin,
        target,
        enemies,
        toward_enemy=toward_enemy,
    )
    if direct_detour != target:
        target = direct_detour

    nearby = tuple(enemy for enemy in enemies if _manhattan(target, enemy.position) <= 6)
    if not nearby:
        return target

    sign = _travel_sign(obs.my_team, toward_enemy=toward_enemy)
    baseline = _lock_forward_progress(obs, origin, target, sign)
    baseline_pressure = _enemy_pressure(baseline, enemies)
    best = baseline
    best_score = (
        baseline_pressure,
        _manhattan(origin, baseline),
        _manhattan(baseline, target),
        abs(baseline.z - target.z),
        -_progress_value(baseline.x, sign),
    )

    for step in forward_steps:
        for z_offset in z_offsets:
            candidate = _clamp_to_map(obs, target.x + (sign * step), target.z + z_offset)
            candidate = _lock_forward_progress(obs, origin, candidate, sign)
            score = (
                _enemy_pressure(candidate, enemies),
                _manhattan(origin, candidate),
                _manhattan(candidate, target),
                abs(candidate.z - target.z),
                -_progress_value(candidate.x, sign),
            )
            if score < best_score:
                best = candidate
                best_score = score

    if _manhattan(origin, best) > _manhattan(origin, baseline) + 8 and best_score[0] >= baseline_pressure:
        return baseline
    return best


def _intruders(obs: Observation, enemies: tuple[PlayerState, ...]) -> tuple[PlayerState, ...]:
    return tuple(enemy for enemy in enemies if _is_home_defense_zone(enemy.position, obs.my_team))


def _needs_rescue(obs: Observation) -> bool:
    return any(player.in_prison for player in obs.teammates)


def _is_support_bot(obs: Observation) -> bool:
    ordered_players = sorted(player.name for player in obs.myteam_players if not player.in_prison)
    if len(ordered_players) <= 1:
        return True
    return obs.bot_name == ordered_players[-1]


def _assign_flag(obs: Observation, enemies: tuple[PlayerState, ...]) -> GridPosition | None:
    available = list(_unplaced_flags(obs))
    if not available:
        return None

    assignments: dict[str, GridPosition] = {}
    for player in sorted(obs.myteam_players, key=lambda current: current.name):
        if player.in_prison or player.has_flag or not available:
            continue
        best = min(
            available,
            key=lambda flag: (
                _manhattan(player.position, flag.grid_position)
                + _enemy_pressure(flag.grid_position, enemies),
                flag.grid_position.x,
                flag.grid_position.z,
            ),
        )
        assignments[player.name] = best.grid_position
        available.remove(best)
    return assignments.get(obs.bot_name)


def _should_rescue(
    obs: Observation,
    origin: GridPosition,
    assigned_flag: GridPosition | None,
    support: bool,
) -> bool:
    if not _needs_rescue(obs):
        return False
    if support:
        return True
    if assigned_flag is None:
        return True
    gate = PRISON_GATES[obs.my_team]
    rescue_distance = _manhattan(origin, gate)
    offense_distance = _manhattan(origin, assigned_flag)
    return rescue_distance + RESCUE_ADVANTAGE < offense_distance


def _patrol_target(obs: Observation, patrol_index: int) -> GridPosition:
    return _clamp_to_map(
        obs,
        PATROL_X[obs.my_team],
        PATROL_Z_POINTS[patrol_index % len(PATROL_Z_POINTS)],
    )


def _go(
    actions: list[MoveTo | Chat],
    destination: GridPosition,
    *,
    radius: int,
    avoid_entities: bool = False,
) -> None:
    actions.append(
        MoveTo(
            x=destination.x,
            z=destination.z,
            radius=radius,
            sprint=True,
            jump=True,
            avoid_entities=avoid_entities,
        )
    )


@dataclass
class RouteState:
    mode: str | None = None
    key: str | None = None
    stage: str | None = None
    anchor: GridPosition | None = None
    waypoint: GridPosition | None = None

    def clear(self) -> None:
        self.mode = None
        self.key = None
        self.stage = None
        self.anchor = None
        self.waypoint = None


@dataclass
class HybridStrategy:
    chat_cooldown: float = 6.0
    last_intent: tuple[str, int, int] | None = None
    last_chat_at: float = 0.0
    patrol_index: int = 0
    last_position: GridPosition | None = None
    idle_ticks: int = 0
    patrol_target: GridPosition | None = None
    route: RouteState = field(default_factory=RouteState)
    enemy_history: dict[str, GridPosition] = field(default_factory=dict)

    def on_game_start(self, obs: Observation) -> None:
        self.last_intent = None
        self.last_chat_at = 0.0
        self.patrol_index = 0
        self.last_position = obs.self_player.position
        self.idle_ticks = 0
        self.patrol_target = None
        self.route.clear()
        self.enemy_history.clear()

    def compute_next_action(self, obs: Observation) -> list[MoveTo | Chat]:
        actions: list[MoveTo | Chat] = []
        me = obs.self_player
        my_pos = me.position
        enemies = _active_enemies(obs)
        support = _is_support_bot(obs)

        forced_target = self._check_idle(obs)
        if forced_target is not None and not me.in_prison:
            return self._issue(
                actions,
                enemies,
                "Unstuck",
                forced_target,
                radius=1,
                mode="reset",
                key="reset",
                stage="nudge",
                anchor=forced_target,
            )

        if me.in_prison:
            gate = PRISON_GATES[obs.my_team]
            return self._issue(
                actions,
                enemies,
                "Jailbreak",
                gate,
                radius=0,
                mode="prison",
                key="gate",
                stage="exit",
                anchor=gate,
            )

        if me.has_flag:
            home_block = _closest_block(my_pos, obs.my_targets)
            if home_block is not None:
                home_target = home_block.grid_position
            else:
                home_target = _clamp_to_map(obs, HOME_FALLBACK_X[obs.my_team], 0)
            route_target, stage = self._carrier_target(obs, my_pos, home_target, enemies)
            return self._issue(
                actions,
                enemies,
                "Plant flag",
                route_target,
                radius=0,
                avoid_entities=True,
                mode="carrier",
                key="home",
                stage=stage,
                anchor=home_target,
            )

        invaders = _intruders(obs, enemies)
        if invaders:
            target_enemy = min(
                invaders,
                key=lambda enemy: (
                    0 if enemy.has_flag else 1,
                    _manhattan(my_pos, enemy.position),
                    enemy.position.z,
                ),
            )
            enemy_distance = _manhattan(my_pos, target_enemy.position)
            if support or target_enemy.has_flag or enemy_distance <= NEAR_DEFENSE_DISTANCE:
                intercept = self._intercept_target(obs, my_pos, target_enemy)
                intent = "Tag carrier" if target_enemy.has_flag else "Tag intruder"
                return self._issue(
                    actions,
                    enemies,
                    intent,
                    intercept,
                    radius=0,
                    mode="defense",
                    key=target_enemy.name,
                    stage="intercept",
                    anchor=target_enemy.position,
                )

        committed_flag = self._committed_flag(obs)
        assigned_flag = committed_flag or _assign_flag(obs, enemies)

        if committed_flag is None and _should_rescue(obs, my_pos, assigned_flag, support):
            gate = PRISON_GATES[obs.my_team]
            return self._issue(
                actions,
                enemies,
                "Rescue",
                gate,
                radius=0,
                mode="rescue",
                key="gate",
                stage="rescue",
                anchor=gate,
            )

        if assigned_flag is not None:
            route_target, stage = self._offense_target(obs, my_pos, assigned_flag, enemies)
            return self._issue(
                actions,
                enemies,
                "Collect flag",
                route_target,
                radius=0,
                mode="offense",
                key=f"flag:{assigned_flag.x}:{assigned_flag.z}",
                stage=stage,
                anchor=assigned_flag,
            )

        self.route.clear()
        patrol_target = self._next_patrol_target(obs, my_pos)
        return self._issue(
            actions,
            enemies,
            "Patrol",
            patrol_target,
            radius=2,
            mode="patrol",
            key=f"patrol:{patrol_target.x}:{patrol_target.z}",
            stage="patrol",
            anchor=patrol_target,
        )

    def _committed_flag(self, obs: Observation) -> GridPosition | None:
        if self.route.mode != "offense" or self.route.anchor is None:
            return None
        available = {(flag.grid_position.x, flag.grid_position.z) for flag in _unplaced_flags(obs)}
        anchor = self.route.anchor
        if (anchor.x, anchor.z) in available:
            return anchor
        self.route.clear()
        return None

    def _check_idle(self, obs: Observation) -> GridPosition | None:
        current = obs.self_player.position
        if self.last_position is not None and _manhattan(current, self.last_position) <= 1:
            self.idle_ticks += 1
        else:
            self.idle_ticks = 0
        self.last_position = current
        if self.idle_ticks < IDLE_THRESHOLD_TICKS:
            return None
        self.idle_ticks = 0
        self.patrol_target = _patrol_target(obs, self.patrol_index)
        self.patrol_index += 1
        self.route.clear()
        return self.patrol_target

    def _next_patrol_target(self, obs: Observation, origin: GridPosition) -> GridPosition:
        if self.patrol_target is None or _manhattan(origin, self.patrol_target) <= PATROL_REACHED_RADIUS:
            self.patrol_target = _patrol_target(obs, self.patrol_index)
            self.patrol_index += 1
        return self.patrol_target

    def _offense_target(
        self,
        obs: Observation,
        origin: GridPosition,
        flag_target: GridPosition,
        enemies: tuple[PlayerState, ...],
    ) -> tuple[GridPosition, str]:
        key = f"flag:{flag_target.x}:{flag_target.z}"
        same_route = self.route.mode == "offense" and self.route.key == key
        stage = self.route.stage if same_route else None
        waypoint = self.route.waypoint if same_route else None

        if stage is None:
            waypoint = _build_crossing_waypoint(
                obs,
                origin,
                flag_target,
                enemies,
                toward_enemy=True,
            )
            stage = "entry" if waypoint != flag_target else "approach"

        if stage == "entry":
            waypoint = waypoint or _build_crossing_waypoint(
                obs,
                origin,
                flag_target,
                enemies,
                toward_enemy=True,
            )
            sign = _travel_sign(obs.my_team, toward_enemy=True)
            if (
                _is_enemy_territory(origin, obs.my_team)
                or _manhattan(origin, waypoint) <= ENTRY_REACHED_RADIUS
                or _progress_value(origin.x, sign) >= _progress_value(waypoint.x, sign)
            ):
                stage = "approach"
            else:
                return (
                    _detour_target(
                        obs,
                        origin,
                        waypoint,
                        enemies,
                        toward_enemy=True,
                        forward_steps=(0, 2),
                        z_offsets=(0, -4, 4, -8, 8),
                    ),
                    "entry",
                )

        return (
            _detour_target(
                obs,
                origin,
                flag_target,
                enemies,
                toward_enemy=True,
                forward_steps=OFFENSE_DETOUR_FORWARD_STEPS,
                z_offsets=OFFENSE_DETOUR_Z_OFFSETS,
            ),
            "approach",
        )

    def _carrier_target(
        self,
        obs: Observation,
        origin: GridPosition,
        home_target: GridPosition,
        enemies: tuple[PlayerState, ...],
    ) -> tuple[GridPosition, str]:
        stage = self.route.stage if self.route.mode == "carrier" else None
        waypoint = self.route.waypoint if self.route.mode == "carrier" else None

        if stage is None:
            waypoint = _build_crossing_waypoint(
                obs,
                origin,
                home_target,
                enemies,
                toward_enemy=False,
            )
            stage = "exit" if waypoint != home_target else "score"

        if stage == "exit":
            waypoint = waypoint or _build_crossing_waypoint(
                obs,
                origin,
                home_target,
                enemies,
                toward_enemy=False,
            )
            if _is_true_home_territory(origin, obs.my_team) or _manhattan(origin, waypoint) <= ENTRY_REACHED_RADIUS:
                stage = "score"
            else:
                return (
                    _detour_target(
                        obs,
                        origin,
                        waypoint,
                        enemies,
                        toward_enemy=False,
                        forward_steps=RETREAT_DETOUR_FORWARD_STEPS,
                        z_offsets=RETREAT_DETOUR_Z_OFFSETS,
                    ),
                    "exit",
                )

        return (
            _detour_target(
                obs,
                origin,
                home_target,
                enemies,
                toward_enemy=False,
                forward_steps=RETREAT_DETOUR_FORWARD_STEPS,
                z_offsets=RETREAT_DETOUR_Z_OFFSETS,
            ),
            "score",
        )

    def _intercept_target(
        self,
        obs: Observation,
        origin: GridPosition,
        enemy: PlayerState,
    ) -> GridPosition:
        previous = self.enemy_history.get(enemy.name)
        if previous is None:
            return enemy.position
        delta_x = _clamp(enemy.position.x - previous.x, -MAX_INTERCEPT_DELTA, MAX_INTERCEPT_DELTA)
        delta_z = _clamp(enemy.position.z - previous.z, -MAX_INTERCEPT_DELTA, MAX_INTERCEPT_DELTA)
        predicted = _clamp_to_map(
            obs,
            enemy.position.x + (delta_x * INTERCEPT_LOOKAHEAD_STEPS),
            enemy.position.z + (delta_z * INTERCEPT_LOOKAHEAD_STEPS),
        )
        if _manhattan(origin, predicted) > _manhattan(origin, enemy.position) + 4:
            return enemy.position
        return predicted

    def _issue(
        self,
        actions: list[MoveTo | Chat],
        enemies: tuple[PlayerState, ...],
        intent: str,
        target: GridPosition,
        *,
        radius: int,
        avoid_entities: bool = False,
        mode: str,
        key: str,
        stage: str,
        anchor: GridPosition,
    ) -> list[MoveTo | Chat]:
        self.route.mode = mode
        self.route.key = key
        self.route.stage = stage
        self.route.anchor = anchor
        self.route.waypoint = target
        self._travel(actions, intent, target, radius=radius, avoid_entities=avoid_entities)
        self.enemy_history = {enemy.name: enemy.position for enemy in enemies}
        return actions

    def _travel(
        self,
        actions: list[MoveTo | Chat],
        intent: str,
        target: GridPosition,
        *,
        radius: int,
        avoid_entities: bool = False,
    ) -> None:
        self._announce(actions, intent, target)
        _go(actions, target, radius=radius, avoid_entities=avoid_entities)

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
