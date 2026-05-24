# Plano Completo de Machine Learning para Bot de Trading em BTC

> Objetivo: construir um bot de trading em BTC usando dados de velas do MT5, preço, order book e order flow, combinando aprendizado supervisionado, aprendizado não supervisionado e redes neurais em uma evolução segura e validável.

---

## 1. Visão geral do projeto

Este plano parte da seguinte ideia:

```text
Velas históricas do MT5
    ↓
Contexto de mercado, tendência, volatilidade e regimes

Order Book
    ↓
Liquidez disponível, desequilíbrio bid/ask e microestrutura

Order Flow
    ↓
Pressão real de compra/venda, agressão, absorção e fluxo

Machine Learning Supervisionado
    ↓
Decisão de compra, venda ou não operação

Machine Learning Não Supervisionado
    ↓
Detecção de regimes, anomalias e momentos ruins para operar

Redes Neurais
    ↓
Etapa avançada para capturar padrões sequenciais e complexos

Gerenciamento de Risco
    ↓
Filtro final antes de qualquer execução
```

O objetivo não é simplesmente prever o preço do BTC.

O objetivo correto é responder:

> Existe uma oportunidade com probabilidade suficiente para superar spread, taxa, slippage, latência e risco?

---

## 2. Fontes de dados

O sistema deve trabalhar com três grandes grupos de dados:

```text
1. Velas históricas do MT5
2. Order Book
3. Order Flow
```

Cada grupo tem uma função diferente.

---

## 3. Velas históricas do MT5

As velas serão puxadas do MT5.

Como já existe um `.md` no repositório sobre MT5, este plano deve referenciar esse documento em vez de duplicar toda a explicação técnica.

### 3.1 Documento relacionado

No repositório, manter ou criar referência para algo como:

```text
docs/mt5_data_collection.md
```

ou, se o arquivo já tiver outro nome:

```text
docs/NOME_DO_ARQUIVO_MT5.md
```

Este documento de MT5 deve conter:

```text
como conectar ao MT5
como puxar velas
quais timeframes usar
como salvar os dados
como lidar com timezone
como validar buracos no histórico
como atualizar os dados incrementalmente
```

### 3.2 Função das velas no bot

As velas históricas servem principalmente para:

```text
contexto de mercado
detecção de tendência
volatilidade
regime macro
filtro operacional
treinamento inicial com histórico maior
backtests mais longos
```

Elas não substituem order book nem order flow.

Velas mostram:

```text
open
high
low
close
volume
spread, se disponível
tick volume, se disponível
```

Mas não mostram completamente:

```text
liquidez real no book
ordens passivas
agressão compradora/vendedora real
absorção
spoofing
remoção de liquidez
fila de execução
slippage real
```

Portanto:

> Velas são o mapa do mercado.  
> Order book e order flow são o radar de entrada.

---

## 4. Timeframes recomendados das velas

Usar múltiplos timeframes.

### 4.1 Timeframes para contexto curto

```text
M1
M5
M15
```

Função:

```text
detectar microtendência
medir volatilidade recente
identificar rompimentos curtos
evitar entradas contra movimento muito forte
```

### 4.2 Timeframes para contexto médio

```text
M30
H1
H4
```

Função:

```text
detectar tendência principal
identificar lateralização
medir volatilidade estrutural
classificar regime
```

### 4.3 Timeframe diário

```text
D1
```

Função:

```text
contexto macro
volatilidade diária
zonas importantes
risco de operar em região esticada
```

---

## 5. Dados de velas que devem ser salvos

Para cada candle:

```text
symbol
timeframe
timestamp_open
timestamp_close
open
high
low
close
tick_volume
real_volume, se disponível
spread, se disponível
source
```

Também salvar informações técnicas:

```text
timezone
broker
servidor MT5
data de coleta
versão do coletor
```

---

## 6. Features derivadas das velas

### 6.1 Retornos

```text
return_1
return_3
return_5
return_10
return_20
log_return
```

### 6.2 Volatilidade

```text
volatility_5
volatility_10
volatility_20
volatility_50
atr_14
true_range
range_percent
```

