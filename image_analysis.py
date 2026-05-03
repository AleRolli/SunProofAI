"""
image_analysis.py  —  VLM Image Analysis Module
Role: Vision / AI Lead (Ole)

Analyzes real-estate listing photos for sun and lighting conditions.
Feeds a structured JSON dict straight into Gabriel's reconciliation engine.

────────────────────────────────────────────────────────────────────────────────
SETUP GUIDE  —  remove these comments once your key is working
────────────────────────────────────────────────────────────────────────────────

1. CREATE AN ANTHROPIC ACCOUNT AND API KEY
   a. Open https://console.anthropic.com in your browser.
   b. Sign up (or log in if you already have an account).
   c. In the left sidebar click "API Keys", then "Create Key".
   d. Give it a name like "SunProofAI" and click Create.
   e. COPY THE KEY NOW — it starts with "sk-ant-api03-..." and you only see it once.

2. ADD BILLING (the API will refuse requests without a payment method)
   a. In the console go to Settings → Billing.
   b. Click "Add payment method" and enter a credit card.
   c. Top up with €5–10 — more than enough for all your test images.
   d. Cost estimate: ~$0.02–0.05 per image with claude-opus-4-7.

3. SET THE ENVIRONMENT VARIABLE  (NEVER hardcode the key in the file)

   macOS / Linux — run this in your terminal, or add it to ~/.zshrc:
       export ANTHROPIC_API_KEY="sk-ant-api03-..."

   Windows Command Prompt:
       set ANTHROPIC_API_KEY=sk-ant-api03-...

   Windows PowerShell:
       $env:ANTHROPIC_API_KEY = "sk-ant-api03-..."

   Or use a .env file in the project root (easier for the whole team):
       Create a file called ".env" containing:
           ANTHROPIC_API_KEY=sk-ant-api03-...
       Then install python-dotenv:
           pip install python-dotenv
       And add these two lines at the top of main.py / app.py:
           from dotenv import load_dotenv
           load_dotenv()

   ⚠ Add ".env" to .gitignore RIGHT NOW — never commit API keys to GitHub!

4. INSTALL DEPENDENCIES
       pip install anthropic Pillow
   (pydantic is bundled with the anthropic package — no separate install needed)

────────────────────────────────────────────────────────────────────────────────
"""

import base64
from io import BytesIO
from typing import Literal, Optional

from dotenv import load_dotenv
load_dotenv()

import anthropic
from pydantic import BaseModel


# ── Output schema ──────────────────────────────────────────────────────────────
# This is the agreed interface contract between Ole → Gabriel.
# Gabriel expects exactly these five fields.
#
# shadows_visible and sun_on_facade use Optional[bool]:
#   True   → clearly yes
#   False  → clearly no
#   None   → unclear  (Gabriel uses this to trigger the "Inconclusive" path)

class SunAnalysis(BaseModel):
    sun_direction: Literal["N", "NE", "E", "SE", "S", "SW", "W", "NW", "unclear"]
    sun_elevation: Literal["low", "medium", "high", "unclear"]
    lighting: Literal["direct", "diffuse", "overcast"]
    shadows_visible: Optional[bool]   # None = unclear
    sun_on_facade: Optional[bool]     # None = unclear


# ── System prompt ──────────────────────────────────────────────────────────────
# Kept as a module-level constant so Gabriel can inspect the exact prompt
# the model receives when he integrates this module.

