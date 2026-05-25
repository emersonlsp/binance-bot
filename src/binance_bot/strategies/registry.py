from __future__ import annotations

from typing import Any

from .baselines import ImbalanceBaselineStrategy, NoTradeBaselineStrategy
from .base import Strategy
from .mlofi_seq_gru import MlofiSeqGruStrategy
from .mlofi_seq_mlp import MlofiSeqMlpStrategy
from .mlofi_xgb import MlofiXgbStrategy


def create_strategy(name: str, **kwargs: Any) -> Strategy:
    normalized = name.strip().lower()
    if normalized == "baseline_no_trade_v1":
        return NoTradeBaselineStrategy(**kwargs)
    if normalized == "baseline_imbalance_v1":
        return ImbalanceBaselineStrategy(**kwargs)
    if normalized == "mlofi_xgb_v1":
        return MlofiXgbStrategy(**kwargs)
    if normalized == "mlofi_seq_mlp_v1":
        return MlofiSeqMlpStrategy(**kwargs)
    if normalized == "mlofi_seq_gru_v1":
        return MlofiSeqGruStrategy(**kwargs)
    raise ValueError(f"Unknown strategy '{name}'")
