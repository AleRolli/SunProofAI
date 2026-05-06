"""
report.py — Joint task: PDF compliance-support note generator.

Called by Gabriel's POST /report endpoint in main.py:

    from report import build_report
    pdf_bytes = build_report(request_data.model_dump())
    return Response(content=pdf_bytes, media_type="application/pdf")

Input:  dict with keys  address, orientation, month, verdict,
                        explanation, solar_summary.
Output: raw PDF bytes — one A4 page, no external font files required.
"""

from datetime import datetime
from fpdf import FPDF


def _safe(text: str) -> str:
    """Normalize text to Latin-1 for fpdf2's built-in Helvetica font.

    Replaces common typographic Unicode (en/em dashes, smart quotes, ellipsis)
    with plain ASCII equivalents, then drops any remaining non-Latin-1 characters
    rather than crashing. Ferdinand's solar summaries use en dashes in time ranges
    (e.g. "09:00–17:00") which triggered this.
    """
    for src, dst in (
        ("–", "-"),    # en dash  –
        ("—", "-"),    # em dash  —
        ("‘", "'"),    # left single quote  '
        ("’", "'"),    # right single quote '
        ("“", '"'),    # left double quote  "
        ("”", '"'),    # right double quote "
        ("…", "..."),  # ellipsis  …
    ):
        text = text.replace(src, dst)
    return text.encode("latin-1", errors="replace").decode("latin-1")


# ── Brand palette (mirrors .streamlit/config.toml) ────────────────────────────
_ORANGE   = (233, 127,  34)   # primaryColor  #E97F22
_OFFWHITE = (250, 247, 242)   # secondaryBackground #FAF7F2
_DARK     = ( 31,  41,  55)   # textColor     #1F2937
_WHITE    = (255, 255, 255)
_GREY     = (130, 130, 130)

_VERDICT_BG = {
    "Consistent":          ( 40, 167,  69),   # green  #28A745
    "Possibly misleading": (220,  53,  69),   # red    #DC3545
    "Inconclusive":        (233, 127,  34),   # orange #E97F22
}
_VERDICT_LABEL = {
    "Consistent":          "CONSISTENT",
    "Possibly misleading": "POSSIBLY MISLEADING",
    "Inconclusive":        "INCONCLUSIVE",
}


