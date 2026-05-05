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

    # Count unclear fields based on Ole's 5-field contract
    unclear_count = sum([
        vlm_data.get("sun_direction") == "unclear",
        vlm_data.get("sun_elevation") == "unclear",
        vlm_data.get("lighting") == "overcast",
        vlm_data.get("shadows_visible") is None,
        vlm_data.get("sun_on_facade") is None
    ])

    # Scenario A: The photo is too blurry, or lacks sufficient clear evidence
    if vlm_data.get("sun_direction") == "unclear" or unclear_count >= 2:
        verdict = "Inconclusive"
        explanation = "The image lighting is too ambiguous or lacks sufficient clear evidence to confidently verify."
    
    # Scenario B: The photo shows sun where physics says it's impossible
    elif vlm_data.get("sun_on_facade") is True and solar_data.get("facade_receives_sun") is False:
        verdict = "Possibly misleading"
        explanation = f"Photo shows sun, but calculations show {orientation} faces get no sun in {month}."
    
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