"""Band presets and frequency dictionaries for the 6 supported bands.

Each band defines:
  - name:        human-readable name
  - start_mhz:   lower bound (MHz)
  - end_mhz:     upper bound (MHz)
  - modulation:  default Gqrx modulation (WFM_ST, FM, AM, USB, LSB, CWU...)
  - step_khz:    scan step (kHz) used by the auto-discovery scanner
  - description: short description
  - known:       dict of {freq_hz: name} for known channels in the band
                 (NOAA weather, ATC frequencies, marine channels, etc.)

The scanner uses these to:
  1. Sweep the band at `step_khz` intervals
  2. Find frequencies above a signal threshold
  3. Cross-reference `known` to label channels
  4. Apply AI tagging for everything else
"""

from typing import Dict, List, NamedTuple


class Band(NamedTuple):
    name: str
    start_mhz: float
    end_mhz: float
    modulation: str
    step_khz: float
    description: str
    known: Dict[int, str]  # freq_hz -> station name


# ----------------------------- FM Broadcast -----------------------------
# 88–108 MHz, WFM stereo. Step 200 kHz (US/EU channel spacing).
# Includes Baltimore, MD area presets.
FM_BROADCAST = Band(
    name="FM Broadcast",
    start_mhz=88.0,
    end_mhz=108.0,
    modulation="WFM_ST",
    step_khz=200.0,
    description="FM broadcast radio (88–108 MHz). Stereo WFM with 75 µs de-emphasis.",
    known={
        # ---- Baltimore, MD area FM stations ----
        88_100_000: "WYPR 88.1 — Baltimore NPR News/Talk",
        88_900_000: "WEAA 88.9 — Morgan State Jazz",
        89_300_000: "WBJC 89.3 — Classical",
        89_700_000: "WTMD 89.7 — Towson Univ. Alt/Indie",
        90_100_000: "WETA 90.1 — DC NPR Classical (audible in Balt.)",
        92_300_000: "WERQ 92.3 — Urban/Hip-Hop '92Q'",
        93_100_000: "WPOC 93.1 — Country '93.1 WPOC'",
        95_500_000: "WWIN 95.5 — Urban AC 'Magic 95.5'",
        97_900_000: "WIYY 97.9 — Rock '98 Rock'",
        99_100_000: "WHFS 99.1 — Alt Rock '99.1 HFS'",
        101_900_000: "WLIF 101.9 — Adult Contemp 'Today's 101.9'",
        102_700_000: "WQSR 102.7 — Classic Hits '102.7 Jack FM'",
        104_300_000: "WSMJ 104.3 — Smooth Jazz",
        106_500_000: "WWMX 106.5 — Adult Top 40 'Mix 106.5'",
        107_300_000: "WRBS 107.3 — Religious '95.1 Shine FM relay'",
    },
)


# ----------------------------- AM Broadcast -----------------------------
# 540–1700 kHz, AM. Step 10 kHz (US channel spacing).
# Includes Baltimore, MD area presets.
AM_BROADCAST = Band(
    name="AM Broadcast",
    start_mhz=0.540,
    end_mhz=1.700,
    modulation="AM",
    step_khz=10.0,
    description="AM broadcast radio (540–1700 kHz). Requires RTL-SDR V3 direct sampling (Q-branch).",
    known={
        # ---- Baltimore, MD area AM stations ----
        600_000: "WCAO 600 — Gospel 'Heaven 600'",
        630_000: "WFED 630 — Federal News Network",
        810_000: "WYRE 810 — Annapolis Adult Standards",
        970_000: "WAMD 970 — Aberdeen MD",
        980_000: "WOLB 980 — Talk",
        1090_000: "WBAL 1090 — News/Talk 'WBAL NewsRadio' (50kW clear-channel)",
        1300_000: "WJZ 1300 — Sports Talk",
        1590_000: "WAMD 1590 — Religious",
        1670_000: "WTTZ 1670 — Religious (Lutherville MD)",
    },
)


