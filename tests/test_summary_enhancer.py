from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
import os
from unittest.mock import patch

from pyesis.config import AppConfig, EntryRecord
from pyesis.summary_enhancer import run_periodic_enhancer


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
            self.assertEqual(config.entries[0].rewritten_by, "EnhancerTest")
            self.assertTrue(config.entries[0].rewritten_at)

            self.assertEqual(config.entries[1].summary_source, "human")
            self.assertEqual(config.entries[1].summary, "made updates")

            updated_items = json.loads(buffer_path.read_text(encoding="utf-8"))
            self.assertEqual(updated_items[0]["gitDiffText"], diff_text)
            self.assertEqual(updated_items[0]["diffHash"], original_hash)
            self.assertIn("DAM compile exclusion", updated_items[0]["gitDiffDescription"])
            self.assertEqual(updated_items[0]["rewrittenBy"], "EnhancerTest")
            self.assertTrue(updated_items[0]["rewrittenAt"])

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


if __name__ == "__main__":
    unittest.main()