def build_report(data: dict) -> bytes:
    """
    Build a one-page A4 PDF compliance-support note.

    Args:
        data: dict matching Gabriel's ReportRequest schema —
              address, orientation, month, verdict, explanation, solar_summary.

    Returns:
        Raw PDF bytes.
    """
    address       = _safe(data.get("address",       "-"))
    orientation   = _safe(data.get("orientation",   "-"))
    month         = _safe(data.get("month",         "-"))
    verdict       =       data.get("verdict",       "Inconclusive")
    explanation   = _safe(data.get("explanation",   ""))
    solar_summary = _safe(data.get("solar_summary", ""))

    dt        = datetime.now()
    generated = f"{dt.day} {dt.strftime('%B %Y')}"   # e.g. "6 May 2026" — cross-platform

    badge_bg    = _VERDICT_BG.get(verdict, _ORANGE)
    badge_label = _VERDICT_LABEL.get(verdict, verdict.upper())

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=False)
    pdf.add_page()

    PW = pdf.w          # 210 mm
    PH = pdf.h          # 297 mm
    M  = 15             # side margin
    CW = PW - 2 * M    # content width — 180 mm

    # ── 1. Header bar ──────────────────────────────────────────────────────────
    pdf.set_fill_color(*_ORANGE)
    pdf.rect(0, 0, PW, 28, style="F")

    pdf.set_xy(M, 7)
    pdf.set_text_color(*_WHITE)
    pdf.set_font("Helvetica", "B", 22)
    pdf.cell(0, 9, "SunProof AI", new_x="LMARGIN", new_y="NEXT")

    pdf.set_x(M)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 5, "Sun Condition Verification Note", new_x="LMARGIN", new_y="NEXT")

    # ── 2. Property details ────────────────────────────────────────────────────
    pdf.set_fill_color(*_OFFWHITE)
    pdf.rect(0, 28, PW, 34, style="F")

    pdf.set_xy(M, 32)
    pdf.set_text_color(*_ORANGE)
    pdf.set_font("Helvetica", "B", 7)
    pdf.cell(0, 4, "PROPERTY DETAILS", new_x="LMARGIN", new_y="NEXT")

    label_w = 28

    def _row(label: str, value: str) -> None:
        pdf.set_x(M)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(label_w, 5.5, label)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(*_DARK)
        pdf.cell(0, 5.5, value, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(*_DARK)

    _row("Address",  address)
    _row("Facade",   orientation)
    _row("Month",    month)
    _row("Analysed", generated)

    # ── 3. Verdict badge ───────────────────────────────────────────────────────
    badge_y = 68
    badge_h = 17

    pdf.set_fill_color(*badge_bg)
    pdf.rect(M, badge_y, CW, badge_h, style="F")

    pdf.set_xy(M, badge_y + 3.5)
    pdf.set_text_color(*_WHITE)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(CW, 10, f"VERDICT:  {badge_label}", align="C")

    # ── helper: orange section title + rule ────────────────────────────────────
    def _section(title: str, y: float) -> float:
        """Draw a section header with an orange rule. Returns the y to write body text."""
        pdf.set_xy(M, y)
        pdf.set_text_color(*_ORANGE)
        pdf.set_font("Helvetica", "B", 8)
        pdf.cell(0, 4, title, new_x="LMARGIN", new_y="NEXT")
        ry = pdf.get_y()
        pdf.set_draw_color(*_ORANGE)
        pdf.set_line_width(0.4)
        pdf.line(M, ry, M + CW, ry)
        pdf.ln(3)
        return pdf.get_y()

    # ── 4. Explanation ─────────────────────────────────────────────────────────
    y = _section("EXPLANATION", badge_y + badge_h + 9)

    pdf.set_xy(M, y)
    pdf.set_text_color(*_DARK)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(CW, 5.5, explanation)
    y = pdf.get_y()

    # ── 5. Solar calculation summary ───────────────────────────────────────────
    y = _section("SOLAR CALCULATION SUMMARY", y + 8)

    # Tinted box sized to the content
    approx_lines = max(2, len(solar_summary) // 52 + 2)
    box_h = approx_lines * 5.5 + 5
    pdf.set_fill_color(*_OFFWHITE)
    pdf.rect(M, y - 1, CW, box_h, style="F")

    pdf.set_xy(M + 3, y + 2)
    pdf.set_text_color(*_DARK)
    pdf.set_font("Helvetica", "", 10)
    pdf.multi_cell(CW - 6, 5.5, solar_summary)
    y = pdf.get_y() + 4

    # ── 6. Disclaimer ──────────────────────────────────────────────────────────
    disc_y = max(y + 8, 238)

    pdf.set_xy(M, disc_y)
    pdf.set_text_color(*_GREY)
    pdf.set_font("Helvetica", "B", 7)
    pdf.cell(0, 4, "DISCLAIMER", new_x="LMARGIN", new_y="NEXT")

    pdf.set_x(M)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(
        CW, 4.5,
        "This document is a compliance-support note, not legal advice. "
        "The verdict is generated by an AI model analysing a single photograph "
        "and deterministic solar geometry calculations. Results depend on the "
        "accuracy of the provided address, facade orientation, and image quality. "
        "SunProof AI does not guarantee correctness and accepts no liability.",
    )

    # ── 7. Footer bar ──────────────────────────────────────────────────────────
    footer_y = PH - 12
    pdf.set_fill_color(*_ORANGE)
    pdf.rect(0, footer_y, PW, 12, style="F")

    pdf.set_xy(M, footer_y + 3.5)
    pdf.set_text_color(*_WHITE)
    pdf.set_font("Helvetica", "", 8)
    pdf.cell(CW / 2, 5, "SunProof AI  |  sunproofai.streamlit.app")
    pdf.cell(CW / 2, 5, f"Generated  {generated}", align="R")

    return bytes(pdf.output())
