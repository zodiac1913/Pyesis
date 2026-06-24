from __future__ import annotations

from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from pyesis.github_auth import (
    GITHUB_DOTCOM_AUTH_MODE,
    GITHUB_MODELS_CHAT_COMPLETIONS_URL,
    normalize_github_auth_endpoint,
    normalize_github_auth_mode,
)


STATE_PATH = Path("pyesis_state.json")
NEAR_DUP_DIFF_SIMILARITY_THRESHOLD = 0.80
OLLAMA_DEFAULT_URL = "http://localhost:11434/api/chat"
OLLAMA_DEFAULT_TIMEOUT_SECONDS = 180
SUPPORTED_AI_MODES = {"heuristic", "ollama", "openai-compatible", "github-gpt"}
LEGACY_GITHUB_COPILOT_MODE = "github-copilot"
GITHUB_GPT_DEFAULT_MODEL = "openai/gpt-5"
GITHUB_GPT_MINI_MODEL = "openai/gpt-5-mini"
GITHUB_GPT_DEFAULT_MODELS = (GITHUB_GPT_DEFAULT_MODEL, GITHUB_GPT_MINI_MODEL)
LEGACY_GITHUB_GPT_MODEL_ALIASES = {
    "gpt-5.4": GITHUB_GPT_DEFAULT_MODEL,
    "gpt-5.4-mini": GITHUB_GPT_MINI_MODEL,
    "gpt-5": GITHUB_GPT_DEFAULT_MODEL,
    "gpt-5-mini": GITHUB_GPT_MINI_MODEL,
}


def default_export_directory() -> str:
    documents_dir = Path.home() / "Documents"
    base_dir = documents_dir if documents_dir.exists() else Path.home()
    return str(base_dir / "Pyesis")


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
    summary_source: str = ""
    author: str = "Backup"
    rewritten_by: str = ""
    rewritten_at: str = ""
    requested_summary_source: str = ""
    summary_warning: str = ""
    fallback_summary_source: str = ""
    summary_timing_ms: int = 0
    summary_provider_details: str = ""


@dataclass
class AppConfig:
    week_end_day: str = "Thursday"
    theme_mode: str = "system"
    high_contrast: bool = False
    ui_font_size: int = 11
    export_directory: str = field(default_factory=default_export_directory)
    auto_export_time: str = ""
    last_auto_export_date: str = ""
    ai_mode: str = "heuristic"
    ai_fallback_enabled: bool = True
    ai_attempt_logging_enabled: bool = True
    ai_ollama_url: str = OLLAMA_DEFAULT_URL
    ai_ollama_model: str = ""
    ai_ollama_keep_alive: str = "30m"
    ai_ollama_timeout_seconds: int = OLLAMA_DEFAULT_TIMEOUT_SECONDS
    ai_openai_url: str = ""
    ai_openai_model: str = ""
    ai_github_gpt_url: str = GITHUB_MODELS_CHAT_COMPLETIONS_URL
    ai_github_gpt_model: str = GITHUB_GPT_DEFAULT_MODEL
    github_auth_mode: str = GITHUB_DOTCOM_AUTH_MODE
    github_auth_endpoint: str = ""
    github_oauth_client_id: str = ""
    summary_enhancer_enabled: bool = True
    summary_enhancer_interval_minutes: int = 5
    summary_enhancer_dry_run: bool = True
    summary_enhancer_aggressive_prodding: bool = False
    summary_enhancer_last_run_at: str = ""
    summary_enhancer_rewritten_by: str = "PyesisSummaryEnhancer"
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
                existing_source = (existing.summary_source or "").strip().lower()
                entry_source = (entry.summary_source or "").strip().lower()
                if existing_source != "heuristic" and entry_source == "heuristic":
                    return
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
        summary_source=str(item.get("summary_source", "")).strip().lower(),
        author=str(item.get("author", "Backup")),
        rewritten_by=str(item.get("rewritten_by", "")).strip(),
        rewritten_at=str(item.get("rewritten_at", "")).strip(),
        requested_summary_source=str(item.get("requested_summary_source", "")).strip().lower(),
        summary_warning=str(item.get("summary_warning", "")).strip(),
        fallback_summary_source=str(item.get("fallback_summary_source", "")).strip().lower(),
        summary_timing_ms=max(0, int(item.get("summary_timing_ms", 0) or 0)),
        summary_provider_details=str(item.get("summary_provider_details", "")).strip(),
        diff_hash=item["diff_hash"],
        diff_excerpt=item.get("diff_excerpt", ""),
    )


