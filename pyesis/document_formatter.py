from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import re

from docx import Document

from pyesis.config import AppConfig, EntryRecord
from pyesis.git_monitor import summarize_file_changes


DAY_ORDER = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]
LIST_BULLET_LEVEL_THREE = "List Bullet 3"


@dataclass(frozen=True)
class RenderedTextChunk:
    text: str
    tags: tuple[str, ...] = ()


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


def render_plain_text(config: AppConfig, now: datetime | None = None) -> str:
    return "".join(chunk.text for chunk in render_text_chunks(config, now=now)).rstrip("\n") + "\n"


def render_text_chunks(
    config: AppConfig,
    entry_tag_resolver=None,
    warning_comment_resolver=None,
    now: datetime | None = None,
) -> list[RenderedTextChunk]:
    active_week_start_iso, active_week_entries = _active_week_entries(config.entries, config.week_end_day, now=now)
    chunks: list[RenderedTextChunk] = []

    _append_week_header(chunks, active_week_start_iso)
    if active_week_entries:
        _append_week_entries(chunks, active_week_entries, entry_tag_resolver, warning_comment_resolver)

    return chunks


def _append_week_header(chunks: list[RenderedTextChunk], week_start_iso: str) -> None:
    week_start = datetime.fromisoformat(week_start_iso)
    week_end = _week_end_date(week_start)
    chunks.extend(RenderedTextChunk("\n") for _ in range(6))
    chunks.append(RenderedTextChunk(f"({week_end.strftime('%Y %b %d')})\n"))
    chunks.append(RenderedTextChunk("What I worked on for this week:\n"))
    chunks.append(RenderedTextChunk("\n"))


def _append_week_entries(
    chunks: list[RenderedTextChunk],
    day_map: dict[str, list[EntryRecord]],
    entry_tag_resolver,
    warning_comment_resolver,
) -> None:
    for day_name in DAY_ORDER:
        entries = day_map.get(day_name)
        if not entries:
            continue
        chunks.append(RenderedTextChunk(f"@{day_name}\n"))
        _append_day_repo_entries(chunks, entries, entry_tag_resolver, warning_comment_resolver)
        chunks.append(RenderedTextChunk("\n"))


def _append_day_repo_entries(
    chunks: list[RenderedTextChunk],
    entries: list[EntryRecord],
    entry_tag_resolver,
    warning_comment_resolver,
) -> None:
    for repo_label, repo_entries in _group_entries_by_repo(entries).items():
        chunks.append(RenderedTextChunk(f"\t• {repo_label}:\n"))
        for entry in repo_entries:
            tags = _resolved_entry_tags(entry, entry_tag_resolver)
            for line in _summary_lines(_summary_body_text(entry.summary)):
                chunks.append(RenderedTextChunk(f"\t\t• {line}\n", tags=tags))
            evidence = _entry_evidence_line(entry)
            if evidence:
                chunks.append(RenderedTextChunk(f"\t\t  Evidence: {evidence}\n", tags=tags + ("evidence",)))
            for label, change_line in _change_detail_lines(entry.diff_excerpt):
                chunks.append(RenderedTextChunk(f"\t\t  {label}: {change_line}\n", tags=tags + ("evidence",)))
            warning_comment = _resolved_warning_comment(entry, warning_comment_resolver)
            if warning_comment:
                chunks.append(RenderedTextChunk(f"\t\t  {warning_comment}\n", tags=tags + ("ai-comment",)))


def _resolved_entry_tags(entry: EntryRecord, entry_tag_resolver) -> tuple[str, ...]:
    if entry_tag_resolver is not None:
        resolved = entry_tag_resolver(entry)
        if resolved is not None:
            return tuple(resolved)
    return ("heuristic",) if _is_heuristic_entry(entry) else ()


def _resolved_warning_comment(entry: EntryRecord, warning_comment_resolver) -> str:
    if warning_comment_resolver is None:
        return ""
    return str(warning_comment_resolver(entry) or "").strip()


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
        for idx, line in enumerate(_summary_lines(_summary_body_text(entry.summary))):
            style = "List Bullet 2" if idx == 0 else LIST_BULLET_LEVEL_THREE
            entry_paragraph = document.add_paragraph(style=style)
            entry_paragraph.paragraph_format.left_indent = None
            entry_paragraph.paragraph_format.first_line_indent = None
            entry_paragraph.add_run(line)
        evidence = _entry_evidence_line(entry)
        if evidence:
            evidence_paragraph = document.add_paragraph(style=LIST_BULLET_LEVEL_THREE)
            evidence_paragraph.paragraph_format.left_indent = None
            evidence_paragraph.paragraph_format.first_line_indent = None
            evidence_paragraph.add_run(f"Evidence: {evidence}")
        for label, change_line in _change_detail_lines(entry.diff_excerpt):
            paragraph = document.add_paragraph(style=LIST_BULLET_LEVEL_THREE)
            paragraph.paragraph_format.left_indent = None
            paragraph.paragraph_format.first_line_indent = None
            paragraph.add_run(f"{label}: {change_line}")


