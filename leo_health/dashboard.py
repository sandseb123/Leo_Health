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

def api_summary():
    s = _since(7)

    rhr = _q1("SELECT ROUND(AVG(value),0) AS v FROM heart_rate "
               "WHERE metric='resting_heart_rate' AND recorded_at >= ?", (s,))

    hrv = _q1("SELECT ROUND(AVG(value),1) AS v FROM hrv "
               "WHERE recorded_at >= ?", (s,))

    # Sleep: prefer Whoop/Oura aggregates, fall back to Apple Health stages
    sleep_agg = _q1("""
        SELECT ROUND(AVG(
            COALESCE(deep_sleep_hours,0)+COALESCE(rem_sleep_hours,0)+
            COALESCE(light_sleep_hours,0)
        ),2) AS v
        FROM sleep WHERE recorded_at>=? AND source IN ('whoop','oura')
          AND (stage='asleep' OR stage IS NULL)
    """, (s,))
    if not sleep_agg.get("v"):
        sleep_agg = _q1("""
            SELECT ROUND(AVG(hours),2) AS v FROM (
                SELECT date(recorded_at) AS d,
                       COALESCE(SUM(CASE WHEN stage IN ('asleepdeep','asleeprem','asleepcore','asleepunspecified')
                           THEN (julianday(SUBSTR(end,1,19))-julianday(SUBSTR(start,1,19)))*24 END),0) AS hours
                FROM sleep WHERE recorded_at>=? AND source='apple_health'
                  AND end IS NOT NULL AND start IS NOT NULL AND length(end)>=19 AND length(start)>=19
                GROUP BY d HAVING hours>0
            )
        """, (s,))

    whoop = _q1("SELECT ROUND(AVG(recovery_score),0) AS v "
                "FROM whoop_recovery WHERE recorded_at>=?", (s,))

    oura  = _q1("SELECT ROUND(AVG(readiness_score),0) AS v "
                "FROM oura_readiness WHERE recorded_at>=?", (s,))

    strain = _q1("SELECT ROUND(AVG(day_strain),1) AS v "
                 "FROM whoop_strain WHERE recorded_at>=?", (s,))

    # Detect which sources have data
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
        "resting_hr":      _safe_int(rhr.get("v")),
        "hrv":             rhr_or_none(hrv.get("v")),
        "sleep_hours":     rhr_or_none(sleep_agg.get("v")),
        "whoop_recovery":  _safe_int(whoop.get("v")),
        "oura_readiness":  _safe_int(oura.get("v")),
        "whoop_strain":    rhr_or_none(strain.get("v")),
        "sources":         sources,
        "last_recorded":   (last.get("d") or "")[:10],
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


def _dur_hours(end_col, start_col):
    """SQLite expression: hours between two ISO8601 columns (handles tz offsets)."""
    # SUBSTR(...,1,19) strips timezone offset so julianday() can parse it.
    return f"(julianday(SUBSTR({end_col},1,19))-julianday(SUBSTR({start_col},1,19)))*24"


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
        return rows

    # ── 2. Apple Health detailed stages ──────────────────────────────────────
    # Apple Health stores stage as lowercased enum suffix:
    #   asleepdeep, asleeprem, asleepcore, asleepunspecified, awake, in_bed
    # Two sources of inflation to guard against:
    #   A) Multiple devices (Apple Watch + AutoSleep) writing for the same night
    #      → group by device, pick device with most deep+REM (best stage data)
    #   B) Same device writes both granular stage segments AND a long
    #      asleepunspecified umbrella for the whole night (Apple Watch behaviour)
    #      → if device has any deep/rem/core, drop asleepunspecified entirely
    dur = _dur_hours("end", "start")
    raw = _q(f"""
        SELECT date(recorded_at) AS date,
               device,
               ROUND(COALESCE(SUM(CASE WHEN stage='asleepdeep' THEN {dur} END),0),2) AS deep,
               ROUND(COALESCE(SUM(CASE WHEN stage='asleeprem'  THEN {dur} END),0),2) AS rem,
               ROUND(COALESCE(SUM(CASE WHEN stage='asleepcore' THEN {dur} END),0),2) AS core,
               ROUND(COALESCE(SUM(CASE WHEN stage='asleepunspecified' THEN {dur} END),0),2) AS unspec,
               ROUND(COALESCE(SUM(CASE WHEN stage='awake'      THEN {dur} END),0),2) AS awake,
               0 AS efficiency
        FROM sleep
        WHERE recorded_at>=? AND source='apple_health'
          AND stage IN ('asleepdeep','asleeprem','asleepcore','asleepunspecified','awake')
          AND end IS NOT NULL AND start IS NOT NULL
          AND length(end)>=19 AND length(start)>=19
        GROUP BY date(recorded_at), device
        ORDER BY date
    """, (s,))
    # ── DEBUG: write all raw sleep rows to /tmp/sleep_debug.txt ─────────────
    with open("/tmp/sleep_debug.txt", "w") as _dbg:
        _dbg.write(f"total rows: {len(raw)}\n")
        for r in raw:
            _dbg.write(
                f"date={r['date']} device={r['device']!r:50s} "
                f"deep={r.get('deep',0):.2f} rem={r.get('rem',0):.2f} "
                f"core={r.get('core',0):.2f} unspec={r.get('unspec',0):.2f} "
                f"awake={r.get('awake',0):.2f}\n"
            )
    # ── END DEBUG ────────────────────────────────────────────────────────────
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


