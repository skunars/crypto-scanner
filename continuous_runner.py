import json
import os
import subprocess
import time
import urllib.request
from datetime import datetime, timezone

ACTIVE_MINUTES = int(os.getenv("RUNNER_DURATION_MINUTES", "345"))
PREPARE_AT_MINUTES = int(os.getenv("RUNNER_PREPARE_AT_MINUTES", "335"))
SCAN_SECONDS = int(os.getenv("SCAN_SECONDS", "180"))
CHECKPOINT_SECONDS = int(os.getenv("CHECKPOINT_SECONDS", "60"))
POLL_SECONDS = int(os.getenv("STANDBY_POLL_SECONDS", "10"))
ROLE = os.getenv("RUNNER_ROLE", "active").lower()
STATE_FILE = "data/runner_state.json"
PAPER_FILE = "paper_trades.json"


def now():
    return datetime.now(timezone.utc).isoformat()


def telegram(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    try:
        body = json.dumps({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=8) as response:
            return 200 <= response.status < 300
    except Exception as exc:
        print(f"Telegram notification failed: {exc}")
        return False


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = json.load(handle)
            return value
    except Exception:
        return default


def load_state():
    value = load_json(STATE_FILE, {})
    return value if isinstance(value, dict) else {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, STATE_FILE)


def trade_snapshot():
    trades = load_json(PAPER_FILE, [])
    if not isinstance(trades, list):
        return {}
    return {f"{t.get('symbol')}|{t.get('entry_time')}|{t.get('side')}": t for t in trades if isinstance(t, dict)}


def notify_trade_changes(before, after):
    for key, trade in after.items():
        if key not in before and trade.get("status") == "OPEN":
            telegram(f"CRYPTO SCANNER\nPAPER OPEN\n{trade.get('symbol')} | score={trade.get('last_score')} | entry={trade.get('entry_price')} | SL={trade.get('initial_sl')}")
    for key, trade in after.items():
        old = before.get(key, {})
        if old.get("status") == "OPEN" and trade.get("status") == "CLOSED":
            telegram(f"CRYPTO SCANNER\nPAPER CLOSE\n{trade.get('symbol')} | reason={trade.get('exit_reason')} | net={trade.get('net_pnl_tl', 0):.2f} TL")


def git(*args, check=True):
    return subprocess.run(["git", *args], text=True, capture_output=True, check=check)


def checkpoint(state):
    """Durable checkpoint with race-safe retry; never rebase a live runner."""
    save_state(state)
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    for attempt in range(6):
        try:
            git("fetch", "origin", "main")
            git("reset", "--hard", "origin/main")
            save_state(state)
            git("add", STATE_FILE, PAPER_FILE)
            if git("diff", "--cached", "--quiet", check=False).returncode == 0:
                return True
            git("commit", "-m", f"Continuous scanner checkpoint: {state.get('cycle', 0)}")
            pushed = git("push", "origin", "HEAD:main", check=False)
            if pushed.returncode == 0:
                return True
            print(f"checkpoint push race, retry {attempt + 1}")
        except subprocess.CalledProcessError as exc:
            print(f"checkpoint attempt {attempt + 1} failed: {exc}")
        time.sleep(min(2 ** attempt, 10))
    print("WARNING: durable checkpoint not confirmed after retries")
    return False


def dispatch_standby():
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY", "skunars/crypto-scanner")
    if not token:
        raise RuntimeError("GITHUB_TOKEN/GH_TOKEN bulunamadı")
    payload = json.dumps({"event_type": "crypto_standby", "client_payload": {
        "duration_minutes": str(ACTIVE_MINUTES), "prepare_at_minutes": str(PREPARE_AT_MINUTES),
        "parent_run_id": os.getenv("GITHUB_RUN_ID", ""), "parent_generation": int(load_state().get("generation", 0))
    }}).encode()
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/dispatches", data=payload,
        headers={"Accept": "application/vnd.github+json", "Authorization": f"Bearer {token}",
                 "X-GitHub-Api-Version": "2026-03-10", "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as response:
        if response.status not in (200, 201, 204):
            raise RuntimeError(f"Standby repository_dispatch HTTP {response.status}")


def sync_main():
    git("fetch", "origin", "main")
    git("reset", "--hard", "origin/main")


def run_scan():
    before = trade_snapshot()
    result = subprocess.run(["python", "scanner_v2.py"], text=True)
    if result.returncode != 0:
        raise RuntimeError(f"scanner_v2.py exit={result.returncode}")
    notify_trade_changes(before, trade_snapshot())


def active():
    sync_main()
    state = load_state()
    generation = int(state.get("generation", 0)) + (1 if state.get("status") == "handoff_ready" else 0)
    handoffs = int(state.get("handoff_count", 0))
    started = time.monotonic()
    last_scan = 0.0
    last_checkpoint = 0.0
    dispatched = False
    cycle = int(state.get("cycle", 0))
    state.update({"version": 1, "role": "active", "status": "running", "generation": generation,
                  "active_minutes": ACTIVE_MINUTES, "prepare_at_minutes": PREPARE_AT_MINUTES,
                  "started_at": now(), "last_checkpoint_at": now(), "last_scan_at": state.get("last_scan_at"),
                  "handoff_count": handoffs, "error": None})
    checkpoint(state)
    telegram(f"CRYPTO SCANNER\nACTIVE başladı | generation={generation}")
    duration = ACTIVE_MINUTES * 60
    prepare = min(PREPARE_AT_MINUTES * 60, duration - 30)
    while time.monotonic() - started < duration:
        elapsed = time.monotonic() - started
        try:
            if not dispatched and elapsed >= prepare:
                dispatch_standby(); dispatched = True
                state["status"] = "standby_dispatched"; state["standby_dispatched_at"] = now(); checkpoint(state)
                telegram(f"CRYPTO SCANNER\nStandby B dispatch edildi | generation={generation}")
            if time.monotonic() - last_scan >= SCAN_SECONDS:
                run_scan(); last_scan = time.monotonic(); cycle += 1
                state["cycle"] = cycle; state["last_scan_at"] = now(); state["status"] = "running"
            if time.monotonic() - last_checkpoint >= CHECKPOINT_SECONDS:
                state["last_checkpoint_at"] = now(); checkpoint(state); last_checkpoint = time.monotonic()
        except Exception as exc:
            state["status"] = "error"; state["error"] = str(exc); state["error_at"] = now(); checkpoint(state)
            telegram(f"CRYPTO SCANNER\nKRİTİK HATA: {exc}"); raise
        time.sleep(1)
    state["status"] = "handoff_ready"; state["handoff_ready_at"] = now(); state["handoff_count"] = handoffs + 1; checkpoint(state)
    telegram(f"CRYPTO SCANNER\nA→B HANDOFF HAZIR | generation={generation} | cycles={cycle}")


def standby():
    telegram("CRYPTO SCANNER\nSTANDBY B hazır bekliyor.")
    deadline = time.monotonic() + (ACTIVE_MINUTES * 60) + 900
    while time.monotonic() < deadline:
        try:
            sync_main(); state = load_state()
            if state.get("status") == "handoff_ready":
                telegram("CRYPTO SCANNER\nB son state'i aldı, takeover başlıyor."); active(); return
        except Exception as exc:
            print(f"Standby polling error: {exc}")
        time.sleep(POLL_SECONDS)
    raise TimeoutError("Standby handoff_ready beklerken zaman aşımına uğradı")


if __name__ == "__main__":
    standby() if ROLE == "standby" else active()
