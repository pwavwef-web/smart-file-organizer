from __future__ import annotations

import os
import queue
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from . import notifier
from .config import Settings, load_settings
from .models import PlannedMove
from .organizer import Organizer


@dataclass(frozen=True)
class PreviewResult:
    settings: Settings
    files: list[Path]
    moves: list[PlannedMove]
    skipped_in_place: int
    duplicates: int
    gemini_error: str | None


@dataclass(frozen=True)
class ApplyResult:
    settings: Settings
    batch_id: str
    moves: list[PlannedMove]


def parse_extensions(value: str) -> set[str]:
    result: set[str] = set()
    for item in value.split(","):
        cleaned = item.strip().casefold()
        if cleaned:
            result.add(cleaned if cleaned.startswith(".") else f".{cleaned}")
    return result


def parse_optional_int(value: str, *, minimum: int = 0) -> int | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    number = int(cleaned)
    if number < minimum:
        raise ValueError(f"Must be {minimum} or higher")
    return number


def move_action_label(move: PlannedMove, *, applied: bool) -> str:
    if move.note == "duplicate skipped":
        return "Skipped" if applied else "Skip duplicate"
    return "Moved" if applied else "Move"


def format_confidence(value: float) -> str:
    return f"{value:.0%}"


def category_summary(moves: Iterable[PlannedMove]) -> str:
    counts = Counter(move.classification.category for move in moves if move.note != "duplicate skipped")
    if not counts:
        return "No movable files"
    return ", ".join(f"{name}: {count}" for name, count in counts.most_common(4))


