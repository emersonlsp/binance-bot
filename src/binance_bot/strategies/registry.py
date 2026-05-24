from __future__ import annotations

from typing import Any

from .base import Strategy
from .mlofi_xgb import MlofiXgbStrategy


def create_strategy(name: str, **kwargs: Any) -> Strategy:
    normalized = name.strip().lower()
    if normalized == "mlofi_xgb_v1":
        return MlofiXgbStrategy(**kwargs)
    raise ValueError(f"Unknown strategy '{name}'")
