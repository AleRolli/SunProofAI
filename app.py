import mimetypes
import streamlit as st
from datetime import datetime

try:
    import requests as _requests
except ImportError:
    _requests = None

mimetypes.add_type("image/jpeg", ".jpeg")

st.set_page_config(
    page_title="SunProof AI",
    page_icon="☀️",
    layout="centered"
)

# ── Session state ─────────────────────────────────────────────
if "page" not in st.session_state:
    st.session_state.page = "input"
if "result" not in st.session_state:
    st.session_state.result = None
if "submitted_image" not in st.session_state:
    st.session_state.submitted_image = None
if "submitted_inputs" not in st.session_state:
    st.session_state.submitted_inputs = None

BACKEND_URL = "http://localhost:8000"

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]

# ══════════════════════════════════════════════════════════════
# BACKEND CALL — tries FastAPI first, falls back to mock
# ══════════════════════════════════════════════════════════════
def call_backend(image_bytes, address, orientation, month):
    if _requests is not None:
        try:
            response = _requests.post(
                f"{BACKEND_URL}/analyze",
                files={"image": ("photo.jpg", image_bytes, "image/jpeg")},
                data={"address": address, "orientation": orientation, "month": month},
                timeout=30,
            )
            response.raise_for_status()
            return response.json()
        except Exception:
            pass
    return _mock_analyze(address, orientation, month)


def _mock_analyze(address, orientation, month):
    """
    Rotates through all 3 verdicts by orientation so every result state
    is testable without a live backend:
      N / NE / NW  →  Possibly misleading
      S / SE / SW  →  Consistent
      E / W        →  Inconclusive
    """
    if orientation in ("N", "NE", "NW"):
        verdict = "Possibly misleading"
        explanation = (
            f"The image suggests direct south-westerly sunlight on the facade, "
            f"but solar calculations show that a {orientation}-facing facade at "
            f"{address} cannot receive direct sunlight from that direction in {month}."
        )
        solar_summary = (
            f"A {orientation}-facing facade in {month} receives direct sun only in the "
            f"early morning hours. The warm golden-hour light shown in the image is "
            f"geometrically inconsistent with this orientation."
        )
    elif orientation in ("S", "SE", "SW"):
        verdict = "Consistent"
        explanation = (
            f"The lighting shown in the image is consistent with what a "
            f"{orientation}-facing facade at {address} can receive in {month}. "
            f"No geometric inconsistency detected."
        )
        solar_summary = (
            f"A {orientation}-facing facade in {month} receives direct sun from "
            f"approximately 10:00 to 16:00. The image lighting matches this window."
        )
    else:
        verdict = "Inconclusive"
        explanation = (
            f"The image does not contain sufficient shadow or lighting evidence "
            f"to determine whether the sun conditions shown are plausible for a "
            f"{orientation}-facing facade at {address} in {month}."
        )
        solar_summary = (
            f"A {orientation}-facing facade in {month} receives sun during a limited "
            f"window. Overcast or diffuse lighting in the image prevents a clear "
            f"geometric comparison."
        )
    return {"verdict": verdict, "explanation": explanation, "solar_summary": solar_summary}


