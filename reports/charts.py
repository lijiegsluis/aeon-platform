"""TikZ chart primitives for the report.

Hand-rolled rather than pgfplots, because every chart here is a bar, a line or a
scatter over a handful of points, and a dependency that has to be installed on
whatever machine builds the report is a worse trade than sixty lines of
coordinate arithmetic.

Every function returns LaTeX. None of them invent data: a series with a gap gets
a gap, not an interpolation.
"""
from __future__ import annotations
import math

B, NL = chr(92), chr(10)


def _ticks(lo, hi, want=5):
    """Round tick values inside a range. A scale that reads 7.5x / 12.5x / 17.4x
    is a script printing its own min and max; a person sets 5x / 10x / 15x."""
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / max(want - 1, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), 10 * mag)
    t, out = math.ceil(lo / step) * step, []
    while t <= hi + 1e-9:
        out.append(t)
        t += step
    return out or [lo, hi]


def _n(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _fmt(v, dp=0):
    return f"{v:,.{dp}f}"


def frame(body: list[str], w: float, h: float) -> str:
    return (NL.join([f"{B}begin{{center}}",
                     f"{B}begin{{tikzpicture}}[x=1cm,y=1cm]"] + body +
                    [f"{B}end{{tikzpicture}}", f"{B}end{{center}}"]))


def bars(labels, values, *, w=15.0, h=4.4, colour="navy", shades=None,
         dp=0, value_labels=True, zero_line=True, unit=""):
    """Vertical bars. `shades` lets the forecast years read lighter."""
    vals = [v if _n(v) else None for v in values]
    live = [v for v in vals if v is not None]
    if not live:
        return ""
    hi, lo = max(live), min(live)
    top = max(hi, 0) * 1.12 or 1.0
    bot = min(lo, 0) * 1.12
    span = (top - bot) or 1.0
    n = len(vals)
    bw = w / n * 0.58
    y0 = -bot / span * h                    # where zero sits
    out = []
    for i, v in enumerate(vals):
        x = (i + 0.5) * w / n
        if v is None:
            continue
        sh = (shades[i] if shades else 78)
        yv = (v - bot) / span * h
        a, b = (y0, yv) if v >= 0 else (yv, y0)
        fill = "serA" if sh >= 60 else "serB"
        out.append(f"{B}fill[{fill}] ({x - bw/2:.2f},{a:.2f}) "
                   f"rectangle ({x + bw/2:.2f},{b:.2f});")
        if value_labels:
            ly = max(a, b) + 0.06
            out.append(f"{B}node[anchor=south,font={B}scriptsize] at ({x:.2f},{ly:.2f}) "
                       f"{{{_fmt(v, dp)}}};")
        out.append(f"{B}node[anchor=north,font={B}scriptsize,gray] at ({x:.2f},{y0-0.08:.2f}) "
                   f"{{{labels[i]}}};")
    for t in _ticks(bot, top, 6):
        yy = (t - bot) / span * h
        if yy < -0.01 or yy > h + 0.01:
            continue
        out.append(f"{B}draw[rule,line width=0.3pt] (-0.10,{yy:.2f}) -- (0,{yy:.2f});")
        out.append(f"{B}node[anchor=east,font={B}scriptsize,text=gray] at "
                   f"(-0.16,{yy:.2f}) {{{_fmt(t, dp)}}};")
    if zero_line:
        out.append(f"{B}draw[rule] (0,{y0:.2f}) -- ({w:.2f},{y0:.2f});")
    if unit:
        out.append(f"{B}node[anchor=west,font={B}scriptsize,gray] at (0,{h+0.30:.2f}) "
                   f"{{{unit}}};")
    return frame(out, w, h)


def bars_with_line(labels, bar_vals, line_vals, *, w=15.0, h=4.4, dp=0,
                   line_dp=1, shades=None, bar_unit="", line_unit=""):
    """Bars on the left scale, a line on its own right scale."""
    bv = [v if _n(v) else None for v in bar_vals]
    lv = [v if _n(v) else None for v in line_vals]
    blive = [v for v in bv if v is not None]
    llive = [v for v in lv if v is not None]
    if not blive or not llive:
        return ""
    btop = max(blive) * 1.14
    lmin, lmax = min(llive), max(llive)
    pad = (lmax - lmin) * 0.35 or abs(lmax) * 0.2 or 1.0
    lmin, lmax = lmin - pad, lmax + pad
    n = len(bv)
    bw = w / n * 0.58
    out, vlabels = [], []
    for i, v in enumerate(bv):
        if v is None:
            continue
        x = (i + 0.5) * w / n
        y = v / btop * h
        sh = (shades[i] if shades else 78)
        col = "serA" if sh >= 60 else "serB"
        out.append(f"{B}fill[{col}] ({x - bw/2:.2f},0) rectangle ({x + bw/2:.2f},{y:.2f});")
        ly = y
        lval = lv[i] if i < len(lv) else None
        if lval is not None:
            liney = (lval - lmin) / (lmax - lmin) * h
            if abs(liney - y) < 0.42:            # label would sit on the line
                ly = max(y, liney) + 0.30
        vlabels.append(f"{B}node[anchor=south,fill=white,inner sep=1.2pt,"
                       f"font={B}scriptsize] at ({x:.2f},{ly:.2f}) {{{_fmt(v, dp)}}};")
        out.append(f"{B}node[anchor=north,font={B}scriptsize,gray] at ({x:.2f},-0.08) "
                   f"{{{labels[i]}}};")
    pts = []
    for i, v in enumerate(lv):
        if v is None:
            continue
        x = (i + 0.5) * w / n
        y = (v - lmin) / (lmax - lmin) * h
        pts.append((x, y, v))
    if pts:
        out.append(f"{B}draw[sell,line width=1.1pt] "
                   + " -- ".join(f"({x:.2f},{y:.2f})" for x, y, _ in pts) + ";")
        for x, y, v in pts:
            out.append(f"{B}fill[sell] ({x:.2f},{y:.2f}) circle (1.5pt);")
        # No inline end-labels. They sat on top of the bars whenever the line
        # ran low, and a number that collides with another number is worse than
        # no number. The first and last values go in the caption instead.
    out.extend(vlabels)           # after the line, so nothing is struck through
    # Left scale for the bars and a right scale for the line. Without the right
    # axis the series is decoration: a reader cannot read a single margin off it.
    for t in _ticks(0, btop, 6):
        if t <= 0:
            continue
        yy = t / btop * h
        out.append(f"{B}draw[rule,line width=0.3pt] (-0.10,{yy:.2f}) -- (0,{yy:.2f});")
        out.append(f"{B}node[anchor=east,font={B}scriptsize,gray] at "
                   f"(-0.16,{yy:.2f}) {{{_fmt(t, dp)}}};")
    for t in _ticks(lmin, lmax, 6):
        yy = (t - lmin) / (lmax - lmin) * h
        if yy < -0.01 or yy > h + 0.01:
            continue
        out.append(f"{B}draw[sell,line width=0.3pt] ({w:.2f},{yy:.2f}) -- "
                   f"({w + 0.10:.2f},{yy:.2f});")
        out.append(f"{B}node[anchor=west,font={B}scriptsize,text=sell] at "
                   f"({w + 0.16:.2f},{yy:.2f}) {{{_fmt(t, line_dp)}}};")
    out.append(f"{B}draw[rule] (0,0) -- ({w:.2f},0);")
    if bar_unit:
        out.append(f"{B}node[anchor=west,font={B}scriptsize,gray] at (0,{h+0.34:.2f}) "
                   f"{{{bar_unit}}};")
    if line_unit:
        out.append(f"{B}node[anchor=east,font={B}scriptsize,sell] at ({w:.2f},{h+0.34:.2f}) "
                   f"{{{line_unit}}};")
    return frame(out, w, h)


def hbars(labels, values, *, w=13.0, rowh=0.52, dp=1, suffix="\\%"):
    """Horizontal bars, for a mix where the labels are long."""
    live = [v for v in values if _n(v)]
    if not live:
        return ""
    hi = max(live) * 1.02
    out = []
    for i, (lab, v) in enumerate(zip(labels, values)):
        if not _n(v):
            continue
        y = -i * rowh
        bl = v / hi * (w - 4.2)
        out.append(f"{B}fill[serA{'' if i == 0 else '!45'}] (0,{y-0.16:.2f}) "
                   f"rectangle ({bl:.2f},{y+0.16:.2f});")
        out.append(f"{B}node[anchor=east,font={B}scriptsize] at (-0.15,{y:.2f}) {{{lab}}};")
        out.append(f"{B}node[anchor=west,font={B}scriptsize] at ({bl+0.12:.2f},{y:.2f}) "
                   f"{{{_fmt(v, dp)}{suffix}}};")
    return frame(out, w, rowh * len(labels))


def scatter(points, *, w=14.0, h=8.0, xlab="", ylab="", medx=None, medy=None,
            highlight=None):
    """points: [(name, x, y)]. `highlight` names the one to mark."""
    pts = [(n, x, y) for n, x, y in points if _n(x) and _n(y)]
    if not pts:
        return ""
    xs = [p[1] for p in pts] + ([medx] if medx else [])
    ys = [p[2] for p in pts] + ([medy] if medy else [])
    x0, x1 = min(xs) * 0.88, max(xs) * 1.10
    y0, y1 = min(ys) * 0.88, max(ys) * 1.10
    X = lambda v: (v - x0) / (x1 - x0) * w          # noqa: E731
    Y = lambda v: (v - y0) / (y1 - y0) * h          # noqa: E731
    out = [f"{B}draw[rule] (0,0) rectangle ({w:.2f},{h:.2f});"]
    if medx:
        out.append(f"{B}draw[rule,dashed] ({X(medx):.2f},0) -- ({X(medx):.2f},{h:.2f});")
    if medy:
        out.append(f"{B}draw[rule,dashed] (0,{Y(medy):.2f}) -- ({w:.2f},{Y(medy):.2f});")
    # Labels sit to the right by default and flip below when a neighbour is
    # already there. Two overlapping names are a chart that cannot be read.
    placed = []
    for name, x, y in sorted(pts, key=lambda p: (-p[2], p[1])):
        is_hi = (name == highlight)
        col = "sell" if is_hi else "serA"
        r = "2.6pt" if is_hi else "1.9pt"
        px, py = X(x), Y(y)
        out.append(f"{B}fill[{col}] ({px:.2f},{py:.2f}) circle ({r});")
        clash = any(abs(px - qx) < 2.6 and abs(py - qy) < 0.30 for qx, qy in placed)
        if clash:
            anchor, ox, oy = "north", 0.0, -0.16
        else:
            anchor, ox, oy = "west", 0.16, 0.0
        # A bare colour name in TikZ sets draw, fill AND text, so it must not
        # follow fill=white or it silently paints the mask box that colour.
        out.append(f"{B}node[anchor={anchor},fill=white,inner sep=1.2pt,font={B}scriptsize"
                   f"{',text=sell' if is_hi else ',text=serA'}] at "
                   f"({px+ox:.2f},{py+oy:.2f}) {{{name}}};")
        placed.append((px + ox, py + oy))
    for v in _ticks(x0, x1, 8):
        out.append(f"{B}node[anchor=north,font={B}scriptsize,gray] at ({X(v):.2f},-0.06) "
                   f"{{{_fmt(v, 0)}x}};")
    for v in _ticks(y0, y1, 8):
        out.append(f"{B}node[anchor=east,font={B}scriptsize,gray] at (-0.08,{Y(v):.2f}) "
                   f"{{{_fmt(v, 0)}x}};")
    if xlab:
        out.append(f"{B}node[anchor=north,font={B}scriptsize,gray] at ({w/2:.2f},-0.55) "
                   f"{{{xlab}}};")
    if ylab:
        out.append(f"{B}node[anchor=south,rotate=90,font={B}scriptsize,gray] at "
                   f"(-0.75,{h/2:.2f}) {{{ylab}}};")
    return frame(out, w, h)


def price_line(points, *, w=15.0, h=4.6, target=None, band=None, unit="KShs per share"):
    """A dated price series, with an optional target line and 52-week band.

    `points` is [(label, date_fraction, close)] where date_fraction runs 0..1
    across the window. Spacing is by DATE, not by index: the observations are
    unevenly spaced and drawing them evenly would misstate the path.
    """
    if not points:
        return ""
    vals = [p[2] for p in points]
    lo, hi = min(vals), max(vals)
    if band:
        lo, hi = min(lo, band[0]), max(hi, band[1])
    if target:
        lo, hi = min(lo, target), max(hi, target)
    pad = (hi - lo) * 0.12 or 1.0
    lo, hi = lo - pad, hi + pad
    ticks = _ticks(lo, hi)
    sy = h / (hi - lo)
    Y = lambda v: (v - lo) * sy
    X = lambda f: f * w

    out = []          # frame() opens the picture; opening a second nests them
    # the 52-week band, drawn behind everything
    if band:
        out.append(f"{B}fill[navy!7] (0,{Y(band[0]):.3f}) rectangle ({w:.2f},{Y(band[1]):.3f});")
        for v, lab in ((band[0], "52-week low"), (band[1], "52-week high")):
            out.append(f"{B}draw[navy!35,dashed,line width=0.3pt] (0,{Y(v):.3f}) -- ({w:.2f},{Y(v):.3f});")
            out.append(f"{B}node[anchor=west,font={B}tiny,text=navy!65] at (0.12,{Y(v)+0.20:.3f}) "
                       f"{{{lab} {_fmt(v, 2)}}};")
    for t in ticks:
        out.append(f"{B}draw[black!12,line width=0.2pt] (0,{Y(t):.3f}) -- ({w:.2f},{Y(t):.3f});")
        out.append(f"{B}node[anchor=east,font={B}tiny,text=black!55] at (-0.10,{Y(t):.3f}) "
                   f"{{{_fmt(t, 0)}}};")
    if target is not None:
        out.append(f"{B}draw[amber,line width=1.0pt,dash pattern=on 3pt off 2pt] "
                   f"(0,{Y(target):.3f}) -- ({w:.2f},{Y(target):.3f});")
        out.append(f"{B}node[anchor=east,font={B}tiny{B}bfseries,text=amber] "
                   f"at ({w-0.06:.2f},{Y(target)-0.26:.3f}) {{target {_fmt(target, 2)}}};")
    path = " -- ".join(f"({X(f):.3f},{Y(v):.3f})" for _lab, f, v in points)
    out.append(f"{B}draw[navy,line width=1.1pt] {path};")
    for lab, f, v in points:
        out.append(f"{B}fill[navy] ({X(f):.3f},{Y(v):.3f}) circle (1.5pt);")
    first, last = points[0], points[-1]
    out.append(f"{B}node[anchor=west,font={B}tiny{B}bfseries,text=navy] "
               f"at ({X(last[1])-1.55:.3f},{Y(last[2])+0.34:.3f}) {{{_fmt(last[2], 2)}}};")
    out.append(f"{B}node[anchor=west,font={B}tiny,text=black!55] "
               f"at (0.02,{-0.34:.3f}) {{{first[0]}}};")
    out.append(f"{B}node[anchor=east,font={B}tiny,text=black!55] "
               f"at ({w:.2f},{-0.34:.3f}) {{{last[0]}}};")
    out.append(f"{B}node[anchor=west,font={B}tiny,text=black!45] at (0,{h+0.30:.2f}) {{{unit}}};")
    return frame(out, w, h)
