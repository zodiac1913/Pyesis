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


if __name__ == "__main__":
    unittest.main()