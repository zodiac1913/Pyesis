from __future__ import annotations

import unittest

from pyesis.config import EntryRecord, dedupe_entries


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

    def test_dedupe_collapses_same_day_same_file_exact_same_summary(self) -> None:
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

        self.assertEqual(len(deduped), 1)
        self.assertEqual(deduped[0].diff_hash, "hash-two")


if __name__ == "__main__":
    unittest.main()