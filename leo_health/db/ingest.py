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


# ── Fitbit ingest ─────────────────────────────────────────────────────────────

def ingest_fitbit(data: dict, db_path: str = DEFAULT_DB_PATH) -> dict:
    """
    Write parsed Fitbit data to the database.

    Args:
        data: Output from fitbit.parse()
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


# ── Oura ingest ───────────────────────────────────────────────────────────────

def ingest_oura(data: dict, db_path: str = DEFAULT_DB_PATH) -> dict:
    """
    Write parsed Oura Ring data to the database.

    Args:
        data: Output from oura.parse() or oura.parse_folder()
        db_path: Path to SQLite database

    Returns:
        Dict with counts of inserted rows per table
    """
    conn = create_schema(db_path)
    counts = {}

    try:
        counts["oura_readiness"] = _insert_many(conn, "oura_readiness", data.get("readiness", []))
        counts["sleep"] = _insert_many(conn, "sleep", data.get("sleep", []))
        counts["heart_rate"] = _insert_many(conn, "heart_rate", data.get("heart_rate", []))
        counts["hrv"] = _insert_many(conn, "hrv", data.get("hrv", []))
        conn.commit()
    finally:
        conn.close()

    return counts


# ── Combined ingest ───────────────────────────────────────────────────────────

def ingest_all(
    apple_health_zip: Optional[str] = None,
    whoop_csv: Optional[str] = None,
    whoop_folder: Optional[str] = None,
    fitbit_zip: Optional[str] = None,
    oura_csv: Optional[str] = None,
    oura_folder: Optional[str] = None,
    db_path: str = DEFAULT_DB_PATH,
) -> dict:
    """
    One-shot ingest from any combination of sources.

    Args:
        apple_health_zip: Path to Apple Health export.zip
        whoop_csv: Path to a single Whoop CSV file
        whoop_folder: Path to folder of Whoop CSV files
        fitbit_zip: Path to Fitbit data export ZIP
        oura_csv: Path to a single Oura CSV file (readiness, sleep, or activity)
        oura_folder: Path to folder of Oura CSV files
        db_path: Path to SQLite database

    Returns:
        Nested dict: { 'apple_health': {...counts}, 'whoop': {...counts},
                       'fitbit': {...counts}, 'oura': {...counts} }

    Example:
        >>> results = ingest_all(
        ...     apple_health_zip="~/Downloads/export.zip",
        ...     oura_folder="~/Downloads/oura/",
        ... )
        >>> print(results)
    """
    from ..parsers import apple_health, whoop as whoop_parser, fitbit as fitbit_parser, oura as oura_parser

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

    if fitbit_zip:
        print(f"Parsing Fitbit export: {fitbit_zip}")
        f_data = fitbit_parser.parse(fitbit_zip)
        f_counts = ingest_fitbit(f_data, db_path)
        results["fitbit"] = f_counts
        total = sum(f_counts.values())
        print(f"  ✓ {total:,} records ingested")

    if oura_folder:
        print(f"Parsing Oura exports from folder: {oura_folder}")
        o_data = oura_parser.parse_folder(oura_folder)
        o_counts = ingest_oura(o_data, db_path)
        results["oura"] = o_counts
        total = sum(o_counts.values())
        print(f"  ✓ {total:,} records ingested")
    elif oura_csv:
        print(f"Parsing Oura CSV: {oura_csv}")
        o_data = oura_parser.parse(oura_csv)
        o_counts = ingest_oura(o_data, db_path)
        results["oura"] = o_counts
        total = sum(o_counts.values())
        print(f"  ✓ {total:,} records ingested")

    return results
