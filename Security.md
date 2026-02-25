# Security Policy

## Overview

Leo Health handles sensitive personal health data. This document describes the security model, known limitations, and how to report vulnerabilities.

---

## Threat Model

**What Leo Health protects against:**
- Network exfiltration — dashboard binds to `127.0.0.1` only, zero outbound network code
- Browser caching of health data — `Cache-Control: no-store` on all responses
- MIME sniffing attacks — `X-Content-Type-Options: nosniff` on all responses
- Script injection — `Content-Security-Policy` header on HTML responses
- SQL injection via table/column names — explicit allowlist in `_insert_many()`
- AppleScript injection via filenames — input sanitized before `osascript` calls
- Weak file deduplication — SHA-256 full-file hashing

**What Leo Health does NOT protect against:**
- Local machine access — anyone with access to your Mac can read `~/.leo-health/leo.db`
- No authentication — the dashboard has no login, designed for single-user desktop use
- Full disk encryption — not provided, use macOS FileVault for data at rest encryption
- Multi-user environments — not designed for shared machines

**Designed for:** Single-user, personal Mac. Not intended for servers or shared environments.

---

## Data Storage

- Database: `~/.leo-health/leo.db` (SQLite)
- Directory permissions: `0700` (owner read/write only)
- No cloud sync, no telemetry, no analytics
- No data ever leaves your machine

---

## Verifying Zero Network Code

You can verify there is no outbound network code by running:

```bash
grep -r "import urllib\|import http\|import requests\|import socket\|http.client\|urllib.request" .
```

You will see three results, all in `leo_health/dashboard.py` — these are Python stdlib imports used to run a **local web server** on your own machine (localhost only). No data is sent anywhere.

---

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest (main) | ✅ |

This project is under active development. Always use the latest version from `main`.

---

## Reporting a Vulnerability

If you discover a security vulnerability, please **do not open a public GitHub issue**.

Instead, report it via:
- GitHub private vulnerability reporting (Security tab → "Report a vulnerability")
- Or email the maintainer directly via GitHub profile

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

You will receive a response within 72 hours. We take all reports seriously given the sensitive nature of health data.

---

## Security Fixes Log

| Date | Issue | Fix |
|------|-------|-----|
| 2026-02-25 | AppleScript injection via filenames | Escape backslashes and quotes before osascript |
| 2026-02-25 | SQL f-string with user-derived keys | Added table/column allowlist to `_insert_many()` |
| 2026-02-25 | Missing HTTP security headers | Added Cache-Control, X-Content-Type-Options, CSP |
| 2026-02-25 | DB directory world-readable | Set permissions to `0700` on creation |
| 2026-02-25 | MD5 partial file hashing | Replaced with SHA-256 full file hash |
