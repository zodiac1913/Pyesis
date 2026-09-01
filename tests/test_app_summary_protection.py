from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import threading

from pyesis.ai_summary import AISummaryResult, AIWeeklyReportResult, GITHUB_GPT_MODE, HEURISTIC_MODE, OLLAMA_MODE
from pyesis.app import PyesisApp
from pyesis.config import AppConfig, EntryRecord, RepoConfig


class DummyVar:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def get(self) -> str:
        return self.value

    def set(self, value: str) -> None:
        self.value = value


class DummyRoot:
    def __init__(self) -> None:
        self.after_calls: list[tuple[int, object]] = []
        self.title_text = ""
        self.bell_count = 0
        self.deiconify_count = 0
        self.lift_count = 0
        self.focus_force_count = 0
        self.attributes_calls: list[tuple[str, object]] = []

    def after(self, delay_ms: int, callback) -> None:
        self.after_calls.append((delay_ms, callback))

    def update_idletasks(self) -> None:
        return None

    def title(self, value: str) -> None:
        self.title_text = value

    def bell(self) -> None:
        self.bell_count += 1

    def deiconify(self) -> None:
        self.deiconify_count += 1

    def lift(self) -> None:
        self.lift_count += 1

    def focus_force(self) -> None:
        self.focus_force_count += 1

    def attributes(self, key: str, value) -> None:
        self.attributes_calls.append((key, value))


class DummyEditor:
    def __init__(self) -> None:
        self.ops: list[tuple[str, str, tuple[str, ...] | None]] = []
        self.scroll_position = 0.0
        self.cursor = "xterm"
        self.tag_name_lookup: dict[str, tuple[str, ...]] = {}

    def delete(self, start: str, end: str) -> None:
        self.ops.append(("delete", "", None))

    def insert(self, _index: str, text: str, tags=None) -> None:
        if tags is None:
            normalized_tags = None
        elif isinstance(tags, tuple):
            normalized_tags = tags
        else:
            normalized_tags = tuple(tags)
        self.ops.append(("insert", text, normalized_tags))

    def update_idletasks(self) -> None:
        return None

    def yview(self) -> tuple[float, float]:
        return (self.scroll_position, 1.0)

    def yview_moveto(self, fraction: float) -> None:
        self.scroll_position = fraction

    def configure(self, **kwargs) -> None:
        if "cursor" in kwargs:
            self.cursor = kwargs["cursor"]

    def tag_configure(self, _tag: str, **_kwargs) -> None:
        return None

    def tag_bind(self, _tag: str, _sequence: str, _callback) -> None:
        return None

    def index(self, _index: str) -> str:
        return "1.0"

    def tag_names(self, index: str) -> tuple[str, ...]:
        return self.tag_name_lookup.get(index, ())

    def contents(self) -> str:
        return "".join(text for op, text, _tags in self.ops if op == "insert")


class DummyButton:
    def __init__(self) -> None:
        self.text = ""
        self.command = None
        self.state = "normal"

    def configure(self, **kwargs) -> None:
        if "text" in kwargs:
            self.text = kwargs["text"]
        if "command" in kwargs:
            self.command = kwargs["command"]
        if "state" in kwargs:
            self.state = kwargs["state"]


class ImmediateThread:
    def __init__(self, target=None, args=(), kwargs=None, daemon=None) -> None:
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self.daemon = daemon

    def start(self) -> None:
        if self._target is not None:
            self._target(*self._args, **self._kwargs)

    def is_alive(self) -> bool:
        return False


