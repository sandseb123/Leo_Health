# Leo Health 🫀

> Your Apple Health, Whoop, and Oura data — unified in a local SQLite database. In 60 seconds.

Apple Health locks your biometrics in a 4GB XML file. Whoop buries yours in CSVs. Oura scatters data across endpoints. Leo Core parses all three in under 60 seconds and writes everything to a single, normalized SQLite database — Heart Rate, Sleep, Workouts, HRV, Recovery Score, Blood Oxygen — all queryable with standard SQL.

**Zero network requests. Runs locally. MIT licensed.**

![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue)
![License MIT](https://img.shields.io/badge/license-MIT-green)
![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen)
![Status](https://img.shields.io/badge/status-active-success)

---

## 🧠 Leo Max — AI Health Coach (Coming Soon)

Local LLM that analyses your data privately. Bloodwork history, wearable trends, medical literature — all on your Mac, nothing leaves your machine.

**[Join the waitlist →](https://sandseb123.github.io/Leo-Health-Core)**
Founding members get lifetime preferred pricing.

---

## What it looks like

```
## What it looks like

![Leo Health Dashboard](assets/dashboard-overview.png)

![Leo Health Sleep Breakdown](assets/dashboard-sleep.png)
```

---

## Install

```bash
git clone https://github.com/sandseb123/Leo-Health-Core.git
cd Leo-Health-Core
bash install.sh
```

Two commands are now available anywhere on your Mac:

```bash
leo          # view your health dashboard
leo-watch    # start watching Downloads for new exports
```

---

## Get your data in

**Apple Health (iPhone):**
1. Open the Health app
2. Tap your profile picture → **Export All Health Data**
3. AirDrop it to your Mac — Leo detects and parses it automatically

**Whoop:** Open Whoop app → Profile → Export Data → check email for CSVs → AirDrop to Mac

**Oura:** Go to [ouraring.com](https://ouraring.com) → Account → Data Export → Download → AirDrop to Mac

```bash
leo-watch    # start the watcher — detects exports within 10 seconds
```

---

## Linux Support

Leo Core runs on Linux too. AirDrop isn't available, but there are easy alternatives:

**Transfer via LocalSend (recommended — wireless, no account needed):**
1. Install [LocalSend](https://localsend.org) on both your iPhone and Linux machine
2. Export from Health app → Share → LocalSend → select your Linux machine
3. File lands in `~/Downloads/` automatically — `leo-watch` picks it up

**Transfer via email or Google Drive:**
1. Export from Health app → Share → Mail or Google Drive
2. Download to `~/Downloads/` on your Linux machine
3. `leo-watch` picks it up automatically

---

## Auto-Ingest via AirDrop ✨

Leo watches your Downloads folder and automatically parses any health export the moment it arrives — no commands needed after setup.

- Checks for new files every 10 seconds
- Uses ~8MB RAM, near-zero CPU while idle
- Never processes the same file twice
- Sends a macOS notification when ingestion completes

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
| Blood Oxygen | `blood_oxygen` | SpO₂ % |

### Whoop (CSV exports)
| Data | Table | Metrics |
|------|-------|---------|
| Recovery | `whoop_recovery` | Score, HRV, resting HR, SpO2 |
| Strain | `whoop_strain` | Day strain, calories, max/avg HR |
| Sleep | `sleep` | Performance %, time in bed, stages |

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

**Leo Core** is an open-source developer tool. If you're comfortable with Terminal, `git clone` and `bash install.sh` gets you running in 2 minutes. Free forever, MIT licensed.

**Leo Max** is the upcoming AI health coach layer — a local LLM that runs against your unified health database, cross-references medical literature, and lets you upload bloodwork PDFs to track lab panels over time. Nothing leaves your Mac. [Join the waitlist →](https://sandseb123.github.io/Leo-Health-Core)

---

## Project structure

```
leo_health/
├── parsers/
│   ├── apple_health.py   # SAX streaming parser for export.zip
│   ├── whoop.py          # Auto-detecting CSV parser
│   └── oura.py           # Oura Ring CSV parser
├── db/
│   ├── schema.py         # SQLite schema — 6 tables
│   └── ingest.py         # Unified ingest for all sources
├── status.py             # leo command — terminal dashboard
└── watcher.py            # leo-watch — auto-ingest on AirDrop
tests/
└── test_parsers.py
install.sh                # One-command installer for macOS + Linux
pyproject.toml
```

---

## Roadmap

- [x] Apple Health XML parser
- [x] Whoop CSV parser
- [x] Oura Ring CSV support
- [x] Normalized SQLite schema
- [x] `leo` terminal dashboard
- [x] `leo-watch` auto-ingest watcher
- [x] AirDrop → auto-parse workflow
- [x] Linux support
- [ ] Fitbit CSV support
- [ ] Garmin `.fit` support
- [ ] Leo Max — AI Health Coach *(local LLM, fully private)*
- [ ] Leo Max — bloodwork PDF/photo ingestion
- [ ] Leo Max — macOS app *(no Terminal required)*

---

## Contributing

Good first issues:
- Add Fitbit CSV parser
- Add Garmin `.fit` file support
- Add missing Whoop metrics to schema
- Improve test coverage

See [`good first issue`](../../issues?q=is%3Aissue+label%3A%22good+first+issue%22) labels to get started.

---

## License

**Leo Core** — MIT. Free to use, modify, and distribute.

**Leo Max** (AI Coach) is a separate commercial product — [join the waitlist](https://sandseb123.github.io/Leo-Health-Core).

---

<p align="center">Built by <a href="https://github.com/sandseb123">sandseb123</a></p>
