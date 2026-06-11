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
    "Assume each diff is usually a single file change and name the file directly when the evidence supports it. "
    "Prefer explicit verbs like added, removed, renamed, refactored, and mention what changed. "
    "Avoid vague wording like updated or worked on unless details are unavailable. "
    "Never use filler phrases like 'updated implementation details', 'keep behavior aligned with the current implementation goals', or 'advance implementation quality'. "
    "Do not describe the task mechanically. Explain the real code change and likely intent in plain engineering language. "
    "If the diff mostly shows cleanup or reformatting, say that directly instead of inventing behavior changes. "
    "Prefer describing intent over line counts; only include numeric deltas as a fallback."
)
HEURISTIC_MODE = "heuristic"
OLLAMA_MODE = "ollama"
OPENAI_COMPATIBLE_MODE = "openai-compatible"
GITHUB_GPT_MODE = "github-gpt"
LEGACY_GITHUB_COPILOT_MODE = "github-copilot"
SUPPORTED_AI_MODES = {
    HEURISTIC_MODE,
    OLLAMA_MODE,
    OPENAI_COMPATIBLE_MODE,
    GITHUB_GPT_MODE,
}
AI_PROVIDER_LABELS = {
    HEURISTIC_MODE: "Heuristic",
    OLLAMA_MODE: "Ollama",
    OPENAI_COMPATIBLE_MODE: "OpenAI-compatible",
    GITHUB_GPT_MODE: "GitHub GPT",
}
NO_INTENT_SENTINEL = "made updates"
IMPORT_INTENT = "adding imports"
LOW_SIGNAL_INTENTS = {IMPORT_INTENT, "adding follow-up notes"}
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
MARKDOWN_SUFFIXES = ("readme.md", ".md")
JSON_SUFFIX = ".json"
YAML_SUFFIXES = (".yml", ".yaml")
DIFF_TIME_UNAVAILABLE = "Not available from the diff."
SUMMARY_EXCLUDED_PATH_PREFIXES = ("diff_buffers/", "exports/", "__pycache__/")
SUMMARY_EXCLUDED_PATHS = {"pyesis_state.json"}
AI_DIFF_CHAR_LIMIT = 8000
AI_CONTEXT_FILE_LIMIT = 3
AI_CONTEXT_RADIUS = 20
AI_CONTEXT_CHAR_LIMIT = 6000
HUNK_HEADER_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
GOOD_WRITING_EXAMPLES = (
    "In Controllers/Configurer/ConfigurerController.cs, I hardened AppSec recovery by adding a null check around appSecDto, rebuilding appSec only when session data exists, and cleaning up the AppSec and role-handling flow so the code is easier to read.",
    "In pyesis/app.py, I changed the capture flow to split diffs by file before summarizing them and tightened validation so write-ups are generated from cleaner per-file inputs.",
)
BAD_WRITING_EXAMPLES = (
    "In wwwroot/js/Configurer/conjure.js, I refined logic to clarify configuration behavior by changing code around '//}'.",
    "In wwwroot/js/Configurer/conjureTable.js, I updated logging to clarify configuration behavior by updated logging and changing code around 'import {'.",
)


@dataclass
class AISummaryResult:
    text: str
    source: str
    requested_source: str = ""
    warning: str = ""
    fallback_source: str = ""

    @property
    def used_fallback(self) -> bool:
        return bool(self.fallback_source)


@dataclass
class StructuredSummary:
    who: str
    what: str
    where: str
    when: str
    why: str
    how: str

    def to_text(self) -> str:
        return _compose_description(self)


def build_summary(
    repo_label: str,
    diff_text: str,
    repo_path: str | None = None,
    mode: str | None = None,
    allow_fallback: bool = True,
) -> AISummaryResult:
    selected_mode = _normalize_ai_mode(mode or os.getenv("PYESIS_AI_MODE", HEURISTIC_MODE))
    if selected_mode == OLLAMA_MODE:
        return _build_provider_summary(selected_mode, repo_label, diff_text, repo_path, _ollama_structured_summary, allow_fallback)
    if selected_mode == OPENAI_COMPATIBLE_MODE:
        return _build_provider_summary(selected_mode, repo_label, diff_text, repo_path, _openai_compatible_structured_summary, allow_fallback)
    if selected_mode == GITHUB_GPT_MODE:
        return _build_provider_summary(selected_mode, repo_label, diff_text, repo_path, _github_gpt_structured_summary, allow_fallback)

    return AISummaryResult(
        text=_heuristic_structured_summary(repo_label, diff_text).to_text(),
        source=HEURISTIC_MODE,
        requested_source=selected_mode,
    )


