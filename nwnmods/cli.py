import argparse
import sys

from ._common import Err
from .config import load_cfg, save_cfg
from .detection import Game, detect_all, launch, steam_workshop_items
from .gui import run_gui
from .library import Library


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
        p = sp.add_parser(c)
        p.add_argument("name")
        p.add_argument("--force", action="store_true", help="ignore dependency checks")
    for c in ("list", "conflicts", "backup", "launch"):
        sp.add_parser(c)
    sp.add_parser("restore").add_argument("zip")
    ad = sp.add_parser("adopt")
    ad.add_argument("name")
    ad.add_argument("files", nargs="+")
    sp.add_parser("snapshot")
    sp.add_parser("restore-original")
    sp.add_parser("changed")
    sp.add_parser("unmanaged")
    em = sp.add_parser("extmap"); ems = em.add_subparsers(dest="em_cmd")
    ems.add_parser("show"); ems.add_parser("reset")
    ws = sp.add_parser("workshop"); wsv = ws.add_subparsers(dest="ws_cmd")
    wsv.add_parser("list")
    wi = wsv.add_parser("import"); wi.add_argument("id"); wi.add_argument("--name")
    cb = sp.add_parser("convert-bik"); cb.add_argument("--all", action="store_true")
    gr = sp.add_parser("group")
    gsv = gr.add_subparsers(dest="group_cmd")
    gsv.add_parser("list")
    gc = gsv.add_parser("create"); gc.add_argument("name")
    gd = gsv.add_parser("delete"); gd.add_argument("name")
    ga = gsv.add_parser("add"); ga.add_argument("mod"); ga.add_argument("group")
    grm = gsv.add_parser("remove"); grm.add_argument("mod")
    gac = gsv.add_parser("activate"); gac.add_argument("name")
    gdc = gsv.add_parser("deactivate"); gdc.add_argument("name")
    nt = sp.add_parser("note"); nt.add_argument("name"); nt.add_argument("text", nargs="+")
    rt = sp.add_parser("rate"); rt.add_argument("name"); rt.add_argument("value")
    mt = sp.add_parser("meta"); mt.add_argument("name")
    se = sp.add_parser("search"); se.add_argument("query")
    # saves manager
    sv = sp.add_parser("saves")
    ssv = sv.add_subparsers(dest="saves_cmd")
    ssv.add_parser("list")
    sw = ssv.add_parser("switch")
    sw.add_argument("mod")
    sa = ssv.add_parser("archive")
    sa.add_argument("mod")
    sr = ssv.add_parser("restore")
    sr.add_argument("mod")
    sr.add_argument("--from-archive", action="store_true")
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
        print(f"[OK] '{n}': {len(lib.mods[n]['files'])} files")
    elif a.cmd == "list":
        for n, m in lib.mods.items():
            print(f"{'[ON]' if m['enabled'] else '[OFF]'} {n}  ({len(m['files'])} files)")
    elif a.cmd == "enable":
        lib.enable(a.name, lambda c: "overwrite", force_deps=getattr(a, 'force', False))
    elif a.cmd == "disable":
        lib.disable(a.name, force_deps=getattr(a, 'force', False))
    elif a.cmd == "remove":
        lib.remove(a.name, force_deps=getattr(a, 'force', False))
    elif a.cmd == "conflicts":
        conflicts = lib.conflicts_report()
        for f, ns in conflicts.items():
            print(f"{f}: {', '.join(ns)}")
        if not conflicts:
            print("No conflicts found.")
    elif a.cmd == "backup":
        print(lib.backup())
    elif a.cmd == "restore":
        lib.restore(a.zip)
    elif a.cmd == "adopt":
        n = lib.adopt_files(a.name, a.files)
        print(f"[OK] adopted {n}: {len(lib.mods[n]['files'])} files")
    elif a.cmd == "snapshot":
        d = lib.create_original_snapshot()
        print(f"[OK] snapshot: {d}")
    elif a.cmd == "restore-original":
        t = lib.restore_original()
        print(f"[OK] restored original; previous content in {t}")
    elif a.cmd == "changed":
        for rel, status in lib.changed_files():
            print(f"{status}: {rel}")
    elif a.cmd == "unmanaged":
        for f in lib.unmanaged_files():
            print(f)
    elif a.cmd == "note":
        lib.set_notes(a.name, " ".join(a.text))
        print(f"[OK] notes saved for {a.name}")
    elif a.cmd == "rate":
        lib.set_rating(a.name, a.value)
        print(f"[OK] rating saved for {a.name}")
    elif a.cmd == "meta":
        m = lib.mods[a.name]
        print(f"Name: {a.name}")
        print(f"Requires: {m.get('requires')}")
        print(f"Author: {m.get('author')}")
        print(f"Version: {m.get('version')}")
        print(f"Rating: {m.get('rating')}")
        print(f"Vault URL: {m.get('vault_url')}")
        print(f"Notes: {m.get('notes')}")
        print(f"Files: {len(m.get('files', []))}")
    elif a.cmd == "search":
        q = a.query.lower()
        for n, m in lib.mods.items():
            hay = f"{n} {m.get('notes','')} {m.get('author','')} {m.get('vault_url','')}".lower()
            if q in hay:
                print(f"{'[ON]' if m.get('enabled') else '[OFF]'} {n}  (rating {m.get('rating',0)})")
    elif a.cmd == "group":
        gc = getattr(a, "group_cmd", None)
        if gc == "list" or gc is None:
            for name, mods in lib.groups().items():
                print(f"{name}: {len(mods)} mods -> {mods}")
        elif gc == "create":
            lib.create_group(a.name); print(f"[OK] group created: {a.name}")
        elif gc == "delete":
            lib.delete_group(a.name); print(f"[OK] group deleted: {a.name}")
        elif gc == "add":
            lib.add_to_group(a.mod, a.group); print(f"[OK] {a.mod} -> {a.group}")
        elif gc == "remove":
            lib.remove_from_group(a.mod); print(f"[OK] {a.mod} removed from group")
        elif gc == "activate":
            lib.activate_group(a.name); print(f"[OK] activated group {a.name}")
        elif gc == "deactivate":
            lib.deactivate_group(a.name); print(f"[OK] deactivated group {a.name}")
    elif a.cmd == "convert-bik":
        files = lib.find_bik_without_wbm() if a.all else None
        conv = lib.convert_bik(files)
        print(f"[OK] converted {len(conv)} file(s)")
        for c in conv:
            print(f"  {c}")
    elif a.cmd == "workshop":
        wsc = getattr(a, "ws_cmd", None)
        if wsc == "list" or wsc is None:
            for it in steam_workshop_items():
                print(f"{it['id']}  {it['title']}  {it['path']}")
        elif wsc == "import":
            items = [i for i in steam_workshop_items() if i["id"] == a.id]
            if not items:
                raise Err("Workshop item not found.")
            n = lib.install(items[0]["path"], a.name, cfg["lowercase"])
            print(f"[OK] imported workshop item: {n}")
    elif a.cmd == "extmap":
        ec = getattr(a, "em_cmd", None)
        if ec == "reset" or ec is None:
            cfg = load_cfg(); cfg["ext_map"] = None; cfg["ignore_ext"] = None; cfg["ignore_names"] = None
            save_cfg(cfg); print("[OK] mappings reset to defaults")
        else:
            cfg = load_cfg()
            print("ext_map:", cfg.get("ext_map"))
            print("ignore_ext:", cfg.get("ignore_ext"))
            print("ignore_names:", cfg.get("ignore_names"))
    elif a.cmd == "launch":
        launch(g)
    elif a.cmd == "saves":
        sc = getattr(a, "saves_cmd", None)
        if sc == "list" or sc is None:
            rows, active = lib.saves_list()
            print(f"Active save set: {active or '(none)'}")
            for sub, rel, size, owner in rows:
                print(f"{sub}/{rel}  {size} bytes  [{owner}]")
        elif sc == "switch":
            lib.saves_switch(a.mod)
            print(f"[OK] switched saves to '{a.mod}'")
        elif sc == "archive":
            p = lib.saves_archive(a.mod)
            print(f"[OK] archived: {p}")
        elif sc == "restore":
            lib.saves_restore(a.mod, from_archive=a.from_archive)
            print(f"[OK] restored saves for '{a.mod}'")


def main(argv=None):
    try:
        return cli(sys.argv[1:] if argv is None else argv)
    except Err as e:
        sys.exit(f"Error: {e}")