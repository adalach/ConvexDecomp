#!/usr/bin/env python3
"""The pipeline diagram of the corner-ray perimeter's README: every step in the order it runs, in four rows (rays,
lines, zones, final rounds), with the final rounds' loop and the wing-end and bay checks' retry::

    python tools/corner_perimeter/pipeline_diagram.py [--out src/convexdecomp/corner_perimeter/figures/perimeter_pipeline.svg]

Writes one SVG with live text (TUM figure style: TUM blue boxes with light-blue panels, port-and-chevron
connectors). Text is measured with Helvetica Neue where the system has it, else estimated.
"""

from __future__ import annotations

import argparse
import re
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

BLUE, LIGHT, PALE, NOTE, GREY = "#0065BD", "#98C6EA", "#EAF4FB", "#64A0C8", "#9C9E9F"
FONT = "'TUM Neue Helvetica', 'Helvetica Neue', HelveticaNeue, Helvetica, Arial, sans-serif"
PT = 0.3528  # mm per point
HELVETICA = Path("/System/Library/Fonts/HelveticaNeue.ttc")

#: page and grid, in mm
W, MARGIN, PAD, GAP, ROW_GAP = 250.0, 9.0, 4.0, 5.0, 11.0
COLS = 6
BW = (W - 2 * MARGIN - 2 * PAD - (COLS - 1) * GAP) / COLS
TITLE_H, RADIUS = 6.5, 2.4
TITLE_PT, TEXT_PT, PILL_PT, LABEL_PT = 9.5, 8.5, 8.0, 8.0

#: the rows: (label, first column, boxes); a box is (title, description), its column follows from its place
ROWS = (
    ("Rays", 1, (
        ("Corners", "Outline and courtyard corners that turn"),
        ("Ray sources", "Wing ends and bays cast no rays; a short wall casts one"),
        ("Band", "The outline offset 4.5 m inwards"),
        ("Corner rays", "Bisectors from the corners, stopped by the first ray, cut at the band line"),
    )),
    ("Lines · rules 1–4", 0, (
        ("Rule 2", "Close opposite rays become one corner-to-corner line"),
        ("Rule 3", "Rays that reach nothing are discarded"),
        ("Rule 1", "Rays that meet merge"),
        ("Rule 3", "Those from reflex corners are cast again"),
        ("Rule 4", "Core narrower than 1.2 m becomes lines"),
        ("Zones", "Lines and band line split the footprint into zones and core"),
    )),
    ("Zones · rules 5–8", 0, (
        ("Rule 5", "Points closer than 0.5 m become one"),
        ("Rule 6", "Zones narrower than 2 m give way to a diagonal"),
        ("Rule 5 again", "Points closer than 1.2 m, in groups up to 2 m across"),
        ("Rule 7", "Touching thin zones merge"),
        ("Rule 8", "A lone thin zone merges into a neighbour"),
    )),
    ("Final rounds", 0, (
        ("Rule 9", "Corners under 20° are cut off"),
        ("Rules 7, 8", "Once more, for the zones rule 9 thinned"),
        ("Rule 10", "Specks under 0.5 m² join a neighbour"),
        ("Tidy", "Snap, merge, straighten and split zone corners"),
        ("Checks", "Wing ends and bays tried again with their rays"),
    )),
)
INPUT = ("Footprint", "A valid polygon in metres, courtyards allowed")
OUTPUT = "Perimeter zones and core"


def measurer():
    """A function giving a text's width in mm: (text, points, bold, italic) -> mm."""
    try:
        from PIL import ImageFont
        faces = {(b, i): ImageFont.truetype(str(HELVETICA), 100, index=b + 2 * i)
                 for b in (0, 1) for i in (0, 1)}
        return lambda s, pt, bold=False, italic=False: faces[(int(bold), int(italic))].getlength(s) / 100 * pt * PT
    except (ImportError, OSError):
        return lambda s, pt, bold=False, italic=False: len(s) * pt * PT * (0.6 if bold else 0.55)


WIDTH = measurer()


def wrap(text: str, pt: float, width: float) -> list[str]:
    """text in lines no wider than width (mm), a number kept with its unit."""
    lines, line = [], ""
    for word in re.sub(r"(\d) (?=m)", "\\1\u00a0", text).split(" "):
        trial = f"{line} {word}".strip()
        if line and WIDTH(trial, pt) * 1.05 > width:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + [line]


