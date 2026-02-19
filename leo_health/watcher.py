"""
Leo Core — Auto Watcher
Monitors your Downloads folder for new Apple Health or Whoop exports
and automatically ingests them into ~/.leo-health/leo.db

Works on: macOS + Linux

Usage:
    python -m leo_health.watcher              # watches ~/Downloads
    python -m leo_health.watcher --folder ~/Desktop

ZERO network imports. Stdlib only.
"""

import os
import time
import hashlib
import argparse
import platform
import subprocess
from pathlib import Path
from datetime import datetime

from .db.ingest import ingest_apple_health, ingest_whoop
from .parsers import apple_health, whoop as whoop_parser


# ── Config ────────────────────────────────────────────────────────

WATCH_FOLDER = Path.home() / "Downloads"
PROCESSED_LOG = Path.home() / ".leo-health" / "processed.txt"
CHECK_INTERVAL = 10  # seconds between scans
SILENT = False       # set True to disable notifications


# ── Platform detection ────────────────────────────────────────────

SYSTEM = platform.system()  # 'Darwin' or 'Linux'


# ── Notifications ─────────────────────────────────────────────────

def _notify(title: str, message: str):
    """
    Send a desktop notification.
    macOS: uses osascript
    Linux: uses notify-send (install: sudo apt install libnotify-bin)
    Falls back silently if neither is available.
    """
    if SILENT:
        return
    try:
        if SYSTEM == "Darwin":
            script = f'display notification "{message}" with title "{title}" sound name "Glass"'
            subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
        elif SYSTEM == "Linux":
            subprocess.run(
                ["notify-send", "--app-name=Leo Health", title, message],
                check=False,
                capture_output=True
            )
    except Exception:
        pass  # Fail silently — notifications are nice-to-have


# ── File fingerprinting ───────────────────────────────────────────

def _file_hash(filepath: str) -> str:
    """MD5 of first 64KB — fast fingerprint without reading whole file."""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        h.update(f.read(65536))
    return h.hexdigest()


def _load_processed() -> set:
    """Load set of already-processed file hashes."""
    if not PROCESSED_LOG.exists():
        return set()
    return set(PROCESSED_LOG.read_text().splitlines())


def _mark_processed(file_hash: str):
    """Record a file hash so we never process it twice."""
    os.makedirs(PROCESSED_LOG.parent, exist_ok=True)
    with open(PROCESSED_LOG, "a") as f:
        f.write(file_hash + "\n")


# ── File detection ────────────────────────────────────────────────

def _is_apple_health_export(filepath: Path) -> bool:
    """Check if a file looks like an Apple Health export.zip"""
    if filepath.suffix.lower() != ".zip":
        return False
    name = filepath.name.lower()
    return "export" in name or "apple_health" in name or "health" in name


def _is_whoop_export(filepath: Path) -> bool:
    """Check if a file looks like a Whoop CSV export."""
    if filepath.suffix.lower() != ".csv":
        return False
    name = filepath.name.lower()
    return "whoop" in name or "recovery" in name or "strain" in name


def _is_oura_export(filepath: Path) -> bool:
    """Check if a file looks like an Oura Ring export."""
    if filepath.suffix.lower() not in (".json", ".csv"):
        return False
    name = filepath.name.lower()
    return "oura" in name or "readiness" in name


def _is_file_ready(filepath: Path) -> bool:
    """
    Check if a file has finished copying.
    AirDrop and network transfers arrive mid-write.
    We verify file size is stable over 2 seconds.
    """
    try:
        size1 = filepath.stat().st_size
        time.sleep(2)
        size2 = filepath.stat().st_size
        return size1 == size2 and size1 > 0
    except OSError:
        return False


# ── Processors ────────────────────────────────────────────────────

