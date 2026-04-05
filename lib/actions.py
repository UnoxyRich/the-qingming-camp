from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias


@dataclass(frozen=True, slots=True)
class MoveTo:
    x: int
    z: int
    radius: int = 1
    sprint: bool = True
    jump: bool = False
    avoid_entities: bool = False


@dataclass(frozen=True, slots=True)
class Chat:
    message: str


@dataclass(frozen=True, slots=True)
class DashTo:
    x: int
    z: int
    radius: int = 1
    sprint: bool = True
    jump: bool = True


@dataclass(frozen=True, slots=True)
class Teleport:
    x: float
    y: float
    z: float


Action: TypeAlias = MoveTo | Chat | DashTo | Teleport


__all__ = ["Action", "Chat", "DashTo", "MoveTo", "Teleport"]
