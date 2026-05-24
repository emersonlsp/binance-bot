from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VENV_PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"


def main() -> None:
    if not VENV_PYTHON.exists():
        return
    env = dict(**__import__("os").environ)
    src_path = str(ROOT / "src")
    current_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src_path if not current_pp else f"{src_path};{current_pp}"

    cmd = f'"{VENV_PYTHON}" -m binance_bot.paper.live_runner'
    subprocess.Popen(
        ["cmd.exe", "/k", cmd],
        cwd=str(ROOT),
        env=env,
        creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    )


if __name__ == "__main__":
    main()
