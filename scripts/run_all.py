#!/usr/bin/env python3
import os
import sys
import subprocess
import time
import platform
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENV_DIR = ROOT / ".venv"


def ensure_venv() -> Path:
    if not VENV_DIR.exists():
        print("[setup] Creating virtual environment .venv ...", flush=True)
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV_DIR)])

    if platform.system().lower().startswith("win"):
        python_bin = VENV_DIR / "Scripts" / "python.exe"
    else:
        python_bin = VENV_DIR / "bin" / "python"

    if not python_bin.exists():
        raise RuntimeError("Python executable not found in virtualenv")
    return python_bin


def pip_install(python_bin: Path):
    print("[setup] Upgrading pip, setuptools, wheel ...", flush=True)
    subprocess.check_call([str(python_bin), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"], cwd=ROOT)

    req = ROOT / "requirements.txt"
    if req.exists():
        print("[setup] Installing requirements ...", flush=True)
        subprocess.check_call([str(python_bin), "-m", "pip", "install", "-r", str(req)], cwd=ROOT)
    else:
        print("[setup] requirements.txt not found, skipping.")


def migrate(python_bin: Path):
    script = ROOT / "scripts" / "migrate_pending_to_db.py"
    if script.exists():
        print("[migrate] Migrating legacy Excel queue to SQLite ...", flush=True)
        subprocess.check_call([str(python_bin), str(script)], cwd=ROOT)
    else:
        print("[migrate] Migration script not found, skipping.")


def start_services(python_bin: Path):
    env = os.environ.copy()
    # Clean env vars that might have comments from .env parsing
    for key in list(env.keys()):
        if key.startswith(('TRADE_', 'GOOGLE_', 'CRYPTO_', 'TELEGRAM_', 'HEALTH_')):
            val = env[key]
            if '#' in val:
                # Remove comment part
                clean_val = val.split('#')[0].strip()
                env[key] = clean_val
                print(f"[env] Cleaned {key}: '{val}' -> '{clean_val}'", flush=True)

    logs_dir = ROOT / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    yf_log = open(logs_dir / "yf_stdout.log", "a", buffering=1)
    ex_log = open(logs_dir / "executor_stdout.log", "a", buffering=1)

    print("[run] Starting yf.py (signals & sheet updater) ...", flush=True)
    p1 = subprocess.Popen([str(python_bin), str(ROOT / "yf.py")], cwd=ROOT, env=env, stdout=yf_log, stderr=subprocess.STDOUT)

    # Small delay to avoid initial spike
    time.sleep(1)

    print("[run] Starting trade_executor.py (execution & batch updates) ...", flush=True)
    p2 = subprocess.Popen([str(python_bin), str(ROOT / "trade_executor.py")], cwd=ROOT, env=env, stdout=ex_log, stderr=subprocess.STDOUT)

    print("[run] Services started. Health endpoint (executor): http://localhost:%s/health" % os.getenv("HEALTH_PORT", "8080"), flush=True)
    print("[run] Logs: logs/yf_stdout.log, logs/executor_stdout.log", flush=True)

    try:
        # Wait on both; if one exits, we still keep the script alive unless Ctrl+C
        while True:
            rc1 = p1.poll()
            rc2 = p2.poll()
            if rc1 is not None:
                print(f"[run] yf.py exited with code {rc1}")
            if rc2 is not None:
                print(f"[run] trade_executor.py exited with code {rc2}")
            time.sleep(5)
    except KeyboardInterrupt:
        print("\n[run] Stopping services ...", flush=True)
        for p in (p1, p2):
            if p.poll() is None:
                p.terminate()
        try:
            p1.wait(timeout=10)
        except Exception:
            pass
        try:
            p2.wait(timeout=10)
        except Exception:
            pass
    finally:
        try:
            yf_log.close()
        except Exception:
            pass
        try:
            ex_log.close()
        except Exception:
            pass


def main():
    python_bin = ensure_venv()
    pip_install(python_bin)
    migrate(python_bin)
    start_services(python_bin)


if __name__ == "__main__":
    main()

