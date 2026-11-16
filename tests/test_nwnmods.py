import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

import pytest

import nwnmods


@pytest.fixture
def user_dir(tmp_path):
    d = tmp_path / "userdata"
    d.mkdir()
    return d


@pytest.fixture
def lib(user_dir):
    return nwnmods.Library(user_dir)


def make_zip(path, members):
    """members: list of (arcname, content bytes)"""
    with zipfile.ZipFile(path, "w") as z:
        for name, data in members:
            z.writestr(name, data)
    return path


def test_map_path_basic():
    assert nwnmods.map_path(Path("mod/hak/foo.hak"), True) == Path("hak/foo.hak")
    # ignored
    assert nwnmods.map_path(Path("mod/readme.txt"), True) is None
    # portraits by po_ prefix
    assert nwnmods.map_path(Path("po_bar.tga"), True) == Path("portraits/po_bar.tga")


def test_extract_zip_path_traversal(user_dir):
    zp = user_dir.parent / "evil.zip"
    make_zip(zp, [("hak/ok.hak", b"x"), ("../../etc/passwd", b"bad")])
    out = user_dir / "out"
    out.mkdir()
    with pytest.raises(nwnmods.Err):
        nwnmods.extract(zp, out)


def test_extract_zip_symlink_rejected(user_dir):
    zp = user_dir.parent / "sym.zip"
    with zipfile.ZipFile(zp, "w") as z:
        info = zipfile.ZipInfo("hak/link")
        info.external_attr = (0xA1ED << 16)
        z.writestr(info, b"")
    out = user_dir / "out"
    out.mkdir()
    with pytest.raises(nwnmods.Err):
        nwnmods.extract(zp, out)


def test_extract_zip_size_limit(user_dir, monkeypatch):
    monkeypatch.setattr(nwnmods, "MAX_FILE_BYTES", 4)
    zp = user_dir.parent / "big.zip"
    make_zip(zp, [("hak/big.hak", b"12345")])
    out = user_dir / "out"
    out.mkdir()
    with pytest.raises(nwnmods.Err):
        nwnmods.extract(zp, out)


def test_extract_tar_path_traversal(user_dir):
    tp = user_dir.parent / "evil.tar"
    with tarfile.open(tp, "w") as t:
        data = b"bad"
        info = tarfile.TarInfo(name="../../etc/passwd")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
        # also a safe one
        info2 = tarfile.TarInfo(name="hak/ok.hak")
        info2.size = len(b"x")
        t.addfile(info2, io.BytesIO(b"x"))
    out = user_dir / "out"
    out.mkdir()
    with pytest.raises(nwnmods.Err):
        nwnmods.extract(tp, out)


def test_install_uninstall_roundtrip(lib, user_dir, tmp_path):
    zp = tmp_path / "mod.zip"
    make_zip(zp, [("hak/foo.hak", b"content"), ("override/bar.2da", b"data")])
    name = lib.install(zp, lowercase=True)
    assert name in lib.mods
    assert (user_dir / "hak" / "foo.hak").read_bytes() == b"content"
    assert (user_dir / "override" / "bar.2da").read_bytes() == b"data"
    # uninstall
    lib.remove(name)
    assert name not in lib.mods
    assert not (user_dir / "hak" / "foo.hak").exists()


def test_conflicts_report(lib, tmp_path):
    z1 = tmp_path / "a.zip"
    z2 = tmp_path / "b.zip"
    make_zip(z1, [("hak/x.hak", b"a")])
    make_zip(z2, [("hak/x.hak", b"b")])
    lib.install(z1, name="modA")
    # second install with overwrite
    lib.install(z2, name="modB", on_conflict=lambda c: "overwrite")
    rep = lib.conflicts_report()
    assert "hak/x.hak" in rep


def test_cleanup_protects_saves(lib, user_dir):
    (user_dir / "saves").mkdir(parents=True, exist_ok=True)
    (user_dir / "saves" / "save.sav").write_bytes(b"x")
    (user_dir / "hak").mkdir(parents=True, exist_ok=True)
    (user_dir / "hak" / "vanilla.hak").write_bytes(b"v")
    preview = lib.cleanup_preview()
    assert any("hak/vanilla.hak" == p for p, _ in preview)
    assert not any("saves/save.sav" == p for p, _ in preview)
    res = lib.cleanup()
    assert res["count"] == 1
    assert (user_dir / "saves" / "save.sav").exists()
    assert not (user_dir / "hak" / "vanilla.hak").exists()
    # recovery
    assert res["trash"] is not None
    trash = Path(res["trash"])
    assert (trash / "hak" / "vanilla.hak").exists()


