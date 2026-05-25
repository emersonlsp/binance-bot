@echo off
setlocal

REM ============================================================
REM Binance Bot - Seed Batch Runner (double-click friendly)
REM
REM Edite os parametros abaixo como quiser.
REM ============================================================

set "SEEDS=0"
set "CANDIDATES=100"
set "FOLDS=4"
set "WORKERS=6"
set "XGB_DEVICE=cpu"
set "REGIME_GATE=on"
set "OUTPUT_ROOT=artifacts\reports\xgb_clean_search_batch"

REM ============================================================

set "ROOT=%~dp0"
set "PYTHON_EXE=%ROOT%.venv\Scripts\python.exe"

echo.
echo [run] Root: %ROOT%
echo [run] Seeds: %SEEDS%
echo [run] Candidates: %CANDIDATES%
echo [run] Folds: %FOLDS%
echo [run] Workers: %WORKERS%
echo [run] XGB device: %XGB_DEVICE%
echo [run] Regime gate: %REGIME_GATE%
echo [run] Output root: %OUTPUT_ROOT%
echo.

if not exist "%PYTHON_EXE%" (
  echo [run] ERRO: Python da venv nao encontrado em:
  echo [run] %PYTHON_EXE%
  echo [run] Crie a venv e instale dependencias antes de rodar.
  goto :END
)

pushd "%ROOT%"
set "PYTHONPATH=src"

"%PYTHON_EXE%" -m binance_bot.training.run_xgb_seed_batch ^
  --seeds %SEEDS% ^
  --candidates %CANDIDATES% ^
  --folds %FOLDS% ^
  --workers %WORKERS% ^
  --xgb-device %XGB_DEVICE% ^
  --regime-gate %REGIME_GATE% ^
  --output-root %OUTPUT_ROOT%

if errorlevel 1 (
  echo.
  echo [run] Finalizado com ERRO.
) else (
  echo.
  echo [run] Finalizado com sucesso.
)

popd

:END
echo.
pause
endlocal

