# Changelog

All notable changes to Leo Health Core are documented here.

---

## [Unreleased]

### Added
- Docker support with non-root user, localhost binding, env var configuration
- GitHub Actions CI pipeline — tests run on Python 3.9, 3.10, 3.11
- SECURITY.md with full threat model, data flow diagram, vulnerability reporting
- SECURITY-CHECKLIST.md for release gates
- Core test suite — 11 tests covering schema, ingest, security, watcher
- MIT License

### Changed
- Whoop parser: replaced `or` chains with `_coalesce_float()` to preserve valid 0.0 values
- Whoop parser: added `_hours_from_hours_or_minutes()` for accurate sleep duration conversion
- Whoop parser: deterministic folder parsing via sorted CSV filenames
- install.sh: removed PYTHONPATH pollution, now uses `pip install -e .`

### Fixed
- Schema drift: missing workout columns (`active_calories`, `avg_cadence`, `avg_hr`, `max_hr`)
- AppleScript injection via crafted filenames in watcher
- SQL identifier injection via f-strings in `_insert_many()` — added allowlist
- Missing HTTP security headers on dashboard responses
- DB directory permissions now set to `0700` on creation
- SHA-256 full file hashing replaces MD5 partial reads
- Unvalidated `days` query parameter — now clamped to 1–3650

---

## [0.1.0] — 2026-02-10

### Added
- Apple Health XML parser (SAX streaming — handles 4GB+ exports)
- Whoop CSV parser with auto-detection across export versions
- Normalized SQLite schema — 6 tables
- `leo` terminal dashboard
- `leo-watch` AirDrop watcher — auto-ingest within 10 seconds
- `leo-dash` web dashboard at localhost:5380
- One-command installer (`pip install -e .`)
