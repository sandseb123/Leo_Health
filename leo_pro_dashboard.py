#!/usr/bin/env python3
"""
Leo Health Dashboard
────────────────────
A beautiful, local-only web dashboard for your health data.

  • Binds to 127.0.0.1 only — data never leaves your machine
  • Zero external dependencies (stdlib only)
  • Opens automatically in your default browser

Usage:
  python3 -m leo_health.dashboard        # CLI / terminal
  leo-dash                               # if installed via install.sh
"""

import http.server
import json
import os
import socketserver
import sqlite3
from collections import defaultdict
import sys
import threading
import time
import urllib.parse
import webbrowser
from datetime import datetime, timedelta

# ── Constants ─────────────────────────────────────────────────────────────────

DB_PATH = os.path.join(os.path.expanduser("~"), ".leo-health", "leo.db")
HOST    = "127.0.0.1"
PORT    = 5380
IS_APP  = getattr(sys, "frozen", False)   # True when packaged by PyInstaller

# ── DB helpers ────────────────────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _startup_migrate():
    """
    Idempotent migration: remove duplicate sleep rows created by multiple
    Apple Health imports, then lock the table with a unique index so
    INSERT OR IGNORE prevents future duplicates.

    Runs once at dashboard start — fixes inflated stage totals without
    requiring a full re-import.
    """
    try:
        c = _conn()
        before = c.execute("SELECT COUNT(*) FROM sleep").fetchone()[0]
        # Keep only the earliest row per unique sleep segment
        c.execute("""
            DELETE FROM sleep
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM sleep
                GROUP BY source,
                         COALESCE(stage,  ''),
                         COALESCE(start,  ''),
                         COALESCE(end,    ''),
                         COALESCE(device, '')
            )
        """)
        after = c.execute("SELECT COUNT(*) FROM sleep").fetchone()[0]
        deleted = before - after
        if deleted > 0:
            print(f"      Deduplicated sleep: {before} -> {after} rows ({deleted} duplicates removed)")
        # Expression index makes INSERT OR IGNORE work for future imports
        c.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_sleep_unique
            ON sleep (
                source,
                COALESCE(stage,  ''),
                COALESCE(start,  ''),
                COALESCE(end,    ''),
                COALESCE(device, '')
            )
        """)
        c.commit()
        c.close()
    except Exception as e:
        print(f"      Warning: sleep migration failed: {e}")

def _q(sql, params=()):
    """Run a SELECT and return list-of-dicts; returns [] on any error."""
    try:
        c = _conn()
        rows = c.execute(sql, params).fetchall()
        c.close()
        return [dict(r) for r in rows]
    except Exception:
        return []

def _q1(sql, params=()):
    """Run a SELECT and return a single dict; returns {} on any error."""
    rows = _q(sql, params)
    return rows[0] if rows else {}

def _since(days):
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

# ── API functions ─────────────────────────────────────────────────────────────

def _sleep_avg(days):
    """Return average total sleep hours using the properly interval-merged api_sleep()."""
    rows = api_sleep(days)
    if not rows:
        return None
    totals = [
        (r.get("deep") or 0) + (r.get("rem") or 0) + (r.get("light") or 0)
        for r in rows
        if (r.get("deep") or 0) + (r.get("rem") or 0) + (r.get("light") or 0) > 0
    ]
    return round(sum(totals) / len(totals), 2) if totals else None


def _spo2_avg(since):
    """SpO2 average from Apple Health and/or Whoop (Apple overwrites if both present)."""
    apple = _q1("SELECT ROUND(AVG(CASE WHEN value <= 1.5 THEN value * 100.0 ELSE value END),1) AS v "
                "FROM heart_rate WHERE metric='blood_oxygen_spo2' AND recorded_at>=?", (since,))
    whoop = _q1("SELECT ROUND(AVG(spo2_pct),1) AS v FROM whoop_recovery "
                "WHERE spo2_pct IS NOT NULL AND recorded_at>=?", (since,))
    return apple.get("v") or whoop.get("v")


def _trend_pct(now_v, base_v):
    """% change from baseline (30d avg) to recent (7d avg). Positive = recent is higher."""
    try:
        if now_v is None or base_v is None or base_v == 0:
            return None
        return round((float(now_v) - float(base_v)) / float(base_v) * 100, 1)
    except Exception:
        return None


def api_summary():
    s7  = _since(7)
    s30 = _since(30)

    # ── Current 7-day values ──────────────────────────────────────────────────
    rhr  = _q1("SELECT ROUND(AVG(value),1) AS v FROM heart_rate "
               "WHERE metric='resting_heart_rate' AND recorded_at>=?", (s7,))
    hrv  = _q1("SELECT ROUND(AVG(value),1) AS v FROM hrv WHERE recorded_at>=?", (s7,))
    resp = _q1("SELECT ROUND(AVG(value),1) AS v FROM heart_rate "
               "WHERE metric='respiratory_rate' AND recorded_at>=?", (s7,))

    # Sleep via properly merged intervals (fixes double-counting)
    sleep_now = _sleep_avg(7)
    spo2_now  = _spo2_avg(s7)

    # ── 30-day baselines for trend comparison ─────────────────────────────────
    rhr_base  = _q1("SELECT ROUND(AVG(value),1) AS v FROM heart_rate "
                    "WHERE metric='resting_heart_rate' AND recorded_at>=?", (s30,))
    hrv_base  = _q1("SELECT ROUND(AVG(value),1) AS v FROM hrv WHERE recorded_at>=?", (s30,))
    resp_base = _q1("SELECT ROUND(AVG(value),1) AS v FROM heart_rate "
                    "WHERE metric='respiratory_rate' AND recorded_at>=?", (s30,))
    sleep_base = _sleep_avg(30)
    spo2_base  = _spo2_avg(s30)

    # ── Recovery scores ───────────────────────────────────────────────────────
    whoop  = _q1("SELECT ROUND(AVG(recovery_score),0) AS v FROM whoop_recovery WHERE recorded_at>=?", (s7,))
    oura   = _q1("SELECT ROUND(AVG(readiness_score),0) AS v FROM oura_readiness WHERE recorded_at>=?", (s7,))
    strain = _q1("SELECT ROUND(AVG(day_strain),1) AS v FROM whoop_strain WHERE recorded_at>=?", (s7,))

    # ── Detect data sources ───────────────────────────────────────────────────
    sources = []
    if _q1("SELECT 1 AS x FROM heart_rate WHERE source='apple_health' LIMIT 1").get("x"):
        sources.append("apple_health")
    if _q1("SELECT 1 AS x FROM whoop_recovery LIMIT 1").get("x"):
        sources.append("whoop")
    if _q1("SELECT 1 AS x FROM oura_readiness LIMIT 1").get("x"):
        sources.append("oura")
    if _q1("SELECT 1 AS x FROM heart_rate WHERE source='fitbit' LIMIT 1").get("x"):
        sources.append("fitbit")

    last = _q1("SELECT MAX(recorded_at) AS d FROM heart_rate")

    return {
        "resting_hr":       _safe_int(rhr.get("v")),
        "resting_hr_trend": _trend_pct(rhr.get("v"), rhr_base.get("v")),
        "hrv":              rhr_or_none(hrv.get("v")),
        "hrv_trend":        _trend_pct(hrv.get("v"), hrv_base.get("v")),
        "sleep_hours":      rhr_or_none(sleep_now),
        "sleep_trend":      _trend_pct(sleep_now, sleep_base),
        "spo2":             rhr_or_none(spo2_now),
        "spo2_trend":       _trend_pct(spo2_now, spo2_base),
        "resp_rate":        rhr_or_none(resp.get("v")),
        "resp_trend":       _trend_pct(resp.get("v"), resp_base.get("v")),
        "whoop_recovery":   _safe_int(whoop.get("v")),
        "oura_readiness":   _safe_int(oura.get("v")),
        "whoop_strain":     rhr_or_none(strain.get("v")),
        "sources":          sources,
        "last_recorded":    (last.get("d") or "")[:10],
    }

def _safe_int(v):
    try: return int(v) if v is not None else None
    except: return None

def rhr_or_none(v):
    try: return float(v) if v is not None else None
    except: return None


def api_heart_rate(days=30):
    return _q("""
        SELECT date(recorded_at) AS date,
               ROUND(AVG(value),0) AS avg,
               MIN(value) AS min, MAX(value) AS max
        FROM heart_rate
        WHERE metric='heart_rate' AND recorded_at>=?
        GROUP BY date(recorded_at) ORDER BY date
    """, (_since(days),))


def api_resting_hr(days=30):
    return _q("""
        SELECT date(recorded_at) AS date, ROUND(AVG(value),0) AS value
        FROM heart_rate
        WHERE metric='resting_heart_rate' AND recorded_at>=?
        GROUP BY date(recorded_at) ORDER BY date
    """, (_since(days),))


def api_hrv(days=30):
    rows = _q("""
        SELECT date(recorded_at) AS date, ROUND(AVG(value),1) AS value, source
        FROM hrv WHERE recorded_at>=?
        GROUP BY date(recorded_at), source ORDER BY date
    """, (_since(days),))
    # Collapse multiple sources per day → prefer apple_health
    by_date = {}
    for r in rows:
        d = r["date"]
        if d not in by_date or r["source"] == "apple_health":
            by_date[d] = r
    return sorted(by_date.values(), key=lambda x: x["date"])


def api_blood_oxygen(days=30):
    s = _since(days)
    # Apple Health (parsed from HKQuantityTypeIdentifierOxygenSaturation → blood_oxygen_spo2)
    apple = _q("""
        SELECT date(recorded_at) AS date,
               ROUND(AVG(CASE WHEN value <= 1.5 THEN value * 100.0 ELSE value END),1) AS value
        FROM heart_rate
        WHERE metric='blood_oxygen_spo2' AND recorded_at>=?
        GROUP BY date(recorded_at) ORDER BY date
    """, (s,))
    # Whoop (spo2_pct column in whoop_recovery)
    whoop = _q("""
        SELECT date(recorded_at) AS date, ROUND(AVG(spo2_pct),1) AS value
        FROM whoop_recovery
        WHERE spo2_pct IS NOT NULL AND recorded_at>=?
        GROUP BY date(recorded_at) ORDER BY date
    """, (s,))
    # Merge: apple overwrites whoop on the same date
    by_date = {r["date"]: r for r in whoop}
    for r in apple:
        by_date[r["date"]] = r
    return sorted(by_date.values(), key=lambda x: x["date"])


def api_respiration(days=30):
    return _q("""
        SELECT date(recorded_at) AS date, ROUND(AVG(value),1) AS value
        FROM heart_rate
        WHERE metric='respiratory_rate' AND recorded_at>=?
        GROUP BY date(recorded_at) ORDER BY date
    """, (_since(days),))


def _dur_hours(end_col, start_col):
    """SQLite expression: hours between two ISO8601 columns (handles tz offsets)."""
    # SUBSTR(...,1,19) strips timezone offset so julianday() can parse it.
    return f"(julianday(SUBSTR({end_col},1,19))-julianday(SUBSTR({start_col},1,19)))*24"


_STAGE_KEY = {
    "asleepdeep": "deep", "asleeprem": "rem", "asleepcore": "core",
    "asleepunspecified": "unspec", "awake": "awake",
}


def _merge_sleep_segments(segments):
    """
    Merge overlapping Apple Health sleep intervals.

    Apple Watch can write both short per-cycle stage segments (~30 min)
    AND longer processed blocks that cover the same time range.  A naive
    SUM() double-counts the overlap; this function computes the true
    *union* of time for each (date, device, stage) group, then returns
    one row per (date, device) matching the dict shape the caller expects.
    """
    # Group raw segments by (date, device, stage)
    groups = defaultdict(list)
    for seg in segments:
        key = (seg["date"], seg["device"], seg["stage"])
        groups[key].append((seg["seg_start"], seg["seg_end"]))

    # Merge overlapping intervals and measure total hours
    def _merged_hours(intervals):
        iv = sorted(intervals)
        merged = [list(iv[0])]
        for s, e in iv[1:]:
            if s <= merged[-1][1]:              # overlapping or touching
                merged[-1][1] = max(merged[-1][1], e)
            else:
                merged.append([s, e])
        total = 0.0
        for s, e in merged:
            try:
                ds = datetime.fromisoformat(s)
                de = datetime.fromisoformat(e)
                total += (de - ds).total_seconds() / 3600.0
            except (ValueError, TypeError):
                pass
        return round(total, 2)

    # Aggregate per (date, device)
    dd = defaultdict(lambda: {"deep": 0, "rem": 0, "core": 0,
                               "unspec": 0, "awake": 0, "device": ""})
    for (date, device, stage), ivs in groups.items():
        dd[(date, device)]["device"] = device
        dd[(date, device)][_STAGE_KEY.get(stage, "unspec")] += _merged_hours(ivs)

    # Build result list matching the shape the caller expects
    raw = []
    for (date, device), vals in sorted(dd.items()):
        raw.append({
            "date": date,
            "device": vals["device"],
            "deep": round(vals["deep"], 2),
            "rem": round(vals["rem"], 2),
            "core": round(vals["core"], 2),
            "unspec": round(vals["unspec"], 2),
            "awake": round(vals["awake"], 2),
            "efficiency": 0,
        })
    return raw


def api_sleep(days=30):
    s = _since(days)

    # ── 1. Whoop / Oura (have pre-computed stage hours) ──────────────────────
    rows = _q("""
        SELECT date(recorded_at) AS date,
               ROUND(AVG(COALESCE(deep_sleep_hours,0)),2)      AS deep,
               ROUND(AVG(COALESCE(rem_sleep_hours,0)),2)       AS rem,
               ROUND(AVG(COALESCE(light_sleep_hours,0)),2)     AS light,
               ROUND(AVG(COALESCE(awake_hours,0)),2)           AS awake,
               ROUND(AVG(COALESCE(sleep_performance_pct,0)),0) AS efficiency
        FROM sleep
        WHERE recorded_at>=? AND source IN ('whoop','oura') AND stage='asleep'
        GROUP BY date(recorded_at) ORDER BY date
    """, (s,))
    if rows:
        # Deduplicate to one row per calendar date — keep whichever session
        # has the most total sleep (handles Whoop + Oura both reporting same night,
        # or a nap + nighttime session on the same day)
        by_date: dict = {}
        for r in rows:
            d = r["date"]
            total = (r.get("deep") or 0) + (r.get("rem") or 0) + (r.get("light") or 0)
            prev = by_date.get(d)
            if prev is None:
                by_date[d] = r
            else:
                prev_total = (prev.get("deep") or 0) + (prev.get("rem") or 0) + (prev.get("light") or 0)
                if total > prev_total:
                    by_date[d] = r
        return sorted(by_date.values(), key=lambda x: x["date"])

    # ── 2. Apple Health detailed stages ──────────────────────────────────────
    # Apple Health stores stage as lowercased enum suffix:
    #   asleepdeep, asleeprem, asleepcore, asleepunspecified, awake, in_bed
    #
    # Sources of inflation to guard against:
    #   A) Multiple devices (Apple Watch + AutoSleep) writing for the same night
    #      → group by device, pick device with most deep+REM (best stage data)
    #   B) Same device writes both granular stage segments AND a long
    #      asleepunspecified umbrella for the whole night (Apple Watch behaviour)
    #      → if device has any deep/rem/core, drop asleepunspecified entirely
    #   C) Overlapping segments: Watch writes both short per-cycle segments
    #      AND longer processed blocks covering the same time range. A naive
    #      SUM() double-counts these. Fix: merge overlapping intervals in
    #      Python per (date, device, stage) and measure the union.
    #   D) Duplicate rows from re-importing the same export (handled by the
    #      UNIQUE index, but interval merging also collapses exact dupes).
    segments = _q("""
        SELECT date(recorded_at) AS date,
               device,
               stage,
               SUBSTR(start, 1, 19) AS seg_start,
               SUBSTR(end,   1, 19) AS seg_end
        FROM sleep
        WHERE recorded_at>=? AND source='apple_health'
          AND stage IN ('asleepdeep','asleeprem','asleepcore','asleepunspecified','awake')
          AND end IS NOT NULL AND start IS NOT NULL
          AND length(end)>=19 AND length(start)>=19
        ORDER BY date, device, stage, start
    """, (s,))
    if segments:
        raw = _merge_sleep_segments(segments)
    else:
        raw = []
    # Device selection priority:
    #   1. Devices whose name contains "watch" (the physical Apple Watch)
    #      — always preferred over third-party apps (AutoSleep, Sleep Cycle …)
    #      which also write deep/REM and inflate light/core records.
    #   2. Among same device-class, pick the one with the most deep+REM hours.
    # Light-sleep dedup:
    #   When a device has any deep/rem/core, Apple Watch also writes a long
    #   asleepunspecified umbrella for the whole session — exclude it and use
    #   only asleepcore for "light". Use asleepunspecified only when there are
    #   zero stage records (older watch with basic tracking).
    by_date = {}
    for r in raw:
        d = r["date"]
        is_watch = "watch" in (r.get("device") or "").lower()
        score = (r.get("deep") or 0) + (r.get("rem") or 0)
        prev = by_date.get(d)
        if prev is None:
            by_date[d] = r
        else:
            prev_is_watch = "watch" in (prev.get("device") or "").lower()
            prev_score = (prev.get("deep") or 0) + (prev.get("rem") or 0)
            # Real Apple Watch beats any third-party app unconditionally.
            # Within the same class, higher deep+REM wins.
            if is_watch and not prev_is_watch:
                by_date[d] = r
            elif is_watch == prev_is_watch and score > prev_score:
                by_date[d] = r
    rows = []
    for r in sorted(by_date.values(), key=lambda x: x["date"]):
        deep   = r.get("deep")  or 0
        rem    = r.get("rem")   or 0
        core   = r.get("core")  or 0
        unspec = r.get("unspec") or 0
        has_stage_tracking = deep > 0 or rem > 0 or core > 0
        light = core if has_stage_tracking else unspec
        entry = dict(r)
        entry["light"] = round(light, 2)
        if deep + rem + light > 0:
            rows.append(entry)
    if rows:
        return rows

    # ── 3. Last resort: Apple Health 'in_bed' only (older Apple Watch) ───────
    dur = _dur_hours("end", "start")
    rows = _q(f"""
        SELECT date(recorded_at) AS date,
               0 AS deep, 0 AS rem,
               ROUND(COALESCE(SUM({dur}),0),2) AS light,
               0 AS awake, 0 AS efficiency
        FROM sleep
        WHERE recorded_at>=? AND source='apple_health' AND stage='in_bed'
          AND end IS NOT NULL AND start IS NOT NULL
        GROUP BY date(recorded_at)
        ORDER BY date
    """, (s,))
    return [r for r in rows if (r.get("light") or 0) > 0]


def api_debug_sleep():
    """Diagnostic endpoint — call /api/debug/sleep to see raw sleep table info."""
    try:
        conn = _conn()
        total  = conn.execute("SELECT COUNT(*) AS n FROM sleep").fetchone()["n"]
        sample = [dict(r) for r in conn.execute(
            "SELECT source, stage, start, end, recorded_at FROM sleep LIMIT 10"
        ).fetchall()]
        stages = [dict(r) for r in conn.execute(
            "SELECT stage, COUNT(*) AS n FROM sleep GROUP BY stage ORDER BY n DESC"
        ).fetchall()]
        dates  = dict(conn.execute(
            "SELECT MIN(date(recorded_at)) AS mn, MAX(date(recorded_at)) AS mx FROM sleep"
        ).fetchone())
        conn.close()
        return {"total_rows": total, "date_range": dates,
                "stages": stages, "sample": sample}
    except Exception as e:
        return {"error": str(e)}


def api_sleep_stages(date=""):
    """Individual sleep stage segments for a specific night (for hypnogram hover).

    Handles both pre-iOS-16 stage names (deep, rem, core) and post-iOS-16
    names (asleepdeep, asleeprem, asleepcore, asleepunspecified).
    Uses a ±12 h window around midnight so sessions starting before midnight
    are still captured under the correct date.
    """
    if not date:
        return []
    return _q("""
        SELECT stage, start, "end",
               ROUND((julianday("end") - julianday(start)) * 24, 4) AS hours
        FROM sleep
        WHERE start >= datetime(?, '-12 hours')
          AND start <  datetime(?, '+12 hours')
          AND stage IN (
              'awake',
              'rem',   'deep',   'core',   'light',   'asleep',   'in_bed',
              'asleeprem','asleepdeep','asleepcore','asleepunspecified'
          )
          AND start IS NOT NULL
          AND "end" IS NOT NULL
        ORDER BY start
    """, (date, date))


def api_vo2max(days=365):
    """VO2 Max readings from Apple Watch (mL/min·kg).
    Measured only after outdoor runs so we use a 365-day default window."""
    rows = _q("""
        SELECT date(recorded_at) AS date, ROUND(AVG(value), 1) AS value
        FROM heart_rate
        WHERE metric = 'vo2_max'
          AND recorded_at >= ?
        GROUP BY date(recorded_at)
        ORDER BY date
    """, (_since(days),))
    return rows


def api_workout_hr(start, end):
    """Heart rate samples recorded during a workout window.
    Uses datetime() for timezone-safe comparison (handles -05:00 vs Z offsets)."""
    return _q("""
        SELECT recorded_at AS time, ROUND(value,0) AS value
        FROM heart_rate
        WHERE metric='heart_rate'
          AND datetime(recorded_at) >= datetime(?)
          AND datetime(recorded_at) <= datetime(?)
        ORDER BY recorded_at LIMIT 500
    """, (start, end))


def api_workout_route(start):
    """GPS route points for a workout.

    Apple Health GPX filenames use LOCAL time (no timezone), while
    workouts.recorded_at stores the timezone offset (e.g. -05:00).
    Comparing datetime() values converts one to UTC and leaves the other
    as local time, so they never match.  Matching on SUBSTR(…,1,16)
    (YYYY-MM-DDTHH:MM) compares the local-time portion only, which is
    identical in both representations.
    """
    return _q("""
        SELECT latitude AS lat, longitude AS lon, altitude_m AS alt, timestamp AS time
        FROM workout_routes
        WHERE SUBSTR(workout_start, 1, 16) = SUBSTR(?, 1, 16)
        ORDER BY timestamp LIMIT 5000
    """, (start,))


def api_recovery(days=30):
    s = _since(days)
    whoop = _q("""
        SELECT date(recorded_at) AS date, recovery_score AS value
        FROM whoop_recovery WHERE recorded_at>=? ORDER BY date
    """, (s,))
    whoop_strain = _q("""
        SELECT date(recorded_at) AS date, day_strain AS value
        FROM whoop_strain WHERE recorded_at>=? ORDER BY date
    """, (s,))
    oura = _q("""
        SELECT date(recorded_at) AS date, readiness_score AS value
        FROM oura_readiness WHERE recorded_at>=? ORDER BY date
    """, (s,))
    return {"whoop": whoop, "whoop_strain": whoop_strain, "oura": oura}


def api_temperature(days=30):
    s = _since(days)
    whoop = _q("""
        SELECT date(recorded_at) AS date, ROUND(skin_temp_celsius, 2) AS value
        FROM whoop_recovery
        WHERE recorded_at>=? AND skin_temp_celsius IS NOT NULL
        ORDER BY date
    """, (s,))
    oura = _q("""
        SELECT date(recorded_at) AS date, ROUND(temperature_deviation, 2) AS value
        FROM oura_readiness
        WHERE recorded_at>=? AND temperature_deviation IS NOT NULL
        ORDER BY date
    """, (s,))
    return {"whoop": whoop, "oura": oura}


def api_workouts(days=30):
    return _q("""
        SELECT w.recorded_at,
               w.end,
               date(w.recorded_at)               AS date,
               time(w.recorded_at)               AS time,
               w.activity,
               ROUND(w.duration_minutes, 1)      AS duration,
               ROUND(w.calories, 0)              AS calories,
               ROUND(w.distance_km, 2)           AS distance_km,
               w.source,
               (SELECT ROUND(AVG(h.value), 0)
                FROM heart_rate h
                WHERE h.metric = 'heart_rate'
                  AND h.recorded_at >= w.recorded_at
                  AND h.recorded_at <= COALESCE(w.end,
                        datetime(w.recorded_at, '+2 hours'))
               ) AS avg_hr,
               (SELECT ROUND(MAX(h.value), 0)
                FROM heart_rate h
                WHERE h.metric = 'heart_rate'
                  AND h.recorded_at >= w.recorded_at
                  AND h.recorded_at <= COALESCE(w.end,
                        datetime(w.recorded_at, '+2 hours'))
               ) AS max_hr,
               CASE WHEN EXISTS (
                   SELECT 1 FROM workout_routes wr
                   WHERE SUBSTR(wr.workout_start, 1, 16) = SUBSTR(w.recorded_at, 1, 16)
               ) THEN 1 ELSE 0 END AS has_route
        FROM workouts w
        WHERE w.recorded_at >= ?
          AND w.rowid = (
                SELECT MIN(w2.rowid) FROM workouts w2
                WHERE w2.recorded_at = w.recorded_at
                  AND w2.activity IS w.activity
                  AND COALESCE(w2.source,'') = COALESCE(w.source,'')
          )
        ORDER BY w.recorded_at DESC
        LIMIT 60
    """, (_since(days),))


# ── Embedded HTML/CSS/JS frontend ─────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Leo Health</title>
<style>
:root{
  --bg:#08080f;--card:#111119;--card2:#17172a;
  --border:rgba(255,255,255,0.07);--border2:rgba(255,255,255,0.12);
  --text:#f0f0f8;--dim:rgba(240,240,248,0.55);--muted:rgba(240,240,248,0.3);
  --hr:#ff375f;--hrv:#bf5af2;--vo2:#ff9f0a;
  --sleep-deep:#5e5ce6;--sleep-rem:#bf5af2;--sleep-light:#32ade6;--sleep-awake:rgba(255,149,0,0.45);
  --rec:#30d158;--read:#ffd60a;--strain:#ff9f0a;--workout:#ff9f0a;
  --spo2:#34c759;--resp:#64d2ff;--temp:#5ac8fa;
  --r:16px;--r2:10px;
}
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{background:var(--bg);color:var(--text);
  font-family:-apple-system,BlinkMacSystemFont,'SF Pro Display','Segoe UI',sans-serif;
  -webkit-font-smoothing:antialiased;min-height:100vh}

/* ── Header ─────────────────────────────────────────────────────────── */
header{position:sticky;top:0;z-index:100;
  background:rgba(8,8,15,0.85);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);
  border-bottom:1px solid var(--border);
  height:58px;display:flex;align-items:center;justify-content:space-between;padding:0 28px}
.logo{display:flex;align-items:center;gap:10px;font-size:17px;font-weight:700;letter-spacing:-.3px}
.logo-mark{width:30px;height:30px;border-radius:8px;display:flex;align-items:center;justify-content:center;
  background:linear-gradient(135deg,#ff375f,#bf5af2);font-size:15px;flex-shrink:0}
.hdr-right{display:flex;align-items:center;gap:14px}
.last-sync{font-size:11px;color:var(--muted)}
.badges{display:flex;gap:5px}
.badge{font-size:10px;padding:2px 7px;border-radius:4px;background:rgba(255,255,255,0.06);color:var(--dim)}
.crange{display:flex;background:rgba(255,255,255,0.04);border-radius:7px;padding:2px;gap:1px}
.crbtn{background:none;border:none;color:var(--muted);padding:2px 9px;border-radius:5px;
  font-size:11px;cursor:pointer;transition:all .15s;font-family:inherit;letter-spacing:.2px}
.crbtn:hover{color:var(--text)}
.crbtn.on{background:rgba(255,255,255,0.1);color:var(--text);font-weight:500}

/* ── Layout ─────────────────────────────────────────────────────────── */
main{max-width:1360px;margin:0 auto;padding:28px 28px 60px}

/* ── Summary cards ───────────────────────────────────────────────────── */
.stats-row{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:12px;margin-bottom:20px}
.stat{position:relative;background:var(--card);border:1px solid var(--border);
  border-radius:var(--r);padding:20px 20px 16px;
  transition:border-color .2s,transform .2s;cursor:default;overflow:hidden}
.stat::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;
  background:var(--stat-col,#fff);opacity:.55}
.stat:hover{border-color:var(--border2);transform:translateY(-2px)}
.stat-hdr{display:flex;align-items:center;gap:6px;margin-bottom:12px}
.stat-icon{font-size:14px;line-height:1}
.stat-label{font-size:10px;text-transform:uppercase;letter-spacing:.9px;color:var(--muted)}
.stat-val{font-size:40px;font-weight:800;letter-spacing:-2px;line-height:1;margin-bottom:10px}
.stat-unit{font-size:14px;font-weight:500;color:var(--dim);margin-left:3px;letter-spacing:0}
.stat-ftr{display:flex;align-items:center;gap:7px}
.stat-sub{font-size:10px;color:var(--muted)}

/* ── Chart cards ─────────────────────────────────────────────────────── */
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
  padding:22px 22px 18px;margin-bottom:14px;transition:border-color .2s}
.card:hover{border-color:var(--border2)}
.card-hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
.card-title{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:600}
.dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.card-stat{text-align:right}
.card-stat-val{font-size:26px;font-weight:800;letter-spacing:-.8px}
.card-stat-lbl{font-size:10px;color:var(--muted);margin-top:1px}

/* ── Activity breakdown bars ─────────────────────────────────────────── */
.act-breakdown{margin-top:18px;border-top:1px solid var(--border);padding-top:16px}
.act-breakdown-title{font-size:11px;text-transform:uppercase;letter-spacing:.8px;
  color:var(--muted);margin-bottom:12px}
.act-bar-row{display:flex;align-items:center;gap:10px;margin-bottom:9px}
.act-bar-label{font-size:12px;font-weight:500;width:110px;flex-shrink:0;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis}
.act-bar-track{flex:1;height:6px;background:rgba(255,255,255,0.07);border-radius:3px;overflow:hidden}
.act-bar-fill{height:100%;border-radius:3px;transition:width .6s cubic-bezier(.4,0,.2,1)}
.act-bar-pct{font-size:11px;color:var(--muted);width:30px;text-align:right;flex-shrink:0}
.two{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}

/* ── Canvas ──────────────────────────────────────────────────────────── */
.chart-wrap{position:relative;width:100%}
canvas{display:block;width:100%}
.overlay{position:absolute;top:0;left:0;width:100%;height:100%;pointer-events:none}

/* ── Sleep legend ────────────────────────────────────────────────────── */
.legend{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px}
.leg{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--dim)}
.leg-sq{width:9px;height:9px;border-radius:2px;flex-shrink:0}

/* ── Workout list ────────────────────────────────────────────────────── */
.wo-list{display:flex;flex-direction:column;gap:5px;margin-top:6px}
.wo{background:rgba(255,255,255,0.03);border:1px solid transparent;
  border-radius:var(--r2);overflow:hidden;transition:border-color .15s,background .15s;cursor:pointer}
.wo:hover{background:rgba(255,255,255,0.055);border-color:var(--border)}
.wo-main{display:flex;align-items:center;justify-content:space-between;padding:12px 14px}
.wo-left{display:flex;align-items:center;gap:11px}
.wo-icon{font-size:20px;line-height:1;flex-shrink:0}
.wo-name{font-size:13px;font-weight:500}
.wo-date{font-size:11px;color:var(--muted);margin-top:1px}
.wo-right{display:flex;align-items:center;gap:12px}
.wo-dur{font-size:13px;font-weight:600;color:var(--workout)}
.wo-cal{font-size:12px;color:var(--dim)}
.wo-chev{font-size:11px;color:var(--muted);transition:transform .2s;flex-shrink:0}
.wo.open .wo-chev{transform:rotate(90deg)}
/* Expandable detail panel */
.wo-detail{max-height:0;overflow:hidden;transition:max-height .4s ease}
.wo.open .wo-detail{max-height:1400px}
.wo-type-badge{display:inline-block;font-size:9px;font-weight:700;letter-spacing:.6px;
  padding:2px 7px;border-radius:10px;margin-left:6px;vertical-align:middle;text-transform:uppercase}
.wo-badge-outdoor{background:rgba(48,209,88,0.15);color:#30d158}
.wo-badge-indoor{background:rgba(255,255,255,0.07);color:rgba(255,255,255,0.35)}
.wo-no-gps{padding:12px 0;font-size:11px;color:var(--muted);text-align:center;font-style:italic}
.wo-splits{padding:10px 0 2px}
.wo-splits-title{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.7px;margin-bottom:8px}
.wo-split-row{display:flex;align-items:center;gap:8px;margin-bottom:4px}
.wo-split-lbl{font-size:10px;color:var(--muted);width:28px;text-align:right;flex-shrink:0}
.wo-split-bar-track{flex:1;height:6px;border-radius:3px;background:rgba(255,255,255,0.07);overflow:hidden}
.wo-split-bar{height:100%;border-radius:3px;transition:width .4s ease}
.wo-split-pace{font-size:10px;color:var(--text);width:38px;flex-shrink:0}
.wo-detail-inner{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));
  gap:10px 16px;padding:0 14px 10px 46px}
.wo-stat{display:flex;flex-direction:column;gap:2px}
.wo-stat-val{font-size:14px;font-weight:600}
.wo-stat-lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}
/* Workout HR chart + route map */
.wo-detail-charts{display:flex;gap:12px;padding:0 14px 10px 14px;align-items:flex-start}
.wo-hr-chart{flex:1;min-width:0}
.wo-hr-chart canvas{display:block;width:100%;height:120px;border-radius:6px}
.wo-hr-lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.7px;margin-bottom:4px}
.wo-route-wrap{width:130px;flex-shrink:0}
.wo-route-wrap svg{display:block;width:130px;height:130px;border-radius:8px}
.wo-route-lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.7px;margin-bottom:4px}
.wo-route-section{padding:0 14px 14px}
.wo-route-canvas{display:block;width:100%;height:200px;border-radius:10px}
.wo-elev-canvas{display:block;width:100%;height:52px;margin-top:4px;border-radius:6px}
/* HR Zones bar */
.wo-zones{padding:4px 14px 14px 14px}
.wo-zones-lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.7px;margin-bottom:7px;display:flex;align-items:center;gap:8px}
.wo-zones-maxhr{font-size:9px;color:rgba(240,240,248,0.25);font-weight:400;letter-spacing:0}
.wo-zone-bar{display:flex;height:10px;border-radius:5px;overflow:hidden;gap:2px;margin-bottom:8px}
.wo-zone-seg{border-radius:3px}
.wo-zone-legend{display:flex;gap:10px 18px;flex-wrap:wrap}
.wo-zone-item{display:flex;align-items:center;gap:5px;font-size:10px;color:var(--dim)}
.wo-zone-dot{width:7px;height:7px;border-radius:50%;flex-shrink:0}
.wo-zone-time{color:var(--muted);font-size:9px;margin-left:1px}

/* ── Tooltip ─────────────────────────────────────────────────────────── */
#tt{position:fixed;background:rgba(14,14,26,.95);border:1px solid rgba(255,255,255,.12);
  border-radius:9px;padding:9px 13px;font-size:12px;pointer-events:none;z-index:999;
  display:none;backdrop-filter:blur(12px);min-width:110px}
#tt-date{color:var(--muted);font-size:10px;margin-bottom:3px}
#tt-val{font-weight:600;font-size:15px}
#tt-sub{color:var(--dim);font-size:11px;margin-top:1px}

/* ── Empty state ─────────────────────────────────────────────────────── */
.empty{display:flex;align-items:center;justify-content:center;
  height:80px;color:var(--muted);font-size:13px;font-style:italic}

/* ── Trend badges ────────────────────────────────────────────────────── */
.trend{display:inline-flex;align-items:center;
  font-size:11px;font-weight:700;letter-spacing:.2px;
  padding:3px 9px;border-radius:20px;white-space:nowrap}
.trend-improve{background:rgba(180,255,80,.13);color:#b4ff50;border:1px solid rgba(180,255,80,.25)}
.trend-decline{background:rgba(255,55,95,.13);color:#ff375f;border:1px solid rgba(255,55,95,.25)}
.trend-stable{background:rgba(255,255,255,.05);color:var(--muted);border:1px solid rgba(255,255,255,.07)}

/* ── Card note (contextual sub-text) ────────────────────────────────── */
.card-note{font-size:11px;color:var(--muted);margin:-10px 0 14px;
  padding:7px 10px;background:rgba(255,255,255,.03);border-radius:6px;
  border-left:2px solid var(--muted)}

/* ── Responsive ──────────────────────────────────────────────────────── */
@media(max-width:720px){
  main{padding:16px 14px 50px}
  .two{grid-template-columns:1fr}
  .stats-row{grid-template-columns:repeat(2,1fr)}
  .stat-val{font-size:34px}
  header{padding:0 16px}
}
</style>
</head>
<body>

<header>
  <div class="logo">
    <div class="logo-mark">🦁</div>
    Leo Health
  </div>
  <div class="hdr-right">
    <div class="badges" id="badges"></div>
    <span class="last-sync" id="sync"></span>
  </div>
</header>

<main>
  <!-- Summary row -->
  <div class="stats-row" id="statsRow"></div>

  <!-- HRV + RHR -->
  <div class="two">
    <div class="card">
      <div class="card-hdr">
        <div class="card-title"><div class="dot" style="background:var(--hrv)"></div>HRV</div>
        <div class="crange" data-chart="hrv">
          <button class="crbtn on" data-d="7">7D</button>
          <button class="crbtn" data-d="14">14D</button>
          <button class="crbtn" data-d="30">30D</button>
        </div>
        <div class="card-stat"><div class="card-stat-val" id="hrvVal" style="color:var(--hrv)">—</div><div class="card-stat-lbl">avg ms</div></div>
      </div>
      <div class="chart-wrap"><canvas id="hrvC" height="140"></canvas><canvas class="overlay" id="hrvO" height="140"></canvas></div>
    </div>
    <div class="card">
      <div class="card-hdr">
        <div class="card-title"><div class="dot" style="background:#ff6b6b"></div>Resting HR</div>
        <div class="crange" data-chart="rhr">
          <button class="crbtn on" data-d="7">7D</button>
          <button class="crbtn" data-d="14">14D</button>
          <button class="crbtn" data-d="30">30D</button>
        </div>
        <div class="card-stat"><div class="card-stat-val" id="rhrVal" style="color:#ff6b6b">—</div><div class="card-stat-lbl">avg bpm</div></div>
      </div>
      <div class="chart-wrap"><canvas id="rhrC" height="140"></canvas><canvas class="overlay" id="rhrO" height="140"></canvas></div>
    </div>
  </div>

  <!-- VO2 Max -->
  <div class="card" id="vo2Card" style="display:none">
    <div class="card-hdr">
      <div class="card-title"><div class="dot" style="background:var(--vo2)"></div>VO2 Max</div>
      <div class="crange" data-chart="vo2max">
        <button class="crbtn on" data-d="90">90D</button>
        <button class="crbtn" data-d="180">180D</button>
        <button class="crbtn" data-d="365">1Y</button>
      </div>
      <div class="card-stat">
        <div class="card-stat-val" id="vo2Val" style="color:var(--vo2)">—</div>
        <div class="card-stat-lbl">mL/min·kg</div>
      </div>
    </div>
    <div class="chart-wrap"><canvas id="vo2C" height="140"></canvas><canvas class="overlay" id="vo2O" height="140"></canvas></div>
    <div id="vo2Zone" style="padding:6px 16px 10px;font-size:11px;color:var(--muted)"></div>
  </div>

  <!-- Heart Rate Daily Range -->
  <div class="card" id="hrCard" style="display:none">
    <div class="card-hdr">
      <div class="card-title"><div class="dot" style="background:#ff9f43"></div>Heart Rate</div>
      <div class="crange" data-chart="hr">
        <button class="crbtn on" data-d="7">7D</button>
        <button class="crbtn" data-d="14">14D</button>
        <button class="crbtn" data-d="30">30D</button>
      </div>
      <div class="card-stat"><div class="card-stat-val" id="hrVal" style="color:#ff9f43">—</div><div class="card-stat-lbl">avg bpm</div></div>
    </div>
    <div class="chart-wrap"><canvas id="hrC" height="128"></canvas><canvas class="overlay" id="hrO" height="128"></canvas></div>
  </div>

  <!-- Blood Oxygen + Respiration Rate -->
  <div class="two">
    <div class="card" id="spo2Card">
      <div class="card-hdr">
        <div class="card-title"><div class="dot" style="background:var(--spo2)"></div>Blood Oxygen</div>
        <div class="crange" data-chart="spo2">
          <button class="crbtn on" data-d="7">7D</button>
          <button class="crbtn" data-d="14">14D</button>
          <button class="crbtn" data-d="30">30D</button>
        </div>
        <div class="card-stat"><div class="card-stat-val" id="spo2Val" style="color:var(--spo2)">—</div><div class="card-stat-lbl">avg SpO₂ %</div></div>
      </div>
      <div class="chart-wrap"><canvas id="spo2C" height="140"></canvas><canvas class="overlay" id="spo2O" height="140"></canvas></div>
    </div>
    <div class="card" id="respCard">
      <div class="card-hdr">
        <div class="card-title"><div class="dot" style="background:var(--resp)"></div>Respiration Rate</div>
        <div class="crange" data-chart="resp">
          <button class="crbtn on" data-d="7">7D</button>
          <button class="crbtn" data-d="14">14D</button>
          <button class="crbtn" data-d="30">30D</button>
        </div>
        <div class="card-stat"><div class="card-stat-val" id="respVal" style="color:var(--resp)">—</div><div class="card-stat-lbl">avg br/min</div></div>
      </div>
      <div class="chart-wrap"><canvas id="respC" height="140"></canvas><canvas class="overlay" id="respO" height="140"></canvas></div>
    </div>
  </div>

  <!-- Sleep -->
  <div class="card">
    <div class="card-hdr">
      <div class="card-title"><div class="dot" style="background:var(--sleep-deep)"></div>Sleep</div>
      <div class="crange" data-chart="sleep">
        <button class="crbtn on" data-d="7">7D</button>
        <button class="crbtn" data-d="14">14D</button>
        <button class="crbtn" data-d="30">30D</button>
      </div>
      <div class="card-stat"><div class="card-stat-val" id="sleepVal" style="color:var(--sleep-deep)">—</div><div class="card-stat-lbl">avg hours</div></div>
      <div class="card-stat" id="effStat" style="display:none"><div class="card-stat-val" id="effVal" style="color:#32ade6">—</div><div class="card-stat-lbl">efficiency %</div></div>
    </div>
    <div class="chart-wrap"><canvas id="slC" height="150"></canvas><canvas class="overlay" id="slO" height="150"></canvas></div>
    <div class="legend">
      <div class="leg"><div class="leg-sq" style="background:var(--sleep-deep)"></div>Deep</div>
      <div class="leg"><div class="leg-sq" style="background:var(--sleep-rem)"></div>REM</div>
      <div class="leg"><div class="leg-sq" style="background:var(--sleep-light)"></div>Light / Core</div>
      <div class="leg"><div class="leg-sq" style="background:var(--sleep-awake)"></div>Awake</div>
    </div>
  </div>

  <!-- Recovery + Readiness -->
  <div class="two" id="recRow">
    <div class="card" id="whoopCard">
      <div class="card-hdr">
        <div class="card-title"><div class="dot" style="background:var(--rec)"></div>Whoop Recovery</div>
        <div class="crange" data-chart="rec">
          <button class="crbtn on" data-d="7">7D</button>
          <button class="crbtn" data-d="14">14D</button>
          <button class="crbtn" data-d="30">30D</button>
        </div>
        <div class="card-stat"><div class="card-stat-val" id="whoopVal" style="color:var(--rec)">—</div><div class="card-stat-lbl">avg %</div></div>
      </div>
      <div class="chart-wrap"><canvas id="whoopC" height="140"></canvas><canvas class="overlay" id="whoopO" height="140"></canvas></div>
    </div>
    <div class="card" id="ouraCard">
      <div class="card-hdr">
        <div class="card-title"><div class="dot" style="background:var(--read)"></div>Oura Readiness</div>
        <div class="crange" data-chart="rec">
          <button class="crbtn on" data-d="7">7D</button>
          <button class="crbtn" data-d="14">14D</button>
          <button class="crbtn" data-d="30">30D</button>
        </div>
        <div class="card-stat"><div class="card-stat-val" id="ouraVal" style="color:var(--read)">—</div><div class="card-stat-lbl">avg score</div></div>
      </div>
      <div class="chart-wrap"><canvas id="ouraC" height="140"></canvas><canvas class="overlay" id="ouraO" height="140"></canvas></div>
    </div>
  </div>

  <!-- Whoop Strain -->
  <div class="card" id="strainCard" style="display:none">
    <div class="card-hdr">
      <div class="card-title"><div class="dot" style="background:var(--strain)"></div>Whoop Strain</div>
      <div class="crange" data-chart="rec">
        <button class="crbtn on" data-d="7">7D</button>
        <button class="crbtn" data-d="14">14D</button>
        <button class="crbtn" data-d="30">30D</button>
      </div>
      <div class="card-stat"><div class="card-stat-val" id="strainVal" style="color:var(--strain)">—</div><div class="card-stat-lbl">avg / 21</div></div>
    </div>
    <div class="chart-wrap"><canvas id="strainC" height="128"></canvas><canvas class="overlay" id="strainO" height="128"></canvas></div>
  </div>

  <!-- Body Temperature -->
  <div class="card" id="tempCard" style="display:none">
    <div class="card-hdr">
      <div class="card-title"><div class="dot" style="background:var(--temp)"></div>Body Temperature</div>
      <div class="crange" data-chart="temp">
        <button class="crbtn on" data-d="7">7D</button>
        <button class="crbtn" data-d="14">14D</button>
        <button class="crbtn" data-d="30">30D</button>
      </div>
      <div class="card-stat"><div class="card-stat-val" id="tempVal" style="color:var(--temp)">—</div><div class="card-stat-lbl" id="tempLbl">deviation °C</div></div>
    </div>
    <div class="chart-wrap"><canvas id="tempC" height="128"></canvas><canvas class="overlay" id="tempO" height="128"></canvas></div>
  </div>

  <!-- Workouts -->
  <div class="card">
    <div class="card-hdr">
      <div class="card-title"><div class="dot" style="background:var(--workout)"></div>Workouts</div>
      <div class="crange" data-chart="wo">
        <button class="crbtn on" data-d="7">7D</button>
        <button class="crbtn" data-d="14">14D</button>
        <button class="crbtn" data-d="30">30D</button>
      </div>
    </div>
    <div id="woList"></div>
    <div id="actBreakdown"></div>
  </div>
</main>

<div id="tt"><div id="tt-date"></div><div id="tt-val"></div><div id="tt-sub"></div></div>

<script>
// ── Colors (hex literals — CSS vars don't work in Canvas API) ─────────────────
const C = {
  hr:    '#ff375f',
  hrv:   '#bf5af2',
  rhr:   '#ff6b6b',
  sleep: '#0a84ff',
  rec:   '#30d158',
  read:  '#ffd60a',
  strain:'#ff9f0a',
  spo2:  '#34c759',
  resp:  '#64d2ff',
  temp:  '#5ac8fa',
  vo2:   '#ff9f0a',
};

// ── State & utils ─────────────────────────────────────────────────────────────
const D = {hr:7, hrv:7, rhr:7, sleep:7, rec:7, wo:7, spo2:7, resp:7, temp:7, vo2max:90};
const cache = {};
const $ = id => document.getElementById(id);
const fmt = (n, d=0) => n == null ? '—' : (+n).toFixed(d);
const avg = arr => arr.length ? arr.reduce((s,v)=>s+v,0)/arr.length : 0;

function fmtDate(s) {
  const d = new Date(s + 'T12:00:00');
  return d.toLocaleDateString('en-US', {month:'short', day:'numeric'});
}
function fmtDateLong(s) {
  const d = new Date(s + 'T12:00:00');
  return d.toLocaleDateString('en-US', {weekday:'short', month:'short', day:'numeric'});
}

// Animated counter (counts from 0 to target)
function countUp(el, target, decimals=0, suffix='') {
  if (target == null || isNaN(target)) { el.innerHTML = '—'; return; }
  const dur = 800, steps = 40, step = dur/steps;
  let i = 0;
  const tick = () => {
    i++;
    const progress = i/steps;
    const ease = 1 - Math.pow(1-progress, 3); // ease-out cubic
    el.textContent = (+target * ease).toFixed(decimals) + suffix;
    if (i < steps) setTimeout(tick, step);
    else el.textContent = (+target).toFixed(decimals) + suffix;
  };
  tick();
}

// ── Canvas helpers ────────────────────────────────────────────────────────────
function ctx2d(id) {
  const c = $(id);
  if (!c) return null;
  const dpr = window.devicePixelRatio || 1;
  const w = c.offsetWidth || 600;
  const h = c.offsetHeight || parseInt(c.getAttribute('height')) || 128;
  c.width  = w * dpr;
  c.height = h * dpr;
  const cx = c.getContext('2d');
  cx.scale(dpr, dpr);
  return { cx, w, h };
}

// ── Line Chart ────────────────────────────────────────────────────────────────
const chartMeta = {};   // stores layout info per chart id for hover

function drawLine(mainId, overlayId, data, {
  color='#fff', valueKey='value', dateKey='date',
  minY=null, maxY=null, unit='', label2=null, value2Key=null, color2=null
}={}, hoverIdx=null) {
  if (!data || !data.length) return;

  const m = ctx2d(mainId);
  if (!m) return;
  const {cx, w, h} = m;

  const pad = {t:12, r:12, b:26, l:38};
  const cw = w - pad.l - pad.r;
  const ch = h - pad.t - pad.b;

  const vals  = data.map(d=>d[valueKey]).filter(v=>v!=null);
  const vals2 = (value2Key ? data.map(d=>d[value2Key]).filter(v=>v!=null) : []);
  const allVals = [...vals, ...vals2];
  if (!allVals.length) return;

  const yMin = minY ?? Math.min(...allVals) * 0.94;
  const yMax = maxY ?? Math.max(...allVals) * 1.06;
  const yRange = yMax - yMin || 1;

  const xOf = i => pad.l + (i / Math.max(data.length-1, 1)) * cw;
  const yOf = v => pad.t + ch - ((v-yMin)/yRange)*ch;

  // Compute screen points
  const pts = data.map((d,i) => ({
    x: xOf(i),
    y: d[valueKey] != null ? yOf(d[valueKey]) : null,
    y2: (value2Key && d[value2Key] != null) ? yOf(d[value2Key]) : null,
  }));

  // Save meta for hover
  chartMeta[mainId] = { data, pts, valueKey, dateKey, color, unit, pad, cw, ch };

  // Clear
  cx.clearRect(0, 0, w, h);

  // Horizontal grid lines
  cx.strokeStyle = 'rgba(255,255,255,0.04)';
  cx.lineWidth   = 1;
  for (let i=0; i<=4; i++) {
    const y = pad.t + (ch/4)*i;
    cx.beginPath(); cx.moveTo(pad.l, y); cx.lineTo(w-pad.r, y); cx.stroke();
  }

  // Y labels
  cx.fillStyle   = 'rgba(255,255,255,0.28)';
  cx.font        = '10px -apple-system,sans-serif';
  cx.textAlign   = 'right';
  cx.textBaseline= 'middle';
  for (let i=0; i<=2; i++) {
    const v = yMin + (yRange/2)*i;
    const y = pad.t + ch - ((v-yMin)/yRange)*ch;
    cx.fillText(Math.round(v), pad.l-6, y);
  }

  // X labels (first / middle / last)
  cx.textAlign   = 'center';
  cx.textBaseline= 'alphabetic';
  [0, Math.floor(data.length/2), data.length-1].forEach(i => {
    if (data[i]) cx.fillText(fmtDate(data[i][dateKey]), xOf(i), h-4);
  });

  // Draw a single series
  function drawSeries(pts, col, yk) {
    const validPts = pts.filter(p => p[yk] != null);
    if (validPts.length < 2) return;

    // Gradient fill
    const grad = cx.createLinearGradient(0, pad.t, 0, pad.t+ch);
    grad.addColorStop(0,   col + '28');
    grad.addColorStop(0.7, col + '08');
    grad.addColorStop(1,   col + '00');

    cx.beginPath();
    let first = true;
    for (let i=0; i<pts.length; i++) {
      const p = pts[i], y = p[yk];
      if (y == null) { first=true; continue; }
      if (first) { cx.moveTo(p.x, y); first=false; }
      else {
        const pp = pts.slice(0,i).reverse().find(pp=>pp[yk]!=null);
        if (pp) {
          const cpx = (pp.x + p.x) / 2;
          cx.bezierCurveTo(cpx, pp[yk], cpx, y, p.x, y);
        } else cx.lineTo(p.x, y);
      }
    }
    const last = validPts[validPts.length-1];
    const fst  = validPts[0];
    cx.lineTo(last.x, pad.t+ch);
    cx.lineTo(fst.x,  pad.t+ch);
    cx.closePath();
    cx.fillStyle = grad; cx.fill();

    // Line
    cx.beginPath();
    first = true;
    for (let i=0; i<pts.length; i++) {
      const p = pts[i], y = p[yk];
      if (y == null) { first=true; continue; }
      if (first) { cx.moveTo(p.x, y); first=false; }
      else {
        const pp = pts.slice(0,i).reverse().find(pp=>pp[yk]!=null);
        if (pp) {
          const cpx = (pp.x + p.x) / 2;
          cx.bezierCurveTo(cpx, pp[yk], cpx, y, p.x, y);
        } else cx.lineTo(p.x, y);
      }
    }
    cx.strokeStyle = col; cx.lineWidth = 2;
    cx.lineJoin='round'; cx.lineCap='round'; cx.stroke();

    // Terminus dot
    cx.beginPath();
    cx.arc(last.x, last[yk], 4, 0, Math.PI*2);
    cx.fillStyle = col; cx.fill();
    cx.beginPath();
    cx.arc(last.x, last[yk], 2, 0, Math.PI*2);
    cx.fillStyle = '#fff'; cx.fill();
  }

  drawSeries(pts, color, 'y');
  if (value2Key && color2) drawSeries(pts, color2, 'y2');

  // Hover indicator (drawn on overlay canvas instead, see drawOverlay)
  if (hoverIdx !== null) drawOverlay(mainId, overlayId, hoverIdx);
}

function drawOverlay(mainId, overlayId, idx) {
  const meta = chartMeta[mainId];
  if (!meta || !overlayId) return;
  const m = ctx2d(overlayId);
  if (!m) return;
  const {cx, w, h} = m;
  cx.clearRect(0, 0, w, h);

  const p = meta.pts[idx];
  if (!p || p.y === null) return;

  const {pad, ch, color} = meta;

  // Vertical line
  cx.strokeStyle = 'rgba(255,255,255,0.18)';
  cx.lineWidth   = 1;
  cx.setLineDash([3,4]);
  cx.beginPath();
  cx.moveTo(p.x, pad.t);
  cx.lineTo(p.x, pad.t+ch);
  cx.stroke();
  cx.setLineDash([]);

  // Halo
  cx.beginPath();
  cx.arc(p.x, p.y, 8, 0, Math.PI*2);
  cx.fillStyle = color + '30';
  cx.fill();
  // Dot
  cx.beginPath();
  cx.arc(p.x, p.y, 4, 0, Math.PI*2);
  cx.fillStyle = color; cx.fill();
  cx.beginPath();
  cx.arc(p.x, p.y, 2, 0, Math.PI*2);
  cx.fillStyle = '#fff'; cx.fill();
}

function clearOverlay(overlayId) {
  const c = $(overlayId);
  if (!c) return;
  const dpr = window.devicePixelRatio || 1;
  c.getContext('2d').clearRect(0, 0, c.width/dpr, c.height/dpr);
}

// ── Hover tooltips ────────────────────────────────────────────────────────────
function attachHover(wrapEl, mainId, overlayId, getLabel) {
  const tt = $('tt');
  wrapEl.addEventListener('mousemove', e => {
    const meta = chartMeta[mainId];
    if (!meta) return;

    const rect = wrapEl.getBoundingClientRect();
    const mx   = e.clientX - rect.left;

    // Find nearest point by x
    let best=-1, bestD=Infinity;
    meta.pts.forEach((p,i)=>{ if(p.y==null)return; const d=Math.abs(p.x-mx); if(d<bestD){bestD=d;best=i;}});
    if (best < 0) return;

    drawOverlay(mainId, overlayId, best);

    const d   = meta.data[best];
    const {val, sub} = getLabel(d, best);

    tt.style.display = 'block';
    tt.style.left    = (e.clientX + 14) + 'px';
    tt.style.top     = (e.clientY - 48) + 'px';
    $('tt-date').textContent = fmtDateLong(d[meta.dateKey]);
    $('tt-val').textContent  = val;
    $('tt-sub').textContent  = sub || '';
  });
  wrapEl.addEventListener('mouseleave', () => {
    tt.style.display = 'none';
    clearOverlay(overlayId);
  });
}

// ── Sleep chart (stacked bars) ────────────────────────────────────────────────
function drawSleep(id, data) {
  if (!data || !data.length) return;
  const nights = data.slice(-Math.min(30, data.length));
  const m = ctx2d(id);
  if (!m) return;
  const {cx, w, h} = m;
  const pad = {t:10, r:10, b:26, l:36};
  const cw = w - pad.l - pad.r, ch = h - pad.t - pad.b;

  const maxH = Math.max(...nights.map(n=>(n.deep||0)+(n.rem||0)+(n.light||0)+(n.awake||0)), 8);
  const yScale = v => (v/maxH)*ch;

  cx.clearRect(0, 0, w, h);

  // Grid
  [0,4,8].forEach(v=>{
    const y=pad.t+ch-yScale(v);
    cx.strokeStyle='rgba(255,255,255,0.04)'; cx.lineWidth=1;
    cx.beginPath(); cx.moveTo(pad.l,y); cx.lineTo(w-pad.r,y); cx.stroke();
    cx.fillStyle='rgba(255,255,255,0.28)'; cx.font='10px -apple-system,sans-serif';
    cx.textAlign='right'; cx.textBaseline='middle';
    cx.fillText(v+'h', pad.l-5, y);
  });

  const barW = (cw - (nights.length-1)*3) / nights.length;

  nights.forEach((n,i)=>{
    const x = pad.l + i*(barW+3);
    let top = pad.t + ch;

    [
      {val:n.awake||0,  col:'rgba(255,149,0,0.45)'},
      {val:n.light||0,  col:'#32ade6'},
      {val:n.rem||0,    col:'#bf5af2'},
      {val:n.deep||0,   col:'#5e5ce6'},
    ].forEach(({val,col})=>{
      if (!val) return;
      const bh = yScale(val);
      top -= bh;
      cx.beginPath();
      cx.fillStyle = col;
      if (cx.roundRect) cx.roundRect(x, top, barW, bh, 2.5);
      else cx.rect(x, top, barW, bh);
      cx.fill();
    });

    // Date labels (sparse)
    if (nights.length <= 10 || i % Math.ceil(nights.length/6) === 0 || i===nights.length-1) {
      cx.fillStyle='rgba(255,255,255,0.28)'; cx.font='10px -apple-system,sans-serif';
      cx.textAlign='center'; cx.textBaseline='alphabetic';
      cx.fillText(fmtDate(n.date), x+barW/2, h-4);
    }
  });
}

// ── Workout helpers ───────────────────────────────────────────────────────────
const WO_META = {
  // key: [icon, display name]
  running:                    ['🏃', 'Running'],
  cycling:                    ['🚴', 'Cycling'],
  walking:                    ['🚶', 'Walking'],
  swimming:                   ['🏊', 'Swimming'],
  yoga:                       ['🧘', 'Yoga'],
  hiit:                       ['⚡', 'HIIT'],
  strength_training:          ['🏋️', 'Strength Training'],
  functional_strength:        ['🏋️', 'Functional Strength'],
  // raw lowercase fallthrough names from Apple Health
  traditionalstrengthtraining:['🏋️', 'Strength Training'],
  functionalstrengthtraining: ['🏋️', 'Functional Strength'],
  mindandbody:                ['🧘', 'Mind & Body'],
  mixedcardio:                ['❤️‍🔥', 'Mixed Cardio'],
  coretraining:               ['🤸', 'Core Training'],
  crosstraining:              ['🔄', 'Cross Training'],
  elliptical:                 ['🔄', 'Elliptical'],
  rowing:                     ['🚣', 'Rowing'],
  stairclimbing:              ['🪜', 'Stair Climbing'],
  stairs:                     ['🪜', 'Stairs'],
  tennis:                     ['🎾', 'Tennis'],
  basketball:                 ['🏀', 'Basketball'],
  soccer:                     ['⚽', 'Soccer'],
  hiking:                     ['🥾', 'Hiking'],
  skiing:                     ['⛷️', 'Skiing'],
  snowboarding:               ['🏂', 'Snowboarding'],
  dance:                      ['💃', 'Dance'],
  pilates:                    ['🤸', 'Pilates'],
  golf:                       ['⛳', 'Golf'],
  barre:                      ['🩰', 'Barre'],
  cooldown:                   ['🧊', 'Cooldown'],
  preparationandrecovery:     ['🧊', 'Recovery'],
  other:                      ['💪', 'Workout'],
};

function woIcon(activity)  { return (WO_META[activity] || WO_META.other)[0]; }
function woName(activity) {
  if (!activity) return 'Workout';
  const meta = WO_META[activity.toLowerCase().replace(/[\s_]/g,'')];
  if (meta) return meta[1];
  // fallback: split on underscore/space and title-case
  return activity.replace(/_/g,' ').replace(/\b\w/g, c=>c.toUpperCase());
}

function toggleWo(el, idx) {
  const wasOpen = el.classList.contains('open');
  el.classList.toggle('open');
  if (!wasOpen && !el.dataset.hrLoaded) {
    el.dataset.hrLoaded = '1';
    loadWoDetail(el, idx);
  }
}

// ── Workout HR mini-chart (with hover) ────────────────────────────────────────
function drawWoHR(canvasId, data) {
  const c = $(canvasId);
  if (!c) return;
  const dpr = window.devicePixelRatio || 1;
  const w = c.offsetWidth || c.parentElement.offsetWidth || 280;
  const h = 120;
  c.width = w * dpr; c.height = h * dpr;
  const cx = c.getContext('2d');
  cx.scale(dpr, dpr);

  const vals = data.map(d => +d.value).filter(v => !isNaN(v));
  if (vals.length < 2) return;
  const rawMax = Math.max(...vals);
  const maxRef = Math.round(Math.max(rawMax * 1.12, rawMax + 15));
  const mn = Math.min(Math.min(...vals) * 0.97, maxRef * 0.45);
  const mx = maxRef * 1.03;
  const rng = mx - mn || 1;
  const pad = {t:8, r:8, b:18, l:36};
  const cw = w - pad.l - pad.r, ch = h - pad.t - pad.b;
  const avgV = Math.round(vals.reduce((s,v)=>s+v,0)/vals.length);
  const maxV = Math.round(rawMax);

  const ZONES = [
    { lo:0,    hi:0.50, color:'#5ac8fa', name:'Easy' },
    { lo:0.50, hi:0.60, color:'#30d158', name:'Fat Burn' },
    { lo:0.60, hi:0.70, color:'#ffd60a', name:'Aerobic' },
    { lo:0.70, hi:0.85, color:'#ff9f0a', name:'Tempo' },
    { lo:0.85, hi:1.01, color:'#ff375f', name:'Peak' },
  ];
  const getZone = bpm => ZONES.find(z => (bpm/maxRef) >= z.lo && (bpm/maxRef) < z.hi) || ZONES[4];

  const pts = data.map((d, i) => ({
    x: pad.l + (i / Math.max(data.length-1, 1)) * cw,
    y: pad.t + ch - ((+d.value - mn) / rng) * ch,
    v: +d.value,
    t: d.time,
  }));

  function rrect(x, y, rw, rh, r) {
    cx.beginPath();
    cx.moveTo(x+r, y);
    cx.lineTo(x+rw-r, y); cx.quadraticCurveTo(x+rw, y, x+rw, y+r);
    cx.lineTo(x+rw, y+rh-r); cx.quadraticCurveTo(x+rw, y+rh, x+rw-r, y+rh);
    cx.lineTo(x+r, y+rh); cx.quadraticCurveTo(x, y+rh, x, y+rh-r);
    cx.lineTo(x, y+r); cx.quadraticCurveTo(x, y, x+r, y);
    cx.closePath();
  }

  function drawBase() {
    cx.clearRect(0, 0, w, h);

    // Zone bands
    ZONES.forEach(z => {
      const yTop = pad.t + ch - Math.min(1, Math.max(0, (z.hi * maxRef - mn) / rng)) * ch;
      const yBot = pad.t + ch - Math.min(1, Math.max(0, (z.lo * maxRef - mn) / rng)) * ch;
      if (yBot > yTop) { cx.fillStyle = z.color+'1a'; cx.fillRect(pad.l, yTop, cw, yBot-yTop); }
    });

    // Grid
    cx.lineWidth = 1;
    [0, 0.33, 0.67, 1].forEach(f => {
      const y = pad.t + ch * f;
      cx.beginPath(); cx.moveTo(pad.l, y); cx.lineTo(w-pad.r, y);
      cx.strokeStyle = 'rgba(255,255,255,0.05)'; cx.stroke();
      cx.fillStyle = 'rgba(255,255,255,0.28)'; cx.font = '9px -apple-system,sans-serif';
      cx.textAlign = 'right'; cx.textBaseline = 'middle';
      cx.fillText(Math.round(mx - rng*f), pad.l-4, y);
    });

    // Fill
    const grad = cx.createLinearGradient(0, pad.t, 0, pad.t+ch);
    grad.addColorStop(0, C.hr+'40'); grad.addColorStop(1, C.hr+'04');
    cx.beginPath(); cx.moveTo(pts[0].x, pts[0].y);
    for (let i=1; i<pts.length; i++) {
      const cpx = (pts[i-1].x+pts[i].x)/2;
      cx.bezierCurveTo(cpx, pts[i-1].y, cpx, pts[i].y, pts[i].x, pts[i].y);
    }
    cx.lineTo(pts[pts.length-1].x, pad.t+ch); cx.lineTo(pts[0].x, pad.t+ch);
    cx.closePath(); cx.fillStyle = grad; cx.fill();

    // Line
    cx.beginPath(); cx.moveTo(pts[0].x, pts[0].y);
    for (let i=1; i<pts.length; i++) {
      const cpx = (pts[i-1].x+pts[i].x)/2;
      cx.bezierCurveTo(cpx, pts[i-1].y, cpx, pts[i].y, pts[i].x, pts[i].y);
    }
    cx.strokeStyle = C.hr; cx.lineWidth = 1.5; cx.lineJoin = 'round'; cx.stroke();

    // Avg dashed line
    const avgY = pad.t + ch - ((avgV - mn) / rng) * ch;
    cx.beginPath(); cx.moveTo(pad.l, avgY); cx.lineTo(w-pad.r, avgY);
    cx.strokeStyle = 'rgba(255,255,255,0.18)'; cx.lineWidth = 1;
    cx.setLineDash([3,4]); cx.stroke(); cx.setLineDash([]);

    // Max marker
    const maxY = pad.t + ch - ((maxV - mn) / rng) * ch;
    cx.fillStyle = C.hr; cx.font = 'bold 9px -apple-system,sans-serif';
    cx.textAlign = 'left'; cx.textBaseline = 'bottom';
    cx.fillText(`▲ ${maxV}`, pad.l+2, maxY-1);

    // Avg label
    cx.fillStyle = 'rgba(255,255,255,0.35)'; cx.font = '9px -apple-system,sans-serif';
    cx.textAlign = 'right'; cx.textBaseline = 'alphabetic';
    cx.fillText(`avg ${avgV} bpm`, w-pad.r, h-2);
  }

  function drawHover(mouseX) {
    // Nearest point
    const idx = pts.reduce((b, p, i) =>
      Math.abs(p.x - mouseX) < Math.abs(pts[b].x - mouseX) ? i : b, 0);
    const pt = pts[idx];
    const zone = getZone(pt.v);

    // Vertical crosshair
    cx.beginPath(); cx.moveTo(pt.x, pad.t); cx.lineTo(pt.x, pad.t+ch);
    cx.strokeStyle = 'rgba(255,255,255,0.22)'; cx.lineWidth = 1;
    cx.setLineDash([2,3]); cx.stroke(); cx.setLineDash([]);

    // Horizontal crosshair
    cx.beginPath(); cx.moveTo(pad.l, pt.y); cx.lineTo(w-pad.r, pt.y);
    cx.strokeStyle = 'rgba(255,255,255,0.1)'; cx.lineWidth = 1;
    cx.setLineDash([2,3]); cx.stroke(); cx.setLineDash([]);

    // Outer glow ring
    cx.beginPath(); cx.arc(pt.x, pt.y, 6, 0, Math.PI*2);
    cx.fillStyle = zone.color+'44'; cx.fill();
    // Coloured dot
    cx.beginPath(); cx.arc(pt.x, pt.y, 4, 0, Math.PI*2);
    cx.fillStyle = zone.color; cx.fill();
    // White centre
    cx.beginPath(); cx.arc(pt.x, pt.y, 2, 0, Math.PI*2);
    cx.fillStyle = '#fff'; cx.fill();

    // Elapsed time
    let elapsed = '';
    if (pt.t && pts[0].t) {
      const ms = new Date(pt.t) - new Date(pts[0].t);
      const m = Math.floor(ms/60000), s = Math.floor((ms%60000)/1000);
      elapsed = `+${m}:${String(s).padStart(2,'0')}`;
    }

    // Tooltip dimensions
    const bpmStr = `${Math.round(pt.v)} bpm`;
    cx.font = 'bold 13px -apple-system,sans-serif';
    const bpmW = cx.measureText(bpmStr).width;
    cx.font = '10px -apple-system,sans-serif';
    const subW = cx.measureText(zone.name).width + (elapsed ? cx.measureText(elapsed).width + 14 : 0);
    const tipW = Math.max(bpmW, subW) + 20;
    const tipH = 40;

    let tx = pt.x - tipW/2;
    if (tx < pad.l) tx = pad.l;
    if (tx + tipW > w - 4) tx = w - 4 - tipW;
    const ty = pt.y - tipH - 12 < pad.t ? pt.y + 10 : pt.y - tipH - 12;

    // Bubble background
    cx.fillStyle = 'rgba(12,12,22,0.93)';
    rrect(tx, ty, tipW, tipH, 7); cx.fill();
    cx.strokeStyle = zone.color+'55'; cx.lineWidth = 1;
    rrect(tx, ty, tipW, tipH, 7); cx.stroke();

    // BPM
    cx.fillStyle = zone.color;
    cx.font = 'bold 13px -apple-system,sans-serif';
    cx.textAlign = 'left'; cx.textBaseline = 'top';
    cx.fillText(bpmStr, tx+10, ty+7);

    // Zone name + elapsed
    cx.fillStyle = 'rgba(240,240,248,0.45)';
    cx.font = '10px -apple-system,sans-serif';
    cx.textAlign = 'left'; cx.textBaseline = 'top';
    cx.fillText(zone.name, tx+10, ty+23);
    if (elapsed) {
      cx.textAlign = 'right';
      cx.fillText(elapsed, tx+tipW-10, ty+23);
    }
  }

  drawBase();

  // Attach hover listeners (clean up previous if canvas reused)
  if (c._hrMove) c.removeEventListener('mousemove', c._hrMove);
  if (c._hrLeave) c.removeEventListener('mouseleave', c._hrLeave);

  c._hrMove = e => {
    const rect = c.getBoundingClientRect();
    const mouseX = (e.clientX - rect.left) * (w / rect.width);
    if (mouseX < pad.l || mouseX > w-pad.r) { drawBase(); return; }
    drawBase();
    drawHover(mouseX);
  };
  c._hrLeave = () => drawBase();

  c.addEventListener('mousemove', c._hrMove);
  c.addEventListener('mouseleave', c._hrLeave);
  c.style.cursor = 'crosshair';
}

// ── Workout GPS map — pace-coloured canvas ────────────────────────────────────
function drawRouteMap(canvasId, points, elevCanvasId) {
  const c = $(canvasId);
  if (!c || !points || points.length < 2) return;
  const dpr = window.devicePixelRatio || 1;
  const W   = c.offsetWidth || 340;
  const H   = 200;
  c.width   = W * dpr; c.height = H * dpr;
  c.style.width = W + 'px'; c.style.height = H + 'px';
  const ctx = c.getContext('2d');
  ctx.scale(dpr, dpr);

  const lats = points.map(p => +p.lat), lons = points.map(p => +p.lon);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const minLon = Math.min(...lons), maxLon = Math.max(...lons);
  const latR = maxLat - minLat || 0.001, lonR = maxLon - minLon || 0.001;
  const PAD  = 22;
  const cw   = W - PAD*2, ch = H - PAD*2;
  const sc   = Math.min(cw / lonR, ch / latR);
  const ox   = (cw - lonR*sc)/2, oy = (ch - latR*sc)/2;
  const nx   = lon => PAD + ox + (lon - minLon)*sc;
  const ny   = lat => PAD + oy + (maxLat - lat)*sc;

  // Background
  ctx.fillStyle = '#0b0f1a';
  ctx.beginPath(); ctx.roundRect(0, 0, W, H, 10); ctx.fill();

  // Subtle grid lines
  ctx.strokeStyle = 'rgba(255,255,255,0.03)'; ctx.lineWidth = 1;
  [1,2,3].forEach(i => {
    const gx = PAD + i*cw/4, gy = PAD + i*ch/4;
    ctx.beginPath(); ctx.moveTo(gx, PAD); ctx.lineTo(gx, H-PAD); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(PAD, gy); ctx.lineTo(W-PAD, gy); ctx.stroke();
  });

  // Pace calculation per segment (min/km)
  function haversineKm(la1, lo1, la2, lo2) {
    const R = 6371, dLa = (la2-la1)*Math.PI/180, dLo = (lo2-lo1)*Math.PI/180;
    const a = Math.sin(dLa/2)**2 + Math.cos(la1*Math.PI/180)*Math.cos(la2*Math.PI/180)*Math.sin(dLo/2)**2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
  }
  const rawPaces = points.slice(1).map((p,i) => {
    const dt = p.time && points[i].time ? (new Date(p.time)-new Date(points[i].time))/60000 : 0;
    const km = haversineKm(+points[i].lat, +points[i].lon, +p.lat, +p.lon);
    return (km > 0.0001 && dt > 0 && dt < 5) ? dt/km : null;
  });
  // Smooth with a 5-sample running average
  const pSmooth = rawPaces.map((_,i) => {
    const win = rawPaces.slice(Math.max(0,i-2), i+3).filter(v=>v!==null);
    return win.length ? win.reduce((a,b)=>a+b,0)/win.length : null;
  });
  const valid  = pSmooth.filter(p => p !== null && p > 2 && p < 20);
  const pFast  = valid.length ? valid.slice().sort((a,b)=>a-b)[Math.floor(valid.length*0.05)] : 4;
  const pSlow  = valid.length ? valid.slice().sort((a,b)=>a-b)[Math.floor(valid.length*0.95)] : 8;

  function paceColor(p) {
    if (p === null) return 'rgba(94,142,247,0.7)';
    const t = Math.max(0, Math.min(1, (p - pFast) / ((pSlow - pFast) || 1)));
    // fast=green → mid=yellow → slow=red
    if (t < 0.5) {
      const u = t * 2;
      return `rgb(${Math.round(48+207*u)},${Math.round(209+5*u)},${Math.round(88-88*u)})`;
    }
    const u = (t-0.5)*2;
    return `rgb(255,${Math.round(214-214*u)},0)`;
  }

  // Draw pace-coloured route
  ctx.lineWidth = 2.5; ctx.lineCap = 'round';
  points.slice(1).forEach((p, i) => {
    ctx.strokeStyle = paceColor(pSmooth[i]);
    ctx.beginPath();
    ctx.moveTo(nx(+points[i].lon), ny(+points[i].lat));
    ctx.lineTo(nx(+p.lon),         ny(+p.lat));
    ctx.stroke();
  });

  // Start marker (green glow)
  const p0 = points[0], pe = points[points.length-1];
  ctx.shadowColor = '#30d158'; ctx.shadowBlur = 10;
  ctx.fillStyle = '#30d158';
  ctx.beginPath(); ctx.arc(nx(+p0.lon), ny(+p0.lat), 5, 0, Math.PI*2); ctx.fill();
  ctx.shadowBlur = 0;
  // End marker (red glow)
  ctx.shadowColor = '#ff375f'; ctx.shadowBlur = 10;
  ctx.fillStyle = '#ff375f';
  ctx.beginPath(); ctx.arc(nx(+pe.lon), ny(+pe.lat), 5, 0, Math.PI*2); ctx.fill();
  ctx.shadowBlur = 0;

  // Pace legend (bottom-left)
  const fmtPace = p => `${Math.floor(p)}:${String(Math.round((p%1)*60)).padStart(2,'0')}`;
  const grad = ctx.createLinearGradient(PAD, 0, PAD+80, 0);
  grad.addColorStop(0, '#30d158'); grad.addColorStop(0.5, '#ffd60a'); grad.addColorStop(1, '#ff375f');
  ctx.fillStyle = grad;
  ctx.beginPath(); ctx.roundRect(PAD, H-PAD-14, 80, 5, 2); ctx.fill();
  ctx.fillStyle = 'rgba(255,255,255,0.4)';
  ctx.font = '8px -apple-system,sans-serif';
  ctx.textAlign = 'left';  ctx.textBaseline = 'bottom';
  ctx.fillText(`${fmtPace(pFast)}/km`, PAD, H-PAD-17);
  ctx.textAlign = 'right';
  ctx.fillText(`${fmtPace(pSlow)}/km`, PAD+80, H-PAD-17);

  if (elevCanvasId) drawElevationProfile(elevCanvasId, points);
}

// ── Elevation profile strip ───────────────────────────────────────────────────
function drawElevationProfile(canvasId, points) {
  const c = $(canvasId);
  if (!c) return;
  const alts = points.map(p => +p.alt).filter(a => !isNaN(a) && a > -500 && a < 9000);
  if (alts.length < 2) { c.style.display='none'; return; }
  const dpr = window.devicePixelRatio || 1;
  const W   = c.offsetWidth || 340;
  const H   = 52;
  c.width   = W * dpr; c.height = H * dpr;
  c.style.width = W+'px'; c.style.height = H+'px';
  const ctx = c.getContext('2d'); ctx.scale(dpr, dpr);
  const PAD = {l:2, r:2, t:5, b:18};
  const cw  = W - PAD.l - PAD.r, ch = H - PAD.t - PAD.b;
  const mn  = Math.min(...alts), mx = Math.max(...alts);
  const rng = mx - mn || 1;
  const xOf = i => PAD.l + (i/(alts.length-1))*cw;
  const yOf = a => PAD.t + ch - ((a-mn)/rng)*ch;

  // Fill
  const grad = ctx.createLinearGradient(0, PAD.t, 0, H-PAD.b);
  grad.addColorStop(0, 'rgba(255,149,0,0.35)'); grad.addColorStop(1, 'rgba(255,149,0,0.0)');
  ctx.fillStyle = grad;
  ctx.beginPath(); ctx.moveTo(xOf(0), H-PAD.b);
  alts.forEach((a,i) => ctx.lineTo(xOf(i), yOf(a)));
  ctx.lineTo(xOf(alts.length-1), H-PAD.b); ctx.closePath(); ctx.fill();

  // Line
  ctx.strokeStyle = '#ff9f0a'; ctx.lineWidth = 1.5; ctx.lineJoin = 'round';
  ctx.beginPath();
  alts.forEach((a,i) => i ? ctx.lineTo(xOf(i),yOf(a)) : ctx.moveTo(xOf(i),yOf(a)));
  ctx.stroke();

  // Gain annotation
  let gain = 0;
  for (let i = 1; i < alts.length; i++) if (alts[i] > alts[i-1]) gain += alts[i]-alts[i-1];
  ctx.fillStyle = 'rgba(255,255,255,0.3)'; ctx.font = '9px -apple-system,sans-serif';
  ctx.textAlign = 'left'; ctx.textBaseline = 'bottom';
  ctx.fillText(`↑ ${Math.round(gain)} m gain  ·  ${Math.round(mn)}–${Math.round(mx)} m alt`, PAD.l+4, H-2);
  ctx.textAlign = 'right';
  ctx.fillText('Elevation', W-PAD.r-4, H-2);

  // Return gain for stat display
  return Math.round(gain);
}

// ── HR Zone breakdown ────────────────────────────────────────────────────────
function renderHRZones(el, vals, workoutMax) {
  if (!el || !vals.length) return;
  // Estimate true max HR: assume workout peaked at ~90% of real max
  const maxRef = Math.round(Math.max(workoutMax * 1.11, workoutMax + 12));
  const ZONES = [
    { n:'Z1', label:'Easy',     color:'#5ac8fa', lo:0,    hi:0.50 },
    { n:'Z2', label:'Fat Burn', color:'#30d158', lo:0.50, hi:0.60 },
    { n:'Z3', label:'Aerobic',  color:'#ffd60a', lo:0.60, hi:0.70 },
    { n:'Z4', label:'Tempo',    color:'#ff9f0a', lo:0.70, hi:0.85 },
    { n:'Z5', label:'Peak',     color:'#ff375f', lo:0.85, hi:1.01 },
  ];
  const counts = ZONES.map(() => 0);
  vals.forEach(v => {
    const pct = v / maxRef;
    const i = ZONES.findIndex(z => pct >= z.lo && pct < z.hi);
    if (i >= 0) counts[i]++;
  });
  const total = vals.length;
  const pcts  = counts.map(c => c / total);
  // Approximate time per zone assuming ~5s per sample
  const secPerSample = 5;
  const fmtTime = secs => secs >= 60
    ? `${Math.floor(secs/60)}m${secs%60 ? ' '+secs%60+'s' : ''}`
    : `${secs}s`;
  const timeStrs = counts.map(c => fmtTime(Math.round(c * secPerSample)));

  el.innerHTML = `<div class="wo-zones">
    <div class="wo-zones-lbl">HR Zones <span class="wo-zones-maxhr">est. max ${maxRef} bpm</span></div>
    <div class="wo-zone-bar">${ZONES.map((z,i) => pcts[i]>0.005
      ? `<div class="wo-zone-seg" style="flex:${pcts[i]};background:${z.color};opacity:0.82"></div>`
      : '').join('')}</div>
    <div class="wo-zone-legend">${ZONES.map((z,i) => `
      <div class="wo-zone-item">
        <div class="wo-zone-dot" style="background:${z.color}"></div>
        <span>${z.n} ${z.label}</span>
        <span class="wo-zone-time">${Math.round(pcts[i]*100)}%${counts[i]>0?' · '+timeStrs[i]:''}</span>
      </div>`).join('')}
    </div>
  </div>`;
}

// ── Load workout detail (HR chart + route) ────────────────────────────────────
async function loadWoDetail(el, idx) {
  const start    = el.dataset.start;
  const end      = el.dataset.end || start;
  const activity = el.dataset.activity || '';

  // Compute end time: if no end stored, use start + 2h as upper bound
  const endParam = end && end !== start ? end
    : new Date(new Date(start).getTime() + 2*3600*1000).toISOString();

  const hrData = await get(
    `/api/workout-hr?start=${encodeURIComponent(start)}&end=${encodeURIComponent(endParam)}`
  );
  const hrWrap = $(`woHrWrap${idx}`);
  if (hrData && hrData.length >= 2) {
    drawWoHR(`woHrC${idx}`, hrData);
    const vals = hrData.map(d => +d.value).filter(v => !isNaN(v));
    const avgHR = Math.round(vals.reduce((s,v)=>s+v,0)/vals.length);
    const maxHR = Math.round(Math.max(...vals));
    const hrStatAvg = $(`woAvgHR${idx}`);
    if (hrStatAvg) hrStatAvg.innerHTML =
      `<div class="wo-stat-val" style="color:var(--hr)">${avgHR}</div><div class="wo-stat-lbl">Avg HR (bpm)</div>`;
    const hrStatMax = $(`woMaxHR${idx}`);
    if (hrStatMax) hrStatMax.innerHTML =
      `<div class="wo-stat-val" style="color:var(--hr)">${maxHR}</div><div class="wo-stat-lbl">Max HR (bpm)</div>`;
    renderHRZones($(`woZones${idx}`), vals, maxHR);
  } else if (hrWrap) {
    hrWrap.innerHTML = '<div style="font-size:11px;color:var(--muted);padding:8px 0">No HR samples in Apple Health for this workout</div>';
  }

  const ROUTE_ACTS = new Set(['running','indoorrunning','cycling','walking','hiking','skiing','snowboarding']);
  if (ROUTE_ACTS.has(activity)) {
    const route = await get(`/api/workout-route?start=${encodeURIComponent(start)}`);
    const rtWrap = $(`woRtWrap${idx}`);
    if (route && route.length >= 2) {
      // Defer canvas draw one frame so layout is settled after detail opens
      requestAnimationFrame(() => drawRouteMap(`woRtC${idx}`, route, `woElC${idx}`));

      // ── Elevation gain ────────────────────────────────────────────────────
      const alts = route.map(p => +p.alt).filter(a => !isNaN(a) && a > -500 && a < 9000);
      if (alts.length >= 2) {
        let gain = 0;
        for (let i = 1; i < alts.length; i++) if (alts[i] > alts[i-1]) gain += alts[i]-alts[i-1];
        const elevEl = $(`woElev${idx}`);
        if (elevEl) elevEl.innerHTML =
          `<div class="wo-stat-val">${Math.round(gain)}<span style="font-size:10px;font-weight:400"> m</span></div><div class="wo-stat-lbl">Elevation Gain</div>`;
      }

      // ── Per-km splits + best split ────────────────────────────────────────
      function haversineKm(la1,lo1,la2,lo2) {
        const R=6371, dLa=(la2-la1)*Math.PI/180, dLo=(lo2-lo1)*Math.PI/180;
        const a=Math.sin(dLa/2)**2+Math.cos(la1*Math.PI/180)*Math.cos(la2*Math.PI/180)*Math.sin(dLo/2)**2;
        return R*2*Math.atan2(Math.sqrt(a),Math.sqrt(1-a));
      }
      const kmSplits = [];   // pace in min/km per each full km
      let cumDist = 0, kmStart = 0, kmStartDist = 0;
      if (route[0].time) {
        for (let i = 1; i < route.length; i++) {
          cumDist += haversineKm(+route[i-1].lat,+route[i-1].lon,+route[i].lat,+route[i].lon);
          while (cumDist - kmStartDist >= 1.0) {
            const dt = (new Date(route[i].time) - new Date(route[kmStart].time)) / 60000;
            const pac = dt / (cumDist - kmStartDist);
            if (pac > 1.5 && pac < 20) kmSplits.push(pac);
            kmStartDist += 1.0; kmStart = i;
          }
        }
      }
      // Best split stat
      if (kmSplits.length) {
        const bestPace = Math.min(...kmSplits);
        const splitEl = $(`woSplit${idx}`);
        if (splitEl) splitEl.innerHTML =
          `<div class="wo-stat-val">${Math.floor(bestPace)}:${String(Math.round((bestPace%1)*60)).padStart(2,'0')}<span style="font-size:10px;font-weight:400"> /km</span></div><div class="wo-stat-lbl">Best Split</div>`;
      }
      // Per-km splits bar chart
      if (kmSplits.length >= 2) {
        const slowest = Math.max(...kmSplits), fastest = Math.min(...kmSplits);
        const range = slowest - fastest || 1;
        const splitsEl = $(`woSplits${idx}`);
        if (splitsEl) {
          const paceStr = p => `${Math.floor(p)}:${String(Math.round((p%1)*60)).padStart(2,'0')}`;
          const hue = p => {
            const t = (p - fastest) / range;  // 0 = fast (green), 1 = slow (red)
            const r = Math.round(t*255), g = Math.round((1-t)*255);
            return `rgb(${r},${g},60)`;
          };
          splitsEl.innerHTML = `<div class="wo-splits-title">Km Splits</div>` +
            kmSplits.map((p,i) => {
              const w = Math.round((1-(p-fastest)/range)*100);
              return `<div class="wo-split-row">
                <div class="wo-split-lbl">km ${i+1}</div>
                <div class="wo-split-bar-track"><div class="wo-split-bar" style="width:${w}%;background:${hue(p)}"></div></div>
                <div class="wo-split-pace">${paceStr(p)}</div>
              </div>`;
            }).join('');
        }
      }
    } else {
      // No GPS data returned — show message, hide canvas
      const noGps = $(`woNoGps${idx}`);
      const canvas = $(`woRtC${idx}`);
      const elevC  = $(`woElC${idx}`);
      if (noGps) noGps.style.display = '';
      if (canvas) canvas.style.display = 'none';
      if (elevC)  elevC.style.display  = 'none';
    }
  }
}

function renderWorkouts(data) {
  const el = $('woList');
  if (!data || !data.length) { el.innerHTML='<div class="empty">No workouts in this period</div>'; return; }

  const ROUTE_ACTS = new Set(['running','cycling','walking','hiking','skiing','snowboarding']);

  const rows = data.slice(0, 20).map((w, idx) => {
    const key  = (w.activity||'').toLowerCase().replace(/[\s_]/g,'');
    const icon = woIcon(key);
    const name = woName(w.activity);
    const dur  = w.duration  ? Math.round(w.duration) + 'm'          : '';
    const cals = w.calories  ? Math.round(w.calories) + ' kcal'      : '';
    const dist = w.distance_km ? (+w.distance_km).toFixed(2) + ' km' : '';
    const pace = (w.duration && w.distance_km && w.distance_km > 0)
                   ? Math.floor(w.duration / w.distance_km) + ':' +
                     String(Math.round((w.duration / w.distance_km % 1) * 60)).padStart(2,'0') + ' /km'
                   : '';
    const isRunAct   = key === 'running' || key === 'indoorrunning';
    const isOutdoor  = !!w.has_route;                     // true = GPS exists
    const isIndoor   = key === 'indoorrunning' || (isRunAct && !isOutdoor);
    const canRoute   = ROUTE_ACTS.has(key);               // always render route section
    const typeBadge  = isRunAct
      ? (isIndoor
          ? `<span class="wo-type-badge wo-badge-indoor">Treadmill</span>`
          : `<span class="wo-type-badge wo-badge-outdoor">Outdoor</span>`)
      : '';

    const stats = [
      dur   && `<div class="wo-stat"><div class="wo-stat-val">${dur}</div><div class="wo-stat-lbl">Duration</div></div>`,
      dist  && `<div class="wo-stat"><div class="wo-stat-val">${dist}</div><div class="wo-stat-lbl">Distance</div></div>`,
      cals  && `<div class="wo-stat"><div class="wo-stat-val">${cals}</div><div class="wo-stat-lbl">Calories</div></div>`,
      pace  && `<div class="wo-stat"><div class="wo-stat-val">${pace}</div><div class="wo-stat-lbl">Pace</div></div>`,
      `<div class="wo-stat" id="woAvgHR${idx}"></div>`,
      `<div class="wo-stat" id="woMaxHR${idx}"></div>`,
      isRunAct && `<div class="wo-stat" id="woSplit${idx}"></div>`,
      w.source && `<div class="wo-stat"><div class="wo-stat-val" style="font-size:11px;font-weight:400">${w.source.replace('_',' ')}</div><div class="wo-stat-lbl">Source</div></div>`,
    ].filter(Boolean).join('');

    const safeStart = encodeURIComponent(w.recorded_at||'');
    const safeEnd   = encodeURIComponent(w.end||w.recorded_at||'');

    return `<div class="wo" onclick="toggleWo(this,${idx})"
        data-start="${w.recorded_at||''}" data-end="${w.end||''}" data-activity="${key}" data-idx="${idx}">
      <div class="wo-main">
        <div class="wo-left">
          <div class="wo-icon">${icon}</div>
          <div>
            <div class="wo-name">${name}${typeBadge}</div>
            <div class="wo-date">${fmtDateLong(w.date)}${w.time?' · '+w.time.slice(0,5):''}</div>
          </div>
        </div>
        <div class="wo-right">
          <div class="wo-dur">${dur}</div>
          ${pace ? `<div class="wo-cal">${pace}</div>` : dist ? `<div class="wo-cal">${dist}</div>` : cals ? `<div class="wo-cal">${cals}</div>` : ''}
          <span class="wo-chev">›</span>
        </div>
      </div>
      <div class="wo-detail">
        <div class="wo-detail-inner">
          ${stats}
          ${canRoute ? `<div class="wo-stat" id="woElev${idx}"></div>` : ''}
        </div>
        <div class="wo-detail-charts">
          <div class="wo-hr-chart" id="woHrWrap${idx}">
            <div class="wo-hr-lbl">Heart Rate Trend</div>
            <canvas id="woHrC${idx}" height="120"></canvas>
          </div>
        </div>
        <div id="woZones${idx}"></div>
        ${canRoute ? `<div class="wo-route-section" id="woRtWrap${idx}">
          <div class="wo-route-lbl">GPS Route</div>
          <canvas id="woRtC${idx}" class="wo-route-canvas"></canvas>
          <canvas id="woElC${idx}" class="wo-elev-canvas"></canvas>
          <div id="woNoGps${idx}" class="wo-no-gps" style="display:none">No GPS data for this workout</div>
          <div id="woSplits${idx}" class="wo-splits"></div>
        </div>` : ''}
      </div>
    </div>`;
  });

  el.innerHTML = '<div class="wo-list">' + rows.join('') + '</div>';

  // ── Activity breakdown bars ──────────────────────────────────────────
  const counts = {};
  data.forEach(w => {
    const name = woName(w.activity);
    counts[name] = (counts[name] || 0) + 1;
  });
  const sorted = Object.entries(counts).sort((a,b)=>b[1]-a[1]).slice(0, 5);
  const maxC = sorted[0]?.[1] || 1;
  const barColors = ['#ff9f0a','#bf5af2','#32ade6','#30d158','#ff375f'];
  const breakdown = $('actBreakdown');
  if (breakdown && sorted.length > 1) {
    breakdown.innerHTML = `
      <div class="act-breakdown">
        <div class="act-breakdown-title">Activity Mix</div>
        ${sorted.map(([name, count], i) => `
          <div class="act-bar-row">
            <div class="act-bar-label">${name}</div>
            <div class="act-bar-track">
              <div class="act-bar-fill" style="width:${Math.round(count/maxC*100)}%;background:${barColors[i%barColors.length]}"></div>
            </div>
            <div class="act-bar-pct">${count}</div>
          </div>`).join('')}
      </div>`;
  } else if (breakdown) {
    breakdown.innerHTML = '';
  }
}

// ── API loaders ───────────────────────────────────────────────────────────────
async function get(path) {
  try { const r=await fetch(path); return r.ok ? r.json() : null; }
  catch { return null; }
}

function trendBadge(pct, higherIsBetter) {
  if (pct == null || Math.abs(pct) < 1.0)
    return '<span class="trend trend-stable">— stable</span>';
  const improving = higherIsBetter ? pct > 0 : pct < 0;
  const arrow = pct > 0 ? '↑' : '↓';
  const cls = improving ? 'trend-improve' : 'trend-decline';
  return `<span class="trend ${cls}">${arrow} ${Math.abs(pct).toFixed(1)}%</span>`;
}

async function loadSummary() {
  const d = await get('/api/summary');
  if (!d) return;

  // Source badges
  const SRC = {apple_health:'❤️ Apple Health', whoop:'⌚ Whoop', oura:'💍 Oura', fitbit:'📊 Fitbit'};
  $('badges').innerHTML = (d.sources||[]).map(s=>`<span class="badge">${SRC[s]||s}</span>`).join('');
  $('sync').textContent = d.last_recorded ? `Last record: ${fmtDate(d.last_recorded)}` : 'No data yet';

  // Stat cards — priority order, only shown if value exists
  const stats = [
    {label:'Resting HR',    icon:'❤️',  val:d.resting_hr,    trend:d.resting_hr_trend, hb:false, unit:'bpm',   dec:0, col:'var(--hr)'},
    {label:'HRV',           icon:'📊',  val:d.hrv,           trend:d.hrv_trend,        hb:true,  unit:' ms',   dec:0, col:'var(--hrv)'},
    {label:'Blood Oxygen',  icon:'🫁',  val:d.spo2,          trend:d.spo2_trend,       hb:true,  unit:'%',     dec:1, col:'var(--spo2)'},
    {label:'Avg Sleep',     icon:'🌙',  val:d.sleep_hours,   trend:d.sleep_trend,      hb:true,  unit:' hrs',  dec:1, col:'var(--sleep-deep)'},
    {label:'Respiration',   icon:'💨',  val:d.resp_rate,     trend:d.resp_trend,       hb:false, unit:' br/m', dec:1, col:'var(--resp)'},
    {label:'Whoop Recovery',icon:'⚡',  val:d.whoop_recovery,trend:null,               hb:true,  unit:'%',     dec:0, col:'var(--rec)'},
    {label:'Oura Readiness',icon:'💍',  val:d.oura_readiness,trend:null,               hb:true,  unit:'',      dec:0, col:'var(--read)'},
    {label:'Whoop Strain',  icon:'🔥',  val:d.whoop_strain,  trend:null,               hb:false, unit:' / 21', dec:1, col:'var(--strain)'},
  ].filter(s=>s.val!=null);

  $('statsRow').innerHTML = stats.map(s=>
    `<div class="stat" style="--stat-col:${s.col}">
       <div class="stat-hdr">
         <span class="stat-icon">${s.icon}</span>
         <span class="stat-label">${s.label}</span>
       </div>
       <div class="stat-val" style="color:${s.col}" data-val="${s.val}" data-dec="${s.dec}">
         —<span class="stat-unit">${s.unit}</span>
       </div>
       <div class="stat-ftr">
         ${trendBadge(s.trend, s.hb)}
         <span class="stat-sub">vs 30d avg</span>
       </div>
     </div>`
  ).join('');

  // Animate counters
  $('statsRow').querySelectorAll('.stat-val').forEach(el=>{
    const v = el.dataset.val, dec = +el.dataset.dec;
    const unit = el.querySelector('.stat-unit').textContent;
    el.innerHTML = '<span class="num">0</span><span class="stat-unit">'+unit+'</span>';
    countUp(el.querySelector('.num'), +v, dec);
  });
}

async function loadBloodOxygen() {
  const d = await get(`/api/blood-oxygen?days=${D.spo2}`);
  cache.spo2 = d;
  const card = $('spo2Card');
  if (!d||!d.length) { if(card) card.style.display='none'; return; }
  if(card) card.style.display='';
  const a = avg(d.map(r=>r.value).filter(v=>v));
  const el = $('spo2Val'); if(el) countUp(el, a, 1);
  drawLine('spo2C','spo2O', d, {color:C.spo2, unit:'%', minY:90, maxY:100});
  const wrap = $('spo2C').parentElement;
  attachHover(wrap,'spo2C','spo2O', d=>({val:fmt(d.value,1)+'%', sub:'Blood Oxygen'}));
}

async function loadRespiration() {
  const d = await get(`/api/respiration?days=${D.resp}`);
  cache.resp = d;
  const card = $('respCard');
  if (!d||!d.length) { if(card) card.style.display='none'; return; }
  if(card) card.style.display='';
  const a = avg(d.map(r=>r.value).filter(v=>v));
  const el = $('respVal'); if(el) countUp(el, a, 1);
  drawLine('respC','respO', d, {color:C.resp, unit:' br/min'});
  const wrap = $('respC').parentElement;
  attachHover(wrap,'respC','respO', d=>({val:fmt(d.value,1)+' br/min', sub:'Respiration Rate'}));
}

function drawHRBand(mainId, overlayId, data) {
  const m = ctx2d(mainId);
  if (!m) return;
  const {cx, w, h} = m;
  const pad = {t:12, r:12, b:26, l:38};
  const cw = w - pad.l - pad.r;
  const ch = h - pad.t - pad.b;

  const allVals = data.flatMap(d=>[d.min, d.max]).filter(v=>v!=null);
  if (!allVals.length) return;

  const yMin = Math.min(...allVals) * 0.96;
  const yMax = Math.max(...allVals) * 1.04;
  const yRange = yMax - yMin || 1;

  const xOf = i => pad.l + (i / Math.max(data.length-1, 1)) * cw;
  const yOf = v => pad.t + ch - ((v-yMin)/yRange)*ch;

  cx.clearRect(0, 0, w, h);

  // Grid
  cx.strokeStyle = 'rgba(255,255,255,0.04)';
  cx.lineWidth = 1;
  for (let i=0; i<=4; i++) {
    const y = pad.t + (ch/4)*i;
    cx.beginPath(); cx.moveTo(pad.l, y); cx.lineTo(w-pad.r, y); cx.stroke();
  }

  // Y labels
  cx.fillStyle = 'rgba(255,255,255,0.28)';
  cx.font = '10px -apple-system,sans-serif';
  cx.textAlign = 'right'; cx.textBaseline = 'middle';
  for (let i=0; i<=2; i++) {
    const v = yMin + (yRange/2)*i;
    cx.fillText(Math.round(v), pad.l-6, pad.t + ch - ((v-yMin)/yRange)*ch);
  }

  // X labels
  cx.textAlign = 'center'; cx.textBaseline = 'alphabetic';
  [0, Math.floor(data.length/2), data.length-1].forEach(i => {
    if (data[i]) cx.fillText(fmtDate(data[i].date), xOf(i), h-4);
  });

  const color = '#ff9f43';

  // Shaded band between min and max
  if (data.some(d=>d.min!=null && d.max!=null)) {
    cx.beginPath();
    let first = true;
    data.forEach((d,i) => {
      if (d.max == null) { first = true; return; }
      if (first) { cx.moveTo(xOf(i), yOf(d.max)); first = false; }
      else cx.lineTo(xOf(i), yOf(d.max));
    });
    [...data].reverse().forEach((d,i) => {
      if (d.min != null) cx.lineTo(xOf(data.length-1-i), yOf(d.min));
    });
    cx.closePath();
    cx.fillStyle = color + '28';
    cx.fill();
  }

  // Avg line
  cx.beginPath();
  let started = false;
  data.forEach((d,i) => {
    if (d.avg == null) { started = false; return; }
    if (!started) { cx.moveTo(xOf(i), yOf(d.avg)); started = true; }
    else cx.lineTo(xOf(i), yOf(d.avg));
  });
  cx.strokeStyle = color;
  cx.lineWidth = 2;
  cx.stroke();

  chartMeta[mainId] = {
    data,
    pts: data.map((d,i) => ({x: xOf(i), y: d.avg != null ? yOf(d.avg) : null})),
    valueKey: 'avg', dateKey: 'date', color, unit: 'bpm', pad, cw, ch
  };
}

async function loadHR() {
  const d = await get(`/api/heart-rate?days=${D.hr}`);
  cache.hr = d;
  const card = $('hrCard');
  if (!d||!d.length) { if(card) card.style.display='none'; return; }
  if(card) card.style.display='';
  const a = avg(d.map(r=>r.avg).filter(v=>v!=null));
  const el=$('hrVal'); if(el) countUp(el, a, 0);
  drawHRBand('hrC','hrO', d);
  const wrap=$('hrC').parentElement;
  attachHover(wrap,'hrC','hrO', r=>({val:fmt(r.avg,0)+' bpm', sub:`min ${fmt(r.min,0)} / max ${fmt(r.max,0)}`}));
}

async function loadHRV() {
  const d = await get(`/api/hrv?days=${D.hrv}`);
  cache.hrv = d;
  if (!d||!d.length) return;
  const a = avg(d.map(r=>r.value).filter(v=>v));
  const el = $('hrvVal'); if(el) countUp(el, a, 0);
  drawLine('hrvC','hrvO', d, {color:C.hrv, unit:'ms', minY:0});
  const wrap = $('hrvC').parentElement;
  attachHover(wrap,'hrvC','hrvO', d=>({val:fmt(d.value,1)+' ms', sub:d.source||''}));
}

async function loadVO2Max() {
  const d = await get(`/api/vo2max?days=${D.vo2max}`);
  cache.vo2max = d;
  if (!d||!d.length) return;
  $('vo2Card').style.display = '';
  const latest = d[d.length-1]?.value;
  const el = $('vo2Val'); if(el && latest) countUp(el, latest, 1);
  drawLine('vo2C','vo2O', d, {color:C.vo2, unit:' mL/min·kg', minY:25, maxY:65});
  const wrap = $('vo2C').parentElement;
  attachHover(wrap,'vo2C','vo2O', r=>({val:fmt(r.value,1)+' mL/min·kg', sub:'VO2 Max'}));
  // Fitness zone label
  const zone = $('vo2Zone');
  if (zone && latest) {
    const [label, col] = latest >= 55 ? ['Excellent fitness', '#30d158']
                       : latest >= 47 ? ['Good fitness',      '#34c759']
                       : latest >= 40 ? ['Average fitness',   '#ff9f0a']
                       : latest >= 35 ? ['Below average',     '#ff6b35']
                       :                ['Poor fitness',      '#ff375f'];
    zone.innerHTML = `Fitness level: <span style="color:${col};font-weight:600">${label}</span>
      &nbsp;·&nbsp; Latest reading: <strong>${latest}</strong> mL/min·kg`;
  }
}

async function loadRHR() {
  const d = await get(`/api/resting-hr?days=${D.rhr}`);
  cache.rhr = d;
  if (!d||!d.length) return;
  const a = avg(d.map(r=>r.value).filter(v=>v));
  const el = $('rhrVal'); if(el) countUp(el, a, 0);
  drawLine('rhrC','rhrO', d, {color:'#ff6b6b',unit:'bpm'});
  const wrap = $('rhrC').parentElement;
  attachHover(wrap,'rhrC','rhrO', d=>({val:fmt(d.value,0)+' bpm',sub:'Resting HR'}));
}

function attachSleepHover(data) {
  const canvas = $('slC');
  const overlay = $('slO');
  if (!canvas || !overlay) return;
  const nights = data.slice(-Math.min(30, data.length));
  const wrap = canvas.parentElement;

  // ── Stage config ─────────────────────────────────────────────────────────
  // Covers both pre-iOS-16 names (deep/rem/core) and post-iOS-16 names
  // (asleepdeep / asleeprem / asleepcore / asleepunspecified).
  const STAGE_ROW = {
    awake:0,
    rem:1,  asleeprem:1,
    core:2, light:2, asleep:2, in_bed:2, asleepcore:2, asleepunspecified:2,
    deep:3, asleepdeep:3,
  };
  const STAGE_COLOR = {
    awake:'#ff6b6b',
    rem:'#32ade6',   asleeprem:'#32ade6',
    core:'#5e8ef7',  light:'#5e8ef7', asleep:'#5e8ef7', in_bed:'#5e8ef7',
    asleepcore:'#5e8ef7', asleepunspecified:'#5e8ef7',
    deep:'#5e5ce6',  asleepdeep:'#5e5ce6',
  };
  const ROW_LABELS  = ['Awake','REM','Core','Deep'];
  const ROW_COLORS  = ['#ff6b6b','#32ade6','#5e8ef7','#5e5ce6'];
  // Totals footer colours (match aggregated keys in the nightly data)
  const TOTAL_COLS  = [
    { key:'deep',  color:'#5e5ce6', label:'Deep'  },
    { key:'rem',   color:'#32ade6', label:'REM'   },
    { key:'light', color:'#5e8ef7', label:'Core'  },
    { key:'awake', color:'#ff6b6b', label:'Awake' },
  ];

  const stagesCache = {};  // date → segments[] | null (pending)
  let currentIdx = -1;

  function getIdx(e) {
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX - rect.left) / rect.width * (canvas.width / (window.devicePixelRatio||1));
    const cw = (canvas.width / (window.devicePixelRatio||1)) - 36 - 10;
    const barW = (cw - (nights.length-1)*3) / nights.length;
    return Math.floor((mx - 36) / (barW + 3));
  }

  function rrect(ctx, x, y, rw, rh, r) {
    r = Math.min(r, rw/2, rh/2);
    ctx.beginPath();
    ctx.moveTo(x+r, y);
    ctx.lineTo(x+rw-r, y); ctx.quadraticCurveTo(x+rw, y, x+rw, y+r);
    ctx.lineTo(x+rw, y+rh-r); ctx.quadraticCurveTo(x+rw, y+rh, x+rw-r, y+rh);
    ctx.lineTo(x+r, y+rh); ctx.quadraticCurveTo(x, y+rh, x, y+rh-r);
    ctx.lineTo(x, y+r); ctx.quadraticCurveTo(x, y, x+r, y);
    ctx.closePath();
  }

  function hm(v) {
    const h = Math.floor(v), m = Math.round((v - h) * 60);
    return m > 0 ? `${h}h ${m}m` : `${h}h`;
  }

  function fmt12(iso) {
    const d = new Date(iso);
    let h = d.getHours(), m = d.getMinutes();
    const ap = h >= 12 ? 'PM' : 'AM';
    h = h % 12 || 12;
    return `${h}${m ? ':'+String(m).padStart(2,'0') : ''}${ap}`;
  }

  function draw(idx, segs) {
    const dpr = window.devicePixelRatio || 1;
    const W   = overlay.offsetWidth  || canvas.offsetWidth  || 600;
    const H   = overlay.offsetHeight || canvas.offsetHeight || 150;
    overlay.width  = W * dpr; overlay.height = H * dpr;
    overlay.style.width = W+'px'; overlay.style.height = H+'px';
    const ctx = overlay.getContext('2d'); ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, W, H);
    if (idx < 0 || idx >= nights.length) return;

    // ── Highlight hovered bar on main chart ────────────────────────────────
    const PAD  = {l:36, r:10, t:10, b:26};
    const cw   = W - PAD.l - PAD.r, ch = H - PAD.t - PAD.b;
    const barW = (cw - (nights.length-1)*3) / nights.length;
    const barX = PAD.l + idx*(barW+3);
    ctx.fillStyle = 'rgba(255,255,255,0.07)';
    rrect(ctx, barX-1, PAD.t, barW+2, ch, 3); ctx.fill();

    const n = nights[idx];
    const sleepH = (n.deep||0) + (n.rem||0) + (n.light||0);

    // ── Card geometry ──────────────────────────────────────────────────────
    const CW = 350, CH = 210;
    const barCx = barX + barW / 2;
    let cx0 = barCx > W / 2 ? barX - CW - 6 : barX + barW + 6;
    cx0 = Math.max(4, Math.min(cx0, W - CW - 4));
    const cy0 = Math.max(4, Math.min(PAD.t + (ch - CH) / 2, H - CH - 4));

    // Card background + border
    ctx.fillStyle = 'rgba(8,8,20,0.97)';
    rrect(ctx, cx0, cy0, CW, CH, 12); ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,0.09)'; ctx.lineWidth = 1;
    rrect(ctx, cx0, cy0, CW, CH, 12); ctx.stroke();

    // ── Header ─────────────────────────────────────────────────────────────
    ctx.fillStyle = 'rgba(255,255,255,0.38)';
    ctx.font = '10px -apple-system,sans-serif';
    ctx.textAlign = 'left'; ctx.textBaseline = 'top';
    ctx.fillText(fmtDateLong(n.date), cx0+13, cy0+11);
    ctx.fillStyle = '#fff';
    ctx.font = 'bold 16px -apple-system,sans-serif';
    ctx.fillText(hm(sleepH) + ' sleep', cx0+13, cy0+25);

    // ── Hypnogram area ─────────────────────────────────────────────────────
    const HP = { l: cx0+54, r: cx0+CW-14, t: cy0+52, b: cy0+CH-36 };
    const HW = HP.r - HP.l, HH = HP.b - HP.t;
    const rowH = HH / 4;

    if (!segs || !segs.length) {
      // Fallback: proportional stage strip (no raw segments available)
      const totalH = sleepH + (n.awake||0) || 1;
      let sx = HP.l;
      ctx.save();
      rrect(ctx, HP.l, HP.t + HH*0.3, HW, HH*0.4, 5); ctx.clip();
      TOTAL_COLS.forEach(c => {
        const sw = ((n[c.key]||0) / totalH) * HW;
        if (sw < 0.5) return;
        ctx.fillStyle = c.color + 'cc';
        ctx.fillRect(sx, HP.t + HH*0.3, sw, HH*0.4); sx += sw;
      });
      ctx.restore();
      ctx.fillStyle = 'rgba(255,255,255,0.2)';
      ctx.font = '10px -apple-system,sans-serif';
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillText('No stage detail in database', HP.l + HW/2, HP.t + HH/2);
    } else {
      // ── Row labels + dashed guide lines ──────────────────────────────────
      ROW_LABELS.forEach((lbl, r) => {
        const ly = HP.t + r * rowH + rowH / 2;
        ctx.setLineDash([2,5]); ctx.strokeStyle = 'rgba(255,255,255,0.07)'; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(HP.l, ly); ctx.lineTo(HP.r, ly); ctx.stroke();
        ctx.setLineDash([]);
        ctx.fillStyle = ROW_COLORS[r];
        ctx.font = 'bold 8px -apple-system,sans-serif';
        ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
        ctx.fillText(lbl, HP.l - 5, ly);
      });

      // ── Time mapping ──────────────────────────────────────────────────────
      const t0    = new Date(segs[0].start).getTime();
      const t1    = new Date(segs[segs.length-1].end).getTime();
      const tSpan = (t1 - t0) || 1;
      const tx    = t => HP.l + ((t - t0) / tSpan) * HW;
      const midY  = r => HP.t + r * rowH + rowH / 2;

      // Bezier connectors between consecutive segments (drawn behind segments)
      ctx.lineWidth = 1.5;
      for (let i = 0; i < segs.length - 1; i++) {
        const s = segs[i], sn = segs[i+1];
        const r  = STAGE_ROW[s.stage]  ?? 2;
        const rn = STAGE_ROW[sn.stage] ?? 2;
        if (r === rn) continue;
        const x1 = tx(new Date(s.end).getTime());
        const x2 = tx(new Date(sn.start).getTime());
        const mx  = (x1 + x2) / 2;
        ctx.strokeStyle = (STAGE_COLOR[s.stage] || '#5e8ef7') + '55';
        ctx.beginPath(); ctx.moveTo(x1, midY(r));
        ctx.bezierCurveTo(mx, midY(r), mx, midY(rn), x2, midY(rn)); ctx.stroke();
      }

      // Segment blocks
      segs.forEach(s => {
        const r     = STAGE_ROW[s.stage] ?? 2;
        const color = STAGE_COLOR[s.stage] || '#5e8ef7';
        const x1    = tx(new Date(s.start).getTime());
        const x2    = tx(new Date(s.end).getTime());
        const segW  = Math.max(x2 - x1, 2);
        const sy    = HP.t + r * rowH + rowH * 0.18;
        const sh    = rowH * 0.64;
        ctx.fillStyle = color + 'cc';
        rrect(ctx, x1, sy, segW, sh, Math.min(3, segW/2)); ctx.fill();
        // Bright top highlight
        ctx.fillStyle = color;
        ctx.fillRect(x1, sy, segW, Math.min(2, sh));
      });

      // Time axis labels
      ctx.fillStyle = 'rgba(255,255,255,0.25)';
      ctx.font = '8px -apple-system,sans-serif';
      ctx.textAlign = 'left';  ctx.textBaseline = 'top';
      ctx.fillText(fmt12(segs[0].start),               HP.l,      HP.b+5);
      ctx.textAlign = 'right';
      ctx.fillText(fmt12(segs[segs.length-1].end),     HP.r,      HP.b+5);
    }

    // ── Stage totals footer ────────────────────────────────────────────────
    const activeCols = TOTAL_COLS.filter(c => (n[c.key]||0) > 0);
    const colW = (CW - 24) / Math.max(activeCols.length, 1);
    activeCols.forEach((c, i) => {
      const gx = cx0 + 12 + i * colW;
      const gy = cy0 + CH - 28;
      ctx.fillStyle = c.color;
      ctx.beginPath(); ctx.arc(gx+5, gy+5, 3, 0, Math.PI*2); ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,0.35)';
      ctx.font = '9px -apple-system,sans-serif';
      ctx.textAlign = 'left'; ctx.textBaseline = 'top';
      ctx.fillText(c.label, gx+12, gy);
      ctx.fillStyle = '#fff';
      ctx.font = 'bold 9px -apple-system,sans-serif';
      ctx.fillText(hm(n[c.key]), gx+12, gy+11);
    });
  }

  // ── Event handlers ─────────────────────────────────────────────────────────
  const domTT = $('tt');

  function onMove(e) {
    if (domTT) domTT.style.display = 'none';
    const idx = getIdx(e);
    if (idx < 0 || idx >= nights.length) { draw(-1, null); currentIdx = -1; return; }
    if (idx === currentIdx) return;
    currentIdx = idx;
    const n    = nights[idx];
    const segs = stagesCache[n.date];
    if (segs !== undefined) {
      draw(idx, segs);  // already cached (or confirmed empty)
    } else {
      draw(idx, null);  // show card immediately with totals while fetching
      stagesCache[n.date] = null;  // mark pending
      get(`/api/sleep-stages?date=${n.date}`).then(s => {
        stagesCache[n.date] = (s && s.length) ? s : [];
        if (currentIdx === idx) draw(idx, stagesCache[n.date]);
      });
    }
  }

  wrap.addEventListener('mousemove', onMove);
  wrap.addEventListener('mouseleave', () => { draw(-1, null); currentIdx = -1; });
}

async function loadSleep() {
  const d = await get(`/api/sleep?days=${D.sleep}`);
  cache.sleep = d;
  if (!d||!d.length) { $('sleepVal').textContent='—'; return; }
  const a = avg(d.map(n=>(n.deep||0)+(n.rem||0)+(n.light||0)).filter(v=>v>0));
  const el = $('sleepVal'); if(el) countUp(el, a, 1);

  // Sleep efficiency — only shown when Whoop/Oura provide it (non-zero)
  const effVals = d.map(n=>n.efficiency).filter(v=>v!=null && v>0);
  const effStat = $('effStat');
  if (effVals.length && effStat) {
    effStat.style.display = '';
    const effEl = $('effVal'); if(effEl) countUp(effEl, avg(effVals), 0);
  } else if (effStat) {
    effStat.style.display = 'none';
  }

  drawSleep('slC', d);
  attachSleepHover(d);
}

async function loadRecovery() {
  const d = await get(`/api/recovery?days=${D.rec}`);
  cache.rec = d;

  const hasWhoop = d && d.whoop && d.whoop.length;
  const hasOura  = d && d.oura  && d.oura.length;
  $('whoopCard').style.display = hasWhoop ? '' : 'none';
  $('ouraCard').style.display  = hasOura  ? '' : 'none';
  // Full-width when only one recovery source; hide entirely when neither
  const recRow = $('recRow');
  if (!hasWhoop && !hasOura) { recRow.style.display = 'none'; }
  else { recRow.style.display = ''; recRow.className = (hasWhoop && hasOura) ? 'two' : ''; }

  if (hasWhoop) {
    const a = avg(d.whoop.map(r=>r.value).filter(v=>v));
    const el=$('whoopVal'); if(el) countUp(el,a,0);
    drawLine('whoopC','whoopO', d.whoop, {color:C.rec, unit:'%', minY:0, maxY:100});
    const wrap=$('whoopC').parentElement;
    attachHover(wrap,'whoopC','whoopO', r=>({val:fmt(r.value,0)+'%',sub:'Recovery'}));
  }
  if (hasOura) {
    const a = avg(d.oura.map(r=>r.value).filter(v=>v));
    const el=$('ouraVal'); if(el) countUp(el,a,0);
    drawLine('ouraC','ouraO', d.oura, {color:C.read, unit:'', minY:0, maxY:100});
    const wrap=$('ouraC').parentElement;
    attachHover(wrap,'ouraC','ouraO', r=>({val:fmt(r.value,0),sub:'Readiness'}));
  }

  const hasStrain = d && d.whoop_strain && d.whoop_strain.length;
  $('strainCard').style.display = hasStrain ? '' : 'none';
  if (hasStrain) {
    const a = avg(d.whoop_strain.map(r=>r.value).filter(v=>v));
    const el=$('strainVal'); if(el) countUp(el,a,1);
    drawLine('strainC','strainO', d.whoop_strain, {color:C.strain, unit:' / 21', minY:0, maxY:21});
    const wrap=$('strainC').parentElement;
    attachHover(wrap,'strainC','strainO', r=>({val:fmt(r.value,1),sub:'Day Strain'}));
  }
}

async function loadWorkouts() {
  const d = await get(`/api/workouts?days=${D.wo}`);
  cache.wo = d;
  renderWorkouts(d);
}

async function loadTemperature() {
  const d = await get(`/api/temperature?days=${D.temp}`);
  cache.temp = d;
  const card = $('tempCard');
  const hasOura  = d && d.oura  && d.oura.length;
  const hasWhoop = d && d.whoop && d.whoop.length;
  if (!hasOura && !hasWhoop) { if(card) card.style.display='none'; return; }
  if(card) card.style.display='';
  if (hasOura) {
    // Oura temperature deviation — show deviation from personal baseline
    const a = avg(d.oura.map(r=>r.value).filter(v=>v!=null));
    const el=$('tempVal'); if(el) countUp(el,a,2);
    const lbl=$('tempLbl'); if(lbl) lbl.textContent='deviation °C (Oura)';
    drawLine('tempC','tempO', d.oura, {color:C.temp, unit:'°C', minY:-2, maxY:2});
    const wrap=$('tempC').parentElement;
    attachHover(wrap,'tempC','tempO', r=>({val:(r.value>=0?'+':'')+fmt(r.value,2)+'°C',sub:'Temp deviation'}));
  } else if (hasWhoop) {
    // Whoop skin temperature — absolute Celsius
    const a = avg(d.whoop.map(r=>r.value).filter(v=>v));
    const el=$('tempVal'); if(el) countUp(el,a,1);
    const lbl=$('tempLbl'); if(lbl) lbl.textContent='skin temp °C (Whoop)';
    drawLine('tempC','tempO', d.whoop, {color:C.temp, unit:'°C'});
    const wrap=$('tempC').parentElement;
    attachHover(wrap,'tempC','tempO', r=>({val:fmt(r.value,1)+'°C',sub:'Skin temp'}));
  }
}

function loadAll() {
  loadBloodOxygen(); loadHRV(); loadVO2Max(); loadRHR(); loadHR(); loadRespiration(); loadSleep(); loadRecovery(); loadWorkouts(); loadTemperature();
}

// ── Per-card range buttons ────────────────────────────────────────────────────
const LOADERS = {
  spo2:loadBloodOxygen, hrv:loadHRV, vo2max:loadVO2Max, rhr:loadRHR, hr:loadHR,
  resp:loadRespiration, sleep:loadSleep, rec:loadRecovery, wo:loadWorkouts, temp:loadTemperature,
};
document.querySelectorAll('.crange').forEach(group=>{
  group.querySelectorAll('.crbtn').forEach(btn=>{
    btn.addEventListener('click', ()=>{
      // Only toggle buttons in this specific group
      group.querySelectorAll('.crbtn').forEach(b=>b.classList.remove('on'));
      btn.classList.add('on');
      const chart = group.dataset.chart;
      D[chart] = +btn.dataset.d;
      // Sync sibling groups with same data-chart (Whoop + Oura share 'rec')
      document.querySelectorAll(`.crange[data-chart="${chart}"]`).forEach(g=>{
        g.querySelectorAll('.crbtn').forEach(b=>b.classList.remove('on'));
        g.querySelector(`.crbtn[data-d="${btn.dataset.d}"]`)?.classList.add('on');
      });
      LOADERS[chart]?.();
    });
  });
});

// ── Resize: redraw from cache ──────────────────────────────────────────────────
window.addEventListener('resize', ()=>{
  clearTimeout(window._rsz);
  window._rsz = setTimeout(()=>{
    if(cache.spo2)   drawLine('spo2C','spo2O',  cache.spo2, {color:C.spo2, unit:'%', minY:90, maxY:100});
    if(cache.hrv)    drawLine('hrvC','hrvO',    cache.hrv,  {color:C.hrv,  unit:'ms', minY:0});
    if(cache.vo2max) drawLine('vo2C','vo2O',  cache.vo2max,{color:C.vo2,  unit:' mL/min·kg', minY:25, maxY:65});
    if(cache.rhr)    drawLine('rhrC','rhrO',    cache.rhr,  {color:C.rhr,  unit:'bpm'});
    if(cache.hr?.length) drawHRBand('hrC','hrO', cache.hr);
    if(cache.resp)   drawLine('respC','respO',  cache.resp, {color:C.resp, unit:' br/min'});
    if(cache.sleep)  drawSleep('slC', cache.sleep);
    if(cache.rec){
      if(cache.rec.whoop?.length)        drawLine('whoopC','whoopO', cache.rec.whoop,        {color:C.rec,    unit:'%', minY:0, maxY:100});
      if(cache.rec.oura?.length)         drawLine('ouraC','ouraO',   cache.rec.oura,         {color:C.read,   unit:'',  minY:0, maxY:100});
      if(cache.rec.whoop_strain?.length) drawLine('strainC','strainO',cache.rec.whoop_strain,{color:C.strain, unit:' / 21', minY:0, maxY:21});
    }
    if(cache.temp){
      if(cache.temp.oura?.length)  drawLine('tempC','tempO', cache.temp.oura,  {color:C.temp, unit:'°C', minY:-2, maxY:2});
      else if(cache.temp.whoop?.length) drawLine('tempC','tempO', cache.temp.whoop, {color:C.temp, unit:'°C'});
    }
  }, 120);
});

// ── Init ──────────────────────────────────────────────────────────────────────
loadSummary();
loadAll();
</script>
</body>
</html>
"""

