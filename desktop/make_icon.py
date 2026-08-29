"""Draw the app icon and compile it to AppIcon.icns.

Pillow and iconutil are both already on the machine. Run this again if the mark changes:

    python3 desktop/make_icon.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
ICONSET = HERE / "AppIcon.iconset"
ICNS = HERE / "AppIcon.icns"

INK = (23, 28, 33, 255)          # the app's dark surface
INK_LIFT = (35, 42, 49, 255)     # a touch of light at the top so it is not a flat slab
PAGE = (243, 244, 242, 255)
PLUM = (177, 58, 125, 255)       # the accent, opened up so it holds at 16px
SHADOW = (0, 0, 0, 60)

S = 1024                          # draw once, large, then downsample


def squircle(draw: ImageDraw.ImageDraw, box, radius, fill) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def build() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # macOS leaves the outer edge clear and insets the art
    pad = int(S * 0.085)
    box = (pad, pad, S - pad, S - pad)
    radius = int(S * 0.225)

    squircle(d, box, radius, INK)
    # lit from above. A smooth ramp, because a hard-edged band reads as a rendering fault
    top = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(top).rounded_rectangle(box, radius=radius, fill=INK_LIFT)
    ramp = Image.new("L", (1, S))
    for y in range(S):
        ramp.putpixel((0, y), max(0, int(110 * (1.0 - y / (S * 0.92)))))
    img.paste(top, (0, 0), ramp.resize((S, S)))

    # the page
    pw, ph = int(S * 0.40), int(S * 0.52)
    px, py = (S - pw) // 2, int(S * 0.20)
    fold = int(S * 0.105)

    d.polygon([(px + 12, py + 14), (px + pw - fold + 12, py + 14),
               (px + pw + 12, py + fold + 14), (px + pw + 12, py + ph + 14),
               (px + 12, py + ph + 14)], fill=SHADOW)
    d.polygon([(px, py), (px + pw - fold, py), (px + pw, py + fold),
               (px + pw, py + ph), (px, py + ph)], fill=PAGE)
    d.polygon([(px + pw - fold, py), (px + pw, py + fold),
               (px + pw - fold, py + fold)], fill=(214, 217, 213, 255))

    # three ruled lines, the shortest last, so it reads as text even when tiny
    lx = px + int(pw * 0.16)
    lw = int(pw * 0.68)
    lh = int(S * 0.026)
    for i, frac in enumerate((1.0, 1.0, 0.55)):
        ly = py + int(ph * (0.30 + i * 0.15))
        d.rounded_rectangle((lx, ly, lx + int(lw * frac), ly + lh),
                            radius=lh // 2, fill=(158, 166, 172, 255))

    # the check: this is the whole point of the app, so it is the loudest thing here
    t = int(S * 0.058)
    d.line([(int(S * 0.335), int(S * 0.700)),
            (int(S * 0.452), int(S * 0.812)),
            (int(S * 0.688), int(S * 0.520))],
           fill=PLUM, width=t, joint="curve")
    for pt in ((0.335, 0.700), (0.688, 0.520)):
        cx, cy = int(S * pt[0]), int(S * pt[1])
        d.ellipse((cx - t // 2, cy - t // 2, cx + t // 2, cy + t // 2), fill=PLUM)
    return img


def main() -> int:
    master = build()
    if ICONSET.exists():
        for f in ICONSET.iterdir():
            f.unlink()
    ICONSET.mkdir(exist_ok=True)

    for size in (16, 32, 128, 256, 512):
        for scale in (1, 2):
            px = size * scale
            name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
            master.resize((px, px), Image.LANCZOS).save(ICONSET / name)

    subprocess.run(["iconutil", "-c", "icns", str(ICONSET), "-o", str(ICNS)], check=True)
    master.resize((512, 512), Image.LANCZOS).save(HERE / "icon-preview.png")
    print(f"wrote {ICNS.name} ({ICNS.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
