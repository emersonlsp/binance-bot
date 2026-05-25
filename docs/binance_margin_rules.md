# Binance Margin Rules (Precificacao e Operacao)

Este documento define o que o bot precisa para **precificar, executar e controlar risco** em trades de margin na Binance.

Objetivo: evitar PnL ilusorio (sem juros/sem restricoes reais) e reduzir erros operacionais.

## 1. Escopo de produto

- Modos suportados:
  - Cross Margin (`isIsolated=FALSE`)
  - Isolated Margin (`isIsolated=TRUE`, requer `symbol`)
- Mercado-alvo atual: scalping (nao manter posicao por dias).

## 2. Endpoints criticos

- Borrow/Repay:
  - `POST /sapi/v1/margin/borrow-repay` (`type=BORROW|REPAY`)
  - `GET /sapi/v1/margin/borrow-repay` (historico)
  - `GET /sapi/v1/margin/maxBorrowable` (capacidade real)
  - `GET /sapi/v1/margin/next-hourly-interest-rate` (juros futuros por hora)
- Trade:
  - `POST /sapi/v1/margin/order`
  - `sideEffectType`: `NO_SIDE_EFFECT`, `MARGIN_BUY`, `AUTO_REPAY`, `AUTO_BORROW_REPAY`

Referencias oficiais:
- https://developers.binance.com/docs/margin_trading/borrow-and-repay/Margin-Account-Borrow-Repay
- https://developers.binance.com/docs/margin_trading/borrow-and-repay/Query-Borrow-Repay
- https://developers.binance.com/docs/margin_trading/borrow-and-repay/Query-Max-Borrow
- https://developers.binance.com/docs/margin_trading/trade/Margin-Account-New-Order

## 3. Precificacao correta de uma trade margin

Para cada trade, o PnL liquido deve considerar:

- PnL de preco (entrada/saida)
- Taxa de execucao (maker/taker)
- Slippage (entrada/saida)
- Juros de emprestimo (liability no periodo)
- Custos de falha parcial/no-fill (quando aplicavel)

Formula simplificada:

`pnl_liquido = pnl_preco - taxa_execucao - slippage - juros_borrow`

## 4. Sizing por risco (regra obrigatoria)

Risco monetario por trade:

`risk_amount = bankroll * max_risk_per_trade_pct`

Tamanho de posicao:

`position_notional = risk_amount / stop_loss_pct_efetivo`

Quantidade:

`qty = position_notional / preco_entrada`

Notas:
- `stop_loss_pct_efetivo` pode ser fixo ou adaptativo por volatilidade.
- TP define payoff esperado; nao define risco inicial.

## 5. Regras de SL/TP para scalping

- Preferencia: stop/target adaptativos por volatilidade com limites.
- Piso de payoff obrigatorio:
  - `tp_pct >= stop_pct * max(1.0, risk_reward_ratio)`
- Time stop para scalping:
  - encerrar posicao se exceder janela maxima (minutos), evitando drift para swing.

## 6. Capacidade de emprestimo e pre-trade checks

Antes de abrir posicao com borrow:

1. Consultar `maxBorrowable` do ativo necessario.
2. Calcular notional/qty viavel com folga.
3. Validar filtros da corretora (ex.: notional minimo, precision, lot size).
4. Se capacidade insuficiente: reduzir tamanho ou cancelar sinal.

## 7. Modo de execucao (sideEffectType)

Recomendacao:

- Fluxo simples (menos chamadas): usar `margin/order` com `AUTO_BORROW_REPAY` quando fizer sentido.
- Fluxo controlado (mais previsivel): borrow/repay manual e ordem com `NO_SIDE_EFFECT`.

Observacoes:
- Chamadas com borrow/auto-borrow possuem peso alto (rate-limit).
- Em alta frequencia, evitar excesso de borrow/repay por ordem.

## 8. Juros e liability

O bot deve:

- Registrar principal e juros por posicao.
- Reconciliar passivo apos fechamento (principal + juros).
- Tratar residual pequeno de divida (`FEW_LIABILITY_LEFT`) com rotina de ajuste.

No backtest/paper:
- Simular juros por tempo em posicao para aproximar do real.

## 9. Erros operacionais que devem virar regra

Erros importantes:

- `-3045 INSUFFICIENT_INVENTORY`: sem estoque de emprestimo no sistema.
- `-3006 EXCEED_MAX_BORROWABLE`: excedeu capacidade.
- `-3007 HAS_PENDING_TRANSACTION`: borrow/repay concorrente.
- `-3015 REPAY_EXCEED_LIABILITY` / residual de liability.
- `Filter failure: NOTIONAL` em ordem.

Tratamento:

1. Retry com backoff curto em `-3007`.
2. Recalcular tamanho em `-3006`/`NOTIONAL`.
3. Bloquear ativo temporariamente em `-3045`.
4. Reconciliar liability antes de nova entrada.

## 10. Estado minimo por trade (auditoria)

Persistir por trade:

- `trade_id`, `symbol`, `mode` (cross/isolated)
- `sideEffectType`
- `entry/exit ts`, preco, qty
- `borrow_principal`, `interest_paid`, `repay_amount`
- `fees`, `slippage`
- `stop_pct`, `tp_pct`, `risk_amount`, `bankroll_before/after`
- `exit_reason` (tp/sl/time/forced/risk)

## 11. Gate de promocao de estrategia (go/no-go)

Uma estrategia so promove se:

1. PnL liquido > 0 apos taxas + slippage + juros
2. Drawdown dentro do limite
3. Fill rate minimo e cancel rate aceitavel
4. Estabilidade por fold e por regime
5. Sem erros operacionais recorrentes de borrow/repay

## 12. Checklist de implementacao no bot

- [ ] Pre-trade `maxBorrowable` integrado
- [ ] Sizing por risco com stop efetivo
- [ ] TP com piso de payoff (>= 1:1)
- [ ] Juros de borrow no PnL
- [ ] Retry/backoff para `HAS_PENDING_TRANSACTION`
- [ ] Tratamento de `INSUFFICIENT_INVENTORY` e `EXCEED_MAX_BORROWABLE`
- [ ] Reconciliacao de liability apos fechamento
- [ ] Telemetria completa de trade margin
- [ ] Relatorio com PnL liquido realista (inclui juros)

