"""Load and prepare observed train movements from the rail-data SQLite database."""

import json
import sqlite3
from datetime import datetime, timezone


def load_observations(db_path: str, date_str: str) -> list[dict]:
    """
    Load all movement events for a given date (YYYY-MM-DD, UTC).

    Returns a list of trains, each:
      {
        'train_id': str,
        'toc_id': str,
        'stops': [
          {
            'stanox':       str,
            'ts_ms':        int,   # actual timestamp (epoch ms UTC)
            'scheduled_ms': int|None,  # gbtt_timestamp from payload
            'event_type':   str,   # 'ARRIVAL' | 'DEPARTURE'
          },
          ...
        ]
      }

    Stops are sorted by ts_ms and deduplicated: for each STANOX, only the first
    event (usually ARRIVAL) is kept. Trains with fewer than 3 stops are excluded.
    """
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    day_start = int(dt.timestamp() * 1000)
    day_end = day_start + 86_400_000

    con = sqlite3.connect(db_path)
    rows = con.execute(
        """
        SELECT train_id, toc_id, stanox, timestamp_ms, payload
        FROM raw_movements
        WHERE msg_type = '0003'
          AND timestamp_ms >= ? AND timestamp_ms < ?
          AND stanox IS NOT NULL AND stanox != ''
        ORDER BY train_id, timestamp_ms
        """,
        (day_start, day_end),
    ).fetchall()
    con.close()

    trains: dict[str, dict] = {}
    for train_id, toc_id, stanox, ts_ms, payload in rows:
        stanox = stanox.strip()
        if not stanox or stanox == "00000":
            continue

        # Extract richer fields from payload
        scheduled_ms: int | None = None
        event_type = "MOVEMENT"
        if payload:
            try:
                body = json.loads(payload).get("body", {})
                gbtt = body.get("gbtt_timestamp")
                if gbtt:
                    scheduled_ms = int(gbtt)
                event_type = body.get("planned_event_type") or body.get("event_type") or "MOVEMENT"
            except (json.JSONDecodeError, ValueError):
                pass

        if train_id not in trains:
            trains[train_id] = {"train_id": train_id, "toc_id": toc_id or "", "stops": []}
        trains[train_id]["stops"].append({
            "stanox": stanox,
            "ts_ms": ts_ms,
            "scheduled_ms": scheduled_ms,
            "event_type": event_type,
        })

    result = []
    for train in trains.values():
        stops = train["stops"]
        # Deduplicate: keep first event per STANOX in sequence
        seen: set[str] = set()
        deduped = []
        for s in stops:
            if s["stanox"] not in seen:
                seen.add(s["stanox"])
                deduped.append(s)
        if len(deduped) >= 3:
            train["stops"] = deduped
            result.append(train)

    return result


def timetable_baseline(trains: list[dict]) -> list[dict]:
    """
    Build a prediction set using the timetable (gbtt_timestamp) as the 'predicted' time.

    For each stop where scheduled_ms is known, returns:
      {'predicted_ms': scheduled_ms, 'actual_ms': ts_ms, 'stanox': ..., 'train_id': ..., 'toc_id': ...}

    This gives the MAE of the timetable itself — the floor below which Darwin and
    the simulator aim to improve.
    """
    results = []
    for train in trains:
        for stop in train["stops"]:
            if stop.get("scheduled_ms") is not None:
                results.append({
                    "train_id": train["train_id"],
                    "toc_id": train["toc_id"],
                    "stanox": stop["stanox"],
                    "predicted_ms": stop["scheduled_ms"],
                    "actual_ms": stop["ts_ms"],
                })
    return results
