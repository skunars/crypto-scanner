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

    # Patch only the decrypted runtime copy so the scanner is launched
    # from the same decrypted /dev/shm payload tree.
    runner_text = runner.read_text()
    old_import = "import subprocess\nimport time"
    new_import = "import subprocess\nimport sys\nimport time\nfrom pathlib import Path"
    old_launch = '    result = subprocess.run(["python", "scanner_v2.py"], text=True)'
    new_launch = '    runtime_source = os.getenv("RUNTIME_SOURCE_DIR")\n    scanner_path = str(Path(runtime_source) / "scanner_v2.py") if runtime_source else "scanner_v2.py"\n    result = subprocess.run([sys.executable, scanner_path], text=True)'

    if old_launch in runner_text:
        runner_text = runner_text.replace(old_import, new_import).replace(old_launch, new_launch)
        runner.write_text(runner_text)
    elif "RUNTIME_SOURCE_DIR" not in runner_text or "scanner_path" not in runner_text:
        raise SystemExit("Encrypted continuous runner has an unknown scanner launch pattern")

    env = os.environ.copy()
    env["PYTHONPATH"] = str(runtime)
    result = subprocess.run([sys.executable, str(runner)], cwd=repo, env=env)
    raise SystemExit(result.returncode)
finally:
    shutil.rmtree(runtime, ignore_errors=True)
