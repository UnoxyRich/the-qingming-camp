from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from lib.actions import Chat, MoveTo
from lib.observation import BlockState, GridPosition, Observation, PlayerState

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
PRISON_GATE_L = GridPosition(x=-16, z=24)
PRISON_GATE_R = GridPosition(x=16, z=24)
ENEMY_AVOID_RADIUS = 8
ENEMY_PRESSURE_RADIUS = 12
HOME_INTRUSION_BUFFER = 2
INTERCEPT_LOOKAHEAD = (0.25, 0.5, 0.75, 1.0)
INTERCEPT_COMMIT_RANGE = 20
TERRITORY_ENTRY_DEPTH = 6
TERRITORY_ENTRY_SPREAD = (-18, -10, -4, 0, 4, 10, 18)
PATROL_Z_POINTS = (-18, -10, -2, 6, 14)
RESCUE_RANGE = 30
MAP_X_MIN, MAP_X_MAX = -23, 23
MAP_Z_MIN, MAP_Z_MAX = -35, 35


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------
def _manhattan(a: GridPosition, b: GridPosition) -> int:
    return abs(a.x - b.x) + abs(a.z - b.z)


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _clamp_pos(x: int, z: int) -> GridPosition:
    return GridPosition(x=_clamp(x, MAP_X_MIN, MAP_X_MAX), z=_clamp(z, MAP_Z_MIN, MAP_Z_MAX))


# ---------------------------------------------------------------------------
# Observation helpers
# ---------------------------------------------------------------------------
def _active_enemies(obs: Observation) -> tuple[PlayerState, ...]:
    return tuple(e for e in obs.enemies if not e.in_prison)


def _closest_block(origin: GridPosition, blocks: tuple[BlockState, ...]) -> BlockState | None:
    if not blocks:
        return None
    return min(blocks, key=lambda b: (_manhattan(origin, b.grid_position), b.grid_position.x, b.grid_position.z))


def _unplaced_flags(flags: tuple[BlockState, ...], gold_positions: tuple[GridPosition, ...]) -> tuple[BlockState, ...]:
    occupied = {(p.x, p.z) for p in gold_positions}
    return tuple(f for f in flags if (f.grid_position.x, f.grid_position.z) not in occupied)


def _prison_gate(obs: Observation) -> GridPosition:
    return PRISON_GATE_L if obs.my_team == "L" else PRISON_GATE_R


def _is_enemy_territory(pos: GridPosition, my_team: str) -> bool:
    return pos.x > 0 if my_team == "L" else pos.x < 0


def _is_our_territory(pos: GridPosition, my_team: str) -> bool:
    if my_team == "L":
        return pos.x <= HOME_INTRUSION_BUFFER
    return pos.x >= -HOME_INTRUSION_BUFFER


def _sorted_players(players: tuple[PlayerState, ...]) -> tuple[PlayerState, ...]:
    return tuple(sorted(players, key=lambda p: p.name))


def _sorted_blocks(blocks: tuple[BlockState, ...]) -> tuple[BlockState, ...]:
    return tuple(sorted(blocks, key=lambda b: (b.grid_position.x, b.grid_position.z)))


# ---------------------------------------------------------------------------
# Enemy threat model
# ---------------------------------------------------------------------------
def _enemy_pressure(pos: GridPosition, enemies: tuple[PlayerState, ...]) -> float:
    pressure = 0.0
    for e in enemies:
        d = _manhattan(pos, e.position)
        if d <= ENEMY_PRESSURE_RADIUS:
            pressure += (ENEMY_PRESSURE_RADIUS - d) ** 1.5
    return pressure


def _nearest_enemy_dist(pos: GridPosition, enemies: tuple[PlayerState, ...]) -> int:
    if not enemies:
        return 999
    return min(_manhattan(pos, e.position) for e in enemies)


def _enemy_centroid(enemies: tuple[PlayerState, ...]) -> GridPosition | None:
    if not enemies:
        return None
    cx = round(sum(e.position.x for e in enemies) / len(enemies))
    cz = round(sum(e.position.z for e in enemies) / len(enemies))
    return GridPosition(x=cx, z=cz)


