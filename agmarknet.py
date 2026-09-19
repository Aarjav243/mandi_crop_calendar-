"""Live daily mandi prices *and* arrivals from the Agmarknet 2.0 dashboard API.

Why this exists
---------------
The two feeds this replaces each had half the picture:

  * data.gov.in carries prices only, and runs a day behind Agmarknet -- on
    2026-09-19 its newest day was 09-17 (608 Onion rows) where Agmarknet had
    09-18, and even for 09-17 Agmarknet held 683 markets to its 608.
  * CEDA carried arrivals but is a frozen mirror: it stops at 2025-10-30 and
    has not moved since. Not a scraper bug -- the archive itself is dead.

Agmarknet is the source both of those were copying, and one call to it returns
price and arrivals together, per market, per day, back to 2021.

No key, no login, no captcha
----------------------------
POST /v1/dashboard-data/ is the public endpoint behind the homepage's live
report. HANDOFF.md says Agmarknet 2.0 is "behind an official login and encrypts
its request bodies" -- that is true of daily-price-arrival/report, which does
demand a captcha, but not of this one. Verified: no Authorization header, no
cookie, plain JSON in and out.

Late reporting is the real accuracy trap
----------------------------------------
Mandis file late. Measured on Onion, asking for each day in turn:

    1 day old   548 markets     4 days old  667
    2 days old  683             9 days old  714
    3 days old  713            11 days old  716

So a day is ~75% reported when it is one day old and only settles around three.
Fetching sooner without re-fetching would freeze a three-quarters-empty day into
the history for good. That is why dates_to_fetch always reaches REVISE_DAYS
back and the callers overwrite rather than append -- we take the fresh day *and*
correct it as the stragglers arrive.
"""

import datetime as dt
import json
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict

import ceda_map
import crops

API = "https://api.agmarknet.gov.in/v1/dashboard-data/"
FILTERS = "https://api.agmarknet.gov.in/v1/daily-price-arrival/filters"

# "price trend" in the UI: one row per market per day, carrying both the modal
# price (as_on) and that market's arrivals (cumm_arr).
DASHBOARD = "cumm_data_sp"

# The API's magic "everything" ids, lifted from the site's own bundle. Passing
# a real empty list instead returns nothing.
ALL_GROUPS, ALL_STATES, ALL_DISTRICTS = 100000, 100006, 100007
ALL_MARKETS, ALL_MARKET_TYPES, ALL_GRADES = 100009, 100004, 100011

PAGE = 500
TIMEOUT = 180
THROTTLE = 1.0
RETRIES = 5
RETRY_WAIT = 5.0
MAX_PAGES = 40  # ~20k market-rows for one crop-day; far above the ~750 seen

# Agmarknet publishes a day sooner than data.gov.in, so we can take yesterday.
LAG_DAYS = 1
# ...but only because we come back for it. See the reporting curve above.
REVISE_DAYS = 5

SOURCE_TAG = "agmarknet.gov.in:dashboard-data"

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Content-Type": "application/json",
    # The API is public but still checks these.
    "Origin": "https://agmarknet.gov.in",
    "Referer": "https://agmarknet.gov.in/",
}

# Agmarknet's own spelling for our crops, where it differs. Resolved against
# its 604-commodity filter list and checked by test_agmarknet.
AGMARKNET_NAME = {
    "Bajra": "Bajra(Pearl Millet/Cumbu)",
    "Barley": "Barley(Jau)",
    "Castor": "Castor Seed",
    "Coriander Leaf": "Coriander(Leaves)",
    "Dry Chilli": "Dry Chillies",
    "Ginger": "Ginger(Dry)",
    "Gram": "Bengal Gram(Gram)(Whole)",
    "Jowar": "Jowar(Sorghum)",
    "Masoor": "Lentil(Masur)(Whole)",
    "Moong": "Green Gram(Moong)(Whole)",
    "Okra": "Bhindi(Ladies Finger)",
    "Paddy": "Paddy(Common)",
    "Sesamum": "Sesamum(Sesame,Gingelly,Til)",
    "Soybean": "Soyabean",
    "Sunflower": "Sunflower/Sunflower Seed",
    "Tur/Arhar": "Red gram/Arhar/Tur(whole)",
    "Urad": "Black Gram(Urd Beans)(Whole)",
}

