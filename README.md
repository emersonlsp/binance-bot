# Binance Bot (Collector + XGBoost Clean)

Este repositório foi reduzido para manter apenas:
- coletor Binance Spot (`BTCBRL`)
- pipeline de treino/varredura `XGBoost clean`

## Setup

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install httpx websockets pyarrow xgboost
Copy-Item .env.example .env
```

## Rodar o coletor

```powershell
.\.venv\Scripts\python.exe .\run_collector.py
```

Duplo clique no Windows:
- execute `start_collector.pyw`
- logs em `logs/collector_YYYYMMDD.log`

Paper runner (simulado, separado do coletor):
- execute `start_paper.pyw`
- artefatos em `artifacts/paper_sim/`
  - `paper_report.json` (resumo atual: trades, win rate, pnl, retorno, drawdown)
  - `signals.jsonl`
  - `trades.jsonl`

Reset do paper simulado:
- pare o `start_paper.pyw`
- apague `artifacts/paper_sim/paper_report.json`
- inicie o paper novamente

Saída:
- `data/raw/binance/BTCBRL/{snapshots|updates|trades|collector_logs}/YYYY/MM/DD/*.parquet`

## Rodar busca XGBoost clean

```powershell
$env:PYTHONPATH="src"; .\.venv\Scripts\python.exe -m binance_bot.training.run_xgb_clean_search --candidates 60 --folds 4 --min-trades 40 --min-positive-folds 3 --seed 42 --workers 4
```

Com regime gate (MT5 candles):

```powershell
$env:PYTHONPATH="src"; .\.venv\Scripts\python.exe -m binance_bot.training.run_xgb_clean_search --candidates 60 --folds 4 --min-trades 40 --min-positive-folds 3 --seed 42 --workers 4 --regime-gate on --regime-chop-min-confidence 0.78
```

Ao terminar:
- mensagem: `[xgb_clean] done. leaderboard: ...`
- campeão: `artifacts/champion_strategy_xgb_clean.json`
- relatórios: `artifacts/reports/xgb_clean_search/*.json`
- vencedores (`mean_pnl_net_brl > 0`): `artifacts/reports/xgb_clean_search/winners_latest.json`
- promoção: só ocorre quando XGBoost bate o baseline em PnL e estabilidade por fold (`gate.promote=true`)

## MT5: Coleta de velas para regime

Pré-requisitos:
- MT5 desktop instalado
- credenciais MT5 no `.env` (`MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`, `MT5_PATH`)

Coletar velas (`BTCUSD`):

```powershell
$env:PYTHONPATH="src"; .\.venv\Scripts\python.exe -m binance_bot.mt5.collect_candles
```

Saída:
- `data/raw/mt5/BTCUSD/candles/{M1|M5|M15|H1}/*.parquet`

Gerar features de regime:

```powershell
$env:PYTHONPATH="src"; .\.venv\Scripts\python.exe -m binance_bot.mt5.build_regime_features
```

Saída:
- `data/processed/mt5/BTCUSD/regime/regime_features_YYYYMMDD.parquet`
- `artifacts/reports/mt5_regime_summary.json`
