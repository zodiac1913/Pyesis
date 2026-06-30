from __future__ import annotations

from datetime import datetime, timedelta
import unittest
from unittest.mock import patch
import threading

from pyesis.ai_summary import GITHUB_GPT_MODE, HEURISTIC_MODE, OLLAMA_MODE
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

    def after(self, delay_ms: int, callback) -> None:
        self.after_calls.append((delay_ms, callback))


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
        app.config = AppConfig(entries=[])
        app.week_end_var = DummyVar(app.config.week_end_day)
        app.status_var = DummyVar()
        app.backlog_button_var = DummyVar()
        app.summary_refresh_button_var = DummyVar()
        app.backlog_button = None
        app.summary_refresh_button = None
        app._enhancer_in_flight = False
        app._refresh_editor = lambda: None
        return app

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
                summary="I adjusted return flow in pyesis/ai_summary.py.",
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

    def test_periodic_summary_enhancer_runs_in_background_and_completes_on_ui_thread(self) -> None:
        app = self._make_app()
        app.root = DummyRoot()
        app.poll_summary_var = DummyVar()
        app._on_entry_rewrite_progress = lambda *_args: None
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


if __name__ == "__main__":
    unittest.main()