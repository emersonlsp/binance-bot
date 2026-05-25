from __future__ import annotations

import argparse
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from .paper_aligned import build_paper_aligned_dataset, run_paper_aligned_training_from_dataset


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Evaluate a single saved candidate/champion on current data.")
    p.add_argument(
        "--source",
        type=Path,
        default=Path("artifacts/champion_strategy_xgb_clean.json"),
        help="Candidate/champion JSON source.",
    )
    p.add_argument(
        "--raw-root",
        type=Path,
        default=Path("data/raw/binance/BTCBRL"),
        help="Raw Binance data root.",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/reports/single_candidate_eval/report.json"),
        help="Output report JSON path.",
    )
    p.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("data/features/binance/BTCBRL/single_eval_cache"),
        help="Dataset cache directory for faster reruns.",
    )
    p.add_argument(
        "--rebuild-cache",
        action="store_true",
        help="Force rebuild cached features/labeled rows.",
    )
    p.add_argument(
        "--xgb-n-jobs",
        type=int,
        default=6,
        help="Threads used by XGBoost during fit/predict (single candidate speed-up).",
    )
    p.add_argument(
        "--xgb-device",
        choices=["cpu", "cuda"],
        default="cpu",
        help="XGBoost device for this single evaluation.",
    )
    p.add_argument(
        "--regime-gate",
        choices=["inherit", "on", "off"],
        default="inherit",
        help="Regime gate behavior. 'inherit' uses value from source/search config when available.",
    )
    p.add_argument(
        "--regime-chop-min-confidence",
        type=float,
        default=None,
        help="Override chop confidence threshold for regime gate. Default inherits from source when available.",
    )
    p.add_argument(
        "--embargo-gap-steps",
        type=int,
        default=None,
        help="Override purge/embargo gap steps. Default inherits from source when available.",
    )
    p.add_argument(
        "--entry-latency-steps",
        type=int,
        default=None,
        help="Override entry latency steps. Default inherits from source when available.",
    )
    p.add_argument(
        "--allow-partial-fill",
        choices=["inherit", "on", "off"],
        default="inherit",
        help="Override partial fill behavior. Default inherits from source when available.",
    )
    p.add_argument(
        "--min-fill-ratio",
        type=float,
        default=None,
        help="Override minimum fill ratio. Default inherits from source when available.",
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()
    print("[single_eval] starting...", flush=True)
    if not args.source.exists():
        raise FileNotFoundError(f"source not found: {args.source}")

    print(f"[single_eval] loading source: {args.source}", flush=True)
    obj = json.loads(args.source.read_text(encoding="utf-8"))
    params = obj.get("params", {})
    search_cfg = obj.get("search_config", {})
    strategy_name = str(obj.get("strategy_name", "mlofi_xgb_v1"))
    strategy_kwargs = dict(params.get("strategy_kwargs", obj.get("strategy_kwargs", {})))
    strategy_kwargs["n_jobs"] = int(args.xgb_n_jobs)
    strategy_kwargs["xgb_device"] = str(args.xgb_device)
    horizon = int(params.get("horizon_steps", 80))
    move_bps = float(params.get("move_threshold_bps", 0.8))
    sample_every = int(params.get("sample_every_updates", 40))
    folds = int(params.get("n_folds", 4))
    cost_bps = float(params.get("cost_bps_per_side", 1.5))
    min_conf = float(params.get("min_signal_confidence", 0.75))
    max_spread = float(params.get("max_spread_brl", 3.0))
    inherited_regime_gate = bool(search_cfg.get("regime_gate", False))
    inherited_regime_chop_min_conf = float(search_cfg.get("regime_chop_min_confidence", 0.78))
    inherited_embargo = int(search_cfg.get("embargo_gap_steps", 0))
    inherited_entry_latency = int(search_cfg.get("entry_latency_steps", 0))
    inherited_allow_partial_fill = bool(search_cfg.get("allow_partial_fill", True))
    inherited_min_fill_ratio = float(search_cfg.get("min_fill_ratio", 0.1))

    if args.regime_gate == "on":
        regime_gate_enabled = True
    elif args.regime_gate == "off":
        regime_gate_enabled = False
    else:
        regime_gate_enabled = inherited_regime_gate

    regime_chop_min_conf = (
        float(args.regime_chop_min_confidence)
        if args.regime_chop_min_confidence is not None
        else inherited_regime_chop_min_conf
    )
    embargo_gap_steps = (
        max(0, int(args.embargo_gap_steps))
        if args.embargo_gap_steps is not None
        else max(0, inherited_embargo)
    )
    entry_latency_steps = (
        max(0, int(args.entry_latency_steps))
        if args.entry_latency_steps is not None
        else max(0, inherited_entry_latency)
    )
    if args.allow_partial_fill == "on":
        allow_partial_fill = True
    elif args.allow_partial_fill == "off":
        allow_partial_fill = False
    else:
        allow_partial_fill = inherited_allow_partial_fill
    min_fill_ratio = (
        float(args.min_fill_ratio)
        if args.min_fill_ratio is not None
        else inherited_min_fill_ratio
    )

    key_name = f"h{horizon}_m{str(move_bps).replace('.', '_')}_s{sample_every}"
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    feature_cache = args.cache_dir / f"{key_name}_features.parquet"
    labeled_cache = args.cache_dir / f"{key_name}_labeled.parquet"

    if feature_cache.exists() and labeled_cache.exists() and not args.rebuild_cache:
        print(f"[single_eval] loading cache files: {feature_cache.name}, {labeled_cache.name}", flush=True)
        feature_rows = pq.read_table(feature_cache).to_pylist()
        labeled_rows = pq.read_table(labeled_cache).to_pylist()
        ds = {
            "update_rows_count": 0,
            "bad_minutes_count": 0,
            "feature_rows": feature_rows,
            "labeled_rows": labeled_rows,
        }
        print(f"[single_eval] cache hit: {key_name} rows={len(labeled_rows)}")
    else:
        print("[single_eval] building dataset cache from raw data...", flush=True)
        ds = build_paper_aligned_dataset(
            raw_root=args.raw_root,
            horizon_steps=horizon,
            move_threshold_bps=move_bps,
            sample_every_updates=sample_every,
            feature_output_path=None,
        )
        pq.write_table(pa.Table.from_pylist(ds["feature_rows"]), feature_cache)
        pq.write_table(pa.Table.from_pylist(ds["labeled_rows"]), labeled_cache)
        print(f"[single_eval] cache build: {key_name} rows={len(ds['labeled_rows'])}")
    print("[single_eval] running walk-forward evaluation...", flush=True)
    report = run_paper_aligned_training_from_dataset(
        dataset=ds,
        report_output_path=args.output,
        strategy_name=strategy_name,
        strategy_kwargs=strategy_kwargs,
        horizon_steps=horizon,
        n_folds=folds,
        cost_bps_per_side=cost_bps,
        sample_every_updates=sample_every,
        min_signal_confidence=min_conf,
        max_spread_brl=max_spread,
        regime_gate_enabled=regime_gate_enabled,
        regime_chop_min_confidence=regime_chop_min_conf,
        embargo_gap_steps=embargo_gap_steps,
        entry_latency_steps=entry_latency_steps,
        allow_partial_fill=allow_partial_fill,
        min_fill_ratio=min_fill_ratio,
    )
    s = report.get("summary", {})
    print(
        "[single_eval]",
        f"name={obj.get('name','unknown')}",
        f"xgb_n_jobs={int(args.xgb_n_jobs)}",
        f"xgb_device={str(args.xgb_device)}",
        f"pnl_brl={float(s.get('mean_pnl_net_brl',0.0)):.2f}",
        f"trades={int(s.get('total_trades',0))}",
        f"win_rate={float(s.get('mean_win_rate',0.0)):.2%}",
    )
    print("[single_eval] report:", args.output)


if __name__ == "__main__":
    main()
