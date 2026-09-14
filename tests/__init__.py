"""Keep unittest runs off the live ~/PyesisState database.

`unittest discover -s tests` imports modules as top-level `test_*` names and
skips this file. Use `python -m unittest discover -t . -s tests` so this
package init runs first, or set PYESIS_STATE_DIR before importing pyesis.
"""

from __future__ import annotations

import os
from pathlib import Path

_STATE_ROOT = Path(__file__).resolve().parent / ".tmp-state"
_STATE_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["PYESIS_STATE_DIR"] = str(_STATE_ROOT)

import pyesis.config as config

_DB_PATH = _STATE_ROOT / "pyesis.db"
config.STATE_DIRECTORY = _STATE_ROOT
config.DB_PATH = _DB_PATH
config.STATE_PATH = _DB_PATH
config.LEGACY_JSON_PATH = _STATE_ROOT / "pyesis_state.json"
config.BUFFER_DIR = _STATE_ROOT / "diff_buffers"
config.ARCHIVE_DIR = _STATE_ROOT / "week_archives"
config.AI_ATTEMPT_LOG_PATH = _STATE_ROOT / "logs" / "ai_attempts.jsonl"
config.ensure_state_storage(_STATE_ROOT)

import pyesis.app as app
import pyesis.diff_buffer as diff_buffer
import pyesis.summary_enhancer as summary_enhancer

app.STATE_DIRECTORY = _STATE_ROOT
app.STATE_PATH = _DB_PATH
diff_buffer.STATE_PATH = _DB_PATH
diff_buffer.BUFFER_DIR = config.BUFFER_DIR
summary_enhancer.STATE_PATH = _DB_PATH
