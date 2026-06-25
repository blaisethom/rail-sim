"""
rail-sim command-line interface.

Commands:
  build-network   Parse BPLAN + CORPUS into a cached network JSON
  simulate        Run simulation for a given date and time_scale
  validate        Print accuracy metrics for a simulation output
  calibrate       Find the best time_scale on a training date
  run-all         build-network → calibrate → validate in one shot
"""

import argparse
import json
import sys


def cmd_build_network(args: argparse.Namespace) -> None:
    from rail_sim.model import Network

    net = Network.build(args.bplan, args.corpus)
    net.save(args.out)
    print(f"Network saved to {args.out}")


def cmd_simulate(args: argparse.Namespace) -> None:
    from rail_sim.model import Network
    from rail_sim.observations import load_observations
    from rail_sim.sim import simulate_all
    from rail_sim.validate import compute_metrics, metrics_by_toc, print_report, print_comparison

    net = Network.load(args.network)
    print(f"Loading observations for {args.date}…")
    trains = load_observations(args.db, args.date)
    print(f"  {len(trains)} trains with 3+ stops")

    preds = simulate_all(trains, net, time_scale=args.time_scale)
    metrics = compute_metrics(preds)
    by_toc = metrics_by_toc(preds)
    print_report(metrics, by_toc)

    if args.darwin_db:
        _print_darwin_comparison(args.darwin_db, args.date, net, metrics, args.tz_offset)

    if args.out:
        out = {
            "date": args.date,
            "time_scale": args.time_scale,
            "metrics": metrics,
            "predictions": preds,
        }
        with open(args.out, "w") as f:
            json.dump(out, f)
        print(f"Output saved to {args.out}")


def cmd_validate(args: argparse.Namespace) -> None:
    from rail_sim.validate import compute_metrics, metrics_by_toc, print_report

    with open(args.sim) as f:
        data = json.load(f)
    preds = data["predictions"]
    metrics = compute_metrics(preds)
    by_toc = metrics_by_toc(preds)
    print(f"Simulation: date={data.get('date')}  time_scale={data.get('time_scale')}")
    print_report(metrics, by_toc)


def cmd_calibrate(args: argparse.Namespace) -> None:
    from rail_sim.calibrate import calibrate, save_model
    from rail_sim.model import Network
    from rail_sim.observations import load_observations

    net = Network.load(args.network)
    print(f"Loading observations for {args.date}…")
    trains = load_observations(args.db, args.date)
    print(f"  {len(trains)} trains")

    best_scale, best_metrics = calibrate(trains, net)
    save_model(args.out, best_scale, best_metrics, trained_on=args.date)


def _print_darwin_comparison(darwin_db: str, date_str: str, net, sim_metrics: dict, tz_offset: int) -> None:
    """Load Darwin baseline for date_str and print side-by-side comparison."""
    from rail_sim.darwin_baseline import load_darwin_baseline
    from rail_sim.validate import compute_metrics, print_comparison

    tiploc_stanox = net.tiploc_stanox
    if not tiploc_stanox:
        print("  (Darwin comparison skipped: rebuild network with --rebuild to include TIPLOC→STANOX mapping)")
        return

    print(f"\nLoading Darwin baseline from {darwin_db} for {date_str}…")
    baseline = load_darwin_baseline(darwin_db, date_str, tiploc_stanox, tz_hours=tz_offset)
    if not baseline:
        print("  No Darwin↔NROD matched predictions found for this date.")
        print("  Run tools/build_links.py and ensure darwin_ingest is collecting data.")
        return
    print(f"  {len(baseline)} Darwin predictions matched to NROD observations")
    darwin_metrics = compute_metrics(baseline)
    print_comparison(sim_metrics, darwin_metrics)


