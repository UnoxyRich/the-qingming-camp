from __future__ import annotations

from dataclasses import dataclass

from lib.actions import MoveTo
from lib.observation import Observation


@dataclass
class AfkStrategy:
    def on_game_start(self, obs: Observation) -> None:
        return None

    def compute_next_action(self, obs: Observation) -> list[MoveTo]:
        position = obs.self_player.position
        return [MoveTo(x=position.x, z=position.z, radius=0, sprint=False, jump=False)]


__all__ = ["AfkStrategy"]