# ---------------------------------------------------------------------------
# Velocity tracking & intercept prediction
# ---------------------------------------------------------------------------
def _estimate_velocity(
    enemy: PlayerState,
    prev_positions: dict[str, GridPosition],
) -> tuple[int, int]:
    prev = prev_positions.get(enemy.name)
    if prev is None:
        return (0, 0)
    return (enemy.position.x - prev.x, enemy.position.z - prev.z)


def _predict_position(enemy: PlayerState, vx: int, vz: int, steps: float) -> GridPosition:
    return GridPosition(
        x=_clamp(round(enemy.position.x + vx * steps), MAP_X_MIN, MAP_X_MAX),
        z=_clamp(round(enemy.position.z + vz * steps), MAP_Z_MIN, MAP_Z_MAX),
    )


def _best_intercept_point(
    pursuer: GridPosition,
    enemy: PlayerState,
    prev_positions: dict[str, GridPosition],
) -> GridPosition:
    vx, vz = _estimate_velocity(enemy, prev_positions)
    if vx == 0 and vz == 0:
        return enemy.position

    best_point = enemy.position
    best_score = float("inf")
    for step in INTERCEPT_LOOKAHEAD:
        predicted = _predict_position(enemy, vx, vz, step)
        pursuer_d = _manhattan(pursuer, predicted)
        enemy_d = _manhattan(enemy.position, predicted)
        score = abs(pursuer_d - enemy_d) + pursuer_d * 0.6
        if score < best_score:
            best_score = score
            best_point = predicted
    return best_point


# ---------------------------------------------------------------------------
# Territory entry waypointing – cross midfield at safest point
# ---------------------------------------------------------------------------
def _territory_entry_waypoint(
    origin: GridPosition,
    dest: GridPosition,
    enemies: tuple[PlayerState, ...],
    my_team: str,
) -> GridPosition:
    if not _is_enemy_territory(dest, my_team) or _is_enemy_territory(origin, my_team):
        return dest
    entry_x = TERRITORY_ENTRY_DEPTH if my_team == "L" else -TERRITORY_ENTRY_DEPTH
    candidates = tuple(
        GridPosition(x=entry_x, z=_clamp(dest.z + offset, MAP_Z_MIN, MAP_Z_MAX))
        for offset in TERRITORY_ENTRY_SPREAD
    )
    best = min(
        candidates,
        key=lambda c: (
            _enemy_pressure(c, enemies) * 2.5
            + _manhattan(origin, c)
            + _manhattan(c, dest) * 0.4,
            abs(c.z - dest.z),
        ),
    )
    if _manhattan(origin, dest) <= 10:
        return dest
    return best


# ---------------------------------------------------------------------------
# Evasive routing – dodge nearby enemies around a target
# ---------------------------------------------------------------------------
def _evasive_target(
    origin: GridPosition,
    target: GridPosition,
    enemies: tuple[PlayerState, ...],
    my_team: str,
) -> GridPosition:
    nearby = tuple(e for e in enemies if _manhattan(target, e.position) <= ENEMY_AVOID_RADIUS)
    if not nearby:
        return target

    centroid = _enemy_centroid(nearby)
    if centroid is None:
        return target

    dx = target.x - centroid.x
    dz = target.z - centroid.z
    length = math.hypot(dx, dz) or 1.0
    push_x = round(dx / length * 4)
    push_z = round(dz / length * 4)

    home_bias = -2 if my_team == "L" else 2
    shifted = _clamp_pos(target.x + push_x + home_bias, target.z + push_z)

    if _manhattan(origin, shifted) > _manhattan(origin, target) + 10:
        return target
    return shifted


