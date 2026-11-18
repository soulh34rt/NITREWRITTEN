# Gradual Module Split for NITREWRITTEN

The backend, CLI and GUI now live in the `nwnmods` package. The top-level
`nwnmods.py` is a thin entry-point wrapper around `nwnmods.cli.main`.

## Modules

1. `nwnmods/config.py` - `cfg_dir`, `load_cfg`, `save_cfg`, `CFG`.
2. `nwnmods/detection.py` - `Game`, `detect_all`, `steam_libraries`, `other_bases`, `classify`, `launch`, `open_path`.
3. `nwnmods/files.py` - `KNOWN`, `map_path`, `extract`, `_inside`, `_safe_member_name`, limits, `download`, `fetch_bytes`.
4. `nwnmods/vault.py` - `_CatalogParser`, `parse_catalog`, `vault_catalog`, `_Blocks`, `parse_project`, `http_get`.
5. `nwnmods/library.py` - `Library`, `Err`.
6. `nwnmods/gui.py` - full tkinter GUI (self-contained, no legacy bridge).
7. `nwnmods/cli.py` - `pick`, `cli`, `main`.
8. `nwnmods/__main__.py` - dispatch to `cli.main`; `nwnmods.py` delegates to the same entry point.

## Migration status

- Complete: all logic lives in the `nwnmods` package; `nwnmods.py` is a wrapper of <= 30 lines.
- Complete: `nwnmods.__init__` re-exports the existing API, including `Err`, and bridges mutable `CFG` and safety limits used by callers/tests.
- Complete: `python nwnmods.py` and `python -m nwnmods` dispatch to the package CLI.
- Complete: GUI implementation moved into `nwnmods/gui.py` (lazy tkinter import keeps the CLI headless).
- Complete: PyInstaller builds use `nwnmods.py` as entry point and bundle the package automatically.
- Complete: Pre-extraction size validation for external 7z/rar tools (via `7z l` / `unrar l` listing).
- Complete: Vault catalog parsing tests and GUI smoke tests added to test suite.

## Rules

- No behavior changes during a split; only move code.
- Keep `Err` importable from `nwnmods` (tests rely on `nwnmods.Err`).
- The package and script entrypoints are covered by tests, including a CLI install/list/remove round trip in a temporary data folder.
- Avoid circular imports: lower-level modules must not import gui/cli.