# Agmarknet's commodity ids. NOT interchangeable with ceda_map.COMMODITY_ID --
# only 11 of 18 shared names agree, so copying those across would quietly fetch
# the wrong crop. Keep this table separate.
CROP_ID = {
    "Apple": 17,
    "Bajra": 28,
    "Banana": 19,
    "Barley": 29,
    "Brinjal": 32,
    "Cabbage": 126,
    "Castor": 106,
    "Cauliflower": 31,
    "Coriander Leaf": 39,
    # "Coriander Seed": Agmarknet has no dhania-seed commodity, only
    # Coriander(Leaves) -- the same gap ceda_map.py records for CEDA.
    "Cotton": 15,
    "Dry Chilli": 113,
    "Garlic": 25,
    "Ginger": 27,
    "Gram": 6,
    "Green Chilli": 73,
    "Groundnut": 10,
    "Jowar": 5,
    "Maize": 4,
    "Mango": 20,
    "Masoor": 52,
    "Moong": 9,
    "Mustard": 12,
    "Okra": 71,
    "Onion": 23,
    "Paddy": 2,
    "Potato": 24,
    "Sesamum": 11,
    "Soybean": 13,
    "Sugarcane": 122,
    "Sunflower": 14,
    "Tomato": 65,
    "Tur/Arhar": 45,
    "Turmeric": 35,
    "Urad": 8,
    "Wheat": 1,
}

# Agmarknet's spelling -> ours, for the five that differ. Ladakh is absent from
# Agmarknet entirely (no reporting mandis), so it simply never appears.
STATE_NAME_FIX = {
    "Chattisgarh": "Chhattisgarh",
    "NCT of Delhi": "Delhi",
    "Keralam": "Kerala",
    "Pondicherry": "Puducherry",
}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


_STATE_BY_NORM = {norm(s): s for s in ceda_map.STATE_ID}
for _wrong, _right in STATE_NAME_FIX.items():
    _STATE_BY_NORM[norm(_wrong)] = _right


def project_state(name):
    """Agmarknet's state spelling -> ours, or None if we don't track it."""
    return _STATE_BY_NORM.get(norm(str(name or "")))


def agmarknet_name(crop):
    """Our crop name -> Agmarknet's spelling for it."""
    return AGMARKNET_NAME.get(crop, crop)


_CROP_BY_NORM = {norm(agmarknet_name(c)): c for c in CROP_ID}


def project_crop(name):
    """Agmarknet's commodity spelling -> ours, or None if we don't track it."""
    return _CROP_BY_NORM.get(norm(str(name or "")))


