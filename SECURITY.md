# Security Policy

## Overview

Leo Health handles sensitive personal health data. This document describes the security model, known limitations, how to verify privacy claims, and how to report vulnerabilities.

---

## Privacy Model

Leo Health does not intentionally transmit data to external services. The application only binds to localhost and performs no outbound network requests in the current codebase.

Leo Health is designed so that all health data remains local to your machine.

**Primary platform:** macOS
**Other platforms:** Best effort (Linux parsing works; macOS-specific features such as notifications require `osascript`)

---

## Threat Model

### What Leo Health protects against

- **Network exfiltration** — dashboard binds to `127.0.0.1` (loopback only), no outbound connections
- **Browser caching of health data** — `Cache-Control: no-store` on all responses
- **MIME sniffing attacks** — `X-Content-Type-Options: nosniff` on all responses
- **Script injection** — Content-Security-Policy on HTML responses:
  `default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'`
- **SQL injection via identifiers** — explicit table/column allowlist in `_insert_many()`
- **AppleScript injection via filenames** — input sanitized before `osascript` calls
- **Weak file deduplication** — SHA-256 full-file hashing (not MD5 partial reads)
- **World-readable health data** — DB directory created with `0700` permissions

### Known limitations

- **Local machine access** — anyone with access to your Mac can read `~/.leo-health/leo.db`. Use macOS FileVault for full disk encryption.
- **No authentication** — the dashboard has no login. Designed for single-user desktop use only.
- **Local process access** — any process running on the same machine can access the local dashboard. Leo Health assumes the local system is trusted.
- **Multi-user environments** — not designed or tested for shared machines.
- **Browser extensions and DNS rebinding** — advanced local attack vectors exist on any localhost server. Leo Health does not mitigate these.

---

## Verifying Network Behaviour

The codebase intentionally avoids outbound network libraries. You can verify by searching for common networking modules:

```bash
grep -r "import urllib\|import http\|import requests\|import socket\|http.client\|urllib.request" .
```

You will see three results, all in `leo_health/dashboard.py` — these are Python stdlib imports used exclusively to run a local web server on your own machine (localhost only).

Note: This grep covers the most common patterns but does not catch dynamic imports or future contributions. Review the full source for complete assurance.

---

## Dependency Model

Leo Health is implemented using the Python standard library only and has **zero runtime third-party dependencies**. This significantly reduces supply chain risk — there are no third-party packages to audit, pin, or patch.

Installation via `pip install -e .` uses `setuptools` and `wheel` for build only. These are not runtime dependencies.

---

## Data Storage

| Path | Purpose | Permissions |
|------|---------|-------------|
| `~/.leo-health/leo.db` | SQLite database | Directory: `0700` |
| `~/.leo-health/processed.txt` | SHA-256 hashes of ingested files | Directory: `0700` |

No data is synced to the cloud, shared with third parties, or transmitted anywhere.

---

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest (`main`) | ✅ |

This project is under active development. Always use the latest version from `main`. Security fixes will be released as quickly as possible after validation.

---

## Reporting a Vulnerability

If you discover a security vulnerability, please **do not open a public GitHub issue**.

Report it via:
- GitHub private vulnerability reporting (**Security tab → "Report a vulnerability"**)
- Or contact the maintainer directly via GitHub profile

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

You will receive a response within 72 hours. We take all reports seriously given the sensitive nature of health data.

---

## Security Fixes Log

| Date | Severity | Issue | Fix |
|------|----------|-------|-----|
| 2026-02-25 | P2 | AppleScript injection via filenames | Escape backslashes and quotes before `osascript` |
| 2026-02-25 | P2 | SQL f-string with user-derived identifiers | Added table/column allowlist to `_insert_many()` |
| 2026-02-25 | P2 | Missing HTTP security headers | Added `Cache-Control`, `X-Content-Type-Options`, `CSP` |
| 2026-02-25 | P3 | DB directory world-readable on some systems | Set permissions to `0700` on creation |
| 2026-02-25 | P3 | MD5 partial file hashing (collision risk) | Replaced with SHA-256 full file hash |
| 2026-02-25 | P4 | Unvalidated `days` query param crashes request handler | Added `try/except` with range clamp (1–3650) |
