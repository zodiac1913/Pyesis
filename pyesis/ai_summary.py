from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from urllib import error, request

from pyesis.git_monitor import FileChangeSummary, summarize_file_changes


SYSTEM_PROMPT = (
    "Rewrite git diff activity as a short first-person work-log bullet. "
    "Use a single sentence, past tense, concrete wording, and no markdown bullet prefix. "
    "Prefer explicit verbs like added, removed, renamed, refactored, and mention what changed. "
    "Avoid vague wording like updated or worked on unless details are unavailable. "
    "Prefer describing intent over line counts; only include numeric deltas as a fallback."
)
NO_INTENT_SENTINEL = "made updates"
LOW_SIGNAL_INTENTS = {"adding imports", "adding follow-up notes"}
INTENT_PAST_TENSE_PREFIXES = {
    "adding ": "added ",
    "updating ": "updated ",
    "adjusting ": "adjusted ",
    "tightening ": "tightened ",
    "changing ": "changed ",
}
INTENT_PURPOSE_PREFIXES = {
    "adding ": "add ",
    "updating ": "update ",
    "adjusting ": "adjust ",
    "tightening ": "tighten ",
    "changing ": "change ",
    "improving ": "improve ",
}
APP_FILENAME = "app.py"
APP_PATH_SUFFIXES = (f"/{APP_FILENAME}", APP_FILENAME)


@dataclass
class AISummaryResult:
    text: str
    source: str


def build_summary(repo_label: str, diff_text: str) -> AISummaryResult:
    mode = os.getenv("PYESIS_AI_MODE", "heuristic").strip().lower()
    if mode == "openai-compatible":
        try:
            return AISummaryResult(text=_openai_compatible_summary(repo_label, diff_text), source="openai-compatible")
        except Exception:
            pass
    return AISummaryResult(text=_heuristic_summary(repo_label, diff_text), source="heuristic")


def _heuristic_summary(repo_label: str, diff_text: str) -> str:
    changes = _coalesce_changes(summarize_file_changes(diff_text))
    if not changes:
        return f"Updated work in {repo_label}."
    return _robust_bulleted_summary(repo_label, changes)


def _robust_bulleted_summary(repo_label: str, changes: list[FileChangeSummary]) -> str:
    lines: list[str] = []
    top_paths = _path_rollup(changes)
    top_intents = _top_intents(changes, max_items=3)
    lines.append(f"I updated {repo_label} by changing {top_paths} in this capture window.")
    lines.append(f"This work aimed to {_why_clause(top_intents, changes)} through {_how_clause(changes, top_intents)}.")

    ranked = sorted(
        changes,
        key=lambda c: (_file_priority(c.path), c.added_lines + c.removed_lines),
        reverse=True,
    )
    for change in ranked[:6]:
        lines.append(_change_detail_line(change))

    return "\n".join(lines)


def _why_clause(intents: list[str], changes: list[FileChangeSummary]) -> str:
    if not intents or intents == [NO_INTENT_SENTINEL]:
        return _fallback_goal(changes)

    purpose_bits = [_intent_to_purpose(intent) for intent in intents[:2]]
    joined = _join_with_and(purpose_bits)
    return joined


def _how_clause(changes: list[FileChangeSummary], intents: list[str]) -> str:
    file_count = _count_phrase(len(changes), "file")
    if intents and intents != [NO_INTENT_SENTINEL]:
        return f"edits across {file_count}, including {_join_with_and(intents[:2])}"
    return f"{_action_rollup(changes)} across {file_count}"


def _fallback_goal(changes: list[FileChangeSummary]) -> str:
    path_l = [change.path.lower().replace("\\", "/") for change in changes]
    if any(p.endswith(APP_PATH_SUFFIXES) for p in path_l):
        return "improve application flow and user-facing behavior"
    if any("config" in p or p.endswith(".json") for p in path_l):
        return "improve configuration reliability"
    if any(p.endswith(".md") for p in path_l):
        return "improve documentation clarity"
    return "advance implementation quality in the touched areas"


def _action_rollup(changes: list[FileChangeSummary]) -> str:
    created = sum(1 for c in changes if c.action == "created")
    deleted = sum(1 for c in changes if c.action == "deleted")
    renamed = sum(1 for c in changes if c.action == "renamed")
    modified = sum(1 for c in changes if c.action == "modified")

    action_bits: list[str] = []
    if created:
        action_bits.append(f"creating {_count_phrase(created, 'file')}")
    if modified:
        action_bits.append(f"refining {_count_phrase(modified, 'file')}")
    if renamed:
        action_bits.append(f"renaming {_count_phrase(renamed, 'file')}")
    if deleted:
        action_bits.append(f"removing {_count_phrase(deleted, 'file')}")

    if not action_bits:
        return "implementation updates"
    return _join_with_and(action_bits)


