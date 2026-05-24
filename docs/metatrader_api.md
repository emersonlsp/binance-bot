# MetaTrader API Integration Guide

Documento genérico para integrar qualquer bot ao MetaTrader 5, com foco prático em:
- conexão com terminal e conta
- leitura de mercado e execução
- modelo de custos
- particularidades da Tickmill para `BTCUSD`

Data de referência deste documento: `2026-04-23`.

## 1. Escopo

Este guia cobre uma integração de bot com `MetaTrader 5` via:
- terminal desktop do MT5
- API Python oficial `MetaTrader5`
- execução por `market execution`
- leitura de ticks, candles, ordens, posições e histórico
- cálculo de custos de spread, swap, slippage e, quando aplicável, comissão

Não depende de um bot específico. A ideia é servir como contrato técnico para qualquer engine de execução.

## 2. Fontes oficiais recomendadas

- MetaTrader 5 Python Integration:
  [https://www.mql5.com/en/docs/integration/python_metatrader5](https://www.mql5.com/en/docs/integration/python_metatrader5)
- MetaTrader Python / MetaEditor docs:
  [https://www.metatrader5.com/en/metaeditor/help/development/python](https://www.metatrader5.com/en/metaeditor/help/development/python)
- Tickmill spreads and swaps:
  [https://www.tickmill.com/conditions/spreads-swaps](https://www.tickmill.com/conditions/spreads-swaps)
- Tickmill BTCUSD contract specs:
  [https://www.tickmill.com/pt/instruments/btcusd](https://www.tickmill.com/pt/instruments/btcusd)
- Tickmill Raw account:
  [https://www.tickmill.com/trading/raw-account](https://www.tickmill.com/trading/raw-account)

## 3. Arquitetura mínima recomendada

Separe a integração em 6 módulos:

1. `terminal_connector`
- inicia e fecha conexão com o terminal MT5
- faz login na conta
- expõe `terminal_info`, `account_info`, `last_error`

2. `symbol_registry`
- resolve símbolo correto no broker
- lê `symbol_info`
- mantém specs normalizadas por símbolo

3. `market_data_gateway`
- lê ticks
- lê candles
- gera snapshots de bid/ask/last/spread

4. `execution_gateway`
- envia ordens
- valida ordens com `order_check`
- consulta posições, ordens ativas e histórico

5. `cost_model`
- calcula spread cost
- calcula swap
- calcula comissão
- estima slippage

6. `risk_layer`
- valida volume mínimo, step e máximo
- valida margem
- impõe limites por símbolo, drawdown, exposição e horário

## 4. Fluxo operacional recomendado

1. Inicializar terminal MT5.
2. Logar na conta.
3. Confirmar conexão com servidor.
4. Resolver símbolo real do broker.
5. Ler `symbol_info` e congelar uma cópia local das specs do símbolo.
6. Validar que o símbolo está visível em `MarketWatch`.
7. Ler tick atual.
8. Calcular custos estimados antes da ordem.
9. Rodar `order_check`.
10. Enviar ordem.
11. Confirmar resultado via `order_send`.
12. Persistir pedido, deal, posição e custos reais.

## 5. API oficial MT5 que um bot normalmente precisa

Essas são as chamadas essenciais da integração Python oficial:

- `initialize`
- `login`
- `shutdown`
- `version`
- `last_error`
- `terminal_info`
- `account_info`
- `symbols_get`
- `symbol_select`
- `symbol_info`
- `symbol_info_tick`
- `copy_ticks_from`
- `copy_ticks_range`
- `copy_rates_from`
- `copy_rates_range`
- `order_calc_margin`
- `order_calc_profit`
- `order_check`
- `order_send`
- `orders_get`
- `positions_get`
- `history_orders_get`
- `history_deals_get`

## 6. Contrato de dados mínimo para o bot

### 6.1 SymbolSpec

O bot deve normalizar pelo menos:

```json
{
  "symbol": "BTCUSD",
  "digits": 2,
  "point": 0.01,
  "tick_size": 0.01,
  "tick_value": 0.0,
  "contract_size": 1.0,
  "volume_min": 0.01,
  "volume_max": 30.0,
  "volume_step": 0.01,
  "spread_float": true,
  "trade_mode": "enabled",
  "execution_mode": "market",
  "swap_long": null,
  "swap_short": null
}
```

### 6.2 Tick

```json
{
  "time": "2026-04-23T13:00:00.123Z",
  "bid": 93500.12,
  "ask": 93525.02,
  "last": 93520.10,
  "spread_points": 2490
}
```

### 6.3 OrderRequest

```json
{
  "symbol": "BTCUSD",
  "side": "buy",
  "volume": 0.10,
  "type": "market",
  "sl": 92800.00,
  "tp": 94800.00,
  "deviation_points": 500,
  "comment": "bot-entry"
}
```

## 7. Exemplo mínimo em Python

```python
from datetime import datetime, timedelta
import MetaTrader5 as mt5

ACCOUNT = 12345678
PASSWORD = "YOUR_PASSWORD"
SERVER = "Tickmill-Demo"
SYMBOL = "BTCUSD"

if not mt5.initialize(login=ACCOUNT, password=PASSWORD, server=SERVER):
    raise RuntimeError(f"MT5 initialize failed: {mt5.last_error()}")

try:
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        raise RuntimeError(f"Symbol not found: {SYMBOL}")

    if not info.visible:
        if not mt5.symbol_select(SYMBOL, True):
            raise RuntimeError(f"Could not select symbol: {SYMBOL}")

    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        raise RuntimeError(f"No tick available for {SYMBOL}")

    print("account:", mt5.account_info())
    print("symbol:", info)
    print("tick:", tick)

    start = datetime.utcnow() - timedelta(hours=6)
    ticks = mt5.copy_ticks_from(SYMBOL, start, 10000, mt5.COPY_TICKS_ALL)
    print("ticks:", 0 if ticks is None else len(ticks))

finally:
    mt5.shutdown()
```

## 8. Custos: modelo genérico

O custo real de uma operação deve ser decomposto em:

`total_cost = spread_cost + commission_cost + swap_cost + slippage_cost + exchange_or_broker_fees`

### 8.1 Spread cost

Para compra:

`spread_cost = (ask - bid) * contract_size * volume`

Para venda, o custo inicial equivalente continua sendo modelado pelo spread vigente.

### 8.2 Commission cost

Se houver comissão por lado:

`round_turn_commission = commission_per_lot_per_side * 2 * volume`

### 8.3 Swap cost

Modelo genérico por dia:

`swap_cost = swap_per_lot_per_day * volume * days_held`

Se o broker usar taxa anual:

`daily_swap = notional_value * annual_rate / 365`

### 8.4 Slippage cost

`slippage_cost = abs(expected_fill - actual_fill) * contract_size * volume`

## 9. Tickmill: foco em BTCUSD

### 9.1 Especificações de contrato observadas nas páginas oficiais da Tickmill

Para `BTCUSD`, a página oficial do instrumento mostra:

- `Min spread`: `12`
- `Typical spread`: `24.9`
- `Lot size`: `1`
- `Volume step`: `0.01 lot`
- `Volume min`: `0.01 lot`
- `Volume max`: `30 lots`
- `Contract size`: `1`
- `Max leverage`: `1:200`

Observação importante:
- esses números podem variar por entidade regulatória, conta, liquidez e manutenção do broker
- o bot deve sempre conferir `symbol_info` no terminal antes de operar

### 9.2 Comissão Tickmill

A página oficial da conta Raw informa:

- `Commissions: $3 per lot per side`
- mas também informa que essas comissões se aplicam a `CFDs on FX and Precious Metals`

Implicação prática para `BTCUSD`:

- não assuma automaticamente a comissão Raw de `$3/lot/side` para `BTCUSD`
- para `BTCUSD`, o modelo deve tratar a comissão como:
  - `0` por padrão
  - ou valor confirmado por `statement`, `contract specification` no terminal ou documentação específica do instrumento

Recomendação:

```yaml
tickmill_costs:
  symbol: BTCUSD
  commission_mode: verify_per_symbol
  default_commission_per_lot_per_side: 0.0
```

### 9.3 Swap Tickmill para cripto

Na página oficial de spread/swap da Tickmill:

- posições `long` em criptomoedas têm `swap de 10% ao ano`
- novas posições em cripto abertas em `MT4/MT5` têm `5 dias iniciais sem swap`
- o rollover é aplicado em `00:00` no horário da plataforma

O site não deixa tão claro, na página encontrada, o valor definitivo do lado `short` para `BTCUSD`.

Portanto, o módulo de custos deve:

1. Preferir `symbol_info` / propriedades do símbolo no terminal.
2. Guardar `swap_long` e `swap_short` por símbolo.
3. Se o valor do lado short não vier confiável, marcar como `unknown` e bloquear cálculo final até confirmação.

## 10. Módulo de custos recomendado para Tickmill BTCUSD

Estrutura sugerida:

```python
from dataclasses import dataclass

@dataclass
class SymbolTradingCostSpec:
    symbol: str
    contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    spread_mode: str
    raw_commission_per_lot_per_side: float | None
    commission_applies: bool
    annual_swap_long_rate: float | None
    annual_swap_short_rate: float | None
    swap_free_days: int
    rollover_hour_platform: int
```

Exemplo inicial para `BTCUSD`:

```python
btcusd_tickmill = {
    "symbol": "BTCUSD",
    "contract_size": 1.0,
    "volume_min": 0.01,
    "volume_max": 30.0,
    "volume_step": 0.01,
    "spread_mode": "floating",
    "raw_commission_per_lot_per_side": 0.0,
    "commission_applies": False,
    "annual_swap_long_rate": 0.10,
    "annual_swap_short_rate": None,
    "swap_free_days": 5,
    "rollover_hour_platform": 0
}
```

### 10.1 Fórmula prática de custo para BTCUSD na Tickmill

Se comissão explícita do símbolo não existir:

`total_cost = spread_cost + swap_cost + slippage_cost`

Se houver comissão específica confirmada:

`total_cost = spread_cost + commission_cost + swap_cost + slippage_cost`

### 10.2 Cálculo de spread para BTCUSD

Com `contract_size = 1`:

`spread_cost_usd = (ask - bid) * volume`

Exemplo:

- `bid = 93500.00`
- `ask = 93524.90`
- `spread = 24.90`
- `volume = 0.10`

Então:

`spread_cost_usd = 24.90 * 0.10 = 2.49 USD`

## 11. Regras importantes para bot em BTCUSD

### 11.1 Nunca confiar só em documentação estática

Antes de operar, ler ao vivo:

- `symbol_info`
- `symbol_info_tick`
- `account_info`
- histórico recente de spread

### 11.2 Validar volume

```text
volume >= volume_min
volume <= volume_max
((volume - volume_min) % volume_step) == 0
```

### 11.3 Validar execução

Para cripto, o spread pode abrir muito fora do típico.

O bot deve ter:

- `max_spread_abs_usd`
- `max_spread_pct_of_stop`
- `max_slippage_abs_usd`

### 11.4 Validar custo antes da ordem

Bloquear entrada se:

- `round_turn_cost > x% do alvo`
- `spread > y% do stop`
- `swap esperado > limite para holding`

## 12. Requisitos de robustez

O bot deve suportar:

- reconexão automática do terminal
- perda temporária de feed
- símbolo invisível em `MarketWatch`
- `initialize()` falhando
- `order_check` reprovando por margem, volume ou trade mode
- discrepância entre documentação e `symbol_info`

## 13. Checklist de produção

- MT5 instalado e logado
- símbolo `BTCUSD` confirmado no broker correto
- `symbol_info` persistido localmente
- tick atual disponível
- spread atual dentro do limite
- margem suficiente
- custo estimado calculado
- `order_check` aprovado
- `order_send` com retry controlado
- deals e posições reconciliados após execução
- custos reais reconciliados no pós-trade

## 14. Recomendação final para qualquer bot

Para `BTCUSD` na Tickmill:

- trate `spread` como custo principal e variável
- trate `swap` como custo relevante para holding acima de intraday, especialmente no lado comprado
- não assuma comissão Raw de FX/metals para cripto sem confirmação por símbolo
- use o terminal MT5 como fonte final de verdade para:
  - spread
  - volume limits
  - trade mode
  - swap
  - contract spec

## 15. Resumo executivo

Se você for integrar qualquer bot com MetaTrader 5 para operar `BTCUSD` na Tickmill, o mínimo necessário é:

- conectar ao terminal via API oficial Python
- resolver e validar o símbolo
- ler specs dinâmicas do símbolo antes de operar
- modelar custos como `spread + swap + slippage + comissão se confirmada`
- tratar a documentação do broker como referência inicial, nunca como única fonte

Para `BTCUSD`, o módulo de custos deve ser desenhado para aceitar atualização dinâmica por símbolo, porque esse instrumento tem condições que podem mudar por entidade, liquidez, manutenção e janela de mercado.
