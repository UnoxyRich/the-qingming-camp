from __future__ import annotations

import math
import random
import re
import time
from dataclasses import dataclass, field

from lib.actions import Chat, MoveTo
from lib.observation import BlockState, GridPosition, Observation, PlayerState, TeamName

# Map layout constants
MIDFIELD_X = 0
L_TERRITORY_X = -12  # Deep in left territory
R_TERRITORY_X = 12   # Deep in right territory
PRISON_GATE_L = (-16, 24)
PRISON_GATE_R = (16, 24)

# Danger zone: how close an enemy must be to be considered a threat
ENEMY_DANGER_RADIUS = 8
RESCUE_RANGE = 25
FLAG_CARRIER_CHASE_RANGE = 30
RESCUE_FLAG_PROXIMITY = 5
PRESSURE_PLATE_RADIUS = 0
INTERCEPT_LOOKAHEAD_STEPS = (0.25, 0.5, 0.75, 1.0)
INTERCEPT_COMMIT_RANGE = 26
ENEMY_INTENT_MEMORY = 6
TERRITORY_ENTRY_DEPTH = 6
TERRITORY_ENTRY_SPREAD = (-18, -10, -4, 0, 4, 10, 18)
COORDINATE_PATTERN = re.compile(r"\((-?\d+)\s*,\s*(-?\d+)\)")


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _manhattan(a: GridPosition, b: GridPosition) -> int:
    return abs(a.x - b.x) + abs(a.z - b.z)


def _closest_block(origin: GridPosition, blocks: tuple[BlockState, ...]) -> BlockState | None:
    if not blocks:
        return None
    return min(blocks, key=lambda b: (_manhattan(origin, b.grid_position), b.grid_position.x, b.grid_position.z))


def _closest_player(origin: GridPosition, players: tuple[PlayerState, ...]) -> PlayerState | None:
    if not players:
        return None
    return min(players, key=lambda p: (_manhattan(origin, p.position), p.position.x, p.position.z))


def _unplaced_flags(flags: tuple[BlockState, ...], gold_positions: tuple[GridPosition, ...]) -> tuple[BlockState, ...]:
    golds = {(g.x, g.z) for g in gold_positions}
    return tuple(f for f in flags if (f.grid_position.x, f.grid_position.z) not in golds)


def _enemy_near(pos: GridPosition, enemies: tuple[PlayerState, ...], radius: int = ENEMY_DANGER_RADIUS) -> bool:
    return any(_manhattan(pos, e.position) <= radius for e in enemies if not e.in_prison)


def _nearest_enemy_dist(pos: GridPosition, enemies: tuple[PlayerState, ...]) -> int:
    active = [e for e in enemies if not e.in_prison]
    if not active:
        return 999
    return min(_manhattan(pos, e.position) for e in active)


def _safest_flag(origin: GridPosition, flags: tuple[BlockState, ...], enemies: tuple[PlayerState, ...]) -> BlockState | None:
    """Pick the flag that is farthest from enemies, with tiebreak on distance to self."""
    if not flags:
        return None
    active_enemies = [e for e in enemies if not e.in_prison]

    def score(f: BlockState) -> tuple[int, int]:
        min_enemy_dist = min((_manhattan(f.grid_position, e.position) for e in active_enemies), default=999)
        self_dist = _manhattan(origin, f.grid_position)
        # Prefer flags far from enemies (negate), then close to us
        return (-min_enemy_dist, self_dist)

    return min(flags, key=score)


def _evasive_target(pos: GridPosition, dest: GridPosition, enemies: tuple[PlayerState, ...]) -> GridPosition:
    """If enemies are near the direct path, offset the target slightly to evade."""
    active = [e for e in enemies if not e.in_prison and _manhattan(pos, e.position) <= ENEMY_DANGER_RADIUS]
    if not active:
        return dest
    # Average enemy position
    avg_ex = sum(e.position.x for e in active) // len(active)
    avg_ez = sum(e.position.z for e in active) // len(active)
    # Offset destination away from enemies
    offset_x = 3 if dest.x > avg_ex else -3
    offset_z = 3 if dest.z > avg_ez else -3
    return GridPosition(x=max(-23, min(23, dest.x + offset_x)), z=max(-35, min(35, dest.z + offset_z)))


