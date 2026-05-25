@echo off
setlocal

REM Sync Binance raw data copied from VPS mirror folder into local training folder.
REM Usage: double-click or run from repo root.

set "ROOT=%~dp0"
set "SRC_BINANCE=%ROOT%data_vps\raw\binance\BTCBRL"
set "DST_BINANCE=%ROOT%data\raw\binance\BTCBRL"
set "PYTHON_EXE=%ROOT%.venv\Scripts\python.exe"

echo.
echo [sync] Root: %ROOT%
echo [sync] Source (Binance): %SRC_BINANCE%
echo [sync] Target (Binance): %DST_BINANCE%

if not exist "%SRC_BINANCE%" (
  echo [sync] Source Binance folder not found. Nothing to sync.
  goto :MT5
)

if exist "%PYTHON_EXE%" (
  echo [sync] Running incremental block sync for Binance...
  set "PYTHONPATH=src"
  "%PYTHON_EXE%" scripts\sync_incremental_blocks.py --src "%SRC_BINANCE%" --dst "%DST_BINANCE%" --state "%ROOT%artifacts\sync\binance_sync_state.json" --safety-hours 24
  if errorlevel 1 (
    echo [sync] ERRO: incremental block sync failed.
    goto :FAIL
  )
) else (
  echo [sync] Python venv not found. Falling back to robocopy for Binance.
  robocopy "%SRC_BINANCE%" "%DST_BINANCE%" *.parquet /E /XO /R:2 /W:2 /Z
  echo [sync] Binance sync finished. robocopy exit code: %ERRORLEVEL%
)

:MT5
set "SRC_MT5=%ROOT%data_vps\raw\mt5"
set "DST_MT5=%ROOT%data\raw\mt5"

if not exist "%SRC_MT5%" (
  echo [sync] Source MT5 folder not found. Skipping MT5 sync.
  goto :DONE
)

echo [sync] Source (MT5): %SRC_MT5%
echo [sync] Target (MT5): %DST_MT5%
robocopy "%SRC_MT5%" "%DST_MT5%" *.parquet /E /XO /R:2 /W:2 /Z
echo [sync] MT5 sync finished. robocopy exit code: %ERRORLEVEL%

:DONE
echo.
echo [sync] Sync completed. Preparing MT5 regime features...
echo [sync] Keeping caches for incremental build (no full cache wipe).

if not exist "%PYTHON_EXE%" (
  echo [sync] Python venv not found at: %PYTHON_EXE%
  echo [sync] Skipping regime build.
  goto :END
)

set "PYTHONPATH=src"
pushd "%ROOT%"
"%PYTHON_EXE%" -m binance_bot.mt5.collect_candles
if errorlevel 1 (
  echo [sync] ERRO: MT5 local candle collect failed. Sync interrompido.
  echo [sync] Sem velas MT5 atualizadas, o pipeline fica inconsistente para treino com regime gate.
  popd
  goto :FAIL
) else (
  echo [sync] MT5 local candle collect finished successfully.
)

"%PYTHON_EXE%" -m binance_bot.mt5.build_regime_features
if errorlevel 1 (
  echo [sync] ERRO: Regime build failed. Sync interrompido.
  popd
  goto :FAIL
) else (
  echo [sync] Regime build finished successfully.
)
popd

:END
echo [sync] Completed.
echo.
pause
endlocal
exit /b 0

:FAIL
echo.
echo [sync] Processo finalizado com erro.
echo.
pause
endlocal
exit /b 1
