"""Compute accuracy metrics comparing simulated to observed train timings."""

import math


def compute_metrics(predictions: list[dict]) -> dict:
    """
    predictions: list of {'predicted_ms': int, 'actual_ms': int, ...}

    Returns dict with mae_s, rmse_s, within_60s_pct, within_120s_pct, within_300s_pct,
    median_error_s, n.
    """
    if not predictions:
        return {"n": 0}

    errors = [abs(p["predicted_ms"] - p["actual_ms"]) / 1000.0 for p in predictions]
    n = len(errors)
    mae = sum(errors) / n
    rmse = math.sqrt(sum(e ** 2 for e in errors) / n)
    sorted_e = sorted(errors)
    median_e = sorted_e[n // 2]

    return {
        "n": n,
        "mae_s": round(mae, 1),
        "rmse_s": round(rmse, 1),
        "median_error_s": round(median_e, 1),
        "within_60s_pct": round(100 * sum(1 for e in errors if e <= 60) / n, 1),
        "within_120s_pct": round(100 * sum(1 for e in errors if e <= 120) / n, 1),
        "within_300s_pct": round(100 * sum(1 for e in errors if e <= 300) / n, 1),
    }


def metrics_by_toc(predictions: list[dict]) -> dict[str, dict]:
    by_toc: dict[str, list[dict]] = {}
    for p in predictions:
        toc = p.get("toc_id", "??")
        by_toc.setdefault(toc, []).append(p)
    return {toc: compute_metrics(preds) for toc, preds in by_toc.items()}


def print_report(metrics: dict, by_toc: dict[str, dict] | None = None) -> None:
    print(f"\n{'='*50}")
    print(f"  Predictions : {metrics.get('n', 0):,}")
    print(f"  MAE         : {metrics.get('mae_s', '?')} s")
    print(f"  RMSE        : {metrics.get('rmse_s', '?')} s")
    print(f"  Median error: {metrics.get('median_error_s', '?')} s")
    print(f"  Within  60s : {metrics.get('within_60s_pct', '?')} %")
    print(f"  Within 120s : {metrics.get('within_120s_pct', '?')} %")
    print(f"  Within 300s : {metrics.get('within_300s_pct', '?')} %")
    print(f"{'='*50}")

    if by_toc:
        print("\n  By TOC (MAE seconds):")
        for toc, m in sorted(by_toc.items(), key=lambda x: x[1].get("mae_s", 9999)):
            print(f"    {toc:>4}  n={m['n']:>5,}  MAE={m['mae_s']:>7.1f}s")
