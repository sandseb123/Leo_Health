"""
Leo Core — Oura Ring CSV Parser
Parses Oura CSV exports into normalized dicts.
ZERO network imports. Stdlib only.

Oura exports via:
  oura.com → Account → Data Export → Download (or Oura app → Profile → Export)
You'll receive CSV files for sleep, readiness, and activity.

Supported data:
  - Sleep sessions + stage breakdown (sleep.csv)
  - Readiness score + HRV + RHR (readiness.csv)
  - Activity summary (activity.csv) — steps, calories, activity score

Note: Oura duration fields are in seconds. Leo normalizes them to hours.
"""

import csv
import os
from datetime import datetime
from typing import Optional


# ── Helpers ───────────────────────────────────────────────────────────────────

def _iso(date_str: str) -> str:
    """Normalize Oura date strings to ISO8601."""
    if not date_str:
        return ""
    # Oura uses: "2024-01-15", "2024-01-15T23:30:00+00:00", "2024-01-15 23:30:00"
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            return dt.replace(tzinfo=None).isoformat()
        except ValueError:
            continue
    return date_str.strip()


def _float(val: str) -> Optional[float]:
    """Safely parse float, return None if empty or invalid."""
    try:
        return float(val.strip()) if val and val.strip() else None
    except ValueError:
        return None


def _seconds_to_hours(val: str) -> Optional[float]:
    """Convert a seconds string to hours, rounded to 3dp."""
    f = _float(val)
    return round(f / 3600, 3) if f is not None else None


def _normalize_header(header: str) -> str:
    """Lowercase, strip, replace spaces/special chars for consistent matching."""
    return (
        header.lower().strip()
        .replace(" ", "_")
        .replace("(", "").replace(")", "")
        .replace("%", "pct")
        .replace("/", "_per_")
    )


# ── CSV type detection ────────────────────────────────────────────────────────

def _detect_csv_type(headers: list[str]) -> str:
    """
    Oura exports multiple CSV files. Auto-detect which one this is
    based on column names.
    """
    joined = " ".join(headers).lower()
    if "readiness" in joined or "recovery_index" in joined:
        return "readiness"
    if "bedtime" in joined or "deep_sleep" in joined or "sleep_score" in joined:
        return "sleep"
    if "steps" in joined or "activity_score" in joined or "active_calories" in joined:
        return "activity"
    return "unknown"


# ── Row parsers ───────────────────────────────────────────────────────────────

def _parse_readiness_row(row: dict) -> Optional[dict]:
    """
    Parse one row from Oura readiness CSV.

    Readiness CSV contains: date, readiness_score, resting_heart_rate,
    hrv_balance, recovery_index, temperature_deviation, activity_balance,
    sleep_balance, previous_night, etc.
    """
    norm = {_normalize_header(k): v for k, v in row.items()}

    date = norm.get("date") or norm.get("day") or norm.get("summary_date") or ""
    if not date:
        return None

    # Readiness score — Oura uses 0-100
    score = (
        _float(norm.get("readiness_score", "")) or
        _float(norm.get("score", "")) or
        _float(norm.get("readiness", ""))
    )
    rhr = (
        _float(norm.get("resting_heart_rate", "")) or
        _float(norm.get("rhr", "")) or
        _float(norm.get("heart_rate", ""))
    )
    hrv = (
        _float(norm.get("hrv_balance", "")) or
        _float(norm.get("hrv", "")) or
        _float(norm.get("average_hrv", ""))
    )
    temp = (
        _float(norm.get("temperature_deviation", "")) or
        _float(norm.get("temperature", "")) or
        _float(norm.get("skin_temp_deviation", ""))
    )

    return {
        "source": "oura",
        "recorded_at": _iso(date),
        "readiness_score": score,
        "hrv_balance": hrv,
        "resting_heart_rate": rhr,
        "temperature_deviation": temp,
        "recovery_index": _float(norm.get("recovery_index", "")),
        "activity_balance": _float(norm.get("activity_balance", "")),
        "sleep_balance": _float(norm.get("sleep_balance", "")),
    }