def _is_enemy_territory(pos: GridPosition, my_team: TeamName) -> bool:
    return pos.x > 0 if my_team == "L" else pos.x < 0


def _enemy_pressure(pos: GridPosition, enemies: tuple[PlayerState, ...]) -> float:
    pressure = 0.0
    for enemy in enemies:
        if enemy.in_prison:
            continue
        distance = _manhattan(pos, enemy.position)
        pressure += max(0.0, ENEMY_DANGER_RADIUS + 4 - distance)
    return pressure


def _territory_entry_waypoint(
    origin: GridPosition,
    dest: GridPosition,
    enemies: tuple[PlayerState, ...],
    my_team: TeamName,
) -> GridPosition:
    if not _is_enemy_territory(dest, my_team) or _is_enemy_territory(origin, my_team):
        return dest

    entry_x = TERRITORY_ENTRY_DEPTH if my_team == "L" else -TERRITORY_ENTRY_DEPTH
    candidates = tuple(
        GridPosition(x=entry_x, z=max(-35, min(35, dest.z + offset)))
        for offset in TERRITORY_ENTRY_SPREAD
    )
    best = min(
        candidates,
        key=lambda candidate: (
            _enemy_pressure(candidate, enemies) * 2.5
            + _manhattan(origin, candidate)
            + (_manhattan(candidate, dest) * 0.4),
            abs(candidate.z - dest.z),
        ),
    )
    if _manhattan(origin, dest) <= 10:
        return _evasive_target(origin, dest, enemies)
    return best


def _offensive_route_target(
    origin: GridPosition,
    dest: GridPosition,
    enemies: tuple[PlayerState, ...],
    my_team: TeamName,
) -> GridPosition:
    staged = _territory_entry_waypoint(origin, dest, enemies, my_team)
    return _evasive_target(origin, staged, enemies)


def _prison_gate_for_team(team: str) -> GridPosition:
    if team == "L":
        return GridPosition(x=PRISON_GATE_L[0], z=PRISON_GATE_L[1])
    return GridPosition(x=PRISON_GATE_R[0], z=PRISON_GATE_R[1])


def _risk_penalty(pos: GridPosition, enemies: tuple[PlayerState, ...]) -> int:
    nearest = _nearest_enemy_dist(pos, enemies)
    return max(0, ENEMY_DANGER_RADIUS - min(nearest, ENEMY_DANGER_RADIUS)) * 4


def _targets_for_team(obs: Observation, team: TeamName) -> tuple[BlockState, ...]:
    occupied_positions = {(position.x, position.z) for position in obs.flag_positions}
    return tuple(
        block
        for block in obs.gold_blocks
        if (block.grid_position.x, block.grid_position.z) not in occupied_positions
        and ((team == "L" and block.grid_position.x < 0) or (team == "R" and block.grid_position.x > 0))
    )


def _vector_from_positions(previous: GridPosition | None, current: GridPosition) -> tuple[int, int]:
    if previous is None:
        return (0, 0)
    return (current.x - previous.x, current.z - previous.z)


def _heading_penalty(enemy: PlayerState, target: GridPosition, previous: GridPosition | None) -> float:
    velocity_x, velocity_z = _vector_from_positions(previous, enemy.position)
    if velocity_x == 0 and velocity_z == 0:
        return 0.0
    target_dx = target.x - enemy.position.x
    target_dz = target.z - enemy.position.z
    target_length = math.hypot(target_dx, target_dz)
    velocity_length = math.hypot(velocity_x, velocity_z)
    if target_length < 0.001 or velocity_length < 0.001:
        return 0.0
    normalized_dot = ((velocity_x * target_dx) + (velocity_z * target_dz)) / (velocity_length * target_length)
    alignment_penalty = (1.0 - max(-1.0, min(1.0, normalized_dot))) * 6.0
    cross_track = abs((velocity_x * target_dz) - (velocity_z * target_dx)) / velocity_length
    return alignment_penalty + min(8.0, cross_track)


