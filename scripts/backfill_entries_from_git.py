"""One-time backfill of Pyesis entries from git commit history.

Pyesis normally captures uncommitted work, so a day spent committing and pushing
leaves no trace. This script reads commit history instead and writes one entry
per commit. It is deliberately separate from the app: nothing here runs during
polling.

Defaults to a dry run. Pass --apply to write.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import hashlib
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pyesis.config import STATE_PATH, deleted_entry_key_for_values
from pyesis.git_monitor import github_repo_name, is_noise_work_text

DIFF_EXCERPT_LIMIT = 12_000
DIFF_CONTEXT_LINES = 20
EVIDENCE_FILE_LIMIT = 8
WEEK_DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
BACKFILL_SUMMARY_SOURCE = "manual"


def run_git(repo_path: str, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or f"git {' '.join(args)} failed")
    return completed.stdout


def week_start_for(moment: datetime, week_end_day: str) -> datetime:
    end_index = WEEK_DAYS.index(week_end_day) if week_end_day in WEEK_DAYS else 3
    week_end = (moment + timedelta(days=(end_index - moment.weekday()) % 7)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return week_end - timedelta(days=6)


def sanitize_diff(patch: str) -> str:
    """Drop lines carrying Pyesis noise markers.

    Pyesis purges any entry whose text mentions the nested sqlLite copy or the
    legacy state files, so a patch that merely references those paths would be
    silently deleted after being written.
    """
    kept = [line for line in patch.splitlines() if not is_noise_work_text(line)]
    return "\n".join(kept)[:DIFF_EXCERPT_LIMIT]


def build_summary(subject: str, body: str, files: list[str]) -> str:
    parts = [subject.strip().rstrip(".") + "."]
    body_text = " ".join(line.strip() for line in body.splitlines() if line.strip())
    if body_text and not is_noise_work_text(body_text):
        parts.append(body_text)
    shown = [path for path in files if not is_noise_work_text(path)][:EVIDENCE_FILE_LIMIT]
    if shown:
        extra = len(files) - len(shown)
        listing = ", ".join(shown) + (f", and {extra} more" if extra > 0 else "")
        parts.append(f"Evidence: {listing}.")
    return " ".join(parts)


def collect_commits(repo_path: str, author: str, since: str, until: str) -> list[dict[str, str]]:
    sep = "\x1f"
    log = run_git(
        repo_path,
        "log",
        "--no-merges",
        f"--since={since}",
        f"--until={until}",
        f"--author={author}",
        f"--format=%H{sep}%aI{sep}%an{sep}%s{sep}%b\x1e",
    )
    commits: list[dict[str, str]] = []
    for record in log.split("\x1e"):
        record = record.strip("\n")
        if not record.strip():
            continue
        sha, iso, name, subject, body = (record.split(sep) + ["", "", "", "", ""])[:5]
        commits.append({"sha": sha, "iso": iso, "author": name, "subject": subject, "body": body})
    commits.reverse()
    return commits


def entry_for_commit(repo_path: str, repo_label: str, commit: dict[str, str], week_end_day: str) -> dict[str, object]:
    patch = run_git(repo_path, "show", commit["sha"], "--format=", f"-U{DIFF_CONTEXT_LINES}")
    files = [line.strip() for line in run_git(repo_path, "show", commit["sha"], "--name-only", "--format=").splitlines() if line.strip()]
    created_at = datetime.fromisoformat(commit["iso"]).replace(tzinfo=None)
    diff_hash = hashlib.sha256(f"git-commit:{commit['sha']}".encode("utf-8")).hexdigest()
    return {
        "repo_label": repo_label,
        "repo_path": repo_path,
        "created_at": created_at.isoformat(timespec="seconds"),
        "day_name": created_at.strftime("%A"),
        "week_start_iso": week_start_for(created_at, week_end_day).isoformat(),
        "summary": build_summary(commit["subject"], commit["body"], files),
        "diff_hash": diff_hash,
        "diff_excerpt": sanitize_diff(patch),
        "summary_source": BACKFILL_SUMMARY_SOURCE,
        "author": commit["author"] or "Backup",
        "rewritten_by": "",
        "rewritten_at": "",
        "requested_summary_source": "",
        "summary_warning": "",
        "fallback_summary_source": "",
        "summary_timing_ms": 0,
        "summary_provider_details": "",
        "last_ai_attempt_at": "",
        "_sha": commit["sha"],
    }


def resolve_author(repo_path: str, override: str) -> str:
    if override:
        return override
    try:
        return run_git(repo_path, "config", "--get", "user.email").strip()
    except RuntimeError:
        return ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", action="append", required=True, help="Repository path to scan; repeatable.")
    parser.add_argument("--since", required=True, help="Start of window, for example 2026-09-09T00:00:00.")
    parser.add_argument("--until", required=True, help="End of window, exclusive.")
    parser.add_argument("--author", default="", help="Author email filter. Defaults to each repo's own user.email.")
    parser.add_argument("--db", default=str(STATE_PATH), help="Pyesis SQLite database path.")
    parser.add_argument("--apply", action="store_true", help="Write the entries. Without this flag nothing is written.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_path = Path(args.db).expanduser()
    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return 1

    connection = sqlite3.connect(str(db_path), timeout=30)
    connection.row_factory = sqlite3.Row
    week_end_row = connection.execute("SELECT value FROM settings WHERE key = 'week_end_day'").fetchone()
    week_end_day = (week_end_row["value"].strip('"') if week_end_row else "Thursday") or "Thursday"

    existing = {(str(row["repo_path"]), str(row["diff_hash"])) for row in connection.execute("SELECT repo_path, diff_hash FROM entries")}
    deleted_keys = {str(row["key"]) for row in connection.execute("SELECT key FROM deleted_entries")}

    planned: list[dict[str, object]] = []
    for raw_path in args.repo:
        repo_path = str(Path(raw_path).expanduser().resolve())
        author = resolve_author(repo_path, args.author)
        if not author:
            print(f"! {repo_path}: no author email configured and none supplied; skipping")
            continue
        repo_label = github_repo_name(repo_path)
        commits = collect_commits(repo_path, author, args.since, args.until)
        print(f"\n{repo_label}  ({repo_path})\n  author filter: {author}\n  commits found: {len(commits)}")

        for commit in commits:
            entry = entry_for_commit(repo_path, repo_label, commit, week_end_day)
            marker = (entry["repo_path"], entry["diff_hash"])
            if marker in existing:
                print(f"  = {commit['sha'][:9]} already present, skipping")
                continue
            deleted_key = deleted_entry_key_for_values(
                str(entry["repo_path"]),
                str(entry["diff_hash"]),
                str(entry["week_start_iso"]),
                str(entry["day_name"]),
                str(entry["summary"]),
                str(entry["diff_excerpt"]),
            )
            if deleted_key in deleted_keys:
                print(f"  - {commit['sha'][:9]} was previously deleted, skipping")
                continue
            if is_noise_work_text(str(entry["summary"])) or is_noise_work_text(str(entry["diff_excerpt"])):
                print(f"  ! {commit['sha'][:9]} still trips the noise filter, skipping")
                continue
            planned.append(entry)
            print(f"  + {commit['sha'][:9]} {entry['day_name']} {entry['created_at']}")
            print(f"      {str(entry['summary'])[:150]}")

    print(f"\n{len(planned)} entries to add.")
    if not planned:
        connection.close()
        return 0

    if not args.apply:
        print("Dry run. Re-run with --apply to write.")
        connection.close()
        return 0

    backup = db_path.with_name(f"{db_path.name}.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(db_path, backup)
    print(f"Backup written to {backup}")

    columns = [key for key in planned[0] if not key.startswith("_")]
    placeholders = ", ".join("?" for _ in columns)
    connection.executemany(
        f"INSERT INTO entries({', '.join(columns)}) VALUES ({placeholders})",
        [tuple(entry[column] for column in columns) for entry in planned],
    )
    connection.commit()
    connection.close()
    print(f"Inserted {len(planned)} entries into {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
