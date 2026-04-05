from __future__ import annotations

import time
from dataclasses import dataclass

from lib.actions import Chat, MoveTo
from lib.observation import BlockState, GridPosition, Observation, PlayerState, TeamName

PRISON_GATES = {
    "L": GridPosition(x=-16, z=24),
    "R": GridPosition(x=16, z=24),
}
HOME_FALLBACK_X = {
    "L": -18,
    "R": 18,
}
ENTRY_X = {
    "L": 6,
    "R": -6,
}
PATROL_Z_POINTS = (-18, -10, -2, 6, 14)
IDLE_THRESHOLD_TICKS = 8
RESCUE_DISTANCE = 28
NEAR_DEFENSE_DISTANCE = 8
HOME_BUFFER = 2


def _manhattan(left: GridPosition, right: GridPosition) -> int:
    return abs(left.x - right.x) + abs(left.z - right.z)


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def _clamp_to_map(obs: Observation, x: int, z: int) -> GridPosition:
    return GridPosition(
        x=_clamp(x, obs.map.min_x, obs.map.max_x),
        z=_clamp(z, obs.map.min_z, obs.map.max_z),
    )


def _active_enemies(obs: Observation) -> tuple[PlayerState, ...]:
    return tuple(enemy for enemy in obs.enemies if not enemy.in_prison)


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


def _is_our_territory(position: GridPosition, team: TeamName) -> bool:
    if team == "L":
        return position.x <= HOME_BUFFER
    return position.x >= -HOME_BUFFER


def _is_enemy_territory(position: GridPosition, team: TeamName) -> bool:
    return not _is_our_territory(position, team)


def _enemy_pressure(position: GridPosition, enemies: tuple[PlayerState, ...]) -> float:
    pressure = 0.0
    for enemy in enemies:
        distance = _manhattan(position, enemy.position)
        if distance <= 10:
            pressure += (10 - distance) * 2.5
    return pressure


