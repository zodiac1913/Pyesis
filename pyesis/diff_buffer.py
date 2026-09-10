from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
from pathlib import Path
from typing import TypedDict

from pyesis.config import BUFFER_DIR, STATE_PATH
from pyesis.git_monitor import is_noise_work_text
from pyesis import storage


class DiffLedgerItem(TypedDict):
    datetime: str
    repo: str
    gitDiffText: str
    gitDiffDescription: str
    shown: bool
    diffHash: str
    repoPath: str
    author: str
    summarySource: str
    rewrittenBy: str
    rewrittenAt: str
    requestedSummarySource: str
    summaryWarning: str
    fallbackSummarySource: str
    summaryTimingMs: int
    summaryProviderDetails: str
    lastAiAttemptAt: str


def _today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _buffer_path(day_key: str, buffer_dir: Path | None = None) -> Path:
    return (buffer_dir or BUFFER_DIR) / f"{day_key}.json"


def _uses_files(buffer_dir: Path | None) -> bool:
    return buffer_dir is not None


def purge_old_daily_buffers(days_to_keep: int = 7, day_key: str | None = None, buffer_dir: Path | None = None) -> None:
    keep_from = datetime.fromisoformat(day_key or _today_key()) - timedelta(days=max(0, days_to_keep - 1))
    keep_from_key = keep_from.strftime("%Y-%m-%d")
    if _uses_files(buffer_dir):
        directory = buffer_dir or BUFFER_DIR
        if not directory.exists():
            return
        for path in directory.glob("*.json"):
            try:
                file_day = datetime.fromisoformat(path.stem)
            except ValueError:
                path.unlink(missing_ok=True)
                continue
            if file_day < keep_from:
                path.unlink(missing_ok=True)
        return
    storage.delete_buffer_days_before(STATE_PATH, keep_from_key)
    if BUFFER_DIR.exists():
        for path in BUFFER_DIR.glob("*.json"):
            try:
                file_day = datetime.fromisoformat(path.stem)
            except ValueError:
                path.unlink(missing_ok=True)
                continue
            if file_day < keep_from:
                path.unlink(missing_ok=True)


def clear_buffers_for_day(day_key: str | None = None, buffer_dir: Path | None = None) -> None:
    target_day = day_key or _today_key()
    if _uses_files(buffer_dir):
        _buffer_path(target_day, buffer_dir).unlink(missing_ok=True)
        return
    storage.delete_buffer_day(STATE_PATH, target_day)


def list_buffer_day_keys(buffer_dir: Path | None = None) -> list[str]:
    if _uses_files(buffer_dir):
        directory = buffer_dir or BUFFER_DIR
        if not directory.exists():
            return []
        day_keys: list[str] = []
        for path in sorted(directory.glob("*.json")):
            try:
                datetime.fromisoformat(path.stem)
            except ValueError:
                continue
            day_keys.append(path.stem)
        return day_keys
    return storage.list_buffer_days(STATE_PATH)


def load_buffer_items(day_key: str | None = None, buffer_dir: Path | None = None) -> list[DiffLedgerItem]:
    active_day = day_key or _today_key()
    if _uses_files(buffer_dir):
        return _normalize_items(_read_raw_items(_buffer_path(active_day, buffer_dir)))
    return _normalize_items(storage.load_buffer_day(STATE_PATH, active_day))


def replace_buffer_items(day_key: str, items: list[DiffLedgerItem], buffer_dir: Path | None = None) -> None:
    if _uses_files(buffer_dir):
        _write_items(_buffer_path(day_key, buffer_dir), items)
        return
    storage.replace_buffer_day(STATE_PATH, day_key, list(items))


def _buffer_item_is_noise(item: DiffLedgerItem) -> bool:
    return is_noise_work_text(item.get("gitDiffText", "")) or is_noise_work_text(item.get("gitDiffDescription", ""))


