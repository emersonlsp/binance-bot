from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from random import Random
from bisect import bisect_right
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ..runtime.params import load_trading_params
from .paper_aligned import build_paper_aligned_dataset, run_paper_aligned_training_from_dataset


@dataclass(slots=True)
class SearchConfig:
    candidates: int = 24
    n_folds: int = 4
    min_trades: int = 40
    min_positive_folds: int = 3
    seed: int = 42
    workers: int = 4
    regime_gate: bool = False
    regime_chop_min_confidence: float = 0.78
    xgb_device: str = "cpu"
    embargo_gap_steps: int = 0
    entry_latency_steps: int = 0
    allow_partial_fill: bool = True
    min_fill_ratio: float = 0.1
    detailed_report_top_n: int = 1


def _dir_fingerprint(root: Path) -> dict[str, int]:
    files = sorted(root.rglob("*.parquet"))
    count = 0
    size_sum = 0
    mtime_max = 0
    for f in files:
        st = f.stat()
        count += 1
        size_sum += int(st.st_size)
        mtime_max = max(mtime_max, int(st.st_mtime_ns))
    return {"files": count, "size_sum": size_sum, "mtime_max_ns": mtime_max}


def _raw_data_fingerprint(raw_root: Path) -> dict[str, dict[str, int]]:
    return {
        "updates": _dir_fingerprint(raw_root / "updates"),
        "collector_logs": _dir_fingerprint(raw_root / "collector_logs"),
    }


def _champion_sort_key(entry: dict[str, Any]) -> tuple[float, float, float]:
    summary = entry.get("summary", {})
    score = float(entry.get("score", summary.get("mean_pnl_net_brl", 0.0)))
    pnl = float(summary.get("mean_pnl_net_brl", 0.0))
    trades = float(summary.get("total_trades", 0.0))
    return (score, pnl, trades)


def _archive_and_promote_if_better(
    candidate: dict[str, Any],
    *,
    current_reference: dict[str, Any] | None,
    champion_file: Path,
    archive_dir: Path,
) -> tuple[bool, str]:
    current: dict[str, Any] | None = None
    if champion_file.exists():
        current = json.loads(champion_file.read_text(encoding="utf-8"))
    reference = current_reference if current_reference is not None else current
    if reference is not None and _champion_sort_key(candidate) <= _champion_sort_key(reference):
        msg = (
            "[xgb_clean] champion_action: kept_current_champion "
            "(promoted candidate did not beat active champion)"
        )
        print(msg)
        return False, msg
    archived_path = ""
    if current is not None:
        archive_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        current_name = str(current.get("name", "champion"))
        archive_path = archive_dir / f"{ts}_{current_name}.json"
        archive_path.write_text(json.dumps(current, indent=2), encoding="utf-8")
        archived_path = str(archive_path)
        print(f"[xgb_clean] archived current champion: {archive_path}")
    champion_file.parent.mkdir(parents=True, exist_ok=True)
    champion_file.write_text(json.dumps(candidate, indent=2), encoding="utf-8")
    print(f"[xgb_clean] active champion updated: {champion_file}")
    msg = (
        f"[xgb_clean] champion_action: promoted_new_champion + archived_old "
        f"(archive={archived_path if archived_path else 'none'})"
    )
    print(msg)
    return True, msg


