"""
Leo Core — DB Ingest
Writes normalized parser output into the Leo SQLite database.
ZERO network imports. Stdlib only.
"""

import sqlite3
from typing import Optional
from .schema import create_schema, DEFAULT_DB_PATH


# ── Insert helpers ────────────────────────────────────────────────────────────

def _insert_many(conn: sqlite3.Connection, table: str, rows: list[dict]) -> int:
    """
    Bulk insert rows into a table. Skips rows missing required fields.
    Returns number of rows inserted.
    """
    if not rows:
        return 0

    # Build INSERT from first row's keys
    keys = list(rows[0].keys())
    placeholders = ", ".join("?" for _ in keys)
    cols = ", ".join(keys)
    sql = f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({placeholders})"

    values = [tuple(row.get(k) for k in keys) for row in rows]
    conn.executemany(sql, values)
    return len(rows)


# ── Apple Health ingest ───────────────────────────────────────────────────────

def ingest_apple_health(data: dict, db_path: str = DEFAULT_DB_PATH) -> dict:
    """
    Write parsed Apple Health data to the database.

    Args:
        data: Output from apple_health.parse()
        db_path: Path to SQLite database

    Returns:
        Dict with counts of inserted rows per table
    """
    conn = create_schema(db_path)
    counts = {}

    try:
        counts["heart_rate"] = _insert_many(conn, "heart_rate", data.get("heart_rate", []))
        counts["hrv"] = _insert_many(conn, "hrv", data.get("hrv", []))
        counts["sleep"] = _insert_many(conn, "sleep", data.get("sleep", []))
        counts["workouts"] = _insert_many(conn, "workouts", data.get("workouts", []))
        conn.commit()
    finally:
        conn.close()

    return counts


# ── Whoop ingest ──────────────────────────────────────────────────────────────

def ingest_whoop(data: dict, db_path: str = DEFAULT_DB_PATH) -> dict:
    """
    Write parsed Whoop data to the database.

    Args:
        data: Output from whoop.parse() or whoop.parse_folder()
        db_path: Path to SQLite database

    Returns:
        Dict with counts of inserted rows per table
    """
    conn = create_schema(db_path)
    counts = {}

    try:
        counts["whoop_recovery"] = _insert_many(conn, "whoop_recovery", data.get("recovery", []))
        counts["whoop_strain"] = _insert_many(conn, "whoop_strain", data.get("strain", []))
        counts["hrv"] = _insert_many(conn, "hrv", data.get("hrv", []))

        # Whoop sleep rows need a recorded_at at minimum
        sleep_rows = [r for r in data.get("sleep", []) if r.get("recorded_at")]
        counts["sleep"] = _insert_many(conn, "sleep", sleep_rows)
        conn.commit()
    finally:
        conn.close()

    return counts


# ── Combined ingest ───────────────────────────────────────────────────────────

def ingest_all(
    apple_health_zip: Optional[str] = None,
    whoop_csv: Optional[str] = None,
    whoop_folder: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    """
    One-shot ingest from any combination of sources.

    Args:
        apple_health_zip: Path to Apple Health export.zip
        whoop_csv: Path to a single Whoop CSV file
        whoop_folder: Path to folder of Whoop CSV files
        db_path: Path to SQLite database

    Returns:
        Nested dict: { 'apple_health': {...counts}, 'whoop': {...counts} }

    Example:
        >>> results = ingest_all(
        ...     apple_health_zip="~/Downloads/export.zip",
        ...     whoop_folder="~/Downloads/whoop/"
        ... )
        >>> print(results)
    """
    from ..parsers import apple_health, whoop as whoop_parser

    results = {}

    if apple_health_zip:
        print(f"Parsing Apple Health export: {apple_health_zip}")
        ah_data = apple_health.parse(apple_health_zip)
        ah_counts = ingest_apple_health(ah_data, db_path)
        results["apple_health"] = ah_counts
        total = sum(ah_counts.values())
        print(f"  ✓ {total:,} records ingested")

    if whoop_folder:
        print(f"Parsing Whoop exports from folder: {whoop_folder}")
        w_data = whoop_parser.parse_folder(whoop_folder)
        w_counts = ingest_whoop(w_data, db_path)
        results["whoop"] = w_counts
        total = sum(w_counts.values())
        print(f"  ✓ {total:,} records ingested")
    elif whoop_csv:
        print(f"Parsing Whoop CSV: {whoop_csv}")
        w_data = whoop_parser.parse(whoop_csv)
        w_counts = ingest_whoop(w_data, db_path)
        results["whoop"] = w_counts
        total = sum(w_counts.values())
        print(f"  ✓ {total:,} records ingested")

    return results
