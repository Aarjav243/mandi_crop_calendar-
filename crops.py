"""Crop table: growing duration, plantable months by agro-climatic zone, season.

No API exists for this. Values follow ICAR package-of-practices and state
agriculture department norms. Every entry carries a `confidence` flag:

    "sourced"  - duration/window matches published ICAR or state-dept norms
    "inferred" - extrapolated from a neighbouring zone or a similar crop

Zones, not states. 36 crops x 25 states of hand-filled plantable months would be
900 cells of mostly guesswork; six agro-climatic zones is what the sources
actually support. STATE_ZONE maps each state to its dominant zone, and
DISTRICT_ZONE overrides it where a state straddles two (east UP).

Timing fields, in the order they happen:

    nursery_weeks           raising seedlings before transplanting. Only for
                            crops that go through a nursery. Counted because the
                            farmer starts here - ignoring it makes advice late.
    duration_weeks          field time, planting -> FIRST harvest. The spread is
                            kept in `duration_range_weeks` (paddy is 100-155 days
                            depending on variety) so the analysis step can widen a
                            window instead of faking one-week precision.
    peak_pick_offset_weeks  only for crops picked repeatedly. First picking is
                            small; this shifts the target to the heavy-volume
                            weeks so the price peak lands on the bulk of the crop.

`lead_weeks()` adds the three. That total, not duration_weeks alone, is what the
planting recommendation should subtract from the peak selling week.

Months are 1-12. A window crossing the year end is written out, e.g. [10,11,12,1].
"""

# --- Agro-climatic zones -------------------------------------------------
# Condensed from the Planning Commission's 15 zones to 6: plantable months
# genuinely differ across these six and rarely within them.
ZONES = {
    "NW": "North-West (Punjab, Haryana, W-UP, Rajasthan plains)",
    "IGP_EAST": "Eastern Indo-Gangetic (E-UP, Bihar, WB, Assam, Odisha)",
    "CENTRAL": "Central plateau (MP, Chhattisgarh, Vidarbha, Bundelkhand)",
    "WEST": "Western dry / Deccan (Gujarat, Maharashtra, N-Karnataka)",
    "SOUTH": "Southern peninsula (TN, Kerala, S-Karnataka, AP, Telangana)",
    "HILL": "Temperate hills (J&K, Himachal, Uttarakhand hills, NE hills)",
}

STATE_ZONE = {
    "Punjab": "NW",
    "Haryana": "NW",
    "Rajasthan": "NW",
    "Delhi": "NW",
    "Uttar Pradesh": "NW",  # west UP default; east UP overridden in DISTRICT_ZONE
    "Uttarakhand": "HILL",
    "Himachal Pradesh": "HILL",
    "Jammu and Kashmir": "HILL",
    "Ladakh": "HILL",
    "Sikkim": "HILL",
    "Arunachal Pradesh": "HILL",
    "Meghalaya": "HILL",
    "Nagaland": "HILL",
    "Manipur": "HILL",
    "Mizoram": "HILL",
    "Tripura": "HILL",
    "Bihar": "IGP_EAST",
    "Jharkhand": "IGP_EAST",
    "West Bengal": "IGP_EAST",
    "Odisha": "IGP_EAST",
    "Assam": "IGP_EAST",
    "Madhya Pradesh": "CENTRAL",
    "Chhattisgarh": "CENTRAL",
    "Gujarat": "WEST",
    "Maharashtra": "WEST",
    "Goa": "WEST",
    "Karnataka": "SOUTH",
    "Tamil Nadu": "SOUTH",
    "Kerala": "SOUTH",
    "Andhra Pradesh": "SOUTH",
    "Telangana": "SOUTH",
    "Puducherry": "SOUTH",
}

