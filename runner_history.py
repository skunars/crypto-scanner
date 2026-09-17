import json
import os
from datetime import datetime, timezone
from pathlib import Path

STATE_PATH = Path("data/runner_state.json")
HISTORY_PATH = Path("data/runner_history.jsonl")


def load_state():
    if not STATE_PATH.exists():
        return {}
    raw = STATE_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    obj = json.loads(raw)
    # Some tooling/API representations wrap the actual JSON in a content field.
    if isinstance(obj, dict) and isinstance(obj.get("content"), str):
        try:
            obj = json.loads(obj["content"])
        except json.JSONDecodeError:
            pass
    return obj if isinstance(obj, dict) else {}


def main():
    state = load_state()
    run_id = os.getenv("GITHUB_RUN_ID", "")
    run_number = os.getenv("GITHUB_RUN_NUMBER", "")
    role = os.getenv("RUNNER_ROLE", state.get("role", ""))

    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = set()
    if HISTORY_PATH.exists():
        for line in HISTORY_PATH.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
                if item.get("run_id"):
                    existing.add(str(item["run_id"]))
            except json.JSONDecodeError:
                continue

    if run_id and run_id in existing:
        return

    now = datetime.now(timezone.utc).isoformat()
    record = {
        "recorded_at": now,
        "run_id": run_id,
        "run_number": run_number,
        "job": os.getenv("GITHUB_JOB", ""),
        "role": role,
        "status": state.get("status"),
        "error": state.get("error"),
        "generation": state.get("generation"),
        "cycle": state.get("cycle"),
        "started_at": state.get("started_at"),
        "last_checkpoint_at": state.get("last_checkpoint_at"),
        "last_scan_at": state.get("last_scan_at"),
        "active_minutes": state.get("active_minutes"),
        "prepare_at_minutes": state.get("prepare_at_minutes"),
        "handoff_count": state.get("handoff_count"),
        "standby_dispatched_at": state.get("standby_dispatched_at"),
        "handoff_ready_at": state.get("handoff_ready_at"),
        "takeover_claimed_at": state.get("takeover_claimed_at"),
        "takeover_run_id": state.get("takeover_run_id"),
    }
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    main()
