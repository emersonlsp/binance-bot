# Binance Margin

## Objetivo

Resumo de como funciona trading com margem na Binance e o que considerar no bot.

## Conceitos básicos

- `Cross Margin`: saldo e risco compartilhados na conta de margem.
- `Isolated Margin`: risco isolado por par/símbolo.
- `Borrow`: empréstimo de ativo para alavancar.
- `Repay`: devolução do principal + juros.
- `Liquidation`: fechamento forçado quando risco ultrapassa limite.

## Fluxo operacional de margem

1. Transferir fundos para conta de margem.
2. (Opcional) emprestar ativo (`borrow`).
3. Enviar ordem de compra/venda.
4. Monitorar risco (margin level, juros, posição).
5. Fechar posição.
6. Pagar empréstimo (`repay`).

## Custos adicionais em margem

- Juros do ativo emprestado.
- Taxas de negociação normais.
- Slippage e spread.
- Risco de liquidação em movimentos extremos.

## Riscos principais

- Alavancagem amplifica perdas.
- Liquidação pode ocorrer antes do stop esperado.
- Eventos de alta volatilidade aumentam slippage.
- Custos de juros corroem trades longos.

## Recomendações para bot

- Começar em `isolated margin`.
- Definir limite de exposição por símbolo.
- Limitar alavancagem efetiva.
- Bloquear novas entradas em regime `high_vol_shock`.
- Considerar custo de juros no PnL esperado.
- Manter kill switch por drawdown diário.

## Endpoints (categoria Margin)

Na documentação de Margin Trading da Binance, os grupos principais são:

- `Market Data`
- `Borrow And Repay`
- `Trade`
- `Transfer`
- `Account`
- `Trade Data Stream`
- `Risk Data Stream`

## Política prática de banca inicial

Para início real com margem:

- usar somente uma fração da banca total na conta margin
- risco por trade baixo (ex.: `0.25%` a `0.50%` da banca)
- evitar alavancagem alta no início

Sugestão operacional inicial:

- paper: manter como está para validação de processo
- live inicial: pequena, foco em robustez operacional, não em retorno

