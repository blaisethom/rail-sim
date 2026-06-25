"""Tests for the simulator and calibration."""

import pytest

from rail_sim.model import Network, haversine_seconds
from rail_sim.sim import simulate_train, simulate_all
from rail_sim.validate import compute_metrics, compute_metrics_by_horizon, compute_bias, HORIZON_BUCKETS
from rail_sim.calibrate import calibrate


def make_network(timing: dict[tuple, int] = None, coords: dict = None) -> Network:
    t = {f"{a}|{b}": s for (a, b), s in (timing or {}).items()}
    c = {k: list(v) for k, v in (coords or {}).items()}
    return Network({"stanox_timing": t, "stanox_coords": c, "stanox_names": {}})


# --- haversine ---

def test_haversine_london_manchester():
    # London to Manchester ~262 km; at 80 km/h ~11775 s
    secs = haversine_seconds(51.5074, -0.1278, 53.4808, -2.2426)
    assert 10000 < secs < 14000


def test_haversine_minimum():
    # Same coords → minimum 30 s (floor)
    assert haversine_seconds(51.0, -1.0, 51.0, -1.0) == 30


# --- simulator ---

def test_simulate_uses_bplan_timing():
    net = make_network({("AAA", "BBB"): 300, ("BBB", "CCC"): 120})
    stops = [
        {"stanox": "AAA", "ts_ms": 0},
        {"stanox": "BBB", "ts_ms": 330_000},   # actual 330s
        {"stanox": "CCC", "ts_ms": 450_000},   # actual 120s later
    ]
    preds = simulate_train(stops, net, time_scale=1.0)
    # First prediction: 0 + 300*1000 = 300000
    assert preds[0]["predicted_ms"] == 300_000
    assert preds[0]["actual_ms"] == 330_000
    # Second leg anchors on actual ts of BBB (330000) + 120*1000 = 450000
    assert preds[1]["predicted_ms"] == 450_000


def test_simulate_time_scale():
    net = make_network({("AAA", "BBB"): 300})
    stops = [
        {"stanox": "AAA", "ts_ms": 0},
        {"stanox": "BBB", "ts_ms": 0},
    ]
    preds = simulate_train(stops, net, time_scale=2.0)
    assert preds[0]["predicted_ms"] == 600_000


def test_simulate_haversine_fallback():
    # No timing in network → haversine fallback (non-zero result)
    net = make_network(
        coords={"12345": (51.5, -0.1), "67890": (53.5, -2.2)}
    )
    stops = [
        {"stanox": "12345", "ts_ms": 0},
        {"stanox": "67890", "ts_ms": 0},
    ]
    preds = simulate_train(stops, net)
    assert preds[0]["predicted_ms"] > 0


def test_simulate_default_fallback():
    # No timing and no coords → DEFAULT_SECONDS = 180
    net = make_network()
    stops = [
        {"stanox": "AAAA", "ts_ms": 0},
        {"stanox": "BBBB", "ts_ms": 0},
    ]
    preds = simulate_train(stops, net)
    assert preds[0]["predicted_ms"] == 180_000


# --- metrics ---

def test_compute_metrics_perfect():
    preds = [{"predicted_ms": 1000, "actual_ms": 1000} for _ in range(10)]
    m = compute_metrics(preds)
    assert m["mae_s"] == 0.0
    assert m["within_60s_pct"] == 100.0


def test_compute_metrics_mixed():
    preds = [
        {"predicted_ms": 0, "actual_ms": 30_000},    # 30s error
        {"predicted_ms": 0, "actual_ms": 90_000},    # 90s error
        {"predicted_ms": 0, "actual_ms": 200_000},   # 200s error
    ]
    m = compute_metrics(preds)
    assert m["n"] == 3
    assert abs(m["mae_s"] - (30 + 90 + 200) / 3) < 0.1
    assert m["within_60s_pct"] == pytest.approx(33.3, abs=0.2)
    assert m["within_300s_pct"] == 100.0


# --- horizon_s in predictions ---

