from __future__ import annotations

import unittest

from pyesis.git_monitor import _is_excluded_path, is_noise_work_text, split_diff_by_file


class GitMonitorExcludeTests(unittest.TestCase):
    def test_excludes_nested_sqlite_copy_paths(self) -> None:
        self.assertTrue(_is_excluded_path("cms-sqlLite-cats-source/Views/Home/Index.cshtml"))
        self.assertTrue(_is_excluded_path("vendor/cms-sqlLite-cats-source/CATS.csproj"))
        self.assertFalse(_is_excluded_path("generated/catsUpDate.json"))

    def test_noise_text_detects_sqlite_copy_diffs(self) -> None:
        self.assertTrue(is_noise_work_text("I created cms-sqlLite-cats-source/CATS.csproj."))
        self.assertFalse(is_noise_work_text("I added SourceFolderLastWriteUtcTicks in generated/catsUpDate.JSON."))

    def test_split_diff_by_file_drops_sqlite_copy_chunks(self) -> None:
        diff_text = "\n".join(
            [
                "diff --git a/generated/catsUpDate.json b/generated/catsUpDate.json",
                "--- a/generated/catsUpDate.json",
                "+++ b/generated/catsUpDate.json",
                "@@ -1,1 +1,1 @@",
                '-{"a":1}',
                '+{"a":2}',
                "diff --git a/cms-sqlLite-cats-source/CATS.csproj b/cms-sqlLite-cats-source/CATS.csproj",
                "new file mode 100644",
                "--- /dev/null",
                "+++ b/cms-sqlLite-cats-source/CATS.csproj",
                "+<Project />",
            ]
        )
        chunks = split_diff_by_file(diff_text)
        self.assertEqual([path for path, _text in chunks], ["generated/catsUpDate.json"])


if __name__ == "__main__":
    unittest.main()
