from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


TS_RE = re.compile(r"_(\d{8}_\d{6})_\d+\.parquet$", re.IGNORECASE)


@dataclass(slots=True)
class SyncStats:
    copied: int = 0
    skipped_existing: int = 0
    scanned: int = 0


def _parse_file_ts(path: Path) -> datetime | None:
    m = TS_RE.search(path.name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y%m%d_%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _stream_day_dirs(stream_src: Path) -> list[Path]:
    out: list[Path] = []
    for y in sorted(stream_src.glob("[0-9][0-9][0-9][0-9]")):
        if not y.is_dir():
            continue
        for m in sorted(y.glob("[0-1][0-9]")):
            if not m.is_dir():
                continue
            for d in sorted(m.glob("[0-3][0-9]")):
                if d.is_dir():
                    out.append(d)
    return out


def _day_dir_date(day_dir: Path) -> datetime | None:
    try:
        y = int(day_dir.parent.parent.name)
        m = int(day_dir.parent.name)
        d = int(day_dir.name)
        return datetime(y, m, d, tzinfo=UTC)
    except Exception:
        return None


def _copy_stream_incremental(
    stream_name: str,
    stream_src: Path,
    stream_dst: Path,
    state_ts_iso: str | None,
    safety_hours: int,
) -> tuple[SyncStats, str | None]:
    stats = SyncStats()
    last_ts: datetime | None = None
    if state_ts_iso:
        try:
            last_ts = datetime.fromisoformat(state_ts_iso.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            last_ts = None
    floor = (last_ts - timedelta(hours=max(0, safety_hours))) if last_ts else None

    day_dirs = _stream_day_dirs(stream_src)
    if floor is not None:
        floor_day = datetime(floor.year, floor.month, floor.day, tzinfo=UTC)
        day_dirs = [d for d in day_dirs if (_day_dir_date(d) or floor_day) >= floor_day]

    max_seen_ts = last_ts
    for day_dir in day_dirs:
        rel_day = day_dir.relative_to(stream_src)
        dst_day = stream_dst / rel_day
        dst_day.mkdir(parents=True, exist_ok=True)
        for src_file in sorted(day_dir.glob("*.parquet")):
            stats.scanned += 1
            fts = _parse_file_ts(src_file)
            if floor is not None and fts is not None and fts < floor:
                continue
            dst_file = dst_day / src_file.name
            if dst_file.exists():
                stats.skipped_existing += 1
            else:
                shutil.copy2(src_file, dst_file)
                stats.copied += 1
            if fts is not None and (max_seen_ts is None or fts > max_seen_ts):
                max_seen_ts = fts
    next_state = max_seen_ts.isoformat() if max_seen_ts is not None else state_ts_iso
    return stats, next_state


def sync_tree(
    src_root: Path,
    dst_root: Path,
    state_file: Path,
    safety_hours: int,
) -> int:
    if not src_root.exists():
        print(f"[sync-block] source_not_found: {src_root}")
        return 0
    state = _read_json(state_file)
    streams_state = dict(state.get("streams", {}))
    streams = [p for p in sorted(src_root.iterdir()) if p.is_dir()]
    total_copied = 0
    total_scanned = 0
    total_skipped_existing = 0
    for stream_dir in streams:
        stream = stream_dir.name
        dst_stream = dst_root / stream
        prev_ts = streams_state.get(stream)
        stats, next_ts = _copy_stream_incremental(
            stream,
            stream_dir,
            dst_stream,
            prev_ts,
            safety_hours=safety_hours,
        )
        streams_state[stream] = next_ts
        total_copied += stats.copied
        total_scanned += stats.scanned
        total_skipped_existing += stats.skipped_existing
        print(
            f"[sync-block] stream={stream} scanned={stats.scanned} "
            f"copied={stats.copied} exists={stats.skipped_existing} last_ts={next_ts}"
        )
    _write_json(
        state_file,
        {
            "updated_at_utc": datetime.now(UTC).isoformat(),
            "src_root": str(src_root),
            "dst_root": str(dst_root),
            "streams": streams_state,
            "safety_hours": safety_hours,
        },
    )
    print(
        f"[sync-block] done scanned={total_scanned} copied={total_copied} exists={total_skipped_existing}"
    )
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Incremental block sync with per-stream watermark.")
    p.add_argument("--src", type=Path, required=True)
    p.add_argument("--dst", type=Path, required=True)
    p.add_argument("--state", type=Path, required=True)
    p.add_argument("--safety-hours", type=int, default=24)
    args = p.parse_args()
    raise SystemExit(sync_tree(args.src, args.dst, args.state, args.safety_hours))


if __name__ == "__main__":
    main()

