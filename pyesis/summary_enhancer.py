from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from time import perf_counter
import re
from typing import Callable

from pyesis.ai_summary import (
    AISummaryResult,
    GITHUB_GPT_MODE,
    HEURISTIC_MODE,
    OLLAMA_MODE,
    OPENAI_COMPATIBLE_MODE,
    build_summary,
)
from pyesis.config import AppConfig, EntryRecord, STATE_PATH, save_config
from pyesis.diff_buffer import BUFFER_DIR
from pyesis.github_auth import load_github_auth_token, normalize_github_auth_endpoint, normalize_github_auth_mode


DEFAULT_REWRITER_ID = "PyesisSummaryEnhancer"
DAY_ORDER = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]
WEAK_PHRASES = (
    "made updates",
    "refined logic",
    "clarify behavior",
    "updated documentation text",
    "updated workflow automation",
    "updated package metadata",
)
WEAK_TEMPLATE_PATTERNS = (
    re.compile(r"\bnot available from diff\b", re.IGNORECASE),
    re.compile(r"\bby i\b", re.IGNORECASE),
    re.compile(r"\bchanging code around\b", re.IGNORECASE),
    re.compile(r"\bimprove the user-facing app flow\b", re.IGNORECASE),
    re.compile(r"\brefined application flow\b", re.IGNORECASE),
)
HUMAN_AUTHORS = {"human", "user", "manual"}
HUMAN_SOURCES = {"human", "manual"}


@dataclass
class EnhanceReport:
    ran: bool
    dry_run: bool
    scanned_state: int = 0
    scanned_buffer: int = 0
    rewritten_state: int = 0
    rewritten_buffer: int = 0
    skipped_human: int = 0
    skipped_strong: int = 0
    skipped_weak: int = 0
    skipped_ai_unavailable: int = 0
    skipped_gated: int = 0
    timed_attempts: int = 0
    total_attempt_seconds: float = 0.0
    provider_timed_attempts: int = 0
    total_provider_ms: int = 0
    logs: list[str] | None = None

    @property
    def total_rewritten(self) -> int:
        return self.rewritten_state + self.rewritten_buffer

    @property
    def average_attempt_ms(self) -> int:
        if not self.timed_attempts:
            return 0
        return int(round((self.total_attempt_seconds / self.timed_attempts) * 1000))

    @property
    def average_provider_ms(self) -> int:
        if not self.provider_timed_attempts:
            return 0
        return int(round(self.total_provider_ms / self.provider_timed_attempts))


SummaryBuilder = Callable[[str, str, str | None], AISummaryResult | str]
RewriteGate = Callable[[str, str | None], bool]
RewriteActivityCallback = Callable[[str, str | None, bool], None]


@dataclass(frozen=True)
class SummaryRewrite:
    text: str
    source: str
    author: str
    timing_ms: int = 0
    provider_details: str = ""


@dataclass(frozen=True)
class BuilderCallResult:
    result: AISummaryResult | str
    gate_blocked: bool
    duration_seconds: float


def _now_iso(now: datetime | None = None) -> str:
    return (now or datetime.now()).isoformat(timespec="seconds")


