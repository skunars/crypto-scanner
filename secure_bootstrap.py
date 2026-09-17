import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

if not os.environ.get("CODE_DECRYPTION_KEY"):
    raise SystemExit("CODE_DECRYPTION_KEY is required")

repo = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd())).resolve()
runtime = Path(tempfile.mkdtemp(prefix="crypto-scanner-", dir="/dev/shm"))
os.chmod(runtime, 0o700)
os.environ["RUNTIME_SOURCE_DIR"] = str(runtime)

try:
    encrypted = sorted(repo.rglob("*.py.enc"))
    if not encrypted:
        raise SystemExit("No encrypted Python payloads found")
    for src in encrypted:
        rel = src.relative_to(repo)
        if any(part.startswith(".") for part in rel.parts):
            continue
        target = runtime / rel.with_suffix("")
        target.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([
            "openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-iter", "200000",
            "-in", str(src), "-out", str(target), "-pass", "env:CODE_DECRYPTION_KEY"
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        os.chmod(target, 0o600)
    runner = runtime / "continuous_runner.py"
    if not runner.is_file():
        raise SystemExit("Encrypted continuous_runner.py payload is missing")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(runtime)
    result = subprocess.run([sys.executable, str(runner)], cwd=repo, env=env)
    raise SystemExit(result.returncode)
finally:
    shutil.rmtree(runtime, ignore_errors=True)
