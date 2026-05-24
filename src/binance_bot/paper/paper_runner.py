from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..collector.env_loader import load_dotenv
from ..runtime.params import load_trading_params
from ..strategies.registry import create_strategy
from ..training.paper_aligned import build_paper_aligned_dataset


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_iso_utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)


def _entry_price_from_book(side: str, mid: float, spread: float) -> float:
    half = max(0.0, spread / 2.0)
    best_bid = max(0.0, mid - half)
    best_ask = mid + half
    return best_ask if side == "long" else best_bid


def _exit_price_from_book(side: str, mid: float, spread: float) -> float:
    half = max(0.0, spread / 2.0)
    best_bid = max(0.0, mid - half)
    best_ask = mid + half
    # Close long selling at bid; close short buying at ask.
    return best_bid if side == "long" else best_ask


def _best_bid_ask(mid: float, spread: float) -> tuple[float, float]:
    half = max(0.0, spread / 2.0)
    best_bid = max(0.0, mid - half)
    best_ask = mid + half
    return best_bid, best_ask


def _ioc_fills(side: str, limit_price: float, mid: float, spread: float) -> bool:
    best_bid, best_ask = _best_bid_ask(mid, spread)
    if side == "long":
        return limit_price >= best_ask and best_ask > 0
    if side == "short":
        return limit_price <= best_bid and best_bid > 0
    return False


def _best_level_size(latest: dict[str, Any], side: str) -> float:
    key = "ask_size_l1" if side == "long" else "bid_size_l1"
    raw = latest.get(key, 0.0)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0


def _load_champion() -> dict[str, Any]:
    p = Path("artifacts/champion_strategy_xgb_clean.json")
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    lb = Path("artifacts/reports/xgb_clean_search_with_regime/leaderboard.json")
    if not lb.exists():
        raise RuntimeError("Champion artifact not found. Run training first.")
    obj = json.loads(lb.read_text(encoding="utf-8"))
    champ = obj.get("promoted_champion") or obj.get("champion")
    if not champ:
        raise RuntimeError("No champion found in leaderboard.")
    return champ


def _load_state(path: Path, initial_bankroll: float) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {
        "bankroll_brl": initial_bankroll,
        "start_bankroll_brl": initial_bankroll,
        "open_position": None,
        "last_feature_ts": "",
        "trades_count": 0,
        "wins": 0,
        "losses": 0,
        "realized_pnl_brl": 0.0,
        "max_drawdown_brl": 0.0,
        "peak_bankroll_brl": initial_bankroll,
    }


def _save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _state_summary(state: dict[str, Any]) -> dict[str, Any]:
    start = float(state.get("start_bankroll_brl", 0.0))
    bankroll = float(state.get("bankroll_brl", 0.0))
    pnl = float(state.get("realized_pnl_brl", 0.0))
    trades = int(state.get("trades_count", 0))
    wins = int(state.get("wins", 0))
    losses = int(state.get("losses", 0))
    win_rate = (wins / trades) if trades else 0.0
    ret_pct = ((bankroll / start) - 1.0) if start > 0 else 0.0
    return {
        "updated_at": _now_iso(),
        "start_bankroll_brl": start,
        "bankroll_brl": bankroll,
        "realized_pnl_brl": pnl,
        "return_pct": ret_pct,
        "trades_count": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "max_drawdown_brl": float(state.get("max_drawdown_brl", 0.0)),
        "open_position": state.get("open_position"),
    }


def _build_strategy_and_latest_feature(champion: dict[str, Any]) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    params = champion.get("params", {})
    horizon = int(params.get("horizon_steps", 80))
    move_bps = float(params.get("move_threshold_bps", 0.8))
    sample_every = int(params.get("sample_every_updates", 40))
    strategy_name = str(champion.get("strategy_name", "mlofi_xgb_v1"))
    strategy_kwargs = dict(params.get("strategy_kwargs", champion.get("strategy_kwargs", {})))

    ds = build_paper_aligned_dataset(
        raw_root=Path("data/raw/binance/BTCBRL"),
        horizon_steps=horizon,
        move_threshold_bps=move_bps,
        sample_every_updates=sample_every,
        feature_output_path=None,
    )
    labeled = ds["labeled_rows"]
    features = ds["feature_rows"]
    if len(labeled) < 200 or not features:
        raise RuntimeError("Not enough data to run paper cycle.")
    strategy = create_strategy(strategy_name, **strategy_kwargs)
    strategy.fit(labeled)
    latest = features[-1]
    return strategy, latest, params


