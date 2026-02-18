"""
Leo Core — Apple Health Parser
Parses export.zip from Apple Health into normalized dicts.
ZERO network imports. Stdlib only.
"""

import re
import xml.etree.ElementTree as ET
import xml.sax
import xml.sax.handler
import zipfile
from datetime import datetime
from typing import Generator


# ── Helpers ──────────────────────────────────────────────────────────────────

def _iso(date_str: str) -> str:
    """Normalize Apple Health date strings to ISO8601."""
    if not date_str:
        return ""
    # Apple uses: "2024-01-15 08:23:44 -0500"
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str.strip(), fmt).isoformat()
        except ValueError:
            continue
    return date_str.strip()


# ── SAX Handler ───────────────────────────────────────────────────────────────

class _HealthHandler(xml.sax.handler.ContentHandler):
    """
    Streaming SAX parser — memory efficient for 4GB+ XML files.
    Emits normalized dicts for each supported record type.
    """

    # Apple Health type identifiers → our internal metric names
    HEART_RATE_TYPES = {
        "HKQuantityTypeIdentifierHeartRate": "heart_rate",
        "HKQuantityTypeIdentifierRestingHeartRate": "resting_heart_rate",
        "HKQuantityTypeIdentifierWalkingHeartRateAverage": "walking_heart_rate_avg",
    }
    HRV_TYPES = {
        "HKQuantityTypeIdentifierHeartRateVariabilitySDNN": "hrv_sdnn",
    }
    SLEEP_VALUES = {
        "HKCategoryValueSleepAnalysisAsleep": "asleep",
        "HKCategoryValueSleepAnalysisInBed": "in_bed",
        "HKCategoryValueSleepAnalysisAwake": "awake",
        "HKCategoryValueSleepAnalysisREM": "rem",
        "HKCategoryValueSleepAnalysisDeepSleep": "deep",
        "HKCategoryValueSleepAnalysisCoreSleep": "core",
    }
    WORKOUT_TYPES = {
        "HKWorkoutActivityTypeRunning": "running",
        "HKWorkoutActivityTypeCycling": "cycling",
        "HKWorkoutActivityTypeWalking": "walking",
        "HKWorkoutActivityTypeSwimming": "swimming",
        "HKWorkoutActivityTypeHIIT": "hiit",
        "HKWorkoutActivityTypeStrengthTraining": "strength_training",
        "HKWorkoutActivityTypeYoga": "yoga",
        "HKWorkoutActivityTypeFunctionalStrengthTraining": "functional_strength",
    }

    def __init__(self):
        super().__init__()
        self.heart_rate: list[dict] = []
        self.hrv: list[dict] = []
        self.sleep: list[dict] = []
        self.workouts: list[dict] = []

    def startElement(self, name: str, attrs):
        if name == "Record":
            self._handle_record(attrs)
        elif name == "Workout":
            self._handle_workout(attrs)

    def _handle_record(self, attrs):
        rtype = attrs.get("type", "")

        # Heart rate
        if rtype in self.HEART_RATE_TYPES:
            self.heart_rate.append({
                "source": "apple_health",
                "metric": self.HEART_RATE_TYPES[rtype],
                "value": float(attrs.get("value", 0)),
                "unit": attrs.get("unit", "count/min"),
                "recorded_at": _iso(attrs.get("startDate", "")),
                "device": attrs.get("sourceName", ""),
            })

        # HRV
        elif rtype in self.HRV_TYPES:
            self.hrv.append({
                "source": "apple_health",
                "metric": self.HRV_TYPES[rtype],
                "value": float(attrs.get("value", 0)),
                "unit": attrs.get("unit", "ms"),
                "recorded_at": _iso(attrs.get("startDate", "")),
                "device": attrs.get("sourceName", ""),
            })

        # Sleep
        elif rtype == "HKCategoryTypeIdentifierSleepAnalysis":
            stage_raw = attrs.get("value", "")
            stage = self.SLEEP_VALUES.get(stage_raw, stage_raw.replace("HKCategoryValueSleepAnalysis", "").lower())
            self.sleep.append({
                "source": "apple_health",
                "stage": stage,
                "start": _iso(attrs.get("startDate", "")),
                "end": _iso(attrs.get("endDate", "")),
                "recorded_at": _iso(attrs.get("startDate", "")),
                "device": attrs.get("sourceName", ""),
            })

    def _handle_workout(self, attrs):
        activity_raw = attrs.get("workoutActivityType", "")
        activity = self.WORKOUT_TYPES.get(activity_raw, activity_raw.replace("HKWorkoutActivityType", "").lower())
        duration_raw = attrs.get("duration")
        distance_raw = attrs.get("totalDistance")
        energy_raw = attrs.get("totalEnergyBurned")

        self.workouts.append({
            "source": "apple_health",
            "activity": activity,
            "duration_minutes": round(float(duration_raw), 2) if duration_raw else None,
            "distance_km": round(float(distance_raw) * 1.60934, 3) if distance_raw else None,
            "calories": round(float(energy_raw), 1) if energy_raw else None,
            "recorded_at": _iso(attrs.get("startDate", "")),
            "end": _iso(attrs.get("endDate", "")),
            "device": attrs.get("sourceName", ""),
        })


