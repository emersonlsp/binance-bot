from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ..paper.config import load_paper_mode_config
from ..runtime.params import load_trading_params
from ..strategies.registry import create_strategy

QUALITY_EVENTS = {"quality_update_gap", "quality_snapshot_gap", "quality_sequence_gap"}


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)


def _minute_bucket(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M")


def _rel_day_key(root: Path, file_path: Path) -> str:
    rel_parts = file_path.relative_to(root).parts
    if len(rel_parts) >= 4:
        return "/".join(rel_parts[:3])  # YYYY/MM/DD
    return "unknown"


def _file_sig(path: Path) -> dict[str, int]:
    st = path.stat()
    return {"size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}


def _read_parquet_rows_batched(path: Path, columns: list[str], batch_size: int = 50_000) -> list[dict[str, Any]]:
    pf = pq.ParquetFile(path)
    out: list[dict[str, Any]] = []
    for batch in pf.iter_batches(batch_size=batch_size, columns=columns):
        out.extend(pa.Table.from_batches([batch]).to_pylist())
    return out


def _read_day_rows_parallel(day_files: list[Path], columns: list[str], workers: int) -> list[dict[str, Any]]:
    if not day_files:
        return []
    if workers <= 1 or len(day_files) == 1:
        out: list[dict[str, Any]] = []
        for f in day_files:
            out.extend(_read_parquet_rows_batched(f, columns))
        return out
    out: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_read_parquet_rows_batched, f, columns) for f in day_files]
        for fut in futures:
            out.extend(fut.result())
    return out


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _read_all_rows_incremental_cached(
    *,
    root: Path,
    cache_root: Path,
    stream_name: str,
    columns: list[str],
    workers: int,
) -> list[dict[str, Any]]:
    files = sorted(root.rglob("*.parquet"))
    cache_root.mkdir(parents=True, exist_ok=True)
    stream_cache = cache_root / stream_name
    stream_cache.mkdir(parents=True, exist_ok=True)
    manifest_path = stream_cache / "manifest.json"

    prev = _load_json(manifest_path, default={"files": {}, "days": {}})
    prev_files: dict[str, dict[str, int]] = dict(prev.get("files", {}))
    prev_days: dict[str, list[str]] = {k: list(v) for k, v in dict(prev.get("days", {})).items()}

    curr_files: dict[str, dict[str, int]] = {}
    curr_days: dict[str, list[str]] = {}
    days_to_rebuild: set[str] = set()

    for f in files:
        rel = f.relative_to(root).as_posix()
        day = _rel_day_key(root, f)
        sig = _file_sig(f)
        curr_files[rel] = sig
        curr_days.setdefault(day, []).append(rel)
        if prev_files.get(rel) != sig:
            days_to_rebuild.add(day)

    removed = set(prev_files.keys()) - set(curr_files.keys())
    for rel in removed:
        old_day = None
        for d, rels in prev_days.items():
            if rel in rels:
                old_day = d
                break
        if old_day is not None:
            days_to_rebuild.add(old_day)

    # Rebuild only changed day partitions.
    for day in sorted(days_to_rebuild):
        rels = curr_days.get(day, [])
        day_files = [root / rel for rel in rels]
        rows = _read_day_rows_parallel(day_files, columns, workers=workers)
        day_dir = stream_cache / day
        day_dir.mkdir(parents=True, exist_ok=True)
        day_file = day_dir / "rows.parquet"
        if not rows:
            if day_file.exists():
                day_file.unlink()
            continue
        table = pa.Table.from_pylist(rows)
        pq.write_table(table, day_file)

    # Remove stale day caches no longer present.
    curr_day_keys = set(curr_days.keys())
    for d in prev_days.keys():
        if d not in curr_day_keys:
            stale_dir = stream_cache / d
            if stale_dir.exists():
                for p in stale_dir.rglob("*"):
                    if p.is_file():
                        p.unlink()
                for p in sorted(stale_dir.rglob("*"), reverse=True):
                    if p.is_dir():
                        p.rmdir()
                if stale_dir.exists():
                    stale_dir.rmdir()

    # Persist new manifest.
    manifest = {"files": curr_files, "days": curr_days}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # Read merged rows from all day partitions.
    rows_out: list[dict[str, Any]] = []
    for day in sorted(curr_days.keys()):
        day_file = stream_cache / day / "rows.parquet"
        if not day_file.exists():
            continue
        rows_out.extend(_read_parquet_rows_batched(day_file, columns))
    return rows_out


