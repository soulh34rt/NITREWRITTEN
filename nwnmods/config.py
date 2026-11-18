import json
import os
import sys
from pathlib import Path

from ._common import APP, SYS


def cfg_dir() -> Path:
    if SYS == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    elif SYS == "Darwin":
        base = Path.home() / "Library/Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP


CFG = cfg_dir() / "config.json"


def load_cfg() -> dict:
    d = {"manual": [], "lowercase": True, "policy": "ask", "last": None, "user_override": {}, "ffmpeg_path": None, "convert_bik": True, "ext_map": None, "ignore_ext": None, "ignore_names": None, "folder_aliases": {}, "installations": {}, "basic_mode": False, "splash_enabled": False, "splash_image": None, "splash_ms": 2000, "vault_cache_mb": 200, "vault_view": "list"}
    try:
        if CFG.exists():
            d.update(json.loads(CFG.read_text(encoding="utf-8")))
    except Exception as e:  # noqa: BLE001
        sys.stderr.write(f"nwnmods: could not read config {CFG}: {e!r}\n")
    return d


def save_cfg(c: dict):
    CFG.parent.mkdir(parents=True, exist_ok=True)
    tmp = CFG.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CFG)
