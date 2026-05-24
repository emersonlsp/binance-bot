from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Archive current active champion artifact.")
    p.add_argument(
        "--champion-file",
        type=Path,
        default=Path("artifacts/champion_strategy_xgb_clean.json"),
        help="Active champion artifact path.",
    )
    p.add_argument(
        "--archive-dir",
        type=Path,
        default=Path("artifacts/champions_archive"),
        help="Archive directory.",
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()
    src = args.champion_file
    if not src.exists():
        raise FileNotFoundError(f"Champion artifact not found: {src}")
    payload = json.loads(src.read_text(encoding="utf-8"))
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    name = str(payload.get("name", "champion"))
    out = args.archive_dir / f"{ts}_{name}.json"
    args.archive_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[champion] archived: {out}")


if __name__ == "__main__":
    main()