def purge_noise_buffer_items(buffer_dir: Path | None = None) -> int:
    if _uses_files(buffer_dir):
        directory = buffer_dir or BUFFER_DIR
        if not directory.exists():
            return 0
        removed = 0
        for path in directory.glob("*.json"):
            items = _normalize_items(_read_raw_items(path))
            kept = [item for item in items if not _buffer_item_is_noise(item)]
            dropped = len(items) - len(kept)
            if dropped:
                _write_items(path, kept)
                removed += dropped
        return removed
    return storage.purge_noise_buffers(STATE_PATH)


def _read_raw_items(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _normalize_items(raw_items: list[dict]) -> list[DiffLedgerItem]:
    items: list[DiffLedgerItem] = []
    for raw in raw_items:
        repo = str(raw.get("repo", "")).strip()
        git_diff_text = str(raw.get("gitDiffText", ""))
        git_diff_description = str(raw.get("gitDiffDescription", ""))
        if not repo or not git_diff_text:
            continue
        items.append(
            {
                "datetime": str(raw.get("datetime", "")),
                "repo": repo,
                "gitDiffText": git_diff_text,
                "gitDiffDescription": git_diff_description,
                "shown": bool(raw.get("shown", False)),
                "diffHash": str(raw.get("diffHash", "")) or hashlib.sha256(git_diff_text.encode("utf-8")).hexdigest(),
                "repoPath": str(raw.get("repoPath", "")),
                "author": str(raw.get("author", "Backup")),
                "summarySource": str(raw.get("summarySource", "")).strip().lower(),
                "rewrittenBy": str(raw.get("rewrittenBy", "")).strip(),
                "rewrittenAt": str(raw.get("rewrittenAt", "")).strip(),
                "requestedSummarySource": str(raw.get("requestedSummarySource", "")).strip().lower(),
                "summaryWarning": str(raw.get("summaryWarning", "")).strip(),
                "fallbackSummarySource": str(raw.get("fallbackSummarySource", "")).strip().lower(),
                "summaryTimingMs": max(0, int(raw.get("summaryTimingMs", 0) or 0)),
                "summaryProviderDetails": str(raw.get("summaryProviderDetails", "")).strip(),
                "lastAiAttemptAt": str(raw.get("lastAiAttemptAt", "")).strip(),
            }
        )
    return items


def _write_items(path: Path, items: list[DiffLedgerItem]) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [
        {
            "datetime": item["datetime"],
            "repo": item["repo"],
            "gitDiffText": item["gitDiffText"],
            "gitDiffDescription": item["gitDiffDescription"],
            "shown": item["shown"],
            "diffHash": item["diffHash"],
            "repoPath": item["repoPath"],
            "author": item["author"],
            "summarySource": item["summarySource"],
            "rewrittenBy": item["rewrittenBy"],
            "rewrittenAt": item["rewrittenAt"],
            "requestedSummarySource": item["requestedSummarySource"],
            "summaryWarning": item["summaryWarning"],
            "fallbackSummarySource": item["fallbackSummarySource"],
            "summaryTimingMs": item["summaryTimingMs"],
            "summaryProviderDetails": item["summaryProviderDetails"],
            "lastAiAttemptAt": item["lastAiAttemptAt"],
        }
        for item in items
        if not _buffer_item_is_noise(item)
    ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def find_item(repo_label: str, diff_text: str, day_key: str | None = None, buffer_dir: Path | None = None) -> DiffLedgerItem | None:
    active_day = day_key or _today_key()
    diff_hash = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    for item in load_buffer_items(active_day, buffer_dir=buffer_dir):
        if item["repo"] != repo_label:
            continue
        if item["diffHash"] == diff_hash or item["gitDiffText"] == diff_text:
            return item
    return None


def _matches_diff_item(item: DiffLedgerItem, repo_label: str, diff_hash: str, diff_text: str) -> bool:
    if item["repo"] != repo_label:
        return False
    return item["diffHash"] == diff_hash or item["gitDiffText"] == diff_text


def _update_existing_item(
    item: DiffLedgerItem,
    *,
    description: str,
    author: str,
    summary_source: str,
    requested_summary_source: str,
    summary_warning: str,
    fallback_summary_source: str,
    summary_timing_ms: int,
    summary_provider_details: str,
    last_ai_attempt_at: str,
    created_at: str,
    repo_path: str,
) -> None:
    if description.strip():
        item["gitDiffDescription"] = description
    if author:
        item["author"] = author
    if summary_source:
        item["summarySource"] = summary_source
    item["requestedSummarySource"] = requested_summary_source
    item["summaryWarning"] = summary_warning
    item["fallbackSummarySource"] = fallback_summary_source
    item["summaryTimingMs"] = max(0, int(summary_timing_ms))
    item["summaryProviderDetails"] = summary_provider_details
    item["lastAiAttemptAt"] = last_ai_attempt_at
    item["datetime"] = created_at
    item["repoPath"] = repo_path


def remember_diff(
    repo_label: str,
    repo_path: str,
    diff_text: str,
    description: str,
    author: str = "Backup",
    summary_source: str = "",
    requested_summary_source: str = "",
    summary_warning: str = "",
    fallback_summary_source: str = "",
    summary_timing_ms: int = 0,
    summary_provider_details: str = "",
    last_ai_attempt_at: str = "",
    day_key: str | None = None,
    buffer_dir: Path | None = None,
) -> DiffLedgerItem:
    active_day = day_key or _today_key()
    diff_hash = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    created_at = datetime.now().isoformat(timespec="seconds")
    items = load_buffer_items(active_day, buffer_dir=buffer_dir)
    for item in items:
        if not _matches_diff_item(item, repo_label, diff_hash, diff_text):
            continue
        _update_existing_item(
            item,
            description=description,
            author=author,
            summary_source=summary_source,
            requested_summary_source=requested_summary_source,
            summary_warning=summary_warning,
            fallback_summary_source=fallback_summary_source,
            summary_timing_ms=summary_timing_ms,
            summary_provider_details=summary_provider_details,
            last_ai_attempt_at=last_ai_attempt_at,
            created_at=created_at,
            repo_path=repo_path,
        )
        if _uses_files(buffer_dir):
            replace_buffer_items(active_day, items, buffer_dir=buffer_dir)
        else:
            storage.upsert_buffer_item(STATE_PATH, active_day, item)
        return item

    new_item: DiffLedgerItem = {
        "datetime": created_at,
        "repo": repo_label,
        "gitDiffText": diff_text,
        "gitDiffDescription": description,
        "shown": False,
        "diffHash": diff_hash,
        "repoPath": repo_path,
        "author": author,
        "summarySource": summary_source,
        "rewrittenBy": "",
        "rewrittenAt": "",
        "requestedSummarySource": requested_summary_source,
        "summaryWarning": summary_warning,
        "fallbackSummarySource": fallback_summary_source,
        "summaryTimingMs": max(0, int(summary_timing_ms)),
        "summaryProviderDetails": summary_provider_details,
        "lastAiAttemptAt": last_ai_attempt_at,
    }
    if _uses_files(buffer_dir):
        items.append(new_item)
        replace_buffer_items(active_day, items, buffer_dir=buffer_dir)
    else:
        storage.upsert_buffer_item(STATE_PATH, active_day, new_item)
    return new_item


def mark_as_shown(repo_label: str, diff_text: str, day_key: str | None = None, buffer_dir: Path | None = None) -> bool:
    active_day = day_key or _today_key()
    diff_hash = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    items = load_buffer_items(active_day, buffer_dir=buffer_dir)
    changed = False
    for item in items:
        if item["repo"] != repo_label:
            continue
        if item["diffHash"] == diff_hash or item["gitDiffText"] == diff_text:
            if not item["shown"]:
                item["shown"] = True
                changed = True
            if changed:
                if _uses_files(buffer_dir):
                    replace_buffer_items(active_day, items, buffer_dir=buffer_dir)
                else:
                    storage.upsert_buffer_item(STATE_PATH, active_day, item)
            break
    return changed