def _parse_sleep_row(row: dict) -> tuple[Optional[dict], Optional[dict], Optional[dict]]:
    """
    Parse one row from Oura sleep CSV.

    Returns a tuple of (sleep_record, heart_rate_record, hrv_record).
    Any can be None if data is missing.
    Oura duration fields are in seconds — converted to hours.
    """
    norm = {_normalize_header(k): v for k, v in row.items()}

    date = norm.get("date") or norm.get("day") or norm.get("summary_date") or ""
    if not date:
        return None, None, None

    recorded_at = _iso(date)
    start = _iso(norm.get("bedtime_start", "") or norm.get("sleep_start", ""))
    end = _iso(norm.get("bedtime_end", "") or norm.get("sleep_end", ""))

    # Efficiency — Oura reports as 0-100 or 0.0-1.0
    efficiency_raw = _float(norm.get("efficiency", "") or norm.get("sleep_efficiency", ""))
    if efficiency_raw is not None and efficiency_raw <= 1.0:
        efficiency_raw = round(efficiency_raw * 100, 1)  # normalize to 0-100

    # Duration fields are in seconds
    time_in_bed = _seconds_to_hours(norm.get("time_in_bed", "") or norm.get("total_bedtime", ""))
    deep = _seconds_to_hours(
        norm.get("deep_sleep_duration", "") or norm.get("deep", "") or norm.get("deep_sleep", "")
    )
    light = _seconds_to_hours(
        norm.get("light_sleep_duration", "") or norm.get("light", "") or norm.get("light_sleep", "")
    )
    rem = _seconds_to_hours(
        norm.get("rem_sleep_duration", "") or norm.get("rem", "") or norm.get("rem_sleep", "")
    )
    awake = _seconds_to_hours(
        norm.get("awake_duration", "") or norm.get("awake_time", "") or norm.get("awake", "")
    )

    sleep_record = {
        "source": "oura",
        "stage": "asleep",
        "start": start,
        "end": end,
        "recorded_at": recorded_at,
        "device": "oura",
        "sleep_performance_pct": efficiency_raw,
        "time_in_bed_hours": time_in_bed,
        "light_sleep_hours": light,
        "rem_sleep_hours": rem,
        "deep_sleep_hours": deep,
        "awake_hours": awake,
        "disturbances": _float(norm.get("restless_periods", "") or norm.get("disturbances", "")),
    }

    # Resting heart rate from sleep CSV (hr_lowest is a proxy)
    rhr_val = _float(norm.get("hr_lowest", "") or norm.get("lowest_heart_rate", ""))
    hr_record = None
    if rhr_val and recorded_at:
        hr_record = {
            "source": "oura",
            "metric": "resting_heart_rate",
            "value": rhr_val,
            "unit": "count/min",
            "recorded_at": recorded_at,
            "device": "oura",
        }

    # HRV from sleep CSV
    hrv_val = _float(norm.get("average_hrv", "") or norm.get("hrv_average", "") or norm.get("hrv", ""))
    hrv_record = None
    if hrv_val and recorded_at:
        hrv_record = {
            "source": "oura",
            "metric": "hrv_rmssd",   # Oura uses RMSSD
            "value": hrv_val,
            "unit": "ms",
            "recorded_at": recorded_at,
            "device": "oura",
        }

    return sleep_record, hr_record, hrv_record


def _parse_activity_row(row: dict) -> Optional[dict]:
    """
    Parse one row from Oura activity CSV.
    Stored in oura_readiness table as it's a daily aggregate.
    Only returns a record if there's an activity_score to show.
    """
    norm = {_normalize_header(k): v for k, v in row.items()}

    date = norm.get("date") or norm.get("day") or norm.get("summary_date") or ""
    if not date:
        return None

    # Activity CSV doesn't have a readiness score — return None to skip
    # The activity data currently isn't stored in a dedicated table.
    # Future: add oura_activity table.
    return None


# ── CSV File Parser ───────────────────────────────────────────────────────────

def _parse_csv_file(filepath: str) -> tuple[str, dict]:
    """Parse a single Oura CSV file, auto-detecting its type."""
    result: dict[str, list] = {"readiness": [], "sleep": [], "heart_rate": [], "hrv": []}

    with open(filepath, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        csv_type = _detect_csv_type(headers)

        if csv_type == "unknown":
            return ("unknown", result)

        for row in reader:
            if csv_type == "readiness":
                parsed = _parse_readiness_row(row)
                if parsed:
                    result["readiness"].append(parsed)
                    # Also extract HRV and RHR into their normalized tables
                    if parsed.get("hrv_balance") and parsed.get("recorded_at"):
                        result["hrv"].append({
                            "source": "oura",
                            "metric": "hrv_rmssd",
                            "value": parsed["hrv_balance"],
                            "unit": "ms",
                            "recorded_at": parsed["recorded_at"],
                            "device": "oura",
                        })
                    if parsed.get("resting_heart_rate") and parsed.get("recorded_at"):
                        result["heart_rate"].append({
                            "source": "oura",
                            "metric": "resting_heart_rate",
                            "value": parsed["resting_heart_rate"],
                            "unit": "count/min",
                            "recorded_at": parsed["recorded_at"],
                            "device": "oura",
                        })

            elif csv_type == "sleep":
                sleep_rec, hr_rec, hrv_rec = _parse_sleep_row(row)
                if sleep_rec:
                    result["sleep"].append(sleep_rec)
                if hr_rec:
                    result["heart_rate"].append(hr_rec)
                if hrv_rec:
                    result["hrv"].append(hrv_rec)

    return (csv_type, result)


# ── Public API ────────────────────────────────────────────────────────────────

def parse(csv_path: str) -> dict:
    """
    Parse a single Oura CSV export file.

    Args:
        csv_path: Path to an Oura CSV file (readiness, sleep, or activity)

    Returns:
        Dict with keys: readiness, sleep, heart_rate, hrv
        Only the detected type will have data; others will be empty lists.

    Example:
        >>> data = parse("~/Downloads/oura_readiness_2024.csv")
        >>> print(f"Parsed {len(data['readiness'])} readiness days")
    """
    _, result = _parse_csv_file(csv_path)
    return result


def parse_folder(folder_path: str) -> dict:
    """
    Parse all Oura CSV files in a folder.
    Oura may export multiple CSVs — drop them all in one folder.

    Args:
        folder_path: Path to folder containing Oura CSV exports

    Returns:
        Dict with keys: readiness, sleep, heart_rate, hrv
        All records from all matching files, merged.

    Example:
        >>> data = parse_folder("~/Downloads/oura_exports/")
        >>> print(f"Parsed {len(data['readiness'])} readiness days")
    """
    result: dict[str, list] = {"readiness": [], "sleep": [], "heart_rate": [], "hrv": []}

    csv_files = [
        os.path.join(folder_path, f)
        for f in os.listdir(folder_path)
        if f.lower().endswith(".csv")
    ]

    if not csv_files:
        raise FileNotFoundError(f"No CSV files found in {folder_path}")

    for filepath in csv_files:
        try:
            parsed = parse(filepath)
            for key in result:
                result[key].extend(parsed.get(key, []))
        except Exception:
            continue  # Skip non-Oura CSVs in the folder

    return result
