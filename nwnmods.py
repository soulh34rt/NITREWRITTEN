#!/usr/bin/env python3
"""nwnmods - Professional Mod Manager for Neverwinter Nights (Enhanced Edition and Diamond).

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
        d.update(json.loads(CFG.read_text(encoding="utf-8")))
    except Exception:
        pass
    return d


def save_cfg(c: dict):
    CFG.parent.mkdir(parents=True, exist_ok=True)
    CFG.write_text(json.dumps(c, indent=2, ensure_ascii=False), encoding="utf-8")


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
        where = str(self.root) if self.root else "(solo carpeta de usuario)"
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
    """(tienda, carpeta base, profundidad de busqueda)."""
    h = Path.home()
    pfs = [Path(os.environ[v]) for v in ("ProgramFiles", "ProgramFiles(x86)") if os.environ.get(v)]
    drives = [Path(f"{c}:/") for c in "CDEFGH" if Path(f"{c}:/").exists()] if SYS == "Windows" else []
    out = []
    # GOG
    out += [("GOG", d / "GOG Games", 1) for d in drives]
    out += [("GOG", p / "GOG Galaxy/Games", 1) for p in pfs]
    out += [("GOG", h / "GOG Games", 1), ("GOG", h / "Games", 2), ("GOG", h / "Games/gog", 1)]
    # Beamdog (carpetas con ID numerico)
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
        games.append(Game(None, "EE", "carpeta de usuario"))
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
        raise Err("No hay carpeta de instalacion para iniciar el juego.")
    cands = sorted(game.root.glob("bin/*/nwmain*")) if game.edition == "EE" else [game.root / "nwmain.exe"]
    exe = next((c for c in cands if c.is_file() and c.suffix.lower() not in (".so", ".pdb", ".dll")), None)
    if not exe:
        raise Err("No encontre el ejecutable del juego.")
    cmd = [str(exe)]
    if SYS != "Windows" and exe.suffix.lower() == ".exe":
        cmd = ["wine"] + cmd
    subprocess.Popen(cmd, cwd=str(exe.parent))


# --------------------------------------------------------------------------- archivos
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


def _inside(out: Path, name: str) -> bool:
    dest = (out / name).resolve()
    root = out.resolve()
    return dest == root or str(dest).startswith(str(root) + os.sep)


def extract(src: Path, out: Path):
    if src.is_dir():
        shutil.copytree(src, out, dirs_exist_ok=True)
        return
    if zipfile.is_zipfile(src):
        with zipfile.ZipFile(src) as z:
            for m in z.infolist():
                if not _inside(out, m.filename):
                    raise Err("El archivo contiene rutas inseguras.")
            z.extractall(out)
        return
    if tarfile.is_tarfile(src):
        with tarfile.open(src) as t:
            for m in t.getmembers():
                if not _inside(out, m.name):
                    raise Err("El archivo contiene rutas inseguras.")
            try:
                t.extractall(out, filter="data")
            except TypeError:
                t.extractall(out)
        return
    if src.suffix.lower() == ".7z":
        try:
            import py7zr  # type: ignore
            with py7zr.SevenZipFile(src) as z:
                z.extractall(out)
            return
        except ImportError:
            pass
    tools = ["7z", "7zz", "7za", "7zr"]
    if SYS == "Windows":
        for pf in ("ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(pf):
                tools.append(str(Path(os.environ[pf]) / "7-Zip/7z.exe"))
    for t in tools:
        if shutil.which(t) or Path(t).is_file():
            subprocess.run([t, "x", "-y", f"-o{out}", str(src)], check=True, stdout=subprocess.DEVNULL)
            return
    if src.suffix.lower() == ".rar":
        for cmd in (["unrar", "x", "-y", str(src), str(out) + os.sep], ["bsdtar", "-xf", str(src), "-C", str(out)]):
            if shutil.which(cmd[0]):
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
                return
    if src.suffix.lower() in DEST or src.suffix.lower() in {".2da", ".nss", ".ncs", ".tga", ".dds", ".mdl"}:
        shutil.copy2(src, out / src.name)
        return
    raise Err("No pude abrir el archivo. Para .7z/.rar instala 7-Zip (7z) o py7zr.")


def download(url: str, dest_dir: Path, referer=None, fallback=None) -> Path:
    headers = {"User-Agent": UA}
    if referer:
        headers["Referer"] = referer
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=90) as r:
        if r.headers.get("Content-Type", "").lower().startswith("text/html"):
            raise Err("El sitio no devolvio un archivo (¿requiere iniciar sesion o cambio el enlace?).")
        name = None
        m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', r.headers.get("Content-Disposition", ""))
        if m:
            name = unquote(m.group(1))
        name = name or unquote(Path(urlparse(r.geturl()).path).name)
        if not name or name.lower().endswith((".php", ".html")):
            name = fallback or "descarga.zip"
        dest = dest_dir / Path(name).name
        with open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
    return dest


def fetch_bytes(url: str, timeout=30) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
        return r.read()


# --------------------------------------------------------------------------- Neverwinter Vault
VAULT = "https://neverwintervault.org"
VAULT_GAMES = {"NWN:EE": "nwnee", "NWN 1": "nwn1", "Todos": "all"}
VAULT_TTL = 6 * 3600  # el catalogo se guarda 6 h para no pedirlo en cada clic


def http_get(url: str, timeout=40) -> str:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=timeout) as r:
        return r.read().decode(r.headers.get_content_charset() or "utf-8", "replace")


def _norm(t: str) -> str:
    return " ".join(t.split())


class _CatalogParser(HTMLParser):
    """Tablas del catalogo: Captura | Nombre | Autor | Descripcion | Publicado | Actualizado | Votos."""

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
        parts = urlparse(url).path.strip("/").split("/")  # project/<juego>/<categoria...>/<slug>
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
        raise Err("No pude leer el catalogo del Vault (¿sin conexion o cambio el sitio?).")
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


FIELD_LABELS = {"Category": "Categoria", "Game": "Juego", "Requirements": "Requisitos",
                "Language": "Idioma", "Tags": "Etiquetas", "Permissions": "Permisos"}


def parse_project(html: str, url: str) -> dict:
    """Extrae titulo, descripcion, datos y enlaces de descarga de la pagina de un proyecto (mejor esfuerzo)."""
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
            fields["Autor"] = m.group(1).strip()
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
                files.append({"url": href, "name": label or Path(urlparse(href).path).name or "archivo"})
    m = re.search(r'src="([^"]*/files/(?:styles/[^/"]+/public/)?project/\d+/images/[^"]+)"', html)
    image = urljoin(url, htmlmod.unescape(m.group(1))) if m else None
    return {"title": title, "url": url, "desc": desc, "fields": fields, "files": files, "image": image}


# --------------------------------------------------------------------------- biblioteca
class Library:
    """Registro de mods de una carpeta de usuario (.nwnmods/manifest.json)."""

    def __init__(self, user):
        self.user = Path(user)
        self.base = self.user / ".nwnmods"
        self.mf = self.base / "manifest.json"
        self.d = {"mods": {}, "profiles": {}}
        if self.mf.exists():
            try:
                self.d.update(json.loads(self.mf.read_text(encoding="utf-8")))
            except Exception:
                pass

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
        for src, rel in items:
            key, tgt = rel.as_posix(), self.user / rel
            if tgt.exists() or tgt.is_symlink():
                if action == "skip":
                    continue
                sp = self.base / "stash" / name / rel
                sp.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(tgt), str(sp))
                stash[key] = owners.get(key)
            tgt.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(tgt))
            files.append(key)
        return files, stash

    def _conflicts(self, items):
        owners = self.owners()
        return [(r.as_posix(), owners.get(r.as_posix()) or "(archivo existente)")
                for _, r in items if (self.user / r).exists()]

    def install(self, archive, name=None, lowercase=True, on_conflict=None, source=None) -> str:
        archive = Path(archive)
        if not archive.exists():
            raise Err(f"No existe: {archive}")
        if not name:
            name = archive.name if archive.is_dir() else re.sub(r"\.(tar\.\w+|\w+)$", "", archive.name)
        if name in self.mods:
            raise Err(f"'{name}' ya esta instalado.")
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
                raise Err("El archivo no contiene nada instalable.")
            conflicts = self._conflicts(items)
            action = "overwrite"
            if conflicts:
                action = on_conflict(conflicts) if on_conflict else "skip"
                if action == "cancel":
                    raise Err("Instalacion cancelada.")
            files, stash = self._deploy(name, items, action, self.owners())
        if not files:
            raise Err("No se instalo nada (todo existia ya).")
        self.mods[name] = {"files": files, "stash": stash, "enabled": True,
                           "date": datetime.now().isoformat(timespec="seconds"), "source": source or str(archive)}
        self.save()
        return name

    def _undeploy(self, name, keep=None):
        m = self.mods[name]
        for key in m["files"]:
            rel = Path(key)
            tgt = self.user / rel
            over = [n for n, o in self.mods.items()
                    if n != name and o["enabled"] and o["stash"].get(key) == name]
            if over:  # otro mod sobrescribio este archivo; la version de 'name' esta en su stash
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
            raise Err("No quedan archivos guardados de este mod.")
        conflicts = self._conflicts(items)
        if conflicts:
            action = on_conflict(conflicts) if on_conflict else "cancel"
            if action != "overwrite":
                raise Err("Activacion cancelada por conflictos.")
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
        """Otro mod Y sobrescribio archivos de 'name': Y hereda lo que 'name' tenia guardado debajo."""
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
            raise Err(f"'{name}' no esta registrado.")
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
                    rows.append((sub, key, f.stat().st_size, owners.get(key, "—")))
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
            raise Err("No hay guardados ni personajes que respaldar.")
        return dest

    def backups(self):
        d = self.base / "backups"
        return sorted(d.glob("*.zip"), reverse=True) if d.is_dir() else []

    def restore(self, zpath):
        with zipfile.ZipFile(zpath) as z:
            for m in z.infolist():
                if not _inside(self.user, m.filename):
                    raise Err("Copia con rutas inseguras.")
            z.extractall(self.user)


# --------------------------------------------------------------------------- GUI
def run_gui(initial_user=None):
    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("NWN Mod Manager - Profesional")
            self.geometry("1100x700")
            self.minsize(900, 600)
            
            style = ttk.Style()
            if SYS == "Windows":
                style.theme_use("vista")
            elif SYS == "Darwin":
                style.theme_use("aqua")
            else:
                style.theme_use("clam")
                
            # Configuracion de colores y fuentes
            style.configure("TFrame", background="#f0f0f0")
            style.configure("TLabel", background="#f0f0f0", font=("Segoe UI", 10))
            style.configure("TButton", font=("Segoe UI", 9))
            style.configure("Header.TLabel", font=("Segoe UI", 12, "bold"), foreground="#2c3e50")
            style.configure("Treeview", font=("Segoe UI", 9), rowheight=25)
            style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
            self.cfg = load_cfg()
            self.games, self.game, self.lib = [], None, None
            self.vault_items, self.vault_info, self.vault_sel = [], {}, None
            self.vault_shown, self.vault_busy, self.vault_sort, self._img = [], False, ("posted", True), None
            self.v_lower = tk.BooleanVar(value=self.cfg["lowercase"])
            self.v_policy = tk.StringVar(value=self.cfg["policy"])
            self._build()
            self.after(100, lambda: self.detect(initial_user))

        # ---- construccion
        def _build(self):
            self.configure(background="#f0f0f0")
            top = ttk.Frame(self, padding=8)
            top.pack(fill="x")
            ttk.Label(top, text="Instalación de NWN:", style="Header.TLabel").pack(side="left", padx=(0, 10))
            self.combo = ttk.Combobox(top, state="readonly", width=60)
            self.combo.pack(side="left", padx=6, fill="x", expand=True)
            self.combo.bind("<<ComboboxSelected>>", lambda e: self.set_game(self.combo.current()))
            
            btn_frame = ttk.Frame(top)
            btn_frame.pack(side="left", padx=5)
            for txt, fn in (("Detect", self.detect), ("Add folder…", self.add_folder),
                            ("Open data", lambda: self.game and open_path(self.game.user)),
                            ("▶ Play", self.play)):
                ttk.Button(btn_frame, text=txt, command=fn).pack(side="left", padx=2)
            self.info = ttk.Label(self, padding=(10, 0), foreground="#555")
            self.info.pack(fill="x")
            nb = ttk.Notebook(self)
            nb.pack(fill="both", expand=True, padx=8, pady=8)
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
            self._btns(p, (("Instalar archivo(s)…", self.install_files), ("Instalar carpeta…", self.install_dir),
                           ("Instalar desde URL…", self.install_url), ("Activar", self.enable),
                           ("Desactivar", self.disable), ("Desinstalar", self.remove),
                           ("Ver archivos", self.show_files), ("Conflictos", self.show_conflicts)))
            self.q_mods = tk.StringVar()
            e = ttk.Entry(p, textvariable=self.q_mods)
            e.pack(fill="x", pady=(0, 6))
            self.q_mods.trace_add("write", lambda *a: self.refresh_mods())
            self.tv_mods = self._tree(p, ("nombre", "estado", "archivos", "fecha", "origen"),
                                      (260, 100, 80, 100, 380))

        def _build_content(self):
            p = self.tab_cont
            f = ttk.Frame(p)
            f.pack(fill="x", pady=(0, 6))
            self.cat = ttk.Combobox(f, state="readonly", values=["(todo)"] + CONTENT_DIRS, width=16)
            self.cat.current(0)
            self.cat.pack(side="left")
            self.cat.bind("<<ComboboxSelected>>", lambda e: self.refresh_content())
            self.q_cont = tk.StringVar()
            ttk.Entry(f, textvariable=self.q_cont).pack(side="left", fill="x", expand=True, padx=6)
            self.q_cont.trace_add("write", lambda *a: self.refresh_content())
            self.lbl_cont = ttk.Label(f)
            self.lbl_cont.pack(side="left")
            self.tv_cont = self._tree(p, ("carpeta", "archivo", "tamaño", "mod"), (110, 520, 90, 220))

        def _build_profiles(self):
            p = self.tab_prof
            ttk.Label(p, text="Un perfil recuerda qué mods están activos y los cambia de un clic.",
                      foreground="#555").pack(anchor="w", pady=(0, 6))
            self._btns(p, (("Guardar perfil actual…", self.prof_save), ("Aplicar", self.prof_apply),
                           ("Borrar", self.prof_del)))
            self.lb_prof = tk.Listbox(p)
            self.lb_prof.pack(fill="both", expand=True)

        def _build_backups(self):
            p = self.tab_bak
            ttk.Label(p, text="Copias de guardados y personajes (saves, localvault, servervault, dmvault).",
                      foreground="#555").pack(anchor="w", pady=(0, 6))
            self._btns(p, (("Crear copia", self.bak_make), ("Restaurar seleccionada", self.bak_restore),
                           ("Abrir carpeta", lambda: self.lib and open_path(self.lib.base / "backups"))))
            self.lb_bak = tk.Listbox(p)
            self.lb_bak.pack(fill="both", expand=True)

        def _build_settings(self):
            p = self.tab_set
            ttk.Checkbutton(p, text="Nombres en minúsculas para hak/tlk/override (recomendado en Linux)",
                            variable=self.v_lower, command=self.save_settings).pack(anchor="w", pady=4)
            ttk.Label(p, text="Cuando un archivo ya existe al instalar:").pack(anchor="w", pady=(10, 0))
            for v, t in (("ask", "Preguntar"), ("overwrite", "Sobrescribir (se guarda copia y se restaura al desinstalar)"),
                         ("skip", "Omitir esos archivos")):
                ttk.Radiobutton(p, text=t, value=v, variable=self.v_policy,
                                command=self.save_settings).pack(anchor="w", padx=16)
            ttk.Button(p, text="Cambiar carpeta de datos del juego seleccionado…",
                       command=self.change_user).pack(anchor="w", pady=14)
            ttk.Label(p, text=f"Configuración: {CFG}", foreground="#555").pack(anchor="w")

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
                self.log(f"✘ Error inesperado: {e!r}")
                messagebox.showerror("Error inesperado", repr(e))
            finally:
                self.configure(cursor="")

        def save_settings(self):
            self.cfg["lowercase"], self.cfg["policy"] = self.v_lower.get(), self.v_policy.get()
            save_cfg(self.cfg)

        def ask_conflict(self, conf):
            pol = self.v_policy.get()
            if pol in ("overwrite", "skip"):
                return pol
            lines = "\n".join(f"• {r}  ←  {o}" for r, o in conf[:12])
            more = f"\n… y {len(conf) - 12} más" if len(conf) > 12 else ""
            r = messagebox.askyesnocancel(
                "Archivos en conflicto",
                f"{len(conf)} archivo(s) ya existen:\n\n{lines}{more}\n\nSí = sobrescribir (con copia de respaldo)\n"
                "No = omitirlos\nCancelar = abortar")
            return "overwrite" if r else ("skip" if r is False else "cancel")

        # ---- instalaciones
        def detect(self, initial_user=None):
            self.games = self.guard(detect_all, self.cfg) or []
            if isinstance(initial_user, (str, Path)):
                self.games.insert(0, Game(initial_user, "Personalizada", "manual", initial_user))
            self.combo["values"] = [g.label for g in self.games]
            self.log(f"Instalaciones detectadas: {len(self.games)}")
            if not self.games:
                self.game = self.lib = None
                self.info.configure(text="No encontré ninguna instalación. Usa «Agregar carpeta…».")
                self.refresh_all()
                return
            last = self.cfg.get("last")
            idx = next((i for i, g in enumerate(self.games) if str(g.root) == last), 0)
            self.combo.current(idx)
            self.set_game(idx)

        def add_folder(self):
            d = filedialog.askdirectory(title="Carpeta de instalación (o de datos) de Neverwinter Nights")
            if not d:
                return
            ed = classify(Path(d))
            if ed:
                self.cfg["manual"].append({"root": d, "edition": ed})
            elif messagebox.askyesno("Carpeta desconocida",
                                     "No parece una instalación de NWN. ¿Usarla como carpeta de datos "
                                     "(donde van hak, override, modules…)?"):
                self.cfg["manual"].append({"root": d, "edition": "Personalizada", "user": d})
            else:
                return
            save_cfg(self.cfg)
            self.detect()

        def set_game(self, i):
            self.game = self.games[i]
            self.lib = Library(self.game.user)
            self.cfg["last"] = str(self.game.root)
            save_cfg(self.cfg)
            self.info.configure(text=f"Datos del juego: {self.game.user}")
            want = "NWN 1" if self.game.edition == "Diamond" else "NWN:EE"
            if not self.vault_items:
                self.v_game.set(want)
            self.refresh_all()

        def change_user(self):
            if not self.game:
                return
            d = filedialog.askdirectory(title="Carpeta de datos (hak, override, modules…)")
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
                        n, "Activo" if m["enabled"] else "Desactivado", len(m["files"]),
                        m["date"][:10], m["source"] if m["source"].startswith("http") else Path(m["source"]).name))

        def refresh_content(self):
            self.tv_cont.delete(*self.tv_cont.get_children())
            self.lbl_cont.configure(text="")
            if not self.lib:
                return
            cat = self.cat.get()
            rows = self.lib.scan(None if cat == "(todo)" else cat)
            q = self.q_cont.get().lower()
            shown = [r for r in rows if q in r[1].lower() or q in r[3].lower()]
            for sub, key, size, owner in shown[:5000]:
                self.tv_cont.insert("", "end", values=(sub, key, f"{size / 1024:,.0f} KB", owner))
            self.lbl_cont.configure(text=f"{len(shown)} archivos" + (" (mostrando 5000)" if len(shown) > 5000 else ""))

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
                messagebox.showinfo("NWN Mod Manager", "Selecciona uno o más mods de la lista.")
            return s

        def _need_lib(self):
            if not self.lib:
                messagebox.showinfo("NWN Mod Manager", "Primero elige o agrega una instalación.")
            return self.lib

        def do_install(self, paths):
            for p in paths:
                n = self.guard(self.lib.install, p, None, self.v_lower.get(), self.ask_conflict)
                if n:
                    self.log(f"✔ Instalado: {n}")
            self.refresh_all()

        def install_files(self):
            if not self._need_lib():
                return
            ps = filedialog.askopenfilenames(title="Mods", filetypes=[
                ("Mods y paquetes", "*.zip *.7z *.rar *.tar *.gz *.bz2 *.xz *.hak *.tlk *.mod *.erf *.nwm"),
                ("Todos", "*.*")])
            if ps:
                self.do_install(ps)

        def install_dir(self):
            if self._need_lib():
                d = filedialog.askdirectory(title="Carpeta del mod ya descomprimido")
                if d:
                    self.do_install([d])

        def install_url(self):
            if not self._need_lib():
                return
            url = simpledialog.askstring("Instalar desde URL", "Enlace directo de descarga:")
            if not url:
                return
            self.log(f"Descargando {url} …")
            tmp = Path(tempfile.mkdtemp(prefix="nwnmods-"))

            def worker():
                try:
                    f = download(url, tmp)
                    self.after(0, lambda: (self.do_install([f]), shutil.rmtree(tmp, ignore_errors=True)))
                except Exception as e:  # noqa: BLE001
                    self.after(0, lambda: (self.log(f"✘ Descarga fallida: {e}"),
                                           messagebox.showerror("Descarga fallida", str(e))))

            threading.Thread(target=worker, daemon=True).start()

        def enable(self):
            for n in self.selected():
                self.guard(self.lib.enable, n, self.ask_conflict)
                self.log(f"Activado: {n}")
            self.refresh_all()

        def disable(self):
            for n in self.selected():
                self.guard(self.lib.disable, n)
                self.log(f"Desactivado: {n}")
            self.refresh_all()

        def remove(self):
            s = self.selected()
            if s and messagebox.askyesno("Desinstalar", f"¿Desinstalar {len(s)} mod(s)?"):
                for n in s:
                    self.guard(self.lib.remove, n)
                    self.log(f"Desinstalado: {n}")
                self.refresh_all()

        def _text_window(self, title, text):
            w = tk.Toplevel(self)
            w.title(title)
            w.geometry("720x460")
            t = tk.Text(w, wrap="none")
            t.insert("1.0", text)
            t.configure(state="disabled")
            t.pack(fill="both", expand=True)

        def show_files(self):
            for n in self.selected()[:1]:
                m = self.lib.mods[n]
                self._text_window(f"{n} — {len(m['files'])} archivos", "\n".join(m["files"]))

        def show_conflicts(self):
            if not self._need_lib():
                return
            c = self.lib.conflicts_report()
            txt = "\n".join(f"{f}\n    mods: {', '.join(ns)}  (gana el último instalado)" for f, ns in c.items())
            self._text_window("Conflictos entre mods", txt or "Sin conflictos entre mods activos.")

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
            self.v_lbl.configure(text="Error de red")
            self.log(f"✘ {e}")

        def _tab_changed(self, _e=None):
            if self.nb.select() == str(self.tab_vault) and not self.vault_items and not self.vault_busy:
                self.vault_load()

        def _build_vault(self):
            p = self.tab_vault
            top = ttk.Frame(p)
            top.pack(fill="x", pady=(0, 6))
            self.v_game = tk.StringVar(value="NWN:EE")
            g = ttk.Combobox(top, state="readonly", width=8, values=list(VAULT_GAMES), textvariable=self.v_game)
            g.pack(side="left")
            g.bind("<<ComboboxSelected>>", lambda e: self.vault_load())
            self.v_cat = ttk.Combobox(top, state="readonly", width=18, values=["(todas)"])
            self.v_cat.set("(todas)")
            self.v_cat.pack(side="left", padx=4)
            self.v_cat.bind("<<ComboboxSelected>>", lambda e: self.vault_filter())
            self.v_q = tk.StringVar()
            ttk.Entry(top, textvariable=self.v_q).pack(side="left", fill="x", expand=True, padx=4)
            self.v_q.trace_add("write", lambda *a: self.vault_filter())
            ttk.Button(top, text="Actualizar", command=lambda: self.vault_load(True)).pack(side="left", padx=2)
            self.v_lbl = ttk.Label(top, foreground="#555")
            self.v_lbl.pack(side="left", padx=6)
            cols = ("nombre", "categoria", "autor", "publicado", "votos")
            self.tv_vault = self._tree(p, cols, (330, 110, 130, 90, 60))
            for c in cols:
                self.tv_vault.heading(c, text=c.capitalize(), command=lambda c=c: self.vault_sort_by(c))
            self.tv_vault.bind("<<TreeviewSelect>>", self.vault_select)
            det = ttk.Frame(p)
            det.pack(fill="x", pady=(6, 0))
            self.v_img = ttk.Label(det, width=28, anchor="center")
            self.v_img.pack(side="left", padx=(0, 8))
            self.v_det = tk.Text(det, height=10, wrap="word", state="disabled")
            self.v_det.pack(side="left", fill="both", expand=True)
            side = ttk.Frame(det)
            side.pack(side="left", fill="y", padx=(8, 0))
            ttk.Label(side, text="Archivos:").pack(anchor="w")
            self.lb_vfiles = tk.Listbox(side, height=4, width=34, exportselection=False)
            self.lb_vfiles.pack(fill="x")
            ttk.Button(side, text="⬇ Descargar e instalar", command=self.vault_install).pack(fill="x", pady=(6, 2))
            ttk.Button(side, text="Abrir página", command=lambda: self.vault_sel and
                       webbrowser.open(self.vault_sel["url"])).pack(fill="x")

        def vault_load(self, force=False):
            if self.vault_busy:
                return
            self.vault_busy = True
            self.v_lbl.configure(text="Cargando catálogo…")
            game = VAULT_GAMES[self.v_game.get()]

            def ok(items):
                self.vault_busy = False
                self.vault_items = items
                self.v_cat.configure(values=["(todas)"] + sorted({i["cat"] for i in items if i["cat"]}))
                self.v_cat.set("(todas)")
                self.log(f"Vault: {len(items)} proyectos ({self.v_game.get()})")
                self.vault_filter()

            self.bg(lambda: vault_catalog(game, force), lambda i: ok(i))

        def vault_sort_by(self, col):
            cur, desc = self.vault_sort
            self.vault_sort = (col, not desc if cur == col else col in ("publicado", "votos"))
            self.vault_filter()

        def vault_filter(self):
            keys = {"nombre": "title", "categoria": "cat", "autor": "author", "publicado": "posted", "votos": "votes"}
            col, desc = self.vault_sort
            k = keys.get(col, col)
            cat, q = self.v_cat.get(), self.v_q.get().lower().strip()
            rows = [i for i in self.vault_items
                    if (cat in ("", "(todas)") or i["cat"] == cat)
                    and (not q or q in f'{i["title"]} {i["author"]} {i["desc"]} {i["cat"]}'.lower())]
            rows.sort(key=lambda i: i[k].lower() if isinstance(i[k], str) else i[k], reverse=desc)
            self.vault_shown = rows[:3000]
            self.tv_vault.delete(*self.tv_vault.get_children())
            for n, i in enumerate(self.vault_shown):
                self.tv_vault.insert("", "end", iid=str(n),
                                     values=(i["title"], i["cat"], i["author"], i["posted"], i["votes"]))
            self.v_lbl.configure(text=f"{len(rows)} resultados" if self.vault_items else "")

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
            self._vtext(f'{it["title"]}\n{it["author"]} · {it["cat"]} · {it["posted"]}\n\n{it["desc"]}\n\nCargando detalles…')
            if it["url"] in self.vault_info:
                return self._vault_details(it, self.vault_info[it["url"]])

            def ok(info):
                self.vault_info[it["url"]] = info
                if self.vault_sel is it:
                    self._vault_details(it, info)

            self.bg(lambda: parse_project(http_get(it["url"]), it["url"]), lambda i: ok(i),
                    lambda e: (self._vtext(f'{it["title"]}\n\n{it["desc"]}\n\n(No pude cargar los detalles: {e})')))

        def _vault_details(self, it, info):
            lines = [info["title"] or it["title"], ""]
            lines += [f"{k}: {v}" for k, v in info["fields"].items()]
            lines += ["", info["desc"] or it["desc"]]
            if not info["files"]:
                lines += ["", "(Sin enlace de descarga directo: usa «Abrir página».)"]
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
                try:  # JPG/WebP: solo si hay Pillow instalado
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
                return messagebox.showinfo("Vault", "Selecciona un mod de la lista.")
            if not info or not info["files"]:
                return messagebox.showinfo("Vault", "Este proyecto no tiene descarga directa. Usa «Abrir página».")
            sel = self.lb_vfiles.curselection()
            f = info["files"][sel[0] if sel else 0]
            name = re.sub(r'[\\/:*?"<>|]+', "-", info["title"] or it["title"]).strip()[:80]
            self.log(f"Descargando {f['name']} …")
            tmp = Path(tempfile.mkdtemp(prefix="nwnmods-"))

            def done(path):
                n = self.guard(self.lib.install, path, name, self.v_lower.get(), self.ask_conflict, it["url"])
                shutil.rmtree(tmp, ignore_errors=True)
                if n:
                    self.log(f"✔ Instalado desde el Vault: {n}")
                    self.refresh_all()

            def fail(e):
                shutil.rmtree(tmp, ignore_errors=True)
                self.log(f"✘ Descarga fallida: {e}")
                messagebox.showerror("Descarga fallida", str(e))

            self.bg(lambda: download(f["url"], tmp, it["url"], f["name"]), lambda p: done(p), lambda e: fail(e))

        # ---- perfiles y copias
        def prof_name(self):
            s = self.lb_prof.curselection()
            return self.lb_prof.get(s[0]).rsplit("  (", 1)[0] if s else None

        def prof_save(self):
            if self._need_lib():
                n = simpledialog.askstring("Perfil", "Nombre del perfil:")
                if n:
                    self.lib.save_profile(n)
                    self.refresh_profiles()

        def prof_apply(self):
            n = self.prof_name()
            if n:
                self.guard(self.lib.apply_profile, n, self.ask_conflict)
                self.log(f"Perfil aplicado: {n}")
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
                    self.log(f"✔ Copia creada: {p.name}")
                    self.refresh_backups()

        def bak_restore(self):
            s = self.lb_bak.curselection()
            if s and messagebox.askyesno("Restaurar", "Esto sobrescribe guardados con el mismo nombre. ¿Continuar?"):
                self.guard(self.lib.restore, self.lib.base / "backups" / self.lb_bak.get(s[0]))
                self.log("✔ Copia restaurada")

    App().mainloop()


# --------------------------------------------------------------------------- CLI
def pick(args):
    if args.user:
        return Game(args.user, "Personalizada", "manual", args.user)
    games = detect_all()
    if not games:
        raise Err("No encontre NWN. Usa --user RUTA_DE_DATOS o abre la interfaz y agrega la carpeta.")
    return games[args.game]


def cli(argv):
    ap = argparse.ArgumentParser(prog="nwnmods", description="Mod manager para Neverwinter Nights")
    ap.add_argument("--game", type=int, default=0, help="indice mostrado por 'detect'")
    ap.add_argument("--user", help="carpeta de datos (hak, override, modules...)")
    sp = ap.add_subparsers(dest="cmd")
    sp.add_parser("gui")
    sp.add_parser("detect")
    i = sp.add_parser("install")
    i.add_argument("archivo")
    i.add_argument("--name")
    i.add_argument("--force", action="store_true", help="sobrescribir (con respaldo)")
    i.add_argument("--keep-case", action="store_true")
    for c in ("enable", "disable", "remove"):
        sp.add_parser(c).add_argument("nombre")
    for c in ("list", "conflicts", "backup", "launch"):
        sp.add_parser(c)
    sp.add_parser("restore").add_argument("zip")
    a = ap.parse_args(argv)
    if a.cmd in (None, "gui"):
        return run_gui(a.user)
    if a.cmd == "detect":
        for n, g in enumerate(detect_all()):
            print(f"[{n}] {g.label}\n     datos: {g.user}")
        return
    g = pick(a)
    lib = Library(g.user)
    cfg = load_cfg()
    if a.cmd == "install":
        n = lib.install(a.archivo, a.name, not a.keep_case and cfg["lowercase"],
                        lambda c: "overwrite" if a.force else "skip")
        print(f"✔ '{n}': {len(lib.mods[n]['files'])} archivos")
    elif a.cmd == "list":
        for n, m in lib.mods.items():
            print(f"{'●' if m['enabled'] else '○'} {n}  ({len(m['files'])} archivos)")
    elif a.cmd == "enable":
        lib.enable(a.nombre, lambda c: "overwrite")
    elif a.cmd == "disable":
        lib.disable(a.nombre)
    elif a.cmd == "remove":
        lib.remove(a.nombre)
    elif a.cmd == "conflicts":
        c = lib.conflicts_report()
        for f, ns in c.items():
            print(f"{f}: {', '.join(ns)}")
        if not c:
            print("Sin conflictos.")
    elif a.cmd == "backup":
        print(lib.backup())
    elif a.cmd == "restore":
        lib.restore(a.zip)
    elif a.cmd == "launch":
        launch(g)


if __name__ == "__main__":
    try:
        cli(sys.argv[1:])
    except Err as e:
        sys.exit(f"Error: {e}")
