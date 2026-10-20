from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from pyesis.config import EntryRecord, dedupe_entries, load_startup_config_snapshot


class ConfigSummaryProtectionTests(unittest.TestCase):
    def test_dedupe_keeps_existing_ai_entry_over_later_heuristic_duplicate(self) -> None:
        ai_entry = EntryRecord(
            repo_label="Pyesis",
            repo_path="/tmp/pyesis",
            created_at="2026-06-24T05:46:27",
            day_name="Wednesday",
            week_start_iso="2026-06-22T00:00:00",
            summary="I added AST-based Python-literal recovery in pyesis/ai_summary.py.",
            diff_hash="e9fd06b75a4724c1372adb03faa6a50f50fd3d12328267aaf14d6e5bfe8ed41f",
            diff_excerpt="diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n@@ -1,5 +1,6 @@\n+import ast\n",
            summary_source="ollama",
            author="AI",
            requested_summary_source="ollama",
            summary_timing_ms=105444,
            summary_provider_details="qwen2.5-coder:latest",
        )
        heuristic_entry = EntryRecord(
            repo_label="Pyesis",
            repo_path="/tmp/pyesis",
            created_at="2026-06-24T05:52:02",
            day_name="Wednesday",
            week_start_iso="2026-06-22T00:00:00",
            summary="I adjusted return flow in pyesis/ai_summary.py.",
            diff_hash="e9fd06b75a4724c1372adb03faa6a50f50fd3d12328267aaf14d6e5bfe8ed41f",
            diff_excerpt="diff --git a/pyesis/ai_summary.py b/pyesis/ai_summary.py\n+++ b/pyesis/ai_summary.py\n@@ -1,5 +1,6 @@\n+import ast\n",
            summary_source="heuristic",
            author="Backup",
            requested_summary_source="ollama",
            summary_warning="Ollama summary failed",
        )

        deduped = dedupe_entries([ai_entry, heuristic_entry])

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].summary_source, "ollama")
        self.assertEqual(deduped[0].author, "AI")
        self.assertEqual(deduped[0].summary, ai_entry.summary)

    def test_dedupe_keeps_same_day_same_file_distinct_diffs(self) -> None:
        first_entry = EntryRecord(
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
        second_entry = EntryRecord(
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

        deduped = dedupe_entries([first_entry, second_entry])

        self.assertEqual(len(deduped), 2)
        self.assertEqual(deduped[0].summary, first_entry.summary)
        self.assertEqual(deduped[1].summary, second_entry.summary)

    def test_dedupe_keeps_distinct_diff_hashes_even_with_same_summary_prefix(self) -> None:
        first_entry = EntryRecord(
            repo_label="Cats",
            repo_path="/tmp/cats",
            created_at="2026-06-29T09:00:00",
            day_name="Monday",
            week_start_iso="2026-06-26T00:00:00",
            summary="I updated wwwroot/js/global/sml/Form/smlForm.js around initValidation().",
            diff_hash="hash-one",
            diff_excerpt="diff --git a/wwwroot/js/global/sml/Form/smlForm.js b/wwwroot/js/global/sml/Form/smlForm.js\n+++ b/wwwroot/js/global/sml/Form/smlForm.js\n@@ -10 +10 @@\n+initValidation();\n",
            summary_source="ollama",
            author="AI",
        )
        second_entry = EntryRecord(
            repo_label="Cats",
            repo_path="/tmp/cats",
            created_at="2026-06-29T10:15:00",
            day_name="Monday",
            week_start_iso="2026-06-26T00:00:00",
            summary="I updated wwwroot/js/global/sml/Form/smlForm.js around bindSaveHandlers().",
            diff_hash="hash-two",
            diff_excerpt="diff --git a/wwwroot/js/global/sml/Form/smlForm.js b/wwwroot/js/global/sml/Form/smlForm.js\n+++ b/wwwroot/js/global/sml/Form/smlForm.js\n@@ -25 +25 @@\n+bindSaveHandlers();\n",
            summary_source="ollama",
            author="AI",
        )

        deduped = dedupe_entries([first_entry, second_entry])

        self.assertEqual(len(deduped), 2)
        self.assertEqual({entry.diff_hash for entry in deduped}, {"hash-one", "hash-two"})

    def test_dedupe_keeps_distinct_diffs_with_exact_same_summary(self) -> None:
        first_entry = EntryRecord(
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
        second_entry = EntryRecord(
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

        deduped = dedupe_entries([first_entry, second_entry])

        self.assertEqual([entry.diff_hash for entry in deduped], ["hash-one", "hash-two"])

    def test_dedupe_keeps_cross_day_edits_with_similar_or_truncated_excerpts(self) -> None:
        context = " unchanged context\n" * 800
        first = EntryRecord(
            repo_label="Cats",
            repo_path="/tmp/cats",
            created_at="2026-10-19T09:00:00",
            day_name="Monday",
            week_start_iso="2026-10-16T00:00:00",
            summary="I updated reporting validation.",
            diff_hash="full-diff-before",
            diff_excerpt=f"+++ b/report.cs\n{context}+old_validation();\n",
        )
        for excerpt in (first.diff_excerpt.replace("old_validation", "new_validation"), first.diff_excerpt):
            with self.subTest(identical_excerpt=excerpt == first.diff_excerpt):
                second = replace(
                    first,
                    created_at="2026-10-20T09:00:00",
                    day_name="Tuesday",
                    diff_hash="full-diff-after",
                    diff_excerpt=excerpt,
                )
                self.assertEqual(dedupe_entries([first, second]), [first, second])

    def test_dedupe_skips_identical_cross_day_diff_only_in_same_repo(self) -> None:
        first = EntryRecord(
            repo_label="Cats",
            repo_path="/tmp/cats",
            created_at="2026-10-19T09:00:00",
            day_name="Monday",
            week_start_iso="2026-10-16T00:00:00",
            summary="I updated reporting validation.",
            diff_hash="same-full-diff",
            diff_excerpt="+++ b/report.cs\n+validate();\n",
        )
        repeat = replace(first, created_at="2026-10-20T09:00:00", day_name="Tuesday")
        other_repo = replace(repeat, repo_path="/tmp/other")
        self.assertEqual(dedupe_entries([first, repeat, other_repo]), [first, other_repo])

    def test_load_startup_config_snapshot_reads_theme_and_repos_without_entries(self) -> None:
        payload = {
            "theme_mode": "dark",
            "high_contrast": True,
            "ui_font_size": 14,
            "week_end_day": "Friday",
            "repos": [
                {"path": "/tmp/pyesis", "label": "Pyesis", "poll_seconds": 90},
            ],
            "entries": [
                {
                    "repo_label": "Pyesis",
                    "repo_path": "/tmp/pyesis",
                    "created_at": "2026-08-26T10:05:00",
                    "day_name": "Tuesday",
                    "week_start_iso": "2026-08-21T00:00:00",
                    "summary": "I changed startup loading.",
                    "diff_hash": "hash-1",
                    "diff_excerpt": "diff --git a/pyesis/app.py b/pyesis/app.py\n+++ b/pyesis/app.py\n",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "pyesis_state.json"
            state_path.write_text(json.dumps(payload), encoding="utf-8")

            snapshot = load_startup_config_snapshot(state_path)

        self.assertEqual(snapshot.theme_mode, "dark")
        self.assertTrue(snapshot.high_contrast)
        self.assertEqual(snapshot.ui_font_size, 14)
        self.assertEqual(snapshot.week_end_day, "Friday")
        self.assertEqual(len(snapshot.repos), 1)
        self.assertEqual(snapshot.repos[0].label, "Pyesis")
        self.assertEqual(snapshot.entries, [])


if __name__ == "__main__":
    unittest.main()