def _parse_iso(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _is_human_authored(author: str, source: str) -> bool:
    author_l = (author or "").strip().lower()
    source_l = (source or "").strip().lower()
    return author_l in HUMAN_AUTHORS or source_l in HUMAN_SOURCES


def _is_protected_ai_source(source: str) -> bool:
    normalized = (source or "").strip().lower()
    return bool(normalized) and normalized != HEURISTIC_MODE and normalized not in HUMAN_SOURCES


def _looks_weak(text: str) -> bool:
    cleaned = (text or "").strip()
    if len(cleaned) < 32:
        return True
    normalized = cleaned.lower()
    if any(phrase in normalized for phrase in WEAK_PHRASES):
        return True
    return any(pattern.search(cleaned) for pattern in WEAK_TEMPLATE_PATTERNS)


def _safe_item_id(repo: str, diff_hash: str) -> str:
    digest = (diff_hash or "")[:10]
    return f"{repo}:{digest}" if digest else repo


def _call_builder(
    builder: SummaryBuilder,
    repo_label: str,
    diff_text: str,
    repo_path: str,
    rewrite_gate: RewriteGate | None = None,
    activity_callback: RewriteActivityCallback | None = None,
) -> BuilderCallResult:
    started_at = perf_counter()
    if rewrite_gate is not None and not rewrite_gate(repo_label, repo_path):
        return BuilderCallResult(
            result=AISummaryResult(text="", source=HEURISTIC_MODE),
            gate_blocked=True,
            duration_seconds=perf_counter() - started_at,
        )

    if activity_callback is not None:
        activity_callback(repo_label, repo_path, True)

    try:
        return BuilderCallResult(
            result=builder(repo_label, diff_text, repo_path),
            gate_blocked=False,
            duration_seconds=perf_counter() - started_at,
        )
    finally:
        if activity_callback is not None:
            activity_callback(repo_label, repo_path, False)


def _rewrite_from_builder_result(result: AISummaryResult | str, current_source: str, current_author: str) -> SummaryRewrite | None:
    if isinstance(result, AISummaryResult):
        text = (result.text or "").strip()
        if not text:
            return None
        source = (result.source or current_source or HEURISTIC_MODE).strip().lower() or HEURISTIC_MODE
        author = "AI" if source != HEURISTIC_MODE else current_author
        return SummaryRewrite(
            text=text,
            source=source,
            author=author,
            timing_ms=result.timing_ms,
            provider_details=result.provider_details,
        )

    text = str(result or "").strip()
    if not text:
        return None
    source = (current_source or HEURISTIC_MODE).strip().lower() or HEURISTIC_MODE
    author = "AI" if source != HEURISTIC_MODE else current_author
    return SummaryRewrite(text=text, source=source, author=author)


def _read_buffer_items(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _write_buffer_items(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, indent=2), encoding="utf-8")


def _is_new_since(last_run: datetime | None, timestamp: str) -> bool:
    if last_run is None:
        return True
    candidate = _parse_iso(timestamp)
    if candidate is None:
        return True
    return candidate >= last_run


def _active_week_start(now: datetime) -> datetime:
    return (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_hhmm(value: str) -> tuple[int, int] | None:
    text = (value or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        return None
    return hour, minute


def _weekly_enhancer_cutoff(config: AppConfig, active_week_start: datetime) -> datetime | None:
    scheduled_time = _parse_hhmm(config.auto_export_time)
    if scheduled_time is None:
        return None
    if config.week_end_day not in DAY_ORDER:
        return None
    day_offset = DAY_ORDER.index(config.week_end_day)
    cutoff_day = active_week_start + timedelta(days=day_offset)
    return cutoff_day.replace(hour=scheduled_time[0], minute=scheduled_time[1])


def _is_week_frozen(config: AppConfig, current_time: datetime, active_week_start: datetime) -> bool:
    cutoff = _weekly_enhancer_cutoff(config, active_week_start)
    if cutoff is None:
        return False
    return current_time >= cutoff


def _is_current_week_timestamp(timestamp: str, active_week_start: datetime) -> bool:
    candidate = _parse_iso(timestamp)
    if candidate is None:
        return False
    week_end = active_week_start + timedelta(days=7)
    return active_week_start <= candidate < week_end


def _is_current_week_entry(entry: EntryRecord, active_week_start: datetime) -> bool:
    return entry.week_start_iso.strip() == active_week_start.isoformat()


def _eligible_state_entry(
    entry: EntryRecord,
    active_week_start: datetime,
    aggressive_prodding: bool,
    force_ai_upgrade: bool,
) -> bool:
    if not _is_current_week_entry(entry, active_week_start):
        return False
    if _is_protected_ai_source(entry.summary_source):
        return False
    if not force_ai_upgrade and not aggressive_prodding and not _looks_weak(entry.summary):
        return False
    if _is_human_authored(entry.author, entry.summary_source):
        return False
    if (entry.rewritten_at or "").strip() and not force_ai_upgrade:
        return False
    return bool(entry.diff_excerpt.strip())


def _eligible_buffer_item(
    item: dict,
    active_week_start: datetime,
    aggressive_prodding: bool,
    force_ai_upgrade: bool,
) -> bool:
    if not _is_current_week_timestamp(str(item.get("datetime", "")), active_week_start):
        return False
    if _is_protected_ai_source(str(item.get("summarySource", ""))):
        return False
    if _is_human_authored(str(item.get("author", "")), str(item.get("summarySource", ""))):
        return False
    if str(item.get("rewrittenAt", "")).strip() and not force_ai_upgrade:
        return False
    description = str(item.get("gitDiffDescription", ""))
    if not force_ai_upgrade and not aggressive_prodding and not _looks_weak(description):
        return False
    diff_text = str(item.get("gitDiffText", ""))
    return bool(diff_text.strip())


def _should_run_now(config: AppConfig, current_time: datetime) -> tuple[bool, datetime | None, str]:
    if not config.summary_enhancer_enabled:
        return False, None, "Enhancer skipped: disabled"

    active_week_start = _active_week_start(current_time)
    if _is_week_frozen(config, current_time, active_week_start):
        return False, None, "Enhancer skipped: active week frozen after export cutoff"

    interval_minutes = max(1, int(config.summary_enhancer_interval_minutes))
    last_run = _parse_iso(config.summary_enhancer_last_run_at)
    if last_run is not None and current_time < (last_run + timedelta(minutes=interval_minutes)):
        return False, last_run, "Enhancer skipped: interval not reached"
    return True, last_run, ""


def _default_builder(config: AppConfig) -> SummaryBuilder:
    def builder(repo_label: str, diff_text: str, repo_path: str | None) -> AISummaryResult:
        return _build_summary_with_priority_modes(config, repo_label, diff_text, repo_path)

    return builder


def _build_summary_for_mode(config: AppConfig, repo_label: str, diff_text: str, repo_path: str | None, mode: str) -> AISummaryResult:
    allow_fallback = mode != HEURISTIC_MODE and config.ai_fallback_enabled
    return build_summary(
        repo_label,
        diff_text,
        repo_path,
        mode=mode,
        allow_fallback=allow_fallback,
    )


def _accept_summary_result(result: AISummaryResult, requested_mode: str) -> bool:
    return bool(result.text.strip()) and (result.source != HEURISTIC_MODE or requested_mode == HEURISTIC_MODE)


def _attach_summary_warnings(result: AISummaryResult, warnings: list[str]) -> AISummaryResult:
    if warnings and not result.warning.strip():
        result.warning = "; ".join(warnings)
    return result


def _build_summary_with_priority_modes(
    config: AppConfig,
    repo_label: str,
    diff_text: str,
    repo_path: str | None,
) -> AISummaryResult:
    last_result: AISummaryResult | None = None
    warnings: list[str] = []

    for mode in _preferred_summary_modes(config):
        result = _build_summary_for_mode(config, repo_label, diff_text, repo_path, mode)
        if result.warning.strip():
            warnings.append(result.warning.strip())
        if _accept_summary_result(result, mode):
            return _attach_summary_warnings(result, warnings)
        last_result = result

    if last_result is not None:
        return _attach_summary_warnings(last_result, warnings)

    fallback_result = _build_summary_for_mode(config, repo_label, diff_text, repo_path, HEURISTIC_MODE)
    return _attach_summary_warnings(fallback_result, warnings)


def _preferred_summary_modes(config: AppConfig) -> list[str]:
    modes: list[str] = []

    current_mode = (config.ai_mode or "").strip().lower()
    if current_mode and current_mode != HEURISTIC_MODE and current_mode not in modes:
        modes.append(current_mode)

    github_token_env = os.getenv("PYESIS_GITHUB_GPT_API_KEY", "").strip()
    github_auth_mode = normalize_github_auth_mode(config.github_auth_mode)
    github_auth_endpoint = normalize_github_auth_endpoint(github_auth_mode, config.github_auth_endpoint)
    github_token_stored, _ = load_github_auth_token(github_auth_mode, github_auth_endpoint)
    if (github_token_env or github_token_stored.strip()) and GITHUB_GPT_MODE not in modes:
        modes.append(GITHUB_GPT_MODE)

    if config.ai_ollama_url.strip() and config.ai_ollama_model.strip() and OLLAMA_MODE not in modes:
        modes.append(OLLAMA_MODE)

    if config.ai_openai_url.strip() and os.getenv("PYESIS_AI_API_KEY", "").strip() and OPENAI_COMPATIBLE_MODE not in modes:
        modes.append(OPENAI_COMPATIBLE_MODE)

    modes.append(HEURISTIC_MODE)
    return modes


def _has_non_heuristic_summary_mode(config: AppConfig) -> bool:
    return any(mode != HEURISTIC_MODE for mode in _preferred_summary_modes(config))


def _should_force_ai_upgrade(summary_source: str, config: AppConfig) -> bool:
    return (summary_source or "").strip().lower() == HEURISTIC_MODE and _has_non_heuristic_summary_mode(config)


def _accept_rewrite(rewrite: SummaryRewrite | None, force_ai_upgrade: bool) -> bool:
    if rewrite is None:
        return False
    if force_ai_upgrade and rewrite.source == HEURISTIC_MODE:
        return False
    return not _looks_weak(rewrite.text)


def _rewrite_skip_reason(rewrite: SummaryRewrite | None, force_ai_upgrade: bool) -> str:
    if force_ai_upgrade and rewrite is not None and rewrite.source == HEURISTIC_MODE:
        return "ai upgrade unavailable"
    return "weak rewrite"


def _record_attempt_timing(report: EnhanceReport, duration_seconds: float) -> int:
    report.timed_attempts += 1
    report.total_attempt_seconds += duration_seconds
    return int(round(duration_seconds * 1000))


def _record_provider_timing(report: EnhanceReport, timing_ms: int) -> None:
    if timing_ms <= 0:
        return
    report.provider_timed_attempts += 1
    report.total_provider_ms += timing_ms


def _count_skip_reason(report: EnhanceReport, reason: str) -> None:
    if reason == "ai upgrade unavailable":
        report.skipped_ai_unavailable += 1
        return
    if reason == "weak rewrite":
        report.skipped_weak += 1


def _log_rewrite_skip(
    report: EnhanceReport,
    scope: str,
    item_id: str,
    rewrite: SummaryRewrite | None,
    force_ai_upgrade: bool,
    duration_seconds: float,
) -> None:
    reason = _rewrite_skip_reason(rewrite, force_ai_upgrade)
    _count_skip_reason(report, reason)
    elapsed_ms = rewrite.timing_ms if rewrite is not None and rewrite.timing_ms > 0 else _record_attempt_timing(report, duration_seconds)
    _record_provider_timing(report, rewrite.timing_ms if rewrite is not None else 0)
    assert report.logs is not None
    detail = f" via {rewrite.provider_details}" if rewrite is not None and rewrite.provider_details else ""
    report.logs.append(f"{scope} skipped ({reason}, {elapsed_ms} ms{detail}): {item_id}")


def _mark_rewrite_success(report: EnhanceReport, scope: str, item_id: str, rewrite: SummaryRewrite, duration_seconds: float) -> None:
    elapsed_ms = rewrite.timing_ms if rewrite.timing_ms > 0 else _record_attempt_timing(report, duration_seconds)
    _record_provider_timing(report, rewrite.timing_ms)
    assert report.logs is not None
    detail = f" via {rewrite.provider_details}" if rewrite.provider_details else ""
    report.logs.append(f"{scope} rewritten ({rewrite.source}, {elapsed_ms} ms{detail}): {item_id}")


def _mark_rewrite_deferred(report: EnhanceReport, scope: str, item_id: str) -> None:
    report.skipped_gated += 1
    assert report.logs is not None
    report.logs.append(f"{scope} deferred by rewrite gate: {item_id}")


def _count_strong_skip(report: EnhanceReport, force_ai_upgrade: bool, aggressive_prodding: bool, summary_text: str) -> None:
    if not force_ai_upgrade and not aggressive_prodding and not _looks_weak(summary_text):
        report.skipped_strong += 1


def _state_rewrite_order(entry: EntryRecord) -> tuple[int, str, str, str]:
    retried_rank = 0 if (entry.rewritten_at or "").strip() else 1
    return (retried_rank, entry.created_at, entry.repo_label.lower(), entry.diff_hash)


def _buffer_rewrite_order(item: dict) -> tuple[int, str, str, str]:
    retried_rank = 0 if str(item.get("rewrittenAt", "")).strip() else 1
    timestamp = str(item.get("datetime", ""))
    repo_label = str(item.get("repo", "")).lower()
    diff_hash = str(item.get("diffHash", ""))
    return (retried_rank, timestamp, repo_label, diff_hash)


def _rewrite_state_entries(
    config: AppConfig,
    *,
    builder: SummaryBuilder,
    rewrite_gate: RewriteGate | None,
    activity_callback: RewriteActivityCallback | None,
    active_week_start: datetime,
    rewritten_by: str,
    rewritten_at: str,
    dry_run: bool,
    aggressive_prodding: bool,
    report: EnhanceReport,
) -> None:
    ordered_entries = sorted(enumerate(config.entries), key=lambda item: _state_rewrite_order(item[1]))
    for index, entry in ordered_entries:
        report.scanned_state += 1
        if _is_human_authored(entry.author, entry.summary_source):
            report.skipped_human += 1
            continue
        force_ai_upgrade = _should_force_ai_upgrade(entry.summary_source, config)
        if not _eligible_state_entry(entry, active_week_start, aggressive_prodding, force_ai_upgrade):
            _count_strong_skip(report, force_ai_upgrade, aggressive_prodding, entry.summary)
            continue

        builder_call = _call_builder(
            builder,
            entry.repo_label,
            entry.diff_excerpt,
            entry.repo_path,
            rewrite_gate,
            activity_callback,
        )
        if builder_call.gate_blocked:
            _mark_rewrite_deferred(report, "State", entry.repo_label)
            continue

        rewrite = _rewrite_from_builder_result(
            builder_call.result,
            entry.summary_source,
            entry.author,
        )
        if not _accept_rewrite(rewrite, force_ai_upgrade):
            _log_rewrite_skip(report, "State", entry.repo_label, rewrite, force_ai_upgrade, builder_call.duration_seconds)
            continue

        report.rewritten_state += 1
        assert rewrite is not None
        _mark_rewrite_success(report, "State", entry.repo_label, rewrite, builder_call.duration_seconds)
        if dry_run:
            continue

        config.entries[index] = EntryRecord(
            repo_label=entry.repo_label,
            repo_path=entry.repo_path,
            created_at=entry.created_at,
            day_name=entry.day_name,
            week_start_iso=entry.week_start_iso,
            summary=rewrite.text,
            diff_hash=entry.diff_hash,
            diff_excerpt=entry.diff_excerpt,
            summary_source=rewrite.source,
            author=rewrite.author,
            rewritten_by=rewritten_by,
            rewritten_at=rewritten_at,
        )


def _rewrite_buffer_items(
    config: AppConfig,
    items: list[dict],
    *,
    builder: SummaryBuilder,
    rewrite_gate: RewriteGate | None,
    activity_callback: RewriteActivityCallback | None,
    active_week_start: datetime,
    rewritten_by: str,
    rewritten_at: str,
    dry_run: bool,
    aggressive_prodding: bool,
    report: EnhanceReport,
) -> bool:
    file_changed = False
    for item in sorted(items, key=_buffer_rewrite_order):
        report.scanned_buffer += 1
        if _is_human_authored(str(item.get("author", "")), str(item.get("summarySource", ""))):
            report.skipped_human += 1
            continue
        force_ai_upgrade = _should_force_ai_upgrade(str(item.get("summarySource", "")), config)
        if not _eligible_buffer_item(item, active_week_start, aggressive_prodding, force_ai_upgrade):
            description = str(item.get("gitDiffDescription", ""))
            _count_strong_skip(report, force_ai_upgrade, aggressive_prodding, description)
            continue

        repo_label = str(item.get("repo", ""))
        repo_path = str(item.get("repoPath", ""))
        diff_text = str(item.get("gitDiffText", ""))
        builder_call = _call_builder(
            builder,
            repo_label,
            diff_text,
            repo_path,
            rewrite_gate,
            activity_callback,
        )
        if builder_call.gate_blocked:
            _mark_rewrite_deferred(report, "Buffer", _safe_item_id(repo_label, str(item.get("diffHash", ""))))
            continue

        rewrite = _rewrite_from_builder_result(
            builder_call.result,
            str(item.get("summarySource", "")),
            str(item.get("author", "Backup")),
        )
        if not _accept_rewrite(rewrite, force_ai_upgrade):
            _log_rewrite_skip(
                report,
                "Buffer",
                _safe_item_id(repo_label, str(item.get("diffHash", ""))),
                rewrite,
                force_ai_upgrade,
                builder_call.duration_seconds,
            )
            continue

        report.rewritten_buffer += 1
        assert rewrite is not None
        _mark_rewrite_success(
            report,
            "Buffer",
            _safe_item_id(repo_label, str(item.get("diffHash", ""))),
            rewrite,
            builder_call.duration_seconds,
        )
        if dry_run:
            continue

        item["gitDiffDescription"] = rewrite.text
        item["summarySource"] = rewrite.source
        item["author"] = rewrite.author
        item["rewrittenBy"] = rewritten_by
        item["rewrittenAt"] = rewritten_at
        file_changed = True
    return file_changed


def _rewrite_buffer_files(
    config: AppConfig,
    buffer_dir: Path,
    *,
    builder: SummaryBuilder,
    rewrite_gate: RewriteGate | None,
    activity_callback: RewriteActivityCallback | None,
    active_week_start: datetime,
    rewritten_by: str,
    rewritten_at: str,
    dry_run: bool,
    aggressive_prodding: bool,
    report: EnhanceReport,
) -> bool:
    updated = False
    for buffer_file in sorted(buffer_dir.glob("*.json")):
        items = _read_buffer_items(buffer_file)
        if not items:
            continue
        file_changed = _rewrite_buffer_items(
            config,
            items,
            builder=builder,
            rewrite_gate=rewrite_gate,
            activity_callback=activity_callback,
            active_week_start=active_week_start,
            rewritten_by=rewritten_by,
            rewritten_at=rewritten_at,
            dry_run=dry_run,
            aggressive_prodding=aggressive_prodding,
            report=report,
        )
        if file_changed:
            _write_buffer_items(buffer_file, items)
            updated = True
    return updated


def run_periodic_enhancer(
    config: AppConfig,
    *,
    summary_builder: SummaryBuilder | None = None,
    rewrite_gate: RewriteGate | None = None,
    activity_callback: RewriteActivityCallback | None = None,
    state_path: Path | None = None,
    buffer_dir: Path | None = None,
    now: datetime | None = None,
    force_run: bool = False,
    aggressive_prodding_override: bool | None = None,
) -> EnhanceReport:
    resolved_state_path = state_path or STATE_PATH
    resolved_buffer_dir = buffer_dir or BUFFER_DIR
    logs: list[str] = []
    dry_run = bool(config.summary_enhancer_dry_run)
    report = EnhanceReport(ran=False, dry_run=dry_run, logs=logs)
    current_time = now or datetime.now()

    if not _should_execute_enhancer(config, current_time, force_run, logs):
        return report

    report.ran = True
    active_week_start = _active_week_start(current_time)
    rewritten_at = _now_iso(current_time)
    rewritten_by = (config.summary_enhancer_rewritten_by or DEFAULT_REWRITER_ID).strip() or DEFAULT_REWRITER_ID
    if aggressive_prodding_override is None:
        aggressive_prodding = bool(config.summary_enhancer_aggressive_prodding)
    else:
        aggressive_prodding = aggressive_prodding_override
    builder = summary_builder or _default_builder(config)

    if not (resolved_state_path.exists() or resolved_state_path.parent.exists()):
        logs.append(f"State path unavailable: {resolved_state_path}")

    _rewrite_state_entries(
        config,
        builder=builder,
        rewrite_gate=rewrite_gate,
        activity_callback=activity_callback,
        active_week_start=active_week_start,
        rewritten_by=rewritten_by,
        rewritten_at=rewritten_at,
        dry_run=dry_run,
        aggressive_prodding=aggressive_prodding,
        report=report,
    )
    buffer_updated = _rewrite_buffer_files(
        config,
        resolved_buffer_dir,
        builder=builder,
        rewrite_gate=rewrite_gate,
        activity_callback=activity_callback,
        active_week_start=active_week_start,
        rewritten_by=rewritten_by,
        rewritten_at=rewritten_at,
        dry_run=dry_run,
        aggressive_prodding=aggressive_prodding,
        report=report,
    )

    if not dry_run:
        config.summary_enhancer_last_run_at = rewritten_at
        save_config(config, state_path=resolved_state_path)

    if buffer_updated:
        logs.append("Buffer files updated")

    return report


def _should_execute_enhancer(
    config: AppConfig,
    current_time: datetime,
    force_run: bool,
    logs: list[str],
) -> bool:
    should_run, _, skip_message = _should_run_now(config, current_time)
    if should_run:
        return True
    if force_run and skip_message == "Enhancer skipped: interval not reached":
        return True
    if skip_message:
        logs.append(skip_message)
    return False
