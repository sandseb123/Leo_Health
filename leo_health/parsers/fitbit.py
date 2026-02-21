"""
Leo Core — Fitbit Parser
Parses Fitbit data export ZIP into normalized dicts.
ZERO network imports. Stdlib only.

Fitbit exports via:
  fitbit.com → Settings → Data Export → Request Data
You'll receive a ZIP. AirDrop or copy it to ~/Downloads and Leo auto-ingests it.

Supported data:
  - Resting heart rate (activities-heart-*.json)
  - HRV / RMSSD (hrv-*.json)
  - Sleep sessions with stages (sleep-*.json)
  - Exercise / workouts (exercise-*.json)

Note: Fitbit HRV uses RMSSD, stored as metric='hrv_rmssd'.
      Apple Health uses SDNN, stored as metric='hrv_sdnn'.
      Both share the hrv table but are distinct metrics.
"""

import zipfile
import json
import re
from datetime import datetime
from typing import Optional


# ── Helpers ───────────────────────────────────────────────────────────────────

def _iso(date_str: str) -> str:
    """Normalize Fitbit date strings to ISO8601."""
    if not date_str:
        return ""
    # Fitbit uses: "2024-01-15T07:11:00.000", "2024-01-15T23:30:30.000", "2024-01-15"
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(date_str.strip(), fmt).isoformat()
        except ValueError:
            continue
    return date_str.strip()


# ── File classification ────────────────────────────────────────────────────────

def _classify_file(name: str) -> str:
    """
    Classify a JSON file inside the Fitbit ZIP by its purpose.
    Fitbit names files like: activities-heart-2024-01-15.json,
    sleep-2024-01-15.json, hrv-2024-01-15.json, exercise-2024-01-15.json
    """
    basename = name.lower().split("/")[-1]
    if not basename.endswith(".json"):
        return "unknown"

    if re.search(r"activities-heart\b", basename) and "intraday" not in basename:
        return "heart"
    if re.match(r"sleep-\d{4}", basename) or re.match(r"sleep_\d{4}", basename):
        return "sleep"
    if re.match(r"hrv[-_]\d{4}", basename):
        return "hrv"
    if re.match(r"exercise[-_]\d{4}", basename):
        return "exercise"
    return "unknown"


# ── Row parsers ───────────────────────────────────────────────────────────────

def _parse_heart_file(data: list) -> list[dict]:
    """
    Parse activities-heart-YYYY-MM-DD.json for resting heart rate.

    Format: [{"dateTime": "YYYY-MM-DD", "value": {"restingHeartRate": 62, ...}}]
    """
    records = []
    for entry in data:
        date = entry.get("dateTime", "")
        value_obj = entry.get("value", {})
        if not isinstance(value_obj, dict):
            continue
        rhr = value_obj.get("restingHeartRate")
        if rhr and date:
            records.append({
                "source": "fitbit",
                "metric": "resting_heart_rate",
                "value": float(rhr),
                "unit": "count/min",
                "recorded_at": _iso(date),
                "device": "fitbit",
            })
    return records


def _parse_hrv_file(data: list) -> list[dict]:
    """
    Parse hrv-YYYY-MM-DD.json for daily HRV (RMSSD).

    Format: [{"hrv": [{"value": {"dailyRmssd": 42.8}, "dateTime": "YYYY-MM-DD"}]}]
    Note: Stored as metric='hrv_rmssd' to distinguish from Apple's 'hrv_sdnn'.
    """
    records = []
    for entry in data:
        hrv_list = entry.get("hrv", [])
        if not isinstance(hrv_list, list):
            continue
        for hrv_entry in hrv_list:
            date = hrv_entry.get("dateTime", "")
            value_obj = hrv_entry.get("value", {})
            if not isinstance(value_obj, dict):
                continue
            rmssd = value_obj.get("dailyRmssd")
            if rmssd and date:
                records.append({
                    "source": "fitbit",
                    "metric": "hrv_rmssd",
                    "value": round(float(rmssd), 2),
                    "unit": "ms",
                    "recorded_at": _iso(date),
                    "device": "fitbit",
                })
    return records