### 6.3 Tendência

```text
ema_9
ema_21
ema_50
ema_200
distance_from_ema_21
distance_from_ema_50
ema_slope_21
ema_slope_50
trend_strength
```

### 6.4 Momentum

```text
rsi_14
macd
macd_signal
macd_histogram
roc
stochastic
```

### 6.5 Volume

```text
tick_volume_mean_20
tick_volume_ratio
volume_zscore
volume_acceleration
```

### 6.6 Estrutura do candle

```text
body_size
upper_wick
lower_wick
wick_ratio
body_to_range_ratio
candle_direction
inside_bar
outside_bar
engulfing_pattern
```

### 6.7 Contexto multi-timeframe

Exemplo:

```text
M1_return_5
M5_trend
M15_volatility
H1_ema_slope
H4_trend
D1_range_position
```

O modelo de entrada pode operar no curto prazo, mas deve saber se está contra ou a favor do contexto maior.

---

## 7. Order Book

Order book mostra a liquidez passiva disponível.

### 7.1 Dados mínimos de order book

Salvar snapshots frequentes contendo:

```text
timestamp
best_bid
best_ask
spread
mid_price
bid_price_1
bid_size_1
ask_price_1
ask_size_1
...
bid_price_N
bid_size_N
ask_price_N
ask_size_N
```

Idealmente:

```text
top 10 níveis
top 20 níveis
```

### 7.2 Função do order book

O order book ajuda a medir:

```text
liquidez disponível
desequilíbrio entre compradores e vendedores passivos
spread
profundidade
paredes de liquidez
remoção súbita de liquidez
compressão ou expansão do spread
```

### 7.3 Features de order book

```text
spread_abs
spread_pct
mid_price
bid_depth_top_5
ask_depth_top_5
bid_depth_top_10
ask_depth_top_10
bid_depth_top_20
ask_depth_top_20
book_imbalance_top_1
book_imbalance_top_5
book_imbalance_top_10
book_imbalance_top_20
bid_ask_depth_ratio
liquidity_wall_bid
liquidity_wall_ask
distance_to_bid_wall
distance_to_ask_wall
depth_slope_bid
depth_slope_ask
```

### 7.4 Cálculo simples de imbalance

```text
bid_volume_total = soma dos volumes bid nos N níveis
ask_volume_total = soma dos volumes ask nos N níveis

book_imbalance = bid_volume_total / (bid_volume_total + ask_volume_total)
```

Interpretação simplificada:

```text
book_imbalance > 0.5
    mais volume no bid do que no ask

book_imbalance < 0.5
    mais volume no ask do que no bid
```

Mas isso não deve ser usado sozinho, pois volume passivo pode sumir.

---

## 8. Order Flow

Order flow é diferente de order book.

Order book mostra intenção passiva.

Order flow mostra agressão real.

### 8.1 Diferença entre order book e order flow

```text
Order Book:
    ordens limitadas disponíveis no book

Order Flow:
    ordens executadas, agressões, compras a mercado, vendas a mercado
```

Exemplo:

```text
book mostra muitos compradores no bid
mas order flow mostra vendas agressivas batendo no bid
```

Nesse caso, o book parecia comprador, mas o fluxo real pode ser vendedor.

---

## 9. Dados necessários para order flow

Salvar trades executados:

```text
timestamp
price
volume
side/aggressor, se disponível
trade_id
```

Se a corretora não informar o agressor diretamente, tentar inferir:

```text
trade price próximo do ask => provável agressão compradora
trade price próximo do bid => provável agressão vendedora
```

### 9.1 Features de order flow

```text
buy_aggressive_volume_1s
sell_aggressive_volume_1s
buy_aggressive_volume_5s
sell_aggressive_volume_5s
buy_aggressive_volume_15s
sell_aggressive_volume_15s
delta_volume_1s
delta_volume_5s
delta_volume_15s
cumulative_delta
trade_count_5s
average_trade_size_5s
large_trade_count
large_trade_volume
aggression_ratio
flow_acceleration
```

### 9.2 Delta de volume

```text
delta = aggressive_buy_volume - aggressive_sell_volume
```

