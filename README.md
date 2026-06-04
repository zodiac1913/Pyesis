# Pyesis

Desktop tool for monitoring `git diff` activity across multiple repositories and building a weekly first-person work log.

## Platform prerequisites

- Python 3.11 or newer
- Git available on `PATH`
- Linux desktop environments may require the system Tk package, for example `python3-tk` on Debian/Ubuntu

## Features

- Add and remove repositories to monitor.
- Periodically check for changed `git diff` output.
- Generate first-person summaries through a pluggable AI hook.
- Group entries by day with `@DayName` markers and then by repository name (alphabetical).
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

## Notes

- The app stores configuration in `pyesis_state.json` in the project root.
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

The macOS app bundle produced by CI is unsigned. It runs locally, but distribution outside your own machine will require the usual Apple signing and notarization work.