# ----------------------------- Airband (VHF AM) -----------------------------
# 118–137 MHz, AM. Step 25 kHz (8.33 kHz channel spacing in Europe).
# Common ATC frequencies below — they are widely used but each airport differs.
AIRBAND = Band(
    name="Aviation (Airband)",
    start_mhz=118.0,
    end_mhz=137.0,
    modulation="AM",
    step_khz=25.0,
    description="VHF aviation voice (AM). 118–137 MHz. Includes ATIS, tower, ground, approach, departure, en-route, AWOS, FSS.",
    known={
        121_500_000: "EMERGENCY Guard 121.5",
        122_750_000: "Air-to-Air (GA)",
        121_600_000: "Ground (common)",
        121_900_000: "Ground (common)",
        122_800_000: "Unicom (multicom)",
        123_100_000: "Search & Rescue",
        122_950_000: "Unicom",
        126_700_000: "ATC Approach (common)",
        124_400_000: "ATC Approach (common)",
        127_400_000: "ATC Approach (common)",
        125_000_000: "ATC Approach (common)",
        119_000_000: "ATC Approach (common)",
        128_700_000: "ATC Approach (common)",
        134_500_000: "ATC Clearance Delivery (common)",
        124_900_000: "ATC Clearance Delivery (common)",
        133_700_000: "ATC Approach (common)",
        132_100_000: "ATC Departure (common)",
        133_900_000: "ATC Departure (common)",
        134_850_000: "ATC Departure (common)",
        128_900_000: "ATC Departure (common)",
    },
)


# ----------------------------- NOAA Weather -----------------------------
# 162.400–162.55 MHz, WFM (5 kHz deviation). Seven channels.
NOAA_WEATHER = Band(
    name="NOAA Weather Radio",
    start_mhz=162.400,
    end_mhz=162.550,
    modulation="WFM",
    step_khz=25.0,
    description="US NOAA Weather Radio (WFM). Continuous weather forecasts, alerts, and SAME warnings.",
    known={
        162_400_000: "NOAA WX-1 (162.400)",
        162_425_000: "NOAA WX-2 (162.425)",
        162_450_000: "NOAA WX-3 (162.450)",
        162_475_000: "NOAA WX-4 (162.475)",
        162_500_000: "NOAA WX-5 (162.500)",
        162_525_000: "NOAA WX-6 (162.525)",
        162_550_000: "NOAA WX-7 (162.550)",
    },
)


# ----------------------------- 2-Meter Amateur -----------------------------
# 144–148 MHz, NBFM (12.5/25 kHz) with SSB/CW in 144.0–144.1 sub-band.
HAM_2M = Band(
    name="2m Amateur (Ham)",
    start_mhz=144.0,
    end_mhz=148.0,
    modulation="FM",
    step_khz=15.0,
    description="2-meter amateur band (144–148 MHz). NBFM voice, SSB/CW on 144.0–144.1, repeaters throughout.",
    known={
        146_520_000: "2m National Simplex Calling (146.520)",
        145_500_000: "2m Simplex (UK calling, 145.500)",
        144_390_000: "ISS APRS (145.825 / 144.390 — US APRS)",
        145_800_000: "ISS Voice Downlink (145.800)",
        144_100_000: "2m SSB/CW Calling (144.100)",
        144_200_000: "2m SSB (144.200)",
        147_000_000: "2m Repeater Output (147.000)",
        146_940_000: "2m Repeater Output (146.940)",
        147_300_000: "2m Repeater Output (147.300)",
    },
)


# ----------------------------- Marine VHF -----------------------------
# 156–162 MHz, NBFM (25 kHz channel spacing). International channels.
MARINE_VHF = Band(
    name="Marine VHF",
    start_mhz=156.0,
    end_mhz=162.0,
    modulation="FM",
    step_khz=25.0,
    description="International marine VHF channels (NBFM). Includes Channel 16 distress, weather, harbor, port operations.",
    known={
        156_050_000: "Marine Ch 1",
        156_100_000: "Marine Ch 2",
        156_150_000: "Marine Ch 3",
        156_200_000: "Marine Ch 4",
        156_250_000: "Marine Ch 5",
        156_300_000: "Marine Ch 6 (Inter-ship)",
        156_500_000: "Marine Ch 8",
        156_550_000: "Marine Ch 9 (Hailing)",
        156_600_000: "Marine Ch 10",
        156_700_000: "Marine Ch 11",
        156_800_000: "Marine Ch 16 DISTRESS / Calling",
        156_850_000: "Marine Ch 17",
        156_900_000: "Marine Ch 18",
        157_000_000: "Marine Ch 20",
        157_100_000: "Marine Ch 22 (USCG)",
        157_250_000: "Marine Ch 24",
        157_300_000: "Marine Ch 26",
        157_400_000: "Marine Ch 28",
        161_600_000: "Marine Ch 21",
        161_650_000: "Marine Ch 23",
        161_700_000: "Marine Ch 24 (duplex)",
        161_750_000: "Marine Ch 25",
        161_800_000: "Marine Ch 26 (duplex)",
        161_850_000: "Marine Ch 27",
        161_900_000: "Marine Ch 28 (duplex)",
        162_000_000: "Marine Ch 20 (duplex, US)",
    },
)