def _path_rollup(changes: list[FileChangeSummary], explicit_limit: int = 6) -> str:
    paths = [change.path for change in changes]
    if len(paths) <= explicit_limit:
        return _join_with_and(paths)

    shown = _join_with_and(paths[:explicit_limit])
    return f"{shown}, plus {_count_phrase(len(paths) - explicit_limit, 'additional file')}"


def _intent_to_purpose(intent: str) -> str:
    text = intent.strip()
    for from_prefix, to_prefix in INTENT_PURPOSE_PREFIXES.items():
        if text.startswith(from_prefix):
            return to_prefix + text[len(from_prefix):]
    return text


def _change_detail_line(change: FileChangeSummary) -> str:
    path = change.path
    churn = f"{change.added_lines}+/{change.removed_lines}-"
    intents = _intents_for_change(change)
    intent_text = _join_with_and([_to_past_tense(intent) for intent in intents[:2]]) if intents else "refined logic"

    if change.action == "created":
        base = f"Created {path} ({churn}) and {intent_text}."
    elif change.action == "deleted":
        base = f"Removed {path} ({churn}) while cleaning obsolete implementation."
    elif change.action == "renamed":
        base = f"Renamed {path} ({churn}) and updated dependent behavior."
    else:
        base = f"Updated {path} ({churn}) by {intent_text}."

    snippet = _best_sample_snippet(change)
    if not snippet:
        return base
    return f"{base} Notable line: {snippet}."


def _best_sample_snippet(change: FileChangeSummary) -> str:
    candidate = ""
    if change.added_samples:
        candidate = change.added_samples[0]
    elif change.removed_samples:
        candidate = change.removed_samples[0]
    cleaned = re.sub(r"\s+", " ", candidate.strip())
    if not cleaned:
        return ""
    if len(cleaned) > 110:
        return cleaned[:107] + "..."
    return cleaned


def _coalesce_changes(changes: list[FileChangeSummary]) -> list[FileChangeSummary]:
    merged: dict[tuple[str, str], FileChangeSummary] = {}
    order: list[tuple[str, str]] = []
    for change in changes:
        key = (change.path, change.action)
        existing = merged.get(key)
        if existing is None:
            merged[key] = FileChangeSummary(
                path=change.path,
                action=change.action,
                added_lines=change.added_lines,
                removed_lines=change.removed_lines,
                added_samples=list(change.added_samples),
                removed_samples=list(change.removed_samples),
            )
            order.append(key)
            continue

        existing.added_lines += change.added_lines
        existing.removed_lines += change.removed_lines
        _merge_samples(existing.added_samples, change.added_samples)
        _merge_samples(existing.removed_samples, change.removed_samples)

    return [merged[key] for key in order]


def _merge_samples(target: list[str], incoming: list[str], limit: int = 6) -> None:
    for snippet in incoming:
        if snippet in target:
            continue
        target.append(snippet)
        if len(target) >= limit:
            break


def _single_change_sentence(repo_label: str, change: FileChangeSummary) -> str:
    intent_suffix = _intent_suffix(change)
    if change.action == "created":
        return f"I created {change.path} in {repo_label}{intent_suffix}."
    if change.action == "deleted":
        return f"I removed {change.path} from {repo_label}{intent_suffix}."
    if change.action == "renamed":
        return (
            f"I renamed {change.path} in {repo_label}{intent_suffix} "
            "and adjusted related logic."
        )

    description = _file_phrase(change)
    if description != f"changed {change.path}":
        return f"I {description} in {repo_label}."

    return (
        f"I changed {change.path} in {repo_label}{intent_suffix}, "
        f"adding {change.added_lines} lines and removing {change.removed_lines}."
    )


