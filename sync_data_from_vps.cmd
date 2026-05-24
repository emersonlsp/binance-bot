@echo off
setlocal

REM Sync Binance raw data copied from VPS mirror folder into local training folder.
REM Usage: double-click or run from repo root.

set "ROOT=%~dp0"
set "SRC_BINANCE=%ROOT%data_vps\raw\binance\BTCBRL"
set "DST_BINANCE=%ROOT%data\raw\binance\BTCBRL"

echo.
echo [sync] Root: %ROOT%
echo [sync] Source (Binance): %SRC_BINANCE%
echo [sync] Target (Binance): %DST_BINANCE%

if not exist "%SRC_BINANCE%" (
  echo [sync] Source Binance folder not found. Nothing to sync.
  goto :MT5
)

robocopy "%SRC_BINANCE%" "%DST_BINANCE%" *.parquet /E /XO /R:2 /W:2 /Z
echo [sync] Binance sync finished. robocopy exit code: %ERRORLEVEL%

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
echo [sync] Completed.
endlocal