class AppSummaryProtectionTests(unittest.TestCase):
    def _make_app(self) -> PyesisApp:
        app = PyesisApp.__new__(PyesisApp)
        app.root = DummyRoot()
        app.config = AppConfig(entries=[])
        app.week_end_var = DummyVar(app.config.week_end_day)
        app.status_var = DummyVar()
        app.repo_path_var = DummyVar()
        app.repo_label_var = DummyVar()
        app.poll_seconds_var = DummyVar("120")
        app.backlog_button_var = DummyVar()
        app.summary_refresh_button_var = DummyVar()
        app.backlog_button = None
        app.summary_refresh_button = None
        app.repo_action_button = None
        app._selected_repo_index = None
        app._active_ai_entry_keys = set()
        app._ai_working_pulse_on = False
        app._enhancer_in_flight = False
        app._ai_backend_unavailable = False
        app._ai_recovery_probe_in_flight = False
        app._ai_last_warning = ""
        app._ai_status_severity = "ok"
        app._editor_scroll_initialized = False
        app._ollama_alert_visible = False
        app._ollama_alert_cycle_id = 0
        app.ollama_activity_var = DummyVar()
        app.export_week_json_button = DummyButton()
        app.import_week_json_button = DummyButton()
        app.ai_weekly_button = DummyButton()
        app.settings_button = DummyButton()
        app.startup_message_var = DummyVar()
        app.ai_status_var = DummyVar()
        app.theme_mode_var = DummyVar(app.config.theme_mode.capitalize())
        app.high_contrast_var = DummyVar(app.config.high_contrast)
        app.ui_font_size_var = DummyVar(app.config.ui_font_size)
        app._startup_loading = False
        app._file_task_in_flight = False
        app._refresh_editor = lambda: None
        return app

    def test_export_docx_exports_and_opens_document(self) -> None:
        app = self._make_app()
        opened: list[Path] = []
        app._persist = lambda: None
        app._docx_output_dir = lambda: Path("/tmp/pyesis-docx")
        app._open_created_document = lambda target: opened.append(target)

        with patch("pyesis.app.export_docx", return_value=Path("/tmp/pyesis-docx/weekly_changes_20260702_120000.docx")) as mock_export, patch(
            "pyesis.app.messagebox.showinfo"
        ) as mock_info:
            app._export_docx()

        self.assertEqual(mock_export.call_args.args[0], app.config)
        self.assertEqual(mock_export.call_args.args[1], Path("/tmp/pyesis-docx"))
        self.assertEqual(app.status_var.get(), "Exported weekly_changes_20260702_120000.docx")
        self.assertEqual(opened, [Path("/tmp/pyesis-docx/weekly_changes_20260702_120000.docx")])
        self.assertTrue(mock_info.called)

    def test_export_week_json_uses_pyesis_date_filename(self) -> None:
        app = self._make_app()
        app._docx_output_dir = lambda: Path("/tmp/pyesis-json")
        app._active_week_start = lambda: datetime(2026, 8, 20, 0, 0, 0)
        entry = EntryRecord(
            repo_label="Pyesis",
            repo_path="/tmp/pyesis",
            created_at="2026-08-24T10:05:00",
            day_name="Monday",
            week_start_iso="2026-08-20T00:00:00",
            summary="I added strict JSON output guidance in pyesis/ai_summary.py.",
            diff_hash="weekly-json",
            diff_excerpt="diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n",
            summary_source="ollama",
            author="AI",
        )
        app.config.entries = [entry]
        app._is_current_week_entry = lambda current: current is entry

        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "chosen.json"

            with patch("pyesis.app.datetime") as mock_datetime, patch("pyesis.app.filedialog.asksaveasfilename", return_value=str(target)) as mock_save, patch(
                "pyesis.app.messagebox.showinfo"
            ):
                mock_datetime.now.return_value = datetime(2026, 8, 26, 19, 15, 0)
                mock_datetime.side_effect = lambda *args, **kwargs: datetime(*args, **kwargs)
                app._export_week_json()

            self.assertEqual(mock_save.call_args.kwargs["initialfile"], "Pyesis20260826.json")
            self.assertTrue(target.exists())

    def test_export_week_json_disables_long_process_buttons_while_running(self) -> None:
        app = self._make_app()
        app._docx_output_dir = lambda: Path("/tmp/pyesis-json")
        app._active_week_start = lambda: datetime(2026, 8, 20, 0, 0, 0)
        entry = EntryRecord(
            repo_label="Pyesis",
            repo_path="/tmp/pyesis",
            created_at="2026-08-24T10:05:00",
            day_name="Monday",
            week_start_iso="2026-08-20T00:00:00",
            summary="I added strict JSON output guidance in pyesis/ai_summary.py.",
            diff_hash="weekly-json-busy",
            diff_excerpt="diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n",
            summary_source="ollama",
            author="AI",
        )
        app.config.entries = [entry]
        app._is_current_week_entry = lambda current: current is entry

        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "chosen.json"

            def fake_save_dialog(**_kwargs):
                self.assertEqual(app.export_week_json_button.state, "disabled")
                self.assertEqual(app.import_week_json_button.state, "disabled")
                self.assertEqual(app.ai_weekly_button.state, "disabled")
                self.assertEqual(app.export_week_json_button.text, "Saving Week JSON...")
                return str(target)

            with patch("pyesis.app.filedialog.asksaveasfilename", side_effect=fake_save_dialog), patch("pyesis.app.messagebox.showinfo"):
                app._export_week_json()

        self.assertEqual(app.export_week_json_button.state, "normal")
        self.assertEqual(app.import_week_json_button.state, "normal")
        self.assertEqual(app.ai_weekly_button.state, "normal")
        self.assertEqual(app.export_week_json_button.text, "Export Week JSON")

    def test_normalize_entry_calendar_fields_preserves_summary_metadata(self) -> None:
        app = self._make_app()
        app._week_start_for_datetime = lambda _moment: datetime(2026, 8, 21, 0, 0, 0)
        entry = EntryRecord(
            repo_label="RustyPythia",
            repo_path="/tmp/rusty",
            created_at="2026-08-24T05:42:26",
            day_name="Sunday",
            week_start_iso="2026-08-14T00:00:00",
            summary="I updated index.html.",
            diff_hash="hash-1",
            diff_excerpt="diff --git a/index.html b/index.html\n+++ b/index.html\n",
            summary_source="ollama",
            author="AI",
            requested_summary_source="ollama",
            summary_warning="",
            fallback_summary_source="heuristic",
            summary_timing_ms=4498,
            summary_provider_details="qwen2.5-coder:latest",
            last_ai_attempt_at="2026-08-24T06:02:31",
        )

        normalized = app._normalize_entry_calendar_fields([entry])

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0].day_name, "Monday")
        self.assertEqual(normalized[0].week_start_iso, "2026-08-21T00:00:00")
        self.assertEqual(normalized[0].summary_provider_details, "qwen2.5-coder:latest")
        self.assertEqual(normalized[0].summary_timing_ms, 4498)
        self.assertEqual(normalized[0].requested_summary_source, "ollama")
        self.assertEqual(normalized[0].fallback_summary_source, "heuristic")
        self.assertEqual(normalized[0].last_ai_attempt_at, "2026-08-24T06:02:31")

    def test_migrate_entries_skips_save_when_entries_are_already_normalized(self) -> None:
        app = self._make_app()
        app._week_start_for_datetime = lambda _moment: datetime(2026, 8, 21, 0, 0, 0)
        app.config.entries = [
            EntryRecord(
                repo_label="RustyPythia",
                repo_path="/tmp/rusty",
                created_at="2026-08-24T05:42:26",
                day_name="Monday",
                week_start_iso="2026-08-21T00:00:00",
                summary="I updated index.html.",
                diff_hash="hash-1",
                diff_excerpt="diff --git a/index.html b/index.html\n+++ b/index.html\n",
                summary_source="ollama",
                author="AI",
            )
        ]

        with patch("pyesis.app.dedupe_entries") as mock_dedupe, patch("pyesis.app.save_config") as mock_save:
            app._migrate_entries(allow_ai_rewrite=False)

        mock_dedupe.assert_not_called()
        mock_save.assert_not_called()

    def test_complete_startup_config_load_refreshes_saved_state_before_recovery(self) -> None:
        app = self._make_app()
        app._startup_loading = True
        repo_refreshes: list[str] = []
        editor_refreshes: list[str] = []
        startup_messages: list[str] = []
        scheduled: list[tuple[int, object]] = []
        app._apply_ai_environment_defaults = lambda: None
        app._initial_ai_status_severity = lambda: "ok"
        app._initial_ai_status_text = lambda: "[OK] Healthy"
        app._apply_fonts = lambda: None
        app._apply_theme = lambda: None
        app._refresh_repo_list = lambda: repo_refreshes.append("repos")
        app._refresh_editor = lambda: editor_refreshes.append("editor")
        app._set_startup_loading_message = lambda message: startup_messages.append(message)
        app.root.after = lambda delay, callback: scheduled.append((delay, callback))
        loaded = AppConfig(
            week_end_day="Friday",
            theme_mode="dark",
            high_contrast=True,
            ui_font_size=15,
            repos=[RepoConfig(path="/tmp/repo", label="Repo", poll_seconds=90)],
        )

        app._complete_startup_config_load(loaded)

        self.assertIs(app.config, loaded)
        self.assertEqual(app.week_end_var.get(), "Friday")
        self.assertEqual(app.theme_mode_var.get(), "Dark")
        self.assertTrue(app.high_contrast_var.get())
        self.assertEqual(app.ui_font_size_var.get(), 15)
        self.assertEqual(app.ai_status_var.get(), "[OK] Healthy")
        self.assertEqual(repo_refreshes, ["repos"])
        self.assertEqual(editor_refreshes, [])
        self.assertEqual(startup_messages, ["Saved settings loaded. Finishing startup..."])
        self.assertEqual(len(scheduled), 1)

    def test_open_settings_blocks_while_startup_is_loading(self) -> None:
        app = self._make_app()
        app._startup_loading = True

        with patch("pyesis.app.messagebox.showinfo") as mock_info:
            app._open_settings()

        mock_info.assert_called_once()

    def test_import_week_json_disables_long_process_buttons_while_running(self) -> None:
        app = self._make_app()
        app._active_week_start = lambda: datetime(2026, 8, 20, 0, 0, 0)
        app._persist = lambda: None

        with tempfile.TemporaryDirectory() as tmp_dir:
            source = Path(tmp_dir) / "week.json"
            payload = {
                "pyesis_week_export": True,
                "exported_at": "2026-08-26T19:15:00",
                "week_start_iso": "2026-08-20T00:00:00",
                "week_end_day": "Thursday",
                "entries": [
                    {
                        "repo_label": "Pyesis",
                        "repo_path": "/tmp/pyesis",
                        "created_at": "2026-08-24T10:05:00",
                        "day_name": "Monday",
                        "week_start_iso": "2026-08-20T00:00:00",
                        "summary": "I added strict JSON output guidance in pyesis/ai_summary.py.",
                        "diff_hash": "weekly-import",
                        "diff_excerpt": "diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n",
                        "summary_source": "ollama",
                        "author": "AI",
                    }
                ],
            }
            source.write_text(__import__("json").dumps(payload), encoding="utf-8")

            def fake_open_dialog(**_kwargs):
                self.assertEqual(app.export_week_json_button.state, "disabled")
                self.assertEqual(app.import_week_json_button.state, "disabled")
                self.assertEqual(app.ai_weekly_button.state, "disabled")
                self.assertEqual(app.import_week_json_button.text, "Importing Week JSON...")
                return str(source)

            with patch("pyesis.app.filedialog.askopenfilename", side_effect=fake_open_dialog), patch("pyesis.app.messagebox.showinfo"):
                app._import_week_json()

        self.assertEqual(app.import_week_json_button.state, "normal")
        self.assertEqual(app.import_week_json_button.text, "Import Week JSON")

    def test_ai_weekly_report_disables_long_process_buttons_while_running(self) -> None:
        app = self._make_app()
        now = datetime(2026, 6, 29, 12, 0, 0)
        entry = EntryRecord(
            repo_label="Pyesis",
            repo_path="/tmp/pyesis",
            created_at="2026-06-29T10:05:00",
            day_name="Monday",
            week_start_iso="2026-06-26T00:00:00",
            summary="I added strict JSON output guidance in pyesis/ai_summary.py.",
            diff_hash="weekly-ai-busy",
            diff_excerpt="diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n@@ -1 +1 @@\n+prompt\n",
            summary_source="ollama",
            author="AI",
        )
        app.config.entries = [entry]
        app._apply_ai_environment_defaults = lambda: None
        app._docx_output_dir = lambda: Path("/tmp/pyesis-docx")
        app._open_created_document = lambda _target: None

        def fake_build(_evidence_text, *, model_override=None):
            self.assertEqual(app.export_week_json_button.state, "disabled")
            self.assertEqual(app.import_week_json_button.state, "disabled")
            self.assertEqual(app.ai_weekly_button.state, "disabled")
            self.assertEqual(app.ai_weekly_button.text, "Generating AI Weekly...")
            self.assertEqual(model_override, "qwen3-coder:30b")
            return AIWeeklyReportResult(
                text="Monday\nPyesis\nDetailed weekly report.",
                timing_ms=1234,
                provider_details="qwen3-coder:30b",
            )

        with patch("pyesis.app.render_weekly_evidence_text", return_value="Day: Monday\nRepo: Pyesis\n- Summary: Added prompt"), patch(
            "pyesis.app.build_weekly_report",
            side_effect=fake_build,
        ), patch("pyesis.app.export_ai_weekly_report_docx", return_value=Path("/tmp/pyesis-docx/WhatIDidThisWeek20260826.docx")), patch(
            "pyesis.app.messagebox.showinfo"
        ), patch("pyesis.app.threading.Thread", ImmediateThread), patch("pyesis.app.datetime") as mock_datetime:
            mock_datetime.now.return_value = now
            mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
            app._open_ai_weekly_report()
            self.assertEqual(app.root.after_calls[-1][0], 0)
            app.root.after_calls[-1][1]()

        self.assertEqual(app.ai_weekly_button.state, "normal")
        self.assertEqual(app.ai_weekly_button.text, "AI Weekly")

    def test_ai_weekly_report_requires_current_week_entries(self) -> None:
        app = self._make_app()
        app.config.entries = [
            EntryRecord(
                repo_label="Pyesis",
                repo_path="/tmp/pyesis",
                created_at="2026-06-29T10:05:00",
                day_name="Monday",
                week_start_iso="2026-06-26T00:00:00",
                summary="I added strict JSON output guidance in pyesis/ai_summary.py.",
                diff_hash="weekly-ai-old",
                diff_excerpt="diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n@@ -1 +1 @@\n+prompt\n",
                summary_source="ollama",
                author="AI",
            )
        ]

        with patch("pyesis.app.messagebox.showinfo") as mock_info, patch("pyesis.app.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 7, 7, 12, 0, 0)
            mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
            app._open_ai_weekly_report()

        self.assertTrue(mock_info.called)
        self.assertIn("No entries found for the current week.", mock_info.call_args.args[1])

    def test_ai_weekly_report_builds_from_current_week_evidence_and_opens_document(self) -> None:
        app = self._make_app()
        now = datetime(2026, 6, 29, 12, 0, 0)
        app.config.ai_ollama_model = "qwen2.5-coder:latest"
        app.config.entries = [
            EntryRecord(
                repo_label="Pyesis",
                repo_path="/tmp/pyesis",
                created_at="2026-06-29T10:05:00",
                day_name="Monday",
                week_start_iso="2026-06-26T00:00:00",
                summary="I added strict JSON output guidance in pyesis/ai_summary.py.",
                diff_hash="weekly-ai",
                diff_excerpt="diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n@@ -1 +1 @@\n+prompt\n",
                summary_source="ollama",
                author="AI",
            )
        ]
        export_results: list[object] = []
        opened: list[Path] = []
        app._apply_ai_environment_defaults = lambda: export_results.append("env")
        app._docx_output_dir = lambda: Path("/tmp/pyesis-docx")
        app._open_created_document = lambda target: opened.append(target)

        with patch("pyesis.app.render_weekly_evidence_text", return_value="Day: Monday\nRepo: Pyesis\n- Summary: Added prompt"), patch(
            "pyesis.app.build_weekly_report",
            return_value=AIWeeklyReportResult(
                text="Monday\nPyesis\nDetailed weekly report.",
                timing_ms=1234,
                provider_details="qwen3-coder:30b",
            ),
        ) as mock_build, patch("pyesis.app.export_ai_weekly_report_docx", return_value=Path("/tmp/pyesis-docx/WhatIDidThisWeek20260826.docx")) as mock_export, patch("pyesis.app.messagebox.showinfo") as mock_info, patch("pyesis.app.threading.Thread", ImmediateThread), patch("pyesis.app.datetime") as mock_datetime:
            mock_datetime.now.return_value = now
            mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
            app._open_ai_weekly_report()
            self.assertEqual(app.root.after_calls[-1][0], 0)
            app.root.after_calls[-1][1]()

        self.assertIn("env", export_results)
        self.assertEqual(mock_build.call_args.args[0], "Day: Monday\nRepo: Pyesis\n- Summary: Added prompt")
        self.assertEqual(mock_build.call_args.kwargs["model_override"], "qwen3-coder:30b")
        self.assertEqual(mock_export.call_args.args[0], "Monday\nPyesis\nDetailed weekly report.")
        self.assertEqual(mock_export.call_args.args[1], Path("/tmp/pyesis-docx"))
        self.assertEqual(mock_export.call_args.args[2], "2026-06-26T00:00:00")
        self.assertEqual(app.status_var.get(), "AI weekly report exported to WhatIDidThisWeek20260826.docx")
        self.assertEqual(opened, [Path("/tmp/pyesis-docx/WhatIDidThisWeek20260826.docx")])
        self.assertTrue(mock_info.called)

    def test_repo_action_button_shows_add_when_path_present_without_selection(self) -> None:
        app = self._make_app()
        button = DummyButton()
        app.repo_action_button = button
        app.repo_path_var.set("/tmp/pyesis")

        app._update_repo_action_button()

        self.assertEqual(button.text, "Add Repo")

    def test_repo_action_button_shows_update_for_selected_repo(self) -> None:
        app = self._make_app()
        button = DummyButton()
        app.repo_action_button = button
        app.config.repos = [RepoConfig(path="/tmp/pyesis", label="Pyesis", poll_seconds=120)]
        app._selected_repo_index = 0

        app._update_repo_action_button()

        self.assertEqual(button.text, "Update Repo")

    def test_dead_github_mode_yields_to_live_ollama_for_status_and_order(self) -> None:
        app = self._make_app()
        app.config.ai_mode = GITHUB_GPT_MODE
        app.config.ai_ollama_url = "http://localhost:11434/api/chat"
        app.config.ai_fallback_enabled = True
        app._github_auth_status = lambda: type("Auth", (), {"has_token": False, "detail": "Not signed in"})()

        self.assertEqual(app._effective_ai_mode(), OLLAMA_MODE)
        self.assertEqual(app._preferred_summary_modes(), [OLLAMA_MODE, HEURISTIC_MODE])
        self.assertEqual(
            app._initial_ai_status_text(),
            "[PENDING] Ollama waiting for first response with heuristic fallback",
        )

    def test_no_live_external_ai_falls_back_to_heuristic_status(self) -> None:
        app = self._make_app()
        app.config.ai_mode = GITHUB_GPT_MODE
        app.config.ai_ollama_url = ""
        app.config.ai_openai_url = ""
        app._github_auth_status = lambda: type("Auth", (), {"has_token": False, "detail": "Not signed in"})()

        self.assertEqual(app._effective_ai_mode(), HEURISTIC_MODE)
        self.assertEqual(app._preferred_summary_modes(), [HEURISTIC_MODE])
        self.assertEqual(app._initial_ai_status_text(), "[OK] Heuristic summaries active")

    def test_legacy_rewrite_skips_non_heuristic_entries(self) -> None:
        app = self._make_app()
        entry = EntryRecord(
            repo_label="Pyesis",
            repo_path="/tmp/pyesis",
            created_at="2026-06-24T06:10:00",
            day_name="Wednesday",
            week_start_iso="2026-06-22T00:00:00",
            summary="I added strict JSON output guidance in pyesis/ai_summary.py.",
            diff_hash="hash-1",
            diff_excerpt="diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n@@ -1 +1 @@\n+prompt\n",
            summary_source="ollama",
            author="AI",
        )

        rewritten = app._rewrite_legacy_summaries([entry])

        self.assertEqual(len(rewritten), 1)
        self.assertEqual(rewritten[0].summary, entry.summary)
        self.assertEqual(rewritten[0].summary_source, "ollama")

    def test_heuristic_capture_does_not_overwrite_existing_ai_entry(self) -> None:
        app = self._make_app()
        existing = EntryRecord(
            repo_label="Pyesis",
            repo_path="/tmp/pyesis",
            created_at="2026-06-24T06:10:00",
            day_name="Wednesday",
            week_start_iso="2026-06-22T00:00:00",
            summary="I added strict JSON output guidance in pyesis/ai_summary.py.",
            diff_hash="hash-ai",
            diff_excerpt="diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n@@ -1 +1 @@\n+prompt\n",
            summary_source="ollama",
            author="AI",
        )
        candidate = EntryRecord(
            repo_label="Pyesis",
            repo_path="/tmp/pyesis",
            created_at="2026-06-24T06:20:00",
            day_name="Wednesday",
            week_start_iso="2026-06-22T00:00:00",
            summary="I adjusted return flow in pyesis/ai_summary.py.",
            diff_hash="hash-ai",
            diff_excerpt="diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n@@ -1 +1 @@\n+prompt\n",
            summary_source="heuristic",
            author="Backup",
        )
        app.config.entries = [existing]

        app._merge_or_append_captured_entry(candidate)

        self.assertEqual(len(app.config.entries), 1)
        self.assertEqual(app.config.entries[0].summary, existing.summary)
        self.assertEqual(app.config.entries[0].summary_source, "ollama")

    def test_current_week_heuristic_entry_count_only_counts_visible_oranges(self) -> None:
        app = self._make_app()
        now = datetime.now().replace(microsecond=0)
        previous_week = now - timedelta(days=7)
        app.config.entries = [
            EntryRecord(
                repo_label="Pyesis",
                repo_path="/tmp/pyesis",
                created_at=now.isoformat(),
                day_name=now.strftime("%A"),
                week_start_iso="",
                summary="I changed async flow in pyesis/ai_summary.py.",
                diff_hash="heuristic-now",
                diff_excerpt="diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n",
                summary_source="heuristic",
                author="Backup",
            ),
            EntryRecord(
                repo_label="Pyesis",
                repo_path="/tmp/pyesis",
                created_at=now.isoformat(),
                day_name=now.strftime("%A"),
                week_start_iso="",
                summary="I added strict JSON output guidance in pyesis/ai_summary.py.",
                diff_hash="heuristic-strong-now",
                diff_excerpt="diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n",
                summary_source="heuristic",
                author="Backup",
            ),
            EntryRecord(
                repo_label="Pyesis",
                repo_path="/tmp/pyesis",
                created_at=now.isoformat(),
                day_name=now.strftime("%A"),
                week_start_iso="",
                summary="I added strict JSON output guidance in pyesis/ai_summary.py.",
                diff_hash="ollama-now",
                diff_excerpt="diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n",
                summary_source="ollama",
                author="AI",
            ),
            EntryRecord(
                repo_label="Pyesis",
                repo_path="/tmp/pyesis",
                created_at=previous_week.isoformat(),
                day_name=previous_week.strftime("%A"),
                week_start_iso="",
                summary="I adjusted return flow in pyesis/ai_summary.py.",
                diff_hash="heuristic-old",
                diff_excerpt="diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n",
                summary_source="heuristic",
                author="Backup",
            ),
        ]

        self.assertEqual(app._current_week_heuristic_entry_count(), 1)

    def test_strong_heuristic_entry_is_not_rendered_as_orange(self) -> None:
        app = self._make_app()
        entry = EntryRecord(
            repo_label="Pyesis",
            repo_path="/tmp/pyesis",
            created_at="2026-08-03T09:00:00",
            day_name="Monday",
            week_start_iso="2026-07-31T00:00:00",
            summary="I added strict JSON output guidance in pyesis/ai_summary.py.",
            diff_hash="heuristic-strong",
            diff_excerpt="diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n",
            summary_source="heuristic",
            author="Backup",
        )

        self.assertEqual(app._entry_render_tags(entry), ())

    def test_same_day_same_file_distinct_diffs_do_not_merge(self) -> None:
        app = self._make_app()
        existing = EntryRecord(
            repo_label="Pyesis",
            repo_path="/tmp/pyesis",
            created_at="2026-06-24T09:00:00",
            day_name="Wednesday",
            week_start_iso="2026-06-22T00:00:00",
            summary="I adjusted return flow in pyesis/ai_summary.py.",
            diff_hash="hash-early",
            diff_excerpt="diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n@@ -1 +1 @@\n+parser\n",
            summary_source="heuristic",
            author="Backup",
        )
        candidate = EntryRecord(
            repo_label="Pyesis",
            repo_path="/tmp/pyesis",
            created_at="2026-06-24T10:05:00",
            day_name="Wednesday",
            week_start_iso="2026-06-22T00:00:00",
            summary="I tightened JSON parsing in pyesis/ai_summary.py.",
            diff_hash="hash-late",
            diff_excerpt="diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n@@ -1 +1 @@\n+parser\n",
            summary_source="heuristic",
            author="Backup",
        )
        app.config.entries = [existing]

        app._merge_or_append_captured_entry(candidate)

        self.assertEqual(len(app.config.entries), 2)
        self.assertEqual(app.config.entries[0].summary, existing.summary)
        self.assertEqual(app.config.entries[1].summary, candidate.summary)

    def test_same_day_same_file_exact_same_summary_merges(self) -> None:
        app = self._make_app()
        existing = EntryRecord(
            repo_label="Cats",
            repo_path="/tmp/cats",
            created_at="2026-06-30T07:18:57",
            day_name="Tuesday",
            week_start_iso="2026-06-26T00:00:00",
            summary="I cleaning up code layout in Controllers/Configurer/Configs/AppConfig.cs.",
            diff_hash="hash-one",
            diff_excerpt="diff --git a/Controllers/Configurer/Configs/AppConfig.cs b/Controllers/Configurer/Configs/AppConfig.cs\n+++ b/Controllers/Configurer/Configs/AppConfig.cs\n@@ -1 +1 @@\n+CfgReport<AppFacadeDTO> ctx\n",
            summary_source="ollama",
            author="AI",
        )
        candidate = EntryRecord(
            repo_label="Cats",
            repo_path="/tmp/cats",
            created_at="2026-06-30T07:44:57",
            day_name="Tuesday",
            week_start_iso="2026-06-26T00:00:00",
            summary="I cleaning up code layout in Controllers/Configurer/Configs/AppConfig.cs.",
            diff_hash="hash-two",
            diff_excerpt="diff --git a/Controllers/Configurer/Configs/AppConfig.cs b/Controllers/Configurer/Configs/AppConfig.cs\n+++ b/Controllers/Configurer/Configs/AppConfig.cs\n@@ -1 +1 @@\n+CfgReport<AppFacadeDTO> report\n",
            summary_source="ollama",
            author="AI",
        )
        app.config.entries = [existing]

        app._merge_or_append_captured_entry(candidate)

        self.assertEqual(len(app.config.entries), 1)
        self.assertEqual(app.config.entries[0].diff_hash, "hash-two")

    def test_next_poll_interval_respects_repo_poll_seconds_even_with_backlog(self) -> None:
        app = self._make_app()
        app.config.repos = [RepoConfig(path="/tmp/repo-a", label="RepoA", poll_seconds=120)]

        self.assertEqual(app._next_poll_interval_ms(), 120_000)

    def test_poll_enhancer_does_not_force_run_every_poll(self) -> None:
        app = self._make_app()
        app.root = DummyRoot()
        app._set_ollama_activity = lambda _message: None
        app._current_ai_mode = lambda: OLLAMA_MODE

        with patch("pyesis.app.run_periodic_enhancer", return_value=object()) as mock_run:
            report, error = app._run_poll_enhancer(lambda *_args: True)

        self.assertEqual(error, "")
        self.assertIsNotNone(report)
        self.assertTrue(mock_run.called)
        self.assertNotIn("force_run", mock_run.call_args.kwargs)

    def test_budgeted_poll_rewrite_gate_reserves_about_one_third_for_backlog(self) -> None:
        app = self._make_app()
        gate = app._make_budgeted_poll_rewrite_gate({}, 4)

        self.assertTrue(gate("RepoA", "/tmp/repo-a"))
        self.assertTrue(gate("RepoA", "/tmp/repo-a"))
        self.assertFalse(gate("RepoA", "/tmp/repo-a"))

    def test_poll_worker_never_runs_backlog_enhancer(self) -> None:
        app = self._make_app()
        app._has_current_week_ai_backlog = lambda: True
        app._capture_repo_snapshot_change = lambda _repo, _snapshot: True
        enhancer_calls: list[object] = []
        app._run_poll_enhancer = lambda _gate: enhancer_calls.append(object()) or (object(), "")

        repo = RepoConfig(path="/tmp/repo-a", label="RepoA", poll_seconds=120)

        with patch("pyesis.app.capture_snapshot", return_value=object()):
            app._poll_worker([repo])

        self.assertEqual(app._poll_captured_count, 1)
        self.assertEqual(enhancer_calls, [])
        self.assertIsNone(app._poll_enhancement_report)
        self.assertEqual(app._poll_enhancement_error, "")

    def test_capture_summary_uses_heuristic_without_calling_ai(self) -> None:
        app = self._make_app()
        app._buffer_day = "2026-08-26"
        app._build_summary_heuristic = lambda *_args: "I captured the change without waiting for AI."
        app._build_summary_with_current_provider = lambda *_args: self.fail("capture must not call the AI provider")
        repo = RepoConfig(path="/tmp/repo-a", label="RepoA")

        with patch("pyesis.app.find_item", return_value=None):
            summary, shown, author, source, metadata = app._resolve_summary_from_ledger(
                repo,
                "diff --git a/a.py b/a.py\n+++ b/a.py\n+change\n",
            )

        self.assertEqual(summary, "I captured the change without waiting for AI.")
        self.assertFalse(shown)
        self.assertEqual(author, "Backup")
        self.assertEqual(source, HEURISTIC_MODE)
        self.assertEqual(metadata["requested_summary_source"], HEURISTIC_MODE)

    def test_periodic_summary_enhancer_runs_in_background_and_completes_on_ui_thread(self) -> None:
        app = self._make_app()
        app.root = DummyRoot()
        app.poll_summary_var = DummyVar()
        app._on_entry_rewrite_progress = lambda *_args: None
        app._poll_activity_callback = lambda *_args: None
        update_calls: list[str] = []
        app._update_backlog_button = lambda: update_calls.append("button")

        report = type(
            "Report",
            (),
            {
                "ran": True,
                "dry_run": False,
                "total_rewritten": 1,
                "total_failed_marked": 0,
                "rewritten_state": 1,
                "rewritten_buffer": 0,
                "skipped_weak": 0,
                "skipped_ai_unavailable": 0,
                "skipped_gated": 0,
                "provider_timed_attempts": 0,
                "timed_attempts": 0,
                "average_provider_ms": 0,
                "average_attempt_ms": 0,
            },
        )()

        with patch("pyesis.app.run_periodic_enhancer", return_value=report), patch("pyesis.app.threading.Thread", ImmediateThread):
            started = app._run_periodic_summary_enhancer(force_run=True, update_status=True)

        self.assertTrue(started)
        self.assertTrue(app._enhancer_in_flight)
        self.assertGreaterEqual(len(app.root.after_calls), 1)

        delay_ms, callback = app.root.after_calls[-1]
        self.assertEqual(delay_ms, 0)
        callback()

        self.assertFalse(app._enhancer_in_flight)
        self.assertIn("rewrote 1 entries", app.status_var.get())
        self.assertTrue(update_calls)

    def test_successful_rewrite_immediately_queues_next_orange_item(self) -> None:
        app = self._make_app()
        app.root = DummyRoot()
        app._current_ai_mode = lambda: OLLAMA_MODE
        app._has_current_week_ai_backlog = lambda: True
        app._run_idle_backlog_enhancer = lambda: None
        report = type("Report", (), {"total_rewritten": 1, "total_failed_marked": 0})()

        app._queue_next_backlog_rewrite(report)

        self.assertEqual(app.root.after_calls, [(0, app._run_idle_backlog_enhancer)])

    def test_no_progress_does_not_tightly_retry_orange_item(self) -> None:
        app = self._make_app()
        app.root = DummyRoot()
        app._current_ai_mode = lambda: OLLAMA_MODE
        app._has_current_week_ai_backlog = lambda: True
        report = type("Report", (), {"total_rewritten": 0, "total_failed_marked": 0})()

        app._queue_next_backlog_rewrite(report)

        self.assertEqual(app.root.after_calls, [])

    def test_force_upgrade_backlog_disables_dry_run_before_running(self) -> None:
        app = self._make_app()
        app.config.summary_enhancer_dry_run = True
        app._current_ai_mode = lambda: OLLAMA_MODE
        app._current_week_heuristic_entry_count = lambda: 3
        run_calls: list[tuple[bool, bool]] = []
        app._run_periodic_summary_enhancer = lambda *, force_run, update_status: run_calls.append((force_run, update_status)) or True

        with patch("pyesis.app.save_config") as mock_save:
            result = app._force_upgrade_heuristic_backlog()

        self.assertEqual(result, "break")
        self.assertFalse(app.config.summary_enhancer_dry_run)
        self.assertEqual(run_calls, [(True, True)])
        self.assertTrue(mock_save.called)

    def test_queue_startup_poll_schedules_immediate_poll_when_repos_exist(self) -> None:
        app = self._make_app()
        app.config.repos = [RepoConfig(path="/tmp/repo-a", label="RepoA", poll_seconds=120)]
        app.root = DummyRoot()
        app.run_poll_once = lambda: None

        app._queue_startup_poll()

        self.assertEqual(len(app.root.after_calls), 1)
        delay_ms, callback = app.root.after_calls[0]
        self.assertEqual(delay_ms, 0)
        self.assertIs(callback, app.run_poll_once)

    def test_queue_startup_poll_skips_when_no_repos_exist(self) -> None:
        app = self._make_app()
        app.root = DummyRoot()
        app.run_poll_once = lambda: None

        app._queue_startup_poll()

        self.assertEqual(app.root.after_calls, [])

    def test_refresh_editor_does_not_show_previous_week_when_current_week_is_empty(self) -> None:
        app = self._make_app()
        app.editor = DummyEditor()
        app._last_rendered_week_start_iso = ""
        app._update_backlog_button = lambda: None
        app._entry_render_tags = lambda _entry: ()
        app._entry_warning_comment = lambda _entry: ""
        app.config.entries = [
            EntryRecord(
                repo_label="Pyesis",
                repo_path="/tmp/pyesis",
                created_at="2026-06-29T10:05:00",
                day_name="Monday",
                week_start_iso="2026-06-26T00:00:00",
                summary="I fixed the previous week issue.",
                diff_hash="old-week",
                diff_excerpt="diff --git a/file b/file\n+++ b/file\n",
                summary_source="heuristic",
                author="Backup",
            )
        ]

        frozen_now = datetime(2026, 7, 7, 12, 0, 0)
        with patch("pyesis.app.datetime") as mock_app_datetime, patch("pyesis.document_formatter.datetime") as mock_formatter_datetime:
            mock_app_datetime.now.return_value = frozen_now
            mock_app_datetime.fromisoformat.side_effect = datetime.fromisoformat
            mock_formatter_datetime.now.return_value = frozen_now
            mock_formatter_datetime.fromisoformat.side_effect = datetime.fromisoformat
            PyesisApp._refresh_editor(app)

        rendered = app.editor.contents()
        self.assertIn("(2026 Jul 09)", rendered)
        self.assertIn("No captured code changes for this week yet.", rendered)
        self.assertNotIn("Showing your most recent captured week", rendered)
        self.assertNotIn("I fixed the previous week issue.", rendered)

    def test_delete_entry_by_key_removes_entry_after_confirmation(self) -> None:
        app = self._make_app()
        app.editor = DummyEditor()
        refresh_calls: list[str] = []
        app._refresh_editor = lambda: refresh_calls.append("refresh")
        entry = EntryRecord(
            repo_label="Cats",
            repo_path="/tmp/cats",
            created_at="2026-08-31T07:52:11",
            day_name="Monday",
            week_start_iso="2026-08-28T00:00:00",
            summary="I removed Infrastructure/Models/CatsCRUDL/ccQuery.cs.",
            diff_hash="delete-hash",
            diff_excerpt="diff --git a/Infrastructure/Models/CatsCRUDL/ccQuery.cs b/Infrastructure/Models/CatsCRUDL/ccQuery.cs\n+++ b/Infrastructure/Models/CatsCRUDL/ccQuery.cs\n",
            summary_source="ollama",
            author="AI",
        )
        app.config.entries = [entry]

        with patch("pyesis.app.messagebox.askyesno", return_value=True) as mock_confirm, patch("pyesis.app.save_config") as mock_save:
            deleted = app._delete_entry_by_key(app._entry_status_key(entry))

        self.assertTrue(deleted)
        self.assertEqual(app.config.entries, [])
        self.assertEqual(refresh_calls, ["refresh"])
        self.assertEqual(app.status_var.get(), "Deleted 1 entry for Cats")
        self.assertTrue(mock_confirm.called)
        self.assertTrue(mock_save.called)

    def test_delete_entry_by_key_keeps_entry_when_confirmation_declined(self) -> None:
        app = self._make_app()
        entry = EntryRecord(
            repo_label="Cats",
            repo_path="/tmp/cats",
            created_at="2026-08-31T07:52:11",
            day_name="Monday",
            week_start_iso="2026-08-28T00:00:00",
            summary="I removed Infrastructure/Models/CatsCRUDL/ccQuery.cs.",
            diff_hash="delete-hash-2",
            diff_excerpt="diff --git a/Infrastructure/Models/CatsCRUDL/ccQuery.cs b/Infrastructure/Models/CatsCRUDL/ccQuery.cs\n+++ b/Infrastructure/Models/CatsCRUDL/ccQuery.cs\n",
            summary_source="ollama",
            author="AI",
        )
        app.config.entries = [entry]

        with patch("pyesis.app.messagebox.askyesno", return_value=False) as mock_confirm, patch("pyesis.app.save_config") as mock_save:
            deleted = app._delete_entry_by_key(app._entry_status_key(entry))

        self.assertFalse(deleted)
        self.assertEqual(app.config.entries, [entry])
        self.assertTrue(mock_confirm.called)
        self.assertFalse(mock_save.called)

    def test_entry_warning_comment_and_progress_tracking(self) -> None:
        app = self._make_app()
        app.root = DummyRoot()
        app._active_ai_entry_keys = set()
        app._ai_working_pulse_on = False
        app._ai_working_pulse_scheduled = False
        refresh_calls: list[str] = []
        app._refresh_editor = lambda: refresh_calls.append("refresh")

        entry = EntryRecord(
            repo_label="Pyesis",
            repo_path="/tmp/pyesis",
            created_at="2026-06-24T06:10:00",
            day_name="Wednesday",
            week_start_iso="2026-06-22T00:00:00",
            summary="I added strict JSON output guidance in pyesis/ai_summary.py.",
            diff_hash="hash-1",
            diff_excerpt="diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n@@ -1 +1 @@\n+prompt\n",
            summary_source="heuristic",
            author="Backup",
            summary_warning="Ollama summary failed: offline",
        )

        self.assertEqual(app._entry_warning_comment(entry), "[[Ollama summary failed: offline]]")
        self.assertEqual(app._entry_render_tags(entry), ("ai-failed",))

        entry_key = app._entry_status_key(entry)
        app._on_entry_rewrite_progress(entry_key, "start")
        self.assertEqual(app.root.after_calls[0][0], 0)
        app.root.after_calls[0][1]()

        self.assertIn(entry_key, app._active_ai_entry_keys)
        self.assertTrue(refresh_calls)

    def test_failed_ai_fallback_renders_as_orange_retry_without_inline_error(self) -> None:
        app = self._make_app()
        entry = EntryRecord(
            repo_label="Cats",
            repo_path="/tmp/cats",
            created_at="2026-08-31T09:00:55",
            day_name="Sunday",
            week_start_iso="2026-08-28T00:00:00",
            summary="I added configuredReport in Controllers/Officials/CCXOController.cs.",
            diff_hash="failed-gemma",
            diff_excerpt="diff --git a/a.cs b/a.cs\n+++ b/a.cs\n+configuredReport\n",
            summary_source="heuristic",
            author="Backup",
            requested_summary_source="ollama",
            summary_warning="Ollama summary failed: malformed JSON",
            fallback_summary_source="heuristic",
        )

        self.assertTrue(app._is_visible_heuristic_entry(entry))
        self.assertEqual(app._entry_render_tags(entry), ("heuristic",))
        self.assertEqual(app._entry_warning_comment(entry), "")

    def test_refresh_current_week_weak_summaries_rewrites_low_quality_ai_entry(self) -> None:
        app = self._make_app()
        app._build_summary_heuristic = lambda *_args: "I updated wwwroot/js/global/sml/Form/smlToggler.js around 'await togglePanelAsync(nextState);'."
        now = datetime(2026, 6, 29, 12, 0, 0)
        app.config.entries = [
            EntryRecord(
                repo_label="cms-dotnet-cats-source",
                repo_path="/tmp/cats",
                created_at="2026-06-29T10:05:00",
                day_name="Monday",
                week_start_iso="2026-06-26T00:00:00",
                summary="I changed async flow in wwwroot/js/global/sml/Form/smlToggler.js.",
                diff_hash="weak-ai",
                diff_excerpt="diff --git a/wwwroot/js/global/sml/Form/smlToggler.js b/wwwroot/js/global/sml/Form/smlToggler.js\n+++ b/wwwroot/js/global/sml/Form/smlToggler.js\n@@ -1 +1 @@\n+await togglePanelAsync(nextState);\n",
                summary_source="ollama",
                author="AI",
                requested_summary_source="ollama",
                summary_warning="old warning",
                fallback_summary_source="heuristic",
                summary_timing_ms=150,
                summary_provider_details="qwen3-coder:30b",
            )
        ]

        with patch("pyesis.app.datetime") as mock_datetime, patch("pyesis.app.save_config") as mock_save:
            mock_datetime.now.return_value = now
            mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
            result = app._refresh_current_week_weak_summaries()

        self.assertEqual(result, "break")
        self.assertEqual(app.config.entries[0].summary_source, "heuristic")
        self.assertEqual(app.config.entries[0].author, "Backup")
        self.assertEqual(app.config.entries[0].requested_summary_source, "heuristic")
        self.assertEqual(app.config.entries[0].summary_warning, "")
        self.assertIn("togglePanelAsync", app.config.entries[0].summary)
        self.assertIn("Refreshed 1 current-week weak summary", app.status_var.get())
        self.assertTrue(mock_save.called)

    def test_refresh_current_week_weak_summaries_skips_manual_and_strong_entries(self) -> None:
        app = self._make_app()
        now = datetime(2026, 6, 29, 12, 0, 0)
        manual_entry = EntryRecord(
            repo_label="cms-dotnet-cats-source",
            repo_path="/tmp/cats",
            created_at="2026-06-29T09:00:00",
            day_name="Monday",
            week_start_iso="2026-06-26T00:00:00",
            summary="I manually documented the toggler change.",
            diff_hash="manual",
            diff_excerpt="diff --git a/file b/file\n",
            summary_source="manual",
            author="Manual",
        )
        strong_ai_entry = EntryRecord(
            repo_label="cms-dotnet-cats-source",
            repo_path="/tmp/cats",
            created_at="2026-06-29T09:15:00",
            day_name="Monday",
            week_start_iso="2026-06-26T00:00:00",
            summary="I added togglePanelAsync in wwwroot/js/global/sml/Form/smlToggler.js.",
            diff_hash="strong-ai",
            diff_excerpt="diff --git a/file b/file\n",
            summary_source="ollama",
            author="AI",
        )
        app.config.entries = [manual_entry, strong_ai_entry]

        with patch("pyesis.app.datetime") as mock_datetime, patch("pyesis.app.save_config") as mock_save:
            mock_datetime.now.return_value = now
            mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
            result = app._refresh_current_week_weak_summaries()

        self.assertEqual(result, "break")
        self.assertEqual(app.config.entries[0].summary, manual_entry.summary)
        self.assertEqual(app.config.entries[1].summary, strong_ai_entry.summary)
        self.assertEqual(app.status_var.get(), "No current-week weak summaries to refresh")
        self.assertFalse(mock_save.called)

    def test_manual_entries_are_treated_as_trusted_final_entries(self) -> None:
        app = self._make_app()
        entry = EntryRecord(
            repo_label="Pyesis",
            repo_path="/tmp/pyesis",
            created_at="2026-07-21T09:00:00",
            day_name="Tuesday",
            week_start_iso="2026-07-17T00:00:00",
            summary="I clarified the writeup manually.",
            diff_hash="manual-1",
            diff_excerpt="diff --git a/pyesis/app.py b/pyesis/app.py\n+++ b/pyesis/app.py\n",
            summary_source="manual",
            author="Manual",
        )

        self.assertTrue(app._is_trusted_ai_entry(entry))
        self.assertEqual(app._entry_render_tags(entry), ())

    def test_ollama_alert_pulse_starts_on_ai_degraded_and_stops_on_recovery(self) -> None:
        app = self._make_app()
        app.root = DummyRoot()
        app._apply_theme = lambda: None
        app._app_version = lambda: "test"
        app._current_ai_mode = lambda: OLLAMA_MODE
        app.ai_status_var = DummyVar()

        degraded = AISummaryResult(
            text="heuristic fallback",
            source=HEURISTIC_MODE,
            requested_source=OLLAMA_MODE,
            warning="Ollama summary failed: offline",
            fallback_source=HEURISTIC_MODE,
        )

        app._handle_ai_health(degraded, "Cats")

        self.assertTrue(app._ai_backend_unavailable)
        self.assertTrue(app._ollama_alert_visible)
        self.assertEqual(app.root.title_text, "[OLLAMA DOWN] Pyesis vtest")
        self.assertEqual(app.root.bell_count, 1)
        self.assertEqual(app.root.deiconify_count, 0)
        self.assertEqual(app.root.lift_count, 0)
        self.assertEqual(app.root.focus_force_count, 0)
        self.assertEqual(app.root.attributes_calls, [])
        self.assertEqual(app.root.after_calls[-1][0], 5000)

        app.root.after_calls[-1][1]()

        self.assertFalse(app._ollama_alert_visible)
        self.assertEqual(app.root.title_text, "Pyesis vtest")
        self.assertEqual(app.root.after_calls[-1][0], 15000)

        recovered = AISummaryResult(text="AI summary", source=OLLAMA_MODE)
        app._handle_ai_health(recovered, "Cats")

        self.assertFalse(app._ai_backend_unavailable)
        self.assertFalse(app._ollama_alert_visible)
        self.assertEqual(app.root.title_text, "Pyesis vtest")

    def test_non_ollama_degraded_state_does_not_start_ollama_alert_pulse(self) -> None:
        app = self._make_app()
        app.root = DummyRoot()
        app._apply_theme = lambda: None
        app._app_version = lambda: "test"
        app._current_ai_mode = lambda: GITHUB_GPT_MODE
        app.ai_status_var = DummyVar()

        degraded = AISummaryResult(
            text="heuristic fallback",
            source=HEURISTIC_MODE,
            requested_source=GITHUB_GPT_MODE,
            warning="GitHub GPT unavailable",
            fallback_source=HEURISTIC_MODE,
        )

        app._handle_ai_health(degraded, "Pyesis")

        self.assertTrue(app._ai_backend_unavailable)
        self.assertFalse(app._ollama_alert_visible)
        self.assertEqual(app.root.after_calls, [])

    def test_missing_summary_source_defaults_to_heuristic_not_current_ai_mode(self) -> None:
        app = self._make_app()
        app.config.ai_mode = GITHUB_GPT_MODE

        self.assertEqual(app._summary_mode_or_default("", "AI"), HEURISTIC_MODE)

    def test_recover_shown_buffer_entries_rehydrates_missing_state_entry(self) -> None:
        app = self._make_app()
        shown_item = {
            "datetime": "2026-07-21T09:02:51",
            "repo": "Cats",
            "gitDiffText": "diff --git a/wwwroot/compliance-bookmarklet.html b/wwwroot/compliance-bookmarklet.html\n+++ b/wwwroot/compliance-bookmarklet.html\n@@ -0,0 +1 @@\n+<!doctype html>\n",
            "gitDiffDescription": "I added bookmarklet installer markup in wwwroot/compliance-bookmarklet.html.",
            "shown": True,
            "diffHash": "recover-hash-1",
            "repoPath": "/Users/rxjr/Desktop/Dev/cms-dotnet-cats-source",
            "author": "AI",
            "summarySource": "ollama",
            "rewrittenBy": "",
            "rewrittenAt": "",
            "requestedSummarySource": "ollama",
            "summaryWarning": "",
            "fallbackSummarySource": "",
            "summaryTimingMs": 1200,
            "summaryProviderDetails": "qwen2.5-coder:latest",
            "lastAiAttemptAt": "2026-07-21T09:02:51",
        }

        with patch("pyesis.app.list_buffer_day_keys", return_value=["2026-07-21"]), patch(
            "pyesis.app.load_buffer_items",
            return_value=[shown_item],
        ), patch("pyesis.app.save_config") as mock_save:
            recovered = app._recover_shown_buffer_entries()

        self.assertEqual(recovered, 1)
        self.assertEqual(len(app.config.entries), 1)
        self.assertEqual(app.config.entries[0].diff_hash, "recover-hash-1")
        self.assertEqual(app.config.entries[0].summary_source, "ollama")
        self.assertTrue(mock_save.called)


if __name__ == "__main__":
    unittest.main()