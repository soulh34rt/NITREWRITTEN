import argparse
import sys

from ._common import Err
from .config import load_cfg
from .detection import Game, detect_all, launch
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
        print(f"[OK] '{n}': {len(lib.mods[n]['files'])} files")
    elif a.cmd == "list":
        for n, m in lib.mods.items():
            print(f"{'[ON]' if m['enabled'] else '[OFF]'} {n}  ({len(m['files'])} files)")
    elif a.cmd == "enable":
        lib.enable(a.name, lambda c: "overwrite")
    elif a.cmd == "disable":
        lib.disable(a.name)
    elif a.cmd == "remove":
        lib.remove(a.name)
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
    elif a.cmd == "launch":
        launch(g)


def main(argv=None):
    try:
        return cli(sys.argv[1:] if argv is None else argv)
    except Err as e:
        sys.exit(f"Error: {e}")
