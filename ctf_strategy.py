from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from lib.actions import Chat, MoveTo
from lib.observation import BlockState, GridPosition, Observation, PlayerState

# Map layout constants
MIDFIELD_X = 0
L_TERRITORY_X = -12  # Deep in left territory
R_TERRITORY_X = 12   # Deep in right territory
PRISON_GATE_L = (-16, 24)
PRISON_GATE_R = (16, 24)
PRISON_OUTER_PLATE_L = (-16, 24)
PRISON_OUTER_PLATE_R = (16, 24)
PRISON_INNER_PLATE_L = (-16, 28)
PRISON_INNER_PLATE_R = (16, 28)
PRISON_INNER_STAGING_L = (-16, 27)
PRISON_INNER_STAGING_R = (16, 27)
PRISON_ESCAPE_L = (-16, 23)
PRISON_ESCAPE_R = (16, 23)

# Danger zone: how close an enemy must be to be considered a threat
ENEMY_DANGER_RADIUS = 8
RESCUE_RANGE = 25
FLAG_CARRIER_CHASE_RANGE = 30


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


def _prison_gate_for_team(team: str) -> GridPosition:
    if team == "L":
        return GridPosition(x=PRISON_GATE_L[0], z=PRISON_GATE_L[1])
    return GridPosition(x=PRISON_GATE_R[0], z=PRISON_GATE_R[1])


def _prison_outer_plate_for_team(team: str) -> GridPosition:
    if team == "L":
        return GridPosition(x=PRISON_OUTER_PLATE_L[0], z=PRISON_OUTER_PLATE_L[1])
    return GridPosition(x=PRISON_OUTER_PLATE_R[0], z=PRISON_OUTER_PLATE_R[1])


def _prison_inner_plate_for_team(team: str) -> GridPosition:
    if team == "L":
        return GridPosition(x=PRISON_INNER_PLATE_L[0], z=PRISON_INNER_PLATE_L[1])
    return GridPosition(x=PRISON_INNER_PLATE_R[0], z=PRISON_INNER_PLATE_R[1])


def _prison_inner_staging_for_team(team: str) -> GridPosition:
    if team == "L":
        return GridPosition(x=PRISON_INNER_STAGING_L[0], z=PRISON_INNER_STAGING_L[1])
    return GridPosition(x=PRISON_INNER_STAGING_R[0], z=PRISON_INNER_STAGING_R[1])


def _prison_escape_for_team(team: str) -> GridPosition:
    if team == "L":
        return GridPosition(x=PRISON_ESCAPE_L[0], z=PRISON_ESCAPE_L[1])
    return GridPosition(x=PRISON_ESCAPE_R[0], z=PRISON_ESCAPE_R[1])


def _prisoner_release_target(position: GridPosition, team: str) -> tuple[GridPosition, int, str]:
    inner_plate = _prison_inner_plate_for_team(team)
    inner_staging = _prison_inner_staging_for_team(team)
    escape = _prison_escape_for_team(team)
    if _manhattan(position, inner_staging) <= 1 or _manhattan(position, inner_plate) <= 1:
        return escape, 0, "Exit prison"
    return inner_plate, 0, "Move to release plate"


def _is_home_territory(position: GridPosition, team: str) -> bool:
    if team == "L":
        return position.x <= MIDFIELD_X
    return position.x >= MIDFIELD_X


def _is_enemy_territory(position: GridPosition, team: str) -> bool:
    return not _is_home_territory(position, team)


def _home_reentry_target(position: GridPosition, team: str) -> GridPosition:
    if team == "L":
        target_x = max(L_TERRITORY_X, -6)
    else:
        target_x = min(R_TERRITORY_X, 6)
    target_z = max(-20, min(20, position.z))
    return GridPosition(x=target_x, z=target_z)


# ---------------------------------------------------------------------------
# Attacker Strategy — focuses on capturing enemy flags and scoring
# ---------------------------------------------------------------------------

