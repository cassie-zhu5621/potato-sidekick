#!/usr/bin/env python3
"""Draw every state's motion as one static figure, from the clip CSVs.

WHY IT IS GENERATED AND NOT DRAWN. `motion/clips/*.csv` is the authority for
every motion fact in the paper (STUDY_CLIP_AUTHORITY.md). A figure drawn by hand
starts accurate and drifts the first time anything is re-exported from Blender,
and the drift is invisible -- a diagram never fails a test. So every angle,
duration and repeat count in the output is read out of the CSVs at draw time,
and re-exporting the clips and re-running this is the whole update procedure.

WHAT IS AUTHORED is the annotation: which arc to draw, what to call it, and
which pose is worth freezing. That judgement is not in the data.

    python3 tools/make_state_figure.py                    -> figures_paper/
    python3 tools/make_state_figure.py -o /tmp --png

THE TWO ANGLES, because the rig's names do not mean what they look like:

    tilt  the NECK's pitch. Negative leans the whole neck forward.
    nod   the HEAD's pitch RELATIVE TO THE NECK. Positive lifts the gaze.

so the head's absolute angle is `tilt + nod`, and that is what a viewer reads as
"where it is looking". S7b is the case that makes it matter: tilt -24 with nod
+24 is a neck craned 24 degrees forward while the gaze stays level -- the
epistemic lean, and it is a different gesture from simply looking down, which is
what a figure drawn from `tilt` alone would show.

PAN cannot be drawn in profile at all -- it is rotation about the vertical -- so
it gets a plan-view fan under the base rather than being faked in the silhouette.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIPS = os.path.join(ROOT, "motion", "clips")
ART = os.path.join(ROOT, "motion", "robot_art")


# --------------------------------------------------------------------------- #
# Cassie's drawing, rigged
# --------------------------------------------------------------------------- #
def load_art():
    """The three pieces of her drawing, base64'd, or None if not cut yet.

    Embedded rather than referenced so the .svg is one file: it goes into
    Overleaf on its own, and a figure that silently loses its images when moved
    is worse than a figure that never had any.
    """
    meta = os.path.join(ART, "parts.json")
    if not os.path.exists(meta):
        return None
    import base64
    import json
    m = json.load(open(meta))
    for name, part in m["parts"].items():
        with open(os.path.join(ART, part["file"]), "rb") as f:
            part["b64"] = base64.b64encode(f.read()).decode("ascii")
    return m


ART_META = load_art()

# SVG rotates clockwise-positive. `tilt` and `nod` are positive when the joint
# lifts, and her robot faces LEFT in the drawing, so a lift is anticlockwise on
# the page: the sign flips once, here, rather than in three call sites.
SPIN = -1.0


# --------------------------------------------------------------------------- #
# reading the authority
# --------------------------------------------------------------------------- #
def load(name):
    rows = list(csv.DictReader(open(os.path.join(CLIPS, name + ".csv"))))
    return {
        "t": [int(r["t_ms"]) for r in rows],
        "pan": [float(r["pan_deg"]) for r in rows],
        "tilt": [float(r["tilt_deg"]) for r in rows],
        "nod": [float(r["nod_deg"]) for r in rows],
        "led": [int(r["led"]) for r in rows],
    }


def turns(v, t, eps=0.4):
    """The turning points, which is what a motion arc is actually made of."""
    out, d = [(t[0], v[0])], 0
    for i in range(1, len(v)):
        dv = v[i] - v[i - 1]
        if abs(dv) < 1e-9:
            continue
        nd = 1 if dv > 0 else -1
        if d and nd != d and abs(v[i - 1] - out[-1][1]) > eps:
            out.append((t[i - 1], v[i - 1]))
        d = nd
    out.append((t[-1], v[-1]))
    return out


def still(v):
    return max(v) - min(v) < 0.5


# --------------------------------------------------------------------------- #
# the glyph
# --------------------------------------------------------------------------- #
INK, GHOST, ACCENT, LAMP = "#1f3d2f", "#b9cfc0", "#c8622f", "#f0c419"
BODY, BODY_G = "#9dbfab", "#dfeae2"


def robot(cx, cy, tilt, nod, ghost=False, scale=1.0, lamp=None):
    """One silhouette, in Cassie's own line. cy is the ground line.

    THE BODY IS HERS AND THE ANGLES ARE THE CLIPS'. Her drawing is one pose --
    S1_IDLE, bowed -- so every state is that drawing with the neck and the head
    turned by the DIFFERENCE between this pose and hers. Drawing the shapes in
    code instead, which is what this did first, produced a figure of a robot
    that does not exist.

    The chain is two rotations composed, and SVG applies them right to left:
    the head turns about its own joint first, then the neck carries it. Writing
    them in the other order would rotate the head about a pivot that has
    already moved.
    """
    if ART_META is None:                      # nothing traced yet
        return _fallback(cx, cy, tilt, nod, ghost, scale, lamp)

    m = ART_META
    Hpx = 190.0 * scale                       # the body's height on the page
    Wpx = Hpx * m["aspect"]
    x0, y0 = cx - Wpx / 2, cy - Hpx           # cy is the ground

    def at(frac):
        return x0 + frac[0] * Wpx, y0 + frac[1] * Hpx

    npv, hpv = at(m["neck_pivot"]), at(m["head_pivot"])
    dneck = SPIN * (tilt - m["ref_tilt"])
    dhead = SPIN * (nod - m["ref_nod"])

    op = 0.38 if ghost else 1.0
    g = [f'<g opacity="{op}">']
    if ghost:
        # a wash, not a second robot: the pale pose is context for the solid one
        g = [f'<g opacity="{op}" filter="url(#pale)">']

    def img(part, tf=""):
        p = m["parts"][part]
        return (f'<image x="{x0:.2f}" y="{y0 + p["y"] * Hpx:.2f}" '
                f'width="{Wpx:.2f}" height="{p["h"] * Hpx:.2f}" {tf} '
                f'href="data:image/png;base64,{p["b64"]}"/>')

    # NECK, THEN BASE, THEN HEAD. Each part keeps some of its neighbour so the
    # joints do not open a wedge when they turn, and the overlap has to end up
    # UNDERNEATH: base over the neck's foot, head over the neck's top. Drawing
    # base first put the neck's lower rows on top of the collar instead, and
    # they swung out past it as a green spike every time the neck leaned.
    g.append(img("neck", f'transform="rotate({dneck:.2f} {npv[0]:.1f} {npv[1]:.1f})"'))
    g.append(img("base"))
    g.append(img("head", f'transform="rotate({dneck:.2f} {npv[0]:.1f} {npv[1]:.1f}) '
                         f'rotate({dhead:.2f} {hpv[0]:.1f} {hpv[1]:.1f})"'))
    g.append("</g>")

    # where the head ended up, for the annotations to hang off
    import math as _m
    def spin(px, py, deg, ox, oy):
        a = _m.radians(deg)
        dx, dy = px - ox, py - oy
        return ox + dx * _m.cos(a) - dy * _m.sin(a), oy + dx * _m.sin(a) + dy * _m.cos(a)
    # The head's VISUAL centre, carried through both joints -- so annotations
    # and the light sit on the head wherever the head has gone, instead of
    # hanging in the air beside a bowed one.
    c0 = at((0.44, 0.19))
    cx_, cy_ = spin(c0[0], c0[1], dhead, hpv[0], hpv[1])
    hx, hy = spin(cx_, cy_, dneck, npv[0], npv[1])

    if lamp and not ghost:
        # the light channel is drawn per state, which is why the drawing's own
        # lamp was repainted out in trace_robot_parts.py
        g.insert(-1, f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="{0.055*Hpx:.1f}" '
                     f'fill="{lamp}" opacity=".95"/>')
        g.insert(-1, f'<circle cx="{hx:.1f}" cy="{hy:.1f}" r="{0.105*Hpx:.1f}" '
                     f'fill="{lamp}" opacity=".22"/>')
    return "\n".join(g), (hx, hy)


def _fallback(cx, cy, tilt, nod, ghost, scale, lamp):
    """Used only before `trace_robot_parts.py` has been run."""
    fill = BODY_G if ghost else BODY
    line = GHOST if ghost else INK
    s, w = scale, (1.6 if ghost else 2.4)
    bw, bh = 34 * s, 22 * s
    g = [f'<g opacity="{0.55 if ghost else 1}">',
         f'<rect x="{cx-bw}" y="{cy-bh}" width="{2*bw}" height="{bh}" rx="{5*s}" '
         f'fill="{fill}" stroke="{line}" stroke-width="{w}"/>']
    px, py = cx, cy - bh - 4 * s
    a = math.radians(tilt)
    nx, ny = px + math.sin(a) * 54 * s, py - math.cos(a) * 54 * s
    g.append(f'<line x1="{px}" y1="{py}" x2="{nx}" y2="{ny}" stroke="{line}" '
             f'stroke-width="{9*s}" stroke-linecap="round"/>')
    g.append(f'<g transform="translate({nx},{ny}) rotate({-(tilt+nod)})">'
             f'<rect x="-14" y="-20" width="39" height="36" rx="6" fill="{fill}" '
             f'stroke="{line}" stroke-width="{w}"/></g></g>')
    return "\n".join(g), (nx, ny)


def fan(cx, cy, lo, hi, marks=(), end=None, label=None):
    """Plan-view pan arc, foreshortened, sitting under the base.

    Pan is rotation about the vertical and cannot appear in a profile
    silhouette. Faking it in the body would make the figure lie about which
    joint moved, so it gets its own protractor seen from above: pan 0 is the
    bottom of the ellipse (facing the reader), negative is to the left.

    The full reachable span is drawn faint underneath, so a 30-degree move
    cannot read the same size as a 120-degree one.
    """
    RX, RY = 62.0, 17.0
    fy = cy + 30

    def pt(d):
        a = math.radians(90 - d)
        return cx + RX * math.cos(a), fy + RY * math.sin(a)

    out = []
    a0, a1 = pt(-90), pt(90)
    out.append(f'<path d="M {a0[0]:.1f} {a0[1]:.1f} A {RX} {RY} 0 0 0 '
               f'{a1[0]:.1f} {a1[1]:.1f}" fill="none" stroke="#dfe7e1" '
               f'stroke-width="1.6"/>')
    if abs(hi - lo) > 0.5:
        x0, y0 = pt(lo)
        x1, y1 = pt(hi)
        out.append(f'<path d="M {x0:.1f} {y0:.1f} A {RX} {RY} 0 0 0 '
                   f'{x1:.1f} {y1:.1f}" fill="none" stroke="{ACCENT}" '
                   f'stroke-width="2.6" stroke-linecap="round"/>')
    for m in marks:
        mx, my = pt(m)
        out.append(f'<circle cx="{mx:.1f}" cy="{my:.1f}" r="2.8" '
                   f'fill="#ffffff" stroke="{ACCENT}" stroke-width="1.4"/>')
    if end is not None:
        ex, ey = pt(end)
        out.append(f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="4.4" fill="{ACCENT}"/>')
    if label:
        out.append(f'<text x="{cx}" y="{fy+30}" class="pan">{label}</text>')
    return "\n".join(out)


def neck_angle(cx, cy, t0, t1, label=None, s=1.0):
    """Mark the neck's two pitches and the angle between them.

    S7's lean is 12 degrees. On a 54px neck that is 11px of travel, and the
    pale silhouette alone does not read -- which tempts an author to draw a
    deeper lean than the robot performs. Measuring it instead keeps the
    silhouette honest and still makes the gesture visible.
    """
    px, py = cx, cy - 22 * s - 4 * s
    L = 104 * s
    out = []
    for t, op, dash in ((t0, .45, "4 4"), (t1, .95, None)):
        a = math.radians(t)
        x, y = px + math.sin(a) * L, py - math.cos(a) * L
        d = f' stroke-dasharray="{dash}"' if dash else ""
        out.append(f'<line x1="{px}" y1="{py}" x2="{x:.1f}" y2="{y:.1f}" '
                   f'stroke="{ACCENT}" stroke-width="1.3" opacity="{op}"{d}/>')
    r = 92 * s
    a0, a1 = math.radians(t0), math.radians(t1)
    p0 = (px + math.sin(a0) * r, py - math.cos(a0) * r)
    p1 = (px + math.sin(a1) * r, py - math.cos(a1) * r)
    sweep = 0 if t1 < t0 else 1
    out.append(f'<path d="M {p0[0]:.1f} {p0[1]:.1f} A {r} {r} 0 0 {sweep} '
               f'{p1[0]:.1f} {p1[1]:.1f}" fill="none" stroke="{ACCENT}" '
               f'stroke-width="2.2" marker-end="url(#tip)"/>')
    if label:
        mid = math.radians((t0 + t1) / 2)
        lx, ly = px + math.sin(mid) * (r + 15), py - math.cos(mid) * (r + 15) - 4
        out.append(f'<text x="{lx:.1f}" y="{ly:.1f}" class="pan">{label}</text>')
    return "\n".join(out)


def arc(x, y, r, a0, a1, w=2.0, dash=None, head=True):
    """A rotation arrow, for the joint that moved."""
    def p(d):
        a = math.radians(d)
        return x + r * math.cos(a), y + r * math.sin(a)
    x0, y0 = p(a0)
    x1, y1 = p(a1)
    big = 1 if abs(a1 - a0) > 180 else 0
    sweep = 1 if a1 > a0 else 0
    d = f' stroke-dasharray="{dash}"' if dash else ""
    m = ' marker-end="url(#tip)"' if head else ""
    return (f'<path d="M {x0:.1f} {y0:.1f} A {r} {r} 0 {big} {sweep} '
            f'{x1:.1f} {y1:.1f}" fill="none" stroke="{ACCENT}" '
            f'stroke-width="{w}"{d}{m}/>')


# --------------------------------------------------------------------------- #
# what each cell says. The numbers come from the CSV; the framing is authored.
# --------------------------------------------------------------------------- #
def cells():
    C = {n: load(n) for n in ("S1_IDLE S2_LISTEN S3_ACK S4_PLAN S5A_SETTLE "
                              "S5B_TRACK S6_FINETUNE S7a S7b S8_ERROR".split())}

    def dur(n):
        return C[n]["t"][-1] / 1000.0

    def pose(n, i):
        c = C[n]
        return c["tilt"][i], c["nod"][i], c["pan"][i]

    out = []

    # --- IDLE -------------------------------------------------------------
    t, d, p = pose("S1_IDLE", 0)
    lo, hi = min(C["S1_IDLE"]["led"]), max(C["S1_IDLE"]["led"])
    out.append(dict(
        key="S1", name="Idle", sub="asleep",
        ghost=None, solid=(t, d), lamp=LAMP,
        pan=None,
        note=f"held: neck {t:+.0f}°, head {t+d:+.0f}°",
        why=f"no motion at all. The light breathes ({lo}–{hi}) and that is "
            f"the whole state: present, not attending.",
        glyphs=[("breathe", None)]))

    # --- ATTEND -----------------------------------------------------------
    a, b = pose("S2_LISTEN", 0), pose("S2_LISTEN", -1)
    out.append(dict(
        key="S2", name="Attend", sub="lifts out of the bow",
        ghost=(a[0], a[1]), solid=(b[0], b[1]), lamp=None,
        pan=dict(lo=min(a[2], b[2]), hi=max(a[2], b[2]), end=b[2],
                 label=f"pan {a[2]:+.0f}° → {b[2]:+.0f}°"),
        note=f"head {a[0]+a[1]:+.0f}° → {b[0]+b[1]:+.0f}° in "
             f"{dur('S2_LISTEN'):.1f} s",
        why="the gaze rises through 75 degrees. Nod and tilt start ~300 ms "
            "before pan, so it is looking up before it is turning.",
        glyphs=[("up", None)]))

    # --- ACKNOWLEDGE ------------------------------------------------------
    tp = turns(C["S3_ACK"]["nod"], C["S3_ACK"]["t"])
    first = abs(tp[1][1] - tp[0][1])
    second = abs(tp[3][1] - tp[2][1])
    a = pose("S3_ACK", 0)
    low = tp[1][1]                      # the bottom of the first nod
    out.append(dict(
        key="S3", name="Acknowledge", sub="two nods, the second smaller",
        ghost=(a[0], low), solid=(a[0], a[1]), lamp=None,
        pan=None,
        note=f"nod {first:.0f}° then {second:.0f}° "
             f"({second/first:.2f}×) · {dur('S3_ACK'):.1f} s",
        why="a decaying pair. One nod reads as a twitch; two equal ones read "
            "as a machine repeating itself.",
        glyphs=[("nod2", None)]))

    # --- SCAN -------------------------------------------------------------
    pans = C["S4_PLAN"]["pan"]
    a, b = pose("S4_PLAN", 0), pose("S4_PLAN", -1)
    out.append(dict(
        key="S4", name="Scan", sub="five stations, then back to the pick",
        ghost=(a[0], a[1]), solid=(b[0], b[1]), lamp=None,
        pan=dict(lo=min(pans), hi=max(pans), end=b[2],
                 marks=(-60, -30, 0, 30, 60),
                 label=f"pan {min(pans):+.0f}° ↔ {max(pans):+.0f}°"),
        note=f"{max(pans)-min(pans):.0f}° of sweep · "
             f"{dur('S4_PLAN'):.1f} s",
        why="the only clip that crosses the room. Head level throughout: it is "
            "surveying, not addressing anyone.",
        glyphs=[]))

    # --- SETTLE -----------------------------------------------------------
    tt = turns(C["S5A_SETTLE"]["tilt"], C["S5A_SETTLE"]["t"])
    over = abs(tt[1][1] - tt[-1][1])
    a, b = pose("S5A_SETTLE", 0), pose("S5A_SETTLE", -1)
    mid = (tt[1][1], turns(C["S5A_SETTLE"]["nod"], C["S5A_SETTLE"]["t"])[1][1])
    out.append(dict(
        key="S5a", name="Settle", sub="arrives, and overshoots once",
        ghost=mid, solid=(b[0], b[1]), lamp=None,
        pan=None,
        note=f"{over:.0f}° overshoot, one rebound · "
             f"{dur('S5A_SETTLE'):.1f} s",
        why="on tilt and nod only — pan is held. A body that arrives "
            "exactly reads as a machine; one that has to settle reads as mass.",
        glyphs=[("rebound", None)]))

    # --- WATCH ------------------------------------------------------------
    a = pose("S5B_TRACK", 0)
    out.append(dict(
        key="S5b", name="Watch", sub="holds",
        ghost=None, solid=(a[0], a[1]), lamp=None,
        pan=None,
        note=f"held: neck {a[0]:+.0f}°, head {a[0]+a[1]:+.0f}°, "
             f"level",
        why="stillness is the performance. The gaze is horizontal and stays "
            "there; everything that follows is a departure from this pose.",
        glyphs=[("hold", None)]))

    # --- CORRECT ----------------------------------------------------------
    tp = turns(C["S6_FINETUNE"]["pan"], C["S6_FINETUNE"]["t"])
    base = tp[0][1]
    amps = [abs(v - base) for _t, v in tp[1:-1]]
    a, b = pose("S6_FINETUNE", 0), pose("S6_FINETUNE", -1)
    out.append(dict(
        key="S6", name="Correct", sub="a shake that decays, then looks up",
        ghost=(a[0], a[1]), solid=(b[0], b[1]), lamp=None,
        pan=dict(lo=min(C["S6_FINETUNE"]["pan"]), hi=max(C["S6_FINETUNE"]["pan"]),
                 end=b[2],
                 label=f"shake ±{amps[0]:.0f}° → "
                       f"±{amps[2]:.0f}°"),
        note=f"then neck {a[0]:+.0f}° → {b[0]:+.0f}°, head comes up "
             f"· {dur('S6_FINETUNE'):.1f} s",
        why="'not that one', then it comes back up to be told where instead. "
            "The decay is what stops a shake reading as a fault.",
        glyphs=[("shake", None)]))

    # --- CALL, arrival ----------------------------------------------------
    a = pose("S7a", 0)
    tt = turns(C["S7a"]["tilt"], C["S7a"]["t"])
    lean = abs(tt[1][1] - tt[0][1])
    out.append(dict(
        key="S7a", name="Call", sub="leans in — the bid",
        ghost=(a[0], a[1]), solid=(tt[1][1], turns(C["S7a"]["nod"], C["S7a"]["t"])[1][1]),
        lamp=None, pan=None,
        note=f"neck {lean:.0f}° forward, gaze stays level · "
             f"{dur('S7a'):.1f} s",
        why="neck and head move by the SAME amount in opposite senses, so it "
            "cranes toward what it found without turning to face her.",
        glyphs=[("neck", (a[0], tt[1][1], f"{lean:.0f}°"))]))

    # --- CALL, holding ----------------------------------------------------
    tt = turns(C["S7b"]["tilt"], C["S7b"]["t"])
    cycles = max(1, (len(tt) - 1) // 2)
    a = pose("S7b", 0)
    out.append(dict(
        key="S7b", name="Call", sub="repeats, then holds",
        ghost=(a[0], a[1]),
        solid=(tt[1][1], turns(C['S7b']['nod'], C['S7b']['t'])[1][1]),
        lamp=None, pan=None,
        note=f"{cycles} lean cycles, pan held at {a[2]:+.0f}° · "
             f"{dur('S7b'):.1f} s",
        why="it asks three times and then waits. Pan never moves: the bid is "
            "about the thing, not about her.",
        glyphs=[("neck3", (a[0], tt[1][1],
                            f"{abs(tt[1][1]-a[0]):.0f}° × {cycles}"))]))

    # --- ERROR ------------------------------------------------------------
    tp = turns(C["S8_ERROR"]["pan"], C["S8_ERROR"]["t"])
    a = pose("S8_ERROR", 0)
    pans = C["S8_ERROR"]["pan"]
    out.append(dict(
        key="S8", name="Error", sub="a slow, aimless wander",
        ghost=None, solid=(a[0], a[1]), lamp=None,
        pan=dict(lo=min(pans), hi=max(pans), end=a[2],
                 label=f"pan {min(pans):+.0f}° ↔ {max(pans):+.0f}°"),
        note=f"head {a[0]+a[1]:+.0f}°, lowered · "
             f"{dur('S8_ERROR'):.1f} s",
        why="the head stays down and the pan drifts without settling. Looking "
            "for something it is not going to find.",
        glyphs=[("wander", None)]))

    return out


# --------------------------------------------------------------------------- #
# the sheet
# --------------------------------------------------------------------------- #
W, COLS = 1500, 5
CW, CH = 296, 448


def decorate(kind, cx, gy, head, arg=None):
    hx, hy = head
    if kind == "breathe":
        return (f'<circle cx="{hx}" cy="{hy}" r="26" fill="none" '
                f'stroke="{LAMP}" stroke-width="1.6" opacity=".5"/>'
                f'<circle cx="{hx}" cy="{hy}" r="36" fill="none" '
                f'stroke="{LAMP}" stroke-width="1.2" opacity=".25"/>')
    if kind == "up":
        return arc(hx + 6, hy + 30, 40, 128, 196, 2.2)
    if kind == "nod2":
        return (arc(hx + 14, hy + 4, 30, 200, 250, 2.4)
                + arc(hx + 14, hy + 4, 42, 210, 240, 1.8, dash="4 4"))
    if kind == "rebound":
        return (arc(hx + 10, hy + 22, 34, 150, 205, 2.4)
                + arc(hx + 10, hy + 22, 34, 205, 188, 1.6, dash="3 3"))
    if kind == "hold":
        # stillness needs a mark of its own, or the cell reads as unfinished:
        # a level sightline, plus brackets saying nothing inside them moves
        return (f'<line x1="{hx-28}" y1="{hy-2}" x2="{hx-108}" y2="{hy-2}" '
                f'stroke="{ACCENT}" stroke-width="1.4" stroke-dasharray="6 5" '
                f'opacity=".8"/>'
                f'<circle cx="{hx-112}" cy="{hy-2}" r="3.4" fill="none" '
                f'stroke="{ACCENT}" stroke-width="1.6"/>'
                f'<text x="{hx-70}" y="{hy-12}" class="pan">level</text>')
    if kind == "shake":
        return (f'<path d="M {hx-54} {hy-24} q 10 -9 20 0 t 20 0 t 20 0" '
                f'fill="none" stroke="{ACCENT}" stroke-width="2.2"/>')
    if kind in ("neck", "neck3"):
        t0, t1, lab = arg
        s = neck_angle(cx, gy, t0, t1, lab)
        if kind == "neck3":
            # three passes of the same lean, so the repeat is visible without
            # three silhouettes fighting for the same 11 pixels
            for k, op in ((0, .5), (1, .3)):
                s += (f'<path d="M {cx-52} {gy-166-k*11} q 26 -11 52 0" '
                      f'fill="none" stroke="{ACCENT}" stroke-width="1.4" '
                      f'opacity="{op}"/>')
        return s
    if kind == "wander":
        return (f'<path d="M {hx-58} {hy-30} q 14 10 28 -2 t 28 6" '
                f'fill="none" stroke="{ACCENT}" stroke-width="1.8" '
                f'stroke-dasharray="5 4" opacity=".8"/>')
    return ""


def build():
    cs = cells()
    rows = (len(cs) + COLS - 1) // COLS
    H = 78 + rows * CH
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
         f'width="{W}" height="{H}" font-family="Helvetica, Arial, sans-serif">',
         f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
         '<defs><marker id="tip" viewBox="0 0 10 10" refX="8" refY="5" '
         'markerWidth="5.5" markerHeight="5.5" orient="auto-start-reverse">'
         f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{ACCENT}"/></marker>'
         '<filter id="pale"><feColorMatrix type="saturate" values="0.25"/>'
         '<feComponentTransfer><feFuncA type="linear" slope="0.9"/>'
         '</feComponentTransfer></filter></defs>',
         f'''<style>
  .h   {{ font-size:19px; font-weight:700; fill:{INK} }}
  .hs  {{ font-size:12.5px; fill:#7d8b83 }}
  .nm  {{ font-size:17px; font-weight:700; fill:{INK} }}
  .sb  {{ font-size:12px; fill:#7d8b83 }}
  .id  {{ font-size:10.5px; fill:#a8b3ac; letter-spacing:.12em; font-weight:700 }}
  .no  {{ font-size:11.5px; fill:{ACCENT}; font-family:ui-monospace,Menlo,monospace }}
  .wh  {{ font-size:12px; fill:#5d6b63 }}
  .pan {{ font-size:10.5px; fill:{ACCENT}; text-anchor:middle;
          font-family:ui-monospace,Menlo,monospace }}
  .chip{{ fill:#eef3ef }}
</style>''',
         f'<text class="h" x="30" y="34">The eight-state cycle, as motion</text>',
         f'<text class="hs" x="30" y="55">Every angle, duration and repeat count '
         f'below is read from motion/clips/*.csv at draw time. '
         f'Pale silhouette = where the gesture starts; solid = where it ends. '
         f'The fan under the base is pan, which a profile cannot show.</text>']

    for i, c in enumerate(cs):
        col, row = i % COLS, i // COLS
        x0 = 24 + col * CW
        y0 = 78 + row * CH
        cx = x0 + CW / 2 - 12
        gy = y0 + 214                     # ground line

        o.append(f'<rect x="{x0}" y="{y0}" width="{CW-16}" height="{CH-18}" '
                 f'rx="10" fill="#fbfcfb" stroke="#e4ebe6" stroke-width="1.4"/>')
        o.append(f'<text class="id" x="{x0+18}" y="{y0+26}">{c["key"]}</text>')

        if c["ghost"] and (abs(c["ghost"][0] - c["solid"][0]) > 0.5
                           or abs(c["ghost"][1] - c["solid"][1]) > 0.5):
            s, _ = robot(cx, gy, c["ghost"][0], c["ghost"][1], ghost=True)
            o.append(s)
        s, head = robot(cx, gy, c["solid"][0], c["solid"][1], lamp=c["lamp"])
        o.append(s)
        for kind, arg in c["glyphs"]:
            o.append(decorate(kind, cx, gy, head, arg))

        o.append(f'<line x1="{x0+18}" y1="{gy+3}" x2="{x0+CW-34}" y2="{gy+3}" '
                 f'stroke="#d6e0d9" stroke-width="1.4"/>')

        if c["pan"]:
            p = c["pan"]
            o.append(fan(cx, gy, p["lo"], p["hi"], p.get("marks", ()),
                         p.get("end"), p.get("label")))

        ty = y0 + 300
        o.append(f'<rect class="chip" x="{x0+16}" y="{ty-20}" '
                 f'width="{CW-48}" height="30" rx="8"/>')
        o.append(f'<text class="nm" x="{x0+28}" y="{ty}">{c["name"]}</text>')
        o.append(f'<text class="sb" x="{x0+18}" y="{ty+30}">{c["sub"]}</text>')
        o.append(f'<text class="no" x="{x0+18}" y="{ty+52}">{c["note"]}</text>')
        for k, ln in enumerate(wrap(c["why"], 40)):
            o.append(f'<text class="wh" x="{x0+18}" y="{ty+76+k*16}">{esc(ln)}</text>')

    o.append("</svg>")
    return "\n".join(o)


def wrap(s, n):
    out, ln = [], ""
    for w in s.split():
        if len(ln) + len(w) + 1 > n:
            out.append(ln); ln = w
        else:
            ln = (ln + " " + w).strip()
    if ln:
        out.append(ln)
    return out[:4]


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out",
                    default=os.path.join(os.path.dirname(ROOT), "figures_paper"))
    ap.add_argument("--png", action="store_true")
    a = ap.parse_args(argv)
    os.makedirs(a.out, exist_ok=True)
    p = os.path.join(a.out, "fig3_states_as_motion.svg")
    open(p, "w").write(build())
    print("->", p)
    if a.png:
        import cairosvg
        q = p[:-4] + ".png"
        cairosvg.svg2png(url=p, write_to=q, output_width=2400)
        print("->", q)
    return 0


if __name__ == "__main__":
    sys.exit(main())
