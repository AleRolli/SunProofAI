from fastapi import FastAPI, UploadFile, File, Form, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import anthropic

from image_analysis import analyze_image  # Ole's Vision API
from solar import analyze_address         # Ferdinand's Solar/Geocoding API

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

# ── 3. Public Endpoints ────────────────────────────────────

@app.get("/health")
def health_check():
    return {"status": "ok", "message": "FastAPI backend is running!"}

@app.post("/analyze")
async def analyze_property(
    image: UploadFile = File(...),
    address: str = Form(...),
    orientation: str = Form(...),
    month: str = Form(...)
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

    # Count unclear fields based on Ole's 4-field contract
    unclear_count = sum([
        vlm_data.get("sun_elevation") == "unclear",
        vlm_data.get("lighting") == "overcast",
        vlm_data.get("shadows_visible") is None,
        vlm_data.get("sun_on_facade") is None 

    ])

    # Pre-compute whether the facade's solar window includes golden-hour slots.
    # Low-angle sun only occurs before 09:00 or after 19:00; if the window has
    # no such hours, a sunset/sunrise photo on that facade is impossible.
    best_times = solar_data.get("best_sun_times", [])
    has_golden_hour = any(
        int(t.split(":")[0]) < 9 or int(t.split(":")[0]) >= 19
        for t in best_times
    )

    # Scenario A: The photo is too blurry, or lacks sufficient clear evidence
    if unclear_count >= 2:
        verdict = "Inconclusive"
        explanation = "The image lighting is too ambiguous or lacks sufficient clear evidence to confidently verify."

    # Scenario B1: The photo shows sun where physics says it's impossible
    elif vlm_data.get("sun_on_facade") is True and solar_data.get("facade_receives_sun") is False:
        verdict = "Possibly misleading"
        explanation = f"Photo shows sun, but calculations show {orientation} faces get no sun in {month}."

    # Scenario B2: Photo shows low-angle golden-hour light but the facade's
    # solar window never includes those hours — the light is geometrically wrong.
    elif (
        vlm_data.get("sun_elevation") == "low"
        and vlm_data.get("sun_on_facade") is True
        and not has_golden_hour
    ):
        window = f"{best_times[0]}–{best_times[-1]}" if best_times else "a limited window"
        verdict = "Possibly misleading"
        explanation = (
            f"Photo shows low-angle golden-hour light, but a {orientation}-facing facade "
            f"in {month} only receives sun between {window} — not during sunrise or sunset hours."
        )

    # Scenario C: Everything matches
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