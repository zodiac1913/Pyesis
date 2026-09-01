# Changelog

## 2026.9.1.0 - 2026-09-01

### Added
- Weekly JSON export and import for moving the current week between machines.
- AI Weekly DOCX export built from current-week evidence.
- Click-to-delete preview markers with confirmation for cleaning up individual entries.

### Changed
- Startup now loads from a lightweight snapshot first, then finishes saved-entry normalization and shown-diff recovery in the background.
- Runtime state and diff buffers now live under `~/PyesisState` with legacy repo-local data migration.
- Weekly rendering, evidence generation, and summary cleanup respect the configured week-end day.
- Ollama requests now support configurable timeout and thread settings, and AI Weekly forces its dedicated report model.

### Fixed
- Current-week recovery restores shown buffer items so heavy prior-day work does not disappear on reopen.
- Orange backlog and weak-summary handling now track only current-week entries that still need attention.
- Preview rendering now avoids partial startup views that could temporarily show an incomplete week.

## 2026.7.30.1 - 2026-07-30

### Fixed
- Weekly preview refresh now preserves the user's scroll position.
- Added bottom-magnet behavior so views at the bottom stay pinned through refreshes until the user scrolls away.

## 2026.6.4.1 - 2026-06-04

### Changed
- Release automation now publishes zipped native binaries for Windows, macOS, and Linux.
- Native build script now emits `.zip` artifacts on Linux instead of `.tar.gz`.

## 2026.6.3.3 - 2026-06-03

### Changed
- Repo editing now resets the form after Add/Update and clears the selection.
- Repo action button now toggles between Add Repo and Update Repo based on selection.

### Fixed
- Git subprocesses on Windows now run hidden so repo refresh no longer flashes consoles.

## 2026.6.3.2 - 2026-06-03

### Changed
- Repo polling now runs in a background worker so git scans do not block UI input.
- Poll cycle now avoids overlapping scans and reports scan-in-progress status.

### Fixed
- Heuristic fallback summaries now use context-aware phrasing to reduce repetitive duplicate wording.

## 2026.6.3.1 - 2026-06-03

### Fixed
- Local native build now invokes PyInstaller via the active Python interpreter for reliable venv builds.

## 2026.6.3.0 - 2026-06-03

### Changed
- Release tag validation now accepts compact and zero-padded date formats.
- Release automation docs now match the supported tag formats.

### Fixed
- Ensured markdown dependency is declared for runtime installs.

## 2026.06.01.01 - 2026-06-01

### Added
- Light, Dark, and System theme modes with persisted preference.
- Settings dialog with scheduled daily DOCX auto-export.
- Header actions in preview pane: GitHub, README, and Settings controls.
- Keyboard shortcuts:
  - `Ctrl+,` opens Settings
  - `F1` opens README
  - `Ctrl+Shift+G` opens GitHub

### Changed
- Weekly log preview and DOCX output now group by day and repository (alphabetical).
- Summary generation is more descriptive and intent-focused.
- Diff capture excludes housekeeping noise (`pyesis_state.json`, `exports`, `.venv`, `__pycache__`).
- Entry migration and deduping reduce repetitive local-summary spam.
- Increased stored diff excerpt size for better summary rewrites.

### Fixed
- Dark mode field readability for ttk controls on Windows.
- Multiple quality and formatting refinements in summary wording.
