"""Build the extension icon: docs/media/icon.svg (the master, lettering as outlines) and client/media/icon.png.

The design is a chip: a dark die marked "elegant" (Playfair Display Bold Italic) and "UVM" (IBM Plex
Mono Bold), a pin-1 dot and blue pins, on a white tile. Both typefaces are under the SIL Open Font
License 1.1; only their glyph outlines end up in the icon, no font files are shipped.

Usage, with fonttools and cairosvg installed (pip install fonttools cairosvg):
    python tools/make_icon.py <IBMPlexMono-Bold.ttf> <PlayfairDisplay-BoldItalic.ttf>
Both fonts are on Google Fonts (fonts.google.com/specimen/IBM+Plex+Mono, .../Playfair+Display).
"""
import sys
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.boundsPen import BoundsPen


def text_path(font_file, text, size, cx, baseline, target_width=None):
    """Outline `text` centred on cx, with its baseline at `baseline`; stretch it to target_width if given."""
    font = TTFont(font_file)
    gs, cmap, hmtx = font.getGlyphSet(), font.getBestCmap(), font["hmtx"]
    upm = font["head"].unitsPerEm
    names = [cmap[ord(ch)] for ch in text]
    advance = sum(hmtx[n][0] for n in names)
    sx = sy = size / upm
    if target_width:
        # stretch like textLength/lengthAdjust="spacingAndGlyphs": scale x so the ink box spans target_width
        bp = BoundsPen(gs)
        x = 0
        for n in names:
            gs[n].draw(TransformPen(bp, (1, 0, 0, 1, x, 0)))
            x += hmtx[n][0]
        xmin, _, xmax, _ = bp.bounds
        sx = target_width / (xmax - xmin)
        left = cx - target_width / 2 - xmin * sx
    else:
        left = cx - advance * sx / 2
    pen = SVGPathPen(gs)
    x = 0
    for n in names:
        gs[n].draw(TransformPen(pen, (sx, 0, 0, -sy, left + x * sx, baseline)))
        x += hmtx[n][0]
    return pen.getCommands()


def build(mono, serif):
    d0, d1, plen, pw = 48, 208, 20, 16
    span = d1 - d0
    k = span / 144
    mid, step = (d0 + d1) / 2, span / 4.5
    pins = []
    for c in (mid - step, mid, mid + step):
        x = c - pw / 2
        pins += [(x, d0 - plen, pw, plen + 2), (x, d1 - 2, pw, plen + 2), (d0 - plen, x, plen + 2, pw), (d1 - 2, x, plen + 2, pw)]
    pin_svg = "".join(f'<rect x="{x:.2f}" y="{y:.2f}" width="{w}" height="{h}" rx="2.5" fill="#2f5bd3"/>' for x, y, w, h in pins)
    elegant = text_path(serif, "elegant", 24 * k, 128, d0 + 46 * k + 3)
    uvm = text_path(mono, "UVM", 62 * k, 128, d0 + 110 * k + 3, target_width=122 * k)
    dot = d0 + 22 * k
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" width="256" height="256">
<title>ElegantUVM</title>
<defs><linearGradient id="die" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#1d2230"/><stop offset="1" stop-color="#11141c"/></linearGradient></defs>
<rect width="256" height="256" rx="56" fill="#ffffff"/>
<rect x="3" y="3" width="250" height="250" rx="53" fill="none" stroke="#d8dce6" stroke-width="6"/>
{pin_svg}
<rect x="{d0}" y="{d0}" width="{span}" height="{span}" rx="6" fill="url(#die)"/>
<circle cx="{dot:.2f}" cy="{dot:.2f}" r="{5.5 * k:.2f}" fill="#ffffff"/>
<path fill="#9fb4ff" d="{elegant}"/>
<path fill="#ffffff" d="{uvm}"/>
</svg>
"""


if __name__ == "__main__":
    from pathlib import Path
    import cairosvg

    root = Path(__file__).resolve().parent.parent
    svg = build(*sys.argv[1:3])
    (root / "docs/media/icon.svg").write_text(svg)
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(root / "client/media/icon.png"), output_width=256, output_height=256)
    print("wrote docs/media/icon.svg and client/media/icon.png")
