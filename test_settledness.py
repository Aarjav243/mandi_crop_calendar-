"""Tests for settledness.py -- estimating how complete a recent day's mandi
data is. Run: python test_settledness.py
"""

import settledness as se


def test_curve_uses_fallback_when_log_is_empty():
    curve = se.settle_curve(log=[])
    assert curve == se.FALLBACK_CURVE, curve


def test_curve_uses_fallback_when_a_date_has_too_few_samples():
    # Only 2 observations at age 1 -- below MIN_SAMPLES, so the fallback wins
    # even though the measured ratio (0.5) is very different.
    log = [
        {"date": "2026-09-01", "run_date": "2026-09-02", "market_rows": 100},
        {"date": "2026-09-01", "run_date": "2026-09-05", "market_rows": 200},
        {"date": "2026-09-03", "run_date": "2026-09-04", "market_rows": 50},
        {"date": "2026-09-03", "run_date": "2026-09-07", "market_rows": 100},
    ]
    curve = se.settle_curve(log)
    assert curve[1] == se.FALLBACK_CURVE[1], curve


def test_curve_replaces_fallback_once_enough_dates_are_observed():
    # 5 dates, each seen at age 1 with 80% of its eventual (age-4) count.
    log = []
    for d, final in [
        ("2026-09-01", 100),
        ("2026-09-02", 200),
        ("2026-09-03", 100),
        ("2026-09-04", 100),
        ("2026-09-05", 100),
    ]:
        log.append(
            {
                "date": d,
                "run_date": se._shift(d, 1),
                "market_rows": round(final * 0.8),
            }
        )
        log.append({"date": d, "run_date": se._shift(d, 4), "market_rows": final})
    curve = se.settle_curve(log)
    assert abs(curve[1] - 0.8) < 0.001, curve


def test_pct_reported_none_before_a_date_is_ever_fetched():
    assert se.pct_reported(0) is None
    assert se.pct_reported(-1) is None


def test_pct_reported_treats_dates_outside_the_revision_window_as_settled():
    assert se.pct_reported(se.REVISE_DAYS) == 100
    assert se.pct_reported(se.REVISE_DAYS + 10) == 100


def test_pct_reported_reads_the_fallback_curve_by_default():
    assert se.pct_reported(1) == round(se.FALLBACK_CURVE[1] * 100)


def test_record_appends_one_line_and_load_reads_it_back(tmp_path):
    log_path = tmp_path / "reporting_log.jsonl"
    se.record("2026-09-16", "2026-09-17", 419, log_path=log_path)
    se.record("2026-09-16", "2026-09-18", 500, log_path=log_path)
    rows = se.load_log(log_path)
    assert len(rows) == 2, rows
    assert rows[0] == {
        "date": "2026-09-16",
        "run_date": "2026-09-17",
        "market_rows": 419,
    }


if __name__ == "__main__":
    import inspect
    import tempfile
    from pathlib import Path

    fails = 0
    for name, fn in sorted(globals().items()):
        if not (name.startswith("test_") and callable(fn)):
            continue
        try:
            if "tmp_path" in inspect.signature(fn).parameters:
                with tempfile.TemporaryDirectory() as d:
                    fn(Path(d))
            else:
                fn()
            print("PASS", name)
        except AssertionError as e:
            fails += 1
            print("FAIL", name, e)
    raise SystemExit(fails)