# ══════════════════════════════════════════════════════════════
# PAGE 1 — INPUT FORM
# ══════════════════════════════════════════════════════════════
def show_input_page():
    st.title("☀️ SunProof AI")
    st.caption(
        "Verify whether AI-edited listing photos show sun conditions that are "
        "physically possible at that address and facade direction."
    )
    st.divider()

    # ── Step 1: Upload ────────────────────────────────────────
    st.subheader("1. Upload a listing photo")
    uploaded_file = st.file_uploader(
        "Choose an image file",
        type=["jpg", "jpeg", "png"],
        help="Upload the AI-edited listing photo you want to verify.",
        label_visibility="collapsed",
    )
    if uploaded_file:
        st.image(uploaded_file, caption=uploaded_file.name, use_container_width=True)

    st.divider()

    # ── Step 2: Property details ──────────────────────────────
    st.subheader("2. Property details")

    address = st.text_input(
        "Street address",
        placeholder="e.g. Bygdøy allé 30, Oslo",
    )

    col1, col2 = st.columns(2)
    with col1:
        orientation = st.selectbox(
            "Facade / balcony orientation",
            options=["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
            index=4,
            help="The compass direction the facade or balcony faces.",
        )
    with col2:
        month = st.selectbox(
            "Month to check",
            options=MONTHS,
            index=datetime.now().month - 1,
            help="Defaults to the current month.",
        )

    st.divider()

    if st.button("Check sun conditions", type="primary", use_container_width=True):
        if not uploaded_file:
            st.warning("Please upload a listing photo before checking.")
        elif not address.strip():
            st.warning("Please enter a street address.")
        else:
            with st.spinner("Analysing photo and calculating solar position..."):
                result = call_backend(
                    image_bytes=uploaded_file.getvalue(),
                    address=address.strip(),
                    orientation=orientation,
                    month=month,
                )
            st.session_state.result = result
            st.session_state.submitted_image = uploaded_file.getvalue()
            st.session_state.submitted_inputs = {
                "address": address.strip(),
                "orientation": orientation,
                "month": month,
                "filename": uploaded_file.name,
            }
            st.session_state.page = "results"
            st.rerun()


# ══════════════════════════════════════════════════════════════
# PAGE 2 — RESULTS PAGE
# ══════════════════════════════════════════════════════════════
_VERDICT_STYLE = {
    "Consistent":         ("success", "✅  Consistent"),
    "Possibly misleading": ("error",   "🚨  Possibly misleading"),
    "Inconclusive":       ("warning",  "⚠️  Inconclusive"),
}

def show_results_page():
    result = st.session_state.result
    inputs = st.session_state.submitted_inputs or {}
    verdict = result.get("verdict", "Inconclusive")

    st.title("☀️ SunProof AI — Result")
    st.caption(
        f"{inputs.get('address', '')}  ·  "
        f"Orientation: {inputs.get('orientation', '')}  ·  "
        f"{inputs.get('month', '')}"
    )
    st.divider()

    # ── Verdict badge ─────────────────────────────────────────
    style, label = _VERDICT_STYLE.get(verdict, ("info", f"ℹ️  {verdict}"))
    getattr(st, style)(f"**Verdict: {label}**")

    st.divider()

    # ── Explanation + Solar summary (two columns) ─────────────
    col_left, col_right = st.columns(2)
    with col_left:
        st.subheader("Explanation")
        st.write(result.get("explanation", ""))
    with col_right:
        st.subheader("Solar calculation summary")
        st.info(result.get("solar_summary", ""))

    # ── Analysed photo ────────────────────────────────────────
    if st.session_state.submitted_image:
        st.divider()
        st.subheader("Analysed photo")
        st.image(
            st.session_state.submitted_image,
            caption=inputs.get("filename", "Uploaded photo"),
            use_container_width=True,
        )

    st.divider()

    # ── PDF download (placeholder — Person 5 supplies bytes) ──
    st.subheader("Compliance note")
    st.download_button(
        label="Download PDF report",
        data=b"",
        file_name="sunproof_report.pdf",
        mime="application/pdf",
        disabled=True,
        use_container_width=True,
        help="PDF generation coming in Week 2 (Person 5).",
    )
    st.caption(
        "This tool provides a compliance-support note, not legal advice. "
        "PDF download will be enabled once the report module is connected."
    )

    st.divider()

    # ── Back button ───────────────────────────────────────────
    if st.button("← Check another photo", use_container_width=True):
        st.session_state.page = "input"
        st.session_state.result = None
        st.session_state.submitted_image = None
        st.session_state.submitted_inputs = None
        st.rerun()


# ══════════════════════════════════════════════════════════════
# ROUTER
# ══════════════════════════════════════════════════════════════
if st.session_state.page == "input":
    show_input_page()
else:
    show_results_page()
