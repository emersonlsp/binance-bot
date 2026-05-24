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

Saída:
- `data/raw/binance/BTCBRL/{snapshots|updates|trades|collector_logs}/YYYY/MM/DD/*.parquet`

## Rodar busca XGBoost clean

```powershell
$env:PYTHONPATH="src"; .\.venv\Scripts\python.exe -m binance_bot.training.run_xgb_clean_search --candidates 60 --folds 4 --min-trades 40 --min-positive-folds 3 --seed 42 --workers 4
```

Ao terminar:
- mensagem: `[xgb_clean] done. leaderboard: ...`
- campeão: `artifacts/champion_strategy_xgb_clean.json`
- relatórios: `artifacts/reports/xgb_clean_search/*.json`