# ---------------------------------------------------------------------------
# Safe flag-carrier route – go DIRECTLY to gold block, dodge enemies on path
# ---------------------------------------------------------------------------
def _carrier_direct_route(
    origin: GridPosition,
    gold_target: GridPosition,
    enemies: tuple[PlayerState, ...],
    my_team: str,
) -> GridPosition:
    if not enemies or _nearest_enemy_dist(gold_target, enemies) > ENEMY_AVOID_RADIUS:
        return gold_target
    nearby = tuple(e for e in enemies if _manhattan(gold_target, e.position) <= ENEMY_AVOID_RADIUS)
    if not nearby:
        return gold_target
    centroid = _enemy_centroid(nearby)
    if centroid is None:
        return gold_target
    dx = gold_target.x - centroid.x
    dz = gold_target.z - centroid.z
    length = math.hypot(dx, dz) or 1.0
    push_x = round(dx / length * 3)
    push_z = round(dz / length * 3)
    home_pull = -2 if my_team == "L" else 2
    detour = _clamp_pos(gold_target.x + push_x + home_pull, gold_target.z + push_z)
    if _manhattan(origin, detour) > _manhattan(origin, gold_target) + 6:
        return gold_target
    return detour


# ---------------------------------------------------------------------------
# Intruder detection & tagging
# ---------------------------------------------------------------------------
def _intruders(obs: Observation, enemies: tuple[PlayerState, ...]) -> tuple[PlayerState, ...]:
    return tuple(e for e in enemies if _is_our_territory(e.position, obs.my_team))


def _best_intruder_target(
    origin: GridPosition,
    intruders: tuple[PlayerState, ...],
    my_team: str,
    prev_positions: dict[str, GridPosition],
) -> tuple[PlayerState, GridPosition] | None:
    if not intruders:
        return None
    scored = []
    for enemy in intruders:
        depth = enemy.position.x if my_team == "L" else -enemy.position.x
        priority = (0 if enemy.has_flag else 1, depth)
        intercept = _best_intercept_point(origin, enemy, prev_positions)
        dist = _manhattan(origin, intercept)
        scored.append((priority, dist, enemy, intercept))
    scored.sort()
    best_enemy, best_intercept = scored[0][2], scored[0][3]
    return (best_enemy, best_intercept)


# ---------------------------------------------------------------------------
# Teammate rescue – go step on their pressure plate
# ---------------------------------------------------------------------------
def _teammate_needs_rescue(obs: Observation) -> bool:
    return any(p.in_prison and p.name != obs.bot_name for p in obs.myteam_players)


def _rescue_gate(obs: Observation) -> GridPosition:
    return _prison_gate(obs)


# ---------------------------------------------------------------------------
# Flag scoring & assignment (optimal for 2 players)
# ---------------------------------------------------------------------------
def _flag_score(
    player_pos: GridPosition,
    flag: BlockState,
    enemies: tuple[PlayerState, ...],
    my_team: str,
) -> float:
    fp = flag.grid_position
    dist = _manhattan(player_pos, fp)
    pressure = _enemy_pressure(fp, enemies)
    safety_bonus = _nearest_enemy_dist(fp, enemies)
    cross_penalty = 4.0 if _is_enemy_territory(fp, my_team) and not _is_enemy_territory(player_pos, my_team) else 0.0
    return dist + pressure * 3.0 - safety_bonus * 0.5 + cross_penalty


def _plan_flag_assignments(
    players: tuple[PlayerState, ...],
    flags: tuple[BlockState, ...],
    enemies: tuple[PlayerState, ...],
    my_team: str,
) -> dict[str, BlockState]:
    active = _sorted_players(tuple(p for p in players if not p.in_prison and not p.has_flag))
    available = list(_sorted_blocks(flags))
    if not active or not available:
        return {}

    if len(active) == 1:
        best = min(available, key=lambda f: _flag_score(active[0].position, f, enemies, my_team))
        return {active[0].name: best}

    if len(active) >= 2 and len(available) >= 2:
        best_cost = float("inf")
        best_assign: dict[str, BlockState] = {}
        p0, p1 = active[0], active[1]
        for i, f0 in enumerate(available):
            c0 = _flag_score(p0.position, f0, enemies, my_team)
            if c0 >= best_cost:
                continue
            for j, f1 in enumerate(available):
                if i == j:
                    continue
                c1 = _flag_score(p1.position, f1, enemies, my_team)
                spread_bonus = min(6.0, _manhattan(f0.grid_position, f1.grid_position) * 0.3)
                total = c0 + c1 - spread_bonus
                if total < best_cost:
                    best_cost = total
                    best_assign = {p0.name: f0, p1.name: f1}
        return best_assign

    assignments: dict[str, BlockState] = {}
    remaining = list(available)
    for p in active:
        if not remaining:
            break
        chosen = min(remaining, key=lambda f: _flag_score(p.position, f, enemies, my_team))
        assignments[p.name] = chosen
        remaining.remove(chosen)
    return assignments