# Districts that sit in a different zone from their state's default.
# East UP grows paddy and lentil and its prices track Bihar, not Punjab -
# treating all of UP as one zone gets half the state wrong. Agmarknet reports
# district names, so this costs us nothing to split.
DISTRICT_ZONE = {
    ("Uttar Pradesh", d): "IGP_EAST"
    for d in (
        "Gorakhpur",
        "Deoria",
        "Kushinagar",
        "Maharajganj",
        "Basti",
        "Sant Kabir Nagar",
        "Siddharthnagar",
        "Azamgarh",
        "Mau",
        "Ballia",
        "Varanasi",
        "Chandauli",
        "Ghazipur",
        "Jaunpur",
        "Sonbhadra",
        "Mirzapur",
        "Bhadohi",
        "Allahabad",
        "Prayagraj",
        "Pratapgarh",
        "Kaushambi",
        "Ambedkar Nagar",
        "Sultanpur",
        "Amethi",
        "Faizabad",
        "Ayodhya",
        "Bahraich",
        "Shrawasti",
        "Balrampur",
        "Gonda",
    )
}


def zone_for(state, district=None):
    """Agro-climatic zone for a state, or for a district that overrides it."""
    if district:
        hit = DISTRICT_ZONE.get((state, district.strip().title()))
        if hit:
            return hit
    return STATE_ZONE.get(state)


# --- The table -----------------------------------------------------------
# plant_months: zone -> months. A zone absent from the dict means the crop is
# not meaningfully grown there, and the analysis step must not emit a planting
# recommendation for states in that zone.

