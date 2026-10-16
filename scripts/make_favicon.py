"""
Generate frontend/favicon.png: the blackletter "A" from the masthead font
(UnifrakturCook) on the paper-beige background, matching the site.

Usage:
    py -3.12 scripts/make_favicon.py
"""

import io
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_URL = "https://github.com/google/fonts/raw/main/ofl/unifrakturcook/UnifrakturCook-Bold.ttf"
OUT = Path(__file__).parent.parent / "frontend" / "favicon.png"

PAPER = (244, 241, 232)   # --paper-2
INK = (27, 25, 19)        # --ink
SIZE = 256


def main():
    req = urllib.request.Request(FONT_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        font_bytes = resp.read()

    img = Image.new("RGB", (SIZE, SIZE), PAPER)
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(io.BytesIO(font_bytes), 230)

    # Centre the glyph using its actual bounding box.
    box = draw.textbbox((0, 0), "A", font=font)
    w, h = box[2] - box[0], box[3] - box[1]
    x = (SIZE - w) / 2 - box[0]
    y = (SIZE - h) / 2 - box[1]
    draw.text((x, y), "A", font=font, fill=INK)

    OUT.write_bytes(_to_png(img))
    print(f"Wrote {OUT}")


def _to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


if __name__ == "__main__":
    main()