def _multi_change_sentence(repo_label: str, changes: list[FileChangeSummary]) -> str:
    created = sum(1 for c in changes if c.action == "created")
    deleted = sum(1 for c in changes if c.action == "deleted")
    renamed = sum(1 for c in changes if c.action == "renamed")
    modified = sum(1 for c in changes if c.action == "modified")
    added = sum(c.added_lines for c in changes)
    removed = sum(c.removed_lines for c in changes)

    action_bits: list[str] = []
    if created:
        action_bits.append(f"created {_count_phrase(created, 'file')}")
    if deleted:
        action_bits.append(f"removed {_count_phrase(deleted, 'file')}")
    if renamed:
        action_bits.append(f"renamed {_count_phrase(renamed, 'file')}")
    if modified:
        action_bits.append(f"modified {_count_phrase(modified, 'file')}")

    lead = _join_with_and(action_bits)
    top_paths = ", ".join(change.path for change in changes[:2])
    if len(changes) > 2:
        top_paths = f"{top_paths}, and {_count_phrase(len(changes) - 2, 'other file')}"

    top_intents = _join_with_and(_top_intents(changes, max_items=2))
    intent_clause = f", including {top_intents}" if top_intents != NO_INTENT_SENTINEL else ""

    file_phrases = _top_file_phrases(changes, max_items=3)
    if file_phrases:
        return f"I worked on {repo_label} by {_join_with_and(file_phrases)}{intent_clause}."

    if top_intents != NO_INTENT_SENTINEL:
        return f"I worked on {repo_label} by {top_intents} across {top_paths}."

    return (
        f"I {lead} in {repo_label} across {top_paths}, "
        f"with {_count_phrase(added, 'addition')} and {_count_phrase(removed, 'removal')}."
    )


def _join_with_and(parts: list[str]) -> str:
    if not parts:
        return NO_INTENT_SENTINEL
    if len(parts) == 1:
        return parts[0]
    if len(parts) == 2:
        return f"{parts[0]} and {parts[1]}"
    return f"{', '.join(parts[:-1])}, and {parts[-1]}"


def _count_phrase(value: int, singular: str) -> str:
    suffix = "" if value == 1 else "s"
    return f"{value} {singular}{suffix}"


def _intent_suffix(change: FileChangeSummary) -> str:
    intents = _intents_for_change(change)
    if not intents:
        return ""
    return f" by {_join_with_and(intents[:2])}"


def _top_intents(changes: list[FileChangeSummary], max_items: int) -> list[str]:
    high_signal = _collect_unique_intents(changes, max_items=max_items, skip=LOW_SIGNAL_INTENTS)
    if high_signal:
        return high_signal
    return _collect_unique_intents(changes, max_items=max_items)


def _collect_unique_intents(
    changes: list[FileChangeSummary],
    max_items: int,
    skip: set[str] | None = None,
) -> list[str]:
    found: list[str] = []
    blocked = skip or set()
    for change in changes:
        for intent in _intents_for_change(change):
            if intent in blocked or intent in found:
                continue
            found.append(intent)
            if len(found) >= max_items:
                return found
    return found


def _top_file_phrases(changes: list[FileChangeSummary], max_items: int) -> list[str]:
    ranked = sorted(
        changes,
        key=lambda c: (_file_priority(c.path), c.added_lines + c.removed_lines),
        reverse=True,
    )
    phrases: list[str] = []
    for change in ranked:
        phrase = _file_phrase(change)
        if phrase and phrase not in phrases:
            phrases.append(phrase)
        if len(phrases) >= max_items:
            break
    return phrases


def _file_phrase(change: FileChangeSummary) -> str:
    path = change.path
    path_l = path.lower()
    intents = _intents_for_change(change)

    special = _special_file_phrase(path, path_l, change, intents)
    if special:
        return special

    if any(token in path_l for token in ("controller", "service", "extension", "handler")):
        if intents:
            return f"{_to_past_tense(intents[0])} in {path}"
        return f"refined logic in {path}"
    if intents:
        return f"{_to_past_tense(intents[0])} in {path}"
    return f"changed {path}"


def _special_file_phrase(path: str, path_l: str, change: FileChangeSummary, intents: list[str]) -> str:
    path_name = Path(path).name.lower()
    path_stem = Path(path).stem.lower()
    if path_l.endswith(APP_FILENAME) or "/app." in path_l.replace("\\", "/"):
        app_phrase = _app_change_phrase(change)
        if app_phrase:
            return app_phrase
        return f"enhanced application flow in {path}"
    if path_l.endswith("ai_summary.py"):
        significant = [x for x in intents if x not in LOW_SIGNAL_INTENTS]
        if significant:
            return f"improved summary generation in {path} by {_join_with_and([_to_past_tense(x) for x in significant[:2]])}"
        return f"improved summary generation in {path}"
    if path_l.endswith("document_formatter.py"):
        return f"improved log formatting in {path}"
    if path_l.endswith(("readme.md", ".md")):
        return f"updated documentation in {path}"
    if path_name in {"config.py", "appsettings.json", "settings.json"} or path_stem.startswith("config"):
        setting_phrase = _config_setting_phrase(change)
        if setting_phrase:
            return f"expanded configuration in {path} by {setting_phrase}"
        return f"expanded configuration handling in {path}"
    return ""


