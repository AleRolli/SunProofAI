from fastapi import FastAPI, UploadFile, File, Form, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import anthropic

from image_analysis import analyze_image                      # Ole's Vision API
from solar import analyze_address, get_sun_elevation_at_time  # Ferdinand's Solar/Geocoding API

app = FastAPI()

# ── 1. Middleware ──────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 2. Data Contracts & Helpers ────────────────────────────
class ReportRequest(BaseModel):
    address: str
    orientation: str
    month: str
    verdict: str
    explanation: str
    solar_summary: str

# Ferdinand's math requires an integer (1-12), but Alessandro's frontend sends a string ("July").
MONTH_MAP = {
    "January": 1, "February": 2, "March": 3, "April": 4,
    "May": 5, "June": 6, "July": 7, "August": 8,
    "September": 9, "October": 10, "November": 11, "December": 12
}

# Approximate hour ranges during which each facade can receive direct sun.
# Broad enough to cover seasonal variation; precise check is done by solar.py.
# None = orientation never gets direct sun at mid-latitudes.
SUN_WINDOWS: dict[str, tuple[int, int] | None] = {
    "N":  None,
    "NE": (4, 10),
    "E":  (5, 13),
    "SE": (7, 15),
    "S":  (9, 17),
    "SW": (11, 19),
    "W":  (13, 21),
    "NW": (17, 22),
}

# Human-readable label for each VLM sun_elevation category.
_VLM_LABEL = {"low": "sunrise/sunset", "medium": "morning/afternoon", "high": "midday"}


def _sun_period(altitude_deg: float, local_hour: int) -> str:
    """Classify the character of sunlight from real solar altitude and local hour."""
    if altitude_deg < 6:
        return "sunrise" if local_hour <= 12 else "sunset"
    elif altitude_deg < 30:
        return "morning sun" if local_hour < 13 else "afternoon sun"
    else:
        return "midday sun"

# ── 3. Public Endpoints ────────────────────────────────────

@app.get("/health")
def health_check():
    return {"status": "ok", "message": "FastAPI backend is running!"}

