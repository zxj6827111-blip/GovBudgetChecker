"""Create and update the local Python virtual environment."""

from __future__ import annotations

import os
import subprocess
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENV_DIR = ROOT / ".venv"
VENV_PYTHON = (
    VENV_DIR / "Scripts" / "python.exe"
    if os.name == "nt"
    else VENV_DIR / "bin" / "python"
)


def run(args: list[str]) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def ensure_venv() -> None:
    if not VENV_PYTHON.exists():
        venv.EnvBuilder(with_pip=True).create(VENV_DIR)
        return

    try:
        run([str(VENV_PYTHON), "-m", "pip", "--version"])
    except subprocess.CalledProcessError:
        run([str(VENV_PYTHON), "-m", "ensurepip", "--upgrade"])


def main() -> None:
    ensure_venv()
    run([str(VENV_PYTHON), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(VENV_PYTHON), "-m", "pip", "install", "-r", "requirements-dev.txt"])
    run(
        [
            str(VENV_PYTHON),
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--no-binary=mypy",
            "mypy==1.19.1",
        ]
    )
    print(f"Python environment ready: {VENV_PYTHON}")


if __name__ == "__main__":
    main()
