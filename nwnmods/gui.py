"""Graphical user interface for NITREWRITTEN — tkinter-based mod manager."""

import base64
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from datetime import datetime
from pathlib import Path

try:  # tkinter is only needed for the GUI; the CLI must work without it (headless servers)
    import tkinter as tk
    from tkinter import filedialog, messagebox, simpledialog, ttk
except ImportError:  # pragma: no cover - depends on the Python build
    tk = filedialog = messagebox = simpledialog = ttk = None

from . import config as _config
from . import files as _files
from . import vault as _vault
from ._common import (AUTHOR, Err, REPO_URL, STATE_COLORS, STATE_LABELS, STATE_SYMBOLS,
                      STEAM_APPID_EE, SYS, VERSION, about_text, conflict_partners,
                      group_conflicts, mod_state)
from .detection import (Game, classify, detect_all, documents_dir, ee_user_dir, launch,
                        open_path, other_bases, safe_dirs, steam_libraries, steam_workshop_items)
from .files import (CONTENT_DIRS, DEST, IGNORE_EXT, IGNORE_NAMES, KNOWN, LOWER_DIRS,
                    download, extract, fetch_bytes, map_path, setup_7zip)
from .library import Library
from .vault import (FIELD_LABELS, VAULT, VAULT_CACHE_DEFAULT_MB, http_get, parse_catalog,
                    parse_project, vault_cache_clear, vault_cache_dir, vault_cache_get,
                    vault_cache_put, vault_cache_size)


def load_cfg():
    return _config.load_cfg()


def save_cfg(c):
    return _config.save_cfg(c)

_splash_shown = False


def _show_splash(root, cfg):
    """Show the startup splash as a Toplevel over `root`; returns it or None.

    The window closes itself after cfg['splash_ms'] milliseconds (<=0 = no
    auto-close is treated as "no splash"). Never raises: a broken image or
    display problem must not block the app.
    """
    global _splash_shown
    if _splash_shown or not cfg.get("splash_enabled"):
        return None
    ms = int(cfg.get("splash_ms", 2000) or 2000)
    if ms <= 0:
        return None
    img_path = cfg.get("splash_image") or ""
    if not img_path:
        return None
    p = Path(img_path)
    if not p.exists():
        return None
    try:
        from PIL import Image, ImageTk
        root.withdraw()
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        img = Image.open(p)
        img.thumbnail((1024, 768))
        photo = ImageTk.PhotoImage(img)
        lbl = tk.Label(win, image=photo, bd=0)
        lbl.image = photo
        lbl.pack()
        win.update_idletasks()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        w, h = win.winfo_width(), win.winfo_height()
        win.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")
        win.after(ms, lambda: (win.destroy(), root.deiconify()))
        _splash_shown = True
        return win
    except Exception:  # noqa: BLE001
        try:
            root.deiconify()
        except Exception:  # noqa: BLE001
            pass
        return None


