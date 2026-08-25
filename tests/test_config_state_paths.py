from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pyesis.config as config


class ConfigStatePathTests(unittest.TestCase):
    def test_default_state_directory_uses_fixed_home_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            fake_home = Path(temp_dir)
            with patch("pyesis.config.Path.home", return_value=fake_home):
                self.assertEqual(config.default_state_directory(), fake_home / "PyesisState")

    def test_migrate_legacy_runtime_data_uses_newest_legacy_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home_dir = Path(temp_dir)
            target_dir = home_dir / "PyesisState"
            older_root = home_dir / "older-repo"
            newer_root = home_dir / "newer-repo"

            self._write_runtime_state(older_root, summary_text="older", diff_text="older-diff", log_text="older-log")
            self._write_runtime_state(newer_root, summary_text="newer", diff_text="newer-diff", log_text="newer-log")

            older_time = 1_700_000_000
            newer_time = older_time + 600
            self._touch_runtime_state(older_root, older_time)
            self._touch_runtime_state(newer_root, newer_time)

            migrated = config.migrate_legacy_runtime_data(state_directory=target_dir, search_root=home_dir)

            self.assertTrue(migrated)
            self.assertEqual((target_dir / "pyesis_state.json").read_text(encoding="utf-8"), "newer")
            self.assertEqual((target_dir / "diff_buffers" / "2026-08-10.json").read_text(encoding="utf-8"), "newer-diff")
            self.assertEqual((target_dir / "logs" / "ai_attempts.jsonl").read_text(encoding="utf-8"), "newer-log")

    def test_migrate_legacy_runtime_data_overwrites_stale_target_with_newer_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home_dir = Path(temp_dir)
            target_dir = home_dir / "PyesisState"
            source_root = home_dir / "repo"

            self._write_runtime_state(source_root, summary_text="fresh", diff_text="fresh-diff", log_text="fresh-log")
            self._write_runtime_state(target_dir, summary_text="stale", diff_text="stale-diff", log_text="stale-log")

            stale_time = 1_700_000_000
            fresh_time = stale_time + 600
            self._touch_runtime_state(target_dir, stale_time)
            self._touch_runtime_state(source_root, fresh_time)

            migrated = config.migrate_legacy_runtime_data(legacy_root=source_root, state_directory=target_dir)

            self.assertTrue(migrated)
            self.assertEqual((target_dir / "pyesis_state.json").read_text(encoding="utf-8"), "fresh")
            self.assertEqual((target_dir / "diff_buffers" / "2026-08-10.json").read_text(encoding="utf-8"), "fresh-diff")
            self.assertEqual((target_dir / "logs" / "ai_attempts.jsonl").read_text(encoding="utf-8"), "fresh-log")

    def _write_runtime_state(self, root: Path, *, summary_text: str, diff_text: str, log_text: str) -> None:
        (root / "diff_buffers").mkdir(parents=True, exist_ok=True)
        (root / "logs").mkdir(parents=True, exist_ok=True)
        (root / "pyesis_state.json").write_text(summary_text, encoding="utf-8")
        (root / "diff_buffers" / "2026-08-10.json").write_text(diff_text, encoding="utf-8")
        (root / "logs" / "ai_attempts.jsonl").write_text(log_text, encoding="utf-8")

    def _touch_runtime_state(self, root: Path, timestamp: int) -> None:
        paths = [
            root / "pyesis_state.json",
            root / "diff_buffers",
            root / "diff_buffers" / "2026-08-10.json",
            root / "logs",
            root / "logs" / "ai_attempts.jsonl",
        ]
        for path in paths:
            os.utime(path, (timestamp, timestamp))


if __name__ == "__main__":
    unittest.main()