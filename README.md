# Binance Bot (Collector + XGBoost Clean)

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.mdEste repositÃ³rio foi reduzido para manter apenas:
- coletor Binance Spot (`BTCBRL`)
- pipeline de treino/varredura `XGBoost clean`

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.md## Setup

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.md```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install httpx websockets pyarrow xgboost
Copy-Item .env.example .env
```

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.md## Rodar o coletor

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.md```powershell
.\.venv\Scripts\python.exe .\run_collector.py
```

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.mdDuplo clique no Windows:
- execute `start_collector.pyw`
- logs em `logs/collector_YYYYMMDD.log`

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.mdPaper runner realtime (simulado, separado do coletor):
- execute `start_paper.pyw`
- artefatos em `artifacts/paper_sim/`
  - `paper_report.json` (resumo atual: trades, win rate, pnl, retorno, drawdown)
  - `signals.jsonl`
  - `trades.jsonl`

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.mdReset do paper simulado:
- pare o `start_paper.pyw`
- apague `artifacts/paper_sim/paper_report.json`
- inicie o paper novamente

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.mdSaÃ­da:
- `data/raw/binance/BTCBRL/{snapshots|updates|trades|collector_logs}/YYYY/MM/DD/*.parquet`

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.md## Rodar busca XGBoost clean

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.md```powershell
$env:PYTHONPATH="src"; .\.venv\Scripts\python.exe -m binance_bot.training.run_xgb_clean_search --candidates 60 --folds 4 --min-trades 40 --min-positive-folds 3 --seed 42 --workers 4
```

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.mdCom regime gate (MT5 candles):

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.md```powershell
$env:PYTHONPATH="src"; .\.venv\Scripts\python.exe -m binance_bot.training.run_xgb_clean_search --candidates 60 --folds 4 --min-trades 40 --min-positive-folds 3 --seed 42 --workers 4 --regime-gate on --regime-chop-min-confidence 0.78
```

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.mdAo terminar:
- mensagem: `[xgb_clean] done. leaderboard: ...`
- campeÃ£o: `artifacts/champion_strategy_xgb_clean.json`
- relatÃ³rios: `artifacts/reports/xgb_clean_search/*.json`
- vencedores (`mean_pnl_net_brl > 0`): `artifacts/reports/xgb_clean_search/winners_latest.json`
- promoÃ§Ã£o: sÃ³ ocorre quando XGBoost bate o baseline em PnL e estabilidade por fold (`gate.promote=true`)

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.md## MT5: Coleta de velas para regime

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.mdPrÃ©-requisitos:
- MT5 desktop instalado
- credenciais MT5 no `.env` (`MT5_LOGIN`, `MT5_PASSWORD`, `MT5_SERVER`, `MT5_PATH`)

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.mdColetar velas (`BTCUSD`):

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.md```powershell
$env:PYTHONPATH="src"; .\.venv\Scripts\python.exe -m binance_bot.mt5.collect_candles
```

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.mdSaÃ­da:
- `data/raw/mt5/BTCUSD/candles/{M1|M5|M15|H1}/*.parquet`

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.mdGerar features de regime:

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.md```powershell
$env:PYTHONPATH="src"; .\.venv\Scripts\python.exe -m binance_bot.mt5.build_regime_features
```

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.mdSaÃ­da:
- `data/processed/mt5/BTCUSD/regime/regime_features_YYYYMMDD.parquet`
- `artifacts/reports/mt5_regime_summary.json`

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.md## Champion Management

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.mdArquivar campeao atual (antes de novo treino):

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.md```powershell
$env:PYTHONPATH="src"; .\.venv\Scripts\python.exe -m binance_bot.training.archive_champion
```

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.mdPromover campeao escolhido manualmente:

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.md```powershell
$env:PYTHONPATH="src"; .\.venv\Scripts\python.exe -m binance_bot.training.promote_champion --source artifacts\champions_archive\SEU_ARQUIVO.json
```

## Regras de Margin

Para operacao e precificacao realista em margin (borrow/repay, juros, capacidade de emprestimo, erros operacionais e criterios de PnL liquido), consulte:

- docs/binance_margin_rules.md
