"""Load and prepare observed train movements from the rail-data SQLite database."""

import sqlite3
from datetime import datetime, timezone


def load_observations(db_path: str, date_str: str) -> list[dict]:
    """
    Load all movement events for a given date (YYYY-MM-DD, UTC).

    Returns a list of trains, each:
      {'train_id': str, 'toc_id': str, 'stops': [{'stanox': str, 'ts_ms': int, 'event_type': str}, ...]}

    Stops are sorted by ts_ms and deduplicated so only the first event per STANOX is kept.
    Trains with fewer than 3 stops are excluded.
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
        if train_id not in trains:
            trains[train_id] = {"train_id": train_id, "toc_id": toc_id or "", "stops": []}
        trains[train_id]["stops"].append({"stanox": stanox, "ts_ms": ts_ms, "event_type": "MOVEMENT"})

    result = []
    for train in trains.values():
        stops = trains[train["train_id"]]["stops"]
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
