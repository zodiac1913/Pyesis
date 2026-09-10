from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from pyesis.config import AppConfig, EntryRecord, RepoConfig, load_config, save_config
from pyesis.storage import load_buffer_day, migrate_legacy_json, normalized_db_path, read_payload, replace_buffer_day


class StorageMigrationTests(unittest.TestCase):
    def test_migrate_drops_cms_sqlite_cats_source_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            json_path = root / "pyesis_state.json"
            keep = {
                "repo_label": "CatsDbGetSomeIpsum",
                "repo_path": "/tmp/ipsum",
                "created_at": "2026-09-08T08:53:03",
                "day_name": "Tuesday",
                "week_start_iso": "2026-09-07T00:00:00",
                "summary": "I added SourceFolderLastWriteUtcTicks in generated/catsUpDate.JSON.",
                "diff_hash": "keep-1",
                "diff_excerpt": "diff --git a/generated/catsUpDate.json b/generated/catsUpDate.json\n+++ b/generated/catsUpDate.json\n",
            }
            drop = {
                "repo_label": "CatsDbGetSomeIpsum",
                "repo_path": "/tmp/ipsum",
                "created_at": "2026-09-08T06:07:00",
                "day_name": "Tuesday",
                "week_start_iso": "2026-09-07T00:00:00",
                "summary": "I created cms-sqlLite-cats-source/Views/Home/Index.cshtml.",
                "diff_hash": "drop-1",
                "diff_excerpt": "diff --git a/cms-sqlLite-cats-source/Views/Home/Index.cshtml b/cms-sqlLite-cats-source/Views/Home/Index.cshtml\n+++ b/cms-sqlLite-cats-source/Views/Home/Index.cshtml\n",
            }
            json_path.write_text(
                json.dumps({"week_end_day": "Thursday", "theme_mode": "system", "repos": [], "entries": [keep, drop]}),
                encoding="utf-8",
            )

            self.assertTrue(migrate_legacy_json(json_path))
            db_path = normalized_db_path(json_path)
            loaded = load_config(state_path=db_path)
            self.assertEqual([entry.diff_hash for entry in loaded.entries], ["keep-1"])
            with sqlite3.connect(db_path) as connection:
                hashes = [row[0] for row in connection.execute("SELECT diff_hash FROM entries")]
            self.assertEqual(hashes, ["keep-1"])
            self.assertFalse(json_path.exists())
            self.assertTrue(json_path.with_name("pyesis_state.json.migrated").exists())

    def test_save_config_writes_sqlite_not_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pyesis.db"
            save_config(AppConfig(ai_ollama_num_threads=3, entries=[]), state_path=db_path)
            self.assertTrue(db_path.exists())
            self.assertFalse((Path(temp_dir) / "pyesis_state.json").exists())
            payload = read_payload(db_path, include_entries=True)
            self.assertIsNotNone(payload)
            self.assertEqual(payload["ai_ollama_num_threads"], 3)

    def test_save_config_strips_sqlite_copy_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pyesis.db"
            saved = AppConfig(
                entries=[
                    EntryRecord(
                        repo_label="CatsDbGetSomeIpsum",
                        repo_path="/tmp/ipsum",
                        created_at="2026-09-08T06:07:00",
                        day_name="Tuesday",
                        week_start_iso="2026-09-07T00:00:00",
                        summary="I created cms-sqlLite-cats-source/CATS.csproj.",
                        diff_hash="drop-2",
                        diff_excerpt="diff --git a/cms-sqlLite-cats-source/CATS.csproj b/cms-sqlLite-cats-source/CATS.csproj\n",
                    )
                ]
            )
            save_config(saved, state_path=db_path)
            loaded = load_config(state_path=db_path)
            self.assertEqual(loaded.entries, [])

    def test_save_config_persists_monitored_repos(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pyesis.db"
            saved = AppConfig(
                repos=[
                    RepoConfig(path="/tmp/ipsum", label="CatsDbGetSomeIpsum", poll_seconds=90),
                    RepoConfig(path="/tmp/cats", label="Cats", poll_seconds=120),
                ]
            )
            save_config(saved, state_path=db_path)
            loaded = load_config(state_path=db_path)
            self.assertEqual([(repo.label, repo.path, repo.poll_seconds) for repo in loaded.repos], [
                ("CatsDbGetSomeIpsum", "/tmp/ipsum", 90),
                ("Cats", "/tmp/cats", 120),
            ])

    def test_buffer_rows_skip_sqlite_copy_diffs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pyesis.db"
            replace_buffer_day(
                db_path,
                "2026-09-08",
                [
                    {
                        "datetime": "2026-09-08T06:07:00",
                        "repo": "CatsDbGetSomeIpsum",
                        "gitDiffText": "diff --git a/cms-sqlLite-cats-source/CATS.csproj b/cms-sqlLite-cats-source/CATS.csproj\n",
                        "gitDiffDescription": "I created cms-sqlLite-cats-source/CATS.csproj.",
                        "shown": True,
                        "diffHash": "drop-buf",
                        "repoPath": "/tmp/ipsum",
                    },
                    {
                        "datetime": "2026-09-08T08:53:00",
                        "repo": "CatsDbGetSomeIpsum",
                        "gitDiffText": "diff --git a/generated/catsUpDate.json b/generated/catsUpDate.json\n",
                        "gitDiffDescription": "I added SourceFolderLastWriteUtcTicks.",
                        "shown": True,
                        "diffHash": "keep-buf",
                        "repoPath": "/tmp/ipsum",
                    },
                ],
            )
            items = load_buffer_day(db_path, "2026-09-08")
            self.assertEqual([item["diffHash"] for item in items], ["keep-buf"])

    def test_backfill_imports_entries_when_db_already_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "pyesis.db"
            save_config(AppConfig(week_end_day="Thursday", entries=[]), state_path=db_path)
            (root / "pyesis_state.json.migrated").write_text(
                json.dumps({
                    "week_end_day": "Thursday",
                    "repos": [],
                    "entries": [{
                        "repo_label": "Pyesis",
                        "repo_path": "/tmp/pyesis",
                        "created_at": "2026-09-08T08:53:03",
                        "day_name": "Tuesday",
                        "week_start_iso": "2026-09-07T00:00:00",
                        "summary": "I kept a real summary.",
                        "diff_hash": "keep-backfill",
                        "diff_excerpt": "diff --git a/pyesis/app.py b/pyesis/app.py\n",
                    }],
                }),
                encoding="utf-8",
            )
            loaded = load_config(state_path=db_path)
            self.assertEqual([entry.diff_hash for entry in loaded.entries], ["keep-backfill"])

    def test_empty_save_does_not_wipe_existing_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pyesis.db"
            save_config(
                AppConfig(
                    entries=[
                        EntryRecord(
                            repo_label="Pyesis",
                            repo_path="/tmp/pyesis",
                            created_at="2026-09-08T08:53:03",
                            day_name="Tuesday",
                            week_start_iso="2026-09-07T00:00:00",
                            summary="I kept a real summary.",
                            diff_hash="keep-empty-save",
                            diff_excerpt="diff --git a/pyesis/app.py b/pyesis/app.py\n",
                        )
                    ]
                ),
                state_path=db_path,
            )
            save_config(AppConfig(week_end_day="Friday", entries=[]), state_path=db_path)
            loaded = load_config(state_path=db_path)
            self.assertEqual([entry.diff_hash for entry in loaded.entries], ["keep-empty-save"])
            self.assertEqual(loaded.week_end_day, "Friday")

    def test_partial_save_does_not_wipe_existing_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "pyesis.db"
            first = EntryRecord(
                repo_label="Pyesis",
                repo_path="/tmp/pyesis",
                created_at="2026-09-08T08:53:03",
                day_name="Tuesday",
                week_start_iso="2026-09-07T00:00:00",
                summary="I kept the original summary.",
                diff_hash="keep-original",
                diff_excerpt="diff --git a/pyesis/app.py b/pyesis/app.py\n",
            )
            second = EntryRecord(
                repo_label="Pyesis",
                repo_path="/tmp/pyesis",
                created_at="2026-09-09T08:53:03",
                day_name="Wednesday",
                week_start_iso="2026-09-07T00:00:00",
                summary="I added a later summary.",
                diff_hash="keep-new",
                diff_excerpt="diff --git a/pyesis/config.py b/pyesis/config.py\n",
            )
            save_config(AppConfig(entries=[first]), state_path=db_path)
            save_config(AppConfig(entries=[second]), state_path=db_path)
            loaded = load_config(state_path=db_path)
            self.assertEqual(sorted(entry.diff_hash for entry in loaded.entries), ["keep-new", "keep-original"])


if __name__ == "__main__":
    unittest.main()
