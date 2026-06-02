# Pyesis

Desktop tool for monitoring `git diff` activity across multiple repositories and building a weekly first-person work log.

## Features

- Add and remove repositories to monitor.
- Periodically check for changed `git diff` output.
- Generate first-person summaries through a pluggable AI hook.
- Group entries by day with `@DayName` markers and then by repository name (alphabetical).
- Start each Monday with blank spacing and a weekly header.
- Export the current log to `.docx`.
- Optional daily auto-export at a configured time from Settings, saved as `YYYYMMMddPyesis.docx`.
- Accessibility options in Settings: high contrast mode and adjustable UI font size.
- Keyboard shortcuts for common actions (Settings, README, GitHub, add/remove/check/export repo actions).

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

## AI configuration

The app works without an external model. By default it uses a local heuristic summarizer that rewrites git changes in first person.

To use an OpenAI-compatible endpoint instead, set these environment variables before starting the app:

```powershell
$env:PYESIS_AI_MODE = "openai-compatible"
$env:PYESIS_AI_URL = "https://your-endpoint/v1/chat/completions"
$env:PYESIS_AI_MODEL = "your-model-name"
$env:PYESIS_AI_API_KEY = "your-api-key"
```

## Notes

- The app stores configuration in `pyesis_state.json` in the project root.
- Each entry stores a larger diff excerpt to improve summary quality for future rewrites.
- Exported documents are written to the `exports` folder.

## Release Automation

- GitHub Actions release workflow lives at `.github/workflows/release.yml`.
- Versioning format is `YYYY.MM.DD.xx` (example: `2026.06.01.01`).
- Release tags must use `vYYYY.MM.DD.xx` (example: `v2026.06.01.01`).
- Pushing a valid release tag builds `sdist`/wheel artifacts and a Windows `Pyesis-vYYYY.MM.DD.xx.exe`, then publishes a GitHub Release with all artifacts.
- You can also run it manually from Actions using `workflow_dispatch` and provide a valid tag.

### Local Windows EXE Test

Use this to verify the EXE icon locally before a release:

```powershell
python -m pip install --upgrade pip -r requirements.txt pyinstaller
pyinstaller --clean --noconfirm --onefile --icon assets/pyesis.ico --name Pyesis main.py
```

The generated executable is written to `dist/Pyesis.exe`.