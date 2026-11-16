import json
import re
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from ._common import Err
from .files import CONTENT_DIRS, _inside, extract, map_path


class Library:
    """Mod registry for a user folder (.nwnmods/manifest.json)."""

    def __init__(self, user):
        self.user = Path(user)
        self.base = self.user / ".nwnmods"
        self.mf = self.base / "manifest.json"
        self.d = {"mods": {}, "profiles": {}, "active_save_mod": None}
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
            if over:
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
    PARTY_FOLDERS = ("saves", "localvault", "servervault", "dmvault")

    def _save_set_dir(self, mod):
        key = (mod if mod else "_default")
        # sanitize
        key = re.sub(r'[\\/:*?"<>|]+', "-", str(key)).strip() or "_default"
        return self.base / "saves" / key

    def active_save_mod(self):
        return self.d.get("active_save_mod")

    def saves_list(self):
        """Return (rows, active_mod). rows: (folder, relpath, size, owner_mod)."""
        rows = []
        active = self.d.get("active_save_mod")
        for sub in self.PARTY_FOLDERS:
            d = self.user / sub
            if d.is_dir():
                for f in d.rglob("*"):
                    if f.is_file():
                        try:
                            rows.append((sub, f.relative_to(self.user).as_posix(), f.stat().st_size, active or "(none)"))
                        except OSError:
                            rows.append((sub, f.relative_to(self.user).as_posix(), 0, active or "(none)"))
        saves_root = self.base / "saves"
        if saves_root.is_dir():
            for mod_dir in sorted(saves_root.iterdir()):
                if not mod_dir.is_dir():
                    continue
                mod = mod_dir.name
                if mod == (active if active else "_default"):
                    continue
                label = "(default)" if mod == "_default" else mod
                for sub in self.PARTY_FOLDERS:
                    d = mod_dir / sub
                    if d.is_dir():
                        for f in d.rglob("*"):
                            if f.is_file():
                                try:
                                    rows.append((sub, f.relative_to(mod_dir).as_posix(), f.stat().st_size, label))
                                except OSError:
                                    rows.append((sub, f.relative_to(mod_dir).as_posix(), 0, label))
        return rows, active

    def saves_switch(self, mod):
        active = self.d.get("active_save_mod")
        if active == mod:
            return
        # stash current live saves into active's set
        active_key = active if active else "_default"
        stash_dir = self._save_set_dir(active_key)
        stash_dir.mkdir(parents=True, exist_ok=True)
        for sub in self.PARTY_FOLDERS:
            src = self.user / sub
            dst = stash_dir / sub
            if src.is_dir():
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    # merge into existing stash
                    for f in src.rglob("*"):
                        if f.is_file():
                            rel = f.relative_to(src)
                            (dst / rel).parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(f), str(dst / rel))
                    # remove now-empty dirs
                    for f in sorted(src.rglob("*"), reverse=True):
                        if f.is_dir():
                            try:
                                f.rmdir()
                            except OSError:
                                pass
                else:
                    shutil.move(str(src), str(dst))
        # restore target's set
        new_dir = self._save_set_dir(mod)
        for sub in self.PARTY_FOLDERS:
            src = new_dir / sub
            dst = self.user / sub
            if src.is_dir():
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    # move any leftover into stash of target? should be empty; merge just in case
                    for f in dst.rglob("*"):
                        if f.is_file():
                            rel = f.relative_to(dst)
                            (src / rel).parent.mkdir(parents=True, exist_ok=True)
                            shutil.move(str(f), str(src / rel))
                    for f in sorted(dst.rglob("*"), reverse=True):
                        if f.is_dir():
                            try:
                                f.rmdir()
                            except OSError:
                                pass
                shutil.move(str(src), str(dst))
        self.d["active_save_mod"] = mod
        self.save()

    def saves_archive(self, mod):
        d = self._save_set_dir(mod)
        if not d.exists():
            raise Err("No saved set for this mod.")
        dest = self.base / "saves_archive" / f"{mod}.zip"
        dest.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for f in d.rglob("*"):
                if f.is_file():
                    z.write(f, f.relative_to(d).as_posix())
                    n += 1
        if not n:
            dest.unlink(missing_ok=True)
            raise Err("No files to archive.")
        shutil.rmtree(d, ignore_errors=True)
        return dest

    def saves_restore(self, mod, from_archive=False):
        d = self._save_set_dir(mod)
        if from_archive:
            zp = self.base / "saves_archive" / f"{mod}.zip"
            if not zp.exists():
                raise Err("No archive for this mod.")
            d.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zp) as z:
                for m in z.infolist():
                    if not _inside(d, m.filename):
                        raise Err("Archive contains insecure paths.")
                z.extractall(d)
        if not d.exists():
            raise Err("No saved set for this mod.")
        self.saves_switch(mod)
        return d
