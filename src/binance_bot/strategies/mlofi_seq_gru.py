from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .base import Signal, Strategy


@dataclass(slots=True)
class MlofiSeqGruStrategy(Strategy):
    hidden_size: int = 24
    num_layers: int = 1
    dropout: float = 0.0
    epochs: int = 8
    batch_size: int = 256
    learning_rate: float = 1.0e-3
    weight_decay: float = 1.0e-5
    min_confidence: float = 0.45
    random_state: int = 42
    seq_len: int = 8
    _model: Any = field(default=None, init=False, repr=False)
    _torch: Any = field(default=None, init=False, repr=False)
    _device: str = field(default="cpu", init=False, repr=False)

    @property
    def name(self) -> str:
        return "mlofi_seq_gru_v1"

    def _build_sequence(self, row: dict[str, Any]) -> list[list[float]]:
        seq: list[list[float]] = []
        for lag in range(self.seq_len, 0, -1):
            seq.append(
                [
                    float(row.get(f"mlofi_score_lag{lag}", 0.0) or 0.0),
                    float(row.get(f"spread_lag{lag}", 0.0) or 0.0),
                ]
            )
        seq.append(
            [
                float(row.get("mlofi_score", 0.0) or 0.0),
                float(row.get("spread", 0.0) or 0.0),
            ]
        )
        return seq

    def fit(self, rows: list[dict[str, Any]]) -> None:
        try:
            import numpy as np
            import torch
            import torch.nn as nn
            from torch.utils.data import DataLoader, TensorDataset
        except Exception:
            self._model = None
            self._torch = None
            return

        x: list[list[list[float]]] = []
        y: list[int] = []
        for row in rows:
            target = row.get("target_direction")
            if target not in (-1, 0, 1):
                continue
            x.append(self._build_sequence(row))
            y.append(int(target) + 1)
        if len(x) < 300:
            self._model = None
            self._torch = torch
            return

        torch.manual_seed(int(self.random_state))
        np.random.seed(int(self.random_state))
        self._device = "cuda" if torch.cuda.is_available() else "cpu"

        x_np = np.asarray(x, dtype=np.float32)
        y_np = np.asarray(y, dtype=np.int64)
        x_tensor = torch.from_numpy(x_np)
        y_tensor = torch.from_numpy(y_np)
        ds = TensorDataset(x_tensor, y_tensor)
        dl = DataLoader(ds, batch_size=max(32, int(self.batch_size)), shuffle=True)

        class SeqGruNet(nn.Module):
            def __init__(self, hidden_size: int, num_layers: int, dropout: float) -> None:
                super().__init__()
                self.gru = nn.GRU(
                    input_size=2,
                    hidden_size=hidden_size,
                    num_layers=num_layers,
                    batch_first=True,
                    dropout=dropout if num_layers > 1 else 0.0,
                )
                self.head = nn.Sequential(
                    nn.LayerNorm(hidden_size),
                    nn.Linear(hidden_size, 3),
                )

            def forward(self, x_in: Any) -> Any:
                out, _ = self.gru(x_in)
                last = out[:, -1, :]
                return self.head(last)

        model = SeqGruNet(
            hidden_size=int(self.hidden_size),
            num_layers=int(self.num_layers),
            dropout=float(self.dropout),
        ).to(self._device)
        loss_fn = nn.CrossEntropyLoss()
        opt = torch.optim.AdamW(
            model.parameters(),
            lr=float(self.learning_rate),
            weight_decay=float(self.weight_decay),
        )

        model.train()
        for _ in range(max(1, int(self.epochs))):
            for xb, yb in dl:
                xb = xb.to(self._device)
                yb = yb.to(self._device)
                logits = model(xb)
                loss = loss_fn(logits, yb)
                opt.zero_grad(set_to_none=True)
                loss.backward()
                opt.step()

        model.eval()
        self._model = model
        self._torch = torch

    def predict(self, row: dict[str, Any]) -> Signal:
        if self._model is None or self._torch is None:
            return Signal(
                action_intent="none",
                confidence=0.0,
                signal_score=0.0,
                entry_reason="model_not_fitted",
                metadata={"strategy": self.name},
            )
        torch = self._torch
        seq = self._build_sequence(row)
        x = torch.tensor([seq], dtype=torch.float32, device=self._device)
        with torch.no_grad():
            logits = self._model(x)
            probs_t = torch.softmax(logits, dim=1)[0]
            probs = [float(v) for v in probs_t.detach().cpu().tolist()]
        pred_cls = int(max(range(len(probs)), key=lambda i: probs[i]))
        confidence = float(probs[pred_cls])
        direction = pred_cls - 1
        if confidence < self.min_confidence:
            action = "none"
            reason = "confidence_below_threshold"
        elif direction > 0:
            action = "long"
            reason = "seq_gru_predict_long"
        elif direction < 0:
            action = "short"
            reason = "seq_gru_predict_short"
        else:
            action = "none"
            reason = "seq_gru_predict_flat"
        score = float(probs[2] - probs[0]) if len(probs) == 3 else 0.0
        return Signal(
            action_intent=action,
            confidence=confidence,
            signal_score=score,
            entry_reason=reason,
            metadata={"strategy": self.name, "probs": probs},
        )
