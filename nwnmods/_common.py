import platform


SYS = platform.system()  # Windows / Linux / Darwin
APP = "nwnmods"
STEAM_APPID_EE = "704450"
UA = "nwnmods/1.0 (personal mod manager)"

# Mod list states: single source of truth for the GUI (both themes).
# Each entry is (foreground, background), WCAG contrast >= 4.5:1 (tested).
STATE_SYMBOLS = {"active": "\u25cf", "disabled": "\u25cb", "conflict": "\u25b2"}  # \u25cf \u25cb \u25b2
STATE_LABELS = {"active": "Active", "disabled": "Disabled", "conflict": "Conflict"}
STATE_COLORS = {
    "dark": {"active": ("#4CAF50", "#1E2F22"), "disabled": ("#8A8A8A", "#121212"),
             "conflict": ("#E6C84A", "#2F2A12")},
    "light": {"active": ("#2E7D32", "#FFFFFF"), "disabled": ("#000000", "#ECECEC"),
              "conflict": ("#8A6D00", "#FFF8E1")},
}


def mod_state(enabled: bool, conflict: bool) -> str:
    """State key for a mod row: disabled beats conflict (inactive mods never conflict)."""
    if not enabled:
        return "disabled"
    return "conflict" if conflict else "active"


def conflict_partners(report: dict) -> dict:
    """Map each active mod to the set of active mods it shares files with."""
    partners = {}
    for ns in report.values():
        for n in ns:
            partners.setdefault(n, set()).update(x for x in ns if x != n)
    return partners


class Err(Exception):
    pass


MAX_ENTRIES = 10000
MAX_FILE_BYTES = 512 * 1024 * 1024  # 512 MiB per file
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB per archive
MAX_NAME_LEN = 255