# ----------------------------- Shortwave (HF, RTL-SDR V3 direct sampling) -----------------------------
# 1.7–30 MHz, requires RTL-SDR V3 Q-branch direct sampling mode (no upconverter).
# Gqrx supports this via Device Settings → Direct sampling → Q-branch.
# (AM broadcast 540-1700 kHz is a separate band — see AM_BROADCAST above.)
SHORTWAVE = Band(
    name="Shortwave (HF)",
    start_mhz=1.7,
    end_mhz=30.0,
    modulation="AM",
    step_khz=10.0,
    description="HF shortwave (1.7–30 MHz). Requires RTL-SDR V3 direct sampling (Q-branch). International broadcasters, ham, utility, CB.",
    known={
        # HF amateur bands
        1_800_000: "160m Ham (1.8–2.0 MHz)",
        3_500_000: "80m Ham (3.5–4.0 MHz)",
        5_330_500: "60m Ham (5.3305 MHz USB)",
        7_000_000: "40m Ham (7.0–7.3 MHz)",
        10_100_000: "30m Ham (10.1–10.15 MHz)",
        14_000_000: "20m Ham (14.0–14.35 MHz)",
        18_068_000: "17m Ham (18.068–18.168 MHz)",
        21_000_000: "15m Ham (21.0–21.45 MHz)",
        24_890_000: "12m Ham (24.89–24.99 MHz)",
        28_000_000: "10m Ham (28.0–29.7 MHz)",
        # International broadcasters (examples, frequencies vary)
        5_900_000: "SW Broadcast (5.900 MHz)",
        6_000_000: "SW Broadcast (6.000 MHz)",
        7_200_000: "SW Broadcast (7.200 MHz)",
        9_400_000: "SW Broadcast (9.400 MHz)",
        9_600_000: "SW Broadcast (9.600 MHz)",
        11_700_000: "SW Broadcast (11.700 MHz)",
        13_700_000: "SW Broadcast (13.700 MHz)",
        15_200_000: "SW Broadcast (15.200 MHz)",
        17_600_000: "SW Broadcast (17.600 MHz)",
        21_500_000: "SW Broadcast (21.500 MHz)",
        # Time stations
        2_500_000: "WWV/CHU Time (2.500 MHz)",
        5_000_000: "WWV Time (5.000 MHz)",
        10_000_000: "WWV Time (10.000 MHz)",
        15_000_000: "WWV Time (15.000 MHz)",
        20_000_000: "WWV Time (20.000 MHz)",
        # CB (US)
        27_185_000: "CB Channel 19 (27.185 MHz)",
    },
)


BANDS: List[Band] = [
    FM_BROADCAST,
    AM_BROADCAST,
    AIRBAND,
    NOAA_WEATHER,
    HAM_2M,
    MARINE_VHF,
    SHORTWAVE,
]


BANDS_BY_NAME: Dict[str, Band] = {b.name: b for b in BANDS}


def band_for_frequency(freq_hz: int) -> "Band | None":
    """Return the band that contains the given frequency, or None."""
    f_mhz = freq_hz / 1e6
    for b in BANDS:
        if b.start_mhz <= f_mhz <= b.end_mhz:
            return b
    return None


def guess_modulation(freq_hz: int) -> str:
    """Guess the appropriate Gqrx modulation for a frequency."""
    b = band_for_frequency(freq_hz)
    return b.modulation if b else "FM"


def lookup_known(freq_hz: int) -> str | None:
    """Look up a frequency in all known-channel tables.

    Tolerances: 12.5 kHz for VHF/UHF, 1 kHz for HF.
    """
    for band in BANDS:
        for known_hz, name in band.known.items():
            tol = 1000 if band is SHORTWAVE else 12_500
            if abs(known_hz - freq_hz) <= tol:
                return name
    return None