def api_workout_hr(start, end):
    """Heart rate samples recorded during a workout window."""
    return _q("""
        SELECT recorded_at AS time, ROUND(value,0) AS value
        FROM heart_rate
        WHERE metric='heart_rate' AND recorded_at>=? AND recorded_at<=?
        ORDER BY recorded_at LIMIT 500
    """, (start, end))


def api_workout_route(start):
    """GPS route points for a workout (empty list if not yet imported)."""
    return _q("""
        SELECT latitude AS lat, longitude AS lon, altitude_m AS alt, timestamp AS time
        FROM workout_routes
        WHERE workout_start=?
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


def api_workouts(days=30):
    return _q("""
        SELECT recorded_at,
               date(recorded_at)               AS date,
               time(recorded_at)               AS time,
               activity,
               ROUND(duration_minutes, 1)      AS duration,
               ROUND(calories, 0)              AS calories,
               ROUND(distance_km, 2)           AS distance_km,
               source
        FROM workouts
        WHERE recorded_at >= ?
        ORDER BY recorded_at DESC
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
  --hr:#ff375f;--hrv:#bf5af2;
  --sleep-deep:#5e5ce6;--sleep-rem:#bf5af2;--sleep-light:#32ade6;--sleep-awake:rgba(255,149,0,0.45);
  --rec:#30d158;--read:#ffd60a;--strain:#ff9f0a;--workout:#ff9f0a;
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
.stats-row{display:flex;gap:14px;margin-bottom:20px;flex-wrap:wrap}
.stat{flex:1;min-width:150px;background:var(--card);border:1px solid var(--border);
  border-radius:var(--r);padding:18px 20px;transition:border-color .2s,transform .2s;cursor:default}
.stat:hover{border-color:var(--border2);transform:translateY(-2px)}
.stat-label{font-size:10px;text-transform:uppercase;letter-spacing:.9px;color:var(--muted);margin-bottom:10px}
.stat-val{font-size:30px;font-weight:700;letter-spacing:-1px;line-height:1}
.stat-unit{font-size:13px;font-weight:400;color:var(--dim);margin-left:2px}
.stat-sub{font-size:11px;color:var(--muted);margin-top:5px}

/* ── Chart cards ─────────────────────────────────────────────────────── */
.card{background:var(--card);border:1px solid var(--border);border-radius:var(--r);
  padding:22px 22px 18px;margin-bottom:14px;transition:border-color .2s}
.card:hover{border-color:var(--border2)}
.card-hdr{display:flex;align-items:center;justify-content:space-between;margin-bottom:18px}
.card-title{display:flex;align-items:center;gap:8px;font-size:14px;font-weight:600}
.dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.card-stat{text-align:right}
.card-stat-val{font-size:20px;font-weight:700;letter-spacing:-.5px}
.card-stat-lbl{font-size:10px;color:var(--muted);margin-top:1px}
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
.wo-detail{max-height:0;overflow:hidden;transition:max-height .3s ease}
.wo.open .wo-detail{max-height:400px}
.wo-detail-inner{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));
  gap:10px 16px;padding:0 14px 10px 46px}
