from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from pyesis.ai_summary import (
    ProviderStructuredSummary,
    _build_ai_user_prompt,
    _coalesce_changes,
    _first_evidence_reference,
    _is_summary_excluded_path,
    _normalize_acronyms_in_text,
    _ollama_model_candidates,
    _ollama_structured_summary,
    _parse_ai_json_payload,
    _structured_summary_from_json,
    _to_past_tense,
    build_summary,
)
from pyesis.git_monitor import FileChangeSummary, summarize_changed_files, summarize_file_changes


class AISummaryTests(unittest.TestCase):
    def test_to_past_tense_converts_hardening(self) -> None:
        self.assertEqual(_to_past_tense("hardening null recovery"), "hardened null recovery")

    def test_normalize_acronyms_uppercases_words_only(self) -> None:
        text = "I updated smlAutoComplete to call oauth endpoint and align ui behavior for api responses."

        normalized = _normalize_acronyms_in_text(text)

        self.assertIn("smlAutoComplete", normalized)
        self.assertIn("OAUTH endpoint", normalized)
        self.assertIn("UI behavior", normalized)
        self.assertIn("API responses", normalized)

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
        self.assertIn("Evidence:", result.text)
        self.assertIn("pyesis/diff_buffer.py:2", result.text)
        self.assertNotIn("refined logic", result.text.lower())

    def test_summary_does_not_repeat_by_or_to_connectors(self) -> None:
        diff_text = (
            "diff --git a/pyesis/diff_buffer.py b/pyesis/diff_buffer.py\n"
            "+++ b/pyesis/diff_buffer.py\n"
            "@@ -1,3 +1,4 @@\n"
            " class DiffLedgerItem(TypedDict):\n"
            "+    summarySource: str\n"
            "     rewrittenBy: str\n"
        )
        changes = _coalesce_changes(summarize_file_changes(diff_text))

        repaired = _structured_summary_from_json(
            {
                "who": "I",
                "what": "I updated summary storage",
                "where": "pyesis/diff_buffer.py",
                "when": "Not available from the diff.",
                "why": "to keep summary metadata explicit",
                "how": "By adding summarySource",
            },
            changes,
            "Pyesis",
        )

        text = repaired.to_text().lower()
        self.assertNotIn("to to", text)
        self.assertNotIn("by by", text)

    def test_heuristic_summary_avoids_cleaning_layout_phrase_for_modified_signature(self) -> None:
        diff_text = (
            "diff --git a/Controllers/Configurer/Configs/AppConfig.cs b/Controllers/Configurer/Configs/AppConfig.cs\n"
            "+++ b/Controllers/Configurer/Configs/AppConfig.cs\n"
            "@@ -1,3 +1,3 @@\n"
            "-        public CCReport<AppFacadeDTO> ConfigApps(CCReport<AppFacadeDTO> ctx)\n"
            "+        public CfgReport<AppFacadeDTO> ConfigApps(CfgReport<AppFacadeDTO> ctx)\n"
            "         {\n"
        )

        result = build_summary("Cats", diff_text, repo_path="/tmp/cats", mode="heuristic")

        self.assertNotIn("i cleaning", result.text.lower())
        self.assertNotIn("cleaning up code layout", result.text.lower())
        self.assertNotIn("introduced configapps", result.text.lower())
        self.assertIn("ConfigApps", result.text)

    def test_heuristic_summary_avoids_short_ambiguous_symbol_anchor(self) -> None:
        diff_text = (
            "diff --git a/wwwroot/js/global/sml/Form/smlAutoComplete.js b/wwwroot/js/global/sml/Form/smlAutoComplete.js\n"
            "+++ b/wwwroot/js/global/sml/Form/smlAutoComplete.js\n"
            "@@ -10,0 +11,2 @@\n"
            "+const sac = createAutoComplete(config);\n"
            "+sac.bindEvents();\n"
        )

        result = build_summary("Cats", diff_text, repo_path="/tmp/cats", mode="heuristic")

        self.assertNotIn(" added sac ", f" {result.text.lower()} ")
        self.assertIn("smlAutoComplete.js", result.text)

    def test_heuristic_summary_ignores_whitespace_only_hunk(self) -> None:
        diff_text = (
            "diff --git a/wwwroot/css/site.css b/wwwroot/css/site.css\n"
            "+++ b/wwwroot/css/site.css\n"
            "@@ -29,20 +29,21 @@ html {\n"
            "   }\n"
            " }\n"
            "+\n"
        )

        result = build_summary("Cats", diff_text, repo_path="/tmp/cats", mode="heuristic")

        self.assertIn("Updated work in Cats.", result.text)
        self.assertNotIn("validation-required", result.text)
        self.assertNotIn("site.css", result.text)
        self.assertNotIn("Evidence:", result.text)

    def test_evidence_reference_requires_line_numbered_added_line(self) -> None:
        diff_text = (
            "diff --git a/Controllers/Configurer/Controllers/AppController.cs b/Controllers/Configurer/Controllers/AppController.cs\n"
            "+++ b/Controllers/Configurer/Controllers/AppController.cs\n"
            "@@ -40,0 +41,2 @@\n"
            "+private List<object> BuildAppsGridRows()\n"
            "+{\n"
        )

        changes = _coalesce_changes(summarize_file_changes(diff_text))
        evidence = _first_evidence_reference(changes)

        self.assertIn("AppController.cs:41", evidence)
        self.assertIn("BuildAppsGridRows", evidence)

    def test_evidence_reference_omits_line_less_sample_fallback(self) -> None:
        changes = [
            # Simulate legacy/coalesced data that only has sample text but no line-numbered added sample.
            FileChangeSummary(
                path="Controllers/Configurer/Controllers/AppController.cs",
                action="modified",
                added_lines=1,
                removed_lines=0,
                added_samples=["ControllerName = app.ControllerName ?? \"\", "],
                removed_samples=[],
                added_line_samples=[],
            )
        ]

        evidence = _first_evidence_reference(changes)

        self.assertEqual(evidence, "")

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

    def test_gerund_leading_what_clause_is_repaired(self) -> None:
        diff_text = (
            "diff --git a/Controllers/Configurer/Controllers/AppController.cs b/Controllers/Configurer/Controllers/AppController.cs\n"
            "+++ b/Controllers/Configurer/Controllers/AppController.cs\n"
            "@@ -22,1 +22,1 @@\n"
            "+if (appSec == null) { return BadRequest(); }\n"
        )
        changes = _coalesce_changes(summarize_file_changes(diff_text))

        repaired = _structured_summary_from_json(
            {
                "who": "I",
                "what": "I hardening null recovery in Controllers/Configurer/Controllers/AppController.cs",
                "where": "Controllers/Configurer/Controllers/AppController.cs",
                "when": "Not available from the diff.",
                "why": "clarify behavior",
                "how": "changing code around 'if (appSec == null) { return BadRequest(); }'",
            },
            changes,
            "Cats",
        )

        repaired_text = repaired.to_text().lower()
        self.assertNotIn("i hardening", repaired_text)

    def test_compose_description_normalizes_by_i_changed(self) -> None:
        diff_text = (
            "diff --git a/Views/Configurer/Apps.cshtml b/Views/Configurer/Apps.cshtml\n"
            "+++ b/Views/Configurer/Apps.cshtml\n"
            "@@ -100,1 +100,1 @@\n"
            "+data-api=\"/Configurer/ApiConfigurerAppsCCReportingSearch\"\n"
        )
        changes = _coalesce_changes(summarize_file_changes(diff_text))

        repaired = _structured_summary_from_json(
            {
                "who": "I",
                "what": "I updated the data-API attribute in Views/Configurer/Apps.cshtml",
                "where": "Views/Configurer/Apps.cshtml",
                "when": "Not available from the diff.",
                "why": "clarify behavior",
                "how": "I changed the value of the data-API attribute",
            },
            changes,
            "Cats",
        )

        repaired_text = repaired.to_text().lower()
        self.assertIn("by changing the value of the data-api attribute", repaired_text)
        self.assertNotIn("by i changed", repaired_text)

    def test_summary_excludes_ai_attempt_log_path(self) -> None:
        self.assertTrue(_is_summary_excluded_path("logs/ai_attempts.jsonl"))

    def test_git_monitor_excludes_ai_attempt_log_path(self) -> None:
        diff_text = (
            "diff --git a/logs/ai_attempts.jsonl b/logs/ai_attempts.jsonl\n"
            "+++ b/logs/ai_attempts.jsonl\n"
            "@@ -1 +1 @@\n"
            "+entry\n"
            "diff --git a/pyesis/app.py b/pyesis/app.py\n"
            "+++ b/pyesis/app.py\n"
            "@@ -1 +1 @@\n"
            "+change\n"
        )

        self.assertEqual(summarize_changed_files(diff_text), ["pyesis/app.py"])

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
        self.assertIn("do a grammar pass", prompt)
        self.assertIn("I hardening", prompt)
        self.assertIn("by I changed", prompt)
        self.assertIn("actual added or removed line in the diff", prompt)
        self.assertIn("Do not cite unchanged context lines", prompt)
        self.assertIn("refined logic", prompt)
        self.assertIn("summarySource", prompt)
        self.assertIn("Return exactly one JSON object and nothing else", prompt)
        self.assertIn("Use double quotes for every key and every string value", prompt)
        self.assertIn('{"who":"I","what":"I ..."', prompt)

    def test_ai_json_parser_accepts_wrapped_json(self) -> None:
        payload = _parse_ai_json_payload(
            "Here is the result:\n```json\n{\"who\":\"I\",\"what\":\"I added summarySource in pyesis/diff_buffer.py\",\"where\":\"pyesis/diff_buffer.py\",\"when\":\"Not available from the diff.\",\"why\":\"track summary source metadata\",\"how\":\"adding the summarySource field\"}\n```\nHope that helps."
        )

        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["who"], "I")
        self.assertIn("summarySource", payload["what"])

    def test_ai_json_parser_accepts_python_style_dict(self) -> None:
        payload = _parse_ai_json_payload(
            "{'who': 'I', 'what': 'I added summarySource in pyesis/diff_buffer.py', 'where': 'pyesis/diff_buffer.py', 'when': 'Not available from the diff.', 'why': 'track summary source metadata', 'how': 'adding the summarySource field'}"
        )

        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["who"], "I")
        self.assertIn("summarySource", payload["what"])

    def test_ai_json_parser_accepts_inline_labeled_payload(self) -> None:
        payload = _parse_ai_json_payload(
            "Here is your answer: who=I what=I added summarySource in pyesis/diff_buffer.py where=pyesis/diff_buffer.py when=Not available from the diff. why=track summary source metadata how=adding the summarySource field"
        )

        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["who"], "I")
        self.assertIn("summarySource", payload["what"])

    def test_ai_json_parser_accepts_multiline_labeled_payload(self) -> None:
        payload = _parse_ai_json_payload(
            "Who: I\nWhat: I added summarySource in pyesis/diff_buffer.py\nWhere: pyesis/diff_buffer.py\nWhen: Not available from the diff.\nWhy: track summary source metadata\nHow: adding the summarySource field"
        )

        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["where"], "pyesis/diff_buffer.py")
        self.assertEqual(payload["why"], "track summary source metadata")

    def test_ai_json_parser_salvages_truncated_json_object(self) -> None:
        payload = _parse_ai_json_payload(
            '{"who":"I","what":"I added summarySource in pyesis/diff_buffer.py","where":"pyesis/diff_buffer.py","when":"Not available from the diff.","why":"track summary source metadata","how":"adding the summarySource field'
        )

        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["who"], "I")
        self.assertEqual(payload["where"], "pyesis/diff_buffer.py")
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

        def fake_request(repo_label, passed_diff, repo_path, *, url, model, keep_alive, timeout):
            del repo_label, passed_diff, repo_path, url, keep_alive, timeout
            if model == "broken-model":
                raise RuntimeError("model offline")
            return ProviderStructuredSummary(
                structured=_structured_summary_from_json(
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
                ),
                timing_ms=1234,
                provider_details=model,
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

        self.assertIn("summarySource", summary.structured.to_text())

    def test_build_summary_records_ollama_timing_and_model(self) -> None:
        diff_text = (
            "diff --git a/pyesis/diff_buffer.py b/pyesis/diff_buffer.py\n"
            "+++ b/pyesis/diff_buffer.py\n"
            "@@ -1,5 +1,6 @@\n"
            " class DiffLedgerItem(TypedDict):\n"
            "+    summarySource: str\n"
            "     rewrittenBy: str\n"
        )

        response_payload = {
            "message": {
                "content": json.dumps(
                    {
                        "who": "I",
                        "what": "I added summarySource in pyesis/diff_buffer.py",
                        "where": "pyesis/diff_buffer.py",
                        "when": "Not available from the diff.",
                        "why": "track summary source metadata",
                        "how": "adding the summarySource field",
                    }
                )
            }
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(response_payload).encode("utf-8")

        with patch("pyesis.ai_summary.request.urlopen", return_value=FakeResponse()):
            with patch.dict(
                "os.environ",
                {
                    "PYESIS_OLLAMA_URL": "http://localhost:11434/api/chat",
                    "PYESIS_OLLAMA_MODEL": "qwen3-coder:30b",
                    "PYESIS_OLLAMA_KEEP_ALIVE": "5m",
                },
                clear=False,
            ):
                result = build_summary("Pyesis", diff_text, repo_path="/tmp/repo", mode="ollama")

        self.assertEqual(result.source, "ollama")
        self.assertEqual(result.provider_details, "qwen3-coder:30b")
        self.assertGreaterEqual(result.timing_ms, 0)

    def test_build_summary_uses_configured_ollama_timeout(self) -> None:
        diff_text = (
            "diff --git a/pyesis/diff_buffer.py b/pyesis/diff_buffer.py\n"
            "+++ b/pyesis/diff_buffer.py\n"
            "@@ -1,5 +1,6 @@\n"
            " class DiffLedgerItem(TypedDict):\n"
            "+    summarySource: str\n"
            "     rewrittenBy: str\n"
        )

        response_payload = {
            "message": {
                "content": json.dumps(
                    {
                        "who": "I",
                        "what": "I added summarySource in pyesis/diff_buffer.py",
                        "where": "pyesis/diff_buffer.py",
                        "when": "Not available from the diff.",
                        "why": "track summary source metadata",
                        "how": "adding the summarySource field",
                    }
                )
            }
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(response_payload).encode("utf-8")

        with patch("pyesis.ai_summary.request.urlopen", return_value=FakeResponse()) as mock_urlopen:
            with patch.dict(
                "os.environ",
                {
                    "PYESIS_OLLAMA_URL": "http://localhost:11434/api/chat",
                    "PYESIS_OLLAMA_MODEL": "qwen3-coder:30b",
                    "PYESIS_OLLAMA_KEEP_ALIVE": "5m",
                    "PYESIS_OLLAMA_TIMEOUT_SECONDS": "240",
                },
                clear=False,
            ):
                result = build_summary("Pyesis", diff_text, repo_path="/tmp/repo", mode="ollama")

        self.assertEqual(result.source, "ollama")
        self.assertEqual(mock_urlopen.call_args.kwargs["timeout"], 240)

    def test_build_summary_requests_json_mode_from_ollama(self) -> None:
        diff_text = (
            "diff --git a/pyesis/diff_buffer.py b/pyesis/diff_buffer.py\n"
            "+++ b/pyesis/diff_buffer.py\n"
            "@@ -1,5 +1,6 @@\n"
            " class DiffLedgerItem(TypedDict):\n"
            "+    summarySource: str\n"
            "     rewrittenBy: str\n"
        )

        response_payload = {
            "message": {
                "content": json.dumps(
                    {
                        "who": "I",
                        "what": "I added summarySource in pyesis/diff_buffer.py",
                        "where": "pyesis/diff_buffer.py",
                        "when": "Not available from the diff.",
                        "why": "track summary source metadata",
                        "how": "adding the summarySource field",
                    }
                )
            }
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(response_payload).encode("utf-8")

        with patch("pyesis.ai_summary.request.urlopen", return_value=FakeResponse()) as mock_urlopen:
            with patch.dict(
                "os.environ",
                {
                    "PYESIS_OLLAMA_URL": "http://localhost:11434/api/chat",
                    "PYESIS_OLLAMA_MODEL": "qwen3-coder:30b",
                    "PYESIS_OLLAMA_KEEP_ALIVE": "5m",
                },
                clear=False,
            ):
                result = build_summary("Pyesis", diff_text, repo_path="/tmp/repo", mode="ollama")

        request_payload = json.loads(mock_urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(result.source, "ollama")
        self.assertEqual(request_payload["format"], "json")
        self.assertEqual(request_payload["options"]["temperature"], 0)

    def test_build_summary_reports_ollama_content_preview_on_parse_failure(self) -> None:
        diff_text = (
            "diff --git a/pyesis/diff_buffer.py b/pyesis/diff_buffer.py\n"
            "+++ b/pyesis/diff_buffer.py\n"
            "@@ -1,5 +1,6 @@\n"
            " class DiffLedgerItem(TypedDict):\n"
            "+    summarySource: str\n"
            "     rewrittenBy: str\n"
        )

        response_payload = {
            "message": {
                "content": "The provided code snippet is from a Python module named pyesis, which appears to track git diff metadata and summarize file activity."
            }
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(response_payload).encode("utf-8")

        with patch("pyesis.ai_summary.request.urlopen", return_value=FakeResponse()):
            with patch.dict(
                "os.environ",
                {
                    "PYESIS_OLLAMA_URL": "http://localhost:11434/api/chat",
                    "PYESIS_OLLAMA_MODEL": "qwen2.5-coder:latest",
                    "PYESIS_OLLAMA_KEEP_ALIVE": "5m",
                },
                clear=False,
            ):
                result = build_summary("Pyesis", diff_text, repo_path="/tmp/repo", mode="ollama")

        self.assertEqual(result.source, "heuristic")
        self.assertIn("content preview:", result.warning)
        self.assertIn("The provided code snippet is from a Python module", result.warning)

    def test_build_summary_salvages_truncated_ollama_json_response(self) -> None:
        diff_text = (
            "diff --git a/pyesis/diff_buffer.py b/pyesis/diff_buffer.py\n"
            "+++ b/pyesis/diff_buffer.py\n"
            "@@ -1,5 +1,6 @@\n"
            " class DiffLedgerItem(TypedDict):\n"
            "+    summarySource: str\n"
            "     rewrittenBy: str\n"
        )

        response_payload = {
            "message": {
                "content": '{"who":"I","what":"I added summarySource in pyesis/diff_buffer.py","where":"pyesis/diff_buffer.py","when":"Not available from the diff.","why":"track summary source metadata","how":"adding the summarySource field'
            }
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(response_payload).encode("utf-8")

        with patch("pyesis.ai_summary.request.urlopen", return_value=FakeResponse()):
            with patch.dict(
                "os.environ",
                {
                    "PYESIS_OLLAMA_URL": "http://localhost:11434/api/chat",
                    "PYESIS_OLLAMA_MODEL": "qwen3-coder:30b",
                    "PYESIS_OLLAMA_KEEP_ALIVE": "5m",
                },
                clear=False,
            ):
                result = build_summary("Pyesis", diff_text, repo_path="/tmp/repo", mode="ollama")

        self.assertEqual(result.source, "ollama")
        self.assertEqual(result.warning, "")
        self.assertIn("summarySource", result.text)

    def test_heuristic_summary_uses_async_snippet_instead_of_async_flow_filler(self) -> None:
        diff_text = (
            "diff --git a/wwwroot/js/global/sml/Form/smlForm.js b/wwwroot/js/global/sml/Form/smlForm.js\n"
            "+++ b/wwwroot/js/global/sml/Form/smlForm.js\n"
            "@@ -40,2 +40,3 @@\n"
            "-    submitForm();\n"
            "+    await submitFormAsync(payload);\n"
            "+    toggleSubmitState(false);\n"
        )

        result = build_summary("cms-dotnet-cats-source", diff_text, repo_path="/tmp/repo", mode="heuristic")

        self.assertIn("submitFormAsync", result.text)
        self.assertNotIn("changed async flow", result.text.lower())

    def test_low_quality_async_flow_ai_summary_repairs_to_async_snippet(self) -> None:
        diff_text = (
            "diff --git a/wwwroot/js/global/sml/Form/smlToggler.js b/wwwroot/js/global/sml/Form/smlToggler.js\n"
            "+++ b/wwwroot/js/global/sml/Form/smlToggler.js\n"
            "@@ -15,2 +15,3 @@\n"
            "-    toggle();\n"
            "+    await togglePanelAsync(nextState);\n"
            "+    syncToggleButton(nextState);\n"
        )
        changes = _coalesce_changes(summarize_file_changes(diff_text))

        repaired = _structured_summary_from_json(
            {
                "who": "I",
                "what": "I changed async flow in wwwroot/js/global/sml/Form/smlToggler.js.",
                "where": "wwwroot/js/global/sml/Form/smlToggler.js",
                "when": "Not available from the diff.",
                "why": "changed async flow",
                "how": "changed async flow",
            },
            changes,
            "cms-dotnet-cats-source",
        )

        repaired_text = repaired.to_text()
        self.assertIn("togglePanelAsync", repaired_text)
        self.assertNotIn("changed async flow", repaired_text.lower())


if __name__ == "__main__":
    unittest.main()