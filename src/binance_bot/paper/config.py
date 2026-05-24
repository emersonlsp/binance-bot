from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class PaperExecutionConfig:
    mode: str
    fill_model: str
    slippage_bps_per_side: float
    max_open_positions: int


@dataclass(slots=True)
class PaperRiskConfig:
    min_signal_confidence: float
    max_spread_brl: float
    allow_short: bool


@dataclass(slots=True)
class PaperPositionRules:
    time_stop_minutes: int
    trailing_stop_enabled: bool
    trailing_activation_rr: float
    trailing_distance_rr: float
    trailing_lock_breakeven: bool
    disable_take_profit_when_trailing: bool


@dataclass(slots=True)
class PaperStorageConfig:
    base_dir: Path


@dataclass(slots=True)
class PaperModeConfig:
    enabled: bool
    symbol: str
    champion_file: Path
    execution: PaperExecutionConfig
    risk: PaperRiskConfig
    position_rules: PaperPositionRules
    storage: PaperStorageConfig


def load_paper_mode_config(path: Path | None = None) -> PaperModeConfig:
    cfg_path = path or Path("config/paper_mode.json")
    payload = json.loads(cfg_path.read_text(encoding="utf-8"))
    return PaperModeConfig(
        enabled=bool(payload.get("enabled", False)),
        symbol=str(payload.get("symbol", "BTCBRL")),
        champion_file=Path(
            payload.get("strategy_source", {}).get(
                "champion_file", "artifacts/champion_strategy.json"
            )
        ),
        execution=PaperExecutionConfig(
            mode=str(payload.get("execution", {}).get("mode", "simulated")),
            fill_model=str(payload.get("execution", {}).get("fill_model", "mid_plus_slippage")),
            slippage_bps_per_side=float(
                payload.get("execution", {}).get("slippage_bps_per_side", 1.0)
            ),
            max_open_positions=int(payload.get("execution", {}).get("max_open_positions", 1)),
        ),
        risk=PaperRiskConfig(
            min_signal_confidence=float(payload.get("risk", {}).get("min_signal_confidence", 0.55)),
            max_spread_brl=float(payload.get("risk", {}).get("max_spread_brl", 20.0)),
            allow_short=bool(payload.get("risk", {}).get("allow_short", True)),
        ),
        position_rules=PaperPositionRules(
            time_stop_minutes=int(payload.get("position_rules", {}).get("time_stop_minutes", 120)),
            trailing_stop_enabled=bool(
                payload.get("position_rules", {}).get("trailing_stop_enabled", True)
            ),
            trailing_activation_rr=float(
                payload.get("position_rules", {}).get("trailing_activation_rr", 1.0)
            ),
            trailing_distance_rr=float(
                payload.get("position_rules", {}).get("trailing_distance_rr", 0.75)
            ),
            trailing_lock_breakeven=bool(
                payload.get("position_rules", {}).get("trailing_lock_breakeven", True)
            ),
            disable_take_profit_when_trailing=bool(
                payload.get("position_rules", {}).get(
                    "disable_take_profit_when_trailing", True
                )
            ),
        ),
        storage=PaperStorageConfig(
            base_dir=Path(payload.get("storage", {}).get("base_dir", "artifacts/paper_mode"))
        ),
    )
