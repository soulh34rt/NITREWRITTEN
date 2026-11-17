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

# ---- Fase B ----

def test_ext_map_configurable():
    assert nwnmods.map_path(Path("m/x.hak"), True, ext_map={".hak": "custom"}) == Path("custom/x.hak")
    # custom ignore_ext replaces the defaults
    assert nwnmods.map_path(Path("m/a.hak"), True, ignore_ext={".hak"}) is None
    assert nwnmods.map_path(Path("m/a.txt"), True, ignore_ext={".hak"}) == Path("override/a.txt")
    assert nwnmods.map_path(Path("m/README"), True, ignore_names={"readme"}) is None


def test_export_import_mod_roundtrip(lib, user_dir, tmp_path):
    z = tmp_path / "exp.zip"
    make_zip(z, [("hak/exp.hak", b"data")])
    lib.install(z, name="exp")
    lib.set_notes("exp", "a note")
    dest = tmp_path / "exported.zip"
    lib.export_mod("exp", dest)

    user2 = tmp_path / "u2"
    user2.mkdir()
    lib2 = nwnmods.Library(user2)
    lib2.import_mod(dest)
    assert lib2.mods["exp"]["enabled"] is True
    assert lib2.mods["exp"]["notes"] == "a note"
    assert (user2 / "hak" / "exp.hak").read_bytes() == b"data"


def test_export_import_profile(lib, tmp_path):
    z = tmp_path / "p.zip"
    make_zip(z, [("hak/p.hak", b"p")])
    lib.install(z, name="p")
    lib.save_profile("profile1")
    dest = tmp_path / "profile1.json"
    lib.export_profile("profile1", dest)

    user2 = tmp_path / "u2"
    user2.mkdir()
    lib2 = nwnmods.Library(user2)
    lib2.import_profile(dest)
    assert lib2.d["profiles"]["profile1"] == ["p"]


def test_folder_aliases(lib, user_dir, tmp_path, monkeypatch):
    alt = tmp_path / "alt-hak"
    alt.mkdir()
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({"folder_aliases": {"hak": str(alt)}}), encoding="utf-8")
    monkeypatch.setattr(nwnmods.config, "CFG", cfg_path)
    assert lib.content_dir("hak") == alt
    assert lib.content_path("hak/x.hak") == alt / "x.hak"
    # not aliased -> normal user path
    assert lib.content_path("override/y.ncs") == user_dir / "override" / "y.ncs"


def test_bik_discovery_and_convert(lib, user_dir, monkeypatch):
    mv = user_dir / "movies"
    mv.mkdir()
    bik = mv / "intro.bik"
    bik.write_bytes(b"bik")
    assert bik in lib.find_bik_without_wbm()
    wbm = mv / "intro.wbm"
    wbm.write_bytes(b"wbm")
    assert bik not in lib.find_bik_without_wbm()
    wbm.unlink()

    import subprocess as sp_mod

    def fake_run(cmd, **kw):
        Path(cmd[-1]).write_bytes(b"converted")
        return sp_mod.CompletedProcess(cmd, 0)

    monkeypatch.setattr(sp_mod, "run", fake_run)
    out = lib.convert_bik(ffmpeg="ffmpeg-fake")
    assert out == [wbm]
    assert wbm.read_bytes() == b"converted"


def test_steam_workshop_items_is_list():
    assert isinstance(nwnmods.steam_workshop_items(), list)


def test_bundled_7zip_extracts_zip(tmp_path):
    from nwnmods.files import find_7zip, sevenzip_extract

    p, src = find_7zip()
    assert p and Path(p).is_file()
    zp = tmp_path / "a.zip"
    make_zip(zp, [("hak/z.hak", b"zz")])
    out = tmp_path / "out"
    out.mkdir()
    sevenzip_extract(zp, out)
    assert (out / "hak" / "z.hak").read_bytes() == b"zz"


# ---- Fase C ----

def test_list_characters_and_portraits(lib, user_dir):
    lv = user_dir / "localvault"
    lv.mkdir()
    (lv / "hero.bic").write_bytes(b"bic")
    p = user_dir / "portraits"
    p.mkdir()
    (p / "po_hero.tga").write_bytes(b"tga")

    chars = lib.list_characters()
    assert [c["name"] for c in chars] == ["hero"]
    assert chars[0]["owner"] == "(unmanaged)"
    assert chars[0]["size"] == 3

    pts = lib.list_portraits()
    assert [x["name"] for x in pts] == ["po_hero.tga"]


def test_playtime_roundtrip(lib):
    lib.start_playtime("modX")
    lib.end_playtime()
    totals, sessions = lib.playtime_report()
    assert "modX" in totals
    assert len(sessions) == 1
    assert sessions[0]["unexpected"] is False


def test_playtime_recover_after_crash(lib):
    lib.base.mkdir(parents=True, exist_ok=True)
    (lib.base / "playtime.json").write_text(json.dumps({
        "totals": {}, "pending": {"mod": "ghost", "start": "2020-01-01T00:00:00"},
        "sessions": []}), encoding="utf-8")
    lib2 = nwnmods.Library(lib.user)
    totals, sessions = lib2.playtime_report()
    assert totals.get("ghost", 0) > 0
    assert sessions and sessions[0]["unexpected"] is True


def test_nwn_log_errors(lib, user_dir):
    logs = user_dir / "logs"
    logs.mkdir()
    (logs / "nwserver.log").write_text("hello\nERROR: something failed\n", encoding="utf-8")
    errs = lib.nwn_log_errors()
    assert any("ERROR" in e for e in errs)


