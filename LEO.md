# LEO.md — Leo Health

This file provides context for AI assistants working on the Leo Health codebase.

---

## Project Overview

Leo Health is a local-first CLI tool that ingests personal health data from Apple Health (export.zip) and Whoop (CSV exports) into a local SQLite database, then displays a pretty terminal dashboard. The core design principle is **zero network requests** — all data stays on the user's machine.

- **Language**: Python 3.9+
- **Dependencies**: None (production code is stdlib only)
- **Dev dependency**: `pytest>=7.0`
- **Database**: SQLite3 at `~/.leo-health/leo.db`
- **Primary platform**: macOS (Linux works for parsing; macOS notifications require `osascript`)

---

## Repository Structure

```
Leo_Health/
├── leo_health/                  # Main package
│   ├── __init__.py
│   ├── status.py                # `leo` CLI command — pretty dashboard
│   ├── watcher.py               # `leo-watch` CLI command — auto-ingest daemon
│   ├── parsers/
│   │   ├── __init__.py
│   │   ├── apple_health.py      # SAX-based streaming XML parser
│   │   └── whoop.py             # CSV parser with auto-detection
│   └── db/
│       ├── __init__.py
│       ├── schema.py            # SQLite schema + connection helpers
│       └── ingest.py            # DB write logic
├── status.py                    # Root-level wrapper (legacy)
├── install.sh                   # macOS installer (modifies ~/.zshrc)
├── pyproject.toml               # PEP 517/518 build config
└── README.md                    # User-facing documentation
```

---

## Development Setup

### Installation (macOS)

```bash
git clone <repo>
cd Leo_Health
bash install.sh        # adds aliases to ~/.zshrc
```

The installer appends to `~/.zshrc`:
- `export PYTHONPATH="<install_dir>:$PYTHONPATH"`
- `alias leo="python3 -m leo_health.status"`
- `alias leo-watch="python3 -m leo_health.watcher"`

### Manual / Dev Setup

```bash
# Install as editable package (with dev deps)
pip install -e ".[dev]"

# Or run directly without installing
PYTHONPATH=. python3 -m leo_health.status
PYTHONPATH=. python3 -m leo_health.watcher
```

### Running Tests

```bash
pip install -e ".[dev]"
pytest
```

> **Note**: No tests exist yet. The test infrastructure (pytest) is configured but the test suite has not been written. Adding tests is a high-value contribution.

---

## CLI Commands

| Command | Module | Description |
|---------|--------|-------------|
| `leo` | `leo_health.status:main` | Display health dashboard |
| `leo-watch` | `leo_health.watcher:main` | Watch folder for new exports |
| `leo-watch --folder /path` | `leo_health.watcher:main` | Watch a custom folder |

---

## Architecture

### Data Flow

```
Apple Health export.zip   →  parsers/apple_health.py  →  db/ingest.py  →  leo.db
Whoop CSV export(s)       →  parsers/whoop.py          →  db/ingest.py  →  leo.db
                                                                              ↓
                                                                         status.py
                                                                        (dashboard)
```

### Module Responsibilities

#### `leo_health/parsers/apple_health.py`
- SAX streaming XML parser — handles 4 GB+ exports with low memory
- Entry point inside ZIP: `apple_health_export/export.xml`
- Maps Apple Health type identifiers to normalized names:
  - `HKQuantityTypeIdentifierHeartRate` → `heart_rate`
  - `HKQuantityTypeIdentifierRestingHeartRate` → `resting_heart_rate`
  - `HKQuantityTypeIdentifierHeartRateVariabilitySDNN` → `hrv_sdnn`
  - `HKCategoryTypeIdentifierSleepAnalysis` → sleep stages
  - `HKWorkoutActivityType*` → activity names
- Public API: `parse(zip_path) -> dict` and `parse_stream(zip_path) -> Generator`
- Date normalization: Apple's `"2024-01-15 08:23:44 -0500"` → ISO8601

#### `leo_health/parsers/whoop.py`
- Auto-detects CSV type (recovery / strain / sleep) from column headers
- Handles column name variations across Whoop app versions
- `parse(csv_path) -> dict` — single CSV file
- `parse_folder(folder_path) -> dict` — all CSVs in a directory
- HRV is extracted from recovery rows and also placed in a separate `hrv` list
- Date normalization: handles `YYYY-MM-DD HH:MM:SS`, `MM/DD/YYYY`, ISO8601

#### `leo_health/db/schema.py`
- `get_connection(db_path) -> sqlite3.Connection` — opens DB with WAL mode + NORMAL sync
- `create_schema(db_path) -> sqlite3.Connection` — idempotent schema creation
- `get_stats(db_path) -> dict` — returns row counts for all tables
- `conn.row_factory = sqlite3.Row` is always set (dict-like row access)

#### `leo_health/db/ingest.py`
- `ingest_apple_health(data, db_path) -> dict` — writes Apple Health parsed data
- `ingest_whoop(data, db_path) -> dict` — writes Whoop parsed data
- `ingest_all(apple_health_zip, whoop_csv, whoop_folder, db_path) -> dict` — combined entry point
- Uses `INSERT OR IGNORE` for idempotent upserts (deduplication is key-based)

#### `leo_health/watcher.py`
- Polls a folder every 10 seconds (`CHECK_INTERVAL`)
- File detection heuristics:
  - Apple Health: `.zip` files with "export", "apple_health", or "health" in name
  - Whoop: `.csv` files with "whoop", "recovery", "strain", or "sleep" in name
