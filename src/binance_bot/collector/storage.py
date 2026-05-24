from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


class ParquetBufferWriter:
    def __init__(
        self,
        base_dir: Path,
        exchange: str,
        symbol: str,
        max_rows_per_file: int = 10_000,
    ) -> None:
        self.base_dir = base_dir
        self.exchange = exchange
        self.symbol = symbol
        self.max_rows_per_file = max_rows_per_file
        self._buffers: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._flush_counter: dict[str, int] = defaultdict(int)

    def add(self, stream: str, row: dict[str, Any]) -> None:
        self._buffers[stream].append(row)
        if len(self._buffers[stream]) >= self.max_rows_per_file:
            self.flush_stream(stream)

    def flush_all(self) -> None:
        for stream in list(self._buffers.keys()):
            self.flush_stream(stream)

    def flush_stream(self, stream: str) -> None:
        rows = self._buffers.get(stream, [])
        if not rows:
            return
        now = datetime.now(UTC)
        path = (
            self.base_dir
            / self.exchange
            / self.symbol
            / stream
            / f"{now:%Y}"
            / f"{now:%m}"
            / f"{now:%d}"
        )
        path.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pylist(rows)
        self._flush_counter[stream] += 1
        file_path = path / (
            f"{self.symbol}_{stream}_{now:%Y%m%d_%H%M%S}_{self._flush_counter[stream]:06d}.parquet"
        )
        pq.write_table(table, file_path)
        self._buffers[stream].clear()
