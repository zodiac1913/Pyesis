from __future__ import annotations

from datetime import datetime, timedelta
from difflib import SequenceMatcher
from html import escape
import hashlib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
import webbrowser
import ctypes

import markdown

from pyesis.ai_summary import build_summary
from pyesis.config import AppConfig, EntryRecord, RepoConfig, dedupe_entries, load_config, save_config
from pyesis.diff_buffer import find_item, mark_as_shown, purge_old_daily_buffers, remember_diff
from pyesis.document_formatter import export_docx, render_plain_text
from pyesis.git_monitor import capture_snapshot, validate_repo


DIFF_EXCERPT_LIMIT = 12_000
NEAR_DUP_DIFF_SIMILARITY_THRESHOLD = 0.80
WINDOWS_APP_ID = "rxjr.pyesis.app"
PYESIS_GITHUB_URL = "https://github.com/cms-enterprise/Pyesis"


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str) -> None:
        self.widget = widget
        self.text = text
        self.tip_window: tk.Toplevel | None = None
        self.widget.bind("<Enter>", self._show)
        self.widget.bind("<Leave>", self._hide)
        self.widget.bind("<FocusIn>", self._show)
        self.widget.bind("<FocusOut>", self._hide)

    def _show(self, _event: tk.Event) -> None:
        if self.tip_window is not None:
            return
        x = self.widget.winfo_rootx() + 16
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self.tip_window = tk.Toplevel(self.widget)
        self.tip_window.wm_overrideredirect(True)
        self.tip_window.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            self.tip_window,
            text=self.text,
            background="#111111",
            foreground="#ffffff",
            relief="solid",
            borderwidth=1,
            padx=6,
            pady=2,
        )
        label.pack()

    def _hide(self, _event: tk.Event) -> None:
        if self.tip_window is None:
            return
        self.tip_window.destroy()
        self.tip_window = None


class PyesisApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self._icon_image: tk.PhotoImage | None = None
        self._editor_bg_image: tk.PhotoImage | None = None
        self._editor_bg_canvas: tk.Canvas | None = None
        self._set_windows_app_id()
        self.root.title("Pyesis")
        self.root.geometry("1180x760")
        self._apply_window_icon()
        self.config = load_config()
        self.status_var = tk.StringVar(value="Idle")
        self.week_end_var = tk.StringVar(value=self.config.week_end_day)
        self.theme_mode_var = tk.StringVar(value=self.config.theme_mode.capitalize())
        self.high_contrast_var = tk.BooleanVar(value=self.config.high_contrast)
        self.ui_font_size_var = tk.IntVar(value=self.config.ui_font_size)
        self.repo_path_var = tk.StringVar()
        self.repo_label_var = tk.StringVar()
        self.poll_seconds_var = tk.StringVar(value="120")
        self.repo_items: dict[str, RepoConfig] = {}
        self._buffer_day = datetime.now().strftime("%Y-%m-%d")
        purge_old_daily_buffers(7, self._buffer_day)
        self.style = ttk.Style(self.root)
        # Use a ttk theme that reliably applies custom field colors on Windows.
        self.style.theme_use("clam")

        self._build_layout()
        self._bind_shortcuts()
        self._apply_fonts()
        self._apply_theme()
        self._migrate_entries()
        self._refresh_repo_list()
        self._refresh_editor()
        self._maybe_auto_export_daily()
        self._schedule_poll()

    def _set_windows_app_id(self) -> None:
        if os.name != "nt":
            return
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
        except Exception:
            return

    def _asset_roots(self) -> list[Path]:
        roots: list[Path] = []
        if getattr(sys, "frozen", False):
            bundle_root = Path(getattr(sys, "_MEIPASS", ""))
            if bundle_root:
                roots.append(bundle_root)
        roots.append(Path.cwd())
        roots.append(Path(__file__).resolve().parent.parent)
        return roots

    def _apply_window_icon(self) -> None:
        ico_names = ("assets/pyesis.ico", "assets/Pyesis.ico")
        png_names = ("assets/Pyesis.png", "assets/pyesis.png")
        if self._try_apply_ico_icon(ico_names):
            return
        self._try_apply_png_icon(png_names)

    def _iter_existing_asset_paths(self, names: tuple[str, ...]) -> list[Path]:
        found: list[Path] = []
        for root in self._asset_roots():
            for name in names:
                path = root / name
                if path.exists():
                    found.append(path)
        return found

    def _try_apply_ico_icon(self, names: tuple[str, ...]) -> bool:
        for ico_path in self._iter_existing_asset_paths(names):
            try:
                self.root.iconbitmap(default=str(ico_path))
                return True
            except tk.TclError:
                continue
        return False

    def _try_apply_png_icon(self, names: tuple[str, ...]) -> bool:
        for png_path in self._iter_existing_asset_paths(names):
            try:
                self._icon_image = tk.PhotoImage(file=str(png_path))
                self.root.iconphoto(True, self._icon_image)
                return True
            except tk.TclError:
                continue
        return False

    def _build_layout(self) -> None:
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        sidebar = ttk.Frame(self.root, padding=12)
        sidebar.grid(row=0, column=0, sticky="nsew")
        editor_area = ttk.Frame(self.root, padding=(0, 12, 12, 12))
        editor_area.grid(row=0, column=1, sticky="nsew")
        editor_area.columnconfigure(0, weight=1)
        editor_area.rowconfigure(1, weight=1)

        header = ttk.Frame(sidebar)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="Observed Repositories").grid(row=0, column=0, sticky="w")
        self.repo_list = tk.Listbox(sidebar, height=12, width=38)
        self.repo_list.grid(row=1, column=0, sticky="nsew", pady=(6, 12))
        sidebar.rowconfigure(1, weight=1)

        ttk.Button(sidebar, text="Browse Repo", underline=0, command=self._browse_repo).grid(row=2, column=0, sticky="ew")
        ttk.Label(sidebar, text="Repository path").grid(row=3, column=0, sticky="w", pady=(8, 2))
        ttk.Entry(sidebar, textvariable=self.repo_path_var).grid(row=4, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(sidebar, text="Display label (optional)").grid(row=5, column=0, sticky="w", pady=(0, 2))
        ttk.Entry(sidebar, textvariable=self.repo_label_var).grid(row=6, column=0, sticky="ew", pady=(0, 6))
        ttk.Label(sidebar, text="Poll seconds").grid(row=7, column=0, sticky="w", pady=(0, 2))
        poll_row = ttk.Frame(sidebar)
        poll_row.grid(row=8, column=0, sticky="ew", pady=(0, 12))
        poll_row.columnconfigure(0, weight=0)
        poll_row.columnconfigure(1, weight=1)
        ttk.Entry(poll_row, textvariable=self.poll_seconds_var, width=7).grid(row=0, column=0, sticky="w")
        ttk.Button(poll_row, text="Refresh", command=self.run_poll_once).grid(row=0, column=1, sticky="e", padx=(8, 0))
        ttk.Button(sidebar, text="Add Repo", underline=0, command=self._add_repo).grid(row=9, column=0, sticky="ew")
        ttk.Button(sidebar, text="Remove Selected", underline=0, command=self._remove_selected_repo).grid(row=10, column=0, sticky="ew", pady=(6, 12))

        ttk.Label(sidebar, text="Week end day").grid(row=11, column=0, sticky="w")
        ttk.Combobox(
            sidebar,
            textvariable=self.week_end_var,
            values=["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
            state="readonly",
        ).grid(row=12, column=0, sticky="ew", pady=(6, 12))

        ttk.Label(sidebar, text="Theme").grid(row=13, column=0, sticky="w")
        theme_box = ttk.Combobox(
            sidebar,
            textvariable=self.theme_mode_var,
            values=["System", "Light", "Dark"],
            state="readonly",
        )
        theme_box.grid(row=14, column=0, sticky="ew", pady=(6, 12))
        theme_box.bind("<<ComboboxSelected>>", self._on_theme_changed)

        ttk.Button(sidebar, text="Export DOCX", underline=0, command=self._export_docx).grid(row=15, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(sidebar, textvariable=self.status_var, wraplength=260).grid(row=16, column=0, sticky="ew", pady=(16, 0))

        editor_header = ttk.Frame(editor_area)
        editor_header.grid(row=0, column=0, sticky="ew")
        editor_header.columnconfigure(0, weight=1)

        ttk.Label(editor_header, text="Weekly Work Log Preview").grid(row=0, column=0, sticky="w")

        github_button = ttk.Button(editor_header, text="🐙 GitHub", underline=2, width=10, command=self._open_github_repo)
        github_button.grid(row=0, column=1, sticky="e", padx=(0, 6))

        info_button = ttk.Button(editor_header, text="ⓘ Info", underline=2, width=8, command=self._open_readme_view)
        info_button.grid(row=0, column=2, sticky="e", padx=(0, 6))

        settings_button = ttk.Button(editor_header, text="⚙ Settings", underline=2, width=11, command=self._open_settings)
        settings_button.grid(row=0, column=3, sticky="e")

        ToolTip(github_button, "Open GitHub Repository (Ctrl+Shift+G)")
        ToolTip(info_button, "Open README (F1)")
        ToolTip(settings_button, "Settings (Ctrl+,)")

        preview_shell = ttk.Frame(editor_area)
        preview_shell.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        preview_shell.rowconfigure(0, weight=1)
        preview_shell.columnconfigure(0, weight=1)

        self._editor_bg_canvas = tk.Canvas(
            preview_shell,
            borderwidth=0,
            highlightthickness=0,
        )
        self._editor_bg_canvas.grid(row=0, column=0, sticky="nsew")

        self.editor = tk.Text(
            preview_shell,
            wrap="word",
            font=("Consolas", 11),
            tabs=(36,),
            borderwidth=0,
            highlightthickness=0,
            relief="flat",
        )
        self.editor.grid(row=0, column=0, sticky="nsew")
        self._setup_editor_background()

    def _setup_editor_background(self) -> None:
        watermark_names = ("assets/Pyesis-watermark.png", "assets/pyesis-watermark.png")
        for path in self._iter_existing_asset_paths(watermark_names):
            try:
                self._editor_bg_image = tk.PhotoImage(file=str(path))
                break
            except tk.TclError:
                continue

        if self._editor_bg_image is None or self._editor_bg_canvas is None:
            return

        self._editor_bg_canvas.create_image(0, 0, anchor="nw", image=self._editor_bg_image, tags=("watermark",))
        self._editor_bg_canvas.lower("watermark")
        self._editor_bg_canvas.bind("<Button-1>", lambda _e: self.editor.focus_set())
        self._editor_bg_canvas.bind("<MouseWheel>", self._forward_editor_scroll)

    def _forward_editor_scroll(self, event: tk.Event) -> str:
        if event.delta:
            self.editor.yview_scroll(-1 * int(event.delta / 120), "units")
        return "break"

    def _bind_shortcuts(self) -> None:
        self.root.bind_all("<Control-comma>", self._on_shortcut_settings)
        self.root.bind_all("<F1>", self._on_shortcut_readme)
        self.root.bind_all("<Control-Shift-G>", self._on_shortcut_github)
        self.root.bind_all("<Alt-b>", lambda _e: self._browse_repo())
        self.root.bind_all("<Alt-a>", lambda _e: self._add_repo())
        self.root.bind_all("<Alt-r>", lambda _e: self._remove_selected_repo())
        self.root.bind_all("<Alt-c>", lambda _e: self.run_poll_once())
        self.root.bind_all("<Alt-e>", lambda _e: self._export_docx())
        self.root.bind_all("<Alt-s>", lambda _e: self._open_settings())
        self.root.bind_all("<Alt-i>", lambda _e: self._open_readme_view())
        self.root.bind_all("<Alt-g>", lambda _e: self._open_github_repo())

    def _on_shortcut_settings(self, _event: tk.Event) -> str:
        self._open_settings()
        return "break"

    def _on_shortcut_readme(self, _event: tk.Event) -> str:
        self._open_readme_view()
        return "break"

    def _on_shortcut_github(self, _event: tk.Event) -> str:
        self._open_github_repo()
        return "break"

    def _browse_repo(self) -> None:
        selected = filedialog.askdirectory(title="Select repository")
        if selected:
            self.repo_path_var.set(selected)
            self.repo_label_var.set(Path(selected).name)

    def _add_repo(self) -> None:
        path = self.repo_path_var.get().strip()
        label = self.repo_label_var.get().strip() or Path(path).name
        try:
            poll_seconds = max(5, int(self.poll_seconds_var.get().strip() or "120"))
        except ValueError:
            messagebox.showerror("Invalid poll", "Poll seconds must be a number.")
            return

        ok, message = validate_repo(path)
        if not ok:
            messagebox.showerror("Invalid repository", message)
            return

        if any(repo.path == path for repo in self.config.repos):
            messagebox.showinfo("Already added", "This repository is already being monitored.")
            return

        self.config.repos.append(RepoConfig(path=path, label=label, poll_seconds=poll_seconds))
        self._persist()
        self._refresh_repo_list()
        self.status_var.set(f"Added {label}")

    def _remove_selected_repo(self) -> None:
        selection = self.repo_list.curselection()
        if not selection:
            return
        selected_label = self.repo_list.get(selection[0])
        repo = self.repo_items.get(selected_label)
        if repo is None:
            return
        self.config.repos = [item for item in self.config.repos if item.path != repo.path]
        self._persist()
        self._refresh_repo_list()
        self.status_var.set(f"Removed {repo.label}")

    def _refresh_repo_list(self) -> None:
        self.repo_items = {}
        self.repo_list.delete(0, tk.END)
        for repo in self.config.repos:
            label = f"{repo.label} ({repo.poll_seconds}s)"
            self.repo_items[label] = repo
            self.repo_list.insert(tk.END, label)

    def _migrate_entries(self) -> None:
        original_len = len(self.config.entries)
        filtered = self._remove_noise_entries(self.config.entries)
        deduped = dedupe_entries(filtered)
        rewritten = self._rewrite_legacy_summaries(deduped)

        if len(rewritten) != original_len or any(a.summary != b.summary for a, b in zip(deduped, rewritten)):
            self.config.entries = rewritten
            save_config(self.config)

    def _remove_noise_entries(self, entries: list[EntryRecord]) -> list[EntryRecord]:
        cleaned: list[EntryRecord] = []
        for entry in entries:
            summary_l = entry.summary.lower()
            excerpt_l = entry.diff_excerpt.lower()
            if "pyesis_state.json" in summary_l or "pyesis_state.json" in excerpt_l:
                continue
            cleaned.append(entry)
        return cleaned

    def _should_merge_entries(self, previous: EntryRecord, current: EntryRecord) -> bool:
        if previous.repo_path != current.repo_path:
            return False
        if previous.day_name != current.day_name or previous.week_start_iso != current.week_start_iso:
            return False

        try:
            prev_dt = datetime.fromisoformat(previous.created_at)
            cur_dt = datetime.fromisoformat(current.created_at)
        except ValueError:
            return False

        return (cur_dt - prev_dt) <= timedelta(minutes=20)

    def _is_duplicate_entry(self, existing: EntryRecord, candidate: EntryRecord) -> bool:
        same_repo = existing.repo_path == candidate.repo_path
        if not same_repo:
            return False

        if existing.diff_hash and candidate.diff_hash:
            return existing.diff_hash == candidate.diff_hash

        same_scope = (
            existing.week_start_iso == candidate.week_start_iso
            and existing.day_name == candidate.day_name
        )
        if not same_scope:
            return False

        return (
            existing.summary.strip() == candidate.summary.strip()
            and existing.diff_excerpt.strip() == candidate.diff_excerpt.strip()
        )

    def _current_day_key(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _ensure_active_buffer_day(self) -> None:
        today = self._current_day_key()
        if self._buffer_day == today:
            return
        self._buffer_day = today
        purge_old_daily_buffers(7, today)

    def _rewrite_legacy_summaries(self, entries: list[EntryRecord]) -> list[EntryRecord]:
        rewritten: list[EntryRecord] = []
        for entry in entries:
            if self._needs_summary_refresh(entry) and entry.diff_excerpt.strip():
                refreshed = build_summary(entry.repo_label, entry.diff_excerpt).text
                rewritten.append(
                    EntryRecord(
                        repo_label=entry.repo_label,
                        repo_path=entry.repo_path,
                        created_at=entry.created_at,
                        day_name=entry.day_name,
                        week_start_iso=entry.week_start_iso,
                        summary=refreshed,
                        diff_hash=entry.diff_hash,
                        diff_excerpt=entry.diff_excerpt,
                    )
                )
            else:
                rewritten.append(entry)
        return rewritten

    def _needs_summary_refresh(self, entry: EntryRecord) -> bool:
        text = entry.summary.lower()
        return (
            text.startswith("who:")
            or text.startswith("what:")
            or text.startswith("where:")
            or text.startswith("why:")
            or
            text.startswith("i worked on ")
            or
            text.startswith("i modified ")
            or (text.startswith("i changed ") and "adding " in text and "removing " in text)
            or text.startswith("implemented modified ")
            or (" across " in text and " additions" in text and " removals" in text)
            or text.startswith("i expanded configuration handling in ")
            or text.startswith("i ")
            or "\n" not in entry.summary
            or " with " in text and " additions" in text
        )

    def _persist(self) -> None:
        self.config.week_end_day = self.week_end_var.get()
        self.config.theme_mode = self.theme_mode_var.get().lower()
        self.config.high_contrast = self.high_contrast_var.get()
        self.config.ui_font_size = max(10, min(20, int(self.ui_font_size_var.get())))
        save_config(self.config)
        self._refresh_editor()

    def _open_settings(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("Settings")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        frame = ttk.Frame(dialog, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")

        time_var = tk.StringVar(value=self.config.auto_export_time)
        high_contrast_var = tk.BooleanVar(value=self.config.high_contrast)
        font_size_var = tk.IntVar(value=self.config.ui_font_size)

        ttk.Label(frame, text="Daily DOCX export time (24h HH:MM)").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=time_var, width=12).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(frame, text="Leave blank to disable daily auto-export.").grid(row=2, column=0, sticky="w", pady=(6, 12))

        ttk.Label(frame, text="Accessibility").grid(row=3, column=0, sticky="w")
        ttk.Checkbutton(frame, text="High contrast mode", variable=high_contrast_var).grid(row=4, column=0, sticky="w", pady=(6, 0))
        ttk.Label(frame, text="UI font size").grid(row=5, column=0, sticky="w", pady=(8, 2))
        ttk.Spinbox(frame, from_=10, to=20, textvariable=font_size_var, width=6).grid(row=6, column=0, sticky="w")
        ttk.Label(frame, text="Tip: Keyboard shortcuts include Alt+B/A/R/C/E/S/I/G").grid(row=7, column=0, sticky="w", pady=(6, 12))

        controls = ttk.Frame(frame)
        controls.grid(row=8, column=0, sticky="e")

        def save_and_close() -> None:
            raw_time = time_var.get().strip()
            if raw_time:
                try:
                    self.config.auto_export_time = self._normalize_time(raw_time)
                except ValueError:
                    messagebox.showerror("Invalid time", "Use 24-hour HH:MM format, for example 17:30.")
                    return
            else:
                self.config.auto_export_time = ""
            self.high_contrast_var.set(high_contrast_var.get())
            self.ui_font_size_var.set(max(10, min(20, int(font_size_var.get()))))
            self._apply_fonts()
            self._apply_theme()
            self._persist()
            state = self.config.auto_export_time or "disabled"
            self.status_var.set(f"Auto-export time set to {state}")
            dialog.destroy()

        ttk.Button(controls, text="Cancel", command=dialog.destroy).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(controls, text="Save", command=save_and_close).grid(row=0, column=1)

    def _open_readme_view(self) -> None:
        readme_path = Path("README.md")
        if not readme_path.exists():
            messagebox.showerror("README missing", "README.md was not found in the workspace root.")
            return

        markdown_text = readme_path.read_text(encoding="utf-8")
        html_body = markdown.markdown(markdown_text, extensions=["fenced_code", "tables"])
        html = (
            "<!doctype html><html><head><meta charset='utf-8'>"
            "<title>Pyesis README</title>"
            "<style>"
            "body{font-family:Segoe UI,Arial,sans-serif;max-width:960px;margin:2rem auto;padding:0 1rem;line-height:1.6;}"
            "pre{background:#111827;color:#e5e7eb;padding:1rem;border-radius:8px;overflow:auto;}"
            "code{font-family:Consolas,monospace;}"
            "table{border-collapse:collapse;}th,td{border:1px solid #d1d5db;padding:.5rem;}"
            "a{color:#0d6efd;}"
            "</style></head><body>"
            f"{html_body}"
            "</body></html>"
        )

        temp_path = Path(tempfile.gettempdir()) / "pyesis_readme_preview.html"
        try:
            temp_path.write_text(html, encoding="utf-8")
        except Exception:
            fallback_html = (
                "<!doctype html><html><head><meta charset='utf-8'><title>Pyesis README</title></head>"
                f"<body><pre>{escape(markdown_text)}</pre></body></html>"
            )
            temp_path.write_text(fallback_html, encoding="utf-8")

        webbrowser.open(temp_path.as_uri())

    def _open_github_repo(self) -> None:
        url = self._resolve_repo_url()
        if not url:
            messagebox.showerror(
                "GitHub URL unavailable",
                "Could not find a repository URL from git remote origin."
                " Configure origin or set PYESIS_REPO_URL.",
            )
            return
        webbrowser.open(url)

    def _resolve_repo_url(self) -> str:
        return PYESIS_GITHUB_URL

    def _normalize_time(self, value: str) -> str:
        parts = value.split(":")
        if len(parts) != 2:
            raise ValueError("Expected HH:MM")
        try:
            hour = int(parts[0])
            minute = int(parts[1])
        except ValueError as exc:
            raise ValueError("Expected numeric HH:MM") from exc
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            raise ValueError("Out of range")
        return f"{hour:02d}:{minute:02d}"

    def _maybe_auto_export_daily(self) -> None:
        time_value = self.config.auto_export_time.strip()
        if not time_value:
            return
        try:
            scheduled = self._normalize_time(time_value)
        except ValueError:
            return

        now = datetime.now()
        scheduled_hour, scheduled_minute = (int(part) for part in scheduled.split(":"))
        if (now.hour, now.minute) < (scheduled_hour, scheduled_minute):
            return

        today = now.strftime("%Y-%m-%d")
        if self.config.last_auto_export_date == today:
            return

        self.config.week_end_day = self.week_end_var.get()
        file_name = now.strftime("%Y%b%d") + "Pyesis.docx"
        try:
            target = export_docx(self.config, Path("exports"), file_name=file_name)
        except Exception as exc:
            self.status_var.set(f"Auto-export failed: {exc}")
            return

        self.config.last_auto_export_date = today
        save_config(self.config)
        self.status_var.set(f"Auto-exported {target.name}")

    def _on_theme_changed(self, _event: tk.Event) -> None:
        self._apply_theme()
        self._persist()

    def _resolve_theme(self) -> str:
        selected = self.theme_mode_var.get().strip().lower()
        if selected in {"light", "dark"}:
            return selected
        return self._detect_system_theme()

    def _detect_system_theme(self) -> str:
        if sys.platform != "win32":
            return "light"
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return "light" if int(value) else "dark"
        except Exception:
            return "light"

    def _apply_theme(self) -> None:
        palette = self._palette_for(self._resolve_theme(), self.high_contrast_var.get())

        self.root.configure(bg=palette["bg"])
        self.style.configure(".", background=palette["bg"], foreground=palette["fg"])
        self.style.configure("TFrame", background=palette["bg"])
        self.style.configure("TLabel", background=palette["bg"], foreground=palette["fg"])
        self.style.configure("TButton", background=palette["surface"], foreground=palette["fg"])
        self.style.map(
            "TButton",
            background=[("active", palette["surface_alt"])],
            foreground=[("disabled", palette["muted_fg"])],
        )
        self.style.configure(
            "TEntry",
            fieldbackground=palette["input_bg"],
            foreground=palette["fg"],
            insertcolor=palette["fg"],
            bordercolor=palette["border"],
        )
        self.style.map(
            "TEntry",
            fieldbackground=[("disabled", palette["surface"]), ("readonly", palette["surface"])],
            foreground=[("disabled", palette["muted_fg"]), ("readonly", palette["fg"])],
        )
        self.style.configure(
            "TCombobox",
            fieldbackground=palette["input_bg"],
            background=palette["surface"],
            foreground=palette["fg"],
            arrowcolor=palette["fg"],
            bordercolor=palette["border"],
            selectbackground=palette["accent"],
            selectforeground=palette["accent_fg"],
        )
        self.style.map(
            "TCombobox",
            fieldbackground=[("readonly", palette["input_bg"]), ("disabled", palette["surface"])],
            foreground=[("readonly", palette["fg"]), ("disabled", palette["muted_fg"])],
            selectbackground=[("readonly", palette["accent"])],
            selectforeground=[("readonly", palette["accent_fg"])],
            arrowcolor=[("readonly", palette["fg"]), ("disabled", palette["muted_fg"])],
        )

        self.repo_list.configure(
            bg=palette["input_bg"],
            fg=palette["fg"],
            selectbackground=palette["accent"],
            selectforeground=palette["accent_fg"],
            highlightbackground=palette["border"],
            highlightcolor=palette["accent"],
        )
        self.editor.configure(
            bg=palette["surface"],
            fg=palette["fg"],
            insertbackground=palette["fg"],
            selectbackground=palette["accent"],
            selectforeground=palette["accent_fg"],
            highlightbackground=palette["surface"],
            highlightcolor=palette["surface"],
        )
        if self._editor_bg_canvas is not None:
            self._editor_bg_canvas.configure(bg=palette["surface"])

    def _palette_for(self, resolved_theme: str, high_contrast: bool) -> dict[str, str]:
        if high_contrast:
            return {
                "bg": "#000000",
                "surface": "#000000",
                "surface_alt": "#1a1a1a",
                "input_bg": "#000000",
                "fg": "#ffffff",
                "muted_fg": "#d7d7d7",
                "accent": "#ffff00",
                "accent_fg": "#000000",
                "border": "#ffffff",
            }
        if resolved_theme == "dark":
            return {
                "bg": "#171a1f",
                "surface": "#252b33",
                "surface_alt": "#313a45",
                "input_bg": "#1f252d",
                "fg": "#e6ebf2",
                "muted_fg": "#97a4b5",
                "accent": "#3b82f6",
                "accent_fg": "#ffffff",
                "border": "#384353",
            }
        return {
            "bg": "#f4f6f8",
            "surface": "#e6ecf2",
            "surface_alt": "#d9e2ec",
            "input_bg": "#ffffff",
            "fg": "#1d2733",
            "muted_fg": "#5c6978",
            "accent": "#0d6efd",
            "accent_fg": "#ffffff",
            "border": "#b8c4d2",
        }

    def _apply_fonts(self) -> None:
        size = max(10, min(20, int(self.ui_font_size_var.get())))
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkTooltipFont"):
            try:
                font = tkfont.nametofont(name)
                font.configure(size=size)
            except tk.TclError:
                continue
        if hasattr(self, "editor"):
            self.editor.configure(font=("Consolas", size))

    def _refresh_editor(self) -> None:
        self.editor.delete("1.0", tk.END)
        self.editor.insert("1.0", render_plain_text(self.config) if self.config.entries else "")

    def _schedule_poll(self) -> None:
        self.root.after(self._next_poll_interval_ms(), self._scheduled_poll)

    def _next_poll_interval_ms(self) -> int:
        if not self.config.repos:
            return 60_000
        seconds = min(max(5, int(repo.poll_seconds)) for repo in self.config.repos)
        return seconds * 1000

    def _scheduled_poll(self) -> None:
        self.run_poll_once()
        self._maybe_auto_export_daily()
        self._schedule_poll()

    def _try_capture_snapshot(self, repo: RepoConfig):
        try:
            return capture_snapshot(repo)
        except Exception as exc:
            self.status_var.set(f"{repo.label}: {exc}")
            return None

    def _resolve_summary_from_ledger(self, repo: RepoConfig, diff_text: str) -> tuple[str, bool]:
        existing = find_item(repo.label, diff_text, self._buffer_day)
        if existing is not None and existing["shown"]:
            return "", True

        summary_text = existing["gitDiffDescription"] if existing is not None else ""
        if not summary_text.strip():
            summary = build_summary(repo.label, diff_text)
            summary_text = summary.text
        return summary_text, False

    def _build_entry(self, repo: RepoConfig, snapshot, summary_text: str, diff_hash: str) -> EntryRecord:
        created_at = snapshot.created_at
        week_start = (created_at - timedelta(days=created_at.weekday())).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        return EntryRecord(
            repo_label=repo.label,
            repo_path=repo.path,
            created_at=created_at.isoformat(timespec="seconds"),
            day_name=created_at.strftime("%A"),
            week_start_iso=week_start.isoformat(),
            summary=summary_text,
            diff_hash=diff_hash,
            diff_excerpt=snapshot.diff_text[:DIFF_EXCERPT_LIMIT],
        )

    def _entry_day_key(self, created_at_iso: str) -> str:
        return created_at_iso[:10] if len(created_at_iso) >= 10 else ""

    def _entry_diff_fingerprint(self, entry: EntryRecord) -> str:
        diff_hash = entry.diff_hash.strip()
        if diff_hash:
            return f"hash:{diff_hash}"

        excerpt = entry.diff_excerpt.strip()
        if not excerpt:
            return ""
        return f"excerpt:{hashlib.sha256(excerpt.encode('utf-8')).hexdigest()}"

    def _entry_files_fingerprint(self, entry: EntryRecord) -> list[str]:
        excerpt_files = self._excerpt_files_fingerprint(entry.diff_excerpt)
        if excerpt_files:
            return excerpt_files
        return self._summary_files_fingerprint(entry.summary)

    def _excerpt_files_fingerprint(self, diff_excerpt: str) -> list[str]:
        files: list[str] = []
        for line in diff_excerpt.splitlines():
            if not line.startswith("+++ b/"):
                continue
            path = line.removeprefix("+++ b/").strip()
            if path == "/dev/null":
                continue
            normalized = path.lower()
            if normalized not in files:
                files.append(normalized)
        return files[:16]

    def _summary_files_fingerprint(self, summary: str) -> list[str]:
        files: list[str] = []
        for line in summary.splitlines():
            text = line.strip()
            if not text.startswith(("Updated ", "Created ", "Removed ", "Renamed ")):
                continue
            path = text.split(" ", 1)[1]
            path = re.split(r"\s+by\s+|\s+while\s+|\s+and\s+", path, maxsplit=1)[0]
            path = re.sub(r"\s*\([^)]*\)", "", path).strip(" .")
            normalized = path.lower()
            if normalized and normalized not in files:
                files.append(normalized)
        return files[:16]

    def _fingerprint_overlap(self, current: list[str], previous: list[str]) -> bool:
        if not current or not previous:
            return False
        current_set = set(current)
        previous_set = set(previous)
        intersect = current_set & previous_set
        if not intersect:
            return False
        if current_set <= previous_set or previous_set <= current_set:
            return True
        union_size = len(current_set | previous_set)
        if union_size == 0:
            return False
        return (len(intersect) / union_size) >= 0.6

    def _is_possible_duplicate_diff(self, candidate: EntryRecord) -> bool:
        candidate_fp = self._entry_diff_fingerprint(candidate)
        if not candidate_fp:
            return False

        candidate_day = self._entry_day_key(candidate.created_at)
        for existing in self.config.entries:
            if existing.repo_path != candidate.repo_path:
                continue
            if self._entry_day_key(existing.created_at) != candidate_day:
                continue
            if self._entry_diff_fingerprint(existing) == candidate_fp:
                return True
        return False

    def _is_possible_carryover_duplicate(self, candidate: EntryRecord) -> bool:
        candidate_excerpt = candidate.diff_excerpt.strip()
        candidate_files = self._entry_files_fingerprint(candidate)
        if not candidate_excerpt:
            return False

        candidate_day = self._entry_day_key(candidate.created_at)
        for existing in reversed(self.config.entries):
            if existing.repo_path != candidate.repo_path:
                continue
            if existing.week_start_iso != candidate.week_start_iso:
                continue

            existing_day = self._entry_day_key(existing.created_at)
            if not existing_day or existing_day == candidate_day:
                continue

            existing_excerpt = existing.diff_excerpt.strip()
            if not existing_excerpt:
                continue
            existing_files = self._entry_files_fingerprint(existing)
            if not self._fingerprint_overlap(candidate_files, existing_files):
                continue

            similarity = SequenceMatcher(None, candidate_excerpt, existing_excerpt).ratio()
            if similarity >= NEAR_DUP_DIFF_SIMILARITY_THRESHOLD:
                return True
        return False

    def _capture_repo_change(self, repo: RepoConfig) -> bool:
        snapshot = self._try_capture_snapshot(repo)
        if snapshot is None:
            return False

        summary_text, already_shown = self._resolve_summary_from_ledger(repo, snapshot.diff_text)
        if already_shown:
            return False

        ledger_item = remember_diff(repo.label, repo.path, snapshot.diff_text, summary_text, self._buffer_day)
        if ledger_item["shown"]:
            return False

        new_entry = self._build_entry(repo, snapshot, summary_text, ledger_item["diffHash"] or snapshot.diff_hash)
        if self._is_possible_duplicate_diff(new_entry):
            self.status_var.set(f"[POSSIBLE DUPLICATE] {repo.label}: duplicate git diff detected for this repo/day")
            mark_as_shown(repo.label, snapshot.diff_text, self._buffer_day)
            return False

        if self._is_possible_carryover_duplicate(new_entry):
            self.status_var.set(f"[POSSIBLE DUPLICATE] {repo.label}: carry-over git diff from previous day")
            mark_as_shown(repo.label, snapshot.diff_text, self._buffer_day)
            return False

        if any(self._is_duplicate_entry(entry, new_entry) for entry in self.config.entries):
            self.status_var.set(f"[POSSIBLE DUPLICATE] {repo.label}: duplicate git diff detected")
            mark_as_shown(repo.label, snapshot.diff_text, self._buffer_day)
            return False

        self.config.entries.append(new_entry)
        mark_as_shown(repo.label, snapshot.diff_text, self._buffer_day)
        return True

    def run_poll_once(self) -> None:
        self.config.week_end_day = self.week_end_var.get()
        self._ensure_active_buffer_day()
        captured = 0
        for repo in self.config.repos:
            if self._capture_repo_change(repo):
                captured += 1

        if captured:
            self._persist()
            self.status_var.set(f"Captured {captured} new change summary{'ies' if captured != 1 else ''} at {datetime.now().strftime('%H:%M:%S')}")
        else:
            self.status_var.set(f"No new diffs at {datetime.now().strftime('%H:%M:%S')}")

    def _export_docx(self) -> None:
        self.config.week_end_day = self.week_end_var.get()
        self._persist()
        target = export_docx(self.config, Path("exports"))
        self.status_var.set(f"Exported {target.name}")
        messagebox.showinfo("Export complete", f"Saved {target}")


def launch() -> None:
    root = tk.Tk()
    app = PyesisApp(root)
    root.minsize(980, 640)
    app.status_var.set("Ready")
    root.mainloop()


def main() -> None:
    launch()