CROPS = {
    # ---------------- Cereals ----------------
    "Wheat": dict(
        season="rabi",
        duration_weeks=20,
        duration_range_weeks=(17, 22),
        confidence="sourced",
        plant_months={
            "NW": [11, 12],
            "IGP_EAST": [11, 12],
            "CENTRAL": [10, 11, 12],
            "WEST": [10, 11],
            "HILL": [10, 11],
        },
        note="Sown after the monsoon recedes. Cannot be sown Jun-Sep anywhere.",
    ),
    "Paddy": dict(
        season="kharif",
        duration_weeks=18,
        duration_range_weeks=(14, 22),
        confidence="sourced",
        plant_months={
            "NW": [6, 7],
            "IGP_EAST": [6, 7],
            "CENTRAL": [6, 7],
            "WEST": [6, 7],
            "SOUTH": [6, 7, 12, 1],
            "HILL": [5, 6],
        },
        note="South has a second (rabi/samba) crop sown Dec-Jan. Widest duration "
        "spread of any cereal: short-duration 100d to long-duration 155d.",
    ),
    "Maize": dict(
        season="kharif+rabi",
        duration_weeks=14,
        duration_range_weeks=(12, 17),
        confidence="sourced",
        plant_months={
            "NW": [6, 7, 1, 2],
            "IGP_EAST": [6, 7, 10, 11],
            "CENTRAL": [6, 7],
            "WEST": [6, 7],
            "SOUTH": [6, 7, 10, 11],
            "HILL": [4, 5],
        },
        note="Grown in up to three seasons in Bihar and the south.",
    ),
    "Bajra": dict(
        season="kharif",
        duration_weeks=13,
        duration_range_weeks=(11, 15),
        confidence="sourced",
        plant_months={
            "NW": [7, 8],
            "WEST": [6, 7],
            "CENTRAL": [6, 7],
            "SOUTH": [6, 7, 1, 2],
        },
        note="Drought crop. Rajasthan is the bulk of arrivals; summer bajra in TN/AP.",
    ),
    "Jowar": dict(
        season="kharif+rabi",
        duration_weeks=16,
        duration_range_weeks=(14, 19),
        confidence="sourced",
        plant_months={
            "WEST": [6, 7, 9, 10],
            "CENTRAL": [6, 7, 9, 10],
            "SOUTH": [6, 7, 9, 10],
            "NW": [6, 7],
        },
        note="Rabi jowar, sown Sep-Oct on residual moisture, is the larger crop "
        "in Maharashtra and Karnataka.",
    ),
    "Barley": dict(
        season="rabi",
        duration_weeks=19,
        duration_range_weeks=(17, 21),
        confidence="sourced",
        plant_months={
            "NW": [10, 11],
            "IGP_EAST": [11],
            "CENTRAL": [10, 11],
            "HILL": [10, 11],
        },
        note="Rajasthan and UP dominate. Slightly earlier and hardier than wheat.",
    ),
    # ---------------- Pulses ----------------
    "Gram": dict(
        season="rabi",
        duration_weeks=17,
        duration_range_weeks=(14, 20),
        confidence="sourced",
        plant_months={
            "NW": [10, 11],
            "CENTRAL": [10, 11],
            "WEST": [10, 11],
            "IGP_EAST": [10, 11],
            "SOUTH": [9, 10],
        },
        note="Chana. MP and Rajasthan are most arrivals.",
    ),
    "Tur/Arhar": dict(
        season="kharif",
        duration_weeks=30,
        duration_range_weeks=(24, 38),
        confidence="sourced",
        plant_months={
            "CENTRAL": [6, 7],
            "WEST": [6, 7],
            "SOUTH": [6, 7],
            "NW": [6, 7],
            "IGP_EAST": [6, 7],
        },
        note="Long duration - sown Jun-Jul, harvested Dec-Mar. Medium and "
        "long-duration varieties differ by nearly three months.",
    ),
    "Moong": dict(
        season="kharif+zaid",
        duration_weeks=10,
        duration_range_weeks=(9, 12),
        confidence="sourced",
        plant_months={
            "NW": [3, 4, 7],
            "CENTRAL": [6, 7, 3],
            "WEST": [6, 7, 2, 3],
            "SOUTH": [6, 7, 1, 2],
            "IGP_EAST": [3, 4, 7],
        },
        note="Shortest-duration pulse. Fits as a summer (zaid) catch crop between "
        "wheat and paddy in the north.",
    ),
    "Urad": dict(
        season="kharif+zaid",
        duration_weeks=12,
        duration_range_weeks=(10, 14),
        confidence="sourced",
        plant_months={
            "NW": [3, 4, 7],
            "CENTRAL": [6, 7],
            "WEST": [6, 7],
            "SOUTH": [6, 7, 12, 1],
            "IGP_EAST": [6, 7, 3],
        },
        note="Rice-fallow urad is sown Dec-Jan in the south and Odisha.",
    ),
    "Masoor": dict(
        season="rabi",
        duration_weeks=18,
        duration_range_weeks=(16, 21),
        confidence="sourced",
        plant_months={"IGP_EAST": [10, 11], "CENTRAL": [10, 11], "NW": [10, 11]},
        note="Lentil. UP, MP, Bihar. Not a southern crop.",
    ),
    # ---------------- Oilseeds ----------------
    "Soybean": dict(
        season="kharif",
        duration_weeks=14,
        duration_range_weeks=(13, 17),
        confidence="sourced",
        plant_months={"CENTRAL": [6, 7], "WEST": [6, 7], "SOUTH": [6, 7]},
        note="MP and Maharashtra are almost the whole crop. Sowing is tied "
        "tightly to monsoon onset.",
    ),
    "Groundnut": dict(
        season="kharif+rabi",
        duration_weeks=16,
        duration_range_weeks=(14, 19),
        confidence="sourced",
        plant_months={
            "WEST": [6, 7],
            "SOUTH": [6, 7, 11, 12],
            "CENTRAL": [6, 7],
            "NW": [6, 7],
        },
        note="Gujarat kharif is the bulk. Rabi/summer groundnut in AP and TN is "
        "sown Nov-Jan.",
    ),
    "Mustard": dict(
        season="rabi",
        duration_weeks=18,
        duration_range_weeks=(16, 20),
        confidence="sourced",
        plant_months={
            "NW": [10, 11],
            "IGP_EAST": [10, 11],
            "CENTRAL": [10, 11],
            "WEST": [10, 11],
        },
        note="Rajasthan dominates. Sowing after mid-Nov cuts yield sharply.",
    ),
    "Sunflower": dict(
        season="kharif+rabi",
        duration_weeks=14,
        duration_range_weeks=(12, 16),
        confidence="sourced",
        plant_months={
            "SOUTH": [6, 7, 10, 11, 1, 2],
            "WEST": [6, 7, 10, 11],
            "CENTRAL": [10, 11],
            "NW": [1, 2],
        },
        note="Photoperiod-insensitive, so sown almost year-round in Karnataka.",
    ),
    "Sesamum": dict(
        season="kharif+zaid",
        duration_weeks=13,
        duration_range_weeks=(11, 15),
        confidence="sourced",
        plant_months={
            "CENTRAL": [6, 7],
            "WEST": [6, 7],
            "IGP_EAST": [6, 7, 2, 3],
            "SOUTH": [1, 2, 6, 7],
            "NW": [6, 7],
        },
        note="Til. Summer sesame in WB and Odisha is sown Feb-Mar.",
    ),
    "Castor": dict(
        peak_pick_offset_weeks=10,
        season="kharif",
        duration_weeks=24,
        duration_range_weeks=(20, 30),
        confidence="sourced",
        plant_months={"WEST": [7, 8], "SOUTH": [6, 7], "NW": [7, 8]},
        note="Gujarat is ~80% of the crop. Picked over several months rather than "
        "harvested once, so peak-minus-duration logic is weak here.",
    ),
    # ---------------- Commercial ----------------
    "Cotton": dict(
        peak_pick_offset_weeks=8,
        season="kharif",
        duration_weeks=24,
        duration_range_weeks=(21, 30),
        confidence="sourced",
        plant_months={
            "NW": [4, 5],
            "WEST": [6, 7],
            "CENTRAL": [6, 7],
            "SOUTH": [6, 7, 8],
        },
        note="North India sows Apr-May under irrigation; the rest waits for the "
        "monsoon. Multiple pickings, so harvest is a range not a week.",
    ),
    "Sugarcane": dict(
        season="perennial",
        duration_weeks=52,
        duration_range_weeks=(44, 78),
        confidence="sourced",
        perennial=True,
        plant_months={
            "NW": [2, 3, 10],
            "IGP_EAST": [2, 3, 10],
            "WEST": [10, 11, 1, 2],
            "SOUTH": [12, 1, 2, 6, 7],
            "CENTRAL": [2, 3],
        },
        note="PERENNIAL/RATOON - planting advice is weak. Price is largely set by "
        "state FRP/SAP, not mandi supply. Price-curve entry.",
    ),
    # ---------------- Vegetables ----------------
    "Potato": dict(
        season="rabi",
        duration_weeks=13,
        duration_range_weeks=(11, 16),
        confidence="sourced",
        plant_months={
            "IGP_EAST": [10, 11],
            "NW": [10, 11],
            "CENTRAL": [10, 11],
            "WEST": [10, 11],
            "HILL": [3, 4],
            "SOUTH": [10, 11, 6, 7],
        },
        note="Hills grow a summer crop planted Mar-Apr. Plains are strictly a "
        "winter crop - potato will not tuber above ~30C.",
    ),
    "Onion": dict(
        nursery_weeks=6,
        season="kharif+rabi",
        duration_weeks=18,
        duration_range_weeks=(15, 22),
        confidence="sourced",
        plant_months={
            "WEST": [6, 7, 10, 11, 12],
            "SOUTH": [5, 6, 10, 11],
            "CENTRAL": [6, 7, 11],
            "NW": [11, 12],
            "IGP_EAST": [10, 11],
        },
        note="Three crops in Maharashtra: kharif, late-kharif, rabi. Duration is "
        "from transplanting; add ~6 weeks if counting from nursery sowing.",
    ),
    "Tomato": dict(
        peak_pick_offset_weeks=8,
        nursery_weeks=4,
        season="all-season",
        duration_weeks=13,
        duration_range_weeks=(11, 16),
        confidence="sourced",
        plant_months={
            "NW": [7, 8, 11, 12, 2],
            "IGP_EAST": [6, 7, 9, 10],
            "CENTRAL": [6, 7, 10, 11],
            "WEST": [6, 7, 10, 11, 1],
            "SOUTH": [1, 2, 5, 6, 9, 10],
            "HILL": [3, 4, 5],
        },
        note="Grown nearly year-round. Duration is transplant -> first picking; "
        "harvest then runs another 6-10 weeks.",
    ),
    "Brinjal": dict(
        peak_pick_offset_weeks=12,
        nursery_weeks=4,
        season="all-season",
        duration_weeks=15,
        duration_range_weeks=(12, 18),
        confidence="sourced",
        plant_months={
            "NW": [6, 7, 10, 11, 2],
            "IGP_EAST": [6, 7, 10, 11],
            "CENTRAL": [6, 7, 10, 11],
            "WEST": [6, 7, 10, 11],
            "SOUTH": [1, 2, 6, 7, 10, 11],
            "HILL": [4, 5],
        },
        note="Long picking period - one planting supplies 3-5 months of arrivals.",
    ),
    "Cauliflower": dict(
        nursery_weeks=4,
        season="rabi",
        duration_weeks=13,
        duration_range_weeks=(10, 17),
        confidence="sourced",
        plant_months={
            "NW": [8, 9, 10],
            "IGP_EAST": [8, 9, 10],
            "CENTRAL": [9, 10],
            "WEST": [8, 9, 10],
            "SOUTH": [9, 10, 6, 7],
            "HILL": [4, 5, 6],
        },
        note="Strongly variety-dependent: early types 60d, late types 120d. Curd "
        "will not form above ~25C, so the plains window is fixed.",
    ),
    "Cabbage": dict(
        nursery_weeks=4,
        season="rabi",
        duration_weeks=13,
        duration_range_weeks=(11, 17),
        confidence="sourced",
        plant_months={
            "NW": [8, 9, 10],
            "IGP_EAST": [8, 9, 10],
            "CENTRAL": [9, 10],
            "WEST": [8, 9, 10],
            "SOUTH": [9, 10, 6, 7],
            "HILL": [4, 5, 6],
        },
        note="Same window as cauliflower, slightly more heat-tolerant.",
    ),
    "Okra": dict(
        peak_pick_offset_weeks=6,
        season="kharif+zaid",
        duration_weeks=8,
        duration_range_weeks=(7, 10),
        confidence="sourced",
        plant_months={
            "NW": [2, 3, 6, 7],
            "IGP_EAST": [2, 3, 6, 7],
            "CENTRAL": [2, 3, 6, 7],
            "WEST": [1, 2, 6, 7],
            "SOUTH": [1, 2, 6, 7, 10],
        },
        note="Bhindi. Fastest crop in the table - first picking around 50 days.",
    ),
    "Green Chilli": dict(
        peak_pick_offset_weeks=8,
        nursery_weeks=5,
        season="kharif",
        duration_weeks=17,
        duration_range_weeks=(14, 22),
        confidence="sourced",
        plant_months={
            "SOUTH": [6, 7, 8, 9],
            "WEST": [6, 7],
            "CENTRAL": [6, 7],
            "NW": [2, 3, 6, 7],
            "IGP_EAST": [6, 7],
        },
        note="AP and Telangana dominate. Sold fresh, so price tracks arrivals "
        "closely. Picked repeatedly over ~2 months.",
    ),
    "Dry Chilli": dict(
        nursery_weeks=5,
        season="kharif",
        duration_weeks=24,
        duration_range_weeks=(21, 28),
        confidence="sourced",
        plant_months={
            "SOUTH": [6, 7, 8, 9],
            "WEST": [6, 7],
            "CENTRAL": [6, 7],
            "NW": [6, 7],
            "IGP_EAST": [6, 7],
        },
        note="Left on the plant to ripen and dry - about 7 weeks longer than "
        "green. Stores well and trades through cold stores (Guntur), so its "
        "price pattern is set by the trade calendar, not just harvest.",
    ),
    "Garlic": dict(
        season="rabi",
        duration_weeks=22,
        duration_range_weeks=(19, 26),
        confidence="sourced",
        plant_months={
            "CENTRAL": [10, 11],
            "NW": [10, 11],
            "WEST": [10, 11],
            "SOUTH": [8, 9],
            "HILL": [9, 10],
        },
        note="MP and Rajasthan dominate. Bulbing needs a cold spell, so the "
        "window cannot shift later.",
    ),
    "Ginger": dict(
        season="kharif",
        duration_weeks=34,
        duration_range_weeks=(30, 39),
        confidence="sourced",
        plant_months={
            "SOUTH": [4, 5],
            "IGP_EAST": [4, 5],
            "HILL": [4, 5],
            "CENTRAL": [5, 6],
            "WEST": [5, 6],
        },
        note="8-9 months in the ground. Planted pre-monsoon, harvested Dec-Feb.",
    ),
    "Turmeric": dict(
        season="kharif",
        duration_weeks=36,
        duration_range_weeks=(32, 41),
        confidence="sourced",
        plant_months={
            "SOUTH": [5, 6],
            "IGP_EAST": [5, 6],
            "WEST": [5, 6],
            "CENTRAL": [5, 6],
            "HILL": [4, 5],
        },
        note="Traded dried and polished, so arrivals lag harvest by weeks - the "
        "price curve reflects the trade calendar as much as the field.",
    ),
    "Coriander Seed": dict(
        season="rabi",
        duration_weeks=16,
        duration_range_weeks=(14, 19),
        confidence="sourced",
        plant_months={
            "CENTRAL": [10, 11],
            "NW": [10, 11],
            "WEST": [10, 11],
            "SOUTH": [6, 7, 10, 11],
            "IGP_EAST": [10, 11],
        },
        note="Dhania seed. Grown to maturity, dried, stores well. MP, Rajasthan "
        "and Gujarat dominate.",
    ),
    "Coriander Leaf": dict(
        peak_pick_offset_weeks=2,
        season="all-season",
        duration_weeks=7,
        duration_range_weeks=(5, 9),
        confidence="sourced",
        plant_months={
            "CENTRAL": [10, 11, 12, 1, 6, 7],
            "NW": [9, 10, 11, 12, 1, 2],
            "WEST": [10, 11, 12, 1, 6, 7],
            "SOUTH": [1, 2, 6, 7, 10, 11],
            "IGP_EAST": [10, 11, 12, 1],
        },
        note="Kothmir. Cut at ~45d and highly perishable - no storage at all, so "
        "price swings hard on daily arrivals. Sown in short repeated batches.",
    ),
    # ---------------- Fruit (perennial) ----------------
    "Banana": dict(
        season="perennial",
        duration_weeks=52,
        duration_range_weeks=(44, 65),
        confidence="sourced",
        perennial=True,
        plant_months={
            "SOUTH": [6, 7, 8, 1, 2],
            "WEST": [6, 7, 10, 11],
            "IGP_EAST": [6, 7],
            "CENTRAL": [6, 7],
        },
        note="PERENNIAL. ~12 months to first bunch, then ratoons. Planting advice "
        "is weak; primarily a price-curve entry.",
    ),
    "Mango": dict(
        season="perennial",
        duration_weeks=None,
        duration_range_weeks=None,
        confidence="sourced",
        perennial=True,
        tree=True,
        plant_months={},
        note="TREE CROP - fruits for 30+ years, 4-6 years to first bearing. "
        "Planting advice is meaningless. Price-curve entry only. Harvest "
        "runs Mar-Jul, moving south to north.",
    ),
    "Apple": dict(
        season="perennial",
        duration_weeks=None,
        duration_range_weeks=None,
        confidence="sourced",
        perennial=True,
        tree=True,
        plant_months={},
        note="TREE CROP - 5-8 years to bearing. HILL zone only (HP, J&K, "
        "Uttarakhand). Price-curve entry only. Harvest Jul-Oct.",
    ),
}


