# solar.py — Ferdinand, Solar Engine Lead
# SunProof AI — Geocoding + Solar Calculation Module
#
# pip install pysolar requests
#
# Usage:
#   python solar.py                    <- runs built-in tests
#   from solar import geocode_address, get_solar_window

import warnings
warnings.filterwarnings("ignore")  # suppress pysolar leap-second warning

import time
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pysolar.solar import get_altitude, get_azimuth

_OSLO_TZ = ZoneInfo("Europe/Oslo")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Facade direction -> the azimuth range (degrees) from which that facade receives sun.
#
# These ranges are calibrated for Norwegian latitudes (~59-70°N) where the sun
# arcs across the southern sky and never reaches true north at meaningful altitudes.
# A south-facing wall receives sun across a wide arc (90°–270°);
# a north-facing wall receives essentially zero direct sun year-round.
#
# Range format: (lo, hi) — sun azimuth must be between lo and hi.
# Ranges that wrap around north (e.g. NW: 225–45) use lo > hi and are
# handled with the "az >= lo OR az <= hi" check below.
FACADE_RANGES = {
    "N":  (315,  45),   # sun never reaches this arc in Norway -> always 0h
    "NE": (315, 135),   # morning sun, wraps through north
    "E":  (  0, 180),   # morning sun
    "SE": ( 45, 225),   # morning + midday sun
    "S":  ( 90, 270),   # broadest window — morning through afternoon
    "SW": (135, 315),   # midday + afternoon sun
    "W":  (180, 360),   # afternoon + evening sun
    "NW": (225,  45),   # evening sun, wraps through north
}

# Simple in-memory geocoding cache — avoids re-calling Nominatim for same address
_geocache: dict[str, tuple[float, float]] = {}


# ---------------------------------------------------------------------------
# Function 1: Geocoding
# ---------------------------------------------------------------------------

