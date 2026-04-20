from __future__ import annotations

import os
import sys
import time
from pathlib import Path
import subprocess
import fcntl


ROOT = Path("/Users/satojunichi/.openclaw/workspace/systems/p1")
LOCK_PATH = ROOT / "state" / "processes" / "autonomy-loop.lock"
POST_TICK_DELAY_SECONDS = 1
VERIFICATION_MODE = os.environ.get("P1_VERIFICATION_MODE") == "1"

CMD = [sys.executable, "-m", "p1_core.cli", "--root", str(ROOT)]
if VERIFICATION_MODE:
    CMD.append("--verification-mode")
CMD.append("tick")

P1_CORE_DIR = Path("/Users/satojunichi/Documents/openclaw/p1-core")


def main() -> None:
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock_file = LOCK_PATH.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"[p1-loop] another loop already holds {LOCK_PATH}", flush=True)
        sys.exit(0)

    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()

    print(f"[p1-loop] starting for {ROOT}", flush=True)
    while True:
        try:
            tick = subprocess.run(CMD, capture_output=True, text=True, cwd=P1_CORE_DIR)
            print(
                f"[p1-loop] tick rc={tick.returncode} stdout={tick.stdout.strip()} stderr={tick.stderr.strip()}",
                flush=True,
            )
        except Exception as exc:
            print(f"[p1-loop] error: {exc}", flush=True)
        time.sleep(POST_TICK_DELAY_SECONDS)


if __name__ == "__main__":
    main()
