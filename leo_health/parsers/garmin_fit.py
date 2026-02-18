"""
Leo Core — Garmin FIT Parser
Parses Garmin .fit files into normalized dicts using fitparse.
Normalizes into Leo's existing tables: heart_rate, hrv, workouts.

FIT activity files typically contain:
  - record: timestamped heart rate during activity
  - session: workout summary (sport, duration, distance, calories)
  - hrv: RR intervals for HRV (RMSSD)

Usage:
  Export activities from Garmin Connect as .fit, or copy from device.
  AirDrop to ~/Downloads for auto-ingest, or call parse() / parse_folder().
"""

import math
from datetime import datetime
from pathlib import Path
from typing import Optional

try:
    from fitparse import FitFile, FitParseError
except ImportError:
    FitFile = None  # type: ignore
    FitParseError = Exception  # type: ignore


# ── Helpers ───────────────────────────────────────────────────────────────────

def _iso(dt: Optional[datetime]) -> str:
    """Convert datetime to ISO8601 string."""
    if dt is None:
        return ""
    return dt.isoformat()


# ── Sport mapping (FIT enum / string → Leo normalized activity) ───────────────

_SPORT_MAP = {
    "running": "running",
    "run": "running",
    "cycling": "cycling",
    "bike": "cycling",
    "indoor_cycling": "cycling",
    "swimming": "swimming",
    "indoor_swimming": "swimming",
    "walking": "walking",
    "hiking": "walking",
    "strength_training": "strength_training",
    "weight_training": "strength_training",
    "yoga": "yoga",
    "hiit": "hiit",
    "functional_fitness": "hiit",
    "elliptical": "hiit",
    "rowing": "hiit",
    "golf": "walking",
    "tennis": "hiit",
    "soccer": "hiit",
    "basketball": "hiit",
    "cross_country_skiing": "running",
    "stand_up_paddleboarding": "swimming",
}


def _normalize_sport(sport: Optional[str], subsport: Optional[str] = None) -> str:
    """Map FIT sport/sub_sport to Leo's normalized workout type."""
    s = (sport or "").lower().strip().replace(" ", "_")
    sub = (subsport or "").lower().strip().replace(" ", "_")
    for key, val in _SPORT_MAP.items():
        if key in s or key in sub:
            return val
    if s:
        return s
    return "unknown"


# ── RMSSD from RR intervals ───────────────────────────────────────────────────

def _compute_rmssd_ms(rr_intervals: list[float]) -> Optional[float]:
    """
    Compute RMSSD (ms) from RR intervals in seconds.
    RMSSD = sqrt(mean of squared successive differences).
    """
    if len(rr_intervals) < 2:
        return None
    diffs = []
    for i in range(1, len(rr_intervals)):
        d = (rr_intervals[i] - rr_intervals[i - 1]) * 1000  # s → ms
        diffs.append(d * d)
    return round(math.sqrt(sum(diffs) / len(diffs)), 2)


# ── Extractors ────────────────────────────────────────────────────────────────

def _parse_record_heart_rate(messages, device: str) -> list[dict]:
    """Extract heart rate from record messages."""
    records = []
    for msg in messages:
        ts = msg.get_value("timestamp")
        hr = msg.get_value("heart_rate")
        if ts and hr is not None:
            records.append({
                "source": "garmin",
                "metric": "heart_rate",
                "value": float(hr),
                "unit": "count/min",
                "recorded_at": _iso(ts),
                "device": device,
            })
    return records


def _parse_hrv(messages, recorded_at: str, device: str) -> list[dict]:
    """
    Extract HRV from hrv messages. RR intervals are in seconds.
    Computes RMSSD and stores as hrv_rmssd (like Fitbit).
    """
    all_rr: list[float] = []
    for msg in messages:
        # 'time' can be single value or tuple of RR intervals
        raw = msg.get_value("time")
        if raw is None:
            continue
        if isinstance(raw, (int, float)):
            all_rr.append(float(raw))
        elif isinstance(raw, (list, tuple)):
            for v in raw:
                if v is not None and not (isinstance(v, float) and math.isnan(v)):
                    all_rr.append(float(v))

    rmssd = _compute_rmssd_ms(all_rr)
    if rmssd is None or not recorded_at:
        return []
    return [{
        "source": "garmin",
        "metric": "hrv_rmssd",
        "value": rmssd,
        "unit": "ms",
        "recorded_at": recorded_at,
        "device": device,
    }]