_SYSTEM_PROMPT = """\
You are a solar-geometry and architectural photography expert. \
Your sole task is to analyze real-estate listing photos and output a \
structured assessment of the sun and lighting conditions visible in the image.

Field definitions:

sun_direction
  The compass direction FROM WHICH the dominant light is arriving — i.e. where \
  the sun sits in the sky when the photo was taken. Use the 8-point compass: \
  N, NE, E, SE, S, SW, W, NW. Evidence to look for: the direction cast shadows \
  point away from, the position of bright sky or sun glare in the frame, the \
  angle of highlights on vertical surfaces. Return "unclear" for interior shots, \
  overcast scenes, or any image where the light direction cannot be estimated.

sun_elevation
  The apparent height of the sun above the horizon:
  • "low"    — golden or blue hour; shadows are very long, stretching across the \
               ground; sun is near or below roofline level.
  • "medium" — mid-morning or mid-afternoon; shadows have moderate length and \
               point clearly to one side.
  • "high"   — near midday; shadows are short, pointing almost straight down; \
               strong overhead light.
  Return "unclear" if the image gives insufficient evidence.

lighting
  The character of the light:
  • "direct"   — crisp hard-edged shadows, bright sunlit surfaces with clear \
                 contrast between lit and shaded areas.
  • "diffuse"  — soft shadows with visible directionality; thin cloud cover or \
                 haze; surfaces look evenly lit but not flat.
  • "overcast" — no shadows at all; uniform flat grey sky; no directional light.

shadows_visible
  true  — clear cast shadows from objects (railings, window frames, trees) are \
          visible somewhere in the scene.
  false — the scene is uniformly shaded, in shadow, or the sky is fully overcast \
          with no cast shadows.
  null  — interior shot, or the image is so tightly cropped that shadows cannot \
          be assessed.

sun_on_facade
  true  — the photographed facade or balcony surface is directly illuminated by \
          sunlight (warm bright surface tones, clearly lit).
  false — the facade or balcony is in shade or shadow at the time of the photo.
  null  — interior-only shot, extremely ambiguous lighting, or too little of the \
          facade is visible to judge.

Be conservative: return "unclear" or null rather than guessing when evidence is weak.\
"""

_USER_PROMPT = (
    "Analyze the sun and lighting conditions in this real-estate listing photo. "
    "Return the five structured fields as defined. "
    "Use 'unclear' (for string fields) or null (for boolean fields) "
    "whenever you cannot make a confident determination from the visual evidence."
)


# ── Public API ─────────────────────────────────────────────────────────────────

def analyze_image(image_bytes: bytes) -> dict:
    """
    Send a listing photo to Claude vision and return structured lighting analysis.

    Args:
        image_bytes: Raw image bytes (JPEG, PNG, or WebP).

    Returns:
        dict matching the Gabriel interface contract:
            {
                "sun_direction" : "N"|"NE"|"E"|"SE"|"S"|"SW"|"W"|"NW"|"unclear",
                "sun_elevation" : "low"|"medium"|"high"|"unclear",
                "lighting"      : "direct"|"diffuse"|"overcast",
                "shadows_visible": True | False | None,
                "sun_on_facade"  : True | False | None
            }
        None means "unclear" — Gabriel uses this to trigger the Inconclusive path.

    Raises:
        anthropic.AuthenticationError : ANTHROPIC_API_KEY is missing or invalid.
        anthropic.BadRequestError     : image was rejected by the API (corrupt / unsupported format).
        anthropic.RateLimitError      : API quota exceeded — wait a moment and retry.
    """
    client = anthropic.Anthropic()

    resized_bytes = _resize_image(image_bytes)
    image_b64 = base64.standard_b64encode(resized_bytes).decode("utf-8")

    response = client.messages.parse(
        model="claude-opus-4-7",
        max_tokens=4096,             # generous headroom for thinking blocks + JSON output
        thinking={"type": "adaptive"},  # improves accuracy on ambiguous lighting conditions
        system=_SYSTEM_PROMPT,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/jpeg",
                        "data": image_b64,
                    }
                },
                {"type": "text", "text": _USER_PROMPT},
            ]
        }],
        output_format=SunAnalysis,
    )

    return response.parsed_output.model_dump()


# ── Internal helpers ───────────────────────────────────────────────────────────

def _resize_image(image_bytes: bytes, max_long_edge: int = 1568) -> bytes:
    """
    Downscale the image so the longest edge does not exceed max_long_edge pixels,
    then return the result as JPEG bytes. This keeps vision token costs low.
    """
    from PIL import Image

    img = Image.open(BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")

    w, h = img.size
    if max(w, h) > max_long_edge:
        scale = max_long_edge / max(w, h)
        img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


# ── Quick smoke test ───────────────────────────────────────────────────────────
# Run from the project root:
#   python image_analysis.py test_images/sunny_balcony.jpg

if __name__ == "__main__":
    import json
    import sys

    if len(sys.argv) < 2:
        print("Usage: python image_analysis.py <path/to/image.jpg>")
        print("\nExample output:")
        print(json.dumps({
            "sun_direction": "SW",
            "sun_elevation": "medium",
            "lighting": "direct",
            "shadows_visible": True,
            "sun_on_facade": True,
        }, indent=2))
        sys.exit(0)

    image_path = sys.argv[1]
    print(f"Analysing: {image_path}")

    with open(image_path, "rb") as f:
        result = analyze_image(f.read())

    print(json.dumps(result, indent=2))