def text(x, y, s, pt, *, anchor="middle", weight="normal", style="normal", fill="#000000", spacing=0.0, halo=False):
    extra = f' letter-spacing="{spacing}"' if spacing else ""
    if halo:
        extra += ' stroke="#ffffff" stroke-width="1.2" paint-order="stroke" stroke-linejoin="round"'
    return (f'<text x="{x:.2f}" y="{y:.2f}" font-size="{pt * PT:.3f}" text-anchor="{anchor}" font-weight="{weight}" '
            f'font-style="{style}" fill="{fill}"{extra}>{escape(s)}</text>')


def box(x, y, h, title, lines, outline=BLUE):
    """A title box with its description panel tucked under it; h is the whole height."""
    lh = TEXT_PT * PT * 1.3
    panel_top = y + TITLE_H - RADIUS
    out = [f'<path d="M{x:.2f},{panel_top:.2f} H{x + BW:.2f} V{y + h - RADIUS:.2f} '
           f'A{RADIUS},{RADIUS} 0 0 1 {x + BW - RADIUS:.2f},{y + h:.2f} H{x + RADIUS:.2f} '
           f'A{RADIUS},{RADIUS} 0 0 1 {x:.2f},{y + h - RADIUS:.2f} Z" fill="{LIGHT}"/>',
           f'<rect x="{x:.2f}" y="{y:.2f}" width="{BW:.2f}" height="{TITLE_H}" rx="{RADIUS}" fill="#ffffff" '
           f'stroke="{outline}" stroke-width="0.45"/>',
           text(x + BW / 2, y + TITLE_H / 2 + TITLE_PT * PT * 0.36, title.upper(), TITLE_PT)]
    top = y + TITLE_H + 1.6 + TEXT_PT * PT * 0.8
    out += [text(x + BW / 2, top + k * lh, s, TEXT_PT) for k, s in enumerate(lines)]
    return out


def connector(points, dashed=False):
    """An orthogonal connector through points: a port at the first, a chevron at the last."""
    d = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in points)
    dash = ' stroke-dasharray="1.5 1.1"' if dashed else ""
    (x0, y0) = points[0]
    return [f'<path d="{d}" fill="none" stroke="{BLUE}" stroke-width="0.35" stroke-linejoin="round"{dash} '
            f'marker-end="url(#chev)"/>',
            f'<circle cx="{x0:.2f}" cy="{y0:.2f}" r="0.8" fill="#ffffff" stroke="{BLUE}" stroke-width="0.3"/>']


def pill(x, y, label):
    """A section tab centred on the height y, starting at x."""
    s = label.upper()
    w = WIDTH(s, PILL_PT, bold=True) * 1.05 + len(s) * 0.12 + 5.0
    h = 4.6
    return [f'<rect x="{x:.2f}" y="{y - h / 2:.2f}" width="{w:.2f}" height="{h}" rx="{h / 2}" fill="{BLUE}"/>',
            text(x + w / 2, y + PILL_PT * PT * 0.36, s, PILL_PT, weight="bold", fill="#ffffff", spacing=0.12)]


def col_x(c: int) -> float:
    return MARGIN + PAD + c * (BW + GAP)