def _normalize_ai_mode(value: str) -> str:
    normalized = value.strip().lower()
    if normalized == LEGACY_GITHUB_COPILOT_MODE:
        return GITHUB_GPT_MODE
    if normalized in SUPPORTED_AI_MODES:
        return normalized
    return HEURISTIC_MODE


def _ai_provider_label(mode: str) -> str:
    return AI_PROVIDER_LABELS.get(mode, mode or "AI")


def _build_provider_summary(
    provider: str,
    repo_label: str,
    diff_text: str,
    repo_path: str | None,
    builder: callable,
    allow_fallback: bool,
) -> AISummaryResult:
    try:
        structured = builder(repo_label, diff_text, repo_path)
        return AISummaryResult(
            text=structured.to_text(),
            source=provider,
            requested_source=provider,
        )
    except Exception as exc:
        if not allow_fallback:
            return AISummaryResult(
                text="",
                source=provider,
                requested_source=provider,
                warning=f"{_ai_provider_label(provider)} summary failed: {exc}",
            )
        return AISummaryResult(
            text=_heuristic_structured_summary(repo_label, diff_text).to_text(),
            source=HEURISTIC_MODE,
            requested_source=provider,
            warning=f"{_ai_provider_label(provider)} summary failed: {exc}",
            fallback_source=HEURISTIC_MODE,
        )


def _heuristic_structured_summary(repo_label: str, diff_text: str) -> StructuredSummary:
    changes = _summary_relevant_changes(_coalesce_changes(summarize_file_changes(diff_text)))
    if not changes:
        return StructuredSummary(
            who="I",
            what=f"Updated work in {repo_label}.",
            where=repo_label,
            when=DIFF_TIME_UNAVAILABLE,
            why="The diff does not expose the intent clearly enough to infer more detail.",
            how="By applying code changes that were present in the diff.",
        )
    return _structured_summary_from_changes(repo_label, changes)


def _structured_summary_from_changes(repo_label: str, changes: list[FileChangeSummary]) -> StructuredSummary:
    ranked = sorted(
        changes,
        key=lambda c: (_file_priority(c.path), c.added_lines + c.removed_lines),
        reverse=True,
    )
    focus_changes = ranked[:3]
    top_change = focus_changes[0]
    intents = _top_intents(focus_changes, max_items=3)
    what = _structured_what_clause(repo_label, focus_changes, intents)
    where = _path_rollup(focus_changes)
    why = _structured_why_clause(focus_changes, intents)
    how = _structured_how_clause(focus_changes, intents)
    who = _structured_who_clause(repo_label, focus_changes)
    when = _structured_when_clause(top_change)
    return StructuredSummary(who=who, what=what, where=where, when=when, why=why, how=how)


def _significant_intents(intents: list[str]) -> list[str]:
    return [intent for intent in intents if intent not in LOW_SIGNAL_INTENTS]


def _structured_who_clause(repo_label: str, changes: list[FileChangeSummary]) -> str:
    del repo_label, changes
    return "I"


def _structured_what_clause(
    repo_label: str,
    changes: list[FileChangeSummary],
    intents: list[str],
) -> str:
    if len(changes) == 1:
        change = changes[0]
        return _sentence_without_period(_change_detail_line(change))

    file_phrases = _top_file_phrases(changes, max_items=3)
    if file_phrases:
        return f"I {_join_with_and(file_phrases)}"

    action_bits: list[str] = []
    created = sum(1 for change in changes if change.action == "created")
    deleted = sum(1 for change in changes if change.action == "deleted")
    renamed = sum(1 for change in changes if change.action == "renamed")
    modified = sum(1 for change in changes if change.action == "modified")
    if created:
        action_bits.append(f"created {_count_phrase(created, 'file')}")
    if deleted:
        action_bits.append(f"removed {_count_phrase(deleted, 'file')}")
    if renamed:
        action_bits.append(f"renamed {_count_phrase(renamed, 'file')}")
    if modified:
        action_bits.append(f"modified {_count_phrase(modified, 'file')}")

    if action_bits:
        lead = _join_with_and(action_bits)
        focus = _join_with_and([change.path for change in changes])
        if intents and intents != [NO_INTENT_SENTINEL]:
            return f"I {lead} in {repo_label}, mainly across {focus}, including {_join_with_and([_to_past_tense(intent) for intent in intents[:2]])}"
        return f"I {lead} in {repo_label}, mainly across {focus}"

    return f"I made targeted updates in {repo_label}"