def cmd_run_all(args: argparse.Namespace) -> None:
    """Build network, calibrate on train_date, validate on val_date."""
    import os
    from rail_sim.calibrate import calibrate, load_model, save_model
    from rail_sim.model import Network
    from rail_sim.observations import load_observations
    from rail_sim.sim import simulate_all
    from rail_sim.validate import compute_metrics, metrics_by_toc, print_report

    network_path = os.path.join(args.data_dir, "network.json")
    model_path = os.path.join(args.data_dir, "model.json")

    # 1. Build network
    if os.path.exists(network_path) and not args.rebuild:
        print(f"Loading cached network from {network_path}")
        net = Network.load(network_path)
    else:
        net = Network.build(args.bplan, args.corpus)
        net.save(network_path)
        print(f"Network saved to {network_path}")

    # 2. Calibrate
    print(f"\n--- Calibration ({args.train_date}) ---")
    train_obs = load_observations(args.db, args.train_date)
    print(f"  {len(train_obs)} trains")
    best_scale, best_metrics = calibrate(train_obs, net)
    save_model(model_path, best_scale, best_metrics, trained_on=args.train_date)

    # 3. Validate on a different day
    print(f"\n--- Validation ({args.val_date}) ---")
    val_obs = load_observations(args.db, args.val_date)
    print(f"  {len(val_obs)} trains")
    preds = simulate_all(val_obs, net, time_scale=best_scale)
    metrics = compute_metrics(preds)
    by_toc = metrics_by_toc(preds)
    print_report(metrics, by_toc)

    # Timetable baseline (scheduled vs actual from NROD payload)
    from rail_sim.observations import timetable_baseline
    from rail_sim.validate import compute_bias, compute_by_stop_index, print_comparison
    tt_preds = timetable_baseline(val_obs)
    tt_metrics = compute_metrics(tt_preds) if tt_preds else {}

    # Horizon breakdown
    from rail_sim.validate import compute_metrics_by_horizon, print_horizon_table
    horizon_rows = compute_metrics_by_horizon(preds)
    print_horizon_table(horizon_rows, source_label="Rail-sim")

    # Bias report
    bias = compute_bias(preds)
    print(f"\n--- Bias ({args.val_date}) ---")
    print(f"  Mean signed error : {bias.get('mean_bias_s', '?')} s  (+ = sim predicts late)")
    print(f"  Median signed error: {bias.get('median_bias_s', '?')} s")
    print(f"  Predicted late (>30s): {bias.get('late_pct', '?')} %")
    print(f"  Predicted early (<-30s): {bias.get('early_pct', '?')} %")

    # Error by stop index
    by_idx = compute_by_stop_index(preds)
    if by_idx:
        print("\n--- Error by stop index (does it compound?) ---")
        print(f"  {'Stop':<6} {'n':>6} {'MAE (s)':>9}")
        for row in by_idx[:10]:
            print(f"  {row['stop_index']:<6} {row['n']:>6,} {row['mae_s']:>9.1f}")
        if len(by_idx) > 10:
            print(f"  ... ({len(by_idx)} total stop positions)")

    print(f"\nDone. Calibrated model: time_scale={best_scale}, MAE={best_metrics.get('mae_s')}s (train)")
    print(f"Held-out validation:   MAE={metrics.get('mae_s')}s")

    if args.darwin_db:
        from rail_sim.darwin_baseline import load_darwin_baseline, load_darwin_horizon_predictions
        tiploc_stanox = net.tiploc_stanox
        if tiploc_stanox:
            print(f"\nLoading Darwin baseline from {args.darwin_db} for {args.val_date}…")
            baseline = load_darwin_baseline(args.darwin_db, args.val_date, tiploc_stanox, tz_hours=args.tz_offset)
            darwin_metrics = compute_metrics(baseline) if baseline else {}
            if baseline:
                print(f"  {len(baseline)} Darwin last-known predictions matched to NROD")
            print_comparison(metrics, darwin_metrics or None, tt_metrics or None)

            # Darwin horizon breakdown (requires prediction history)
            dar_horizon = load_darwin_horizon_predictions(args.darwin_db, args.val_date, tiploc_stanox, tz_hours=args.tz_offset)
            if dar_horizon:
                dar_h_rows = compute_metrics_by_horizon(dar_horizon)
                print_horizon_table(dar_h_rows, source_label="Darwin")
            else:
                print("  (Darwin horizon breakdown not available — prediction history accumulates from today)")
        else:
            print("  (Darwin comparison skipped: rebuild network to include TIPLOC→STANOX)")
    elif tt_metrics:
        print_comparison(metrics, timetable_metrics=tt_metrics)


def main() -> None:
    parser = argparse.ArgumentParser(prog="rail-sim", description="UK rail movement simulator")
    sub = parser.add_subparsers(dest="command", required=True)

    # build-network
    p = sub.add_parser("build-network", help="Parse BPLAN + CORPUS into network.json")
    p.add_argument("--bplan", default="BPLAN.zip")
    p.add_argument("--corpus", default="corpus.json")
    p.add_argument("--out", default="data/network.json")

    # simulate
    p = sub.add_parser("simulate", help="Run simulation for a date")
    p.add_argument("--network", default="data/network.json")
    p.add_argument("--db", required=True, help="Path to railmetrics.db")
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--time-scale", type=float, default=1.0)
    p.add_argument("--out", default=None, help="Save predictions JSON")
    p.add_argument("--darwin-db", default=None, help="Path to railmetrics.db for Darwin baseline comparison")
    p.add_argument("--tz-offset", type=int, default=1, help="Darwin timezone offset hours (1=BST, 0=GMT)")

    # validate
    p = sub.add_parser("validate", help="Print metrics from a saved simulation JSON")
    p.add_argument("sim", help="Path to simulation output JSON")

    # calibrate
    p = sub.add_parser("calibrate", help="Find best time_scale on a training date")
    p.add_argument("--network", default="data/network.json")
    p.add_argument("--db", required=True)
    p.add_argument("--date", required=True, help="Training date YYYY-MM-DD")
    p.add_argument("--out", default="data/model.json")

    # run-all
    p = sub.add_parser("run-all", help="Build network, calibrate, validate end-to-end")
    p.add_argument("--bplan", default="BPLAN.zip")
    p.add_argument("--corpus", default="corpus.json")
    p.add_argument("--db", required=True)
    p.add_argument("--train-date", required=True, help="Calibration date YYYY-MM-DD")
    p.add_argument("--val-date", required=True, help="Validation date YYYY-MM-DD")
    p.add_argument("--data-dir", default="data")
    p.add_argument("--rebuild", action="store_true", help="Force rebuild of network.json")
    p.add_argument("--darwin-db", default=None, help="Path to railmetrics.db for Darwin baseline comparison")
    p.add_argument("--tz-offset", type=int, default=1, help="Darwin timezone offset hours (1=BST, 0=GMT)")

    args = parser.parse_args()
    dispatch = {
        "build-network": cmd_build_network,
        "simulate": cmd_simulate,
        "validate": cmd_validate,
        "calibrate": cmd_calibrate,
        "run-all": cmd_run_all,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    main()
