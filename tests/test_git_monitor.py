from __future__ import annotations

from unittest.mock import patch
import unittest

from pyesis.git_monitor import (
    _is_excluded_path,
    github_repo_name,
    is_formatting_only_diff,
    is_noise_work_text,
    is_non_substantive_work_diff,
    parse_remote_repo_name,
    should_skip_work_diff,
    split_diff_by_file,
)


class GitMonitorExcludeTests(unittest.TestCase):
    def test_formatting_only_json_diff_ignores_trailing_comma(self) -> None:
        diff_text = "\n".join(
            [
                "diff --git a/.vscode/settings.json b/.vscode/settings.json",
                "--- a/.vscode/settings.json",
                "+++ b/.vscode/settings.json",
                "@@ -28,1 +28,1 @@",
                '-    "activityBarTop.activeBorder": "#e7e7e7"',
                '+    "activityBarTop.activeBorder": "#e7e7e7",',
            ]
        )

        self.assertTrue(is_formatting_only_diff(".vscode/settings.json", diff_text))

    def test_json_value_change_is_not_formatting_only(self) -> None:
        diff_text = "\n".join(
            [
                "diff --git a/.vscode/settings.json b/.vscode/settings.json",
                "--- a/.vscode/settings.json",
                "+++ b/.vscode/settings.json",
                "@@ -28,1 +28,1 @@",
                '-    "workbench.colorTheme": "Light"',
                '+    "workbench.colorTheme": "Dark",',
            ]
        )

        self.assertFalse(is_formatting_only_diff(".vscode/settings.json", diff_text))

    def test_excludes_nested_sqlite_copy_paths(self) -> None:
        self.assertTrue(_is_excluded_path("cms-sqlLite-cats-source/Views/Home/Index.cshtml"))
        self.assertTrue(_is_excluded_path("vendor/cms-sqlLite-cats-source/CATS.csproj"))
        self.assertFalse(_is_excluded_path("generated/catsUpDate.json"))

    def test_noise_text_detects_sqlite_copy_diffs(self) -> None:
        self.assertTrue(is_noise_work_text("I created cms-sqlLite-cats-source/CATS.csproj."))
        self.assertTrue(is_noise_work_text("wwwroot/tzedek/sync-metadata.json"))
        self.assertFalse(is_noise_work_text("I added SourceFolderLastWriteUtcTicks in generated/catsUpDate.JSON."))

    def test_excludes_sync_metadata_path(self) -> None:
        self.assertTrue(_is_excluded_path("wwwroot/tzedek/sync-metadata.json"))
        self.assertFalse(_is_excluded_path("wwwroot/tzedek/smlCompliance.js"))

    def test_excludes_schema_docs_dump_paths(self) -> None:
        self.assertTrue(_is_excluded_path("src-tauri/resources/AI/schema_docs/tables/PMAP2/PMAP2_Rating.md"))
        self.assertTrue(_is_excluded_path("docs/schema_docs/query_playbook.md"))
        self.assertTrue(is_noise_work_text("I created src-tauri/resources/AI/schema_docs/tables/PMAP2/PMAP2_Rating.md"))
        self.assertFalse(_is_excluded_path("src-tauri/src/ai.rs"))

    def test_timestamp_only_json_is_non_substantive(self) -> None:
        diff_text = "\n".join(
            [
                "diff --git a/wwwroot/tzedek/sync-metadata.json b/wwwroot/tzedek/sync-metadata.json",
                "--- a/wwwroot/tzedek/sync-metadata.json",
                "+++ b/wwwroot/tzedek/sync-metadata.json",
                "@@ -2,1 +2,1 @@",
                '-  "syncedAtUtc": "2026-08-31T16:53:52.716Z",',
                '+  "syncedAtUtc": "2026-09-28T16:14:27.245Z",',
            ]
        )
        self.assertTrue(is_non_substantive_work_diff("wwwroot/tzedek/sync-metadata.json", diff_text))
        self.assertTrue(should_skip_work_diff("wwwroot/tzedek/sync-metadata.json", diff_text))

    def test_version_constant_only_diff_is_non_substantive(self) -> None:
        diff_text = "\n".join(
            [
                "diff --git a/wwwroot/tzedek/smlComplianceRunner.js b/wwwroot/tzedek/smlComplianceRunner.js",
                "--- a/wwwroot/tzedek/smlComplianceRunner.js",
                "+++ b/wwwroot/tzedek/smlComplianceRunner.js",
                "@@ -9,1 +9,1 @@",
                '-const TZEDEK_VERSION = "2026.08.13.01";',
                '+const TZEDEK_VERSION = "2026.09.28.01";',
            ]
        )
        self.assertTrue(is_non_substantive_work_diff("wwwroot/tzedek/smlComplianceRunner.js", diff_text))

    def test_behavior_change_is_substantive(self) -> None:
        diff_text = "\n".join(
            [
                "diff --git a/wwwroot/tzedek/smlCompliance.js b/wwwroot/tzedek/smlCompliance.js",
                "--- a/wwwroot/tzedek/smlCompliance.js",
                "+++ b/wwwroot/tzedek/smlCompliance.js",
                "@@ -4151,1 +4151,1 @@",
                '-fixButton.setAttribute("title", "Developer Fix");',
                '+fixButton.setAttribute("title", `Developer Fix for ${normalizedTitle}`);',
            ]
        )
        self.assertFalse(is_non_substantive_work_diff("wwwroot/tzedek/smlCompliance.js", diff_text))
        self.assertFalse(should_skip_work_diff("wwwroot/tzedek/smlCompliance.js", diff_text))

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


class GitRemoteNameTests(unittest.TestCase):
    def test_parse_github_and_ssh_urls(self) -> None:
        self.assertEqual(
            parse_remote_repo_name("https://github.com/CMS-Enterprise/cms-dotnet-cats-source.git"),
            "cms-dotnet-cats-source",
        )
        self.assertEqual(parse_remote_repo_name("git@github.com:cms-enterprise/Pyesis.git"), "Pyesis")
        self.assertEqual(parse_remote_repo_name("ssh://git@github.com/foo/RustyPythia.git"), "RustyPythia")

    def test_github_repo_name_uses_origin_not_folder_or_label(self) -> None:
        listing = "\n".join(
            [
                "origin  https://github.com/CMS-Enterprise/cms-dotnet-cats-source.git (fetch)",
                "origin  https://github.com/CMS-Enterprise/cms-dotnet-cats-source.git (push)",
            ]
        )
        with patch("pyesis.git_monitor._run_git", return_value=listing):
            self.assertEqual(github_repo_name("/tmp/local-nickname", fallback="Cats"), "cms-dotnet-cats-source")


if __name__ == "__main__":
    unittest.main()
