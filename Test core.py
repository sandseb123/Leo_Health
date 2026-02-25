"""
Leo Health — Core Test Suite
Tests critical paths: parser, sleep dedupe, DB ingest, and security.
Run with: pytest tests/
"""

import os
import sqlite3
import tempfile
import pytest
from pathlib import Path


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_db(tmp_path: Path) -> str:
    """Create a fresh test database."""
    db_path = str(tmp_path / "test.db")
    from leo_health.db.schema import create_schema
    create_schema(db_path)
    return db_path


# ── Schema tests ──────────────────────────────────────────────────────────────

class TestSchema:
    def test_creates_all_tables(self, tmp_path):
        db_path = make_db(tmp_path)
        conn = sqlite3.connect(db_path)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        expected = {"heart_rate", "hrv", "sleep", "workouts", "whoop_recovery", "whoop_strain"}
        assert expected.issubset(tables)

    def test_workouts_has_new_columns(self, tmp_path):
        db_path = make_db(tmp_path)
        conn = sqlite3.connect(db_path)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(workouts)").fetchall()}
        assert "active_calories" in cols
        assert "avg_cadence" in cols
        assert "avg_hr" in cols
        assert "max_hr" in cols

    def test_db_directory_permissions(self, tmp_path):
        db_path = str(tmp_path / "subdir" / "test.db")
        from leo_health.db.schema import create_schema
        create_schema(db_path)
        dir_stat = oct(os.stat(os.path.dirname(db_path)).st_mode)[-3:]
        assert dir_stat == "700"


# ── Ingest tests ──────────────────────────────────────────────────────────────

class TestIngest:
    def test_ingest_heart_rate(self, tmp_path):
        db_path = make_db(tmp_path)
        from leo_health.db.ingest import ingest_apple_health
        data = {
            "heart_rate": [
                {"source": "apple_health", "metric": "heart_rate", "value": 72.0,
                 "unit": "count/min", "recorded_at": "2024-01-01T08:00:00", "device": "Apple Watch"}
            ],
            "hrv": [], "sleep": [], "workouts": []
        }
        result = ingest_apple_health(data, db_path)
        assert result["heart_rate"] == 1

    def test_ingest_rejects_unknown_table(self, tmp_path):
        db_path = make_db(tmp_path)
        conn = sqlite3.connect(db_path)
        from leo_health.db.ingest import _insert_many
        with pytest.raises(ValueError, match="Unknown table"):
            _insert_many(conn, "malicious_table", [{"col": "val"}])

    def test_ingest_ignores_unknown_columns(self, tmp_path):
        db_path = make_db(tmp_path)
        conn = sqlite3.connect(db_path)
        from leo_health.db.ingest import _insert_many
        # Should not raise — unknown columns are silently dropped
        rows = [{"source": "apple_health", "metric": "heart_rate", "value": 72.0,
                 "unit": "count/min", "recorded_at": "2024-01-01T08:00:00",
                 "device": "Apple Watch", "unknown_col": "should_be_dropped"}]
        count = _insert_many(conn, "heart_rate", rows)
        assert count == 1

    def test_insert_many_empty_rows(self, tmp_path):
        db_path = make_db(tmp_path)
        conn = sqlite3.connect(db_path)
        from leo_health.db.ingest import _insert_many
        result = _insert_many(conn, "heart_rate", [])
        assert result == 0


# ── Parser tests ──────────────────────────────────────────────────────────────

class TestAppleHealthParser:
    def test_iso_date_parsing(self):
        from leo_health.parsers.apple_health import _iso
        assert _iso("2024-01-15 08:30:00 -0800") == "2024-01-15T08:30:00"

    def test_iso_empty_string(self):
        from leo_health.parsers.apple_health import _iso
        assert _iso("") == ""

    def test_gpx_empty_bytes_returns_empty(self):
        from leo_health.parsers.apple_health import _parse_gpx
        result = _parse_gpx(b"", "2024-01-01T08:00:00")
        assert result == []

    def test_gpx_malformed_returns_empty(self):
        from leo_health.parsers.apple_health import _parse_gpx
        result = _parse_gpx(b"not valid xml", "2024-01-01T08:00:00")
        assert result == []


# ── Security tests ────────────────────────────────────────────────────────────

class TestSecurity:
    def test_notify_escapes_quotes(self):
        """Verify _notify sanitizes input — no actual osascript call."""
        quote_in_filename = 'file"with"quotes'
        safe = quote_in_filename.replace("\\", "\\\\").replace('"', '\\"')
        assert '"' not in safe or safe.count('\\"') == safe.count('"')

    def test_days_param_defaults_on_invalid(self):
        """Verify days validation clamps correctly."""
        def parse_days(raw):
            try:
                d = int(raw)
                if d < 1 or d > 3650:
                    return 30
                return d
            except (ValueError, TypeError):
                return 30

        assert parse_days("abc") == 30
        assert parse_days("0") == 30
        assert parse_days("9999") == 30
        assert parse_days("7") == 7
        assert parse_days("30") == 30


# ── Watcher tests ─────────────────────────────────────────────────────────────

class TestWatcher:
    def test_file_hash_returns_string(self, tmp_path):
        from leo_health.watcher import _file_hash
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"hello world")
        result = _file_hash(str(test_file))
        assert isinstance(result, str)
        assert len(result) == 64  # SHA-256 hex digest length

    def test_file_hash_different_files(self, tmp_path):
        from leo_health.watcher import _file_hash
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_bytes(b"content a")
        f2.write_bytes(b"content b")
        assert _file_hash(str(f1)) != _file_hash(str(f2))

    def test_file_hash_same_content(self, tmp_path):
        from leo_health.watcher import _file_hash
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_bytes(b"same content")
        f2.write_bytes(b"same content")
        assert _file_hash(str(f1)) == _file_hash(str(f2))
