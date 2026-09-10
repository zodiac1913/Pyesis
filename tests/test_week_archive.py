from __future__ import annotations

from datetime import datetime
from pathlib import Path
import json
import tempfile
import unittest

import py7zr

from pyesis.config import EntryRecord
from pyesis.week_archive import archive_completed_weeks, archive_file_name


class WeekArchiveTests(unittest.TestCase):
    def test_archives_completed_week_as_7z_and_skips_current_week(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            archive_dir = root / "week_archives"
            buffer_dir = root / "diff_buffers"
            buffer_dir.mkdir()
            (buffer_dir / "2026-08-28.json").write_text(
                json.dumps(
                    [
                        {
                            "datetime": "2026-08-28T09:00:00",
                            "repo": "Pyesis",
                            "gitDiffText": "diff --git a/old.py b/old.py\n+++ b/old.py\n",
                            "gitDiffDescription": "I finished last week's work.",
                            "shown": True,
                            "diffHash": "old-week",
                            "repoPath": "/tmp/pyesis",
                        }
                    ]
                ),
                encoding="utf-8",
            )

            completed = EntryRecord(
                repo_label="Pyesis",
                repo_path="/tmp/pyesis",
                created_at="2026-08-31T09:00:00",
                day_name="Monday",
                week_start_iso="2026-08-28T00:00:00",
                summary="I finished last week's work.",
                diff_hash="old-week",
                diff_excerpt="diff --git a/old.py b/old.py\n+++ b/old.py\n",
            )
            current = EntryRecord(
                repo_label="Pyesis",
                repo_path="/tmp/pyesis",
                created_at="2026-09-08T09:00:00",
                day_name="Tuesday",
                week_start_iso="2026-09-04T00:00:00",
                summary="I am still working this week.",
                diff_hash="current-week",
                diff_excerpt="diff --git a/now.py b/now.py\n+++ b/now.py\n",
            )

            written = archive_completed_weeks(
                [completed, current],
                week_end_day="Thursday",
                archive_dir=archive_dir,
                buffer_dir=buffer_dir,
                now=datetime.fromisoformat("2026-09-09T12:00:00"),
            )

            self.assertEqual(len(written), 1)
            archive_path = archive_dir / archive_file_name("2026-08-28")
            self.assertEqual(written[0], archive_path)
            self.assertTrue(archive_path.is_file())
            self.assertFalse((archive_dir / archive_file_name("2026-09-04")).exists())

            with py7zr.SevenZipFile(archive_path, "r") as archive:
                names = set(archive.getnames())
            self.assertIn("week.json", names)
            self.assertIn("diff_buffers/2026-08-28.json", names)

            second = archive_completed_weeks(
                [completed, current],
                week_end_day="Thursday",
                archive_dir=archive_dir,
                buffer_dir=buffer_dir,
                now=datetime.fromisoformat("2026-09-09T12:00:00"),
            )
            self.assertEqual(second, [])

            extract_dir = root / "extract"
            extract_dir.mkdir()
            with py7zr.SevenZipFile(archive_path, "r") as archive:
                archive.extractall(path=extract_dir)
            payload = json.loads((extract_dir / "week.json").read_text(encoding="utf-8"))
            self.assertTrue(payload["pyesis_week_archive"])
            self.assertEqual(payload["week_start_iso"], "2026-08-28T00:00:00")
            self.assertEqual(len(payload["entries"]), 1)
            self.assertEqual(payload["entries"][0]["diff_hash"], "old-week")


if __name__ == "__main__":
    unittest.main()
