# Changelog

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
