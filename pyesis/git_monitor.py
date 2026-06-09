from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import os
from pathlib import Path
import subprocess

from pyesis.config import EntryRecord, RepoConfig


DIFF_START = "diff --git "
PLUS_PATH_PREFIX = "+++ b/"
MINUS_PATH_PREFIX = "--- a/"
RENAME_TO_PREFIX = "rename to "
DEFAULT_EXCLUDES = [
    "pyesis_state.json",
    "diff_buffers/**",
    "exports/**",
    ".venv/**",
    "__pycache__/**",
]
DIFF_CONTEXT_LINES = 20
DIFF_SAMPLE_LIMIT = 12


@dataclass
class DiffSnapshot:
    repo: RepoConfig
    diff_text: str
    diff_hash: str
    created_at: datetime


@dataclass
class FileChangeSummary:
    path: str
    action: str
    added_lines: int
    removed_lines: int
    added_samples: list[str] = field(default_factory=list)
    removed_samples: list[str] = field(default_factory=list)


def _run_git(repo_path: str, *args: str) -> str:
    kwargs = {
        "cwd": repo_path,
        "capture_output": True,
        "text": True,
        "encoding": "utf-8",
        "errors": "replace",
        "check": False,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW

    completed = subprocess.run(
        ["git", *args],
        **kwargs,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or "git command failed")
    return completed.stdout


def validate_repo(path: str) -> tuple[bool, str]:
    repo_path = Path(path)
    if not repo_path.exists():
        return False, "Path does not exist."
    if not repo_path.is_dir():
        return False, "Path is not a directory."
    try:
        _run_git(path, "rev-parse", "--show-toplevel")
    except RuntimeError as exc:
        return False, f"Not a git repository: {exc}"
    return True, "OK"


def capture_snapshot(repo: RepoConfig) -> DiffSnapshot | None:
    diff_text = _run_diff(repo.path, "diff")
    if not diff_text.strip():
        diff_text = _run_diff(repo.path, "diff", "--cached")
    if not diff_text.strip():
        return None

    diff_hash = hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    return DiffSnapshot(
        repo=repo,
        diff_text=diff_text,
        diff_hash=diff_hash,
        created_at=datetime.now(),
    )


def _run_diff(repo_path: str, *prefix_args: str) -> str:
    exclude_args = [f":(exclude){pattern}" for pattern in DEFAULT_EXCLUDES]
    return _run_git(repo_path, *prefix_args, f"-U{DIFF_CONTEXT_LINES}", "--", ".", *exclude_args)


def has_snapshot_changed(snapshot: DiffSnapshot, existing_entries: list[EntryRecord]) -> bool:
    return all(entry.diff_hash != snapshot.diff_hash for entry in existing_entries)


def summarize_changed_files(diff_text: str) -> list[str]:
    files: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith(PLUS_PATH_PREFIX):
            path = line.removeprefix(PLUS_PATH_PREFIX)
            if not _is_excluded_path(path):
                files.append(path)
    return list(dict.fromkeys(files))


def summarize_file_changes(diff_text: str) -> list[FileChangeSummary]:
    summaries: list[FileChangeSummary] = []
    current: FileChangeSummary | None = None

    for line in diff_text.splitlines():
        new_change = _parse_diff_header(line)
        if new_change is not None:
            _finalize_change(summaries, current)
            current = new_change
            continue

        if current is None:
            continue

        if _apply_change_metadata(current, line):
            continue
        if _apply_path_metadata(current, line):
            continue
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            current.added_lines += 1
            _collect_sample(current.added_samples, line[1:])
            continue
        if line.startswith("-"):
            current.removed_lines += 1
            _collect_sample(current.removed_samples, line[1:])

    _finalize_change(summaries, current)
    return summaries


def split_diff_by_file(diff_text: str) -> list[tuple[str, str]]:
    chunks: list[tuple[str, str]] = []
    current_lines: list[str] = []
    current_path = ""

    for line in diff_text.splitlines():
        next_change = _parse_diff_header(line)
        if next_change is not None:
            _finalize_diff_chunk(chunks, current_path, current_lines)
            current_lines = [line]
            current_path = next_change.path
            continue

        if not current_lines:
            continue

        current_lines.append(line)
        current_path = _updated_chunk_path(current_path, line)

    _finalize_diff_chunk(chunks, current_path, current_lines)
    return chunks


def _updated_chunk_path(current_path: str, line: str) -> str:
    if line.startswith(RENAME_TO_PREFIX):
        return line.removeprefix(RENAME_TO_PREFIX).strip()
    if line.startswith(PLUS_PATH_PREFIX):
        path = line.removeprefix(PLUS_PATH_PREFIX).strip()
        if path != "/dev/null":
            return path
    if line.startswith(MINUS_PATH_PREFIX) and current_path == "/dev/null":
        return line.removeprefix(MINUS_PATH_PREFIX).strip()
    return current_path


def _finalize_diff_chunk(chunks: list[tuple[str, str]], path: str, lines: list[str]) -> None:
    if not lines or not path or _is_excluded_path(path):
        return
    chunks.append((path, "\n".join(lines)))


def _parse_diff_header(line: str) -> FileChangeSummary | None:
    if not line.startswith(DIFF_START):
        return None
    parts = line.split()
    if len(parts) < 4:
        return None
    target = parts[3]
    path = target.removeprefix("b/") if target.startswith("b/") else target
    return FileChangeSummary(path=path, action="modified", added_lines=0, removed_lines=0)


def _finalize_change(summaries: list[FileChangeSummary], current: FileChangeSummary | None) -> None:
    if current is not None and not _is_excluded_path(current.path):
        summaries.append(current)


def _apply_change_metadata(current: FileChangeSummary, line: str) -> bool:
    if line.startswith("new file mode "):
        current.action = "created"
        return True
    if line.startswith("deleted file mode "):
        current.action = "deleted"
        return True
    if line.startswith(RENAME_TO_PREFIX):
        current.path = line.removeprefix(RENAME_TO_PREFIX).strip()
        current.action = "renamed"
        return True
    return False


def _apply_path_metadata(current: FileChangeSummary, line: str) -> bool:
    if line.startswith(PLUS_PATH_PREFIX) and current.action != "deleted":
        current.path = line.removeprefix(PLUS_PATH_PREFIX)
        return True
    if line.startswith(MINUS_PATH_PREFIX) and current.action == "deleted":
        current.path = line.removeprefix(MINUS_PATH_PREFIX)
        return True
    return False


def _collect_sample(target: list[str], text: str, limit: int = DIFF_SAMPLE_LIMIT) -> None:
    if len(target) >= limit:
        return
    snippet = text.strip()
    if not snippet:
        return
    if len(snippet) > 140:
        snippet = f"{snippet[:137]}..."
    target.append(snippet)


def _is_excluded_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    for pattern in DEFAULT_EXCLUDES:
        if pattern == normalized:
            return True
        prefix = pattern.removesuffix("/**")
        if pattern.endswith("/**") and normalized.startswith(prefix + "/"):
            return True
    return False