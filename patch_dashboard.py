#!/usr/bin/env python3
"""
patch_dashboard.py  —  apply Leo Health dashboard fixes locally.
Run once from inside your Leo_Health folder:

    cd ~/Leo_Health
    python3 patch_dashboard.py
"""
import re, sys, os

TARGET = os.path.join(os.path.dirname(__file__), "leo_health", "dashboard.py")

if not os.path.exists(TARGET):
    print(f"✗  Not found: {TARGET}")
    print("   Run this script from inside your Leo_Health folder.")
    sys.exit(1)

with open(TARGET, "r") as f:
    src = f.read()

changes = 0

# ── Fix 1: sleep summary query (julianday can't parse timezone offsets) ────────
OLD1 = '''                       SUM(CASE WHEN stage IN (\'deep\',\'rem\',\'core\',\'asleep\')
                                 AND end IS NOT NULL AND start IS NOT NULL
                           THEN (julianday(end)-julianday(start))*24 ELSE 0 END) AS hours
                FROM sleep WHERE recorded_at>=? AND source=\'apple_health\'
                GROUP BY d HAVING hours>0'''
NEW1 = '''                       COALESCE(SUM(CASE WHEN stage IN (\'deep\',\'rem\',\'core\',\'asleep\')
                           THEN (julianday(SUBSTR(end,1,19))-julianday(SUBSTR(start,1,19)))*24 END),0) AS hours
                FROM sleep WHERE recorded_at>=? AND source=\'apple_health\'
                  AND end IS NOT NULL AND start IS NOT NULL AND length(end)>=19 AND length(start)>=19
                GROUP BY d HAVING hours>0'''
if OLD1 in src:
    src = src.replace(OLD1, NEW1, 1); changes += 1
    print("  ✓  Fixed summary sleep query (julianday timezone)")
else:
    print("  –  Summary sleep query already patched or not found")

# ── Fix 2: main sleep chart query (julianday + HAVING alias bug) ──────────────
OLD2_PAT = re.compile(
    r'# Fall back to Apple Health per-stage rows.*?ORDER BY date\s*""", \(s,\)\)',
    re.DOTALL
)
NEW2 = '''# Fall back to Apple Health per-stage rows.
    # julianday() cannot parse timezone offsets (e.g. -05:00), so we strip
    # to plain YYYY-MM-DDTHH:MM:SS with SUBSTR before computing duration.
    return _q("""
        SELECT date(recorded_at) AS date,
               ROUND(COALESCE(SUM(CASE WHEN stage=\'deep\'
                   THEN (julianday(SUBSTR(end,1,19))-julianday(SUBSTR(start,1,19)))*24 END),0),2) AS deep,
               ROUND(COALESCE(SUM(CASE WHEN stage=\'rem\'
                   THEN (julianday(SUBSTR(end,1,19))-julianday(SUBSTR(start,1,19)))*24 END),0),2) AS rem,
               ROUND(COALESCE(SUM(CASE WHEN stage IN (\'core\',\'asleep\')
                   THEN (julianday(SUBSTR(end,1,19))-julianday(SUBSTR(start,1,19)))*24 END),0),2) AS light,
               ROUND(COALESCE(SUM(CASE WHEN stage=\'awake\'
                   THEN (julianday(SUBSTR(end,1,19))-julianday(SUBSTR(start,1,19)))*24 END),0),2) AS awake,
               0 AS efficiency
        FROM sleep
        WHERE recorded_at>=? AND source=\'apple_health\'
          AND stage NOT IN (\'in_bed\')
          AND end IS NOT NULL AND start IS NOT NULL
          AND length(end) >= 19 AND length(start) >= 19
        GROUP BY date(recorded_at)
        HAVING COALESCE(SUM(CASE WHEN stage IN (\'deep\',\'rem\',\'core\',\'asleep\')
                   THEN (julianday(SUBSTR(end,1,19))-julianday(SUBSTR(start,1,19)))*24 END),0) > 0
        ORDER BY date
    """, (s,))'''
if OLD2_PAT.search(src):
    src = OLD2_PAT.sub(NEW2, src, count=1); changes += 1
    print("  ✓  Fixed sleep chart query (julianday timezone + HAVING)")
else:
    print("  –  Sleep chart query already patched or not found")

# ── Fix 3: workouts query — individual records, not grouped ───────────────────
OLD3 = '''def api_workouts(days=30):
    return _q("""
        SELECT date(recorded_at) AS date, activity,
               ROUND(AVG(duration_minutes),0) AS duration,
               ROUND(AVG(calories),0)         AS calories,
               ROUND(AVG(distance_km),2)      AS distance_km,
               COUNT(*)                       AS count
        FROM workouts WHERE recorded_at>=?
        GROUP BY date(recorded_at), activity
        ORDER BY date DESC LIMIT 60
    """, (_since(days),))'''
NEW3 = '''def api_workouts(days=30):
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
    """, (_since(days),))'''
if OLD3 in src:
    src = src.replace(OLD3, NEW3, 1); changes += 1
    print("  ✓  Fixed workouts query (individual records)")
else:
    print("  –  Workouts query already patched or not found")

# ── Fix 4: color constants in JS (CSS vars don't work in Canvas API) ──────────
OLD4 = "// ── State & utils ─────────────────────────────────────────────────────────────\nlet days = 30;"
NEW4 = """// ── Colors (hex literals — CSS vars don't work in Canvas API) ─────────────────
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
let days = 30;"""
if "const C = {" in src:
    print("  –  Color constants already present")
elif OLD4 in src:
    src = src.replace(OLD4, NEW4, 1); changes += 1
    print("  ✓  Added color constants")

# ── Fix 5: replace var(--hr) etc. with C.xx in canvas calls ──────────────────
replacements = [
    ("color:'var(--hr)'",  "color:C.hr"),
    ("color:'var(--hrv)'", "color:C.hrv"),
    ("color:'var(--rec)'", "color:C.rec"),
    ("color:'var(--read)'","color:C.read"),
]
for old, new in replacements:
    if old in src:
        src = src.replace(old, new); changes += 1
        print(f"  ✓  Replaced {old!r} → {new!r}")

# ── Write back ────────────────────────────────────────────────────────────────
if changes:
    with open(TARGET, "w") as f:
        f.write(src)
    print(f"\n  ✅  {changes} fix(es) applied to {TARGET}")
    print("  Restart the dashboard:  python3 -m leo_health.dashboard")
else:
    print("\n  ✅  Nothing to patch — already up to date.")
