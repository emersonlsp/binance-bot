from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUNNER = ROOT / "src" / "binance_bot" / "paper" / "live_runner.py"
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def main() -> None:
    if not VENV_PYTHON.exists() or not RUNNER.exists():
        return
    subprocess.Popen(
        [str(VENV_PYTHON), str(RUNNER)],
        cwd=str(ROOT),
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )


if __name__ == "__main__":
    main()