Interpretação:

```text
delta positivo:
    compradores agredindo mais

delta negativo:
    vendedores agredindo mais
```

### 9.3 Cumulative delta

```text
cumulative_delta = soma acumulada do delta
```

Pode ajudar a detectar:

```text
pressão compradora
pressão vendedora
divergência entre preço e fluxo
exaustão
absorção
```

---

## 10. Conceitos de order flow úteis

### 10.1 Absorção

Absorção ocorre quando há agressão forte de um lado, mas o preço não anda proporcionalmente.

Exemplo:

```text
muitas compras agressivas
mas o preço não sobe
```

Possível interpretação:

```text
vendedores passivos estão absorvendo as compras
risco de reversão para baixo
```

### 10.2 Exaustão

Exaustão ocorre quando o fluxo agressor perde força após um movimento.

Exemplo:

```text
preço sobe
delta comprador diminui
volume agressor comprador cai
```

Possível interpretação:

```text
movimento pode estar perdendo força
```

### 10.3 Liquidez sumindo

Exemplo:

```text
ask_depth cai rapidamente
spread abre
book fica fino
```

Pode indicar:

```text
risco de slippage
mercado perigoso
rompimento forte
falso rompimento
```

### 10.4 Divergência preço x fluxo

Exemplo:

```text
preço sobe
mas cumulative delta cai
```

Pode indicar:

```text
movimento fraco
possível armadilha
```

---

## 11. Organização dos dados

Estrutura sugerida:

```text
data/
  raw/
    mt5_candles/
    orderbook/
    trades/
  processed/
    candles/
    orderbook/
    orderflow/
  training/
    supervised/
    unsupervised/
    neural/
```

### 11.1 Dados brutos

Nunca sobrescrever.

```text
data/raw/mt5_candles/BTCUSD_M1_YYYY-MM.parquet
data/raw/orderbook/BTCUSD_orderbook_YYYY-MM-DD.parquet
data/raw/trades/BTCUSD_trades_YYYY-MM-DD.parquet
```

### 11.2 Dados processados

```text
data/processed/candles/features_M1.parquet
data/processed/orderbook/features_orderbook.parquet
data/processed/orderflow/features_orderflow.parquet
```

### 11.3 Dataset final

```text
data/training/supervised/dataset_horizon_5s.parquet
data/training/supervised/dataset_horizon_15s.parquet
data/training/supervised/dataset_horizon_60s.parquet
```

---

## 12. Criação dos labels

Não treinar o modelo com um label ingênuo como:

```text
se o preço futuro subiu, compra
se o preço futuro caiu, vende
```

Isso ignora spread, taxa e slippage.

### 12.1 Label recomendado

```text
mid_price = (best_bid + best_ask) / 2

future_return = (future_mid_price - current_mid_price) / current_mid_price

estimated_cost = spread_pct + fee_pct + slippage_pct

threshold = estimated_cost + safety_margin
```

Label:

```text
1  = comprar, se future_return > threshold
0  = não operar, se -threshold <= future_return <= threshold
-1 = vender, se future_return < -threshold
```

### 12.2 Horizontes de label

Criar labels para vários horizontes:

```text
5 segundos
15 segundos
30 segundos
60 segundos
5 minutos
```

Para velas puras, usar também:

```text
1 candle à frente
3 candles à frente
5 candles à frente
10 candles à frente
```

---

## 13. Aprendizado supervisionado

O aprendizado supervisionado será a primeira etapa forte do bot.

### 13.1 Objetivo

Treinar modelos para prever:

```text
comprar
não operar
vender
```

ou:

```text
probabilidade de alta líquida
probabilidade de queda líquida
probabilidade de não haver edge
```

### 13.2 Modelos iniciais

Começar com:

```text
Logistic Regression
Random Forest
XGBoost
LightGBM
CatBoost
```

### 13.3 Modelo principal inicial

Escolha recomendada:

```text
LightGBM ou XGBoost
```

Motivo:

```text
fortes em dados tabulares
bons com features de velas
bons com features de order book
bons com features de order flow
mais fáceis de validar que redes neurais profundas
bons para baseline robusto
```

