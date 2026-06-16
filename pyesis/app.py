from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from html import escape
import json
import hashlib
import shutil
import tomllib
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
from urllib import error as urlerror, parse as urlparse, request as urlrequest
import webbrowser
import ctypes

import markdown

from pyesis.ai_summary import (
    AI_PROVIDER_LABELS,
    AISummaryResult,
    GITHUB_GPT_MODE,
    HEURISTIC_MODE,
    OLLAMA_MODE,
    OPENAI_COMPATIBLE_MODE,
    build_summary,
)
from pyesis.config import (
    AppConfig,
    EntryRecord,
    GITHUB_GPT_DEFAULT_MODELS,
    RepoConfig,
    dedupe_entries,
    default_export_directory,
    load_config,
    save_config,
)
from pyesis.diff_buffer import find_item, mark_as_shown, purge_old_daily_buffers, remember_diff
from pyesis.document_formatter import export_docx, render_plain_text
from pyesis.github_auth import (
    GITHUB_DOTCOM_AUTH_MODE,
    GITHUB_ENTERPRISE_AUTH_MODE,
    GitHubDeviceLogin,
    GitHubAuthStatus,
    GitHubUserIdentity,
    clear_github_auth_token,
    describe_github_auth,
    fetch_github_user_identity,
    load_github_auth_token,
    normalize_github_auth_endpoint,
    normalize_github_auth_mode,
    poll_github_device_login_token,
    start_github_device_login,
    store_github_auth_token,
)
from pyesis.git_monitor import DiffSnapshot, capture_snapshot, split_diff_by_file, validate_repo
from pyesis.summary_enhancer import run_periodic_enhancer


DIFF_EXCERPT_LIMIT = 12_000
NEAR_DUP_DIFF_SIMILARITY_THRESHOLD = 0.80
WINDOWS_APP_ID = "rxjr.pyesis.app"
PYESIS_GITHUB_URL = "https://github.com/cms-enterprise/Pyesis"
MOUSEWHEEL_EVENT = "<MouseWheel>"
VSCODE_OLLAMA_AUTOCODER_MODEL_SETTING = "ollama-autocoder.model"
DEFAULT_OLLAMA_SUMMARY_MODEL = "qwen3-coder:30b"


def _ollama_tags_url(base_url: str) -> str:
    text = base_url.strip()
    if not text:
        text = "http://localhost:11434/api/chat"

    parsed = urlparse.urlsplit(text)
    scheme = parsed.scheme or "http"
    netloc = parsed.netloc or parsed.path
    path = parsed.path if parsed.netloc else ""
    clean_path = path.rstrip("/")

    if not clean_path or clean_path == "/":
        tags_path = "/api/tags"
    elif clean_path.startswith("/api/"):
        segments = [segment for segment in clean_path.split("/") if segment]
        segments[-1] = "tags"
        tags_path = "/" + "/".join(segments)
    else:
        tags_path = "/api/tags"

    return urlparse.urlunsplit((scheme, netloc, tags_path, "", ""))


@dataclass
class SnapshotCaptureResult:
    repo: RepoConfig
    snapshot: DiffSnapshot | None
    error: str = ""


@dataclass(frozen=True)
class SettingsValues:
    export_directory: str
    auto_export_time: str
    ai_mode: str
    ai_fallback_enabled: bool
    ai_ollama_url: str
    ai_ollama_model: str
    ai_ollama_keep_alive: str
    ai_openai_url: str
    ai_openai_model: str
    github_auth_mode: str
    github_auth_endpoint: str
    github_oauth_client_id: str
    ai_github_gpt_url: str
    ai_github_gpt_model: str
    high_contrast_enabled: bool
    ui_font_size: int


@dataclass(frozen=True)
class SettingsDialogState:
    time_var: tk.StringVar
    export_dir_var: tk.StringVar
    high_contrast_var: tk.BooleanVar
    font_size_var: tk.IntVar
    ai_mode_var: tk.StringVar
    ai_fallback_var: tk.BooleanVar
    ollama_url_var: tk.StringVar
    ollama_model_var: tk.StringVar
    ollama_keep_alive_var: tk.StringVar
    openai_url_var: tk.StringVar
    openai_model_var: tk.StringVar
    github_auth_mode_var: tk.StringVar
    github_auth_endpoint_var: tk.StringVar
    github_oauth_client_id_var: tk.StringVar
    github_auth_token_var: tk.StringVar
    github_auth_clear_var: tk.BooleanVar
    github_gpt_url_var: tk.StringVar
    github_gpt_model_var: tk.StringVar


def _default_font_families() -> tuple[str, str]:
    if sys.platform == "win32":
        return "Segoe UI", "Consolas"
    if sys.platform == "darwin":
        return "Helvetica Neue", "Menlo"
    return "TkDefaultFont", "TkFixedFont"


