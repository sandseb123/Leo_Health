"""
Leo Core — SQLite Schema
Creates and manages the Leo Health database.
ZERO network imports. Stdlib only.
"""

import sqlite3
import os
from pathlib import Path


# ── Default DB location ───────────────────────────────────────────────────────

DEFAULT_DB_PATH = os.path.join(Path.home(), ".leo-health", "leo.db")


# ── Schema SQL ────────────────────────────────────────────────────────────────

SCHEMA = """
-- Heart rate records (Apple Health + future sources)
CREATE TABLE IF NOT EXISTS heart_rate (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,              -- 'apple_health'
    metric          TEXT NOT NULL,              -- 'heart_rate', 'resting_heart_rate', etc.
    value           REAL NOT NULL,              -- BPM
    unit            TEXT DEFAULT 'count/min',
    recorded_at     TEXT NOT NULL,              -- ISO8601
    device          TEXT,
    active_calories REAL,
    avg_cadence     REAL,
    avg_hr          REAL,
    max_hr          REAL,                       -- e.g. 'Apple Watch'
    created_at      TEXT DEFAULT (datetime('now'))
);

-- HRV records (Apple Health SDNN + Whoop HRV)
CREATE TABLE IF NOT EXISTS hrv (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,              -- 'apple_health' or 'whoop'
    metric          TEXT NOT NULL,              -- 'hrv_sdnn'
    value           REAL NOT NULL,              -- milliseconds
    unit            TEXT DEFAULT 'ms',
    recorded_at     TEXT NOT NULL,
    device          TEXT,
    active_calories REAL,
    avg_cadence     REAL,
    avg_hr          REAL,
    max_hr          REAL,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Sleep sessions (Apple Health stages + Whoop sleep)
CREATE TABLE IF NOT EXISTS sleep (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    stage           TEXT,                       -- 'asleep','rem','deep','core','in_bed','awake'
    start           TEXT,                       -- ISO8601 (Apple Health)
    end             TEXT,                       -- ISO8601 (Apple Health)
    recorded_at     TEXT NOT NULL,
    device          TEXT,
    active_calories REAL,
    avg_cadence     REAL,
    avg_hr          REAL,
    max_hr          REAL,
    -- Whoop-specific aggregates (NULL for Apple Health rows)
    sleep_performance_pct   REAL,
    time_in_bed_hours       REAL,
    light_sleep_hours       REAL,
    rem_sleep_hours         REAL,
    deep_sleep_hours        REAL,
    awake_hours             REAL,
    disturbances            REAL,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Workouts (Apple Health)
CREATE TABLE IF NOT EXISTS workouts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL,
    activity        TEXT NOT NULL,              -- 'running', 'cycling', etc.
    duration_minutes REAL,
    distance_km     REAL,
    calories        REAL,
    recorded_at     TEXT NOT NULL,
    end             TEXT,
    device          TEXT,
    active_calories REAL,
    avg_cadence     REAL,
    avg_hr          REAL,
    max_hr          REAL,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Whoop Recovery scores
CREATE TABLE IF NOT EXISTS whoop_recovery (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL DEFAULT 'whoop',
    recorded_at     TEXT NOT NULL,
    recovery_score  REAL,                       -- 0–100
    hrv_ms          REAL,
    resting_heart_rate REAL,
    spo2_pct        REAL,
    skin_temp_celsius REAL,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Whoop Strain scores
CREATE TABLE IF NOT EXISTS whoop_strain (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source          TEXT NOT NULL DEFAULT 'whoop',
    recorded_at     TEXT NOT NULL,
    day_strain      REAL,                       -- 0–21 Whoop strain scale
    calories        REAL,
    max_heart_rate  REAL,
    avg_heart_rate  REAL,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Oura Ring readiness scores
CREATE TABLE IF NOT EXISTS oura_readiness (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    source                  TEXT NOT NULL DEFAULT 'oura',
    recorded_at             TEXT NOT NULL,
    readiness_score         REAL,               -- 0–100
    hrv_balance             REAL,               -- ms RMSSD (relative to 3-month baseline)
    resting_heart_rate      REAL,               -- BPM
    temperature_deviation   REAL,               -- degrees C from personal baseline
    recovery_index          REAL,               -- 0–100
    activity_balance        REAL,               -- 0–100
    sleep_balance           REAL,               -- 0–100
    created_at              TEXT DEFAULT (datetime('now'))
);

-- GPS route points extracted from Apple Health workout-routes/*.gpx files
CREATE TABLE IF NOT EXISTS workout_routes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    workout_start   TEXT NOT NULL,  -- matches workouts.recorded_at (for joining)
    timestamp       TEXT NOT NULL,  -- GPS fix time (ISO8601)
    latitude        REAL NOT NULL,
    longitude       REAL NOT NULL,
    altitude_m      REAL,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_heart_rate_recorded_at ON heart_rate(recorded_at);
CREATE INDEX IF NOT EXISTS idx_hrv_recorded_at ON hrv(recorded_at);
CREATE INDEX IF NOT EXISTS idx_sleep_recorded_at ON sleep(recorded_at);
CREATE INDEX IF NOT EXISTS idx_workouts_recorded_at ON workouts(recorded_at);
CREATE INDEX IF NOT EXISTS idx_whoop_recovery_recorded_at ON whoop_recovery(recorded_at);
CREATE INDEX IF NOT EXISTS idx_whoop_strain_recorded_at ON whoop_strain(recorded_at);
CREATE INDEX IF NOT EXISTS idx_oura_readiness_recorded_at ON oura_readiness(recorded_at);
CREATE INDEX IF NOT EXISTS idx_heart_rate_source ON heart_rate(source);
CREATE INDEX IF NOT EXISTS idx_hrv_source ON hrv(source);
CREATE INDEX IF NOT EXISTS idx_workout_routes_start ON workout_routes(workout_start);
"""


