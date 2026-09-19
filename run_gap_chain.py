"""Finish the arrivals gap (2025-11-11 .. 2026-09-10), one month at a time.

Why month-sized pieces rather than the two big ranges this started as:
backfill_agmarknet.py aborts a whole range on one failed day by design, and
2025-11-11 timed out sixteen minutes in, taking the other fifty days with it.
A month is small enough that a bad day costs a month, not half a year, and
each month gets three attempts before we give up on it and move on.

Ranges must not overlap in time, and nothing here may run while another
backfill is going: store.py rewrites a whole JSON file per pair, so two
writers would lose each other's rows. Hence the wait on the marker line the
running job prints when it finishes.
"""

import datetime as dt
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
RUNNING_MARKER = "RANGE 2026-06-12 .. 2026-09-08 exit="
RUNNING_LOG = HERE / "backfill_gap.out"

# The arrivals hole, minus the 2026-06-12..2026-09-08 stretch already in flight.
MONTHS = [
    ("2025-11-11", "2025-11-30"),
    ("2025-12-01", "2025-12-31"),
    ("2026-01-01", "2026-01-31"),
    ("2026-02-01", "2026-02-28"),
    ("2026-03-01", "2026-03-31"),
    ("2026-04-01", "2026-04-30"),
    ("2026-05-01", "2026-05-31"),
    ("2026-06-01", "2026-06-11"),
    ("2026-09-09", "2026-09-10"),
]


def wait_for_running_job():
    """Block until the in-flight backfill prints its exit marker."""
    while True:
        try:
            if RUNNING_MARKER in RUNNING_LOG.read_text(
                encoding="utf-8", errors="replace"
            ):
                return
        except FileNotFoundError:
            return
        time.sleep(60)


def main():
    wait_for_running_job()
    print(
        f"{dt.datetime.now():%H:%M:%S} earlier backfill finished; starting queued months",
        flush=True,
    )

    failed = []
    for a, b in MONTHS:
        for attempt in (1, 2, 3):
            print(f"=== {a} .. {b}  attempt {attempt} ===", flush=True)
            rc = subprocess.call(
                [
                    sys.executable,
                    "-u",
                    str(HERE / "backfill_agmarknet.py"),
                    "--from",
                    a,
                    "--to",
                    b,
                    "--chunk",
                    "10",
                ],
                cwd=str(HERE),
            )
            print(f"=== {a} .. {b}  attempt {attempt} exit={rc} ===", flush=True)
            if rc == 0:
                break
            time.sleep(120)  # let a flaky upstream settle
        else:
            failed.append(f"{a}..{b}")

    print("ALL QUEUED MONTHS DONE", flush=True)
    print("still incomplete: " + (", ".join(failed) if failed else "none"), flush=True)


if __name__ == "__main__":
    main()
