import platform


SYS = platform.system()  # Windows / Linux / Darwin
APP = "nwnmods"
STEAM_APPID_EE = "704450"
UA = "nwnmods/1.0 (personal mod manager)"

VERSION = "1.0"
REPO_URL = "https://github.com/soulh34rt/NITREWRITTEN"
AUTHOR = "ada_dev"


def about_text() -> str:
    """ASCII-only About text shared by the CLI 'about' command and the GUI About window."""
    return "\n".join([
        f"NITREWRITTEN {VERSION}",
        "The definitive Mod Manager for Neverwinter Nights",
        f"Made by {AUTHOR}",
        f"Repository: {REPO_URL}",
        "",
        "Third-party credits:",
        "- 7-Zip: archive extraction (LGPL). https://www.7-zip.org/",
        "  Full license text: THIRD_PARTY_LICENSES (from resources/tools/License.txt).",
        "- unRAR: optional .rar extraction via the unrar command-line tool (not bundled).",
        "- FFmpeg: optional BIK to WBM conversion (not bundled; install separately).",
        "- Neverwinter Vault: project catalog and images source. https://neverwintervault.org/",
        "- NIT (Neverwinter Nights Mod Installer Tool) by Surazal: the original tool",
        "  that inspired this project.",
        "  https://neverwintervault.org/project/nwn1/other/tool/neverwinter-nights-mod-installer-tool-nit",
        "",
        "Neverwinter Nights is a trademark of Wizards of the Coast. This project is",
        "not affiliated with Beamdog, Bioware or Wizards of the Coast.",
    ])

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


def group_conflicts(report: dict) -> dict:
    """Group a conflicts_report (file -> mods) per mod: mod -> {other mod: [shared files]}."""
    out = {}
    for f, ns in report.items():
        mods = set(ns)
        for n in mods:
            bucket = out.setdefault(n, {})
            for other in mods:
                if other != n:
                    bucket.setdefault(other, set()).add(f)
    return {n: {o: sorted(fs) for o, fs in b.items()} for n, b in out.items()}


class Err(Exception):
    pass


MAX_ENTRIES = 10000
MAX_FILE_BYTES = 512 * 1024 * 1024  # 512 MiB per file
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB per archive
MAX_NAME_LEN = 255