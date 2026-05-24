from __future__ import annotations

import argparse
from pathlib import Path

from .xgb_clean_search import SearchConfig, run_xgb_clean_search


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Run robust XGBoost clean search.")
    p.add_argument("--candidates", type=int, default=12, help="Number of sampled candidates.")
    p.add_argument("--folds", type=int, default=4, help="Number of walk-forward folds.")
    p.add_argument(
        "--min-trades",
        type=int,
        default=40,
        help="Minimum trades required before heavy score penalty.",
    )
    p.add_argument(
        "--min-positive-folds",
        type=int,
        default=3,
        help="Minimum count of positive-PnL folds required before heavy score penalty.",
    )
    p.add_argument("--seed", type=int, default=42, help="Random seed for candidate sampling.")
    p.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel worker processes (use 2-6 depending on CPU/RAM).",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=Path("artifacts/reports/xgb_clean_search"),
        help="Directory for per-candidate reports and leaderboard.",
    )
    p.add_argument(
        "--raw-root",
        type=Path,
        default=Path("data/raw/binance/BTCBRL"),
        help="Root directory with collected raw data.",
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()
    cfg = SearchConfig(
        candidates=args.candidates,
        n_folds=args.folds,
        min_trades=args.min_trades,
        min_positive_folds=args.min_positive_folds,
        seed=args.seed,
        workers=args.workers,
    )
    result = run_xgb_clean_search(
        raw_root=args.raw_root,
        output_dir=args.output_dir,
        cfg=cfg,
    )
    champion = result.get("champion")
    if champion:
        s = champion["summary"]
        print(
            "[xgb_clean] champion:",
            champion["name"],
            "pnl_brl=",
            round(float(s["mean_pnl_net_brl"]), 2),
            "trades=",
            int(s["total_trades"]),
        )
    print("[xgb_clean] done. leaderboard:", args.output_dir / "leaderboard.json")


if __name__ == "__main__":
    main()