@app.post("/analyze")
async def analyze_property(
    image: UploadFile = File(...),
    address: str = Form(...),
    orientation: str = Form(...),
    month: str = Form(...),
    photo_time: str = Form(default=""),
):
    # 1. Read image bytes
    image_bytes = await image.read()

    # 2. Get Physical Truth (from Ferdinand's Real Solar Engine)
    month_int = MONTH_MAP.get(month, 7) # Defaults to 7 (July) if something goes wrong
    try:
        solar_data = analyze_address(address, orientation, month_int)
    except Exception as e:
        # If OpenStreetMap can't find the address, catch it gracefully!
        return {
            "verdict": "Inconclusive",
            "explanation": f"Could not verify location: {str(e)}",
            "solar_summary": "Geocoding failed. Check the address spelling."
        }

    # 3. Get Visual Evidence (from Ole's Vision Engine)
    try:
        vlm_data = analyze_image(image_bytes)
    except (anthropic.AuthenticationError, anthropic.BadRequestError, anthropic.RateLimitError) as e:
        return {
            "verdict": "Inconclusive",
            "explanation": f"Vision AI API error: {str(e)}",
            "solar_summary": solar_data["notes"]
        }
    except Exception as e:
        return {
            "verdict": "Inconclusive",
            "explanation": f"Unexpected error during vision analysis: {str(e)}",
            "solar_summary": solar_data["notes"]
        }

    # 4. Reconciliation Logic
    verdict = "Inconclusive"
    explanation = "Not enough clear evidence in the photo."

    # Interior window shots legitimately have sun_on_facade=None and
    # shadows_visible=None — that's not ambiguity, just scene type.
    is_interior = vlm_data.get("scene_type") == "interior_window"

    # sun_present: sun is visible in the frame OR the facade is clearly lit.
    # Both are equally strong evidence that sunlight exists in the photo.
    sun_present = (
        vlm_data.get("sun_visible_in_frame") is True
        or vlm_data.get("sun_on_facade") is True
    )

    # Ambiguity score: only penalise None values when we'd actually expect data.
    # For interior shots, sun_on_facade and shadows_visible are always None —
    # don't count them as missing evidence.
    unclear_count = sum([
        vlm_data.get("sun_elevation") == "unclear",
        vlm_data.get("lighting") == "overcast",
        vlm_data.get("shadows_visible") is None and not is_interior,
        vlm_data.get("sun_on_facade") is None and not is_interior,
        vlm_data.get("sun_visible_in_frame") is None,
    ])

    # has_golden_hour: computed by solar.py from real altitude data (< 6°).
    # Location- and season-aware, unlike a fixed clock cutoff.
    best_times = solar_data.get("best_sun_times", [])
    has_golden_hour = solar_data.get("has_golden_hour", False)

    # Pre-compute time-based verdict BEFORE the elif chain so both the elevation
    # check (real solar math) and the coarse window check can inform a single branch.
    _time_verdict: str | None = None
    _time_explanation = ""

    if photo_time and sun_present:
        try:
            _photo_hour_int = int(photo_time.split(":")[0])
            _lat = solar_data.get("lat")
            _lon = solar_data.get("lon")
            _observed_elev = vlm_data.get("sun_elevation")

            # Always compute real altitude when coordinates are available so we
            # can label the expected light character in every explanation.
            _elev = None
            if _lat is not None and _lon is not None:
                _elev = get_sun_elevation_at_time(_lat, _lon, _photo_hour_int, month_int)

            # Priority 1: compare the elevation the VLM sees to the real solar altitude.
            if _elev is not None:
                _expected_cat = _elev["elevation_category"]
                _expected_period = (
                    ("before sunrise" if _photo_hour_int < 12 else "after sunset")
                    if _expected_cat == "below_horizon"
                    else _sun_period(_elev["altitude_deg"], _photo_hour_int)
                )

                if _expected_cat == "below_horizon":
                    _time_verdict = "Possibly misleading"
                    _time_explanation = (
                        f"Photo taken at {photo_time} ({_expected_period}), but the sun is "
                        f"below the horizon at that time at this location in {month}."
                    )
                elif _observed_elev not in (None, "unclear") and _expected_cat != _observed_elev:
                    _observed_period = _VLM_LABEL.get(_observed_elev, _observed_elev)
                    _time_verdict = "Possibly misleading"
                    _time_explanation = (
                        f"The photo shows {_observed_period} light, but at {photo_time} in "
                        f"{month} the sun is at {_elev['altitude_deg']}° — {_expected_period}, "
                        f"not {_observed_period}."
                    )

            # Priority 2: window check using the real pysolar result so the
            # reported window matches the solar summary exactly.
            if _time_verdict is None:
                _period_label = (
                    f" ({_sun_period(_elev['altitude_deg'], _photo_hour_int)})"
                    if _elev and _elev["elevation_category"] != "below_horizon"
                    else ""
                )
                if best_times:
                    _real_start = int(best_times[0].split(":")[0])
                    _real_end   = int(best_times[-1].split(":")[0])
                    if not (_real_start <= _photo_hour_int <= _real_end):
                        _time_verdict = "Possibly misleading"
                        _time_explanation = (
                            f"Photo taken at {photo_time}{_period_label}, but this "
                            f"{orientation}-facing facade only receives direct sun between "
                            f"{best_times[0]} and {best_times[-1]} in {month}."
                        )
                    else:
                        _time_verdict = "Consistent"
                        _time_explanation = (
                            f"The photo time ({photo_time}) falls within the sun window for "
                            f"this {orientation}-facing facade ({best_times[0]}–{best_times[-1]})"
                            f"{_period_label}."
                        )
                else:
                    # Fallback: no real sun hours means facade_receives_sun is False,
                    # normally caught by B1 — use SUN_WINDOWS as a safety net.
                    _sun_window = SUN_WINDOWS.get(orientation)
                    if _sun_window is None:
                        _time_verdict = "Possibly misleading"
                        _time_explanation = (
                            f"Photo taken at {photo_time}{_period_label} appears to show "
                            f"direct sunlight, but {orientation}-facing facades do not receive "
                            f"direct sun at mid-latitudes."
                        )
                    elif not (_sun_window[0] <= _photo_hour_int < _sun_window[1]):
                        _time_verdict = "Possibly misleading"
                        _time_explanation = (
                            f"Photo taken at {photo_time}{_period_label}, but "
                            f"{orientation}-facing facades typically only receive direct sun "
                            f"between {_sun_window[0]:02d}:00 and {_sun_window[1]:02d}:00."
                        )
                    else:
                        _time_verdict = "Consistent"
                        _time_explanation = (
                            f"The photo time ({photo_time}) falls within the sun window for a "
                            f"{orientation}-facing facade "
                            f"({_sun_window[0]:02d}:00–{_sun_window[1]:02d}:00){_period_label}."
                        )
        except (ValueError, IndexError):
            pass  # malformed photo_time — skip time checks

    # Scenario A: Image too ambiguous to judge
    if unclear_count >= 2:
        verdict = "Inconclusive"
        explanation = "The image lighting is too ambiguous or lacks sufficient clear evidence to confidently verify."

    # Scenario B1: Sun visible in photo but this facade gets zero sun in this month
    elif sun_present and solar_data.get("facade_receives_sun") is False:
        verdict = "Possibly misleading"
        explanation = f"Photo shows sun, but calculations show {orientation} faces get no sun in {month}."

    # Scenario B2: Photo shows intense golden-hour light but the facade's solar
    # window never reaches those early/late hours — geometrically impossible.
    elif (
        vlm_data.get("sun_elevation") == "low"
        and vlm_data.get("lighting") == "direct"
        and sun_present
        and not has_golden_hour
    ):
        window = f"{best_times[0]}–{best_times[-1]}" if best_times else "a limited window"
        verdict = "Possibly misleading"
        explanation = (
            f"Photo shows low-angle golden-hour light, but a {orientation}-facing facade "
            f"in {month} only receives sun between {window} — not during sunrise or sunset hours."
        )

    # Scenario T/E: time + solar-elevation cross-check
    elif _time_verdict is not None:
        verdict = _time_verdict
        explanation = _time_explanation

    # Scenario C: Evidence is consistent with the stated orientation
    else:
        verdict = "Consistent"
        explanation = "The photo lighting is consistent with the property's orientation."

    return {
        "verdict": verdict,
        "explanation": explanation,
        "solar_summary": solar_data["notes"]
    }

@app.post("/report")
async def create_report(request_data: ReportRequest):
    from report import build_report
    pdf_bytes = build_report(request_data.model_dump())
    return Response(content=pdf_bytes, media_type="application/pdf")