# SunProof AI

**Verify whether AI-edited real estate listing photos show sun conditions that are physically possible.**

Real estate agents increasingly use AI image editors to add sunshine to property photos, showing balconies bathed in warm afternoon light that the facade could never actually receive given its compass direction and the time of year. SunProof AI cross-references the lighting visible in a photo against deterministic solar geometry to flag misleading listings.

---

## How It Works

A user uploads a listing photo and provides the property address, facade orientation, and the month of interest. The system runs two independent analyses in parallel and then reconciles them:

```
Streamlit Frontend  (app.py)
Upload photo · Address · Orientation · Month · Photo time
        │
        │  POST /analyze
        ▼
── FastAPI Backend (main.py) ──────────────────────────────────

 ┌──────────────────────────┐  ┌──────────────────────────┐
 │  Vision Engine           │  │  Solar Engine            │
 │  image_analysis.py       │  │  solar.py                │
 │                          │  │                          │
 │  Claude claude-opus-4-7  │  │  Nominatim geocoding     │
 │  (adaptive thinking)     │  │  pysolar calculations    │
 │                          │  │                          │
 │  · sun_elevation         │  │  · sun hours / day       │
 │  · lighting type         │  │  · facade_receives_sun   │
 │  · shadows_visible       │  │  · has_golden_hour       │
 │  · sun_on_facade         │  │  · elevation @ time      │
 │  · scene_type            │  │                          │
 └────────────┬─────────────┘  └─────────────┬────────────┘
              └───────────────┬──────────────┘
                              ▼
                    Reconciliation Logic
       CONSISTENT · POSSIBLY MISLEADING · INCONCLUSIVE

───────────────────────────────────────────────────────────────

        │  POST /report
        ▼
 ┌──────────────────────┐
 │  report.py           │
 │  PDF compliance note │
 │  (fpdf2)             │
 └──────────────────────┘
```

### Verdict Logic

The reconciliation engine in `main.py` applies a priority cascade:

| Priority | Scenario | Verdict |
|----------|----------|---------|
| 1 | Interior shot with no direct sun evidence | Inconclusive |
| 2 | Image too ambiguous (overcast, unclear, no shadows) | Inconclusive |
| 3 | Sun visible in photo but this facade gets zero sun this month | Possibly misleading |
| 4 | Photo shows golden-hour light but facade's sun window never reaches those hours | Possibly misleading |
| 5 | Photo time + VLM-observed elevation cross-check with real solar altitude | Possibly misleading or Consistent |
| 6 | All checks pass | Consistent |

---

## Project Structure

```
SunProofAI/
├── app.py              — Streamlit frontend (two-page: input form + results)
├── main.py             — FastAPI backend: /health, /analyze, /report endpoints
├── image_analysis.py   — Claude vision module: extracts sun/lighting signals from photos
├── solar.py            — Solar engine: geocoding + pysolar solar window calculations
├── report.py           — PDF compliance note generator (fpdf2)
├── requirements.txt    — Python dependencies
├── .streamlit/
│   └── config.toml     — Streamlit theme (warm orange brand palette)
└── test_images/        — Sample images for local testing
    └── report_test_*.pdf    — Sample PDF outputs
```

---

## Setup

### Prerequisites

- Python 3.11+
- An Anthropic API key

### Install dependencies

```bash
pip install -r requirements.txt
pip install timezonefinder   # recommended for accurate local-time solar windows
```

### Environment variables

Create a `.env` file in the project root (it is git-ignored):

```
ANTHROPIC_API_KEY=sk-ant-...
```

The Streamlit frontend picks up the backend URL from:
1. `BACKEND_URL` environment variable
2. `st.secrets["BACKEND_URL"]` (for Streamlit Cloud deployment)
3. Default: `http://localhost:8000`

---

## Running Locally

The app requires two processes running at the same time — open two terminal windows.

**Terminal 1 — Backend (FastAPI)**

```bash
uvicorn main:app --reload
```

The API will be available at `http://localhost:8000`. You can check it at `http://localhost:8000/health`.

**Terminal 2 — Frontend (Streamlit)**

```bash
streamlit run app.py
```

The UI will open at `http://localhost:8501`.

> If the backend is not running, the frontend falls back to a rule-based mock that rotates through the three verdicts by orientation. A yellow banner makes this visible.

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET`  | `/health` | Liveness check |
| `POST` | `/analyze` | Main analysis — `multipart/form-data`: `image` (file), `address`, `orientation`, `month`, `photo_time` (optional, `HH:MM`) |
| `POST` | `/report` | Generate PDF — JSON body: `address`, `orientation`, `month`, `verdict`, `explanation`, `solar_summary` |

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | Streamlit |
| Backend | FastAPI + Uvicorn |
| Vision AI | Anthropic Claude (`claude-opus-4-7`) — adaptive thinking + structured output |
| Solar math | pysolar + timezonefinder |
| Geocoding | OpenStreetMap Nominatim (no API key required) |
| PDF generation | fpdf2 |
| Data validation | Pydantic v2 |
| Image processing | Pillow |

---

## Key Design Decisions

**Why two separate engines?** Neither source of truth is sufficient alone. The VLM can read lighting character from pixels but has no knowledge of the building's location. The solar engine knows exactly when the sun hits a given facade but cannot see the photo. The reconciliation layer in `main.py` is where the real verification happens, treating disagreements between the two as evidence of manipulation.

**Why Claude with adaptive thinking?** Judging sun elevation from a single photo is genuinely ambiguous. The model needs to reason about shadow angle, surface warmth, sky gradient, and scene context together. Adaptive thinking improves accuracy on edge cases (reflections, interior shots, overcast light) without incurring the full cost of extended thinking on clear-cut images.

**Why OpenStreetMap Nominatim?** No API key, no cost, works globally. Results are cached in memory per session to avoid re-querying the same address.

**EXIF extraction (frontend-only):** When a photo carries EXIF data, the frontend reads the capture timestamp and GPS coordinates directly from the file using Pillow — no backend call needed. The UI offers one-click buttons to pre-fill the month and hour fields from the photo's metadata.
