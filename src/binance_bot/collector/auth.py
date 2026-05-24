from __future__ import annotations

import hashlib
import hmac
import time
from urllib.parse import urlencode


class BinanceRequestSigner:
    def __init__(self, api_key: str, api_secret: str, recv_window_ms: int = 5000) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.recv_window_ms = recv_window_ms

    @property
    def enabled(self) -> bool:
        return bool(self.api_key and self.api_secret)

    def build_signed_params(self, params: dict[str, object] | None = None) -> dict[str, object]:
        payload = dict(params or {})
        payload["timestamp"] = int(time.time() * 1000)
        payload["recvWindow"] = self.recv_window_ms
        query = urlencode(payload, doseq=True)
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        payload["signature"] = signature
        return payload

    def headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        return {"X-MBX-APIKEY": self.api_key}