def _parse_announced_target(enemy_name: str, recent_messages: tuple[str, ...]) -> GridPosition | None:
    enemy_name_lower = enemy_name.lower()
    for message in reversed(recent_messages[-ENEMY_INTENT_MEMORY:]):
        lowered = message.lower()
        if enemy_name_lower not in lowered:
            continue
        match = COORDINATE_PATTERN.search(message)
        if match is None:
            continue
        return GridPosition(x=int(match.group(1)), z=int(match.group(2)))
    return None


def _predict_enemy_target(
    enemy: PlayerState,
    obs: Observation,
    previous_positions: dict[str, GridPosition],
) -> GridPosition:
    announced_target = _parse_announced_target(enemy.name, obs.recent_messages)
    if announced_target is not None:
        return announced_target

    if enemy.has_flag and enemy.team is not None:
        enemy_targets = _targets_for_team(obs, enemy.team)
        nearest_target = _closest_block(enemy.position, enemy_targets)
        if nearest_target is not None:
            return nearest_target.grid_position

    candidate_targets = tuple(flag.grid_position for flag in obs.flags_to_protect)
    if not candidate_targets:
        return enemy.position

    previous_position = previous_positions.get(enemy.name)
    return min(
        candidate_targets,
        key=lambda target: (
            _manhattan(enemy.position, target) + _heading_penalty(enemy, target, previous_position),
            target.x,
            target.z,
        ),
    )


def _project_intercept_point(
    pursuer: GridPosition,
    enemy: PlayerState,
    predicted_target: GridPosition,
) -> GridPosition:
    if predicted_target == enemy.position:
        return predicted_target

    best_point = predicted_target
    best_score = float("inf")
    for step in INTERCEPT_LOOKAHEAD_STEPS:
        sample_x = int(round(enemy.position.x + (predicted_target.x - enemy.position.x) * step))
        sample_z = int(round(enemy.position.z + (predicted_target.z - enemy.position.z) * step))
        sample = GridPosition(x=sample_x, z=sample_z)
        pursuer_distance = _manhattan(pursuer, sample)
        enemy_distance = _manhattan(enemy.position, sample)
        score = abs(pursuer_distance - enemy_distance) + pursuer_distance * 0.65
        if score < best_score:
            best_score = score
            best_point = sample
    return best_point


def _choose_interception_move(
    pursuer: GridPosition,
    obs: Observation,
    previous_positions: dict[str, GridPosition],
    *,
    require_home_intrusion: bool,
) -> tuple[str, GridPosition] | None:
    best_choice: tuple[str, GridPosition] | None = None
    best_score = float("inf")
    for enemy in obs.enemies:
        if enemy.in_prison:
            continue
        if require_home_intrusion and ((obs.my_team == "L" and enemy.position.x >= -2) or (obs.my_team == "R" and enemy.position.x <= 2)):
            continue

        predicted_target = _predict_enemy_target(enemy, obs, previous_positions)
        intercept_point = _project_intercept_point(pursuer, enemy, predicted_target)
        pursue_distance = _manhattan(pursuer, intercept_point)
        if pursue_distance > INTERCEPT_COMMIT_RANGE and not enemy.has_flag:
            continue

        target_distance = _manhattan(enemy.position, predicted_target)
        score = pursue_distance + (target_distance * 0.35) - (6 if enemy.has_flag else 0)
        if score < best_score:
            best_score = score
            best_choice = (enemy.name, intercept_point)
    return best_choice


def _remember_enemy_positions(previous_positions: dict[str, GridPosition], enemies: tuple[PlayerState, ...]) -> None:
    previous_positions.clear()
    for enemy in enemies:
        previous_positions[enemy.name] = enemy.position


