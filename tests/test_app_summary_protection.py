from __future__ import annotations

from datetime import datetime, timedelta
import unittest
from unittest.mock import patch

from pyesis.ai_summary import OLLAMA_MODE
from pyesis.app import PyesisApp
from pyesis.config import AppConfig, EntryRecord, RepoConfig


class AppSummaryProtectionTests(unittest.TestCase):
    def _make_app(self) -> PyesisApp:
        app = PyesisApp.__new__(PyesisApp)
        app.config = AppConfig(entries=[])
        return app

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
            diff_hash="hash-heuristic",
            diff_excerpt="diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n@@ -1 +1 @@\n+parser\n",
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

    def test_same_day_heuristic_series_merges_even_after_twenty_minutes(self) -> None:
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

        self.assertEqual(len(app.config.entries), 1)
        self.assertEqual(app.config.entries[0].summary, candidate.summary)
        self.assertEqual(app.config.entries[0].created_at, existing.created_at)

    def test_next_poll_interval_respects_repo_poll_seconds_even_with_backlog(self) -> None:
        app = self._make_app()
        app.config.repos = [RepoConfig(path="/tmp/repo-a", label="RepoA", poll_seconds=120)]

        self.assertEqual(app._next_poll_interval_ms(), 120_000)

    def test_poll_enhancer_does_not_force_run_every_poll(self) -> None:
        app = self._make_app()
        app._set_ollama_activity = lambda _message: None
        app._current_ai_mode = lambda: OLLAMA_MODE

        with patch("pyesis.app.run_periodic_enhancer", return_value=object()) as mock_run:
            report, error = app._run_poll_enhancer(lambda *_args: True)

        self.assertEqual(error, "")
        self.assertIsNotNone(report)
        self.assertTrue(mock_run.called)
        self.assertNotIn("force_run", mock_run.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()