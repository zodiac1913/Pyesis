from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import TypedDict


BUFFER_DIR = Path("diff_buffers")


class DiffLedgerItem(TypedDict):
    datetime: str
    repo: str
    gitDiffText: str
    gitDiffDescription: str
    shown: bool
    diffHash: str
    repoPath: str


def _today_key() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _buffer_path(day_key: str) -> Path:
    return BUFFER_DIR / f"{day_key}.json"


def purge_old_daily_buffers(days_to_keep: int = 7, day_key: str | None = None) -> None:
    keep_from = datetime.fromisoformat(day_key or _today_key()) - timedelta(days=max(0, days_to_keep - 1))
    if not BUFFER_DIR.exists():
        return
    for path in BUFFER_DIR.glob("*.json"):
        stem = path.stem
        try:
            file_day = datetime.fromisoformat(stem)
        except ValueError:
            path.unlink(missing_ok=True)
            continue
        if file_day < keep_from:
            path.unlink(missing_ok=True)


def clear_buffers_for_day(day_key: str | None = None) -> None:
    target_day = day_key or _today_key()
    _buffer_path(target_day).unlink(missing_ok=True)


def _read_items(path: Path) -> list[DiffLedgerItem]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []

    items: list[DiffLedgerItem] = []
    for raw in data:
        if not isinstance(raw, dict):
            continue
        repo = str(raw.get("repo", "")).strip()
        git_diff_text = str(raw.get("gitDiffText", ""))
        git_diff_description = str(raw.get("gitDiffDescription", ""))
        item_datetime = str(raw.get("datetime", ""))
        shown = bool(raw.get("shown", False))
        diff_hash = str(raw.get("diffHash", ""))
        repo_path = str(raw.get("repoPath", ""))
        if not repo or not git_diff_text:
            continue
        items.append(
            {
                "datetime": item_datetime,
                "repo": repo,
                "gitDiffText": git_diff_text,
                "gitDiffDescription": git_diff_description,
                "shown": shown,
                "diffHash": diff_hash or hashlib.sha256(git_diff_text.encode("utf-8")).hexdigest(),
                "repoPath": repo_path,
            }
        )
    return items


def _write_items(path: Path, items: list[DiffLedgerItem]) -> None:
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
        }
        for item in items
    ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def find_item(repo_label: str, diff_text: str, day_key: str | None = None) -> DiffLedgerItem | None:
    active_day = day_key or _today_key()
    path = _buffer_path(active_day)
    diff_hash = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()

    for item in _read_items(path):
        if item["repo"] != repo_label:
            continue
        if item["diffHash"] == diff_hash or item["gitDiffText"] == diff_text:
            return item
    return None


def remember_diff(
    repo_label: str,
    repo_path: str,
    diff_text: str,
    description: str,
    day_key: str | None = None,
) -> DiffLedgerItem:
    active_day = day_key or _today_key()
    path = _buffer_path(active_day)
    diff_hash = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    created_at = datetime.now().isoformat(timespec="seconds")

    items = _read_items(path)
    for item in items:
        if item["repo"] != repo_label:
            continue
        if item["diffHash"] == diff_hash or item["gitDiffText"] == diff_text:
            if description.strip():
                item["gitDiffDescription"] = description
            item["datetime"] = created_at
            item["repoPath"] = repo_path
            _write_items(path, items)
            return item

    new_item: DiffLedgerItem = {
        "datetime": created_at,
        "repo": repo_label,
        "gitDiffText": diff_text,
        "gitDiffDescription": description,
        "shown": False,
        "diffHash": diff_hash,
        "repoPath": repo_path,
    }
    items.append(new_item)
    _write_items(path, items)
    return new_item


def mark_as_shown(repo_label: str, diff_text: str, day_key: str | None = None) -> bool:
    active_day = day_key or _today_key()
    path = _buffer_path(active_day)
    items = _read_items(path)
    diff_hash = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    changed = False
    for item in items:
        if item["repo"] != repo_label:
            continue
        if item["diffHash"] == diff_hash or item["gitDiffText"] == diff_text:
            if not item["shown"]:
                item["shown"] = True
                changed = True
            break
    if changed:
        _write_items(path, items)
    return changed