def test_backup_restore(lib, user_dir):
    (user_dir / "saves").mkdir(parents=True, exist_ok=True)
    (user_dir / "saves" / "s.sav").write_bytes(b"save")
    bp = lib.backup()
    assert bp.exists()
    # delete and restore
    (user_dir / "saves" / "s.sav").unlink()
    lib.restore(bp)
    assert (user_dir / "saves" / "s.sav").read_bytes() == b"save"


def test_download_error_handling(tmp_path, monkeypatch):
    # invalid URL should raise Err, not crash
    with pytest.raises(nwnmods.Err):
        nwnmods.download("http://127.0.0.1:1/nope", tmp_path)


def test_config_save_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(nwnmods, "CFG", tmp_path / "cfg.json")
    nwnmods.save_cfg({"a": 1})
    assert (tmp_path / "cfg.json").exists()
    assert json.loads((tmp_path / "cfg.json").read_text()) == {"a": 1}


def test_load_cfg_surfaces_error(tmp_path, monkeypatch, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    monkeypatch.setattr(nwnmods, "CFG", bad)
    cfg = nwnmods.load_cfg()
    # still returns default-ish dict
    assert isinstance(cfg, dict)
    # error surfaced to stderr
    captured = capsys.readouterr()
    assert "could not read config" in captured.err


def test_manifest_parse_error_surfaced(tmp_path, monkeypatch, capsys):
    user = tmp_path / "u"
    user.mkdir()
    base = user / ".nwnmods"
    base.mkdir()
    (base / "manifest.json").write_text("{bad")
    nwnmods.Library(user)
    captured = capsys.readouterr()
    assert "could not read manifest" in captured.err


def test_extract_rejects_huge_tar_member(user_dir, monkeypatch):
    monkeypatch.setattr(nwnmods, "MAX_FILE_BYTES", 4)
    tp = user_dir.parent / "big.tar"
    with tarfile.open(tp, "w") as t:
        data = b"12345"
        info = tarfile.TarInfo(name="hak/big.hak")
        info.size = len(data)
        t.addfile(info, io.BytesIO(data))
    out = user_dir / "out"
    out.mkdir()
    with pytest.raises(nwnmods.Err):
        nwnmods.extract(tp, out)


def test_package_modules_and_compatibility_exports():
    from nwnmods import config, detection, files, library, vault

    assert nwnmods.Err is files.Err
    assert nwnmods.Library is library.Library
    assert nwnmods.Game is detection.Game
    assert nwnmods.map_path is files.map_path
    assert config.CFG == nwnmods.CFG
    assert callable(vault.parse_project)


def test_script_and_package_entrypoints_show_help():
    root = Path(__file__).resolve().parents[1]
    script = subprocess.run([sys.executable, str(root / "nwnmods.py"), "--help"], cwd=root,
                            capture_output=True, text=True, check=True)
    package = subprocess.run([sys.executable, "-m", "nwnmods", "--help"], cwd=root,
                             capture_output=True, text=True, check=True)

    assert "Mod Manager for Neverwinter Nights" in script.stdout
    assert "Mod Manager for Neverwinter Nights" in package.stdout


def test_script_and_package_entrypoints_operate_on_temporary_user(tmp_path):
    root = Path(__file__).resolve().parents[1]
    user = tmp_path / "userdata"
    archive = make_zip(tmp_path / "cli-mod.zip", [("hak/cli.hak", b"cli-content")])
    common = ["--user", str(user)]

    installed = subprocess.run([sys.executable, str(root / "nwnmods.py"), *common, "install",
                                 str(archive), "--name", "cli-mod"], cwd=root,
                                capture_output=True, text=True)
    assert installed.returncode == 0, installed.stderr
    listed = subprocess.run([sys.executable, "-m", "nwnmods", *common, "list"], cwd=root,
                            capture_output=True, text=True, check=True)
    removed = subprocess.run([sys.executable, "-m", "nwnmods", *common, "remove", "cli-mod"], cwd=root,
                             capture_output=True, text=True, check=True)

    assert "cli-mod" in installed.stdout
    assert "cli-mod" in listed.stdout
    assert (user / "hak" / "cli.hak").exists() is False


def test_saves_switch_never_deletes(lib, user_dir):
    (user_dir / "saves").mkdir(parents=True, exist_ok=True)
    (user_dir / "saves" / "a.sav").write_bytes(b"hello")
    lib.saves_switch("modA")
    assert not (user_dir / "saves" / "a.sav").exists()
    stash = user_dir / ".nwnmods" / "saves" / "_default" / "saves" / "a.sav"
    assert stash.exists()
    assert stash.read_bytes() == b"hello"


def test_dependencies_block_enable(lib, tmp_path):
    z1 = tmp_path / "base.zip"
    z2 = tmp_path / "child.zip"
    import zipfile
    with zipfile.ZipFile(z1, "w") as z:
        z.writestr("hak/base.hak", b"b")
    with zipfile.ZipFile(z2, "w") as z:
        z.writestr("hak/child.hak", b"c")
    lib.install(z1, name="base")
    lib.install(z2, name="child")
    lib.set_requires("child", ["base"])
    assert lib.requires("child") == ["base"]
    # base disabled -> enabling child should warn
    lib.disable("base", force_deps=True)
    import pytest
    lib.disable("child")
    with pytest.raises(Exception):
        lib.enable("child")
    # force works
    lib.enable("child", force_deps=True)
    assert lib.mods["child"]["enabled"] is True


def test_original_snapshot_and_restore(lib, user_dir):
    (user_dir / "hak").mkdir(parents=True, exist_ok=True)
    (user_dir / "hak" / "v.hak").write_bytes(b"orig")
    d = lib.create_original_snapshot()
    assert d.is_dir()
    assert lib.has_original_snapshot()
    # modify
    (user_dir / "hak" / "v.hak").write_bytes(b"changed")
    (user_dir / "hak" / "new.hak").write_bytes(b"new")
    changed = lib.changed_files()
    statuses = {c for _, c in changed}
    assert "changed" in statuses or "new" in statuses
    trash = lib.restore_original()
    assert (user_dir / "hak" / "v.hak").read_bytes() == b"orig"
    assert not (user_dir / "hak" / "new.hak").exists()
    assert trash.is_dir()

def test_adopt_files(lib, user_dir):
    (user_dir / "hak").mkdir(parents=True, exist_ok=True)
    (user_dir / "hak" / "x.hak").write_bytes(b"x")
    n = lib.adopt_files("adopted", ["hak/x.hak"])
    assert n == "adopted"
    assert "adopted" in lib.mods
    assert "hak/x.hak" in lib.mods["adopted"]["files"]


def test_mod_metadata(lib, tmp_path):
    import zipfile
    z = tmp_path / "m.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("hak/m.hak", b"x")
    lib.install(z, name="m")
    lib.set_notes("m", "hello notes")
    lib.set_rating("m", 4)
    lib.set_meta("m", author="me", version="1.0", vault_url="http://x")
    m = lib.mods["m"]
    assert m["notes"] == "hello notes"
    assert m["rating"] == 4
    assert m["author"] == "me"
    assert m["version"] == "1.0"
    assert m["vault_url"] == "http://x"


def test_groups(lib, tmp_path):
    import zipfile
    for name in ("a", "b"):
        z = tmp_path / f"{name}.zip"
        with zipfile.ZipFile(z, "w") as zf:
            zf.writestr(f"hak/{name}.hak", name.encode())
        lib.install(z, name=name)
    lib.create_group("g1")
    lib.add_to_group("a", "g1")
    lib.add_to_group("b", "g1")
    assert set(lib.group_mods("g1")) == {"a", "b"}
    assert lib.mods["a"].get("group") == "g1"
    # deactivate/activate
    lib.deactivate_group("g1")
    assert lib.mods["a"]["enabled"] is False
    assert lib.mods["b"]["enabled"] is False
    lib.activate_group("g1")
    assert lib.mods["a"]["enabled"] is True
    assert lib.mods["b"]["enabled"] is True
    lib.delete_group("g1")
    assert "g1" not in lib.groups()
    assert lib.mods["a"].get("group") == ""
