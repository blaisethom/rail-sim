"""
Darwin Push Port baseline: load Darwin predictions as a comparison for the simulator.

Matches NROD trains to Darwin RIDs via the train_uid field in NROD activation payloads,
then aligns Darwin predicted times (eta/etd, stored as 'HH:MM' UK local time) with
actual NROD movement timestamps (epoch ms UTC).

Timezone note: Darwin times are in UK local time. In summer (BST) this is UTC+1,
so pass tz_hours=1. In winter (GMT) pass tz_hours=0.
"""

import sqlite3
from datetime import datetime, timezone, timedelta


def _hhmm_to_ms(hhmm: str | None, ssd: str, tz_hours: int = 1) -> int | None:
    """
    Convert a Darwin time string ('HH:MM' or 'HH:MM:SS' UK local) to epoch ms UTC.

    tz_hours=1 for BST (UTC+1), 0 for GMT/UTC.
    Returns None if hhmm is falsy.
    """
    if not hhmm:
        return None
    parts = hhmm.split(":")
    h, m = int(parts[0]), int(parts[1])
    s = int(parts[2]) if len(parts) > 2 else 0
    base = datetime.strptime(ssd, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    delta = timedelta(hours=h, minutes=m, seconds=s) - timedelta(hours=tz_hours)
    return int((base + delta).timestamp() * 1000)


def _load_links(db_path: str, date_str: str) -> dict[str, tuple[str, str]]:
    """
    Return {train_id: (rid, toc_id)} for trains on date_str.

    Tries the pre-built nrod_darwin_link table first; falls back to a live
    json_extract join on raw_movements if the table is absent or empty.
    """
    conn = sqlite3.connect(db_path)

    # Check for pre-built link table
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='nrod_darwin_link'"
    ).fetchone()

    if has_table:
        rows = conn.execute(
            "SELECT train_id, rid, toc_id FROM nrod_darwin_link WHERE ssd = ?",
            (date_str,),
        ).fetchall()
        if rows:
            conn.close()
            return {tid: (rid, toc) for tid, rid, toc in rows}

    # Fallback: query via json_extract (slower but always current)
    rows = conn.execute("""
        SELECT DISTINCT
            rm.train_id,
            rm.toc_id,
            ds.rid
        FROM raw_movements rm
        JOIN darwin_schedules ds
            ON json_extract(rm.payload, '$.body.train_uid') = ds.uid
           AND json_extract(rm.payload, '$.body.tp_origin_timestamp') = ds.ssd
        WHERE rm.msg_type = '0001'
          AND json_extract(rm.payload, '$.body.tp_origin_timestamp') = ?
    """, (date_str,)).fetchall()
    conn.close()
    return {tid: (rid, toc or "") for tid, toc, rid in rows if tid and rid}


def load_darwin_baseline(
    db_path: str,
    date_str: str,
    tiploc_stanox: dict[str, str],
    tz_hours: int = 1,
) -> list[dict]:
    """
    Load Darwin predictions as a baseline comparison set.

    For each Darwin estimated time (eta preferred, falling back to etd) that maps
    to a known STANOX and for which NROD also recorded a movement, returns:

        {
            'predicted_ms': int,   # Darwin ETA/ETD in epoch ms UTC
            'actual_ms':    int,   # NROD movement timestamp epoch ms UTC
            'stanox':       str,
            'tiploc':       str,
            'train_id':     str,
            'rid':          str,
            'toc_id':       str,
        }

    Only rows where both Darwin and NROD have data are returned.
    Pairs with |predicted - actual| > 6h are dropped (overnight-wrap guard).
    """
    links = _load_links(db_path, date_str)
    if not links:
        return []

    rid_to_train: dict[str, tuple[str, str]] = {
        rid: (train_id, toc_id)
        for train_id, (rid, toc_id) in links.items()
    }
    rids = list(rid_to_train.keys())

    conn = sqlite3.connect(db_path)
    placeholders = ",".join("?" * len(rids))

    # Darwin predictions + schedule date for those RIDs
    pred_rows = conn.execute(
        f"""
        SELECT dp.rid, dp.tiploc, dp.eta, dp.etd, ds.ssd
        FROM darwin_predictions dp
        JOIN darwin_schedules ds ON dp.rid = ds.rid AND dp.tiploc = ds.tiploc
        WHERE dp.rid IN ({placeholders})
        """,
        rids,
    ).fetchall()

    # NROD actual times (first arrival at each stanox per train)
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    day_start = int(dt.timestamp() * 1000)
    day_end = day_start + 86_400_000

    train_ids = list(links.keys())
    placeholders2 = ",".join("?" * len(train_ids))
    mv_rows = conn.execute(
        f"""
        SELECT train_id, stanox, MIN(timestamp_ms)
        FROM raw_movements
        WHERE msg_type = '0003'
          AND timestamp_ms >= ? AND timestamp_ms < ?
          AND train_id IN ({placeholders2})
          AND stanox IS NOT NULL AND stanox != '' AND stanox != '00000'
        GROUP BY train_id, stanox
        """,
        [day_start, day_end] + train_ids,
    ).fetchall()
    conn.close()

    # {(train_id, stanox): actual_ms}
    nrod_actual: dict[tuple[str, str], int] = {}
    for tid, st, ts in mv_rows:
        if st:
            nrod_actual[(tid, st.strip())] = ts

    # Convert Darwin predictions to stanox-keyed lookup
    # {(rid, stanox): (predicted_ms, tiploc)} — keep first TIPLOC per STANOX
    darwin_pred: dict[tuple[str, str], tuple[int, str]] = {}
    for rid, tiploc, eta, etd, ssd in pred_rows:
        stanox = tiploc_stanox.get(tiploc)
        if not stanox:
            continue
        key = (rid, stanox)
        if key in darwin_pred:
            continue
        pred_ms = _hhmm_to_ms(eta or etd, ssd, tz_hours)
        if pred_ms is not None:
            darwin_pred[key] = (pred_ms, tiploc)

    results = []
    for (rid, stanox), (pred_ms, tiploc) in darwin_pred.items():
        train_id, toc_id = rid_to_train[rid]
        actual_ms = nrod_actual.get((train_id, stanox))
        if actual_ms is None:
            continue
        # Overnight-wrap guard: reject if offset > 6 hours
        if abs(pred_ms - actual_ms) > 6 * 3600 * 1000:
            continue
        results.append({
            "train_id": train_id,
            "rid": rid,
            "toc_id": toc_id,
            "stanox": stanox,
            "tiploc": tiploc,
            "predicted_ms": pred_ms,
            "actual_ms": actual_ms,
        })

    return results


