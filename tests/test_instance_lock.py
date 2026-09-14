from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from pyesis.instance_lock import acquire_instance_lock, already_running_message, fatal_error_message


class InstanceLockTests(unittest.TestCase):
    def test_second_acquire_fails_until_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = acquire_instance_lock(root)
            self.assertIsNotNone(first)
            second = acquire_instance_lock(root)
            self.assertIsNone(second)
            assert first is not None
            first.release()
            third = acquire_instance_lock(root)
            self.assertIsNotNone(third)
            assert third is not None
            third.release()

    def test_startup_messages_are_plain_text(self) -> None:
        self.assertIn("already running", already_running_message())
        self.assertIn("RuntimeError", fatal_error_message(RuntimeError("boom")))
