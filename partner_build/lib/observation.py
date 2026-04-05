from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType


def _load_shared_module() -> ModuleType:
    module_path = Path(__file__).resolve().parents[2] / "lib" / "observation.py"
    spec = spec_from_file_location("partner_shared_observation", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load shared observation module from {module_path}.")
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_shared_module = _load_shared_module()

__all__ = list(getattr(_shared_module, "__all__", ()))

for export_name in __all__:
    globals()[export_name] = getattr(_shared_module, export_name)