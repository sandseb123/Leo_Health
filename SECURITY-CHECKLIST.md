# Leo Health Security Release Checklist

Before each release, verify:

## Network Safety
- [ ] No new outbound network libraries introduced
- [ ] Dashboard still binds to 127.0.0.1 only
- [ ] No telemetry or analytics added

## Input Safety
- [ ] All query parameters validated and clamped
- [ ] CSV/XML parsers handle malformed input safely
- [ ] Filenames sanitized before any shell/osascript use

## Database Safety
- [ ] SQL identifiers validated against allowlist
- [ ] No f-strings used for user-controlled SQL values
- [ ] Database directory permissions remain 0700

## Browser Hardening
- [ ] Cache-Control: no-store present
- [ ] X-Content-Type-Options: nosniff present
- [ ] CSP header present on HTML responses

## Dependency Safety
- [ ] No new runtime dependencies added
- [ ] If added, dependency security reviewed

## Regression Tests
- [ ] Parser tests pass
- [ ] Sleep dedupe tests pass
- [ ] Large-file parsing smoke test passes
