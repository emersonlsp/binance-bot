from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Signal:
    action_intent: str
    confidence: float
    signal_score: float
    entry_reason: str
    metadata: dict[str, Any]


class Strategy(ABC):
    @abstractmethod
    def fit(self, rows: list[dict[str, Any]]) -> None: ...

    @abstractmethod
    def predict(self, row: dict[str, Any]) -> Signal: ...

    @property
    @abstractmethod
    def name(self) -> str: ...