def run_gui(initial_user=None):
    if tk is None:
        raise SystemExit("The graphical interface requires tkinter (Linux: install python3-tk). "
                         "The command-line interface works without it.")

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title("NITREWRITTEN - NWN Mod Manager")
            self.geometry("1100x850")
            self.minsize(900, 700)
            
            # Load assets
            if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
                self.res = Path(sys._MEIPASS) / "resources"
            else:
                self.res = Path(__file__).resolve().parent.parent / "resources"
            try:
                if (self.res / "pluma.ico").exists():
                    try:
                        from PIL import Image, ImageTk
                        icon_img = Image.open(self.res / "pluma.ico")
                        self.icon_photo = ImageTk.PhotoImage(icon_img)
                        self.iconphoto(True, self.icon_photo)
                        if SYS == "Windows":
                            self.iconbitmap(str(self.res / "pluma.ico"))
                    except Exception:
                        if SYS == "Windows":
                            self.iconbitmap(str(self.res / "pluma.ico"))
            except Exception:
                pass
            
            self.cfg = load_cfg()
            self.games, self.game, self.lib = [], None, None
            self.vault_items, self.vault_info, self.vault_sel = [], {}, None
            self.vault_shown, self.vault_busy, self.vault_sort, self._img = [], False, ("posted", True), None
            self.v_dark = tk.BooleanVar(value=self.cfg.get("dark_mode", False))
            self.v_lower = tk.BooleanVar(value=self.cfg["lowercase"])
            self.v_convert_bik = tk.BooleanVar(value=self.cfg.get("convert_bik", True))
            self.v_policy = tk.StringVar(value=self.cfg["policy"])
            
            self._apply_theme() # Apply theme BEFORE building to set correct styles
            self._build()
            self.after(100, lambda: self.detect(initial_user))

        def _apply_theme(self):
            dark = self.v_dark.get()
            bg = "#1E1E1E" if dark else "#ffffff"
            panel = "#252526" if dark else "#f3f3f3"
            self._panel_bg = panel
            self._state_palette = STATE_COLORS["dark" if dark else "light"]
            fg = "#E0E0E0" if dark else "#000000"
            header_fg = "#4DA3FF" if dark else "#1a73e8"
            btn_bg = "#333337" if dark else "#e1e1e1"
            btn_active = "#3C3C3C" if dark else "#d0d0d0"
            tree_bg = "#252526" if dark else "#ffffff"
            tree_fg = "#E0E0E0" if dark else "#000000"
            field_bg = "#252526" if dark else "#ffffff"
            secondary_fg = "#9D9D9D" if dark else "#666666"
            border = "#3C3C3C" if dark else "#c8c8c8"
            
            style = ttk.Style()
            # In Dark Mode, we use 'clam' even on Windows because 'vista/xpnative' doesn't support custom colors well
            if dark:
                style.theme_use("clam")
            else:
                if SYS == "Windows": style.theme_use("vista")
                elif SYS == "Darwin": style.theme_use("aqua")
                else: style.theme_use("clam")
                
            style.configure("TFrame", background=panel)
            style.configure("TLabel", background=panel, foreground=fg, font=("Segoe UI", 13))
            style.configure("Secondary.TLabel", background=panel, foreground=secondary_fg, font=("Segoe UI", 11))
            style.configure("Header.TLabel", background=panel, foreground=header_fg, font=("Segoe UI", 13, "bold"))
            style.configure("TButton", background=btn_bg, foreground=fg, font=("Segoe UI", 12), padding=(8, 3))
            style.map("TButton", background=[("active", btn_active)], foreground=[("active", header_fg)])
            style.configure("Treeview", background=tree_bg, foreground=tree_fg, fieldbackground=tree_bg, font=("Segoe UI", 12), rowheight=26)
            style.configure("Treeview.Heading", background=btn_bg, foreground=fg, borderwidth=1, font=("Segoe UI", 12, "bold"), relief="flat")
            style.map("Treeview", background=[("selected", "#264F78")], foreground=[("selected", "#ffffff")])
            
            style.configure("TNotebook", background=bg, borderwidth=0)
            style.configure("TNotebook.Tab", background=btn_bg, foreground=fg, padding=[10, 4])
            style.map("TNotebook.Tab", background=[("selected", panel)], foreground=[("selected", header_fg)])
            
            style.configure("TCombobox", fieldbackground=field_bg, background=btn_bg, foreground=fg, arrowcolor=fg, bordercolor=border)
            style.map("TCombobox", fieldbackground=[("readonly", field_bg)], foreground=[("readonly", fg)])
            
            style.configure("TEntry", fieldbackground=field_bg, foreground=fg, insertcolor=fg, bordercolor=border)
            style.map("TEntry", fieldbackground=[("readonly", field_bg)], foreground=[("readonly", fg)])
            
            style.configure("TSeparator", background=border)
            style.configure("TPanedwindow", background=bg)
            
            style.configure("TCheckbutton", background=bg, foreground=fg)
            style.map("TCheckbutton", background=[("active", bg)], foreground=[("active", fg)])

            self.configure(background=bg)
            if hasattr(self, "banner_lbl"):
                self.banner_lbl.configure(background=bg)
            
            if hasattr(self, "logbox"):
                self.logbox.configure(background=tree_bg, foreground=fg,
                                      insertbackground=fg,
                                      highlightthickness=1,
                                      highlightbackground=border,
                                      borderwidth=0)
            if hasattr(self, "v_det"):
                self.v_det.configure(background=tree_bg, foreground=fg,
                                     insertbackground=fg,
                                     highlightthickness=1,
                                     highlightbackground=border,
                                     borderwidth=0)
            
            # Listbox/Text widgets need manual config as they are tk not ttk
            listbox_args = {"background": tree_bg, "foreground": tree_fg, 
                            "selectbackground": header_fg, "borderwidth": 0, "highlightthickness": 1,
                            "highlightbackground": "#444" if dark else "#ccc"}
            for attr in ("lb_prof", "lb_bak", "lb_vfiles", "lb_games"):
                if hasattr(self, attr):
                    getattr(self, attr).configure(**listbox_args)

            # Global tag configuration for text widgets if needed
            if hasattr(self, "v_det"):
                self.v_det.tag_configure("header", foreground=header_fg, font=("Segoe UI", 12, "bold"))
                self.v_det.tag_configure("field", foreground=secondary_fg)

            # Mod list state colors (STATE_COLORS is the single source of truth)
            if hasattr(self, "tv_mods"):
                self._config_mod_tags()
                self._update_mod_mark()
            if hasattr(self, "_legend_labels"):
                for st, lbl in self._legend_labels.items():
                    lbl.configure(fg=self._state_palette[st][0], bg=panel)
            # Detail panel tags + refresh (conflict section must follow the theme)
            self._det_colors = {"fg": fg, "header": header_fg, "secondary": secondary_fg, "bg": tree_bg}
            if hasattr(self, "mod_det"):
                self._config_mod_detail_tags()
                self._update_mod_detail()
            if hasattr(self, "_mod_tip") and self._mod_tip is not None:
                self._hide_mod_tooltip()

        # ---- construccion
        def _build(self):
            # Toolbar (28px): icon + name, installation selector, actions
            tb = ttk.Frame(self, padding=(6, 4))
            tb.pack(fill="x")
            try:
                from PIL import Image, ImageTk
                if (self.res / "pluma.ico").exists():
                    ic = Image.open(self.res / "pluma.ico")
                    ic.thumbnail((20, 20))
                    self._toolbar_icon = ImageTk.PhotoImage(ic)
                    ttk.Label(tb, image=self._toolbar_icon).pack(side="left", padx=(0, 4))
            except Exception:
                pass
            ttk.Label(tb, text="NitRewritten", font=("Segoe UI", 11, "bold")).pack(side="left", padx=(0, 10))
            ttk.Label(tb, text="NWN Installation:").pack(side="left", padx=(0, 4))
            self.combo = ttk.Combobox(tb, state="readonly")
            self.combo.pack(side="left", fill="x", expand=True, padx=4)
            self.combo.bind("<<ComboboxSelected>>", lambda e: self.set_game(self.combo.current()))
            for txt, fn in (("Scan", self.detect), ("Add...", self.add_folder),
                            ("Folder", lambda: self.game and open_path(self.game.user)),
                            ("Launch Game", self.play)):
                ttk.Button(tb, text=txt, command=fn).pack(side="left", padx=2)

            # Notebook (tabs flush to content)
            nb = ttk.Notebook(self)
            nb.pack(fill="both", expand=True, padx=6, pady=(2, 0))
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

            # Status bar (26px)
            sb = ttk.Frame(self, padding=(6, 3))
            sb.pack(fill="x", side="bottom")
            self.status_left = ttk.Label(sb, text="", style="Secondary.TLabel")
            self.status_left.pack(side="left")
            self.status_right = ttk.Label(sb, text="", style="Secondary.TLabel")
            self.status_right.pack(side="right")

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

            def _onscroll(*a):
                sb.set(*a)
                if tv is getattr(self, "tv_mods", None):
                    self._update_mod_mark()

            tv.configure(yscrollcommand=_onscroll)
            tv.pack(side="left", fill="both", expand=True)
            sb.pack(side="right", fill="y")
            return tv

        # ---- mod list state colors (single source of truth: STATE_COLORS)
        def _config_mod_tags(self):
            for st, (fg_c, bg_c) in self._state_palette.items():
                self.tv_mods.tag_configure(st, foreground=fg_c, background=bg_c)
            # Selected rows keep the blue selection; the state mark shows on the left.
            self.tv_mods.tag_configure("sel", background="#264F78", foreground="#ffffff")

        def _mods_select(self, _e=None):
            tv = self.tv_mods
            sel = set(tv.selection())
            for iid in tv.get_children():
                tags = [t for t in tv.item(iid, "tags") if t != "sel"]
                if iid in sel:
                    tags.append("sel")
                if list(tv.item(iid, "tags")) != tags:
                    tv.item(iid, tags=tags)
            self._update_mod_mark()
            self._update_mod_detail()

        def _update_mod_mark(self):
            """State-color strip on the left edge of the selected mod row."""
            mark = getattr(self, "_state_mark", None)
            if mark is None:
                return
            sel = self.tv_mods.selection()
            bb = self.tv_mods.bbox(sel[0]) if sel else None
            if not bb:
                mark.place_forget()
                return
            x, y, _w, h = bb
            tags = self.tv_mods.item(sel[0], "tags")
            state = next((t for t in tags if t in STATE_LABELS), "active")
            mark.configure(bg=self._state_palette[state][0])
            mark.place(x=x, y=y, width=4, height=h)

        # ---- mod detail panel (info + conflicts, click a mod name to select it)
        def _config_mod_detail_tags(self):
            c = getattr(self, "_det_colors", None)
            if not c or not hasattr(self, "mod_det"):
                return
            det = self.mod_det
            det.configure(background=c["bg"], foreground=c["fg"], insertbackground=c["fg"])
            det.tag_configure("header", foreground=c["header"], font=("Segoe UI", 12, "bold"))
            det.tag_configure("section", foreground=c["header"], font=("Segoe UI", 11, "bold"))
            det.tag_configure("field", foreground=c["secondary"], font=("Segoe UI", 9, "bold"))
            det.tag_configure("value", foreground=c["fg"], font=("Segoe UI", 10))
            det.tag_configure("file", foreground=c["secondary"], font=("Consolas", 9))
            det.tag_configure("modlink", foreground=c["header"], underline=True,
                              font=("Segoe UI", 10, "bold"))
            det.tag_configure("toggle", foreground=c["header"], underline=True,
                              font=("Segoe UI", 9))
            for st in ("active", "disabled", "conflict"):
                fg_c, _bg_c = self._state_palette[st]
                det.tag_configure(f"state_{st}", foreground=fg_c, font=("Segoe UI", 10, "bold"))

        def _update_mod_detail(self):
            det = getattr(self, "mod_det", None)
            if det is None:
                return
            det.configure(state="normal")
            det.delete("1.0", "end")
            for t in getattr(self, "_mod_detail_tags", []):
                try:
                    det.tag_delete(t)
                except tk.TclError:
                    pass
            self._mod_detail_tags = []
            sel = self.tv_mods.selection()
            name = sel[0] if sel else None
            if not self.lib or not name or name not in self.lib.mods:
                det.insert("end", "Select a mod to see its details and conflicts.", ("value",))
                det.configure(state="disabled")
                return
            m = self.lib.mods[name]
            partners = getattr(self, "_mod_partners", {})
            conflicts = getattr(self, "_mod_conflicts", {}).get(name, {})
            state = mod_state(m["enabled"], name in partners)
            det.insert("end", f"{name}\n", "header")
            status = f"{STATE_SYMBOLS[state]} {STATE_LABELS[state]}"
            if state == "conflict":
                status += f" ({len(partners[name])})"
            det.insert("end", f"{status}\n\n", f"state_{state}")
            for label, value in (
                ("Files", str(len(m["files"]))),
                ("Date", m["date"][:10]),
                ("Source", m["source"] if m["source"].startswith("http") else Path(m["source"]).name),
                ("Author", m.get("author") or "-"),
                ("Notes", m.get("notes") or "-"),
            ):
                det.insert("end", f"{label}: ", "field")
                det.insert("end", f"{value}\n", "value")
            if conflicts:
                det.insert("end", "\nConflicts\n", "section")
                show_all = name in self._detail_expanded
                total = 0
                for other in sorted(conflicts):
                    files = conflicts[other]
                    total += len(files)
                    tag = f"mod::{other}"
                    det.insert("end", f"{other} ", ("modlink", tag))
                    det.insert("end", f"({len(files)} shared)\n", "field")
                    det.tag_bind(tag, "<Button-1>", lambda _e, o=other: self._select_mod(o))
                    self._mod_detail_tags.append(tag)
                    shown = files if show_all else files[:10]
                    for f in shown:
                        det.insert("end", f"  {f}\n", "file")
                    if not show_all and len(files) > len(shown):
                        det.insert("end", f"  ... {len(files) - len(shown)} more\n", "field")
                if total > 10:
                    label = "Hide extra files" if show_all else f"Show all files ({total})"
                    tag = "toggle::all"
                    det.insert("end", f"\n{label}\n", ("toggle", tag))
                    det.tag_bind(tag, "<Button-1>", lambda _e: self._toggle_detail_files())
                    self._mod_detail_tags.append(tag)
            else:
                note = ("Mod is disabled - disabled mods never conflict."
                        if not m["enabled"] else "No conflicts with other active mods.")
                det.insert("end", f"\n{note}\n", "value")
            det.configure(state="disabled")

        def _detail_motion(self, event):
            """Hand cursor over clickable mod names / show-all links in the detail panel."""
            idx = self.mod_det.index(f"@{event.x},{event.y}")
            over_link = any(t.startswith("mod::") or t == "toggle::all"
                            for t in self.mod_det.tag_names(idx))
            self.mod_det.configure(cursor="hand2" if over_link else "")

        def _toggle_detail_files(self):
            sel = self.tv_mods.selection()
            if not sel:
                return
            name = sel[0]
            if name in self._detail_expanded:
                self._detail_expanded.discard(name)
            else:
                self._detail_expanded.add(name)
            self._update_mod_detail()

        def _select_mod(self, name):
            tv = self.tv_mods
            if not tv.exists(name):
                self.q_mods.set("")  # clear the search filter so the mod becomes visible
            if tv.exists(name):
                tv.selection_set(name)
                tv.see(name)

        # ---- conflict tooltip on mod rows
        def _mod_tooltip(self, event):
            row = self.tv_mods.identify_row(event.y)
            names = sorted(getattr(self, "_mod_partners", {}).get(row, ())) if row else []
            if not names:
                self._hide_mod_tooltip()
                return
            text = "Conflicts with: " + ", ".join(names)
            tip = getattr(self, "_mod_tip", None)
            if tip is None:
                tip = tk.Toplevel(self)
                tip.overrideredirect(True)
                tip.attributes("-topmost", True)
                self._mod_tip_lbl = tk.Label(tip, padx=6, pady=2, font=("Segoe UI", 9),
                                             relief="flat", borderwidth=1)
                self._mod_tip_lbl.pack()
                self._mod_tip = tip
            lbl = self._mod_tip_lbl
            c = getattr(self, "_det_colors", {})
            lbl.configure(text=text, bg=self._panel_bg, fg=c.get("fg", "#E0E0E0"),
                          highlightthickness=1, highlightbackground=c.get("secondary", "#666666"))
            tip.geometry(f"+{event.x_root + 14}+{event.y_root + 16}")
            tip.deiconify()

        def _hide_mod_tooltip(self):
            tip = getattr(self, "_mod_tip", None)
            if tip is not None:
                tip.withdraw()

        def _build_mods(self):
            p = self.tab_mods
            self._btns(p, (("Install Files...", self.install_files), ("Install Folder...", self.install_dir),
                           ("Install URL...", self.install_url), ("Enable", self.enable),
                           ("Disable", self.disable), ("Uninstall", self.remove),
                           ("View Files", self.show_files), ("Conflicts", self.show_conflicts)))

            filter_frame = ttk.Frame(p)
            filter_frame.pack(fill="x", pady=(0, 8))
            ttk.Label(filter_frame, text="Search: ", style="Secondary.TLabel").pack(side="left")
            self.q_mods = tk.StringVar()
            e = ttk.Entry(filter_frame, textvariable=self.q_mods)
            e.pack(side="left", fill="x", expand=True)
            self.q_mods.trace_add("write", lambda *a: self.refresh_mods())

            # Detail-panel state (must exist before any <<TreeviewSelect>> fires)
            self._mod_detail_tags = []
            self._detail_expanded = set()
            self._mod_partners = {}
            self._mod_conflicts = {}

            # Legend under the table (packed bottom-first so the tree fills above it)
            # Paned: table+legend (left) | detail panel with conflicts (right)
            paned = ttk.PanedWindow(p, orient="horizontal")
            paned.pack(fill="both", expand=True)
            left = ttk.Frame(paned)
            paned.add(left, weight=3)
            right = ttk.Frame(paned, padding=6)
            paned.add(right, weight=1)
            right.configure(width=300)

            leg = ttk.Frame(left)
            leg.pack(fill="x", side="bottom", pady=(4, 0))
            self._legend_labels = {}
            for st in ("active", "disabled", "conflict"):
                lbl = tk.Label(leg, text=f"{STATE_SYMBOLS[st]} {STATE_LABELS[st]}",
                               fg=self._state_palette[st][0], bg=self._panel_bg,
                               font=("Segoe UI", 10))
                lbl.pack(side="left", padx=(0, 14))
                self._legend_labels[st] = lbl

            self.tv_mods = self._tree(left, ("name", "status", "files", "date", "source"),
                                      (260, 150, 70, 100, 340))
            for c in ("name", "status", "files", "date", "source"):
                self.tv_mods.heading(c, text=c.title())
            self._config_mod_tags()
            self.tv_mods.bind("<<TreeviewSelect>>", self._mods_select)
            self.tv_mods.bind("<Motion>", self._mod_tooltip)
            self.tv_mods.bind("<Leave>", lambda e: self._hide_mod_tooltip())
            # Small state-color strip drawn over the left edge of the selected row
            self._state_mark = tk.Frame(self.tv_mods.master, width=4, highlightthickness=0)
            self._state_mark.place_forget()

            # Detail panel: mod info + Conflicts section (click a mod name to select it)
            ttk.Label(right, text="Details", style="Secondary.TLabel").pack(anchor="w")
            self.mod_det = tk.Text(right, wrap="word", state="disabled", font=("Segoe UI", 10),
                                   relief="flat", borderwidth=0, padx=4, pady=4)
            self.mod_det.pack(fill="both", expand=True, pady=(2, 0))
            self.mod_det.bind("<Motion>", self._detail_motion)
            self._config_mod_detail_tags()

        def _build_content(self):
            p = self.tab_cont
            f = ttk.Frame(p)
            f.pack(fill="x", pady=(0, 8))
            self.cat = ttk.Combobox(f, state="readonly", values=["(All)"] + CONTENT_DIRS, width=16)
            self.cat.current(0)
            self.cat.pack(side="left")
            self.cat.bind("<<ComboboxSelected>>", lambda e: self.refresh_content())
            
            self.q_cont = tk.StringVar()
            e = ttk.Entry(f, textvariable=self.q_cont)
            e.pack(side="left", fill="x", expand=True, padx=8)
            self.q_cont.trace_add("write", lambda *a: self.refresh_content())
            
            ttk.Button(f, text="Clean Up", command=self.do_cleanup).pack(side="left", padx=2)
            
            self.lbl_cont = ttk.Label(f, style="Secondary.TLabel")
            self.lbl_cont.pack(side="left")
            self.tv_cont = self._tree(p, ("folder", "file", "size", "mod"), (110, 520, 90, 220))
            for c in ("folder", "file", "size", "mod"):
                self.tv_cont.heading(c, text=c.title())

        def _build_profiles(self):
            p = self.tab_prof
            ttk.Label(p, text="Profiles allow you to save and switch between different sets of active mods.",
                      style="Secondary.TLabel").pack(anchor="w", pady=(5, 10))
            self._btns(p, (("Save Current Profile...", self.prof_save), ("Apply Selected", self.prof_apply),
                           ("Delete Profile", self.prof_del)))
            self.lb_prof = tk.Listbox(p, font=("Segoe UI", 10), borderwidth=1, relief="solid")
            self.lb_prof.pack(fill="both", expand=True, pady=5)

        def _build_backups(self):
            p = self.tab_bak
            ttk.Label(p, text="Manage backups of your save games and characters.",
                      style="Secondary.TLabel").pack(anchor="w", pady=(5, 10))
            self._btns(p, (("Create New Backup", self.bak_make), ("Restore Selected", self.bak_restore),
                           ("Open Backups Folder", lambda: self.lib and open_path(self.lib.base / "backups"))))
            self.lb_bak = tk.Listbox(p, font=("Segoe UI", 10), borderwidth=1, relief="solid")
            self.lb_bak.pack(fill="both", expand=True, pady=5)

            ttk.Separator(p, orient="horizontal").pack(fill="x", pady=6)
            ttk.Label(p, text="Save sets (per mod/campaign):", style="Secondary.TLabel").pack(anchor="w")
            self.lb_saves = tk.Listbox(p, font=("Segoe UI", 10), borderwidth=1, relief="solid", height=6)
            self.lb_saves.pack(fill="x", pady=4)
            bf = ttk.Frame(p)
            bf.pack(fill="x")
            for txt, fn in (("List", self.saves_list_ui), ("Switch", self.saves_switch_ui),
                            ("Archive", self.saves_archive_ui), ("Restore", self.saves_restore_ui)):
                ttk.Button(bf, text=txt, command=fn).pack(side="left", padx=2)

        def refresh_saves(self):
            if not hasattr(self, "lb_saves") or not self.lib:
                return
            self.lb_saves.delete(0, "end")
            rows, active = self.lib.saves_list()
            self.lb_saves.insert("end", f"Active: {active or '(none)'}")
            for sub, rel, size, owner in rows[:200]:
                self.lb_saves.insert("end", f"{owner} | {sub}/{rel} ({size} B)")

        def saves_list_ui(self):
            if not self._need_lib():
                return
            self.refresh_saves()
            rows, active = self.lib.saves_list()
            self._text_window("Save sets", "\n".join(f"{o} | {s}/{r} ({sz} B)" for s, r, sz, o in rows) or "No saves.")

        def saves_switch_ui(self):
            if not self._need_lib():
                return
            mod = simpledialog.askstring("Switch saves", "Mod/campaign name:")
            if mod:
                self.guard(self.lib.saves_switch, mod)
                self.log(f"Saves switched to '{mod}'")
                self.refresh_saves()

        def saves_archive_ui(self):
            if not self._need_lib():
                return
            mod = simpledialog.askstring("Archive saves", "Mod/campaign name:")
            if mod:
                p = self.guard(self.lib.saves_archive, mod)
                if p:
                    self.log(f"Archived saves: {p}")
                    self.refresh_saves()

        def saves_restore_ui(self):
            if not self._need_lib():
                return
            mod = simpledialog.askstring("Restore saves", "Mod/campaign name:")
            if mod:
                froma = messagebox.askyesno("From archive?", "Restore from compressed archive?")
                self.guard(self.lib.saves_restore, mod, from_archive=froma)
                self.log(f"Restored saves for '{mod}'")
                self.refresh_saves()

        def _build_settings(self):
            p = self.tab_set
            ttk.Checkbutton(p, text="Dark Mode",
                            variable=self.v_dark, command=self.save_settings).pack(anchor="w", pady=8)
            
            ttk.Checkbutton(p, text="Lowercase file names for hak/tlk/override (Recommended for Linux)",
                            variable=self.v_lower, command=self.save_settings).pack(anchor="w", pady=8)
            
            ttk.Label(p, text="When a file already exists during installation:", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(15, 5))
            for v, t in (("ask", "Ask every time"), 
                         ("overwrite", "Overwrite (Backup will be created and restored on uninstall)"),
                         ("skip", "Skip existing files")):
                ttk.Radiobutton(p, text=t, value=v, variable=self.v_policy,
                                command=self.save_settings).pack(anchor="w", padx=20, pady=2)
            
            ttk.Checkbutton(p, text="Convert BIK to WBM for EE (requires FFmpeg)",
                            variable=self.v_convert_bik, command=self.save_settings).pack(anchor="w", pady=8)
            ttk.Checkbutton(p, text="Basic mode (restart required)",
                            variable=tk.BooleanVar(value=load_cfg().get("basic_mode", False)),
                            command=lambda: save_cfg({**load_cfg(), "basic_mode": not load_cfg().get("basic_mode", False)})).pack(anchor="w", pady=8)
            ttk.Button(p, text="Convert BIK files now", command=self.convert_bik_ui).pack(anchor="w", pady=4)
            
            ttk.Separator(p, orient="horizontal").pack(fill="x", pady=20)
            
            ttk.Button(p, text="Change Game Data Folder...",
                       command=self.change_user).pack(anchor="w", pady=5)
            ttk.Label(p, text=f"Config location: {CFG}", style="Secondary.TLabel").pack(anchor="w", pady=5)
            try:
                from .files import find_7zip, sevenzip_version
            except Exception:
                from nwnmods.files import find_7zip, sevenzip_version
            p7, src = find_7zip()
            ver = sevenzip_version()
            ttk.Label(p, text=f"7-Zip: {ver or p7 or 'not found'} ({src})", style="Secondary.TLabel").pack(anchor="w", pady=5)

            ttk.Separator(p, orient="horizontal").pack(fill="x", pady=20)

            # ---- NWN start screen (main menu background)
            ttk.Label(p, text="Game start screen (main menu background):",
                      font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(5, 3))
            self.lbl_startscreen = ttk.Label(p, text="", style="Secondary.TLabel")
            self.lbl_startscreen.pack(anchor="w", pady=2)
            bf = ttk.Frame(p)
            bf.pack(anchor="w", pady=4)
            ttk.Button(bf, text="Choose image...", command=self.startscreen_choose_ui).pack(side="left", padx=(0, 6))
            ttk.Button(bf, text="Restore original", command=self.startscreen_restore_ui).pack(side="left")
            self.refresh_startscreen_label()

            # ---- App splash screen
            ttk.Label(p, text="App splash screen on startup:",
                      font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(15, 3))
            self.v_splash = tk.BooleanVar(value=load_cfg().get("splash_enabled", False))
            ttk.Checkbutton(p, text="Show splash window when the app starts",
                            variable=self.v_splash,
                            command=self.save_settings).pack(anchor="w", pady=2)
            sf = ttk.Frame(p)
            sf.pack(anchor="w", pady=4)
            ttk.Button(sf, text="Choose splash image...", command=self.splash_choose_ui).pack(side="left", padx=(0, 6))
            ttk.Button(sf, text="Clear", command=self.splash_clear_ui).pack(side="left")
            self.lbl_splash = ttk.Label(p, text="", style="Secondary.TLabel")
            self.lbl_splash.pack(anchor="w", pady=2)
            self.refresh_splash_label()

            ttk.Separator(p, orient="horizontal").pack(fill="x", pady=20)

            ttk.Button(p, text=f"About NITREWRITTEN {VERSION}...",
                       command=self.show_about).pack(anchor="w", pady=4)

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
                self.log(f"âœ˜ {e}")
                messagebox.showerror("NWN Mod Manager", str(e))
            except Exception as e:  # noqa: BLE001
                self.log(f"âœ˜ Unexpected error: {e!r}")
                messagebox.showerror("Unexpected error", repr(e))
            finally:
                self.configure(cursor="")

        def save_settings(self):
            self.cfg["lowercase"], self.cfg["policy"] = self.v_lower.get(), self.v_policy.get()
            self.cfg["dark_mode"] = self.v_dark.get()
            self.cfg["convert_bik"] = self.v_convert_bik.get()
            if hasattr(self, "v_splash"):
                self.cfg["splash_enabled"] = bool(self.v_splash.get())
            if self.game is not None:
                ip = self.cfg.setdefault("installations", {}).setdefault(str(self.game.root), {})
                ip["lowercase"] = self.v_lower.get()
                ip["policy"] = self.v_policy.get()
            save_cfg(self.cfg)
            self._apply_theme()

        # ---- NWN start screen + app splash (Settings)
        def refresh_startscreen_label(self):
            if not hasattr(self, "lbl_startscreen"):
                return
            if not self.lib:
                self.lbl_startscreen.configure(text="No game folder selected.")
                return
            cur = self.lib.current_start_screen()
            if cur:
                self.lbl_startscreen.configure(text=f"Custom: {cur['path']} ({cur['size']} bytes)")
            else:
                self.lbl_startscreen.configure(text="Game default (no custom image)")

        def startscreen_choose_ui(self):
            if not self._need_lib():
                return
            p = filedialog.askopenfilename(
                title="Select start screen image",
                filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.tga *.webp"), ("All files", "*.*")])
            if not p:
                return
            dst = self.guard(self.lib.set_start_screen, p)
            if dst:
                self.log(f"Start screen installed: {dst}")
                self.refresh_startscreen_label()

        def startscreen_restore_ui(self):
            if not self._need_lib():
                return
            if not self.lib.current_start_screen():
                messagebox.showinfo("Start screen", "No custom start screen in use.")
                return
            if not messagebox.askyesno("Restore start screen",
                                       "Remove the custom start screen?\n\n"
                                       "It will be kept in .nwnmods/trash/ so you can restore it later."):
                return
            if self.guard(self.lib.restore_start_screen):
                self.log("Custom start screen removed (kept in .nwnmods/trash/)")
                self.refresh_startscreen_label()

        def refresh_splash_label(self):
            if not hasattr(self, "lbl_splash"):
                return
            cfg = load_cfg()
            img = cfg.get("splash_image") or "(none)"
            ms = cfg.get("splash_ms", 2000)
            self.lbl_splash.configure(text=f"Image: {img}  |  {ms} ms")

        def splash_choose_ui(self):
            p = filedialog.askopenfilename(
                title="Select splash image",
                filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.gif"), ("All files", "*.*")])
            if not p:
                return
            cfg = load_cfg()
            cfg["splash_image"] = str(p)
            cfg["splash_enabled"] = True
            save_cfg(cfg)
            if hasattr(self, "v_splash"):
                self.v_splash.set(True)
            self.log(f"Splash image set: {p}")
            self.refresh_splash_label()

        def splash_clear_ui(self):
            cfg = load_cfg()
            cfg["splash_image"] = None
            save_cfg(cfg)
            self.refresh_splash_label()

        def ask_conflict(self, conf):
            pol = self.v_policy.get()
            if pol in ("overwrite", "skip"):
                return pol
            lines = "\n".join(f"â€¢ {r}  â†  {o}" for r, o in conf[:12])
            more = f"\nâ€¦ and {len(conf) - 12} more" if len(conf) > 12 else ""
            r = messagebox.askyesnocancel(
                "File Conflicts",
                f"{len(conf)} file(s) already exist:\n\n{lines}{more}\n\nYes = Overwrite (with backup)\n"
                "No = Skip them\nCancel = Abort installation")
            return "overwrite" if r else ("skip" if r is False else "cancel")

        # ---- instalaciones
        def detect(self, initial_user=None):
            self.games = self.guard(detect_all, self.cfg) or []
            if isinstance(initial_user, (str, Path)):
                self.games.insert(0, Game(initial_user, "Custom", "manual", initial_user))
            self.combo["values"] = [g.label for g in self.games]
            self.log(f"Installations detected: {len(self.games)}")
            if not self.games:
                self.game = self.lib = None
                self.status_left.configure(text="No installations found. Use 'Add...' to manually locate your game folder.")
                self.refresh_all()
                return
            last = self.cfg.get("last")
            idx = next((i for i, g in enumerate(self.games) if str(g.root) == last), 0)
            self.combo.current(idx)
            self.set_game(idx)

        def add_folder(self):
            d = filedialog.askdirectory(title="NWN Installation or Data Folder")
            if not d:
                return
            ed = classify(Path(d))
            if ed:
                self.cfg["manual"].append({"root": d, "edition": ed})
            elif messagebox.askyesno("Unknown Folder",
                                     "This doesn't look like a standard NWN installation. Use it as a data folder "
                                     "(where hak, override, modules... are located)?"):
                self.cfg["manual"].append({"root": d, "edition": "Custom", "user": d})
            else:
                return
            save_cfg(self.cfg)
            self.detect()

        def set_game(self, i):
            self.game = self.games[i]
            self.lib = Library(self.game.user)
            self.cfg["last"] = str(self.game.root)
            save_cfg(self.cfg)
            self.status_left.configure(text=f"Game data: {self.game.user}")
            ip = self.cfg.setdefault("installations", {}).setdefault(str(self.game.root), {})
            self.v_lower.set(ip.get("lowercase", self.cfg.get("lowercase", True)))
            self.v_policy.set(ip.get("policy", self.cfg.get("policy", "ask")))
            want = "NWN 1" if self.game.edition == "Diamond" else "NWN:EE"
            if not self.vault_items:
                self.v_game.set(want)
            self.refresh_all()

        def change_user(self):
            if not self.game:
                return
            d = filedialog.askdirectory(title="Select Game Data Folder (hak, override, modules...)")
            if d:
                self.cfg["user_override"][str(self.game.root)] = d
                save_cfg(self.cfg)
                self.game.user = Path(d)
                self.set_game(self.combo.current())

        def play(self):
            if self.game and self.lib:
                self.lib.start_playtime(self.lib.active_save_mod())
                proc = self.guard(launch, self.game)
                if proc is not None:
                    def watcher():
                        try:
                            proc.wait()
                        except Exception:
                            pass
                        try:
                            self.lib.end_playtime()
                        except Exception:
                            pass
                        errs = []
                        try:
                            errs = self.lib.nwn_log_errors()
                        except Exception:
                            errs = []
                        if errs:
                            msg = "\\n".join(errs[:20])
                            try:
                                self.after(0, lambda m=msg: messagebox.showwarning("NWN log", m))
                            except Exception:
                                pass
                    import threading
                    threading.Thread(target=watcher, daemon=True).start()

        # ---- refrescos
        def refresh_all(self):
            self.refresh_mods()
            self.refresh_content()
            self.refresh_profiles()
            self.refresh_backups()
            self.refresh_saves()
            if hasattr(self, "status_right") and self.lib:
                self.status_right.configure(text=f"{len(self.lib.mods)} mods")

        def refresh_mods(self):
            self.tv_mods.delete(*self.tv_mods.get_children())
            if not self.lib:
                self._mod_partners = {}
                self._mod_conflicts = {}
                self._update_mod_mark()
                return
            self._mod_conflicts = self.lib.conflicts_by_mod()
            partners = conflict_partners(self.lib.conflicts_report())
            self._mod_partners = partners
            q = self.q_mods.get().lower()
            for n, m in self.lib.mods.items():
                hay = f"{n} {m.get('notes','')} {m.get('author','')}".lower()
                if q in hay:
                    state = mod_state(m["enabled"], n in partners)
                    label = STATE_LABELS[state]
                    if state == "conflict":
                        label += f" ({len(partners[n])})"
                    status = f"{STATE_SYMBOLS[state]} {label}"
                    self.tv_mods.insert("", "end", iid=n, values=(
                        n, status, len(m["files"]),
                        m["date"][:10], m["source"] if m["source"].startswith("http") else Path(m["source"]).name),
                        tags=(state,))
            self._mods_select()

        def refresh_content(self):
            self.tv_cont.delete(*self.tv_cont.get_children())
            self.lbl_cont.configure(text="")
            if not self.lib:
                return
            cat = self.cat.get()
            rows = self.lib.scan(None if cat == "(all)" else cat)
            q = self.q_cont.get().lower()
            shown = [r for r in rows if q in r[1].lower() or q in r[3].lower()]
            for sub, key, size, owner in shown[:5000]:
                self.tv_cont.insert("", "end", values=(sub, key, f"{size / 1024:,.0f} KB", owner))
            self.lbl_cont.configure(text=f"{len(shown)} files" + (" (showing 5000)" if len(shown) > 5000 else ""))

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
                messagebox.showinfo("NITREWRITTEN", "Please select one or more mods from the list.")
            return s

        def _need_lib(self):
            if not self.lib:
                messagebox.showinfo("NITREWRITTEN", "Please select or add a game installation first.")
            return self.lib

        def do_install(self, paths):
            for p in paths:
                n = self.guard(self.lib.install, p, None, self.v_lower.get(), self.ask_conflict)
                if n:
                    self.log(f"âœ” Installed: {n}")
            self.refresh_all()

        def install_files(self):
            if not self._need_lib():
                return
            ps = filedialog.askopenfilenames(title="Select Mod Files", filetypes=[
                ("Mods & Packages", "*.zip *.7z *.rar *.tar *.gz *.bz2 *.xz *.hak *.tlk *.mod *.erf *.nwm"),
                ("All Files", "*.*")])
            if ps:
                self.do_install(ps)

        def install_dir(self):
            if self._need_lib():
                d = filedialog.askdirectory(title="Select Unpacked Mod Folder")
                if d:
                    self.do_install([d])

        def install_url(self):
            if not self._need_lib():
                return
            url = simpledialog.askstring("Install from URL", "Direct download link:")
            if not url:
                return
            self.log(f"Downloading {url} ...")
            tmp = Path(tempfile.mkdtemp(prefix="nwnmods-"))
            
            def worker():
                try:
                    f = download(url, tmp)
                    self.after(0, lambda: (self.do_install([f]), shutil.rmtree(tmp, ignore_errors=True)))
                except Exception as e:  # noqa: BLE001
                    self.after(0, lambda: (self.log(f"âœ˜ Download failed: {e}"),
                                           messagebox.showerror("Download Failed", str(e))))

            threading.Thread(target=worker, daemon=True).start()

        def enable(self):
            for n in self.selected():
                self.guard(self.lib.enable, n, self.ask_conflict)
                self.log(f"Enabled: {n}")
            self.refresh_all()
        
        def disable(self):
            for n in self.selected():
                self.guard(self.lib.disable, n)
                self.log(f"Disabled: {n}")
            self.refresh_all()
        
        def remove(self):
            s = self.selected()
            if s and messagebox.askyesno("Uninstall", f"Uninstall {len(s)} mod(s)?"):
                for n in s:
                    self.guard(self.lib.remove, n)
                    self.log(f"Uninstalled: {n}")
                self.refresh_all()

        def _text_window(self, title, text):
            dark = self.v_dark.get()
            bg = "#1e1e1e" if dark else "#ffffff"
            fg = "#e0e0e0" if dark else "#000000"
            w = tk.Toplevel(self)
            w.title(title)
            w.geometry("720x460")
            w.configure(background=bg)
            try:
                if (self.res / "pluma.ico").exists():
                    w.iconbitmap(str(self.res / "pluma.ico"))
            except Exception: pass
            t = tk.Text(w, wrap="none", background="#121212" if dark else "#ffffff", foreground=fg, 
                        insertbackground=fg, borderwidth=0, highlightthickness=0)
            t.insert("1.0", text)
            t.configure(state="disabled")
            sb_y = ttk.Scrollbar(w, orient="vertical", command=t.yview)
            sb_x = ttk.Scrollbar(w, orient="horizontal", command=t.xview)
            t.configure(yscrollcommand=sb_y.set, xscrollcommand=sb_x.set)
            sb_y.pack(side="right", fill="y")
            sb_x.pack(side="bottom", fill="x")
            t.pack(fill="both", expand=True)

        def show_about(self):
            """About screen: name + version, author, repo link, third-party credits."""
            dark = self.v_dark.get()
            bg = "#1e1e1e" if dark else "#ffffff"
            fg = "#e0e0e0" if dark else "#000000"
            header = "#4DA3FF" if dark else "#1a73e8"
            secondary = "#9D9D9D" if dark else "#666666"
            w = tk.Toplevel(self)
            w.title("About NITREWRITTEN")
            w.geometry("620x520")
            w.configure(background=bg)
            try:
                if (self.res / "pluma.ico").exists():
                    w.iconbitmap(str(self.res / "pluma.ico"))
            except Exception:
                pass
            t = tk.Text(w, wrap="word", background=bg, foreground=fg, insertbackground=fg,
                        borderwidth=0, highlightthickness=0, padx=16, pady=14,
                        font=("Segoe UI", 10), spacing1=2, spacing3=4)
            t.tag_configure("title", foreground=header, font=("Segoe UI", 16, "bold"),
                            spacing1=6, spacing3=10)
            t.tag_configure("author", foreground=fg, font=("Segoe UI", 11), spacing3=12)
            t.tag_configure("section", foreground=header, font=("Segoe UI", 11, "bold"),
                            spacing1=10, spacing3=4)
            t.tag_configure("link", foreground=header, underline=True, font=("Segoe UI", 10))
            t.tag_configure("legal", foreground=secondary, font=("Segoe UI", 9), spacing1=12)

            t.insert("end", f"NITREWRITTEN {VERSION}\n", "title")
            t.insert("end", f"Made by {AUTHOR}\n\n", "author")
            t.insert("end", "Repository: ", "author")
            t.insert("end", REPO_URL + "\n", "link")
            t.insert("end", "Third-party credits\n", "section")
            t.insert("end",
                     "- 7-Zip: archive extraction (LGPL). https://www.7-zip.org/\n"
                     "  Full license text: THIRD_PARTY_LICENSES (from resources/tools/License.txt).\n"
                     "- unRAR: optional .rar extraction via the unrar command-line tool (not bundled).\n"
                     "- FFmpeg: optional BIK to WBM conversion (not bundled; install separately).\n"
                     "- Neverwinter Vault: project catalog and images source. https://neverwintervault.org/\n"
                     "- NIT (Neverwinter Nights Mod Installer Tool) by Surazal: the original tool\n"
                     "  that inspired this project.\n")
            t.insert("end",
                     "https://neverwintervault.org/project/nwn1/other/tool/"
                     "neverwinter-nights-mod-installer-tool-nit\n", "link")
            t.insert("end",
                     "\nNeverwinter Nights is a trademark of Wizards of the Coast. This project is "
                     "not affiliated with Beamdog, Bioware or Wizards of the Coast.\n", "legal")
            t.configure(state="disabled")
            sb = ttk.Scrollbar(w, orient="vertical", command=t.yview)
            t.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            t.pack(fill="both", expand=True)

            def _on_click(event):
                idx = t.index(f"@{event.x},{event.y}")
                if "link" in t.tag_names(idx):
                    # read the whole link line under the cursor
                    line_start = t.index(f"{idx} lineend linestart")
                    line_end = t.index(f"{idx} lineend")
                    url = t.get(line_start, line_end).strip()
                    if url.startswith("http"):
                        webbrowser.open(url)

            def _on_motion(event):
                idx = t.index(f"@{event.x},{event.y}")
                t.configure(cursor="hand2" if "link" in t.tag_names(idx) else "")

            t.bind("<Button-1>", _on_click)
            t.bind("<Motion>", _on_motion)

        def show_files(self):
            for n in self.selected()[:1]:
                m = self.lib.mods[n]
                lines = []
                req = m.get("requires") or []
                if req:
                    lines.append("Requires: " + ", ".join(req))
                if m.get("notes"):
                    lines.append("Notes: " + m["notes"])
                if m.get("rating"):
                    lines.append("Rating: " + str(m["rating"]))
                lines.append("")
                lines += m["files"]
                self._text_window(f"{n} - {len(m['files'])} files", "\n".join(lines))

        def show_conflicts(self):
            if not self._need_lib():
                return
            grouped = self.lib.conflicts_by_mod()
            lines = []
            for n in sorted(grouped):
                for other in sorted(grouped[n]):
                    files = grouped[n][other]
                    lines.append(f"{n} <-> {other} ({len(files)} shared files)")
                    lines += [f"    {f}" for f in files]
            txt = "\n".join(lines)
            self._text_window("Mod Conflicts", txt or "No conflicts between active mods.")

        def do_cleanup(self):
            if not self._need_lib(): return
            preview = self.lib.cleanup_preview()
            if not preview:
                messagebox.showinfo("Clean Up", "No unmanaged files to remove (saves and characters are protected).")
                return
            sample = "\n".join(f"  {p}  ({s} bytes)" for p, s in preview[:20])
            more = f"\n  ... and {len(preview) - 20} more" if len(preview) > 20 else ""
            if not messagebox.askyesno("Clean Up Preview",
                                       f"The following {len(preview)} unmanaged file(s) will be moved to a recovery folder "
                                       f"(saves and characters are never touched):\n\n{sample}{more}\n\nContinue?"):
                return
            res = self.guard(self.lib.cleanup)
            if isinstance(res, dict):
                self.log(f"Cleaned up {res['count']} unmanaged files. Recovery: {res['trash']}")
            self.refresh_all()

        def convert_bik_ui(self):
            if not self._need_lib():
                return
            files = self.lib.find_bik_without_wbm()
            if not files:
                messagebox.showinfo("BIK Conversion", "No .bik files without .wbm found.")
                return
            if not self.lib.ffmpeg_available():
                messagebox.showerror("BIK Conversion", "FFmpeg not found. Install FFmpeg or set ffmpeg_path in config.")
                return
            if not messagebox.askyesno("BIK Conversion", f"Convert {len(files)} .bik file(s) to .wbm?"):
                return
            conv = self.guard(self.lib.convert_bik, files)
            if conv:
                self.log(f"Converted {len(conv)} .bik file(s).")
                self.refresh_all()

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
            self.v_lbl.configure(text="Network error")
            self.log(f"âœ˜ {e}")

        def _tab_changed(self, _e=None):
            if self.nb.select() == str(self.tab_vault) and not self.vault_items and not self.vault_busy:
                self.vault_load()

        def _build_vault(self):
            p = self.tab_vault
            top = ttk.Frame(p)
            top.pack(fill="x", pady=(0, 4))
            self.v_game = tk.StringVar(value="NWN:EE")
            g = ttk.Combobox(top, state="readonly", width=10, values=list(VAULT_GAMES), textvariable=self.v_game)
            g.pack(side="left")
            g.bind("<<ComboboxSelected>>", lambda e: self.vault_load())
            self.v_cat = ttk.Combobox(top, state="readonly", width=20, values=["(All)"])
            self.v_cat.set("(All)")
            self.v_cat.pack(side="left", padx=5)
            self.v_cat.bind("<<ComboboxSelected>>", lambda e: self.vault_filter())

            self.v_q = tk.StringVar()
            ttk.Label(top, text="Search: ", style="Secondary.TLabel").pack(side="left", padx=(5, 0))
            ttk.Entry(top, textvariable=self.v_q).pack(side="left", fill="x", expand=True, padx=5)
            self.v_q.trace_add("write", lambda *a: self.vault_filter())
            ttk.Button(top, text="Refresh", command=lambda: self.vault_load(True)).pack(side="left", padx=2)
            self.v_lbl = ttk.Label(top, foreground="#4DA3FF", font=("Segoe UI", 9, "bold"))
            self.v_lbl.pack(side="left", padx=10)

            # Paned: table (left) | detail (right, 290px min)
            paned = ttk.PanedWindow(p, orient="horizontal")
            paned.pack(fill="both", expand=True)

            left = ttk.Frame(paned)
            paned.add(left, weight=3)
            cols = ("name", "category", "author", "posted", "votes")
            self.tv_vault = self._tree(left, cols, (330, 110, 130, 90, 60))
            for c in cols:
                self.tv_vault.heading(c, text=c.title(), command=lambda c=c: self.vault_sort_by(c))
            self.tv_vault.bind("<<TreeviewSelect>>", self.vault_select)

            right = ttk.Frame(paned, padding=6)
            paned.add(right, weight=1)
            # min width for detail
            right.configure(width=290)

            self.v_img = ttk.Label(right, anchor="center")
            self.v_img.pack(fill="x", pady=(0, 6))
            self.v_det = tk.Text(right, height=8, wrap="word", state="disabled")
            self.v_det.pack(fill="both", expand=True)
            ttk.Label(right, text="Files:").pack(anchor="w", pady=(6, 0))
            self.lb_vfiles = tk.Listbox(right, height=4, exportselection=False, font=("Segoe UI", 9))
            self.lb_vfiles.pack(fill="x")
            ttk.Button(right, text="Download & Install", command=self.vault_install).pack(fill="x", pady=(6, 2))
            ttk.Button(right, text="Open Page", command=lambda: self.vault_sel and webbrowser.open(self.vault_sel["url"])).pack(fill="x")

        def vault_load(self, force=False):
            if self.vault_busy:
                return
            self.vault_busy = True
            self.v_lbl.configure(text="Loading catalog...")
            game = VAULT_GAMES[self.v_game.get()]
            
            def ok(items):
                self.vault_busy = False
                self.vault_items = items
                self.v_cat.configure(values=["(All)"] + sorted({i["cat"] for i in items if i["cat"]}))
                self.v_cat.set("(All)")
                self.log(f"Vault: {len(items)} projects ({self.v_game.get()})")
                self.vault_filter()

            self.bg(lambda: vault_catalog(game, force), lambda i: ok(i))

        def vault_sort_by(self, col):
            cur, desc = self.vault_sort
            self.vault_sort = (col, not desc if cur == col else col in ("posted", "votes"))
            self.vault_filter()

        def vault_filter(self):
            keys = {"name": "title", "category": "cat", "author": "author", "posted": "posted", "votes": "votes"}
            col, desc = self.vault_sort
            k = keys.get(col, col)
            cat, q = self.v_cat.get(), self.v_q.get().lower().strip()
            rows = [i for i in self.vault_items
                    if (cat in ("", "(All)") or i["cat"] == cat)
                    and (not q or q in f'{i["title"]} {i["author"]} {i["desc"]} {i["cat"]}'.lower())]
            rows.sort(key=lambda i: i[k].lower() if isinstance(i[k], str) else i[k], reverse=desc)
            self.vault_shown = rows[:3000]
            self.tv_vault.delete(*self.tv_vault.get_children())
            for n, i in enumerate(self.vault_shown):
                self.tv_vault.insert("", "end", iid=str(n),
                                     values=(i["title"], i["cat"], i["author"], i["posted"], i["votes"]))
            self.v_lbl.configure(text=f"{len(rows)} results" if self.vault_items else "")

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
            self._vtext(f'{it["title"]}\n{it["author"]} Â· {it["cat"]} Â· {it["posted"]}\n\n{it["desc"]}\n\nLoading details...')
            if it["url"] in self.vault_info:
                return self._vault_details(it, self.vault_info[it["url"]])
            
            def ok(info):
                self.vault_info[it["url"]] = info
                if self.vault_sel is it:
                    self._vault_details(it, info)
            
            self.bg(lambda: parse_project(http_get(it["url"]), it["url"]), lambda i: ok(i),
                    lambda e: (self._vtext(f'{it["title"]}\n\n{it["desc"]}\n\n(Could not load details: {e})')))

        def _vault_details(self, it, info):
            lines = [info["title"] or it["title"], ""]
            lines += [f"{k}: {v}" for k, v in info["fields"].items()]
            lines += ["", info["desc"] or it["desc"]]
            if not info["files"]:
                lines += ["", "(No direct download link available. Use 'Open Page'.)"]
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
                try:  # JPG/WebP: only if Pillow is installed
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
                return messagebox.showinfo("Vault", "Select a mod from the list.")
            if not info or not info["files"]:
                return messagebox.showinfo("Vault", "This project has no direct download. Use 'Open Page'.")
            sel = self.lb_vfiles.curselection()
            f = info["files"][sel[0] if sel else 0]
            name = re.sub(r'[\\/:*?"<>|]+', "-", info["title"] or it["title"]).strip()[:80]
            self.log(f"Downloading {f['name']} ...")
            tmp = Path(tempfile.mkdtemp(prefix="nwnmods-"))
            
            def done(path):
                n = self.guard(self.lib.install, path, name, self.v_lower.get(), self.ask_conflict, it["url"])
                shutil.rmtree(tmp, ignore_errors=True)
                if n:
                    self.log(f"âœ” Installed from Vault: {n}")
                    self.refresh_all()
            
            def fail(e):
                shutil.rmtree(tmp, ignore_errors=True)
                self.log(f"âœ˜ Download failed: {e}")
                messagebox.showerror("Download Failed", str(e))
            
            self.bg(lambda: download(f["url"], tmp, it["url"], f["name"]), lambda p: done(p), lambda e: fail(e))

        # ---- perfiles y copias
        def prof_name(self):
            s = self.lb_prof.curselection()
            return self.lb_prof.get(s[0]).rsplit("  (", 1)[0] if s else None

        def prof_save(self):
            if self._need_lib():
                n = simpledialog.askstring("Profile", "Profile name:")
                if n:
                    self.lib.save_profile(n)
                    self.refresh_profiles()
        
        def prof_apply(self):
            n = self.prof_name()
            if n:
                self.guard(self.lib.apply_profile, n, self.ask_conflict)
                self.log(f"Profile applied: {n}")
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
                    self.log(f"âœ” Backup created: {p.name}")
                    self.refresh_backups()
        
        def bak_restore(self):
            s = self.lb_bak.curselection()
            if s and messagebox.askyesno("Restore", "This will overwrite existing saves with the same name. Continue?"):
                self.guard(self.lib.restore, self.lib.base / "backups" / self.lb_bak.get(s[0]))
                self.log("âœ” Backup restored")

    cfg = load_cfg()
    if cfg.get("basic_mode"):
        # Minimal launcher window
        root = tk.Tk()
        _show_splash(root, cfg)
        root.title("NitRewritten - Basic")
        root.geometry("320x180")
        ttk.Label(root, text="NitRewritten", font=("Segoe UI", 14, "bold")).pack(pady=10)
        frm = ttk.Frame(root, padding=10)
        frm.pack(fill="both", expand=True)
        ttk.Button(frm, text="Install mod...", command=lambda: (run_gui(None), root.destroy())).pack(fill="x", pady=4)
        ttk.Button(frm, text="Launch Game", command=lambda: None).pack(fill="x", pady=4)
        ttk.Button(frm, text="Exit", command=root.destroy).pack(fill="x", pady=4)
        ttk.Button(frm, text="Advanced >>", command=lambda: (save_cfg({**load_cfg(), "basic_mode": False}), root.destroy(), run_gui(None))).pack(fill="x", pady=4)
        root.mainloop()
    else:
        try:
            setup_7zip()
        except Exception:
            pass
        app = App()
        _show_splash(app, cfg)
        app.mainloop()
