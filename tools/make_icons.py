"""Draw the CrapSniffer icon and write it in every format the builds need.

Writes packaging/crapsniffer.ico (Windows), packaging/crapsniffer.icns (macOS) and
packaging/crapsniffer.png (256 px, used by the splash image).
"""

import os

import numpy as np
from PIL import Image, ImageDraw

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "packaging")
S = 1024
A, B = (0x6C, 0x8C, 0xFF), (0x9B, 0x6C, 0xFF)  # same gradient as the in-app logo


def draw(rounded_radius: int, inset: int = 0) -> Image.Image:
    """Gradient tile with a white magnifier and pulse line; `inset` leaves macOS-style margins."""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    size = S - 2 * inset
    yy, xx = np.mgrid[0:size, 0:size]
    t = (xx + yy) / (2 * size - 2)  # top-left -> bottom-right
    rgb = np.stack([A[i] + (B[i] - A[i]) * t for i in range(3)], axis=-1)
    tile = Image.fromarray(np.round(rgb).astype(np.uint8), "RGB")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=rounded_radius, fill=255)
    img.paste(tile, (inset, inset), mask)

    d = ImageDraw.Draw(img)
    k = size / S

    def p(x, y):
        return inset + x * k, inset + y * k

    w = round(64 * k)
    cx, cy, r = 430, 430, 250
    d.ellipse((*p(cx - r, cy - r), *p(cx + r, cy + r)), outline="white", width=w)
    d.line((*p(cx + 177, cy + 177), *p(820, 820)), fill="white", width=w + 6)
    e = (w + 6) / 2
    ex, ey = p(820, 820)
    d.ellipse((ex - e, ey - e, ex + e, ey + e), fill="white")
    pts = [p(x, y) for x, y in [(255, 430), (335, 430), (380, 345), (450, 535), (505, 390), (530, 430), (605, 430)]]
    lw = round(46 * k)
    d.line(pts, fill="white", width=lw, joint="curve")
    for x, y in (pts[0], pts[-1]):
        d.ellipse((x - lw / 2, y - lw / 2, x + lw / 2, y + lw / 2), fill="white")
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    win = draw(rounded_radius=230)
    win.save(os.path.join(OUT, "crapsniffer.ico"),
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    win.resize((256, 256), Image.LANCZOS).save(os.path.join(OUT, "crapsniffer.png"))
    # macOS icons sit inside a transparent margin (824 px tile on a 1024 canvas, ~185 px corner radius)
    mac = draw(rounded_radius=185, inset=100)
    mac.save(os.path.join(OUT, "crapsniffer.icns"))
    print("wrote crapsniffer.ico, crapsniffer.icns, crapsniffer.png to", OUT)


if __name__ == "__main__":
    main()
