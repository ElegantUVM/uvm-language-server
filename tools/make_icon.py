"""Draw the extension icon (client/media/icon.png): a small UVM class hierarchy on a dark tile.

Run with the dev venv, which has Pillow:  .venv/bin/python tools/make_icon.py
Deterministic, so re-running produces the same bytes."""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 256
BG = (20, 26, 38)        # the gallery banner colour, #141a26
BASE = (45, 212, 191)    # teal: the base class and inheritance edges
LEAF = (251, 191, 36)    # amber: the leaves (the user's components)
INK = (20, 26, 38)


def rounded_tile(draw: ImageDraw.ImageDraw) -> None:
    draw.rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), radius=52, fill=BG)


def node(draw: ImageDraw.ImageDraw, cx: int, cy: int, w: int, h: int, fill: tuple[int, int, int]) -> None:
    """A UML class box: a header band and a body, as the class graph draws them."""
    x0, y0, x1, y1 = cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2
    draw.rounded_rectangle((x0, y0, x1, y1), radius=10, fill=fill)
    band = y0 + h // 3
    draw.line((x0 + 8, band, x1 - 8, band), fill=INK, width=4)
    for i in range(2):  # two "member" lines in the body
        y = band + 16 + i * 14
        draw.line((x0 + 14, y, x1 - 14 - i * 18, y), fill=INK, width=4)


def inheritance(draw: ImageDraw.ImageDraw, x0: int, y0: int, x1: int, y1: int) -> None:
    """An edge from a subclass (x0, y0) up to its base (x1, y1) with a hollow triangle head."""
    draw.line((x0, y0, x1, y1 + 22), fill=BASE, width=6)
    head = [(x1, y1), (x1 - 14, y1 + 24), (x1 + 14, y1 + 24)]
    draw.polygon(head, outline=BASE, fill=BG, width=5)


def main(out: Path) -> None:
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    rounded_tile(draw)
    base = (SIZE // 2, 72)
    leaves = [(72, 184), (SIZE - 72, 184)]
    for lx, ly in leaves:
        inheritance(draw, lx, ly - 34, base[0] + (-26 if lx < base[0] else 26), base[1] + 34)
    node(draw, *base, 112, 68, BASE)
    for lx, ly in leaves:
        node(draw, lx, ly, 104, 68, LEAF)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="PNG", optimize=True)
    print(f"wrote {out} ({SIZE}x{SIZE})")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "client" / "media" / "icon.png")