def _evaluate_existing_champion_on_current_data(
    *,
    current: dict[str, Any],
    dataset_by_key: dict[tuple[int, float, int], dict[str, Any]],
    baseline_ref_by_key: dict[tuple[int, float, int], dict[str, Any]],
    output_dir: Path,
    cfg: SearchConfig,
) -> dict[str, Any] | None:
    params = dict(current.get("params", {}))
    try:
        h = int(params["horizon_steps"])
        m = float(params["move_threshold_bps"])
        s_upd = int(params["sample_every_updates"])
        n_folds = int(params.get("n_folds", cfg.n_folds))
        min_sig = float(params["min_signal_confidence"])
        max_spread = float(params["max_spread_brl"])
        cost = float(params.get("cost_bps_per_side", load_trading_params().risk.taker_fee_bps_per_side))
        strategy_kwargs = dict(params.get("strategy_kwargs", {}))
    except Exception:
        return None

    key = (h, m, s_upd)
    dataset = dataset_by_key.get(key)
    baseline_ref = baseline_ref_by_key.get(key)
    if dataset is None or baseline_ref is None:
        return None
    dataset_key = f"h{h}_m{str(m).replace('.', '_')}_s{s_upd}"
    candidate_like = {
        "name": str(current.get("name", "active_champion")),
        "strategy_name": str(current.get("strategy_name", "mlofi_xgb_v1")),
        "strategy_kwargs": strategy_kwargs,
        "horizon_steps": h,
        "move_threshold_bps": m,
        "sample_every_updates": s_upd,
        "cost_bps_per_side": cost,
        "min_signal_confidence": min_sig,
        "max_spread_brl": max_spread,
    }
    entry = _evaluate_candidate(
        candidate_like,
        search_seed=int(params.get("search_seed", cfg.seed)),
        output_dir=output_dir,
        dataset=dataset,
        dataset_key=dataset_key,
        sample_every_updates=s_upd,
        n_folds=n_folds,
        min_trades=cfg.min_trades,
        min_positive_folds=cfg.min_positive_folds,
        baseline_ref=baseline_ref,
        regime_gate_enabled=cfg.regime_gate,
        regime_chop_min_confidence=cfg.regime_chop_min_confidence,
        embargo_gap_steps=cfg.embargo_gap_steps,
        entry_latency_steps=cfg.entry_latency_steps,
        allow_partial_fill=cfg.allow_partial_fill,
        min_fill_ratio=cfg.min_fill_ratio,
    )
    return entry