---

## 14. Três modelos supervisionados separados

Em vez de criar um modelo único logo no início, separar em três modelos.

### 14.1 Modelo A: velas

Entrada:

```text
features de M1
features de M5
features de M15
features de H1
features de H4
```

Saída:

```text
contexto favorável para compra
contexto neutro
contexto favorável para venda
```

Função:

```text
filtro de direção e volatilidade
```

### 14.2 Modelo B: order book

Entrada:

```text
spread
imbalance
profundidade
paredes de liquidez
mudança de profundidade
compressão/expansão de spread
```

Saída:

```text
pressão micro de alta
neutro
pressão micro de baixa
```

Função:

```text
sinal de microestrutura
```

### 14.3 Modelo C: order flow

Entrada:

```text
delta
cumulative delta
agressão compradora
agressão vendedora
absorção
exaustão
large trades
```

Saída:

```text
fluxo comprador
neutro
fluxo vendedor
```

Função:

```text
confirmar se o movimento tem fluxo real
```

### 14.4 Modelo final supervisionado

Depois, criar um modelo final que recebe:

```text
probabilidades do modelo de velas
probabilidades do modelo de order book
probabilidades do modelo de order flow
features principais
regime de mercado
```

Saída:

```text
comprar
não operar
vender
```

Esse modelo final pode ser:

```text
LightGBM
XGBoost
CatBoost
Logistic Regression como meta-modelo
```

---

## 15. Aprendizado não supervisionado

O aprendizado não supervisionado será usado para detectar regimes e anomalias.

Ele não deve ser a primeira ferramenta para decidir compra e venda.

### 15.1 Objetivos

Usar não supervisionado para descobrir:

```text
mercado lateral
mercado direcional
mercado volátil
mercado calmo
mercado com book fino
mercado com book profundo
mercado com fluxo agressivo
mercado com fluxo divergente
mercado perigoso
momentos anormais
```

### 15.2 Modelos recomendados

```text
K-Means
Gaussian Mixture Models
HDBSCAN
DBSCAN
PCA
UMAP
Isolation Forest
Hidden Markov Models
Autoencoders, em etapa neural
```

### 15.3 Regimes com velas

Usar features de velas para criar regimes macro:

```text
tendência de alta
tendência de baixa
lateral
alta volatilidade
baixa volatilidade
range apertado
range expandido
```

### 15.4 Regimes com order book

Usar features de book para regimes de liquidez:

```text
book equilibrado
book comprador
book vendedor
book fino
book profundo
spread comprimido
spread aberto
liquidez instável
```

### 15.5 Regimes com order flow

Usar features de fluxo para regimes de agressão:

```text
agressão compradora
agressão vendedora
fluxo neutro
absorção
exaustão
divergência
```

### 15.6 Como usar os regimes

O regime deve funcionar como filtro.

Exemplo:

```text
se regime_macro = alta volatilidade extrema:
    reduzir tamanho ou não operar

se regime_book = liquidez instável:
    não operar

se regime_flow = absorção contra a entrada:
    bloquear operação

se regime_macro = tendência
e regime_flow confirma:
    permitir entrada a favor da tendência
```

---

## 16. Combinação entre supervisionado e não supervisionado

Arquitetura recomendada:

```text
Velas MT5
    ↓
Modelo supervisionado de contexto
    ↓
Probabilidade direcional

Velas + book + flow
    ↓
Modelo não supervisionado
    ↓
Regime de mercado

Order book
    ↓
Modelo supervisionado de microestrutura
    ↓
Pressão micro

Order flow
    ↓
Modelo supervisionado de fluxo
    ↓
Confirmação do fluxo

Tudo junto
    ↓
Modelo final ou regra de decisão
    ↓
Comprar / Não operar / Vender
```

Exemplo de regra conservadora:

```text
operar comprado apenas se:
    modelo_velas favorece compra
    modelo_book favorece compra
    modelo_flow confirma compra
    regime não é perigoso
    custo operacional está aceitável
```

---

## 17. Validação temporal

Não usar validação aleatória.

