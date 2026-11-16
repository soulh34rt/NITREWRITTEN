import os
import re
import subprocess
from pathlib import Path

from ._common import Err, STEAM_APPID_EE, SYS
from .config import load_cfg


def documents_dir() -> Path:
    if SYS == "Windows":
        try:
            import winreg
        except ImportError:
            winreg = None
        if winreg:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                    return Path(os.path.expandvars(winreg.QueryValueEx(k, "Personal")[0]))
            except Exception:
                pass
    return Path.home() / "Documents"


def ee_user_dir() -> Path:
    env = os.environ.get("NWN_HOME")
    if env:
        return Path(env)
    if SYS == "Linux":
        xdg = os.environ.get("XDG_DATA_HOME")
        return (Path(xdg) if xdg else Path.home() / ".local/share") / "Neverwinter Nights"
    return documents_dir() / "Neverwinter Nights"


def safe_dirs(p: Path):
    try:
        return [c for c in sorted(p.iterdir()) if c.is_dir()]
    except OSError:
        return []


def classify(p: Path):
    """'EE', 'Diamond' or None based on folder contents."""
    try:
        if (p / "data/nwn_base.key").exists() or any(p.glob("bin/*/nwmain*")):
            return "EE"
        if (p / "chitin.key").exists() and ((p / "nwmain.exe").exists() or (p / "nwn.exe").exists()):
            return "Diamond"
    except OSError:
        pass
    return None


class Game:
    def __init__(self, root, edition, store, user=None):
        self.root = Path(root) if root else None
        self.edition = edition
        self.store = store
        self.user = Path(user) if user else self._default_user()

    def _default_user(self) -> Path:
        if self.edition == "Diamond" and self.root:
            return self.root
        if SYS == "Linux" and self.root and "com.valvesoftware.Steam" in str(self.root):
            fp = Path.home() / ".var/app/com.valvesoftware.Steam"
            cands = [fp / "data/Neverwinter Nights", fp / ".local/share/Neverwinter Nights"]
            return next((c for c in cands if c.exists()), cands[0])
        return ee_user_dir()

    @property
    def label(self) -> str:
        where = str(self.root) if self.root else "(user folder only)"
        return f"{self.edition} · {self.store} · {where}"


