from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import Signal, Strategy


@dataclass(slots=True)
class MlofiSeqMlpStrategy(Strategy):
    hidden_layer_sizes: tuple[int, int] = (64, 32)
    alpha: float = 1.0e-4
    learning_rate_init: float = 1.0e-3
    max_iter: int = 200
    min_confidence: float = 0.45
    random_state: int = 42
    _model: Any = field(default=None, init=False, repr=False)
    _features: list[str] = field(default_factory=list, init=False, repr=False)

    @property
    def name(self) -> str:
        return "mlofi_seq_mlp_v1"

    def _build_features(self, row: dict[str, Any]) -> list[float]:
        vals: list[float] = []
        for key in self._features:
            try:
                vals.append(float(row.get(key, 0.0)))
            except (TypeError, ValueError):
                vals.append(0.0)
        return vals

    def fit(self, rows: list[dict[str, Any]]) -> None:
        from sklearn.neural_network import MLPClassifier

        self._features = [f"mlofi_l{i}" for i in range(1, 11)] + ["mlofi_score", "spread"]
        for lag in range(1, 9):
            self._features.append(f"mlofi_score_lag{lag}")
            self._features.append(f"spread_lag{lag}")
        x: list[list[float]] = []
        y: list[int] = []
        for row in rows:
            target = row.get("target_direction")
            if target not in (-1, 0, 1):
                continue
            x.append(self._build_features(row))
            y.append(int(target) + 1)
        if len(x) < 100:
            self._model = None
            return
        self._model = MLPClassifier(
            hidden_layer_sizes=self.hidden_layer_sizes,
            alpha=self.alpha,
            learning_rate_init=self.learning_rate_init,
            max_iter=self.max_iter,
            random_state=self.random_state,
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
        direction = pred_cls - 1
        if confidence < self.min_confidence:
            action = "none"
            reason = "confidence_below_threshold"
        elif direction > 0:
            action = "long"
            reason = "seq_mlp_predict_long"
        elif direction < 0:
            action = "short"
            reason = "seq_mlp_predict_short"
        else:
            action = "none"
            reason = "seq_mlp_predict_flat"
        score = float(probs[2] - probs[0]) if len(probs) == 3 else 0.0
        return Signal(
            action_intent=action,
            confidence=confidence,
            signal_score=score,
            entry_reason=reason,
            metadata={"strategy": self.name, "probs": [float(p) for p in probs]},
        )

