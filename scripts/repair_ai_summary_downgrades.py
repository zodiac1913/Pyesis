from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pyesis.ai_summary import HEURISTIC_MODE
from pyesis.config import load_config, save_config
from pyesis.diff_buffer import BUFFER_DIR


AI_LOG_PATH = Path("logs") / "ai_attempts.jsonl"


def _normalize_source(value: Any) -> str:
    return str(value or "").strip().lower()


def _successful_ai_attempts() -> dict[tuple[str, str], dict[str, Any]]:
    if not AI_LOG_PATH.exists():
        return {}

    successes: dict[tuple[str, str], dict[str, Any]] = {}
    for line in AI_LOG_PATH.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue

        repo_path = str(payload.get("repoPath", "")).strip()
        diff_hash = str(payload.get("diffHash", "")).strip()
        timestamp = str(payload.get("timestamp", "")).strip()
        if not repo_path or not diff_hash:
            continue

        attempts = payload.get("attempts")
        if not isinstance(attempts, list):
            attempts = [payload]

        for attempt in attempts:
            if not isinstance(attempt, dict):
                continue
            actual_source = _normalize_source(attempt.get("actualSource"))
            accepted = bool(attempt.get("accepted", False))
            if not accepted or not actual_source or actual_source == HEURISTIC_MODE:
                continue
            key = (repo_path, diff_hash)
            previous = successes.get(key)
            if previous is not None and str(previous.get("timestamp", "")) >= timestamp:
                continue
            successes[key] = {
                "timestamp": timestamp,
                "actual_source": actual_source,
                "requested_source": _normalize_source(attempt.get("requestedSource")) or actual_source,
                "timing_ms": max(0, int(attempt.get("timingMs", 0) or 0)),
                "provider_details": str(attempt.get("providerDetails", "")).strip(),
            }
    return successes


def _repair_state_entries(successes: dict[tuple[str, str], dict[str, Any]]) -> int:
    config = load_config()
    repaired = 0
    for entry in config.entries:
        if _normalize_source(entry.summary_source) != HEURISTIC_MODE:
            continue
        success = successes.get((entry.repo_path, entry.diff_hash.strip()))
        if success is None:
            continue
        entry.summary_source = success["actual_source"]
        entry.author = "AI"
        entry.requested_summary_source = success["requested_source"]
        entry.summary_warning = ""
        entry.fallback_summary_source = ""
        entry.summary_timing_ms = success["timing_ms"]
        entry.summary_provider_details = success["provider_details"]
        repaired += 1
    if repaired:
        save_config(config)
    return repaired


def _repair_buffer_file(path: Path, successes: dict[tuple[str, str], dict[str, Any]]) -> int:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0
    if not isinstance(payload, list):
        return 0

    repaired = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        if _normalize_source(item.get("summarySource")) != HEURISTIC_MODE:
            continue
        repo_path = str(item.get("repoPath", "")).strip()
        diff_hash = str(item.get("diffHash", "")).strip()
        success = successes.get((repo_path, diff_hash))
        if success is None:
            continue
        item["summarySource"] = success["actual_source"]
        item["author"] = "AI"
        item["requestedSummarySource"] = success["requested_source"]
        item["summaryWarning"] = ""
        item["fallbackSummarySource"] = ""
        item["summaryTimingMs"] = success["timing_ms"]
        item["summaryProviderDetails"] = success["provider_details"]
        repaired += 1

    if repaired:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return repaired


def repair_ai_summary_downgrades() -> tuple[int, int]:
    successes = _successful_ai_attempts()
    repaired_entries = _repair_state_entries(successes)
    repaired_buffers = 0
    if BUFFER_DIR.exists():
        for path in sorted(BUFFER_DIR.glob("*.json")):
            repaired_buffers += _repair_buffer_file(path, successes)
    return repaired_entries, repaired_buffers


if __name__ == "__main__":
    repaired_entries, repaired_buffers = repair_ai_summary_downgrades()
    print(
        json.dumps(
            {
                "repairedEntries": repaired_entries,
                "repairedBufferItems": repaired_buffers,
            }
        )
    )