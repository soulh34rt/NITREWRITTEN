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
        self.d = {"mods": {}, "profiles": {}, "groups": {}, "active_save_mod": None}
        if self.mf.exists():
            try:
                self.d.update(json.loads(self.mf.read_text(encoding="utf-8")))
            except Exception as e:  # noqa: BLE001
                sys.stderr.write(f"nwnmods: could not read manifest {self.mf}: {e!r}\n")
        self.d.setdefault("groups", {})
        self._migrate()

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
            d = self.content_dir(sub)
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
            d = self.content_dir(sub)
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
        p = self.content_path(rel).parent
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
                key, tgt = rel.as_posix(), self.content_path(rel)
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
                try:
                    from .config import load_cfg as _lc
                except Exception:
                    import nwnmods as _n
                    _lc = getattr(_n, 'load_cfg', lambda: {})
                _cfg = _lc() if callable(_lc) else {}
                rel = map_path(f.relative_to(t), lowercase,
                               ext_map=_cfg.get('ext_map'),
                               ignore_ext=_cfg.get('ignore_ext'),
                               ignore_names=_cfg.get('ignore_names'))
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
            tgt = self.content_path(rel)
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

    def disable(self, name, force_deps=False):
        m = self._get(name)
        if not m["enabled"]:
            return
        self.check_dependents_for_disable(name, force=force_deps)
        self._undeploy(name, keep=self.base / "disabled" / name)
        self._relink(name)
        m["enabled"], m["stash"] = False, {}
        shutil.rmtree(self.base / "stash" / name, ignore_errors=True)
        self.save()

    def enable(self, name, on_conflict=None, force_deps=False):
        m = self._get(name)
        if m["enabled"]:
            return
        self.check_deps_for_enable(name, force=force_deps)
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

    def remove(self, name, force_deps=False):
        m = self._get(name)
        if m["enabled"]:
            self.check_dependents_for_disable(name, force=force_deps)
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


    def _migrate(self):
        for name, m in (self.d.get("mods") or {}).items():
            m.setdefault("requires", [])
            m.setdefault("dependents", [])
            m.setdefault("notes", "")
            m.setdefault("rating", 0)
            m.setdefault("vault_url", "")
            m.setdefault("author", "")
            m.setdefault("version", "")
            m.setdefault("group", "")


    def requires(self, name):
        return list(self.mods[name].get("requires", [])) if name in self.mods else []

    def set_requires(self, name, requires):
        m = self._get(name)
        m["requires"] = [r for r in (requires or []) if r]
        self.save()

    def dependents(self, name):
        return [n for n, m in self.mods.items() if name in (m.get("requires") or [])]

    def missing_deps(self, name):
        reqs = self.requires(name)
        missing = []
        for r in reqs:
            if r not in self.mods:
                missing.append((r, "missing"))
            elif not self.mods[r].get("enabled", False):
                missing.append((r, "disabled"))
        return missing

    def check_deps_for_enable(self, name, force=False):
        md = self.missing_deps(name)
        if md and not force:
            raise Err("Missing/disabled dependencies for '{}': {}".format(
                name, ", ".join(f"{r} ({s})" for r, s in md)))
        # reverse: others depend on it? only relevant for disable/remove
        return md

    def check_dependents_for_disable(self, name, force=False):
        deps = self.dependents(name)
        active_deps = [d for d in deps if self.mods[d].get("enabled", False)]
        if active_deps and not force:
            raise Err("'{}' is required by active mods: {}".format(name, ", ".join(active_deps)))
        return active_deps


    def original_snapshot_dir(self):
        return self.base / "original"

    def has_original_snapshot(self):
        return self.d.get("original_snapshot", False) and self.original_snapshot_dir().is_dir()

    def create_original_snapshot(self):
        if self.has_original_snapshot():
            return self.original_snapshot_dir()
        d = self.original_snapshot_dir()
        d.mkdir(parents=True, exist_ok=True)
        for sub in CONTENT_DIRS:
            src = self.content_dir(sub)
            if src.is_dir():
                dst = d / sub
                if dst.exists():
                    shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(src, dst)
        self.d["original_snapshot"] = True
        self.save()
        return d

    def changed_files(self):
        """Return list of (relpath, 'changed'|'new'|'missing') vs original snapshot."""
        if not self.has_original_snapshot():
            return []
        orig = self.original_snapshot_dir()
        res = []
        # current files
        current = set()
        for sub in CONTENT_DIRS:
            d = self.content_dir(sub)
            if d.is_dir():
                for f in d.rglob("*"):
                    if f.is_file():
                        current.add(f.relative_to(self.user).as_posix())
        # original files
        original = set()
        for sub in CONTENT_DIRS:
            d = orig / sub
            if d.is_dir():
                for f in d.rglob("*"):
                    if f.is_file():
                        original.add(f.relative_to(orig).as_posix())
        for rel in sorted(current | original):
            if rel in current and rel not in original:
                res.append((rel, "new"))
            elif rel in original and rel not in current:
                res.append((rel, "missing"))
            elif rel in current and rel in original:
                # compare hash
                import hashlib
                def h(p):
                    return hashlib.sha256(p.read_bytes()).hexdigest()
                try:
                    if h(self.content_path(rel)) != h(orig / rel):
                        res.append((rel, "changed"))
                except OSError:
                    pass
        return res

    def restore_original(self):
        if not self.has_original_snapshot():
            raise Err("No original snapshot available.")
        # trash current content
        trash = self.base / "trash" / ("restore-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
        trash.mkdir(parents=True, exist_ok=True)
        for sub in CONTENT_DIRS:
            src = self.content_dir(sub)
            if src.is_dir():
                dst = trash / sub
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
        # restore from snapshot
        orig = self.original_snapshot_dir()
        for sub in CONTENT_DIRS:
            src = orig / sub
            dst = self.content_dir(sub)
            if src.is_dir():
                shutil.copytree(src, dst)
        # clear mods? keep registry but mark? leave as is
        self.save()
        return trash

    def unmanaged_files(self):
        owners = self.owners()
        out = []
        for sub in CONTENT_DIRS:
            d = self.content_dir(sub)
            if not d.is_dir():
                continue
            for f in d.rglob("*"):
                if f.is_file():
                    key = f.relative_to(self.user).as_posix()
                    if key not in owners:
                        out.append(key)
        return out

    def adopt_files(self, name, relpaths, lowercase=True):
        if name in self.mods:
            raise Err(f"'{name}' is already registered.")
        files = [str(r).replace("\\", "/") for r in relpaths]
        self.mods[name] = {"files": files, "stash": {}, "enabled": True,
                           "date": datetime.now().isoformat(timespec="seconds"),
                           "source": "adopted", "requires": [], "notes": "", "rating": 0,
                           "vault_url": "", "author": "", "version": ""}
        self.save()
        return name


    def set_notes(self, name, notes):
        self._get(name)["notes"] = notes or ""
        self.save()

    def set_rating(self, name, rating):
        try:
            r = int(rating)
        except (TypeError, ValueError):
            raise Err("Rating must be an integer.")
        if r < 0 or r > 5:
            raise Err("Rating must be 0-5.")
        self._get(name)["rating"] = r
        self.save()

    def set_meta(self, name, **kwargs):
        m = self._get(name)
        for k in ("notes", "rating", "vault_url", "author", "version"):
            if k in kwargs and kwargs[k] is not None:
                m[k] = kwargs[k]
        self.save()


    def groups(self):
        return dict(self.d.get("groups", {}))

    def create_group(self, name):
        if not name:
            raise Err("Group name required.")
        self.d.setdefault("groups", {})
        if name in self.d["groups"]:
            raise Err(f"Group '{name}' already exists.")
        self.d["groups"][name] = []
        self.save()

    def delete_group(self, name):
        if name not in self.d.get("groups", {}):
            raise Err(f"Group '{name}' not found.")
        for m in self.mods.values():
            if m.get("group") == name:
                m["group"] = ""
        del self.d["groups"][name]
        self.save()

    def add_to_group(self, mod, group):
        if mod not in self.mods:
            raise Err(f"'{mod}' not registered.")
        if group not in self.d.get("groups", {}):
            raise Err(f"Group '{group}' not found.")
        # remove from previous group
        prev = self.mods[mod].get("group")
        if prev and prev in self.d["groups"]:
            if mod in self.d["groups"][prev]:
                self.d["groups"][prev].remove(mod)
        self.mods[mod]["group"] = group
        if mod not in self.d["groups"][group]:
            self.d["groups"][group].append(mod)
        self.save()

    def remove_from_group(self, mod):
        if mod not in self.mods:
            raise Err(f"'{mod}' not registered.")
        prev = self.mods[mod].get("group")
        if prev and prev in self.d.get("groups", {}):
            if mod in self.d["groups"][prev]:
                self.d["groups"][prev].remove(mod)
        self.mods[mod]["group"] = ""
        self.save()

    def group_mods(self, group):
        return list(self.d.get("groups", {}).get(group, []))

    def activate_group(self, group, on_conflict=None):
        if group not in self.d.get("groups", {}):
            raise Err(f"Group '{group}' not found.")
        for name in self.d["groups"][group]:
            if name in self.mods and not self.mods[name].get("enabled"):
                self.enable(name, on_conflict)

    def deactivate_group(self, group):
        if group not in self.d.get("groups", {}):
            raise Err(f"Group '{group}' not found.")
        for name in self.d["groups"][group]:
            if name in self.mods and self.mods[name].get("enabled"):
                self.disable(name)


    def ffmpeg_available(self):
        import shutil as _sh
        cfg = load_cfg() if False else None
        try:
            from .config import load_cfg as _lc
            cfg = _lc()
        except Exception:
            import nwnmods as _n
            cfg = _n.load_cfg() if hasattr(_n, "load_cfg") else {}
        p = (cfg or {}).get("ffmpeg_path")
        if p and Path(p).is_file():
            return p
        return _sh.which("ffmpeg")

    def find_bik_without_wbm(self):
        out = []
        for f in self.user.rglob("*.bik"):
            if f.is_file() and not f.with_suffix(".wbm").exists():
                out.append(f)
        return out

    def convert_bik(self, files=None, ffmpeg=None):
        import subprocess as _sp
        if files is None:
            files = self.find_bik_without_wbm()
        ffmpeg = ffmpeg or self.ffmpeg_available()
        if not ffmpeg:
            raise Err("FFmpeg not found. Install FFmpeg or set ffmpeg_path in config.")
        converted = []
        for bik in files:
            bik = Path(bik)
            wbm = bik.with_suffix(".wbm")
            try:
                _sp.run([ffmpeg, "-y", "-i", str(bik), str(wbm)], check=True,
                        stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
            except _sp.CalledProcessError:
                raise Err(f"Failed to convert {bik}")
            converted.append(wbm)
        return converted


    def export_mod(self, name, dest):
        m = self._get(name)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            # metadata
            meta = {
                "name": name,
                "files": m.get("files", []),
                "requires": m.get("requires", []),
                "notes": m.get("notes", ""),
                "rating": m.get("rating", 0),
                "vault_url": m.get("vault_url", ""),
                "author": m.get("author", ""),
                "version": m.get("version", ""),
                "group": m.get("group", ""),
            }
            z.writestr("mod.json", json.dumps(meta, indent=2, ensure_ascii=False))
            for rel in m.get("files", []):
                src = self.content_path(rel)
                if src.is_file():
                    z.write(src, "files/" + rel)
        return dest

    def import_mod(self, zpath, name=None):
        zpath = Path(zpath)
        with zipfile.ZipFile(zpath) as z:
            meta = json.loads(z.read("mod.json").decode("utf-8"))
            mod_name = name or meta.get("name")
            if not mod_name:
                raise Err("Mod name missing in archive.")
            if mod_name in self.mods:
                raise Err(f"'{mod_name}' is already installed.")
            # extract files
            for info in z.infolist():
                if info.filename.startswith("files/"):
                    rel = info.filename[len("files/"):]
                    if not _inside(self.user, rel):
                        raise Err("Archive contains insecure paths.")
            for info in z.infolist():
                if info.filename.startswith("files/"):
                    rel = info.filename[len("files/"):]
                    tgt = self.content_path(rel)
                    tgt.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as srcf, open(tgt, "wb") as dstf:
                        shutil.copyfileobj(srcf, dstf)
            files = meta.get("files", [])
            # ensure files list matches extracted
            if not files:
                files = [i.filename[len("files/"):] for i in z.infolist() if i.filename.startswith("files/") and not i.is_dir()]
            self.mods[mod_name] = {
                "files": files,
                "stash": {},
                "enabled": True,
                "date": datetime.now().isoformat(timespec="seconds"),
                "source": str(zpath),
                "requires": meta.get("requires", []),
                "notes": meta.get("notes", ""),
                "rating": meta.get("rating", 0),
                "vault_url": meta.get("vault_url", ""),
                "author": meta.get("author", ""),
                "version": meta.get("version", ""),
                "group": meta.get("group", ""),
            }
            self.save()
            return mod_name

    def export_profile(self, name, dest):
        if name not in self.d.get("profiles", {}):
            raise Err(f"Profile '{name}' not found.")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        data = {"profile": name, "mods": self.d["profiles"][name]}
        dest.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        return dest

    def import_profile(self, src, name=None):
        src = Path(src)
        data = json.loads(src.read_text(encoding="utf-8"))
        pname = name or data.get("profile")
        if not pname:
            raise Err("Profile name missing.")
        self.d.setdefault("profiles", {})
        self.d["profiles"][pname] = data.get("mods", [])
        self.save()
        return pname


    def _aliases(self):
        try:
            from .config import load_cfg as _lc
            cfg = _lc()
        except Exception:
            try:
                import nwnmods as _n
                cfg = _n.load_cfg()
            except Exception:
                cfg = {}
        return cfg.get("folder_aliases") or {}

    def content_dir(self, sub):
        a = self._aliases().get(sub)
        return Path(a) if a else (self.user / sub)

    def content_path(self, rel):
        rel = Path(rel)
        parts = rel.parts
        if parts and parts[0] in self._aliases():
            return Path(self._aliases()[parts[0]]) / Path(*parts[1:]) if len(parts) > 1 else Path(self._aliases()[parts[0]])
        return self.user / rel

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
            d = self.content_dir(sub)
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
                for f in (self.content_dir(sub)).rglob("*") if (self.content_dir(sub)).is_dir() else []:
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
            d = self.content_dir(sub)
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
            src = self.content_dir(sub)
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
            dst = self.content_dir(sub)
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
