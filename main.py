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

    # Golden-hour threshold: true golden-hour light (intense orange, sun near
    # horizon) only occurs before 07:00 or after 19:00. An 08:00 morning window
    # start is not sunset-level low-angle light.
    best_times = solar_data.get("best_sun_times", [])
    has_golden_hour = any(
        int(t.split(":")[0]) < 7 or int(t.split(":")[0]) >= 19
        for t in best_times
    )

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

            # Priority 1: compare the elevation the VLM sees to the real solar altitude.
            if _lat is not None and _lon is not None and _observed_elev not in (None, "unclear"):
                _elev = get_sun_elevation_at_time(_lat, _lon, _photo_hour_int, month_int)
                _expected_cat = _elev["elevation_category"]

                if _expected_cat == "below_horizon":
                    _time_verdict = "Possibly misleading"
                    _time_explanation = (
                        f"Photo taken at {photo_time}, but the sun is below the horizon "
                        f"at that time at this location in {month}."
                    )
                elif _expected_cat != _observed_elev:
                    _time_verdict = "Possibly misleading"
                    _time_explanation = (
                        f"The sun appears {_observed_elev} in the photo, but at {photo_time} "
                        f"in {month} the calculated solar elevation is {_elev['altitude_deg']}° "
                        f"— consistent with {_expected_cat} light, not {_observed_elev}."
                    )

            # Priority 2: window check using the real pysolar result so the
            # reported window matches the solar summary exactly.
            if _time_verdict is None:
                if best_times:
                    _real_start = int(best_times[0].split(":")[0])
                    _real_end   = int(best_times[-1].split(":")[0])
                    if not (_real_start <= _photo_hour_int <= _real_end):
                        _time_verdict = "Possibly misleading"
                        _time_explanation = (
                            f"Photo taken at {photo_time}, but this {orientation}-facing facade "
                            f"only receives direct sun between {best_times[0]} and "
                            f"{best_times[-1]} in {month}."
                        )
                    else:
                        _time_verdict = "Consistent"
                        _time_explanation = (
                            f"The photo time ({photo_time}) falls within the calculated sun window "
                            f"for this {orientation}-facing facade ({best_times[0]}–{best_times[-1]})."
                        )
                else:
                    # Fallback: no real sun hours means facade_receives_sun is False,
                    # normally caught by B1 — use SUN_WINDOWS as a safety net.
                    _sun_window = SUN_WINDOWS.get(orientation)
                    if _sun_window is None:
                        _time_verdict = "Possibly misleading"
                        _time_explanation = (
                            f"Photo taken at {photo_time} appears to show direct sunlight, but "
                            f"{orientation}-facing facades do not receive direct sun at mid-latitudes."
                        )
                    elif not (_sun_window[0] <= _photo_hour_int < _sun_window[1]):
                        _time_verdict = "Possibly misleading"
                        _time_explanation = (
                            f"Photo taken at {photo_time}, but {orientation}-facing facades typically "
                            f"only receive direct sun between {_sun_window[0]:02d}:00 and "
                            f"{_sun_window[1]:02d}:00."
                        )
                    else:
                        _time_verdict = "Consistent"
                        _time_explanation = (
                            f"The photo time ({photo_time}) falls within the expected sun window "
                            f"for a {orientation}-facing facade "
                            f"({_sun_window[0]:02d}:00–{_sun_window[1]:02d}:00)."
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
    # This remains a STUB until the PDF generation module is ready.
    empty_pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF"
    return Response(content=empty_pdf_bytes, media_type="application/pdf")