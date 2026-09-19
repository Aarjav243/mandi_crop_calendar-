"""Tests for agmarknet.py -- the live price+arrival feed.

No network: synthetic dashboard rows only. Run: python test_agmarknet.py
"""

import datetime as dt

import agmarknet as ag


def _row(state, market, price, qty):
    """One record shaped like dashboard-data's cumm_data_sp rows."""
    return {
        "state_name": state,
        "market_name": market,
        "cmdt_name": "Onion",
        "as_on": price,
        "cumm_arr": qty,
    }


def test_crop_map_covers_every_crop_but_coriander_seed():
    # Coriander Seed has no Agmarknet commodity, exactly as it has no CEDA one
    # (see ceda_map.py). Any *other* gap means a rename we haven't caught.
    import crops

    missing = sorted(set(crops.CROPS) - set(ag.CROP_ID))
    assert missing == ["Coriander Seed"], missing


def test_state_names_map_to_project_spellings():
    # Agmarknet spells five of them differently; unmapped states must not
    # silently vanish into a None bucket.
    assert ag.project_state("Chattisgarh") == "Chhattisgarh"
    assert ag.project_state("NCT of Delhi") == "Delhi"
    assert ag.project_state("Keralam") == "Kerala"
    assert ag.project_state("Pondicherry") == "Puducherry"
    assert ag.project_state("Maharashtra") == "Maharashtra"
    assert ag.project_state("Nowhere") is None


def test_price_is_weighted_by_arrivals_arrivals_still_sum():
    # The whole point: a state's arrivals are the sum of its markets. Its price
    # is their mean WEIGHTED by how much each market actually sold -- a mandi
    # that moved 2 tonnes shouldn't count the same as one that moved 2,000.
    # Plain mean would give 1500.0; the tonnage-weighted mean is pulled toward
    # Lasalgaon, the bigger market.
    rows = [
        _row("Maharashtra", "Lasalgaon", "1000.00", "100.00"),
        _row("Maharashtra", "Pune", "2000.00", "50.00"),
    ]
    price, volume = ag.to_buckets(rows, dt.date(2026, 9, 16), "Onion")
    got = price[("Onion", "Maharashtra", "2026-09-16")]
    assert abs(got - 1333.33) < 0.01, got
    assert volume[("Onion", "Maharashtra", "2026-09-16")] == 150.0, volume


def test_price_falls_back_to_plain_mean_when_nothing_reported_arrivals():
    # If every market that reported a price reported no arrivals (or didn't
    # report arrivals at all), there is no tonnage to weight by. Falling back
    # to a plain mean beats dropping the price for that state entirely.
    rows = [
        _row("Maharashtra", "Lasalgaon", "1000.00", "0"),
        _row("Maharashtra", "Pune", "2000.00", "0"),
    ]
    price, volume = ag.to_buckets(rows, dt.date(2026, 9, 16), "Onion")
    assert price[("Onion", "Maharashtra", "2026-09-16")] == 1500.0, price
    assert volume[("Onion", "Maharashtra", "2026-09-16")] == 0.0, volume


def test_zero_and_missing_prices_are_dropped_but_zero_arrivals_kept():
    # Zero price is a missing-data placeholder (same rule as daily_update).
    # Zero *arrivals* is a real fact: the mandi opened and nothing came in.
    rows = [
        _row("Maharashtra", "A", "0", "10.00"),
        _row("Maharashtra", "B", None, "20.00"),
        _row("Maharashtra", "C", "1200.00", "0"),
    ]
    price, volume = ag.to_buckets(rows, dt.date(2026, 9, 16), "Onion")
    assert price[("Onion", "Maharashtra", "2026-09-16")] == 1200.0, price
    assert volume[("Onion", "Maharashtra", "2026-09-16")] == 30.0, volume


def test_unknown_state_is_skipped_not_crashed():
    rows = [_row("Atlantis", "X", "100", "1"), _row("Goa", "Y", "200", "2")]
    price, volume = ag.to_buckets(rows, dt.date(2026, 9, 16), "Onion")
    assert list(price) == [("Onion", "Goa", "2026-09-16")], price