def test_simulate_emits_horizon_s():
    net = make_network({("AAA", "BBB"): 300, ("BBB", "CCC"): 600})
    stops = [
        {"stanox": "AAA", "ts_ms": 0},
        {"stanox": "BBB", "ts_ms": 320_000},   # 320s later
        {"stanox": "CCC", "ts_ms": 950_000},   # 630s after that
    ]
    preds = simulate_train(stops, net)
    # Stop 1: horizon = actual_ms - anchor_ms = 320000 - 0 = 320s
    assert preds[0]["horizon_s"] == 320
    # Stop 2: horizon = 950000 - 0 = 950s
    assert preds[1]["horizon_s"] == 950


# --- compute_metrics_by_horizon ---

def test_horizon_buckets_split_correctly():
    # 3 predictions: 60s, 500s, 4000s horizons
    preds = [
        {"horizon_s": 60,   "predicted_ms": 0, "actual_ms": 30_000},   # 30s err, ≤5min bucket
        {"horizon_s": 500,  "predicted_ms": 0, "actual_ms": 90_000},   # 90s err, 5-15min
        {"horizon_s": 4000, "predicted_ms": 0, "actual_ms": 200_000},  # 200s err, 1-2hr
    ]
    rows = compute_metrics_by_horizon(preds)
    by_label = {r["label"]: r for r in rows}

    assert by_label["≤5 min"]["n"] == 1
    assert by_label["≤5 min"]["mae_s"] == 30.0

    assert by_label["5-15 min"]["n"] == 1
    assert by_label["5-15 min"]["mae_s"] == 90.0

    assert by_label["1-2 hr"]["n"] == 1
    assert by_label["1-2 hr"]["mae_s"] == 200.0

    # Remaining buckets empty
    assert by_label["15-30 min"]["n"] == 0
    assert by_label[">4 hr"]["n"] == 0


def test_horizon_bias_direction():
    # All predictions are 60s late (positive bias)
    preds = [
        {"horizon_s": 100, "predicted_ms": 160_000, "actual_ms": 100_000}
        for _ in range(5)
    ]
    rows = compute_metrics_by_horizon(preds)
    by_label = {r["label"]: r for r in rows}
    assert by_label["≤5 min"]["bias_s"] == pytest.approx(60.0)


def test_horizon_no_horizon_s_defaults_to_first_bucket():
    # Records without horizon_s should fall in ≤5 min bucket
    preds = [{"predicted_ms": 0, "actual_ms": 10_000}]
    rows = compute_metrics_by_horizon(preds)
    by_label = {r["label"]: r for r in rows}
    assert by_label["≤5 min"]["n"] == 1


# --- compute_bias ---

def test_bias_positive_when_predicts_late():
    preds = [{"predicted_ms": 110_000, "actual_ms": 100_000}]  # pred 10s after actual
    b = compute_bias(preds)
    assert b["mean_bias_s"] == pytest.approx(10.0)
    assert b["late_pct"] == 0.0   # 10s is not > 30s threshold


def test_bias_negative_when_predicts_early():
    preds = [{"predicted_ms": 0, "actual_ms": 60_000}]  # pred 60s before actual
    b = compute_bias(preds)
    assert b["mean_bias_s"] == pytest.approx(-60.0)
    assert b["early_pct"] == 100.0


# --- calibrate ---

def test_calibrate_finds_scale():
    # Build a network where A→B takes 300s (BPLAN)
    # Trains actually take 240s → time_scale=0.8 is optimal
    net = make_network({("AAA", "BBB"): 300, ("BBB", "CCC"): 300})

    def make_train(actual_leg_ms):
        return {
            "train_id": "T1",
            "toc_id": "XX",
            "stops": [
                {"stanox": "AAA", "ts_ms": 0},
                {"stanox": "BBB", "ts_ms": actual_leg_ms},
                {"stanox": "CCC", "ts_ms": actual_leg_ms * 2},
            ],
        }

    trains = [make_train(240_000) for _ in range(20)]
    best_scale, _ = calibrate(trains, net, grid=[0.7, 0.8, 0.9, 1.0])
    assert best_scale == 0.8
