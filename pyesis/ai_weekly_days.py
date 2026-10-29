"""Per-day cache for the AI Weekly report.

Each day's Ollama text is saved as a visible .odt plus a .json sidecar holding the text and a
hash of that day's evidence, in AIWeeklyDays/<week start>/ named <date>.*. Pyesis writes
missing past days at startup and saves every day up to today when it closes. The final
AI Weekly ensures every elapsed day has a report before merging the saved text.
Saved past days are reused; today's report is regenerated when its evidence changes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import threading

from pyesis.ai_summary import AIWeeklyReportResult, _join_weekly_report_parts, _split_weekly_evidence_by_day


DAY_CACHE_DIR_NAME = "AIWeeklyDays"
WEEKLY_REPORTS_DIR_NAME = "AIWeeklyReports"

# Save-on-close and the AI Weekly button can overlap; this keeps them from
# generating the same day twice.
_DAY_LOCK = threading.Lock()


@dataclass(frozen=True)
class DayEvidence:
    day_name: str
    day_date: date
    evidence_text: str
    evidence_hash: str


BuildDay = Callable[[str], AIWeeklyReportResult]
ExportDay = Callable[[str, Path, str, str, str], Path]


def split_week_evidence(evidence_text: str, week_start: date) -> list[DayEvidence]:
    dates_by_name = {(week_start + timedelta(days=offset)).strftime("%A"): week_start + timedelta(days=offset) for offset in range(7)}
    days: list[DayEvidence] = []
    for chunk in _split_weekly_evidence_by_day(evidence_text):
        first_line = chunk.split("\n", 1)[0].strip()
        if not first_line.lower().startswith("day:"):
            continue
        day_name = first_line.split(":", 1)[1].strip()
        day_date = dates_by_name.get(day_name)
        if day_date is None:
            continue
        digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()
        days.append(DayEvidence(day_name, day_date, chunk, digest))
    return days


def days_to_save(days: list[DayEvidence], today: date) -> list[DayEvidence]:
    """Every day of the week so far, today included (saved when the app closes)."""
    return [day for day in days if day.day_date <= today]


def days_missing(days: list[DayEvidence], output_dir: Path, week_start: date, today: date) -> list[DayEvidence]:
    """Past days with missing files; repaired at startup without rewriting saved work."""
    return [
        day
        for day in days
        if day.day_date < today
        and (
            load_cached_day(output_dir, week_start, day, require_current=False) is None
            or not (day_cache_dir(output_dir, week_start) / f"{_day_stem(day)}.odt").is_file()
        )
    ]


def day_cache_dir(output_dir: Path, week_start: date) -> Path:
    return output_dir / DAY_CACHE_DIR_NAME / week_start.isoformat()


def _day_stem(day: DayEvidence) -> str:
    return day.day_date.isoformat()


def load_cached_day(
    output_dir: Path, week_start: date, day: DayEvidence, require_current: bool = True
) -> tuple[str, str] | None:
    """Return (text, provider_details); with require_current only if built from identical evidence."""
    sidecar = day_cache_dir(output_dir, week_start) / f"{_day_stem(day)}.json"
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if require_current and data.get("evidenceHash") != day.evidence_hash:
        return None
    text = str(data.get("text", ""))
    return (text, str(data.get("providerDetails", ""))) if text.strip() else None


def ensure_day_report(
    output_dir: Path,
    week_start: date,
    day: DayEvidence,
    build_day: BuildDay,
    export_day: ExportDay,
    *,
    require_current: bool = True,
) -> tuple[str, str, bool]:
    """Save one day; require_current=False preserves completed past-day text despite later edits."""
    with _DAY_LOCK:
        cached = load_cached_day(output_dir, week_start, day, require_current=require_current)
        cache_dir = day_cache_dir(output_dir, week_start)
        week_start_iso = datetime.combine(week_start, datetime.min.time()).isoformat()
        if cached is not None:
            if not (cache_dir / f"{_day_stem(day)}.odt").is_file():
                export_day(cached[0], cache_dir, week_start_iso, cached[1], f"{_day_stem(day)}.odt")
            return cached[0], cached[1], False

        result = build_day(day.evidence_text)
        cache_dir.mkdir(parents=True, exist_ok=True)
        export_day(result.text, cache_dir, week_start_iso, result.provider_details, f"{_day_stem(day)}.odt")
        sidecar = cache_dir / f"{_day_stem(day)}.json"
        sidecar.write_text(
            json.dumps(
                {
                    "day": day.day_name,
                    "date": day.day_date.isoformat(),
                    "evidenceHash": day.evidence_hash,
                    "providerDetails": result.provider_details,
                    "text": result.text,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        return result.text, result.provider_details, True


def build_week_from_days(
    days: list[DayEvidence],
    output_dir: Path,
    week_start: date,
    build_day: BuildDay,
    export_day: ExportDay,
    today: date,
) -> tuple[AIWeeklyReportResult, int]:
    """Save elapsed days in order before merging; return the report and AI generation count."""
    texts: list[str] = []
    providers: list[str] = []
    generated = 0
    for day in sorted(days_to_save(days, today), key=lambda item: item.day_date):
        try:
            text, provider, was_generated = ensure_day_report(
                output_dir, week_start, day, build_day, export_day, require_current=day.day_date == today
            )
        except Exception as exc:
            raise RuntimeError(f"{day.day_name} ({day.day_date.isoformat()}): {exc}") from exc
        if text.strip():
            texts.append(text)
        if provider and provider not in providers:
            providers.append(provider)
        generated += int(was_generated)
    return AIWeeklyReportResult(text=_join_weekly_report_parts(texts), provider_details=", ".join(providers)), generated
