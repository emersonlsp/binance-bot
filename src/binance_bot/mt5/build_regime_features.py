from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, pstdev
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq


def _load_latest_candles(root: Path, symbol: str, timeframe: str) -> list[dict[str, Any]]:
    tf_root = root / symbol / "candles" / timeframe
    files = sorted(tf_root.glob("*.parquet"))
    if not files:
        return []
    rows = pq.read_table(files[-1]).to_pylist()
    rows.sort(key=lambda r: str(r.get("ts_open", "")))
    return rows


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1.0)
    out = [values[0]]
    for v in values[1:]:
        out.append((alpha * v) + ((1.0 - alpha) * out[-1]))
    return out


def _build_m1_regime_features(m1_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(m1_rows) < 80:
        return []
    closes = [float(r["close"]) for r in m1_rows]
    highs = [float(r["high"]) for r in m1_rows]
    lows = [float(r["low"]) for r in m1_rows]
    ema21 = _ema(closes, 21)
    ema50 = _ema(closes, 50)
    out: list[dict[str, Any]] = []
    for i in range(60, len(m1_rows)):
        c = closes[i]
        ret_5 = (c / closes[i - 5]) - 1.0
        ret_15 = (c / closes[i - 15]) - 1.0
        ret_60 = (c / closes[i - 60]) - 1.0
        tr_window = [highs[j] - lows[j] for j in range(i - 13, i + 1)]
        atr14 = mean(tr_window)
        atr_pct = (atr14 / c) if c > 0 else 0.0
        vol_window = [((closes[j] / closes[j - 1]) - 1.0) for j in range(i - 19, i + 1)]
        rv20 = pstdev(vol_window) if len(vol_window) > 1 else 0.0
        slope21 = (ema21[i] / ema21[i - 5]) - 1.0 if ema21[i - 5] != 0 else 0.0
        slope50 = (ema50[i] / ema50[i - 10]) - 1.0 if ema50[i - 10] != 0 else 0.0

        if atr_pct > 0.0045 and abs(ret_5) > 0.003:
            regime = "high_vol_shock"
        elif slope50 > 0.0015 and ret_60 > 0.002:
            regime = "trend_up"
        elif slope50 < -0.0015 and ret_60 < -0.002:
            regime = "trend_down"
        else:
            regime = "chop"

        out.append(
            {
                "ts_open": m1_rows[i]["ts_open"],
                "symbol": m1_rows[i]["symbol"],
                "timeframe": "M1",
                "close": c,
                "return_5": ret_5,
                "return_15": ret_15,
                "return_60": ret_60,
                "atr14": atr14,
                "atr_pct": atr_pct,
                "realized_vol_20": rv20,
                "ema21": ema21[i],
                "ema50": ema50[i],
                "ema_slope_21_5": slope21,
                "ema_slope_50_10": slope50,
                "regime_label": regime,
            }
        )
    return out


def main() -> None:
    root = Path("data/raw/mt5")
    symbol = "BTCUSD"
    m1_rows = _load_latest_candles(root, symbol, "M1")
    if not m1_rows:
        raise RuntimeError("No MT5 M1 candle files found. Run mt5.collect_candles first.")
    features = _build_m1_regime_features(m1_rows)
    if not features:
        raise RuntimeError("Not enough M1 candles to build regime features.")
    out_dir = Path("data/processed/mt5") / symbol / "regime"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"regime_features_{datetime.now(UTC):%Y%m%d}.parquet"
    pq.write_table(pa.Table.from_pylist(features), out_file)
    report = Path("artifacts/reports/mt5_regime_summary.json")
    report.parent.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for row in features:
        k = str(row["regime_label"])
        counts[k] = counts.get(k, 0) + 1
    payload = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "symbol": symbol,
        "rows": len(features),
        "regime_counts": counts,
        "output": str(out_file),
    }
    report.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("[mt5_regime] rows=", len(features), "output=", out_file)


if __name__ == "__main__":
    main()
