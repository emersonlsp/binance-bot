from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(slots=True)
class QualityEvent:
    event_type: str
    message: str
    severity: str


class GapMonitor:
    def __init__(self, update_gap_seconds_threshold: int, snapshot_gap_seconds_threshold: int) -> None:
        self.update_gap_seconds_threshold = update_gap_seconds_threshold
        self.snapshot_gap_seconds_threshold = snapshot_gap_seconds_threshold
        self.last_update_receive_dt: datetime | None = None
        self.last_snapshot_receive_dt: datetime | None = None
        self.last_sequence_end: int | None = None

    @staticmethod
    def _parse_iso(ts: str) -> datetime:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(UTC)

    def on_snapshot(self, snapshot: dict[str, object]) -> list[QualityEvent]:
        events: list[QualityEvent] = []
        ts_receive = str(snapshot["ts_receive"])
        receive_dt = self._parse_iso(ts_receive)
        if self.last_snapshot_receive_dt is not None:
            gap_seconds = (receive_dt - self.last_snapshot_receive_dt).total_seconds()
            if gap_seconds > self.snapshot_gap_seconds_threshold:
                events.append(
                    QualityEvent(
                        event_type="quality_snapshot_gap",
                        severity="warning",
                        message=(
                            f"snapshot receive gap detected: {gap_seconds:.2f}s "
                            f"(threshold={self.snapshot_gap_seconds_threshold}s)"
                        ),
                    )
                )
        self.last_snapshot_receive_dt = receive_dt
        return events

    def on_update(self, update: dict[str, object]) -> list[QualityEvent]:
        events: list[QualityEvent] = []
        ts_receive = str(update["ts_receive"])
        receive_dt = self._parse_iso(ts_receive)
        if self.last_update_receive_dt is not None:
            gap_seconds = (receive_dt - self.last_update_receive_dt).total_seconds()
            if gap_seconds > self.update_gap_seconds_threshold:
                events.append(
                    QualityEvent(
                        event_type="quality_update_gap",
                        severity="warning",
                        message=(
                            f"update receive gap detected: {gap_seconds:.2f}s "
                            f"(threshold={self.update_gap_seconds_threshold}s)"
                        ),
                    )
                )
        sequence_start = update.get("sequence_start")
        sequence_end = update.get("sequence_end")
        if isinstance(sequence_start, int) and isinstance(sequence_end, int):
            if self.last_sequence_end is not None and sequence_start > (self.last_sequence_end + 1):
                events.append(
                    QualityEvent(
                        event_type="quality_sequence_gap",
                        severity="error",
                        message=(
                            f"sequence gap detected: previous_end={self.last_sequence_end}, "
                            f"current_start={sequence_start}, current_end={sequence_end}"
                        ),
                    )
                )
            self.last_sequence_end = sequence_end
        self.last_update_receive_dt = receive_dt
        return events

