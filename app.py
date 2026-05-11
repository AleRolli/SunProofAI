import base64
import mimetypes
import os
import streamlit as st
from datetime import datetime
from io import BytesIO
from pathlib import Path

from PIL import Image, ExifTags

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
if "sample_image_bytes" not in st.session_state:
    st.session_state.sample_image_bytes = None
if "sample_filename" not in st.session_state:
    st.session_state.sample_filename = None
if "uploader_key" not in st.session_state:
    st.session_state.uploader_key = 0
if "photo_hour" not in st.session_state:
    st.session_state.photo_hour = "Not specified"

def _resolve_backend_url() -> str:
    """Pick a backend URL from env var → st.secrets → localhost default.

    Env var wins so a local dev can override a deployed config without
    touching secrets. st.secrets is the path Streamlit Cloud uses (paste
    BACKEND_URL into the app's Secrets section in the dashboard).
    """
    if env_url := os.environ.get("BACKEND_URL"):
        return env_url
    try:
        return st.secrets["BACKEND_URL"]
    except Exception:
        return "http://localhost:8000"


BACKEND_URL = _resolve_backend_url()

MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]

# ── Demo samples ──────────────────────────────────────────────
# One-click presets that load a test image and pre-fill the form so the
# three verdict states are reachable without uploading anything.
SAMPLES_DIR = Path(__file__).parent / "test_images"

SAMPLES = [
    {
        "label": "✅  Consistent",
        "filename": "direct_sun.PNG",
        "address": "Bygdøy allé 30, Oslo",
        "orientation": "S",
        "month": "July",
        "photo_hour": "13:00",
        "help": "South-facing facade in summer with bright direct sun — the geometry checks out.",
    },
    {
        "label": "🚨  Possibly Misleading",
        "filename": "golden_hour.PNG",
        "address": "Karl Johans gate 1, Oslo",
        "orientation": "E",
        "month": "July",
        "photo_hour": "17:00",
        "help": "East-facing facade with sun shown at 17:00 — east facades only get morning sun.",
    },
    {
        "label": "⚠️  Inconclusive",
        "filename": "normal.jpg",
        "address": "Markveien 35, Grünerløkka, Oslo",
        "orientation": "E",
        "month": "October",
        "photo_hour": "Not specified",
        "help": "Diffuse autumn lighting on an east-facing facade — not enough evidence to judge.",
    },
]

# ══════════════════════════════════════════════════════════════
# EXIF EXTRACTION — pure-frontend, best-effort
# ══════════════════════════════════════════════════════════════
# Tag IDs are integer constants from the EXIF spec; using them directly
# avoids needing the ExifTags.IFD enum (Pillow ≥ 8.4) on older installs.
_EXIF_IFD_POINTER = 0x8769
_GPS_IFD_POINTER  = 0x8825
_TAG_DATETIME_ORIGINAL = 0x9003
_TAG_DATETIME          = 0x0132
_TAG_MAKE              = 0x010F
_TAG_MODEL             = 0x0110


def _gps_dms_to_decimal(dms, ref):
    """Convert ((deg,min,sec) rationals, 'N'/'S'/'E'/'W' ref) → signed decimal degrees."""
    if not dms or not ref:
        return None
    try:
        deg, mn, sec = (float(x) for x in dms)
        decimal = deg + mn / 60.0 + sec / 3600.0
        return -decimal if ref in ("S", "W") else decimal
    except (TypeError, ValueError):
        return None


def extract_photo_metadata(image_bytes):
    """Pull capture datetime, camera make/model, and GPS from image EXIF.

    Returns a dict with keys capture_dt, camera, gps. Values are None when
    the corresponding tags are missing or unreadable. Never raises.
    """
    out = {"capture_dt": None, "camera": None, "gps": None}
    try:
        img = Image.open(BytesIO(image_bytes))
        exif = img.getexif()
    except Exception:
        return out
    if not exif:
        return out

    # ── Capture datetime ─────────────────────────────────────
    dt_str = None
    try:
        exif_ifd = exif.get_ifd(_EXIF_IFD_POINTER)
        dt_str = exif_ifd.get(_TAG_DATETIME_ORIGINAL)
    except Exception:
        pass
    if not dt_str:
        dt_str = exif.get(_TAG_DATETIME)
    if dt_str:
        try:
            out["capture_dt"] = datetime.strptime(dt_str, "%Y:%m:%d %H:%M:%S")
        except (ValueError, TypeError):
            pass

    # ── Camera make + model ──────────────────────────────────
    make  = (exif.get(_TAG_MAKE)  or "").strip()
    model = (exif.get(_TAG_MODEL) or "").strip()
    camera = " ".join(s for s in (make, model) if s).strip()
    if camera:
        out["camera"] = camera

    # ── GPS coordinates ──────────────────────────────────────
    try:
        gps_ifd = exif.get_ifd(_GPS_IFD_POINTER)
        if gps_ifd:
            lat = _gps_dms_to_decimal(gps_ifd.get(2), gps_ifd.get(1))
            lon = _gps_dms_to_decimal(gps_ifd.get(4), gps_ifd.get(3))
            if lat is not None and lon is not None:
                out["gps"] = (lat, lon)
    except Exception:
        pass

    return out


