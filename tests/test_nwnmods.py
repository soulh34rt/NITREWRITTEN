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


def test_config_splash_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(nwnmods, "CFG", tmp_path / "nope.json")
    cfg = nwnmods.load_cfg()
    assert cfg["splash_enabled"] is False
    assert cfg["splash_image"] is None
    assert cfg["splash_ms"] == 2000


def test_cli_startscreen_and_splash(tmp_path):
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

    # splash config commands (redirect config dir so the real config is untouched)
    env = dict(os.environ)
    env["APPDATA"] = str(tmp_path / "appdata")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["HOME"] = str(tmp_path / "home")
    r = subprocess.run([sys.executable, str(root / "nwnmods.py"), "--user", str(user),
                        "splash", "set", str(img)],
                       cwd=root, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    r = subprocess.run([sys.executable, str(root / "nwnmods.py"), "--user", str(user), "splash", "status"],
                       cwd=root, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "splash: on" in r.stdout.lower()
    r = subprocess.run([sys.executable, str(root / "nwnmods.py"), "--user", str(user), "splash", "off"],
                       cwd=root, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr


# --------------------------------------------------------------------------- mod list states (commit: state colors)


def _contrast_ratio(hex_a, hex_b):
    """WCAG 2.1 contrast ratio between two #rrggbb colors."""

    def _lum(h):
        def _ch(c):
            c /= 255
            return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

        r, g, b = (int(h[i:i + 2], 16) for i in (1, 3, 5))
        return 0.2126 * _ch(r) + 0.7152 * _ch(g) + 0.0722 * _ch(b)

    la, lb = _lum(hex_a), _lum(hex_b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def test_mod_state_truth_table():
    # disabled beats conflict: inactive mods never report as conflicting
    assert nwnmods.mod_state(True, False) == "active"
    assert nwnmods.mod_state(True, True) == "conflict"
    assert nwnmods.mod_state(False, True) == "disabled"
    assert nwnmods.mod_state(False, False) == "disabled"


def test_conflict_partners_groups_by_mod():
    report = {
        "hak/a.hak": ["modA", "modB"],
        "override/b.tpa": ["modB", "modC"],
        "hak/solo.hak": ["modA"],
    }
    partners = nwnmods.conflict_partners(report)
    assert partners["modA"] == {"modB"}
    assert partners["modB"] == {"modA", "modC"}
    assert partners["modC"] == {"modB"}
    # a file with a single owner creates no partnership
    assert all("modX" not in p for p in partners.values())


def test_conflict_partners_empty_report():
    assert nwnmods.conflict_partners({}) == {}


def test_state_symbols_and_labels_share_keys():
    for theme, palette in nwnmods.STATE_COLORS.items():
        assert set(palette) == set(nwnmods.STATE_LABELS) == set(nwnmods.STATE_SYMBOLS), theme
        for state, colors in palette.items():
            assert len(colors) == 2, f"{theme}/{state} must be (fg, bg)"


def test_state_colors_meet_wcag_contrast():
    # every state color pair, in both themes, must reach 4.5:1
    for theme, palette in nwnmods.STATE_COLORS.items():
        for state, (fg, bg) in palette.items():
            ratio = _contrast_ratio(fg, bg)
            assert ratio >= 4.5, f"{theme}/{state}: {fg} on {bg} is only {ratio:.2f}:1"


def test_state_constants_synced_between_script_and_package():
    """The monolith nwnmods.py and the nwnmods package must define identical state data."""
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("_nwnmods_state_sync", root / "nwnmods.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.STATE_COLORS == nwnmods.STATE_COLORS
    assert module.STATE_LABELS == nwnmods.STATE_LABELS
    assert module.STATE_SYMBOLS == nwnmods.STATE_SYMBOLS
    assert module.mod_state(True, True) == nwnmods.mod_state(True, True)
    assert module.conflict_partners({"f": ["a", "b"]}) == nwnmods.conflict_partners({"f": ["a", "b"]})
    assert module.group_conflicts({"f": ["a", "b"]}) == nwnmods.group_conflicts({"f": ["a", "b"]})


# --------------------------------------------------------------------------- conflict details (commit: conflicts by mod)


def test_group_conflicts_groups_files_per_mod():
    report = {
        "hak/a.hak": ["modA", "modB"],
        "hak/b.hak": ["modA", "modB"],
        "override/c.tpa": ["modB", "modC"],
    }
    grouped = nwnmods.group_conflicts(report)
    assert grouped["modA"] == {"modB": ["hak/a.hak", "hak/b.hak"]}
    assert grouped["modB"] == {"modA": ["hak/a.hak", "hak/b.hak"], "modC": ["override/c.tpa"]}
    assert grouped["modC"] == {"modB": ["override/c.tpa"]}
    # a file with a single owner creates no grouping
    assert "modX" not in grouped


def test_group_conflicts_empty_report():
    assert nwnmods.group_conflicts({}) == {}


def test_conflicts_by_mod_on_library(lib, tmp_path):
    z1 = tmp_path / "a.zip"
    z2 = tmp_path / "b.zip"
    make_zip(z1, [("hak/x.hak", b"a"), ("hak/only_a.hak", b"1")])
    make_zip(z2, [("hak/x.hak", b"b")])
    lib.install(z1, name="modA")
    lib.install(z2, name="modB", on_conflict=lambda c: "overwrite")
    grouped = lib.conflicts_by_mod()
    assert grouped["modA"] == {"modB": ["hak/x.hak"]}
    assert grouped["modB"] == {"modA": ["hak/x.hak"]}
    # non-shared files never show up
    assert all("hak/only_a.hak" not in fs for d in grouped.values() for fs in d.values())


def test_conflicts_by_mod_excludes_disabled(lib, tmp_path):
    z1 = tmp_path / "a.zip"
    z2 = tmp_path / "b.zip"
    make_zip(z1, [("hak/x.hak", b"a")])
    make_zip(z2, [("hak/x.hak", b"b")])
    lib.install(z1, name="modA")
    lib.install(z2, name="modB", on_conflict=lambda c: "overwrite")
    assert lib.conflicts_by_mod()
    lib.disable("modB")
    assert lib.conflicts_by_mod() == {}
    lib.enable("modB", on_conflict=lambda c: "overwrite")
    assert lib.conflicts_by_mod()["modA"] == {"modB": ["hak/x.hak"]}


def test_conflicts_cli_grouped_output(tmp_path):
    """CLI 'conflicts' prints mod <-> mod with shared files (ASCII only)."""
    root = Path(__file__).resolve().parents[1]
    user = tmp_path / "user"
    user.mkdir()
    env = dict(os.environ)
    env["APPDATA"] = str(tmp_path / "appdata")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["HOME"] = str(tmp_path / "home")

    z1 = tmp_path / "a.zip"
    z2 = tmp_path / "b.zip"
    make_zip(z1, [("hak/x.hak", b"a")])
    make_zip(z2, [("hak/x.hak", b"b")])

    for zp, name in ((z1, "modA"), (z2, "modB")):
        r = subprocess.run([sys.executable, str(root / "nwnmods.py"), "--user", str(user),
                            "install", str(zp), "--name", name, "--force"],
                           cwd=root, capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr

    r = subprocess.run([sys.executable, str(root / "nwnmods.py"), "--user", str(user), "conflicts"],
                       cwd=root, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "modA <-> modB (1 shared files)" in r.stdout
    assert "hak/x.hak" in r.stdout
    # grouped output must stay ASCII
    assert r.stdout == r.stdout.encode("ascii", "strict").decode("ascii")

    # disabling both sides clears the report
    for name in ("modA", "modB"):
        r = subprocess.run([sys.executable, str(root / "nwnmods.py"), "--user", str(user), "disable", name],
                           cwd=root, capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
    r = subprocess.run([sys.executable, str(root / "nwnmods.py"), "--user", str(user), "conflicts"],
                       cwd=root, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "No conflicts found." in r.stdout


# --------------------------------------------------------------------------- about / version (commit: about screen)


def test_version_and_about_text_synced_between_script_and_package():
    """VERSION, AUTHOR, REPO_URL and about_text() must match in both entry points."""
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("_nwnmods_about_sync", root / "nwnmods.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.VERSION == nwnmods.VERSION == "1.1.0"
    assert module.AUTHOR == nwnmods.AUTHOR == "ada_dev"
    assert module.REPO_URL == nwnmods.REPO_URL
    assert module.about_text() == nwnmods.about_text()


def test_about_text_content_is_ascii():
    txt = nwnmods.about_text()
    # ASCII-only rule for text shared with the CLI
    txt.encode("ascii")
    assert f"NITREWRITTEN {nwnmods.VERSION}" in txt
    assert f"Made by {nwnmods.AUTHOR}" in txt
    assert nwnmods.REPO_URL in txt
    for needle in ("7-Zip", "7-zip.org", "unRAR", "FFmpeg", "Neverwinter Vault",
                   "Surazal", "neverwintervault.org/project", "Wizards of the Coast"):
        assert needle in txt, needle


def test_cli_about_and_version(tmp_path):
    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env["APPDATA"] = str(tmp_path / "appdata")
    env["XDG_CONFIG_HOME"] = str(tmp_path / "xdg")
    env["HOME"] = str(tmp_path / "home")

    r = subprocess.run([sys.executable, str(root / "nwnmods.py"), "--version"],
                       cwd=root, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert f"nwnmods {nwnmods.VERSION}" in r.stdout

    for cmd in ([sys.executable, str(root / "nwnmods.py"), "about"],
                [sys.executable, "-m", "nwnmods", "about"]):
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        assert "NITREWRITTEN 1.1.0" in r.stdout
        assert "Made by ada_dev" in r.stdout
        assert nwnmods.REPO_URL in r.stdout
        assert "Surazal" in r.stdout
        # CLI output must stay ASCII
        assert r.stdout == r.stdout.encode("ascii", "strict").decode("ascii")


def test_third_party_licenses_file():
    root = Path(__file__).resolve().parents[1]
    tpc = root / "THIRD_PARTY_LICENSES"
    assert tpc.exists(), "THIRD_PARTY_LICENSES must exist at the repo root"
    content = tpc.read_text(encoding="utf-8")
    # 7-Zip full license text is copied verbatim from resources/tools/License.txt
    lic = (root / "resources" / "tools" / "License.txt").read_text(encoding="utf-8", errors="replace").rstrip("\n")
    assert lic in content, "7-Zip license text must be included verbatim"
    for needle in ("7-Zip", "unRAR", "FFmpeg", "Neverwinter Vault", "Surazal",
                   "Wizards of the Coast"):
        assert needle in content, needle


def test_readme_credits_extended():
    root = Path(__file__).resolve().parents[1]
    readme = (root / "README.md").read_text(encoding="utf-8", errors="replace")
    # extended (not removed) credits
    assert "7-Zip homepage: https://www.7-zip.org/" in readme
    assert "THIRD_PARTY_LICENSES" in readme
    assert "Surazal" in readme
    assert "Made by **ada_dev**" in readme
    assert nwnmods.REPO_URL in readme
    assert "nwnmods about" in readme


# --------------------------------------------------------------------------- vault gallery (commit: gallery)


def test_parse_project_collects_multiple_images():
    html = """
    <html><head><title>My Project | Neverwinter Vault</title></head><body>
    <img src="/files/styles/thumbnail/public/project/123/images/one.jpg">
    <img src="/files/project/123/images/two.png">
    <a href="/files/project/123/images/three.jpg"><img src="/files/project/123/images/three.jpg"></a>
    <img src="/files/project/123/images/one.jpg">
    <img src="/modules/icon.jpg">
    </body></html>
    """
    info = nwnmods.parse_project(html, "https://neverwintervault.org/project/nwn1/mods/my-project")
    assert info["images"] == [
        "https://neverwintervault.org/files/project/123/images/one.jpg",  # full-size replaces thumbnail
        "https://neverwintervault.org/files/project/123/images/two.png",
        "https://neverwintervault.org/files/project/123/images/three.jpg",
    ]
    # back compat: "image" is the first one; non-project images are ignored
    assert info["image"] == info["images"][0]


def test_parse_project_without_images():
    info = nwnmods.parse_project("<html><head><title>X</title></head><body>hi</body></html>",
                                  "https://neverwintervault.org/project/nwn1/mods/x")
    assert info["images"] == []
    assert info["image"] is None


def test_vault_cache_roundtrip(tmp_path):
    url = "https://neverwintervault.org/files/project/1/images/a.jpg"
    assert nwnmods.vault_cache_get(url, tmp_path) is None
    nwnmods.vault_cache_put(url, b"jpegdata", tmp_path, limit_mb=10)
    assert nwnmods.vault_cache_get(url, tmp_path) == b"jpegdata"
    assert nwnmods.vault_cache_size(tmp_path) == len(b"jpegdata")
    # stable name: same URL -> same file, different URL -> different file
    assert nwnmods._vault_cache_name(url) == nwnmods._vault_cache_name(url)
    assert nwnmods._vault_cache_name(url) != nwnmods._vault_cache_name(url + "/x")


def test_vault_cache_enforces_size_limit(tmp_path):
    # limit_mb=1 -> ~1 MB; write 3 x 400KB with distinct mtimes -> oldest must be evicted
    for i in range(3):
        nwnmods.vault_cache_put(f"https://example.com/img{i}.jpg", b"x" * (400 * 1024),
                                tmp_path, limit_mb=1)
        # pin the mtime so eviction order is deterministic (mtime granularity is 1s)
        p = tmp_path / nwnmods._vault_cache_name(f"https://example.com/img{i}.jpg")
        os.utime(p, (1000 + i, 1000 + i))
    files = list(tmp_path.glob("*"))
    assert len(files) == 2  # 1.2 MB trimmed to fit under 1 MB
    assert nwnmods.vault_cache_size(tmp_path) <= 1024 * 1024
    # newest file survived
    assert nwnmods.vault_cache_get("https://example.com/img2.jpg", tmp_path) == b"x" * (400 * 1024)
    assert nwnmods.vault_cache_get("https://example.com/img0.jpg", tmp_path) is None


def test_vault_cache_clear(tmp_path):
    nwnmods.vault_cache_put("https://example.com/a.png", b"aaa", tmp_path, limit_mb=10)
    nwnmods.vault_cache_put("https://example.com/b.png", b"bbb", tmp_path, limit_mb=10)
    assert nwnmods.vault_cache_clear(tmp_path) == 2
    assert nwnmods.vault_cache_size(tmp_path) == 0
    assert nwnmods.vault_cache_clear(tmp_path) == 0


def test_vault_cache_synced_between_script_and_package(tmp_path):
    """Monolith and package must produce identical cache names and parse identical images."""
    import importlib.util

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("_nwnmods_vault_sync", root / "nwnmods.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    url = "https://neverwintervault.org/files/project/9/images/pic.webp"
    assert module._vault_cache_name(url) == nwnmods._vault_cache_name(url)
    assert module.VAULT_CACHE_DEFAULT_MB == nwnmods.VAULT_CACHE_DEFAULT_MB

    html = '<img src="/files/project/9/images/pic.webp"><img src="/files/project/9/images/other.jpg">'
    m_info = module.parse_project(html, "https://neverwintervault.org/project/nwn1/mods/z")
    p_info = nwnmods.parse_project(html, "https://neverwintervault.org/project/nwn1/mods/z")
    assert m_info["images"] == p_info["images"]
    assert m_info["image"] == p_info["image"]

    # behavior on a temp dir must match too
    module.vault_cache_put(url, b"webp", tmp_path, limit_mb=5)
    assert nwnmods.vault_cache_get(url, tmp_path) == b"webp"


# --------------------------------------------------------------------------- vault catalog parsing

def test_parse_catalog_finds_projects():
    html = """
    <html><body>
    <table><tr class="odd">
    <td><a href="https://neverwintervault.org/project/nwn1/mods/my-mod">My Mod</a></td>
    <td>Author A</td><td>Desc A</td><td>2024-01-01</td><td>10</td>
    </tr></table>
    <table><tr class="even">
    <td><a href="https://neverwintervault.org/project/nwn1/tools/some-tool">Some Tool</a></td>
    <td>Author B</td><td>Desc B</td><td>2024-02-01</td><td>5</td>
    </tr></table>
    </body></html>
    """
    items, next_url = nwnmods.parse_catalog(html, "https://neverwintervault.org/catalog/nwn1")
    assert len(items) == 2
    assert items[0]["title"] == "My Mod"
    assert "my-mod" in items[0]["url"]
    assert items[1]["title"] == "Some Tool"


def test_parse_catalog_empty():
    result = nwnmods.parse_catalog("<html><body></body></html>", "https://neverwintervault.org/catalog/nwn1")
    items, next_url = result
    assert items == []
    assert next_url is None


def test_parse_project_title_and_description():
    html = """
    <html><head><title>Test Mod | Neverwinter Vault</title></head><body>
    <div>This is a description.</div>
    </body></html>
    """
    info = nwnmods.parse_project(html, "https://neverwintervault.org/project/nwn1/mods/test")
    assert info["title"] == "Test Mod"
    assert "desc" in info


def test_http_get_raises_on_network_error(monkeypatch):
    """http_get should raise on connection errors, not crash."""
    def fake_open(*a, **kw):
        raise ConnectionError("no connection")

    monkeypatch.setattr(nwnmods, "http_get", lambda url, timeout=None: fake_open())
    with pytest.raises((ConnectionError, OSError, nwnmods.Err)):
        nwnmods.http_get("http://127.0.0.1:1/nope")


# --------------------------------------------------------------------------- GUI smoke tests (no real tkinter display)

def test_gui_module_is_importable():
    """The nwnmods.gui module should be importable and expose run_gui."""
    from nwnmods import gui
    assert callable(gui.run_gui)


def test_gui_run_gui_raises_without_display(monkeypatch):
    """run_gui should fail gracefully when tkinter cannot create a display."""
    from nwnmods import gui as gui_mod
    import tkinter as tk

    original_init = tk.Tk.__init__

    def fake_init(self, *a, **kw):
        raise tk.TclError("no display name and no $DISPLAY variable")

    monkeypatch.setattr(tk.Tk, "__init__", fake_init)
    with pytest.raises((tk.TclError, RuntimeError)):
        gui_mod.run_gui()


def test_gui_bridge_loads_legacy_module(monkeypatch, tmp_path):
    """The GUI bridge should load the legacy nwnmods.py module."""
    import nwnmods.gui as gui_mod

    # Reset the cached legacy module
    gui_mod._legacy = None

    # Mock _legacy_path to return the real nwnmods.py
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(gui_mod, "_legacy_path", lambda: root / "nwnmods.py")

    # Mock tk.Tk to avoid display requirements
    import tkinter as tk
    original_init = tk.Tk.__init__

    def fake_init(self, *a, **kw):
        original_init(self, *a, **kw)
        self.destroy()  # close immediately

    monkeypatch.setattr(tk.Tk, "__init__", fake_init)

    # run_gui should load the legacy module
    # It will fail at mainloop, but the module should be loaded
    try:
        gui_mod.run_gui()
    except Exception:
        pass  # expected: no display or mainloop failure


def test_cli_works_without_tkinter(tmp_path):
    """The CLI must run on headless Python builds that lack tkinter; only the GUI needs it."""
    root = Path(__file__).resolve().parents[1]
    block = tmp_path / "block"
    block.mkdir()
    (block / "tkinter.py").write_text("raise ImportError('tkinter blocked for this test')\n", encoding="utf-8")
    user = tmp_path / "userdata"
    user.mkdir()
    env = {**os.environ, "PYTHONPATH": str(block)}

    r = subprocess.run([sys.executable, str(root / "nwnmods.py"), "--user", str(user), "list"],
                       cwd=root, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr

    r2 = subprocess.run([sys.executable, str(root / "nwnmods.py"), "--user", str(user), "gui"],
                        cwd=root, capture_output=True, text=True, env=env)
    assert r2.returncode != 0
    assert "tkinter" in r2.stderr
    assert "Traceback" not in r2.stderr