DEFAULT_UI_FONT_FAMILY, DEFAULT_EDITOR_FONT_FAMILY = _default_font_families()


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str | Callable[[], str]) -> None:
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
        text = self.text() if callable(self.text) else self.text
        label = tk.Label(
            self.tip_window,
            text=text,
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
        self.root.title(self._window_title())
        self._set_initial_window_size()
        self._apply_window_icon()
        self.config = load_config()
        self._apply_ai_environment_defaults()
        self.status_var = tk.StringVar(value="Idle")
        self._ai_status_severity = self._initial_ai_status_severity()
        self.ai_status_var = tk.StringVar(value=self._initial_ai_status_text())
        self.week_end_var = tk.StringVar(value=self.config.week_end_day)
        self.theme_mode_var = tk.StringVar(value=self.config.theme_mode.capitalize())
        self.high_contrast_var = tk.BooleanVar(value=self.config.high_contrast)
        self.ui_font_size_var = tk.IntVar(value=self.config.ui_font_size)
        self.repo_path_var = tk.StringVar()
        self.repo_label_var = tk.StringVar()
        self.poll_seconds_var = tk.StringVar(value="120")
        self.repo_items: dict[str, RepoConfig] = {}
        self._selected_repo_index: int | None = None
        self.repo_action_button: ttk.Button | None = None
        self._poll_in_flight = False
        self._poll_thread: threading.Thread | None = None
        self._poll_results: list[SnapshotCaptureResult] = []
        self._poll_captured_count = 0
        self._poll_errors: list[str] = []
        self._ai_backend_unavailable = False
        self._ai_last_warning = ""
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
        self._schedule_enhancer_tick()

    def _set_windows_app_id(self) -> None:
        if os.name != "nt":
            return
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
        except Exception:
            return

    def _set_initial_window_size(self) -> None:
        # Open with a roomy layout now that the sidebar has additional controls.
        min_width, min_height = 1180, 780
        self.root.minsize(min_width, min_height)

        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        width = max(min_width, int(screen_w * 0.86))
        height = max(min_height, int(screen_h * 0.84))

        width = min(width, screen_w - 40)
        height = min(height, screen_h - 80)

        pos_x = max(0, (screen_w - width) // 2)
        pos_y = max(0, (screen_h - height) // 3)
        self.root.geometry(f"{width}x{height}+{pos_x}+{pos_y}")

    def _apply_ai_environment_defaults(self) -> None:
        ollama_model = self.config.ai_ollama_model.strip() or DEFAULT_OLLAMA_SUMMARY_MODEL
        env_pairs = {
            "PYESIS_AI_MODE": self.config.ai_mode,
            "PYESIS_OLLAMA_URL": self.config.ai_ollama_url,
            "PYESIS_OLLAMA_MODEL": ollama_model,
            "PYESIS_OLLAMA_KEEP_ALIVE": self.config.ai_ollama_keep_alive,
            "PYESIS_AI_URL": self.config.ai_openai_url,
            "PYESIS_AI_MODEL": self.config.ai_openai_model,
            "PYESIS_GITHUB_GPT_URL": self.config.ai_github_gpt_url,
            "PYESIS_GITHUB_GPT_MODEL": self.config.ai_github_gpt_model,
        }
        for key, value in env_pairs.items():
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
        legacy_github_env = {
            "PYESIS_GITHUB_COPILOT_URL": self.config.ai_github_gpt_url,
            "PYESIS_GITHUB_COPILOT_MODEL": self.config.ai_github_gpt_model,
        }
        for key, value in legacy_github_env.items():
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)
        github_auth_mode = normalize_github_auth_mode(self.config.github_auth_mode)
        github_auth_endpoint = normalize_github_auth_endpoint(github_auth_mode, self.config.github_auth_endpoint)
        os.environ["PYESIS_GITHUB_AUTH_MODE"] = github_auth_mode
        if github_auth_endpoint:
            os.environ["PYESIS_GITHUB_AUTH_ENDPOINT"] = github_auth_endpoint
        else:
            os.environ.pop("PYESIS_GITHUB_AUTH_ENDPOINT", None)

        github_token, _ = load_github_auth_token(github_auth_mode, github_auth_endpoint)
        secure_env_pairs = {
            "PYESIS_GITHUB_GPT_API_KEY": github_token,
            "PYESIS_GITHUB_COPILOT_API_KEY": github_token,
        }
        for key, value in secure_env_pairs.items():
            if value:
                os.environ[key] = value
            else:
                os.environ.pop(key, None)

    def _current_ai_mode(self) -> str:
        mode = self.config.ai_mode.strip().lower()
        if mode in {HEURISTIC_MODE, OLLAMA_MODE, OPENAI_COMPATIBLE_MODE, GITHUB_GPT_MODE}:
            return mode
        return HEURISTIC_MODE

    def _has_github_gpt_config(self) -> bool:
        return bool(
            self.config.ai_github_gpt_model.strip()
            or self._github_auth_status().has_token
        )

    def _detect_ollama_presence(self) -> bool:
        return bool(shutil.which("ollama"))

    def _preferred_ai_mode_for_settings(self) -> str:
        current_mode = self._current_ai_mode()
        if current_mode != HEURISTIC_MODE:
            return current_mode
        ollama_present = self._detect_ollama_presence()
        if ollama_present:
            return OLLAMA_MODE
        if current_mode == HEURISTIC_MODE:
            return GITHUB_GPT_MODE
        return current_mode

    def _summary_mode_or_default(self, summary_source: str, author: str) -> str:
        normalized = summary_source.strip().lower()
        if normalized:
            return normalized
        if author == "Backup":
            return HEURISTIC_MODE
        return self._current_ai_mode()

    def _preferred_summary_modes(self) -> list[str]:
        modes: list[str] = []

        if self._github_auth_status().has_token:
            modes.append(GITHUB_GPT_MODE)

        current_mode = self._current_ai_mode()
        if current_mode != HEURISTIC_MODE and current_mode not in modes:
            modes.append(current_mode)

        if self.config.ai_ollama_url.strip() and OLLAMA_MODE not in modes:
            modes.append(OLLAMA_MODE)

        if self.config.ai_openai_url.strip() and os.getenv("PYESIS_AI_API_KEY", "").strip() and OPENAI_COMPATIBLE_MODE not in modes:
            modes.append(OPENAI_COMPATIBLE_MODE)

        modes.append(HEURISTIC_MODE)
        return modes

    def _ai_provider_label(self, mode: str) -> str:
        return AI_PROVIDER_LABELS.get(mode, mode or "AI")

    def _github_auth_status(self) -> GitHubAuthStatus:
        return describe_github_auth(self.config.github_auth_mode, self.config.github_auth_endpoint)

    def _initial_ai_status_severity(self) -> str:
        mode = self._current_ai_mode()
        if mode == GITHUB_GPT_MODE and not self._github_auth_status().has_token:
            return "degraded"
        return "ok"

    def _normalize_github_auth_settings_or_show_error(self, mode: str, endpoint: str) -> tuple[str, str] | None:
        normalized_mode = normalize_github_auth_mode(mode)
        normalized_endpoint = normalize_github_auth_endpoint(normalized_mode, endpoint)
        if normalized_mode == GITHUB_ENTERPRISE_AUTH_MODE and not normalized_endpoint:
            messagebox.showerror("GitHub Enterprise required", "Enter the GitHub Enterprise host, for example github.company.com.")
            return None
        return normalized_mode, normalized_endpoint

    def _store_github_auth_token_or_show_error(
        self,
        mode: str,
        endpoint: str,
        token: str,
        clear_token: bool,
    ) -> bool:
        if clear_token:
            ok, message = clear_github_auth_token(mode, endpoint)
        elif token.strip():
            ok, message = store_github_auth_token(mode, endpoint, token)
        else:
            return True

        if ok:
            return True
        messagebox.showerror("GitHub token storage failed", message or "Could not update the stored GitHub token.")
        return False

    def _build_summary_with_current_provider(
        self,
        repo_label: str,
        diff_excerpt: str,
        repo_path: str | None = None,
    ) -> AISummaryResult:
        return self._build_summary_with_priority_modes(repo_label, diff_excerpt, repo_path)

    def _build_summary_for_mode(self, repo_label: str, diff_excerpt: str, repo_path: str | None, mode: str) -> AISummaryResult:
        allow_fallback = mode == HEURISTIC_MODE and self.config.ai_fallback_enabled
        return build_summary(
            repo_label,
            diff_excerpt,
            repo_path,
            mode=mode,
            allow_fallback=allow_fallback,
        )

    def _accept_summary_result(self, result: AISummaryResult, requested_mode: str) -> bool:
        return bool(result.text.strip()) and (result.source != HEURISTIC_MODE or requested_mode == HEURISTIC_MODE)

    def _attach_warnings(self, result: AISummaryResult, warnings: list[str]) -> AISummaryResult:
        if warnings and not result.warning.strip():
            result.warning = "; ".join(warnings)
        return result

    def _build_summary_with_priority_modes(self, repo_label: str, diff_excerpt: str, repo_path: str | None = None) -> AISummaryResult:
        last_result: AISummaryResult | None = None
        warnings: list[str] = []

        for mode in self._preferred_summary_modes():
            result = self._build_summary_for_mode(repo_label, diff_excerpt, repo_path, mode)
            if result.warning.strip():
                warnings.append(result.warning.strip())
            if self._accept_summary_result(result, mode):
                return self._attach_warnings(result, warnings)
            last_result = result

        if last_result is not None:
            return self._attach_warnings(last_result, warnings)

        fallback_result = self._build_summary_for_mode(repo_label, diff_excerpt, repo_path, HEURISTIC_MODE)
        return self._attach_warnings(fallback_result, warnings)

    def _asset_roots(self) -> list[Path]:
        roots: list[Path] = []
        if getattr(sys, "frozen", False):
            bundle_root = Path(getattr(sys, "_MEIPASS", ""))
            if bundle_root:
                roots.append(bundle_root)
            exe_root = Path(sys.executable).resolve().parent
            roots.append(exe_root)
        roots.append(Path.cwd())
        roots.append(Path(__file__).resolve().parent.parent)
        return roots

    def _window_title(self) -> str:
        return f"Pyesis v{self._app_version()}"

    def _app_version(self) -> str:
        version_file_names = ("pyproject.toml",)
        for root in self._asset_roots():
            for name in version_file_names:
                candidate = root / name
                if not candidate.exists():
                    continue
                try:
                    data = tomllib.loads(candidate.read_text(encoding="utf-8"))
                except Exception:
                    continue
                project = data.get("project", {})
                version = str(project.get("version", "")).strip()
                if version:
                    return version
        return "dev"

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

        if os.name == "nt" and getattr(sys, "frozen", False):
            try:
                self.root.iconbitmap(default=str(Path(sys.executable)))
                return True
            except tk.TclError:
                pass
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
        self.repo_list = tk.Listbox(
            sidebar,
            height=12,
            width=38,
            exportselection=False,
            activestyle="dotbox",
            selectborderwidth=2,
        )
        self.repo_list.grid(row=1, column=0, sticky="nsew", pady=(6, 12))
        self.repo_list.bind("<<ListboxSelect>>", self._on_repo_selected)
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
        self.repo_action_button = ttk.Button(sidebar, text="Add Repo", underline=0, command=self._add_or_update_repo)
        self.repo_action_button.grid(row=9, column=0, sticky="ew")
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
        ttk.Button(sidebar, text="Edit Entry", underline=0, command=self._open_entry_editor).grid(row=16, column=0, sticky="ew", pady=(6, 0))
        ttk.Label(sidebar, text="AI status").grid(row=17, column=0, sticky="w", pady=(12, 2))
        ttk.Label(sidebar, textvariable=self.ai_status_var, style="AIStatus.TLabel", wraplength=260).grid(row=18, column=0, sticky="ew")
        ttk.Label(sidebar, textvariable=self.status_var, wraplength=260).grid(row=19, column=0, sticky="ew", pady=(12, 0))

        editor_header = ttk.Frame(editor_area)
        editor_header.grid(row=0, column=0, sticky="ew")
        editor_header.columnconfigure(0, weight=1)

        ttk.Label(editor_header, text="Weekly Work Log Preview").grid(row=0, column=0, sticky="w")

        github_button = ttk.Button(editor_header, text="🐙 GitHub", underline=2, width=10, command=self._open_github_repo)
        github_button.grid(row=0, column=1, sticky="e", padx=(0, 6))

        docs_button = ttk.Button(editor_header, text="Docs", underline=0, width=8, command=self._open_docx_folder)
        docs_button.grid(row=0, column=2, sticky="e", padx=(0, 6))

        info_button = ttk.Button(editor_header, text="ⓘ Info", underline=2, width=8, command=self._open_readme_view)
        info_button.grid(row=0, column=3, sticky="e", padx=(0, 6))

        settings_button = ttk.Button(editor_header, text="⚙ Settings", underline=2, width=11, command=self._open_settings)
        settings_button.grid(row=0, column=4, sticky="e")

        ToolTip(github_button, "Open GitHub Repository (Ctrl+Shift+G)")
        ToolTip(docs_button, lambda: f"Pyesis docx file folder ({self._docx_output_dir()})")
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
            font=(DEFAULT_EDITOR_FONT_FAMILY, 11),
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
        self._editor_bg_canvas.bind(MOUSEWHEEL_EVENT, self._forward_editor_scroll)

    def _forward_editor_scroll(self, event: tk.Event) -> str:
        if event.delta:
            self.editor.yview_scroll(-1 * int(event.delta / 120), "units")
        return "break"

    def _bind_shortcuts(self) -> None:
        self.root.bind_all("<Control-comma>", self._on_shortcut_settings)
        self.root.bind_all("<F1>", self._on_shortcut_readme)
        self.root.bind_all("<Control-Shift-G>", self._on_shortcut_github)
        self.root.bind_all("<Alt-b>", lambda _e: self._browse_repo())
        self.root.bind_all("<Alt-a>", lambda _e: self._add_or_update_repo())
        self.root.bind_all("<Alt-r>", lambda _e: self._remove_selected_repo())
        self.root.bind_all("<Alt-c>", lambda _e: self.run_poll_once())
        self.root.bind_all("<Alt-e>", lambda _e: self._export_docx())
        self.root.bind_all("<Alt-d>", lambda _e: self._open_entry_editor())
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
            if not self.repo_label_var.get().strip():
                self.repo_label_var.set(Path(selected).name)

    def _on_repo_selected(self, _event: tk.Event) -> None:
        selection = self.repo_list.curselection()
        if not selection:
            self._selected_repo_index = None
            self._set_repo_action_button_text(False)
            return

        index = selection[0]
        repo = self.config.repos[index]
        self._selected_repo_index = index
        self.repo_path_var.set(repo.path)
        self.repo_label_var.set(repo.label)
        self.poll_seconds_var.set(str(repo.poll_seconds))
        self._set_repo_action_button_text(True)

    def _selected_repo_index_from_selection(self) -> int | None:
        selection = self.repo_list.curselection()
        if selection:
            return selection[0]

        if self._selected_repo_index is None:
            return None
        if 0 <= self._selected_repo_index < len(self.config.repos):
            return self._selected_repo_index
        return None

    def _add_or_update_repo(self) -> None:
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

        current_index = self._selected_repo_index_from_selection()
        if current_index is None:
            if any(repo.path == path for repo in self.config.repos):
                messagebox.showinfo("Already added", "This repository is already being monitored.")
                return
            self.config.repos.append(RepoConfig(path=path, label=label, poll_seconds=poll_seconds))
            status_text = f"Added {label}"
            current_index = len(self.config.repos) - 1
        else:
            if any(index != current_index and repo.path == path for index, repo in enumerate(self.config.repos)):
                messagebox.showinfo("Already added", "Another monitored repository already uses that path.")
                return

            self.config.repos[current_index] = RepoConfig(path=path, label=label, poll_seconds=poll_seconds)
            status_text = f"Updated {label}"

        self._persist()
        self._refresh_repo_list()
        self._clear_repo_form()
        self.status_var.set(status_text)

    def _remove_selected_repo(self) -> None:
        index = self._selected_repo_index_from_selection()
        if index is None:
            return
        repo = self.config.repos[index]
        self.config.repos.pop(index)
        self._persist()
        self._refresh_repo_list()
        self._clear_repo_form()
        self.status_var.set(f"Removed {repo.label}")

    def _refresh_repo_list(self, select_index: int | None = None) -> None:
        self.repo_items = {}
        self.repo_list.delete(0, tk.END)
        for repo in self.config.repos:
            label = f"{repo.label} ({repo.poll_seconds}s)"
            self.repo_items[label] = repo
            self.repo_list.insert(tk.END, label)

        target_index = select_index if select_index is not None else self._selected_repo_index
        if target_index is None:
            return

        if 0 <= target_index < len(self.config.repos):
            self.repo_list.selection_set(target_index)
            self.repo_list.activate(target_index)
            self.repo_list.see(target_index)
            self._selected_repo_index = target_index

    def _sync_repo_row(self, index: int) -> None:
        if index < 0 or index >= len(self.config.repos):
            self._refresh_repo_list()
            return

        repo = self.config.repos[index]
        label = f"{repo.label} ({repo.poll_seconds}s)"
        self._selected_repo_index = index
        self.repo_items[label] = repo
        self.repo_list.delete(index)
        self.repo_list.insert(index, label)
        self.repo_list.selection_clear(0, tk.END)
        self.repo_list.selection_set(index)
        self.repo_list.activate(index)
        self.repo_list.see(index)

    def _clear_repo_form(self) -> None:
        self._selected_repo_index = None
        self.repo_list.selection_clear(0, tk.END)
        self.repo_path_var.set("")
        self.repo_label_var.set("")
        self.poll_seconds_var.set("120")
        self._set_repo_action_button_text(False)

    def _set_repo_action_button_text(self, is_update_mode: bool) -> None:
        if self.repo_action_button is None:
            return
        self.repo_action_button.configure(text="Update Repo" if is_update_mode else "Add Repo")

    def _migrate_entries(self) -> None:
        original_len = len(self.config.entries)
        filtered = self._remove_noise_entries(self.config.entries)
        deduped = dedupe_entries(filtered)
        rewritten = self._rewrite_legacy_summaries(deduped)

        if len(rewritten) != original_len or any(
            (a.summary != b.summary) or (a.author != b.author)
            for a, b in zip(deduped, rewritten)
        ):
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
            needs_refresh = self._needs_summary_refresh(entry)
            if needs_refresh and entry.diff_excerpt.strip():
                next_summary = self._build_summary_heuristic(entry.repo_label, entry.diff_excerpt, entry.repo_path).strip() or entry.summary
                rewritten.append(
                    EntryRecord(
                        repo_label=entry.repo_label,
                        repo_path=entry.repo_path,
                        created_at=entry.created_at,
                        day_name=entry.day_name,
                        week_start_iso=entry.week_start_iso,
                        summary=next_summary,
                        summary_source=HEURISTIC_MODE,
                        diff_hash=entry.diff_hash,
                        diff_excerpt=entry.diff_excerpt,
                        author="Backup",
                        rewritten_by=entry.rewritten_by,
                        rewritten_at=entry.rewritten_at,
                    )
                )
            else:
                rewritten.append(entry)
        return rewritten

    def _build_summary_heuristic(self, repo_label: str, diff_excerpt: str, repo_path: str | None = None) -> str:
        return build_summary(repo_label, diff_excerpt, repo_path, mode=HEURISTIC_MODE).text

    def _entry_author_from_source(self, source: str, current_is_backup: bool = True) -> str:
        if source and source != HEURISTIC_MODE:
            return "AI"
        return "Backup" if current_is_backup else "AI"

    def _needs_summary_refresh(self, entry: EntryRecord) -> bool:
        text = entry.summary.lower()
        structured_labels = ("who:", "what:", "where:", "when:", "why:", "how:", "description:")
        if all(label in text for label in structured_labels):
            return False
        if text.startswith("i ") and " to " in text and " by " in text:
            return False
        return (
            text.startswith("who:")
            or text.startswith("what:")
            or text.startswith("where:")
            or text.startswith("when:")
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

    def _workspace_settings_path(self) -> Path:
        return Path.cwd() / ".vscode" / "settings.json"

    def _sync_workspace_ollama_model_setting(self) -> None:
        model_name = self.config.ai_ollama_model.strip()
        if not model_name:
            return

        settings_path = self._workspace_settings_path()
        existing_settings: object = {}
        if settings_path.exists():
            try:
                existing_settings = json.loads(settings_path.read_text(encoding="utf-8"))
            except Exception:
                return

        if not isinstance(existing_settings, dict):
            return

        current_model = str(existing_settings.get(VSCODE_OLLAMA_AUTOCODER_MODEL_SETTING, "")).strip()
        if current_model == model_name:
            return

        updated_settings = dict(existing_settings)
        updated_settings[VSCODE_OLLAMA_AUTOCODER_MODEL_SETTING] = model_name
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(updated_settings, indent=4), encoding="utf-8")

    def _normalize_auto_export_time_or_show_error(self, raw_time: str) -> str | None:
        if not raw_time:
            return ""
        try:
            return self._normalize_time(raw_time)
        except ValueError:
            messagebox.showerror("Invalid time", "Use 24-hour HH:MM format, for example 17:30.")
            return None

    def _browse_export_directory(self, export_dir_var: tk.StringVar) -> None:
        selected = filedialog.askdirectory(
            title="Select DOCX export folder",
            initialdir=export_dir_var.get().strip() or str(Path.cwd()),
            mustexist=False,
        )
        if selected:
            export_dir_var.set(selected)

    def _collect_settings_values_or_show_error(self, state: SettingsDialogState) -> SettingsValues | None:
        export_directory = state.export_dir_var.get().strip() or default_export_directory()
        normalized_time = self._normalize_auto_export_time_or_show_error(state.time_var.get().strip())
        if normalized_time is None:
            return None

        normalized_github_auth = self._normalize_github_auth_settings_or_show_error(
            state.github_auth_mode_var.get(),
            state.github_auth_endpoint_var.get(),
        )
        if normalized_github_auth is None:
            return None
        github_auth_mode, github_auth_endpoint = normalized_github_auth

        if not self._store_github_auth_token_or_show_error(
            github_auth_mode,
            github_auth_endpoint,
            state.github_auth_token_var.get(),
            bool(state.github_auth_clear_var.get()),
        ):
            return None

        return SettingsValues(
            export_directory=export_directory,
            auto_export_time=normalized_time,
            ai_mode=state.ai_mode_var.get().strip().lower() or HEURISTIC_MODE,
            ai_fallback_enabled=bool(state.ai_fallback_var.get()),
            ai_ollama_url=state.ollama_url_var.get().strip(),
            ai_ollama_model=state.ollama_model_var.get().strip(),
            ai_ollama_keep_alive=state.ollama_keep_alive_var.get().strip() or "30m",
            ai_openai_url=state.openai_url_var.get().strip(),
            ai_openai_model=state.openai_model_var.get().strip(),
            github_auth_mode=github_auth_mode,
            github_auth_endpoint=github_auth_endpoint,
            github_oauth_client_id=state.github_oauth_client_id_var.get().strip(),
            ai_github_gpt_url=state.github_gpt_url_var.get().strip(),
            ai_github_gpt_model=state.github_gpt_model_var.get().strip() or GITHUB_GPT_DEFAULT_MODELS[0],
            high_contrast_enabled=bool(state.high_contrast_var.get()),
            ui_font_size=int(state.font_size_var.get()),
        )

    def _apply_settings_changes(self, settings: SettingsValues) -> None:
        self.config.auto_export_time = settings.auto_export_time
        self.config.export_directory = settings.export_directory
        self.config.ai_mode = settings.ai_mode
        self.config.ai_fallback_enabled = settings.ai_fallback_enabled
        self.config.ai_ollama_url = settings.ai_ollama_url
        self.config.ai_ollama_model = settings.ai_ollama_model
        self.config.ai_ollama_keep_alive = settings.ai_ollama_keep_alive
        self.config.ai_openai_url = settings.ai_openai_url
        self.config.ai_openai_model = settings.ai_openai_model
        self.config.github_auth_mode = settings.github_auth_mode
        self.config.github_auth_endpoint = settings.github_auth_endpoint
        self.config.github_oauth_client_id = settings.github_oauth_client_id
        self.config.ai_github_gpt_url = settings.ai_github_gpt_url
        self.config.ai_github_gpt_model = settings.ai_github_gpt_model
        self._apply_ai_environment_defaults()
        self._sync_workspace_ollama_model_setting()
        self._ai_backend_unavailable = False
        self._ai_last_warning = ""
        self._ai_status_severity = self._initial_ai_status_severity()
        self.ai_status_var.set(self._initial_ai_status_text())
        self.high_contrast_var.set(settings.high_contrast_enabled)
        self.ui_font_size_var.set(max(10, min(20, settings.ui_font_size)))
        self._apply_fonts()
        self._apply_theme()
        self._persist()

    def _save_settings_dialog(self, dialog: tk.Toplevel, state: SettingsDialogState) -> None:
        settings = self._collect_settings_values_or_show_error(state)
        if settings is None:
            return

        self._apply_settings_changes(settings)
        auto_export_state = self.config.auto_export_time or "disabled"
        provider = self._ai_provider_label(self._current_ai_mode())
        self.status_var.set(
            f"Auto-export time set to {auto_export_state}; DOCX folder set to {self.config.export_directory}; summary provider set to {provider}"
        )
        dialog.destroy()

    def _persist_github_auth_settings(self, mode: str, endpoint: str, client_id: str) -> None:
        self.config.github_auth_mode = mode
        self.config.github_auth_endpoint = endpoint
        self.config.github_oauth_client_id = client_id.strip()
        self._apply_ai_environment_defaults()
        save_config(self.config)

    def _run_github_device_login(
        self,
        mode: str,
        endpoint: str,
        client_id: str,
        status_var: tk.StringVar,
    ) -> None:
        try:
            device_login = start_github_device_login(mode, endpoint, client_id)
        except Exception as exc:
            self.root.after(0, lambda: status_var.set(f"GitHub sign-in failed: {exc}"))
            return

        def show_pending(login: GitHubDeviceLogin) -> None:
            status_var.set(f"Open the browser and enter code {login.user_code} at {login.verification_uri}.")

        self.root.after(0, lambda: show_pending(device_login))
        webbrowser.open(device_login.verification_uri)

        try:
            token = poll_github_device_login_token(
                mode,
                endpoint,
                client_id,
                device_login.device_code,
                device_login.expires_in,
                device_login.interval,
            )
            identity = fetch_github_user_identity(mode, endpoint, token)
            ok, message = store_github_auth_token(mode, endpoint, token)
            if not ok:
                raise RuntimeError(message or "Could not save the GitHub token.")
        except Exception as exc:
            self.root.after(0, lambda: status_var.set(f"GitHub sign-in failed: {exc}"))
            return

        def on_success(user: GitHubUserIdentity) -> None:
            self._persist_github_auth_settings(mode, endpoint, client_id)
            user_label = user.name or user.login or "GitHub user"
            status_var.set(f"Signed in as {user_label}. Token saved to macOS Keychain.")
            self._ai_status_severity = self._initial_ai_status_severity()
            self.ai_status_var.set(self._initial_ai_status_text())

        self.root.after(0, lambda: on_success(identity))

    def _start_github_device_login(
        self,
        mode_var: tk.StringVar,
        endpoint_var: tk.StringVar,
        client_id_var: tk.StringVar,
        status_var: tk.StringVar,
    ) -> None:
        normalized = self._normalize_github_auth_settings_or_show_error(mode_var.get(), endpoint_var.get())
        if normalized is None:
            status_var.set("Enter a GitHub Enterprise host before starting sign-in.")
            return
        mode, endpoint = normalized
        client_id = client_id_var.get().strip()
        if not client_id:
            status_var.set("Enter a GitHub OAuth client ID before starting sign-in.")
            messagebox.showerror(
                "GitHub OAuth client ID required",
                "Enter the OAuth app client ID for Pyesis before starting GitHub sign-in.",
            )
            return

        status_var.set("Requesting GitHub device code...")
        worker = threading.Thread(
            target=self._run_github_device_login,
            args=(mode, endpoint, client_id, status_var),
            daemon=True,
        )
        worker.start()

    def _fetch_ollama_model_names(self, base_url: str) -> list[str]:
        req = urlrequest.Request(
            _ollama_tags_url(base_url),
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urlrequest.urlopen(req, timeout=5) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urlerror.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not load Ollama models: {exc}") from exc

        raw_models = payload.get("models")
        if not isinstance(raw_models, list):
            raise RuntimeError("Could not load Ollama models: unexpected response payload")

        names: list[str] = []
        for item in raw_models:
            if not isinstance(item, dict):
                continue
            name = str(item.get("model") or item.get("name") or "").strip()
            if name and name not in names:
                names.append(name)
        return names

    def _ollama_model_refresh_message(self, model_names: tuple[str, ...], current_model: str) -> str:
        if model_names and current_model and current_model not in model_names:
            return f"Loaded {len(model_names)} installed Ollama models. Current selection is not installed."
        if model_names:
            return f"Loaded {len(model_names)} installed Ollama models."
        if current_model:
            return "No installed Ollama models were reported. Keeping the saved model selection."
        return "No installed Ollama models were reported."

    def _apply_ollama_model_choices(
        self,
        names: list[str],
        model_var: tk.StringVar,
        combobox: ttk.Combobox,
        status_var: tk.StringVar,
        refresh_button: ttk.Button,
        current_model: str,
    ) -> None:
        if not combobox.winfo_exists():
            return

        refresh_button.configure(state="normal")
        model_names = tuple(names)
        combobox.configure(values=model_names)
        if model_names and not model_var.get().strip():
            model_var.set(model_names[0])
        status_var.set(self._ollama_model_refresh_message(model_names, current_model))

    def _show_ollama_model_refresh_error(
        self,
        combobox: ttk.Combobox,
        status_var: tk.StringVar,
        refresh_button: ttk.Button,
        message: str,
    ) -> None:
        if not combobox.winfo_exists():
            return

        refresh_button.configure(state="normal")
        status_var.set(message)

    def _refresh_ollama_model_choices(
        self,
        url_var: tk.StringVar,
        model_var: tk.StringVar,
        combobox: ttk.Combobox,
        status_var: tk.StringVar,
        refresh_button: ttk.Button,
    ) -> None:
        base_url = url_var.get().strip()
        current_model = model_var.get().strip()
        status_var.set("Loading installed Ollama models...")
        refresh_button.configure(state="disabled")

        def worker() -> None:
            try:
                names = self._fetch_ollama_model_names(base_url)
            except Exception as exc:
                self.root.after(
                    0,
                    lambda: self._show_ollama_model_refresh_error(
                        combobox,
                        status_var,
                        refresh_button,
                        str(exc),
                    ),
                )
                return
            self.root.after(
                0,
                lambda: self._apply_ollama_model_choices(
                    names,
                    model_var,
                    combobox,
                    status_var,
                    refresh_button,
                    current_model,
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    def _open_settings(self) -> None:
        ollama_present = self._detect_ollama_presence()
        preferred_ai_mode = self._preferred_ai_mode_for_settings()

        dialog = tk.Toplevel(self.root)
        dialog.title("Settings")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, True)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)

        screen_w = dialog.winfo_screenwidth()
        screen_h = dialog.winfo_screenheight()
        width = min(max(700, int(screen_w * 0.5)), screen_w - 120)
        height = min(max(760, int(screen_h * 0.78)), screen_h - 120)
        dialog.geometry(f"{width}x{height}")
        dialog.minsize(640, 620)

        shell = ttk.Frame(dialog, padding=12)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)

        canvas = tk.Canvas(shell, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(shell, orient="vertical", command=canvas.yview)
        frame = ttk.Frame(canvas, padding=2)
        frame.columnconfigure(0, weight=1)

        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns", padx=(8, 0))

        frame_window = canvas.create_window((0, 0), window=frame, anchor="nw")

        def sync_scroll_region(_event: tk.Event | None = None) -> None:
            canvas.configure(scrollregion=canvas.bbox("all"))

        def sync_frame_width(event: tk.Event) -> None:
            canvas.itemconfigure(frame_window, width=event.width)

        def on_mousewheel(event: tk.Event) -> str:
            if event.delta:
                canvas.yview_scroll(-1 * int(event.delta / 120), "units")
            return "break"

        def bind_mousewheel(_event: tk.Event) -> None:
            canvas.bind_all(MOUSEWHEEL_EVENT, on_mousewheel)

        def unbind_mousewheel(_event: tk.Event) -> None:
            canvas.unbind_all(MOUSEWHEEL_EVENT)

        frame.bind("<Configure>", sync_scroll_region)
        canvas.bind("<Configure>", sync_frame_width)
        canvas.bind("<Enter>", bind_mousewheel)
        canvas.bind("<Leave>", unbind_mousewheel)

        time_var = tk.StringVar(value=self.config.auto_export_time)
        export_dir_var = tk.StringVar(value=self.config.export_directory or default_export_directory())
        high_contrast_var = tk.BooleanVar(value=self.config.high_contrast)
        font_size_var = tk.IntVar(value=self.config.ui_font_size)
        ai_mode_var = tk.StringVar(value=preferred_ai_mode)
        ai_fallback_var = tk.BooleanVar(value=self.config.ai_fallback_enabled)
        ollama_url_var = tk.StringVar(value=self.config.ai_ollama_url)
        ollama_model_var = tk.StringVar(value=self.config.ai_ollama_model or DEFAULT_OLLAMA_SUMMARY_MODEL)
        ollama_model_status_var = tk.StringVar(value="Refresh to load installed Ollama models.")
        ollama_keep_alive_var = tk.StringVar(value=self.config.ai_ollama_keep_alive)
        openai_url_var = tk.StringVar(value=self.config.ai_openai_url)
        openai_model_var = tk.StringVar(value=self.config.ai_openai_model)
        github_auth_status = self._github_auth_status()
        github_auth_status_var = tk.StringVar(value=github_auth_status.detail)
        github_auth_mode_var = tk.StringVar(value=normalize_github_auth_mode(self.config.github_auth_mode))
        github_auth_endpoint_var = tk.StringVar(value=self.config.github_auth_endpoint)
        github_oauth_client_id_var = tk.StringVar(value=self.config.github_oauth_client_id)
        github_login_help_var = tk.StringVar()
        github_auth_token_var = tk.StringVar(value="")
        github_auth_clear_var = tk.BooleanVar(value=False)
        github_gpt_url_var = tk.StringVar(value=self.config.ai_github_gpt_url)
        github_gpt_model_var = tk.StringVar(value=self.config.ai_github_gpt_model or GITHUB_GPT_DEFAULT_MODELS[0])

        state = SettingsDialogState(
            time_var=time_var,
            export_dir_var=export_dir_var,
            high_contrast_var=high_contrast_var,
            font_size_var=font_size_var,
            ai_mode_var=ai_mode_var,
            ai_fallback_var=ai_fallback_var,
            ollama_url_var=ollama_url_var,
            ollama_model_var=ollama_model_var,
            ollama_keep_alive_var=ollama_keep_alive_var,
            openai_url_var=openai_url_var,
            openai_model_var=openai_model_var,
            github_auth_mode_var=github_auth_mode_var,
            github_auth_endpoint_var=github_auth_endpoint_var,
            github_oauth_client_id_var=github_oauth_client_id_var,
            github_auth_token_var=github_auth_token_var,
            github_auth_clear_var=github_auth_clear_var,
            github_gpt_url_var=github_gpt_url_var,
            github_gpt_model_var=github_gpt_model_var,
        )

        ttk.Label(frame, text="Daily DOCX export time (24h HH:MM)").grid(row=0, column=0, sticky="w")
        ttk.Entry(frame, textvariable=time_var, width=12).grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Label(frame, text="Leave blank to disable daily auto-export.").grid(row=2, column=0, sticky="w", pady=(6, 12))

        ttk.Label(frame, text="DOCX export folder").grid(row=3, column=0, sticky="w")
        export_row = ttk.Frame(frame)
        export_row.grid(row=4, column=0, sticky="ew", pady=(6, 12))
        export_row.columnconfigure(0, weight=1)
        ttk.Entry(export_row, textvariable=export_dir_var, width=42).grid(row=0, column=0, sticky="ew")
        ttk.Button(export_row, text="Browse", command=lambda: self._browse_export_directory(export_dir_var)).grid(row=0, column=1, padx=(6, 0))

        ttk.Label(frame, text="Accessibility").grid(row=5, column=0, sticky="w")
        ttk.Checkbutton(frame, text="High contrast mode", variable=high_contrast_var).grid(row=6, column=0, sticky="w", pady=(6, 0))
        ttk.Label(frame, text="UI font size").grid(row=7, column=0, sticky="w", pady=(8, 2))
        ttk.Spinbox(frame, from_=10, to=20, textvariable=font_size_var, width=6).grid(row=8, column=0, sticky="w")
        ttk.Label(frame, text="Tip: Keyboard shortcuts include Alt+B/A/R/C/E/D/S/I/G").grid(row=9, column=0, sticky="w", pady=(6, 12))

        ttk.Label(frame, text="AI provider").grid(row=10, column=0, sticky="w")
        ttk.Combobox(
            frame,
            textvariable=ai_mode_var,
            values=[OLLAMA_MODE, GITHUB_GPT_MODE, OPENAI_COMPATIBLE_MODE, HEURISTIC_MODE],
            state="readonly",
        ).grid(row=11, column=0, sticky="ew", pady=(6, 6))
        ttk.Label(frame, text="Pick provider and model per machine; heavier models can be slower or memory-intensive.").grid(row=12, column=0, sticky="w")
        ttk.Checkbutton(frame, text="Use heuristic fallback when AI fails", variable=ai_fallback_var).grid(row=13, column=0, sticky="w")
        ollama_status = "detected on this machine" if ollama_present else "not detected; defaulting to GitHub GPT"
        ttk.Label(frame, text=f"Ollama status: {ollama_status}").grid(row=14, column=0, sticky="w", pady=(6, 2))
        ttk.Label(frame, text="GitHub tokens can come from the environment or be stored securely in macOS Keychain below.").grid(row=15, column=0, sticky="w", pady=(0, 12))

        ttk.Label(frame, text="Ollama URL").grid(row=16, column=0, sticky="w")
        ttk.Entry(frame, textvariable=ollama_url_var, width=42).grid(row=17, column=0, sticky="ew", pady=(4, 6))
        ttk.Label(frame, text="Ollama model").grid(row=18, column=0, sticky="w")
        ollama_model_row = ttk.Frame(frame)
        ollama_model_row.grid(row=19, column=0, sticky="ew", pady=(4, 4))
        ollama_model_row.columnconfigure(0, weight=1)
        ollama_model_box = ttk.Combobox(
            ollama_model_row,
            textvariable=ollama_model_var,
            values=(ollama_model_var.get().strip(),) if ollama_model_var.get().strip() else (),
            state="normal",
        )
        ollama_model_box.grid(row=0, column=0, sticky="ew")
        ollama_refresh_button = ttk.Button(
            ollama_model_row,
            text="Refresh",
            command=lambda: self._refresh_ollama_model_choices(
                ollama_url_var,
                ollama_model_var,
                ollama_model_box,
                ollama_model_status_var,
                ollama_refresh_button,
            ),
        )
        ollama_refresh_button.grid(row=0, column=1, padx=(6, 0))
        ttk.Label(frame, textvariable=ollama_model_status_var, wraplength=560, justify="left").grid(row=20, column=0, sticky="w", pady=(0, 6))
        ttk.Label(frame, text="Ollama keep alive").grid(row=21, column=0, sticky="w")
        ttk.Entry(frame, textvariable=ollama_keep_alive_var, width=18).grid(row=22, column=0, sticky="w", pady=(4, 12))

        ttk.Label(frame, text="OpenAI-compatible URL").grid(row=23, column=0, sticky="w")
        ttk.Entry(frame, textvariable=openai_url_var, width=42).grid(row=24, column=0, sticky="ew", pady=(4, 6))
        ttk.Label(frame, text="OpenAI-compatible model").grid(row=25, column=0, sticky="w")
        ttk.Entry(frame, textvariable=openai_model_var, width=42).grid(row=26, column=0, sticky="ew", pady=(4, 12))

        ttk.Label(frame, text="GitHub account type").grid(row=27, column=0, sticky="w")
        ttk.Combobox(
            frame,
            textvariable=github_auth_mode_var,
            values=[GITHUB_DOTCOM_AUTH_MODE, GITHUB_ENTERPRISE_AUTH_MODE],
            state="readonly",
        ).grid(row=28, column=0, sticky="ew", pady=(4, 6))
        ttk.Label(frame, text="GitHub Enterprise host (used only for Enterprise sign-in)").grid(row=29, column=0, sticky="w")
        ttk.Entry(frame, textvariable=github_auth_endpoint_var, width=42).grid(row=30, column=0, sticky="ew", pady=(4, 6))
        ttk.Label(frame, textvariable=github_auth_status_var, wraplength=560, justify="left").grid(row=31, column=0, sticky="w")
        ttk.Label(frame, text="GitHub OAuth client ID").grid(row=32, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=github_oauth_client_id_var, width=42).grid(row=33, column=0, sticky="ew", pady=(4, 6))
        ttk.Label(
            frame,
            textvariable=github_login_help_var,
            wraplength=560,
            justify="left",
        ).grid(row=34, column=0, sticky="w")
        auth_actions = ttk.Frame(frame)
        auth_actions.grid(row=35, column=0, sticky="w", pady=(8, 8))
        sign_in_button = ttk.Button(
            auth_actions,
            text="Sign in with GitHub",
            command=lambda: self._start_github_device_login(
                github_auth_mode_var,
                github_auth_endpoint_var,
                github_oauth_client_id_var,
                github_auth_status_var,
            ),
        )
        sign_in_button.grid(row=0, column=0, padx=(0, 8))
        ttk.Label(auth_actions, text="Uses OAuth device flow and opens the browser for verification.").grid(row=0, column=1, sticky="w")

        def refresh_github_login_controls(*_args: object) -> None:
            has_client_id = bool(github_oauth_client_id_var.get().strip())
            if has_client_id:
                github_login_help_var.set(
                    "Pyesis can open GitHub sign-in in the browser because a registered OAuth app client ID is configured."
                )
                sign_in_button.configure(state="normal")
                return

            github_login_help_var.set(
                "GitHub Desktop works because it ships with its own registered OAuth app. Pyesis needs a configured client ID before browser sign-in can work."
            )
            sign_in_button.configure(state="disabled")

        github_oauth_client_id_var.trace_add("write", refresh_github_login_controls)
        refresh_github_login_controls()

        ttk.Label(frame, text="Or store or replace a token manually").grid(row=36, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(frame, textvariable=github_auth_token_var, width=42, show="*").grid(row=37, column=0, sticky="ew", pady=(4, 6))
        ttk.Checkbutton(frame, text="Clear stored GitHub token on save", variable=github_auth_clear_var).grid(row=38, column=0, sticky="w", pady=(0, 12))

        ttk.Label(frame, text="GitHub GPT URL").grid(row=39, column=0, sticky="w")
        ttk.Entry(frame, textvariable=github_gpt_url_var, width=42).grid(row=40, column=0, sticky="ew", pady=(4, 6))
        ttk.Label(frame, text="GitHub GPT model").grid(row=41, column=0, sticky="w")
        ttk.Combobox(
            frame,
            textvariable=github_gpt_model_var,
            values=list(GITHUB_GPT_DEFAULT_MODELS),
            state="readonly",
        ).grid(row=42, column=0, sticky="ew", pady=(4, 12))

        self._refresh_ollama_model_choices(
            ollama_url_var,
            ollama_model_var,
            ollama_model_box,
            ollama_model_status_var,
            ollama_refresh_button,
        )

        controls = ttk.Frame(shell)
        controls.grid(row=1, column=0, columnspan=2, sticky="e", pady=(12, 0))

        ttk.Button(controls, text="Cancel", command=dialog.destroy).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(controls, text="Save", command=lambda: self._save_settings_dialog(dialog, state)).grid(row=0, column=1)

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
            target = export_docx(self.config, self._docx_output_dir(), file_name=file_name)
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
        if sys.platform == "darwin":
            try:
                completed = subprocess.run(
                    ["defaults", "read", "-g", "AppleInterfaceStyle"],
                    capture_output=True,
                    text=True,
                    check=False,
                )
            except Exception:
                return "light"
            return "dark" if completed.returncode == 0 and completed.stdout.strip().lower() == "dark" else "light"
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
        ai_fg = "#d13438" if self._ai_status_severity == "degraded" else "#107c10"
        self.style.configure("AIStatus.TLabel", background=palette["bg"], foreground=ai_fg)
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
                font.configure(family=DEFAULT_UI_FONT_FAMILY, size=size)
            except tk.TclError:
                continue
        try:
            fixed_font = tkfont.nametofont("TkFixedFont")
            fixed_font.configure(family=DEFAULT_EDITOR_FONT_FAMILY, size=size)
        except tk.TclError:
            pass
        if hasattr(self, "editor"):
            self.editor.configure(font=(DEFAULT_EDITOR_FONT_FAMILY, size))

    def _editable_entries(self) -> list[tuple[int, EntryRecord]]:
        indexed_entries = list(enumerate(self.config.entries))
        indexed_entries.sort(key=lambda item: item[1].created_at, reverse=True)
        return indexed_entries

    def _entry_picker_label(self, entry: EntryRecord) -> str:
        stamp = entry.created_at.replace("T", " ")[:16]
        one_line = re.sub(r"\s+", " ", entry.summary.strip())
        excerpt = one_line[:72] + ("..." if len(one_line) > 72 else "")
        if not excerpt:
            excerpt = "(empty summary)"
        return f"{stamp} | {entry.repo_label} | {excerpt}"

    def _open_entry_editor(self) -> None:
        indexed_entries = self._editable_entries()
        if not indexed_entries:
            messagebox.showinfo("No entries", "There are no captured entries to edit yet.")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Edit Entry")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(True, True)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(0, weight=1)

        shell = ttk.Frame(dialog, padding=12)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(2, weight=1)

        ttk.Label(shell, text="Select one entry to edit").grid(row=0, column=0, sticky="w")

        selector_row = ttk.Frame(shell)
        selector_row.grid(row=1, column=0, sticky="nsew", pady=(6, 8))
        selector_row.columnconfigure(0, weight=1)
        selector_row.rowconfigure(0, weight=1)

        listbox = tk.Listbox(selector_row, exportselection=False, height=7)
        listbox.grid(row=0, column=0, sticky="nsew")
        list_scroll = ttk.Scrollbar(selector_row, orient="vertical", command=listbox.yview)
        list_scroll.grid(row=0, column=1, sticky="ns")
        listbox.configure(yscrollcommand=list_scroll.set)

        labels = [self._entry_picker_label(entry) for _, entry in indexed_entries]
        for label in labels:
            listbox.insert(tk.END, label)

        metadata_var = tk.StringVar(value="")
        ttk.Label(shell, textvariable=metadata_var, wraplength=760).grid(row=3, column=0, sticky="w", pady=(0, 4))

        editor = tk.Text(shell, wrap="word", height=12)
        editor.grid(row=4, column=0, sticky="nsew")
        shell.rowconfigure(4, weight=1)

        editor_scroll = ttk.Scrollbar(shell, orient="vertical", command=editor.yview)
        editor_scroll.grid(row=4, column=1, sticky="ns", padx=(6, 0))
        editor.configure(yscrollcommand=editor_scroll.set)

        state = {"position": 0}

        def load_position(position: int) -> None:
            if position < 0 or position >= len(indexed_entries):
                return
            state["position"] = position
            listbox.selection_clear(0, tk.END)
            listbox.selection_set(position)
            listbox.activate(position)
            listbox.see(position)

            _, entry = indexed_entries[position]
            metadata_var.set(f"{entry.day_name} | {entry.created_at} | {entry.repo_label}")
            editor.delete("1.0", tk.END)
            editor.insert("1.0", entry.summary)

        def on_listbox_select(_event: tk.Event) -> None:
            selection = listbox.curselection()
            if not selection:
                return
            load_position(selection[0])

        def save_current_entry() -> None:
            new_summary = editor.get("1.0", "end-1c").strip()
            if not new_summary:
                messagebox.showerror("Invalid summary", "Entry summary cannot be empty.")
                return

            pos = state["position"]
            config_index, current_entry = indexed_entries[pos]
            updated_entry = EntryRecord(
                repo_label=current_entry.repo_label,
                repo_path=current_entry.repo_path,
                created_at=current_entry.created_at,
                day_name=current_entry.day_name,
                week_start_iso=current_entry.week_start_iso,
                summary=new_summary,
                diff_hash=current_entry.diff_hash,
                diff_excerpt=current_entry.diff_excerpt,
                summary_source=current_entry.summary_source,
                author=current_entry.author,
                rewritten_by=current_entry.rewritten_by,
                rewritten_at=current_entry.rewritten_at,
            )
            self.config.entries[config_index] = updated_entry
            indexed_entries[pos] = (config_index, updated_entry)

            labels[pos] = self._entry_picker_label(updated_entry)
            listbox.delete(pos)
            listbox.insert(pos, labels[pos])
            listbox.selection_clear(0, tk.END)
            listbox.selection_set(pos)

            self._persist()
            self.status_var.set(f"Updated 1 entry for {updated_entry.repo_label}")

        def go_previous() -> None:
            load_position(state["position"] - 1)

        def go_next() -> None:
            load_position(state["position"] + 1)

        listbox.bind("<<ListboxSelect>>", on_listbox_select)

        controls = ttk.Frame(shell)
        controls.grid(row=5, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(controls, text="Previous", command=go_previous).grid(row=0, column=0, padx=(0, 6))
        ttk.Button(controls, text="Next", command=go_next).grid(row=0, column=1, padx=(0, 12))
        ttk.Button(controls, text="Cancel", command=dialog.destroy).grid(row=0, column=2, padx=(0, 6))
        ttk.Button(controls, text="Save", command=save_current_entry).grid(row=0, column=3)

        load_position(0)
        editor.focus_set()

    def _refresh_editor(self) -> None:
        self.editor.delete("1.0", tk.END)
        self.editor.insert("1.0", render_plain_text(self.config) if self.config.entries else "")

    def _schedule_poll(self) -> None:
        self.root.after(self._next_poll_interval_ms(), self._scheduled_poll)

    def _schedule_enhancer_tick(self) -> None:
        self.root.after(60_000, self._scheduled_enhancer_tick)

    def _scheduled_enhancer_tick(self) -> None:
        self._run_periodic_summary_enhancer()
        self._schedule_enhancer_tick()

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

    def _resolve_summary_from_ledger(self, repo: RepoConfig, diff_text: str) -> tuple[str, bool, str, str]:
        existing = find_item(repo.label, diff_text, self._buffer_day)
        if existing is not None and existing["shown"]:
            return "", True, "Backup", HEURISTIC_MODE

        summary_text = existing["gitDiffDescription"] if existing is not None else ""
        author = existing.get("author", "Backup") if existing is not None else "Backup"
        summary_source = self._summary_mode_or_default(existing.get("summarySource", "") if existing is not None else "", author)

        if summary_text.strip() and summary_source == GITHUB_GPT_MODE:
            return summary_text, False, author, summary_source

        summary_text, author, summary_source = self._upgrade_or_create_summary(
            repo,
            diff_text,
            summary_text,
            author,
            summary_source,
        )

        return summary_text, False, author, summary_source

    def _upgrade_or_create_summary(
        self,
        repo: RepoConfig,
        diff_text: str,
        summary_text: str,
        author: str,
        summary_source: str,
    ) -> tuple[str, str, str]:
        if summary_text.strip() and summary_source == HEURISTIC_MODE and self._current_ai_mode() != HEURISTIC_MODE:
            upgraded = self._try_upgrade_heuristic_summary(repo, diff_text)
            if upgraded is not None:
                return upgraded

        if summary_text.strip():
            return summary_text, author, summary_source

        return self._create_summary_for_diff(repo, diff_text)

    def _try_upgrade_heuristic_summary(self, repo: RepoConfig, diff_text: str) -> tuple[str, str, str] | None:
        summary = self._build_summary_with_current_provider(repo.label, diff_text, repo.path)
        self._handle_ai_health(summary, repo.label)
        if summary.text.strip() and summary.source != HEURISTIC_MODE:
            return summary.text, "AI", summary.source
        return None

    def _create_summary_for_diff(self, repo: RepoConfig, diff_text: str) -> tuple[str, str, str]:
        summary = self._build_summary_with_current_provider(repo.label, diff_text, repo.path)
        self._handle_ai_health(summary, repo.label)
        summary_text = summary.text or self._build_summary_heuristic(repo.label, diff_text, repo.path)
        author = self._entry_author_from_source(summary.source, current_is_backup=True)
        summary_source = summary.source if summary.text.strip() else HEURISTIC_MODE
        return summary_text, author, summary_source

    def _initial_ai_status_text(self) -> str:
        mode = self._current_ai_mode()
        if mode == HEURISTIC_MODE:
            return "[OK] Heuristic summaries active"
        provider = self._ai_provider_label(mode)
        fallback_note = " with heuristic fallback" if self.config.ai_fallback_enabled else " without fallback"
        if mode == GITHUB_GPT_MODE:
            github_auth_status = self._github_auth_status()
            if not github_auth_status.has_token:
                return f"[SETUP] {provider} selected; {github_auth_status.detail}"
        return f"[PENDING] {provider} waiting for first response{fallback_note}"

    def _handle_ai_health(self, summary: AISummaryResult, repo_label: str) -> None:
        warning = summary.warning.strip()
        if warning:
            provider = self._ai_provider_label(summary.requested_source or summary.source)
            fallback_note = "using heuristic fallback" if summary.used_fallback else "no fallback available"
            self.status_var.set(f"{provider} unavailable for {repo_label}; {fallback_note}")
            self._ai_status_severity = "degraded"
            self.ai_status_var.set(f"//// AI DEGRADED //// {warning}")
            self._apply_theme()
            if not self._ai_backend_unavailable or warning != self._ai_last_warning:
                self._ai_backend_unavailable = True
                self._ai_last_warning = warning
            return

        if self._ai_backend_unavailable and summary.source != HEURISTIC_MODE:
            self._ai_backend_unavailable = False
            self._ai_last_warning = ""
            self._ai_status_severity = "ok"
            provider = self._ai_provider_label(summary.source)
            self.ai_status_var.set(f"[OK] Healthy: {provider} summaries active")
            self._apply_theme()
            self.status_var.set(f"AI recovered for {repo_label}; resumed {provider} summaries")
            return

        if summary.source != HEURISTIC_MODE:
            self._ai_status_severity = "ok"
            self.ai_status_var.set(f"[OK] Healthy: {self._ai_provider_label(summary.source)} summaries active")
            self._apply_theme()

    def _build_entry(
        self,
        repo: RepoConfig,
        created_at: datetime,
        summary_text: str,
        diff_text: str,
        diff_hash: str,
        author: str,
        summary_source: str,
    ) -> EntryRecord:
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
            summary_source=summary_source,
            diff_hash=diff_hash,
            diff_excerpt=diff_text[:DIFF_EXCERPT_LIMIT],
            author=author,
            rewritten_by="",
            rewritten_at="",
        )

    def _merge_or_append_captured_entry(self, candidate: EntryRecord) -> None:
        candidate_fp = self._entry_files_fingerprint(candidate)
        if not candidate_fp:
            self.config.entries.append(candidate)
            return

        for idx, existing in enumerate(self.config.entries):
            if not self._should_merge_entries(existing, candidate):
                continue
            existing_fp = self._entry_files_fingerprint(existing)
            if not self._fingerprint_overlap(candidate_fp, existing_fp):
                continue

            self.config.entries[idx] = EntryRecord(
                repo_label=candidate.repo_label,
                repo_path=candidate.repo_path,
                created_at=existing.created_at,
                day_name=existing.day_name,
                week_start_iso=existing.week_start_iso,
                summary=candidate.summary,
                summary_source=candidate.summary_source,
                diff_hash=candidate.diff_hash,
                diff_excerpt=candidate.diff_excerpt,
                author=candidate.author,
                rewritten_by=candidate.rewritten_by,
                rewritten_at=candidate.rewritten_at,
            )
            return

        self.config.entries.append(candidate)

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

        return self._capture_repo_snapshot_change(repo, snapshot)

    def _set_possible_duplicate_status(self, message: str) -> None:
        if threading.current_thread() is threading.main_thread():
            self.status_var.set(message)

    def _should_skip_captured_entry(
        self,
        repo: RepoConfig,
        file_path: str,
        file_diff_text: str,
        new_entry: EntryRecord,
    ) -> bool:
        if self._is_possible_duplicate_diff(new_entry):
            self._set_possible_duplicate_status(f"[POSSIBLE DUPLICATE] {repo.label}: duplicate git diff detected for {file_path}")
            mark_as_shown(repo.label, file_diff_text, self._buffer_day)
            return True

        if self._is_possible_carryover_duplicate(new_entry):
            self._set_possible_duplicate_status(f"[POSSIBLE DUPLICATE] {repo.label}: carry-over git diff from previous day for {file_path}")
            mark_as_shown(repo.label, file_diff_text, self._buffer_day)
            return True

        if any(self._is_duplicate_entry(entry, new_entry) for entry in self.config.entries):
            self._set_possible_duplicate_status(f"[POSSIBLE DUPLICATE] {repo.label}: duplicate git diff detected for {file_path}")
            mark_as_shown(repo.label, file_diff_text, self._buffer_day)
            return True

        return False

    def _capture_repo_snapshot_change(self, repo: RepoConfig, snapshot: DiffSnapshot) -> bool:
        captured = False

        for file_path, file_diff_text in split_diff_by_file(snapshot.diff_text):
            summary_text, already_shown, author, summary_source = self._resolve_summary_from_ledger(repo, file_diff_text)
            if already_shown:
                continue

            ledger_item = remember_diff(repo.label, repo.path, file_diff_text, summary_text, author, summary_source, self._buffer_day)
            if ledger_item["shown"]:
                continue

            new_entry = self._build_entry(
                repo,
                snapshot.created_at,
                summary_text,
                file_diff_text,
                ledger_item["diffHash"],
                ledger_item.get("author", author),
                self._summary_mode_or_default(ledger_item.get("summarySource", ""), ledger_item.get("author", author)),
            )
            if self._should_skip_captured_entry(repo, file_path, file_diff_text, new_entry):
                continue

            self._merge_or_append_captured_entry(new_entry)
            mark_as_shown(repo.label, file_diff_text, self._buffer_day)
            captured = True

        return captured

    def _poll_worker(self, repos: list[RepoConfig]) -> None:
        captured = 0
        errors: list[str] = []
        for repo in repos:
            try:
                snapshot = capture_snapshot(repo)
            except Exception as exc:
                errors.append(f"{repo.label}: {exc}")
                continue

            if self._capture_repo_snapshot_change(repo, snapshot):
                captured += 1

        self._poll_captured_count = captured
        self._poll_errors = errors

    def _check_poll_worker(self) -> None:
        if self._poll_thread is not None and self._poll_thread.is_alive():
            self.root.after(100, self._check_poll_worker)
            return
        self._finish_poll()

    def _repo_error_count_text(self, count: int) -> str:
        return f"{count} repo error{'s' if count != 1 else ''}"

    def _consume_poll_result(self, result: SnapshotCaptureResult, errors: list[str]) -> bool:
        if result.error:
            errors.append(f"{result.repo.label}: {result.error}")
            return False
        if result.snapshot is None:
            return False
        return self._capture_repo_snapshot_change(result.repo, result.snapshot)

    def _reset_poll_state(self) -> None:
        self._poll_in_flight = False
        self._poll_thread = None
        self._poll_results = []
        self._poll_captured_count = 0
        self._poll_errors = []

    def _finish_poll(self) -> None:
        captured = self._poll_captured_count
        errors = list(self._poll_errors)

        now_time = datetime.now().strftime('%H:%M:%S')
        if captured:
            self._persist()
            base = f"Captured {captured} new change summary{'ies' if captured != 1 else ''} at {now_time}"
            if errors:
                self.status_var.set(f"{base} ({self._repo_error_count_text(len(errors))})")
            else:
                self.status_var.set(base)
        elif errors:
            self.status_var.set(f"No new diffs; {self._repo_error_count_text(len(errors))} during scan")
        else:
            self.status_var.set(f"No new diffs at {now_time}")

        self._run_periodic_summary_enhancer()

        self._reset_poll_state()

    def _run_periodic_summary_enhancer(self) -> None:
        report = run_periodic_enhancer(self.config)
        if not report.ran:
            return
        if report.total_rewritten:
            mode_label = "dry-run" if report.dry_run else "live"
            self._refresh_editor()
            self.status_var.set(
                f"Summary enhancer ({mode_label}) rewrote {report.total_rewritten} entries "
                f"(state={report.rewritten_state}, buffer={report.rewritten_buffer})"
            )

    def run_poll_once(self) -> None:
        if self._poll_in_flight:
            self.status_var.set("Scan already running; waiting for completion")
            return

        self.config.week_end_day = self.week_end_var.get()
        self._ensure_active_buffer_day()
        repos = list(self.config.repos)
        if not repos:
            self.status_var.set("No repositories configured")
            return

        self._poll_in_flight = True
        self._poll_results = []
        self.status_var.set(f"Scanning {len(repos)} repos in background...")
        self._poll_thread = threading.Thread(target=self._poll_worker, args=(repos,), daemon=True)
        self._poll_thread.start()
        self.root.after(100, self._check_poll_worker)

    def _export_docx(self) -> None:
        self.config.week_end_day = self.week_end_var.get()
        self._persist()
        target = export_docx(self.config, self._docx_output_dir())
        self.status_var.set(f"Exported {target.name}")
        messagebox.showinfo("Export complete", f"Saved {target}")

    def _open_docx_folder(self) -> None:
        target_dir = self._docx_output_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(str(target_dir))
            elif sys.platform == "darwin":
                open_cmd = shutil.which("open") or "/usr/bin/open"
                completed = subprocess.run([open_cmd, str(target_dir)], check=False)
                if completed.returncode != 0:
                    webbrowser.open(target_dir.as_uri())
            else:
                completed = subprocess.run(["xdg-open", str(target_dir)], check=False)
                if completed.returncode != 0:
                    webbrowser.open(target_dir.as_uri())
        except Exception as exc:
            messagebox.showerror("Open folder failed", f"Could not open {target_dir}: {exc}")
            return
        self.status_var.set(f"Opened {target_dir}")

    def _docx_output_dir(self) -> Path:
        configured = self.config.export_directory.strip()
        return Path(configured or default_export_directory()).expanduser()


def launch() -> None:
    root = tk.Tk()
    app = PyesisApp(root)
    root.minsize(980, 640)
    app.status_var.set("Ready")
    if sys.platform == "darwin":
        root.update_idletasks()
        root.deiconify()
        root.lift()
        try:
            root.focus_force()
        except tk.TclError:
            pass
        try:
            root.attributes("-topmost", True)
            root.after(250, lambda: root.attributes("-topmost", False))
        except tk.TclError:
            pass
        try:
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    (
                        'tell application "System Events" '
                        f'to set frontmost of the first application process whose unix id is {os.getpid()} to true'
                    ),
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        except Exception:
            pass
    root.mainloop()


def main() -> None:
    launch()