def _collect_bad_minutes(log_rows: list[dict[str, Any]]) -> set[str]:
    bad_minutes: set[str] = set()
    for row in log_rows:
        if str(row.get("event_type", "")) in QUALITY_EVENTS:
            ts = row.get("ts_event")
            if isinstance(ts, str):
                bad_minutes.add(_minute_bucket(_parse_iso(ts)))
    return bad_minutes


def _top_levels(book: dict[float, float], side: str, n: int) -> list[tuple[float, float]]:
    prices = sorted(book.keys(), reverse=(side == "bid"))
    out: list[tuple[float, float]] = []
    for p in prices[:n]:
        out.append((p, book[p]))
    return out


def _build_event_time_features(
    update_rows: list[dict[str, Any]],
    bad_minutes: set[str],
    levels: int = 10,
    sample_every_updates: int = 20,
) -> list[dict[str, Any]]:
    rows_sorted = sorted(update_rows, key=lambda r: str(r.get("ts_receive")))
    bid_book: dict[float, float] = {}
    ask_book: dict[float, float] = {}
    out_rows: list[dict[str, Any]] = []
    step = 0
    active_session: str | None = None

    for row in rows_sorted:
        session_id = str(row.get("session_id", "unknown"))
        if active_session is None:
            active_session = session_id
        elif session_id != active_session:
            # Session boundary: reset reconstructed book state and step counter.
            bid_book.clear()
            ask_book.clear()
            step = 0
            active_session = session_id
        side = str(row.get("side", ""))
        price = row.get("price")
        size = row.get("size")
        ts_receive = row.get("ts_receive")
        if not isinstance(ts_receive, str) or not isinstance(price, (int, float)) or not isinstance(
            size, (int, float)
        ):
            continue
        minute = _minute_bucket(_parse_iso(ts_receive))
        if minute in bad_minutes:
            continue
        price_f = float(price)
        size_f = float(size)
        if side == "bid":
            if size_f <= 0:
                bid_book.pop(price_f, None)
            else:
                bid_book[price_f] = size_f
        elif side == "ask":
            if size_f <= 0:
                ask_book.pop(price_f, None)
            else:
                ask_book[price_f] = size_f
        else:
            continue

        step += 1
        if step % sample_every_updates != 0:
            continue

        top_bids = _top_levels(bid_book, "bid", levels)
        top_asks = _top_levels(ask_book, "ask", levels)
        if not top_bids or not top_asks:
            continue
        best_bid = top_bids[0][0]
        best_ask = top_asks[0][0]
        best_bid_size = float(top_bids[0][1])
        best_ask_size = float(top_asks[0][1])
        spread = best_ask - best_bid
        if spread < 0:
            continue
        mid = (best_bid + best_ask) / 2
        out = {
            "ts_receive": ts_receive,
            "symbol": row.get("symbol"),
            "session_id": session_id,
            "mid_price": mid,
            "spread": spread,
            "bid_price_l1": best_bid,
            "ask_price_l1": best_ask,
            "bid_size_l1": best_bid_size,
            "ask_size_l1": best_ask_size,
            "quality_ok": True,
        }
        for i in range(levels):
            bid_price = float(top_bids[i][0]) if i < len(top_bids) else 0.0
            bid_size = float(top_bids[i][1]) if i < len(top_bids) else 0.0
            ask_price = float(top_asks[i][0]) if i < len(top_asks) else 0.0
            ask_size = float(top_asks[i][1]) if i < len(top_asks) else 0.0
            out[f"bid_price_l{i+1}"] = bid_price
            out[f"bid_size_l{i+1}"] = bid_size
            out[f"ask_price_l{i+1}"] = ask_price
            out[f"ask_size_l{i+1}"] = ask_size
        mvals: list[float] = []
        for i in range(levels):
            bid_size = top_bids[i][1] if i < len(top_bids) else 0.0
            ask_size = top_asks[i][1] if i < len(top_asks) else 0.0
            denom = bid_size + ask_size
            imbalance = 0.0 if denom <= 0 else (bid_size - ask_size) / denom
            out[f"mlofi_l{i+1}"] = imbalance
            mvals.append(imbalance)
        out["mlofi_score"] = sum(mvals) / len(mvals)
        out_rows.append(out)
    return out_rows


