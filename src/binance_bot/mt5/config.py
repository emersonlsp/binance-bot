from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(slots=True)
class Mt5CollectorConfig:
    login: int
    password: str
    server: str
    terminal_path: str | None
    symbol: str
    timeframes: list[str]
    candles_per_timeframe: int
    out_root: Path


def _must_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def load_mt5_config() -> Mt5CollectorConfig:
    login_raw = _must_env("MT5_LOGIN")
    try:
        login = int(login_raw)
    except ValueError as exc:
        raise RuntimeError("MT5_LOGIN must be an integer.") from exc
    password = _must_env("MT5_PASSWORD")
    server = _must_env("MT5_SERVER")
    terminal_path = os.getenv("MT5_PATH", "").strip() or None
    symbol = os.getenv("MT5_SYMBOL", "BTCUSD").strip().upper()
    tfs = os.getenv("MT5_TIMEFRAMES", "M1,M5,M15,H1").strip()
    timeframes = [x.strip().upper() for x in tfs.split(",") if x.strip()]
    if not timeframes:
        timeframes = ["M1", "M5", "M15", "H1"]
    candles_raw = os.getenv("MT5_CANDLES_PER_TIMEFRAME", "20000").strip()
    try:
        candles_per_timeframe = max(500, int(candles_raw))
    except ValueError as exc:
        raise RuntimeError("MT5_CANDLES_PER_TIMEFRAME must be an integer.") from exc
    return Mt5CollectorConfig(
        login=login,
        password=password,
        server=server,
        terminal_path=terminal_path,
        symbol=symbol,
        timeframes=timeframes,
        candles_per_timeframe=candles_per_timeframe,
        out_root=Path("data/raw/mt5"),
    )