.wo-stat{display:flex;flex-direction:column;gap:2px}
.wo-stat-val{font-size:14px;font-weight:600}
.wo-stat-lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.6px}
/* Workout HR chart + route map */
.wo-detail-charts{display:flex;gap:12px;padding:0 14px 14px 14px;align-items:flex-start}
.wo-hr-chart{flex:1;min-width:0}
.wo-hr-chart canvas{display:block;width:100%;height:80px;border-radius:6px}
.wo-hr-lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.7px;margin-bottom:4px}
.wo-route-wrap{width:130px;flex-shrink:0}
.wo-route-wrap svg{display:block;width:130px;height:130px;border-radius:8px}
.wo-route-lbl{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.7px;margin-bottom:4px}

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

/* ── Responsive ──────────────────────────────────────────────────────── */
@media(max-width:720px){
  main{padding:16px 14px 50px}
  .two{grid-template-columns:1fr}
  .stats-row .stat{min-width:140px}
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

  <!-- Heart Rate -->
  <div class="card">
    <div class="card-hdr">
      <div class="card-title"><div class="dot" style="background:var(--hr)"></div>Heart Rate</div>
      <div class="crange" data-chart="hr">
        <button class="crbtn" data-d="7">7D</button>
        <button class="crbtn" data-d="14">14D</button>
        <button class="crbtn on" data-d="30">30D</button>
      </div>
      <div class="card-stat"><div class="card-stat-val" id="hrVal" style="color:var(--hr)">—</div><div class="card-stat-lbl">avg bpm</div></div>
    </div>
    <div class="chart-wrap"><canvas id="hrC" height="128"></canvas><canvas class="overlay" id="hrO" height="128"></canvas></div>
  </div>

  <!-- HRV + RHR -->
  <div class="two">
    <div class="card">
      <div class="card-hdr">
        <div class="card-title"><div class="dot" style="background:var(--hrv)"></div>HRV</div>
        <div class="crange" data-chart="hrv">
          <button class="crbtn" data-d="7">7D</button>
          <button class="crbtn" data-d="14">14D</button>
          <button class="crbtn on" data-d="30">30D</button>
        </div>
        <div class="card-stat"><div class="card-stat-val" id="hrvVal" style="color:var(--hrv)">—</div><div class="card-stat-lbl">avg ms</div></div>
      </div>
      <div class="chart-wrap"><canvas id="hrvC" height="140"></canvas><canvas class="overlay" id="hrvO" height="140"></canvas></div>
    </div>
    <div class="card">
      <div class="card-hdr">
        <div class="card-title"><div class="dot" style="background:#ff6b6b"></div>Resting HR</div>
        <div class="crange" data-chart="rhr">
          <button class="crbtn" data-d="7">7D</button>
          <button class="crbtn" data-d="14">14D</button>
          <button class="crbtn on" data-d="30">30D</button>
        </div>
        <div class="card-stat"><div class="card-stat-val" id="rhrVal" style="color:#ff6b6b">—</div><div class="card-stat-lbl">avg bpm</div></div>
      </div>
      <div class="chart-wrap"><canvas id="rhrC" height="140"></canvas><canvas class="overlay" id="rhrO" height="140"></canvas></div>
    </div>
  </div>

  <!-- Sleep -->
  <div class="card">
    <div class="card-hdr">
      <div class="card-title"><div class="dot" style="background:var(--sleep-deep)"></div>Sleep</div>
      <div class="crange" data-chart="sleep">
        <button class="crbtn" data-d="7">7D</button>
        <button class="crbtn" data-d="14">14D</button>
        <button class="crbtn on" data-d="30">30D</button>
      </div>
      <div class="card-stat"><div class="card-stat-val" id="sleepVal" style="color:var(--sleep-deep)">—</div><div class="card-stat-lbl">avg hours</div></div>
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
          <button class="crbtn" data-d="7">7D</button>
          <button class="crbtn" data-d="14">14D</button>
          <button class="crbtn on" data-d="30">30D</button>
        </div>
        <div class="card-stat"><div class="card-stat-val" id="whoopVal" style="color:var(--rec)">—</div><div class="card-stat-lbl">avg %</div></div>
      </div>
      <div class="chart-wrap"><canvas id="whoopC" height="140"></canvas><canvas class="overlay" id="whoopO" height="140"></canvas></div>
    </div>
    <div class="card" id="ouraCard">
      <div class="card-hdr">
        <div class="card-title"><div class="dot" style="background:var(--read)"></div>Oura Readiness</div>
        <div class="crange" data-chart="rec">
          <button class="crbtn" data-d="7">7D</button>
          <button class="crbtn" data-d="14">14D</button>
          <button class="crbtn on" data-d="30">30D</button>
        </div>
        <div class="card-stat"><div class="card-stat-val" id="ouraVal" style="color:var(--read)">—</div><div class="card-stat-lbl">avg score</div></div>
      </div>
      <div class="chart-wrap"><canvas id="ouraC" height="140"></canvas><canvas class="overlay" id="ouraO" height="140"></canvas></div>
    </div>
  </div>

  <!-- Workouts -->
  <div class="card">
    <div class="card-hdr">
      <div class="card-title"><div class="dot" style="background:var(--workout)"></div>Workouts</div>
      <div class="crange" data-chart="wo">
        <button class="crbtn" data-d="7">7D</button>
        <button class="crbtn" data-d="14">14D</button>
        <button class="crbtn on" data-d="30">30D</button>
      </div>
    </div>
    <div id="woList"></div>
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
};

