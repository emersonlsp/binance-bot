from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import websockets

from .auth import BinanceRequestSigner
from .config import CollectorConfig
from .schemas import normalize_symbol, utc_now_iso


class BinanceSpotClient:
    REST_BASE = "https://api.binance.com"
    WS_BASE = "wss://stream.binance.com:9443/stream"

    def __init__(self, config: CollectorConfig) -> None:
        self.config = config
        self.symbol = normalize_symbol(config.symbol)
        self.stream_symbol = self.symbol.lower()
        self.signer = BinanceRequestSigner(
            api_key=config.binance_api_key,
            api_secret=config.binance_api_secret,
            recv_window_ms=config.recv_window_ms,
        )

    async def public_request(
        self, method: str, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.request(
                method=method.upper(),
                url=f"{self.REST_BASE}{path}",
                params=params,
            )
            response.raise_for_status()
            return response.json()

    async def signed_request(
        self, method: str, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if not self.signer.enabled:
            raise RuntimeError(
                "Binance API credentials are not configured. Set BINANCE_API_KEY and BINANCE_API_SECRET."
            )
        signed_params = self.signer.build_signed_params(params)
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.request(
                method=method.upper(),
                url=f"{self.REST_BASE}{path}",
                params=signed_params,
                headers=self.signer.headers(),
            )
            response.raise_for_status()
            return response.json()

    async def fetch_snapshot(self) -> dict[str, Any]:
        params = {"symbol": self.symbol, "limit": self.config.depth_levels}
        payload = await self.public_request("GET", "/api/v3/depth", params=params)
        now = datetime.now(UTC)
        bids = payload.get("bids", [])
        asks = payload.get("asks", [])
        best_bid_price = float(bids[0][0]) if bids else None
        best_ask_price = float(asks[0][0]) if asks else None
        spread = (
            best_ask_price - best_bid_price
            if best_ask_price is not None and best_bid_price is not None
            else None
        )
        mid_price = (
            (best_bid_price + best_ask_price) / 2
            if best_ask_price is not None and best_bid_price is not None
            else None
        )
        return {
            "ts_event": utc_now_iso(),
            "ts_receive": now.isoformat(),
            "exchange": self.config.exchange,
            "symbol": self.symbol,
            "last_update_id": payload.get("lastUpdateId"),
            "best_bid_price": best_bid_price,
            "best_bid_size": float(bids[0][1]) if bids else None,
            "best_ask_price": best_ask_price,
            "best_ask_size": float(asks[0][1]) if asks else None,
            "mid_price": mid_price,
            "spread": spread,
            "bids": bids[: self.config.depth_levels],
            "asks": asks[: self.config.depth_levels],
        }

    async def stream_updates_and_trades(self) -> AsyncIterator[dict[str, Any]]:
        stream = f"{self.stream_symbol}@depth@100ms/{self.stream_symbol}@trade"
        url = f"{self.WS_BASE}?streams={stream}"
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            async for raw_message in ws:
                receive_ts = datetime.now(UTC).isoformat()
                message = json.loads(raw_message)
                data = message.get("data", {})
                stream_name = message.get("stream", "")
                if stream_name.endswith("@trade"):
                    yield self._normalize_trade(data, receive_ts)
                    continue
                if "@depth" in stream_name:
                    for row in self._normalize_depth_update(data, receive_ts):
                        yield row

    def _normalize_trade(self, data: dict[str, Any], ts_receive: str) -> dict[str, Any]:
        return {
            "stream": "trades",
            "ts_event": datetime.fromtimestamp(data["T"] / 1000, tz=UTC).isoformat(),
            "ts_receive": ts_receive,
            "exchange": self.config.exchange,
            "symbol": self.symbol,
            "trade_id": data.get("t"),
            "price": float(data["p"]),
            "size": float(data["q"]),
            "aggressor_side": "sell" if data.get("m") else "buy",
        }

    def _normalize_depth_update(
        self, data: dict[str, Any], ts_receive: str
    ) -> list[dict[str, Any]]:
        ts_event = datetime.fromtimestamp(data["E"] / 1000, tz=UTC).isoformat()
        rows: list[dict[str, Any]] = []
        for price, size in data.get("b", []):
            rows.append(
                {
                    "stream": "updates",
                    "ts_event": ts_event,
                    "ts_receive": ts_receive,
                    "exchange": self.config.exchange,
                    "symbol": self.symbol,
                    "sequence_start": data.get("U"),
                    "sequence_end": data.get("u"),
                    "side": "bid",
                    "price": float(price),
                    "size": float(size),
                    "action": "upsert",
                }
            )
        for price, size in data.get("a", []):
            rows.append(
                {
                    "stream": "updates",
                    "ts_event": ts_event,
                    "ts_receive": ts_receive,
                    "exchange": self.config.exchange,
                    "symbol": self.symbol,
                    "sequence_start": data.get("U"),
                    "sequence_end": data.get("u"),
                    "side": "ask",
                    "price": float(price),
                    "size": float(size),
                    "action": "upsert",
                }
            )
        return rows

    async def fetch_account_info(self) -> dict[str, Any]:
        return await self.signed_request("GET", "/api/v3/account")


async def forever(coro_factory, delay_seconds: int) -> None:
    while True:
        try:
            await coro_factory()
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(delay_seconds)