def _parse_sleep_file(data: list) -> list[dict]:
    """
    Parse sleep-YYYY-MM-DD.json for sleep sessions.

    Extracts aggregate stage times (light, deep, rem, wake) from the summary.
    Maps Fitbit sleep efficiency % → sleep_performance_pct.
    """
    records = []
    for session in data:
        date = session.get("dateOfSleep", "")
        if not date:
            continue

        start = _iso(session.get("startTime", ""))
        end = _iso(session.get("endTime", ""))
        time_in_bed = session.get("timeInBed")     # minutes
        efficiency = session.get("efficiency")       # 0-100 %
        minutes_asleep = session.get("minutesAsleep")
        minutes_awake = session.get("minutesAwake")

        # Stage breakdown from levels.summary (only present in "stages" type sleep)
        levels = session.get("levels", {})
        summary = levels.get("summary", {}) if isinstance(levels, dict) else {}

        def _mins(key: str) -> Optional[float]:
            stage = summary.get(key, {})
            m = stage.get("minutes") if isinstance(stage, dict) else None
            return round(float(m) / 60, 3) if m is not None else None

        # Fitbit stage keys: deep, light, rem, wake
        records.append({
            "source": "fitbit",
            "stage": "asleep",
            "start": start,
            "end": end,
            "recorded_at": _iso(date),
            "device": "fitbit",
            "sleep_performance_pct": float(efficiency) if efficiency is not None else None,
            "time_in_bed_hours": round(float(time_in_bed) / 60, 3) if time_in_bed else None,
            "light_sleep_hours": _mins("light"),
            "rem_sleep_hours": _mins("rem"),
            "deep_sleep_hours": _mins("deep"),
            "awake_hours": _mins("wake") or (
                round(float(minutes_awake) / 60, 3) if minutes_awake else None
            ),
            "disturbances": None,   # not in Fitbit export
        })
    return records


# ── Workout normalization ─────────────────────────────────────────────────────

_WORKOUT_MAP = {
    "run": "running",
    "walk": "walking",
    "hike": "walking",
    "bike": "cycling",
    "cycling": "cycling",
    "swim": "swimming",
    "yoga": "yoga",
    "pilates": "yoga",
    "weight": "strength_training",
    "strength": "strength_training",
    "circuit": "hiit",
    "interval": "hiit",
    "hiit": "hiit",
    "sport": "hiit",
}


def _normalize_activity(name: str) -> str:
    """Map a Fitbit activityName to Leo's normalized workout type."""
    lower = name.lower().strip()
    for key, val in _WORKOUT_MAP.items():
        if key in lower:
            return val
    return lower.replace(" ", "_")


def _parse_exercise_file(data: list) -> list[dict]:
    """
    Parse exercise-YYYY-MM-DD.json for workout sessions.

    Duration is in milliseconds; distance unit is in distanceUnit field.
    Converts distance to km regardless of source unit.
    """
    records = []
    for session in data:
        start = session.get("startTime", "")
        if not start:
            continue

        duration_ms = session.get("activeDuration") or session.get("duration")
        distance = session.get("distance")
        distance_unit = (session.get("distanceUnit") or "").lower()
        calories = session.get("calories")
        activity_name = session.get("activityName") or "unknown"

        duration_min = round(float(duration_ms) / 60000, 2) if duration_ms else None

        # Normalize distance to km
        distance_km = None
        if distance:
            d = float(distance)
            if "mile" in distance_unit:
                distance_km = round(d * 1.60934, 3)
            elif "kilometer" in distance_unit or distance_unit == "km":
                distance_km = round(d, 3)
            # Unknown unit: skip rather than store wrong data

        records.append({
            "source": "fitbit",
            "activity": _normalize_activity(activity_name),
            "duration_minutes": duration_min,
            "distance_km": distance_km,
            "calories": round(float(calories), 1) if calories else None,
            "recorded_at": _iso(start),
            "end": _iso(session.get("endTime", "")),
            "device": "fitbit",
        })
    return records


# ── Public API ────────────────────────────────────────────────────────────────

def parse(zip_path: str) -> dict:
    """
    Parse a Fitbit data export ZIP and return normalized data.

    Args:
        zip_path: Path to Fitbit export ZIP (e.g. fitbit_export_20240115.zip)

    Returns:
        Dict with keys: heart_rate, hrv, sleep, workouts
        Each is a list of normalized dicts ready for DB ingest.

    Example:
        >>> data = parse("~/Downloads/fitbit_export_20240115.zip")
        >>> print(f"Parsed {len(data['heart_rate'])} resting HR days")
    """
    result: dict[str, list] = {
        "heart_rate": [],
        "hrv": [],
        "sleep": [],
        "workouts": [],
    }

    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            file_type = _classify_file(name)
            if file_type == "unknown":
                continue

            try:
                with zf.open(name) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, KeyError, Exception):
                continue  # Skip malformed files silently

            if not isinstance(data, list) or not data:
                continue

            if file_type == "heart":
                result["heart_rate"].extend(_parse_heart_file(data))
            elif file_type == "hrv":
                result["hrv"].extend(_parse_hrv_file(data))
            elif file_type == "sleep":
                result["sleep"].extend(_parse_sleep_file(data))
            elif file_type == "exercise":
                result["workouts"].extend(_parse_exercise_file(data))

    return result
