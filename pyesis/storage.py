from __future__ import annotations

from dataclasses import asdict, fields
import json
from pathlib import Path
import sqlite3
from typing import Any

from pyesis.config import (
    AppConfig,
    EntryRecord,
    _base_config_from_data,
    _decode_deleted_entry,
    _decode_entry,
)


SCHEMA_VERSION = 2
LEGACY_JSON_NAME = "pyesis_state.json"
SKIP_SETTING_FIELDS = {"repos", "entries", "deleted_entries"}
ENTRY_COLUMNS = tuple(field.name for field in fields(EntryRecord))
SETTING_COLUMNS = tuple(field.name for field in fields(AppConfig) if field.name not in SKIP_SETTING_FIELDS)


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(db_path), timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    return connection


def initialize_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS repos (
            position INTEGER PRIMARY KEY,
            path TEXT NOT NULL,
            label TEXT NOT NULL,
            poll_seconds INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_label TEXT NOT NULL,
            repo_path TEXT NOT NULL,
            created_at TEXT NOT NULL,
            day_name TEXT NOT NULL,
            week_start_iso TEXT NOT NULL,
            summary TEXT NOT NULL,
            diff_hash TEXT NOT NULL,
            diff_excerpt TEXT NOT NULL,
            summary_source TEXT NOT NULL DEFAULT '',
            author TEXT NOT NULL DEFAULT 'Backup',
            rewritten_by TEXT NOT NULL DEFAULT '',
            rewritten_at TEXT NOT NULL DEFAULT '',
            requested_summary_source TEXT NOT NULL DEFAULT '',
            summary_warning TEXT NOT NULL DEFAULT '',
            fallback_summary_source TEXT NOT NULL DEFAULT '',
            summary_timing_ms INTEGER NOT NULL DEFAULT 0,
            summary_provider_details TEXT NOT NULL DEFAULT '',
            last_ai_attempt_at TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS deleted_entries (
            key TEXT PRIMARY KEY,
            deleted_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_entries_created_at ON entries(created_at);
        CREATE INDEX IF NOT EXISTS idx_entries_repo_hash ON entries(repo_path, diff_hash);
        CREATE TABLE IF NOT EXISTS diff_buffer (
            day_key TEXT NOT NULL,
            datetime TEXT NOT NULL,
            repo TEXT NOT NULL,
            git_diff_text TEXT NOT NULL,
            git_diff_description TEXT NOT NULL,
            shown INTEGER NOT NULL DEFAULT 0,
            diff_hash TEXT NOT NULL,
            repo_path TEXT NOT NULL DEFAULT '',
            author TEXT NOT NULL DEFAULT 'Backup',
            summary_source TEXT NOT NULL DEFAULT '',
            rewritten_by TEXT NOT NULL DEFAULT '',
            rewritten_at TEXT NOT NULL DEFAULT '',
            requested_summary_source TEXT NOT NULL DEFAULT '',
            summary_warning TEXT NOT NULL DEFAULT '',
            fallback_summary_source TEXT NOT NULL DEFAULT '',
            summary_timing_ms INTEGER NOT NULL DEFAULT 0,
            summary_provider_details TEXT NOT NULL DEFAULT '',
            last_ai_attempt_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (day_key, repo, diff_hash)
        );
        CREATE INDEX IF NOT EXISTS idx_diff_buffer_day ON diff_buffer(day_key);
        CREATE TABLE IF NOT EXISTS ai_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_ai_attempts_recorded_at ON ai_attempts(recorded_at);
        """
    )
    connection.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
        (str(SCHEMA_VERSION),),
    )


def normalized_db_path(db_path: Path) -> Path:
    if db_path.suffix.lower() == ".json":
        return db_path.with_name("pyesis.db")
    return db_path


def legacy_json_path(db_path: Path) -> Path:
    candidate = db_path
    if db_path.suffix.lower() != ".json":
        candidate = db_path.parent / LEGACY_JSON_NAME
    return candidate


def migrate_legacy_json(db_path: Path) -> bool:
    target = normalized_db_path(db_path)
    if target.exists() and target.stat().st_size > 0:
        return False
    json_path = legacy_json_path(target)
    if db_path.suffix.lower() == ".json" and db_path.exists():
        json_path = db_path
    if not json_path.exists():
        return False
    try:
        raw = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(raw, dict):
        return False
    write_config(_config_from_payload(raw, include_entries=True), target)
    migrated_path = json_path.with_name(json_path.name + ".migrated")
    if json_path.resolve() != target.resolve():
        try:
            json_path.replace(migrated_path)
        except OSError:
            pass
    return True


def read_payload(db_path: Path, *, include_entries: bool) -> dict[str, Any] | None:
    target = normalized_db_path(db_path)
    migrate_legacy_json(target if db_path.suffix.lower() != ".json" else db_path)
    if not target.exists():
        migrate_sidecar_files(target)
        if not target.exists():
            return None
    with connect(target) as connection:
        initialize_schema(connection)
        settings = {str(row["key"]): json.loads(row["value"]) for row in connection.execute("SELECT key, value FROM settings")}
        if not settings:
            return None
        payload: dict[str, Any] = {
            **settings,
            "repos": [
                {"path": row["path"], "label": row["label"], "poll_seconds": int(row["poll_seconds"])}
                for row in connection.execute("SELECT path, label, poll_seconds FROM repos ORDER BY position")
            ],
            "deleted_entries": [
                {"key": row["key"], "deleted_at": row["deleted_at"]}
                for row in connection.execute("SELECT key, deleted_at FROM deleted_entries")
            ],
            "entries": [],
        }
        if include_entries:
            payload["entries"] = [
                {column: row[column] for column in ENTRY_COLUMNS}
                for row in connection.execute(f"SELECT {', '.join(ENTRY_COLUMNS)} FROM entries ORDER BY id")
            ]
    migrate_sidecar_files(target)
    return payload


def write_config(config: AppConfig, db_path: Path) -> None:
    from pyesis.git_monitor import is_noise_entry_record

    config.entries = [
        entry
        for entry in config.entries
        if not is_noise_entry_record(entry.summary, entry.diff_excerpt, entry.repo_path, entry.repo_label)
    ]
    target = normalized_db_path(db_path)
    with connect(target) as connection:
        initialize_schema(connection)
        connection.execute("DELETE FROM settings")
        connection.execute("DELETE FROM repos")
        connection.execute("DELETE FROM entries")
        connection.execute("DELETE FROM deleted_entries")
        connection.executemany(
            "INSERT INTO settings(key, value) VALUES (?, ?)",
            [(key, json.dumps(getattr(config, key))) for key in SETTING_COLUMNS],
        )
        connection.executemany(
            "INSERT INTO repos(position, path, label, poll_seconds) VALUES (?, ?, ?, ?)",
            [(index, repo.path, repo.label, int(repo.poll_seconds)) for index, repo in enumerate(config.repos)],
        )
        placeholders = ", ".join("?" for _ in ENTRY_COLUMNS)
        connection.executemany(
            f"INSERT INTO entries({', '.join(ENTRY_COLUMNS)}) VALUES ({placeholders})",
            [tuple(asdict(entry)[column] for column in ENTRY_COLUMNS) for entry in config.entries],
        )
        connection.executemany(
            "INSERT INTO deleted_entries(key, deleted_at) VALUES (?, ?)",
            [(item.key, item.deleted_at) for item in config.deleted_entries],
        )
        connection.commit()


def migrate_sidecar_files(db_path: Path) -> None:
    target = normalized_db_path(db_path)
    migrate_buffer_files(target, target.parent / "diff_buffers")
    migrate_ai_attempts(target, target.parent / "logs" / "ai_attempts.jsonl")


def list_buffer_days(db_path: Path) -> list[str]:
    target = normalized_db_path(db_path)
    migrate_sidecar_files(target)
    with connect(target) as connection:
        initialize_schema(connection)
        rows = connection.execute("SELECT DISTINCT day_key FROM diff_buffer ORDER BY day_key").fetchall()
    return [str(row["day_key"]) for row in rows]


def load_buffer_day(db_path: Path, day_key: str) -> list[dict[str, Any]]:
    target = normalized_db_path(db_path)
    migrate_sidecar_files(target)
    with connect(target) as connection:
        initialize_schema(connection)
        rows = connection.execute(
            "SELECT * FROM diff_buffer WHERE day_key = ? ORDER BY datetime, repo, diff_hash",
            (day_key,),
        ).fetchall()
    return [_buffer_row_to_item(row) for row in rows]


def replace_buffer_day(db_path: Path, day_key: str, items: list[dict[str, Any]]) -> None:
    target = normalized_db_path(db_path)
    with connect(target) as connection:
        initialize_schema(connection)
        connection.execute("DELETE FROM diff_buffer WHERE day_key = ?", (day_key,))
        for item in items:
            if _buffer_item_is_noise(item):
                continue
            connection.execute(
                """
                INSERT INTO diff_buffer (
                    day_key, datetime, repo, git_diff_text, git_diff_description, shown, diff_hash,
                    repo_path, author, summary_source, rewritten_by, rewritten_at, requested_summary_source,
                    summary_warning, fallback_summary_source, summary_timing_ms, summary_provider_details,
                    last_ai_attempt_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                _buffer_item_values(day_key, item),
            )
        connection.commit()


def upsert_buffer_item(db_path: Path, day_key: str, item: dict[str, Any]) -> None:
    if _buffer_item_is_noise(item):
        return
    target = normalized_db_path(db_path)
    with connect(target) as connection:
        initialize_schema(connection)
        connection.execute(
            """
            INSERT INTO diff_buffer (
                day_key, datetime, repo, git_diff_text, git_diff_description, shown, diff_hash,
                repo_path, author, summary_source, rewritten_by, rewritten_at, requested_summary_source,
                summary_warning, fallback_summary_source, summary_timing_ms, summary_provider_details,
                last_ai_attempt_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(day_key, repo, diff_hash) DO UPDATE SET
                datetime=excluded.datetime,
                git_diff_text=excluded.git_diff_text,
                git_diff_description=excluded.git_diff_description,
                shown=excluded.shown,
                repo_path=excluded.repo_path,
                author=excluded.author,
                summary_source=excluded.summary_source,
                rewritten_by=excluded.rewritten_by,
                rewritten_at=excluded.rewritten_at,
                requested_summary_source=excluded.requested_summary_source,
                summary_warning=excluded.summary_warning,
                fallback_summary_source=excluded.fallback_summary_source,
                summary_timing_ms=excluded.summary_timing_ms,
                summary_provider_details=excluded.summary_provider_details,
                last_ai_attempt_at=excluded.last_ai_attempt_at
            """,
            _buffer_item_values(day_key, item),
        )
        connection.commit()


def delete_buffer_days_before(db_path: Path, keep_from: str) -> None:
    target = normalized_db_path(db_path)
    with connect(target) as connection:
        initialize_schema(connection)
        connection.execute("DELETE FROM diff_buffer WHERE day_key < ?", (keep_from,))
        connection.commit()


def delete_buffer_day(db_path: Path, day_key: str) -> None:
    target = normalized_db_path(db_path)
    with connect(target) as connection:
        initialize_schema(connection)
        connection.execute("DELETE FROM diff_buffer WHERE day_key = ?", (day_key,))
        connection.commit()


def purge_noise_buffers(db_path: Path) -> int:
    target = normalized_db_path(db_path)
    migrate_sidecar_files(target)
    removed = 0
    with connect(target) as connection:
        initialize_schema(connection)
        rows = connection.execute("SELECT day_key, repo, diff_hash, git_diff_text, git_diff_description FROM diff_buffer").fetchall()
        for row in rows:
            item = {"gitDiffText": row["git_diff_text"], "gitDiffDescription": row["git_diff_description"]}
            if not _buffer_item_is_noise(item):
                continue
            connection.execute(
                "DELETE FROM diff_buffer WHERE day_key = ? AND repo = ? AND diff_hash = ?",
                (row["day_key"], row["repo"], row["diff_hash"]),
            )
            removed += 1
        connection.commit()
    return removed


def append_ai_attempt(db_path: Path, payload: dict[str, Any]) -> None:
    from pyesis.git_monitor import is_noise_work_text

    blob = json.dumps(payload, ensure_ascii=True)
    if is_noise_work_text(blob):
        return
    recorded_at = str(payload.get("timestamp", "")).strip()
    target = normalized_db_path(db_path)
    with connect(target) as connection:
        initialize_schema(connection)
        connection.execute(
            "INSERT INTO ai_attempts(recorded_at, payload) VALUES (?, ?)",
            (recorded_at, blob),
        )
        connection.commit()


def load_ai_attempts(db_path: Path, limit: int) -> list[dict[str, Any]]:
    target = normalized_db_path(db_path)
    migrate_sidecar_files(target)
    with connect(target) as connection:
        initialize_schema(connection)
        rows = connection.execute(
            "SELECT payload FROM ai_attempts ORDER BY id DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in reversed(rows):
        try:
            payload = json.loads(row["payload"])
        except Exception:
            continue
        if isinstance(payload, dict):
            items.append(payload)
    return items


def migrate_buffer_files(db_path: Path, buffer_dir: Path) -> None:
    if not buffer_dir.exists():
        return
    from datetime import datetime

    target = normalized_db_path(db_path)
    with connect(target) as connection:
        initialize_schema(connection)
        for path in sorted(buffer_dir.glob("*.json")):
            try:
                datetime.fromisoformat(path.stem)
            except ValueError:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, list):
                continue
            existing = connection.execute("SELECT COUNT(*) AS count FROM diff_buffer WHERE day_key = ?", (path.stem,)).fetchone()
            if int(existing["count"] if existing is not None else 0) == 0:
                for raw in data:
                    if not isinstance(raw, dict) or _buffer_item_is_noise(raw):
                        continue
                    if not str(raw.get("repo", "")).strip() or not str(raw.get("gitDiffText", "")):
                        continue
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO diff_buffer (
                            day_key, datetime, repo, git_diff_text, git_diff_description, shown, diff_hash,
                            repo_path, author, summary_source, rewritten_by, rewritten_at, requested_summary_source,
                            summary_warning, fallback_summary_source, summary_timing_ms, summary_provider_details,
                            last_ai_attempt_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        _buffer_item_values(path.stem, raw),
                    )
            connection.commit()
            migrated = path.with_name(path.name + ".migrated")
            try:
                path.replace(migrated)
            except OSError:
                pass


def migrate_ai_attempts(db_path: Path, log_path: Path) -> None:
    if not log_path.exists():
        return
    target = normalized_db_path(db_path)
    with connect(target) as connection:
        initialize_schema(connection)
        existing = connection.execute("SELECT COUNT(*) AS count FROM ai_attempts").fetchone()
        if int(existing["count"] if existing is not None else 0) > 0:
            return
        from pyesis.git_monitor import is_noise_work_text

        for raw_line in log_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or is_noise_work_text(line):
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            connection.execute(
                "INSERT INTO ai_attempts(recorded_at, payload) VALUES (?, ?)",
                (str(payload.get("timestamp", "")).strip(), json.dumps(payload, ensure_ascii=True)),
            )
        connection.commit()
    migrated = log_path.with_name(log_path.name + ".migrated")
    try:
        log_path.replace(migrated)
    except OSError:
        pass


def _buffer_item_is_noise(item: dict[str, Any]) -> bool:
    from pyesis.git_monitor import is_noise_work_text

    return is_noise_work_text(str(item.get("gitDiffText", ""))) or is_noise_work_text(str(item.get("gitDiffDescription", "")))


def _buffer_item_values(day_key: str, item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        day_key,
        str(item.get("datetime", "")),
        str(item.get("repo", "")).strip(),
        str(item.get("gitDiffText", "")),
        str(item.get("gitDiffDescription", "")),
        1 if item.get("shown") else 0,
        str(item.get("diffHash", "")),
        str(item.get("repoPath", "")),
        str(item.get("author", "Backup")),
        str(item.get("summarySource", "")).strip().lower(),
        str(item.get("rewrittenBy", "")),
        str(item.get("rewrittenAt", "")),
        str(item.get("requestedSummarySource", "")).strip().lower(),
        str(item.get("summaryWarning", "")),
        str(item.get("fallbackSummarySource", "")).strip().lower(),
        max(0, int(item.get("summaryTimingMs", 0) or 0)),
        str(item.get("summaryProviderDetails", "")),
        str(item.get("lastAiAttemptAt", "")),
    )


def _buffer_row_to_item(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "datetime": str(row["datetime"]),
        "repo": str(row["repo"]),
        "gitDiffText": str(row["git_diff_text"]),
        "gitDiffDescription": str(row["git_diff_description"]),
        "shown": bool(row["shown"]),
        "diffHash": str(row["diff_hash"]),
        "repoPath": str(row["repo_path"]),
        "author": str(row["author"]),
        "summarySource": str(row["summary_source"]),
        "rewrittenBy": str(row["rewritten_by"]),
        "rewrittenAt": str(row["rewritten_at"]),
        "requestedSummarySource": str(row["requested_summary_source"]),
        "summaryWarning": str(row["summary_warning"]),
        "fallbackSummarySource": str(row["fallback_summary_source"]),
        "summaryTimingMs": int(row["summary_timing_ms"]),
        "summaryProviderDetails": str(row["summary_provider_details"]),
        "lastAiAttemptAt": str(row["last_ai_attempt_at"]),
    }


def _config_from_payload(data: dict[str, Any], *, include_entries: bool) -> AppConfig:
    raw_deleted = [_decode_deleted_entry(item) for item in data.get("deleted_entries", [])]
    deleted_entries = [entry for entry in raw_deleted if entry is not None]
    entries: list[EntryRecord] = []
    if include_entries:
        for item in data.get("entries", []):
            if isinstance(item, dict):
                entries.append(_decode_entry(item))
    return _base_config_from_data(data, entries, deleted_entries)
