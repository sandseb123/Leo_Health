"""
Leo Core — Whoop CSV Parser
Parses Whoop CSV exports into normalized dicts.
ZERO network imports. Stdlib only.

Whoop exports via: app → Profile → Export Data → Email CSV
You'll receive multiple CSVs. Drop them all in one folder and point Leo at it,
or parse them individually using the functions below.
"""

import csv
import os
from datetime import datetime
from typing import Optional


# ── Helpers ───────────────────────────────────────────────────────────────────

def _iso(date_str: str) -> str:
    """Normalize Whoop date strings to ISO8601."""
    if not date_str:
        return ""
    # Whoop uses: "2024-01-15 08:23:44", "01/15/2024", "2024-01-15"
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%m/%d/%Y",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(date_str.strip(), fmt).isoformat()
        except ValueError:
            continue
    return date_str.strip()


def _float(val: str) -> Optional[float]:
    """Safely parse float, return None if empty or invalid."""
    try:
        return float(val.strip()) if val and val.strip() else None
    except ValueError:
        return None


def _coalesce_float(*vals: str) -> Optional[float]:
    """Return the first parsable numeric value (preserves valid 0.0 values)."""
    for v in vals:
        parsed = _float(v)
        if parsed is not None:
            return parsed
    return None


def _hours_from_hours_or_minutes(hours_val: str, minutes_val: str) -> Optional[float]:
    """
    Parse duration fields where Whoop may provide either hours or minutes.
    Returns hours in both cases.
    """
    hours = _float(hours_val)
    if hours is not None:
        return hours
    minutes = _float(minutes_val)
    if minutes is None:
        return None
    return round(minutes / 60.0, 3)


def _normalize_header(header: str) -> str:
    """Lowercase, strip, replace spaces/special chars for consistent matching."""
    return header.lower().strip().replace(" ", "_").replace("(", "").replace(")", "").replace("%", "pct").replace("/", "_per_")


# ── CSV Detectors ─────────────────────────────────────────────────────────────

def _detect_csv_type(headers: list[str]) -> str:
    """
    Whoop exports several different CSV files. Auto-detect which one this is
    based on column names.
    """
    joined = " ".join(headers).lower()
    if "recovery score %" in joined or "recovery_score" in joined:
        return "recovery"
    if "strain" in joined and "calories" in joined:
        return "strain"
    if "sleep performance %" in joined or "sleep_performance" in joined:
        return "sleep"
    if "hrv" in joined and "rhr" in joined:
        return "recovery"  # HRV is in recovery CSV
    return "unknown"


# ── Row Parsers ───────────────────────────────────────────────────────────────

def _parse_recovery_row(row: dict) -> Optional[dict]:
    """Parse one row from Whoop recovery CSV."""
    norm = {_normalize_header(k): v for k, v in row.items()}

    # Try multiple possible column name variants Whoop has used across versions
    date = (norm.get("cycle_start_time") or norm.get("date") or
            norm.get("start_time") or "")
    recovery = _coalesce_float(
        norm.get("recovery_score_pct", ""),
        norm.get("recovery_score", ""),
        norm.get("recovery", ""),
    )
    hrv = _coalesce_float(
        norm.get("heart_rate_variability_ms", ""),
        norm.get("hrv_ms", ""),
        norm.get("hrv", ""),
    )
    rhr = _coalesce_float(
        norm.get("resting_heart_rate_bpm", ""),
        norm.get("rhr_bpm", ""),
        norm.get("rhr", ""),
    )
    spo2 = _coalesce_float(
        norm.get("spo2_pct", ""),
        norm.get("blood_oxygen_pct", ""),
        norm.get("spo2", ""),
    )

    if not date:
        return None

    return {
        "source": "whoop",
        "recorded_at": _iso(date),
        "recovery_score": recovery,
        "hrv_ms": hrv,
        "resting_heart_rate": rhr,
        "spo2_pct": spo2,
        "skin_temp_celsius": _float(norm.get("skin_temp_celsius", "") or norm.get("skin_temp", "")),
    }


def _parse_strain_row(row: dict) -> Optional[dict]:
    """Parse one row from Whoop strain/activity CSV."""
    norm = {_normalize_header(k): v for k, v in row.items()}

    date = (norm.get("cycle_start_time") or norm.get("date") or
            norm.get("start_time") or "")
    strain = _coalesce_float(
        norm.get("day_strain", ""),
        norm.get("strain", ""),
    )

    if not date:
        return None

    return {
        "source": "whoop",
        "recorded_at": _iso(date),
        "day_strain": strain,
        "calories": _float(norm.get("calories", "") or norm.get("active_calories", "")),
        "max_heart_rate": _float(norm.get("max_heart_rate_bpm", "") or norm.get("max_hr", "")),
        "avg_heart_rate": _float(norm.get("average_heart_rate_bpm", "") or norm.get("avg_hr", "")),
    }


