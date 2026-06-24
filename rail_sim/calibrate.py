"""
Calibrate the time_scale parameter by minimising MAE on a day of observations.

The model has one global parameter:
  time_scale  — multiplier on BPLAN running times (fitted to observed data)
  time_scale < 1: trains run faster than BPLAN baseline
  time_scale > 1: trains run slower
"""

import json

from rail_sim.model import Network
from rail_sim.sim import simulate_all
from rail_sim.validate import compute_metrics

# Search grid: 21 points from 0.5 to 1.5
_GRID = [round(0.5 + i * 0.05, 2) for i in range(21)]


def calibrate(
    trains: list[dict],
    network: Network,
    grid: list[float] | None = None,
) -> tuple[float, dict]:
    """
    Grid-search over time_scale values.

    Returns (best_time_scale, best_metrics).
    """
    if grid is None:
        grid = _GRID

    best_scale = 1.0
    best_mae = float("inf")
    best_metrics: dict = {}

    if not trains:
        print("No training data — returning default time_scale=1.0")
        return best_scale, {}

    print(f"Calibrating over {len(grid)} time_scale values on {len(trains)} trains…")
    for scale in grid:
        preds = simulate_all(trains, network, time_scale=scale)
        m = compute_metrics(preds)
        mae = m.get("mae_s", float("inf"))
        if mae is None:
            mae = float("inf")
        print(f"  time_scale={scale:.2f}  MAE={mae:.1f}s  n={m.get('n',0):,}")
        if mae < best_mae:
            best_mae = mae
            best_scale = scale
            best_metrics = m

    print(f"\nBest time_scale: {best_scale}  MAE: {best_mae:.1f}s")
    return best_scale, best_metrics


def save_model(path: str, time_scale: float, metrics: dict, trained_on: str) -> None:
    model = {
        "time_scale": time_scale,
        "trained_on": trained_on,
        "mae_s": metrics.get("mae_s"),
        "rmse_s": metrics.get("rmse_s"),
        "n_predictions": metrics.get("n"),
    }
    with open(path, "w") as f:
        json.dump(model, f, indent=2)
    print(f"Model saved to {path}")


def load_model(path: str) -> dict:
    with open(path) as f:
        return json.load(f)
