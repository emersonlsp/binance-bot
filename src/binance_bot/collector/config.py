from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(slots=True)
class CollectorConfig:
    exchange: str = "binance"
    symbol: str = "BTCBRL"
    depth_levels: int = 10
    snapshot_interval_seconds: int = 2
    reconnect_delay_seconds: int = 3
    flush_interval_seconds: int = 5
    max_rows_per_file: int = 10_000
    base_data_dir: Path = Path("data/raw")
    binance_api_key: str = ""
    binance_api_secret: str = ""
    recv_window_ms: int = 5000
    update_gap_seconds_threshold: int = 10
    snapshot_gap_seconds_threshold: int = 120

    @classmethod
    def from_env(cls) -> "CollectorConfig":
        return cls(
            exchange=os.getenv("EXCHANGE", "binance"),
            symbol=os.getenv("SYMBOL", "BTCBRL"),
            depth_levels=int(os.getenv("DEPTH_LEVELS", "10")),
            snapshot_interval_seconds=int(os.getenv("SNAPSHOT_INTERVAL_SECONDS", "2")),
            reconnect_delay_seconds=int(os.getenv("RECONNECT_DELAY_SECONDS", "3")),
            flush_interval_seconds=int(os.getenv("FLUSH_INTERVAL_SECONDS", "5")),
            max_rows_per_file=int(os.getenv("MAX_ROWS_PER_FILE", "10000")),
            base_data_dir=Path(os.getenv("BASE_DATA_DIR", "data/raw")),
            binance_api_key=os.getenv("BINANCE_API_KEY", ""),
            binance_api_secret=os.getenv("BINANCE_API_SECRET", ""),
            recv_window_ms=int(os.getenv("RECV_WINDOW_MS", "5000")),
            update_gap_seconds_threshold=int(os.getenv("UPDATE_GAP_SECONDS_THRESHOLD", "10")),
            snapshot_gap_seconds_threshold=int(
                os.getenv("SNAPSHOT_GAP_SECONDS_THRESHOLD", "120")
            ),
        )
