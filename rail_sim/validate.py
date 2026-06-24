"""Compute accuracy metrics comparing simulated to observed train timings."""

import math


_HEADER = f"{'Metric':<20} {'Rail-sim':>10} {'Darwin':>10} {'Delta':>10}"
_SEP = "-" * 54


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


def compute_bias(predictions: list[dict]) -> dict:
    """
    Compute signed error statistics (bias).
    Positive bias = simulator predicts late; negative = predicts early.
    """
    if not predictions:
        return {"n": 0}
    signed = [(p["predicted_ms"] - p["actual_ms"]) / 1000.0 for p in predictions]
    n = len(signed)
    mean_bias = sum(signed) / n
    sorted_s = sorted(signed)
    return {
        "n": n,
        "mean_bias_s": round(mean_bias, 1),
        "median_bias_s": round(sorted_s[n // 2], 1),
        "late_pct": round(100 * sum(1 for s in signed if s > 30) / n, 1),
        "early_pct": round(100 * sum(1 for s in signed if s < -30) / n, 1),
    }


def compute_by_stop_index(trains_with_preds: list[dict]) -> list[dict]:
    """
    Group predictions by stop index (0 = first predicted stop after anchor) and
    compute MAE at each position. Shows whether error compounds along the journey.

    Input: list of {'stop_index': int, 'predicted_ms': int, 'actual_ms': int}.
    Returns: list of {'stop_index': int, 'mae_s': float, 'n': int}.
    """
    by_idx: dict[int, list[float]] = {}
    for p in trains_with_preds:
        idx = p.get("stop_index", 0)
        err = abs(p["predicted_ms"] - p["actual_ms"]) / 1000.0
        by_idx.setdefault(idx, []).append(err)
    return [
        {"stop_index": idx, "n": len(errs), "mae_s": round(sum(errs) / len(errs), 1)}
        for idx, errs in sorted(by_idx.items())
    ]


def print_comparison(
    sim_metrics: dict,
    darwin_metrics: dict | None = None,
    timetable_metrics: dict | None = None,
) -> None:
    """
    Print a side-by-side accuracy table.

    Shows rail-sim, Darwin (if provided), and timetable (if provided) columns.
    Delta column compares rail-sim to Darwin (if Darwin available).
    """
    cols = ["Rail-sim"]
    data = [sim_metrics]
    if timetable_metrics and timetable_metrics.get("n", 0) > 0:
        cols.append("Timetable")
        data.append(timetable_metrics)
    if darwin_metrics and darwin_metrics.get("n", 0) > 0:
        cols.append("Darwin")
        data.append(darwin_metrics)

    col_w = 11
    header = f"  {'Metric':<20}" + "".join(f" {c:>{col_w}}" for c in cols)
    sep = "-" * (22 + col_w * len(cols) + len(cols))

    print(f"\n{'='*(22 + col_w * len(cols) + len(cols))}")
    print("  Accuracy comparison")
    print(sep)
    print(header)
    print(sep)

    def _v(m: dict, key: str) -> str:
        v = m.get(key)
        return f"{v:>{col_w}.1f}" if v is not None else f"{'N/A':>{col_w}}"

    print(f"  {'Predictions':<20}" + "".join(f" {m.get('n', 0):>{col_w},}" for m in data))
    for label, key in [
        ("MAE (s)", "mae_s"),
        ("RMSE (s)", "rmse_s"),
        ("Median err (s)", "median_error_s"),
        ("Within 60s (%)", "within_60s_pct"),
        ("Within 120s (%)", "within_120s_pct"),
        ("Within 300s (%)", "within_300s_pct"),
    ]:
        print(f"  {label:<20}" + "".join(_v(m, key) for m in data))

    print(f"{'='*(22 + col_w * len(cols) + len(cols))}")
    if len(cols) > 1:
        print("  Lower MAE/RMSE is better; higher Within-Xs% is better.\n")