def test_every_crop_name_round_trips_back_to_the_project_name():
    # fetch_day asks for all 35 commodities at once, so rows come back tagged
    # with Agmarknet's spelling and have to be sorted back into our crops. A
    # name we can't reverse would silently drop that crop from the whole run.
    for crop in ag.CROP_ID:
        assert ag.project_crop(ag.agmarknet_name(crop)) == crop, crop
    assert ag.project_crop("Sugar Snap Peas") is None


def test_to_buckets_all_splits_a_mixed_day_by_crop():
    rows = [
        _row("Maharashtra", "Lasalgaon", "1000.00", "100.00"),
        dict(_row("Maharashtra", "Pune", "500.00", "20.00"), cmdt_name="Potato"),
        dict(
            _row("Goa", "Mapusa", "900.00", "5.00"),
            cmdt_name="Bhindi(Ladies Finger)",
        ),
    ]
    price, volume = ag.to_buckets_all(rows, dt.date(2026, 9, 16))
    assert price[("Onion", "Maharashtra", "2026-09-16")] == 1000.0, price
    assert price[("Potato", "Maharashtra", "2026-09-16")] == 500.0, price
    assert volume[("Okra", "Goa", "2026-09-16")] == 5.0, volume


def test_to_buckets_all_ignores_commodities_we_do_not_track():
    rows = [dict(_row("Goa", "M", "1", "1"), cmdt_name="Sugar Snap Peas")]
    price, volume = ag.to_buckets_all(rows, dt.date(2026, 9, 16))
    assert price == {} and volume == {}, (price, volume)


def test_revision_window_refetches_recent_days():
    # A day is only ~75% reported when it is one day old and settles around
    # three. So the nightly run must re-pull recent days, not just append new
    # ones, or an early grab freezes an incomplete day forever.
    days = ag.dates_to_fetch(today=dt.date(2026, 9, 19), have="2026-09-16")
    assert days[0] == dt.date(2026, 9, 14), days  # REVISE_DAYS back from `have`
    assert days[-1] == dt.date(2026, 9, 18), days  # yesterday, LAG_DAYS=1


def test_no_history_still_fetches_the_revision_window():
    days = ag.dates_to_fetch(today=dt.date(2026, 9, 19), have=None)
    assert days[-1] == dt.date(2026, 9, 18), days
    assert len(days) == ag.REVISE_DAYS, days


def test_never_fetches_the_future():
    days = ag.dates_to_fetch(today=dt.date(2026, 9, 19), have="2026-09-30")
    assert all(d < dt.date(2026, 9, 19) for d in days), days


def _retry_waits(code):
    """Sleep lengths _post would use if every call failed with `code`.

    Stubs out the network and the clock, so this measures the backoff policy
    rather than spending fifteen real minutes proving it.
    """
    import urllib.error
    import urllib.request

    waits = []
    real_urlopen, real_sleep = urllib.request.urlopen, ag.time.sleep

    def boom(*a, **k):
        raise urllib.error.HTTPError("u", code, "nope", {}, None)

    urllib.request.urlopen = boom
    ag.time.sleep = waits.append
    try:
        ag._post({"x": 1})
    except urllib.error.HTTPError:
        pass
    finally:
        urllib.request.urlopen = real_urlopen
        ag.time.sleep = real_sleep
    return waits


def test_a_rate_limit_backs_off_far_longer_than_a_glitch():
    # 429 means "you are asking too fast", not "that one call glitched". The
    # ordinary 5-10-20-40 ladder spends all five tries inside 75 seconds,
    # comfortably inside a limit window that outlasts it -- which is exactly
    # how a 70-minute backfill died on a single 429 at 2026-08-22.
    limited = sum(_retry_waits(429))
    glitch = sum(_retry_waits(503))
    assert limited >= 600, f"429 backoff is only {limited}s"
    assert limited > glitch * 5, f"429 {limited}s barely differs from 503 {glitch}s"


def test_an_ordinary_gateway_error_still_retries_quickly():
    # A 503 blip should not cost fifteen minutes; the fast ladder stays.
    assert sum(_retry_waits(503)) <= 120


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as e:
                fails += 1
                print("FAIL", name, e)
    raise SystemExit(fails)
