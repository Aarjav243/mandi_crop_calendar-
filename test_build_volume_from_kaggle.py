"""Smoke test for the parsing/merge logic in build_volume_from_kaggle.py.
No network, no Kaggle download -- synthetic CSV + synthetic existing volume
file only. Run: python test_build_volume_from_kaggle.py
"""

import json
import tempfile
import zipfile
from pathlib import Path
from unittest import mock

import build_volume_from_kaggle as bvk

SAMPLE_CSV = """State Name,District Name,Market Name,Variety,Group,Arrivals (Tonnes),Min Price (Rs./Quintal),Max Price (Rs./Quintal),Modal Price (Rs./Quintal),Reported Date
Punjab,Ludhiana,Ludhiana,Other,Cereals,10.5,1900,2000,1950,01 Feb 2022
NCT of Delhi,Delhi,Azadpur,Other,Cereals,5.0,1900,2000,1950,01 Feb 2022
Punjab,Amritsar,Amritsar,Other,Cereals,2.5,1900,2000,1950,01 Feb 2022
Punjab,Ludhiana,Ludhiana,Other,Cereals,999,1900,2000,1950,01 Feb 2025
Chandigarh,Chandigarh,Chandigarh,Other,Cereals,1.0,1900,2000,1950,01 Feb 2022
"""


def test_day_sums_by_state():
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "Wheat.csv"
        csv_path.write_text(SAMPLE_CSV)
        result = bvk.day_sums_by_state(csv_path)

        # Punjab: two markets on 01 Feb 2022 sum to 13.0; the 2025 row is
        # outside the 2021-2023 window and must be excluded.
        assert result["Punjab"] == {"2022-02-01": 13.0}, result["Punjab"]
        # "NCT of Delhi" must alias to "Delhi".
        assert result["Delhi"] == {"2022-02-01": 5.0}, result["Delhi"]
        # Chandigarh isn't one of our 32 states -- must be dropped entirely.
        assert "Chandigarh" not in result


# Some files in this dataset (e.g. Apple.csv, Banana.csv) use a plain
# "Arrivals" header and ISO dates instead of "Arrivals (Tonnes)" / "27 Aug
# 2005" -- must not be silently dropped.
SAMPLE_CSV_ALT_FORMAT = """State Name,District Name,Market Name,Variety,Group,Arrivals,Min Price,Max Price,Modal Price,Reported Date
Punjab,Ludhiana,Ludhiana,Other,Cereals,7.0,1900,2000,1950,2022-02-01
"""


def test_day_sums_by_state_handles_alt_column_and_date_format():
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = Path(tmp) / "Apple.csv"
        csv_path.write_text(SAMPLE_CSV_ALT_FORMAT)
        result = bvk.day_sums_by_state(csv_path)
        assert result["Punjab"] == {"2022-02-01": 7.0}, result


def test_merge_into_file_skips_existing_dates():
    with tempfile.TemporaryDirectory() as tmp:
        out_path = Path(tmp) / "Wheat__Punjab.json"
        out_path.write_text(
            json.dumps(
                {
                    "crop": "Wheat",
                    "state": "Punjab",
                    "commodity_id": 1,
                    "state_id": 3,
                    "from_date": "2024-01-01",
                    "to_date": "2025-12-31",
                    "record_count": 1,
                    "records": [
                        {
                            "date": "2024-01-01T00:00:00.000Z",
                            "commodity_id": 1,
                            "census_state_id": 3,
                            "quantity": 42.0,
                        }
                    ],
                }
            )
        )

        added = bvk.merge_into_file(
            out_path,
            "Wheat",
            "Punjab",
            {"2022-02-01": 13.0, "2024-01-01": 999.0},  # second date already exists
        )

        assert added == 1, added
        data = json.loads(out_path.read_text())
        assert data["record_count"] == 2
        assert data["from_date"] == "2021-01-01"
        by_date = {r["date"][:10]: r["quantity"] for r in data["records"]}
        assert by_date["2022-02-01"] == 13.0
        assert by_date["2024-01-01"] == 42.0  # CEDA's original value, not overwritten


