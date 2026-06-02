from __future__ import annotations

from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
from typing import Any


STATE_PATH = Path("pyesis_state.json")
NEAR_DUP_DIFF_SIMILARITY_THRESHOLD = 0.80


@dataclass
class RepoConfig:
    path: str
    label: str
    poll_seconds: int = 120


@dataclass
class EntryRecord:
    repo_label: str
    repo_path: str
    created_at: str
    day_name: str
    week_start_iso: str
    summary: str
    diff_hash: str
    diff_excerpt: str


@dataclass
class AppConfig:
    week_end_day: str = "Thursday"
    theme_mode: str = "system"
    high_contrast: bool = False
    ui_font_size: int = 11
    auto_export_time: str = ""
    last_auto_export_date: str = ""
    repos: list[RepoConfig] = field(default_factory=list)
    entries: list[EntryRecord] = field(default_factory=list)


def dedupe_entries(entries: list[EntryRecord]) -> list[EntryRecord]:
    deduped: list[EntryRecord] = []
    deduped_file_fp: list[list[str]] = []
    seen_diff: set[tuple[str, str, str]] = set()
    seen_legacy: set[tuple[str, str, str, str, str]] = set()
    seen_semantic: set[tuple[str, str, str, str, tuple[str, ...]]] = set()

    for entry in sorted(entries, key=lambda item: item.created_at):
        if _is_diff_duplicate(entry, seen_diff):
            continue
        if _is_near_diff_duplicate(entry, deduped):
            continue
        if _is_legacy_duplicate(entry, seen_legacy):
            continue

        entry_fp = _entry_files_fingerprint(entry)
        if _is_semantic_duplicate(entry, entry_fp, seen_semantic):
            continue

        _merge_or_append_entry(entry, entry_fp, deduped, deduped_file_fp)

    return deduped


def _is_diff_duplicate(entry: EntryRecord, seen_diff: set[tuple[str, str, str]]) -> bool:
    diff_fingerprint = _entry_diff_fingerprint(entry)
    if not diff_fingerprint:
        return False

    day_key = entry.created_at[:10] if len(entry.created_at) >= 10 else ""
    diff_key = (entry.repo_path, day_key, diff_fingerprint)
    if diff_key in seen_diff:
        return True
    seen_diff.add(diff_key)
    return False


def _entry_diff_fingerprint(entry: EntryRecord) -> str:
    diff_hash = entry.diff_hash.strip()
    if diff_hash:
        return f"hash:{diff_hash}"

    excerpt = entry.diff_excerpt.strip()
    if not excerpt:
        return ""
    return f"excerpt:{hashlib.sha256(excerpt.encode('utf-8')).hexdigest()}"


def _entry_files_fingerprint(entry: EntryRecord) -> list[str]:
    excerpt_files = _excerpt_files_fingerprint(entry.diff_excerpt)
    if excerpt_files:
        return excerpt_files
    return _summary_files_fingerprint(entry.summary)


def _excerpt_files_fingerprint(diff_excerpt: str) -> list[str]:
    files: list[str] = []
    for line in diff_excerpt.splitlines():
        if not line.startswith("+++ b/"):
            continue
        path = line.removeprefix("+++ b/").strip()
        if path == "/dev/null":
            continue
        normalized = path.lower()
        if normalized not in files:
            files.append(normalized)
    return files[:16]


def _is_near_diff_duplicate(entry: EntryRecord, deduped: list[EntryRecord]) -> bool:
    excerpt = entry.diff_excerpt.strip()
    entry_files = _entry_files_fingerprint(entry)
    if not excerpt:
        return False

    for existing in reversed(deduped):
        if not _same_repo_week_cross_day(existing, entry):
            continue

        existing_excerpt = existing.diff_excerpt.strip()
        if not existing_excerpt:
            continue
        existing_files = _entry_files_fingerprint(existing)
        if not _fingerprint_overlap(entry_files, existing_files):
            continue

        if _diff_similarity(excerpt, existing_excerpt) >= NEAR_DUP_DIFF_SIMILARITY_THRESHOLD:
            return True
    return False


def _same_repo_week_cross_day(existing: EntryRecord, entry: EntryRecord) -> bool:
    if existing.repo_path != entry.repo_path:
        return False
    if existing.week_start_iso != entry.week_start_iso:
        return False

    existing_day = existing.created_at[:10] if len(existing.created_at) >= 10 else ""
    entry_day = entry.created_at[:10] if len(entry.created_at) >= 10 else ""
    return bool(existing_day and entry_day and existing_day != entry_day)


def _diff_similarity(left_excerpt: str, right_excerpt: str) -> float:
    return SequenceMatcher(None, left_excerpt, right_excerpt).ratio()


def _is_legacy_duplicate(entry: EntryRecord, seen_legacy: set[tuple[str, str, str, str, str]]) -> bool:
    legacy_key = (
        entry.repo_path,
        entry.week_start_iso,
        entry.day_name,
        entry.summary.strip(),
        entry.diff_excerpt.strip(),
    )
    if legacy_key in seen_legacy:
        return True
    seen_legacy.add(legacy_key)
    return False


