"""One-off probe: can /agmarknet/quantities return more than one state per
request? If yes, the remaining ~950 crop-state pairs collapse to ~36 requests
and the 40-req/hour quota stops mattering. Retries until the quota frees up,
because rate limiting happens BEFORE request validation (a 429 tells you
nothing about the schema). Delete this file once the answer is recorded.
"""

import json
import sys
import time
from pathlib import Path
from urllib import error, request

import ceda_map

KEY = (Path.home() / ".config" / "wellabs" / "ceda_api_key.txt").read_text().strip()


def post(body):
    req = request.Request(
        "https://api.ceda.ashoka.edu.in/v1/agmarknet/quantities",
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    try:
        with request.urlopen(req, timeout=90) as r:
            data = json.load(r)["output"]["data"]
            states = {d.get("census_state_id") for d in data}
            return f"200 OK records={len(data)} distinct_states={sorted(states)}"
    except error.HTTPError as e:
        return f"HTTP {e.code}: {e.read()[:300].decode(errors='replace')}"
    except Exception as e:
        return f"{type(e).__name__}: {e}"


CID = ceda_map.COMMODITY_ID["Wheat"]
DATES = {"from_date": "2024-01-01", "to_date": "2024-01-07"}
VARIANTS = {
    "no state_id": {"commodity_id": CID, **DATES},
    "state_id list": {
        "commodity_id": CID,
        "state_id": [ceda_map.STATE_ID["Punjab"], ceda_map.STATE_ID["Haryana"]],
        **DATES,
    },
    "state_id 0": {"commodity_id": CID, "state_id": 0, **DATES},
}

for name, body in VARIANTS.items():
    while True:
        result = post(body)
        if "429" not in result:
            break
        print(f"{name}: still rate limited, sleeping 5m", flush=True)
        time.sleep(300)
    print(f"{name}: {result}", flush=True)
    time.sleep(95)
