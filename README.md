# Pyesis

Desktop tool for monitoring `git diff` activity across multiple repositories and building a weekly first-person work log.

## Platform prerequisites

- Python 3.11 or newer
- Git available on `PATH`
- Linux desktop environments may require the system Tk package, for example `python3-tk` on Debian/Ubuntu

## Features

- Add and remove repositories to monitor.
- Periodically check for changed `git diff` output, skipping nested `cms-sqlLite-cats-source` copies so they are not treated as weekly work.
- Generate first-person summaries through a pluggable AI hook.
- Choose AI provider and model per machine in Settings (for example, lighter or heavier Ollama models based on local hardware).
- Group entries by day with `@DayName` markers and then by repository name (alphabetical).
- Edit one saved summary entry at a time when you need to correct wording or add detail.
- Start each Monday with blank spacing and a weekly header.
- Export the current log to `.docx`.
- Optional daily auto-export at a configured time from Settings, saved as `YYYYMMMddPyesis.docx`.
- Settings include a configurable DOCX export folder, so generated files do not need to live inside the repo.
- Accessibility options in Settings: high contrast mode and adjustable UI font size.
- Keyboard shortcuts for common actions (Settings, README, GitHub, add/remove/check/export repo actions).

## Quick start

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

On Windows PowerShell, activate the environment with `.venv\Scripts\Activate.ps1` instead.

The same source tree is intended to run on Windows, macOS, and Linux. Use the platform's normal Python and git installation; there is no platform-specific branch.

## AI configuration

The app works without an external model. By default it uses a local heuristic summarizer that rewrites git changes in first person.

To use Ollama instead, set these environment variables before starting the app:

```bash
export PYESIS_AI_MODE="ollama"
export PYESIS_OLLAMA_URL="http://localhost:11434/api/chat"
export PYESIS_OLLAMA_MODEL="qwen3-coder:30b"
export PYESIS_OLLAMA_KEEP_ALIVE="5m"
# Optional: force broader repository context on or off for AI summaries.
# Default behavior: enabled in Ollama mode, disabled otherwise.
export PYESIS_AI_INCLUDE_REPO_CONTEXT="true"
```

On Windows PowerShell:

```powershell
$env:PYESIS_AI_MODE = "ollama"
$env:PYESIS_OLLAMA_URL = "http://localhost:11434/api/chat"
$env:PYESIS_OLLAMA_MODEL = "qwen3-coder:30b"
$env:PYESIS_OLLAMA_KEEP_ALIVE = "5m"
```

To use an OpenAI-compatible endpoint instead, set these environment variables before starting the app:

```bash
export PYESIS_AI_MODE="openai-compatible"
export PYESIS_AI_URL="https://your-endpoint/v1/chat/completions"
export PYESIS_AI_MODEL="your-model-name"
export PYESIS_AI_API_KEY="your-api-key"
```

On Windows PowerShell, use `$env:PYESIS_AI_MODE = "openai-compatible"` style assignments.

## Data storage

Pyesis no longer uses JSON files as the live store. Runtime state is a SQLite database at `~/PyesisState/pyesis.db` on every OS.

That database holds:

- Settings (theme, AI mode, export folder, week-end day, and related options)
- Monitored repositories (path, label, poll interval)
- Work-log entries and deleted-entry keys
- Daily diff buffers that used to live under `diff_buffers/`
- AI attempt audit records that used to live in `logs/ai_attempts.jsonl`

Closed weeks are still archived as files: `~/PyesisState/week_archives/PyesisWeek_YYYY-MM-DD.7z`. Those archives are long-term copies and are not rewritten after they are created. Live entries in the database are kept for 12 months.

On first launch with this storage, leftover `pyesis_state.json`, `diff_buffers/*.json`, and `logs/ai_attempts.jsonl` under `~/PyesisState/` are imported into `pyesis.db` and renamed with a `.migrated` suffix. Nested `cms-sqlLite-cats-source` copies are skipped so they do not inflate the week log.

A `pyesis_state.json` sitting in this git repo is not the live store. The running app reads and writes `~/PyesisState/pyesis.db`.

Schema (`settings.value` is a JSON-encoded scalar per key; `meta` currently stores `schema_version`):

