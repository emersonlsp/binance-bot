# Status ML: Collector + XGBoost Clean

## O que já temos

- Coletor com snapshots, updates, trades e `collector_logs` com eventos de qualidade.
- Pipeline com construção de features de book (MLOFI), labels sem vazamento e validação walk-forward.
- Simulação de PnL líquido com custo e regras de risco.
- Busca de candidatos XGBoost (`xgb_clean`) com paralelismo e cache.
- Filtro de vencedores por `mean_pnl_net_brl > 0` em `winners_latest.json`.

## Lacunas vs plano

- Falta baseline explícito e versionado (ex.: `no-trade`, `imbalance simples`) para comparar ML.
- Falta separação formal de camadas `processed/training` com versionamento por run.
- Falta persistência por execução (timestamp) para evitar sobrescrever `leaderboard`.
- Falta gap temporal explícito entre treino e teste (purge/embargo).
- Falta avaliação de execução mais realista (fill parcial, no-fill, latência por ordem).

## Próximo passo recomendado (prioridade 1)

Implementar **baseline pack + avaliação comparativa**:

1. Baseline `no-trade` (PnL = 0).
2. Baseline `imbalance_topN` simples.
3. Relatório único comparando XGBoost vs baselines após custo.

Critério de avanço:
- XGBoost só promove se superar baselines em PnL líquido e estabilidade por fold.

## Ajuste tático para próxima run

- Manter filtro primário: `mean_pnl_net_brl > 0`.
- Aplicar filtro secundário de robustez na decisão final (ex.: `total_trades >= 40`).
