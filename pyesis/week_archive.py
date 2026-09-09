from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile

import py7zr

from pyesis.config import EntryRecord


DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
ARCHIVE_NAME_PREFIX = "PyesisWeek_"


def archive_file_name(week_start_date: str) -> str:
    return f"{ARCHIVE_NAME_PREFIX}{week_start_date}.7z"


def active_week_start(week_end_day: str, now: datetime) -> datetime:
    end_index = DAY_ORDER.index(week_end_day) if week_end_day in DAY_ORDER else DAY_ORDER.index("Thursday")
    week_end = (now + timedelta(days=(end_index - now.weekday()) % 7)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return week_end - timedelta(days=6)


def archive_completed_weeks(
    entries: list[EntryRecord],
    *,
    week_end_day: str = "Thursday",
    archive_dir: Path,
    buffer_dir: Path | None = None,
    now: datetime | None = None,
) -> list[Path]:
    current_time = now or datetime.now()
    active_start = active_week_start(week_end_day, current_time)
    grouped: dict[str, list[EntryRecord]] = defaultdict(list)
    for entry in entries:
        week_start = _entry_week_start(entry)
        if week_start is None or week_start >= active_start:
            continue
        grouped[week_start.strftime("%Y-%m-%d")].append(entry)

    written: list[Path] = []
    archive_dir.mkdir(parents=True, exist_ok=True)
    for week_start_date, week_entries in sorted(grouped.items()):
        path = _write_week_archive(
            week_start_date,
            week_entries,
            week_end_day=week_end_day,
            archive_dir=archive_dir,
            buffer_dir=buffer_dir,
            archived_at=current_time,
        )
        if path is not None:
            written.append(path)
    return written


def _entry_week_start(entry: EntryRecord) -> datetime | None:
    raw = entry.week_start_iso.strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed.replace(hour=0, minute=0, second=0, microsecond=0)


def _write_week_archive(
    week_start_date: str,
    entries: list[EntryRecord],
    *,
    week_end_day: str,
    archive_dir: Path,
    buffer_dir: Path | None,
    archived_at: datetime,
) -> Path | None:
    if not entries:
        return None
    target = archive_dir / archive_file_name(week_start_date)
    if target.exists() and target.stat().st_size > 0:
        return None

    week_start = datetime.fromisoformat(week_start_date)
    payload = {
        "pyesis_week_archive": True,
        "archived_at": archived_at.isoformat(timespec="seconds"),
        "week_start_iso": week_start.isoformat(),
        "week_end_day": week_end_day,
        "entries": [asdict(entry) for entry in entries],
    }
    buffer_paths = _buffer_paths_for_week(week_start, buffer_dir)

    with tempfile.TemporaryDirectory() as temp_dir:
        staging = Path(temp_dir)
        (staging / "week.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
        if buffer_paths:
            buffer_stage = staging / "diff_buffers"
            buffer_stage.mkdir()
            for source in buffer_paths:
                (buffer_stage / source.name).write_bytes(source.read_bytes())
        with py7zr.SevenZipFile(target, "w") as archive:
            archive.write(staging / "week.json", "week.json")
            for staged in sorted((staging / "diff_buffers").glob("*.json")) if buffer_paths else []:
                archive.write(staged, f"diff_buffers/{staged.name}")
    return target


def _buffer_paths_for_week(week_start: datetime, buffer_dir: Path | None) -> list[Path]:
    if buffer_dir is None or not buffer_dir.exists():
        return []
    paths: list[Path] = []
    for offset in range(7):
        day_name = (week_start + timedelta(days=offset)).strftime("%Y-%m-%d")
        path = buffer_dir / f"{day_name}.json"
        if path.is_file():
            paths.append(path)
    return paths