def _normalize_ai_mode(value: Any) -> str:
    ai_mode = str(value or "heuristic").strip().lower() or "heuristic"
    if ai_mode == LEGACY_GITHUB_COPILOT_MODE:
        return "github-gpt"
    if ai_mode not in SUPPORTED_AI_MODES:
        return "heuristic"
    return ai_mode


def _normalize_github_gpt_model(value: Any) -> str:
    raw_model = str(value or GITHUB_GPT_DEFAULT_MODEL).strip()
    if not raw_model:
        return GITHUB_GPT_DEFAULT_MODEL
    return LEGACY_GITHUB_GPT_MODEL_ALIASES.get(raw_model.lower(), raw_model)


def _should_rewrite_saved_entries(
    raw_entries: list[EntryRecord],
    entries: list[EntryRecord],
    raw_entry_items: list[Any],
) -> bool:
    missing_entry_author = any(isinstance(item, dict) and "author" not in item for item in raw_entry_items)
    missing_entry_source = any(isinstance(item, dict) and "summary_source" not in item for item in raw_entry_items)
    missing_requested_source = any(isinstance(item, dict) and "requested_summary_source" not in item for item in raw_entry_items)
    missing_warning = any(isinstance(item, dict) and "summary_warning" not in item for item in raw_entry_items)
    missing_fallback_source = any(isinstance(item, dict) and "fallback_summary_source" not in item for item in raw_entry_items)
    missing_timing = any(isinstance(item, dict) and "summary_timing_ms" not in item for item in raw_entry_items)
    missing_provider_details = any(isinstance(item, dict) and "summary_provider_details" not in item for item in raw_entry_items)
    return (
        len(entries) != len(raw_entries)
        or missing_entry_author
        or missing_entry_source
        or missing_requested_source
        or missing_warning
        or missing_fallback_source
        or missing_timing
        or missing_provider_details
    )


