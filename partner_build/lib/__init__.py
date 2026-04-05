from __future__ import annotations

from pathlib import Path
from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)

_shared_lib_dir = Path(__file__).resolve().parents[2] / "lib"
if _shared_lib_dir.is_dir():
    shared_lib_path = str(_shared_lib_dir)
    if shared_lib_path not in __path__:
        __path__.append(shared_lib_path)