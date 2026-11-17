"""Public API for the NITREWRITTEN mod manager."""

from pathlib import Path

from . import config as _config
from . import files as _files
from ._common import (APP, Err, MAX_ENTRIES, MAX_FILE_BYTES, MAX_NAME_LEN, MAX_TOTAL_BYTES, STATE_COLORS,
                      STATE_LABELS, STATE_SYMBOLS, STEAM_APPID_EE, SYS, UA, conflict_partners, mod_state)
from .cli import cli, main, pick
from .detection import (Game, classify, detect_all, documents_dir, ee_user_dir, launch, open_path, other_bases,
                        safe_dirs, steam_libraries, steam_workshop_items)
from .files import (CONTENT_DIRS, DEST, IGNORE_EXT, IGNORE_NAMES, KNOWN, LOWER_DIRS, _inside, _safe_filename,
                    _safe_member_name, _strip_symlinks, download, extract, fetch_bytes, map_path)
from .gui import run_gui
from .library import Library
from .vault import (FIELD_LABELS, VAULT, VAULT_GAMES, VAULT_TTL, _Blocks, _CatalogParser, http_get,
                    parse_catalog, parse_project, vault_catalog)


CFG = _config.CFG


def cfg_dir():
    return _config.cfg_dir()


def load_cfg():
    original = _config.CFG
    _config.CFG = Path(CFG)
    try:
        return _config.load_cfg()
    finally:
        _config.CFG = original


def save_cfg(c):
    original = _config.CFG
    _config.CFG = Path(CFG)
    try:
        return _config.save_cfg(c)
    finally:
        _config.CFG = original


def _sync_file_limits():
    _files.MAX_ENTRIES = MAX_ENTRIES
    _files.MAX_FILE_BYTES = MAX_FILE_BYTES
    _files.MAX_NAME_LEN = MAX_NAME_LEN
    _files.MAX_TOTAL_BYTES = MAX_TOTAL_BYTES


def extract(src, out):
    original = (_files.MAX_ENTRIES, _files.MAX_FILE_BYTES, _files.MAX_NAME_LEN, _files.MAX_TOTAL_BYTES)
    _sync_file_limits()
    try:
        return _files.extract(src, out)
    finally:
        (_files.MAX_ENTRIES, _files.MAX_FILE_BYTES, _files.MAX_NAME_LEN, _files.MAX_TOTAL_BYTES) = original


def _check_total(total):
    original = (_files.MAX_ENTRIES, _files.MAX_FILE_BYTES, _files.MAX_NAME_LEN, _files.MAX_TOTAL_BYTES)
    _sync_file_limits()
    try:
        return _files._check_total(total)
    finally:
        (_files.MAX_ENTRIES, _files.MAX_FILE_BYTES, _files.MAX_NAME_LEN, _files.MAX_TOTAL_BYTES) = original


def _inside(out, name):
    return _files._inside(out, name)


def _safe_member_name(name):
    original = (_files.MAX_ENTRIES, _files.MAX_FILE_BYTES, _files.MAX_NAME_LEN, _files.MAX_TOTAL_BYTES)
    _sync_file_limits()
    try:
        return _files._safe_member_name(name)
    finally:
        (_files.MAX_ENTRIES, _files.MAX_FILE_BYTES, _files.MAX_NAME_LEN, _files.MAX_TOTAL_BYTES) = original


def download(url, dest_dir, referer=None, fallback=None, max_bytes=None):
    original = (_files.MAX_ENTRIES, _files.MAX_FILE_BYTES, _files.MAX_NAME_LEN, _files.MAX_TOTAL_BYTES)
    _sync_file_limits()
    if max_bytes is None:
        max_bytes = MAX_TOTAL_BYTES
    try:
        return _files.download(url, dest_dir, referer, fallback, max_bytes)
    finally:
        (_files.MAX_ENTRIES, _files.MAX_FILE_BYTES, _files.MAX_NAME_LEN, _files.MAX_TOTAL_BYTES) = original


def fetch_bytes(url, timeout=30, max_bytes=None):
    original = (_files.MAX_ENTRIES, _files.MAX_FILE_BYTES, _files.MAX_NAME_LEN, _files.MAX_TOTAL_BYTES)
    _sync_file_limits()
    if max_bytes is None:
        max_bytes = MAX_TOTAL_BYTES
    try:
        return _files.fetch_bytes(url, timeout, max_bytes)
    finally:
        (_files.MAX_ENTRIES, _files.MAX_FILE_BYTES, _files.MAX_NAME_LEN, _files.MAX_TOTAL_BYTES) = original


def http_get(url, timeout=40):
    original = (_files.MAX_ENTRIES, _files.MAX_FILE_BYTES, _files.MAX_NAME_LEN, _files.MAX_TOTAL_BYTES)
    _sync_file_limits()
    from .vault import http_get as _http_get
    try:
        return _http_get(url, timeout)
    finally:
        (_files.MAX_ENTRIES, _files.MAX_FILE_BYTES, _files.MAX_NAME_LEN, _files.MAX_TOTAL_BYTES) = original


def vault_catalog(game, force=False):
    original = _config.CFG
    _config.CFG = Path(CFG)
    from .vault import vault_catalog as _vault_catalog
    try:
        return _vault_catalog(game, force)
    finally:
        _config.CFG = original


__all__ = [name for name in globals() if not name.startswith("_")]
__all__ += ["_inside", "_safe_filename", "_safe_member_name", "_strip_symlinks", "_check_total",
            "_Blocks", "_CatalogParser"]