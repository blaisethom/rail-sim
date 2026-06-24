"""Tests for darwin_baseline: time parsing and prediction matching."""

import sqlite3
import tempfile
import time

import pytest

from rail_sim.darwin_baseline import _hhmm_to_ms, load_darwin_baseline


# ── time parsing ──────────────────────────────────────────────────────────────

def test_hhmm_to_ms_bst():
    # "10:30" BST (UTC+1) on 2026-06-24 → "09:30" UTC
    ms = _hhmm_to_ms("10:30", "2026-06-24", tz_hours=1)
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    assert dt.hour == 9
    assert dt.minute == 30


def test_hhmm_to_ms_gmt():
    ms = _hhmm_to_ms("10:30", "2026-01-15", tz_hours=0)
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    assert dt.hour == 10
    assert dt.minute == 30


def test_hhmm_to_ms_with_seconds():
    ms = _hhmm_to_ms("22:45:30", "2026-06-24", tz_hours=1)
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    assert dt.hour == 21
    assert dt.minute == 45
    assert dt.second == 30


def test_hhmm_to_ms_none():
    assert _hhmm_to_ms(None, "2026-06-24") is None
    assert _hhmm_to_ms("", "2026-06-24") is None


# ── load_darwin_baseline integration ─────────────────────────────────────────

def _make_test_db() -> str:
    """Create an in-memory-backed temp DB with minimal fixtures."""
    db = tempfile.mktemp(suffix=".db")
    conn = sqlite3.connect(db)

    # Tables needed
    conn.executescript("""
        CREATE TABLE raw_movements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            received_at INTEGER,
            train_id TEXT,
            toc_id TEXT,
            train_type TEXT,
            msg_type TEXT,
            stanox TEXT,
            timestamp_ms INTEGER,
            payload TEXT
        );

        CREATE TABLE darwin_schedules (
            rid TEXT NOT NULL,
            uid TEXT NOT NULL DEFAULT '',
            headcode TEXT,
            toc TEXT,
            ssd TEXT NOT NULL,
            tiploc TEXT NOT NULL,
            call_type TEXT,
            seq INTEGER,
            pta TEXT, ptd TEXT, wta TEXT, wtd TEXT,
            cancelled INTEGER DEFAULT 0,
            updated_at INTEGER,
            PRIMARY KEY (rid, tiploc)
        );

        CREATE TABLE darwin_predictions (
            rid TEXT NOT NULL,
            tiploc TEXT NOT NULL,
            eta TEXT,
            etd TEXT,
            ata TEXT,
            atd TEXT,
            updated_at INTEGER,
            PRIMARY KEY (rid, tiploc)
        );
    """)

    DATE = "2026-06-24"
    UID = "P12345"
    RID = "202606247012345"
    TRAIN_ID = "521A12MX24"
    TOC = "VT"

    # NROD activation
    import json
    payload = json.dumps({
        "body": {
            "train_uid": UID,
            "tp_origin_timestamp": DATE,
        }
    })
    conn.execute(
        "INSERT INTO raw_movements (received_at, train_id, toc_id, msg_type, payload) VALUES (?,?,?,?,?)",
        (int(time.time()), TRAIN_ID, TOC, "0001", payload),
    )

    # Darwin schedule with one calling point (TIPLOC = MNCRPIC, STANOX = 33073)
    conn.execute(
        "INSERT INTO darwin_schedules VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (RID, UID, "1A01", TOC, DATE, "MNCRPIC", "IP", 1, None, None, None, None, 0, int(time.time())),
    )

    # Darwin prediction: ETA 10:30 BST on 2026-06-24 → 09:30 UTC
    conn.execute(
        "INSERT INTO darwin_predictions VALUES (?,?,?,?,?,?,?)",
        (RID, "MNCRPIC", "10:30", None, None, None, int(time.time())),
    )

    # NROD movement at STANOX 33073 — 09:28 UTC = 10:28 BST (2 minutes early)
    from datetime import datetime, timezone
    actual_dt = datetime(2026, 6, 24, 9, 28, tzinfo=timezone.utc)
    actual_ms = int(actual_dt.timestamp() * 1000)
    conn.execute(
        "INSERT INTO raw_movements (received_at, train_id, toc_id, msg_type, stanox, timestamp_ms, payload) VALUES (?,?,?,?,?,?,?)",
        (int(time.time()), TRAIN_ID, TOC, "0003", "33073", actual_ms, "{}"),
    )

    conn.commit()
    conn.close()
    return db


def test_load_darwin_baseline_matches():
    db = _make_test_db()
    tiploc_stanox = {"MNCRPIC": "33073"}
    results = load_darwin_baseline(db, "2026-06-24", tiploc_stanox, tz_hours=1)

    assert len(results) == 1
    r = results[0]
    assert r["train_id"] == "521A12MX24"
    assert r["rid"] == "202606247012345"
    assert r["stanox"] == "33073"
    assert r["tiploc"] == "MNCRPIC"

    # Darwin predicted 10:30 BST = 09:30 UTC
    from datetime import datetime, timezone
    pred_dt = datetime.fromtimestamp(r["predicted_ms"] / 1000, tz=timezone.utc)
    assert pred_dt.hour == 9
    assert pred_dt.minute == 30

    # Error should be ~120 s (2 minutes early)
    error_s = abs(r["predicted_ms"] - r["actual_ms"]) / 1000
    assert error_s < 200


def test_load_darwin_baseline_empty_when_no_links():
    db = _make_test_db()
    results = load_darwin_baseline(db, "2099-01-01", {"MNCRPIC": "33073"}, tz_hours=1)
    assert results == []


def test_load_darwin_baseline_unknown_tiploc():
    db = _make_test_db()
    # Pass empty tiploc_stanox — TIPLOC can't resolve to STANOX
    results = load_darwin_baseline(db, "2026-06-24", {}, tz_hours=1)
    assert results == []
