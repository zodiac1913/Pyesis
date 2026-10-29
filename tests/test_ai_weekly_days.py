from __future__ import annotations

from datetime import date
from pathlib import Path
import tempfile
import unittest

from pyesis.ai_summary import AIWeeklyReportResult
from pyesis.ai_weekly_days import build_week_from_days, day_cache_dir, days_missing, days_to_save, ensure_day_report, split_week_evidence


EVIDENCE = (
    "Week ending: 2026-10-22\nWeek start: 2026-10-16\n\n"
    "Day: Friday\nRepo: Pyesis\n- Summary: Friday work\n\n"
    "Day: Monday\nRepo: Pyesis\n- Summary: Monday work\n\n"
    "Day: Tuesday\nRepo: Pyesis\n- Summary: Tuesday work\n\n"
    "Day: Thursday\nRepo: Pyesis\n- Summary: Thursday work\n"
)
WEEK_START = date(2026, 10, 16)


class AIWeeklyDaysTests(unittest.TestCase):
    def setUp(self) -> None:
        self.output_dir = Path(tempfile.mkdtemp())
        self.built: list[str] = []
        self.exported: list[str] = []

    def build_day(self, chunk: str) -> AIWeeklyReportResult:
        self.built.append(chunk)
        return AIWeeklyReportResult(text=chunk.split("\n", 1)[0] + "\nProse.", provider_details="model-x")

    def export_day(self, text, directory, week_start_iso, provider, file_name) -> Path:
        self.exported.append(file_name)
        target = directory / file_name
        target.write_text(text, encoding="utf-8")
        return target

    def test_split_maps_days_to_dates_in_week(self) -> None:
        days = split_week_evidence(EVIDENCE, WEEK_START)
        self.assertEqual(
            [(day.day_name, day.day_date) for day in days],
            [
                ("Friday", date(2026, 10, 16)),
                ("Monday", date(2026, 10, 19)),
                ("Tuesday", date(2026, 10, 20)),
                ("Thursday", date(2026, 10, 22)),
            ],
        )

    def test_days_to_save_includes_today_and_skips_future(self) -> None:
        days = split_week_evidence(EVIDENCE, WEEK_START)
        saved = days_to_save(days, date(2026, 10, 20))
        self.assertEqual([day.day_name for day in saved], ["Friday", "Monday", "Tuesday"])

    def test_missing_days_are_past_days_without_saved_json(self) -> None:
        days = split_week_evidence(EVIDENCE, WEEK_START)
        ensure_day_report(self.output_dir, WEEK_START, days[1], self.build_day, self.export_day)
        missing = days_missing(days, self.output_dir, WEEK_START, date(2026, 10, 22))
        self.assertEqual([day.day_name for day in missing], ["Friday", "Tuesday"])

    def test_week_folder_is_week_start(self) -> None:
        self.assertEqual(day_cache_dir(self.output_dir, WEEK_START), self.output_dir / "AIWeeklyDays" / "2026-10-16")

    def test_cached_day_is_reused_and_stale_day_regenerates(self) -> None:
        monday = split_week_evidence(EVIDENCE, WEEK_START)[1]
        _text, _provider, generated = ensure_day_report(self.output_dir, WEEK_START, monday, self.build_day, self.export_day)
        self.assertTrue(generated)
        text, provider, generated = ensure_day_report(self.output_dir, WEEK_START, monday, self.build_day, self.export_day)
        self.assertFalse(generated)
        self.assertEqual(text, "Day: Monday\nProse.")
        self.assertEqual(provider, "model-x")
        self.assertEqual(len(self.built), 1)

        changed = split_week_evidence(EVIDENCE.replace("Monday work", "Monday more work"), WEEK_START)[1]
        _text, _provider, generated = ensure_day_report(self.output_dir, WEEK_START, changed, self.build_day, self.export_day)
        self.assertTrue(generated)
        self.assertEqual(len(self.built), 2)

    def test_build_week_reuses_saved_past_days_and_builds_missing_days(self) -> None:
        days = split_week_evidence(EVIDENCE, WEEK_START)
        today = date(2026, 10, 22)
        for day in days[1:3]:
            ensure_day_report(self.output_dir, WEEK_START, day, self.build_day, self.export_day)
        changed = split_week_evidence(EVIDENCE.replace("Monday work", "Monday more work"), WEEK_START)
        self.built.clear()

        result, generated = build_week_from_days(changed, self.output_dir, WEEK_START, self.build_day, self.export_day, today)

        self.assertEqual(generated, 2)
        self.assertEqual([chunk.split("\n", 1)[0] for chunk in self.built], ["Day: Friday", "Day: Thursday"])
        self.assertEqual(
            [line for line in result.text.split("\n") if line.startswith("Day:")],
            ["Day: Friday", "Day: Monday", "Day: Tuesday", "Day: Thursday"],
        )
        self.assertEqual(result.provider_details, "model-x")
        self.assertIn("2026-10-19.odt", self.exported)


if __name__ == "__main__":
    unittest.main()