def load_config() -> AppConfig:
    if not STATE_PATH.exists():
        return AppConfig()

    data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    default_export_dir = default_export_directory()
    raw_export_directory = str(data.get("export_directory", "") or "").strip()
    if not raw_export_directory:
        export_directory = default_export_dir
    else:
        configured_path = Path(raw_export_directory).expanduser()
        legacy_export_dir = Path("exports")
        if configured_path == legacy_export_dir or configured_path.resolve() == legacy_export_dir.resolve():
            export_directory = default_export_dir
        else:
            export_directory = raw_export_directory
    theme_mode = str(data.get("theme_mode", "system")).lower()
    if theme_mode not in {"system", "light", "dark"}:
        theme_mode = "system"
    raw_entry_items = data.get("entries", [])
    raw_entries = [_decode_entry(item) for item in raw_entry_items]
    entries = dedupe_entries(raw_entries)
    if _should_rewrite_saved_entries(raw_entries, entries, raw_entry_items):
        data["entries"] = [asdict(entry) for entry in entries]
        STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")

    ai_mode = _normalize_ai_mode(data.get("ai_mode", "heuristic"))

    return AppConfig(
        week_end_day=data.get("week_end_day", "Thursday"),
        theme_mode=theme_mode,
        high_contrast=bool(data.get("high_contrast", False)),
        ui_font_size=max(10, min(20, int(data.get("ui_font_size", 11)))),
        export_directory=export_directory,
        auto_export_time=str(data.get("auto_export_time", "")),
        last_auto_export_date=str(data.get("last_auto_export_date", "")),
        ai_mode=ai_mode,
        ai_fallback_enabled=bool(data.get("ai_fallback_enabled", True)),
        ai_attempt_logging_enabled=bool(data.get("ai_attempt_logging_enabled", True)),
        ai_ollama_url=str(data.get("ai_ollama_url", OLLAMA_DEFAULT_URL)).strip() or OLLAMA_DEFAULT_URL,
        ai_ollama_model=str(data.get("ai_ollama_model", "")).strip(),
        ai_ollama_keep_alive=str(data.get("ai_ollama_keep_alive", "30m")).strip() or "30m",
        ai_ollama_timeout_seconds=max(30, int(data.get("ai_ollama_timeout_seconds", OLLAMA_DEFAULT_TIMEOUT_SECONDS) or OLLAMA_DEFAULT_TIMEOUT_SECONDS)),
        ai_openai_url=str(data.get("ai_openai_url", "")).strip(),
        ai_openai_model=str(data.get("ai_openai_model", "")).strip(),
        ai_github_gpt_url=str(data.get("ai_github_gpt_url", data.get("ai_github_copilot_url", GITHUB_MODELS_CHAT_COMPLETIONS_URL))).strip() or GITHUB_MODELS_CHAT_COMPLETIONS_URL,
        ai_github_gpt_model=_normalize_github_gpt_model(data.get("ai_github_gpt_model", data.get("ai_github_copilot_model", GITHUB_GPT_DEFAULT_MODEL))),
        github_auth_mode=normalize_github_auth_mode(data.get("github_auth_mode", GITHUB_DOTCOM_AUTH_MODE)),
        github_auth_endpoint=normalize_github_auth_endpoint(
            data.get("github_auth_mode", GITHUB_DOTCOM_AUTH_MODE),
            data.get("github_auth_endpoint", ""),
        ),
        github_oauth_client_id=str(data.get("github_oauth_client_id", "")).strip(),
        summary_enhancer_enabled=bool(data.get("summary_enhancer_enabled", True)),
        summary_enhancer_interval_minutes=max(1, int(data.get("summary_enhancer_interval_minutes", 1))),
        summary_enhancer_dry_run=bool(data.get("summary_enhancer_dry_run", True)),
        summary_enhancer_aggressive_prodding=bool(data.get("summary_enhancer_aggressive_prodding", False)),
        summary_enhancer_last_run_at=str(data.get("summary_enhancer_last_run_at", "")).strip(),
        summary_enhancer_rewritten_by=str(data.get("summary_enhancer_rewritten_by", "PyesisSummaryEnhancer")).strip() or "PyesisSummaryEnhancer",
        repos=[_decode_repo(item) for item in data.get("repos", [])],
        entries=entries,
    )


def save_config(config: AppConfig, state_path: Path = STATE_PATH) -> None:
    config.entries = dedupe_entries(config.entries)
    payload = {
        "week_end_day": config.week_end_day,
        "theme_mode": config.theme_mode,
        "high_contrast": config.high_contrast,
        "ui_font_size": config.ui_font_size,
        "export_directory": config.export_directory,
        "auto_export_time": config.auto_export_time,
        "last_auto_export_date": config.last_auto_export_date,
        "ai_mode": config.ai_mode,
        "ai_fallback_enabled": config.ai_fallback_enabled,
        "ai_attempt_logging_enabled": config.ai_attempt_logging_enabled,
        "ai_ollama_url": config.ai_ollama_url,
        "ai_ollama_model": config.ai_ollama_model,
        "ai_ollama_keep_alive": config.ai_ollama_keep_alive,
        "ai_ollama_timeout_seconds": max(30, int(config.ai_ollama_timeout_seconds)),
        "ai_openai_url": config.ai_openai_url,
        "ai_openai_model": config.ai_openai_model,
        "ai_github_gpt_url": config.ai_github_gpt_url,
        "ai_github_gpt_model": config.ai_github_gpt_model,
        "github_auth_mode": config.github_auth_mode,
        "github_auth_endpoint": config.github_auth_endpoint,
        "github_oauth_client_id": config.github_oauth_client_id,
        "summary_enhancer_enabled": config.summary_enhancer_enabled,
        "summary_enhancer_interval_minutes": config.summary_enhancer_interval_minutes,
        "summary_enhancer_dry_run": config.summary_enhancer_dry_run,
        "summary_enhancer_aggressive_prodding": config.summary_enhancer_aggressive_prodding,
        "summary_enhancer_last_run_at": config.summary_enhancer_last_run_at,
        "summary_enhancer_rewritten_by": config.summary_enhancer_rewritten_by,
        "repos": [asdict(repo) for repo in config.repos],
        "entries": [asdict(entry) for entry in config.entries],
    }
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")