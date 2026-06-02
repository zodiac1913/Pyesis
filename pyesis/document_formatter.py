from __future__ import annotations

from collections import defaultdict
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


def _week_end_date(week_start: datetime, week_end_day: str) -> datetime:
    end_index = DAY_ORDER.index(week_end_day)
    return week_start + timedelta(days=end_index)


def _group_entries(entries: list[EntryRecord]) -> dict[str, dict[str, list[EntryRecord]]]:
    grouped: dict[str, dict[str, list[EntryRecord]]] = defaultdict(lambda: defaultdict(list))
    for entry in sorted(entries, key=lambda item: item.created_at):
        grouped[entry.week_start_iso][entry.day_name].append(entry)
    return grouped


def _group_entries_by_repo(entries: list[EntryRecord]) -> dict[str, list[EntryRecord]]:
    by_repo: dict[str, list[EntryRecord]] = defaultdict(list)
    for entry in entries:
        by_repo[entry.repo_label].append(entry)
    return {
        repo_label: sorted(repo_entries, key=lambda item: item.created_at)
        for repo_label, repo_entries in sorted(by_repo.items(), key=lambda item: item[0].lower())
    }


def render_plain_text(config: AppConfig) -> str:
    grouped = _group_entries(config.entries)
    blocks: list[str] = []

    for week_start_iso in sorted(grouped.keys()):
        _append_week_header(blocks, week_start_iso, config.week_end_day)
        _append_week_entries(blocks, grouped[week_start_iso])

    return "\n".join(blocks).strip() + "\n"


def _append_week_header(blocks: list[str], week_start_iso: str, week_end_day: str) -> None:
    week_start = datetime.fromisoformat(week_start_iso)
    week_end = _week_end_date(week_start, week_end_day)
    blocks.extend(["", "", "", "", "", ""])
    blocks.append(f"({week_end.strftime('%Y %b %d')})")
    blocks.append("What I worked on for this week:")
    blocks.append("")


def _append_week_entries(blocks: list[str], day_map: dict[str, list[EntryRecord]]) -> None:
    for day_name in DAY_ORDER:
        entries = day_map.get(day_name)
        if not entries:
            continue
        blocks.append(f"@{day_name}")
        _append_day_repo_entries(blocks, entries)
        blocks.append("")


def _append_day_repo_entries(blocks: list[str], entries: list[EntryRecord]) -> None:
    for repo_label, repo_entries in _group_entries_by_repo(entries).items():
        blocks.append(f"\t• {repo_label}:")
        for entry in repo_entries:
            for line in _summary_lines(entry.summary):
                blocks.append(f"\t\t• {line}")


def export_docx(config: AppConfig, output_dir: Path, file_name: str | None = None) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    document = Document()
    grouped = _group_entries(config.entries)

    for week_start_iso in sorted(grouped.keys()):
        _write_week_block(document, week_start_iso, grouped[week_start_iso], config.week_end_day)

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
    week_end_day: str,
) -> None:
    week_start = datetime.fromisoformat(week_start_iso)
    week_end = _week_end_date(week_start, week_end_day)
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