"""GUI entry point retained as a lazy bridge while the UI is split out."""

import importlib.util
import sys
from pathlib import Path


_legacy = None


def run_gui(initial_user=None):
    global _legacy
    if _legacy is None:
        legacy_path = Path(__file__).resolve().parent.parent / "nwnmods.py"
        spec = importlib.util.spec_from_file_location("_nwnmods_legacy_gui", legacy_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("Could not load the existing graphical interface.")
        _legacy = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = _legacy
        spec.loader.exec_module(_legacy)
    return _legacy.run_gui(initial_user)