def _structured_why_clause(changes: list[FileChangeSummary], intents: list[str]) -> str:
    significant = _significant_intents(intents)
    if significant:
        return _join_with_and([_intent_to_purpose(intent) for intent in significant[:2]])
    return _fallback_goal(changes)


def _structured_how_clause(changes: list[FileChangeSummary], intents: list[str]) -> str:
    methods: list[str] = []
    significant = _significant_intents(intents)
    if significant:
        methods.append(_join_with_and([_to_past_tense(intent) for intent in significant[:2]]))

    sample = _best_sample_snippet(changes[0])
    if sample:
        methods.append(f"changing code around '{sample}'")

    if not methods:
        methods.append(f"making direct edits in {_path_rollup(changes)}")

    return _join_with_and(methods)


def _structured_when_clause(change: FileChangeSummary) -> str:
    del change
    return DIFF_TIME_UNAVAILABLE


def _compose_description(summary: StructuredSummary) -> str:
    what_text = _sentence_without_period(summary.what)
    why_text = _sentence_without_period(summary.why)
    how_text = _sentence_without_period(summary.how)
    normalized_what = _normalized_clause(what_text)

    parts = [what_text]
    if (
        why_text
        and why_text != DIFF_TIME_UNAVAILABLE
        and _normalized_clause(why_text) not in normalized_what
        and not _clauses_meaningfully_overlap(what_text, why_text)
    ):
        parts.append(f"to {why_text}")
    if how_text and how_text != DIFF_TIME_UNAVAILABLE and _normalized_clause(how_text) not in normalized_what:
        parts.append(f"by {how_text}")
    return _sentence_without_period(" ".join(parts)).strip() + "."


def _sentence_without_period(text: str) -> str:
    return text.strip().rstrip(".")


def _clauses_meaningfully_overlap(left: str, right: str) -> bool:
    left_words = _meaningful_words(left)
    right_words = _meaningful_words(right)
    if not left_words or not right_words:
        return False
    return len(left_words & right_words) >= 2