```sql
CREATE TABLE meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE repos (
    position INTEGER PRIMARY KEY,
    path TEXT NOT NULL,
    label TEXT NOT NULL,
    poll_seconds INTEGER NOT NULL,
    repo_name TEXT NOT NULL DEFAULT ''
);

CREATE TABLE entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_label TEXT NOT NULL,
    repo_path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    day_name TEXT NOT NULL,
    week_start_iso TEXT NOT NULL,
    summary TEXT NOT NULL,
    diff_hash TEXT NOT NULL,
    diff_excerpt TEXT NOT NULL,
    summary_source TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT 'Backup',
    rewritten_by TEXT NOT NULL DEFAULT '',
    rewritten_at TEXT NOT NULL DEFAULT '',
    requested_summary_source TEXT NOT NULL DEFAULT '',
    summary_warning TEXT NOT NULL DEFAULT '',
    fallback_summary_source TEXT NOT NULL DEFAULT '',
    summary_timing_ms INTEGER NOT NULL DEFAULT 0,
    summary_provider_details TEXT NOT NULL DEFAULT '',
    last_ai_attempt_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE deleted_entries (
    key TEXT PRIMARY KEY,
    deleted_at TEXT NOT NULL
);

CREATE TABLE diff_buffer (
    day_key TEXT NOT NULL,
    datetime TEXT NOT NULL,
    repo TEXT NOT NULL,
    git_diff_text TEXT NOT NULL,
    git_diff_description TEXT NOT NULL,
    shown INTEGER NOT NULL DEFAULT 0,
    diff_hash TEXT NOT NULL,
    repo_path TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT 'Backup',
    summary_source TEXT NOT NULL DEFAULT '',
    rewritten_by TEXT NOT NULL DEFAULT '',
    rewritten_at TEXT NOT NULL DEFAULT '',
    requested_summary_source TEXT NOT NULL DEFAULT '',
    summary_warning TEXT NOT NULL DEFAULT '',
    fallback_summary_source TEXT NOT NULL DEFAULT '',
    summary_timing_ms INTEGER NOT NULL DEFAULT 0,
    summary_provider_details TEXT NOT NULL DEFAULT '',
    last_ai_attempt_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (day_key, repo, diff_hash)
);

CREATE TABLE ai_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recorded_at TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE INDEX idx_entries_created_at ON entries(created_at);
CREATE INDEX idx_entries_repo_hash ON entries(repo_path, diff_hash);
CREATE INDEX idx_diff_buffer_day ON diff_buffer(day_key);
CREATE INDEX idx_ai_attempts_recorded_at ON ai_attempts(recorded_at);
```

## Notes

- Debug launch (F5) is meant to use `.venv` with `requirements.txt` installed, including `py7zr` for week archives.
- Each entry stores a larger diff excerpt to improve summary quality for future rewrites.
- Exported documents are written to the configured DOCX output folder.
- New installs default DOCX output to a `Pyesis` folder in your home Documents directory when available, and legacy `exports` settings are migrated away from the repo-local folder automatically.
- On macOS, the app follows the current light/dark appearance when Theme is set to `System`.

## Release Automation

- GitHub Actions release workflow lives at `.github/workflows/release.yml`.
- Versioning format can be compact (`YYYY.M.D.x`) or zero-padded (`YYYY.MM.DD.xx`) for example `2026.6.3.0` or `2026.06.03.00`.
- Release tags must use the same format with a `v` prefix (for example: `v2026.6.3.0` or `v2026.06.03.00`).
- Pushing a valid release tag builds `sdist`/wheel artifacts plus native artifacts for Windows, macOS, and Linux, then publishes a GitHub Release with all artifacts.
- You can also run it manually from Actions using `workflow_dispatch` and provide a valid tag.

### Local Native Build Test

Use this on the current OS to verify the native package locally before a release:

```bash
python -m pip install --upgrade pip -r requirements.txt pyinstaller
python scripts/build_native.py --tag v2026.6.3.0
```

Generated artifacts are written to `dist/`:

- Windows: `Pyesis-vYYYY.M.D.x-windows-x64.zip` (contains the `.exe`)
- macOS: `Pyesis-vYYYY.M.D.x-macos-{x64|arm64}.zip` containing `Pyesis.app`
- Linux: `Pyesis-vYYYY.M.D.x-linux-{x64|arm64}.zip`

To launch the macOS build locally after creating it:

```bash
unzip -o dist/Pyesis-vYYYY.M.D.x-macos-arm64.zip -d dist
open dist/Pyesis.app
```

If macOS blocks the unsigned app, use one of these local override paths:

1. In Finder, control-click `Pyesis.app`, choose `Open`, then confirm.
2. Or remove the download quarantine flag from Terminal:

```bash
xattr -dr com.apple.quarantine dist/Pyesis.app
open dist/Pyesis.app
```

The macOS app bundle produced by CI is unsigned. It runs locally, but distribution outside your own machine will require the usual Apple signing and notarization work.

# For Extra Smart write ups build an agent to help:

Build a periodic summary enhancer in Pyesis that:

scans new entries in the Pyesis database (diff buffers and saved work entries)
rewrites weak gitDiffDescription or summary fields into high-quality rationale
updates only description fields, never gitDiffText or diff hash
tags rewritten entries with rewrittenBy and rewrittenAt
skips entries already marked human-authored
supports dry-run mode and safe logging
Ask Copilot to implement it in small steps:
config flags
selection logic
prompt builder
writer with field-level updates
scheduler hook
tests