def _process_apple_health(filepath: Path) -> dict:
    """Parse and ingest an Apple Health export.zip"""
    print(f"  📱 Apple Health export detected: {filepath.name}")
    _notify("Leo Health", f"Parsing {filepath.name}...")

    data = apple_health.parse(str(filepath))
    counts = ingest_apple_health(data)
    total = sum(counts.values())

    summary = (
        f"✓ {counts.get('heart_rate', 0):,} heart rate  "
        f"· {counts.get('hrv', 0):,} HRV  "
        f"· {counts.get('sleep', 0):,} sleep  "
        f"· {counts.get('workouts', 0):,} workouts"
    )
    print(f"  {summary}")
    _notify("Leo Health ✓", f"Done — {total:,} records added")
    return counts


def _process_whoop(filepath: Path) -> dict:
    """Parse and ingest a Whoop CSV export."""
    print(f"  ⌚ Whoop export detected: {filepath.name}")
    _notify("Leo Health", f"Parsing {filepath.name}...")

    data = whoop_parser.parse(str(filepath))
    counts = ingest_whoop(data)
    total = sum(counts.values())

    print(f"  ✓ {total:,} Whoop records ingested")
    _notify("Leo Health ✓", f"Done — {total:,} Whoop records added")
    return counts


# ── Scanner ───────────────────────────────────────────────────────

def scan_once(watch_folder: Path, processed: set) -> set:
    """
    Scan the watch folder once and process any new health exports.
    Returns updated set of processed hashes.
    """
    try:
        entries = list(watch_folder.iterdir())
    except PermissionError:
        return processed

    for entry in entries:
        if not entry.is_file():
            continue

        is_apple = _is_apple_health_export(entry)
        is_whoop = _is_whoop_export(entry)
        is_oura = _is_oura_export(entry)

        if not (is_apple or is_whoop or is_oura):
            continue

        if not _is_file_ready(entry):
            continue

        try:
            fhash = _file_hash(str(entry))
        except OSError:
            continue

        if fhash in processed:
            continue

        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"\n[{timestamp}] New file detected: {entry.name}")

        try:
            if is_apple:
                _process_apple_health(entry)
            elif is_whoop:
                _process_whoop(entry)
            elif is_oura:
                print(f"  🔵 Oura export detected — parser coming soon!")
                _notify("Leo Health", "Oura parser coming in next update!")

            _mark_processed(fhash)
            processed.add(fhash)

        except Exception as e:
            print(f"  ⚠️  Error processing {entry.name}: {e}")
            _notify("Leo Health ⚠️", f"Error parsing {entry.name}")

    return processed


# ── Main loop ─────────────────────────────────────────────────────

def watch(folder: Path = WATCH_FOLDER):
    """
    Start watching a folder for new health exports.
    Works on macOS and Linux.
    Runs until interrupted with Ctrl+C.
    """
    os.makedirs(Path.home() / ".leo-health", exist_ok=True)

    platform_note = "AirDrop exports here" if SYSTEM == "Darwin" else "Copy exports here"

    print(f"""
╔═══════════════════════════════════════════╗
║          Leo Health — Watcher             ║
║          {SYSTEM:<33}║
╚═══════════════════════════════════════════╝
  Watching:  {folder}
  Database:  ~/.leo-health/leo.db
  Platform:  {SYSTEM}

  {platform_note} and Leo will
  automatically parse them.

  Supported: Apple Health .zip · Whoop .csv · Oura .json

  Press Ctrl+C to stop.
""")

    _notify("Leo Health", f"Watcher started on {SYSTEM}")
    processed = _load_processed()

    try:
        while True:
            processed = scan_once(folder, processed)
            time.sleep(CHECK_INTERVAL)
    except KeyboardInterrupt:
        print("\n\nWatcher stopped.")


# ── CLI entry ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Leo Health Watcher — auto-ingest health exports (macOS + Linux)"
    )
    parser.add_argument(
        "--folder",
        default=str(WATCH_FOLDER),
        help=f"Folder to watch (default: {WATCH_FOLDER})"
    )
    parser.add_argument(
        "--silent",
        action="store_true",
        help="Disable desktop notifications"
    )
    args = parser.parse_args()

    global SILENT
    if args.silent:
        SILENT = True

    watch(Path(args.folder))


if __name__ == "__main__":
    main()
