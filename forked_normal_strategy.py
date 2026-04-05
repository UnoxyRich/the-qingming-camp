from __future__ import annotations

from dataclasses import dataclass

from lib.actions import Chat, MoveTo
from lib.observation import GridPosition, Observation
from normal_strategy import (
    ENTRY_X,
    HOME_FALLBACK_X,
    PRISON_GATES,
    RESCUE_DISTANCE,
    NormalStrategy,
    _active_enemies,
    _assign_flag,
    _avoid_enemy_cluster,
    _clamp_to_map,
    _closest_block,
    _entry_waypoint,
    _manhattan,
    _needs_rescue,
    _pick_defense_target,
)

FORK_PATROL_Z_POINTS = (-22, -12, -4, 4, 12, 20)
FORK_DEFENSE_DISTANCE = 12


def _fork_patrol_target(obs: Observation, patrol_index: int) -> GridPosition:
    x = 16 if obs.my_team == "L" else -16
    z = FORK_PATROL_Z_POINTS[patrol_index % len(FORK_PATROL_Z_POINTS)]
    return _clamp_to_map(obs, x, z)


def _forward_pressure_target(obs: Observation, target: GridPosition) -> GridPosition:
    step_x = 2 if obs.my_team == "L" else -2
    return _clamp_to_map(obs, target.x + step_x, target.z)


@dataclass
class ForkedNormalStrategy(NormalStrategy):
    chat_cooldown: float = 4.0

    def _forced_target(self, obs: Observation) -> GridPosition | None:
        current = obs.me.position
        if self.last_position is not None and _manhattan(current, self.last_position) <= 1:
            self.idle_ticks += 1
        else:
            self.idle_ticks = 0
        self.last_position = current
        if self.idle_ticks < 8:
            return None
        self.idle_ticks = 0
        target = _fork_patrol_target(obs, self.patrol_index)
        self.patrol_index += 1
        return target

    def compute_next_action(self, obs: Observation) -> list[MoveTo | Chat]:
        me = obs.self_player
        my_pos = me.position
        enemies = _active_enemies(obs)

        forced_target = self._forced_target(obs)
        if forced_target is not None and not me.in_prison:
            return self._travel("Fork reset", forced_target, radius=1)

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
            if _manhattan(my_pos, target) <= FORK_DEFENSE_DISTANCE or _needs_rescue(obs):
                return self._travel(intent, target, radius=1)

        if _needs_rescue(obs):
            gate = PRISON_GATES[obs.my_team]
            if _manhattan(my_pos, gate) <= RESCUE_DISTANCE + 4:
                return self._travel("Rescue teammate", gate, radius=0)

        assigned_flag = _assign_flag(obs, enemies)
        if assigned_flag is not None:
            target = _entry_waypoint(obs, my_pos, assigned_flag, enemies)
            target = _avoid_enemy_cluster(obs, my_pos, target, enemies)
            target = _forward_pressure_target(obs, target)
            return self._travel("Fork collect", target, radius=0)

        patrol = _fork_patrol_target(obs, self.patrol_index)
        self.patrol_index += 1
        return self._travel("Fork patrol", patrol, radius=2)


__all__ = ["ForkedNormalStrategy"]