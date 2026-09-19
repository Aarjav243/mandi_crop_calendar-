"""Maps our crop/state names (crops.py) to CEDA Agmarknet API ids.

CEDA ids come from /v1/agmarknet/commodities and /v1/agmarknet/geographies,
fetched 2026-09-03. If CEDA adds/renumbers commodities this goes stale —
rerun the same two GET calls and diff.
"""

# crops.py name -> CEDA commodity_id
COMMODITY_ID = {
    "Apple": 17,
    "Bajra": 28,
    "Banana": 19,
    "Barley": 29,
    "Brinjal": 35,
    "Cabbage": 154,
    "Castor": 123,  # Castor Seed (raw), not Castor Oil (270)
    "Cauliflower": 34,
    "Coriander Leaf": 43,  # "Coriander(Leaves)" — CEDA has no separate seed/dhania commodity
    "Coriander Seed": None,  # NOT AVAILABLE on CEDA — see NOTES below
    "Cotton": 15,
    "Dry Chilli": 132,  # "Dry Chillies"
    "Garlic": 25,
    "Ginger": 27,  # "Ginger(Dry)" — matches our storable/traded assumption better than Green (103)
    "Gram": 6,  # "Bengal Gram(Gram)(Whole)"
    "Green Chilli": 87,
    "Groundnut": 10,
    "Jowar": 5,
    "Maize": 4,
    "Mango": 20,
    "Masoor": 63,  # "Lentil (Masur)(Whole)"
    "Moong": 9,  # "Green Gram (Moong)(Whole)"
    "Mustard": 12,
    "Okra": 85,  # "Bhindi(Ladies Finger)"
    "Onion": 23,
    "Paddy": 2,  # "Paddy(Dhan)(Common)" — not Basmati (414)
    "Potato": 24,
    "Sesamum": 11,
    "Soybean": 13,  # "Soyabean"
    "Sugarcane": 150,
    "Sunflower": 14,
    "Tomato": 78,
    "Tur/Arhar": 49,  # "Arhar (Tur/Red Gram)(Whole)"
    "Turmeric": 39,
    "Urad": 8,  # "Black Gram (Urd Beans)(Whole)"
    "Wheat": 1,
}

# crops.py state name -> CEDA census_state_id
# CEDA uses 2011 Census state boundaries: Telangana already has its own id,
# but J&K/Ladakh are NOT split (Ladakh was still part of J&K in 2011).
STATE_ID = {
    "Andhra Pradesh": 28,
    "Arunachal Pradesh": 12,
    "Assam": 18,
    "Bihar": 10,
    "Chhattisgarh": 22,
    "Delhi": 7,
    "Goa": 30,
    "Gujarat": 24,
    "Haryana": 6,
    "Himachal Pradesh": 2,
    "Jammu and Kashmir": 1,
    "Jharkhand": 20,
    "Karnataka": 29,
    "Kerala": 32,
    "Ladakh": 1,  # CEDA has no separate Ladakh id — same as J&K (see NOTES)
    "Madhya Pradesh": 23,
    "Maharashtra": 27,
    "Manipur": 14,
    "Meghalaya": 17,
    "Mizoram": 15,
    "Nagaland": 13,
    "Odisha": 21,
    "Puducherry": 34,
    "Punjab": 3,
    "Rajasthan": 8,
    "Sikkim": 11,
    "Tamil Nadu": 33,
    "Telangana": 36,
    "Tripura": 16,
    "Uttar Pradesh": 9,
    "Uttarakhand": 5,
    "West Bengal": 19,
}

# Known gaps — do not silently drop these, surface them in output.
NOTES = {
    "Coriander Seed": (
        "CEDA/Agmarknet has no separate dhania-seed commodity. Only "
        "'Coriander(Leaves)' exists. Coriander Seed cannot be priced from "
        "this source; drop it from Step 3 output or flag as unavailable."
    ),
    "Ladakh": (
        "CEDA uses 2011 Census state boundaries, before Ladakh was split "
        "from Jammu & Kashmir. Ladakh pulls the same state-level series as "
        "J&K — there is no way to isolate Ladakh-only prices from this API."
    ),
}


def demo():
    import crops

    missing_crops = [c for c in crops.CROPS if c not in COMMODITY_ID]
    assert not missing_crops, (
        f"crops.py has crops with no CEDA mapping: {missing_crops}"
    )

    missing_states = [s for s in crops.STATE_ZONE if s not in STATE_ID]
    assert not missing_states, (
        f"crops.py has states with no CEDA mapping: {missing_states}"
    )

    unresolved = [c for c, v in COMMODITY_ID.items() if v is None]
    assert unresolved == ["Coriander Seed"], f"unexpected unmapped crops: {unresolved}"

    print(
        f"ok - {len(COMMODITY_ID)} crops mapped ({len(unresolved)} unavailable), "
        f"{len(STATE_ID)} states mapped"
    )


if __name__ == "__main__":
    demo()
