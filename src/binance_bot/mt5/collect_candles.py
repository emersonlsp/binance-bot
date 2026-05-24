from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from ..collector.env_loader import load_dotenv
from .config import Mt5CollectorConfig, load_mt5_config
from .timeframes import resolve_mt5_timeframe


def _initialize_mt5(mt5: Any, cfg: Mt5CollectorConfig) -> None:
    ok = mt5.initialize(
        path=cfg.terminal_path,
        login=cfg.login,
        password=cfg.password,
        server=cfg.server,
    )
    if not ok:
        raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")


def _fetch_rates(mt5: Any, cfg: Mt5CollectorConfig, timeframe: str) -> list[dict[str, Any]]:
    tf_value = resolve_mt5_timeframe(mt5, timeframe)
    rates = mt5.copy_rates_from_pos(cfg.symbol, tf_value, 0, cfg.candles_per_timeframe)
    if rates is None:
        raise RuntimeError(
            f"copy_rates_from_pos returned None for {cfg.symbol}/{timeframe}. last_error={mt5.last_error()}"
        )
    out: list[dict[str, Any]] = []
    for r in rates:
        ts_open = datetime.fromtimestamp(int(r["time"]), UTC)
        out.append(
            {
                "symbol": cfg.symbol,
                "timeframe": timeframe,
                "ts_open": ts_open.isoformat(),
                "open": float(r["open"]),
                "high": float(r["high"]),
                "low": float(r["low"]),
                "close": float(r["close"]),
                "tick_volume": float(r["tick_volume"]),
                "spread_points": float(r["spread"]),
                "real_volume": float(r["real_volume"]),
                "source": "mt5",
                "collected_at_utc": datetime.now(UTC).isoformat(),
            }
        )
    return out


def _write_rows(rows: list[dict[str, Any]], out_file: Path) -> None:
    out_file.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), out_file)


def main() -> None:
    load_dotenv()
    cfg = load_mt5_config()
    try:
        import MetaTrader5 as mt5
    except ImportError as exc:
        raise RuntimeError("MetaTrader5 package is not installed. pip install MetaTrader5") from exc
    _initialize_mt5(mt5, cfg)
    try:
        if not mt5.symbol_select(cfg.symbol, True):
            raise RuntimeError(f"Could not select MT5 symbol: {cfg.symbol}")
        day = datetime.now(UTC).strftime("%Y%m%d")
        for tf in cfg.timeframes:
            rows = _fetch_rates(mt5, cfg, tf)
            out_file = cfg.out_root / cfg.symbol / "candles" / tf / f"{cfg.symbol}_{tf}_{day}.parquet"
            _write_rows(rows, out_file)
            print(f"[mt5_candles] {tf} rows={len(rows)} file={out_file}")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()

