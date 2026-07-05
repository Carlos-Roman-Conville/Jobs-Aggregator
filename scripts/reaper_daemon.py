"""
Reaper daemon - releases stale SKIP LOCKED claims so crashed agents don't
strand rows. Runs in its own terminal (or as a Windows scheduled task / cron).

Run with:
    POSTGRES_PORT=5433 POSTGRES_USER=postgres POSTGRES_PASSWORD=... \
    python scripts/reaper_daemon.py

Logs every reap to stdout. Ctrl-C to stop.

Safe to run multiple instances concurrently (the UPDATE is atomic and only
touches expired-lease rows).
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

# Allow running standalone without PYTHONPATH gymnastics.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from job_pipeline.db import list_active_claims, reap_stale_claims  # noqa: E402

REAP_INTERVAL_SECONDS = 60
STATUS_INTERVAL_SECONDS = 15 * 60  # print "still alive" + claim snapshot every 15min


def _ts() -> str:
    return datetime.now().isoformat(timespec="seconds")


def main() -> int:
    print(f"[{_ts()}] reaper daemon started; interval={REAP_INTERVAL_SECONDS}s")
    last_status = 0.0
    try:
        while True:
            try:
                reaped = reap_stale_claims()
                for item_id, prior, reverted_to in reaped:
                    print(f"[{_ts()}] REAPED item={item_id} {prior} -> {reverted_to}")
                now = time.time()
                if now - last_status > STATUS_INTERVAL_SECONDS:
                    active = list_active_claims()
                    if active:
                        print(
                            f"[{_ts()}] {len(active)} active claim(s): "
                            + ", ".join(
                                f"#{c['id']}({c['claimed_by']})" for c in active[:10]
                            )
                        )
                    else:
                        print(f"[{_ts()}] no active claims")
                    last_status = now
            except Exception as e:
                print(f"[{_ts()}] ERROR in reap cycle: {e}", file=sys.stderr)
            time.sleep(REAP_INTERVAL_SECONDS)
    except KeyboardInterrupt:
        print(f"\n[{_ts()}] reaper daemon stopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