def _summary_lines(summary: str) -> list[str]:
    lines = [line.strip().lstrip("-• ").strip() for line in summary.splitlines() if line.strip()]
    return lines or [summary.strip()]


def _entry_evidence_line(entry: EntryRecord) -> str:
    inline_evidence = _summary_inline_evidence(entry.summary)
    if inline_evidence and _evidence_has_line_number(inline_evidence):
        return inline_evidence

    changes = summarize_file_changes(entry.diff_excerpt)
    for change in changes:
        if change.added_line_samples:
            line_no, snippet = change.added_line_samples[0]
            return f"{change.path}:{line_no} \"{snippet}\""
        if change.added_samples:
            return f"{change.path} \"{change.added_samples[0]}\""
    return inline_evidence


def _summary_body_text(summary: str) -> str:
    body, _separator, _evidence = summary.partition(" Evidence: ")
    stripped = body.strip()
    return stripped or summary.strip()


def _summary_inline_evidence(summary: str) -> str:
    _body, separator, evidence = summary.partition(" Evidence: ")
    if not separator:
        return ""
    return evidence.strip().rstrip(".")


def _evidence_has_line_number(evidence: str) -> bool:
    return bool(re.search(r"^[^\s:]+:\d+\s+\"", evidence.strip(), flags=re.IGNORECASE))


def _change_detail_lines(diff_excerpt: str) -> list[tuple[str, str]]:
    removed_lines: list[str] = []
    added_lines: list[str] = []
    fallback_pair: tuple[str, str] | None = None
    added_fallback = ""
    removed_fallback = ""

    def collect_change_details() -> list[tuple[str, str]]:
        if removed_lines and added_lines:
            pair = _first_null_check_pair(removed_lines, added_lines) or _first_changed_line_pair(removed_lines, added_lines)
            if pair is not None:
                return [("Before", pair[0]), ("After", pair[1])]
        if added_lines:
            return [("After", added_lines[0])]
        if removed_lines:
            return [("Before", removed_lines[0])]
        return []

    def flush() -> list[tuple[str, str]]:
        nonlocal removed_lines, added_lines, fallback_pair, added_fallback, removed_fallback
        details = collect_change_details()
        if not details:
            if added_lines and not added_fallback:
                added_fallback = added_lines[0]
            if removed_lines and not removed_fallback:
                removed_fallback = removed_lines[0]
        removed_lines = []
        added_lines = []
        return details

    for raw_line in diff_excerpt.splitlines():
        if raw_line.startswith(("@@", " ")):
            details = flush()
            if details:
                return details
            continue

        if raw_line.startswith(("diff --git ", "index ", "--- ", "+++ ", "\\")):
            continue
        if raw_line.startswith("-"):
            removed_lines.append(raw_line[1:].strip())
            continue
        if raw_line.startswith("+"):
            added_lines.append(raw_line[1:].strip())

    details = flush()
    if details:
        return details
    if fallback_pair is not None:
        return [("Before", fallback_pair[0]), ("After", fallback_pair[1])]
    if added_fallback:
        return [("After", added_fallback)]
    if removed_fallback:
        return [("Before", removed_fallback)]
    return []


def _first_null_check_pair(removed_lines: list[str], added_lines: list[str]) -> tuple[str, str] | None:
    if not removed_lines or not added_lines:
        return None

    for before_line in removed_lines:
        if not before_line:
            continue
        for after_line in added_lines:
            if not after_line or before_line == after_line:
                continue
            if _looks_like_null_check_change(before_line, after_line):
                return before_line, after_line
    return None


def _first_changed_line_pair(removed_lines: list[str], added_lines: list[str]) -> tuple[str, str] | None:
    if not removed_lines or not added_lines:
        return None

    for before_line in removed_lines:
        if not before_line:
            continue
        for after_line in added_lines:
            if not after_line or before_line == after_line:
                continue
            return before_line, after_line
    return None


def _looks_like_null_check_change(before_line: str, after_line: str) -> bool:
    del before_line
    lowered = after_line.lower()
    null_markers = (
        "??",
        "?.",
        "== null",
        "!= null",
        " is none",
        " is not none",
        ".notempty(",
        " if ",
        " else ",
    )
    if any(marker in lowered for marker in null_markers):
        return True
    # Match C# / JS / Python ternary-like guards that often encode null fallback.
    return bool(re.search(r"\?.+:.+", after_line))