def _open_position(state: dict[str, Any], side: str, entry: float, params: dict[str, Any], ts: str) -> dict[str, Any]:
    trading = load_trading_params()
    stop_loss_pct = trading.risk.default_stop_loss_pct
    rr = trading.risk.risk_reward_ratio
    risk_brl = state["bankroll_brl"] * trading.risk.max_risk_per_trade_pct
    position_notional = risk_brl / max(stop_loss_pct, 1.0e-12)
    qty = position_notional / max(entry, 1.0e-12)
    if side == "long":
        sl = entry * (1.0 - stop_loss_pct)
        tp = entry * (1.0 + (stop_loss_pct * rr))
    else:
        sl = entry * (1.0 + stop_loss_pct)
        tp = entry * (1.0 - (stop_loss_pct * rr))
    pos = {
        "side": side,
        "entry_price": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "risk_brl": risk_brl,
        "position_notional_brl": position_notional,
        "qty": qty,
        "opened_at": ts,
        "meta": {"params_snapshot": params},
    }
    state["open_position"] = pos
    return pos


def _try_close_position(state: dict[str, Any], mid: float, spread: float, ts: str) -> dict[str, Any] | None:
    pos = state.get("open_position")
    if not pos:
        return None
    side = pos["side"]
    sl = float(pos["stop_loss"])
    tp = float(pos["take_profit"])
    entry = float(pos["entry_price"])
    should_close = False
    exit_reason = ""
    exit_price = _exit_price_from_book(side, mid, spread)
    if side == "long":
        if mid <= sl:
            should_close = True
            exit_reason = "stop_loss"
            exit_price = sl
        elif mid >= tp:
            should_close = True
            exit_reason = "take_profit"
            exit_price = tp
    else:
        if mid >= sl:
            should_close = True
            exit_reason = "stop_loss"
            exit_price = sl
        elif mid <= tp:
            should_close = True
            exit_reason = "take_profit"
            exit_price = tp
    if not should_close:
        return None

    trading = load_trading_params()
    cost = 2.0 * (1.5 / 10000.0)
    if side == "long":
        trade_ret = (exit_price - entry) / entry
    else:
        trade_ret = (entry - exit_price) / entry
    pnl_brl = (trade_ret - cost) * float(pos["position_notional_brl"])
    state["bankroll_brl"] += pnl_brl
    state["realized_pnl_brl"] += pnl_brl
    state["trades_count"] += 1
    if pnl_brl >= 0:
        state["wins"] += 1
    else:
        state["losses"] += 1
    peak = max(float(state.get("peak_bankroll_brl", state["bankroll_brl"])), state["bankroll_brl"])
    state["peak_bankroll_brl"] = peak
    dd = peak - state["bankroll_brl"]
    state["max_drawdown_brl"] = max(float(state.get("max_drawdown_brl", 0.0)), dd)
    state["open_position"] = None
    return {
        "ts": ts,
        "side": side,
        "entry_price": entry,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "pnl_brl": pnl_brl,
        "bankroll_after_brl": state["bankroll_brl"],
        "risk_mode": trading.risk.margin_mode,
    }