def _sorted_players(players: tuple[PlayerState, ...]) -> tuple[PlayerState, ...]:
    return tuple(sorted(players, key=lambda player: (player.name, player.position.x, player.position.z)))


def _sorted_blocks(blocks: tuple[BlockState, ...]) -> tuple[BlockState, ...]:
    return tuple(sorted(blocks, key=lambda block: (block.grid_position.x, block.grid_position.z)))


def _plan_capture_assignments(
    players: tuple[PlayerState, ...],
    flags: tuple[BlockState, ...],
    targets: tuple[BlockState, ...],
    enemies: tuple[PlayerState, ...],
) -> dict[str, tuple[BlockState, BlockState]]:
    active_players = _sorted_players(tuple(player for player in players if not player.in_prison and not player.has_flag))
    usable_flags = _sorted_blocks(flags)
    usable_targets = _sorted_blocks(targets)
    assignment_count = min(len(active_players), len(usable_flags), len(usable_targets))
    if assignment_count <= 0:
        return {}
    best_assignment: dict[str, tuple[BlockState, BlockState]] = {}
    best_cost: float = float("inf")

    def search(
        index: int,
        used_flag_indices: set[int],
        used_target_indices: set[int],
        cost_so_far: float,
        current_assignment: dict[str, tuple[BlockState, BlockState]],
    ) -> None:
        nonlocal best_assignment, best_cost
        if index >= assignment_count:
            if cost_so_far < best_cost:
                best_cost = cost_so_far
                best_assignment = dict(current_assignment)
            return
        player = active_players[index]
        for flag_index, flag in enumerate(usable_flags):
            if flag_index in used_flag_indices:
                continue
            for target_index, target in enumerate(usable_targets):
                if target_index in used_target_indices:
                    continue
                route_cost = (
                    _manhattan(player.position, flag.grid_position)
                    + _manhattan(flag.grid_position, target.grid_position)
                    + _risk_penalty(flag.grid_position, enemies)
                    + _risk_penalty(target.grid_position, enemies) // 2
                )
                next_cost = cost_so_far + route_cost
                if next_cost >= best_cost:
                    continue
                used_flag_indices.add(flag_index)
                used_target_indices.add(target_index)
                current_assignment[player.name] = (flag, target)
                search(index + 1, used_flag_indices, used_target_indices, next_cost, current_assignment)
                current_assignment.pop(player.name, None)
                used_flag_indices.remove(flag_index)
                used_target_indices.remove(target_index)

    search(0, set(), set(), 0.0, {})
    return best_assignment


def _plan_scoring_assignments(
    players: tuple[PlayerState, ...],
    targets: tuple[BlockState, ...],
    enemies: tuple[PlayerState, ...],
) -> dict[str, BlockState]:
    carriers = _sorted_players(tuple(player for player in players if player.has_flag and not player.in_prison))
    usable_targets = _sorted_blocks(targets)
    assignment_count = min(len(carriers), len(usable_targets))
    if assignment_count <= 0:
        return {}
    best_assignment: dict[str, BlockState] = {}
    best_cost: float = float("inf")

    def search(index: int, used_target_indices: set[int], cost_so_far: float, current_assignment: dict[str, BlockState]) -> None:
        nonlocal best_assignment, best_cost
        if index >= assignment_count:
            if cost_so_far < best_cost:
                best_cost = cost_so_far
                best_assignment = dict(current_assignment)
            return
        player = carriers[index]
        for target_index, target in enumerate(usable_targets):
            if target_index in used_target_indices:
                continue
            route_cost = _manhattan(player.position, target.grid_position) + _risk_penalty(target.grid_position, enemies)
            next_cost = cost_so_far + route_cost
            if next_cost >= best_cost:
                continue
            used_target_indices.add(target_index)
            current_assignment[player.name] = target
            search(index + 1, used_target_indices, next_cost, current_assignment)
            current_assignment.pop(player.name, None)
            used_target_indices.remove(target_index)

    search(0, set(), 0.0, {})
    return best_assignment