def _is_semantic_duplicate(
    entry: EntryRecord,
    entry_fp: list[str],
    seen_semantic: set[tuple[str, str, str, str, tuple[str, ...]]],
) -> bool:
    semantic_key = (
        entry.repo_path,
        entry.week_start_iso,
        entry.day_name,
        _summary_prefix40_normalized(entry.summary),
        tuple(entry_fp),
    )
    if semantic_key in seen_semantic:
        return True
    seen_semantic.add(semantic_key)
    return False


def _merge_or_append_entry(
    entry: EntryRecord,
    entry_fp: list[str],
    deduped: list[EntryRecord],
    deduped_file_fp: list[list[str]],
) -> None:
    if entry_fp:
        for idx, existing in enumerate(deduped):
            if (
                existing.repo_path == entry.repo_path
                and existing.week_start_iso == entry.week_start_iso
                and existing.day_name == entry.day_name
                and _fingerprint_overlap(entry_fp, deduped_file_fp[idx])
            ):
                deduped[idx] = entry
                deduped_file_fp[idx] = entry_fp
                return

    deduped.append(entry)
    deduped_file_fp.append(entry_fp)


def _summary_prefix40_normalized(summary: str) -> str:
    cleaned = summary.lower()
    cleaned = re.sub(r"\bacross\s+\d+\s+files?\b", "across files", cleaned)
    cleaned = re.sub(r"\b\d+\s+additions?\b", "additions", cleaned)
    cleaned = re.sub(r"\b\d+\s+removals?\b", "removals", cleaned)
    cleaned = re.sub(r"\bmodified\s+\d+\s+files?\b", "modified files", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:40]


def _summary_files_fingerprint(summary: str) -> list[str]:
    files: list[str] = []
    for line in summary.splitlines():
        text = line.strip()
        if text.startswith(("Updated ", "Created ", "Removed ", "Renamed ")):
            path = text.split(" ", 1)[1]
            path = re.split(r"\s+by\s+|\s+while\s+|\s+and\s+", path, maxsplit=1)[0]
            path = re.sub(r"\s*\([^)]*\)", "", path).strip(" .")
            if path and path not in files:
                files.append(path.lower())
    return files[:8]


def _fingerprint_overlap(current: list[str], previous: list[str]) -> bool:
    if not current or not previous:
        return False

    current_set = set(current)
    previous_set = set(previous)
    intersect = current_set & previous_set
    if not intersect:
        return False

    # Treat subset/superset as the same rolling change series.
    if current_set <= previous_set or previous_set <= current_set:
        return True

    union_size = len(current_set | previous_set)
    if union_size == 0:
        return False
    jaccard = len(intersect) / union_size
    return jaccard >= 0.6


def _decode_repo(item: dict[str, Any]) -> RepoConfig:
    return RepoConfig(
        path=item["path"],
        label=item.get("label") or Path(item["path"]).name,
        poll_seconds=int(item.get("poll_seconds", 120)),
    )


def _decode_entry(item: dict[str, Any]) -> EntryRecord:
    return EntryRecord(
        repo_label=item["repo_label"],
        repo_path=item["repo_path"],
        created_at=item["created_at"],
        day_name=item["day_name"],
        week_start_iso=item["week_start_iso"],
        summary=item["summary"],
        diff_hash=item["diff_hash"],
        diff_excerpt=item.get("diff_excerpt", ""),
    )


def load_config() -> AppConfig:
    if not STATE_PATH.exists():
        return AppConfig()

    data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    theme_mode = str(data.get("theme_mode", "system")).lower()
    if theme_mode not in {"system", "light", "dark"}:
        theme_mode = "system"
    raw_entries = [_decode_entry(item) for item in data.get("entries", [])]
    entries = dedupe_entries(raw_entries)
    if len(entries) != len(raw_entries):
        data["entries"] = [asdict(entry) for entry in entries]
        STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

    return AppConfig(
        week_end_day=data.get("week_end_day", "Thursday"),
        theme_mode=theme_mode,
        high_contrast=bool(data.get("high_contrast", False)),
        ui_font_size=max(10, min(20, int(data.get("ui_font_size", 11)))),
        auto_export_time=str(data.get("auto_export_time", "")),
        last_auto_export_date=str(data.get("last_auto_export_date", "")),
        repos=[_decode_repo(item) for item in data.get("repos", [])],
        entries=entries,
    )


def save_config(config: AppConfig) -> None:
    config.entries = dedupe_entries(config.entries)
    payload = {
        "week_end_day": config.week_end_day,
        "theme_mode": config.theme_mode,
        "high_contrast": config.high_contrast,
        "ui_font_size": config.ui_font_size,
        "auto_export_time": config.auto_export_time,
        "last_auto_export_date": config.last_auto_export_date,
        "repos": [asdict(repo) for repo in config.repos],
        "entries": [asdict(entry) for entry in config.entries],
    }
    STATE_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")