import html as htmlmod
import hashlib
import json
import re
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

from . import config
from ._common import Err, MAX_TOTAL_BYTES, UA


VAULT = "https://neverwintervault.org"
VAULT_GAMES = {"NWN:EE": "nwnee", "NWN 1": "nwn1", "All": "all"}
VAULT_TTL = 6 * 3600  # catalog is cached for 6h to avoid excessive requests
VAULT_CACHE_DEFAULT_MB = 200  # default disk cache limit for Vault images


# --------------------------------------------------------------------------- image disk cache
def vault_cache_dir(directory=None) -> Path:
    """Cache folder for downloaded Vault images (overridable for tests)."""
    return Path(directory) if directory else config.cfg_dir() / "vault_cache"


def _vault_cache_name(url: str) -> str:
    """Stable file name for a URL: sha1 + image extension from the URL path."""
    ext = Path(urlparse(url).path).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
        ext = ".img"
    return hashlib.sha1(url.encode("utf-8")).hexdigest() + ext


def vault_cache_get(url: str, directory=None) -> bytes | None:
    p = vault_cache_dir(directory) / _vault_cache_name(url)
    try:
        return p.read_bytes() if p.is_file() else None
    except OSError:
        return None


def vault_cache_size(directory=None) -> int:
    d = vault_cache_dir(directory)
    try:
        return sum(f.stat().st_size for f in d.glob("*") if f.is_file())
    except OSError:
        return 0


def _vault_cache_enforce(d: Path, limit_bytes: int) -> None:
    """Delete oldest files (by mtime) until the cache fits under limit_bytes."""
    try:
        files = [f for f in d.glob("*") if f.is_file()]
        files.sort(key=lambda f: f.stat().st_mtime)
    except OSError:
        return
    total = sum(f.stat().st_size for f in files)
    for f in files:
        if total <= limit_bytes:
            break
        try:
            sz = f.stat().st_size
            f.unlink()
            total -= sz
        except OSError:
            pass


def vault_cache_put(url: str, data: bytes, directory=None, limit_mb=None) -> None:
    """Store an image on disk and enforce the size limit (oldest files first)."""
    d = vault_cache_dir(directory)
    try:
        d.mkdir(parents=True, exist_ok=True)
        (d / _vault_cache_name(url)).write_bytes(data)
    except OSError:
        return
    if limit_mb is None:
        limit_mb = config.load_cfg().get("vault_cache_mb", VAULT_CACHE_DEFAULT_MB)
    try:
        limit_mb = max(1, int(limit_mb))
    except (TypeError, ValueError):
        limit_mb = VAULT_CACHE_DEFAULT_MB
    _vault_cache_enforce(d, limit_mb * 1024 * 1024)


def vault_cache_clear(directory=None) -> int:
    """Remove every cached image; returns the number of files deleted."""
    d = vault_cache_dir(directory)
    n = 0
    try:
        for f in d.glob("*"):
            if f.is_file():
                f.unlink()
                n += 1
    except OSError:
        pass
    return n


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
        parts = urlparse(url).path.strip("/").split("/")
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
    cache = config.CFG.parent / f"vault_{game}.json"
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
    pats = (r'<a\b[^>]*href="([^"]*pubdlcnt\.php\?[^\"]*fid=\d+[^\"]*)"[^>]*>(.*?)</a>',
            r'<a\b[^>]*href="([^"]*/files/project/\d+/files/[^\"]+)"[^>]*>(.*?)</a>')
    for pat in pats:
        for m in re.finditer(pat, html, re.S | re.I):
            href = urljoin(url, htmlmod.unescape(m.group(1)))
            label = _norm(htmlmod.unescape(re.sub(r"<[^>]+>", "", m.group(2))))
            if href not in seen:
                seen.add(href)
                files.append({"url": href, "name": label or Path(urlparse(href).path).name or "file"})
    # All project images (page order, deduped by file name: full-size wins over thumbnail);
    # "image" stays the first one (back compat).
    order, by_name = [], {}
    pat_img = r'(?:src|href)="([^"]*/files/(?:styles/[^/"]+/public/)?project/\d+/images/[^"]+)"'
    for m in re.finditer(pat_img, html, re.I):
        u = urljoin(url, htmlmod.unescape(m.group(1)))
        name = Path(urlparse(u).path).name.lower()
        is_thumb = "/styles/" in u
        if name not in by_name:
            by_name[name] = [u, is_thumb]
            order.append(name)
        elif by_name[name][1] and not is_thumb:
            by_name[name][0] = u  # same file, full-size version replaces the thumbnail
    imgs = [by_name[n][0] for n in order]
    image = imgs[0] if imgs else None
    return {"title": title, "url": url, "desc": desc, "fields": fields, "files": files,
            "image": image, "images": imgs}