### 17.1 Proibido

```text
train_test_split aleatório
KFold comum em série temporal
misturar dados futuros no treino
normalizar usando estatísticas do dataset inteiro
criar features que usam dados futuros
```

### 17.2 Recomendado

Usar walk-forward:

```text
treina em janeiro
valida em fevereiro

treina em janeiro + fevereiro
valida em março

treina em janeiro + fevereiro + março
valida em abril
```

### 17.3 Gap temporal

Usar gap entre treino e teste.

```text
treino até 10:00:00
gap até 10:05:00
teste a partir de 10:05:01
```

Isso reduz vazamento quando labels se sobrepõem.

---

## 18. Backtest realista

O backtest precisa simular execução.

### 18.1 Custos obrigatórios

```text
spread
taxa
slippage
latência
ordem parcial
ordem não executada
diferença entre market order e limit order
impacto do tamanho da ordem
fila no book, se aplicável
```

### 18.2 O que não assumir

Não assumir:

```text
compra no mid price
venda no mid price
execução instantânea
slippage zero
liquidez infinita
ordem sempre preenchida
```

### 18.3 Métricas financeiras

```text
retorno líquido
profit factor
drawdown máximo
expectancy por trade
Sharpe
Sortino
média de ganho
média de perda
ganho médio / perda média
sequência máxima de perdas
```

### 18.4 Métricas operacionais

```text
número de trades
turnover
tempo médio em posição
slippage médio
taxa de execução
taxa de ordens parciais
perda máxima diária
performance por horário
performance por regime
```

---

## 19. Redes neurais

Redes neurais entram no fim da evolução, depois que o sistema já tiver:

```text
dados limpos
features bem construídas
labels bons
backtest realista
modelos baseline
modelos de gradient boosting
regimes não supervisionados
paper trading inicial
```

A rede neural deve entrar como diferencial, não como aposta cega.

---

## 20. Onde redes neurais podem gerar diferencial

### 20.1 Sequência temporal

Redes neurais podem capturar:

```text
evolução do book
mudança de fluxo
aceleração de preço
padrões antes de rompimentos
padrões antes de reversões
```

Modelos possíveis:

```text
LSTM
GRU
TCN
1D CNN
Transformer temporal
```

### 20.2 Estrutura do order book

O book pode ser tratado como matriz.

```text
linhas = níveis do book
colunas = preço, volume, lado, distância do mid
tempo = sequência de snapshots
```

Modelos possíveis:

```text
CNN
CNN + LSTM
CNN + TCN
CNN + Transformer
```

### 20.3 Order flow sequencial

O fluxo pode ser tratado como sequência de eventos.

```text
trade price
trade volume
aggressor side
delta
cumulative delta
tempo entre trades
```

Modelos possíveis:

```text
LSTM
GRU
Transformer
Temporal Fusion Transformer
```

### 20.4 Embeddings

Uma rede neural pode transformar dados complexos em embeddings.

Exemplo:

```text
últimos 60 segundos de book + flow
    ↓
encoder neural
    ↓
embedding de 32 ou 64 dimensões
    ↓
LightGBM/XGBoost/modelo final
```

Essa é uma das melhores formas de usar redes neurais junto com gradient boosting.

---

## 21. Ordem recomendada para redes neurais

### 21.1 Fase Neural 1: MLP simples

Entrada:

```text
features tabulares já criadas
```

Objetivo:

```text
ver se uma rede simples supera modelos básicos
```

### 21.2 Fase Neural 2: LSTM/GRU

Entrada:

```text
sequência de features de velas, book e flow
```

Objetivo:

```text
capturar dependência temporal
```

### 21.3 Fase Neural 3: CNN no order book

Entrada:

```text
matriz do book
```

Objetivo:

```text
capturar estrutura dos níveis de liquidez
```

### 21.4 Fase Neural 4: CNN + LSTM/TCN

Entrada:

```text
sequência de matrizes do book
```

Objetivo:

```text
capturar estrutura + evolução temporal
```

### 21.5 Fase Neural 5: Autoencoder

Entrada:

```text
velas + book + flow
```

