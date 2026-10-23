"""Start through the project's virtual environment without terminating existing sessions."""
import os
from pathlib import Path
import subprocess
import sys

if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    python = root / ".venv" / "Scripts" / "python.exe"
    if python.exists():
        raise SystemExit(subprocess.call([str(python), "-m", "services.launcher", *sys.argv[1:]], cwd=root))
    raise SystemExit(subprocess.call([os.environ.get("COMSPEC", "cmd.exe"), "/c",
        str(root / "启动乌托邦高中物理题库助手.bat"), *sys.argv[1:]], cwd=root))
