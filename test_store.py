"""Tests for store.py -- writing revised price/volume days to disk.

No network. Run: python test_store.py
"""

import json
import tempfile
from pathlib import Path

import store


def _doc(tmp, name, records, **extra):
    p = Path(tmp) / name
    doc = {
        "crop": "Onion",
        "state": "Maharashtra",
        "commodity_id": 23,
        "state_id": 27,
        "from_date": "2021-01-01",
        "to_date": records[-1]["date"][:10] if records else "",
        "record_count": len(records),
        "records": records,
    }
    doc.update(extra)
    p.write_text(json.dumps(doc))
    return p


def _price(date, modal):
    return {
        "date": f"{date}T00:00:00.000Z",
        "commodity_id": 23,
        "census_state_id": 27,
        "min_price": None,
        "max_price": None,
        "modal_price": modal,
        "census_district_id": None,
        "market_id": None,
    }


def test_revises_an_existing_day_instead_of_duplicating_it():
    # The whole reason this exists: a day pulled at 1 day old is ~75% reported,
    # so the next night must correct it in place, not append a second row.
    with tempfile.TemporaryDirectory() as tmp:
        p = _doc(tmp, "Onion__Maharashtra.json", [_price("2026-09-16", 100.0)])
        files, recs = store.write_prices(
            {("Onion", "Maharashtra", "2026-09-16"): 3817.4}, Path(tmp), "test"
        )
        doc = json.loads(p.read_text())
        assert files == 1 and recs == 1, (files, recs)
        assert len(doc["records"]) == 1, doc["records"]
        assert doc["records"][0]["modal_price"] == 3817.4, doc["records"]


def test_appends_new_days_and_keeps_order():
    with tempfile.TemporaryDirectory() as tmp:
        p = _doc(tmp, "Onion__Maharashtra.json", [_price("2026-09-14", 1.0)])
        store.write_prices(
            {
                ("Onion", "Maharashtra", "2026-09-16"): 3.0,
                ("Onion", "Maharashtra", "2026-09-15"): 2.0,
            },
            Path(tmp),
            "test",
        )
        doc = json.loads(p.read_text())
        dates = [r["date"][:10] for r in doc["records"]]
        assert dates == ["2026-09-14", "2026-09-15", "2026-09-16"], dates
        assert doc["to_date"] == "2026-09-16", doc["to_date"]
        assert doc["record_count"] == 3, doc["record_count"]
        assert doc["backfill_source"] == "test", doc["backfill_source"]


def test_untouched_pairs_keep_their_source_tag():
    # A crop/state the run had no rows for must not be restamped as though it
    # had been refreshed -- that would hide a feed going quietly dead.
    with tempfile.TemporaryDirectory() as tmp:
        p = _doc(
            tmp,
            "Apple__Assam.json",
            [_price("2026-09-14", 1.0)],
            crop="Apple",
            state="Assam",
            backfill_source="original",
        )
        files, _ = store.write_prices(
            {("Onion", "Maharashtra", "2026-09-16"): 1.0}, Path(tmp), "test"
        )
        assert files == 0, files
        assert json.loads(p.read_text())["backfill_source"] == "original"


def test_volume_writes_quantity_shape():
    with tempfile.TemporaryDirectory() as tmp:
        p = _doc(
            tmp,
            "Onion__Maharashtra.json",
            [
                {
                    "date": "2025-10-30T00:00:00.000Z",
                    "commodity_id": 23,
                    "census_state_id": 27,
                    "quantity": 20523.77,
                }
            ],
        )
        store.write_volumes(
            {("Onion", "Maharashtra", "2026-09-16"): 22488.84}, Path(tmp), "test"
        )
        recs = json.loads(p.read_text())["records"]
        assert len(recs) == 2, recs
        assert recs[-1]["quantity"] == 22488.84, recs[-1]
        assert "modal_price" not in recs[-1], recs[-1]
        assert recs[-1]["census_state_id"] == 27, recs[-1]


def test_empty_file_still_receives_its_first_day():
    # 186 price files and 401 volume files are empty shells. A pair that starts
    # reporting must start filling, not be skipped for having no history --
    # backfill_2026.merge() skips those, which is what froze them.
    with tempfile.TemporaryDirectory() as tmp:
        p = _doc(tmp, "Onion__Maharashtra.json", [])
        files, recs = store.write_volumes(
            {("Onion", "Maharashtra", "2026-09-16"): 5.0}, Path(tmp), "test"
        )
        assert (files, recs) == (1, 1), (files, recs)
        doc = json.loads(p.read_text())
        assert doc["records"][0]["quantity"] == 5.0, doc
        assert doc["to_date"] == "2026-09-16", doc


def test_ignores_dates_before_a_files_own_history_start():
    # Guard against a stray old date rewriting from_date backwards.
    with tempfile.TemporaryDirectory() as tmp:
        p = _doc(tmp, "Onion__Maharashtra.json", [_price("2026-09-16", 1.0)])
        store.write_prices(
            {("Onion", "Maharashtra", "2026-09-15"): 2.0}, Path(tmp), "test"
        )
        doc = json.loads(p.read_text())
        assert doc["from_date"] == "2021-01-01", doc["from_date"]


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