// ── State & utils ─────────────────────────────────────────────────────────────
const D = {hr:30, hrv:30, rhr:30, sleep:30, rec:30, wo:30};
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

// ── Workout HR mini-chart ──────────────────────────────────────────────────────
function drawWoHR(canvasId, data) {
  const c = $(canvasId);
  if (!c) return;
  const dpr = window.devicePixelRatio || 1;
  const w = c.offsetWidth || c.parentElement.offsetWidth || 280;
  const h = 80;
  c.width = w * dpr; c.height = h * dpr;
  const cx = c.getContext('2d');
  cx.scale(dpr, dpr);

  const vals = data.map(d => +d.value).filter(v => !isNaN(v));
  if (vals.length < 2) return;
  const mn = Math.min(...vals) * 0.96;
  const mx = Math.max(...vals) * 1.04;
  const rng = mx - mn || 1;
  const pad = {t:8, r:8, b:18, l:34};
  const cw = w - pad.l - pad.r, ch = h - pad.t - pad.b;

  cx.clearRect(0, 0, w, h);

  // Grid lines
  cx.strokeStyle = 'rgba(255,255,255,0.05)'; cx.lineWidth = 1;
  [0, 0.5, 1].forEach(f => {
    const y = pad.t + ch * f;
    cx.beginPath(); cx.moveTo(pad.l, y); cx.lineTo(w-pad.r, y); cx.stroke();
    cx.fillStyle = 'rgba(255,255,255,0.28)'; cx.font = '9px -apple-system,sans-serif';
    cx.textAlign = 'right'; cx.textBaseline = 'middle';
    cx.fillText(Math.round(mx - rng*f), pad.l-4, y);
  });

  const pts = data.map((d, i) => ({
    x: pad.l + (i / Math.max(data.length-1, 1)) * cw,
    y: pad.t + ch - ((+d.value - mn) / rng) * ch,
  }));

  // Fill
  const grad = cx.createLinearGradient(0, pad.t, 0, pad.t+ch);
  grad.addColorStop(0, C.hr+'40'); grad.addColorStop(1, C.hr+'04');
  cx.beginPath();
  cx.moveTo(pts[0].x, pts[0].y);
  for (let i=1; i<pts.length; i++) {
    const cpx = (pts[i-1].x + pts[i].x) / 2;
    cx.bezierCurveTo(cpx, pts[i-1].y, cpx, pts[i].y, pts[i].x, pts[i].y);
  }
  cx.lineTo(pts[pts.length-1].x, pad.t+ch);
  cx.lineTo(pts[0].x, pad.t+ch);
  cx.closePath();
  cx.fillStyle = grad; cx.fill();

  // Line
  cx.beginPath();
  cx.moveTo(pts[0].x, pts[0].y);
  for (let i=1; i<pts.length; i++) {
    const cpx = (pts[i-1].x + pts[i].x) / 2;
    cx.bezierCurveTo(cpx, pts[i-1].y, cpx, pts[i].y, pts[i].x, pts[i].y);
  }
  cx.strokeStyle = C.hr; cx.lineWidth = 1.5; cx.lineJoin = 'round'; cx.stroke();

  // Avg/max labels
  const avgV = Math.round(vals.reduce((s,v)=>s+v,0)/vals.length);
  const maxV = Math.round(Math.max(...vals));
  cx.fillStyle = 'rgba(255,255,255,0.4)'; cx.font = '9px -apple-system,sans-serif';
  cx.textAlign = 'right'; cx.textBaseline = 'alphabetic';
  cx.fillText(`avg ${avgV} · max ${maxV} bpm`, w-pad.r, h-2);
}

