from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType


def _load_shared_module() -> ModuleType:
    module_path = Path(__file__).resolve().parents[2] / "lib" / "actions.py"
    spec = spec_from_file_location("partner_shared_actions", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load shared actions module from {module_path}.")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_shared_module = _load_shared_module()

Action = _shared_module.Action
Chat = _shared_module.Chat
MoveTo = _shared_module.MoveTo

__all__ = ["Action", "Chat", "MoveTo"]