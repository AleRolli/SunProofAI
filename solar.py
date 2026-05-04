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
        address_str: e.g. "Karl Johans gate 22, Oslo" or "Aker Brygge, Oslo"

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

    # Build a list of query strategies to try in order.
    # Structured search (street + city) is more precise for exact addresses;
    # free-text is the fallback for landmarks and ambiguous inputs.
    parts = [p.strip() for p in address_str.split(",")]
    attempts: list[dict] = []

    if len(parts) >= 2:
        # Structured: lets Nominatim match street number exactly
        attempts.append({
            "street": parts[0],
            "city": ",".join(parts[1:]),
            "format": "json",
            "limit": 1,
            "countrycodes": "no",
        })

    # Free-text with Norway filter
    attempts.append({
        "q": address_str,
        "format": "json",
        "limit": 1,
        "countrycodes": "no",
    })

    # Last resort: no country restriction (catches edge cases)
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
        "Try the format 'Street Name 5, City', e.g. 'Karl Johans gate 22, Oslo'."
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