def _sample_candidate(
    rng: Random,
    idx: int,
    *,
    xgb_n_jobs: int,
    xgb_device: str,
    data_profile: tuple[int, float, int],
) -> dict[str, Any]:
    horizon_steps, move_threshold_bps, sample_every_updates = data_profile
    min_signal_confidence = rng.choice([0.60, 0.65, 0.70, 0.75, 0.80, 0.85])
    max_spread_brl = rng.choice([1.5, 2.0, 2.5, 3.0, 4.0])
    seq_pick = rng.random()
    if seq_pick < 0.10:
        strategy_name = "mlofi_seq_gru_v1"
        strategy_kwargs = {
            "hidden_size": rng.choice([16, 24, 32]),
            "num_layers": rng.choice([1, 2]),
            "dropout": rng.choice([0.0, 0.1]),
            "epochs": rng.choice([6, 8, 10]),
            "batch_size": rng.choice([128, 256, 384]),
            "learning_rate": rng.choice([5.0e-4, 1.0e-3, 2.0e-3]),
            "weight_decay": rng.choice([1.0e-6, 1.0e-5, 1.0e-4]),
            "min_confidence": rng.choice([0.55, 0.60, 0.65, 0.70]),
            "random_state": 42,
            "seq_len": 8,
        }
    elif seq_pick < 0.20:
        strategy_name = "mlofi_seq_mlp_v1"
        strategy_kwargs = {
            "hidden_layer_sizes": rng.choice([(64, 32), (128, 64), (128, 32)]),
            "alpha": rng.choice([1.0e-4, 5.0e-4, 1.0e-3]),
            "learning_rate_init": rng.choice([5.0e-4, 1.0e-3, 2.0e-3]),
            "max_iter": rng.choice([150, 200, 300]),
            "min_confidence": rng.choice([0.55, 0.60, 0.65, 0.70]),
            "random_state": 42,
        }
    else:
        strategy_name = "mlofi_xgb_v1"
        strategy_kwargs = {
            "n_estimators": rng.choice([200, 250, 300, 400, 500]),
            "max_depth": rng.choice([3, 4, 5, 6]),
            "learning_rate": rng.choice([0.02, 0.03, 0.04, 0.05]),
            "subsample": rng.choice([0.8, 0.9, 1.0]),
            "colsample_bytree": rng.choice([0.8, 0.9, 1.0]),
            "reg_lambda": rng.choice([0.5, 1.0, 2.0, 5.0]),
            "min_confidence": rng.choice([0.55, 0.60, 0.65, 0.70]),
            "n_jobs": xgb_n_jobs,
            "xgb_device": xgb_device,
        }
    return {
        "name": f"xgb_clean_{idx:03d}",
        "strategy_name": strategy_name,
        "strategy_kwargs": strategy_kwargs,
        "horizon_steps": horizon_steps,
        "move_threshold_bps": move_threshold_bps,
        "sample_every_updates": sample_every_updates,
        "cost_bps_per_side": 0.0,
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


def _fold_stability(folds_brl: list[dict[str, Any]]) -> dict[str, float]:
    pnls = [float(x.get("pnl_net_brl", 0.0)) for x in folds_brl]
    total = len(pnls)
    if total == 0:
        return {"positive_folds": 0.0, "positive_ratio": 0.0, "worst_fold_pnl_brl": 0.0}
    positive = sum(1 for x in pnls if x > 0)
    return {
        "positive_folds": float(positive),
        "positive_ratio": float(positive / total),
        "worst_fold_pnl_brl": float(min(pnls)),
    }


def _evaluate_baselines(
    *,
    output_dir: Path,
    dataset: dict[str, Any],
    dataset_key: str,
    horizon_steps: int,
    sample_every_updates: int,
    n_folds: int,
    regime_gate_enabled: bool,
    regime_chop_min_confidence: float,
    embargo_gap_steps: int,
    entry_latency_steps: int,
    allow_partial_fill: bool,
    min_fill_ratio: float,
    cost_bps_per_side: float,
) -> list[dict[str, Any]]:
    specs = [
        ("baseline_no_trade_v1", {}, 0.0, 1.0e12),
        ("baseline_imbalance_v1", {"long_threshold": 0.08, "short_threshold": -0.08}, 0.0, 2.5),
    ]
    out: list[dict[str, Any]] = []
    for strategy_name, kwargs, min_conf, max_spread in specs:
        report = run_paper_aligned_training_from_dataset(
            dataset=dataset,
            report_output_path=output_dir / f"{dataset_key}_{strategy_name}.json",
            strategy_name=strategy_name,
            strategy_kwargs=kwargs,
            horizon_steps=horizon_steps,
            n_folds=n_folds,
            cost_bps_per_side=cost_bps_per_side,
            sample_every_updates=sample_every_updates,
            min_signal_confidence=min_conf,
            max_spread_brl=max_spread,
            regime_gate_enabled=regime_gate_enabled,
            regime_chop_min_confidence=regime_chop_min_confidence,
            embargo_gap_steps=embargo_gap_steps,
            entry_latency_steps=entry_latency_steps,
            allow_partial_fill=allow_partial_fill,
            min_fill_ratio=min_fill_ratio,
            include_trade_samples=False,
        )
        stability = _fold_stability(report.get("folds_brl", []))
        out.append(
            {
                "name": strategy_name,
                "summary": report["summary"],
                "folds_brl": report.get("folds_brl", []),
                "stability": stability,
            }
        )
    return out


def _best_baseline(baselines: list[dict[str, Any]]) -> dict[str, Any]:
    if not baselines:
        return {
            "name": "baseline_no_trade_v1",
            "summary": {"mean_pnl_net_brl": 0.0},
            "stability": {"positive_ratio": 0.0},
        }
    return sorted(
        baselines,
        key=lambda b: (
            float(b["summary"].get("mean_pnl_net_brl", 0.0)),
            float(b["stability"].get("positive_ratio", 0.0)),
        ),
        reverse=True,
    )[0]


def _evaluate_candidate(
    candidate: dict[str, Any],
    *,
    search_seed: int,
    output_dir: Path,
    dataset: dict[str, Any],
    dataset_key: str,
    sample_every_updates: int,
    n_folds: int,
    min_trades: int,
    min_positive_folds: int,
    baseline_ref: dict[str, Any],
    regime_gate_enabled: bool,
    regime_chop_min_confidence: float,
    embargo_gap_steps: int,
    entry_latency_steps: int,
    allow_partial_fill: bool,
    min_fill_ratio: float,
    include_trade_samples: bool = True,
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
        regime_gate_enabled=regime_gate_enabled,
        regime_chop_min_confidence=regime_chop_min_confidence,
        embargo_gap_steps=embargo_gap_steps,
        entry_latency_steps=entry_latency_steps,
        allow_partial_fill=allow_partial_fill,
        min_fill_ratio=min_fill_ratio,
        include_trade_samples=include_trade_samples,
    )
    entry = {
        "name": candidate["name"],
        "params": {
            "search_seed": search_seed,
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
    stability = _fold_stability(entry["folds_brl"])
    base_pnl = float(baseline_ref["summary"].get("mean_pnl_net_brl", 0.0))
    base_stability = float(baseline_ref["stability"].get("positive_ratio", 0.0))
    cand_pnl = float(entry["summary"].get("mean_pnl_net_brl", 0.0))
    cand_stability = float(stability.get("positive_ratio", 0.0))
    gate = {
        "baseline_ref": baseline_ref["name"],
        "baseline_pnl_brl": base_pnl,
        "baseline_positive_ratio": base_stability,
        "candidate_positive_ratio": cand_stability,
        "pass_pnl_vs_baseline": cand_pnl > base_pnl,
        "pass_stability_vs_baseline": cand_stability >= base_stability,
    }
    gate["promote"] = bool(gate["pass_pnl_vs_baseline"] and gate["pass_stability_vs_baseline"])
    entry["stability"] = stability
    entry["gate"] = gate
    entry["score"] = _score(
        entry,
        min_trades=min_trades,
        min_positive_folds=min_positive_folds,
    )
    return entry


def _evaluate_group(
    *,
    candidates: list[dict[str, Any]],
    search_seed: int,
    output_dir: Path,
    dataset: dict[str, Any],
    dataset_key: str,
    sample_every_updates: int,
    n_folds: int,
    min_trades: int,
    min_positive_folds: int,
    baseline_ref: dict[str, Any],
    regime_gate_enabled: bool,
    regime_chop_min_confidence: float,
    embargo_gap_steps: int,
    entry_latency_steps: int,
    allow_partial_fill: bool,
    min_fill_ratio: float,
    include_trade_samples: bool = True,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for candidate in candidates:
        out.append(
            _evaluate_candidate(
                candidate,
                search_seed=search_seed,
                output_dir=output_dir,
                dataset=dataset,
                dataset_key=dataset_key,
                sample_every_updates=sample_every_updates,
                n_folds=n_folds,
                min_trades=min_trades,
                min_positive_folds=min_positive_folds,
                baseline_ref=baseline_ref,
                regime_gate_enabled=regime_gate_enabled,
                regime_chop_min_confidence=regime_chop_min_confidence,
                embargo_gap_steps=embargo_gap_steps,
                entry_latency_steps=entry_latency_steps,
                allow_partial_fill=allow_partial_fill,
                min_fill_ratio=min_fill_ratio,
                include_trade_samples=include_trade_samples,
            )
        )
    return out


def _chunked(items: list[dict[str, Any]], chunk_size: int) -> list[list[dict[str, Any]]]:
    if chunk_size <= 0:
        return [items]
    return [items[i : i + chunk_size] for i in range(0, len(items), chunk_size)]


def _fmt_eta(seconds: float) -> str:
    secs = max(0.0, float(seconds))
    if secs >= 3600.0:
        return f"{(secs / 3600.0):.2f}h"
    return f"{(secs / 60.0):.1f}m"


def _parse_iso_utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)


def _load_regime_series() -> tuple[list[datetime], list[str]]:
    root = Path("data/processed/mt5/BTCUSD/regime")
    files = sorted(root.glob("regime_features_*.parquet"))
    if not files:
        return [], []
    rows = pq.read_table(files[-1]).to_pylist()
    pairs: list[tuple[datetime, str]] = []
    for r in rows:
        ts = str(r.get("ts_open", ""))
        if not ts:
            continue
        try:
            dt = _parse_iso_utc(ts)
        except ValueError:
            continue
        pairs.append((dt, str(r.get("regime_label", "unknown"))))
    pairs.sort(key=lambda x: x[0])
    if not pairs:
        return [], []
    times = [p[0] for p in pairs]
    labels = [p[1] for p in pairs]
    return times, labels


def _attach_regime_labels(
    dataset: dict[str, Any],
    regime_times: list[datetime],
    regime_labels: list[str],
) -> None:
    if not regime_times:
        return
    labeled_rows = dataset.get("labeled_rows", [])
    first_label = regime_labels[0]
    for row in labeled_rows:
        ts = str(row.get("ts_receive", ""))
        try:
            event_dt = _parse_iso_utc(ts)
        except ValueError:
            row["regime_label"] = "unknown"
            continue
        idx = bisect_right(regime_times, event_dt) - 1
        if idx < 0:
            # Event happened before first available MT5 regime candle.
            row["regime_label"] = first_label
        else:
            row["regime_label"] = regime_labels[idx]


def run_xgb_clean_search(
    *,
    raw_root: Path,
    output_dir: Path,
    cfg: SearchConfig,
) -> dict[str, Any]:
    os.environ.setdefault("PYTHONOPTIMIZE", "1")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    t0 = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = Path("data/features/binance/BTCBRL/xgb_clean_cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    raw_fp = _raw_data_fingerprint(raw_root)
    trading_params = load_trading_params()
    fee_bps_per_side = float(trading_params.risk.taker_fee_bps_per_side)
    rng = Random(cfg.seed)
    regime_times, regime_labels = _load_regime_series() if cfg.regime_gate else ([], [])
    if cfg.regime_gate:
        print(f"[xgb_clean] regime gate enabled. regime_points={len(regime_times)}")

    cpu_total = max(1, (os.cpu_count() or 4))
    xgb_n_jobs = 1 if cfg.workers > 1 else min(4, cpu_total)
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
            xgb_device=cfg.xgb_device,
            data_profile=rng.choice(data_profiles),
        )
        for i in range(1, cfg.candidates + 1)
    ]
    for c in candidates:
        c["cost_bps_per_side"] = fee_bps_per_side
    entries: list[dict[str, Any]] = []
    total_candidates = len(candidates)
    completed_candidates = 0
    t_candidates_started = time.perf_counter()
    benchmark_chunks_done = 0
    t_last_checkpoint = t_candidates_started
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
    baseline_by_key: dict[tuple[int, float, int], list[dict[str, Any]]] = {}
    baseline_ref_by_key: dict[tuple[int, float, int], dict[str, Any]] = {}
    cache_hit_count = 0
    cache_build_count = 0
    for key, _group in groups.items():
        h, m, s = key
        key_name = f"h{h}_m{str(m).replace('.', '_')}_s{s}"
        feature_cache_path = cache_dir / f"{key_name}_features.parquet"
        label_cache_path = cache_dir / f"{key_name}_labeled.parquet"
        meta_cache_path = cache_dir / f"{key_name}_meta.json"
        cache_valid = False
        if feature_cache_path.exists() and label_cache_path.exists() and meta_cache_path.exists():
            try:
                meta = json.loads(meta_cache_path.read_text(encoding="utf-8"))
                cache_valid = meta.get("raw_fingerprint") == raw_fp
            except Exception:
                cache_valid = False
        if cache_valid:
            feature_rows = pq.read_table(feature_cache_path).to_pylist()
            labeled_rows = pq.read_table(label_cache_path).to_pylist()
            dataset_by_key[key] = {
                "update_rows_count": 0,
                "bad_minutes_count": 0,
                "feature_rows": feature_rows,
                "labeled_rows": labeled_rows,
            }
            if cfg.regime_gate:
                _attach_regime_labels(dataset_by_key[key], regime_times, regime_labels)
            print(f"[xgb_clean] cache hit: {key_name} rows={len(labeled_rows)}")
            cache_hit_count += 1
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
        meta_cache_path.write_text(
            json.dumps(
                {
                    "generated_at_utc": datetime.now(UTC).isoformat(),
                    "dataset_key": key_name,
                    "raw_fingerprint": raw_fp,
                    "rows_features": len(dataset["feature_rows"]),
                    "rows_labeled": len(dataset["labeled_rows"]),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        dataset_by_key[key] = dataset
        if cfg.regime_gate:
            _attach_regime_labels(dataset_by_key[key], regime_times, regime_labels)
        print(f"[xgb_clean] cache build: {key_name} rows={len(dataset['labeled_rows'])}")
        cache_build_count += 1
        baselines = _evaluate_baselines(
            output_dir=output_dir,
            dataset=dataset_by_key[key],
            dataset_key=key_name,
            horizon_steps=h,
            sample_every_updates=s,
            n_folds=cfg.n_folds,
            regime_gate_enabled=cfg.regime_gate,
            regime_chop_min_confidence=cfg.regime_chop_min_confidence,
            embargo_gap_steps=cfg.embargo_gap_steps,
            entry_latency_steps=cfg.entry_latency_steps,
            allow_partial_fill=cfg.allow_partial_fill,
            min_fill_ratio=cfg.min_fill_ratio,
            cost_bps_per_side=fee_bps_per_side,
        )
        baseline_by_key[key] = baselines
        baseline_ref_by_key[key] = _best_baseline(baselines)
        ref = baseline_ref_by_key[key]
        print(
            f"[xgb_clean] baseline_ref {key_name}: {ref['name']} "
            f"pnl_brl={float(ref['summary'].get('mean_pnl_net_brl', 0.0)):.2f} "
            f"stability={float(ref['stability'].get('positive_ratio', 0.0)):.2f}"
        )
    t_after_cache = time.perf_counter()

    for key, ds in dataset_by_key.items():
        if key in baseline_by_key:
            continue
        h, m, s = key
        key_name = f"h{h}_m{str(m).replace('.', '_')}_s{s}"
        baselines = _evaluate_baselines(
            output_dir=output_dir,
            dataset=ds,
            dataset_key=key_name,
            horizon_steps=h,
            sample_every_updates=s,
            n_folds=cfg.n_folds,
            regime_gate_enabled=cfg.regime_gate,
            regime_chop_min_confidence=cfg.regime_chop_min_confidence,
            embargo_gap_steps=cfg.embargo_gap_steps,
            entry_latency_steps=cfg.entry_latency_steps,
            allow_partial_fill=cfg.allow_partial_fill,
            min_fill_ratio=cfg.min_fill_ratio,
            cost_bps_per_side=fee_bps_per_side,
        )
        baseline_by_key[key] = baselines
        baseline_ref_by_key[key] = _best_baseline(baselines)
        ref = baseline_ref_by_key[key]
        print(
            f"[xgb_clean] baseline_ref {key_name}: {ref['name']} "
            f"pnl_brl={float(ref['summary'].get('mean_pnl_net_brl', 0.0)):.2f} "
            f"stability={float(ref['stability'].get('positive_ratio', 0.0)):.2f}"
        )

    if cfg.workers <= 1:
        for key, group in groups.items():
            h, m, s_upd = key
            key_name = f"h{h}_m{str(m).replace('.', '_')}_s{s_upd}"
            ds = dataset_by_key[key]
            baseline_ref = baseline_ref_by_key[key]
            for candidate in group:
                entry = _evaluate_candidate(
                    candidate,
                    search_seed=cfg.seed,
                    output_dir=output_dir,
                    dataset=ds,
                    dataset_key=key_name,
                    sample_every_updates=s_upd,
                    n_folds=cfg.n_folds,
                    min_trades=cfg.min_trades,
                    min_positive_folds=cfg.min_positive_folds,
                    baseline_ref=baseline_ref,
                    regime_gate_enabled=cfg.regime_gate,
                    regime_chop_min_confidence=cfg.regime_chop_min_confidence,
                    embargo_gap_steps=cfg.embargo_gap_steps,
                    entry_latency_steps=cfg.entry_latency_steps,
                    allow_partial_fill=cfg.allow_partial_fill,
                    min_fill_ratio=cfg.min_fill_ratio,
                    include_trade_samples=False,
                )
                entries.append(entry)
                s = entry["summary"]
                print(
                    f"[xgb_clean] {candidate['name']} pnl_brl={s['mean_pnl_net_brl']:.2f} "
                    f"exp={s['mean_expectancy_brl_per_trade']:.2f} trades={s['total_trades']} "
                    f"score={entry['score']:.2f}"
                )
                completed_candidates += 1
                print(f"[xgb_clean] progress {completed_candidates}/{total_candidates}", flush=True)
                if completed_candidates % 6 == 0:
                    now = time.perf_counter()
                    chunk_secs = now - t_last_checkpoint
                    remaining = max(0, total_candidates - completed_candidates)
                    benchmark_chunks_done += 1
                    if benchmark_chunks_done >= 2:
                        eta_secs = chunk_secs * (remaining / 6.0)
                        print(
                            f"[xgb_clean] benchmark chunk=6 done={completed_candidates}/{total_candidates} "
                            f"last6={chunk_secs / 60.0:.1f}m eta={_fmt_eta(eta_secs)}",
                            flush=True,
                        )
                    t_last_checkpoint = now
    else:
        with ProcessPoolExecutor(max_workers=cfg.workers) as ex:
            futures = {}
            for key, group in groups.items():
                h, m, s_upd = key
                key_name = f"h{h}_m{str(m).replace('.', '_')}_s{s_upd}"
                ds = dataset_by_key[key]
                baseline_ref = baseline_ref_by_key[key]
                chunk_size = max(1, min(4, len(group) // max(1, cfg.workers)))
                for candidate_chunk in _chunked(group, chunk_size):
                    fut = ex.submit(
                        _evaluate_group,
                        candidates=candidate_chunk,
                        search_seed=cfg.seed,
                        output_dir=output_dir,
                        dataset=ds,
                        dataset_key=key_name,
                        sample_every_updates=s_upd,
                        n_folds=cfg.n_folds,
                        min_trades=cfg.min_trades,
                        min_positive_folds=cfg.min_positive_folds,
                        baseline_ref=baseline_ref,
                        regime_gate_enabled=cfg.regime_gate,
                        regime_chop_min_confidence=cfg.regime_chop_min_confidence,
                        embargo_gap_steps=cfg.embargo_gap_steps,
                        entry_latency_steps=cfg.entry_latency_steps,
                        allow_partial_fill=cfg.allow_partial_fill,
                        min_fill_ratio=cfg.min_fill_ratio,
                        include_trade_samples=False,
                    )
                    futures[fut] = len(candidate_chunk)
            for fut in as_completed(futures):
                chunk_entries = fut.result()
                for entry in chunk_entries:
                    entries.append(entry)
                    s = entry["summary"]
                    print(
                        f"[xgb_clean] {entry['name']} pnl_brl={s['mean_pnl_net_brl']:.2f} "
                        f"exp={s['mean_expectancy_brl_per_trade']:.2f} trades={s['total_trades']} "
                        f"score={entry['score']:.2f}"
                    )
                    completed_candidates += 1
                    print(f"[xgb_clean] progress {completed_candidates}/{total_candidates}", flush=True)
                    if completed_candidates % 6 == 0:
                        now = time.perf_counter()
                        chunk_secs = now - t_last_checkpoint
                        remaining = max(0, total_candidates - completed_candidates)
                        benchmark_chunks_done += 1
                        if benchmark_chunks_done >= 2:
                            eta_secs = chunk_secs * (remaining / 6.0)
                            print(
                                f"[xgb_clean] benchmark chunk=6 done={completed_candidates}/{total_candidates} "
                                f"last6={chunk_secs / 60.0:.1f}m eta={_fmt_eta(eta_secs)}",
                                flush=True,
                            )
                        t_last_checkpoint = now

    entries.sort(key=lambda x: float(x["score"]), reverse=True)
    champion = entries[0] if entries else None
    promoted = [e for e in entries if bool(e.get("gate", {}).get("promote", False))]
    promoted_champion = promoted[0] if promoted else None
    winners = [e for e in entries if float(e["summary"].get("mean_pnl_net_brl", 0.0)) > 0.0]
    winners.sort(key=lambda x: float(x["summary"]["mean_pnl_net_brl"]), reverse=True)
    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "search_config": {
            "candidates": cfg.candidates,
            "n_folds": cfg.n_folds,
            "min_trades": cfg.min_trades,
            "min_positive_folds": cfg.min_positive_folds,
            "seed": cfg.seed,
            "workers": cfg.workers,
            "regime_gate": cfg.regime_gate,
            "regime_chop_min_confidence": cfg.regime_chop_min_confidence,
            "embargo_gap_steps": cfg.embargo_gap_steps,
            "entry_latency_steps": cfg.entry_latency_steps,
            "allow_partial_fill": cfg.allow_partial_fill,
            "min_fill_ratio": cfg.min_fill_ratio,
        },
        "entries": entries,
        "champion": champion,
        "promoted_count": len(promoted),
        "promoted_champion": promoted_champion,
        "winners_count": len(winners),
        "baselines_by_dataset": {
            f"h{k[0]}_m{str(k[1]).replace('.', '_')}_s{k[2]}": v for k, v in baseline_by_key.items()
        },
        "champion_action": "none",
        "timing": {
            "cache_phase_seconds": round(t_after_cache - t0, 3),
            "candidate_phase_seconds": 0.0,
            "total_seconds": 0.0,
            "postprocess_seconds": 0.0,
            "wall_total_seconds": 0.0,
            "cache_hit_count": cache_hit_count,
            "cache_build_count": cache_build_count,
            "dataset_groups_count": len(groups),
            "candidates_count": len(candidates),
        },
    }
    (output_dir / "leaderboard.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    winners_payload = {
        "generated_at_utc": payload["generated_at_utc"],
        "filter": "mean_pnl_net_brl > 0",
        "count": len(winners),
        "entries": winners,
        "best": winners[0] if winners else None,
    }
    (output_dir / "winners_latest.json").write_text(
        json.dumps(winners_payload, indent=2), encoding="utf-8"
    )

    equity_payload: dict[str, Any] = {
        "generated_at_utc": payload["generated_at_utc"],
        "source_output_dir": str(output_dir),
        "selected": None,
        "summary": {},
        "fold_equity": [],
        "fold_trade_samples": [],
    }
    t_core_end = time.perf_counter()
    payload["timing"]["candidate_phase_seconds"] = round(t_core_end - t_after_cache, 3)
    payload["timing"]["total_seconds"] = round(t_core_end - t0, 3)

    selected = promoted_champion or champion
    if selected and int(cfg.detailed_report_top_n) > 0:
        sparams = dict(selected.get("params", {}))
        selected_key = (
            int(sparams.get("horizon_steps", 80)),
            float(sparams.get("move_threshold_bps", 0.8)),
            int(sparams.get("sample_every_updates", 40)),
        )
        ds = dataset_by_key.get(selected_key)
        baseline_ref = baseline_ref_by_key.get(selected_key)
        if ds is not None and baseline_ref is not None:
            selected = _evaluate_candidate(
                {
                    "name": selected["name"],
                    "strategy_name": selected.get("strategy_name", "mlofi_xgb_v1"),
                    "strategy_kwargs": dict(sparams.get("strategy_kwargs", {})),
                    "horizon_steps": int(sparams.get("horizon_steps", 80)),
                    "move_threshold_bps": float(sparams.get("move_threshold_bps", 0.8)),
                    "sample_every_updates": int(sparams.get("sample_every_updates", 40)),
                    "cost_bps_per_side": float(sparams.get("cost_bps_per_side", fee_bps_per_side)),
                    "min_signal_confidence": float(sparams.get("min_signal_confidence", 0.75)),
                    "max_spread_brl": float(sparams.get("max_spread_brl", 3.0)),
                },
                search_seed=cfg.seed,
                output_dir=output_dir,
                dataset=ds,
                dataset_key=str(sparams.get("dataset_key", "selected")),
                sample_every_updates=int(sparams.get("sample_every_updates", 40)),
                n_folds=int(sparams.get("n_folds", cfg.n_folds)),
                min_trades=cfg.min_trades,
                min_positive_folds=cfg.min_positive_folds,
                baseline_ref=baseline_ref,
                regime_gate_enabled=cfg.regime_gate,
                regime_chop_min_confidence=cfg.regime_chop_min_confidence,
                embargo_gap_steps=cfg.embargo_gap_steps,
                entry_latency_steps=cfg.entry_latency_steps,
                allow_partial_fill=cfg.allow_partial_fill,
                min_fill_ratio=cfg.min_fill_ratio,
                include_trade_samples=True,
            )
            candidate_report = json.loads((output_dir / f"{selected['name']}.json").read_text(encoding="utf-8"))
            equity_payload["selected"] = {
                "name": selected["name"],
                "promoted": bool(
                    promoted_champion is not None
                    and str(selected.get("name", "")) == str(promoted_champion.get("name", ""))
                ),
                "params": selected.get("params", {}),
            }
            equity_payload["summary"] = candidate_report.get("summary", {})
            equity_payload["fold_equity"] = candidate_report.get("fold_equity", [])
            equity_payload["fold_trade_samples"] = candidate_report.get("fold_trade_samples", [])
    (output_dir / "equity_curve_latest.json").write_text(
        json.dumps(equity_payload, indent=2), encoding="utf-8"
    )

    if promoted_champion:
        current_ref: dict[str, Any] | None = None
        champion_file = Path("artifacts/champion_strategy_xgb_clean.json")
        if champion_file.exists():
            try:
                current_payload = json.loads(champion_file.read_text(encoding="utf-8"))
                current_ref = _evaluate_existing_champion_on_current_data(
                    current=current_payload,
                    dataset_by_key=dataset_by_key,
                    baseline_ref_by_key=baseline_ref_by_key,
                    output_dir=output_dir,
                    cfg=cfg,
                )
                if current_ref is not None:
                    cs = current_ref.get("summary", {})
                    print(
                        "[xgb_clean] current_active_reval:",
                        current_ref.get("name", "active_champion"),
                        "pnl_brl=",
                        round(float(cs.get("mean_pnl_net_brl", 0.0)), 2),
                        "trades=",
                        int(cs.get("total_trades", 0)),
                    )
            except Exception:
                current_ref = None
        _, action_msg = _archive_and_promote_if_better(
            promoted_champion,
            current_reference=current_ref,
            champion_file=champion_file,
            archive_dir=Path("artifacts/champions_archive"),
        )
        payload["champion_action"] = action_msg
    else:
        payload["champion_action"] = "[xgb_clean] champion_action: kept_current_champion (no promoted candidate)"
        print(payload["champion_action"])
    t_end = time.perf_counter()
    payload["timing"]["postprocess_seconds"] = round(t_end - t_core_end, 3)
    payload["timing"]["wall_total_seconds"] = round(t_end - t0, 3)
    (output_dir / "leaderboard.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(
        f"[xgb_clean] timing cache={payload['timing']['cache_phase_seconds']:.1f}s "
        f"candidates={payload['timing']['candidate_phase_seconds']:.1f}s "
        f"total={payload['timing']['total_seconds']:.1f}s "
        f"cache_hit={cache_hit_count} cache_build={cache_build_count}",
        flush=True,
    )
    return payload