# ── GPX route parser ──────────────────────────────────────────────────────────

_GPX_NS = {"gpx": "http://www.topografix.com/GPX/1/1"}


def _parse_gpx(content: bytes, workout_start: str) -> list[dict]:
    """Parse a single GPX file and return a list of route point dicts."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []

    points = []
    for trkpt in root.findall(".//gpx:trkpt", _GPX_NS):
        try:
            lat = float(trkpt.get("lat", 0))
            lon = float(trkpt.get("lon", 0))
        except (TypeError, ValueError):
            continue
        ele_el  = trkpt.find("gpx:ele",  _GPX_NS)
        time_el = trkpt.find("gpx:time", _GPX_NS)
        points.append({
            "workout_start": workout_start,
            "timestamp":     time_el.text.strip() if time_el is not None else workout_start,
            "latitude":      lat,
            "longitude":     lon,
            "altitude_m":    float(ele_el.text) if ele_el is not None else None,
        })
    return points


def _gpx_workout_start(gpx_path: str) -> str:
    """
    Extract a workout start timestamp from a GPX filename.
    Apple Health names them:  route_2024-01-15_14-30-45.gpx
    Falls back to empty string if the pattern doesn't match.
    """
    m = re.search(r"route_(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})", gpx_path)
    if m:
        return f"{m.group(1)}T{m.group(2).replace('-', ':')}"
    return ""


# ── Public API ────────────────────────────────────────────────────────────────

def parse(zip_path: str) -> dict:
    """
    Parse an Apple Health export.zip and return normalized data.

    Args:
        zip_path: Path to Apple Health export.zip

    Returns:
        Dict with keys: heart_rate, hrv, sleep, workouts
        Each is a list of normalized dicts ready for DB ingest.

    Example:
        >>> data = parse("~/Downloads/export.zip")
        >>> print(f"Parsed {len(data['heart_rate'])} heart rate records")
    """
    handler = _HealthHandler()

    with zipfile.ZipFile(zip_path, "r") as zf:
        # Apple Health zip contains apple_health_export/export.xml
        xml_candidates = [n for n in zf.namelist() if n.endswith("export.xml")]
        if not xml_candidates:
            raise FileNotFoundError("No export.xml found in zip. Is this an Apple Health export?")

        xml_path = xml_candidates[0]
        with zf.open(xml_path) as xml_file:
            xml.sax.parse(xml_file, handler)

    # Parse GPS workout routes (workout-routes/*.gpx inside the ZIP)
    routes = []
    gpx_files = [n for n in zf.namelist() if n.endswith(".gpx")]
    for gpx_path in gpx_files:
        workout_start = _gpx_workout_start(gpx_path)
        with zf.open(gpx_path) as gpx_file:
            routes.extend(_parse_gpx(gpx_file.read(), workout_start))

    return {
        "heart_rate": handler.heart_rate,
        "hrv": handler.hrv,
        "sleep": handler.sleep,
        "workouts": handler.workouts,
        "routes": routes,
    }


def parse_stream(zip_path: str) -> Generator[tuple[str, dict], None, None]:
    """
    Streaming variant — yields (table_name, record) tuples.
    Use for very large exports where you want to ingest as you parse.
    """
    handler = _HealthHandler()

    with zipfile.ZipFile(zip_path, "r") as zf:
        xml_candidates = [n for n in zf.namelist() if n.endswith("export.xml")]
        if not xml_candidates:
            raise FileNotFoundError("No export.xml found in zip.")

        with zf.open(xml_candidates[0]) as xml_file:
            xml.sax.parse(xml_file, handler)

    for record in handler.heart_rate:
        yield ("heart_rate", record)
    for record in handler.hrv:
        yield ("hrv", record)
    for record in handler.sleep:
        yield ("sleep", record)
    for record in handler.workouts:
        yield ("workouts", record)
