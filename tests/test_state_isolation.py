from __future__ import annotations

import os
import unittest
from pathlib import Path

from pyesis.config import STATE_DIRECTORY, STATE_PATH


class StateIsolationTests(unittest.TestCase):
    def test_tests_do_not_use_live_home_state(self) -> None:
        live = Path.home() / "PyesisState"
        self.assertNotEqual(STATE_DIRECTORY.resolve(), live.resolve())
        self.assertTrue(str(STATE_PATH.resolve()).startswith(str(STATE_DIRECTORY.resolve())))
        override = os.environ.get("PYESIS_STATE_DIR", "")
        self.assertTrue(override)
        self.assertEqual(Path(override).resolve(), STATE_DIRECTORY.resolve())


if __name__ == "__main__":
    unittest.main()
