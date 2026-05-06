"""
image_analysis.py  —  VLM Image Analysis Module
Role: Vision / AI Lead (Ole)

Analyzes real-estate listing photos for sun and lighting conditions.
Feeds a structured JSON dict straight into Gabriel's reconciliation engine.

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
# Optional[bool] fields use three states:
#   True  → clearly yes
#   False → clearly no
#   None  → cannot determine from this image (Gabriel handles each case)

class SunAnalysis(BaseModel):
    sun_elevation: Literal["low", "medium", "high", "unclear"]
    lighting: Literal["direct", "diffuse", "overcast"]
    shadows_visible: Optional[bool]      # None = cannot assess
    sun_on_facade: Optional[bool]        # None = facade not visible (e.g. interior shot)
    sun_visible_in_frame: Optional[bool] # None = sun position not determinable
    scene_type: Literal["exterior_facade", "interior_window", "unclear"]


# ── System prompt ──────────────────────────────────────────────────────────────
# Kept as a module-level constant so Gabriel can inspect the exact prompt
# the model receives when he integrates this module.

_SYSTEM_PROMPT = """\
You are a solar-geometry and architectural photography expert. \
Your sole task is to analyze real-estate listing photos and output a \
structured assessment of the sun and lighting conditions visible in the image.

Field definitions:

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

sun_visible_in_frame
  true  — the sun disk itself is visible somewhere in the image (through a window, \
          above the roofline, reflected, or directly in frame).
  false — the sun is not visible; light is inferred from shadows or surface tones only.
  null  — the image is an interior shot with no window, or so tightly cropped that \
          the presence of the sun cannot be determined.

scene_type
  "exterior_facade"  — the photo is taken from outside and shows the building \
                       facade, balcony, or street-level view.
  "interior_window"  — the photo is taken from inside a room looking outward \
                       through a window; the facade itself is not visible.
  "unclear"          — the scene context cannot be determined (e.g. heavily \
                       cropped, ambiguous angle).

Be conservative: return "unclear" or null rather than guessing when evidence is weak.\
"""

_USER_PROMPT = (
    "Analyze the sun and lighting conditions in this real-estate listing photo. "
    "Return all six structured fields as defined. "
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
            "sun_elevation": "medium",
            "lighting": "direct",
            "shadows_visible": True,
            "sun_on_facade": True,
            "sun_visible_in_frame": False,
            "scene_type": "exterior_facade",
        }, indent=2))
        sys.exit(0)

    image_path = sys.argv[1]
    print(f"Analysing: {image_path}")

    with open(image_path, "rb") as f:
        result = analyze_image(f.read())

    print(json.dumps(result, indent=2))
