from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
import os
from unittest.mock import patch

from pyesis.ai_summary import AISummaryResult
from pyesis.config import AppConfig, EntryRecord
from pyesis.summary_enhancer import _build_summary_for_mode, _preferred_summary_modes, run_periodic_enhancer


class SummaryEnhancerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp_dir_ctx = tempfile.TemporaryDirectory()
        self._tmp_dir = Path(self._tmp_dir_ctx.name)
        self._state_path = self._tmp_dir / "pyesis_state.json"
        self._patch_config_state_path = patch("pyesis.config.STATE_PATH", self._state_path)
        self._patch_enhancer_state_path = patch("pyesis.summary_enhancer.STATE_PATH", self._state_path)
        self._patch_config_state_path.start()
        self._patch_enhancer_state_path.start()

    def tearDown(self) -> None:
        self._patch_enhancer_state_path.stop()
        self._patch_config_state_path.stop()
        self._tmp_dir_ctx.cleanup()

    def _base_config(self) -> AppConfig:
        return AppConfig(
            ai_mode="heuristic",
            ai_fallback_enabled=True,
            summary_enhancer_enabled=True,
            summary_enhancer_interval_minutes=1,
            summary_enhancer_dry_run=True,
            summary_enhancer_last_run_at="",
            summary_enhancer_rewritten_by="EnhancerTest",
            entries=[],
        )

    def test_selected_mode_stays_ahead_of_github_token(self) -> None:
        config = self._base_config()
        config.ai_mode = "ollama"
        config.ai_ollama_model = "qwen3-coder:30b"

        with patch.dict(os.environ, {"PYESIS_GITHUB_GPT_API_KEY": "token"}, clear=False):
            modes = _preferred_summary_modes(config)

        self.assertGreaterEqual(len(modes), 2)
        self.assertEqual(modes[0], "ollama")
        self.assertEqual(modes[1], "github-gpt")

    def test_ai_mode_passes_fallback_flag_to_provider(self) -> None:
        config = self._base_config()
        config.ai_mode = "ollama"

        with patch("pyesis.summary_enhancer.build_summary") as mock_build_summary:
            mock_build_summary.return_value = AISummaryResult(text="Used AI", source="ollama")

            _build_summary_for_mode(config, "RepoA", "diff --git a/a.py b/a.py", "repos/repo-a", "ollama")

        self.assertTrue(mock_build_summary.called)
        self.assertTrue(mock_build_summary.call_args.kwargs["allow_fallback"])

    def test_dry_run_does_not_write_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            state_path = tmp / "pyesis_state.json"
            buffer_dir = tmp / "diff_buffers"
            buffer_dir.mkdir(parents=True, exist_ok=True)
            buffer_path = buffer_dir / "2026-06-16.json"

            diff_text = "diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+print('x')\n"
            buffer_path.write_text(
                json.dumps(
                    [
                        {
                            "datetime": "2026-06-16T12:00:00",
                            "repo": "RepoA",
                            "gitDiffText": diff_text,
                            "gitDiffDescription": "made updates",
                            "shown": False,
                            "diffHash": "abc123",
                            "repoPath": "repos/repo-a",
                            "author": "Backup",
                            "summarySource": "heuristic",
                        }
                    ],
                    indent=2,
                ),
                encoding="utf-8",
            )

            entry = EntryRecord(
                repo_label="RepoA",
                repo_path="repos/repo-a",
                created_at="2026-06-16T12:00:00",
                day_name="Monday",
                week_start_iso="2026-06-15T00:00:00",
                summary="made updates",
                diff_hash="abc123",
                diff_excerpt=diff_text,
                summary_source="heuristic",
                author="Backup",
            )

            config = self._base_config()
            config.entries = [entry]
            config.summary_enhancer_dry_run = True

            report = run_periodic_enhancer(
                config,
                summary_builder=lambda _repo, _diff, _path: "Added validation around null appSec payload and simplified role mapping.",
                state_path=state_path,
                buffer_dir=buffer_dir,
                now=datetime(2026, 6, 16, 12, 30, 0),
            )

            self.assertTrue(report.ran)
            self.assertTrue(report.dry_run)
            self.assertEqual(report.total_rewritten, 2)
            self.assertEqual(config.entries[0].summary, "made updates")
            self.assertEqual(config.entries[0].rewritten_at, "")

            updated_items = json.loads(buffer_path.read_text(encoding="utf-8"))
            self.assertEqual(updated_items[0]["gitDiffDescription"], "made updates")
            self.assertEqual(updated_items[0].get("rewrittenAt", ""), "")

    def test_live_run_rewrites_only_description_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            state_path = tmp / "pyesis_state.json"
            buffer_dir = tmp / "diff_buffers"
            buffer_dir.mkdir(parents=True, exist_ok=True)
            buffer_path = buffer_dir / "2026-06-16.json"

            diff_text = "diff --git a/b.py b/b.py\n+++ b/b.py\n@@ -0,0 +1 @@\n+print('y')\n"
            original_hash = "deadbeef"
            buffer_path.write_text(
                json.dumps(
                    [
                        {
                            "datetime": "2026-06-16T13:00:00",
                            "repo": "RepoB",
                            "gitDiffText": diff_text,
                            "gitDiffDescription": "refined logic",
                            "shown": False,
                            "diffHash": original_hash,
                            "repoPath": "repos/repo-b",
                            "author": "Backup",
                            "summarySource": "heuristic",
                        }
                    ],
                    indent=2,
                ),
                encoding="utf-8",
            )

            weak_entry = EntryRecord(
                repo_label="RepoB",
                repo_path="repos/repo-b",
                created_at="2026-06-16T13:00:00",
                day_name="Monday",
                week_start_iso="2026-06-15T00:00:00",
                summary="refined logic",
                diff_hash=original_hash,
                diff_excerpt=diff_text,
                summary_source="heuristic",
                author="Backup",
            )
            human_entry = EntryRecord(
                repo_label="RepoC",
                repo_path="repos/repo-c",
                created_at="2026-06-16T13:00:00",
                day_name="Monday",
                week_start_iso="2026-06-15T00:00:00",
                summary="made updates",
                diff_hash="facefeed",
                diff_excerpt=diff_text,
                summary_source="human",
                author="Human",
            )

            config = self._base_config()
            config.entries = [weak_entry, human_entry]
            config.summary_enhancer_dry_run = False

            with patch("pyesis.config.STATE_PATH", state_path):
                report = run_periodic_enhancer(
                    config,
                    summary_builder=lambda _repo, _diff, _path: "Removed obsolete DAM compile exclusion and aligned project build behavior.",
                    state_path=state_path,
                    buffer_dir=buffer_dir,
                    now=datetime(2026, 6, 16, 13, 30, 0),
                )

            self.assertTrue(report.ran)
            self.assertFalse(report.dry_run)
            self.assertEqual(report.rewritten_state, 1)
            self.assertEqual(report.rewritten_buffer, 1)
            self.assertGreaterEqual(report.skipped_human, 1)

            self.assertIn("Removed obsolete DAM compile exclusion", config.entries[0].summary)
            self.assertEqual(config.entries[0].diff_hash, original_hash)
            self.assertEqual(config.entries[0].diff_excerpt, diff_text)
            self.assertEqual(config.entries[0].summary_source, "heuristic")
            self.assertEqual(config.entries[0].rewritten_by, "EnhancerTest")
            self.assertTrue(config.entries[0].rewritten_at)

            self.assertEqual(config.entries[1].summary_source, "human")
            self.assertEqual(config.entries[1].summary, "made updates")

            updated_items = json.loads(buffer_path.read_text(encoding="utf-8"))
            self.assertEqual(updated_items[0]["gitDiffText"], diff_text)
            self.assertEqual(updated_items[0]["diffHash"], original_hash)
            self.assertIn("DAM compile exclusion", updated_items[0]["gitDiffDescription"])
            self.assertEqual(updated_items[0]["summarySource"], "heuristic")
            self.assertEqual(updated_items[0]["rewrittenBy"], "EnhancerTest")
            self.assertTrue(updated_items[0]["rewrittenAt"])

    def test_non_weak_heuristic_entries_upgrade_to_ollama_when_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            buffer_dir = tmp / "diff_buffers"
            buffer_dir.mkdir(parents=True, exist_ok=True)
            buffer_path = buffer_dir / "2026-06-16.json"

            diff_text = "diff --git a/f.py b/f.py\n+++ b/f.py\n@@ -0,0 +1 @@\n+print('force-ai')\n"
            buffer_path.write_text(
                json.dumps(
                    [
                        {
                            "datetime": "2026-06-16T12:00:00",
                            "repo": "RepoForceAI",
                            "gitDiffText": diff_text,
                            "gitDiffDescription": "Implemented deterministic startup initialization with explicit focus and launch sequencing to reduce race conditions.",
                            "shown": False,
                            "diffHash": "forcehash",
                            "repoPath": "repos/repo-force-ai",
                            "author": "Backup",
                            "summarySource": "heuristic",
                        }
                    ],
                    indent=2,
                ),
                encoding="utf-8",
            )

            entry = EntryRecord(
                repo_label="RepoForceAI",
                repo_path="repos/repo-force-ai",
                created_at="2026-06-16T12:00:00",
                day_name="Monday",
                week_start_iso="2026-06-15T00:00:00",
                summary="Implemented deterministic startup initialization with explicit focus and launch sequencing to reduce race conditions.",
                diff_hash="forcehash",
                diff_excerpt=diff_text,
                summary_source="heuristic",
                author="Backup",
            )

            config = self._base_config()
            config.summary_enhancer_dry_run = False
            config.ai_mode = "ollama"
            config.ai_ollama_model = "qwen3-coder:30b"
            config.entries = [entry]

            def fake_builder(_repo, _diff, _path):
                from pyesis.ai_summary import AISummaryResult

                return AISummaryResult(
                    text="I added an Ollama-backed rewrite that explains the deterministic startup path and why the launch sequencing was tightened.",
                    source="ollama",
                )

            report = run_periodic_enhancer(
                config,
                summary_builder=fake_builder,
                buffer_dir=buffer_dir,
                now=datetime(2026, 6, 16, 12, 30, 0),
            )

            self.assertTrue(report.ran)
            self.assertEqual(report.rewritten_state, 1)
            self.assertEqual(report.rewritten_buffer, 1)
            self.assertEqual(config.entries[0].summary_source, "ollama")
            self.assertEqual(config.entries[0].author, "AI")

            updated_items = json.loads(buffer_path.read_text(encoding="utf-8"))
            self.assertEqual(updated_items[0]["summarySource"], "ollama")
            self.assertEqual(updated_items[0]["author"], "AI")

    def test_interval_guard_skips_run(self) -> None:
        config = self._base_config()
        config.summary_enhancer_dry_run = False
        config.summary_enhancer_last_run_at = (datetime.now() - timedelta(seconds=10)).isoformat(timespec="seconds")

        report = run_periodic_enhancer(
            config,
            summary_builder=lambda _repo, _diff, _path: "Strong rewrite",
            now=datetime.now(),
        )

        self.assertFalse(report.ran)

    def test_force_run_bypasses_interval_guard(self) -> None:
        entry = EntryRecord(
            repo_label="RepoForced",
            repo_path="repos/repo-forced",
            created_at="2026-06-16T09:00:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="forceinterval",
            diff_excerpt="diff --git a/force.py b/force.py\n+++ b/force.py\n@@ -0,0 +1 @@\n+print('forced')\n",
            summary_source="heuristic",
            author="Backup",
        )

        config = self._base_config()
        config.summary_enhancer_dry_run = False
        forced_now = datetime(2026, 6, 16, 12, 30, 0)
        config.summary_enhancer_last_run_at = (forced_now - timedelta(seconds=10)).isoformat(timespec="seconds")
        config.ai_mode = "ollama"
        config.ai_ollama_model = "qwen3-coder:30b"
        config.entries = [entry]

        report = run_periodic_enhancer(
            config,
            summary_builder=lambda _repo, _diff, _path: "Forced rewrite documented the exact print path update and why it was retried immediately.",
            now=forced_now,
            force_run=True,
        )

        self.assertTrue(report.ran)
        self.assertEqual(report.rewritten_state, 0)
        self.assertEqual(config.entries[0].rewritten_at, "")

    def test_week_freezes_after_export_cutoff(self) -> None:
        config = self._base_config()
        config.summary_enhancer_dry_run = False
        config.week_end_day = "Thursday"
        config.auto_export_time = "14:28"

        report = run_periodic_enhancer(
            config,
            summary_builder=lambda _repo, _diff, _path: "Strong rewrite",
            now=datetime(2026, 6, 18, 14, 29, 0),
        )

        self.assertFalse(report.ran)

    def test_github_gpt_entries_are_not_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            buffer_dir = tmp / "diff_buffers"
            buffer_dir.mkdir(parents=True, exist_ok=True)
            buffer_path = buffer_dir / "2026-06-16.json"

            diff_text = "diff --git a/d.py b/d.py\n+++ b/d.py\n@@ -0,0 +1 @@\n+print('g')\n"
            buffer_path.write_text(
                json.dumps(
                    [
                        {
                            "datetime": "2026-06-16T09:00:00",
                            "repo": "RepoGPT",
                            "gitDiffText": diff_text,
                            "gitDiffDescription": "made updates",
                            "shown": False,
                            "diffHash": "ghash",
                            "repoPath": "repos/repo-gpt",
                            "author": "AI",
                            "summarySource": "github-gpt",
                        }
                    ],
                    indent=2,
                ),
                encoding="utf-8",
            )

            protected_entry = EntryRecord(
                repo_label="RepoGPT",
                repo_path="repos/repo-gpt",
                created_at="2026-06-16T09:00:00",
                day_name="Monday",
                week_start_iso="2026-06-15T00:00:00",
                summary="made updates",
                diff_hash="ghash",
                diff_excerpt=diff_text,
                summary_source="github-gpt",
                author="AI",
            )

            config = self._base_config()
            config.entries = [protected_entry]
            config.summary_enhancer_dry_run = False

            report = run_periodic_enhancer(
                config,
                summary_builder=lambda _repo, _diff, _path: "This rewrite should never apply.",
                buffer_dir=buffer_dir,
                now=datetime(2026, 6, 16, 10, 0, 0),
            )

            self.assertTrue(report.ran)
            self.assertEqual(report.rewritten_state, 0)
            self.assertEqual(report.rewritten_buffer, 0)
            self.assertEqual(config.entries[0].summary, "made updates")

            updated_items = json.loads(buffer_path.read_text(encoding="utf-8"))
            self.assertEqual(updated_items[0]["gitDiffDescription"], "made updates")

    def test_ollama_entries_are_not_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            buffer_dir = tmp / "diff_buffers"
            buffer_dir.mkdir(parents=True, exist_ok=True)
            buffer_path = buffer_dir / "2026-06-16.json"

            diff_text = "diff --git a/d.py b/d.py\n+++ b/d.py\n@@ -0,0 +1 @@\n+print('o')\n"
            buffer_path.write_text(
                json.dumps(
                    [
                        {
                            "datetime": "2026-06-16T09:00:00",
                            "repo": "RepoOllama",
                            "gitDiffText": diff_text,
                            "gitDiffDescription": "made updates",
                            "shown": False,
                            "diffHash": "ohash",
                            "repoPath": "repos/repo-ollama",
                            "author": "AI",
                            "summarySource": "ollama",
                        }
                    ],
                    indent=2,
                ),
                encoding="utf-8",
            )

            protected_entry = EntryRecord(
                repo_label="RepoOllama",
                repo_path="repos/repo-ollama",
                created_at="2026-06-16T09:00:00",
                day_name="Monday",
                week_start_iso="2026-06-15T00:00:00",
                summary="made updates",
                diff_hash="ohash",
                diff_excerpt=diff_text,
                summary_source="ollama",
                author="AI",
            )

            config = self._base_config()
            config.entries = [protected_entry]
            config.summary_enhancer_dry_run = False

            report = run_periodic_enhancer(
                config,
                summary_builder=lambda _repo, _diff, _path: "This rewrite should never apply.",
                buffer_dir=buffer_dir,
                now=datetime(2026, 6, 16, 10, 0, 0),
            )

            self.assertTrue(report.ran)
            self.assertEqual(report.rewritten_state, 0)
            self.assertEqual(report.rewritten_buffer, 0)
            self.assertEqual(config.entries[0].summary, "made updates")

            updated_items = json.loads(buffer_path.read_text(encoding="utf-8"))
            self.assertEqual(updated_items[0]["gitDiffDescription"], "made updates")

    def test_default_builder_prefers_github_before_heuristic(self) -> None:
        config = self._base_config()
        config.summary_enhancer_dry_run = False
        config.ai_mode = "heuristic"
        config.ai_ollama_url = "http://localhost:11434/api/chat"
        config.ai_ollama_model = "qwen3-coder:30b"

        entry = EntryRecord(
            repo_label="RepoPriority",
            repo_path="repos/repo-priority",
            created_at="2026-06-16T10:00:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="priohash",
            diff_excerpt="diff --git a/e.py b/e.py\n+++ b/e.py\n@@ -0,0 +1 @@\n+print('p')\n",
            summary_source="heuristic",
            author="Backup",
        )
        config.entries = [entry]

        def fake_build_summary(repo_label, diff_text, repo_path, mode=None, allow_fallback=True):
            from pyesis.ai_summary import AISummaryResult
            if mode == "github-gpt":
                return AISummaryResult(
                    text="Applied GitHub-priority rewrite by documenting the print path change and preserving exact diff context.",
                    source="github-gpt",
                )
            if mode == "ollama":
                return AISummaryResult(
                    text="Applied Ollama rewrite by summarizing the print path change with concrete behavior details.",
                    source="ollama",
                )
            return AISummaryResult(
                text="Applied heuristic rewrite by describing the print path update with concrete behavior details.",
                source="heuristic",
            )

        with patch.dict(os.environ, {"PYESIS_GITHUB_GPT_API_KEY": "token"}, clear=False):
            with patch("pyesis.summary_enhancer.build_summary", side_effect=fake_build_summary):
                report = run_periodic_enhancer(
                    config,
                    now=datetime(2026, 6, 16, 10, 30, 0),
                )

        self.assertTrue(report.ran)
        self.assertEqual(
            config.entries[0].summary,
            "Applied GitHub-priority rewrite by documenting the print path change and preserving exact diff context.",
        )
        self.assertEqual(config.entries[0].rewritten_by, "EnhancerTest")

    def test_default_builder_prefers_stored_github_token_before_heuristic(self) -> None:
        config = self._base_config()
        config.summary_enhancer_dry_run = False
        config.ai_mode = "heuristic"
        config.github_auth_mode = "github.com"
        config.github_auth_endpoint = "https://github.com"

        entry = EntryRecord(
            repo_label="RepoPriorityStored",
            repo_path="repos/repo-priority-stored",
            created_at="2026-06-16T10:00:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="priohashstored",
            diff_excerpt="diff --git a/es.py b/es.py\n+++ b/es.py\n@@ -0,0 +1 @@\n+print('ps')\n",
            summary_source="heuristic",
            author="Backup",
        )
        config.entries = [entry]

        def fake_build_summary(repo_label, diff_text, repo_path, mode=None, allow_fallback=True):
            from pyesis.ai_summary import AISummaryResult
            if mode == "github-gpt":
                return AISummaryResult(
                    text="Stored-token GitHub rewrite documented the concrete path update and preserved exact diff context.",
                    source="github-gpt",
                )
            return AISummaryResult(
                text="Applied heuristic rewrite by describing the print path update with concrete behavior details.",
                source="heuristic",
            )

        with patch.dict(os.environ, {"PYESIS_GITHUB_GPT_API_KEY": ""}, clear=False):
            with patch("pyesis.summary_enhancer.load_github_auth_token", return_value=("stored-token", "keychain")):
                with patch("pyesis.summary_enhancer.build_summary", side_effect=fake_build_summary):
                    report = run_periodic_enhancer(
                        config,
                        now=datetime(2026, 6, 16, 10, 30, 0),
                    )

        self.assertTrue(report.ran)
        self.assertEqual(
            config.entries[0].summary,
            "Stored-token GitHub rewrite documented the concrete path update and preserved exact diff context.",
        )

    def test_only_current_week_entries_are_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            buffer_dir = tmp / "diff_buffers"
            buffer_dir.mkdir(parents=True, exist_ok=True)
            buffer_path = buffer_dir / "2026-06-10.json"

            diff_text = "diff --git a/c.py b/c.py\n+++ b/c.py\n@@ -0,0 +1 @@\n+print('z')\n"
            buffer_path.write_text(
                json.dumps(
                    [
                        {
                            "datetime": "2026-06-10T09:00:00",
                            "repo": "RepoOld",
                            "gitDiffText": diff_text,
                            "gitDiffDescription": "made updates",
                            "shown": False,
                            "diffHash": "oldhash",
                            "repoPath": "repos/repo-old",
                            "author": "Backup",
                            "summarySource": "heuristic",
                        }
                    ],
                    indent=2,
                ),
                encoding="utf-8",
            )

            old_entry = EntryRecord(
                repo_label="RepoOld",
                repo_path="repos/repo-old",
                created_at="2026-06-10T09:00:00",
                day_name="Tuesday",
                week_start_iso="2026-06-08T00:00:00",
                summary="made updates",
                diff_hash="oldhash",
                diff_excerpt=diff_text,
                summary_source="heuristic",
                author="Backup",
            )

            config = self._base_config()
            config.entries = [old_entry]
            config.summary_enhancer_dry_run = False

            report = run_periodic_enhancer(
                config,
                summary_builder=lambda _repo, _diff, _path: "Detailed rewrite that should be ignored because the item is from a prior week.",
                buffer_dir=buffer_dir,
                now=datetime(2026, 6, 16, 10, 0, 0),
            )

            self.assertTrue(report.ran)
            self.assertEqual(report.rewritten_state, 0)
            self.assertEqual(report.rewritten_buffer, 0)
            self.assertEqual(config.entries[0].summary, "made updates")

            updated_items = json.loads(buffer_path.read_text(encoding="utf-8"))
            self.assertEqual(updated_items[0]["gitDiffDescription"], "made updates")

    def test_rewrites_current_week_entries_even_if_older_than_last_run(self) -> None:
        entry = EntryRecord(
            repo_label="RepoRetry",
            repo_path="repos/repo-retry",
            created_at="2026-06-15T09:00:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="retryhash",
            diff_excerpt="diff --git a/r.py b/r.py\n+++ b/r.py\n@@ -0,0 +1 @@\n+print('retry')\n",
            summary_source="heuristic",
            author="Backup",
        )

        config = self._base_config()
        config.summary_enhancer_dry_run = False
        config.summary_enhancer_last_run_at = "2026-06-16T10:00:00"
        config.entries = [entry]

        report = run_periodic_enhancer(
            config,
            summary_builder=lambda _repo, _diff, _path: "Refined retry summary by documenting the print retry path update and preserved diff details.",
            now=datetime(2026, 6, 16, 11, 0, 0),
        )

        self.assertTrue(report.ran)
        self.assertEqual(report.rewritten_state, 1)
        self.assertEqual(
            config.entries[0].summary,
            "Refined retry summary by documenting the print retry path update and preserved diff details.",
        )
        self.assertEqual(config.entries[0].rewritten_by, "EnhancerTest")

    def test_rewrites_verbose_template_smell_summary(self) -> None:
        entry = EntryRecord(
            repo_label="RepoTemplate",
            repo_path="repos/repo-template",
            created_at="2026-06-16T08:30:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary=(
                "In pyesis/app.py, I refined application flow to improve the user-facing app flow "
                "by changing code around 'VSCODE_OLLAMA_AUTOCODER_MODEL_SETTING = \"ollama-autocoder.model\"'."
            ),
            diff_hash="templatehash",
            diff_excerpt="diff --git a/t.py b/t.py\n+++ b/t.py\n@@ -0,0 +1 @@\n+print('template')\n",
            summary_source="heuristic",
            author="Backup",
        )

        config = self._base_config()
        config.summary_enhancer_dry_run = False
        config.entries = [entry]

        report = run_periodic_enhancer(
            config,
            summary_builder=lambda _repo, _diff, _path: "Reworked startup handling by documenting the launch-focus path and exact topmost toggle behavior.",
            now=datetime(2026, 6, 16, 11, 15, 0),
        )

        self.assertTrue(report.ran)
        self.assertEqual(report.rewritten_state, 1)
        self.assertEqual(
            config.entries[0].summary,
            "Reworked startup handling by documenting the launch-focus path and exact topmost toggle behavior.",
        )

    def test_aggressive_prodding_rewrites_non_weak_current_week_entries(self) -> None:
        entry = EntryRecord(
            repo_label="RepoAggressive",
            repo_path="repos/repo-aggressive",
            created_at="2026-06-16T09:10:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="Implemented deterministic startup initialization with explicit focus and launch sequencing to reduce race conditions.",
            diff_hash="agghash",
            diff_excerpt="diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+print('aggressive')\n",
            summary_source="heuristic",
            author="Backup",
        )

        config = self._base_config()
        config.summary_enhancer_dry_run = False
        config.summary_enhancer_aggressive_prodding = True
        config.entries = [entry]

        report = run_periodic_enhancer(
            config,
            summary_builder=lambda _repo, _diff, _path: "Recast startup sequencing to document focus orchestration and deterministic launch ordering.",
            now=datetime(2026, 6, 16, 11, 45, 0),
        )

        self.assertTrue(report.ran)
        self.assertEqual(report.rewritten_state, 1)
        self.assertEqual(
            config.entries[0].summary,
            "Recast startup sequencing to document focus orchestration and deterministic launch ordering.",
        )

    def test_aggressive_override_rewrites_non_weak_current_week_entries(self) -> None:
        entry = EntryRecord(
            repo_label="RepoAggressiveOverride",
            repo_path="repos/repo-aggressive-override",
            created_at="2026-06-16T09:10:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="Implemented deterministic startup initialization with explicit focus and launch sequencing to reduce race conditions.",
            diff_hash="agg-override-hash",
            diff_excerpt="diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+print('aggressive-override')\n",
            summary_source="heuristic",
            author="Backup",
        )

        config = self._base_config()
        config.summary_enhancer_dry_run = False
        config.entries = [entry]

        report = run_periodic_enhancer(
            config,
            summary_builder=lambda _repo, _diff, _path: "Recast startup sequencing to document focus orchestration and deterministic launch ordering.",
            now=datetime(2026, 6, 16, 11, 45, 0),
            aggressive_prodding_override=True,
        )

        self.assertTrue(report.ran)
        self.assertEqual(report.rewritten_state, 1)
        self.assertEqual(
            config.entries[0].summary,
            "Recast startup sequencing to document focus orchestration and deterministic launch ordering.",
        )

    def test_rewrite_gate_limits_eight_items_per_repo(self) -> None:
        first_entry = EntryRecord(
            repo_label="RepoLimited",
            repo_path="repos/repo-limited",
            created_at="2026-06-16T09:10:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="limit-1",
            diff_excerpt="diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+print('one')\n",
            summary_source="heuristic",
            author="Backup",
        )
        second_entry = EntryRecord(
            repo_label="RepoLimited",
            repo_path="repos/repo-limited",
            created_at="2026-06-16T09:12:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="limit-2",
            diff_excerpt="diff --git a/b.py b/b.py\n+++ b/b.py\n@@ -0,0 +1 @@\n+print('two')\n",
            summary_source="heuristic",
            author="Backup",
        )
        third_entry = EntryRecord(
            repo_label="RepoLimited",
            repo_path="repos/repo-limited",
            created_at="2026-06-16T09:14:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="limit-3",
            diff_excerpt="diff --git a/c.py b/c.py\n+++ b/c.py\n@@ -0,0 +1 @@\n+print('three')\n",
            summary_source="heuristic",
            author="Backup",
        )
        fourth_entry = EntryRecord(
            repo_label="RepoLimited",
            repo_path="repos/repo-limited",
            created_at="2026-06-16T09:16:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="limit-4",
            diff_excerpt="diff --git a/d.py b/d.py\n+++ b/d.py\n@@ -0,0 +1 @@\n+print('four')\n",
            summary_source="heuristic",
            author="Backup",
        )
        fifth_entry = EntryRecord(
            repo_label="RepoLimited",
            repo_path="repos/repo-limited",
            created_at="2026-06-16T09:18:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="limit-5",
            diff_excerpt="diff --git a/e.py b/e.py\n+++ b/e.py\n@@ -0,0 +1 @@\n+print('five')\n",
            summary_source="heuristic",
            author="Backup",
        )
        sixth_entry = EntryRecord(
            repo_label="RepoLimited",
            repo_path="repos/repo-limited",
            created_at="2026-06-16T09:20:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="limit-6",
            diff_excerpt="diff --git a/f.py b/f.py\n+++ b/f.py\n@@ -0,0 +1 @@\n+print('six')\n",
            summary_source="heuristic",
            author="Backup",
        )
        seventh_entry = EntryRecord(
            repo_label="RepoLimited",
            repo_path="repos/repo-limited",
            created_at="2026-06-16T09:22:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="limit-7",
            diff_excerpt="diff --git a/g.py b/g.py\n+++ b/g.py\n@@ -0,0 +1 @@\n+print('seven')\n",
            summary_source="heuristic",
            author="Backup",
        )
        eighth_entry = EntryRecord(
            repo_label="RepoLimited",
            repo_path="repos/repo-limited",
            created_at="2026-06-16T09:24:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="limit-8",
            diff_excerpt="diff --git a/h.py b/h.py\n+++ b/h.py\n@@ -0,0 +1 @@\n+print('eight')\n",
            summary_source="heuristic",
            author="Backup",
        )
        ninth_entry = EntryRecord(
            repo_label="RepoLimited",
            repo_path="repos/repo-limited",
            created_at="2026-06-16T09:26:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="limit-9",
            diff_excerpt="diff --git a/i.py b/i.py\n+++ b/i.py\n@@ -0,0 +1 @@\n+print('nine')\n",
            summary_source="heuristic",
            author="Backup",
        )

        config = self._base_config()
        config.summary_enhancer_dry_run = False
        config.summary_enhancer_aggressive_prodding = True
        config.entries = [
            first_entry,
            second_entry,
            third_entry,
            fourth_entry,
            fifth_entry,
            sixth_entry,
            seventh_entry,
            eighth_entry,
            ninth_entry,
        ]

        handled_repos: dict[str, int] = {}

        def rewrite_gate(repo_label: str, _repo_path: str | None) -> bool:
            handled_count = handled_repos.get(repo_label, 0)
            if handled_count >= 8:
                return False
            handled_repos[repo_label] = handled_count + 1
            return True

        report = run_periodic_enhancer(
            config,
            summary_builder=lambda _repo, _diff, _path: "Documented the concrete file change with a stronger AI rewrite for this repo.",
            rewrite_gate=rewrite_gate,
            now=datetime(2026, 6, 16, 11, 45, 0),
        )

        self.assertTrue(report.ran)
        self.assertEqual(report.rewritten_state, 8)
        self.assertEqual(config.entries[0].summary_source, "heuristic")
        self.assertEqual(config.entries[1].summary_source, "heuristic")
        self.assertEqual(config.entries[2].summary_source, "heuristic")
        self.assertEqual(config.entries[3].summary_source, "heuristic")
        self.assertEqual(config.entries[4].summary_source, "heuristic")
        self.assertEqual(config.entries[5].summary_source, "heuristic")
        self.assertEqual(config.entries[6].summary_source, "heuristic")
        self.assertEqual(config.entries[7].summary_source, "heuristic")
        self.assertEqual(config.entries[8].summary_source, "heuristic")
        self.assertEqual(report.skipped_gated, 1)
        rewritten_count = sum(1 for entry in config.entries if entry.rewritten_by == "EnhancerTest")
        self.assertEqual(rewritten_count, 8)

    def test_force_ai_upgrade_does_not_finalize_heuristic_fallback(self) -> None:
        entry = EntryRecord(
            repo_label="RepoRetryAI",
            repo_path="repos/repo-retry-ai",
            created_at="2026-06-16T09:10:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="retry-ai-1",
            diff_excerpt="diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+print('retry-ai')\n",
            summary_source="heuristic",
            author="Backup",
        )

        config = self._base_config()
        config.summary_enhancer_dry_run = False
        config.ai_mode = "ollama"
        config.ai_ollama_model = "qwen3-coder:30b"
        config.entries = [entry]

        def fake_build_summary(_repo_label, _diff_text, _repo_path, mode=None, allow_fallback=True):
            from pyesis.ai_summary import AISummaryResult

            if mode == "ollama":
                return AISummaryResult(
                    text="Fallback heuristic rewrite described the exact file change but AI was unavailable.",
                    source="heuristic",
                    requested_source="ollama",
                    warning="Ollama summary failed: offline",
                    fallback_source="heuristic",
                )

            return AISummaryResult(
                text="Fallback heuristic rewrite described the exact file change but AI was unavailable.",
                source="heuristic",
                requested_source=mode or "heuristic",
            )

        with patch("pyesis.summary_enhancer.build_summary", side_effect=fake_build_summary):
            report = run_periodic_enhancer(
                config,
                now=datetime(2026, 6, 16, 11, 45, 0),
            )

        self.assertTrue(report.ran)
        self.assertEqual(report.rewritten_state, 0)
        self.assertEqual(report.skipped_ai_unavailable, 1)
        self.assertEqual(config.entries[0].summary, "made updates")
        self.assertEqual(config.entries[0].rewritten_at, "")
        self.assertEqual(config.entries[0].requested_summary_source, "ollama")
        self.assertEqual(config.entries[0].fallback_summary_source, "heuristic")
        self.assertIn("offline", config.entries[0].summary_warning)
        self.assertEqual(config.entries[0].last_ai_attempt_at, "2026-06-16T11:45:00")

        with patch("pyesis.summary_enhancer.build_summary") as second_attempt:
            second_report = run_periodic_enhancer(
                config,
                now=datetime(2026, 6, 16, 11, 47, 0),
            )

        self.assertTrue(second_report.ran)
        self.assertEqual(second_report.rewritten_state, 0)
        self.assertFalse(second_attempt.called)

    def test_failed_ai_upgrade_retries_after_cooldown_within_ten_minutes(self) -> None:
        entry = EntryRecord(
            repo_label="RepoRetryWindow",
            repo_path="repos/repo-retry-window",
            created_at="2026-06-16T11:44:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="retry-window-1",
            diff_excerpt="diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+print('retry-window')\n",
            summary_source="heuristic",
            author="Backup",
            requested_summary_source="ollama",
            summary_warning="Ollama summary failed: offline",
            fallback_summary_source="heuristic",
            last_ai_attempt_at="2026-06-16T11:45:00",
        )

        config = self._base_config()
        config.summary_enhancer_dry_run = False
        config.ai_mode = "ollama"
        config.ai_ollama_model = "qwen3-coder:30b"
        config.entries = [entry]

        with patch("pyesis.summary_enhancer.build_summary") as early_attempt:
            early_report = run_periodic_enhancer(
                config,
                now=datetime(2026, 6, 16, 11, 47, 0),
            )

        self.assertTrue(early_report.ran)
        self.assertEqual(early_report.rewritten_state, 0)
        self.assertFalse(early_attempt.called)

        with patch("pyesis.summary_enhancer.build_summary") as retry_attempt:
            retry_attempt.return_value = AISummaryResult(
                text="I documented the fresh retry path and the AI rewrite completed before the orange window expired.",
                source="ollama",
                requested_source="ollama",
            )

            retry_report = run_periodic_enhancer(
                config,
                now=datetime(2026, 6, 16, 11, 48, 0),
            )

        self.assertTrue(retry_report.ran)
        self.assertEqual(retry_report.rewritten_state, 1)
        self.assertEqual(config.entries[0].summary_source, "ollama")
        self.assertEqual(config.entries[0].author, "AI")

    def test_force_run_retries_failed_ai_upgrade_after_metadata_stamp(self) -> None:
        entry = EntryRecord(
            repo_label="RepoRetryForced",
            repo_path="repos/repo-retry-forced",
            created_at="2026-06-16T09:10:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="retry-ai-3",
            diff_excerpt="diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+print('retry-force')\n",
            summary_source="heuristic",
            author="Backup",
            requested_summary_source="ollama",
            summary_warning="Ollama summary failed: offline",
            fallback_summary_source="heuristic",
        )

        config = self._base_config()
        config.summary_enhancer_dry_run = False
        config.ai_mode = "ollama"
        config.ai_ollama_model = "qwen3-coder:30b"
        config.entries = [entry]

        with patch("pyesis.summary_enhancer.build_summary") as fake_build_summary:
            fake_build_summary.return_value = AISummaryResult(
                text="I documented the exact retry path after the prior Ollama fallback and confirmed the AI rewrite succeeded.",
                source="ollama",
                requested_source="ollama",
                provider_details="qwen3-coder:30b",
            )

            report = run_periodic_enhancer(
                config,
                now=datetime(2026, 6, 16, 11, 50, 0),
                force_run=True,
            )

        self.assertTrue(report.ran)
        self.assertEqual(report.rewritten_state, 1)
        self.assertEqual(config.entries[0].summary_source, "ollama")
        self.assertEqual(config.entries[0].author, "AI")

    def test_attempt_logs_include_duration_and_provider(self) -> None:
        entry = EntryRecord(
            repo_label="RepoTiming",
            repo_path="repos/repo-timing",
            created_at="2026-06-16T09:10:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="timing-1",
            diff_excerpt="diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+print('timing')\n",
            summary_source="heuristic",
            author="Backup",
        )

        config = self._base_config()
        config.summary_enhancer_dry_run = False
        config.ai_mode = "ollama"
        config.ai_ollama_model = "qwen3-coder:30b"
        config.entries = [entry]

        def fake_builder(_repo, _diff, _path):
            return AISummaryResult(
                text="Documented the exact timing test change and why the AI rewrite path succeeded.",
                source="ollama",
                timing_ms=4321,
                provider_details="qwen3-coder:30b",
            )

        report = run_periodic_enhancer(
            config,
            summary_builder=fake_builder,
            now=datetime(2026, 6, 16, 11, 50, 0),
        )

        self.assertTrue(report.ran)
        self.assertEqual(report.rewritten_state, 1)
        self.assertEqual(report.provider_timed_attempts, 1)
        self.assertEqual(report.average_provider_ms, 4321)
        self.assertTrue(any("State rewritten (ollama, 4321 ms via qwen3-coder:30b): RepoTiming" in log for log in report.logs or []))

    def test_provider_timing_contributes_to_average(self) -> None:
        entry = EntryRecord(
            repo_label="RepoProviderTiming",
            repo_path="repos/repo-provider-timing",
            created_at="2026-06-16T09:10:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="provider-timing-1",
            diff_excerpt="diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+print('provider')\n",
            summary_source="heuristic",
            author="Backup",
        )

        config = self._base_config()
        config.summary_enhancer_dry_run = False
        config.entries = [entry]

        report = run_periodic_enhancer(
            config,
            summary_builder=lambda _repo, _diff, _path: AISummaryResult(
                text="Documented the concrete provider timing path and confirmed the rewrite succeeded.",
                source="ollama",
                timing_ms=4321,
                provider_details="qwen3-coder:30b",
            ),
            now=datetime(2026, 6, 16, 11, 51, 0),
        )

        self.assertTrue(report.ran)
        self.assertEqual(report.provider_timed_attempts, 1)
        self.assertEqual(report.average_provider_ms, 4321)

    def test_force_ai_upgrade_retries_heuristic_rewrite_with_rewritten_at(self) -> None:
        entry = EntryRecord(
            repo_label="RepoRetryStamped",
            repo_path="repos/repo-retry-stamped",
            created_at="2026-06-16T09:10:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="I updated src/server.js around 'const OLLAMA_BASE_URL'.",
            diff_hash="retry-ai-2",
            diff_excerpt="diff --git a/server.js b/server.js\n+++ b/server.js\n@@ -0,0 +1 @@\n+print('server')\n",
            summary_source="heuristic",
            author="Backup",
            rewritten_by="EnhancerTest",
            rewritten_at="2026-06-23T05:58:12",
        )

        config = self._base_config()
        config.summary_enhancer_dry_run = False
        config.ai_mode = "ollama"
        config.ai_ollama_model = "qwen3-coder:30b"
        config.entries = [entry]

        with patch("pyesis.summary_enhancer.build_summary") as fake_build_summary:
            from pyesis.ai_summary import AISummaryResult

            fake_build_summary.return_value = AISummaryResult(
                text="Documented the server bootstrap change and the new startup token handling in concrete terms.",
                source="ollama",
                requested_source="ollama",
            )

            report = run_periodic_enhancer(
                config,
                now=datetime(2026, 6, 16, 11, 50, 0),
            )

        self.assertTrue(report.ran)
        self.assertEqual(report.rewritten_state, 1)
        self.assertEqual(config.entries[0].summary_source, "ollama")
        self.assertEqual(config.entries[0].author, "AI")
        self.assertEqual(config.entries[0].rewritten_by, "EnhancerTest")

    def test_failed_rewrite_marks_warning_and_prioritizes_next_round(self) -> None:
        failed_entry = EntryRecord(
            repo_label="RepoPriority",
            repo_path="repos/repo-priority",
            created_at="2026-06-16T09:12:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="priority-failed",
            diff_excerpt="diff --git a/b.py b/b.py\n+++ b/b.py\n@@ -0,0 +1 @@\n+print('two')\n",
            summary_source="heuristic",
            author="Backup",
            summary_warning="Ollama summary failed: offline",
        )
        untouched_entry = EntryRecord(
            repo_label="RepoPriority",
            repo_path="repos/repo-priority",
            created_at="2026-06-16T09:10:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="priority-untouched",
            diff_excerpt="diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+print('one')\n",
            summary_source="heuristic",
            author="Backup",
        )

        config = self._base_config()
        config.summary_enhancer_dry_run = False
        config.summary_enhancer_aggressive_prodding = True
        config.entries = [failed_entry, untouched_entry]

        handled_repos: dict[str, int] = {}

        def rewrite_gate(repo_label: str, _repo_path: str | None) -> bool:
            handled_count = handled_repos.get(repo_label, 0)
            if handled_count >= 1:
                return False
            handled_repos[repo_label] = handled_count + 1
            return True

        report = run_periodic_enhancer(
            config,
            summary_builder=lambda _repo, diff, _path: f"Strong rewrite for {diff.splitlines()[0]}",
            rewrite_gate=rewrite_gate,
            now=datetime(2026, 6, 16, 11, 55, 0),
        )

        self.assertTrue(report.ran)
        self.assertEqual(report.rewritten_state, 1)
        rewritten_by_hash = {entry.diff_hash: entry.rewritten_at for entry in config.entries}
        self.assertTrue(rewritten_by_hash["priority-failed"])
        self.assertEqual(rewritten_by_hash["priority-untouched"], "")

    def test_failed_rewrite_without_provider_warning_still_marks_entry(self) -> None:
        entry = EntryRecord(
            repo_label="RepoWeak",
            repo_path="repos/repo-weak",
            created_at="2026-06-16T09:10:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="weak-rewrite",
            diff_excerpt="diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+print('weak')\n",
            summary_source="heuristic",
            author="Backup",
        )

        config = self._base_config()
        config.summary_enhancer_dry_run = False
        config.summary_enhancer_aggressive_prodding = True
        config.entries = [entry]

        report = run_periodic_enhancer(
            config,
            summary_builder=lambda _repo, _diff, _path: "made updates",
            now=datetime(2026, 6, 16, 11, 55, 0),
        )

        self.assertTrue(report.ran)
        self.assertEqual(report.failed_state_marked, 1)
        self.assertIn("AI rewrite skipped", config.entries[0].summary_warning)
        self.assertEqual(config.entries[0].rewritten_at, "")

    def test_rewrite_gate_prioritizes_older_entries_before_newer_ones(self) -> None:
        newer_entry = EntryRecord(
            repo_label="RepoOrdered",
            repo_path="repos/repo-ordered",
            created_at="2026-06-16T09:14:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="ordered-3",
            diff_excerpt="diff --git a/c.py b/c.py\n+++ b/c.py\n@@ -0,0 +1 @@\n+print('three')\n",
            summary_source="heuristic",
            author="Backup",
        )
        oldest_entry = EntryRecord(
            repo_label="RepoOrdered",
            repo_path="repos/repo-ordered",
            created_at="2026-06-16T09:10:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="ordered-1",
            diff_excerpt="diff --git a/a.py b/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+print('one')\n",
            summary_source="heuristic",
            author="Backup",
        )
        middle_entry = EntryRecord(
            repo_label="RepoOrdered",
            repo_path="repos/repo-ordered",
            created_at="2026-06-16T09:12:00",
            day_name="Monday",
            week_start_iso="2026-06-15T00:00:00",
            summary="made updates",
            diff_hash="ordered-2",
            diff_excerpt="diff --git a/b.py b/b.py\n+++ b/b.py\n@@ -0,0 +1 @@\n+print('two')\n",
            summary_source="heuristic",
            author="Backup",
        )

        config = self._base_config()
        config.summary_enhancer_dry_run = False
        config.summary_enhancer_aggressive_prodding = True
        config.entries = [newer_entry, oldest_entry, middle_entry]

        handled_repos: dict[str, int] = {}

        def rewrite_gate(repo_label: str, _repo_path: str | None) -> bool:
            handled_count = handled_repos.get(repo_label, 0)
            if handled_count >= 2:
                return False
            handled_repos[repo_label] = handled_count + 1
            return True

        report = run_periodic_enhancer(
            config,
            summary_builder=lambda _repo, diff, _path: f"Strong rewrite for {diff.splitlines()[0]}",
            rewrite_gate=rewrite_gate,
            now=datetime(2026, 6, 16, 11, 55, 0),
        )

        self.assertTrue(report.ran)
        self.assertEqual(report.rewritten_state, 2)
        rewritten_summaries = {entry.summary for entry in config.entries if entry.rewritten_at}
        self.assertIn("Strong rewrite for diff --git a/a.py b/a.py", rewritten_summaries)
        self.assertIn("Strong rewrite for diff --git a/b.py b/b.py", rewritten_summaries)
        self.assertNotIn("Strong rewrite for diff --git a/c.py b/c.py", rewritten_summaries)


if __name__ == "__main__":
    unittest.main()
