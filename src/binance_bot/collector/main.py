from __future__ import annotations

import asyncio
import signal
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime

from .binance_spot import BinanceSpotClient
from .config import CollectorConfig
from .env_loader import load_dotenv
from .quality import GapMonitor
from .storage import ParquetBufferWriter


@dataclass(slots=True)
class RuntimeStats:
    snapshots: int = 0
    updates: int = 0
    trades: int = 0
    quality_warnings: int = 0
    quality_errors: int = 0
    ws_errors: int = 0
    snapshot_errors: int = 0
    last_event_ts: str = "-"
    started_at: datetime = datetime.now(UTC)


def print_status(stats: RuntimeStats, symbol: str) -> None:
    uptime_s = int((datetime.now(UTC) - stats.started_at).total_seconds())
    print(
        (
            f"[collector] symbol={symbol} uptime={uptime_s}s "
            f"snapshots={stats.snapshots} updates={stats.updates} trades={stats.trades} "
            f"q_warn={stats.quality_warnings} q_err={stats.quality_errors} "
            f"ws_err={stats.ws_errors} snap_err={stats.snapshot_errors} "
            f"last_ts={stats.last_event_ts}"
        ),
        flush=True,
    )


def log_event(
    writer: ParquetBufferWriter,
    symbol: str,
    session_id: str,
    event_type: str,
    message: str,
    severity: str = "info",
) -> None:
    writer.add(
        "collector_logs",
        {
            "ts_event": datetime.now(UTC).isoformat(),
            "exchange": "binance",
            "symbol": symbol,
            "session_id": session_id,
            "event_type": event_type,
            "severity": severity,
            "message": message,
        },
    )


async def run_collector(config: CollectorConfig) -> None:
    session_id = f"{config.symbol.lower()}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    client = BinanceSpotClient(config)
    writer = ParquetBufferWriter(
        base_dir=config.base_data_dir,
        exchange=config.exchange,
        symbol=config.symbol,
        max_rows_per_file=config.max_rows_per_file,
    )
    quality = GapMonitor(
        update_gap_seconds_threshold=config.update_gap_seconds_threshold,
        snapshot_gap_seconds_threshold=config.snapshot_gap_seconds_threshold,
    )
    stats = RuntimeStats(started_at=datetime.now(UTC))
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()
    if config.binance_api_key and config.binance_api_secret:
        log_event(
            writer, config.symbol, session_id, "auth_configured", "api credentials found in environment"
        )
    else:
        log_event(
            writer,
            config.symbol,
            session_id,
            "auth_not_configured",
            "running public market-data mode without signed endpoints",
        )
    log_event(
        writer,
        config.symbol,
        session_id,
        "collector_start",
        f"collector session started: {session_id}",
    )

    for sig in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop_event.set)

    async def snapshot_loop() -> None:
        while not stop_event.is_set():
            try:
                snapshot = await client.fetch_snapshot()
                snapshot["session_id"] = session_id
                writer.add("snapshots", snapshot)
                stats.snapshots += 1
                stats.last_event_ts = str(snapshot.get("ts_receive", "-"))
                log_event(writer, config.symbol, session_id, "snapshot_ok", "snapshot collected")
                for event in quality.on_snapshot(snapshot):
                    if event.severity == "warning":
                        stats.quality_warnings += 1
                    if event.severity == "error":
                        stats.quality_errors += 1
                    print(f"[quality:{event.severity}] {event.message}", flush=True)
                    log_event(
                        writer,
                        config.symbol,
                        session_id,
                        event.event_type,
                        event.message,
                        severity=event.severity,
                    )
            except Exception as exc:
                stats.snapshot_errors += 1
                print(f"[snapshot:error] {exc}", flush=True)
                log_event(
                    writer, config.symbol, session_id, "snapshot_error", str(exc), severity="error"
                )
            await asyncio.sleep(config.snapshot_interval_seconds)

    async def stream_loop() -> None:
        while not stop_event.is_set():
            try:
                log_event(writer, config.symbol, session_id, "ws_connect", "connecting websocket")
                async for event in client.stream_updates_and_trades():
                    stream = event.pop("stream")
                    event["session_id"] = session_id
                    writer.add(stream, event)
                    stats.last_event_ts = str(event.get("ts_receive", "-"))
                    if stream == "updates":
                        stats.updates += 1
                    elif stream == "trades":
                        stats.trades += 1
                    if stream == "updates":
                        for quality_event in quality.on_update(event):
                            if quality_event.severity == "warning":
                                stats.quality_warnings += 1
                            if quality_event.severity == "error":
                                stats.quality_errors += 1
                            print(
                                f"[quality:{quality_event.severity}] {quality_event.message}",
                                flush=True,
                            )
                            log_event(
                                writer,
                                config.symbol,
                                session_id,
                                quality_event.event_type,
                                quality_event.message,
                                severity=quality_event.severity,
                            )
                    if stop_event.is_set():
                        break
            except Exception as exc:
                stats.ws_errors += 1
                print(f"[ws:error] {exc}", flush=True)
                log_event(writer, config.symbol, session_id, "ws_error", str(exc), severity="error")
                await asyncio.sleep(config.reconnect_delay_seconds)

    async def flush_loop() -> None:
        while not stop_event.is_set():
            await asyncio.sleep(config.flush_interval_seconds)
            writer.flush_all()

    async def status_loop() -> None:
        while not stop_event.is_set():
            await asyncio.sleep(10)
            print_status(stats, config.symbol)

    tasks = [
        asyncio.create_task(snapshot_loop(), name="snapshot_loop"),
        asyncio.create_task(stream_loop(), name="stream_loop"),
        asyncio.create_task(flush_loop(), name="flush_loop"),
        asyncio.create_task(status_loop(), name="status_loop"),
    ]
    await stop_event.wait()
    log_event(
        writer,
        config.symbol,
        session_id,
        "collector_stop",
        f"collector session stopping: {session_id}",
    )
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    writer.flush_all()


def main() -> None:
    load_dotenv(".env")
    config = CollectorConfig.from_env()
    asyncio.run(run_collector(config))


if __name__ == "__main__":
    main()
