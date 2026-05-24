from __future__ import annotations

import argparse
import json
from pathlib import Path


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Promote a chosen candidate JSON as active champion.")
    p.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Source candidate/champion JSON file to promote.",
    )
    p.add_argument(
        "--target",
        type=Path,
        default=Path("artifacts/champion_strategy_xgb_clean.json"),
        help="Active champion output path.",
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()
    if not args.source.exists():
        raise FileNotFoundError(f"Source file not found: {args.source}")
    payload = json.loads(args.source.read_text(encoding="utf-8"))
    args.target.parent.mkdir(parents=True, exist_ok=True)
    args.target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"[champion] promoted: {args.source} -> {args.target}")


if __name__ == "__main__":
    main()