// ── Workout GPS route SVG ─────────────────────────────────────────────────────
function drawWoRoute(svgId, points) {
  const svg = $(svgId);
  if (!svg || !points || points.length < 2) return;
  const lats = points.map(p => +p.lat), lons = points.map(p => +p.lon);
  const minLat = Math.min(...lats), maxLat = Math.max(...lats);
  const minLon = Math.min(...lons), maxLon = Math.max(...lons);
  const latR = maxLat - minLat || 0.001, lonR = maxLon - minLon || 0.001;
  const W = 130, H = 130, PAD = 10;
  const cw = W-PAD*2, ch = H-PAD*2;
  // Keep aspect ratio
  const scale = Math.min(cw / lonR, ch / latR);
  const offX = (cw - lonR*scale)/2, offY = (ch - latR*scale)/2;
  const nx = p => PAD + offX + (p.lon - minLon)*scale;
  const ny = p => PAD + offY + (maxLat - p.lat)*scale;  // flip lat

  const pts = points.map(p => `${nx(p).toFixed(1)},${ny(p).toFixed(1)}`).join(' ');
  const s0 = points[0], se = points[points.length-1];
  svg.innerHTML = `
    <rect width="${W}" height="${H}" fill="rgba(255,255,255,0.03)" rx="8"/>
    <polyline points="${pts}" fill="none" stroke="${C.hr}" stroke-width="2"
              stroke-linejoin="round" stroke-linecap="round" opacity="0.85"/>
    <circle cx="${nx(s0).toFixed(1)}" cy="${ny(s0).toFixed(1)}" r="4" fill="${C.rec}"/>
    <circle cx="${nx(se).toFixed(1)}" cy="${ny(se).toFixed(1)}" r="4" fill="${C.hr}"/>
  `;
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
  if (hrData && hrData.length >= 4) {
    drawWoHR(`woHrC${idx}`, hrData);
  } else if (hrWrap) {
    hrWrap.style.display = 'none';
  }

  const ROUTE_ACTS = new Set(['running','cycling','walking','hiking','skiing','snowboarding']);
  if (ROUTE_ACTS.has(activity)) {
    const route = await get(`/api/workout-route?start=${encodeURIComponent(start)}`);
    const rtWrap = $(`woRtWrap${idx}`);
    if (route && route.length >= 2) {
      drawWoRoute(`woRtSvg${idx}`, route);
    } else if (rtWrap) {
      rtWrap.style.display = 'none';
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
    const canRoute = ROUTE_ACTS.has(key);

    const stats = [
      dur   && `<div class="wo-stat"><div class="wo-stat-val">${dur}</div><div class="wo-stat-lbl">Duration</div></div>`,
      dist  && `<div class="wo-stat"><div class="wo-stat-val">${dist}</div><div class="wo-stat-lbl">Distance</div></div>`,
      cals  && `<div class="wo-stat"><div class="wo-stat-val">${cals}</div><div class="wo-stat-lbl">Calories</div></div>`,
      pace  && `<div class="wo-stat"><div class="wo-stat-val">${pace}</div><div class="wo-stat-lbl">Pace</div></div>`,
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
            <div class="wo-name">${name}</div>
            <div class="wo-date">${fmtDateLong(w.date)}${w.time?' · '+w.time.slice(0,5):''}</div>
          </div>
        </div>
        <div class="wo-right">
          <div class="wo-dur">${dur}</div>
          ${dist ? `<div class="wo-cal">${dist}</div>` : (cals ? `<div class="wo-cal">${cals}</div>` : '')}
          <span class="wo-chev">›</span>
        </div>
      </div>
      <div class="wo-detail">
        ${stats ? `<div class="wo-detail-inner">${stats}</div>` : ''}
        <div class="wo-detail-charts">
          <div class="wo-hr-chart" id="woHrWrap${idx}">
            <div class="wo-hr-lbl">Heart Rate During Workout</div>
            <canvas id="woHrC${idx}" height="80"></canvas>
          </div>
          ${canRoute ? `<div class="wo-route-wrap" id="woRtWrap${idx}">
            <div class="wo-route-lbl">Route</div>
            <svg id="woRtSvg${idx}" viewBox="0 0 130 130"></svg>
          </div>` : ''}
        </div>
      </div>
    </div>`;
  });

  el.innerHTML = '<div class="wo-list">' + rows.join('') + '</div>';
}

// ── API loaders ───────────────────────────────────────────────────────────────
async function get(path) {
  try { const r=await fetch(path); return r.ok ? r.json() : null; }
  catch { return null; }
}

async function loadSummary() {
  const d = await get('/api/summary');
  if (!d) return;

  // Source badges
  const SRC = {apple_health:'❤️ Apple Health', whoop:'⌚ Whoop', oura:'💍 Oura', fitbit:'📊 Fitbit'};
  $('badges').innerHTML = (d.sources||[]).map(s=>`<span class="badge">${SRC[s]||s}</span>`).join('');
  $('sync').textContent = d.last_recorded ? `Last record: ${fmtDate(d.last_recorded)}` : 'No data yet';

  // Stat cards
  const stats = [
    {label:'Resting HR',    val:d.resting_hr,    unit:'bpm',  dec:0, col:'var(--hr)'},
    {label:'HRV',           val:d.hrv,           unit:' ms',  dec:0, col:'var(--hrv)'},
    {label:'Avg Sleep',     val:d.sleep_hours,   unit:' hrs', dec:1, col:'var(--sleep-deep)'},
    {label:'Whoop Recovery',val:d.whoop_recovery,unit:'%',    dec:0, col:'var(--rec)'},
    {label:'Oura Readiness',val:d.oura_readiness,unit:'',     dec:0, col:'var(--read)'},
    {label:'Whoop Strain',  val:d.whoop_strain,  unit:' / 21',dec:1, col:'var(--strain)'},
  ].filter(s=>s.val!=null);

  $('statsRow').innerHTML = stats.map(s=>
    `<div class="stat">
       <div class="stat-label">${s.label}</div>
       <div class="stat-val" style="color:${s.col}" data-val="${s.val}" data-dec="${s.dec}">
         —<span class="stat-unit">${s.unit}</span>
       </div>
       <div class="stat-sub">7-day average</div>
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

async function loadHR() {
  const d = await get(`/api/heart-rate?days=${D.hr}`);
  cache.hr = d;
  if (!d||!d.length) return;
  const a = avg(d.map(r=>r.avg).filter(v=>v));
  const el = $('hrVal'); if(el) countUp(el, a, 0);
  drawLine('hrC','hrO', d, {color:C.hr, valueKey:'avg', unit:'bpm'});

  const wrap = $('hrC').parentElement;
  attachHover(wrap, 'hrC', 'hrO', d=>{
    const rng = (d.min&&d.max) ? `${Math.round(d.min)}–${Math.round(d.max)} bpm` : '';
    return {val: fmt(d.avg,0)+' bpm', sub: rng};
  });
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
  const tt = $('tt'), ttDate = $('tt-date'), ttVal = $('tt-val'), ttSub = $('tt-sub');
  const wrap = canvas.parentElement;
  let lastIdx = -1;

  function getIdx(e) {
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX - rect.left) / rect.width * (canvas.width / (window.devicePixelRatio||1));
    const pad = {l:36, r:10, t:10, b:26};
    const cw = (canvas.width / (window.devicePixelRatio||1)) - pad.l - pad.r;
    const barW = (cw - (nights.length-1)*3) / nights.length;
    return Math.floor((mx - pad.l) / (barW + 3));
  }

  function drawHighlight(idx) {
    const dpr = window.devicePixelRatio||1;
    const W = overlay.offsetWidth||600, H = overlay.offsetHeight||150;
    overlay.width = W*dpr; overlay.height = H*dpr;
    overlay.style.width = W+'px'; overlay.style.height = H+'px';
    const cx = overlay.getContext('2d'); cx.scale(dpr, dpr);
    cx.clearRect(0,0,W,H);
    if (idx < 0 || idx >= nights.length) return;
    const pad = {l:36,r:10,t:10,b:26};
    const cw = W - pad.l - pad.r, ch = H - pad.t - pad.b;
    const barW = (cw - (nights.length-1)*3) / nights.length;
    const x = pad.l + idx*(barW+3);
    cx.fillStyle = 'rgba(255,255,255,0.07)';
    cx.fillRect(x, pad.t, barW, ch);
  }

  wrap.addEventListener('mousemove', e=>{
    const idx = getIdx(e);
    if (idx < 0 || idx >= nights.length) {
      tt.style.display='none'; if(lastIdx!==idx){drawHighlight(-1);lastIdx=idx;} return;
    }
    if (idx !== lastIdx) { drawHighlight(idx); lastIdx = idx; }
    const n = nights[idx];
    const total = (n.deep||0)+(n.rem||0)+(n.light||0);
    const h = v => v>0 ? v.toFixed(1)+'h' : '—';
    ttDate.textContent = fmtDateLong(n.date);
    ttVal.textContent = h(total) + ' sleep';
    ttSub.innerHTML =
      `<span style="color:#5e5ce6">● Deep&nbsp;&nbsp;${h(n.deep||0)}</span><br>`+
      `<span style="color:#bf5af2">● REM&nbsp;&nbsp;&nbsp;${h(n.rem||0)}</span><br>`+
      `<span style="color:#32ade6">● Light&nbsp;&nbsp;${h(n.light||0)}</span><br>`+
      `<span style="color:rgba(255,149,0,.9)">● Awake&nbsp;${h(n.awake||0)}</span>`;
    tt.style.display='block';
    tt.style.left=(e.clientX+14)+'px';
    tt.style.top=Math.max(10,e.clientY-100)+'px';
  });
  wrap.addEventListener('mouseleave', ()=>{ tt.style.display='none'; drawHighlight(-1); lastIdx=-1; });
}

async function loadSleep() {
  const d = await get(`/api/sleep?days=${D.sleep}`);
  cache.sleep = d;
  if (!d||!d.length) { $('sleepVal').textContent='—'; return; }
  const a = avg(d.map(n=>(n.deep||0)+(n.rem||0)+(n.light||0)).filter(v=>v>0));
  const el = $('sleepVal'); if(el) countUp(el, a, 1);
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
  $('recRow').style.display    = (hasWhoop||hasOura) ? '' : 'none';

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
}

async function loadWorkouts() {
  const d = await get(`/api/workouts?days=${D.wo}`);
  cache.wo = d;
  renderWorkouts(d);
}

function loadAll() {
  loadHR(); loadHRV(); loadRHR(); loadSleep(); loadRecovery(); loadWorkouts();
}

// ── Per-card range buttons ────────────────────────────────────────────────────
const LOADERS = {hr:loadHR, hrv:loadHRV, rhr:loadRHR, sleep:loadSleep, rec:loadRecovery, wo:loadWorkouts};
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
    if(cache.hr)     drawLine('hrC','hrO',    cache.hr,  {color:C.hr,  valueKey:'avg', unit:'bpm'});
    if(cache.hrv)    drawLine('hrvC','hrvO',  cache.hrv, {color:C.hrv, unit:'ms', minY:0});
    if(cache.rhr)    drawLine('rhrC','rhrO',  cache.rhr, {color:C.rhr, unit:'bpm'});
    if(cache.sleep)  drawSleep('slC', cache.sleep);
    if(cache.rec){
      if(cache.rec.whoop?.length) drawLine('whoopC','whoopO',cache.rec.whoop,{color:C.rec,  unit:'%', minY:0, maxY:100});
      if(cache.rec.oura?.length)  drawLine('ouraC','ouraO',  cache.rec.oura, {color:C.read, unit:'',  minY:0, maxY:100});
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
        d  = int(qs.get("days", ["30"])[0])
        start = qs.get("start", [""])[0]
        end   = qs.get("end",   [""])[0]
        routes = {
            "/api/summary":      lambda: api_summary(),
            "/api/heart-rate":   lambda: api_heart_rate(d),
            "/api/resting-hr":   lambda: api_resting_hr(d),
            "/api/hrv":          lambda: api_hrv(d),
            "/api/sleep":        lambda: api_sleep(d),
            "/api/recovery":     lambda: api_recovery(d),
            "/api/workouts":     lambda: api_workouts(d),
            "/api/debug/sleep":  lambda: api_debug_sleep(),
            "/api/workout-hr":   lambda: api_workout_hr(start, end),
            "/api/workout-route":lambda: api_workout_route(start),
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