def _post(body, tries=RETRIES):
    """One dashboard-data call, riding out transient gateway errors."""
    data = json.dumps(body).encode()
    for attempt in range(tries):
        try:
            req = urllib.request.Request(API, data=data, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                return json.loads(r.read().decode())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
            if attempt == tries - 1:
                raise
            time.sleep(RETRY_WAIT * 2**attempt)
    raise RuntimeError("unreachable")


def _fetch(date, commodity_ids):
    """Paged market rows for one date and a set of commodity ids.

    Returns [] for a day the API has no data for (a future date, or one too
    fresh to have been published) rather than raising -- the caller logs the
    empty day and carries on.
    """
    rows = []
    for page in range(1, MAX_PAGES + 1):
        body = _post(
            {
                "dashboard": DASHBOARD,
                "date": date.isoformat(),
                "group": [ALL_GROUPS],
                "commodity": list(commodity_ids),
                "state": [ALL_STATES],
                "district": [ALL_DISTRICTS],
                "market": [ALL_MARKETS],
                "market_type": [ALL_MARKET_TYPES],
                "grades": [ALL_GRADES],
                "type": "modal",
                "format": "json",
                "page": page,
                "limit": PAGE,
            }
        )
        # A day with nothing published answers status false / "No data
        # available." rather than an empty record list.
        if not body.get("status") or body.get("status") == "False":
            break
        data = body.get("data")
        rows.extend((data.get("records") or []) if isinstance(data, dict) else [])
        if not (body.get("pagination") or {}).get("next_page"):
            break
        time.sleep(THROTTLE)
    return rows


def fetch_day(date):
    """Every market row for every crop we track, on one date.

    The API takes a list of commodity ids, so the whole day comes back in one
    paged query -- about 7,400 rows over 15 pages in ~25s. Asking crop by crop
    instead costs ~300s for the same rows, which is the difference between a
    backfill of a year taking two hours and taking nine.
    """
    return _fetch(date, sorted(set(CROP_ID.values())))


def fetch_crop_day(crop, date):
    """Every market's price and arrivals for one crop on one date."""
    cmdt = CROP_ID.get(crop)
    return [] if cmdt is None else _fetch(date, [cmdt])


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def to_buckets_all(rows, date, crop=None):
    """Market rows -> ({(crop,state,date): price}, {(crop,state,date): tonnes}).

    Price is the mean across a state's markets WEIGHTED by each market's own
    arrivals -- a mandi that moved 2,000 tonnes should count for more than one
    that moved 2. A plain (unweighted) mean was the old behaviour and is
    measurably wrong: on Onion/Gujarat it overstated the day's price by 12%
    against the tonnage-weighted figure. Where no priced market reported any
    arrivals at all, there is nothing to weight by, so that state-day falls
    back to a plain mean rather than losing the price. Arrivals themselves are
    the *sum*: verified against Agmarknet's own state totals (cumm_arrival_sp),
    where summing the markets reproduced every state to the paisa.

    `crop` forces every row to one crop (for fetch_crop_day, whose rows are all
    one commodity anyway). Left None, each row is sorted by its own cmdt_name,
    which is what a whole-day fetch needs.
    """
    priced = defaultdict(list)  # key -> [(price, weight), ...]
    volumes = defaultdict(float)
    seen_volume = set()
    iso = date.isoformat()
    for row in rows:
        state = project_state(row.get("state_name"))
        this_crop = crop or project_crop(row.get("cmdt_name"))
        if not state or not this_crop:
            continue
        key = (this_crop, state, iso)
        price = _num(row.get("as_on"))
        qty = _num(row.get("cumm_arr"))
        # Zero is a missing-data placeholder for price, the same rule the
        # data.gov.in path applies. A market with no arrivals figure at all
        # weighs zero rather than being dropped outright.
        if price is not None and price > 0:
            priced[key].append((price, qty or 0.0))
        if qty is not None:
            # Zero arrivals is a real observation (market open, nothing came
            # in), so it counts -- but only make a bucket exist if some row
            # actually carried a number.
            volumes[key] += qty
            seen_volume.add(key)
    prices = {}
    for key, pairs in priced.items():
        total_qty = sum(q for _, q in pairs)
        if total_qty > 0:
            prices[key] = sum(p * q for p, q in pairs) / total_qty
        else:
            prices[key] = sum(p for p, _ in pairs) / len(pairs)
    return (
        prices,
        {k: volumes[k] for k in seen_volume},
    )


def to_buckets(rows, date, crop):
    """Single-crop form of to_buckets_all."""
    return to_buckets_all(rows, date, crop)


def dates_to_fetch(today=None, have=None, revise_days=None):
    """Which days to pull: yesterday, plus a look-back to correct late filings.

    `have` is the newest date already on disk. We never simply resume from it:
    the last few days on disk are provisional until the stragglers report, so
    the window always reaches REVISE_DAYS back even when the data looks current.
    """
    today = today or dt.date.today()
    revise = REVISE_DAYS if revise_days is None else revise_days
    end = today - dt.timedelta(days=LAG_DAYS)
    start = end - dt.timedelta(days=revise - 1)
    if have:
        # Resuming after downtime: reach further back, but never let a disk
        # date that runs ahead of us shrink the revision window.
        start = min(start, dt.date.fromisoformat(have) + dt.timedelta(days=1))
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1)]


def refresh_filters():
    """Re-read Agmarknet's commodity and state tables.

    Maintenance helper: run it when a crop stops returning rows, to see whether
    they have renamed it.
    """
    headers = {k: v for k, v in HEADERS.items() if k != "Content-Type"}
    req = urllib.request.Request(FILTERS, headers=headers)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode())["data"]


def unmapped_crops():
    return sorted(set(crops.CROPS) - set(CROP_ID))
