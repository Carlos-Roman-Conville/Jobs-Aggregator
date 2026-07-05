"""CLI: drain the ingested backlog (summarize until empty or cap)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

from job_pipeline.summarize import run_summarize_all


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Summarize all ingested pipeline jobs.")
    p.add_argument("--batch-size", type=int, default=50)
    p.add_argument("--max-batches", type=int, default=100)
    p.add_argument("--max-minutes", type=float, default=45.0)
    args = p.parse_args(argv)
    out = run_summarize_all(
        batch_size=args.batch_size,
        max_batches=args.max_batches,
        max_minutes=args.max_minutes,
    )
    print(json.dumps(out, indent=2))
    return 0 if out.get("ok") and int(out.get("ingested_remaining") or 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
