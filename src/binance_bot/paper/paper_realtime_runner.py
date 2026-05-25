from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..collector.binance_spot import BinanceSpotClient
from ..collector.config import CollectorConfig
from ..collector.env_loader import load_dotenv
from ..paper.config import load_paper_mode_config
from ..runtime.params import load_trading_params
from ..strategies.registry import create_strategy
from ..training.paper_aligned import build_paper_aligned_dataset


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


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
        "losses": int(state.get("losses", 0)),
        "win_rate": win_rate,
        "max_drawdown_brl": float(state.get("max_drawdown_brl", 0.0)),
        "open_position": state.get("open_position"),
    }


def _fit_strategy(champion: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
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
    if len(labeled) < 200:
        raise RuntimeError("Not enough historical data to fit strategy.")
    strategy = create_strategy(strategy_name, **strategy_kwargs)
    strategy.fit(labeled)
    return strategy, params


def _build_feature_from_book(
    *,
    ts_receive: str,
    symbol: str,
    session_id: str,
    bid_book: dict[float, float],
    ask_book: dict[float, float],
    levels: int = 10,
) -> dict[str, Any] | None:
    bids = sorted(bid_book.items(), key=lambda x: x[0], reverse=True)[:levels]
    asks = sorted(ask_book.items(), key=lambda x: x[0])[:levels]
    if not bids or not asks:
        return None
    best_bid, best_bid_size = float(bids[0][0]), float(bids[0][1])
    best_ask, best_ask_size = float(asks[0][0]), float(asks[0][1])
    if best_ask < best_bid:
        return None
    spread = best_ask - best_bid
    mid = (best_bid + best_ask) / 2.0
    row: dict[str, Any] = {
        "ts_receive": ts_receive,
        "symbol": symbol,
        "session_id": session_id,
        "mid_price": mid,
        "spread": spread,
        "bid_price_l1": best_bid,
        "ask_price_l1": best_ask,
        "bid_size_l1": best_bid_size,
        "ask_size_l1": best_ask_size,
    }
    imbalances: list[float] = []
    for i in range(levels):
        bid_price = float(bids[i][0]) if i < len(bids) else 0.0
        bid_size = float(bids[i][1]) if i < len(bids) else 0.0
        ask_price = float(asks[i][0]) if i < len(asks) else 0.0
        ask_size = float(asks[i][1]) if i < len(asks) else 0.0
        row[f"bid_price_l{i+1}"] = bid_price
        row[f"bid_size_l{i+1}"] = bid_size
        row[f"ask_price_l{i+1}"] = ask_price
        row[f"ask_size_l{i+1}"] = ask_size
        denom = bid_size + ask_size
        imbalance = 0.0 if denom <= 0 else (bid_size - ask_size) / denom
        row[f"mlofi_l{i+1}"] = imbalance
        imbalances.append(imbalance)
    row["mlofi_score"] = sum(imbalances) / len(imbalances) if imbalances else 0.0
    return row


def _open_position(state: dict[str, Any], side: str, entry: float, params: dict[str, Any], ts: str) -> dict[str, Any]:
    trading = load_trading_params()
    stop_loss_pct = float(params.get("effective_stop_loss_pct", trading.risk.default_stop_loss_pct))
    tp_pct = float(params.get("effective_take_profit_pct", stop_loss_pct * trading.risk.risk_reward_ratio))
    raw_notional = state["bankroll_brl"] * trading.risk.order_notional_pct
    min_notional = max(0.0, float(getattr(trading.risk, "min_position_notional_brl", 0.0)))
    max_notional = max(0.0, float(getattr(trading.risk, "max_position_notional_brl", 0.0)))
    risk_cap_notional = (state["bankroll_brl"] * trading.risk.max_risk_per_trade_pct) / max(stop_loss_pct, 1.0e-12)
    position_notional = raw_notional
    if min_notional > 0:
        position_notional = max(position_notional, min_notional)
    if max_notional > 0:
        position_notional = min(position_notional, max_notional)
    position_notional = min(position_notional, risk_cap_notional)
    qty = position_notional / max(entry, 1.0e-12)
    risk_brl = position_notional * stop_loss_pct
    if side == "long":
        sl = entry * (1.0 - stop_loss_pct)
        tp = entry * (1.0 + tp_pct)
    else:
        sl = entry * (1.0 + stop_loss_pct)
        tp = entry * (1.0 - tp_pct)
    pos = {
        "side": side,
        "entry_price": entry,
        "stop_loss": sl,
        "take_profit": tp,
        "risk_brl": risk_brl,
        "raw_notional_brl": raw_notional,
        "position_notional_brl": position_notional,
        "qty": qty,
        "opened_at": ts,
        "meta": {"params_snapshot": params},
    }
    state["open_position"] = pos
    return pos


def _try_close_position(state: dict[str, Any], feature: dict[str, Any]) -> dict[str, Any] | None:
    pos = state.get("open_position")
    if not pos:
        return None
    side = pos["side"]
    sl = float(pos["stop_loss"])
    tp = float(pos["take_profit"])
    entry = float(pos["entry_price"])
    bid = _safe_float(feature.get("bid_price_l1"), 0.0)
    ask = _safe_float(feature.get("ask_price_l1"), 0.0)
    ts = str(feature.get("ts_receive", ""))
    if bid <= 0 or ask <= 0:
        return None
    exit_price = bid if side == "long" else ask
    should_close = False
    exit_reason = ""
    if side == "long":
        if bid <= sl:
            should_close = True
            exit_reason = "stop_loss"
            exit_price = sl
        elif bid >= tp:
            should_close = True
            exit_reason = "take_profit"
            exit_price = tp
    else:
        if ask >= sl:
            should_close = True
            exit_reason = "stop_loss"
            exit_price = sl
        elif ask <= tp:
            should_close = True
            exit_reason = "take_profit"
            exit_price = tp
    if not should_close:
        return None
    trading = load_trading_params()
    cost = 2.0 * (float(trading.risk.taker_fee_bps_per_side) / 10000.0)
    trade_ret = ((exit_price - entry) / entry) if side == "long" else ((entry - exit_price) / entry)
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
    state["max_drawdown_brl"] = max(float(state.get("max_drawdown_brl", 0.0)), peak - state["bankroll_brl"])
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


async def run_forever() -> None:
    load_dotenv()
    champion = _load_champion()
    strategy, params = _fit_strategy(champion)
    sample_every = int(params.get("sample_every_updates", 40))
    min_conf = float(params.get("min_signal_confidence", 0.75))
    max_spread = float(params.get("max_spread_brl", 3.0))

    out = Path("artifacts/paper_sim")
    state_path = out / "paper_report.json"
    signals_path = out / "signals.jsonl"
    trades_path = out / "trades.jsonl"
    trading = load_trading_params()
    paper_cfg = load_paper_mode_config()
    state = _load_state(state_path, trading.risk.paper_bankroll_brl)
    state["open_position"] = None
    _save_state(state_path, _state_summary(state))
    print("[paper_rt] started", _now_iso(), "bankroll=", state["bankroll_brl"], flush=True)

    cfg = CollectorConfig.from_env()
    client = BinanceSpotClient(cfg)
    bid_book: dict[float, float] = {}
    ask_book: dict[float, float] = {}
    updates_seen = 0
    mid_history: list[float] = []
    last_heartbeat = 0.0
    session_id = f"paper-rt-{datetime.now(UTC):%Y%m%dT%H%M%S}"

    while True:
        try:
            async for event in client.stream_updates_and_trades():
                event_happened = False
                if event.get("stream") != "updates":
                    now = time.time()
                    if now - last_heartbeat >= 10.0:
                        print("[paper_rt] analisando mercado... sem novo evento", flush=True)
                        last_heartbeat = now
                    continue
                side = str(event.get("side", ""))
                px = _safe_float(event.get("price"), 0.0)
                sz = _safe_float(event.get("size"), 0.0)
                if px <= 0:
                    continue
                book = bid_book if side == "bid" else ask_book
                if sz <= 0:
                    book.pop(px, None)
                else:
                    book[px] = sz
                updates_seen += 1
                if updates_seen % max(1, sample_every) != 0:
                    continue

                feature = _build_feature_from_book(
                    ts_receive=str(event.get("ts_receive", "")),
                    symbol=str(event.get("symbol", cfg.symbol)),
                    session_id=session_id,
                    bid_book=bid_book,
                    ask_book=ask_book,
                    levels=10,
                )
                if feature is None:
                    continue
                ts = str(feature.get("ts_receive", ""))
                if not ts or ts == state.get("last_feature_ts", ""):
                    continue
                state["last_feature_ts"] = ts
                mid = _safe_float(feature.get("mid_price"), 0.0)
                if mid > 0:
                    mid_history.append(mid)
                    if len(mid_history) > 300:
                        mid_history = mid_history[-300:]
                sig = strategy.predict(feature)
                _append_jsonl(
                    signals_path,
                    {
                        "ts": ts,
                        "signal": sig.action_intent,
                        "confidence": sig.confidence,
                        "score": sig.signal_score,
                        "mid_price": feature.get("mid_price"),
                        "spread": feature.get("spread"),
                        "bid": feature.get("bid_price_l1"),
                        "ask": feature.get("ask_price_l1"),
                    },
                )

                closed = _try_close_position(state, feature)
                if closed is not None:
                    event_happened = True
                    _append_jsonl(trades_path, closed)
                    summary = _state_summary(state)
                    print(
                        f"[paper_rt] close {closed['side']} {closed['exit_reason']} pnl={closed['pnl_brl']:.2f} bankroll={closed['bankroll_after_brl']:.2f}",
                        flush=True,
                    )
                    print(
                        f"[paper_rt] summary trades={summary['trades_count']} win_rate={summary['win_rate']:.2%} pnl={summary['realized_pnl_brl']:.2f} return={summary['return_pct']:.2%} dd={summary['max_drawdown_brl']:.2f}",
                        flush=True,
                    )

                if state.get("open_position") is None and sig.action_intent in ("long", "short"):
                    spread = _safe_float(feature.get("spread"), 0.0)
                    if sig.confidence >= min_conf and spread <= max_spread:
                        bid = _safe_float(feature.get("bid_price_l1"), 0.0)
                        ask = _safe_float(feature.get("ask_price_l1"), 0.0)
                        entry = ask if sig.action_intent == "long" else bid
                        l1_qty = _safe_float(feature.get("ask_size_l1" if sig.action_intent == "long" else "bid_size_l1"), 0.0)
                        eff_stop = trading.risk.default_stop_loss_pct
                        eff_tp = eff_stop * trading.risk.risk_reward_ratio
                        if paper_cfg.position_rules.stop_mode == "volatility" and len(mid_history) >= 20:
                            lb = max(10, int(paper_cfg.position_rules.vol_lookback_steps))
                            tail = mid_history[-lb:]
                            rets: list[float] = []
                            for i in range(1, len(tail)):
                                if tail[i - 1] > 0:
                                    rets.append((tail[i] - tail[i - 1]) / tail[i - 1])
                            if rets:
                                mean = sum(rets) / len(rets)
                                var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
                                vol = var**0.5
                                eff_stop = max(
                                    float(paper_cfg.position_rules.min_stop_loss_pct),
                                    min(
                                        float(paper_cfg.position_rules.max_stop_loss_pct),
                                        float(paper_cfg.position_rules.vol_stop_k) * vol,
                                    ),
                                )
                                eff_tp = max(eff_stop * 0.5, float(paper_cfg.position_rules.vol_tp_k) * vol)
                        exec_params = dict(params)
                        exec_params["effective_stop_loss_pct"] = float(eff_stop)
                        exec_params["effective_take_profit_pct"] = float(eff_tp)
                        draft = _open_position(state, sig.action_intent, entry, exec_params, ts)
                        state["open_position"] = None
                        req_qty = _safe_float(draft.get("qty"), 0.0)
                        if entry > 0 and l1_qty >= req_qty and req_qty > 0:
                            pos = _open_position(state, sig.action_intent, entry, exec_params, ts)
                            event_happened = True
                            print(
                                f"[paper_rt] open {pos['side']} ts={ts} entry={entry:.2f} bid={bid:.2f} ask={ask:.2f} qty={req_qty:.6f}",
                                flush=True,
                            )
                        else:
                            event_happened = True
                            print(
                                f"[paper_rt] ioc_canceled ts={ts} side={sig.action_intent} entry={entry:.2f} qty={req_qty:.6f} l1_qty={l1_qty:.6f}",
                                flush=True,
                            )
                _save_state(state_path, _state_summary(state))

                now = time.time()
                if (not event_happened) and (now - last_heartbeat >= 10.0):
                    open_pos = state.get("open_position")
                    if open_pos is None:
                        print("[paper_rt] analisando mercado... sem novo evento", flush=True)
                    else:
                        print(
                            f"[paper_rt] trade aberta side={open_pos['side']} entry={float(open_pos['entry_price']):.2f} sl={float(open_pos['stop_loss']):.2f} tp={float(open_pos['take_profit']):.2f}",
                            flush=True,
                        )
                    last_heartbeat = now
        except Exception as exc:
            print("[paper_rt] stream_error", str(exc), flush=True)
            await asyncio.sleep(2)


def main() -> None:
    asyncio.run(run_forever())


if __name__ == "__main__":
    main()
