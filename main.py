from fastapi import FastAPI, UploadFile, File, Form, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from image_analysis import analyze_image  # Ensure file is named image_analysis.py

app = FastAPI()

# ── 1. Middleware ──────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 2. Data Contracts ──────────────────────────────────────
class ReportRequest(BaseModel):
    address: str
    orientation: str
    month: str
    verdict: str
    explanation: str
    solar_summary: str

# ── 3. Internal Logic Stubs ────────────────────────────────
def get_solar_stub(orientation: str, month: str) -> dict:
    """Placeholder math: assumes North-facing never gets sun."""
    if orientation in ["N", "NE", "NW"]:
        return {
            "facade_receives_sun": False, 
            "notes": f"Physics says a {orientation}-facing facade gets no sun in {month}."
        }
    return {
        "facade_receives_sun": True, 
        "notes": f"Physics says a {orientation}-facing facade can receive sun in {month}."
    }

# ── 4. Public Endpoints ────────────────────────────────────

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

    # 2. Get Visual Evidence (from Ole's module)
    vlm_data = analyze_image(image_bytes)

    # 3. Get Physical Truth (from our stub)
    solar_data = get_solar_stub(orientation, month)

    # 4. Reconciliation Logic
    verdict = "Inconclusive"
    explanation = "Not enough clear evidence in the photo."

    if vlm_data.get("sun_direction") == "unclear":
        verdict = "Inconclusive"
        explanation = "The image lighting is too ambiguous to verify."
    
    elif vlm_data.get("sun_on_facade") is True and solar_data.get("facade_receives_sun") is False:
        verdict = "Possibly misleading"
        explanation = f"Photo shows sun, but calculations show {orientation} faces get no sun in {month}."
    
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