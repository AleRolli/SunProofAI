# solar.py — Ferdinand, Solar Engine Lead
# SunProof AI — Geocoding + Solar Calculation Module
#
# pip install pysolar requests timezonefinder
#
# Usage:
#   python solar.py                    <- runs built-in tests
#   from solar import geocode_address, get_solar_window

import warnings
warnings.filterwarnings("ignore")  # suppress pysolar leap-second warning

import time
import requests
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from pysolar.solar import get_altitude, get_azimuth

try:
    from timezonefinder import TimezoneFinder as _TF
    _tf = _TF()
except ImportError:
    _tf = None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Facade direction -> azimuth range (degrees) from which that facade receives sun.
# These are standard compass arcs and work at any latitude/hemisphere.
# pysolar's azimuth output is hemisphere-aware, so the math is correct globally.
#
# Range format: (lo, hi) — sun azimuth must be between lo and hi.
# Ranges that wrap around north (lo > hi) use "az >= lo OR az <= hi".
FACADE_RANGES = {
    "N":  (315,  45),
    "NE": (315, 135),
    "E":  (  0, 180),
    "SE": ( 45, 225),
    "S":  ( 90, 270),
    "SW": (135, 315),
    "W":  (180, 360),
    "NW": (225,  45),
}

# Simple in-memory geocoding cache — avoids re-calling Nominatim for same address
_geocache: dict[str, tuple[float, float]] = {}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _timezone_for(lat: float, lon: float):
    """Look up the local timezone from coordinates, fall back to longitude offset."""
    if _tf is not None:
        tz_name = _tf.timezone_at(lat=lat, lng=lon)
        if tz_name:
            return ZoneInfo(tz_name)
    # Fallback when timezonefinder is not installed: approximate from longitude
    return timezone(timedelta(hours=round(lon / 15)))


# ---------------------------------------------------------------------------
# Function 1: Geocoding
# ---------------------------------------------------------------------------

def geocode_address(address_str: str) -> tuple[float, float]:
    """
    Convert a human-readable address to (latitude, longitude).

    Uses OpenStreetMap Nominatim — free, no API key required, works worldwide.
    Results are cached in memory so repeated calls don't hit the API.

    Args:
        address_str: e.g. "10 Downing Street, London" or "Eiffel Tower, Paris"

    Returns:
        (latitude, longitude) as floats

    Raises:
        ValueError: if the address cannot be found
    """
    key = address_str.strip().lower()
    if key in _geocache:
        return _geocache[key]

    url = "https://nominatim.openstreetmap.org/search"
    headers = {"User-Agent": "SunProofAI/1.0 (student project)"}

    # Structured search (street + city) is more precise for exact addresses;
    # free-text is the fallback for landmarks and ambiguous inputs.
    parts = [p.strip() for p in address_str.split(",")]
    attempts: list[dict] = []

    if len(parts) >= 2:
        attempts.append({
            "street": parts[0],
            "city": ",".join(parts[1:]),
            "format": "json",
            "limit": 1,
        })

    attempts.append({
        "q": address_str,
        "format": "json",
        "limit": 1,
    })

    last_error: Exception | None = None
    for i, params in enumerate(attempts):
        if i > 0:
            time.sleep(1)           # Nominatim rate-limit: max 1 req/sec
        try:
            response = requests.get(url, params=params, headers=headers, timeout=10)
            response.raise_for_status()
            results = response.json()
            if results:
                lat = float(results[0]["lat"])
                lon = float(results[0]["lon"])
                _geocache[key] = (lat, lon)
                return (lat, lon)
        except requests.RequestException as e:
            last_error = e

    if last_error:
        raise ValueError(f"Geocoding network error for '{address_str}': {last_error}")
    raise ValueError(
        f"Address not found: '{address_str}'. "
        "Try the format 'Street Name, City, Country', e.g. '5 Avenue Anatole France, Paris, France'."
    )


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
    that the facade faces. Times are shown in the local timezone for the
    given coordinates.

    Args:
        lat:              latitude (e.g. 48.86 for Paris)
        lon:              longitude (e.g. 2.35 for Paris)
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
    local_tz = _timezone_for(lat, lon)  # auto-detected from coordinates

    sun_hours: list[str] = []
    rise_az: float | None = None
    set_az: float | None = None
    prev_above = False
    golden_hour_on_facade = False  # any facade sun hour with altitude < 6°

    for hour in range(0, 24):
        dt = datetime(year, month, 15, hour, 0, 0, tzinfo=timezone.utc)
        altitude = get_altitude(lat, lon, dt)
        azimuth  = get_azimuth(lat, lon, dt)

        above_horizon = altitude > 0

        if above_horizon and not prev_above:
            rise_az = round(azimuth, 1)
        if not above_horizon and prev_above:
            set_az = round(azimuth, 1)

        prev_above = above_horizon

        if above_horizon:
            if lo < hi:
                in_arc = lo <= azimuth <= hi
            else:
                # Arc wraps around north (e.g. N: 315–45)
                in_arc = azimuth >= lo or azimuth <= hi

            if in_arc:
                local_hour = dt.astimezone(local_tz).hour
                sun_hours.append(f"{local_hour:02d}:00")
                if altitude < 6:
                    golden_hour_on_facade = True

    hours_count = len(sun_hours)
    receives = hours_count > 0

    month_name = datetime(year, month, 1).strftime("%B")
    if not receives:
        notes = f"{direction}-facing facade receives no direct sun in {month_name}."
    else:
        notes = (
            f"{direction}-facing facade receives direct sun in {month_name} "
            f"from {sun_hours[0]} to {sun_hours[-1]}."
        )

    return {
        "sun_rise_azimuth":          rise_az,
        "sun_set_azimuth":           set_az,
        "hours_facade_receives_sun": hours_count,
        "best_sun_times":            sun_hours,
        "facade_receives_sun":       receives,
        "has_golden_hour":           golden_hour_on_facade,
        "notes":                     notes,
    }


