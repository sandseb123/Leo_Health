# Leo Health 🫀

> Your Apple Health and Whoop data, as a SQL database. In 60 seconds.

Apple Health locks your biometrics in a 4GB XML file. Whoop buries yours in CSVs with inconsistent column names. Leo Core parses both in under 60 seconds and writes everything to a single, normalized SQLite database — Heart Rate, Sleep, Workouts, HRV, Recovery Score, all queryable with standard SQL.

**Zero network requests. Runs locally. MIT licensed.**

![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue)
![License MIT](https://img.shields.io/badge/license-MIT-green)
![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen)
![Status](https://img.shields.io/badge/status-active-success)

---

## What it looks like

```
╔══════════════════════════════════════════════╗
║           Leo Health — Status                ║
╚══════════════════════════════════════════════╝

  ❤️  Heart Rate  (324,116 readings)
      Average:  84 BPM  (min 32 · max 199)
      Resting:  56 BPM  (1,023 readings)

  💜  HRV  (6,519 readings)
      apple_health     78.3 ms  (min 12.4 · max 422.7)

  😴  Sleep  (12,195 sessions)
      In Bed          ████████████████  6,779
      Core Sleep      ████░░░░░░░░░░░░  2,050
      REM             █░░░░░░░░░░░░░░░  796
      Deep Sleep      █░░░░░░░░░░░░░░░  589

  🏃  Workouts  (1,344 total)
      running               ████████████  570  40min avg
      strength training     ██████████░░  476  41min avg
      walking               █████░░░░░░░  243  63min avg

  Data range: 2021-06-18 → 2026-02-16

  ╔══════════════════════════════════════════╗
  ║  Database: ~/.leo-health/leo.db          ║
  ║  Zero network requests. 100% local.      ║
  ╚══════════════════════════════════════════╝
```

---

## Install

```bash
git clone https://github.com/sandseb123/Leo_Health.git
cd Leo_Health
bash install.sh
```

That's it. Two commands are now available anywhere on your Mac:

```bash
leo          # view your health dashboard
leo-watch    # start watching Downloads for new exports
```

---

## Get your data in

**Step 1 — Export from Apple Health (iPhone):**
1. Open the Health app
2. Tap your profile picture → **Export All Health Data**
3. AirDrop it to your Mac

**Step 2 — Start the watcher:**
```bash
leo-watch
```

**Step 3 — AirDrop your export.zip**

Leo detects it within 10 seconds, parses it automatically, and sends you a macOS notification when done. Your database is updated — no commands needed.

**For Whoop:** Open Whoop app → Profile → Export Data → check email for CSVs → AirDrop them to your Mac. Leo auto-detects and ingests them too.

**For Oura:** Go to [oura.com](https://oura.com) → Account → Data Export → Download, or open the Oura app → Profile → Export. You'll get CSV files for sleep, readiness, and activity — AirDrop them to your Mac and Leo handles the rest.

---

## Linux Support

Leo Core runs on Linux too. AirDrop isn't available, but there are easy alternatives for getting your iPhone export to a Linux machine:

**Transfer via LocalSend (recommended — wireless, no account needed):**
1. Install [LocalSend](https://localsend.org) on both your iPhone and Linux machine
2. Open Health app → tap your profile picture → **Export All Health Data**
3. Share → LocalSend → select your Linux machine
4. File lands in `~/Downloads/` automatically
5. `leo-watch` detects it within 10 seconds

**Transfer via email or Google Drive:**
1. Open Health app → tap your profile picture → **Export All Health Data**
2. Share → Mail or Google Drive → download to `~/Downloads/` on your Linux machine
3. `leo-watch` picks it up automatically

**Whoop on Linux:** same as Mac — export CSVs are emailed to you, download them to `~/Downloads/`

**Oura on Linux:** go to [oura.com](https://oura.com) → Account → Data Export → download directly in your browser

---

## Auto-Ingest via AirDrop ✨

Leo watches your Downloads folder and automatically parses any health export the moment it arrives — no commands needed after setup.

- Checks for new files every 10 seconds
- Uses ~8MB RAM, near-zero CPU while idle
- Never processes the same file twice
- Sends a macOS notification when ingestion completes
- Runs automatically on login (optional)

**Run Leo automatically on every login:**
```bash
cp com.leohealth.watcher.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.leohealth.watcher.plist
```

---

## Query your data

```bash
sqlite3 ~/.leo-health/leo.db
```

```sql
-- Last 7 days of Whoop recovery
SELECT recorded_at, recovery_score, hrv_ms, resting_heart_rate
FROM whoop_recovery
ORDER BY recorded_at DESC LIMIT 7;

-- HRV by source (Apple Watch vs Whoop)
SELECT source, ROUND(AVG(value), 1) as avg_hrv, COUNT(*) as readings
FROM hrv GROUP BY source;

-- Sleep stage breakdown
SELECT stage, COUNT(*) as sessions
FROM sleep GROUP BY stage ORDER BY sessions DESC;

-- Top workouts by volume
SELECT activity, COUNT(*) as sessions, ROUND(AVG(calories), 0) as avg_cal
FROM workouts GROUP BY activity ORDER BY sessions DESC;
```

---

## What gets parsed

### Apple Health (`export.zip`)
| Data | Table | Metrics |
|------|-------|---------|
| Heart Rate | `heart_rate` | BPM, resting HR, walking avg |
| HRV | `hrv` | SDNN in milliseconds |
| Sleep | `sleep` | REM, Deep, Core, Awake stages |
| Workouts | `workouts` | Activity, duration, distance, calories |

### Whoop (CSV exports)
| Data | Table | Metrics |
|------|-------|---------|
| Recovery | `whoop_recovery` | Score, HRV, resting HR, SpO2, respiratory rate |
| Strain | `whoop_strain` | Day strain, calories, max/avg HR |
| Sleep | `sleep` | Performance %, time in bed, stages, consistency score |

### Oura Ring (CSV exports)
| Data | Table | Metrics |
|------|-------|---------|
| Readiness | `oura_readiness` | Score, HRV balance, resting HR, temp deviation |
| Sleep | `sleep` | REM, Deep, Light, Awake hours, efficiency % |
| HRV | `hrv` | RMSSD in milliseconds |

---

## Privacy

Leo Core contains zero network code. Verify it yourself:

```bash
grep -r "import urllib\|import http\|import requests\|import socket" leo_health/
# Returns nothing. Zero network imports.
```

Your data lives in `~/.leo-health/leo.db` and never leaves your machine.

---

## Who this is for

**Leo Core is a developer tool.** If you're comfortable with Terminal, git clone and `bash install.sh` gets you running in 2 minutes.

**Not a developer?** Leo Pro (coming soon) is a one-click macOS app with a full dashboard — no Terminal required.

---

## Project structure

```
leo_health/
├── parsers/
│   ├── apple_health.py   # SAX streaming parser for export.zip
│   ├── whoop.py          # Auto-detecting CSV parser
│   └── oura.py           # Oura Ring CSV parser (readiness, sleep, activity)
├── db/
│   ├── schema.py         # SQLite schema — 6 tables
│   └── ingest.py         # Writes both sources to unified DB
├── status.py             # leo command — pretty terminal dashboard
└── watcher.py            # leo-watch command — auto-ingest on AirDrop
tests/
└── test_parsers.py
install.sh                # One-command installer for macOS
pyproject.toml
```

---

## Roadmap

- [x] Apple Health XML parser
- [x] Whoop CSV parser
- [x] Normalized SQLite schema
- [x] `leo` status dashboard
- [x] `leo-watch` auto-ingest watcher
- [x] AirDrop → auto-parse workflow
- [x] Oura Ring CSV support
- [ ] Fitbit CSV support
- [ ] Garmin support
- [ ] Leo Pro — AI Health Coach *(local LLM, 100% private)*
- [ ] Leo Pro — macOS app *(no Terminal required)*

---

## Contributing

Good first issues:
- Add Fitbit CSV parser
- Add Garmin `.fit` file support
- Improve test coverage

See [`good first issue`](../../issues?q=is%3Aissue+label%3A%22good+first+issue%22) labels to get started.

---

## License

**Leo Core** — MIT. Free to use, modify, and distribute.

**Leo Pro** (AI Coach + Dashboard) is a separate commercial product — coming soon.

---

<p align="center">Built by <a href="https://github.com/sandseb123">sandseb123</a></p>