def test_config_basic_mode_default(tmp_path, monkeypatch):
    monkeypatch.setattr(nwnmods, "CFG", tmp_path / "nope.json")
    assert nwnmods.load_cfg()["basic_mode"] is False


def test_cli_chars_and_portraits(tmp_path):
    root = Path(__file__).resolve().parents[1]
    user = tmp_path / "userdata"
    (user / "localvault").mkdir(parents=True)
    (user / "localvault" / "hero.bic").write_bytes(b"x")
    (user / "portraits").mkdir()
    (user / "portraits" / "po_a.tga").write_bytes(b"x")

    r = subprocess.run([sys.executable, str(root / "nwnmods.py"), "--user", str(user), "chars"],
                       cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "hero" in r.stdout

    r2 = subprocess.run([sys.executable, str(root / "nwnmods.py"), "--user", str(user), "portraits"],
                        cwd=root, capture_output=True, text=True)
    assert r2.returncode == 0, r2.stderr
    assert "po_a.tga" in r2.stdout

def test_undeploy_rollback_on_failure(lib, user_dir, tmp_path, monkeypatch):
    # two files so the failure lands mid-operation
    z = tmp_path / "rb.zip"
    make_zip(z, [("hak/one.hak", b"1"), ("hak/two.hak", b"2")])
    lib.install(z, name="rb")

    import nwnmods.library as libmod
    real_move = libmod.shutil.move
    state = {"n": 0}

    def flaky(src, dst, *a, **k):
        state["n"] += 1
        if state["n"] == 2:  # first undeploy move ok, second fails
            raise OSError("disk full")
        return real_move(src, dst, *a, **k)

    monkeypatch.setattr(libmod.shutil, "move", flaky)
    with pytest.raises(OSError):
        lib.remove("rb")
    monkeypatch.setattr(libmod.shutil, "move", real_move)

    # rollback restored the first file; the mod is still registered
    assert "rb" in lib.mods
    assert (user_dir / "hak" / "one.hak").read_bytes() == b"1"
    assert (user_dir / "hak" / "two.hak").read_bytes() == b"2"


def test_remove_parks_files_in_trash(lib, user_dir, tmp_path):
    z = tmp_path / "tr.zip"
    make_zip(z, [("hak/tr.hak", b"x")])
    lib.install(z, name="tr")
    lib.remove("tr")
    assert not (user_dir / "hak" / "tr.hak").exists()
    trash = list((user_dir / ".nwnmods" / "trash").glob("undeploy-tr-*/hak/tr.hak"))
    assert trash and trash[0].read_bytes() == b"x"


def _make_png(path):
    from PIL import Image
    Image.new("RGB", (32, 18), (200, 30, 30)).save(path)
    return path


def test_start_screen_roundtrip(lib, user_dir, tmp_path):
    img = _make_png(tmp_path / "bg.png")
    # nothing installed yet
    assert lib.current_start_screen() is None
    dst = lib.set_start_screen(img)
    assert dst == user_dir / "override" / "gui_pre_bknd3.tga"
    assert dst.exists()
    cur = lib.current_start_screen()
    assert cur and cur["ext"] == ".tga"
    # restore parks the file instead of deleting it
    assert lib.restore_start_screen() is True
    assert lib.current_start_screen() is None
    assert not dst.exists()
    parked = list((user_dir / ".nwnmods" / "trash").glob("startscreen-*/gui_pre_bknd3.tga"))
    assert parked


def test_start_screen_replace_backs_up_previous(lib, user_dir, tmp_path):
    img1 = _make_png(tmp_path / "one.png")
    img2 = _make_png(tmp_path / "two.png")
    lib.set_start_screen(img1)
    lib.set_start_screen(img2)
    assert lib.current_start_screen()["path"].endswith(".tga")
    parked = list((user_dir / ".nwnmods" / "trash").glob("startscreen-*/gui_pre_bknd3.tga"))
    assert len(parked) == 1


def test_start_screen_bad_image_keeps_current(lib, user_dir, tmp_path):
    img = _make_png(tmp_path / "good.png")
    lib.set_start_screen(img)
    before = lib.current_start_screen()
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(nwnmods.Err):
        lib.set_start_screen(bad)
    # the previous screen is untouched and no temp file lingers
    assert lib.current_start_screen() == before
    assert not (user_dir / "override" / ".gui_pre_bknd3.tmp").exists()


def test_start_screen_missing_image_errors(lib):
    with pytest.raises(nwnmods.Err):
        lib.set_start_screen(Path("/nonexistent/img.png"))


def test_cli_startscreen(tmp_path):
    root = Path(__file__).resolve().parents[1]
    user = tmp_path / "userdata"
    (user / "override").mkdir(parents=True)
    img = _make_png(tmp_path / "bg.png")

    # status with no custom screen
    r = subprocess.run([sys.executable, str(root / "nwnmods.py"), "--user", str(user), "startscreen", "status"],
                       cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "game default" in r.stdout.lower()

    # install then status shows the custom file
    r = subprocess.run([sys.executable, str(root / "nwnmods.py"), "--user", str(user),
                        "startscreen", "set", str(img)],
                       cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (user / "override" / "gui_pre_bknd3.tga").exists()

    r = subprocess.run([sys.executable, str(root / "nwnmods.py"), "--user", str(user), "startscreen", "status"],
                       cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "custom start screen" in r.stdout.lower()

    # restore removes it (parked in trash)
    r = subprocess.run([sys.executable, str(root / "nwnmods.py"), "--user", str(user), "startscreen", "restore"],
                       cwd=root, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert not (user / "override" / "gui_pre_bknd3.tga").exists()
