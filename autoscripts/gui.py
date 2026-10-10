"""Tkinter dashboard for the AutoScripts filesystem scanner.

It only displays metadata/source previews; it never imports discovered scripts.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import tkinter as tk
import tokenize
from tkinter import filedialog, messagebox, ttk

if __name__ == "__main__" and not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import autoscripts

_PREFS_PATH = Path.home() / ".autoscripts_monitor.json"


class AutoScriptsMonitor(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("AutoScripts 1.3 | Module & Import Explorer")
        self.geometry("1260x820")
        self.minsize(920, 590)
        self._closing = False
        self._seen_generation = -1
        self._render_generation = 0
        self._filter_after: str | None = None
        self._entries: list[dict[str, str]] = []
        self._messages: queue.SimpleQueue[tuple[str, str | None]] = queue.SimpleQueue()
        self._configuration_busy = False
        self._roots_dirty = False
        self._last_paths: tuple[str, ...] = ()
        self._sort_column = "module"
        self._sort_reverse = False

        self.search_var = tk.StringVar()
        self.show_var = tk.StringVar(value="All")
        self.registered_var = tk.StringVar(value="0")
        self.scanned_var = tk.StringVar(value="0")
        self.skipped_var = tk.StringVar(value="0")
        self.loaded_var = tk.StringVar(value="0")
        self.imported_var = tk.StringVar(value="0")
        self.import_search_var = tk.StringVar()
        self.import_summary_var = tk.StringVar(value="No import analysis yet")
        self.analyze_imports_var = tk.BooleanVar(value=True)
        self.max_source_kb_var = tk.StringVar(value="1953")
        self.last_var = tk.StringVar(value="Last scan: not yet")
        self.state_var = tk.StringVar(value="Starting background scanner...")
        self.progress_var = tk.StringVar(value="Preparing first scan")
        self.visible_var = tk.StringVar(value="0 scripts")
        self.detail_var = tk.StringVar(value="Select a script for more information.")
        self.change_var = tk.StringVar(value="No changes recorded yet")
        self.interval_var = tk.StringVar(value="10")
        self.limit_var = tk.StringVar(value="15000")
        self.recursive_var = tk.BooleanVar(value=True)

        self._build_ui()
        self.search_var.trace_add("write", self._schedule_filter)
        self.show_var.trace_add("write", self._schedule_filter)
        self.import_search_var.trace_add("write", self._schedule_import_filter)
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.bind("<F5>", lambda _ev: self._scan_now())
        self.bind("<Control-f>", self._focus_search)
        self.bind("<Control-e>", lambda _ev: self._export_report())
        self.after(100, self._load_saved_settings)
        self.after(150, self._poll)

    def _build_ui(self) -> None:
        style = ttk.Style(self)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure("Page.TFrame", background="#f2f5fa")
        style.configure("Header.TLabel", background="#f2f5fa", foreground="#152941",
                        font=("Segoe UI", 21, "bold"))
        style.configure("Sub.TLabel", background="#f2f5fa", foreground="#52647a",
                        font=("Segoe UI", 10))
        style.configure("Count.TLabel", background="white", foreground="#11365a",
                        font=("Segoe UI", 26, "bold"))
        style.configure("CountLabel.TLabel", background="white", foreground="#536779",
                        font=("Segoe UI", 9))
        style.configure("Card.TFrame", background="white", borderwidth=1, relief="solid")
        style.configure("Treeview", rowheight=27, font=("Segoe UI", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.configure("TNotebook.Tab", padding=(15, 8))

        page = ttk.Frame(self, padding=(20, 16), style="Page.TFrame")
        page.pack(fill="both", expand=True)
        ttk.Label(page, text="AutoScripts module explorer", style="Header.TLabel").pack(anchor="w")
        ttk.Label(page, text="Automatic discovery  |  Lazy imports  |  Live filesystem monitoring",
                  style="Sub.TLabel").pack(anchor="w", pady=(1, 15))

        counts = ttk.Frame(page, style="Page.TFrame")
        counts.pack(fill="x", pady=(0, 13))
        for column, (caption, value) in enumerate((
            ("REGISTERED MODULES", self.registered_var),
            ("PYTHON FILES EXAMINED", self.scanned_var),
            ("SKIPPED / CONFLICTS", self.skipped_var),
            ("LOADED MODULES", self.loaded_var),
            ("IMPORTED MODULES", self.imported_var),
        )):
            counts.columnconfigure(column, weight=1, uniform="cards")
            card = ttk.Frame(counts, padding=(17, 13), style="Card.TFrame")
            card.grid(row=0, column=column, sticky="nsew", padx=(0, 9) if column < 4 else 0)
            ttk.Label(card, textvariable=value, style="Count.TLabel").pack(anchor="w")
            ttk.Label(card, text=caption, style="CountLabel.TLabel").pack(anchor="w")

        tools = ttk.Frame(page, style="Page.TFrame")
        tools.pack(fill="x", pady=(0, 11))
        self.scan_button = ttk.Button(tools, text="Scan now  (F5)", command=self._scan_now)
        self.scan_button.pack(side="left")
        self.pause_button = ttk.Button(tools, text="Pause watcher", command=self._toggle_pause)
        self.pause_button.pack(side="left", padx=8)
        ttk.Button(tools, text="Export report...", command=self._export_report).pack(side="left")
        ttk.Button(tools, text="Add folder...", command=self._add_folder).pack(side="left", padx=(8, 0))
        ttk.Label(tools, textvariable=self.last_var, style="Sub.TLabel").pack(side="right")

        self.notebook = ttk.Notebook(page)
        self.notebook.pack(fill="both", expand=True)
        self._build_scripts_tab()
        self._build_imports_tab()
        self._build_activity_tab()
        self._build_settings_tab()

        footer = ttk.Frame(page, style="Page.TFrame")
        footer.pack(fill="x", pady=(11, 0))
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=100)
        self.progress.pack(side="left", padx=(0, 9))
        ttk.Label(footer, textvariable=self.progress_var, style="Sub.TLabel").pack(side="left")
        ttk.Label(page, textvariable=self.state_var, style="Sub.TLabel",
                  wraplength=1150).pack(anchor="w", pady=(6, 0))
        self._progress_running = False

    def _build_scripts_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="Scripts")
        filters = ttk.Frame(tab)
        filters.pack(fill="x", pady=(0, 8))
        ttk.Label(filters, text="Discovered Python scripts", font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Label(filters, textvariable=self.visible_var).pack(side="left", padx=12)
        ttk.Label(filters, text="Status:").pack(side="right", padx=(12, 5))
        ttk.Combobox(filters, state="readonly", width=13, textvariable=self.show_var,
                     values=("All", "Registered", "Skipped")).pack(side="right")
        self.search = ttk.Entry(filters, textvariable=self.search_var, width=38)
        self.search.pack(side="right", padx=(0, 12))
        ttk.Label(filters, text="Search:").pack(side="right", padx=(0, 5))

        grid = ttk.Frame(tab)
        grid.pack(fill="both", expand=True)
        grid.rowconfigure(0, weight=1)
        grid.columnconfigure(0, weight=1)
        self.table = ttk.Treeview(grid, show="headings", selectmode="browse",
                                  columns=("result", "module", "filename", "size", "modified", "path"))
        fields = (("result", "Status", 110), ("module", "Submodule", 290),
                  ("filename", "File", 180), ("size", "Size", 83),
                  ("modified", "Last changed", 152), ("path", "Directory", 340))
        for field, title, width in fields:
            self.table.heading(field, text=title, command=lambda field=field: self._sort_by(field))
            self.table.column(field, width=width, minwidth=65, stretch=field == "path")
        self.table.tag_configure("registered", foreground="#1c5b46")
        self.table.tag_configure("skipped", foreground="#9b6126")
        self.table.grid(row=0, column=0, sticky="nsew")
        ybar = ttk.Scrollbar(grid, orient="vertical", command=self.table.yview)
        xbar = ttk.Scrollbar(grid, orient="horizontal", command=self.table.xview)
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        self.table.bind("<<TreeviewSelect>>", self._select)
        self.table.bind("<Double-1>", self._copy_import)
        self.table.bind("<Button-3>", self._context_menu)

        actions = ttk.Frame(tab)
        actions.pack(fill="x", pady=(10, 0))
        ttk.Button(actions, text="Copy import", command=self._copy_import).pack(side="left")
        ttk.Button(actions, text="Copy path", command=self._copy_path).pack(side="left", padx=7)
        ttk.Button(actions, text="Preview source", command=self._preview_source).pack(side="left")
        ttk.Button(actions, text="Show in folder", command=self._open_folder).pack(side="left", padx=7)
        self.detail_label = ttk.Label(tab, textvariable=self.detail_var, wraplength=1120)
        self.detail_label.pack(anchor="w", pady=(9, 0))
        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Copy import", command=self._copy_import)
        self.menu.add_command(label="Copy path", command=self._copy_path)
        self.menu.add_command(label="Preview source", command=self._preview_source)
        self.menu.add_command(label="Show in folder", command=self._open_folder)

    def _build_imports_tab(self) -> None:
        """Static dependency explorer; never imports the referenced packages."""
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="Imported modules")
        head = ttk.Frame(tab)
        head.pack(fill="x", pady=(0, 8))
        ttk.Label(head, text="Imports declared inside discovered scripts",
                  font=("Segoe UI", 11, "bold")).pack(side="left")
        ttk.Label(head, textvariable=self.import_summary_var).pack(side="left", padx=12)
        ttk.Entry(head, textvariable=self.import_search_var, width=34).pack(side="right")
        ttk.Label(head, text="Filter:").pack(side="right", padx=(0, 6))
        box = ttk.Frame(tab)
        box.pack(fill="both", expand=True)
        box.rowconfigure(0, weight=1)
        box.columnconfigure(0, weight=1)
        self.import_table = ttk.Treeview(box, show="headings", selectmode="browse",
                                         columns=("source", "module", "item", "kind", "category", "local", "line"))
        for key, label, width in (
            ("source", "Source script", 240), ("module", "Imported module", 225),
            ("item", "Member / potential child", 200), ("kind", "Syntax", 70),
            ("category", "Category", 150), ("local", "Local module", 215),
            ("line", "Line", 54),
        ):
            self.import_table.heading(key, text=label)
            self.import_table.column(key, width=width, minwidth=45,
                                     stretch=key in {"source", "module", "item"})
        self.import_table.grid(row=0, column=0, sticky="nsew")
        ybar = ttk.Scrollbar(box, orient="vertical", command=self.import_table.yview)
        xbar = ttk.Scrollbar(box, orient="horizontal", command=self.import_table.xview)
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        self.import_table.configure(yscrollcommand=ybar.set, xscrollcommand=xbar.set)
        ttk.Label(tab, text="Discovered by AST inspection only; third-party modules are not executed or installed. "
                  "A 'from package import name' may refer to an attribute, not a submodule.",
                  wraplength=1110).pack(anchor="w", pady=(10, 0))
        self._all_import_entries: list[dict[str, object]] = []
        self._import_render_generation = 0
        self._import_filter_after: str | None = None

    def _schedule_import_filter(self, *_args: object) -> None:
        if self._import_filter_after is not None:
            self.after_cancel(self._import_filter_after)
        self._import_filter_after = self.after(180, self._draw_imports)

    def _draw_imports(self) -> None:
        self._import_filter_after = None
        self._import_render_generation += 1
        generation = self._import_render_generation
        self.import_table.delete(*self.import_table.get_children())
        search = self.import_search_var.get().strip().casefold()
        entries = [entry for entry in self._all_import_entries
                   if not search or any(search in str(value).casefold() for value in entry.values())]
        max_rows = 7000  # Avoid freezing Tk for very large codebases.
        self.import_summary_var.set(
            f"{len(entries):,} matching / {len(self._all_import_entries):,} import occurrences"
            + (f" (showing first {max_rows:,})" if len(entries) > max_rows else "")
        )

        def batch(start: int = 0) -> None:
            if self._closing or generation != self._import_render_generation:
                return
            for entry in entries[start:min(start + 250, max_rows)]:
                member = str(entry["member"])
                candidate = str(entry["submodule_candidate"])
                # A from-import is not necessarily a real submodule.
                item = member if not candidate or str(entry["local_target"]) == candidate else (member + " (?)")
                self.import_table.insert("", "end", values=(
                    entry["source"], entry["module"], item,
                    entry["kind"], entry["category"],
                    entry["local_target"] or "—", entry["line"],
                ))
            if start + 250 < min(len(entries), max_rows):
                self.after(1, batch, start + 250)
        batch()

    def _build_activity_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=10)
        self.notebook.add(tab, text="Recent changes")
        ttk.Label(tab, textvariable=self.change_var, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 8))
        wrapper = ttk.Frame(tab)
        wrapper.pack(fill="both", expand=True)
        wrapper.rowconfigure(0, weight=1)
        wrapper.columnconfigure(0, weight=1)
        self.activity = ttk.Treeview(wrapper, columns=("when", "action", "module", "path"), show="headings")
        for field, label, width in (("when", "Time", 170), ("action", "Change", 110),
                                    ("module", "Submodule", 265), ("path", "Source file", 580)):
            self.activity.heading(field, text=label)
            self.activity.column(field, width=width, minwidth=85, stretch=field == "path")
        self.activity.grid(row=0, column=0, sticky="nsew")
        ybar = ttk.Scrollbar(wrapper, orient="vertical", command=self.activity.yview)
        ybar.grid(row=0, column=1, sticky="ns")
        self.activity.configure(yscrollcommand=ybar.set)
        ttk.Label(tab, text="Scanner errors and naming conflicts").pack(anchor="w", pady=(12, 5))
        self.diagnostics = tk.Text(tab, height=6, font=("Consolas", 9), wrap="word",
                                   background="#f8fafc", state="disabled")
        self.diagnostics.pack(fill="x")

    def _build_settings_tab(self) -> None:
        tab = ttk.Frame(self.notebook, padding=20)
        self.notebook.add(tab, text="Settings")
        ttk.Label(tab, text="Folders to scan", font=("Segoe UI", 13, "bold")).pack(anchor="w")
        ttk.Label(tab, text="Folders are searched in order. The first matching name wins.").pack(anchor="w", pady=(2, 9))
        list_frame = ttk.Frame(tab)
        list_frame.pack(fill="x")
        self.roots_list = tk.Listbox(list_frame, height=6, exportselection=False, font=("Consolas", 10))
        self.roots_list.pack(side="left", fill="x", expand=True)
        root_controls = ttk.Frame(list_frame)
        root_controls.pack(side="left", fill="y", padx=(10, 0))
        ttk.Button(root_controls, text="Add folder", command=self._add_folder).pack(fill="x")
        ttk.Button(root_controls, text="Remove selected", command=self._remove_folder).pack(fill="x", pady=7)
        ttk.Button(root_controls, text="Use current folder", command=self._use_current).pack(fill="x")
        ttk.Separator(tab).pack(fill="x", pady=19)
        ttk.Label(tab, text="Scan options", font=("Segoe UI", 13, "bold")).pack(anchor="w", pady=(0, 10))
        row = ttk.Frame(tab)
        row.pack(anchor="w")
        ttk.Label(row, text="Interval (seconds)", width=24).grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(row, textvariable=self.interval_var, width=16).grid(row=0, column=1, sticky="w")
        ttk.Label(row, text="Maximum .py files", width=24).grid(row=1, column=0, sticky="w", pady=5)
        ttk.Entry(row, textvariable=self.limit_var, width=16).grid(row=1, column=1, sticky="w")
        ttk.Checkbutton(tab, text="Include nested directories", variable=self.recursive_var).pack(anchor="w", pady=(8, 7))
        ttk.Checkbutton(tab, text="Statically analyze imports inside scripts",
                        variable=self.analyze_imports_var).pack(anchor="w", pady=(0, 8))
        ttk.Label(row, text="Maximum source size (KB)", width=24).grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(row, textvariable=self.max_source_kb_var, width=16).grid(row=2, column=1, sticky="w")
        ttk.Button(tab, text="Apply settings", command=self._apply_settings).pack(anchor="w")
        ttk.Label(tab, text="Settings are saved locally for the GUI. Importing the library does not read these preferences.",
                  wraplength=740).pack(anchor="w", pady=(15, 0))

    def _focus_search(self, _event: tk.Event) -> None:
        self.notebook.select(0)
        self.search.focus_set()
        self.search.selection_range(0, "end")

    def _poll(self) -> None:
        if self._closing:
            return
        while True:
            try:
                kind, message = self._messages.get_nowait()
            except queue.Empty:
                break
            if kind == "configuration":
                self._configuration_busy = False
                if message:
                    messagebox.showerror("AutoScripts settings", message, parent=self)
                else:
                    self._roots_dirty = False
                    self._seen_generation = -1
                    self._save_settings()
            elif kind == "export":
                if message and message.startswith("ERROR:"):
                    messagebox.showerror("Report export", message[6:], parent=self)
                elif message:
                    self.state_var.set(f"Report saved: {message}")

        info = autoscripts.status()
        self.registered_var.set(f"{info['count']:,}")
        self.scanned_var.set(f"{info['scanned_count']:,}")
        self.skipped_var.set(f"{info['skipped_count']:,}")
        self.loaded_var.set(f"{info['loaded_count']:,}")
        self.imported_var.set(f"{info['imported_module_count']:,}")
        paths = tuple(info["paths"])
        if paths != self._last_paths and not self._roots_dirty:
            self._set_roots(paths)
            self.interval_var.set(f"{info['interval']:g}")
            self.limit_var.set(str(info["max_files"]))
            self.recursive_var.set(info["recursive"])
            self.analyze_imports_var.set(info["analyze_imports"])
            self.max_source_kb_var.set(str(info["max_source_bytes"] // 1024))
        self._last_paths = paths
        self.pause_button.configure(text="Resume watcher" if info["paused"] else "Pause watcher")
        if info["last_scan_epoch"]:
            scan_time = datetime.fromtimestamp(info["last_scan_epoch"]).strftime("%Y-%m-%d  %H:%M:%S")
            self.last_var.set(f"Last scan: {scan_time}  ({info['last_duration_seconds']:.2f}s)")
        scanning = info["scanning"]
        if scanning and not self._progress_running:
            self.progress.start(12)
            self._progress_running = True
        elif not scanning and self._progress_running:
            self.progress.stop()
            self._progress_running = False
        self.progress_var.set(
            f"Scanning {info['progress_files']:,} files...  {info['current_directory']}" if scanning
            else f"Ready  |  {info['count']:,} submodules available"
        )
        if info["last_error"]:
            state = "Scan error: " + info["last_error"]
        elif self._configuration_busy:
            state = "Applying settings..."
        elif info["paused"]:
            state = "Periodic scanning paused. Scan now (F5) still works."
        else:
            state = f"Watcher active  |  every {info['interval']:g}s  |  {len(info['paths'])} folder(s)  |  double-click a script to copy its import"
        self.state_var.set(state)
        if info["scan_generation"] != self._seen_generation:
            self._seen_generation = info["scan_generation"]
            self._entries = autoscripts.scan_results()
            self._draw_table()
            self._all_import_entries = [entry for group in autoscripts.dependency_graph().values()
                                        for entry in group]
            self._draw_imports()
            self._draw_activity(info)
        self.after(350, self._poll)

    def _set_roots(self, roots: tuple[str, ...]) -> None:
        self.roots_list.delete(0, "end")
        for root in roots:
            self.roots_list.insert("end", root)

    def _schedule_filter(self, *_args: object) -> None:
        if self._filter_after is not None:
            self.after_cancel(self._filter_after)
        self._filter_after = self.after(180, self._draw_table)

    def _sort_by(self, col: str) -> None:
        if self._sort_column == col:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = col
            self._sort_reverse = False
        self._draw_table()

    @staticmethod
    def _time_from_ns(value: str) -> str:
        try:
            return datetime.fromtimestamp(int(value) / 1e9).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OverflowError, OSError):
            return "—"

    def _draw_table(self) -> None:
        self._filter_after = None
        self._render_generation += 1
        generation = self._render_generation
        self.table.delete(*self.table.get_children())
        query = self.search_var.get().strip().casefold()
        kind = self.show_var.get()
        matching = [i for i, entry in enumerate(self._entries)
                    if (kind == "All" or entry["result"] == kind)
                    and (not query or any(query in str(x).casefold() for x in entry.values()))]

        def sort_key(i: int):
            entry = self._entries[i]
            if self._sort_column == "filename":
                value = Path(entry["path"]).name
            elif self._sort_column == "size":
                return int(entry["size"] or -1), i
            elif self._sort_column == "modified":
                return int(entry["modified"] or -1), i
            else:
                value = entry["path"] if self._sort_column == "path" else entry.get(self._sort_column, "")
            return value.casefold(), i

        matching.sort(key=sort_key, reverse=self._sort_reverse)
        self.visible_var.set(f"{len(matching):,} of {len(self._entries):,}")

        def render_batch(start: int = 0) -> None:
            if self._closing or generation != self._render_generation:
                return
            for index in matching[start:start + 220]:
                entry = self._entries[index]
                src = Path(entry["path"])
                size = f"{int(entry['size'])/1024:,.1f} KB" if entry["size"] else "—"
                self.table.insert("", "end", iid=str(index),
                                  tags=("registered" if entry["result"] == "Registered" else "skipped",),
                                  values=(entry["result"],
                                          "autoscripts." + entry["module"] if entry["module"] else "—",
                                          src.name, size, self._time_from_ns(entry["modified"]),
                                          str(src.parent)))
            if start + 220 < len(matching):
                self.after(1, render_batch, start + 220)
        render_batch()

    def _draw_activity(self, info: dict) -> None:
        self.activity.delete(*self.activity.get_children())
        changes = info["changes"]
        self.change_var.set(f"Last scan: +{changes['added']} added    ~{changes['modified']} modified    -{changes['removed']} removed    |    Recent 500 events retained")
        for event in autoscripts.change_history():
            when = datetime.fromtimestamp(float(event["timestamp"])).strftime("%Y-%m-%d %H:%M:%S")
            self.activity.insert("", "end", values=(when, event["action"],
                                                    "autoscripts." + event["module"], event["path"]))
        problems = list(info["errors"]) + ["Name conflict: " + x for x in info["collisions"]]
        problems += [f"Import parsing ({name}): {error}" for name, error in info["import_diagnostics"].items()]
        if info["last_error"]:
            problems.insert(0, info["last_error"])
        self.diagnostics.configure(state="normal")
        self.diagnostics.delete("1.0", "end")
        self.diagnostics.insert("end", "\n".join(problems) if problems else "No scanner errors or naming conflicts.")
        self.diagnostics.configure(state="disabled")

    def _selected_entry(self) -> dict[str, str] | None:
        selected = self.table.selection()
        if not selected:
            return None
        try:
            return self._entries[int(selected[0])]
        except (ValueError, IndexError):
            return None

    def _select(self, _event=None) -> None:
        entry = self._selected_entry()
        if entry:
            note = entry["reason"]
            if entry["result"] == "Registered":
                module = autoscripts.module_info(entry["module"])
                if module and module["stale"]:
                    note += "  |  WARNING: imported module still points to a previous source file"
                elif module and module["loaded"]:
                    note += "  |  Loaded in memory"
            self.detail_var.set(f"{entry['result']}: {entry['path']}   |   {note}")

    def _copy(self, value: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(value)
        self.detail_var.set("Copied: " + value)

    def _copy_import(self, _event=None) -> None:
        entry = self._selected_entry()
        if entry and entry["result"] == "Registered":
            self._copy("import autoscripts." + entry["module"])

    def _copy_path(self) -> None:
        entry = self._selected_entry()
        if entry:
            self._copy(entry["path"])

    def _context_menu(self, event: tk.Event) -> None:
        row = self.table.identify_row(event.y)
        if row:
            self.table.selection_set(row)
            self._select()
            try:
                self.menu.tk_popup(event.x_root, event.y_root)
            finally:
                self.menu.grab_release()

    def _preview_source(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            return
        path = Path(entry["path"])
        try:
            with tokenize.open(path) as fh:
                content = fh.read(65000)
                if fh.read(1):
                    content += "\n\n... preview truncated at 65,000 characters ..."
        except (OSError, UnicodeError, SyntaxError) as exc:
            messagebox.showerror("Source preview", str(exc), parent=self)
            return
        window = tk.Toplevel(self)
        window.title("Source preview (read only) | " + path.name)
        window.geometry("930x640")
        frame = ttk.Frame(window)
        frame.pack(fill="both", expand=True, padx=8, pady=8)
        text = tk.Text(frame, wrap="none", font=("Consolas", 10), undo=False)
        text.pack(side="left", fill="both", expand=True)
        ybar = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        ybar.pack(side="right", fill="y")
        text.configure(yscrollcommand=ybar.set)
        text.insert("1.0", content)
        text.configure(state="disabled")

    def _open_folder(self) -> None:
        entry = self._selected_entry()
        if not entry:
            return
        path = Path(entry["path"])
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", "/select,", str(path)])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path.parent)])
        except (OSError, ValueError) as exc:
            messagebox.showerror("Open folder", str(exc), parent=self)

    def _scan_now(self) -> None:
        try:
            autoscripts.request_refresh()
        except RuntimeError as exc:
            messagebox.showerror("Scan now", str(exc), parent=self)

    def _toggle_pause(self) -> None:
        try:
            if autoscripts.status()["paused"]:
                autoscripts.resume()
            else:
                autoscripts.pause()
        except RuntimeError as exc:
            messagebox.showerror("Watcher", str(exc), parent=self)

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(parent=self, title="Add Python scan directory")
        if not folder:
            return
        folders = list(self.roots_list.get(0, "end"))
        if folder not in folders:
            self.roots_list.insert("end", folder)
            self._roots_dirty = True
            self._apply_settings()

    def _remove_folder(self) -> None:
        selected = self.roots_list.curselection()
        if not selected:
            return
        if self.roots_list.size() < 2:
            messagebox.showwarning("AutoScripts", "At least one scan folder is required.", parent=self)
            return
        self.roots_list.delete(selected[0])
        self._roots_dirty = True

    def _use_current(self) -> None:
        self._set_roots((str(Path.cwd()),))
        self._roots_dirty = True

    def _apply_settings(self) -> None:
        if self._configuration_busy:
            return
        try:
            roots = list(self.roots_list.get(0, "end"))
            if not roots:
                raise ValueError("Add at least one folder")
            interval = float(self.interval_var.get())
            limit = int(self.limit_var.get())
            max_kb = int(self.max_source_kb_var.get())
            if not 1 <= max_kb <= 102400:
                raise ValueError("Maximum source size must be 1 to 102400 KB")
            if not 0.5 <= interval <= 86400:
                raise ValueError("The scan interval must be between 0.5 and 86400 seconds")
            if not 1 <= limit <= 1000000:
                raise ValueError("The file limit must be between 1 and 1,000,000")
            for root in roots:
                if not Path(root).is_dir():
                    raise NotADirectoryError(root)
        except (ValueError, OSError) as exc:
            messagebox.showerror("Invalid settings", str(exc), parent=self)
            return
        self._configuration_busy = True
        kwargs = {"paths": roots, "interval": interval, "max_files": limit,
                  "recursive": self.recursive_var.get(),
                  "analyze_imports": self.analyze_imports_var.get(),
                  "max_source_bytes": max_kb * 1024}

        def configure_worker() -> None:
            error = None
            try:
                autoscripts.configure(**kwargs)
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            self._messages.put(("configuration", error))

        threading.Thread(target=configure_worker, name="autoscripts-gui-configure", daemon=True).start()

    def _load_saved_settings(self) -> None:
        try:
            data = json.loads(_PREFS_PATH.read_text(encoding="utf-8"))
            roots = [p for p in data.get("paths", []) if isinstance(p, str) and Path(p).is_dir()]
            if roots:
                self._set_roots(tuple(roots))
            self.interval_var.set(str(data.get("interval", "10")))
            self.limit_var.set(str(data.get("max_files", "15000")))
            self.recursive_var.set(bool(data.get("recursive", True)))
            self.analyze_imports_var.set(bool(data.get("analyze_imports", True)))
            self.max_source_kb_var.set(str(data.get("max_source_bytes", 2000000) // 1024))
            if roots:
                self._roots_dirty = True
                self._apply_settings()
        except (OSError, ValueError, TypeError):
            pass

    def _save_settings(self) -> None:
        try:
            info = autoscripts.status()
            data = {"paths": info["paths"], "interval": info["interval"],
                    "max_files": info["max_files"], "recursive": info["recursive"],
                    "analyze_imports": info["analyze_imports"],
                    "max_source_bytes": info["max_source_bytes"]}
            _PREFS_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            # Read-only home directory: GUI continues functioning without saving.
            pass

    def _export_report(self) -> None:
        destination = filedialog.asksaveasfilename(
            parent=self, title="Export scan report", defaultextension=".json",
            filetypes=[("JSON report", "*.json"), ("CSV spreadsheet", "*.csv")])
        if not destination:
            return

        def save_worker() -> None:
            try:
                result = autoscripts.export_report(destination)
                self._messages.put(("export", str(result)))
            except Exception as exc:
                self._messages.put(("export", "ERROR:" + str(exc)))

        threading.Thread(target=save_worker, name="autoscripts-export", daemon=True).start()

    def _close(self) -> None:
        self._closing = True
        self._render_generation += 1
        self.destroy()


def main() -> None:
    try:
        window = AutoScriptsMonitor()
    except tk.TclError as exc:
        raise SystemExit(f"AutoScripts GUI requires a graphical desktop: {exc}") from exc
    window.mainloop()


if __name__ == "__main__":
    main()
