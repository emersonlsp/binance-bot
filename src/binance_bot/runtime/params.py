from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class RiskParams:
    max_risk_per_trade_pct: float = 0.01
    default_stop_loss_pct: float = 0.005
    risk_reward_ratio: float = 1.0
    margin_mode: str = "isolated"
    paper_bankroll_brl: float = 500.0
    order_notional_pct: float = 0.01
    min_position_notional_brl: float = 50.0
    max_position_notional_brl: float = 3000.0


@dataclass(slots=True)
class TradingParams:
    risk: RiskParams


def load_trading_params(path: Path | None = None) -> TradingParams:
    cfg_path = path or Path("config/trading_params.json")
    if not cfg_path.exists():
        return TradingParams(risk=RiskParams())
    payload: dict[str, Any] = json.loads(cfg_path.read_text(encoding="utf-8"))
    risk = payload.get("risk", {})
    return TradingParams(
        risk=RiskParams(
            max_risk_per_trade_pct=float(risk.get("max_risk_per_trade_pct", 0.01)),
            default_stop_loss_pct=float(risk.get("default_stop_loss_pct", 0.005)),
            risk_reward_ratio=float(risk.get("risk_reward_ratio", 1.0)),
            margin_mode=str(risk.get("margin_mode", "isolated")).lower(),
            paper_bankroll_brl=float(risk.get("paper_bankroll_brl", 500.0)),
            order_notional_pct=float(risk.get("order_notional_pct", 0.01)),
            min_position_notional_brl=float(risk.get("min_position_notional_brl", 50.0)),
            max_position_notional_brl=float(risk.get("max_position_notional_brl", 3000.0)),
        )
    )
