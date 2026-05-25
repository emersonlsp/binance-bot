# Status ML: Pipeline Atual (Collector + XGB/Seq + Margin Rules)

## O que jÃ¡ temos

- Coletor Binance com `snapshots`, `updates`, `trades` e `collector_logs`.
- Sync incremental por bloco com estado (watermark) para acelerar ingestÃ£o.
- Features de microestrutura (MLOFI), labels sem vazamento e walk-forward com embargo.
- SimulaÃ§Ã£o de execuÃ§Ã£o realista: latÃªncia, no-fill, partial fill e slippage.
- Sizing por risco com banca dinÃ¢mica por trade.
- SL/TP adaptativo por volatilidade com piso de payoff (`tp >= stop * rr`).
- Busca de candidatos com XGBoost + modelos sequenciais (MLP/GRU).
- PromoÃ§Ã£o condicionada por baseline e estabilidade por fold.

## Regras de Margin incorporadas na pipeline

As regras operacionais/financeiras de margin estÃ£o padronizadas em:

- `docs/binance_margin_rules.md`

Pontos obrigatÃ³rios para precificaÃ§Ã£o e operaÃ§Ã£o:

1. PnL lÃ­quido deve incluir taxa, slippage e juros de borrow.
2. Antes de abrir trade em margin: validar `maxBorrowable`.
3. Tratar erros operacionais (`-3006`, `-3007`, `-3045`, filtros de notional).
4. Reconciliar liability (principal + juros) apÃ³s fechamento.
5. Promover estratÃ©gia apenas por mÃ©tricas lÃ­quidas consistentes.

## CritÃ©rio de aceitaÃ§Ã£o para candidato "bom"

- `mean_pnl_net_brl > 0`
- `total_trades` mÃ­nimo aceitÃ¡vel
- drawdown e expectancy dentro do limite
- estabilidade por fold e por regime
- qualidade de execuÃ§Ã£o aceitÃ¡vel (`fill_rate`, `cancel_rate`, slippage)

## PrÃ³ximo foco operacional

- Coletar mais dados.
- Rodar mais candidatos por seed com validaÃ§Ã£o OOS.
- Manter pipeline estÃ¡vel (sem alterar lÃ³gica central de treino sem necessidade).

