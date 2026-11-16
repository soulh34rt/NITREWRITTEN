#!/usr/bin/env python3
"""nwnmods - Mod Manager for Neverwinter Nights (Enhanced Edition and Diamond).

Works on Windows, Linux, and macOS. Uses Python 3.8+ standard library.
(UI uses tkinter; 7-Zip recommended for .7z/.rar support).

  python3 nwnmods.py                 -> open the graphical interface
  python3 nwnmods.py detect          -> show detected installations
  python3 nwnmods.py install X.zip   -> install a mod (zip, 7z, rar, tar, hak, tlk, mod...)
  python3 nwnmods.py list | enable N | disable N | remove N | conflicts
  python3 nwnmods.py backup | restore FILE.zip | launch
Global options: --game N (index from detect)  --user PATH (manual data folder)
"""
from __future__ import annotations

import argparse
import base64
import html as htmlmod
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import urllib.request
import webbrowser
import zipfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

SYS = platform.system()  # Windows / Linux / Darwin
APP = "nwnmods"
STEAM_APPID_EE = "704450"
UA = "nwnmods/1.0 (personal mod manager)"


class Err(Exception):
    pass


# --------------------------------------------------------------------------- safety limits
MAX_ENTRIES = 10000
MAX_FILE_BYTES = 512 * 1024 * 1024  # 512 MiB per file
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024  # 2 GiB per archive
MAX_NAME_LEN = 255


# --------------------------------------------------------------------------- config
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
    d = {"manual": [], "lowercase": True, "policy": "ask", "last": None, "user_override": {}}
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


# --------------------------------------------------------------------------- detection
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
    # GOG
    out += [("GOG", d / "GOG Games", 1) for d in drives]
    out += [("GOG", p / "GOG Galaxy/Games", 1) for p in pfs]
    out += [("GOG", h / "GOG Games", 1), ("GOG", h / "Games", 2), ("GOG", h / "Games/gog", 1)]
    # Beamdog (folders with numeric ID)
    out += [("Beamdog", d / "Beamdog Library", 1) for d in drives]
    out += [("Beamdog", h / "Beamdog Library", 1), ("Beamdog", documents_dir() / "Beamdog Library", 1)]
    out += [("Beamdog", p / "Beamdog", 2) for p in pfs]
    # Origin / EA
    out += [("Origin/EA", p / n, 1) for p in pfs for n in ("Origin Games", "EA Games")]
    out += [("Origin/EA", d / "Origin Games", 1) for d in drives]
    # Disco original / Diamond / Atari / BioWare
    for p in pfs:
        for n in ("Atari", "Infogrames", "BioWare"):
            out.append(("Disco/Diamond", p / n, 1))
        out.append(("Disco/Diamond", p, 1))
    out += [("Disco/Diamond", d, 1) for d in drives]
    out += [("Disco/Diamond", h, 1), ("Disco/Diamond", Path("/opt"), 1)]
    # Wine
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


# --------------------------------------------------------------------------- files
KNOWN = {"hak", "tlk", "modules", "override", "nwm", "erf", "portraits", "music", "ambient", "movies",
         "saves", "localvault", "servervault", "dmvault", "campaigns", "database", "streamwaves"}
LOWER_DIRS = {"hak", "tlk", "override", "portraits", "music", "ambient"}
DEST = {".hak": "hak", ".tlk": "tlk", ".mod": "modules", ".nwm": "nwm", ".erf": "erf",
        ".bic": "localvault", ".nwnmod": "modules"}
IGNORE_EXT = {".txt", ".md", ".rtf", ".pdf", ".html", ".htm", ".url", ".doc", ".docx", ".nfo",
              ".lnk", ".db", ".jpg", ".jpeg", ".gif"}
IGNORE_NAMES = {"thumbs.db", ".ds_store", "readme", "license", "desktop.ini"}
CONTENT_DIRS = ["hak", "tlk", "modules", "override", "nwm", "erf", "portraits", "music", "ambient",
                "campaigns", "saves", "localvault", "servervault"]


def map_path(rel: Path, lowercase: bool):
    parts = list(rel.parts)
    name = parts[-1]
    ext = Path(name).suffix.lower()
    if (name.lower() in IGNORE_NAMES or ext in IGNORE_EXT or name.startswith("._")
            or any(p.startswith("__MACOSX") for p in parts)):
        return None
    for i, p in enumerate(parts[:-1]):
        if p.lower() in KNOWN:
            sub = [p.lower()] + parts[i + 1:]
            if lowercase and sub[0] in LOWER_DIRS:
                sub = [sub[0]] + [x.lower() for x in sub[1:]]
            return Path(*sub)
    sub = DEST.get(ext)
    if sub is None:
        stem = Path(name).stem.lower()
        sub = "portraits" if stem.startswith("po_") and ext in (".tga", ".dds", ".png") else "override"
    if lowercase and sub in LOWER_DIRS:
        name = name.lower()
    return Path(sub) / name


