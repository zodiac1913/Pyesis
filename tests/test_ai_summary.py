from __future__ import annotations

import unittest
from unittest.mock import patch

from pyesis.ai_summary import (
    _build_ai_user_prompt,
    _coalesce_changes,
    _ollama_model_candidates,
    _ollama_structured_summary,
    _parse_ai_json_payload,
    _structured_summary_from_json,
    build_summary,
)
from pyesis.git_monitor import summarize_file_changes


class AISummaryTests(unittest.TestCase):
    def test_heuristic_summary_uses_added_symbol_anchor(self) -> None:
        diff_text = (
            "diff --git a/pyesis/diff_buffer.py b/pyesis/diff_buffer.py\n"
            "+++ b/pyesis/diff_buffer.py\n"
            "@@ -1,5 +1,6 @@\n"
            " class DiffLedgerItem(TypedDict):\n"
            "+    summarySource: str\n"
            "     rewrittenBy: str\n"
        )

        result = build_summary("Pyesis", diff_text, repo_path="/tmp/repo", mode="heuristic")

        self.assertIn("summarySource", result.text)
        self.assertNotIn("refined logic", result.text.lower())

    def test_low_quality_ai_fields_are_repaired_from_diff(self) -> None:
        diff_text = (
            "diff --git a/pyesis/diff_buffer.py b/pyesis/diff_buffer.py\n"
            "+++ b/pyesis/diff_buffer.py\n"
            "@@ -1,5 +1,6 @@\n"
            " class DiffLedgerItem(TypedDict):\n"
            "+    summarySource: str\n"
            "     rewrittenBy: str\n"
        )
        changes = _coalesce_changes(summarize_file_changes(diff_text))

        repaired = _structured_summary_from_json(
            {
                "who": "I",
                "what": "I refined logic in pyesis/diff_buffer.py.",
                "where": "pyesis/diff_buffer.py",
                "when": "Not available from the diff.",
                "why": "clarify behavior in pyesis/diff_buffer.py",
                "how": "changing code around 'summarySource: str'",
            },
            changes,
            "Pyesis",
        )

        repaired_text = repaired.to_text()
        self.assertIn("summarySource", repaired_text)
        self.assertNotIn("refined logic", repaired_text.lower())
        self.assertNotIn("changing code around", repaired_text.lower())

    def test_ai_prompt_forbids_generic_filler_and_requires_anchor(self) -> None:
        diff_text = (
            "diff --git a/pyesis/diff_buffer.py b/pyesis/diff_buffer.py\n"
            "+++ b/pyesis/diff_buffer.py\n"
            "@@ -1,5 +1,6 @@\n"
            " class DiffLedgerItem(TypedDict):\n"
            "+    summarySource: str\n"
            "     rewrittenBy: str\n"
        )

        prompt = _build_ai_user_prompt("Pyesis", diff_text, "/tmp/repo")

        self.assertIn("name at least one concrete anchor", prompt)
        self.assertIn("Forbidden phrases", prompt)
        self.assertIn("refined logic", prompt)
        self.assertIn("summarySource", prompt)

    def test_ai_json_parser_accepts_wrapped_json(self) -> None:
        payload = _parse_ai_json_payload(
            "Here is the result:\n```json\n{\"who\":\"I\",\"what\":\"I added summarySource in pyesis/diff_buffer.py\",\"where\":\"pyesis/diff_buffer.py\",\"when\":\"Not available from the diff.\",\"why\":\"track summary source metadata\",\"how\":\"adding the summarySource field\"}\n```\nHope that helps."
        )

        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["who"], "I")
        self.assertIn("summarySource", payload["what"])

    def test_ollama_model_candidates_parse_unique_list(self) -> None:
        self.assertEqual(
            _ollama_model_candidates("qwen3-coder:30b, llama3.1:70b, qwen3-coder:30b"),
            ["qwen3-coder:30b", "llama3.1:70b"],
        )

    def test_ollama_structured_summary_falls_through_multiple_models(self) -> None:
        diff_text = (
            "diff --git a/pyesis/diff_buffer.py b/pyesis/diff_buffer.py\n"
            "+++ b/pyesis/diff_buffer.py\n"
            "@@ -1,5 +1,6 @@\n"
            " class DiffLedgerItem(TypedDict):\n"
            "+    summarySource: str\n"
            "     rewrittenBy: str\n"
        )

        def fake_request(repo_label, passed_diff, repo_path, *, url, model, keep_alive):
            del repo_label, passed_diff, repo_path, url, keep_alive
            if model == "broken-model":
                raise RuntimeError("model offline")
            return _structured_summary_from_json(
                {
                    "who": "I",
                    "what": "I added summarySource in pyesis/diff_buffer.py",
                    "where": "pyesis/diff_buffer.py",
                    "when": "Not available from the diff.",
                    "why": "track summary source metadata",
                    "how": "adding the summarySource field",
                },
                _coalesce_changes(summarize_file_changes(diff_text)),
                "Pyesis",
            )

        with patch("pyesis.ai_summary._ollama_request_structured_summary", side_effect=fake_request):
            with patch.dict(
                "os.environ",
                {
                    "PYESIS_OLLAMA_URL": "http://localhost:11434/api/chat",
                    "PYESIS_OLLAMA_MODEL": "broken-model, qwen3-coder:30b",
                    "PYESIS_OLLAMA_KEEP_ALIVE": "5m",
                },
                clear=False,
            ):
                summary = _ollama_structured_summary("Pyesis", diff_text, "/tmp/repo")

        self.assertIn("summarySource", summary.to_text())


if __name__ == "__main__":
    unittest.main()