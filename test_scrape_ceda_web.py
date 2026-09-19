"""Smoke test for the row-mapping and merge logic in scrape_ceda_web.py.
No network -- synthetic proxy rows only. Run: python test_scrape_ceda_web.py
"""

import scrape_ceda_web as sw


def test_to_records_price_shape():
    rows = [{"t": "2024-01-01", "p_min": 100.0, "p_max": 200.0, "p_modal": 150.0}]
    (rec,) = sw.to_records(rows, "price", commodity_id=1, state_id=3)
    # Must match what's already on disk under data/: full ISO timestamp, and
    # the district/market keys the existing files carry.
    assert rec["date"] == "2024-01-01T00:00:00.000Z", rec
    assert rec["min_price"] == 100.0 and rec["modal_price"] == 150.0, rec
    assert rec["census_state_id"] == 3 and rec["commodity_id"] == 1, rec
    assert rec["census_district_id"] is None and rec["market_id"] is None, rec


def test_to_records_volume_shape():
    rows = [{"t": "2024-01-01", "qty": 42.5}]
    (rec,) = sw.to_records(rows, "volume", commodity_id=1, state_id=3)
    assert rec["quantity"] == 42.5, rec
    assert "min_price" not in rec, rec


def test_merge_keeps_dates_fresh_lacks():
    # The Kaggle gap-fill sits in `existing` on dates CEDA has no data for --
    # a plain overwrite would silently drop it.
    existing = [
        {"date": "2023-06-01T00:00:00.000Z", "quantity": 1.0},  # kaggle-only date
        {"date": "2024-01-01T00:00:00.000Z", "quantity": 999.0},  # stale, overlaps
    ]
    fresh = [
        {"date": "2024-01-01T00:00:00.000Z", "quantity": 5.0},
        {"date": "2024-01-02T00:00:00.000Z", "quantity": 6.0},
    ]
    out = sw.merge(existing, fresh)
    by_date = {r["date"][:10]: r["quantity"] for r in out}
    assert by_date == {"2023-06-01": 1.0, "2024-01-01": 5.0, "2024-01-02": 6.0}, by_date
    # and it must come back sorted, since callers write it straight to disk
    assert [r["date"] for r in out] == sorted(r["date"] for r in out), out


def test_safe_name():
    assert sw.safe_name("Tur/Arhar") == "Tur-Arhar"
    assert sw.safe_name("Uttar Pradesh") == "Uttar_Pradesh"


if __name__ == "__main__":
    test_to_records_price_shape()
    test_to_records_volume_shape()
    test_merge_keeps_dates_fresh_lacks()
    test_safe_name()
    print("All checks passed.")
