import os
import re
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

from ._common import Err, MAX_ENTRIES, MAX_FILE_BYTES, MAX_NAME_LEN, MAX_TOTAL_BYTES, SYS, UA


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


def map_path(rel: Path, lowercase: bool, ext_map=None, ignore_ext=None, ignore_names=None):
    parts = list(rel.parts)
    name = parts[-1]
    ext = Path(name).suffix.lower()
    _ignore_names = set((ignore_names if ignore_names is not None else IGNORE_NAMES))
    _ignore_ext = set((ignore_ext if ignore_ext is not None else IGNORE_EXT))
    if (name.lower() in _ignore_names or ext in _ignore_ext or name.startswith("._")
            or any(p.startswith("__MACOSX") for p in parts)):
        return None
    for i, p in enumerate(parts[:-1]):
        if p.lower() in KNOWN:
            sub = [p.lower()] + parts[i + 1:]
            if lowercase and sub[0] in LOWER_DIRS:
                sub = [sub[0]] + [x.lower() for x in sub[1:]]
            return Path(*sub)
    _ext_map = ext_map if ext_map is not None else DEST
    sub = _ext_map.get(ext)
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
    try:
        dest = out / name
    except Exception:
        return False
    try:
        dest.relative_to(out)
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
                p.unlink()
            except OSError:
                raise Err("Archive contains symlink entries.")


def extract(src: Path, out: Path):
    if src.is_dir():
        shutil.copytree(src, out, dirs_exist_ok=True)
        return
    if src.suffix.lower() in DEST or src.suffix.lower() in {".2da", ".nss", ".ncs", ".tga", ".dds", ".mdl"}:
        if src.stat().st_size > MAX_FILE_BYTES:
            raise Err("File too large.")
        shutil.copy2(src, out / src.name)
        return

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
                mode = (m.external_attr >> 16) & 0xFFFF
                if (mode & 0xF000) == 0xA000:
                    raise Err("Archive contains symlink entries.")
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
                if m.issym() or m.islnk() or m.isdev() or m.ischr() or m.isblk() or m.isfifo():
                    raise Err("Archive contains unsafe entry types.")
            try:
                t.extractall(out, filter="data")
            except TypeError:
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

    if src.suffix.lower() == ".7z":
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
        tools = ["7z", "7zz", "7za", "7zr"]
        if SYS == "Windows":
            for pf in ("ProgramFiles", "ProgramFiles(x86)"):
                if os.environ.get(pf):
                    tools.append(str(Path(os.environ[pf]) / "7-Zip/7z.exe"))
        for tool in tools:
            if shutil.which(tool) or Path(tool).is_file():
                subprocess.run([tool, "x", "-y", f"-o{out}", str(src)], check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                _strip_symlinks(out)
                for p in out.rglob("*"):
                    try:
                        p.relative_to(out)
                    except ValueError:
                        raise Err("Archive contains insecure paths.")
                return
        raise Err("Could not open .7z archive. Install 7-Zip (7z) or py7zr.")

    if src.suffix.lower() == ".rar":
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
        for cmd in (["unrar", "x", "-y", str(src), str(out) + os.sep],
                    ["bsdtar", "-xf", str(src), "-C", str(out)]):
            if shutil.which(cmd[0]):
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                _strip_symlinks(out)
                return
        raise Err("Could not open .rar archive. Install unrar/bsdtar or rarfile.")

    tools = ["7z", "7zz", "7za", "7zr"]
    if SYS == "Windows":
        for pf in ("ProgramFiles", "ProgramFiles(x86)"):
            if os.environ.get(pf):
                tools.append(str(Path(os.environ[pf]) / "7-Zip/7z.exe"))
    for tool in tools:
        if shutil.which(tool) or Path(tool).is_file():
            subprocess.run([tool, "x", "-y", f"-o{out}", str(src)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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