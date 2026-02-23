#!/usr/bin/env python3
"""
Leo Health Pro — Data Import
════════════════════════════
One command to import from Apple Health, Whoop, Oura, or Fitbit.

Usage examples
─────────────
  # Apple Health (most common — export.zip from iPhone)
  python3 import_data.py --apple ~/Downloads/export.zip

  # Whoop (email yourself the CSV from the Whoop app → Profile → Export)
  python3 import_data.py --whoop ~/Downloads/whoop_recovery.csv
  python3 import_data.py --whoop-folder ~/Downloads/whoop_exports/

  # Oura Ring
  python3 import_data.py --oura ~/Downloads/oura_readiness.csv
  python3 import_data.py --oura-folder ~/Downloads/oura_exports/

  # Fitbit
  python3 import_data.py --fitbit ~/Downloads/MyFitbitData.zip

  # Multiple sources at once
  python3 import_data.py --apple ~/Downloads/export.zip --whoop ~/Downloads/whoop.csv

  # Check what's already in your database
  python3 import_data.py --status
"""

import argparse
import os
import sys
from pathlib import Path


DB_PATH = os.path.join(Path.home(), ".leo-health", "leo.db")


def _check_file(path: str, label: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        print(f"✗ {label} not found: {p}")
        sys.exit(1)
    return str(p)


def cmd_status():
    """Print row counts for all tables in the database."""
    try:
        from leo_health.db.schema import get_stats
    except ImportError:
        print("✗ Could not import leo_health. Run from your Leo-Health-Pro directory.")
        sys.exit(1)

    if not Path(DB_PATH).exists():
        print(f"""
╔══════════════════════════════════════════╗
║          Leo Health — No Data Yet        ║
╚══════════════════════════════════════════╝
  Database not found at:
    {DB_PATH}

  Run this script with your health export to get started.
  Example (Apple Health):
    python3 import_data.py --apple ~/Downloads/export.zip
""")
        return

    stats = get_stats(DB_PATH)
    total = sum(stats.values())

    print(f"""
╔══════════════════════════════════════════╗
║       Leo Health Pro — Database          ║
╚══════════════════════════════════════════╝
  {DB_PATH}

  Table                  Rows
  ─────────────────────────────""")
    for table, count in stats.items():
        print(f"  {table:<24} {count:>8,}")
    print(f"  {'─'*33}")
    print(f"  {'TOTAL':<24} {total:>8,}")
    print()


def cmd_import(args):
    try:
        from leo_health.db.ingest import ingest_all
    except ImportError:
        print("✗ Could not import leo_health. Run from your Leo-Health-Pro directory.")
        sys.exit(1)

    kwargs = {"db_path": DB_PATH}

    if args.apple:
        kwargs["apple_health_zip"] = _check_file(args.apple, "Apple Health export")
    if args.whoop:
        kwargs["whoop_csv"] = _check_file(args.whoop, "Whoop CSV")
    if args.whoop_folder:
        kwargs["whoop_folder"] = _check_file(args.whoop_folder, "Whoop folder")
    if args.oura:
        kwargs["oura_csv"] = _check_file(args.oura, "Oura CSV")
    if args.oura_folder:
        kwargs["oura_folder"] = _check_file(args.oura_folder, "Oura folder")
    if args.fitbit:
        kwargs["fitbit_zip"] = _check_file(args.fitbit, "Fitbit export")

    if len(kwargs) == 1:  # only db_path
        print("No source specified. Run with --help to see options.")
        sys.exit(1)

    print(f"""
╔══════════════════════════════════════════╗
║       Leo Health Pro — Importing         ║
╚══════════════════════════════════════════╝
  Database: {DB_PATH}
""")

    results = ingest_all(**kwargs)

    print(f"""
╔══════════════════════════════════════════╗
║           Import Complete ✓              ║
╚══════════════════════════════════════════╝""")

    grand_total = 0
    for source, counts in results.items():
        source_total = sum(counts.values())
        grand_total += source_total
        print(f"\n  {source.replace('_', ' ').title()}:")
        for table, n in counts.items():
            if n > 0:
                print(f"    {table:<28} {n:>7,} rows")

    print(f"\n  Total records added: {grand_total:,}")
    print(f"""
  Next steps:
    1. Start the Leo Pro dashboard:
         cd ~/Leo-Health-Pro && python3 -m leo_health.dashboard

    2. Open: http://127.0.0.1:5380
""")


def main():
    parser = argparse.ArgumentParser(
        description="Leo Health Pro — Import health data from your devices",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument("--apple",        metavar="export.zip",
                        help="Apple Health export ZIP (from iPhone Health app → Export All Health Data)")
    parser.add_argument("--whoop",        metavar="recovery.csv",
                        help="Single Whoop CSV file")
    parser.add_argument("--whoop-folder", metavar="folder/",
                        help="Folder containing multiple Whoop CSV files")
    parser.add_argument("--oura",         metavar="readiness.csv",
                        help="Single Oura CSV file")
    parser.add_argument("--oura-folder",  metavar="folder/",
                        help="Folder containing multiple Oura CSV files")
    parser.add_argument("--fitbit",       metavar="MyFitbitData.zip",
                        help="Fitbit data export ZIP")
    parser.add_argument("--status",       action="store_true",
                        help="Show current database row counts and exit")

    args = parser.parse_args()

    if args.status:
        cmd_status()
    else:
        cmd_import(args)


if __name__ == "__main__":
    main()