class SmartSorterApp:
    def __init__(self, root: tk.Tk, config_path: Path) -> None:
        self.root = root
        self.root.title("Smart File Organizer")
        self.root.minsize(980, 640)

        self.config_path = config_path
        self.settings: Settings | None = None
        self.moves_by_iid: dict[str, PlannedMove] = {}
        self.preview_moves: list[PlannedMove] = []
        self.worker: threading.Thread | None = None
        self.queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.success_handler: Callable[[object], None] | None = None

        self.config_var = tk.StringVar(value=str(config_path))
        self.folder_var = tk.StringVar()
        self.extensions_var = tk.StringVar()
        self.categories_var = tk.StringVar()
        self.limit_var = tk.StringVar()
        self.min_age_var = tk.StringVar(value="0")
        self.recursive_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="Loading config...")
        self.summary_var = tk.StringVar(value="")
        self.gemini_var = tk.StringVar(value="")

        self._build_styles()
        self._build_layout()
        self._load_config_from_entry(show_errors=True, reset_controls=True)

    def _build_styles(self) -> None:
        style = ttk.Style(self.root)
        if "clam" in style.theme_names():
            style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10))
        style.configure("Header.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Muted.TLabel", foreground="#596170")
        style.configure("Status.TLabel", foreground="#31545c")
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"))
        style.configure("Treeview", rowheight=28)
        style.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)

        header = ttk.Frame(self.root, padding=(18, 14, 18, 8))
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="Smart File Organizer", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, textvariable=self.status_var, style="Status.TLabel").grid(row=0, column=1, sticky="e")

        controls = ttk.Frame(self.root, padding=(18, 6, 18, 12))
        controls.grid(row=1, column=0, sticky="ew")
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(4, weight=1)

        ttk.Label(controls, text="Config").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(controls, textvariable=self.config_var).grid(row=0, column=1, columnspan=3, sticky="ew", pady=4)
        ttk.Button(controls, text="Browse", command=self._choose_config).grid(row=0, column=4, sticky="ew", padx=(8, 0))
        ttk.Button(
            controls,
            text="Reload",
            command=lambda: self._load_config_from_entry(show_errors=True, reset_controls=True),
        ).grid(row=0, column=5, sticky="ew", padx=(8, 0))

        ttk.Label(controls, text="Folder").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        self.folder_box = ttk.Combobox(controls, textvariable=self.folder_var, values=(), state="normal")
        self.folder_box.grid(row=1, column=1, columnspan=3, sticky="ew", pady=4)
        ttk.Button(controls, text="Browse", command=self._choose_folder).grid(row=1, column=4, sticky="ew", padx=(8, 0))
        ttk.Checkbutton(controls, text="Recursive", variable=self.recursive_var).grid(
            row=1, column=5, sticky="w", padx=(8, 0)
        )

        ttk.Label(controls, text="Extensions").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(controls, textvariable=self.extensions_var).grid(row=2, column=1, sticky="ew", pady=4)
        ttk.Label(controls, text="Categories").grid(row=2, column=2, sticky="w", padx=(12, 8), pady=4)
        ttk.Entry(controls, textvariable=self.categories_var).grid(row=2, column=3, sticky="ew", pady=4)
        ttk.Label(controls, text="Limit").grid(row=2, column=4, sticky="e", padx=(8, 8), pady=4)
        ttk.Entry(controls, textvariable=self.limit_var, width=8).grid(row=2, column=5, sticky="ew", pady=4)

        ttk.Label(controls, text="Min age").grid(row=3, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(controls, textvariable=self.min_age_var, width=10).grid(row=3, column=1, sticky="w", pady=4)
        ttk.Label(controls, text="seconds", style="Muted.TLabel").grid(row=3, column=1, sticky="w", padx=(86, 0))
        self.preview_button = ttk.Button(
            controls, text="Preview", style="Primary.TButton", command=self.preview
        )
        self.preview_button.grid(row=3, column=3, sticky="ew", padx=(12, 0), pady=4)
        self.apply_button = ttk.Button(controls, text="Move selected", command=self.apply_selected, state="disabled")
        self.apply_button.grid(row=3, column=4, sticky="ew", padx=(8, 0), pady=4)
        self.undo_button = ttk.Button(controls, text="Undo last batch", command=self.undo_last_batch)
        self.undo_button.grid(row=3, column=5, sticky="ew", padx=(8, 0), pady=4)

        panes = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        panes.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 12))

        moves_frame = ttk.Frame(panes)
        moves_frame.columnconfigure(0, weight=1)
        moves_frame.rowconfigure(1, weight=1)
        panes.add(moves_frame, weight=4)

        toolbar = ttk.Frame(moves_frame)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        toolbar.columnconfigure(0, weight=1)
        ttk.Label(toolbar, textvariable=self.summary_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(toolbar, text="Select all", command=self._select_all).grid(row=0, column=1, padx=(8, 0))
        ttk.Button(toolbar, text="Clear", command=lambda: self.tree.selection_remove(self.tree.selection())).grid(
            row=0, column=2, padx=(8, 0)
        )

        columns = ("action", "file", "category", "confidence", "destination", "classifier", "reason", "note")
        self.tree = ttk.Treeview(moves_frame, columns=columns, show="headings", selectmode="extended")
        self.tree.grid(row=1, column=0, sticky="nsew")
        widths = {
            "action": 96,
            "file": 180,
            "category": 120,
            "confidence": 88,
            "destination": 280,
            "classifier": 120,
            "reason": 240,
            "note": 140,
        }
        headings = {
            "action": "Action",
            "file": "File",
            "category": "Category",
            "confidence": "Confidence",
            "destination": "Destination",
            "classifier": "Classifier",
            "reason": "Reason",
            "note": "Note",
        }
        for column in columns:
            self.tree.heading(column, text=headings[column])
            anchor = "center" if column in {"action", "confidence"} else "w"
            self.tree.column(column, width=widths[column], minwidth=70, anchor=anchor)

        yscroll = ttk.Scrollbar(moves_frame, orient=tk.VERTICAL, command=self.tree.yview)
        xscroll = ttk.Scrollbar(moves_frame, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        yscroll.grid(row=1, column=1, sticky="ns")
        xscroll.grid(row=2, column=0, sticky="ew")

        side = ttk.Frame(panes)
        side.columnconfigure(0, weight=1)
        side.rowconfigure(1, weight=1)
        panes.add(side, weight=1)

        ttk.Label(side, text="History", font=("Segoe UI", 12, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.history = ttk.Treeview(side, columns=("time", "count", "categories"), show="headings", height=12)
        self.history.heading("time", text="Time")
        self.history.heading("count", text="Files")
        self.history.heading("categories", text="Categories")
        self.history.column("time", width=145, minwidth=120)
        self.history.column("count", width=52, minwidth=48, anchor="center")
        self.history.column("categories", width=210, minwidth=140)
        self.history.grid(row=1, column=0, sticky="nsew")
        history_scroll = ttk.Scrollbar(side, orient=tk.VERTICAL, command=self.history.yview)
        self.history.configure(yscrollcommand=history_scroll.set)
        history_scroll.grid(row=1, column=1, sticky="ns")
        ttk.Button(side, text="Refresh", command=self.refresh_history).grid(row=2, column=0, sticky="ew", pady=(8, 12))

        roots = ttk.LabelFrame(side, text="Roots", padding=8)
        roots.grid(row=3, column=0, sticky="nsew")
        roots.columnconfigure(0, weight=1)
        self.roots_text = tk.Text(roots, height=9, wrap="none", borderwidth=0, relief="flat")
        self.roots_text.grid(row=0, column=0, sticky="nsew")
        self.roots_text.configure(state="disabled")

        footer = ttk.Frame(self.root, padding=(18, 0, 18, 14))
        footer.grid(row=3, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.gemini_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="Open config", command=self._open_config).grid(row=0, column=1, sticky="e")

    def _choose_config(self) -> None:
        selected = filedialog.askopenfilename(
            title="Choose Smart File Organizer config",
            filetypes=(("JSON config", "*.json"), ("All files", "*.*")),
            initialdir=str(self.config_path.parent),
        )
        if selected:
            self.config_var.set(selected)
            self._load_config_from_entry(show_errors=True, reset_controls=True)

    def _choose_folder(self) -> None:
        initial = str(Path(self.folder_var.get()).expanduser()) if self.folder_var.get().strip() else str(Path.home())
        selected = filedialog.askdirectory(title="Choose folder to scan", initialdir=initial)
        if selected:
            self.folder_var.set(selected)

    def _open_config(self) -> None:
        path = Path(self.config_var.get()).expanduser()
        if path.exists():
            os.startfile(path)

    def _load_config_from_entry(self, *, show_errors: bool, reset_controls: bool = False) -> bool:
        try:
            path = Path(self.config_var.get()).expanduser().resolve()
            settings = load_settings(path)
        except (OSError, ValueError, KeyError) as exc:
            self.status_var.set("Config error")
            if show_errors:
                messagebox.showerror("Config error", str(exc))
            return False

        self.config_path = path
        self.settings = settings
        inboxes = [str(folder) for folder in settings.inbox_folders]
        self.folder_box.configure(values=[""] + inboxes)
        if not self.folder_var.get() and inboxes:
            self.folder_var.set("")
        if reset_controls:
            self.recursive_var.set(settings.recursive)
        self.status_var.set(f"{len(settings.inbox_folders)} inbox(es) ready")
        self.gemini_var.set(self._gemini_state(settings))
        self._set_roots(settings)
        self.refresh_history()
        return True

    def _set_roots(self, settings: Settings) -> None:
        lines = [
            f"{name}: {path}"
            for name, path in sorted(settings.locations.items())
            if name in {"Desktop", "Documents", "Downloads", "Home", "Library", "Music", "Pictures", "Videos"}
        ]
        self.roots_text.configure(state="normal")
        self.roots_text.delete("1.0", tk.END)
        self.roots_text.insert("1.0", "\n".join(lines))
        self.roots_text.configure(state="disabled")

    @staticmethod
    def _gemini_state(settings: Settings) -> str:
        if not settings.gemini.enabled:
            return "Gemini: disabled"
        if os.environ.get("GEMINI_API_KEY"):
            return f"Gemini: ready ({settings.gemini.model})"
        return f"Gemini: enabled, key missing ({settings.gemini.model})"

    def _selected_folder(self) -> str | None:
        value = self.folder_var.get().strip()
        return value or None

    def _categories(self) -> set[str]:
        return {item.strip().casefold() for item in self.categories_var.get().split(",") if item.strip()}

    def _validate_inputs(self) -> tuple[set[str], int | None, int] | None:
        try:
            extensions = parse_extensions(self.extensions_var.get())
            limit = parse_optional_int(self.limit_var.get(), minimum=1)
            minimum_age = parse_optional_int(self.min_age_var.get(), minimum=0)
        except ValueError as exc:
            messagebox.showerror("Input error", str(exc))
            return None
        return extensions, limit, minimum_age or 0

    def preview(self) -> None:
        if not self._load_config_from_entry(show_errors=True):
            return
        parsed = self._validate_inputs()
        if parsed is None:
            return
        extensions, limit, minimum_age = parsed
        selected_folder = self._selected_folder()
        recursive = self.recursive_var.get()
        categories = self._categories()

        def work() -> PreviewResult:
            settings = load_settings(self.config_path)
            organizer = Organizer(settings)
            folders = [Path(selected_folder).expanduser().resolve()] if selected_folder else list(settings.inbox_folders)
            files: list[Path] = []
            for folder in folders:
                files.extend(organizer.discover(folder, recursive=recursive, minimum_age=minimum_age))
            if extensions:
                files = [path for path in files if path.suffix.casefold() in extensions]
            if limit is not None:
                files = files[:limit]
            moves = organizer.plan(files)
            if categories:
                moves = [move for move in moves if move.classification.category.casefold() in categories]
            return PreviewResult(
                settings=settings,
                files=files,
                moves=moves,
                skipped_in_place=organizer.last_skipped_in_place,
                duplicates=organizer.last_duplicates,
                gemini_error=organizer.classifier.last_gemini_error,
            )

        self._start_task("Scanning...", work, self._show_preview)

    def apply_selected(self) -> None:
        selected = list(self.tree.selection())
        moves = [self.moves_by_iid[iid] for iid in selected if iid in self.moves_by_iid]
        if not moves:
            messagebox.showinfo("Move selected", "Select at least one preview row first.")
            return
        if not messagebox.askyesno("Move selected", f"Move {len(moves)} selected file(s)?"):
            return

        def work() -> ApplyResult:
            settings = load_settings(self.config_path)
            organizer = Organizer(settings)
            batch_id, results = organizer.apply(moves)
            self._notify_moves(results, settings)
            return ApplyResult(settings=settings, batch_id=batch_id, moves=results)

        self._start_task("Moving selected files...", work, self._show_apply)

    def undo_last_batch(self) -> None:
        if not messagebox.askyesno("Undo last batch", "Restore the most recent move batch?"):
            return

        def work() -> tuple[str | None, int, list[str]]:
            settings = load_settings(self.config_path)
            organizer = Organizer(settings)
            return organizer.undo_last_batch()

        self._start_task("Restoring last batch...", work, self._show_undo)

    def refresh_history(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        try:
            settings = load_settings(Path(self.config_var.get()).expanduser())
            rows = Organizer(settings).history(limit=12)
        except (OSError, ValueError, KeyError):
            return
        for iid in self.history.get_children():
            self.history.delete(iid)
        for index, row in enumerate(rows):
            categories = row.get("categories", {})
            if isinstance(categories, dict):
                category_text = ", ".join(
                    f"{name}: {count}" for name, count in sorted(categories.items())
                )
            else:
                category_text = ""
            self.history.insert(
                "",
                tk.END,
                iid=f"history-{index}",
                values=(row.get("timestamp", ""), row.get("count", ""), category_text),
            )

    def _notify_moves(self, moves: list[PlannedMove], settings: Settings) -> None:
        if not settings.notifications.enabled:
            return
        moved = [move for move in moves if move.note != "duplicate skipped"]
        if not moved:
            return
        home = Path.home()
        limit = settings.notifications.max_files
        if len(moved) == 1:
            item = moved[0]
            title = "Smart Sorter"
            message = f"Moved {item.source.name}\n-> {notifier.friendly_folder(item.destination.parent, home)}"
        else:
            lines = [
                f"{item.source.name} -> {notifier.friendly_folder(item.destination.parent, home)}"
                for item in moved[:limit]
            ]
            if len(moved) > limit:
                lines.append(f"...and {len(moved) - limit} more")
            title = f"Smart Sorter - {len(moved)} files sorted"
            message = "\n".join(lines)
        notifier.notify(title, message, open_folder=moved[0].destination.parent)

    def _start_task(self, label: str, work: Callable[[], object], on_success: Callable[[object], None]) -> None:
        if self.worker and self.worker.is_alive():
            return
        self.status_var.set(label)
        self.success_handler = on_success
        self._set_busy(True)

        def runner() -> None:
            try:
                self.queue.put(("ok", work()))
            except Exception as exc:  # The UI thread will show the specific error.
                self.queue.put(("error", exc))

        self.worker = threading.Thread(target=runner, daemon=True)
        self.worker.start()
        self.root.after(100, self._drain_queue)

    def _drain_queue(self) -> None:
        try:
            state, payload = self.queue.get_nowait()
        except queue.Empty:
            if self.worker and self.worker.is_alive():
                self.root.after(100, self._drain_queue)
            return

        self._set_busy(False)
        if state == "error":
            self.status_var.set("Ready")
            messagebox.showerror("Smart File Organizer", str(payload))
            return
        if self.success_handler:
            self.success_handler(payload)
        self.success_handler = None

    def _set_busy(self, busy: bool) -> None:
        state = "disabled" if busy else "normal"
        for button in (self.preview_button, self.undo_button):
            button.configure(state=state)
        if busy:
            self.apply_button.configure(state="disabled")
        else:
            self.apply_button.configure(state="normal" if self.preview_moves else "disabled")

    def _show_preview(self, payload: object) -> None:
        result = payload if isinstance(payload, PreviewResult) else None
        if result is None:
            return
        self.settings = result.settings
        self.preview_moves = result.moves
        self._populate_moves(result.moves, applied=False)
        self._select_all()
        extras = []
        if result.skipped_in_place:
            extras.append(f"{result.skipped_in_place} already in place")
        if result.duplicates:
            extras.append(f"{result.duplicates} duplicate(s)")
        extra_text = f" ({', '.join(extras)})" if extras else ""
        self.status_var.set(f"Previewed {len(result.moves)} action(s) from {len(result.files)} file(s){extra_text}")
        self.summary_var.set(category_summary(result.moves))
        if result.gemini_error:
            self.gemini_var.set(f"Gemini fallback used: {result.gemini_error}")
        else:
            self.gemini_var.set(self._gemini_state(result.settings))
        self.apply_button.configure(state="normal" if result.moves else "disabled")

    def _show_apply(self, payload: object) -> None:
        result = payload if isinstance(payload, ApplyResult) else None
        if result is None:
            return
        self.settings = result.settings
        self.preview_moves = []
        self._populate_moves(result.moves, applied=True)
        moved = sum(1 for move in result.moves if move.note != "duplicate skipped")
        self.status_var.set(f"Applied batch {result.batch_id}: {moved} file(s) moved")
        self.summary_var.set(category_summary(result.moves))
        self.apply_button.configure(state="disabled")
        self.refresh_history()

    def _show_undo(self, payload: object) -> None:
        batch_id, restored, warnings = payload  # type: ignore[misc]
        if batch_id is None:
            self.status_var.set("Nothing to undo")
            messagebox.showinfo("Undo last batch", "Nothing is available to undo.")
            return
        self.status_var.set(f"Restored {restored} file(s) from batch {batch_id}")
        if warnings:
            messagebox.showwarning("Undo completed with warnings", "\n".join(warnings))
        self.refresh_history()

    def _populate_moves(self, moves: list[PlannedMove], *, applied: bool) -> None:
        self.moves_by_iid.clear()
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for index, move in enumerate(moves):
            iid = f"move-{index}"
            self.moves_by_iid[iid] = move
            self.tree.insert(
                "",
                tk.END,
                iid=iid,
                values=(
                    move_action_label(move, applied=applied),
                    move.source.name,
                    move.classification.category,
                    format_confidence(move.classification.confidence),
                    str(move.destination),
                    move.classification.source,
                    move.classification.reason,
                    move.note,
                ),
            )

    def _select_all(self) -> None:
        children = self.tree.get_children()
        if children:
            self.tree.selection_set(children)


def run(config_path: Path) -> int:
    root = tk.Tk()
    SmartSorterApp(root, config_path)
    root.mainloop()
    return 0