def diagram() -> str:
    inner = BW - 3.0
    rows = []
    for label, first, boxes in ROWS:
        wrapped = [(title, wrap(desc, TEXT_PT, inner)) for title, desc in boxes]
        rows.append((label, first, wrapped))
    n_lines = max(len(lines) for _, _, boxes in rows for _, lines in boxes)
    n_lines = max(n_lines, len(wrap(INPUT[1], TEXT_PT, inner)))
    bh = TITLE_H + 1.6 + n_lines * TEXT_PT * PT * 1.3 + 1.2
    loop_drop = 4.0  # the final rounds' loop and the retry run this far under the boxes
    body, y = [], 9.0
    tops, row_y = [], []
    for k, (label, first, boxes) in enumerate(rows):
        top = y
        by = top + 5.0
        bottom = by + bh + (loop_drop + 5.5 if k == len(rows) - 1 else PAD)
        left = col_x(first) - PAD
        body.append(f'<rect x="{left:.2f}" y="{top:.2f}" width="{W - MARGIN - left:.2f}" height="{bottom - top:.2f}" '
                    f'rx="5" fill="none" stroke="{LIGHT}" stroke-width="0.4"/>')
        tops.append(top)
        row_y.append(by)
        y = bottom + ROW_GAP
    height = y - ROW_GAP + 6.0
    mid = TITLE_H / 2

    # the boxes and the connectors within each row
    for k, (label, first, boxes) in enumerate(rows):
        by = row_y[k]
        for c, (title, lines) in enumerate(boxes, start=first):
            body += box(col_x(c), by, bh, title, lines)
            if c > first:
                body += connector([(col_x(c - 1) + BW, by + mid), (col_x(c), by + mid)])
    # the input, the output and their connectors
    body += box(col_x(0), row_y[0], bh, INPUT[0], wrap(INPUT[1], TEXT_PT, inner), outline=GREY)
    body += connector([(col_x(0) + BW, row_y[0] + mid), (col_x(1), row_y[0] + mid)])
    last = rows[-1]
    out_c = last[1] + len(last[2])
    ox, oy = col_x(out_c), row_y[-1]
    body.append(f'<rect x="{ox:.2f}" y="{oy:.2f}" width="{BW:.2f}" height="{bh:.2f}" rx="{RADIUS}" fill="{BLUE}"/>')
    words = wrap(OUTPUT.upper(), TITLE_PT, inner)
    for i, s in enumerate(words):
        body.append(text(ox + BW / 2, oy + bh / 2 + (i - (len(words) - 1) / 2) * TITLE_PT * PT * 1.25
                         + TITLE_PT * PT * 0.36, s, TITLE_PT, weight="bold", fill="#ffffff", spacing=0.12))
    body += connector([(ox - GAP, oy + mid), (ox, oy + mid)])

    # from the end of each row to the start of the next: down into the gap, left past the containers, into the box
    for k in range(len(rows) - 1):
        _, first, boxes = rows[k]
        end_x = col_x(first + len(boxes) - 1) + BW / 2
        gap_y = tops[k + 1] - ROW_GAP / 2
        nx = col_x(rows[k + 1][1])
        side = MARGIN - 4.5
        body += connector([(end_x, row_y[k] + bh), (end_x, gap_y), (side, gap_y), (side, row_y[k + 1] + mid),
                           (nx, row_y[k + 1] + mid)])

    # the final rounds' loop: from the tidy step back to rule 9, under the boxes
    first = last[1]
    loop_y = row_y[-1] + bh + loop_drop
    tidy_x, sharp_x = col_x(first + 3) + BW / 2, col_x(first) + BW / 2
    body += connector([(tidy_x, row_y[-1] + bh), (tidy_x, loop_y), (sharp_x, loop_y), (sharp_x, row_y[-1] + bh)])
    body.append(text((tidy_x + sharp_x) / 2, loop_y + LABEL_PT * PT * 1.25, "until nothing changes, at most 8 rounds",
                     LABEL_PT, style="italic", fill=BLUE, halo=True))

    # the checks' retry: from the checks back to the ray sources, round the right of the rows
    check_x = col_x(first + 4) + BW / 2
    right = W - MARGIN + 4.5
    over = tops[0] - 4.5
    src_x = col_x(ROWS[0][1] + 1) + BW / 2
    body += connector([(check_x, row_y[-1] + bh), (check_x, loop_y), (right, loop_y), (right, over), (src_x, over),
                       (src_x, row_y[0])], dashed=True)
    body.append(text((src_x + right) / 2, over - 1.2, "retry a wing end or bay with its rays; keep the try if it "
                     "leaves fewer thin, non-convex or sharp zones and specks", LABEL_PT, style="italic", fill=BLUE,
                     halo=True))

    marker = (f'<marker id="chev" markerUnits="userSpaceOnUse" markerWidth="2.8" markerHeight="2.8" '
              f'viewBox="0 0 10 10" refX="9.2" refY="5" orient="auto"><path d="M1.2,1 L9.2,5 L1.2,9" fill="none" '
              f'stroke="{BLUE}" stroke-width="1.4" stroke-linejoin="round" stroke-linecap="round"/></marker>')
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" height="{height:.1f}mm" '
            f'viewBox="0 0 {W} {height:.2f}" font-family="{FONT}">')
    tabs = [s for k, (label, first, _) in enumerate(rows) for s in pill(col_x(first), tops[k], label)]
    return "\n".join([head, f"<defs>{marker}</defs>", f'<rect width="{W}" height="{height:.2f}" fill="#ffffff"/>',
                      *body, *tabs, "</svg>"]) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=ROOT / "src" / "convexdecomp" / "corner_perimeter" / "figures" / "perimeter_pipeline.svg")
    a = ap.parse_args()
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(diagram())
    print("wrote", a.out)


if __name__ == "__main__":
    main()