def _parse_sleep_row(row: dict) -> Optional[dict]:
    """Parse one row from Whoop sleep CSV."""
    norm = {_normalize_header(k): v for k, v in row.items()}

    date = (norm.get("cycle_start_time") or norm.get("sleep_onset") or
            norm.get("date") or "")

    if not date:
        return None

    sleep_perf = _coalesce_float(
        norm.get("sleep_performance_pct", ""),
        norm.get("sleep_performance", ""),
    )
    if sleep_perf is not None and sleep_perf <= 1.0:
        sleep_perf = round(sleep_perf * 100.0, 1)

    return {
        "source": "whoop",
        "stage": "asleep",
        "recorded_at": _iso(date),
        "sleep_performance_pct": sleep_perf,
        "time_in_bed_hours": _hours_from_hours_or_minutes(
            norm.get("time_in_bed_hours", ""),
            norm.get("total_in_bed_min_min", "") or
            norm.get("total_in_bed_min", "") or
            norm.get("total_in_bed_minutes", ""),
        ),
        "light_sleep_hours": _hours_from_hours_or_minutes(
            norm.get("light_sleep_duration_hours", ""),
            norm.get("light_sleep_min", ""),
        ),
        "rem_sleep_hours": _hours_from_hours_or_minutes(
            norm.get("rem_sleep_duration_hours", ""),
            norm.get("rem_sleep_min", ""),
        ),
        "deep_sleep_hours": _hours_from_hours_or_minutes(
            norm.get("slow_wave_sleep_duration_hours", ""),
            norm.get("sws_min", ""),
        ),
        "awake_hours": _hours_from_hours_or_minutes(
            norm.get("awake_duration_hours", ""),
            norm.get("awake_min", ""),
        ),
        "disturbances": _float(norm.get("disturbances", "")),
    }


# ── CSV File Parser ───────────────────────────────────────────────────────────

def _parse_csv_file(filepath: str) -> tuple[str, list[dict]]:
    """Parse a single Whoop CSV file, auto-detecting its type."""
    results = []

    with open(filepath, newline="", encoding="utf-8-sig") as f:  # utf-8-sig strips BOM
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        csv_type = _detect_csv_type(headers)

        if csv_type == "unknown":
            return ("unknown", [])

        parser = {
            "recovery": _parse_recovery_row,
            "strain": _parse_strain_row,
            "sleep": _parse_sleep_row,
        }[csv_type]

        for row in reader:
            parsed = parser(row)
            if parsed:
                results.append(parsed)

    return (csv_type, results)


# ── Public API ────────────────────────────────────────────────────────────────

def parse(csv_path: str) -> dict:
    """
    Parse a single Whoop CSV export file.

    Args:
        csv_path: Path to a Whoop CSV file (recovery, strain, or sleep)

    Returns:
        Dict with keys: recovery, strain, sleep
        Only the detected type will have data; others will be empty lists.

    Example:
        >>> data = parse("~/Downloads/whoop_recovery.csv")
        >>> print(f"Parsed {len(data['recovery'])} recovery records")
    """
    csv_type, records = _parse_csv_file(csv_path)

    result = {"recovery": [], "strain": [], "sleep": [], "hrv": []}

    if csv_type == "recovery":
        result["recovery"] = records
        # Extract HRV from recovery records into dedicated hrv list
        result["hrv"] = [
            {
                "source": "whoop",
                "metric": "hrv_sdnn",
                "value": r["hrv_ms"],
                "unit": "ms",
                "recorded_at": r["recorded_at"],
                "device": "whoop",
            }
            for r in records if r.get("hrv_ms") is not None
        ]
    elif csv_type == "strain":
        result["strain"] = records
    elif csv_type == "sleep":
        result["sleep"] = records

    return result


def parse_folder(folder_path: str) -> dict:
    """
    Parse all Whoop CSV files in a folder.
    Whoop emails you multiple CSVs — drop them all in one folder.

    Args:
        folder_path: Path to folder containing Whoop CSV exports

    Returns:
        Dict with keys: recovery, strain, sleep, hrv
        All records from all matching files, merged.

    Example:
        >>> data = parse_folder("~/Downloads/whoop_exports/")
        >>> print(f"Parsed {len(data['recovery'])} recovery days")
    """
    result = {"recovery": [], "strain": [], "sleep": [], "hrv": []}

    csv_files = sorted([
        os.path.join(folder_path, f)
        for f in os.listdir(folder_path)
        if f.lower().endswith(".csv")
    ])

    if not csv_files:
        raise FileNotFoundError(f"No CSV files found in {folder_path}")

    for filepath in csv_files:
        try:
            parsed = parse(filepath)
            for key in result:
                result[key].extend(parsed.get(key, []))
        except Exception:
            # Skip files that can't be parsed (e.g. non-Whoop CSVs in folder)
            continue

    return result
