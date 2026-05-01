import streamlit as st
from datetime import datetime

# ── Page config ───────────────────────────────────────────────
st.set_page_config(
    page_title="SunProof AI",
    page_icon="☀️",
    layout="centered"
)

# ── Session state defaults ────────────────────────────────────
# These variables persist across reruns (e.g. after clicking a button)
if "page" not in st.session_state:
    st.session_state.page = "input"          # "input" or "results"
if "result" not in st.session_state:
    st.session_state.result = None
if "submitted_image" not in st.session_state:
    st.session_state.submitted_image = None

# ══════════════════════════════════════════════════════════════
# MOCK FUNCTION — replace with real API call in Week 2
# ══════════════════════════════════════════════════════════════
def mock_analyze(image_bytes, address, orientation, month):
    """
    Hardcoded dummy response simulating the FastAPI /analyze endpoint.
    Returns the same dict shape the real backend will return.
    Rotate through the 3 verdicts to test all result states.
    """
    return {
        "verdict": "Possibly misleading",
        "explanation": (
            f"The image suggests direct south-westerly sunlight on the facade, "
            f"but solar calculations show that a {orientation}-facing facade at "
            f"{address} cannot receive direct sunlight from that direction in {month}."
        ),
        "solar_summary": (
            f"For a {orientation}-facing facade in {month}, the sun is only visible "
            f"between approximately 08:00 and 11:00. "
            f"The lighting shown in the image implies afternoon sun, which is not "
            f"geometrically possible for this orientation."
        )
    }

# ══════════════════════════════════════════════════════════════
# PAGE 1 — INPUT FORM
# ══════════════════════════════════════════════════════════════
def show_input_page():
    st.title("☀️ SunProof AI")
    st.caption("Verify whether AI-edited listing photos show physically plausible sun conditions.")
    st.divider()

    st.subheader("1. Upload a listing photo")
    uploaded_file = st.file_uploader(
        "Choose an image file",
        type=["jpg", "jpeg", "png"],
        help="Upload the AI-edited listing photo you want to verify."
    )
    if uploaded_file:
        st.image(uploaded_file, caption="Uploaded photo", use_container_width=True)

    st.subheader("2. Property details")
    address = st.text_input(
        "Street address",
        placeholder="e.g. Bygdøy allé 30, Oslo",
    )

    orientation = st.selectbox(
        "Facade / balcony orientation",
        options=["N", "NE", "E", "SE", "S", "SW", "W", "NW"],
        index=4,
    )

    MONTHS = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December"
    ]
    current_month_index = datetime.now().month - 1
    month = st.selectbox(
        "Month to check (optional)",
        options=MONTHS,
        index=current_month_index,
    )

    st.divider()
    submit = st.button("🔍 Check sun conditions", type="primary", use_container_width=True)

    if submit:
        if not uploaded_file:
            st.warning("Please upload a listing photo before checking.")
        elif not address.strip():
            st.warning("Please enter a street address.")
        else:
            # Call the mock function (swap this for real API call in Week 2)
            result = mock_analyze(
                image_bytes=uploaded_file.getvalue(),
                address=address,
                orientation=orientation,
                month=month
            )
            # Save everything to session state and switch page
            st.session_state.result = result
            st.session_state.submitted_image = uploaded_file.getvalue()
            st.session_state.page = "results"
            st.rerun()  # triggers a re-render on the results page

# ══════════════════════════════════════════════════════════════
# PAGE 2 — RESULTS PAGE
# ══════════════════════════════════════════════════════════════
def show_results_page():
    result = st.session_state.result
    verdict = result["verdict"]

    # ── Verdict badge ─────────────────────────────────────────
    st.title("☀️ SunProof AI — Result")
    st.divider()

    if verdict == "Consistent":
        st.success(f"✅  Verdict: {verdict}")
    elif verdict == "Possibly misleading":
        st.error(f"🚨  Verdict: {verdict}")
    elif verdict == "Inconclusive":
        st.warning(f"⚠️  Verdict: {verdict}")
    else:
        st.info(f"ℹ️  Verdict: {verdict}")

    # ── Explanation ───────────────────────────────────────────
    st.subheader("Explanation")
    st.write(result["explanation"])

    # ── Solar summary ─────────────────────────────────────────
    st.subheader("Solar calculation summary")
    st.info(result["solar_summary"])

    # ── Uploaded photo ────────────────────────────────────────
    if st.session_state.submitted_image:
        st.subheader("Analysed photo")
        st.image(st.session_state.submitted_image, use_container_width=True)

    st.divider()

    # ── PDF download placeholder ──────────────────────────────
    st.subheader("Compliance note")
    st.button(
        "📄 Download PDF report",
        disabled=True,
        help="PDF generation is handled by Person 5 — coming in Week 2.",
        use_container_width=True
    )
    st.caption("⏳ PDF download will be enabled once Person 5's module is ready.")

    # ── Back button ───────────────────────────────────────────
    st.divider()
    if st.button("← Check another photo", use_container_width=True):
        st.session_state.page = "input"
        st.session_state.result = None
        st.session_state.submitted_image = None
        st.rerun()

# ══════════════════════════════════════════════════════════════
# ROUTER — decides which page to show
# ══════════════════════════════════════════════════════════════
if st.session_state.page == "input":
    show_input_page()
else:
    show_results_page()