def steam_libraries():
    roots = []
    h = Path.home()
    if SYS == "Windows":
        try:
            import winreg
            for hive, key, val in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                   (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath")):
                try:
                    with winreg.OpenKey(hive, key) as k:
                        roots.append(Path(winreg.QueryValueEx(k, val)[0]))
                except OSError:
                    pass
        except ImportError:
            pass
        for v in ("ProgramFiles(x86)", "ProgramFiles"):
            if os.environ.get(v):
                roots.append(Path(os.environ[v]) / "Steam")
    elif SYS == "Darwin":
        roots.append(h / "Library/Application Support/Steam")
    else:
        roots += [h / ".steam/steam", h / ".steam/root", h / ".local/share/Steam",
                  h / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
                  h / ".var/app/com.valvesoftware.Steam/data/Steam",
                  h / "snap/steam/common/.local/share/Steam"]
    libs, seen = [], set()

    def add(p: Path):
        try:
            rp = p.resolve()
        except OSError:
            rp = p
        if rp not in seen and p.exists():
            seen.add(rp)
            libs.append(p)

    for r in roots:
        add(r)
        vdf = r / "steamapps/libraryfolders.vdf"
        if vdf.exists():
            try:
                txt = vdf.read_text(errors="ignore")
            except OSError:
                continue
            for m in re.finditer(r'"path"\s+"([^"]+)"', txt):
                add(Path(m.group(1).replace("\\\\", "\\")))
    return libs


def other_bases():
    """(store, root folder, search depth)."""
    h = Path.home()
    pfs = [Path(os.environ[v]) for v in ("ProgramFiles", "ProgramFiles(x86)") if os.environ.get(v)]
    drives = [Path(f"{c}:/") for c in "CDEFGH" if Path(f"{c}:/").exists()] if SYS == "Windows" else []
    out = []
    out += [("GOG", d / "GOG Games", 1) for d in drives]
    out += [("GOG", p / "GOG Galaxy/Games", 1) for p in pfs]
    out += [("GOG", h / "GOG Games", 1), ("GOG", h / "Games", 2), ("GOG", h / "Games/gog", 1)]
    out += [("Beamdog", d / "Beamdog Library", 1) for d in drives]
    out += [("Beamdog", h / "Beamdog Library", 1), ("Beamdog", documents_dir() / "Beamdog Library", 1)]
    out += [("Beamdog", p / "Beamdog", 2) for p in pfs]
    out += [("Origin/EA", p / n, 1) for p in pfs for n in ("Origin Games", "EA Games")]
    out += [("Origin/EA", d / "Origin Games", 1) for d in drives]
    for p in pfs:
        for n in ("Atari", "Infogrames", "BioWare"):
            out.append(("Disco/Diamond", p / n, 1))
        out.append(("Disco/Diamond", p, 1))
    out += [("Disco/Diamond", d, 1) for d in drives]
    out += [("Disco/Diamond", h, 1), ("Disco/Diamond", Path("/opt"), 1)]
    if SYS != "Windows":
        for pre in (h / ".wine", h / "Games"):
            for pf in ("Program Files", "Program Files (x86)", "GOG Games"):
                out.append(("Wine", pre / "drive_c" / pf, 2))
    return out


def detect_all(cfg=None) -> list:
    cfg = cfg if cfg is not None else load_cfg()
    found = {}

    def add(path: Path, store: str):
        ed = classify(path)
        if ed:
            try:
                k = path.resolve()
            except OSError:
                k = path
            found.setdefault(k, Game(path, ed, store))

    for lib in steam_libraries():
        for c in safe_dirs(lib / "steamapps/common"):
            add(c, "Steam")
    for store, base, depth in other_bases():
        if not base.is_dir():
            continue
        for c in safe_dirs(base):
            add(c, store)
            if depth > 1:
                for c2 in safe_dirs(c):
                    add(c2, store)
    games = list(found.values())
    for m in cfg.get("manual", []):
        games.append(Game(m["root"], m.get("edition", "Personalizada"), "Manual", m.get("user")))
    for g in games:
        ov = cfg.get("user_override", {}).get(str(g.root))
        if ov:
            g.user = Path(ov)
    if not games and ee_user_dir().exists():
        games.append(Game(None, "EE", "user folder"))
    return games


def open_path(p):
    p = str(p)
    try:
        if SYS == "Windows":
            os.startfile(p)  # type: ignore[attr-defined]
        elif SYS == "Darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
    except Exception:
        pass


def launch(game: Game):
    if game.store == "Steam" and game.edition == "EE":
        open_path(f"steam://rungameid/{STEAM_APPID_EE}")
        return
    if not game.root:
        raise Err("No installation folder found to launch the game.")
    cands = sorted(game.root.glob("bin/*/nwmain*")) if game.edition == "EE" else [game.root / "nwmain.exe"]
    exe = next((c for c in cands if c.is_file() and c.suffix.lower() not in (".so", ".pdb", ".dll")), None)
    if not exe:
        raise Err("Could not find the game executable.")
    cmd = [str(exe)]
    if SYS != "Windows" and exe.suffix.lower() == ".exe":
        cmd = ["wine"] + cmd
    subprocess.Popen(cmd, cwd=str(exe.parent))


def steam_workshop_items():
    """Return list of dicts {id, path, title} for NWN EE workshop subscriptions."""
    out = []
    # find steam libraries
    for lib in steam_libraries():
        content = lib / "steamapps" / "workshop" / "content" / STEAM_APPID_EE
        if not content.is_dir():
            continue
        for d in safe_dirs(content):
            title = d.name
            # try to read title from workshopitem file or appworkshop acf
            for cand in ("workshopitem", "workshop_item", "title.txt"):
                f = d / cand
                if f.is_file():
                    try:
                        txt = f.read_text(errors="ignore")
                        m = re.search(r'"title"\s+"([^"]+)"', txt)
                        if m:
                            title = m.group(1)
                    except OSError:
                        pass
            out.append({"id": d.name, "path": d, "title": title})
    # dedupe by id
    seen = set()
    uniq = []
    for it in out:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        uniq.append(it)
    return uniq