def geocode_address(address_str: str) -> tuple[float, float]:
    """
    Convert a human-readable address to (latitude, longitude).

    Uses OpenStreetMap Nominatim — free, no API key required.
    Results are cached in memory so repeated calls don't hit the API.

    Args:
        address_str: e.g. "Aker Brygge, Oslo" or "Bygdøy allé 5, Oslo"

    Returns:
        (latitude, longitude) as floats

    Raises:
        ValueError: if the address cannot be found
    """
    # Return cached result if we have it
    key = address_str.strip().lower()
    if key in _geocache:
        return _geocache[key]

    # Call Nominatim (OpenStreetMap's free geocoder)
    url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": address_str,
        "format": "json",
        "limit": 1,
        "countrycodes": "no",       # bias toward Norway
        "addressdetails": 0,
    }
    headers = {"User-Agent": "SunProofAI/1.0 (student project)"}

    try:
        time.sleep(1)               # Nominatim rate-limit: max 1 req/sec
        response = requests.get(url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        results = response.json()
    except requests.RequestException as e:
        raise ValueError(f"Geocoding network error for '{address_str}': {e}")

    if not results:
        raise ValueError(
            f"Address not found: '{address_str}'. "
            "Try adding city name, e.g. 'Aker Brygge, Oslo'."
        )

    lat = float(results[0]["lat"])
    lon = float(results[0]["lon"])
    _geocache[key] = (lat, lon)
    return (lat, lon)


# ---------------------------------------------------------------------------
# Function 2: Solar Window
# ---------------------------------------------------------------------------

def get_solar_window(
    lat: float,
    lon: float,
    facade_direction: str,
    month: int,
) -> dict:
    """
    Calculate how much sun a facade receives in a given month.

    Checks every hour of a representative day (15th of the month) and
    counts hours where the sun is above the horizon AND in the arc
    that the facade faces.

    Args:
        lat:              latitude (e.g. 59.91 for Oslo)
        lon:              longitude (e.g. 10.75 for Oslo)
        facade_direction: compass point string — one of:
                          "N", "NE", "E", "SE", "S", "SW", "W", "NW"
        month:            integer 1–12

    Returns:
        dict with keys:
            sun_rise_azimuth       (float | None) — azimuth at sunrise
            sun_set_azimuth        (float | None) — azimuth at sunset
            hours_facade_receives_sun (int)       — count of sun hours
            best_sun_times         (list[str])    — e.g. ["11:00", "12:00"]
            facade_receives_sun    (bool)         — True if any sun at all
            notes                  (str)          — human-readable summary
    """
    direction = facade_direction.upper().strip()
    if direction not in FACADE_RANGES:
        raise ValueError(
            f"Invalid facade_direction '{facade_direction}'. "
            f"Must be one of: {list(FACADE_RANGES.keys())}"
        )
    if not (1 <= month <= 12):
        raise ValueError(f"Invalid month '{month}'. Must be 1–12.")

    lo, hi = FACADE_RANGES[direction]
    year = datetime.now().year

    sun_hours: list[str] = []
    rise_az: float | None = None
    set_az: float | None = None
    prev_above = False

    for hour in range(0, 24):
        dt = datetime(year, month, 15, hour, 0, 0, tzinfo=timezone.utc)
        altitude = get_altitude(lat, lon, dt)
        azimuth  = get_azimuth(lat, lon, dt)

        above_horizon = altitude > 0

        # Detect sunrise
        if above_horizon and not prev_above:
            rise_az = round(azimuth, 1)
        # Detect sunset
        if not above_horizon and prev_above:
            set_az = round(azimuth, 1)

        prev_above = above_horizon

        if above_horizon:
            # Check if sun azimuth is within the facade's receiving arc
            if lo < hi:
                in_arc = lo <= azimuth <= hi
            else:
                # Arc wraps around north (e.g. N: 315–45)
                in_arc = azimuth >= lo or azimuth <= hi

            if in_arc:
                local_hour = dt.astimezone(_OSLO_TZ).hour
                sun_hours.append(f"{local_hour:02d}:00")

    hours_count = len(sun_hours)
    receives = hours_count > 0

    # Build a plain-English note for Gabriel / the report
    month_name = datetime(year, month, 1).strftime("%B")
    if not receives:
        notes = (
            f"{direction}-facing facade receives no direct sun in {month_name}. "
            "Claims of sunlight would be misleading."
        )
    elif hours_count >= 6:
        notes = (
            f"{direction}-facing facade gets excellent sun in {month_name} "
            f"({hours_count}h/day). Sun from {sun_hours[0]} to {sun_hours[-1]}."
        )
    elif hours_count >= 3:
        notes = (
            f"{direction}-facing facade gets moderate sun in {month_name} "
            f"({hours_count}h/day). Best times: {', '.join(sun_hours[:3])}."
        )
    else:
        notes = (
            f"{direction}-facing facade gets limited sun in {month_name} "
            f"({hours_count}h/day). Only around {', '.join(sun_hours)}."
        )

    return {
        "sun_rise_azimuth":          rise_az,
        "sun_set_azimuth":           set_az,
        "hours_facade_receives_sun": hours_count,
        "best_sun_times":            sun_hours,
        "facade_receives_sun":       receives,
        "notes":                     notes,
    }


# ---------------------------------------------------------------------------
# Quick helper: address + direction + month in one call (Gabriel uses this)
# ---------------------------------------------------------------------------

def analyze_address(address_str: str, facade_direction: str, month: int) -> dict:
    """
    Convenience wrapper that geocodes then calculates the solar window.
    This is what Gabriel's /analyze endpoint will call.

    Returns the solar_window dict with lat/lon added for transparency.
    """
    lat, lon = geocode_address(address_str)
    result = get_solar_window(lat, lon, facade_direction, month)
    result["lat"] = round(lat, 5)
    result["lon"] = round(lon, 5)
    return result


# ---------------------------------------------------------------------------
# Self-test — run with: python solar.py
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Use hardcoded Oslo coords so tests work offline / without Nominatim access
    TEST_LOCATIONS = [
        ("Aker Brygge, Oslo",    59.9093, 10.7290),
        ("Frogner, Oslo",        59.9200, 10.7050),
        ("Grünerløkka, Oslo",    59.9238, 10.7602),
        ("Majorstuen, Oslo",     59.9295, 10.7145),
        ("Bjørvika, Oslo",       59.9065, 10.7540),
    ]

    DIRECTIONS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    MONTHS     = [7, 12]   # July (summer) and December (winter)

    print("=" * 65)
    print("SunProof AI — Solar Engine Test")
    print("=" * 65)

    for name, lat, lon in TEST_LOCATIONS:
        print(f"\n📍 {name}  ({lat}, {lon})")
        for month in MONTHS:
            month_name = datetime(2026, month, 1).strftime("%B")
            print(f"  ── {month_name} ──")
            for direction in DIRECTIONS:
                result = get_solar_window(lat, lon, direction, month)
                h = result["hours_facade_receives_sun"]
                times = result["best_sun_times"]
                bar = "█" * h + "░" * (14 - h)
                first = times[0] if times else "  —  "
                last  = times[-1] if times else "  —  "
                print(f"    {direction:>2}  {bar}  {h:>2}h  {first}–{last}")

    print("\n" + "=" * 65)
    print("Spot checks (must pass):")

    # South-facing Oslo, July — should get lots of sun
    r = get_solar_window(59.91, 10.75, "S", 7)
    assert r["facade_receives_sun"] is True, "FAIL: S-facing July should get sun"
    assert r["hours_facade_receives_sun"] >= 5, "FAIL: S-facing July should get ≥5h"
    print(f"  ✅ S-facing July: {r['hours_facade_receives_sun']}h — OK")

    # North-facing Oslo, December — should get zero sun
    r = get_solar_window(59.91, 10.75, "N", 12)
    assert r["facade_receives_sun"] is False, "FAIL: N-facing Dec should get 0h"
    print(f"  ✅ N-facing December: 0h — OK")

    # East-facing, July — morning sun only
    r = get_solar_window(59.91, 10.75, "E", 7)
    if r["best_sun_times"]:
        latest = int(r["best_sun_times"][-1].split(":")[0])
        assert latest <= 15, "FAIL: E-facing should have sun in morning/midday only"
    print(f"  ✅ E-facing July: sun until {r['best_sun_times'][-1] if r['best_sun_times'] else 'none'} — OK")

    # West-facing, July — afternoon sun only
    r = get_solar_window(59.91, 10.75, "W", 7)
    if r["best_sun_times"]:
        earliest = int(r["best_sun_times"][0].split(":")[0])
        assert earliest >= 10, "FAIL: W-facing should have sun in afternoon only"
    print(f"  ✅ W-facing July: sun from {r['best_sun_times'][0] if r['best_sun_times'] else 'none'} — OK")

    print("\nAll tests passed! solar.py is ready to integrate. 🎉")
    print("=" * 65)