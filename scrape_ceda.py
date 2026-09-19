"""Pulls 20 years of daily mandi prices from the CEDA Agmarknet API.

One request per crop-state pair, at the state level (not national, not
district) since the goal is per-state seasonality.

Resumable: each pair writes its own file under data/. If a pair already has
a file, it's skipped — rerun the script after a crash and it picks up where
it left off. Delete a specific file to force a re-pull of just that pair.

Needs an API key from https://api.ceda.ashoka.edu.in/, saved as plain text
in ~/.config/wellabs/ceda_api_key.txt (see HANDOFF.md for how it was
obtained).
"""

import json
import sys
import time
from pathlib import Path
from urllib import request, error

import crops
import ceda_map

API_BASE = "https://api.ceda.ashoka.edu.in/v1"
KEY_PATH = Path.home() / ".config" / "wellabs" / "ceda_api_key.txt"
DATA_DIR = Path(__file__).parent / "data"
FROM_DATE = "2005-01-01"
TO_DATE = "2025-12-31"
RATE_LIMIT_SLEEP = 1.5  # seconds between requests; 429 backs off further


def load_api_key():
    if not KEY_PATH.exists():
        raise SystemExit(
            f"No API key at {KEY_PATH}. See HANDOFF.md for how to get one."
        )
    return KEY_PATH.read_text().strip()


def fetch_prices(api_key, commodity_id, state_id, from_date, to_date):
    body = json.dumps(
        {
            "commodity_id": commodity_id,
            "state_id": state_id,
            "from_date": from_date,
            "to_date": to_date,
        }
    ).encode()
    req = request.Request(
        f"{API_BASE}/agmarknet/prices",
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    # 429 backoff is deliberately long: CEDA's limit took well over 10 minutes
    # to reset when we tripped it, and a 14-hour unattended run must wait it
    # out rather than die. 1+2+5+10+20+30 min = ~68 min of patience.
    RETRY_WAITS_429 = [60, 120, 300, 600, 1200, 1800]
    last_error = None
    for attempt in range(len(RETRY_WAITS_429)):
        try:
            with request.urlopen(req, timeout=90) as resp:
                payload = json.load(resp)
                return payload["output"]["data"]
        except error.HTTPError as e:
            if e.code == 429:
                wait = RETRY_WAITS_429[attempt]
                last_error = "429 rate limited"
                print(f"    rate limited, waiting {wait // 60}m before retry")
                time.sleep(wait)
                continue
            raise SystemExit(
                f"HTTP {e.code} for commodity={commodity_id} state={state_id}: {e.read()[:300]}"
            )
        except (error.URLError, ConnectionError, TimeoutError) as e:
            # Transient network/server hiccups (e.g. RemoteDisconnected) —
            # not an HTTP error response, just the connection dying mid-request.
            last_error = f"{type(e).__name__}: {e}"
            time.sleep(15 * (attempt + 1))
            continue
    raise SystemExit(
        f"Gave up on commodity={commodity_id} state={state_id} "
        f"after {len(RETRY_WAITS_429)} attempts. Last error: {last_error}"
    )


def safe_name(s):
    return s.replace(" ", "_").replace("/", "-")


def main():
    # --crops Onion,Wheat,Tomato restricts the pull to a subset, e.g. for a
    # trial run before committing to all 36 crops x 32 states.
    crop_filter = None
    for arg in sys.argv[1:]:
        if arg.startswith("--crops="):
            crop_filter = set(arg.split("=", 1)[1].split(","))
    target_crops = crop_filter if crop_filter else crops.CROPS.keys()

    api_key = load_api_key()
    DATA_DIR.mkdir(exist_ok=True)

    pairs = [
        (crop, state)
        for crop in target_crops
        for state in crops.STATE_ZONE
        if ceda_map.COMMODITY_ID.get(crop) is not None
    ]
    print(f"{len(pairs)} crop-state pairs to fetch ({FROM_DATE} to {TO_DATE})")

    skipped_no_mapping = sorted(
        {c for c in target_crops if ceda_map.COMMODITY_ID.get(c) is None}
    )
    if skipped_no_mapping:
        print(f"Skipping (no CEDA mapping): {skipped_no_mapping}")

    done = 0
    fetched = 0
    for crop, state in pairs:
        out_path = DATA_DIR / f"{safe_name(crop)}__{safe_name(state)}.json"
        done += 1
        if out_path.exists():
            continue

        commodity_id = ceda_map.COMMODITY_ID[crop]
        state_id = ceda_map.STATE_ID[state]
        records = fetch_prices(api_key, commodity_id, state_id, FROM_DATE, TO_DATE)

        out_path.write_text(
            json.dumps(
                {
                    "crop": crop,
                    "state": state,
                    "commodity_id": commodity_id,
                    "state_id": state_id,
                    "from_date": FROM_DATE,
                    "to_date": TO_DATE,
                    "record_count": len(records),
                    "records": records,
                }
            )
        )
        fetched += 1
        print(f"[{done}/{len(pairs)}] {crop} / {state}: {len(records)} records")
        time.sleep(RATE_LIMIT_SLEEP)

    print(
        f"Done. {fetched} pairs fetched this run, {done - fetched} already had files."
    )


if __name__ == "__main__":
    main()