@dataclass
class AttackerStrategy:
    """Aggressive flag-capturing strategy.

    Priorities:
    1. If jailed → move to prison gate for faster rescue
    2. If carrying flag → sprint home to score (with evasion)
    3. Rescue nearby jailed teammate (opportunistic)
    4. Capture enemy flag (prefer safest one away from enemies)
    5. Patrol enemy territory looking for opportunities
    """

    chat_cooldown: float = 8.0
    last_intent: tuple[str, int, int] | None = None
    last_chat_at: float = 0.0
    rng: random.Random = field(default_factory=random.Random)

    def on_game_start(self, obs: Observation) -> None:
        self.last_intent = None
        self.last_chat_at = 0.0

    def compute_next_action(self, obs: Observation) -> list[MoveTo | Chat]:
        actions: list[MoveTo | Chat] = []
        me = obs.self_player
        pos = obs.me.position
        enemies = obs.enemies
        on_home_side = _is_home_territory(pos, obs.my_team)

        # 1. Jailed — move to the prison gate so a teammate can free us quickly
        if me.in_prison:
            target, radius, intent = _prisoner_release_target(pos, obs.my_team)
            self._announce(actions, intent, target.x, target.z)
            actions.append(MoveTo(x=target.x, z=target.z, radius=radius, sprint=True))
            return actions

        # 2. Carrying flag — rush home to score
        if me.has_flag:
            target = _closest_block(pos, obs.my_targets)
            if target is not None:
                gp = target.grid_position
                safe = _evasive_target(pos, gp, enemies)
                self._announce(actions, "Scoring flag", safe.x, safe.z)
                actions.append(MoveTo(x=safe.x, z=safe.z, radius=0, sprint=True))
                return actions

        # 3. Rescue jailed teammate aggressively in 2v2 so teams do not deadlock
        jailed = tuple(t for t in obs.teammates if t.in_prison)
        nearest_jailed = _closest_player(pos, jailed)
        if nearest_jailed is not None:
            plate = _prison_outer_plate_for_team(obs.my_team)
            self._announce(actions, "Quick rescue", plate.x, plate.z)
            actions.append(MoveTo(x=plate.x, z=plate.z, radius=0, sprint=True))
            return actions

        # 4. Capture enemy flag
        capturable = _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions)
        if capturable:
            flag = _safest_flag(pos, capturable, enemies)
            if flag is None:
                flag = _closest_block(pos, capturable)
            if flag is not None:
                gp = flag.grid_position
                # If enemies are near the flag, try approaching from a different angle
                if _enemy_near(gp, enemies, radius=5):
                    gp = _evasive_target(pos, gp, enemies)
                self._announce(actions, "Attacking flag", gp.x, gp.z)
                actions.append(MoveTo(x=gp.x, z=gp.z, radius=0, sprint=True))
                return actions

        # 5. Patrol enemy territory
        if obs.my_team == "L":
            patrol_x = self.rng.randint(5, 18)
        else:
            patrol_x = self.rng.randint(-18, -5)
        patrol_z = self.rng.randint(-15, 15)
        self._announce(actions, "Raiding", patrol_x, patrol_z)
        actions.append(MoveTo(x=patrol_x, z=patrol_z, radius=2, sprint=True))
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
    1. If jailed → move to prison gate for faster rescue
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

    def on_game_start(self, obs: Observation) -> None:
        self.last_intent = None
        self.last_chat_at = 0.0
        self.patrol_index = 0

    def compute_next_action(self, obs: Observation) -> list[MoveTo | Chat]:
        actions: list[MoveTo | Chat] = []
        me = obs.self_player
        pos = obs.me.position
        enemies = obs.enemies
        on_home_side = _is_home_territory(pos, obs.my_team)

        # 1. Jailed — move to the prison gate so a teammate can free us quickly
        if me.in_prison:
            target, radius, intent = _prisoner_release_target(pos, obs.my_team)
            self._announce(actions, intent, target.x, target.z)
            actions.append(MoveTo(x=target.x, z=target.z, radius=radius, sprint=True))
            return actions

        # 2. Carrying flag — rush home to score (even defenders score if they get a flag)
        if me.has_flag:
            target = _closest_block(pos, obs.my_targets)
            if target is not None:
                gp = target.grid_position
                safe = _evasive_target(pos, gp, enemies)
                self._announce(actions, "Scoring flag", safe.x, safe.z)
                actions.append(MoveTo(x=safe.x, z=safe.z, radius=0, sprint=True))
                return actions

        # 3. Chase enemy carrying our flag — top defensive priority
        enemy_carriers = tuple(e for e in enemies if e.has_flag and not e.in_prison)
        if enemy_carriers:
            carrier = _closest_player(pos, enemy_carriers)
            if carrier is not None and _is_home_territory(carrier.position, obs.my_team):
                if not on_home_side:
                    home_target = _home_reentry_target(pos, obs.my_team)
                    self._announce(actions, "Return home", home_target.x, home_target.z)
                    actions.append(MoveTo(x=home_target.x, z=home_target.z, radius=1, sprint=True))
                    return actions
                if _manhattan(pos, carrier.position) <= FLAG_CARRIER_CHASE_RANGE:
                    self._announce(actions, "Chasing carrier", carrier.position.x, carrier.position.z)
                    actions.append(MoveTo(x=carrier.position.x, z=carrier.position.z, radius=0, sprint=True))
                    return actions

        # 4. Rescue jailed teammates aggressively in 2v2 so teams do not deadlock
        jailed = tuple(t for t in obs.teammates if t.in_prison)
        nearest_jailed = _closest_player(pos, jailed)
        if nearest_jailed is not None:
            plate = _prison_outer_plate_for_team(obs.my_team)
            self._announce(actions, "Rescuing", plate.x, plate.z)
            actions.append(MoveTo(x=plate.x, z=plate.z, radius=0, sprint=True))
            return actions

        # 5. Intercept enemies in our territory
        intruders = tuple(
            e for e in enemies
            if not e.in_prison and _is_home_territory(e.position, obs.my_team)
        )
        nearest_intruder = _closest_player(pos, intruders)
        if nearest_intruder is not None:
            if not on_home_side:
                home_target = _home_reentry_target(pos, obs.my_team)
                self._announce(actions, "Return home", home_target.x, home_target.z)
                actions.append(MoveTo(x=home_target.x, z=home_target.z, radius=1, sprint=True))
                return actions
            if _manhattan(pos, nearest_intruder.position) <= 20:
                self._announce(actions, "Intercepting", nearest_intruder.position.x, nearest_intruder.position.z)
                actions.append(MoveTo(x=nearest_intruder.position.x, z=nearest_intruder.position.z, radius=0, sprint=True))
                return actions

        # 6. Opportunistic flag capture if nearby and safe
        capturable = _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions)
        nearby_flag = _closest_block(pos, capturable)
        if nearby_flag is not None and _manhattan(pos, nearby_flag.grid_position) <= 12:
            if not _enemy_near(nearby_flag.grid_position, enemies, radius=6):
                gp = nearby_flag.grid_position
                self._announce(actions, "Quick grab", gp.x, gp.z)
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
            actions.append(MoveTo(x=gp.x, z=gp.z, radius=2, sprint=True))
            return actions

        # 8. Fallback: patrol home territory
        if obs.my_team == "L":
            patrol_x = self.rng.randint(-20, -5)
        else:
            patrol_x = self.rng.randint(5, 20)
        patrol_z = self.rng.randint(-15, 15)
        self._announce(actions, "Patrolling", patrol_x, patrol_z)
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