def test_ensure_downloaded_finds_kaggle_renamed_zip():
    # kaggle CLI saves the zip with spaces %20-encoded, not under the literal
    # filename requested -- ensure_downloaded must read the real name back
    # from kaggle's own "Downloading X to Y" output, not guess it.
    with tempfile.TemporaryDirectory() as tmp:
        raw_dir = Path(tmp)
        requested = "Bajra(Pearl Millet-Cumbu).csv"
        encoded_name = "Bajra(Pearl%20Millet-Cumbu).csv.zip"
        encoded_zip = raw_dir / encoded_name

        def fake_download(*a, **k):
            with zipfile.ZipFile(encoded_zip, "w") as z:
                z.writestr(requested, "State Name,Reported Date\n")
            return bvk.subprocess.CompletedProcess(
                a, 0, stdout=f"Downloading {encoded_name} to {raw_dir}\n", stderr=""
            )

        with (
            mock.patch.object(bvk, "RAW_DIR", raw_dir),
            mock.patch.object(bvk.subprocess, "run", side_effect=fake_download),
        ):
            csv_path = bvk.ensure_downloaded(requested)

        assert csv_path == raw_dir / requested
        assert csv_path.exists(), "extracted csv should exist under the literal name"
        assert not encoded_zip.exists(), "zip should be cleaned up after extraction"


def test_ensure_downloaded_handles_unzipped_download():
    # kaggle isn't consistent about zipping either -- small files sometimes
    # come down as a plain %20-encoded .csv, no .zip at all.
    with tempfile.TemporaryDirectory() as tmp:
        raw_dir = Path(tmp)
        requested = "Dry Chillies.csv"
        encoded_name = "Dry%20Chillies.csv"
        encoded_csv = raw_dir / encoded_name

        def fake_download(*a, **k):
            encoded_csv.write_text("State Name,Reported Date\n")
            return bvk.subprocess.CompletedProcess(
                a, 0, stdout=f"Downloading {encoded_name} to {raw_dir}\n", stderr=""
            )

        with (
            mock.patch.object(bvk, "RAW_DIR", raw_dir),
            mock.patch.object(bvk.subprocess, "run", side_effect=fake_download),
        ):
            csv_path = bvk.ensure_downloaded(requested)

        assert csv_path == raw_dir / requested
        assert csv_path.exists()
        assert not encoded_csv.exists(), (
            "should be renamed, not left under the encoded name"
        )


def test_ensure_downloaded_recovers_from_stale_leftover():
    # Regression: a previous crashed run can leave the encoded-name file
    # already sitting in RAW_DIR. A before/after directory diff finds
    # "nothing new" in that case and breaks -- --force + reading kaggle's
    # own output line must work regardless.
    with tempfile.TemporaryDirectory() as tmp:
        raw_dir = Path(tmp)
        requested = "Dry Chillies.csv"
        encoded_name = "Dry%20Chillies.csv"
        encoded_csv = raw_dir / encoded_name
        encoded_csv.write_text("stale partial content")  # pre-existing, not renamed

        def fake_download(*a, **k):
            encoded_csv.write_text(
                "State Name,Reported Date\n"
            )  # --force overwrites it
            return bvk.subprocess.CompletedProcess(
                a, 0, stdout=f"Downloading {encoded_name} to {raw_dir}\n", stderr=""
            )

        with (
            mock.patch.object(bvk, "RAW_DIR", raw_dir),
            mock.patch.object(bvk.subprocess, "run", side_effect=fake_download),
        ):
            csv_path = bvk.ensure_downloaded(requested)

        assert csv_path == raw_dir / requested
        assert csv_path.read_text() == "State Name,Reported Date\n"


if __name__ == "__main__":
    test_day_sums_by_state()
    test_day_sums_by_state_handles_alt_column_and_date_format()
    test_merge_into_file_skips_existing_dates()
    test_ensure_downloaded_finds_kaggle_renamed_zip()
    test_ensure_downloaded_handles_unzipped_download()
    test_ensure_downloaded_recovers_from_stale_leftover()
    print("All checks passed.")