Objetivo:

```text
detectar anomalias
criar embeddings
ajudar na detecção de regimes
```

### 21.6 Fase Neural 6: Transformer temporal

Entrada:

```text
sequências longas de velas, order book e order flow
```

Objetivo:

```text
capturar padrões complexos de longo alcance
```

---

## 22. Arquitetura final com redes neurais

Arquitetura híbrida recomendada:

```text
Velas MT5
    ↓
Features manuais
    ↓
LightGBM/XGBoost de contexto
    ↓
Sinal de contexto
```

```text
Order Book
    ↓
Features manuais
    ↓
LightGBM/XGBoost de microestrutura
    ↓
Sinal de book
```

```text
Order Flow
    ↓
Features manuais
    ↓
LightGBM/XGBoost de fluxo
    ↓
Sinal de fluxo
```

```text
Velas + Book + Flow em sequência
    ↓
Rede Neural Temporal / Encoder
    ↓
Embedding ou sinal neural
```

```text
Features + sinais + embeddings + regimes
    ↓
Modelo final / Ensemble / Regras de risco
    ↓
Comprar / Não operar / Vender
```

---

## 23. Ensemble final

O sistema final pode combinar:

```text
modelo de velas
modelo de order book
modelo de order flow
detector de regimes
rede neural temporal
gerenciador de risco
```

Exemplo conservador:

```text
if regime_perigoso:
    no_trade

elif candle_model_buy and orderbook_model_buy and orderflow_model_buy:
    buy

elif candle_model_sell and orderbook_model_sell and orderflow_model_sell:
    sell

else:
    no_trade
```

Exemplo com confiança:

```text
score_final =
    0.25 * score_velas
  + 0.25 * score_orderbook
  + 0.25 * score_orderflow
  + 0.15 * score_neural
  + 0.10 * score_regime
```

Depois:

```text
se score_final > limite_compra:
    comprar

se score_final < limite_venda:
    vender

caso contrário:
    não operar
```

---

## 24. Gerenciamento de risco

O gerenciamento de risco é obrigatório.

### 24.1 Limites mínimos

```text
perda máxima diária
perda máxima por trade
número máximo de trades por dia
tamanho máximo da posição
tempo máximo em posição
drawdown máximo
pausa após sequência de perdas
bloqueio por volatilidade extrema
bloqueio por spread alto
bloqueio por liquidez baixa
```

### 24.2 Regras de desligamento

```text
se perda diária > limite:
    desligar bot

se slippage médio > limite:
    desligar bot

se taxa de execução cair:
    desligar bot

se regime for anormal:
    desligar bot

se o modelo operar demais:
    reduzir frequência ou desligar
```

---

## 25. Paper trading

Antes de operar dinheiro real, rodar em tempo real sem executar ordens reais.

Validar:

```text
sinais ao vivo
latência real
execução simulada
slippage estimado
taxa de acerto fora do backtest
drawdown
comportamento por regime
diferença entre backtest e mercado real
```

Rodar durante diferentes cenários:

```text
mercado lateral
mercado em tendência
alta volatilidade
baixa volatilidade
notícias fortes
fim de semana
horário de baixa liquidez
```

---

## 26. Estrutura sugerida do repositório

