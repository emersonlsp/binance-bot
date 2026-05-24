from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from random import Random
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from .paper_aligned import build_paper_aligned_dataset, run_paper_aligned_training_from_dataset


@dataclass(slots=True)
class SearchConfig:
    candidates: int = 24
    n_folds: int = 4
    min_trades: int = 40
    min_positive_folds: int = 3
    seed: int = 42
    workers: int = 1


def _sample_candidate(
    rng: Random,
    idx: int,
    *,
    xgb_n_jobs: int,
    data_profile: tuple[int, float, int],
) -> dict[str, Any]:
    horizon_steps, move_threshold_bps, sample_every_updates = data_profile
    min_signal_confidence = rng.choice([0.60, 0.65, 0.70, 0.75, 0.80, 0.85])
    max_spread_brl = rng.choice([1.5, 2.0, 2.5, 3.0, 4.0])
    strategy_kwargs = {
        "n_estimators": rng.choice([200, 250, 300, 400, 500]),
        "max_depth": rng.choice([3, 4, 5, 6]),
        "learning_rate": rng.choice([0.02, 0.03, 0.04, 0.05]),
        "subsample": rng.choice([0.8, 0.9, 1.0]),
        "colsample_bytree": rng.choice([0.8, 0.9, 1.0]),
        "reg_lambda": rng.choice([0.5, 1.0, 2.0, 5.0]),
        "min_confidence": rng.choice([0.55, 0.60, 0.65, 0.70]),
        "n_jobs": xgb_n_jobs,
    }
    return {
        "name": f"xgb_clean_{idx:03d}",
        "strategy_name": "mlofi_xgb_v1",
        "strategy_kwargs": strategy_kwargs,
        "horizon_steps": horizon_steps,
        "move_threshold_bps": move_threshold_bps,
        "sample_every_updates": sample_every_updates,
        "cost_bps_per_side": 1.5,
        "min_signal_confidence": min_signal_confidence,
        "max_spread_brl": max_spread_brl,
    }


def _score(entry: dict[str, Any], *, min_trades: int, min_positive_folds: int) -> float:
    summary = entry["summary"]
    pnl = float(summary["mean_pnl_net_brl"])
    dd = float(summary["mean_max_drawdown_brl"])
    expectancy = float(summary["mean_expectancy_brl_per_trade"])
    trades = int(summary["total_trades"])
    fold_pnls = [float(x.get("pnl_net_brl", 0.0)) for x in entry.get("folds_brl", [])]
    positive_folds = sum(1 for x in fold_pnls if x > 0)
    penalty = 0.0
    if trades < min_trades:
        penalty -= 1000.0
    if positive_folds < min_positive_folds:
        penalty -= 1000.0
    # Prioritize money made, then trade expectancy, and punish drawdown.
    return pnl + (25.0 * expectancy) - (0.20 * dd) + penalty


def _evaluate_candidate(
    candidate: dict[str, Any],
    *,
    output_dir: Path,
    dataset: dict[str, Any],
    dataset_key: str,
    sample_every_updates: int,
    n_folds: int,
    min_trades: int,
    min_positive_folds: int,
) -> dict[str, Any]:
    report = run_paper_aligned_training_from_dataset(
        dataset=dataset,
        report_output_path=output_dir / f"{candidate['name']}.json",
        strategy_name=candidate["strategy_name"],
        strategy_kwargs=candidate["strategy_kwargs"],
        horizon_steps=int(candidate["horizon_steps"]),
        n_folds=n_folds,
        cost_bps_per_side=float(candidate["cost_bps_per_side"]),
        sample_every_updates=sample_every_updates,
        min_signal_confidence=float(candidate["min_signal_confidence"]),
        max_spread_brl=float(candidate["max_spread_brl"]),
    )
    entry = {
        "name": candidate["name"],
        "params": {
            "horizon_steps": candidate["horizon_steps"],
            "move_threshold_bps": candidate["move_threshold_bps"],
            "sample_every_updates": candidate["sample_every_updates"],
            "min_signal_confidence": candidate["min_signal_confidence"],
            "max_spread_brl": candidate["max_spread_brl"],
            "cost_bps_per_side": candidate["cost_bps_per_side"],
            "n_folds": n_folds,
            "strategy_kwargs": candidate["strategy_kwargs"],
            "dataset_key": dataset_key,
        },
        "summary": report["summary"],
        "folds_brl": report.get("folds_brl", []),
    }
    entry["score"] = _score(
        entry,
        min_trades=min_trades,
        min_positive_folds=min_positive_folds,
    )
    return entry