def _entry_waypoint(obs: Observation, origin: GridPosition, target: GridPosition, enemies: tuple[PlayerState, ...]) -> GridPosition:
    if _is_enemy_territory(origin, obs.my_team) or not _is_enemy_territory(target, obs.my_team):
        return target
    candidates = tuple(
        _clamp_to_map(obs, ENTRY_X[obs.my_team], target.z + offset)
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


def _avoid_enemy_cluster(obs: Observation, origin: GridPosition, target: GridPosition, enemies: tuple[PlayerState, ...]) -> GridPosition:
    nearby = tuple(enemy for enemy in enemies if _manhattan(target, enemy.position) <= 6)
    if not nearby:
        return target
    avg_x = round(sum(enemy.position.x for enemy in nearby) / len(nearby))
    avg_z = round(sum(enemy.position.z for enemy in nearby) / len(nearby))
    push_x = 3 if target.x >= avg_x else -3
    push_z = 3 if target.z >= avg_z else -3
    shifted = _clamp_to_map(obs, target.x + push_x, target.z + push_z)
    if _manhattan(origin, shifted) > _manhattan(origin, target) + 8:
        return target
    return shifted


def _pick_defense_target(obs: Observation, origin: GridPosition, enemies: tuple[PlayerState, ...]) -> tuple[str, GridPosition] | None:
    defenders = tuple(enemy for enemy in enemies if _is_our_territory(enemy.position, obs.my_team))
    if not defenders:
        return None
    chosen = min(
        defenders,
        key=lambda enemy: (
            0 if enemy.has_flag else 1,
            _manhattan(origin, enemy.position),
        ),
    )
    intent = "Tag carrier" if chosen.has_flag else "Tag intruder"
    return intent, chosen.position


def _needs_rescue(obs: Observation) -> bool:
    return any(player.in_prison for player in obs.teammates)


def _is_support_bot(obs: Observation) -> bool:
    ordered = sorted(player.name for player in obs.myteam_players)
    if len(ordered) < 2:
        return True
    return obs.bot_name == ordered[-1]


def _assign_flag(obs: Observation, enemies: tuple[PlayerState, ...]) -> GridPosition | None:
    available = list(_unplaced_flags(obs))
    if not available:
        return None

    assignments: dict[str, GridPosition] = {}
    for player in sorted(obs.myteam_players, key=lambda item: item.name):
        if player.in_prison or not available:
            continue
        best_flag = min(
            available,
            key=lambda flag: (
                _manhattan(player.position, flag.grid_position) + _enemy_pressure(flag.grid_position, enemies),
                flag.grid_position.x,
                flag.grid_position.z,
            ),
        )
        assignments[player.name] = best_flag.grid_position
        available.remove(best_flag)
    return assignments.get(obs.bot_name)


def _patrol_target(obs: Observation, patrol_index: int) -> GridPosition:
    x = 12 if obs.my_team == "L" else -12
    z = PATROL_Z_POINTS[patrol_index % len(PATROL_Z_POINTS)]
    return _clamp_to_map(obs, x, z)


@dataclass
class NormalStrategy:
    chat_cooldown: float = 6.0
    last_intent: tuple[str, int, int] | None = None
    last_chat_at: float = 0.0
    patrol_index: int = 0
    last_position: GridPosition | None = None
    idle_ticks: int = 0

    def on_game_start(self, obs: Observation) -> None:
        self.last_intent = None
        self.last_chat_at = 0.0
        self.patrol_index = 0
        self.last_position = obs.me.position
        self.idle_ticks = 0

    def compute_next_action(self, obs: Observation) -> list[MoveTo | Chat]:
        me = obs.self_player
        my_pos = me.position
        enemies = _active_enemies(obs)

        forced_target = self._forced_target(obs)
        if forced_target is not None and not me.in_prison:
            return self._travel("Reset route", forced_target, radius=1)

        if me.in_prison:
            return self._travel("Prison exit", PRISON_GATES[obs.my_team], radius=0)

        if me.has_flag:
            home_block = _closest_block(my_pos, obs.my_targets)
            if home_block is not None:
                return self._travel("Plant flag", home_block.grid_position, radius=0)
            return self._travel(
                "Flag home",
                _clamp_to_map(obs, HOME_FALLBACK_X[obs.my_team], my_pos.z),
                radius=1,
            )

        defense = _pick_defense_target(obs, my_pos, enemies)
        if defense is not None:
            intent, target = defense
            if _is_support_bot(obs) or _manhattan(my_pos, target) <= NEAR_DEFENSE_DISTANCE:
                return self._travel(intent, target, radius=1)

        if _is_support_bot(obs) and _needs_rescue(obs):
            gate = PRISON_GATES[obs.my_team]
            if _manhattan(my_pos, gate) <= RESCUE_DISTANCE:
                return self._travel("Rescue teammate", gate, radius=0)

        assigned_flag = _assign_flag(obs, enemies)
        if assigned_flag is not None:
            target = _entry_waypoint(obs, my_pos, assigned_flag, enemies)
            target = _avoid_enemy_cluster(obs, my_pos, target, enemies)
            return self._travel("Collect flag", target, radius=0)

        patrol = _patrol_target(obs, self.patrol_index)
        self.patrol_index += 1
        return self._travel("Patrol", patrol, radius=2)

    def _forced_target(self, obs: Observation) -> GridPosition | None:
        current = obs.me.position
        if self.last_position is not None and _manhattan(current, self.last_position) <= 1:
            self.idle_ticks += 1
        else:
            self.idle_ticks = 0
        self.last_position = current
        if self.idle_ticks < IDLE_THRESHOLD_TICKS:
            return None
        self.idle_ticks = 0
        target = _patrol_target(obs, self.patrol_index)
        self.patrol_index += 1
        return target

    def _travel(self, intent: str, target: GridPosition, *, radius: int) -> list[MoveTo | Chat]:
        actions: list[MoveTo | Chat] = []
        self._announce(actions, intent, target)
        actions.append(MoveTo(x=target.x, z=target.z, radius=radius, sprint=True, jump=False))
        return actions

    def _announce(self, actions: list[MoveTo | Chat], intent: str, target: GridPosition) -> None:
        signature = (intent, target.x, target.z)
        if signature == self.last_intent:
            return
        now = time.monotonic()
        if now - self.last_chat_at >= self.chat_cooldown:
            actions.append(Chat(message=f"[NORMAL] {intent} -> ({target.x}, {target.z})"))
            self.last_chat_at = now
        self.last_intent = signature


__all__ = ["NormalStrategy"]