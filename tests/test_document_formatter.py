from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import patch

from pyesis.config import AppConfig, EntryRecord
from pyesis.document_formatter import render_plain_text, render_text_chunks


class DocumentFormatterTests(unittest.TestCase):
    def test_render_plain_text_uses_configured_week_boundary_for_active_week(self) -> None:
        config = AppConfig(
            week_end_day="Thursday",
            entries=[
                EntryRecord(
                    repo_label="cms-dotnet-cats-source",
                    repo_path="/tmp/cats",
                    created_at="2026-06-29T06:37:47",
                    day_name="Monday",
                    week_start_iso="2026-06-26T00:00:00",
                    summary="I changed async flow in wwwroot/js/global/sml/Form/smlForm.js.",
                    diff_hash="hash-cats",
                    diff_excerpt="diff --git a/wwwroot/js/global/sml/Form/smlForm.js b/wwwroot/js/global/sml/Form/smlForm.js\n+++ b/wwwroot/js/global/sml/Form/smlForm.js\n",
                    summary_source="ollama",
                    author="AI",
                )
            ],
        )

        frozen_now = datetime(2026, 6, 29, 12, 0, 0)
        with patch("pyesis.document_formatter.datetime") as mock_datetime:
            mock_datetime.now.return_value = frozen_now
            mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
            output = render_plain_text(config)

        self.assertIn("cms-dotnet-cats-source", output)
        self.assertIn("I changed async flow in wwwroot/js/global/sml/Form/smlForm.js.", output)

    def test_render_plain_text_header_uses_configured_week_end_date(self) -> None:
        config = AppConfig(
            week_end_day="Thursday",
            entries=[
                EntryRecord(
                    repo_label="cms-dotnet-cats-source",
                    repo_path="/tmp/cats",
                    created_at="2026-06-29T06:37:47",
                    day_name="Monday",
                    week_start_iso="2026-06-26T00:00:00",
                    summary="I changed async flow in wwwroot/js/global/sml/Form/smlForm.js.",
                    diff_hash="hash-cats",
                    diff_excerpt="diff --git a/wwwroot/js/global/sml/Form/smlForm.js b/wwwroot/js/global/sml/Form/smlForm.js\n+++ b/wwwroot/js/global/sml/Form/smlForm.js\n",
                    summary_source="ollama",
                    author="AI",
                )
            ],
        )

        frozen_now = datetime(2026, 6, 29, 12, 0, 0)
        with patch("pyesis.document_formatter.datetime") as mock_datetime:
            mock_datetime.now.return_value = frozen_now
            mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
            output = render_plain_text(config)

        self.assertIn("(2026 Jul 02)", output)

    def test_render_text_chunks_can_show_warning_comment_with_custom_tags(self) -> None:
        entry = EntryRecord(
            repo_label="Pyesis",
            repo_path="/tmp/pyesis",
            created_at="2026-06-29T06:37:47",
            day_name="Monday",
            week_start_iso="2026-06-26T00:00:00",
            summary="I kept the existing heuristic summary.",
            diff_hash="hash-warning",
            diff_excerpt="diff --git a/a.py b/a.py\n+++ b/a.py\n",
            summary_source="heuristic",
            author="Backup",
            summary_warning="Ollama summary failed: offline",
        )
        config = AppConfig(week_end_day="Thursday", entries=[entry])

        with patch("pyesis.document_formatter.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 6, 29, 12, 0, 0)
            mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
            chunks = render_text_chunks(
                config,
                entry_tag_resolver=lambda _entry: ("ai-failed",),
                warning_comment_resolver=lambda current: f"[[{current.summary_warning}]]",
            )

        rendered_text = "".join(chunk.text for chunk in chunks)
        self.assertIn("[[Ollama summary failed: offline]]", rendered_text)
        comment_chunk = next(chunk for chunk in chunks if "[[Ollama summary failed: offline]]" in chunk.text)
        self.assertEqual(comment_chunk.tags, ("ai-failed", "ai-comment"))

    def test_render_text_chunks_adds_evidence_from_diff_when_summary_has_none(self) -> None:
        entry = EntryRecord(
            repo_label="Pyesis",
            repo_path="/tmp/pyesis",
            created_at="2026-06-29T06:37:47",
            day_name="Monday",
            week_start_iso="2026-06-26T00:00:00",
            summary="I added summary source tracking.",
            diff_hash="hash-evidence",
            diff_excerpt=(
                "diff --git a/pyesis/diff_buffer.py b/pyesis/diff_buffer.py\n"
                "+++ b/pyesis/diff_buffer.py\n"
                "@@ -1,5 +1,6 @@\n"
                " class DiffLedgerItem(TypedDict):\n"
                "+    summarySource: str\n"
                "     rewrittenBy: str\n"
            ),
            summary_source="heuristic",
            author="Backup",
        )
        config = AppConfig(week_end_day="Thursday", entries=[entry])

        with patch("pyesis.document_formatter.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 6, 29, 12, 0, 0)
            mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
            chunks = render_text_chunks(config)

        rendered_text = "".join(chunk.text for chunk in chunks)
        self.assertIn("Evidence: pyesis/diff_buffer.py:2 \"summarySource: str\"", rendered_text)

    def test_render_text_chunks_adds_before_after_for_null_check_rewrite(self) -> None:
        entry = EntryRecord(
            repo_label="Cats",
            repo_path="/tmp/cats",
            created_at="2026-06-29T06:37:47",
            day_name="Monday",
            week_start_iso="2026-06-26T00:00:00",
            summary="I added null checks and adjusted return flow in Controllers/Officials/CCXOOwnerAdminController.cs.",
            diff_hash="hash-null-check",
            diff_excerpt=(
                "diff --git a/Controllers/Officials/CCXOOwnerAdminController.cs b/Controllers/Officials/CCXOOwnerAdminController.cs\n"
                "+++ b/Controllers/Officials/CCXOOwnerAdminController.cs\n"
                "@@ -10,3 +10,3 @@\n"
                "-var variable=3*SomeVariable;\n"
                "+var variable=3*(SomeVariable.NotEmpty()?SomeVariable:1);\n"
            ),
            summary_source="heuristic",
            author="Backup",
        )
        config = AppConfig(week_end_day="Thursday", entries=[entry])

        with patch("pyesis.document_formatter.datetime") as mock_datetime:
            mock_datetime.now.return_value = datetime(2026, 6, 29, 12, 0, 0)
            mock_datetime.fromisoformat.side_effect = datetime.fromisoformat
            chunks = render_text_chunks(config)

        rendered_text = "".join(chunk.text for chunk in chunks)
        self.assertIn("Before: var variable=3*SomeVariable;", rendered_text)
        self.assertIn("After:  var variable=3*(SomeVariable.NotEmpty()?SomeVariable:1);", rendered_text)


if __name__ == "__main__":
    unittest.main()