# ---------------------------------------------------------------------------
# Role assignment – who collects, who flexes
# ---------------------------------------------------------------------------
def _am_i_flex(obs: Observation) -> bool:
    teammates = _sorted_players(tuple(p for p in obs.myteam_players if not p.in_prison))
    if len(teammates) < 2:
        return False
    return teammates[-1].name == obs.bot_name


# ---------------------------------------------------------------------------
# Anti-idle – NEVER stand still
# ---------------------------------------------------------------------------
IDLE_THRESHOLD_TICKS = 3
NUDGE_DISTANCE = 4


def _anti_idle_target(origin: GridPosition, target: GridPosition, my_team: str) -> GridPosition:
    if _manhattan(origin, target) > 2:
        return target
    nudge_x = NUDGE_DISTANCE if my_team == "L" else -NUDGE_DISTANCE
    nudge_z = NUDGE_DISTANCE if target.z <= 0 else -NUDGE_DISTANCE
    return _clamp_pos(target.x + nudge_x, target.z + nudge_z)


def _force_move_if_stuck(
    origin: GridPosition,
    last_pos: GridPosition | None,
    idle_count: int,
    my_team: str,
    patrol_index: int,
) -> tuple[GridPosition | None, int]:
    if last_pos is not None and _manhattan(origin, last_pos) <= 1:
        idle_count += 1
    else:
        idle_count = 0
    if idle_count >= IDLE_THRESHOLD_TICKS:
        forced = _patrol_point(my_team, patrol_index)
        if _manhattan(origin, forced) <= 2:
            forced = _clamp_pos(
                origin.x + (5 if my_team == "L" else -5),
                origin.z + (5 if origin.z <= 0 else -5),
            )
        return forced, 0
    return None, idle_count


