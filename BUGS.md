# Leo Health - Bug Tracker

## Fixed Bugs

### BUG-001: CSS vars not rendering in canvas charts
**Status:** FIXED (`4516e5c`)
**Symptom:** Chart lines/bars invisible because `var(--color)` doesn't work in `<canvas>` 2D context.
**Root cause:** Canvas API requires raw hex/rgb strings, not CSS custom properties.
**Fix:** Replaced all CSS variable references in canvas draw calls with hex literals.

### BUG-002: Wrong Apple Health sleep stage names
**Status:** FIXED (`b171de5`)
**Symptom:** Sleep chart showed no stage data despite records in DB.
**Root cause:** SQL filtered for `stage='deep'` but Apple Health stores `stage='asleepdeep'` (the lowercased enum suffix after stripping the `HKCategoryValueSleepAnalysis` prefix).
**Fix:** Updated SQL `IN` clause to use correct stage names: `asleepdeep`, `asleeprem`, `asleepcore`, `asleepunspecified`.

### BUG-003: Sleep chart color stacking incorrect
**Status:** FIXED (`3c6dba0`)
**Symptom:** Stacked bars had wrong visual order, colors overlapped incorrectly.
**Fix:** Corrected stacking order: awake on top, then light, REM, deep at bottom.

### BUG-004: Whoop sleep not appearing in dashboard
**Status:** FIXED (`c802b52`)
**Symptom:** Whoop sleep data existed in DB but dashboard showed no sleep chart.
**Root cause:** The Whoop/Oura sleep query (path 1 in `api_sleep`) had a condition that didn't match how Whoop stages were stored.
**Fix:** Corrected the WHERE clause for Whoop/Oura sleep path.

### BUG-005: Light sleep inflation from asleepunspecified
**Status:** FIXED (`6d9162f`, `69025a8`)
**Symptom:** Light sleep showed 12-15+ hours. Apple Watch writes a long `asleepunspecified` umbrella record covering the entire sleep session AND separate granular stage records (deep/REM/core). Summing all of them double-counts.
**Fix:** When a device has any deep/REM/core records, exclude `asleepunspecified` entirely and use `asleepcore` as "light sleep".

### BUG-006: Third-party apps inflating sleep data
**Status:** FIXED (`0abbee3`)
**Symptom:** Apps like AutoSleep also write sleep stage records to Apple Health under their own device name, causing combined totals when multiple devices are summed.
**Fix:** Device selection priority: prefer devices whose name contains "watch" (the physical Apple Watch). Among same-class devices, pick the one with the most deep+REM hours.

### BUG-007: GPX route parsing with closed ZipFile handle
**Status:** FIXED (`94f477c`)
**Symptom:** Workout route maps failed to load — GPX files were read after the ZipFile context manager had closed.
**Fix:** Read GPX content within the `with zipfile.ZipFile(...)` block.

### BUG-008: Duplicate rows from re-importing Apple Health export
**Status:** FIXED (`a0a453f`, `c453f75`)
**Symptom:** Each `leo import` re-inserted all records because the sleep table had no UNIQUE constraint, making `INSERT OR IGNORE` ineffective.
**Fix:** Added `_migrate_sleep_dedup()` migration that: (1) deletes duplicate rows keeping MIN(id) per (source, stage, start, end, device) group, (2) creates a UNIQUE expression index for future dedup. Migration runs at both import time and dashboard startup.

### BUG-009: Sleep double-counting from overlapping intervals
**Status:** FIXED (`95cd658`)
**Symptom:** Even after dedup, sleep totals showed 15-17 hours. Apple Watch writes both short per-cycle stage segments (~30 min) AND longer processed blocks covering the same time range. These are unique rows (different start/end) so dedup doesn't catch them, but SUM() double-counts the overlap.
**Root cause:** The SQL `SUM(CASE WHEN stage='asleepcore' THEN duration END)` naively adds all segment durations without checking for time overlap.
**Fix:** Replaced SQL SUM aggregation with Python interval-merging. For each (date, device, stage), segments are sorted by start time, overlapping/touching intervals are merged, and only the union of covered time is measured.

## Open Bugs

### BUG-010: install.sh shell compatibility
**Status:** OPEN (partially fixed in `7d4fe37`)
**Symptom:** `install.sh` may not work correctly on all shell environments.
**Notes:** A fix was applied for zsh, bash, and fish, but edge cases may remain.

### BUG-011: Sleep date assignment for cross-midnight sessions
**Status:** OPEN (low priority)
**Symptom:** A sleep session starting at 11pm Jan 24 and ending at 7am Jan 25 may have its pre-midnight segments grouped under Jan 24 and post-midnight segments under Jan 25, splitting one night across two dates.
**Root cause:** `date(recorded_at)` uses the calendar date of `startDate` for each individual segment, not the "sleep night" concept.
**Impact:** Minor — most segments start after midnight so they land on the correct "morning" date. The interval merger handles correctness within each date bucket.

## Feature Gaps (Not Bugs)

- **Oura activity data:** Parser detects activity CSVs but returns None (placeholder for future `oura_activity` table)
- **Oura temperature/recovery/balance:** Stored in DB (`oura_readiness` table) but not displayed as individual dashboard cards
- **Heart rate / HRV dedup:** The `heart_rate` and `hrv` tables also lack UNIQUE constraints and may accumulate duplicates on re-import (same pattern as BUG-008 but for HR/HRV)
