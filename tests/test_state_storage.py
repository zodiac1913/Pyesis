from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from pyesis.config import migrate_legacy_runtime_data


class StateStorageTests(unittest.TestCase):
    def test_migrate_legacy_runtime_data_copies_repo_local_state_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            tmp = Path(temp_dir)
            legacy_root = tmp / "legacy"
            state_dir = tmp / "state"
            (legacy_root / "diff_buffers").mkdir(parents=True, exist_ok=True)
            (legacy_root / "logs").mkdir(parents=True, exist_ok=True)
            (legacy_root / "pyesis_state.json").write_text('{"repos": [], "entries": []}', encoding="utf-8")
            (legacy_root / "diff_buffers" / "2026-08-10.json").write_text("[]", encoding="utf-8")
            (legacy_root / "logs" / "ai_attempts.jsonl").write_text('{"timestamp": "2026-08-10T04:00:00"}\n', encoding="utf-8")

            migrated = migrate_legacy_runtime_data(legacy_root=legacy_root, state_directory=state_dir)

            self.assertTrue(migrated)
            self.assertEqual((state_dir / "pyesis_state.json").read_text(encoding="utf-8"), '{"repos": [], "entries": []}')
            self.assertEqual((state_dir / "diff_buffers" / "2026-08-10.json").read_text(encoding="utf-8"), "[]")
            self.assertEqual(
                (state_dir / "logs" / "ai_attempts.jsonl").read_text(encoding="utf-8"),
                '{"timestamp": "2026-08-10T04:00:00"}\n',
            )


if __name__ == "__main__":
    unittest.main()