def _config_setting_phrase(change: FileChangeSummary) -> str:
    keys: list[str] = []
    patterns = [
        re.compile(r"^\s*([A-Za-z_]\w*)\s*[:=]"),
        re.compile(r'^\s*"([A-Za-z_]\w*)"\s*:'),
    ]
    for line in change.added_samples:
        for pattern in patterns:
            match = pattern.match(line)
            if match:
                name = match.group(1)
                if name not in keys and not name.startswith("__"):
                    keys.append(name)
                break
        if len(keys) >= 3:
            break
    if not keys:
        return ""
    noun = "setting" if len(keys) == 1 else "settings"
    return f"adding {_join_with_and(keys)} {noun}"


def _to_past_tense(intent: str) -> str:
    for from_prefix, to_prefix in INTENT_PAST_TENSE_PREFIXES.items():
        if intent.startswith(from_prefix):
            return to_prefix + intent[len(from_prefix):]
    return intent


def _file_priority(path: str) -> int:
    path_l = path.lower().replace("\\", "/")
    if path_l.endswith(APP_FILENAME):
        return 100
    if path_l.endswith("config.py"):
        return 80
    if path_l.endswith("ai_summary.py"):
        return 70
    if path_l.endswith("document_formatter.py"):
        return 60
    if path_l.endswith(("readme.md", ".md")):
        return 20
    return 40


def _app_change_phrase(change: FileChangeSummary) -> str:
    combined = "\n".join(change.added_samples + change.removed_samples).lower()
    path = change.path
    phrases: list[str] = []

    if any(token in combined for token in ("settings", "_open_settings", "tooltip", "⚙")):
        phrases.append("added settings controls")
    if any(token in combined for token in ("readme", "_open_readme_view", "ⓘ", "info")):
        phrases.append("added a README info action")
    if any(token in combined for token in ("github", "_open_github_repo", "🐙", "webbrowser")):
        phrases.append("added a GitHub link action")
    if any(token in combined for token in ("bind_all", "<control-comma>", "<f1>", "<control-shift-g>")):
        phrases.append("added keyboard shortcuts")
    if any(token in combined for token in ("header", "editor_header", "weekly work log preview")):
        phrases.append("refined the preview header layout")

    if not phrases:
        return ""
    return f"{_join_with_and(phrases[:3])} in {path}"


def _intents_for_change(change: FileChangeSummary) -> list[str]:
    added = "\n".join(change.added_samples).lower()
    removed = "\n".join(change.removed_samples).lower()
    combined = f"{added}\n{removed}"
    return [
        label
        for label, matched in _intent_rules(added, removed, combined)
        if matched
    ]


def _intent_rules(added: str, removed: str, combined: str) -> list[tuple[str, bool]]:
    return [
        ("adding null checks", ("if (" in added or "if " in added) and "null" in added),
        ("adding exception handling", "throw new" in added),
        ("updating logging", any(token in combined for token in ("logger", "log.", "console."))),
        ("adjusting return flow", "return " in added and "return " in removed),
        ("updating mapping logic", "map(" in combined or "mapper" in combined),
        ("tightening validation", "validate" in combined or "validator" in combined),
        ("changing async flow", "await " in combined or "async " in combined),
        ("adding imports", "using " in added or "import " in added),
        ("adding follow-up notes", "todo" in added or "fixme" in added),
    ]


def _openai_compatible_summary(repo_label: str, diff_text: str) -> str:
    url = os.getenv("PYESIS_AI_URL", "").strip()
    model = os.getenv("PYESIS_AI_MODEL", "").strip()
    api_key = os.getenv("PYESIS_AI_API_KEY", "").strip()
    if not url or not model:
        raise RuntimeError("Missing AI endpoint configuration")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Repository: {repo_label}\n"
                        "Describe this diff as one first-person sentence suitable for a weekly work log. "
                        "Be specific about what changed (added, removed, renamed, or modified) and avoid generic phrasing.\n\n"
                    f"{diff_text[:12000]}"
                ),
            },
        ],
        "temperature": 0.2,
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = request.Request(url, data=body, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except error.URLError as exc:
        raise RuntimeError(str(exc)) from exc

    content = data["choices"][0]["message"]["content"].strip()
    return content.splitlines()[0].lstrip("- ").strip()