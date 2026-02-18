"""
Leo Core — Status Command
Pretty-prints a summary of your Leo Health database.

Usage:
    python3 -m leo_health.status
"""

import sqlite3
import os
from pathlib import Path
from datetime import datetime

DB_PATH = os.path.join(Path.home(), ".leo-health", "leo.db")

# ── Terminal colors ───────────────────────────────────────────────────────────
R = "\033[0m"       # reset
BOLD = "\033[1m"
DIM = "\033[2m"
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
BLUE = "\033[94m"
MAGENTA = "\033[95m"
RED = "\033[91m"
WHITE = "\033[97m"

def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def _bar(value, max_value, width=20, color=GREEN):
    filled = int((value / max_value) * width) if max_value > 0 else 0
    bar = "█" * filled + "░" * (width - filled)
    return f"{color}{bar}{R}"

def _format_num(n):
    return f"{n:,}"

def main():
    if not os.path.exists(DB_PATH):
        print(f"{RED}No database found at {DB_PATH}{R}")
        print(f"Run the watcher first: {CYAN}python3 -m leo_health.watcher{R}")
        return

    conn = _conn()

    # ── Header ────────────────────────────────────────────────────────────────
    print()
    print(f"{BOLD}{CYAN}╔══════════════════════════════════════════════╗{R}")
    print(f"{BOLD}{CYAN}║           Leo Health — Status                ║{R}")
    print(f"{BOLD}{CYAN}╚══════════════════════════════════════════════╝{R}")
    print()

    # ── Database info ─────────────────────────────────────────────────────────
    db_size = os.path.getsize(DB_PATH) / (1024 * 1024)
    print(f"{DIM}  Database: {DB_PATH}{R}")
    print(f"{DIM}  Size:     {db_size:.1f} MB{R}")
    print()

    # ── Heart Rate ────────────────────────────────────────────────────────────
    hr = conn.execute("""
        SELECT
            SUM(CASE WHEN metric='heart_rate' THEN 1 ELSE 0 END) as hr_count,
            ROUND(AVG(CASE WHEN metric='heart_rate' THEN value END), 0) as hr_avg,
            MIN(CASE WHEN metric='heart_rate' THEN value END) as hr_min,
            MAX(CASE WHEN metric='heart_rate' THEN value END) as hr_max,
            ROUND(AVG(CASE WHEN metric='resting_heart_rate' THEN value END), 0) as rhr_avg,
            SUM(CASE WHEN metric='resting_heart_rate' THEN 1 ELSE 0 END) as rhr_count
        FROM heart_rate
    """).fetchone()

    print(f"{BOLD}{RED}  ❤️  Heart Rate{R}  {DIM}({_format_num(hr['hr_count'] or 0)} readings){R}")
    if hr['hr_avg']:
        print(f"      Average:  {BOLD}{WHITE}{int(hr['hr_avg'])} BPM{R}  "
              f"{DIM}(min {int(hr['hr_min'])} · max {int(hr['hr_max'])}){R}")
    if hr['rhr_avg']:
        print(f"      Resting:  {BOLD}{WHITE}{int(hr['rhr_avg'])} BPM{R}  "
              f"{DIM}({_format_num(hr['rhr_count'])} readings){R}")
    print()

    # ── HRV ──────────────────────────────────────────────────────────────────
    hrv = conn.execute("""
        SELECT
            COUNT(*) as count,
            ROUND(AVG(value), 1) as avg,
            ROUND(MIN(value), 1) as min,
            ROUND(MAX(value), 1) as max,
            source
        FROM hrv
        GROUP BY source
    """).fetchall()

    total_hrv = conn.execute("SELECT COUNT(*) as n FROM hrv").fetchone()["n"]
    print(f"{BOLD}{MAGENTA}  💜  HRV{R}  {DIM}({_format_num(total_hrv)} readings){R}")
    for row in hrv:
        print(f"      {row['source']:15}  {BOLD}{WHITE}{row['avg']} ms{R}  "
              f"{DIM}(min {row['min']} · max {row['max']}){R}")
    print()

    # ── Sleep ─────────────────────────────────────────────────────────────────
    sleep = conn.execute("""
        SELECT stage, COUNT(*) as count
        FROM sleep
        GROUP BY stage
        ORDER BY count DESC
    """).fetchall()

    total_sleep = conn.execute("SELECT COUNT(*) as n FROM sleep").fetchone()["n"]
    max_sleep = max((r["count"] for r in sleep), default=1)

    print(f"{BOLD}{BLUE}  😴  Sleep{R}  {DIM}({_format_num(total_sleep)} sessions){R}")
    stage_labels = {
        "in_bed": "In Bed",
        "asleepcore": "Core Sleep",
        "asleeprem": "REM",
        "asleepdeep": "Deep Sleep",
        "awake": "Awake",
        "asleepunspecified": "Unspecified",
        "rem": "REM",
        "deep": "Deep Sleep",
        "core": "Core",
        "asleep": "Asleep",
    }
    for row in sleep:
        label = stage_labels.get(row["stage"], row["stage"])
        bar = _bar(row["count"], max_sleep, width=16, color=BLUE)
        print(f"      {label:14}  {bar}  {DIM}{_format_num(row['count'])}{R}")
    print()

    # ── Workouts ──────────────────────────────────────────────────────────────
    workouts = conn.execute("""
        SELECT
            activity,
            COUNT(*) as count,
            ROUND(AVG(duration_minutes), 0) as avg_duration,
            ROUND(AVG(calories), 0) as avg_cal
        FROM workouts
        GROUP BY activity
        ORDER BY count DESC
        LIMIT 6
    """).fetchall()

    total_workouts = conn.execute("SELECT COUNT(*) as n FROM workouts").fetchone()["n"]
    max_w = max((r["count"] for r in workouts), default=1)

    print(f"{BOLD}{GREEN}  🏃  Workouts{R}  {DIM}({_format_num(total_workouts)} total){R}")
    for row in workouts:
        bar = _bar(row["count"], max_w, width=12, color=GREEN)
        cal = f"~{int(row['avg_cal'])} cal" if row["avg_cal"] else ""
        dur = f"{int(row['avg_duration'])}min avg" if row["avg_duration"] else ""
        meta = f"{dur}  {cal}".strip()
        print(f"      {row['activity']:20}  {bar}  {DIM}{_format_num(row['count'])}  {meta}{R}")
    print()

    # ── Whoop ─────────────────────────────────────────────────────────────────
    whoop_count = conn.execute("SELECT COUNT(*) as n FROM whoop_recovery").fetchone()["n"]
    if whoop_count > 0:
        whoop = conn.execute("""
            SELECT
                ROUND(AVG(recovery_score), 0) as avg_recovery,
                ROUND(AVG(hrv_ms), 1) as avg_hrv,
                ROUND(AVG(resting_heart_rate), 0) as avg_rhr,
                COUNT(*) as days
            FROM whoop_recovery
        """).fetchone()
        print(f"{BOLD}{YELLOW}  ⌚  Whoop{R}  {DIM}({_format_num(whoop['days'])} days){R}")
        print(f"      Recovery:  {BOLD}{WHITE}{int(whoop['avg_recovery'] or 0)}%{R} avg")
        print(f"      HRV:       {BOLD}{WHITE}{whoop['avg_hrv'] or 'N/A'} ms{R} avg")
        print(f"      Resting:   {BOLD}{WHITE}{int(whoop['avg_rhr'] or 0)} BPM{R} avg")
        print()

    # ── Date range ────────────────────────────────────────────────────────────
    dates = conn.execute("""
        SELECT MIN(recorded_at) as first, MAX(recorded_at) as last
        FROM heart_rate
    """).fetchone()

    if dates["first"]:
        first = dates["first"][:10]
        last = dates["last"][:10]
        print(f"{DIM}  Data range: {first} → {last}{R}")

    print()
    print(f"{BOLD}{CYAN}  ╔══════════════════════════════════════════╗{R}")
    print(f"{BOLD}{CYAN}  ║  Database: ~/.leo-health/leo.db          ║{R}")
    print(f"{BOLD}{CYAN}  ║  Zero network requests. 100% local.      ║{R}")
    print(f"{BOLD}{CYAN}  ╚══════════════════════════════════════════╝{R}")
    print()

    conn.close()

if __name__ == "__main__":
    main()