def _meaningful_words(text: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    return words - {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "into",
        "your",
        "their",
        "behavior",
        "user",
        "facing",
    }


def _why_clause(intents: list[str], changes: list[FileChangeSummary]) -> str:
    if not intents or intents == [NO_INTENT_SENTINEL]:
        return _fallback_goal(changes)

    purpose_bits = [_intent_to_purpose(intent) for intent in intents[:2]]
    joined = _join_with_and(purpose_bits)
    return joined


def _how_clause(changes: list[FileChangeSummary], intents: list[str]) -> str:
    paths = _path_rollup(changes)
    if intents and intents != [NO_INTENT_SENTINEL]:
        return f"direct updates in {paths}, including {_join_with_and(intents[:2])}"
    return f"direct updates in {paths}"


def _fallback_goal(changes: list[FileChangeSummary]) -> str:
    path_l = [change.path.lower().replace("\\", "/") for change in changes]
    if any(p.endswith(APP_PATH_SUFFIXES) for p in path_l):
        return "improve the app flow and user-facing behavior"
    if any("config" in p or p.endswith(JSON_SUFFIX) for p in path_l):
        return "clarify configuration behavior"
    if any(p.endswith(".md") for p in path_l):
        return "clarify project usage and documentation"

    primary_path = changes[0].path if changes else "the changed files"
    return f"clarify behavior in {primary_path}"


def _path_rollup(changes: list[FileChangeSummary]) -> str:
    paths: list[str] = []
    for change in changes:
        if change.path not in paths:
            paths.append(change.path)
    return _join_with_and(paths)


def _intent_to_purpose(intent: str) -> str:
    text = intent.strip()
    for from_prefix, to_prefix in INTENT_PURPOSE_PREFIXES.items():
        if text.startswith(from_prefix):
            return to_prefix + text[len(from_prefix):]
    return text


def _change_detail_line(change: FileChangeSummary) -> str:
    intents = _intents_for_change(change)
    what_clause = _change_what_clause(change, intents)
    why_clause = _change_why_clause(change, intents)
    if _normalized_clause(what_clause) == _normalized_clause(why_clause):
        why_clause = _fallback_reason_for_path(change.path)
    return f"In {change.path}, I {what_clause} to {why_clause}."


def _change_what_clause(change: FileChangeSummary, intents: list[str]) -> str:
    if change.action == "created":
        return "created the file and added the initial implementation"
    if change.action == "deleted":
        return "removed obsolete code"
    if change.action == "renamed":
        return "renamed the file and adjusted references"

    high_signal = [intent for intent in intents if intent not in LOW_SIGNAL_INTENTS]
    if high_signal:
        return _join_with_and([_to_past_tense(intent) for intent in high_signal[:2]])

    path_l = change.path.lower().replace("\\", "/")
    if path_l.endswith(MARKDOWN_SUFFIXES):
        return "updated documentation text"
    if path_l.endswith(YAML_SUFFIXES):
        return "updated workflow automation"
    if path_l.endswith("pyproject.toml"):
        return "updated package metadata"
    return _fallback_what_for_path(change.path)


def _change_why_clause(change: FileChangeSummary, intents: list[str]) -> str:
    high_signal = [intent for intent in intents if intent not in LOW_SIGNAL_INTENTS]
    if high_signal:
        purposes = [_intent_to_purpose(intent) for intent in high_signal[:2]]
        return _join_with_and(purposes)

    return _fallback_reason_for_path(change.path)


def _fallback_reason_for_path(path: str) -> str:
    path_l = path.lower().replace("\\", "/")
    if path_l.endswith(MARKDOWN_SUFFIXES):
        return "make project usage clearer"
    if path_l.endswith(YAML_SUFFIXES):
        return "keep automation behavior reliable"
    if path_l.endswith("pyproject.toml"):
        return "keep packaging metadata accurate"
    if path_l.endswith(APP_FILENAME):
        return "improve the user-facing app flow"
    if "config" in path_l or path_l.endswith(JSON_SUFFIX):
        return "clarify configuration behavior"
    return f"clarify behavior in {Path(path).name}"


def _fallback_what_for_path(path: str) -> str:
    path_l = path.lower().replace("\\", "/")
    if path_l.endswith(APP_FILENAME):
        return "refined application flow"
    if path_l.endswith((".py", ".js", ".ts", ".tsx", ".jsx", ".cs")):
        return "refined logic"
    if path_l.endswith(".cshtml"):
        return "refined page behavior"
    if path_l.endswith((JSON_SUFFIX, ".toml", *YAML_SUFFIXES)):
        return "adjusted configuration data"
    return f"updated {Path(path).name}"


def _normalized_clause(text: str) -> str:
    normalized = text.lower().strip()
    replacements = {
        "added ": "add ",
        "updated ": "update ",
        "adjusted ": "adjust ",
        "tightened ": "tighten ",
        "changed ": "change ",
        "removed ": "remove ",
        "renamed ": "rename ",
    }
    for from_text, to_text in replacements.items():
        normalized = normalized.replace(from_text, to_text)
    normalized = re.sub(r"[^a-z0-9 ]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


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


def _clean_json_field(value: object, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def _structured_summary_from_json(data: object, changes: list[FileChangeSummary], repo_label: str) -> StructuredSummary:
    if not isinstance(data, dict):
        raise RuntimeError("AI response was not a JSON object")

    structured = StructuredSummary(
        who=_clean_json_field(data.get("who"), "I"),
        what=_clean_json_field(data.get("what"), _structured_what_clause(repo_label, changes[:3], _top_intents(changes, max_items=3))),
        where=_clean_json_field(data.get("where"), _path_rollup(changes[:3])),
        when=_clean_json_field(data.get("when"), DIFF_TIME_UNAVAILABLE),
        why=_clean_json_field(data.get("why"), _structured_why_clause(changes[:3], _top_intents(changes, max_items=3))),
        how=_clean_json_field(data.get("how"), _structured_how_clause(changes[:3], _top_intents(changes, max_items=3))),
    )
    return structured


def _summary_relevant_changes(changes: list[FileChangeSummary]) -> list[FileChangeSummary]:
    filtered = [change for change in changes if not _is_summary_excluded_path(change.path)]
    return filtered or changes


def _is_summary_excluded_path(path: str) -> bool:
    normalized = path.lower().replace("\\", "/")
    if normalized in SUMMARY_EXCLUDED_PATHS:
        return True
    return any(normalized.startswith(prefix) for prefix in SUMMARY_EXCLUDED_PATH_PREFIXES)


def _parse_ai_json_payload(content: str) -> object:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return json.loads(text)


def _build_ai_user_prompt(repo_label: str, diff_text: str, repo_path: str | None) -> str:
    raw_changes = _coalesce_changes(summarize_file_changes(diff_text))
    changes = _summary_relevant_changes(raw_changes) or raw_changes
    preferred_paths = ", ".join(change.path for change in changes[:3]) or DIFF_TIME_UNAVAILABLE
    signal_digest = _build_ai_change_digest(changes)
    prompt = (
        f"Repository: {repo_label}\n"
        f"Likely changed files: {preferred_paths}\n"
        "Review this diff and answer with JSON only. "
        "Use keys who, what, where, when, why, and how. "
        "Do not omit any key. If the diff does not provide a field, say 'Not available from the diff.' "
        "Keep values concise and concrete, and make 'what' a first-person summary of the code changes. "
        "Prefer concrete wording like 'added a null check', 'restored legacy script cleanup', or 'rewired the page to expose appSec earlier'. "
        "Do not mention line counts, generic implementation-quality phrases, or wording like 'changing code around'. "
        "If the diff is ambiguous, use the supplied code context and change digest to infer intent, but do not invent behavior that is not supported by the diff or code context.\n\n"
        "Good style examples:\n"
        f"- {GOOD_WRITING_EXAMPLES[0]}\n"
        f"- {GOOD_WRITING_EXAMPLES[1]}\n\n"
        "Bad style examples to avoid:\n"
        f"- {BAD_WRITING_EXAMPLES[0]}\n"
        f"- {BAD_WRITING_EXAMPLES[1]}\n\n"
        "Change digest:\n"
        f"{signal_digest}\n\n"
        "Diff:\n"
        f"{diff_text[:AI_DIFF_CHAR_LIMIT]}"
    )
    code_context = _build_ai_code_context(repo_path, diff_text)
    if code_context:
        prompt += f"\n\nSupplemental code context:\n{code_context}"
    return prompt


def _build_ai_change_digest(changes: list[FileChangeSummary]) -> str:
    if not changes:
        return "- No structured change hints were available."

    lines: list[str] = []
    for change in changes[:3]:
        intents = [intent for intent in _intents_for_change(change) if intent not in LOW_SIGNAL_INTENTS]
        intent_text = _join_with_and(intents[:2]) if intents else "no strong intent detected"
        sample = _best_sample_snippet(change)
        sample_text = f"; anchor: {sample}" if sample else ""
        lines.append(
            f"- {change.path}: action={change.action}, likely_intent={intent_text}, added={change.added_lines}, removed={change.removed_lines}{sample_text}"
        )
    return "\n".join(lines)


def _build_ai_code_context(repo_path: str | None, diff_text: str) -> str:
    if not repo_path:
        return ""

    repo_root = Path(repo_path)
    if not repo_root.exists():
        return ""

    raw_changes = _coalesce_changes(summarize_file_changes(diff_text))
    changes = _summary_relevant_changes(raw_changes) or raw_changes
    if not changes:
        return ""

    changed_line_map = _diff_changed_line_map(diff_text)
    snippets: list[str] = []
    total_chars = 0

    for change in changes[:AI_CONTEXT_FILE_LIMIT]:
        normalized_path = change.path.replace("\\", "/")
        file_path = repo_root / normalized_path
        if not file_path.exists() or not file_path.is_file():
            continue
        snippet = _read_code_context_snippet(file_path, changed_line_map.get(normalized_path, []))
        if not snippet:
            continue

        block = f"File: {normalized_path}\n```text\n{snippet}\n```"
        next_size = total_chars + len(block)
        if snippets and next_size > AI_CONTEXT_CHAR_LIMIT:
            break
        snippets.append(block)
        total_chars = next_size

    return "\n\n".join(snippets)


def _next_diff_path(line: str) -> str | None:
    if line.startswith("diff --git "):
        parts = line.split()
        if len(parts) >= 4:
            target = parts[3]
            return target.removeprefix("b/") if target.startswith("b/") else target
        return ""
    if line.startswith("+++ b/"):
        return line.removeprefix("+++ b/").strip()
    if line.startswith("+++ /dev/null"):
        return ""
    return None


def _next_hunk_line_number(line: str) -> int | None:
    match = HUNK_HEADER_RE.match(line)
    if not match:
        return None
    return int(match.group(1))


def _record_changed_line(
    changed_lines: dict[str, list[int]],
    current_path: str,
    new_line_number: int | None,
    line: str,
) -> int | None:
    if not current_path or new_line_number is None:
        return new_line_number

    if line.startswith("+") and not line.startswith("+++"):
        changed_lines.setdefault(current_path, []).append(new_line_number)
        return new_line_number + 1

    if line.startswith("-") and not line.startswith("---"):
        return new_line_number

    return new_line_number + 1


def _diff_changed_line_map(diff_text: str) -> dict[str, list[int]]:
    changed_lines: dict[str, list[int]] = {}
    current_path = ""
    new_line_number: int | None = None

    for line in diff_text.splitlines():
        next_path = _next_diff_path(line)
        if next_path is not None:
            current_path = next_path
            if line.startswith("diff --git "):
                new_line_number = None
            continue

        next_line_number = _next_hunk_line_number(line)
        if next_line_number is not None:
            new_line_number = next_line_number
            continue

        new_line_number = _record_changed_line(changed_lines, current_path, new_line_number, line)

    return changed_lines


def _read_code_context_snippet(file_path: Path, changed_lines: list[int]) -> str:
    try:
        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""

    if not lines:
        return ""

    if changed_lines:
        start = max(1, min(changed_lines[:6]) - AI_CONTEXT_RADIUS)
        end = min(len(lines), max(changed_lines[:6]) + AI_CONTEXT_RADIUS)
    else:
        start = 1
        end = min(len(lines), AI_CONTEXT_RADIUS * 2)

    return "\n".join(f"{index + 1}: {lines[index]}" for index in range(start - 1, end))


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


def _merge_samples(target: list[str], incoming: list[str], limit: int = 12) -> None:
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
    import_targets = _import_targets(change)
    has_import_intent = IMPORT_INTENT in intents

    special = _special_file_phrase(path, path_l, change, intents)
    if special:
        return special

    if any(token in path_l for token in ("controller", "service", "extension", "handler")):
        if intents:
            if has_import_intent and import_targets:
                return f"updated imports in {path} to use {_join_with_and(import_targets[:3])}"
            return f"{_to_past_tense(intents[0])} in {path}"
        return f"refined logic in {path}"
    if intents:
        if has_import_intent and import_targets:
            return f"updated imports in {path} to use {_join_with_and(import_targets[:3])}"
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
    if path_l.endswith(MARKDOWN_SUFFIXES):
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


def _import_targets(change: FileChangeSummary) -> list[str]:
    targets: list[str] = []
    for line in change.added_samples:
        target = _import_target_from_line(line)
        if target and target not in targets:
            targets.append(target)
        if len(targets) >= 4:
            break
    return targets


def _import_target_from_line(line: str) -> str:
    text = line.strip()
    if not text:
        return ""

    patterns = [
        re.compile(r"^import\s+.+?\s+from\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"^import\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"^}?\s*from\s+['\"]([^'\"]+)['\"]"),
        re.compile(r"^from\s+([A-Za-z0-9_\.]+)\s+import\s+(.+)$"),
        re.compile(r"^import\s+([A-Za-z0-9_\.,\s]+)$"),
        re.compile(r"^using\s+([A-Za-z0-9_\.]+);?$"),
    ]

    match = patterns[0].match(text)
    if match:
        return match.group(1).strip()

    match = patterns[1].match(text)
    if match:
        return match.group(1).strip()

    match = patterns[2].match(text)
    if match:
        return match.group(1).strip()

    match = patterns[3].match(text)
    if match:
        module_name = match.group(1).strip()
        imported_names = match.group(2).strip()
        imported_names = re.sub(r"\s+as\s+\w+", "", imported_names)
        imported_names = re.sub(r"\s+", " ", imported_names)
        return f"{module_name} ({imported_names})"

    match = patterns[4].match(text)
    if match:
        imported_names = [part.strip() for part in match.group(1).split(",") if part.strip()]
        if imported_names:
            return _join_with_and(imported_names[:3])

    match = patterns[5].match(text)
    if match:
        return match.group(1).strip()

    return ""


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
    if path_l.endswith(MARKDOWN_SUFFIXES):
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
        ("hardening null recovery", (("if (" in added or "if " in added) and "null" in added and "appsec" in combined)),
        ("adding null checks", ("if (" in added or "if " in added) and "null" in added),
        ("adding exception handling", "throw new" in added),
        ("updating logging", any(token in combined for token in ("logger", "log.", "console."))),
        ("adjusting return flow", "return " in added and "return " in removed),
        ("updating mapping logic", "map(" in combined or "mapper" in combined),
        ("tightening validation", "validate" in combined or "validator" in combined),
        ("changing async flow", "await " in combined or "async " in combined),
        ("cleaning up code layout", any(token in combined for token in ("/// <summary>", "public ", "function ", "class ")) and abs(len(added.splitlines()) - len(removed.splitlines())) <= 6),
        ("rewiring page state", any(token in added for token in ("window.appsec", "window.cso", "leviathan", "levi="))),
        ("restoring legacy script cleanup", "removelegacyscripts" in combined),
        ("tightening role rendering", "rolescomponent" in combined and "currentrole" in combined),
        ("expanding styling", any(token in combined for token in (".tab-content", ".nav-tabs", "background expansion", "padding:"))),
        ("adding imports", "using " in added or "import " in added),
        ("adding follow-up notes", "todo" in added or "fixme" in added),
    ]


def _chat_completions_structured_summary(
    repo_label: str,
    diff_text: str,
    repo_path: str | None,
    *,
    url: str,
    model: str,
    api_key: str,
    timeout: int,
    missing_error: str,
) -> StructuredSummary:
    if not url or not model:
        raise RuntimeError(missing_error)

    changes = _summary_relevant_changes(_coalesce_changes(summarize_file_changes(diff_text)))
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _build_ai_user_prompt(repo_label, diff_text, repo_path),
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
        with request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except error.URLError as exc:
        raise RuntimeError(str(exc)) from exc

    content = data["choices"][0]["message"]["content"].strip()
    return _structured_summary_from_json(_parse_ai_json_payload(content), changes, repo_label)


def _openai_compatible_structured_summary(repo_label: str, diff_text: str, repo_path: str | None = None) -> StructuredSummary:
    return _chat_completions_structured_summary(
        repo_label,
        diff_text,
        repo_path,
        url=os.getenv("PYESIS_AI_URL", "").strip(),
        model=os.getenv("PYESIS_AI_MODEL", "").strip(),
        api_key=os.getenv("PYESIS_AI_API_KEY", "").strip(),
        timeout=30,
        missing_error="Missing AI endpoint configuration",
    )


def _github_gpt_structured_summary(repo_label: str, diff_text: str, repo_path: str | None = None) -> StructuredSummary:
    return _chat_completions_structured_summary(
        repo_label,
        diff_text,
        repo_path,
        url=os.getenv("PYESIS_GITHUB_GPT_URL", os.getenv("PYESIS_GITHUB_COPILOT_URL", "")).strip(),
        model=os.getenv("PYESIS_GITHUB_GPT_MODEL", os.getenv("PYESIS_GITHUB_COPILOT_MODEL", "")).strip(),
        api_key=os.getenv("PYESIS_GITHUB_GPT_API_KEY", os.getenv("PYESIS_GITHUB_COPILOT_API_KEY", "")).strip(),
        timeout=30,
        missing_error="Missing GitHub GPT configuration",
    )


def _ollama_structured_summary(repo_label: str, diff_text: str, repo_path: str | None = None) -> StructuredSummary:
    url = os.getenv("PYESIS_OLLAMA_URL", "http://localhost:11434/api/chat").strip()
    model = os.getenv("PYESIS_OLLAMA_MODEL", "llama3.1:8b").strip()
    keep_alive = os.getenv("PYESIS_OLLAMA_KEEP_ALIVE", "5m").strip()
    if not url or not model:
        raise RuntimeError("Missing Ollama configuration")

    changes = _summary_relevant_changes(_coalesce_changes(summarize_file_changes(diff_text)))

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    _build_ai_user_prompt(repo_label, diff_text, repo_path)
                ),
            },
        ],
        "stream": False,
    }
    if keep_alive:
        payload["keep_alive"] = keep_alive

    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}

    req = request.Request(url, data=body, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=45) as response:
            data = json.loads(response.read().decode("utf-8"))
    except error.URLError as exc:
        raise RuntimeError(str(exc)) from exc

    message = data.get("message", {})
    content = str(message.get("content", "")).strip()
    if not content:
        raise RuntimeError("Empty Ollama response")
    return _structured_summary_from_json(_parse_ai_json_payload(content), changes, repo_label)