- File stability check: waits until file size is stable over 2 seconds (handles AirDrop mid-write)
- Deduplication: MD5 of first 64 KB stored in `~/.leo-health/processed.txt`
- macOS notifications via `osascript` (controlled by `SILENT = True` constant)

#### `leo_health/status.py`
- Reads from `~/.leo-health/leo.db` and prints formatted terminal output
- Uses ANSI escape codes for colors and Unicode block characters for bars
- Sections: Heart Rate, HRV, Sleep, Workouts, Whoop, Date Range

---

## Database Schema

**Location**: `~/.leo-health/leo.db`

**SQLite pragmas applied on every connection**:
```sql
PRAGMA journal_mode=WAL;      -- Better concurrent read performance
PRAGMA synchronous=NORMAL;    -- Faster writes, still crash-safe
```

### Tables

```sql
heart_rate(id, source, metric, value, unit, recorded_at, device, created_at)
-- source: 'apple_health'
-- metric: 'heart_rate' | 'resting_heart_rate' | 'walking_heart_rate_avg'
-- unit: 'count/min'

hrv(id, source, metric, value, unit, recorded_at, device, created_at)
-- source: 'apple_health' | 'whoop'
-- metric: 'hrv_sdnn'
-- unit: 'ms'

sleep(id, source, stage, start, end, recorded_at, device,
      sleep_performance_pct, time_in_bed_hours, light_sleep_hours,
      rem_sleep_hours, deep_sleep_hours, awake_hours, disturbances, created_at)
-- stage: 'asleep' | 'rem' | 'deep' | 'core' | 'in_bed' | 'awake'
-- Whoop-specific aggregate columns are NULL for Apple Health rows

workouts(id, source, activity, duration_minutes, distance_km, calories,
         recorded_at, end, device, created_at)
-- activity: 'running' | 'cycling' | 'walking' | 'swimming' | 'hiit' |
--           'strength_training' | 'yoga' | 'functional_strength'
-- distance_km converted from miles (Apple stores in miles)

whoop_recovery(id, source, recorded_at, recovery_score, hrv_ms,
               resting_heart_rate, spo2_pct, skin_temp_celsius, created_at)
-- recovery_score: 0–100

whoop_strain(id, source, recorded_at, day_strain, calories,
             max_heart_rate, avg_heart_rate, created_at)
-- day_strain: 0–21 (Whoop scale)
```

**Indexes**: All tables have an index on `recorded_at`. `heart_rate` and `hrv` also have an index on `source`.

---

## Key Conventions

### Code Style
- **Stdlib only** for all production code — never add third-party imports to `leo_health/`
- Module docstrings always start with `"""Leo Core — <Name>\n...\nZERO network imports. Stdlib only.\n"""`
- Private helpers prefixed with `_` (e.g., `_iso()`, `_insert_many()`)
- Public API functions have full docstrings with Args/Returns/Example sections

### Date Handling
- All dates stored as ISO8601 strings (not Unix timestamps)
- Both parsers have a local `_iso()` helper that normalizes source-specific formats
- Apple Health dates include timezone offsets; Whoop dates are typically local time

### Deduplication
- DB level: `INSERT OR IGNORE` — relies on SQLite's unique constraint enforcement
- File level: MD5 fingerprint of first 64 KB stored in `~/.leo-health/processed.txt`
- When adding new tables, always use `INSERT OR IGNORE` (not `INSERT OR REPLACE`)

### Adding a New Data Source
1. Create `leo_health/parsers/<source>.py` with a `parse()` function returning a normalized dict
2. Add corresponding table(s) to `leo_health/db/schema.py` (`SCHEMA` string)
3. Add an `ingest_<source>()` function in `leo_health/db/ingest.py`
4. Wire detection heuristics into `leo_health/watcher.py` (`_is_<source>_export()`)
5. Add a display section in `leo_health/status.py`

### Adding a New Apple Health Metric
- Add the `HKQuantityTypeIdentifier*` → internal name mapping to the appropriate dict in `_HealthHandler` (`HEART_RATE_TYPES`, `HRV_TYPES`, etc.)
- If it's a new metric category, add a new list to `_HealthHandler.__init__` and a new `elif` in `_handle_record`

### Adding a New Whoop CSV Column
- Update the relevant `_parse_*_row()` function in `whoop.py`
- Use the `or` chaining pattern for fallback column names (handles version differences)
- Headers are normalized via `_normalize_header()` before matching

---

## File Locations (Runtime)

| Path | Purpose |
|------|---------|
| `~/.leo-health/leo.db` | SQLite database |
| `~/.leo-health/processed.txt` | Hashes of already-ingested files |
| `~/Downloads/` | Default watch folder |

---

## No CI/CD

There is no CI/CD pipeline configured. There are no GitHub Actions, no Makefile, and no pre-commit hooks. Contributions should be tested manually:

```bash
# Sanity check — run the status command (requires existing DB)
python3 -m leo_health.status

# Parse a test file without writing to DB (use Python REPL)
python3 -c "
from leo_health.parsers import apple_health
data = apple_health.parse('path/to/export.zip')
print({k: len(v) for k, v in data.items()})
"
```

---

## Roadmap (from README)

- Fitbit and Garmin support
- Leo Pro AI Coach (requires network — would need a separate module to maintain zero-network guarantee in core)
- LaunchAgent plist for auto-startup on macOS login