def load_darwin_horizon_predictions(
    db_path: str,
    date_str: str,
    tiploc_stanox: dict[str, str],
    tz_hours: int = 1,
) -> list[dict]:
    """
    Load Darwin prediction history snapshots for horizon analysis.

    For each snapshot in darwin_prediction_history, paired with a known
    NROD actual time for the same (train_id, stanox), returns:

        {
            'predicted_ms': int,   # Darwin ETA at snapshot_at
            'actual_ms':    int,   # NROD actual movement time
            'horizon_s':    int,   # actual_ms - snapshot_at (seconds before event)
            'stanox':       str,
            'tiploc':       str,
            'train_id':     str,
            'rid':          str,
            'toc_id':       str,
        }

    horizon_s = how many seconds before the event Darwin issued this prediction.
    Groups into the same HORIZON_BUCKETS used for rail-sim.
    Requires darwin_prediction_history table (populated by darwin_ingest >= this version).
    """
    links = _load_links(db_path, date_str)
    if not links:
        return []

    rid_to_train: dict[str, tuple[str, str]] = {
        rid: (train_id, toc_id)
        for train_id, (rid, toc_id) in links.items()
    }
    rids = list(rid_to_train.keys())

    conn = sqlite3.connect(db_path)

    # Check history table exists
    has_history = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='darwin_prediction_history'"
    ).fetchone()
    if not has_history:
        conn.close()
        return []

    placeholders = ",".join("?" * len(rids))

    # Load all history snapshots for these RIDs, joined to schedule date
    history_rows = conn.execute(
        f"""
        SELECT h.rid, h.tiploc, h.eta, h.etd, h.snapshot_at, ds.ssd
        FROM darwin_prediction_history h
        JOIN darwin_schedules ds ON h.rid = ds.rid AND h.tiploc = ds.tiploc
        WHERE h.rid IN ({placeholders})
        """,
        rids,
    ).fetchall()

    # NROD actuals
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    day_start = int(dt.timestamp() * 1000)
    day_end = day_start + 86_400_000
    train_ids = list(links.keys())
    placeholders2 = ",".join("?" * len(train_ids))
    mv_rows = conn.execute(
        f"""
        SELECT train_id, stanox, MIN(timestamp_ms)
        FROM raw_movements
        WHERE msg_type = '0003'
          AND timestamp_ms >= ? AND timestamp_ms < ?
          AND train_id IN ({placeholders2})
          AND stanox IS NOT NULL AND stanox != '' AND stanox != '00000'
        GROUP BY train_id, stanox
        """,
        [day_start, day_end] + train_ids,
    ).fetchall()
    conn.close()

    nrod_actual: dict[tuple[str, str], int] = {
        (tid, st.strip()): ts for tid, st, ts in mv_rows if st
    }

    results = []
    for rid, tiploc, eta, etd, snapshot_at, ssd in history_rows:
        stanox = tiploc_stanox.get(tiploc)
        if not stanox:
            continue
        train_id, toc_id = rid_to_train[rid]
        actual_ms = nrod_actual.get((train_id, stanox))
        if actual_ms is None:
            continue
        pred_ms = _hhmm_to_ms(eta or etd, ssd, tz_hours)
        if pred_ms is None:
            continue
        # Overnight-wrap guard
        if abs(pred_ms - actual_ms) > 6 * 3600 * 1000:
            continue
        snapshot_ms = snapshot_at * 1000
        horizon_s = max(0, (actual_ms - snapshot_ms) // 1000)
        results.append({
            "train_id":    train_id,
            "rid":         rid,
            "toc_id":      toc_id,
            "stanox":      stanox,
            "tiploc":      tiploc,
            "predicted_ms": pred_ms,
            "actual_ms":   actual_ms,
            "horizon_s":   horizon_s,
        })

    return results
