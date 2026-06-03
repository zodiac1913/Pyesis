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
DEFAULT_EXCLUDES = [
    "pyesis_state.json",
    "exports/**",
    ".venv/**",
    "__pycache__/**",
]


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
    return _run_git(repo_path, *prefix_args, "--", ".", *exclude_args)


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
    if line.startswith("rename to "):
        current.path = line.removeprefix("rename to ").strip()
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


def _collect_sample(target: list[str], text: str, limit: int = 6) -> None:
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