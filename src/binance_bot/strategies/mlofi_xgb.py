from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import Signal, Strategy


@dataclass(slots=True)
class MlofiXgbStrategy(Strategy):
    n_estimators: int = 200
    max_depth: int = 4
    learning_rate: float = 0.05
    subsample: float = 0.9
    colsample_bytree: float = 0.9
    reg_lambda: float = 1.0
    min_confidence: float = 0.45
    n_jobs: int = 4
    _model: Any = field(default=None, init=False, repr=False)
    _features: list[str] = field(default_factory=list, init=False, repr=False)

    @property
    def name(self) -> str:
        return "mlofi_xgb_v1"

    def _build_features(self, row: dict[str, Any]) -> list[float]:
        vals: list[float] = []
        for key in self._features:
            val = row.get(key, 0.0)
            try:
                vals.append(float(val))
            except (TypeError, ValueError):
                vals.append(0.0)
        return vals

    def fit(self, rows: list[dict[str, Any]]) -> None:
        from xgboost import XGBClassifier

        self._features = [f"mlofi_l{i}" for i in range(1, 11)] + ["mlofi_score", "spread"]
        x: list[list[float]] = []
        y: list[int] = []
        for row in rows:
            target = row.get("target_direction")
            if target not in (-1, 0, 1):
                continue
            x.append(self._build_features(row))
            # Map {-1,0,1} -> {0,1,2}
            y.append(int(target) + 1)
        if len(x) < 50:
            self._model = None
            return
        self._model = XGBClassifier(
            objective="multi:softprob",
            num_class=3,
            n_estimators=self.n_estimators,
            max_depth=self.max_depth,
            learning_rate=self.learning_rate,
            subsample=self.subsample,
            colsample_bytree=self.colsample_bytree,
            reg_lambda=self.reg_lambda,
            eval_metric="mlogloss",
            random_state=42,
            n_jobs=self.n_jobs,
        )
        self._model.fit(x, y)

    def predict(self, row: dict[str, Any]) -> Signal:
        if self._model is None:
            return Signal(
                action_intent="none",
                confidence=0.0,
                signal_score=0.0,
                entry_reason="model_not_fitted",
                metadata={"strategy": self.name},
            )
        x = [self._build_features(row)]
        probs = self._model.predict_proba(x)[0]
        pred_cls = int(max(range(len(probs)), key=lambda i: probs[i]))
        confidence = float(probs[pred_cls])
        # Map {0,1,2} -> {-1,0,1}
        direction = pred_cls - 1
        if confidence < self.min_confidence:
            action = "none"
            reason = "confidence_below_threshold"
        elif direction > 0:
            action = "long"
            reason = "xgb_predict_long"
        elif direction < 0:
            action = "short"
            reason = "xgb_predict_short"
        else:
            action = "none"
            reason = "xgb_predict_flat"
        score = float(probs[2] - probs[0]) if len(probs) == 3 else 0.0
        return Signal(
            action_intent=action,
            confidence=confidence,
            signal_score=score,
            entry_reason=reason,
            metadata={"strategy": self.name, "probs": [float(p) for p in probs]},
        )
