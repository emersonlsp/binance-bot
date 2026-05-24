from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUNNER = ROOT / "run_collector.py"
VENV_PYTHONW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def main() -> None:
    python_bin = VENV_PYTHONW if VENV_PYTHONW.exists() else VENV_PYTHON
    if not python_bin.exists():
        return

    log_dir = ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"collector_{datetime.now():%Y%m%d}.log"

    flags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        flags |= subprocess.DETACHED_PROCESS
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        flags |= subprocess.CREATE_NEW_PROCESS_GROUP
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        flags |= subprocess.CREATE_NO_WINDOW

    with log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(f"\n[{datetime.now().isoformat()}] launching collector\n")
        log_file.flush()
        subprocess.Popen(
            [str(python_bin), str(RUNNER)],
            cwd=str(ROOT),
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )


if __name__ == "__main__":
    main()

