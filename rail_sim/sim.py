"""Discrete-event simulator: predict timing of train stops given a STANOX sequence."""

from rail_sim.model import Network


def simulate_train(
    stops: list[dict],
    network: Network,
    time_scale: float = 1.0,
) -> list[dict]:
    """
    Given a train's stops (must have 'stanox' and 'ts_ms' for the first stop),
    predict arrival timestamps at each subsequent stop.

    time_scale < 1 means trains run faster than BPLAN expects.
    time_scale > 1 means trains run slower.

    Returns a list of dicts:
      {'stanox': str, 'predicted_ms': int, 'actual_ms': int}
    for stops 1..N (stop 0 is the anchor; its predicted == actual).
    """
    if not stops:
        return []

    predictions = []
    current_ms = stops[0]["ts_ms"]

    for i in range(1, len(stops)):
        from_stanox = stops[i - 1]["stanox"]
        to_stanox = stops[i]["stanox"]
        base_secs = network.running_time(from_stanox, to_stanox)
        predicted_ms = current_ms + int(base_secs * time_scale * 1000)
        actual_ms = stops[i]["ts_ms"]
        predictions.append({
            "stanox": to_stanox,
            "stop_index": i,
            "predicted_ms": predicted_ms,
            "actual_ms": actual_ms,
        })
        # Advance clock using actual time so errors don't compound
        current_ms = actual_ms

    return predictions


def simulate_all(
    trains: list[dict],
    network: Network,
    time_scale: float = 1.0,
) -> list[dict]:
    """
    Simulate all trains. Returns a flat list of prediction records with train_id added.
    """
    results = []
    for train in trains:
        preds = simulate_train(train["stops"], network, time_scale)
        for p in preds:
            p["train_id"] = train["train_id"]
            p["toc_id"] = train.get("toc_id", "")
        results.extend(preds)
    return results
