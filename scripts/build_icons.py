"""Draw the app icon ("the rack") and write every size the app serves.

    python scripts/build_icons.py        # needs Pillow (pip install Pillow); only run when the icon changes

The drawing is a list of rectangles on a 180x180 grid, so the SVG favicon and the PNGs
(iPhone home screen 180, Android 192/512, browser tab 32) always match. Output goes to
app/static/ and is committed; the app itself doesn't need Pillow.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "app" / "static"

BG, PINK, YELLOW, AQUA = "#140B24", "#FF3EA5", "#FFD23F", "#2EE6D6"
HOLE_ROWS = (34, 46, 82, 94, 106, 118, 130)

# (x, y, width, height, corner radius, colour), drawn in order on a 180x180 grid
SHAPES = [
    (0, 0, 180, 180, 0, BG),
    (50, 22, 14, 122, 3, AQUA),                # rack uprights
    (116, 22, 14, 122, 3, AQUA),
    *[(x, y, 6, 3, 0, BG) for x in (54, 120) for y in HOLE_ROWS],   # server-rack holes
    (47, 66, 20, 6, 0, YELLOW),                # J-hooks under the bar
    (113, 66, 20, 6, 0, YELLOW),
    (44, 23, 92, 8, 2, YELLOW),                # pull-up bar across the top
    (10, 58, 160, 8, 2, PINK),                 # barbell
    (30, 38, 13, 48, 3, PINK),                 # big plates, on the sleeves outside the uprights
    (137, 38, 13, 48, 3, PINK),
    (21, 46, 8, 32, 2, YELLOW),                # small plates outside them
    (151, 46, 8, 32, 2, YELLOW),
    (44, 51, 4, 22, 1, PINK),                  # collars
    (132, 51, 4, 22, 1, PINK),
    (20, 150, 47, 10, 0, PINK),                # Miami stripe (the floor)
    (67, 150, 46, 10, 0, YELLOW),
    (113, 150, 47, 10, 0, AQUA),
]

PNG_SIZES = {"apple-touch-icon.png": 180, "icon-192.png": 192, "icon-512.png": 512, "favicon-32.png": 32}
SUPERSAMPLE = 4


def svg() -> str:
    parts = []
    for x, y, w, h, r, fill in SHAPES:
        rx = f' rx="{r}"' if r else ""
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}"{rx} fill="{fill}"/>')
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 180">' + "".join(parts) + "</svg>\n"


def png(size: int) -> Image.Image:
    big = size * SUPERSAMPLE
    k = big / 180
    img = Image.new("RGB", (big, big), BG)
    draw = ImageDraw.Draw(img)
    for x, y, w, h, r, fill in SHAPES:
        box = (round(x * k), round(y * k), round((x + w) * k) - 1, round((y + h) * k) - 1)
        if r:
            draw.rounded_rectangle(box, radius=round(r * k), fill=fill)
        else:
            draw.rectangle(box, fill=fill)
    lanczos = getattr(Image, "Resampling", Image).LANCZOS   # Pillow 9 and 10+
    return img.resize((size, size), lanczos)


def main() -> None:
    (OUT / "icon.svg").write_text(svg(), encoding="utf-8", newline="\n")
    for name, size in PNG_SIZES.items():
        png(size).save(OUT / name, optimize=True)
    print("wrote", ", ".join(["icon.svg", *PNG_SIZES]), "to", OUT)


if __name__ == "__main__":
    main()