```text
btc-ml-bot/
  README.md

  docs/
    mt5_data_collection.md
    machine_learning_plan.md
    backtesting_plan.md
    risk_management.md

  data/
    raw/
      mt5_candles/
      orderbook/
      trades/
    processed/
      candles/
      orderbook/
      orderflow/
    training/
      supervised/
      unsupervised/
      neural/

  collectors/
    mt5_candle_collector.py
    orderbook_collector.py
    trades_collector.py

  features/
    candle_features.py
    orderbook_features.py
    orderflow_features.py
    multi_timeframe_features.py

  labels/
    build_labels.py
    build_execution_labels.py

  models/
    supervised/
      train_logistic_regression.py
      train_random_forest.py
      train_xgboost.py
      train_lightgbm.py
      train_catboost.py
    unsupervised/
      train_kmeans.py
      train_hdbscan.py
      train_gmm.py
      train_isolation_forest.py
    neural/
      train_mlp.py
      train_lstm.py
      train_cnn_orderbook.py
      train_autoencoder.py
      train_transformer.py

  regimes/
    regime_detector.py
    regime_analysis.py

  backtest/
    backtest_engine.py
    execution_simulator.py
    cost_model.py
    slippage_model.py
    metrics.py

  paper_trading/
    paper_trader.py

  execution/
    exchange_client.py
    order_manager.py
    risk_manager.py

  configs/
    mt5_config.yaml
    data_config.yaml
    feature_config.yaml
    label_config.yaml
    supervised_model_config.yaml
    unsupervised_model_config.yaml
    neural_model_config.yaml
    backtest_config.yaml
    risk_config.yaml

  notebooks/
    candle_analysis.ipynb
    orderbook_analysis.ipynb
    orderflow_analysis.ipynb
    regime_analysis.ipynb
    feature_importance.ipynb
    neural_embeddings.ipynb

  reports/
    supervised_model_report.md
    unsupervised_regime_report.md
    neural_model_report.md
    backtest_report.md
    paper_trading_report.md
```

---

## 27. Roadmap de implementação

### Fase 1: Coleta de velas MT5

```text
usar o documento existente de MT5 no repo
puxar histórico grande de BTC
salvar em parquet
validar timezone
validar buracos de dados
criar atualização incremental
```

### Fase 2: Features de velas

```text
retornos
volatilidade
médias
momentum
volume
estrutura dos candles
multi-timeframe
```

### Fase 3: Baselines com velas

```text
não operar
momentum simples
reversão simples
Logistic Regression
Random Forest
LightGBM/XGBoost
```

### Fase 4: Coleta de order book

```text
capturar snapshots
salvar top N níveis
medir spread
medir profundidade
medir imbalance
```

### Fase 5: Coleta de order flow

```text
capturar trades
classificar agressor
calcular delta
calcular cumulative delta
detectar absorção/exaustão
```

### Fase 6: Modelos supervisionados separados

```text
modelo de velas
modelo de order book
modelo de order flow
modelo final combinado
```

### Fase 7: Aprendizado não supervisionado

```text
regimes com velas
regimes com book
regimes com flow
detecção de anomalias
filtros operacionais
```

### Fase 8: Backtest realista

```text
custos
spread
slippage
latência
ordem parcial
ordem não executada
performance por regime
```

### Fase 9: Redes neurais

```text
MLP
LSTM/GRU
CNN no order book
CNN + LSTM/TCN
Autoencoder
Transformer temporal
embeddings para alimentar o modelo final
```

### Fase 10: Paper trading

```text
rodar ao vivo sem dinheiro real
comparar sinal com execução simulada
medir degradação em relação ao backtest
```

### Fase 11: Operação real mínima

```text
capital mínimo
limites rígidos
aumentar tamanho apenas com consistência
monitoramento constante
```

---

## 28. Resumo final

A melhor estratégia é construir o sistema em camadas:

```text
Velas MT5:
    contexto histórico, tendência, volatilidade e regimes macro

Order Book:
    liquidez, spread, imbalance e microestrutura

Order Flow:
    agressão real, delta, absorção, exaustão e confirmação

Aprendizado Supervisionado:
    previsão de compra, venda ou não operação

Aprendizado Não Supervisionado:
    regimes de mercado, anomalias e filtros

Redes Neurais:
    etapa avançada para sequência, embeddings e padrões complexos

Backtest Realista:
    proteção contra ilusão estatística

Paper Trading:
    validação fora do histórico

Gerenciamento de Risco:
    proteção contra erro do modelo
```

Conclusão prática:

> Comece pelas velas do MT5 para criar contexto e histórico longo.  
> Depois adicione order book e order flow para entradas mais precisas.  
> Use XGBoost/LightGBM como primeira base supervisionada forte.  
> Use aprendizado não supervisionado para filtrar regimes e anomalias.  
> Coloque redes neurais no fim como diferencial, especialmente para sequências de book, order flow e embeddings.  
> Só opere real depois de backtest realista e paper trading consistente.