# ══════════════════════════════════════════════════════════════
# BACKEND CALL — tries FastAPI first, falls back to mock
# ══════════════════════════════════════════════════════════════
def call_backend(image_bytes, address, orientation, month, photo_time: str = ""):
    if _requests is not None:
        try:
            response = _requests.post(
                f"{BACKEND_URL}/analyze",
                files={"image": ("photo.jpg", image_bytes, "image/jpeg")},
                data={
                    "address": address,
                    "orientation": orientation,
                    "month": month,
                    "photo_time": photo_time,
                },
                timeout=30,
            )
            response.raise_for_status()
            result = response.json()
            result["_source"] = "live"
            return result
        except Exception:
            pass
    result = _mock_analyze(address, orientation, month)
    result["_source"] = "mock"
    return result


def call_report_backend(result, inputs, image_bytes=None):
    """POST the verdict (and optionally the photo) to /report to get PDF bytes.

    Returns the raw PDF bytes on success, or None if the backend is unreachable
    or the response isn't a PDF.
    """
    if _requests is None:
        return None
    payload = {
        "address": inputs.get("address", ""),
        "orientation": inputs.get("orientation", ""),
        "month": inputs.get("month", ""),
        "verdict": result.get("verdict", ""),
        "explanation": result.get("explanation", ""),
        "solar_summary": result.get("solar_summary", ""),
    }
    if image_bytes:
        payload["image_b64"] = base64.b64encode(image_bytes).decode("utf-8")
    try:
        response = _requests.post(
            f"{BACKEND_URL}/report",
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
    except Exception:
        return None
    if not response.content.startswith(b"%PDF"):
        return None
    return response.content


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
        key=f"uploader_{st.session_state.uploader_key}",
    )

    # Resolve which image is "active": manual upload wins, else the sample.
    if uploaded_file is not None:
        active_image = uploaded_file.getvalue()
        active_filename = uploaded_file.name
    elif st.session_state.sample_image_bytes is not None:
        active_image = st.session_state.sample_image_bytes
        active_filename = f"Sample · {st.session_state.sample_filename}"
    else:
        active_image = None
        active_filename = None

    if active_image is not None:
        st.image(active_image, caption=active_filename, use_container_width=True)

        # ── Photo metadata (EXIF) ─────────────────────────────
        # Only renders when the image actually carries EXIF tags. Sample
        # PNGs and screenshots typically have none → block stays hidden.
        meta = extract_photo_metadata(active_image)
        if any(meta.values()):
            with st.expander("📷  Photo metadata", expanded=True):
                if meta["capture_dt"]:
                    dt = meta["capture_dt"]
                    st.markdown(
                        f"📅 &nbsp; **Captured:** {dt.strftime('%B %d, %Y · %H:%M')}",
                        unsafe_allow_html=True,
                    )
                    inferred_month = MONTHS[dt.month - 1]
                    if st.session_state.get("month") != inferred_month:
                        if st.button(
                            f"Use this month ({inferred_month})",
                            key="use_exif_month",
                            help="Sets 'Month to check' below to match the photo's capture month.",
                        ):
                            st.session_state.month = inferred_month
                            st.rerun()

                    inferred_hour = f"{dt.hour:02d}:00"
                    if st.session_state.get("photo_hour") != inferred_hour:
                        if st.button(
                            f"Use this hour ({inferred_hour})",
                            key="use_exif_hour",
                            help="Sets 'Hour photo was taken' below to match the photo's capture time.",
                        ):
                            st.session_state.photo_hour = inferred_hour
                            st.rerun()

                if meta["camera"]:
                    st.markdown(f"📷 &nbsp; **Camera:** {meta['camera']}", unsafe_allow_html=True)

                if meta["gps"]:
                    lat, lon = meta["gps"]
                    maps_url = (
                        f"https://www.google.com/maps/search/?api=1"
                        f"&query={lat:.6f},{lon:.6f}"
                    )
                    st.markdown(
                        f"📍 &nbsp; **GPS in photo:** {lat:.6f}, {lon:.6f} "
                        f"&nbsp; · &nbsp; [View on Google Maps ↗]({maps_url})",
                        unsafe_allow_html=True,
                    )
                    st.caption(
                        "Sanity-check: this should roughly match the address you enter below."
                    )

    # ── Demo sample buttons ───────────────────────────────────
    st.caption("Or load one of the prepared demo cases:")
    sample_cols = st.columns(len(SAMPLES))
    for col, sample in zip(sample_cols, SAMPLES):
        with col:
            if st.button(
                sample["label"],
                use_container_width=True,
                help=sample["help"],
                key=f"sample_{sample['filename']}",
            ):
                with open(SAMPLES_DIR / sample["filename"], "rb") as f:
                    st.session_state.sample_image_bytes = f.read()
                st.session_state.sample_filename = sample["filename"]
                st.session_state.address = sample["address"]
                st.session_state.orientation = sample["orientation"]
                st.session_state.month = sample["month"]
                st.session_state.photo_hour = sample.get("photo_hour", "Not specified")
                st.session_state.uploader_key += 1  # clear any manual upload
                st.rerun()

    st.divider()

    # ── Step 2: Property details ──────────────────────────────
    st.subheader("2. Property details")

    address = st.text_input(
        "Street address",
        placeholder="e.g. Bygdøy allé 30, Oslo",
        key="address",
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        orientation = st.selectbox(
            "Facade / balcony orientation",
            options=["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
            index=4,
            help="The compass direction the facade or balcony faces.",
            key="orientation",
        )
    with col2:
        month = st.selectbox(
            "Month to check",
            options=MONTHS,
            index=datetime.now().month - 1,
            help="Defaults to the current month.",
            key="month",
        )
    with col3:
        _hour_options = ["Not specified"] + [f"{h:02d}:00" for h in range(24)]
        photo_hour = st.selectbox(
            "Hour photo was taken",
            options=_hour_options,
            help="24-hour clock. Leave as 'Not specified' if unknown.",
            key="photo_hour",
        )

    st.divider()

    if st.button("Check sun conditions", type="primary", use_container_width=True):
        if active_image is None:
            st.warning("Please upload a listing photo or load a demo sample before checking.")
        elif not address.strip():
            st.warning("Please enter a street address.")
        else:
            photo_time = "" if photo_hour == "Not specified" else photo_hour
            with st.spinner("Analysing photo and calculating solar position..."):
                result = call_backend(
                    image_bytes=active_image,
                    address=address.strip(),
                    orientation=orientation,
                    month=month,
                    photo_time=photo_time,
                )
            st.session_state.result = result
            st.session_state.submitted_image = active_image
            st.session_state.submitted_inputs = {
                "address": address.strip(),
                "orientation": orientation,
                "month": month,
                "photo_time": photo_time,
                "filename": active_filename,
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
    _time_part = f"  ·  {inputs['photo_time']}" if inputs.get("photo_time") else ""
    st.caption(
        f"{inputs.get('address', '')}  ·  "
        f"Orientation: {inputs.get('orientation', '')}  ·  "
        f"{inputs.get('month', '')}{_time_part}"
    )
    st.divider()

    # ── Verdict badge ─────────────────────────────────────────
    style, label = _VERDICT_STYLE.get(verdict, ("info", f"ℹ️  {verdict}"))
    getattr(st, style)(f"**Verdict: {label}**")

    # Tell the viewer whether this came from the live backend or the
    # frontend-only fallback — important during the integration call so
    # nobody mistakes a mock verdict for a real one.
    if result.get("_source") == "mock":
        st.warning(
            "⚠️  Backend not reachable — showing a frontend-only mock verdict. "
            "Start FastAPI (`uvicorn main:app --reload`) for a real analysis.",
            icon=None,
        )

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

    # ── PDF download (calls Gabriel's /report endpoint) ───────
    # The endpoint currently returns a placeholder PDF until report.py is
    # wired into main.py — once that lands, the same call yields a real
    # compliance note with no frontend changes needed.
    st.subheader("Compliance note")
    pdf_bytes = call_report_backend(result, inputs, st.session_state.submitted_image)
    st.download_button(
        label="Download PDF report",
        data=pdf_bytes or b"",
        file_name="sunproof_report.pdf",
        mime="application/pdf",
        disabled=pdf_bytes is None,
        use_container_width=True,
        help=(
            "Generated by the backend /report endpoint."
            if pdf_bytes
            else "Backend not reachable — start FastAPI (uvicorn main:app --reload) to enable."
        ),
    )
    st.caption(
        "This tool provides a compliance-support note, not legal advice."
    )

    st.divider()

    # ── Back button ───────────────────────────────────────────
    if st.button("← Check another photo", use_container_width=True):
        st.session_state.page = "input"
        st.session_state.result = None
        st.session_state.submitted_image = None
        st.session_state.submitted_inputs = None
        st.session_state.sample_image_bytes = None
        st.session_state.sample_filename = None
        st.session_state.uploader_key += 1
        st.rerun()


# ══════════════════════════════════════════════════════════════
# ROUTER
# ══════════════════════════════════════════════════════════════
if st.session_state.page == "input":
    show_input_page()
else:
    show_results_page()