def _nearest_capturable_flag(pos: GridPosition, obs: Observation) -> BlockState | None:
    capturable = _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions)
    return _closest_block(pos, capturable)


def _choose_rescue_player(
    players: tuple[PlayerState, ...],
    gate: GridPosition,
    enemies: tuple[PlayerState, ...],
    my_team: TeamName,
) -> PlayerState | None:
    candidates = _sorted_players(
        tuple(player for player in players if not player.in_prison and not player.has_flag)
    )
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda player: (
            _manhattan(player.position, gate)
            + _risk_penalty(gate, enemies)
            + (10 if _is_enemy_territory(player.position, my_team) else 0),
            player.name,
        ),
    )


def _find_enemy_by_name(obs: Observation, enemy_name: str) -> PlayerState | None:
    for enemy in obs.enemies:
        if enemy.name == enemy_name:
            return enemy
    return None


def _choose_team_interceptor(
    players: tuple[PlayerState, ...],
    obs: Observation,
    previous_positions: dict[str, GridPosition],
    *,
    require_home_intrusion: bool,
    max_distance: int,
) -> tuple[str, str, GridPosition] | None:
    candidates = _sorted_players(
        tuple(player for player in players if not player.in_prison and not player.has_flag)
    )
    best_choice: tuple[str, str, GridPosition] | None = None
    best_score = float("inf")

    for player in candidates:
        interception = _choose_interception_move(
            player.position,
            obs,
            previous_positions,
            require_home_intrusion=require_home_intrusion,
        )
        if interception is None:
            continue
        enemy_name, intercept = interception
        pursue_distance = _manhattan(player.position, intercept)
        if pursue_distance > max_distance:
            continue
        enemy = _find_enemy_by_name(obs, enemy_name)
        score = (
            pursue_distance
            + (_enemy_pressure(intercept, obs.enemies) * 1.2)
            + (8 if require_home_intrusion and _is_enemy_territory(player.position, obs.my_team) else 0)
            - (10 if enemy is not None and enemy.has_flag else 0)
        )
        if score < best_score:
            best_score = score
            best_choice = (player.name, enemy_name, intercept)

    return best_choice


# ---------------------------------------------------------------------------
# Attacker Strategy — focuses on capturing enemy flags and scoring
# ---------------------------------------------------------------------------