def _patrol_point(my_team: str, index: int) -> GridPosition:
    x = 10 if my_team == "L" else -10
    z = PATROL_Z_POINTS[index % len(PATROL_Z_POINTS)]
    return GridPosition(x=x, z=z)


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
@dataclass
class CollectOnlyStrategy:
    chat_cooldown: float = 8.0
    last_intent: tuple[str, int, int] | None = None
    last_chat_at: float = 0.0
    prev_enemy_positions: dict[str, GridPosition] = field(default_factory=dict)
    patrol_index: int = 0
    last_pos: GridPosition | None = None
    idle_ticks: int = 0

    def on_game_start(self, obs: Observation) -> None:
        self.last_intent = None
        self.last_chat_at = 0.0
        self.prev_enemy_positions.clear()
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
        flags_to_capture = _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions)
        is_flex = _am_i_flex(obs)

        # --- 0. Force movement if idle too long ---
        forced_dest, self.idle_ticks = _force_move_if_stuck(
            my_pos, self.last_pos, self.idle_ticks, my_team, self.patrol_index,
        )
        self.last_pos = my_pos
        if forced_dest is not None and not me.in_prison:
            self.patrol_index += 1
            self._go(actions, "Force move", forced_dest, radius=1)
            self._snapshot_enemies(enemies)
            return actions

        # --- 1. Prison: walk to gate ---
        if me.in_prison:
            gate = _anti_idle_target(my_pos, _prison_gate(obs), my_team)
            self._go(actions, "Prison exit", gate, radius=0)
            self._snapshot_enemies(enemies)
            return actions

        # --- 2. Carrying flag: go STRAIGHT home, pathfinder avoids leaves+enemies ---
        if me.has_flag:
            home_target = _closest_block(my_pos, obs.my_targets)
            if home_target is not None:
                dest = home_target.grid_position
                self._go(actions, "Plant flag", dest, radius=0)
            else:
                # Fallback: head deep into own territory
                home_x = -18 if my_team == "L" else 18
                dest = _clamp_pos(home_x, my_pos.z)
                self._go(actions, "Flag home", dest, radius=1)
            self._snapshot_enemies(enemies)
            return actions

        # --- 3. Flex role: rescue jailed teammate ---
        if is_flex and _teammate_needs_rescue(obs):
            gate = _rescue_gate(obs)
            if _manhattan(my_pos, gate) <= RESCUE_RANGE:
                dest = _evasive_target(my_pos, gate, enemies, my_team)
                dest = _anti_idle_target(my_pos, dest, my_team)
                self._go(actions, "Rescue teammate", dest, radius=0)
                self._snapshot_enemies(enemies)
                return actions

        # --- 4. Tag intruders (flex prioritises, collector only if very close) ---
        if home_intruders:
            result = _best_intruder_target(my_pos, home_intruders, my_team, self.prev_enemy_positions)
            if result is not None:
                enemy, intercept = result
                should_chase = is_flex or enemy.has_flag or _manhattan(my_pos, intercept) <= 8
                if should_chase:
                    intercept = _anti_idle_target(my_pos, intercept, my_team)
                    intent = "Tag carrier" if enemy.has_flag else "Tag intruder"
                    self._go(actions, intent, intercept, radius=1)
                    self._snapshot_enemies(enemies)
                    return actions

        # --- 5. Collect flags ---
        assignments = _plan_flag_assignments(obs.myteam_players, flags_to_capture, enemies, my_team)
        assigned = assignments.get(me.name)
        if assigned is None and flags_to_capture:
            assigned_block = min(
                flags_to_capture,
                key=lambda f: _flag_score(my_pos, f, enemies, my_team),
            )
            assigned = assigned_block

        if assigned is not None:
            dest = assigned.grid_position
            dest = _territory_entry_waypoint(my_pos, dest, enemies, my_team)
            dest = _evasive_target(my_pos, dest, enemies, my_team)
            dest = _anti_idle_target(my_pos, dest, my_team)
            self._go(actions, "Collect flag", dest, radius=0)
            self._snapshot_enemies(enemies)
            return actions

        # --- 6. Patrol enemy side ---
        fb = _patrol_point(my_team, self.patrol_index)
        self.patrol_index += 1
        fb = _territory_entry_waypoint(my_pos, fb, enemies, my_team)
        fb = _anti_idle_target(my_pos, fb, my_team)
        self._go(actions, "Patrol", fb, radius=2)
        self._snapshot_enemies(enemies)
        return actions

    # -----------------------------------------------------------------------
    def _go(self, actions: list[MoveTo | Chat], intent: str, pos: GridPosition, *, radius: int) -> None:
        self._announce(actions, intent, pos.x, pos.z)
        actions.append(MoveTo(x=pos.x, z=pos.z, radius=radius, sprint=True))

    def _announce(self, actions: list[MoveTo | Chat], intent: str, x: int, z: int) -> None:
        sig = (intent, x, z)
        if sig == self.last_intent:
            return
        now = time.monotonic()
        if now - self.last_chat_at >= self.chat_cooldown:
            actions.append(Chat(message=f"[COLLECT] {intent} -> ({x}, {z})"))
            self.last_chat_at = now
        self.last_intent = sig

    def _snapshot_enemies(self, enemies: tuple[PlayerState, ...]) -> None:
        self.prev_enemy_positions.clear()
        for e in enemies:
            self.prev_enemy_positions[e.name] = e.position


__all__ = ["CollectOnlyStrategy"]