def _evaluate_group(
    *,
    candidates: list[dict[str, Any]],
    output_dir: Path,
    dataset: dict[str, Any],
    dataset_key: str,
    sample_every_updates: int,
    n_folds: int,
    min_trades: int,
    min_positive_folds: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for candidate in candidates:
        out.append(
            _evaluate_candidate(
                candidate,
                output_dir=output_dir,
                dataset=dataset,
                dataset_key=dataset_key,
                sample_every_updates=sample_every_updates,
                n_folds=n_folds,
                min_trades=min_trades,
                min_positive_folds=min_positive_folds,
            )
        )
    return out


def run_xgb_clean_search(
    *,
    raw_root: Path,
    output_dir: Path,
    cfg: SearchConfig,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path("data/features/binance/BTCBRL/xgb_clean_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    rng = Random(cfg.seed)

    xgb_n_jobs = 1 if cfg.workers > 1 else 4
    # Keep dataset regimes limited to maximize cache reuse and reduce expensive feature rebuilding.
    data_profiles = [
        (50, 0.5, 20),
        (50, 0.8, 20),
        (80, 0.5, 30),
        (80, 0.8, 40),
        (100, 0.8, 50),
        (120, 1.0, 50),
    ]
    candidates = [
        _sample_candidate(
            rng,
            i,
            xgb_n_jobs=xgb_n_jobs,
            data_profile=rng.choice(data_profiles),
        )
        for i in range(1, cfg.candidates + 1)
    ]
    entries: list[dict[str, Any]] = []
    groups: dict[tuple[int, float, int], list[dict[str, Any]]] = {}
    for c in candidates:
        key = (
            int(c["horizon_steps"]),
            float(c["move_threshold_bps"]),
            int(c["sample_every_updates"]),
        )
        groups.setdefault(key, []).append(c)

    print(f"[xgb_clean] dataset groups: {len(groups)} for {len(candidates)} candidates")
    dataset_by_key: dict[tuple[int, float, int], dict[str, Any]] = {}
    for key, _group in groups.items():
        h, m, s = key
        key_name = f"h{h}_m{str(m).replace('.', '_')}_s{s}"
        feature_cache_path = cache_dir / f"{key_name}_features.parquet"
        label_cache_path = cache_dir / f"{key_name}_labeled.parquet"
        if feature_cache_path.exists() and label_cache_path.exists():
            feature_rows = pq.read_table(feature_cache_path).to_pylist()
            labeled_rows = pq.read_table(label_cache_path).to_pylist()
            dataset_by_key[key] = {
                "update_rows_count": 0,
                "bad_minutes_count": 0,
                "feature_rows": feature_rows,
                "labeled_rows": labeled_rows,
            }
            print(f"[xgb_clean] cache hit: {key_name} rows={len(labeled_rows)}")
            continue
        dataset = build_paper_aligned_dataset(
            raw_root=raw_root,
            horizon_steps=h,
            move_threshold_bps=m,
            sample_every_updates=s,
            feature_output_path=None,
        )
        pq.write_table(pa.Table.from_pylist(dataset["feature_rows"]), feature_cache_path)
        pq.write_table(pa.Table.from_pylist(dataset["labeled_rows"]), label_cache_path)
        dataset_by_key[key] = dataset
        print(f"[xgb_clean] cache build: {key_name} rows={len(dataset['labeled_rows'])}")

    if cfg.workers <= 1:
        for key, group in groups.items():
            h, m, s_upd = key
            key_name = f"h{h}_m{str(m).replace('.', '_')}_s{s_upd}"
            ds = dataset_by_key[key]
            for candidate in group:
                entry = _evaluate_candidate(
                    candidate,
                    output_dir=output_dir,
                    dataset=ds,
                    dataset_key=key_name,
                    sample_every_updates=s_upd,
                    n_folds=cfg.n_folds,
                    min_trades=cfg.min_trades,
                    min_positive_folds=cfg.min_positive_folds,
                )
                entries.append(entry)
                s = entry["summary"]
                print(
                    f"[xgb_clean] {candidate['name']} pnl_brl={s['mean_pnl_net_brl']:.2f} "
                    f"exp={s['mean_expectancy_brl_per_trade']:.2f} trades={s['total_trades']} "
                    f"score={entry['score']:.2f}"
                )
    else:
        with ProcessPoolExecutor(max_workers=cfg.workers) as ex:
            futures = {}
            for key, group in groups.items():
                h, m, s_upd = key
                key_name = f"h{h}_m{str(m).replace('.', '_')}_s{s_upd}"
                ds = dataset_by_key[key]
                fut = ex.submit(
                    _evaluate_group,
                    candidates=group,
                    output_dir=output_dir,
                    dataset=ds,
                    dataset_key=key_name,
                    sample_every_updates=s_upd,
                    n_folds=cfg.n_folds,
                    min_trades=cfg.min_trades,
                    min_positive_folds=cfg.min_positive_folds,
                )
                futures[fut] = key_name
            for fut in as_completed(futures):
                batch = fut.result()
                for entry in batch:
                    entries.append(entry)
                    s = entry["summary"]
                    print(
                        f"[xgb_clean] {entry['name']} pnl_brl={s['mean_pnl_net_brl']:.2f} "
                        f"exp={s['mean_expectancy_brl_per_trade']:.2f} trades={s['total_trades']} "
                        f"score={entry['score']:.2f}"
                    )

    entries.sort(key=lambda x: float(x["score"]), reverse=True)
    champion = entries[0] if entries else None
    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "search_config": {
            "candidates": cfg.candidates,
            "n_folds": cfg.n_folds,
            "min_trades": cfg.min_trades,
            "min_positive_folds": cfg.min_positive_folds,
            "seed": cfg.seed,
            "workers": cfg.workers,
        },
        "entries": entries,
        "champion": champion,
    }
    (output_dir / "leaderboard.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    if champion:
        Path("artifacts/champion_strategy_xgb_clean.json").write_text(
            json.dumps(champion, indent=2), encoding="utf-8"
        )
    return payload
