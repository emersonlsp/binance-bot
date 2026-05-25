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
    seq_window = 8
    lag_history: list[dict[str, float]] = []

    for row in rows_sorted:
        session_id = str(row.get("session_id", "unknown"))
        if active_session is None:
            active_session = session_id
        elif session_id != active_session:
            # Session boundary: reset reconstructed book state and step counter.
            bid_book.clear()
            ask_book.clear()
            step = 0
            lag_history.clear()
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
        for lag in range(1, seq_window + 1):
            if len(lag_history) >= lag:
                prev = lag_history[-lag]
                out[f"mlofi_score_lag{lag}"] = float(prev.get("mlofi_score", 0.0))
                out[f"spread_lag{lag}"] = float(prev.get("spread", 0.0))
            else:
                out[f"mlofi_score_lag{lag}"] = 0.0
                out[f"spread_lag{lag}"] = 0.0
        out_rows.append(out)
        lag_history.append({"mlofi_score": float(out["mlofi_score"]), "spread": float(out["spread"])})
        if len(lag_history) > seq_window:
            lag_history = lag_history[-seq_window:]
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


def _walk_forward_splits(
    n: int, n_folds: int, embargo_gap_steps: int
) -> list[tuple[int, int, int, int]]:
    min_train = max(300, n // 3)
    test_size = max(100, (n - min_train) // max(1, n_folds))
    splits: list[tuple[int, int, int, int]] = []
    train_end = min_train
    for _ in range(n_folds):
        test_start = min(n, train_end + max(0, embargo_gap_steps))
        test_end = min(n, test_start + test_size)
        if test_end <= test_start:
            break
        splits.append((0, train_end, test_start, test_end))
        train_end = test_end
    return splits


def _accuracy(y_true: list[int], y_pred: list[int]) -> float:
    return (sum(1 for a, b in zip(y_true, y_pred) if a == b) / len(y_true)) if y_true else 0.0


def _entry_exit_prices(row: dict[str, Any], side: int) -> tuple[float, float, float]:
    mid = float(row.get("mid_price", 0.0))
    bid = float(row.get("bid_price_l1", mid))
    ask = float(row.get("ask_price_l1", mid))
    if side == 1:
        return ask, bid, mid
    return bid, ask, mid


def _resolve_stop_tp_pcts(
    *,
    all_rows: list[dict[str, Any]],
    row_idx: int,
    base_stop_loss_pct: float,
    base_risk_reward_ratio: float,
    stop_mode: str,
    vol_lookback_steps: int,
    vol_stop_k: float,
    vol_tp_k: float,
    min_stop_loss_pct: float,
    max_stop_loss_pct: float,
) -> tuple[float, float]:
    if str(stop_mode).lower() != "volatility":
        return base_stop_loss_pct, (base_stop_loss_pct * base_risk_reward_ratio)
    lb = max(10, int(vol_lookback_steps))
    i0 = max(1, row_idx - lb + 1)
    rets: list[float] = []
    prev = float(all_rows[i0 - 1].get("mid_price", 0.0))
    for i in range(i0, row_idx + 1):
        cur = float(all_rows[i].get("mid_price", 0.0))
        if prev > 0 and cur > 0:
            rets.append((cur - prev) / prev)
        prev = cur
    if not rets:
        return base_stop_loss_pct, (base_stop_loss_pct * base_risk_reward_ratio)
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
    vol = var**0.5
    stop_pct = max(float(min_stop_loss_pct), min(float(max_stop_loss_pct), float(vol_stop_k) * vol))
    rr_floor = stop_pct * max(1.0, float(base_risk_reward_ratio))
    tp_pct = max(rr_floor, float(vol_tp_k) * vol)
    return stop_pct, tp_pct


def _consume_book_levels(
    *,
    row: dict[str, Any],
    side: int,
    requested_qty: float,
    max_levels: int = 10,
) -> tuple[float, float, float]:
    # side=1 (long) consumes asks; side=-1 (short) consumes bids.
    if requested_qty <= 0:
        return 0.0, 0.0, 0.0
    remaining = requested_qty
    filled = 0.0
    notional = 0.0
    for lvl in range(1, max_levels + 1):
        if side == 1:
            px = float(row.get(f"ask_price_l{lvl}", 0.0))
            sz = float(row.get(f"ask_size_l{lvl}", 0.0))
        else:
            px = float(row.get(f"bid_price_l{lvl}", 0.0))
            sz = float(row.get(f"bid_size_l{lvl}", 0.0))
        if px <= 0 or sz <= 0:
            continue
        take = min(remaining, sz)
        if take <= 0:
            continue
        filled += take
        notional += take * px
        remaining -= take
        if remaining <= 1.0e-12:
            break
    avg_px = (notional / filled) if filled > 0 else 0.0
    fill_ratio = filled / requested_qty if requested_qty > 0 else 0.0
    return filled, fill_ratio, avg_px


def _simulate_trade_outcome(
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
    requested_qty: float,
    latency_steps: int,
    allow_partial_fill: bool,
    min_fill_ratio: float,
    stop_mode: str,
    vol_lookback_steps: int,
    vol_stop_k: float,
    vol_tp_k: float,
    min_stop_loss_pct: float,
    max_stop_loss_pct: float,
) -> dict[str, float | int]:
    if requested_qty <= 0:
        return {"filled": 0.0, "fill_ratio": 0.0, "trade_ret": 0.0, "slippage_bps": 0.0, "holding_seconds": 0.0}
    entry_i = row_idx + max(0, latency_steps)
    if entry_i >= len(all_rows):
        return {"filled": 0.0, "fill_ratio": 0.0, "trade_ret": 0.0, "slippage_bps": 0.0, "holding_seconds": 0.0}
    now = all_rows[entry_i]
    entry_ref = all_rows[row_idx]
    session_id = str(now.get("session_id", ""))
    ref_session = str(entry_ref.get("session_id", ""))
    if session_id != ref_session:
        return {"filled": 0.0, "fill_ratio": 0.0, "trade_ret": 0.0, "slippage_bps": 0.0, "holding_seconds": 0.0}

    _, exit_touch_px, entry_mid = _entry_exit_prices(now, side)
    filled_qty, fill_ratio, entry_px = _consume_book_levels(
        row=now, side=side, requested_qty=requested_qty, max_levels=10
    )
    if (not allow_partial_fill and fill_ratio < 1.0) or fill_ratio < min_fill_ratio:
        return {"filled": 0.0, "fill_ratio": fill_ratio, "trade_ret": 0.0, "slippage_bps": 0.0, "holding_seconds": 0.0}
    filled = 1.0 if filled_qty > 0 else 0.0

    entry = float(entry_px)
    if entry <= 0:
        return {"filled": 0.0, "fill_ratio": 0.0, "trade_ret": 0.0, "slippage_bps": 0.0, "holding_seconds": 0.0}
    is_long = side == 1
    eff_stop_loss_pct, eff_take_profit_pct = _resolve_stop_tp_pcts(
        all_rows=all_rows,
        row_idx=entry_i,
        base_stop_loss_pct=stop_loss_pct,
        base_risk_reward_ratio=risk_reward_ratio,
        stop_mode=stop_mode,
        vol_lookback_steps=vol_lookback_steps,
        vol_stop_k=vol_stop_k,
        vol_tp_k=vol_tp_k,
        min_stop_loss_pct=min_stop_loss_pct,
        max_stop_loss_pct=max_stop_loss_pct,
    )
    if is_long:
        stop = entry * (1.0 - eff_stop_loss_pct)
        take_profit = entry * (1.0 + eff_take_profit_pct)
    else:
        stop = entry * (1.0 + eff_stop_loss_pct)
        take_profit = entry * (1.0 - eff_take_profit_pct)
    initial_stop = stop
    best_price = entry
    trailing_active = False
    max_j = min(len(all_rows) - 1, entry_i + horizon_steps)
    exit_price = exit_touch_px
    exit_i = entry_i
    for j in range(entry_i + 1, max_j + 1):
        row = all_rows[j]
        if str(row.get("session_id", "")) != session_id:
            break
        px = float(row["mid_price"])
        bid = float(row.get("bid_price_l1", px))
        ask = float(row.get("ask_price_l1", px))
        touch = bid if is_long else ask
        exit_price = touch
        exit_i = j
        if is_long:
            best_price = max(best_price, px)
            risk_per_unit = max(1.0e-12, entry - initial_stop)
            favorable_move = best_price - entry
        else:
            best_price = min(best_price, px)
            risk_per_unit = max(1.0e-12, initial_stop - entry)
            favorable_move = entry - best_price
        if trailing_enabled and (not trailing_active) and favorable_move >= (trailing_activation_rr * risk_per_unit):
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
            if bid <= stop:
                exit_price = stop
                break
            if (not trailing_active or not disable_take_profit_when_trailing) and bid >= take_profit:
                exit_price = take_profit
                break
        else:
            if ask >= stop:
                exit_price = stop
                break
            if (not trailing_active or not disable_take_profit_when_trailing) and ask <= take_profit:
                exit_price = take_profit
                break
    trade_ret = ((exit_price - entry) / entry) if is_long else ((entry - exit_price) / entry)
    slippage_bps = 0.0
    if entry_mid > 0:
        slippage_bps = (((entry - entry_mid) / entry_mid) * 10000.0) if is_long else (((entry_mid - entry) / entry_mid) * 10000.0)
    holding_seconds = 0.0
    try:
        ts_entry = _parse_iso(str(all_rows[entry_i].get("ts_receive", "")))
        ts_exit = _parse_iso(str(all_rows[exit_i].get("ts_receive", "")))
        holding_seconds = max(0.0, float((ts_exit - ts_entry).total_seconds()))
    except Exception:
        holding_seconds = 0.0
    return {
        "filled": filled,
        "fill_ratio": fill_ratio,
        "trade_ret": trade_ret,
        "slippage_bps": slippage_bps,
        "holding_seconds": holding_seconds,
    }


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
    entry_latency_steps: int,
    allow_partial_fill: bool,
    min_fill_ratio: float,
    stop_mode: str,
    vol_lookback_steps: int,
    vol_stop_k: float,
    vol_tp_k: float,
    min_stop_loss_pct: float,
    max_stop_loss_pct: float,
) -> tuple[float, int, dict[str, float]]:
    cost = 2.0 * (cost_bps_side / 10000.0)
    pnl = 0.0
    trades = 0
    attempted = 0
    filled = 0
    partial = 0
    canceled = 0
    slippage_sum = 0.0
    slippage_n = 0
    for row, p in zip(rows, preds):
        if p == 0:
            continue
        attempted += 1
        row_idx = int(row.get("row_idx", -1))
        if row_idx < 0:
            continue
        out = _simulate_trade_outcome(
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
            requested_qty=1.0,
            latency_steps=entry_latency_steps,
            allow_partial_fill=allow_partial_fill,
            min_fill_ratio=min_fill_ratio,
            stop_mode=stop_mode,
            vol_lookback_steps=vol_lookback_steps,
            vol_stop_k=vol_stop_k,
            vol_tp_k=vol_tp_k,
            min_stop_loss_pct=min_stop_loss_pct,
            max_stop_loss_pct=max_stop_loss_pct,
        )
        if float(out.get("filled", 0.0)) <= 0:
            canceled += 1
            continue
        fr = float(out.get("fill_ratio", 0.0))
        if fr < 1.0:
            partial += 1
        filled += 1
        trades += 1
        tr = float(out.get("trade_ret", 0.0))
        pnl += (tr * fr) - (cost * fr)
        slippage_sum += float(out.get("slippage_bps", 0.0))
        slippage_n += 1
    exec_stats = {
        "attempted_trades": float(attempted),
        "filled_trades": float(filled),
        "partial_fills": float(partial),
        "canceled_trades": float(canceled),
        "fill_rate": float(filled / attempted) if attempted else 0.0,
        "cancel_rate": float(canceled / attempted) if attempted else 0.0,
        "avg_slippage_bps": float(slippage_sum / slippage_n) if slippage_n else 0.0,
    }
    return pnl, trades, exec_stats


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
    entry_latency_steps: int,
    allow_partial_fill: bool,
    min_fill_ratio: float,
    stop_mode: str,
    vol_lookback_steps: int,
    vol_stop_k: float,
    vol_tp_k: float,
    min_stop_loss_pct: float,
    max_stop_loss_pct: float,
    margin_sim_enabled: bool,
    margin_max_borrow_notional_brl: float,
    margin_borrow_interest_hourly: float,
) -> tuple[float, int, float, float, dict[str, float]]:
    if bankroll_brl <= 0 or risk_pct <= 0 or stop_loss_pct <= 0:
        return 0.0, 0, 0.0, 0.0, {
            "attempted_trades": 0.0,
            "filled_trades": 0.0,
            "partial_fills": 0.0,
            "canceled_trades": 0.0,
            "fill_rate": 0.0,
            "cancel_rate": 0.0,
            "avg_slippage_bps": 0.0,
        }
    cost = 2.0 * (cost_bps_side / 10000.0)
    pnl = 0.0
    trades = 0
    peak = 0.0
    max_dd = 0.0
    attempted = 0
    partial = 0
    canceled = 0
    slippage_sum = 0.0
    slippage_n = 0
    interest_sum = 0.0
    borrow_sum = 0.0
    for row, p in zip(rows, preds):
        if p == 0:
            continue
        attempted += 1
        row_idx = int(row.get("row_idx", -1))
        if row_idx < 0:
            continue
        eff_stop_loss_pct, _ = _resolve_stop_tp_pcts(
            all_rows=all_rows,
            row_idx=row_idx,
            base_stop_loss_pct=stop_loss_pct,
            base_risk_reward_ratio=risk_reward_ratio,
            stop_mode=stop_mode,
            vol_lookback_steps=vol_lookback_steps,
            vol_stop_k=vol_stop_k,
            vol_tp_k=vol_tp_k,
            min_stop_loss_pct=min_stop_loss_pct,
            max_stop_loss_pct=max_stop_loss_pct,
        )
        equity_before = max(0.0, bankroll_brl + pnl)
        risk_amount = equity_before * risk_pct
        position_notional = risk_amount / max(eff_stop_loss_pct, 1.0e-12)
        borrowed_notional = max(0.0, position_notional - equity_before)
        if margin_sim_enabled and borrowed_notional > max(0.0, margin_max_borrow_notional_brl):
            position_notional = equity_before + max(0.0, margin_max_borrow_notional_brl)
            borrowed_notional = max(0.0, margin_max_borrow_notional_brl)
        out = _simulate_trade_outcome(
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
            requested_qty=position_notional / max(float(row.get("mid_price", 1.0)), 1.0e-12),
            latency_steps=entry_latency_steps,
            allow_partial_fill=allow_partial_fill,
            min_fill_ratio=min_fill_ratio,
            stop_mode=stop_mode,
            vol_lookback_steps=vol_lookback_steps,
            vol_stop_k=vol_stop_k,
            vol_tp_k=vol_tp_k,
            min_stop_loss_pct=min_stop_loss_pct,
            max_stop_loss_pct=max_stop_loss_pct,
        )
        if float(out.get("filled", 0.0)) <= 0:
            canceled += 1
            continue
        fr = float(out.get("fill_ratio", 0.0))
        if fr < 1.0:
            partial += 1
        trade_ret = float(out.get("trade_ret", 0.0))
        holding_seconds = float(out.get("holding_seconds", 0.0))
        interest_brl = 0.0
        if margin_sim_enabled and borrowed_notional > 0 and margin_borrow_interest_hourly > 0:
            interest_brl = borrowed_notional * margin_borrow_interest_hourly * (holding_seconds / 3600.0)
        trade_pnl = ((trade_ret * fr) - (cost * fr)) * position_notional - interest_brl
        pnl += trade_pnl
        trades += 1
        interest_sum += interest_brl
        borrow_sum += borrowed_notional
        slippage_sum += float(out.get("slippage_bps", 0.0))
        slippage_n += 1
        if pnl > peak:
            peak = pnl
        dd = peak - pnl
        if dd > max_dd:
            max_dd = dd
    expectancy = (pnl / trades) if trades else 0.0
    exec_stats = {
        "attempted_trades": float(attempted),
        "filled_trades": float(trades),
        "partial_fills": float(partial),
        "canceled_trades": float(canceled),
        "fill_rate": float(trades / attempted) if attempted else 0.0,
        "cancel_rate": float(canceled / attempted) if attempted else 0.0,
        "avg_slippage_bps": float(slippage_sum / slippage_n) if slippage_n else 0.0,
        "borrow_interest_total_brl": float(interest_sum),
        "avg_borrow_notional_brl": float(borrow_sum / trades) if trades else 0.0,
    }
    return pnl, trades, max_dd, expectancy, exec_stats


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
    entry_latency_steps: int,
    allow_partial_fill: bool,
    min_fill_ratio: float,
    stop_mode: str,
    vol_lookback_steps: int,
    vol_stop_k: float,
    vol_tp_k: float,
    min_stop_loss_pct: float,
    max_stop_loss_pct: float,
    margin_sim_enabled: bool,
    margin_max_borrow_notional_brl: float,
    margin_borrow_interest_hourly: float,
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
        eff_stop_loss_pct, eff_take_profit_pct = _resolve_stop_tp_pcts(
            all_rows=all_rows,
            row_idx=row_idx,
            base_stop_loss_pct=stop_loss_pct,
            base_risk_reward_ratio=risk_reward_ratio,
            stop_mode=stop_mode,
            vol_lookback_steps=vol_lookback_steps,
            vol_stop_k=vol_stop_k,
            vol_tp_k=vol_tp_k,
            min_stop_loss_pct=min_stop_loss_pct,
            max_stop_loss_pct=max_stop_loss_pct,
        )
        risk_amount = bankroll * risk_pct
        position_notional = risk_amount / max(eff_stop_loss_pct, 1.0e-12)
        borrowed_notional = max(0.0, position_notional - bankroll)
        if margin_sim_enabled and borrowed_notional > max(0.0, margin_max_borrow_notional_brl):
            position_notional = bankroll + max(0.0, margin_max_borrow_notional_brl)
            borrowed_notional = max(0.0, margin_max_borrow_notional_brl)
        qty = position_notional / entry
        is_long = p == 1
        sl = entry * (1.0 - eff_stop_loss_pct) if is_long else entry * (1.0 + eff_stop_loss_pct)
        tp = (entry * (1.0 + eff_take_profit_pct)) if is_long else (entry * (1.0 - eff_take_profit_pct))
        out = _simulate_trade_outcome(
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
            requested_qty=qty,
            latency_steps=entry_latency_steps,
            allow_partial_fill=allow_partial_fill,
            min_fill_ratio=min_fill_ratio,
            stop_mode=stop_mode,
            vol_lookback_steps=vol_lookback_steps,
            vol_stop_k=vol_stop_k,
            vol_tp_k=vol_tp_k,
            min_stop_loss_pct=min_stop_loss_pct,
            max_stop_loss_pct=max_stop_loss_pct,
        )
        if float(out.get("filled", 0.0)) <= 0:
            continue
        fr = float(out.get("fill_ratio", 0.0))
        trade_ret = float(out.get("trade_ret", 0.0))
        holding_seconds = float(out.get("holding_seconds", 0.0))
        interest_brl = 0.0
        if margin_sim_enabled and borrowed_notional > 0 and margin_borrow_interest_hourly > 0:
            interest_brl = borrowed_notional * margin_borrow_interest_hourly * (holding_seconds / 3600.0)
        trade_pnl_brl = ((trade_ret * fr) - (cost * fr)) * position_notional - interest_brl
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
                "borrowed_notional_brl": borrowed_notional,
                "borrow_interest_brl": interest_brl,
                "qty": qty,
                "fill_ratio": fr,
                "regime_label": str(row.get("regime_label", "unknown")),
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
    embargo_gap_steps: int = 0,
    entry_latency_steps: int = 0,
    allow_partial_fill: bool = True,
    min_fill_ratio: float = 0.1,
) -> tuple[
    list[FoldResult],
    list[dict[str, float]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    trading_params = load_trading_params()
    paper_cfg = load_paper_mode_config()
    splits = _walk_forward_splits(len(labeled_rows), n_folds, embargo_gap_steps)
    folds: list[FoldResult] = []
    folds_brl: list[dict[str, float]] = []
    fold_equity: list[dict[str, Any]] = []
    fold_trade_samples: list[dict[str, Any]] = []
    fold_exec_stats: list[dict[str, Any]] = []
    regime_metrics: dict[str, dict[str, float]] = {}
    epoch_metrics: dict[str, dict[str, float]] = {"early": {"trades": 0.0, "pnl_brl": 0.0}, "mid": {"trades": 0.0, "pnl_brl": 0.0}, "late": {"trades": 0.0, "pnl_brl": 0.0}}
    for i, (train_start, train_end, test_start, test_end) in enumerate(splits, start=1):
        train_rows = labeled_rows[train_start:train_end]
        test_rows = labeled_rows[test_start:test_end]
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
        pnl_brl, trades_brl, max_dd_brl, expectancy_brl, exec_stats_brl = _pnl_net_brl(
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
            entry_latency_steps=entry_latency_steps,
            allow_partial_fill=allow_partial_fill,
            min_fill_ratio=min_fill_ratio,
            stop_mode=paper_cfg.position_rules.stop_mode,
            vol_lookback_steps=paper_cfg.position_rules.vol_lookback_steps,
            vol_stop_k=paper_cfg.position_rules.vol_stop_k,
            vol_tp_k=paper_cfg.position_rules.vol_tp_k,
            min_stop_loss_pct=paper_cfg.position_rules.min_stop_loss_pct,
            max_stop_loss_pct=paper_cfg.position_rules.max_stop_loss_pct,
            margin_sim_enabled=paper_cfg.margin_sim.enabled,
            margin_max_borrow_notional_brl=paper_cfg.margin_sim.max_borrow_notional_brl,
            margin_borrow_interest_hourly=paper_cfg.margin_sim.borrow_interest_hourly,
        )
        # Single-trace metrics: keep fold pnl/trades aligned with BRL dynamic-risk simulation.
        folds.append(FoldResult(i, len(train_rows), len(test_rows), acc, pnl_brl, trades_brl))
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
            entry_latency_steps=entry_latency_steps,
            allow_partial_fill=allow_partial_fill,
            min_fill_ratio=min_fill_ratio,
            stop_mode=paper_cfg.position_rules.stop_mode,
            vol_lookback_steps=paper_cfg.position_rules.vol_lookback_steps,
            vol_stop_k=paper_cfg.position_rules.vol_stop_k,
            vol_tp_k=paper_cfg.position_rules.vol_tp_k,
            min_stop_loss_pct=paper_cfg.position_rules.min_stop_loss_pct,
            max_stop_loss_pct=paper_cfg.position_rules.max_stop_loss_pct,
            margin_sim_enabled=paper_cfg.margin_sim.enabled,
            margin_max_borrow_notional_brl=paper_cfg.margin_sim.max_borrow_notional_brl,
            margin_borrow_interest_hourly=paper_cfg.margin_sim.borrow_interest_hourly,
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
        fold_exec_stats.append({"fold_id": i, **exec_stats_brl})
        for tp in trade_plans:
            rg = str(tp.get("regime_label", "unknown"))
            bucket = regime_metrics.setdefault(rg, {"trades": 0.0, "pnl_brl": 0.0})
            bucket["trades"] += 1.0
            bucket["pnl_brl"] += float(tp.get("pnl_brl", 0.0))
        n_tp = len(trade_plans)
        if n_tp:
            a = n_tp // 3
            b = (2 * n_tp) // 3
            for idx_tp, tp in enumerate(trade_plans):
                ep = "early" if idx_tp < a else "mid" if idx_tp < b else "late"
                epoch_metrics[ep]["trades"] += 1.0
                epoch_metrics[ep]["pnl_brl"] += float(tp.get("pnl_brl", 0.0))
    stability_segments = {"regime_metrics": regime_metrics, "epoch_metrics": epoch_metrics}
    return folds, folds_brl, fold_equity, fold_trade_samples, fold_exec_stats, stability_segments


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
    embargo_gap_steps: int = 0,
    entry_latency_steps: int = 0,
    allow_partial_fill: bool = True,
    min_fill_ratio: float = 0.1,
) -> dict[str, Any]:
    feature_rows = dataset["feature_rows"]
    labeled_rows = dataset["labeled_rows"]
    (
        folds,
        folds_brl,
        fold_equity,
        fold_trade_samples,
        fold_exec_stats,
        stability_segments,
    ) = _evaluate_paper_aligned_rows(
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
        embargo_gap_steps=embargo_gap_steps,
        entry_latency_steps=entry_latency_steps,
        allow_partial_fill=allow_partial_fill,
        min_fill_ratio=min_fill_ratio,
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
        "embargo_gap_steps": embargo_gap_steps,
        "entry_latency_steps": entry_latency_steps,
        "allow_partial_fill": allow_partial_fill,
        "min_fill_ratio": min_fill_ratio,
        "execution_backtest": {
            "stop_loss_pct": trading_params.risk.default_stop_loss_pct,
            "risk_reward_ratio": trading_params.risk.risk_reward_ratio,
            "trailing_stop_enabled": paper_cfg.position_rules.trailing_stop_enabled,
            "trailing_activation_rr": paper_cfg.position_rules.trailing_activation_rr,
            "trailing_distance_rr": paper_cfg.position_rules.trailing_distance_rr,
            "trailing_lock_breakeven": paper_cfg.position_rules.trailing_lock_breakeven,
            "disable_take_profit_when_trailing": paper_cfg.position_rules.disable_take_profit_when_trailing,
            "stop_mode": paper_cfg.position_rules.stop_mode,
            "vol_lookback_steps": paper_cfg.position_rules.vol_lookback_steps,
            "vol_stop_k": paper_cfg.position_rules.vol_stop_k,
            "vol_tp_k": paper_cfg.position_rules.vol_tp_k,
            "min_stop_loss_pct": paper_cfg.position_rules.min_stop_loss_pct,
            "max_stop_loss_pct": paper_cfg.position_rules.max_stop_loss_pct,
            "margin_sim_enabled": paper_cfg.margin_sim.enabled,
            "margin_max_borrow_notional_brl": paper_cfg.margin_sim.max_borrow_notional_brl,
            "margin_borrow_interest_hourly": paper_cfg.margin_sim.borrow_interest_hourly,
        },
        "folds": [asdict(f) for f in folds],
        "folds_brl": folds_brl,
        "fold_equity": fold_equity,
        "fold_trade_samples": fold_trade_samples,
        "fold_execution_quality": fold_exec_stats,
        "stability_segments": stability_segments,
        "summary": {
            "mean_accuracy": (sum(f.accuracy for f in folds) / len(folds)) if folds else 0.0,
            # Canonical PnL metric is BRL-based dynamic-risk simulation.
            # Keep mean_pnl_net as backward-compatible alias with identical value.
            "mean_pnl_net_brl": (sum(f.pnl_net for f in folds) / len(folds)) if folds else 0.0,
            "mean_pnl_net": (sum(f.pnl_net for f in folds) / len(folds)) if folds else 0.0,
            "total_trades": sum(f.trades for f in folds),
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
            "mean_fill_rate": (
                sum(f.get("fill_rate", 0.0) for f in fold_exec_stats) / len(fold_exec_stats)
                if fold_exec_stats
                else 0.0
            ),
            "mean_cancel_rate": (
                sum(f.get("cancel_rate", 0.0) for f in fold_exec_stats) / len(fold_exec_stats)
                if fold_exec_stats
                else 0.0
            ),
            "mean_slippage_bps": (
                sum(f.get("avg_slippage_bps", 0.0) for f in fold_exec_stats) / len(fold_exec_stats)
                if fold_exec_stats
                else 0.0
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
    embargo_gap_steps: int = 0,
    entry_latency_steps: int = 0,
    allow_partial_fill: bool = True,
    min_fill_ratio: float = 0.1,
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
        embargo_gap_steps=embargo_gap_steps,
        entry_latency_steps=entry_latency_steps,
        allow_partial_fill=allow_partial_fill,
        min_fill_ratio=min_fill_ratio,
    )