def _safe_member_name(name: str) -> str:
    """Reject absolute / traversal / NUL / overly long member names."""
    if not name or "\x00" in name:
        raise Err("Archive contains insecure paths.")
    # normalize separators
    name = name.replace("\\", "/")
    if name.startswith("/") or name.startswith("../") or "/../" in name or name.endswith("/.."):
        raise Err("Archive contains insecure paths.")
    parts = [p for p in name.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise Err("Archive contains insecure paths.")
    if len(name) > MAX_NAME_LEN * 4:
        raise Err("Archive member name too long.")
    return name


def _inside(out: Path, name: str) -> bool:
    try:
        _safe_member_name(name)
    except Err:
        return False
    # lexical check (no resolve, to avoid following symlinks in out)
    try:
        dest = (out / name).resolve() if False else (out / name)
    except Exception:
        return False
    root = out
    try:
        dest.relative_to(root)
        return True
    except ValueError:
        return False


def _check_total(total: int):
    if total > MAX_TOTAL_BYTES:
        raise Err("Archive exceeds maximum allowed size.")


def _strip_symlinks(root: Path):
    """Remove any symlink created during extraction (defense in depth)."""
    for p in list(root.rglob("*")):
        if p.is_symlink():
            try:
                if p.is_dir() and not p.is_file():
                    p.unlink()
                else:
                    p.unlink()
            except OSError:
                raise Err("Archive contains symlink entries.")


def extract(src: Path, out: Path):
    if src.is_dir():
        shutil.copytree(src, out, dirs_exist_ok=True)
        return
    # single known file types
    if src.suffix.lower() in DEST or src.suffix.lower() in {".2da", ".nss", ".ncs", ".tga", ".dds", ".mdl"}:
        if src.stat().st_size > MAX_FILE_BYTES:
            raise Err("File too large.")
        shutil.copy2(src, out / src.name)
        return

    # ZIP
    if zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            infos = z.infolist()
            if len(infos) > MAX_ENTRIES:
                raise Err("Archive has too many entries.")
            total = 0
            for m in infos:
                _safe_member_name(m.filename)
                if m.file_size > MAX_FILE_BYTES:
                    raise Err("Archive member too large.")
                total += m.file_size
                _check_total(total)
                # reject symlinks in unix mode
                mode = (m.external_attr >> 16) & 0xFFFF
                if (mode & 0xF000) == 0xA000:
                    raise Err("Archive contains symlink entries.")
            # extract member by member with path check
            for m in infos:
                if m.is_dir():
                    target = out / _safe_member_name(m.filename)
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target = out / _safe_member_name(m.filename)
                if not _inside(out, m.filename):
                    raise Err("Archive contains insecure paths.")
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(m) as srcf, open(target, "wb") as dstf:
                    shutil.copyfileobj(srcf, dstf)
        _strip_symlinks(out)
        return

    # TAR
    if tarfile.is_tarfile(src):
        with tarfile.open(src) as t:
            members = t.getmembers()
            if len(members) > MAX_ENTRIES:
                raise Err("Archive has too many entries.")
            total = 0
            for m in members:
                _safe_member_name(m.name)
                if m.isfile():
                    if m.size > MAX_FILE_BYTES:
                        raise Err("Archive member too large.")
                    total += m.size
                    _check_total(total)
                # reject dangerous member types
                if m.issym() or m.islnk() or m.isdev() or m.ischr() or m.isblk() or m.isfifo():
                    raise Err("Archive contains unsafe entry types.")
            # use filter when available (3.11.4+/3.12+), else manual
            try:
                t.extractall(out, filter="data")
            except TypeError:
                # older Python: extract manually with validation
                for m in members:
                    if m.isdir():
                        (out / _safe_member_name(m.name)).mkdir(parents=True, exist_ok=True)
                        continue
                    if not m.isfile():
                        continue
                    target = out / _safe_member_name(m.name)
                    if not _inside(out, m.name):
                        raise Err("Archive contains insecure paths.")
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with t.extractfile(m) as srcf, open(target, "wb") as dstf:  # type: ignore
                        shutil.copyfileobj(srcf, dstf)
        _strip_symlinks(out)
        return

    # 7z
    if src.suffix.lower() == ".7z":
        # prefer py7zr
        try:
            import py7zr  # type: ignore
            with py7zr.SevenZipFile(src) as z:
                names = z.getnames()
                if len(names) > MAX_ENTRIES:
                    raise Err("Archive has too many entries.")
                for n in names:
                    _safe_member_name(n)
                z.extractall(out)
            _strip_symlinks(out)
            return
        except ImportError:
            pass
        # external tool fallback
        tools = ["7z", "7zz", "7za", "7zr"]
        if SYS == "Windows":
            for pf in ("ProgramFiles", "ProgramFiles(x86)"):
                if os.environ.get(pf):
                    tools.append(str(Path(os.environ[pf]) / "7-Zip/7z.exe"))
        for tool in tools:
            if shutil.which(tool) or Path(tool).is_file():
                subprocess.run([tool, "x", "-y", f"-o{out}", str(src)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                _strip_symlinks(out)
                # validate all extracted paths stayed inside out
                for p in out.rglob("*"):
                    try:
                        p.relative_to(out)
                    except ValueError:
                        raise Err("Archive contains insecure paths.")
                return
        raise Err("Could not open .7z archive. Install 7-Zip (7z) or py7zr.")

    # RAR
    if src.suffix.lower() == ".rar":
        # try rarfile
        try:
            import rarfile  # type: ignore
            with rarfile.RarFile(src) as rf:
                infos = rf.infolist()
                if len(infos) > MAX_ENTRIES:
                    raise Err("Archive has too many entries.")
                total = 0
                for m in infos:
                    _safe_member_name(m.filename)
                    if m.file_size > MAX_FILE_BYTES:
                        raise Err("Archive member too large.")
                    total += m.file_size
                    _check_total(total)
                rf.extractall(out)
            _strip_symlinks(out)
            return
        except ImportError:
            pass
        for cmd in (["unrar", "x", "-y", str(src), str(out) + os.sep], ["bsdtar", "-xf", str(src), "-C", str(out)]):
            if shutil.which(cmd[0]):
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                _strip_symlinks(out)
                return
        raise Err("Could not open .rar archive. Install unrar/bsdtar or rarfile.")

    # fallback external 7z for unknown archives
    tools = ["7z", "7zz", "7za", "7zr"]
    if SYS == "Windows":
        for pf in ("ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(pf):
                tools.append(str(Path(os.environ[pf]) / "7-Zip/7z.exe"))
    for tool in tools:
        if shutil.which(tool) or Path(tool).is_file():
            subprocess.run([tool, "x", "-y", f"-o{out}", str(src)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            _strip_symlinks(out)
            return

    raise Err("Could not open archive. For .7z/.rar install 7-Zip (7z) or py7zr.")


def _safe_filename(name: str, fallback: str = "download.zip") -> str:
    name = (name or "").strip()
    name = name.replace("\\", "/").split("/")[-1]
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "-", name).strip(". ")
    if not name or name.lower().endswith((".php", ".html", ".htm")):
        name = fallback
    if len(name) > MAX_NAME_LEN:
        stem, ext = os.path.splitext(name)
        name = stem[: MAX_NAME_LEN - len(ext)] + ext
    return name or fallback


def download(url: str, dest_dir: Path, referer=None, fallback=None, max_bytes=MAX_TOTAL_BYTES) -> Path:
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=90) as r:
            ct = r.headers.get("Content-Type", "").lower()
            if ct.startswith("text/html"):
                raise Err("The site did not return a file (check your connection or login).")
            name = None
            m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', r.headers.get("Content-Disposition", ""))
            if m:
                name = unquote(m.group(1))
            name = name or unquote(Path(urlparse(r.geturl()).path).name)
            name = _safe_filename(name, fallback or "download.zip")
            dest = dest_dir / name
            total = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = r.read(64 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        f.close()
                        try:
                            dest.unlink(missing_ok=True)
                        except OSError:
                            pass
                        raise Err("Download exceeds maximum allowed size.")
                    f.write(chunk)
            if total == 0:
                try:
                    dest.unlink(missing_ok=True)
                except OSError:
                    pass
                raise Err("Downloaded file is empty.")
            return dest
    except Err:
        raise
    except Exception as e:  # noqa: BLE001
        raise Err(f"Download failed: {e}")


def fetch_bytes(url: str, timeout=30, max_bytes=MAX_TOTAL_BYTES) -> bytes:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
            data = r.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise Err("Response too large.")
            return data
    except Err:
        raise
    except Exception as e:  # noqa: BLE001
        raise Err(f"Request failed: {e}")


# --------------------------------------------------------------------------- Neverwinter Vault
VAULT = "https://neverwintervault.org"
VAULT_GAMES = {"NWN:EE": "nwnee", "NWN 1": "nwn1", "All": "all"}
VAULT_TTL = 6 * 3600  # catalog is cached for 6h to avoid excessive requests


def http_get(url: str, timeout=40) -> str:
    try:
        headers = {"User-Agent": UA}
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as r:
            charset = r.headers.get_content_charset() or "utf-8"
            data = r.read(MAX_TOTAL_BYTES + 1)
            if len(data) > MAX_TOTAL_BYTES:
                raise Err("Response too large.")
            return data.decode(charset, "replace")
    except Err:
        raise
    except Exception as e:  # noqa: BLE001
        raise Err(f"Request failed: {e}")


def _norm(t: str) -> str:
    return " ".join(t.split())


class _CatalogParser(HTMLParser):
    """Catalog tables: Capture | Name | Author | Description | Posted | Updated | Votes."""

    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base, self.items = base, []
        self.cat, self._h2, self._row, self._cell = "", None, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "h2":
            self._h2 = []
        elif tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = {"t": [], "h": []}
        elif tag == "a" and self._cell is not None and a.get("href"):
            self._cell["h"].append(a["href"])
        elif tag == "br" and self._cell is not None:
            self._cell["t"].append(" ")

    def handle_endtag(self, tag):
        if tag == "h2" and self._h2 is not None:
            self.cat, self._h2 = _norm("".join(self._h2)), None
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(self._cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._finish(self._row)
            self._row = None

    def handle_data(self, d):
        if self._cell is not None:
            self._cell["t"].append(d)
        elif self._h2 is not None:
            self._h2.append(d)

    def _finish(self, row):
        texts = [_norm("".join(c["t"])) for c in row]
        idx = next((i for i, c in enumerate(row) if texts[i] and any("/project/" in h for h in c["h"])), None)
        if idx is None:
            return
        href = next(h for h in row[idx]["h"] if "/project/" in h)
        url = urljoin(self.base, htmlmod.unescape(href)).split("#")[0]
        parts = urlparse(url).path.strip("/").split("/")  # project/<game>/<category...>/<slug>
        get = lambda k: texts[k] if k < len(texts) else ""  # noqa: E731
        votes = texts[-1] if texts and texts[-1].lstrip("-").isdigit() else "0"
        self.items.append({
            "title": texts[idx], "url": url, "game": parts[1] if len(parts) > 1 else "",
            "cat": self.cat or (parts[2].replace("-", " ").title() if len(parts) > 2 else ""),
            "author": get(idx + 1), "desc": get(idx + 2), "posted": get(idx + 3), "votes": int(votes)})


def parse_catalog(html: str, base: str):
    p = _CatalogParser(base)
    p.feed(html)
    p.close()
    m = re.search(r'<li[^>]*class="[^"]*pager-next[^"]*"[^>]*>\s*<a[^>]*href="([^"]+)"', html)
    return p.items, (urljoin(base, htmlmod.unescape(m.group(1))) if m else None)


def vault_catalog(game: str, force=False) -> list:
    cache = CFG.parent / f"vault_{game}.json"
    if not force and cache.exists() and time.time() - cache.stat().st_mtime < VAULT_TTL:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except Exception:
            pass
    items, url, seen = [], f"{VAULT}/catalog/{game}", set()
    while url and url not in seen and len(seen) < 80:
        seen.add(url)
        got, url = parse_catalog(http_get(url), url)
        items += got
    items = list({i["url"]: i for i in items}.values())
    if not items:
        raise Err("Could not read Vault catalog (offline or site changed?).")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")
    return items


class _Blocks(HTMLParser):
    BLOCK = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "tr", "br", "section",
             "article", "dt", "dd", "ul", "ol", "table", "header", "footer"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.buf, self.skip = [], [], 0

    def _flush(self):
        t = _norm("".join(self.buf))
        self.buf = []
        if t:
            self.out.append(t)

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self.skip += 1
        elif tag in self.BLOCK:
            self._flush()

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self._flush()

    def handle_data(self, d):
        if not self.skip:
            self.buf.append(d)


FIELD_LABELS = {"Category": "Category", "Game": "Game", "Requirements": "Requirements",
                "Language": "Language", "Tags": "Tags", "Permissions": "Permissions"}


def parse_project(html: str, url: str) -> dict:
    """Extracts title, description, data and download links from a project page (best effort)."""
    bp = _Blocks()
    bp.feed(html)
    bp.close()
    blocks = bp.out
    m = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    title = _norm(htmlmod.unescape(m.group(1))).split("|")[0].strip() if m else ""
    if not title:
        m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S | re.I)
        title = _norm(htmlmod.unescape(re.sub(r"<[^>]+>", "", m.group(1)))) if m else url.rsplit("/", 1)[-1]
    m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)', html, re.I)
    desc = _norm(htmlmod.unescape(m.group(1))) if m else ""
    longest = max((b for b in blocks if 80 <= len(b) <= 3000), key=len, default="")
    if len(longest) > len(desc):
        desc = longest
    fields = {}
    for label, key in FIELD_LABELS.items():
        for i, b in enumerate(blocks):
            if b.rstrip(": ").lower() == label.lower() and i + 1 < len(blocks):
                fields[key] = blocks[i + 1]
                break
            if b.lower().startswith(label.lower() + ":") and len(b) > len(label) + 1:
                fields[key] = b[len(label) + 1:].strip()
                break
    for b in blocks:
        m = re.match(r"(?:Author|Submitted by|Created by)\s*:?\s*(.+?)(?:,|\s+on\s|$)", b, re.I)
        if m:
            fields["Author"] = m.group(1).strip()
            break
    files, seen = [], set()
    pats = (r'<a\b[^>]*href="([^"]*pubdlcnt\.php\?[^"]*fid=\d+[^"]*)"[^>]*>(.*?)</a>',
            r'<a\b[^>]*href="([^"]*/files/project/\d+/files/[^"]+)"[^>]*>(.*?)</a>')
    for pat in pats:
        for m in re.finditer(pat, html, re.S | re.I):
            href = urljoin(url, htmlmod.unescape(m.group(1)))
            label = _norm(htmlmod.unescape(re.sub(r"<[^>]+>", "", m.group(2))))
            if href not in seen:
                seen.add(href)
                files.append({"url": href, "name": label or Path(urlparse(href).path).name or "file"})
    m = re.search(r'src="([^"]*/files/(?:styles/[^/"]+/public/)?project/\d+/images/[^"]+)"', html)
    image = urljoin(url, htmlmod.unescape(m.group(1))) if m else None
    return {"title": title, "url": url, "desc": desc, "fields": fields, "files": files, "image": image}


# --------------------------------------------------------------------------- biblioteca
class Library:
    """Mod registry for a user folder (.nwnmods/manifest.json)."""

    def __init__(self, user):
        self.user = Path(user)
        self.base = self.user / ".nwnmods"
        self.mf = self.base / "manifest.json"
        self.d = {"mods": {}, "profiles": {}}
        if self.mf.exists():
            try:
                self.d.update(json.loads(self.mf.read_text(encoding="utf-8")))
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"nwnmods: could not read manifest {self.mf}: {e!r}\n")

    @property
    def mods(self) -> dict:
        return self.d["mods"]

    def save(self):
        self.base.mkdir(parents=True, exist_ok=True)
        tmp = self.mf.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.mf)

    def owners(self) -> dict:
        return {f: n for n, m in self.mods.items() if m["enabled"] for f in m["files"]}

    PROTECTED_DIRS = {"saves", "localvault", "servervault", "dmvault"}

    def cleanup_preview(self, protect=None) -> list:
        """Return list of (relative_posix_path, size) that cleanup would remove."""
        if protect is None:
            protect = self.PROTECTED_DIRS
        owners = self.owners()
        out = []
        for sub in CONTENT_DIRS:
            if sub in protect:
                continue
            d = self.user / sub
            if not d.is_dir():
                continue
            for f in d.rglob("*"):
                if f.is_file():
                    key = f.relative_to(self.user).as_posix()
                    if key not in owners:
                        try:
                            out.append((key, f.stat().st_size))
                        except OSError:
                            out.append((key, 0))
        return out

    def cleanup(self, protect=None, to_trash=True) -> dict:
        """Remove unmanaged files. Protects saves/characters by default; moves files to trash for recovery."""
        if protect is None:
            protect = self.PROTECTED_DIRS
        owners = self.owners()
        trash = None
        if to_trash:
            trash = self.base / "trash" / datetime.now().strftime("%Y%m%d-%H%M%S")
            trash.mkdir(parents=True, exist_ok=True)
        count = 0
        removed = []
        for sub in CONTENT_DIRS:
            if sub in protect:
                continue
            d = self.user / sub
            if not d.is_dir():
                continue
            for f in sorted(d.rglob("*"), reverse=True):
                if f.is_file():
                    key = f.relative_to(self.user).as_posix()
                    if key not in owners:
                        try:
                            if to_trash and trash is not None:
                                dest = trash / key
                                dest.parent.mkdir(parents=True, exist_ok=True)
                                shutil.move(str(f), str(dest))
                            else:
                                f.unlink()
                            count += 1
                            removed.append(key)
                        except OSError:
                            pass
                elif f.is_dir():
                    try:
                        f.rmdir()
                    except OSError:
                        pass
        if trash is not None and count == 0:
            try:
                trash.rmdir()
            except OSError:
                pass
            trash = None
        return {"count": count, "trash": str(trash) if trash else None, "removed": removed}

    def _prune(self, rel: Path):
        p = (self.user / rel).parent
        while p != self.user and len(p.relative_to(self.user).parts) > 1:
            try:
                p.rmdir()
            except OSError:
                break
            p = p.parent

    def _deploy(self, name, items, action, owners):
        files, stash = [], {}
        # rollback journal: list of (kind, src, dst)
        journal = []
        try:
            for src, rel in items:
                key, tgt = rel.as_posix(), self.user / rel
                if tgt.exists() or tgt.is_symlink():
                    if action == "skip":
                        continue
                    sp = self.base / "stash" / name / rel
                    sp.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(tgt), str(sp))
                    journal.append(("stash", sp, tgt))
                    stash[key] = owners.get(key)
                tgt.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(tgt))
                journal.append(("deploy", tgt, src))
                files.append(key)
        except Exception:  # noqa: BLE001
            # rollback
            for kind, a, b in reversed(journal):
                try:
                    if kind == "deploy":
                        if a.exists():
                            a.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(a), str(b))
                    else:
                        if a.exists():
                            b.parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(a), str(b))
                except OSError:
                    pass
            raise
        return files, stash

    def _conflicts(self, items):
        owners = self.owners()
        return [(r.as_posix(), owners.get(r.as_posix()) or "(existing file)")
                for _, r in items if (self.user / r).exists()]

    def install(self, archive, name=None, lowercase=True, on_conflict=None, source=None) -> str:
        archive = Path(archive)
        if not archive.exists():
            raise Err(f"No existe: {archive}")
        if not name:
            name = archive.name if archive.is_dir() else re.sub(r"\.(tar\.\w+|\w+)$", "", archive.name)
        if name in self.mods:
            raise Err(f"'{name}' is already installed.")
        self.user.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            extract(archive, t)
            items = []
            for f in sorted(p for p in t.rglob("*") if p.is_file()):
                rel = map_path(f.relative_to(t), lowercase)
                if rel:
                    items.append((f, rel))
            if not items:
                raise Err("Archive does not contain anything installable.")
            conflicts = self._conflicts(items)
            action = "overwrite"
            if conflicts:
                action = on_conflict(conflicts) if on_conflict else "skip"
                if action == "cancel":
                    raise Err("Installation canceled.")
            files, stash = self._deploy(name, items, action, self.owners())
        if not files:
            raise Err("Nothing was installed (everything already existed).")
        self.mods[name] = {"files": files, "stash": stash, "enabled": True,
                           "date": datetime.now().isoformat(timespec="seconds"), "source": source or str(archive)}
        try:
            self.save()
        except Exception as e:  # noqa: BLE001
            # rollback
            try:
                self._undeploy(name)
            except Exception:
                pass
            raise Err(f"Could not save registry: {e}")
        return name

    def _undeploy(self, name, keep=None):
        m = self.mods[name]
        for key in m["files"]:
            rel = Path(key)
            tgt = self.user / rel
            over = [n for n, o in self.mods.items()
                    if n != name and o["enabled"] and o["stash"].get(key) == name]
            if over:  # another mod overwrote this file; the version of 'name' is in its stash
                if keep:
                    src = self.base / "stash" / over[0] / rel
                    if src.exists():
                        (keep / rel).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src, keep / rel)
                continue
            if tgt.exists():
                if keep:
                    (keep / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(tgt), str(keep / rel))
                else:
                    tgt.unlink()
            sp = self.base / "stash" / name / rel
            if key in m["stash"] and sp.exists():
                tgt.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(sp), str(tgt))
            self._prune(rel)

    def disable(self, name):
        m = self._get(name)
        if not m["enabled"]:
            return
        self._undeploy(name, keep=self.base / "disabled" / name)
        self._relink(name)
        m["enabled"], m["stash"] = False, {}
        shutil.rmtree(self.base / "stash" / name, ignore_errors=True)
        self.save()

    def enable(self, name, on_conflict=None):
        m = self._get(name)
        if m["enabled"]:
            return
        d = self.base / "disabled" / name
        items = [(f, f.relative_to(d)) for f in sorted(d.rglob("*")) if f.is_file()] if d.exists() else []
        if not items:
            raise Err("No stashed files left for this mod.")
        conflicts = self._conflicts(items)
        if conflicts:
            action = on_conflict(conflicts) if on_conflict else "cancel"
            if action != "overwrite":
                raise Err("Activation canceled due to conflicts.")
        files, stash = self._deploy(name, items, "overwrite", self.owners())
        m.update(files=files, stash=stash, enabled=True)
        shutil.rmtree(d, ignore_errors=True)
        self.save()

    def remove(self, name):
        m = self._get(name)
        if m["enabled"]:
            self._undeploy(name)
        shutil.rmtree(self.base / "disabled" / name, ignore_errors=True)
        self._relink(name)
        shutil.rmtree(self.base / "stash" / name, ignore_errors=True)
        del self.mods[name]
        self.save()

    def _relink(self, name):
        """Another mod Y overwrote files from 'name': Y inherits what 'name' had stashed underneath."""
        m = self.mods[name]
        for n, o in self.mods.items():
            if n == name:
                continue
            for k, v in list(o["stash"].items()):
                if v != name:
                    continue
                dst = self.base / "stash" / n / k
                src = self.base / "stash" / name / k
                if k in m["stash"] and src.exists():
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dst))
                    o["stash"][k] = m["stash"][k]
                else:
                    dst.unlink(missing_ok=True)
                    del o["stash"][k]

    def _get(self, name):
        if name not in self.mods:
            raise Err(f"'{name}' is not registered.")
        return self.mods[name]

    def conflicts_report(self) -> dict:
        seen = {}
        for n, m in self.mods.items():
            if m["enabled"]:
                for f in m["files"]:
                    seen.setdefault(f, []).append(n)
        return {f: ns for f, ns in seen.items() if len(ns) > 1}

    def scan(self, category=None):
        owners, rows = self.owners(), []
        for sub in ([category] if category else CONTENT_DIRS):
            d = self.user / sub
            if not d.is_dir():
                continue
            for f in sorted(d.rglob("*")):
                if f.is_file():
                    key = f.relative_to(self.user).as_posix()
                    owner = owners.get(key, "(unmanaged/vanilla)")
                    rows.append((sub, key, f.stat().st_size, owner))
        return rows

    # perfiles
    def save_profile(self, pname):
        self.d["profiles"][pname] = [n for n, m in self.mods.items() if m["enabled"]]
        self.save()

    def apply_profile(self, pname, on_conflict=None):
        want = set(self.d["profiles"].get(pname, []))
        for n in list(self.mods):
            if n not in want:
                self.disable(n)
        for n in want:
            if n in self.mods:
                self.enable(n, on_conflict)

    # copias
    def backup(self, folders=("saves", "localvault", "servervault", "dmvault")) -> Path:
        (self.base / "backups").mkdir(parents=True, exist_ok=True)
        dest = self.base / "backups" / f"nwn-backup-{datetime.now():%Y%m%d-%H%M%S}.zip"
        n = 0
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for sub in folders:
                for f in (self.user / sub).rglob("*") if (self.user / sub).is_dir() else []:
                    if f.is_file():
                        z.write(f, f.relative_to(self.user).as_posix())
                        n += 1
        if not n:
            dest.unlink()
            raise Err("No saves or characters to backup.")
        return dest

    def backups(self):
        d = self.base / "backups"
        return sorted(d.glob("*.zip"), reverse=True) if d.is_dir() else []

    def restore(self, zpath):
        with zipfile.ZipFile(zpath) as z:
            for m in z.infolist():
                if not _inside(self.user, m.filename):
                    raise Err("Backup contains insecure paths.")
            z.extractall(self.user)


