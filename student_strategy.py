from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

from lib.actions import Chat, MoveTo
from lib.observation import BlockState, GridPosition, Observation

MIDFIELD_X = 0
MIDFIELD_Z = 0

@dataclass
class RandomWalkStrategy:
    """CTF strategy used by default entrypoint in this repository.

    Kept under the historical class name for CLI compatibility.
    """

    attack_radius: int = 0
    defend_radius: int = 1
    rescue_radius: int = 1
    chat_cooldown_seconds: float = 8.0
    last_intent: tuple[str, int, int] | None = None
    last_chat_at: float = 0.0
    rng: random.Random = field(default_factory=random.Random)

    def on_game_start(self, obs: Observation) -> None:
        self.last_intent = None
        self.last_chat_at = 0.0

    def compute_next_action(self, obs: Observation) -> list[MoveTo | Chat]:
        actions: list[MoveTo | Chat] = []
        me = obs.self_player

        # If we are jailed, stay predictable so teammates can free us quickly.
        if me.in_prison:
            stay = obs.me.position
            self._maybe_announce(actions, "Jailed, waiting for rescue", stay.x, stay.z)
            actions.append(MoveTo(x=stay.x, z=stay.z, radius=0, sprint=False))
            return actions

        # Highest priority: score when carrying enemy flag.
        if me.has_flag:
            home_target = _pick_closest_block(obs.me.position, obs.my_targets)
            if home_target is not None:
                gp = home_target.grid_position
                self._maybe_announce(actions, "Returning with flag", gp.x, gp.z)
                actions.append(MoveTo(x=gp.x, z=gp.z, radius=self.attack_radius, sprint=True))
                return actions

        # Rescue nearby teammate in prison when practical.
        jailed_teammate = _pick_closest_player(
            origin=obs.me.position,
            players=tuple(player for player in obs.teammates if player.in_prison),
        )
        if jailed_teammate is not None and _manhattan_distance(obs.me.position, jailed_teammate.position) <= 20:
            self._maybe_announce(actions, "Rescuing teammate", jailed_teammate.position.x, jailed_teammate.position.z)
            actions.append(
                MoveTo(
                    x=jailed_teammate.position.x,
                    z=jailed_teammate.position.z,
                    radius=self.rescue_radius,
                    sprint=True,
                )
            )
            return actions

        # Default offense: go for enemy flag that is still not placed on a scoring pad.
        capture_candidates = _unplaced_flags(obs.flags_to_capture, obs.gold_block_positions)
        enemy_flag = _pick_closest_block(obs.me.position, capture_candidates)
        if enemy_flag is not None:
            gp = enemy_flag.grid_position
            self._maybe_announce(actions, "Capturing enemy flag", gp.x, gp.z)
            actions.append(MoveTo(x=gp.x, z=gp.z, radius=self.attack_radius, sprint=True))
            return actions

        # Fallback: patrol midfield to stay active and intercept.
        patrol_x = MIDFIELD_X + self.rng.choice((-3, -1, 1, 3))
        patrol_z = MIDFIELD_Z + self.rng.choice((-5, -2, 2, 5))
        self._maybe_announce(actions, "Patrolling midfield", patrol_x, patrol_z)
        actions.append(MoveTo(x=patrol_x, z=patrol_z, radius=self.defend_radius, sprint=True))
        return actions

    def _maybe_announce(self, actions: list[MoveTo | Chat], intent: str, x: int, z: int) -> None:
        signature = (intent, x, z)
        if signature == self.last_intent:
            return
        now = time.monotonic()
        if now - self.last_chat_at >= self.chat_cooldown_seconds:
            actions.append(Chat(message=f"{intent} -> ({x}, {z})"))
            self.last_chat_at = now
        self.last_intent = signature


def _is_near(x: int, z: int, target: tuple[int, int], threshold: int = 2) -> bool:
    return abs(x - target[0]) <= threshold and abs(z - target[1]) <= threshold


def _pick_closest_block(
    origin: GridPosition,
    blocks: tuple[BlockState, ...],
) -> BlockState | None:
    if not blocks:
        return None
    return min(
        blocks,
        key=lambda block: (
            _manhattan_distance(origin, block.grid_position),
            block.grid_position.x,
            block.grid_position.z,
        ),
    )


def _pick_closest_player(origin: GridPosition, players) -> object | None:
    players = tuple(players)
    if not players:
        return None
    return min(
        players,
        key=lambda player: (
            _manhattan_distance(origin, player.position),
            player.position.x,
            player.position.z,
        ),
    )


def _manhattan_distance(left: GridPosition, right: GridPosition) -> int:
    return abs(left.x - right.x) + abs(left.z - right.z)


def _unplaced_flags(
    flags: tuple[BlockState, ...],
    gold_block_positions: tuple[GridPosition, ...],
) -> tuple[BlockState, ...]:
    gold_positions = {(position.x, position.z) for position in gold_block_positions}
    return tuple(
        flag
        for flag in flags
        if (flag.grid_position.x, flag.grid_position.z) not in gold_positions
    )
