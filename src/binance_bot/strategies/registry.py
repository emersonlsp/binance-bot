from __future__ import annotations

from typing import Any

from .baselines import ImbalanceBaselineStrategy, NoTradeBaselineStrategy
from .base import Strategy
from .mlofi_xgb import MlofiXgbStrategy


def create_strategy(name: str, **kwargs: Any) -> Strategy:
    normalized = name.strip().lower()
    if normalized == "baseline_no_trade_v1":
        return NoTradeBaselineStrategy(**kwargs)
    if normalized == "baseline_imbalance_v1":
        return ImbalanceBaselineStrategy(**kwargs)
    if normalized == "mlofi_xgb_v1":
        return MlofiXgbStrategy(**kwargs)
    raise ValueError(f"Unknown strategy '{name}'")