def plantable(crop, state, district=None):
    """Months this crop can be planted here; [] if not grown in this zone."""
    z = zone_for(state, district)
    return CROPS[crop]["plant_months"].get(z, []) if z else []


def is_plantable(crop, state, month, district=None):
    return month in plantable(crop, state, district)


def lead_weeks(crop):
    """Weeks from the day the farmer starts to the week we want to sell in.

    Three parts, each of which is a real delay someone would otherwise eat:

      nursery_weeks          - raising seedlings before they go in the field.
                               Excluding it makes the advice that many weeks late.
      duration_weeks         - field time to first harvest.
      peak_pick_offset_weeks - for crops picked over and over, the first picking
                               is small. This shifts the target to the weeks when
                               volume is actually heavy, so the peak price lands
                               on the big pickings rather than the first crate.

    Returns None for tree crops, which get no planting advice at all.
    """
    c = CROPS[crop]
    if not c["duration_weeks"]:
        return None
    return (
        c.get("nursery_weeks", 0)
        + c["duration_weeks"]
        + c.get("peak_pick_offset_weeks", 0)
    )


def demo():
    assert len(CROPS) == 36, len(CROPS)
    for name, c in CROPS.items():
        assert c["season"], name
        assert "duration_weeks" in c, name
        assert c["confidence"] in ("sourced", "inferred"), name
        for zone in c["plant_months"]:
            assert zone in ZONES, (name, zone)
        for months in c["plant_months"].values():
            assert all(1 <= m <= 12 for m in months), name
        if not c.get("tree"):  # tree crops carry no planting advice
            assert c["plant_months"], name
            assert c["duration_weeks"], name
            lo, hi = c["duration_range_weeks"]
            assert lo <= c["duration_weeks"] <= hi, name

    for z in set(STATE_ZONE.values()):
        assert z in ZONES, z

    # the failure the PRD calls out: never advise sowing wheat into the monsoon
    for st in ("Punjab", "Uttar Pradesh", "Madhya Pradesh"):
        assert not any(is_plantable("Wheat", st, m) for m in (6, 7, 8, 9)), st
    assert plantable("Mango", "Maharashtra") == []
    assert plantable("Apple", "Himachal Pradesh") == []
    assert 7 in plantable("Paddy", "Punjab")
    assert plantable("Soybean", "Punjab") == []  # NW absent from soybean

    # east UP follows Bihar, not Punjab: gram is Oct-Nov in both, but the zone
    # itself must differ or the split bought us nothing
    assert zone_for("Uttar Pradesh") == "NW"
    assert zone_for("Uttar Pradesh", "Varanasi") == "IGP_EAST"
    assert zone_for("Uttar Pradesh", "  varanasi ") == "IGP_EAST"  # dirty input
    assert zone_for("Uttar Pradesh", "Meerut") == "NW"  # not in the override list
    assert zone_for("Punjab", "Ludhiana") == "NW"  # override never fires here

    # lead time must include nursery and the multi-pick shift, or the advice is late
    assert lead_weeks("Wheat") == 20  # neither applies
    assert lead_weeks("Onion") == 6 + 18  # nursery only
    assert lead_weeks("Tomato") == 4 + 13 + 8  # both
    assert lead_weeks("Okra") == 8 + 6  # multi-pick only, direct-sown
    assert lead_weeks("Mango") is None  # tree crop: no planting advice

    # the splits must be genuinely different crops, not duplicates
    assert (
        CROPS["Dry Chilli"]["duration_weeks"] > CROPS["Green Chilli"]["duration_weeks"]
    )
    assert (
        CROPS["Coriander Leaf"]["duration_weeks"]
        < CROPS["Coriander Seed"]["duration_weeks"]
    )

    # every nursery/multi-pick crop must still be internally consistent
    for name, c in CROPS.items():
        assert c.get("nursery_weeks", 0) >= 0, name
        assert c.get("peak_pick_offset_weeks", 0) >= 0, name
        if c.get("tree"):
            assert lead_weeks(name) is None, name
        else:
            assert lead_weeks(name) >= c["duration_weeks"], name

    print(f"ok - {len(CROPS)} crops, {len(STATE_ZONE)} states, {len(ZONES)} zones")


if __name__ == "__main__":
    demo()