def _parse_sessions(messages, device: str) -> list[dict]:
    """Extract workout summaries from session messages."""
    workouts = []
    for msg in messages:
        start = msg.get_value("start_time")
        if not start:
            start = msg.get_value("timestamp")
        if not start:
            continue

        elapsed = msg.get_value("total_elapsed_time")  # seconds
        duration_min = round(float(elapsed) / 60, 2) if elapsed is not None else None

        dist = msg.get_value("total_distance")  # meters in FIT
        distance_km = round(float(dist) / 1000, 3) if dist is not None else None

        calories = msg.get_value("total_calories")
        sport = msg.get_value("sport")
        subsport = msg.get_value("sub_sport")

        end = None
        if start and elapsed:
            try:
                from datetime import timedelta
                end_dt = start + timedelta(seconds=float(elapsed))
                end = end_dt.isoformat()
            except (TypeError, ValueError):
                pass

        workouts.append({
            "source": "garmin",
            "activity": _normalize_sport(
                str(sport) if sport is not None else None,
                str(subsport) if subsport is not None else None,
            ),
            "duration_minutes": duration_min,
            "distance_km": distance_km,
            "calories": round(float(calories), 1) if calories is not None else None,
            "recorded_at": _iso(start),
            "end": end,
            "device": device,
        })
    return workouts


def _get_device_name(fitfile) -> str:
    """Try to get device name from file_info or device_info messages."""
    try:
        for msg in fitfile.get_messages("file_id"):
            prod = msg.get_value("product_name") or msg.get_value("manufacturer")
            if prod:
                return str(prod)
        for msg in fitfile.get_messages("device_info"):
            name = msg.get_value("product_name")
            if name:
                return str(name)
    except Exception:
        pass
    return "garmin"


# ── Public API ────────────────────────────────────────────────────────────────

def parse(fit_path: str) -> dict:
    """
    Parse a single Garmin .fit file and return normalized data.

    Args:
        fit_path: Path to .fit file

    Returns:
        Dict with keys: heart_rate, hrv, workouts
        Each is a list of normalized dicts ready for DB ingest.

    Raises:
        ImportError: if fitparse is not installed
        FitParseError: on corrupt/invalid FIT data
    """
    if FitFile is None:
        raise ImportError("fitparse is required for Garmin FIT support. Install with: pip install fitparse")

    result: dict[str, list] = {
        "heart_rate": [],
        "hrv": [],
        "workouts": [],
    }

    fitfile = FitFile(fit_path)
    fitfile.parse()
    device = _get_device_name(fitfile)

    # Session provides workout summary and reference timestamp for HRV
    sessions = list(fitfile.get_messages("session"))
    heart_rate = _parse_record_heart_rate(
        fitfile.get_messages("record"), device
    )
    hrv_messages = list(fitfile.get_messages("hrv"))

    session_start = None
    if sessions:
        first = sessions[0]
        session_start = first.get_value("start_time") or first.get_value("timestamp")
        if session_start:
            session_start = _iso(session_start)
    recorded_at_ref = session_start
    if not recorded_at_ref and heart_rate:
        recorded_at_ref = heart_rate[0]["recorded_at"]

    result["workouts"] = _parse_sessions(sessions, device)
    result["heart_rate"] = heart_rate
    result["hrv"] = _parse_hrv(hrv_messages, recorded_at=recorded_at_ref or "", device=device)

    return result


def parse_folder(folder: str) -> dict:
    """
    Parse all .fit files in a folder and merge into one normalized dict.

    Args:
        folder: Path to directory containing .fit files

    Returns:
        Merged dict with keys: heart_rate, hrv, workouts
    """
    if FitFile is None:
        raise ImportError("fitparse is required for Garmin FIT support. Install with: pip install fitparse")

    merged: dict[str, list] = {
        "heart_rate": [],
        "hrv": [],
        "workouts": [],
    }

    folder_path = Path(folder)
    if not folder_path.is_dir():
        return merged

    for path in sorted(folder_path.glob("*.fit")):
        try:
            data = parse(str(path))
            merged["heart_rate"].extend(data["heart_rate"])
            merged["hrv"].extend(data["hrv"])
            merged["workouts"].extend(data["workouts"])
        except (FitParseError, OSError):
            continue

    return merged