# ── HTTP server ───────────────────────────────────────────────────────────────

class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_): pass   # suppress terminal noise

    def _json(self, data, status=200):
        body = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type",   "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text):
        body = text.encode()
        self.send_response(200)
        self.send_header("Content-Type",   "text/html; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(p.query)
        d     = int(qs.get("days",  ["30"])[0])
        start = qs.get("start", [""])[0]
        end   = qs.get("end",   [""])[0]
        date  = qs.get("date",  [""])[0]
        routes = {
            "/api/summary":       lambda: api_summary(),
            "/api/heart-rate":    lambda: api_heart_rate(d),
            "/api/resting-hr":    lambda: api_resting_hr(d),
            "/api/hrv":           lambda: api_hrv(d),
            "/api/vo2max":        lambda: api_vo2max(d),
            "/api/sleep":         lambda: api_sleep(d),
            "/api/blood-oxygen":  lambda: api_blood_oxygen(d),
            "/api/respiration":   lambda: api_respiration(d),
            "/api/recovery":      lambda: api_recovery(d),
            "/api/temperature":   lambda: api_temperature(d),
            "/api/workouts":      lambda: api_workouts(d),
            "/api/debug/sleep":   lambda: api_debug_sleep(),
            "/api/sleep-stages":  lambda: api_sleep_stages(date),
            "/api/workout-hr":    lambda: api_workout_hr(start, end),
            "/api/workout-route": lambda: api_workout_route(start),
        }
        if p.path in routes:
            self._json(routes[p.path]())
        elif p.path in ("/", "/index.html"):
            self._html(HTML)
        else:
            self._json({"error": "not found"}, 404)


class _ThreadedServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads      = True


def start_server():
    """Start the HTTP server (blocking). Called in a thread for app mode."""
    with _ThreadedServer((HOST, PORT), _Handler) as srv:
        srv.serve_forever()


# ── macOS mini-window (app mode only) ─────────────────────────────────────────

def _run_app_window(url):
    """Show a small Tkinter status window so the app lives in the Dock."""
    try:
        import tkinter as tk
    except ImportError:
        # No Tkinter — just keep alive
        while True: time.sleep(60)
        return

    root = tk.Tk()
    root.title("Leo Health")
    root.geometry("300x130")
    root.resizable(False, False)
    try:
        root.configure(bg="#0a0a12")
    except Exception:
        pass

    def styled(widget):
        try: widget.configure(bg="#0a0a12", fg="#f0f0f8")
        except Exception: pass
        return widget

    title = styled(tk.Label(root, text="🦁  Leo Health", font=("SF Pro Display", 15, "bold")))
    title.pack(pady=(18, 4))

    sub = styled(tk.Label(root, text=f"Dashboard at {url}", font=("SF Pro Display", 10)))
    sub.pack()

    def open_dash(): webbrowser.open(url)

    btn = tk.Button(root, text="Open Dashboard", command=open_dash,
                    font=("SF Pro Display", 11), relief="flat",
                    padx=18, pady=7, cursor="hand2")
    try: btn.configure(bg="#1c1c2e", fg="#ffffff", activebackground="#2a2a3e", activeforeground="#ffffff")
    except Exception: pass
    btn.pack(pady=14)

    root.mainloop()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    if not os.path.exists(DB_PATH):
        print()
        print("  🦁  Leo Health Dashboard")
        print()
        print(f"  ✗  No database at {DB_PATH}")
        print("     Import your health data first:")
        print("     • Copy your Apple Health export ZIP to ~/Downloads")
        print("     • Run:  leo-watch")
        print()
        return

    _startup_migrate()   # deduplicate sleep rows from multiple imports

    url = f"http://{HOST}:{PORT}"

    # Try to bind; gracefully handle port-in-use
    try:
        test = socketserver.TCPServer((HOST, PORT), _Handler)
        test.server_close()
    except OSError:
        print(f"  Port {PORT} is already in use — Leo Dashboard may already be running.")
        webbrowser.open(url)
        return

    if IS_APP:
        # Running as a packaged .app — start server in thread, show Tkinter window
        t = threading.Thread(target=start_server, daemon=True)
        t.start()
        time.sleep(0.4)
        webbrowser.open(url)
        _run_app_window(url)
    else:
        # CLI mode — print info and block
        print()
        print("  🦁  Leo Health Dashboard")
        print(f"      {url}")
        print(f"      DB: {DB_PATH}")
        print("      Press Ctrl+C to stop\n")
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
        try:
            start_server()
        except KeyboardInterrupt:
            print("\n  Stopped. Your data stays on your machine. 🔒\n")


if __name__ == "__main__":
    main()
