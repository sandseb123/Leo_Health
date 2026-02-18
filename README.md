# Leo Health 🫀

> Your Apple Health and Whoop data, as a SQL database. In 60 seconds.

Apple Health locks your biometrics in a 4GB XML file. Whoop buries yours in CSVs with inconsistent column names. Leo Core parses both in under 60 seconds and writes everything to a single, normalized SQLite database — Heart Rate, Sleep, Workouts, HRV, Recovery Score, all queryable with standard SQL.

**Zero network requests. Runs locally. MIT licensed.**

![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue)
![License MIT](https://img.shields.io/badge/license-MIT-green)
![Zero Dependencies](https://img.shields.io/badge/dependencies-zero-brightgreen)

## Why Leo?

- 🔒 **Your data never leaves your machine** — no cloud, no server, no signup
- ⚡ **60-second parse** — even 4GB Apple Health exports
- 🛠️ **SQL queryable** — use pandas, Jupyter, R, or any SQLite tool
- 📦 **Zero dependencies** — pure Python stdlib
- 🔍 **Auditable** — MIT licensed, read every line

## Install
```bash
pip install leo-health
```

## Quick Start
```python
from leo_health.db.ingest import ingest_all

ingest_all(
    apple_health_zip="~/Downloads/export.zip",
    whoop_folder="~/Downloads/whoop_exports/"
)
```

## Query Your Data
```sql
SELECT recorded_at, recovery_score, hrv_ms, resting_heart_rate
FROM whoop_recovery ORDER BY recorded_at DESC LIMIT 7;

SELECT source, ROUND(AVG(value), 1) as avg_hrv FROM hrv GROUP BY source;

SELECT stage, COUNT(*) as sessions FROM sleep GROUP BY stage;
```

## How to Export Your Data

**Apple Health:** Health app → profile picture → Export All Health Data → share export.zip to Mac

**Whoop:** Whoop app → Profile → Export Data → check email for CSVs

## Auto-Ingest via AirDrop ✨

Leo watches your Downloads folder and automatically parses any health export 
the moment it arrives — no commands needed.

**Start the watcher:**
```bash
python3 -m leo_health.watcher
```

**Then on your iPhone:**
1. Open Health app → profile picture → Export All Health Data
2. AirDrop it to your Mac
3. Leo detects it within 10 seconds, parses it, and sends you a notification

That's it. Your database is updated automatically every time you export.

**Run Leo automatically on every login:**
```bash
cp com.leohealth.watcher.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.leohealth.watcher.plist
```

Leo uses ~8MB RAM and near-zero CPU while watching. You won't notice it's running.

## Privacy

Zero network code. Verify it yourself:
```bash
grep -r "import urllib\|import http\|import requests" leo_health/
# Returns nothing.
```

## Roadmap

- [x] Apple Health XML parser
- [x] Whoop CSV parser
- [x] Normalized SQLite schema
- [ ] CLI — leo health status (Module 2)
- [ ] Fitbit support
- [ ] Leo Pro — AI Health Coach (local LLM, 100% private)

## License

**Leo Core** — MIT licensed.
**Leo Pro** (AI Coach + Dashboard) — commercial, coming soon.

---
<p align="center">Built by <a href="https://github.com/sandseb123">sandseb123</a></p>
