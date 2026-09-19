"""Tests for build_html.py's "right now" snapshots. Run: python test_build_html.py

Covers latest_volume_snapshot, the arrivals counterpart to latest_snapshot:
the page showed a 52-week typical-arrivals curve but no figure for what
actually arrived on the latest reported day.
"""

import datetime as dt

import build_html as bh


def _series(pairs):
    return [(dt.date.fromisoformat(d), q) for d, q in pairs]


NEWEST = dt.date(2026, 9, 18)


def test_no_series_means_no_snapshot():
    assert bh.latest_volume_snapshot([], {1: 100.0}, NEWEST) is None


def test_a_pair_that_went_quiet_shows_nothing():
    # Last arrivals are older than STALE_DAYS -- "right now" would be a lie.
    old = NEWEST - dt.timedelta(days=bh.STALE_DAYS + 1)
    series = _series([(old.isoformat(), 500.0)])
    assert bh.latest_volume_snapshot(series, {1: 100.0}, NEWEST) is None


def test_reports_the_latest_day_and_its_tonnes():
    series = _series([("2026-09-17", 900.0), ("2026-09-18", 1200.0)])
    out = bh.latest_volume_snapshot(series, None, NEWEST)
    assert out["date"] == "2026-09-18", out
    assert out["tonnes"] == 1200, out
    assert out["week"] == bh.seasonality.iso_week(dt.date(2026, 9, 18)), out


def _flat_year(year, level, weeks=40):
    """One reading a week for `weeks` weeks of `year`, all at `level` tonnes --
    enough weeks to clear MIN_WEEKS_FOR_LEVEL so a year level can be estimated."""
    import datetime as _dt
    out = []
    for w in range(1, weeks + 1):
        out.append((_dt.date.fromisocalendar(year, w, 3), level))
    return out


FLAT_CURVE = {w: 1000.0 for w in range(1, 53)}


def test_compares_against_the_typical_for_that_week():
    # A year sitting at its own normal level; the last day is 50% heavier
    # than that year's baseline, so +50%.
    series = _flat_year(2026, 1000.0)
    series.append((dt.date(2026, 9, 18), 1500.0))
    out = bh.latest_volume_snapshot(series, FLAT_CURVE, NEWEST)
    assert out["pct"] == 50, out


def test_a_level_shift_is_not_reported_as_a_busy_day():
    # The real Cauliflower case: the whole recent year runs 4x the multi-year
    # median because more mandis report now. A day that is ordinary FOR THAT
    # YEAR must read near 0%, not +300%.
    series = _flat_year(2026, 4000.0)
    series.append((dt.date(2026, 9, 18), 4000.0))
    out = bh.latest_volume_snapshot(series, FLAT_CURVE, NEWEST)
    assert abs(out["pct"]) <= 5, f"level shift leaked into the figure: {out}"


def test_no_curve_means_the_tonnes_stand_alone():
    # Still worth showing the raw figure; just no "vs usual" claim.
    series = _series([("2026-09-18", 1500.0)])
    out = bh.latest_volume_snapshot(series, None, NEWEST)
    assert out["tonnes"] == 1500, out
    assert "pct" not in out and "normal" not in out, out


def test_a_year_too_thin_to_pin_a_level_gets_no_comparison():
    # One reading is not enough to say what this year's baseline is, so the
    # honest output is the tonnes alone.
    series = _series([("2026-09-18", 1500.0)])
    out = bh.latest_volume_snapshot(series, FLAT_CURVE, NEWEST)
    assert out["tonnes"] == 1500, out
    assert "pct" not in out, out


def test_zero_typical_is_not_divided_by():
    series = _flat_year(2026, 1000.0)
    series.append((dt.date(2026, 9, 18), 1500.0))
    out = bh.latest_volume_snapshot(series, {w: 0.0 for w in range(1, 53)}, NEWEST)
    assert "pct" not in out, out


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        try:
            fn()
            print("PASS", name)
        except AssertionError as e:
            fails += 1
            print("FAIL", name, e)
        except Exception as e:
            fails += 1
            print("ERROR", name, type(e).__name__, e)
    raise SystemExit(fails)