# ── Public API ────────────────────────────────────────────────────────────────

def get_connection(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """
    Get a SQLite connection, creating the DB and schema if needed.

    Args:
        db_path: Path to SQLite database file. Defaults to ~/.leo-health/leo.db

    Returns:
        sqlite3.Connection with row_factory set for dict-like access
    """
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")   # Better concurrent read performance
    conn.execute("PRAGMA synchronous=NORMAL") # Faster writes, still crash-safe
    return conn


def _migrate_sleep_dedup(conn: sqlite3.Connection) -> None:
    """
    One-time idempotent migration:
      1. Delete duplicate sleep rows, keeping the earliest id per unique segment.
      2. Create a unique index so INSERT OR IGNORE prevents future duplicates.

    Root cause: without a UNIQUE constraint, every re-import of an Apple Health
    export creates additional copies of every sleep stage record, inflating totals
    (e.g. 3 imports × 3h core = 9h displayed light sleep).
    """
    conn.execute("""
        DELETE FROM sleep
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM sleep
            GROUP BY source,
                     COALESCE(stage,   ''),
                     COALESCE(start,   ''),
                     COALESCE(end,     ''),
                     COALESCE(device,  '')
        )
    """)
    conn.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_sleep_unique
        ON sleep (
            source,
            COALESCE(stage,  ''),
            COALESCE(start,  ''),
            COALESCE(end,    ''),
            COALESCE(device, '')
        )
    """)


def create_schema(db_path: str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """
    Create the Leo Health database schema.
    Safe to call multiple times — uses CREATE IF NOT EXISTS.

    Args:
        db_path: Path to SQLite database file

    Returns:
        Open sqlite3.Connection
    """
    conn = get_connection(db_path)
    conn.executescript(SCHEMA)
    _migrate_sleep_dedup(conn)
    conn.commit()
    return conn


def get_stats(db_path: str = DEFAULT_DB_PATH) -> dict:
    """
    Return row counts for all tables — used by the CLI status command.
    """
    conn = get_connection(db_path)
    tables = ["heart_rate", "hrv", "sleep", "workouts", "whoop_recovery", "whoop_strain", "oura_readiness"]
    stats = {}
    for table in tables:
        try:
            row = conn.execute(f"SELECT COUNT(*) as n FROM {table}").fetchone()
            stats[table] = row["n"]
        except sqlite3.OperationalError:
            stats[table] = 0
    conn.close()
    return stats