# --------------------------------------------------------------------------- GUI
def run_gui(initial_user=None):
    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("NITREWRITTEN - NWN Mod Manager")
            self.geometry("1100x850")
            self.minsize(900, 700)
            
            # Load assets
            if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
                self.res = Path(sys._MEIPASS) / "resources"
            else:
                self.res = Path(__file__).parent / "resources"
            try:
                if (self.res / "pluma.ico").exists():
                    try:
                        from PIL import Image, ImageTk
                        icon_img = Image.open(self.res / "pluma.ico")
                        self.icon_photo = ImageTk.PhotoImage(icon_img)
                        self.iconphoto(True, self.icon_photo)
                        if SYS == "Windows":
                            self.iconbitmap(str(self.res / "pluma.ico"))
                    except Exception:
                        if SYS == "Windows":
                            self.iconbitmap(str(self.res / "pluma.ico"))
            except Exception:
                pass
            
            self.cfg = load_cfg()
            self.games, self.game, self.lib = [], None, None
            self.vault_items, self.vault_info, self.vault_sel = [], {}, None
            self.vault_shown, self.vault_busy, self.vault_sort, self._img = [], False, ("posted", True), None
            self.v_dark = tk.BooleanVar(value=self.cfg.get("dark_mode", False))
            self.v_lower = tk.BooleanVar(value=self.cfg["lowercase"])
            self.v_policy = tk.StringVar(value=self.cfg["policy"])
            
            self._apply_theme() # Apply theme BEFORE building to set correct styles
            self._build()
            self.after(100, lambda: self.detect(initial_user))

        def _apply_theme(self):
            dark = self.v_dark.get()
            bg = "#1e1e1e" if dark else "#ffffff"
            fg = "#e0e0e0" if dark else "#000000"
            header_fg = "#4daafc" if dark else "#1a73e8"
            btn_active = "#333333" if dark else "#e8f0fe"
            tree_bg = "#2d2d2d" if dark else "#ffffff"
            tree_fg = "#ffffff" if dark else "#000000"
            field_bg = "#333333" if dark else "#ffffff"
            secondary_fg = "#888888" if dark else "#666666"
            
            style = ttk.Style()
            # In Dark Mode, we use 'clam' even on Windows because 'vista/xpnative' doesn't support custom colors well
            if dark:
                style.theme_use("clam")
            else:
                if SYS == "Windows": style.theme_use("vista")
                elif SYS == "Darwin": style.theme_use("aqua")
                else: style.theme_use("clam")
                
            style.configure("TFrame", background=bg)
            style.configure("TLabel", background=bg, foreground=fg, font=("Segoe UI", 10))
            style.configure("Secondary.TLabel", background=bg, foreground=secondary_fg, font=("Segoe UI", 9))
            style.configure("Header.TLabel", background=bg, foreground=header_fg, font=("Segoe UI", 13, "bold"))
            style.configure("TButton", font=("Segoe UI", 9))
            style.configure("Treeview", background=tree_bg, foreground=tree_fg, fieldbackground=tree_bg, font=("Segoe UI", 9), rowheight=28)
            style.configure("Treeview.Heading", background="#333333" if dark else "#f0f0f0", 
                            foreground="#ffffff" if dark else "#000000", borderwidth=1, font=("Segoe UI", 10, "bold"))
            style.map("TButton", background=[("active", btn_active)], foreground=[("active", header_fg)])
            
            # Additional styles for complete dark mode
            style.configure("TNotebook", background=bg, borderwidth=0)
            style.configure("TNotebook.Tab", background="#333" if dark else "#e1e1e1", 
                            foreground=fg, padding=[10, 2])
            style.map("TNotebook.Tab", background=[("selected", bg)], foreground=[("selected", header_fg)])
            
            style.configure("TCombobox", fieldbackground=field_bg, background=bg, foreground=fg, arrowcolor=fg)
            style.map("TCombobox", fieldbackground=[("readonly", field_bg)], foreground=[("readonly", fg)])
            
            style.configure("TEntry", fieldbackground=field_bg, foreground=fg, insertcolor=fg)
            style.map("TEntry", fieldbackground=[("readonly", field_bg)], foreground=[("readonly", fg)])
            
            style.configure("TSeparator", background="#444" if dark else "#ccc")
            
            style.configure("TCheckbutton", background=bg, foreground=fg)
            style.map("TCheckbutton", background=[("active", bg)], foreground=[("active", fg)])

            self.configure(background=bg)
            if hasattr(self, "banner_lbl"):
                self.banner_lbl.configure(background=bg)
            
            if hasattr(self, "logbox"):
                self.logbox.configure(background="#121212" if dark else "#f8f9fa", 
                                      foreground="#a0a0a0" if dark else "#333",
                                      insertbackground=fg,
                                      highlightthickness=1, 
                                      highlightbackground="#444" if dark else "#ccc",
                                      borderwidth=0)
            if hasattr(self, "v_det"):
                self.v_det.configure(background="#121212" if dark else "#f8f9fa", 
                                     foreground="#eee" if dark else "#000",
                                     insertbackground=fg,
                                     highlightthickness=1,
                                     highlightbackground="#444" if dark else "#ccc",
                                     borderwidth=0)
            
            # Listbox/Text widgets need manual config as they are tk not ttk
            listbox_args = {"background": tree_bg, "foreground": tree_fg, 
                            "selectbackground": header_fg, "borderwidth": 0, "highlightthickness": 1,
                            "highlightbackground": "#444" if dark else "#ccc"}
            for attr in ("lb_prof", "lb_bak", "lb_vfiles", "lb_games"):
                if hasattr(self, attr):
                    getattr(self, attr).configure(**listbox_args)

            # Global tag configuration for text widgets if needed
            if hasattr(self, "v_det"):
                self.v_det.tag_configure("header", foreground=header_fg, font=("Segoe UI", 10, "bold"))
                self.v_det.tag_configure("field", foreground=secondary_fg)

        # ---- construccion
        def _build(self):
            # No hardcoded colors here, _apply_theme handles it
            
            # Banner
            if (self.res / "banner-readme-v2.png").exists():
                try:
                    try:
                        from PIL import Image, ImageTk
                        img = Image.open(self.res / "banner-readme-v2.png")
                        # Keep aspect ratio but fit width (~1100px)
                        w, h = img.size
                        new_w = 1100
                        new_h = int(h * (new_w / w))
                        img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
                        self.banner_img = ImageTk.PhotoImage(img)
                    except ImportError:
                        self.banner_img = tk.PhotoImage(file=str(self.res / "banner-readme-v2.png"))
                    
                    self.banner_lbl = tk.Label(self, image=self.banner_img, bd=0)
                    self.banner_lbl.pack(fill="x", pady=(0, 5))
                except Exception:
                    pass

            top = ttk.Frame(self, padding=(15, 10))
            top.pack(fill="x")
            
            ttk.Label(top, text="NWN Installation:", style="Header.TLabel").pack(side="left", padx=(0, 10))
            self.combo = ttk.Combobox(top, state="readonly", width=50)
            self.combo.pack(side="left", padx=5, fill="x", expand=True)
            self.combo.bind("<<ComboboxSelected>>", lambda e: self.set_game(self.combo.current()))
            
            btn_frame = ttk.Frame(top)
            btn_frame.pack(side="left", padx=5)
            for txt, fn in (("Scan", self.detect), ("Add...", self.add_folder),
                            ("Folder", lambda: self.game and open_path(self.game.user)),
                            ("▶ Launch Game", self.play)):
                ttk.Button(btn_frame, text=txt, command=fn).pack(side="left", padx=2)
            
            self.info = ttk.Label(self, padding=(15, 5), foreground="#666", font=("Segoe UI", 9))
            self.info.pack(fill="x")
            
            # Main content separator
            ttk.Separator(self, orient="horizontal").pack(fill="x", padx=15, pady=5)
            
            nb = ttk.Notebook(self)
            nb.pack(fill="both", expand=True, padx=15, pady=(5, 15))
            self.nb = nb
            self.tab_mods, self.tab_cont, self.tab_vault = ttk.Frame(nb), ttk.Frame(nb), ttk.Frame(nb)
            self.tab_prof, self.tab_bak = ttk.Frame(nb), ttk.Frame(nb)
            self.tab_set, self.tab_log = ttk.Frame(nb), ttk.Frame(nb)
            for t, n in ((self.tab_mods, "Mods"), (self.tab_vault, "Vault"), (self.tab_cont, "Content"), (self.tab_prof, "Profiles"),
                         (self.tab_bak, "Backups"), (self.tab_set, "Settings"), (self.tab_log, "Log")):
                nb.add(t, text=n)
            self._build_mods()
            self._build_vault()
            self._build_content()
            self._build_profiles()
            self._build_backups()
            self._build_settings()
            self.logbox = tk.Text(self.tab_log, state="disabled", wrap="word")
            self.logbox.pack(fill="both", expand=True)
            nb.bind("<<NotebookTabChanged>>", self._tab_changed)

        def _btns(self, parent, items):
            f = ttk.Frame(parent)
            f.pack(fill="x", pady=(0, 6))
            for txt, fn in items:
                ttk.Button(f, text=txt, command=fn).pack(side="left", padx=2)
            return f

        def _tree(self, parent, cols, widths):
            fr = ttk.Frame(parent)
            fr.pack(fill="both", expand=True)
            tv = ttk.Treeview(fr, columns=cols, show="headings", selectmode="extended")
            for c, w in zip(cols, widths):
                tv.heading(c, text=c.capitalize())
                tv.column(c, width=w, anchor="w")
            sb = ttk.Scrollbar(fr, command=tv.yview)
            tv.configure(yscrollcommand=sb.set)
            tv.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            return tv

        def _build_mods(self):
            p = self.tab_mods
            self._btns(p, (("Install Files...", self.install_files), ("Install Folder...", self.install_dir),
                           ("Install URL...", self.install_url), ("Enable", self.enable),
                           ("Disable", self.disable), ("Uninstall", self.remove),
                           ("View Files", self.show_files), ("Conflicts", self.show_conflicts)))
            
            filter_frame = ttk.Frame(p)
            filter_frame.pack(fill="x", pady=(0, 8))
            ttk.Label(filter_frame, text="Search: ", style="Secondary.TLabel").pack(side="left")
            self.q_mods = tk.StringVar()
            e = ttk.Entry(filter_frame, textvariable=self.q_mods)
            e.pack(side="left", fill="x", expand=True)
            self.q_mods.trace_add("write", lambda *a: self.refresh_mods())
            
            self.tv_mods = self._tree(p, ("name", "status", "files", "date", "source"),
                                      (260, 100, 80, 100, 380))
            for c in ("name", "status", "files", "date", "source"):
                self.tv_mods.heading(c, text=c.title())

        def _build_content(self):
            p = self.tab_cont
            f = ttk.Frame(p)
            f.pack(fill="x", pady=(0, 8))
            self.cat = ttk.Combobox(f, state="readonly", values=["(All)"] + CONTENT_DIRS, width=16)
            self.cat.current(0)
            self.cat.pack(side="left")
            self.cat.bind("<<ComboboxSelected>>", lambda e: self.refresh_content())
            
            self.q_cont = tk.StringVar()
            e = ttk.Entry(f, textvariable=self.q_cont)
            e.pack(side="left", fill="x", expand=True, padx=8)
            self.q_cont.trace_add("write", lambda *a: self.refresh_content())
            
            ttk.Button(f, text="Clean Up", command=self.do_cleanup).pack(side="left", padx=2)
            
            self.lbl_cont = ttk.Label(f, style="Secondary.TLabel")
            self.lbl_cont.pack(side="left")
            self.tv_cont = self._tree(p, ("folder", "file", "size", "mod"), (110, 520, 90, 220))
            for c in ("folder", "file", "size", "mod"):
                self.tv_cont.heading(c, text=c.title())

        def _build_profiles(self):
            p = self.tab_prof
            ttk.Label(p, text="Profiles allow you to save and switch between different sets of active mods.",
                      style="Secondary.TLabel").pack(anchor="w", pady=(5, 10))
            self._btns(p, (("Save Current Profile...", self.prof_save), ("Apply Selected", self.prof_apply),
                           ("Delete Profile", self.prof_del)))
            self.lb_prof = tk.Listbox(p, font=("Segoe UI", 10), borderwidth=1, relief="solid")
            self.lb_prof.pack(fill="both", expand=True, pady=5)

        def _build_backups(self):
            p = self.tab_bak
            ttk.Label(p, text="Manage backups of your save games and characters.",
                      style="Secondary.TLabel").pack(anchor="w", pady=(5, 10))
            self._btns(p, (("Create New Backup", self.bak_make), ("Restore Selected", self.bak_restore),
                           ("Open Backups Folder", lambda: self.lib and open_path(self.lib.base / "backups"))))
            self.lb_bak = tk.Listbox(p, font=("Segoe UI", 10), borderwidth=1, relief="solid")
            self.lb_bak.pack(fill="both", expand=True, pady=5)

        def _build_settings(self):
            p = self.tab_set
            ttk.Checkbutton(p, text="Dark Mode",
                            variable=self.v_dark, command=self.save_settings).pack(anchor="w", pady=8)
            
            ttk.Checkbutton(p, text="Lowercase file names for hak/tlk/override (Recommended for Linux)",
                            variable=self.v_lower, command=self.save_settings).pack(anchor="w", pady=8)
            
            ttk.Label(p, text="When a file already exists during installation:", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(15, 5))
            for v, t in (("ask", "Ask every time"), 
                         ("overwrite", "Overwrite (Backup will be created and restored on uninstall)"),
                         ("skip", "Skip existing files")):
                ttk.Radiobutton(p, text=t, value=v, variable=self.v_policy,
                                command=self.save_settings).pack(anchor="w", padx=20, pady=2)
            
            ttk.Separator(p, orient="horizontal").pack(fill="x", pady=20)
            
            ttk.Button(p, text="Change Game Data Folder...",
                       command=self.change_user).pack(anchor="w", pady=5)
            ttk.Label(p, text=f"Config location: {CFG}", style="Secondary.TLabel").pack(anchor="w", pady=5)

        # ---- utilidades
        def log(self, msg):
            self.logbox.configure(state="normal")
            self.logbox.insert("end", f"{datetime.now():%H:%M:%S}  {msg}\n")
            self.logbox.see("end")
            self.logbox.configure(state="disabled")
            print(msg)

        def guard(self, fn, *a):
            try:
                self.configure(cursor="watch")
                self.update_idletasks()
                return fn(*a)
            except Err as e:
                self.log(f"✘ {e}")
                messagebox.showerror("NWN Mod Manager", str(e))
            except Exception as e:  # noqa: BLE001
                self.log(f"✘ Unexpected error: {e!r}")
                messagebox.showerror("Unexpected error", repr(e))
            finally:
                self.configure(cursor="")

        def save_settings(self):
            self.cfg["lowercase"], self.cfg["policy"] = self.v_lower.get(), self.v_policy.get()
            self.cfg["dark_mode"] = self.v_dark.get()
            save_cfg(self.cfg)
            self._apply_theme()

        def ask_conflict(self, conf):
            pol = self.v_policy.get()
            if pol in ("overwrite", "skip"):
                return pol
            lines = "\n".join(f"• {r}  ←  {o}" for r, o in conf[:12])
            more = f"\n… and {len(conf) - 12} more" if len(conf) > 12 else ""
            r = messagebox.askyesnocancel(
                "File Conflicts",
                f"{len(conf)} file(s) already exist:\n\n{lines}{more}\n\nYes = Overwrite (with backup)\n"
                "No = Skip them\nCancel = Abort installation")
            return "overwrite" if r else ("skip" if r is False else "cancel")

        # ---- instalaciones
        def detect(self, initial_user=None):
            self.games = self.guard(detect_all, self.cfg) or []
            if isinstance(initial_user, (str, Path)):
                self.games.insert(0, Game(initial_user, "Custom", "manual", initial_user))
            self.combo["values"] = [g.label for g in self.games]
            self.log(f"Installations detected: {len(self.games)}")
            if not self.games:
                self.game = self.lib = None
                self.info.configure(text="No installations found. Use 'Add...' to manually locate your game folder.")
                self.refresh_all()
                return
            last = self.cfg.get("last")
            idx = next((i for i, g in enumerate(self.games) if str(g.root) == last), 0)
            self.combo.current(idx)
            self.set_game(idx)

        def add_folder(self):
            d = filedialog.askdirectory(title="NWN Installation or Data Folder")
            if not d:
                return
            ed = classify(Path(d))
            if ed:
                self.cfg["manual"].append({"root": d, "edition": ed})
            elif messagebox.askyesno("Unknown Folder",
                                     "This doesn't look like a standard NWN installation. Use it as a data folder "
                                     "(where hak, override, modules... are located)?"):
                self.cfg["manual"].append({"root": d, "edition": "Custom", "user": d})
            else:
                return
            save_cfg(self.cfg)
            self.detect()

        def set_game(self, i):
            self.game = self.games[i]
            self.lib = Library(self.game.user)
            self.cfg["last"] = str(self.game.root)
            save_cfg(self.cfg)
            self.info.configure(text=f"Game data: {self.game.user}")
            want = "NWN 1" if self.game.edition == "Diamond" else "NWN:EE"
            if not self.vault_items:
                self.v_game.set(want)
            self.refresh_all()

        def change_user(self):
            if not self.game:
                return
            d = filedialog.askdirectory(title="Select Game Data Folder (hak, override, modules...)")
            if d:
                self.cfg["user_override"][str(self.game.root)] = d
                save_cfg(self.cfg)
                self.game.user = Path(d)
                self.set_game(self.combo.current())

        def play(self):
            if self.game:
                self.guard(launch, self.game)

        # ---- refrescos
        def refresh_all(self):
            self.refresh_mods()
            self.refresh_content()
            self.refresh_profiles()
            self.refresh_backups()

        def refresh_mods(self):
            self.tv_mods.delete(*self.tv_mods.get_children())
            if not self.lib:
                return
            q = self.q_mods.get().lower()
            for n, m in self.lib.mods.items():
                if q in n.lower():
                    self.tv_mods.insert("", "end", iid=n, values=(
                        n, "Active" if m["enabled"] else "Disabled", len(m["files"]),
                        m["date"][:10], m["source"] if m["source"].startswith("http") else Path(m["source"]).name))

        def refresh_content(self):
            self.tv_cont.delete(*self.tv_cont.get_children())
            self.lbl_cont.configure(text="")
            if not self.lib:
                return
            cat = self.cat.get()
            rows = self.lib.scan(None if cat == "(all)" else cat)
            q = self.q_cont.get().lower()
            shown = [r for r in rows if q in r[1].lower() or q in r[3].lower()]
            for sub, key, size, owner in shown[:5000]:
                self.tv_cont.insert("", "end", values=(sub, key, f"{size / 1024:,.0f} KB", owner))
            self.lbl_cont.configure(text=f"{len(shown)} files" + (" (showing 5000)" if len(shown) > 5000 else ""))

        def refresh_profiles(self):
            self.lb_prof.delete(0, "end")
            if self.lib:
                for p, ms in self.lib.d["profiles"].items():
                    self.lb_prof.insert("end", f"{p}  ({len(ms)} mods)")

        def refresh_backups(self):
            self.lb_bak.delete(0, "end")
            if self.lib:
                for b in self.lib.backups():
                    self.lb_bak.insert("end", b.name)

        # ---- acciones de mods
        def selected(self):
            s = list(self.tv_mods.selection())
            if not s:
                messagebox.showinfo("NITREWRITTEN", "Please select one or more mods from the list.")
            return s

        def _need_lib(self):
            if not self.lib:
                messagebox.showinfo("NITREWRITTEN", "Please select or add a game installation first.")
            return self.lib

        def do_install(self, paths):
            for p in paths:
                n = self.guard(self.lib.install, p, None, self.v_lower.get(), self.ask_conflict)
                if n:
                    self.log(f"✔ Installed: {n}")
            self.refresh_all()

        def install_files(self):
            if not self._need_lib():
                return
            ps = filedialog.askopenfilenames(title="Select Mod Files", filetypes=[
                ("Mods & Packages", "*.zip *.7z *.rar *.tar *.gz *.bz2 *.xz *.hak *.tlk *.mod *.erf *.nwm"),
                ("All Files", "*.*")])
            if ps:
                self.do_install(ps)

        def install_dir(self):
            if self._need_lib():
                d = filedialog.askdirectory(title="Select Unpacked Mod Folder")
                if d:
                    self.do_install([d])

        def install_url(self):
            if not self._need_lib():
                return
            url = simpledialog.askstring("Install from URL", "Direct download link:")
            if not url:
                return
            self.log(f"Downloading {url} ...")
            tmp = Path(tempfile.mkdtemp(prefix="nwnmods-"))
            
            def worker():
                try:
                    f = download(url, tmp)
                    self.after(0, lambda: (self.do_install([f]), shutil.rmtree(tmp, ignore_errors=True)))
                except Exception as e:  # noqa: BLE001
                    self.after(0, lambda: (self.log(f"✘ Download failed: {e}"),
                                           messagebox.showerror("Download Failed", str(e))))

            threading.Thread(target=worker, daemon=True).start()

        def enable(self):
            for n in self.selected():
                self.guard(self.lib.enable, n, self.ask_conflict)
                self.log(f"Enabled: {n}")
            self.refresh_all()
        
        def disable(self):
            for n in self.selected():
                self.guard(self.lib.disable, n)
                self.log(f"Disabled: {n}")
            self.refresh_all()
        
        def remove(self):
            s = self.selected()
            if s and messagebox.askyesno("Uninstall", f"Uninstall {len(s)} mod(s)?"):
                for n in s:
                    self.guard(self.lib.remove, n)
                    self.log(f"Uninstalled: {n}")
                self.refresh_all()

        def _text_window(self, title, text):
            dark = self.v_dark.get()
            bg = "#1e1e1e" if dark else "#ffffff"
            fg = "#e0e0e0" if dark else "#000000"
            w = tk.Toplevel(self)
            w.title(title)
            w.geometry("720x460")
            w.configure(background=bg)
            try:
                if (self.res / "pluma.ico").exists():
                    w.iconbitmap(str(self.res / "pluma.ico"))
            except Exception: pass
            t = tk.Text(w, wrap="none", background="#121212" if dark else "#ffffff", foreground=fg, 
                        insertbackground=fg, borderwidth=0, highlightthickness=0)
            t.insert("1.0", text)
            t.configure(state="disabled")
            sb_y = ttk.Scrollbar(w, orient="vertical", command=t.yview)
            sb_x = ttk.Scrollbar(w, orient="horizontal", command=t.xview)
            t.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
            sb_y.pack(side="right", fill="y")
            sb_x.pack(side="bottom", fill="x")
            t.pack(fill="both", expand=True)

        def show_files(self):
            for n in self.selected()[:1]:
                m = self.lib.mods[n]
                self._text_window(f"{n} — {len(m['files'])} files", "\n".join(m["files"]))

        def show_conflicts(self):
            if not self._need_lib():
                return
            c = self.lib.conflicts_report()
            txt = "\n".join(f"{f}\n    mods: {', '.join(ns)}  (last installed wins)" for f, ns in c.items())
            self._text_window("Mod Conflicts", txt or "No conflicts between active mods.")

        def do_cleanup(self):
            if not self._need_lib(): return
            preview = self.lib.cleanup_preview()
            if not preview:
                messagebox.showinfo("Clean Up", "No unmanaged files to remove (saves and characters are protected).")
                return
            sample = "\n".join(f"  {p}  ({s} bytes)" for p, s in preview[:20])
            more = f"\n  ... and {len(preview) - 20} more" if len(preview) > 20 else ""
            if not messagebox.askyesno("Clean Up Preview",
                                       f"The following {len(preview)} unmanaged file(s) will be moved to a recovery folder "
                                       f"(saves and characters are never touched):\n\n{sample}{more}\n\nContinue?"):
                return
            res = self.guard(self.lib.cleanup)
            if isinstance(res, dict):
                self.log(f"Cleaned up {res['count']} unmanaged files. Recovery: {res['trash']}")
            self.refresh_all()

        # ---- Vault
        def bg(self, fn, ok, err=None):
            def work():
                try:
                    r = fn()
                except Exception as e:
                    self.after(0, lambda e=e: (err or self._net_err)(e))
                    return
                self.after(0, lambda r=r: ok(r))
            threading.Thread(target=work, daemon=True).start()

        def _net_err(self, e):
            self.vault_busy = False
            self.v_lbl.configure(text="Network error")
            self.log(f"✘ {e}")

        def _tab_changed(self, _e=None):
            if self.nb.select() == str(self.tab_vault) and not self.vault_items and not self.vault_busy:
                self.vault_load()

        def _build_vault(self):
            p = self.tab_vault
            top = ttk.Frame(p)
            top.pack(fill="x", pady=(0, 6))
            self.v_game = tk.StringVar(value="NWN:EE")
            g = ttk.Combobox(top, state="readonly", width=10, values=list(VAULT_GAMES), textvariable=self.v_game)
            g.pack(side="left")
            g.bind("<<ComboboxSelected>>", lambda e: self.vault_load())
            self.v_cat = ttk.Combobox(top, state="readonly", width=20, values=["(All)"])
            self.v_cat.set("(All)")
            self.v_cat.pack(side="left", padx=5)
            self.v_cat.bind("<<ComboboxSelected>>", lambda e: self.vault_filter())
            
            self.v_q = tk.StringVar()
            ttk.Label(top, text="Search: ", style="Secondary.TLabel").pack(side="left", padx=(5, 0))
            ttk.Entry(top, textvariable=self.v_q).pack(side="left", fill="x", expand=True, padx=5)
            self.v_q.trace_add("write", lambda *a: self.vault_filter())
            ttk.Button(top, text="Refresh", command=lambda: self.vault_load(True)).pack(side="left", padx=2)
            self.v_lbl = ttk.Label(top, foreground="#1a73e8", font=("Segoe UI", 9, "bold"))
            self.v_lbl.pack(side="left", padx=10)
            
            cols = ("name", "category", "author", "posted", "votes")
            self.tv_vault = self._tree(p, cols, (330, 110, 130, 90, 60))
            for c in cols:
                self.tv_vault.heading(c, text=c.title(), command=lambda c=c: self.vault_sort_by(c))
            self.tv_vault.bind("<<TreeviewSelect>>", self.vault_select)
            det = ttk.Frame(p)
            det.pack(fill="x", pady=(6, 0))
            self.v_img = ttk.Label(det, width=28, anchor="center")
            self.v_img.pack(side="left", padx=(0, 8))
            self.v_det = tk.Text(det, height=10, wrap="word", state="disabled")
            self.v_det.pack(side="left", fill="both", expand=True)
            side = ttk.Frame(det)
            side.pack(side="left", fill="y", padx=(8, 0))
            ttk.Label(side, text="Files:").pack(anchor="w")
            self.lb_vfiles = tk.Listbox(side, height=4, width=34, exportselection=False, font=("Segoe UI", 9))
            self.lb_vfiles.pack(fill="x")
            ttk.Button(side, text="⬇ Download & Install", command=self.vault_install).pack(fill="x", pady=(6, 2))
            ttk.Button(side, text="Open Page", command=lambda: self.vault_sel and
                       webbrowser.open(self.vault_sel["url"])).pack(fill="x")

        def vault_load(self, force=False):
            if self.vault_busy:
                return
            self.vault_busy = True
            self.v_lbl.configure(text="Loading catalog...")
            game = VAULT_GAMES[self.v_game.get()]
            
            def ok(items):
                self.vault_busy = False
                self.vault_items = items
                self.v_cat.configure(values=["(All)"] + sorted({i["cat"] for i in items if i["cat"]}))
                self.v_cat.set("(All)")
                self.log(f"Vault: {len(items)} projects ({self.v_game.get()})")
                self.vault_filter()

            self.bg(lambda: vault_catalog(game, force), lambda i: ok(i))

        def vault_sort_by(self, col):
            cur, desc = self.vault_sort
            self.vault_sort = (col, not desc if cur == col else col in ("posted", "votes"))
            self.vault_filter()

        def vault_filter(self):
            keys = {"name": "title", "category": "cat", "author": "author", "posted": "posted", "votes": "votes"}
            col, desc = self.vault_sort
            k = keys.get(col, col)
            cat, q = self.v_cat.get(), self.v_q.get().lower().strip()
            rows = [i for i in self.vault_items
                    if (cat in ("", "(All)") or i["cat"] == cat)
                    and (not q or q in f'{i["title"]} {i["author"]} {i["desc"]} {i["cat"]}'.lower())]
            rows.sort(key=lambda i: i[k].lower() if isinstance(i[k], str) else i[k], reverse=desc)
            self.vault_shown = rows[:3000]
            self.tv_vault.delete(*self.tv_vault.get_children())
            for n, i in enumerate(self.vault_shown):
                self.tv_vault.insert("", "end", iid=str(n),
                                     values=(i["title"], i["cat"], i["author"], i["posted"], i["votes"]))
            self.v_lbl.configure(text=f"{len(rows)} results" if self.vault_items else "")

        def _vtext(self, txt):
            self.v_det.configure(state="normal")
            self.v_det.delete("1.0", "end")
            self.v_det.insert("1.0", txt)
            self.v_det.configure(state="disabled")

        def vault_select(self, _e=None):
            sel = self.tv_vault.selection()
            if not sel:
                return
            it = self.vault_shown[int(sel[0])]
            self.vault_sel = it
            self.lb_vfiles.delete(0, "end")
            self.v_img.configure(image="", text="")
            self._vtext(f'{it["title"]}\n{it["author"]} · {it["cat"]} · {it["posted"]}\n\n{it["desc"]}\n\nLoading details...')
            if it["url"] in self.vault_info:
                return self._vault_details(it, self.vault_info[it["url"]])
            
            def ok(info):
                self.vault_info[it["url"]] = info
                if self.vault_sel is it:
                    self._vault_details(it, info)
            
            self.bg(lambda: parse_project(http_get(it["url"]), it["url"]), lambda i: ok(i),
                    lambda e: (self._vtext(f'{it["title"]}\n\n{it["desc"]}\n\n(Could not load details: {e})')))

        def _vault_details(self, it, info):
            lines = [info["title"] or it["title"], ""]
            lines += [f"{k}: {v}" for k, v in info["fields"].items()]
            lines += ["", info["desc"] or it["desc"]]
            if not info["files"]:
                lines += ["", "(No direct download link available. Use 'Open Page'.)"]
            self._vtext("\n".join(lines))
            self.lb_vfiles.delete(0, "end")
            for f in info["files"]:
                self.lb_vfiles.insert("end", f["name"])
            if info["files"]:
                self.lb_vfiles.selection_set(0)
            if info["image"]:
                self.bg(lambda: fetch_bytes(info["image"]), lambda b: self._set_img(b, it),
                        lambda _e: None)

        def _set_img(self, data, it):
            if self.vault_sel is not it:
                return
            try:
                ph = tk.PhotoImage(data=base64.b64encode(data))
                ph = ph.subsample(max(1, -(-ph.width() // 240)), max(1, -(-ph.height() // 180)))
            except tk.TclError:
                try:  # JPG/WebP: only if Pillow is installed
                    from PIL import Image, ImageTk  # type: ignore
                    im = Image.open(io.BytesIO(data))
                    im.thumbnail((240, 180))
                    ph = ImageTk.PhotoImage(im)
                except Exception:  # noqa: BLE001
                    return
            self._img = ph
            self.v_img.configure(image=ph)

        def vault_install(self):
            if not self._need_lib():
                return
            it = self.vault_sel
            info = self.vault_info.get(it["url"]) if it else None
            if not it:
                return messagebox.showinfo("Vault", "Select a mod from the list.")
            if not info or not info["files"]:
                return messagebox.showinfo("Vault", "This project has no direct download. Use 'Open Page'.")
            sel = self.lb_vfiles.curselection()
            f = info["files"][sel[0] if sel else 0]
            name = re.sub(r'[\\/:*?"<>|]+', "-", info["title"] or it["title"]).strip()[:80]
            self.log(f"Downloading {f['name']} ...")
            tmp = Path(tempfile.mkdtemp(prefix="nwnmods-"))
            
            def done(path):
                n = self.guard(self.lib.install, path, name, self.v_lower.get(), self.ask_conflict, it["url"])
                shutil.rmtree(tmp, ignore_errors=True)
                if n:
                    self.log(f"✔ Installed from Vault: {n}")
                    self.refresh_all()
            
            def fail(e):
                shutil.rmtree(tmp, ignore_errors=True)
                self.log(f"✘ Download failed: {e}")
                messagebox.showerror("Download Failed", str(e))
            
            self.bg(lambda: download(f["url"], tmp, it["url"], f["name"]), lambda p: done(p), lambda e: fail(e))

        # ---- perfiles y copias
        def prof_name(self):
            s = self.lb_prof.curselection()
            return self.lb_prof.get(s[0]).rsplit("  (", 1)[0] if s else None

        def prof_save(self):
            if self._need_lib():
                n = simpledialog.askstring("Profile", "Profile name:")
                if n:
                    self.lib.save_profile(n)
                    self.refresh_profiles()
        
        def prof_apply(self):
            n = self.prof_name()
            if n:
                self.guard(self.lib.apply_profile, n, self.ask_conflict)
                self.log(f"Profile applied: {n}")
                self.refresh_all()

        def prof_del(self):
            n = self.prof_name()
            if n:
                self.lib.d["profiles"].pop(n, None)
                self.lib.save()
                self.refresh_profiles()

        def bak_make(self):
            if self._need_lib():
                p = self.guard(self.lib.backup)
                if p:
                    self.log(f"✔ Backup created: {p.name}")
                    self.refresh_backups()
        
        def bak_restore(self):
            s = self.lb_bak.curselection()
            if s and messagebox.askyesno("Restore", "This will overwrite existing saves with the same name. Continue?"):
                self.guard(self.lib.restore, self.lib.base / "backups" / self.lb_bak.get(s[0]))
                self.log("✔ Backup restored")

    App().mainloop()


# --------------------------------------------------------------------------- CLI
def pick(args):
    if args.user:
        return Game(args.user, "Custom", "manual", args.user)
    games = detect_all()
    if not games:
        raise Err("Could not find NWN. Use --user DATA_PATH or open the GUI and add the folder.")
    return games[args.game]


def cli(argv):
    ap = argparse.ArgumentParser(prog="nwnmods", description="Mod Manager for Neverwinter Nights")
    ap.add_argument("--game", type=int, default=0, help="index shown by 'detect'")
    ap.add_argument("--user", help="data folder (hak, override, modules...)")
    sp = ap.add_subparsers(dest="cmd")
    sp.add_parser("gui")
    sp.add_parser("detect")
    i = sp.add_parser("install")
    i.add_argument("file")
    i.add_argument("--name")
    i.add_argument("--force", action="store_true", help="overwrite (with backup)")
    i.add_argument("--keep-case", action="store_true")
    for c in ("enable", "disable", "remove"):
        sp.add_parser(c).add_argument("name")
    for c in ("list", "conflicts", "backup", "launch"):
        sp.add_parser(c)
    sp.add_parser("restore").add_argument("zip")
    a = ap.parse_args(argv)
    if a.cmd in (None, "gui"):
        return run_gui(a.user)
    if a.cmd == "detect":
        for n, g in enumerate(detect_all()):
            print(f"[{n}] {g.label}\n     data: {g.user}")
        return
    g = pick(a)
    lib = Library(g.user)
    cfg = load_cfg()
    if a.cmd == "install":
        n = lib.install(a.file, a.name, not a.keep_case and cfg["lowercase"],
                        lambda c: "overwrite" if a.force else "skip")
        print(f"✔ '{n}': {len(lib.mods[n]['files'])} files")
    elif a.cmd == "list":
        for n, m in lib.mods.items():
            print(f"{'●' if m['enabled'] else '○'} {n}  ({len(m['files'])} files)")
    elif a.cmd == "enable":
        lib.enable(a.name, lambda c: "overwrite")
    elif a.cmd == "disable":
        lib.disable(a.name)
    elif a.cmd == "remove":
        lib.remove(a.name)
    elif a.cmd == "conflicts":
        c = lib.conflicts_report()
        for f, ns in c.items():
            print(f"{f}: {', '.join(ns)}")
        if not c:
            print("No conflicts found.")
    elif a.cmd == "backup":
        print(lib.backup())
    elif a.cmd == "restore":
        lib.restore(a.zip)
    elif a.cmd == "launch":
        launch(g)


if __name__ == "__main__":
    from nwnmods.cli import main

    main()