def run_forever(poll_seconds: int = 20) -> None:
    load_dotenv()
    champion = _load_champion()
    out = Path("artifacts/paper_sim")
    state_path = out / "paper_report.json"
    signals_path = out / "signals.jsonl"
    trades_path = out / "trades.jsonl"
    trading = load_trading_params()
    state = _load_state(state_path, trading.risk.paper_bankroll_brl)
    _save_state(state_path, _state_summary(state))
    print("[paper_sim] started", _now_iso(), "bankroll=", state["bankroll_brl"], flush=True)
    last_heartbeat = 0.0
    max_feature_age_seconds = 120.0
    while True:
        try:
            event_happened = False
            strategy, latest, params = _build_strategy_and_latest_feature(champion)
            ts = str(latest.get("ts_receive", ""))
            if ts and ts != state.get("last_feature_ts", ""):
                age_seconds = (datetime.now(UTC) - _parse_iso_utc(ts)).total_seconds()
                if age_seconds > max_feature_age_seconds:
                    print(
                        f"[paper_sim] dado atrasado ({age_seconds:.0f}s). aguardando dado novo...",
                        flush=True,
                    )
                    now = time.time()
                    if now - last_heartbeat >= 10.0:
                        last_heartbeat = now
                    time.sleep(max(5, poll_seconds))
                    continue
                state["last_feature_ts"] = ts
                mid = float(latest.get("mid_price", 0.0))
                spread = float(latest.get("spread", 0.0))
                sig = strategy.predict(latest)
                _append_jsonl(
                    signals_path,
                    {
                        "ts": ts,
                        "signal": sig.action_intent,
                        "confidence": sig.confidence,
                        "score": sig.signal_score,
                        "mid_price": mid,
                        "spread": spread,
                    },
                )
                closed = _try_close_position(state, mid, spread, ts)
                if closed is not None:
                    event_happened = True
                    _append_jsonl(trades_path, closed)
                    summary = _state_summary(state)
                    print(
                        f"[paper_sim] close {closed['side']} {closed['exit_reason']} pnl={closed['pnl_brl']:.2f} bankroll={closed['bankroll_after_brl']:.2f}",
                        flush=True,
                    )
                    print(
                        f"[paper_sim] summary trades={summary['trades_count']} win_rate={summary['win_rate']:.2%} pnl={summary['realized_pnl_brl']:.2f} return={summary['return_pct']:.2%} dd={summary['max_drawdown_brl']:.2f}",
                        flush=True,
                    )
                if state.get("open_position") is None:
                    min_conf = float(params.get("min_signal_confidence", 0.75))
                    max_spread = float(params.get("max_spread_brl", 3.0))
                    if sig.confidence >= min_conf and spread <= max_spread:
                        if sig.action_intent in ("long", "short"):
                            entry_px = _entry_price_from_book(sig.action_intent, mid, spread)
                            draft_pos = _open_position(state, sig.action_intent, entry_px, params, ts)
                            state["open_position"] = None
                            book_qty = _best_level_size(latest, sig.action_intent)
                            req_qty = float(draft_pos["qty"])
                            if _ioc_fills(sig.action_intent, entry_px, mid, spread) and book_qty >= req_qty:
                                pos = _open_position(state, sig.action_intent, entry_px, params, ts)
                                event_happened = True
                                print(
                                    f"[paper_sim] ioc_filled side={sig.action_intent} limit={entry_px:.2f} mid={mid:.2f} spread={spread:.2f} qty={req_qty:.6f} l1_qty={book_qty:.6f}",
                                    flush=True,
                                )
                                print(
                                    f"[paper_sim] open {pos['side']} entry={pos['entry_price']:.2f} sl={pos['stop_loss']:.2f} tp={pos['take_profit']:.2f}",
                                    flush=True,
                                )
                            else:
                                event_happened = True
                                print(
                                    f"[paper_sim] ioc_canceled side={sig.action_intent} limit={entry_px:.2f} mid={mid:.2f} spread={spread:.2f} qty={req_qty:.6f} l1_qty={book_qty:.6f}",
                                    flush=True,
                                )
                _save_state(state_path, _state_summary(state))
            now = time.time()
            if (not event_happened) and (now - last_heartbeat >= 10.0):
                open_pos = state.get("open_position")
                if open_pos is None:
                    print("[paper_sim] analisando mercado... sem novo evento", flush=True)
                else:
                    print(
                        f"[paper_sim] trade aberta side={open_pos['side']} entry={float(open_pos['entry_price']):.2f} sl={float(open_pos['stop_loss']):.2f} tp={float(open_pos['take_profit']):.2f}",
                        flush=True,
                    )
                last_heartbeat = now
        except Exception as exc:
            print("[paper_sim] cycle_error", str(exc), flush=True)
        time.sleep(max(5, poll_seconds))


def main() -> None:
    run_forever(poll_seconds=20)


if __name__ == "__main__":
    main()
