# Gradual Module Split for NITREWRITTEN

The backend and CLI now live in the `nwnmods` package, while the top-level script
continues to preserve the existing invocation while package imports use the new modules.

## Modules

1. `nwnmods/config.py` — `cfg_dir`, `load_cfg`, `save_cfg`, `CFG`.
2. `nwnmods/detection.py` — `Game`, `detect_all`, `steam_libraries`, `other_bases`, `classify`, `launch`, `open_path`.
3. `nwnmods/files.py` — `KNOWN`, `map_path`, `extract`, `_inside`, `_safe_member_name`, limits, `download`, `fetch_bytes`.
4. `nwnmods/vault.py` — `_CatalogParser`, `parse_catalog`, `vault_catalog`, `_Blocks`, `parse_project`, `http_get`.
5. `nwnmods/library.py` — `Library`, `Err`.
6. `nwnmods/gui.py` — lazy compatibility bridge to the existing tkinter app.
7. `nwnmods/cli.py` — `pick`, `cli`.
8. `nwnmods/__main__.py` — dispatch to cli/gui; the top-level script dispatches to the same CLI.

## Migration status

- Complete: `files.py`, `config.py`, `detection.py`, `library.py`, `vault.py`, and `cli.py` are package modules.
- Complete: `nwnmods.__init__` re-exports the existing API, including `Err`, and bridges mutable `CFG` and safety limits used by callers/tests.
- Complete: `python nwnmods.py` and `python -m nwnmods` dispatch to the package CLI.
- Complete: Pre-extraction size validation for external 7z/rar tools (via `7z l` / `unrar l` listing).
- Complete: Vault catalog parsing tests and GUI smoke tests added to test suite (73 tests total).
- Remaining: move the tkinter implementation into `nwnmods/gui.py`, update its resource path, and remove the legacy implementation from `nwnmods.py`; for now the GUI bridge lazily loads that legacy implementation.

## Rules

- No behavior changes during a split; only move code.
- Keep `Err` importable from `nwnmods` (tests rely on `nwnmods.Err`).
- The package and script entrypoints are covered by tests, including a CLI install/list/remove round trip in a temporary data folder.
- Avoid circular imports: lower-level modules must not import gui/cli.