@dataclass
class AttackerStrategy:
    """Aggressive flag-capturing strategy.

    Priorities:
    1. If jailed → keep moving to the prison gate
    2. If carrying flag → sprint home to score (with evasion)
    3. Rescue nearby jailed teammate (opportunistic)
    4. Capture enemy flag (prefer safest one away from enemies)
    5. Patrol enemy territory looking for opportunities
    """

    chat_cooldown: float = 8.0
    last_intent: tuple[str, int, int] | None = None
    last_chat_at: float = 0.0
    rng: random.Random = field(default_factory=random.Random)
    enemy_positions: dict[str, GridPosition] = field(default_factory=dict)

    def on_game_start(self, obs: Observation) -> None:
        self.last_intent = None
        self.last_chat_at = 0.0
        self.enemy_positions.clear()

    def compute_next_action(self, obs: Observation) -> list[MoveTo | Chat]:
        actions: list[MoveTo | Chat] = []
        me = obs.self_player
        pos = obs.me.position
        enemies = obs.enemies
        my_targets = obs.my_targets
        gate = _prison_gate_for_team(obs.my_team)
        capture_assignments = _plan_capture_assignments(obs.myteam_players, _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions), my_targets, enemies)
        scoring_assignments = _plan_scoring_assignments(obs.myteam_players, my_targets, enemies)
        assigned_rescuer = _choose_rescue_player(obs.myteam_players, gate, enemies, obs.my_team)
        assigned_interceptor = _choose_team_interceptor(
            obs.myteam_players,
            obs,
            self.enemy_positions,
            require_home_intrusion=False,
            max_distance=FLAG_CARRIER_CHASE_RANGE if any(enemy.has_flag for enemy in enemies) else 12,
        )

        # 1. Jailed — keep pathing to the prison gate so rescue can happen faster
        if me.in_prison:
            self._announce(actions, "Need help at plate", gate.x, gate.z)
            _remember_enemy_positions(self.enemy_positions, enemies)
            actions.append(MoveTo(x=gate.x, z=gate.z, radius=PRESSURE_PLATE_RADIUS, sprint=True))
            return actions

        # 2. Carrying flag — rush home to score
        if me.has_flag:
            target = scoring_assignments.get(me.name) or _closest_block(pos, my_targets)
            if target is not None:
                gp = target.grid_position
                safe = _evasive_target(pos, gp, enemies)
                self._announce(actions, "Scoring flag", safe.x, safe.z)
                _remember_enemy_positions(self.enemy_positions, enemies)
                actions.append(MoveTo(x=safe.x, z=safe.z, radius=0, sprint=True))
                return actions

        if assigned_interceptor is not None and assigned_interceptor[0] == me.name:
            _, enemy_name, intercept = assigned_interceptor
            self._announce(actions, f"Cutting off {enemy_name}", intercept.x, intercept.z)
            _remember_enemy_positions(self.enemy_positions, enemies)
            actions.append(MoveTo(x=intercept.x, z=intercept.z, radius=1, sprint=True))
            return actions

        # 3. Rescue jailed teammate if close
        jailed = tuple(t for t in obs.teammates if t.in_prison)
        nearest_jailed = _closest_player(pos, jailed)
        if nearest_jailed is not None and assigned_rescuer is not None and assigned_rescuer.name == me.name:
            self._announce(actions, "Rescue teammate at plate", gate.x, gate.z)
            actions.append(Chat(message=f"[TEAM] Rescue {nearest_jailed.name} at pressure plate ({gate.x}, {gate.z})"))
            _remember_enemy_positions(self.enemy_positions, enemies)
            actions.append(MoveTo(x=gate.x, z=gate.z, radius=PRESSURE_PLATE_RADIUS, sprint=True))
            return actions

        # 4. Capture enemy flag
        assignment = capture_assignments.get(me.name)
        if assignment is not None:
            flag, target = assignment
            gp = flag.grid_position
            gp = _offensive_route_target(pos, gp, enemies, obs.my_team)
            self._announce(actions, f"Assigned flag for plant ({target.grid_position.x}, {target.grid_position.z})", gp.x, gp.z)
            _remember_enemy_positions(self.enemy_positions, enemies)
            actions.append(MoveTo(x=gp.x, z=gp.z, radius=0, sprint=True))
            return actions

        capturable = _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions)
        if capturable:
            fallback_flag = _safest_flag(pos, capturable, enemies) or _closest_block(pos, capturable)
            if fallback_flag is not None:
                gp = fallback_flag.grid_position
                gp = _offensive_route_target(pos, gp, enemies, obs.my_team)
                self._announce(actions, "Attacking flag", gp.x, gp.z)
                _remember_enemy_positions(self.enemy_positions, enemies)
                actions.append(MoveTo(x=gp.x, z=gp.z, radius=0, sprint=True))
                return actions

        # 5. Patrol enemy territory
        if obs.my_team == "L":
            patrol_x = self.rng.randint(5, 18)
        else:
            patrol_x = self.rng.randint(-18, -5)
        patrol_z = self.rng.randint(-15, 15)
        patrol = _offensive_route_target(pos, GridPosition(x=patrol_x, z=patrol_z), enemies, obs.my_team)
        self._announce(actions, "Raiding", patrol.x, patrol.z)
        _remember_enemy_positions(self.enemy_positions, enemies)
        actions.append(MoveTo(x=patrol.x, z=patrol.z, radius=2, sprint=True))
        return actions

    def _announce(self, actions: list[MoveTo | Chat], intent: str, x: int, z: int) -> None:
        sig = (intent, x, z)
        if sig == self.last_intent:
            return
        now = time.monotonic()
        if now - self.last_chat_at >= self.chat_cooldown:
            actions.append(Chat(message=f"[ATK] {intent} -> ({x}, {z})"))
            self.last_chat_at = now
        self.last_intent = sig


