from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from docx import Document

from pyesis.config import AppConfig, EntryRecord


DAY_ORDER = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]


@dataclass(frozen=True)
class RenderedTextChunk:
    text: str
    tag: str = ""


def _week_end_day_index(week_end_day: str) -> int:
    try:
        return DAY_ORDER.index(week_end_day)
    except ValueError:
        return DAY_ORDER.index("Thursday")


def _active_week_start(week_end_day: str, now: datetime | None = None) -> datetime:
    reference = now or datetime.now()
    end_index = _week_end_day_index(week_end_day)
    week_end = (reference + timedelta(days=(end_index - reference.weekday()) % 7)).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return week_end - timedelta(days=6)


def _week_end_date(week_start: datetime) -> datetime:
    return week_start + timedelta(days=6)


def _group_entries(entries: list[EntryRecord]) -> dict[str, dict[str, list[EntryRecord]]]:
    grouped: dict[str, dict[str, list[EntryRecord]]] = defaultdict(lambda: defaultdict(list))
    for entry in sorted(entries, key=lambda item: item.created_at):
        grouped[entry.week_start_iso][entry.day_name].append(entry)
    return grouped


def _active_week_entries(
    entries: list[EntryRecord],
    week_end_day: str,
    now: datetime | None = None,
) -> tuple[str, dict[str, list[EntryRecord]]]:
    active_week_start_iso = _active_week_start(week_end_day, now).isoformat()
    grouped = _group_entries(entries)
    return active_week_start_iso, grouped.get(active_week_start_iso, {})


def _group_entries_by_repo(entries: list[EntryRecord]) -> dict[str, list[EntryRecord]]:
    by_repo: dict[str, list[EntryRecord]] = defaultdict(list)
    for entry in entries:
        by_repo[entry.repo_label].append(entry)
    return {
        repo_label: sorted(repo_entries, key=lambda item: item.created_at)
        for repo_label, repo_entries in sorted(by_repo.items(), key=lambda item: item[0].lower())
    }


def render_plain_text(config: AppConfig) -> str:
    return "".join(chunk.text for chunk in render_text_chunks(config)).rstrip("\n") + "\n"


def render_text_chunks(config: AppConfig) -> list[RenderedTextChunk]:
    active_week_start_iso, active_week_entries = _active_week_entries(config.entries, config.week_end_day)
    chunks: list[RenderedTextChunk] = []

    _append_week_header(chunks, active_week_start_iso)
    if active_week_entries:
        _append_week_entries(chunks, active_week_entries)

    return chunks


def _append_week_header(chunks: list[RenderedTextChunk], week_start_iso: str) -> None:
    week_start = datetime.fromisoformat(week_start_iso)
    week_end = _week_end_date(week_start)
    chunks.extend(RenderedTextChunk("\n") for _ in range(6))
    chunks.append(RenderedTextChunk(f"({week_end.strftime('%Y %b %d')})\n"))
    chunks.append(RenderedTextChunk("What I worked on for this week:\n"))
    chunks.append(RenderedTextChunk("\n"))


def _append_week_entries(chunks: list[RenderedTextChunk], day_map: dict[str, list[EntryRecord]]) -> None:
    for day_name in DAY_ORDER:
        entries = day_map.get(day_name)
        if not entries:
            continue
        chunks.append(RenderedTextChunk(f"@{day_name}\n"))
        _append_day_repo_entries(chunks, entries)
        chunks.append(RenderedTextChunk("\n"))


def _append_day_repo_entries(chunks: list[RenderedTextChunk], entries: list[EntryRecord]) -> None:
    for repo_label, repo_entries in _group_entries_by_repo(entries).items():
        chunks.append(RenderedTextChunk(f"\t• {repo_label}:\n"))
        for entry in repo_entries:
            tag = "heuristic" if _is_heuristic_entry(entry) else ""
            for line in _summary_lines(entry.summary):
                chunks.append(RenderedTextChunk(f"\t\t• {line}\n", tag=tag))


def _is_heuristic_entry(entry: EntryRecord) -> bool:
    source = (entry.summary_source or "").strip().lower()
    if source:
        return source == "heuristic"
    return entry.author == "Backup"


def export_docx(config: AppConfig, output_dir: Path, file_name: str | None = None) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    document = Document()
    active_week_start_iso, active_week_entries = _active_week_entries(config.entries, config.week_end_day)
    _write_week_block(document, active_week_start_iso, active_week_entries)

    if file_name:
        target = output_dir / file_name
    else:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = output_dir / f"weekly_changes_{timestamp}.docx"
    document.save(target)
    return target


def _write_week_block(
    document: Document,
    week_start_iso: str,
    day_map: dict[str, list[EntryRecord]],
) -> None:
    week_start = datetime.fromisoformat(week_start_iso)
    week_end = _week_end_date(week_start)
    for _ in range(6):
        document.add_paragraph("")
    document.add_paragraph(f"({week_end.strftime('%Y %b %d')})")
    document.add_paragraph("What I worked on for this week:")

    for day_name in DAY_ORDER:
        entries = day_map.get(day_name)
        if not entries:
            continue
        document.add_paragraph(f"@{day_name}")
        for repo_label, repo_entries in _group_entries_by_repo(entries).items():
            repo_paragraph = document.add_paragraph(style="List Bullet")
            repo_paragraph.paragraph_format.left_indent = None
            repo_paragraph.paragraph_format.first_line_indent = None
            repo_paragraph.add_run(f"{repo_label}:")
            _write_repo_entries(document, repo_entries)


def _write_repo_entries(document: Document, repo_entries: list[EntryRecord]) -> None:
    for entry in repo_entries:
        for idx, line in enumerate(_summary_lines(entry.summary)):
            style = "List Bullet 2" if idx == 0 else "List Bullet 3"
            entry_paragraph = document.add_paragraph(style=style)
            entry_paragraph.paragraph_format.left_indent = None
            entry_paragraph.paragraph_format.first_line_indent = None
            entry_paragraph.add_run(line)


def _summary_lines(summary: str) -> list[str]:
    lines = [line.strip().lstrip("-• ").strip() for line in summary.splitlines() if line.strip()]
    return lines or [summary.strip()]