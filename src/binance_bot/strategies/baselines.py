from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import Signal, Strategy


@dataclass(slots=True)
class NoTradeBaselineStrategy(Strategy):
    @property
    def name(self) -> str:
        return "baseline_no_trade_v1"

    def fit(self, rows: list[dict[str, Any]]) -> None:
        _ = rows

    def predict(self, row: dict[str, Any]) -> Signal:
        _ = row
        return Signal(
            action_intent="none",
            confidence=1.0,
            signal_score=0.0,
            entry_reason="baseline_no_trade",
            metadata={"strategy": self.name},
        )


@dataclass(slots=True)
class ImbalanceBaselineStrategy(Strategy):
    long_threshold: float = 0.08
    short_threshold: float = -0.08

    @property
    def name(self) -> str:
        return "baseline_imbalance_v1"

    def fit(self, rows: list[dict[str, Any]]) -> None:
        _ = rows

    def predict(self, row: dict[str, Any]) -> Signal:
        score = float(row.get("mlofi_score", 0.0))
        if score >= self.long_threshold:
            action = "long"
            reason = "imbalance_long"
            confidence = min(1.0, max(0.0, abs(score)))
        elif score <= self.short_threshold:
            action = "short"
            reason = "imbalance_short"
            confidence = min(1.0, max(0.0, abs(score)))
        else:
            action = "none"
            reason = "imbalance_flat"
            confidence = 0.0
        return Signal(
            action_intent=action,
            confidence=confidence,
            signal_score=score,
            entry_reason=reason,
            metadata={"strategy": self.name},
        )