# ---------------------------------------------------------------------------
# Defender Strategy — focuses on protecting own flags and intercepting
# ---------------------------------------------------------------------------

@dataclass
class DefenderStrategy:
    """Defensive strategy: guard flags, intercept carriers, rescue teammates.

    Priorities:
    1. If jailed → keep moving to the prison gate
    2. If carrying flag → sprint home to score
    3. Chase enemy flag carrier (highest defensive priority)
    4. Rescue jailed teammates
    5. Guard own flags / patrol own territory
    """

    chat_cooldown: float = 8.0
    last_intent: tuple[str, int, int] | None = None
    last_chat_at: float = 0.0
    patrol_index: int = 0
    rng: random.Random = field(default_factory=random.Random)
    enemy_positions: dict[str, GridPosition] = field(default_factory=dict)

    def on_game_start(self, obs: Observation) -> None:
        self.last_intent = None
        self.last_chat_at = 0.0
        self.patrol_index = 0
        self.enemy_positions.clear()

    def compute_next_action(self, obs: Observation) -> list[MoveTo | Chat]:
        actions: list[MoveTo | Chat] = []
        me = obs.self_player
        pos = obs.me.position
        enemies = obs.enemies
        my_targets = obs.my_targets
        gate = _prison_gate_for_team(obs.my_team)
        capture_assignments = _plan_capture_assignments(obs.myteam_players, _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions), my_targets, enemies)
        scoring_assignments = _plan_scoring_assignments(obs.myteam_players, my_targets, enemies)
        assigned_rescuer = _choose_rescue_player(obs.myteam_players, gate, enemies, obs.my_team)
        assigned_interceptor = _choose_team_interceptor(
            obs.myteam_players,
            obs,
            self.enemy_positions,
            require_home_intrusion=False,
            max_distance=FLAG_CARRIER_CHASE_RANGE,
        )
        assigned_home_interceptor = _choose_team_interceptor(
            obs.myteam_players,
            obs,
            self.enemy_positions,
            require_home_intrusion=True,
            max_distance=INTERCEPT_COMMIT_RANGE,
        )

        # 1. Jailed — keep pathing to the prison gate so rescue can happen faster
        if me.in_prison:
            self._announce(actions, "Need help at plate", gate.x, gate.z)
            _remember_enemy_positions(self.enemy_positions, enemies)
            actions.append(MoveTo(x=gate.x, z=gate.z, radius=PRESSURE_PLATE_RADIUS, sprint=True))
            return actions

        # 2. Carrying flag — rush home to score (even defenders score if they get a flag)
        if me.has_flag:
            target = scoring_assignments.get(me.name) or _closest_block(pos, my_targets)
            if target is not None:
                gp = target.grid_position
                safe = _evasive_target(pos, gp, enemies)
                self._announce(actions, "Scoring flag", safe.x, safe.z)
                _remember_enemy_positions(self.enemy_positions, enemies)
                actions.append(MoveTo(x=safe.x, z=safe.z, radius=0, sprint=True))
                return actions

        # 3. Chase enemy carrying our flag — top defensive priority
        if assigned_interceptor is not None and assigned_interceptor[0] == me.name:
            _, enemy_name, intercept = assigned_interceptor
            self._announce(actions, f"Intercepting {enemy_name}", intercept.x, intercept.z)
            _remember_enemy_positions(self.enemy_positions, enemies)
            actions.append(MoveTo(x=intercept.x, z=intercept.z, radius=1, sprint=True))
            return actions

        # 4. Rescue jailed teammates
        jailed = tuple(t for t in obs.teammates if t.in_prison)
        nearest_jailed = _closest_player(pos, jailed)
        if nearest_jailed is not None and assigned_rescuer is not None and assigned_rescuer.name == me.name:
            self._announce(actions, "Rescuing", gate.x, gate.z)
            actions.append(Chat(message=f"[TEAM] Rescue {nearest_jailed.name} at pressure plate ({gate.x}, {gate.z})"))
            _remember_enemy_positions(self.enemy_positions, enemies)
            actions.append(MoveTo(x=gate.x, z=gate.z, radius=PRESSURE_PLATE_RADIUS, sprint=True))
            return actions

        # 5. Intercept enemies in our territory
        if assigned_home_interceptor is not None and assigned_home_interceptor[0] == me.name:
            _, enemy_name, intercept = assigned_home_interceptor
            self._announce(actions, f"Protecting lane vs {enemy_name}", intercept.x, intercept.z)
            _remember_enemy_positions(self.enemy_positions, enemies)
            actions.append(MoveTo(x=intercept.x, z=intercept.z, radius=1, sprint=True))
            return actions

        # 6. Opportunistic flag capture if nearby and safe
        capturable = _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions)
        nearby_flag = _closest_block(pos, capturable)
        if nearby_flag is not None and _manhattan(pos, nearby_flag.grid_position) <= 12:
            if not _enemy_near(nearby_flag.grid_position, enemies, radius=6):
                gp = _offensive_route_target(pos, nearby_flag.grid_position, enemies, obs.my_team)
                self._announce(actions, "Quick grab", gp.x, gp.z)
                _remember_enemy_positions(self.enemy_positions, enemies)
                actions.append(MoveTo(x=gp.x, z=gp.z, radius=0, sprint=True))
                return actions

        assignment = capture_assignments.get(me.name)
        if assignment is not None:
            flag, target = assignment
            gp = flag.grid_position
            gp = _offensive_route_target(pos, gp, enemies, obs.my_team)
            self._announce(actions, f"Assigned flag for plant ({target.grid_position.x}, {target.grid_position.z})", gp.x, gp.z)
            _remember_enemy_positions(self.enemy_positions, enemies)
            actions.append(MoveTo(x=gp.x, z=gp.z, radius=0, sprint=True))
            return actions

        # 7. Patrol own territory near flags
        own_flags = obs.flags_to_protect
        if own_flags:
            # Cycle through flag positions for patrol
            idx = self.patrol_index % len(own_flags)
            flag = own_flags[idx]
            gp = flag.grid_position
            if _manhattan(pos, gp) <= 3:
                self.patrol_index += 1
                idx = self.patrol_index % len(own_flags)
                flag = own_flags[idx]
                gp = flag.grid_position
            self._announce(actions, "Guarding flag", gp.x, gp.z)
            _remember_enemy_positions(self.enemy_positions, enemies)
            actions.append(MoveTo(x=gp.x, z=gp.z, radius=2, sprint=True))
            return actions

        # 8. Fallback: patrol home territory
        if obs.my_team == "L":
            patrol_x = self.rng.randint(-20, -5)
        else:
            patrol_x = self.rng.randint(5, 20)
        patrol_z = self.rng.randint(-15, 15)
        self._announce(actions, "Patrolling", patrol_x, patrol_z)
        _remember_enemy_positions(self.enemy_positions, enemies)
        actions.append(MoveTo(x=patrol_x, z=patrol_z, radius=2, sprint=True))
        return actions

    def _announce(self, actions: list[MoveTo | Chat], intent: str, x: int, z: int) -> None:
        sig = (intent, x, z)
        if sig == self.last_intent:
            return
        now = time.monotonic()
        if now - self.last_chat_at >= self.chat_cooldown:
            actions.append(Chat(message=f"[DEF] {intent} -> ({x}, {z})"))
            self.last_chat_at = now
        self.last_intent = sig