def _attach_targets_no_leakage(
    rows: list[dict[str, Any]], horizon_steps: int, move_threshold_bps: float
) -> list[dict[str, Any]]:
    threshold = move_threshold_bps / 10000.0
    out: list[dict[str, Any]] = []
    for i in range(0, len(rows) - horizon_steps):
        now = rows[i]
        fut = rows[i + horizon_steps]
        if str(now.get("session_id")) != str(fut.get("session_id")):
            continue
        mid_now = float(now["mid_price"])
        mid_fut = float(fut["mid_price"])
        ret = (mid_fut - mid_now) / mid_now if mid_now > 0 else 0.0
        target = 1 if ret > threshold else -1 if ret < -threshold else 0
        r = dict(now)
        r["future_mid_price"] = mid_fut
        r["target_ret_h"] = ret
        r["target_direction"] = target
        r["row_idx"] = i
        out.append(r)
    return out


@dataclass(slots=True)
class FoldResult:
    fold_id: int
    train_rows: int
    test_rows: int
    accuracy: float
    pnl_net: float
    trades: int


def _walk_forward_splits(n: int, n_folds: int) -> list[tuple[int, int, int]]:
    min_train = max(300, n // 3)
    test_size = max(100, (n - min_train) // max(1, n_folds))
    splits: list[tuple[int, int, int]] = []
    train_end = min_train
    for _ in range(n_folds):
        test_end = min(n, train_end + test_size)
        if test_end <= train_end:
            break
        splits.append((0, train_end, test_end))
        train_end = test_end
    return splits


def _accuracy(y_true: list[int], y_pred: list[int]) -> float:
    return (sum(1 for a, b in zip(y_true, y_pred) if a == b) / len(y_true)) if y_true else 0.0


def _simulate_trade_ret(
    *,
    all_rows: list[dict[str, Any]],
    row_idx: int,
    side: int,
    horizon_steps: int,
    stop_loss_pct: float,
    risk_reward_ratio: float,
    trailing_enabled: bool,
    trailing_activation_rr: float,
    trailing_distance_rr: float,
    trailing_lock_breakeven: bool,
    disable_take_profit_when_trailing: bool,
) -> float:
    now = all_rows[row_idx]
    entry = float(now["mid_price"])
    if entry <= 0:
        return 0.0
    session_id = str(now.get("session_id", ""))
    is_long = side == 1
    if is_long:
        stop = entry * (1.0 - stop_loss_pct)
        take_profit = entry * (1.0 + (stop_loss_pct * risk_reward_ratio))
    else:
        stop = entry * (1.0 + stop_loss_pct)
        take_profit = entry * (1.0 - (stop_loss_pct * risk_reward_ratio))
    initial_stop = stop
    best_price = entry
    trailing_active = False

    max_j = min(len(all_rows) - 1, row_idx + horizon_steps)
    exit_price = entry
    for j in range(row_idx + 1, max_j + 1):
        row = all_rows[j]
        if str(row.get("session_id", "")) != session_id:
            break
        px = float(row["mid_price"])
        exit_price = px

        if is_long:
            best_price = max(best_price, px)
            risk_per_unit = max(1.0e-12, entry - initial_stop)
            favorable_move = best_price - entry
        else:
            best_price = min(best_price, px)
            risk_per_unit = max(1.0e-12, initial_stop - entry)
            favorable_move = entry - best_price

        if (
            trailing_enabled
            and not trailing_active
            and favorable_move >= (trailing_activation_rr * risk_per_unit)
        ):
            trailing_active = True

        if trailing_enabled and trailing_active:
            if is_long:
                candidate_stop = best_price - (trailing_distance_rr * risk_per_unit)
                stop = max(stop, candidate_stop)
                if trailing_lock_breakeven:
                    stop = max(stop, entry)
            else:
                candidate_stop = best_price + (trailing_distance_rr * risk_per_unit)
                stop = min(stop, candidate_stop)
                if trailing_lock_breakeven:
                    stop = min(stop, entry)

        if is_long:
            if px <= stop:
                exit_price = stop
                break
            if (not trailing_active or not disable_take_profit_when_trailing) and px >= take_profit:
                exit_price = take_profit
                break
        else:
            if px >= stop:
                exit_price = stop
                break
            if (not trailing_active or not disable_take_profit_when_trailing) and px <= take_profit:
                exit_price = take_profit
                break

    if is_long:
        return (exit_price - entry) / entry
    return (entry - exit_price) / entry


def _pnl_net(
    rows: list[dict[str, Any]],
    preds: list[int],
    *,
    all_rows: list[dict[str, Any]],
    horizon_steps: int,
    cost_bps_side: float,
    stop_loss_pct: float,
    risk_reward_ratio: float,
    trailing_enabled: bool,
    trailing_activation_rr: float,
    trailing_distance_rr: float,
    trailing_lock_breakeven: bool,
    disable_take_profit_when_trailing: bool,
) -> tuple[float, int]:
    cost = 2.0 * (cost_bps_side / 10000.0)
    pnl = 0.0
    trades = 0
    for row, p in zip(rows, preds):
        if p == 0:
            continue
        row_idx = int(row.get("row_idx", -1))
        if row_idx < 0:
            continue
        trade_ret = _simulate_trade_ret(
            all_rows=all_rows,
            row_idx=row_idx,
            side=p,
            horizon_steps=horizon_steps,
            stop_loss_pct=stop_loss_pct,
            risk_reward_ratio=risk_reward_ratio,
            trailing_enabled=trailing_enabled,
            trailing_activation_rr=trailing_activation_rr,
            trailing_distance_rr=trailing_distance_rr,
            trailing_lock_breakeven=trailing_lock_breakeven,
            disable_take_profit_when_trailing=disable_take_profit_when_trailing,
        )
        pnl += trade_ret - cost
        trades += 1
    return pnl, trades


def _pnl_net_brl(
    rows: list[dict[str, Any]],
    preds: list[int],
    *,
    all_rows: list[dict[str, Any]],
    horizon_steps: int,
    cost_bps_side: float,
    bankroll_brl: float,
    risk_pct: float,
    stop_loss_pct: float,
    risk_reward_ratio: float,
    trailing_enabled: bool,
    trailing_activation_rr: float,
    trailing_distance_rr: float,
    trailing_lock_breakeven: bool,
    disable_take_profit_when_trailing: bool,
) -> tuple[float, int, float, float]:
    if bankroll_brl <= 0 or risk_pct <= 0 or stop_loss_pct <= 0:
        return 0.0, 0, 0.0, 0.0
    risk_amount = bankroll_brl * risk_pct
    position_notional = risk_amount / stop_loss_pct
    cost = 2.0 * (cost_bps_side / 10000.0)
    pnl = 0.0
    trades = 0
    peak = 0.0
    max_dd = 0.0
    for row, p in zip(rows, preds):
        if p == 0:
            continue
        row_idx = int(row.get("row_idx", -1))
        if row_idx < 0:
            continue
        trade_ret = _simulate_trade_ret(
            all_rows=all_rows,
            row_idx=row_idx,
            side=p,
            horizon_steps=horizon_steps,
            stop_loss_pct=stop_loss_pct,
            risk_reward_ratio=risk_reward_ratio,
            trailing_enabled=trailing_enabled,
            trailing_activation_rr=trailing_activation_rr,
            trailing_distance_rr=trailing_distance_rr,
            trailing_lock_breakeven=trailing_lock_breakeven,
            disable_take_profit_when_trailing=disable_take_profit_when_trailing,
        )
        trade_pnl = (trade_ret - cost) * position_notional
        pnl += trade_pnl
        trades += 1
        if pnl > peak:
            peak = pnl
        dd = peak - pnl
        if dd > max_dd:
            max_dd = dd
    expectancy = (pnl / trades) if trades else 0.0
    return pnl, trades, max_dd, expectancy


def _build_trade_plans_and_equity(
    rows: list[dict[str, Any]],
    preds: list[int],
    *,
    all_rows: list[dict[str, Any]],
    horizon_steps: int,
    cost_bps_side: float,
    bankroll_brl: float,
    risk_pct: float,
    stop_loss_pct: float,
    risk_reward_ratio: float,
    trailing_enabled: bool,
    trailing_activation_rr: float,
    trailing_distance_rr: float,
    trailing_lock_breakeven: bool,
    disable_take_profit_when_trailing: bool,
) -> tuple[list[dict[str, Any]], dict[str, float]]:
    if bankroll_brl <= 0 or risk_pct <= 0 or stop_loss_pct <= 0:
        return [], {
            "start_bankroll_brl": bankroll_brl,
            "final_bankroll_brl": bankroll_brl,
            "max_drawdown_brl": 0.0,
            "max_drawdown_pct": 0.0,
            "return_pct": 0.0,
            "trades": 0.0,
            "wins": 0.0,
            "losses": 0.0,
            "win_rate": 0.0,
        }

    plans: list[dict[str, Any]] = []
    bankroll = bankroll_brl
    peak = bankroll_brl
    max_dd = 0.0
    wins = 0
    losses = 0
    cost = 2.0 * (cost_bps_side / 10000.0)

    for row, p in zip(rows, preds):
        if p == 0:
            continue
        row_idx = int(row.get("row_idx", -1))
        if row_idx < 0:
            continue
        entry = float(row.get("mid_price", 0.0))
        if entry <= 0:
            continue
        risk_amount = bankroll * risk_pct
        position_notional = risk_amount / stop_loss_pct
        qty = position_notional / entry
        is_long = p == 1
        sl = entry * (1.0 - stop_loss_pct) if is_long else entry * (1.0 + stop_loss_pct)
        tp = (
            entry * (1.0 + (stop_loss_pct * risk_reward_ratio))
            if is_long
            else entry * (1.0 - (stop_loss_pct * risk_reward_ratio))
        )
        trade_ret = _simulate_trade_ret(
            all_rows=all_rows,
            row_idx=row_idx,
            side=p,
            horizon_steps=horizon_steps,
            stop_loss_pct=stop_loss_pct,
            risk_reward_ratio=risk_reward_ratio,
            trailing_enabled=trailing_enabled,
            trailing_activation_rr=trailing_activation_rr,
            trailing_distance_rr=trailing_distance_rr,
            trailing_lock_breakeven=trailing_lock_breakeven,
            disable_take_profit_when_trailing=disable_take_profit_when_trailing,
        )
        trade_pnl_brl = (trade_ret - cost) * position_notional
        bankroll += trade_pnl_brl
        if trade_pnl_brl >= 0:
            wins += 1
        else:
            losses += 1
        if bankroll > peak:
            peak = bankroll
        dd = peak - bankroll
        if dd > max_dd:
            max_dd = dd
        plans.append(
            {
                "ts_receive": row.get("ts_receive"),
                "side": "long" if is_long else "short",
                "entry_price": entry,
                "stop_loss": sl,
                "take_profit": tp,
                "risk_brl": risk_amount,
                "position_notional_brl": position_notional,
                "qty": qty,
                "pnl_brl": trade_pnl_brl,
                "bankroll_after_brl": bankroll,
            }
        )

    trades = len(plans)
    win_rate = (wins / trades) if trades else 0.0
    max_dd_pct = (max_dd / peak) if peak > 0 else 0.0
    ret_pct = ((bankroll / bankroll_brl) - 1.0) if bankroll_brl > 0 else 0.0
    equity = {
        "start_bankroll_brl": bankroll_brl,
        "final_bankroll_brl": bankroll,
        "max_drawdown_brl": max_dd,
        "max_drawdown_pct": max_dd_pct,
        "return_pct": ret_pct,
        "trades": float(trades),
        "wins": float(wins),
        "losses": float(losses),
        "win_rate": win_rate,
    }
    return plans, equity


def _evaluate_paper_aligned_rows(
    *,
    feature_rows: list[dict[str, Any]],
    labeled_rows: list[dict[str, Any]],
    strategy_name: str,
    strategy_kwargs: dict[str, Any] | None,
    horizon_steps: int,
    n_folds: int,
    cost_bps_per_side: float,
    min_signal_confidence: float,
    max_spread_brl: float,
    regime_gate_enabled: bool = False,
    regime_chop_min_confidence: float = 0.78,
) -> tuple[list[FoldResult], list[dict[str, float]], list[dict[str, Any]], list[dict[str, Any]]]:
    trading_params = load_trading_params()
    paper_cfg = load_paper_mode_config()
    splits = _walk_forward_splits(len(labeled_rows), n_folds)
    folds: list[FoldResult] = []
    folds_brl: list[dict[str, float]] = []
    fold_equity: list[dict[str, Any]] = []
    fold_trade_samples: list[dict[str, Any]] = []
    for i, (train_start, train_end, test_end) in enumerate(splits, start=1):
        train_rows = labeled_rows[train_start:train_end]
        test_rows = labeled_rows[train_end:test_end]
        if not train_rows or not test_rows:
            continue
        strategy = create_strategy(strategy_name, **(strategy_kwargs or {}))
        strategy.fit(train_rows)
        y_true = [int(r["target_direction"]) for r in test_rows]
        y_pred = []
        for r in test_rows:
            sig = strategy.predict(r)
            spread = float(r.get("spread", 0.0))
            regime_label = str(r.get("regime_label", ""))
            if regime_gate_enabled and regime_label == "high_vol_shock":
                intent = "none"
            elif (
                regime_gate_enabled
                and regime_label == "chop"
                and sig.confidence < regime_chop_min_confidence
            ):
                intent = "none"
            elif sig.confidence < min_signal_confidence or spread > max_spread_brl:
                intent = "none"
            else:
                intent = sig.action_intent
            y_pred.append(1 if intent == "long" else -1 if intent == "short" else 0)
        acc = _accuracy(y_true, y_pred)
        pnl, trades = _pnl_net(
            test_rows,
            y_pred,
            all_rows=feature_rows,
            horizon_steps=horizon_steps,
            cost_bps_side=cost_bps_per_side,
            stop_loss_pct=trading_params.risk.default_stop_loss_pct,
            risk_reward_ratio=trading_params.risk.risk_reward_ratio,
            trailing_enabled=paper_cfg.position_rules.trailing_stop_enabled,
            trailing_activation_rr=paper_cfg.position_rules.trailing_activation_rr,
            trailing_distance_rr=paper_cfg.position_rules.trailing_distance_rr,
            trailing_lock_breakeven=paper_cfg.position_rules.trailing_lock_breakeven,
            disable_take_profit_when_trailing=paper_cfg.position_rules.disable_take_profit_when_trailing,
        )
        pnl_brl, trades_brl, max_dd_brl, expectancy_brl = _pnl_net_brl(
            test_rows,
            y_pred,
            all_rows=feature_rows,
            horizon_steps=horizon_steps,
            cost_bps_side=cost_bps_per_side,
            bankroll_brl=trading_params.risk.paper_bankroll_brl,
            risk_pct=trading_params.risk.max_risk_per_trade_pct,
            stop_loss_pct=trading_params.risk.default_stop_loss_pct,
            risk_reward_ratio=trading_params.risk.risk_reward_ratio,
            trailing_enabled=paper_cfg.position_rules.trailing_stop_enabled,
            trailing_activation_rr=paper_cfg.position_rules.trailing_activation_rr,
            trailing_distance_rr=paper_cfg.position_rules.trailing_distance_rr,
            trailing_lock_breakeven=paper_cfg.position_rules.trailing_lock_breakeven,
            disable_take_profit_when_trailing=paper_cfg.position_rules.disable_take_profit_when_trailing,
        )
        folds.append(FoldResult(i, len(train_rows), len(test_rows), acc, pnl, trades))
        trade_plans, equity = _build_trade_plans_and_equity(
            test_rows,
            y_pred,
            all_rows=feature_rows,
            horizon_steps=horizon_steps,
            cost_bps_side=cost_bps_per_side,
            bankroll_brl=trading_params.risk.paper_bankroll_brl,
            risk_pct=trading_params.risk.max_risk_per_trade_pct,
            stop_loss_pct=trading_params.risk.default_stop_loss_pct,
            risk_reward_ratio=trading_params.risk.risk_reward_ratio,
            trailing_enabled=paper_cfg.position_rules.trailing_stop_enabled,
            trailing_activation_rr=paper_cfg.position_rules.trailing_activation_rr,
            trailing_distance_rr=paper_cfg.position_rules.trailing_distance_rr,
            trailing_lock_breakeven=paper_cfg.position_rules.trailing_lock_breakeven,
            disable_take_profit_when_trailing=paper_cfg.position_rules.disable_take_profit_when_trailing,
        )
        fold_equity.append({"fold_id": i, **equity})
        fold_trade_samples.append(
            {
                "fold_id": i,
                "trade_count": len(trade_plans),
                "trades_sample": trade_plans[:10],
            }
        )
        folds_brl.append(
            {
                "fold_id": i,
                "pnl_net_brl": pnl_brl,
                "trades_brl": float(trades_brl),
                "max_drawdown_brl": max_dd_brl,
                "expectancy_brl_per_trade": expectancy_brl,
            }
        )
    return folds, folds_brl, fold_equity, fold_trade_samples


def build_paper_aligned_dataset(
    *,
    raw_root: Path,
    horizon_steps: int,
    move_threshold_bps: float,
    sample_every_updates: int,
    feature_output_path: Path | None = None,
) -> dict[str, Any]:
    cache_root = Path("data/features/binance/BTCBRL/raw_event_cache")
    io_workers = max(1, min(8, (os.cpu_count() or 4)))
    update_rows = _read_all_rows_incremental_cached(
        root=raw_root / "updates",
        cache_root=cache_root,
        stream_name="updates",
        columns=["ts_receive", "symbol", "session_id", "side", "price", "size"],
        workers=io_workers,
    )
    log_rows = _read_all_rows_incremental_cached(
        root=raw_root / "collector_logs",
        cache_root=cache_root,
        stream_name="collector_logs",
        columns=["ts_event", "event_type"],
        workers=io_workers,
    )
    bad_minutes = _collect_bad_minutes(log_rows)
    feature_rows = _build_event_time_features(
        update_rows=update_rows,
        bad_minutes=bad_minutes,
        levels=10,
        sample_every_updates=sample_every_updates,
    )
    labeled_rows = _attach_targets_no_leakage(feature_rows, horizon_steps, move_threshold_bps)
    if feature_output_path is not None:
        feature_output_path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(pa.Table.from_pylist(labeled_rows), feature_output_path)
    return {
        "update_rows_count": len(update_rows),
        "bad_minutes_count": len(bad_minutes),
        "feature_rows": feature_rows,
        "labeled_rows": labeled_rows,
    }


def run_paper_aligned_training_from_dataset(
    *,
    dataset: dict[str, Any],
    report_output_path: Path,
    strategy_name: str = "mlofi_threshold_v1",
    horizon_steps: int = 50,
    n_folds: int = 4,
    cost_bps_per_side: float = 1.5,
    sample_every_updates: int = 20,
    strategy_kwargs: dict[str, Any] | None = None,
    min_signal_confidence: float = 0.0,
    max_spread_brl: float = 1.0e12,
    regime_gate_enabled: bool = False,
    regime_chop_min_confidence: float = 0.78,
) -> dict[str, Any]:
    feature_rows = dataset["feature_rows"]
    labeled_rows = dataset["labeled_rows"]
    folds, folds_brl, fold_equity, fold_trade_samples = _evaluate_paper_aligned_rows(
        feature_rows=feature_rows,
        labeled_rows=labeled_rows,
        strategy_name=strategy_name,
        strategy_kwargs=strategy_kwargs,
        horizon_steps=horizon_steps,
        n_folds=n_folds,
        cost_bps_per_side=cost_bps_per_side,
        min_signal_confidence=min_signal_confidence,
        max_spread_brl=max_spread_brl,
        regime_gate_enabled=regime_gate_enabled,
        regime_chop_min_confidence=regime_chop_min_confidence,
    )

    trading_params = load_trading_params()
    paper_cfg = load_paper_mode_config()
    report = {
        "strategy_name": strategy_name,
        "rows_updates": int(dataset["update_rows_count"]),
        "bad_minutes": int(dataset["bad_minutes_count"]),
        "rows_features": len(feature_rows),
        "rows_labeled": len(labeled_rows),
        "horizon_steps": horizon_steps,
        "sample_every_updates": sample_every_updates,
        "strategy_kwargs": strategy_kwargs or {},
        "min_signal_confidence": min_signal_confidence,
        "max_spread_brl": max_spread_brl,
        "regime_gate_enabled": regime_gate_enabled,
        "regime_chop_min_confidence": regime_chop_min_confidence,
        "execution_backtest": {
            "stop_loss_pct": trading_params.risk.default_stop_loss_pct,
            "risk_reward_ratio": trading_params.risk.risk_reward_ratio,
            "trailing_stop_enabled": paper_cfg.position_rules.trailing_stop_enabled,
            "trailing_activation_rr": paper_cfg.position_rules.trailing_activation_rr,
            "trailing_distance_rr": paper_cfg.position_rules.trailing_distance_rr,
            "trailing_lock_breakeven": paper_cfg.position_rules.trailing_lock_breakeven,
            "disable_take_profit_when_trailing": paper_cfg.position_rules.disable_take_profit_when_trailing,
        },
        "folds": [asdict(f) for f in folds],
        "folds_brl": folds_brl,
        "fold_equity": fold_equity,
        "fold_trade_samples": fold_trade_samples,
        "summary": {
            "mean_accuracy": (sum(f.accuracy for f in folds) / len(folds)) if folds else 0.0,
            "mean_pnl_net": (sum(f.pnl_net for f in folds) / len(folds)) if folds else 0.0,
            "total_trades": sum(f.trades for f in folds),
            "mean_pnl_net_brl": (
                sum(f["pnl_net_brl"] for f in folds_brl) / len(folds_brl) if folds_brl else 0.0
            ),
            "mean_max_drawdown_brl": (
                sum(f["max_drawdown_brl"] for f in folds_brl) / len(folds_brl)
                if folds_brl
                else 0.0
            ),
            "mean_expectancy_brl_per_trade": (
                sum(f["expectancy_brl_per_trade"] for f in folds_brl) / len(folds_brl)
                if folds_brl
                else 0.0
            ),
            "mean_final_bankroll_brl": (
                sum(f["final_bankroll_brl"] for f in fold_equity) / len(fold_equity)
                if fold_equity
                else trading_params.risk.paper_bankroll_brl
            ),
            "mean_return_pct": (
                sum(f["return_pct"] for f in fold_equity) / len(fold_equity) if fold_equity else 0.0
            ),
            "mean_win_rate": (
                sum(f["win_rate"] for f in fold_equity) / len(fold_equity) if fold_equity else 0.0
            ),
        },
    }
    report_output_path.parent.mkdir(parents=True, exist_ok=True)
    report_output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def run_paper_aligned_training(
    raw_root: Path,
    feature_output_path: Path,
    report_output_path: Path,
    strategy_name: str = "mlofi_threshold_v1",
    horizon_steps: int = 50,
    move_threshold_bps: float = 0.5,
    n_folds: int = 4,
    cost_bps_per_side: float = 1.5,
    sample_every_updates: int = 20,
    strategy_kwargs: dict[str, Any] | None = None,
    min_signal_confidence: float = 0.0,
    max_spread_brl: float = 1.0e12,
    regime_gate_enabled: bool = False,
    regime_chop_min_confidence: float = 0.78,
) -> dict[str, Any]:
    dataset = build_paper_aligned_dataset(
        raw_root=raw_root,
        horizon_steps=horizon_steps,
        move_threshold_bps=move_threshold_bps,
        sample_every_updates=sample_every_updates,
        feature_output_path=feature_output_path,
    )
    return run_paper_aligned_training_from_dataset(
        dataset=dataset,
        report_output_path=report_output_path,
        strategy_name=strategy_name,
        horizon_steps=horizon_steps,
        n_folds=n_folds,
        cost_bps_per_side=cost_bps_per_side,
        sample_every_updates=sample_every_updates,
        strategy_kwargs=strategy_kwargs,
        min_signal_confidence=min_signal_confidence,
        max_spread_brl=max_spread_brl,
        regime_gate_enabled=regime_gate_enabled,
        regime_chop_min_confidence=regime_chop_min_confidence,
    )
