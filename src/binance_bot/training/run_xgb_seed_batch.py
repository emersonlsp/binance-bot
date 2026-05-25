from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from .xgb_clean_search import SearchConfig, run_xgb_clean_search


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run XGB clean search for multiple seeds sequentially.")
    p.add_argument(
        "--seeds",
        type=int,
        nargs="+",
        default=[0, 1],
        help="Seed list to run in sequence (default: 0 1).",
    )
    p.add_argument("--candidates", type=int, default=120, help="Candidates per seed.")
    p.add_argument("--folds", type=int, default=4, help="Walk-forward folds.")
    p.add_argument("--min-trades", type=int, default=40, help="Min trades threshold.")
    p.add_argument(
        "--min-positive-folds",
        type=int,
        default=3,
        help="Min positive folds threshold.",
    )
    p.add_argument("--workers", type=int, default=6, help="Parallel workers.")
    p.add_argument(
        "--xgb-device",
        choices=["cpu", "cuda"],
        default="cpu",
        help="XGBoost device. Use 'cuda' for NVIDIA GPU acceleration.",
    )
    p.add_argument(
        "--regime-gate",
        choices=["on", "off"],
        default="on",
        help="Enable/disable regime gate.",
    )
    p.add_argument(
        "--regime-chop-min-confidence",
        type=float,
        default=0.78,
        help="Regime chop confidence floor when regime gate is on.",
    )
    p.add_argument("--embargo-gap-steps", type=int, default=0, help="Purge/embargo gap between train and test windows.")
    p.add_argument("--entry-latency-steps", type=int, default=0, help="Execution latency in event steps.")
    p.add_argument(
        "--allow-partial-fill",
        choices=["on", "off"],
        default="on",
        help="Allow partial fills in execution simulation.",
    )
    p.add_argument("--min-fill-ratio", type=float, default=0.1, help="Minimum fill ratio required to execute.")
    p.add_argument(
        "--raw-root",
        type=Path,
        default=Path("data/raw/binance/BTCBRL"),
        help="Root with raw Binance data.",
    )
    p.add_argument(
        "--output-root",
        type=Path,
        default=Path("artifacts/reports/xgb_clean_search_batch"),
        help="Root directory for per-seed outputs.",
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    timing_log = args.output_root / "timing_log.jsonl"

    for seed in args.seeds:
        out_dir = args.output_root / f"seed_{seed}"
        print(
            f"[xgb_batch] start seed={seed} candidates={args.candidates} folds={args.folds} out={out_dir}",
            flush=True,
        )
        cfg = SearchConfig(
            candidates=args.candidates,
            n_folds=args.folds,
            min_trades=args.min_trades,
            min_positive_folds=args.min_positive_folds,
            seed=seed,
            workers=args.workers,
            xgb_device=args.xgb_device,
            regime_gate=(args.regime_gate == "on"),
            regime_chop_min_confidence=args.regime_chop_min_confidence,
            embargo_gap_steps=max(0, args.embargo_gap_steps),
            entry_latency_steps=max(0, args.entry_latency_steps),
            allow_partial_fill=(args.allow_partial_fill == "on"),
            min_fill_ratio=float(args.min_fill_ratio),
        )
        result = run_xgb_clean_search(raw_root=args.raw_root, output_dir=out_dir, cfg=cfg)
        promoted = result.get("promoted_champion")
        champion = result.get("champion")
        timing = result.get("timing", {})
        timing_row = {
            "ts_utc": datetime.now(UTC).isoformat(),
            "seed": seed,
            "output_dir": str(out_dir),
            "candidates": args.candidates,
            "folds": args.folds,
            "workers": args.workers,
            "xgb_device": args.xgb_device,
            "cache_phase_seconds": timing.get("cache_phase_seconds"),
            "candidate_phase_seconds": timing.get("candidate_phase_seconds"),
            "total_seconds": timing.get("total_seconds"),
            "cache_hit_count": timing.get("cache_hit_count"),
            "cache_build_count": timing.get("cache_build_count"),
            "champion_name": (champion or {}).get("name"),
            "promoted_name": (promoted or {}).get("name"),
        }
        with timing_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(timing_row, ensure_ascii=False) + "\n")
        print(
            f"[xgb_batch] done seed={seed} champion={champion['name'] if champion else 'none'} "
            f"promoted={promoted['name'] if promoted else 'none'} "
            f"cache={timing.get('cache_phase_seconds', 0)}s "
            f"candidates={timing.get('candidate_phase_seconds', 0)}s "
            f"total={timing.get('total_seconds', 0)}s",
            flush=True,
        )

    print(f"[xgb_batch] finished all seeds. timing_log={timing_log}", flush=True)


if __name__ == "__main__":
    main()