# ---------------------------------------------------------------------------
# Function 3: Sun elevation at a specific time
# ---------------------------------------------------------------------------

def get_sun_elevation_at_time(lat: float, lon: float, local_hour: int, month: int) -> dict:
    """
    Return the solar altitude at a specific local hour on the 15th of the month.

    elevation_category uses the same vocabulary as the VLM's sun_elevation field:
      "low"           — altitude < 15°  (golden-hour / blue-hour light)
      "medium"        — 15° ≤ alt < 45° (mid-morning or mid-afternoon)
      "high"          — altitude ≥ 45°  (near-midday, short shadows)
      "below_horizon" — sun has not risen or has already set

    Args:
        lat, lon:    coordinates of the property
        local_hour:  hour of day in local time (0–23)
        month:       integer 1–12

    Returns:
        dict with keys:
            altitude_deg       (float) — solar altitude in degrees
            elevation_category (str)   — "low" | "medium" | "high" | "below_horizon"
    """
    local_tz = _timezone_for(lat, lon)
    year = datetime.now().year
    local_dt = datetime(year, month, 15, local_hour, 0, 0, tzinfo=local_tz)
    utc_dt = local_dt.astimezone(timezone.utc)

    altitude = get_altitude(lat, lon, utc_dt)

    if altitude <= 0:
        category = "below_horizon"
    elif altitude < 15:
        category = "low"
    elif altitude < 45:
        category = "medium"
    else:
        category = "high"

    return {
        "altitude_deg": round(altitude, 1),
        "elevation_category": category,
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

    month_name = datetime(datetime.now().year, month, 1).strftime("%B")
    sun_hours = result["best_sun_times"]
    if sun_hours:
        result["notes"] = (
            f"{address_str} — {facade_direction}-facing facade receives direct sun "
            f"in {month_name} from {sun_hours[0]} to {sun_hours[-1]}."
        )
    else:
        result["notes"] = (
            f"{address_str} — {facade_direction}-facing facade receives no direct sun in {month_name}."
        )

    return result


# ---------------------------------------------------------------------------
# Built-in tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        ("Karl Johans gate 22, Oslo, Norway",      "S",  6),
        ("Eiffel Tower, Paris, France",             "S",  6),
        ("10 Downing Street, London, UK",           "W",  7),
        ("Sydney Opera House, Sydney, Australia",   "N",  12),
        ("Times Square, New York, USA",             "SE", 9),
        ("Shibuya Crossing, Tokyo, Japan",          "E",  3),
    ]

    for address, direction, month in tests:
        print(f"\n{'='*60}")
        print(f"Address  : {address}")
        print(f"Facade   : {direction}   Month: {month}")
        try:
            r = analyze_address(address, direction, month)
            print(f"Coords   : {r['lat']}, {r['lon']}")
            print(f"Sun hours: {r['hours_facade_receives_sun']}")
            print(f"Notes    : {r['notes']}")
        except ValueError as e:
            print(f"ERROR: {e}")
