from __future__ import annotations

from dataclasses import dataclass

from lib.actions import Chat, MoveTo
from lib.observation import BlockState, GridPosition, Observation, PlayerState

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PRISON_GATE_L = GridPosition(x=-16, z=24)
PRISON_GATE_R = GridPosition(x=16, z=24)
MAP_X_MIN, MAP_X_MAX = -23, 23
MAP_Z_MIN, MAP_Z_MAX = -35, 35
HOME_INTRUSION_BUFFER = 2
ENEMY_AVOID_RADIUS = 8


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _manhattan(a: GridPosition, b: GridPosition) -> int:
    return abs(a.x - b.x) + abs(a.z - b.z)


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _active_enemies(obs: Observation) -> tuple[PlayerState, ...]:
    return tuple(e for e in obs.enemies if not e.in_prison)


def _closest_block(origin: GridPosition, blocks: tuple[BlockState, ...]) -> BlockState | None:
    if not blocks:
        return None
    return min(blocks, key=lambda b: _manhattan(origin, b.grid_position))


def _unplaced_flags(flags: tuple[BlockState, ...], gold_positions: tuple[GridPosition, ...]) -> tuple[BlockState, ...]:
    occupied = {(p.x, p.z) for p in gold_positions}
    return tuple(f for f in flags if (f.grid_position.x, f.grid_position.z) not in occupied)


def _prison_gate(obs: Observation) -> GridPosition:
    return PRISON_GATE_L if obs.my_team == "L" else PRISON_GATE_R


def _is_our_territory(pos: GridPosition, my_team: str) -> bool:
    if my_team == "L":
        return pos.x <= HOME_INTRUSION_BUFFER
    return pos.x >= -HOME_INTRUSION_BUFFER


def _intruders(obs: Observation, enemies: tuple[PlayerState, ...]) -> tuple[PlayerState, ...]:
    return tuple(e for e in enemies if _is_our_territory(e.position, obs.my_team))


def _nearest_enemy_dist(pos: GridPosition, enemies: tuple[PlayerState, ...]) -> int:
    if not enemies:
        return 999
    return min(_manhattan(pos, e.position) for e in enemies)


def _go(actions: list, dest: GridPosition, radius: int = 0, *, avoid_entities: bool = False) -> None:
    actions.append(
        MoveTo(
            x=dest.x,
            z=dest.z,
            radius=radius,
            sprint=True,
            jump=True,
            avoid_entities=avoid_entities,
        )
    )


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
@dataclass
class ForsakenStrategy:
    last_chat_at: float = 0.0
    chat_cooldown: float = 8.0
    patrol_index: int = 0
    last_pos: GridPosition | None = None
    idle_ticks: int = 0

    def on_game_start(self, obs: Observation) -> None:
        self.last_chat_at = 0.0
        self.patrol_index = 0
        self.last_pos = None
        self.idle_ticks = 0

    def compute_next_action(self, obs: Observation) -> list[MoveTo | Chat]:
        actions: list[MoveTo | Chat] = []
        me = obs.self_player
        my_pos = obs.me.position
        my_team = obs.my_team
        enemies = _active_enemies(obs)
        home_intruders = _intruders(obs, enemies)
        flags = _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions)

        # Anti-idle: if stuck in same spot, force a different destination
        if self.last_pos is not None and _manhattan(my_pos, self.last_pos) < 2:
            self.idle_ticks += 1
        else:
            self.idle_ticks = 0
        self.last_pos = my_pos

        if self.idle_ticks > 20 and not me.in_prison:
            self.patrol_index += 1
            patrol_z = [-30, -15, 0, 15, 30][self.patrol_index % 5]
            nudge_x = 5 if my_team == "L" else -5
            _go(actions, GridPosition(x=my_pos.x + nudge_x, z=patrol_z), radius=1)
            return actions

        # --- 1. Prison: sprint to gate ---
        if me.in_prison:
            _go(actions, _prison_gate(obs), radius=0)
            return actions

        # --- 2. Carrying flag: sprint straight home ---
        if me.has_flag:
            home_targets = tuple(block.grid_position for block in obs.my_targets)
            if home_targets:
                home = min(home_targets, key=lambda target: _manhattan(my_pos, target))
                _go(actions, home, radius=0, avoid_entities=True)
            else:
                home_x = -18 if my_team == "L" else 18
                _go(actions, GridPosition(x=home_x, z=0), radius=1, avoid_entities=True)
            return actions

        # --- 3. Tag intruders ---
        if home_intruders:
            target = min(home_intruders, key=lambda e: _manhattan(my_pos, e.position))
            if _manhattan(my_pos, target.position) <= ENEMY_AVOID_RADIUS * 2:
                _go(actions, target.position, radius=0)
                return actions

        # --- 4. Grab closest flag ---
        if flags:
            nearest = _closest_block(my_pos, flags)
            if nearest is not None:
                _go(actions, nearest.grid_position, radius=0)
                return actions

        # --- 5. Rescue jailed allies ---
        jailed = tuple(a for a in obs.allies if a.in_prison)
        if jailed:
            _go(actions, _prison_gate(obs), radius=1)
            return actions

        # --- 6. Patrol enemy territory ---
        patrol_x = 18 if my_team == "L" else -18
        patrol_z = [-30, -15, 0, 15, 30][self.patrol_index % 5]
        self.patrol_index += 1
        _go(actions, GridPosition(x=patrol_x, z=patrol_z), radius=1)
        return actions
