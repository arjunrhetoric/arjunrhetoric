#!/usr/bin/env python3
"""Render the profile README cards into cards/.

Reads data/contributions.json (refreshed daily by .github/workflows/profile-cards.yml)
and writes one animated SVG per card:

    hero.svg                    name, one line about me, optional portrait
    tile-*.svg                  three linked tiles, shown at width 32.5%
    chart-monthly.svg           contributions per month, streak, active days, busiest day
    graph-contributions.svg     contribution graph in the brand colour
    repo-*.svg                  one card per featured repo

    python scripts/render_cards.py             animated cards into cards/
    STATIC=1 python scripts/render_cards.py    final frame, no animation, into preview/

Animation is CSS keyframes (entrances, loops) plus SMIL <animate> (reveals, caret).
Everything that moves is either a CSS class or sits in a <g class="motion"> overlay.
The base content is always the final frame, so prefers-reduced-motion only has to
hide the overlays and stop the CSS, and the page still reads correctly.
"""

import base64
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "contributions.json"
ASSETS = ROOT / "assets"
STATIC = os.environ.get("STATIC") == "1"
OUT = ROOT / ("preview" if STATIC else "cards")

W = 860  # README column width
STEP = 0.35  # seconds between cards, so the stack plays top to bottom

# Colours from arjunrhetoric.vercel.app: --bg-color, --text-primary, --text-secondary, --capsule-bg
CARD = "#0c0c0c"
LINE = "#2a2a2a"
TEXT = "#f5f5f5"
MUTED = "#8a8a8a"
BRAND = "#f16a4b"
EMPTY = "#1f1f1f"

NAME = "Arjun Singh"
EYEBROW = "product @ innoverse"
ABOUT = "i build things that actually work (mostly)."
TILES = [
    ("portfolio", "arjunrhetoric.vercel.app"),
    ("linkedin", "arjun-singh-9b85623a4"),
    ("email", "singharjun211106@gmail.com"),
]
# Repo name -> one-liner from the README. Falls back to the GitHub description.
FEATURED = {
    "formflow": "The Architect's Canvas: a no-code form builder with real-time collaboration.",
    "nexus-bank-support": "Banking support portal with a ticket system, Dockerised behind Nginx.",
    "infrachat": "Login, authentication and logout built with Java Servlets.",
    "innoverse": "Innovation community platform for builders who actually ship.",
}

FONTS = {
    "serif": ("ISerif", ASSETS / "fonts" / "InstrumentSerif-Regular.ttf", "400"),
    "sans": ("IInter", ASSETS / "fonts" / "Inter-Variable.ttf", "100 900"),
}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
OPACITY = {1: 0.28, 2: 0.5, 3: 0.75, 4: 1.0}

FONT_CLASSES = ".serif{font-family:ISerif,Georgia,serif}.sans{font-family:IInter,Arial,sans-serif}"


def _anim_class(name, animation, extra=""):
    return f".{name}{{{extra}animation:{animation};animation-delay:var(--d,0s)}}"


ANIM_CSS = (
    "@keyframes rise{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}"
    "@keyframes fade{from{opacity:0}to{opacity:1}}"
    "@keyframes pop{0%{opacity:0;transform:scale(.3)}70%{opacity:1;transform:scale(1.15)}100%{opacity:1;transform:none}}"
    "@keyframes tilein{0%{opacity:0;transform:translateY(90px) scale(.6) rotate(-9deg)}55%{opacity:1;transform:translateY(-10px) scale(1.05) rotate(2deg)}75%{transform:translateY(3px) scale(.98) rotate(-.8deg)}90%{transform:translateY(-1px) scale(1.01) rotate(.2deg)}100%{opacity:1;transform:none}}"
    "@keyframes slidein{from{opacity:0;transform:translateX(-26px)}to{opacity:1;transform:none}}"
    "@keyframes arrowin{0%{opacity:0;transform:translateX(-30px)}60%{opacity:1;transform:translateX(6px)}100%{opacity:1;transform:none}}"
    "@keyframes growx{from{transform:scaleX(0)}to{transform:scaleX(1)}}"
    "@keyframes pulse{0%,100%{opacity:1}50%{opacity:.25}}"
    "@keyframes nudge{0%,100%{transform:none}50%{transform:translateX(4px)}}"
    "@keyframes sheen{0%{transform:translateX(-260px) skewX(-20deg)}22%,100%{transform:translateX(1200px) skewX(-20deg)}}"
    "@keyframes ring{0%{opacity:.7;transform:scale(.2)}100%{opacity:0;transform:scale(2.4)}}"
    + _anim_class("rise", "rise .8s cubic-bezier(.2,.7,.2,1) both")
    + _anim_class("fade", "fade .9s ease-out both")
    + _anim_class("pop", "pop .4s ease-out both", "transform-box:fill-box;transform-origin:center;")
    + _anim_class("tilein", "tilein 1.1s cubic-bezier(.2,.8,.2,1) both", "transform-box:fill-box;transform-origin:50% 60%;")
    + _anim_class("slidein", "slidein .6s cubic-bezier(.2,.7,.2,1) both", "transform-box:fill-box;")
    + _anim_class("arrowin", "arrowin .7s cubic-bezier(.2,.7,.2,1) both", "transform-box:fill-box;")
    + _anim_class("growx", "growx .8s cubic-bezier(.2,.7,.2,1) both", "transform-box:fill-box;transform-origin:0 50%;")
    + _anim_class("pulse", "pulse 1.6s ease-in-out 3")
    + _anim_class("nudge", "nudge 1.8s ease-in-out 2", "transform-box:fill-box;")
    + _anim_class("sheen", "sheen 6s ease-in-out 1 both")
    + _anim_class("ring", "ring 1.1s ease-out both", "opacity:0;transform-box:fill-box;transform-origin:center;")
    + "@media (prefers-reduced-motion:reduce){*{animation:none!important}.motion{display:none!important}}"
)

_metrics = {}


def advance(font, s, size):
    """Width of s in px, from the full font's advance widths (used for wrapping and centring)."""
    if font not in _metrics:
        ttf = TTFont(FONTS[font][1])
        _metrics[font] = (ttf.getBestCmap(), ttf["hmtx"], ttf["head"].unitsPerEm)
    cmap, hmtx, upm = _metrics[font]
    units = sum(hmtx[cmap[ord(ch)]][0] if ord(ch) in cmap else upm // 2 for ch in s)
    return units * size / upm


def font_face(font, chars):
    """Subset the font to the characters a card uses and embed it as a base64 WOFF."""
    family, path, weight = FONTS[font]
    tool = [shutil.which("pyftsubset")] if shutil.which("pyftsubset") else [sys.executable, "-m", "fontTools.subset"]
    with tempfile.TemporaryDirectory() as tmp:
        text_file = Path(tmp) / "chars.txt"
        text_file.write_text("".join(sorted(chars)), encoding="utf-8")
        out = Path(tmp) / "font.woff"
        subprocess.run(
            [*tool, str(path), f"--text-file={text_file}", "--flavor=woff", "--no-hinting", f"--output-file={out}"],
            check=True,
        )
        data = base64.b64encode(out.read_bytes()).decode("ascii")
    return (
        f'@font-face{{font-family:"{family}";src:url(data:font/woff;base64,{data}) format("woff");'
        f"font-weight:{weight};font-style:normal}}"
    )


def portrait_uri():
    mimes = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
    for path in sorted(ASSETS.glob("portrait.*")):
        mime = mimes.get(path.suffix.lstrip(".").lower())
        if mime:
            return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode('ascii')}"
    return None


def wrap(s, font, size, width, max_lines):
    lines, cur = [], ""
    for word in s.split():
        trial = f"{cur} {word}".strip()
        if not cur or advance(font, trial, size) <= width:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while lines[-1] and advance(font, lines[-1] + "…", size) > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1].rstrip() + "…"
    return lines


class Card:
    def __init__(self, name, height, label, at=0.0, width=W):
        self.name = name
        self.width = width
        self.height = height
        self.label = label
        self.at = at  # seconds from page load before this card's first animation
        self.body = []
        self.used = {"serif": set(), "sans": set()}

    def el(self, tag, content=None, *, cls="", anim="", t=0.0, **attrs):
        if anim and not STATIC:
            cls = f"{cls} {anim}".strip()
            attrs["style"] = f"--d:{self.at + t:.2f}s"
        if cls:
            attrs["class"] = cls
        # trailing underscore escapes Python keywords (from_ -> from); other underscores become hyphens
        pairs = "".join(
            f' {k.rstrip("_").replace("_", "-")}="{escape(str(v), {chr(34): "&quot;"})}"' for k, v in attrs.items()
        )
        if content is None:
            return f"<{tag}{pairs}/>"
        return f"<{tag}{pairs}>{content}</{tag}>"

    def add(self, tag, content=None, **kw):
        self.body.append(self.el(tag, content, **kw))

    def overlay(self, *parts):
        """Decorative or SMIL-driven layer. Hidden entirely under prefers-reduced-motion."""
        if not STATIC:
            self.body.append(self.el("g", "".join(parts), cls="motion"))

    def wipe(self, x, y, w, h, t=0.0, dur=1.4):
        """SMIL reveal: a card-coloured cover slides off to the right and a brand scan line follows its edge."""
        if STATIC:
            return
        begin = f"{self.at + t:.2f}s"
        ease = dict(calcMode="spline", keyTimes="0;1", keySplines=".5 0 .2 1", fill="freeze", begin=begin, dur=f"{dur}s")
        cover = self.el(
            "rect",
            self.el("animate", None, attributeName="x", from_=x, to=x + w, **ease)
            + self.el("animate", None, attributeName="width", from_=w, to=0, **ease),
            x=x, y=y, width=w, height=h, fill=CARD,
        )
        scan = self.el(
            "rect",
            self.el("animate", None, attributeName="x", from_=x - 1, to=x + w - 1, **ease)
            + self.el("animate", None, attributeName="opacity", values="0;1;1;0", keyTimes="0;0.05;0.9;1",
                      begin=begin, dur=f"{dur}s", fill="freeze"),
            x=x - 1, y=y, width=2, height=h, fill=BRAND, opacity=0,
        )
        self.overlay(cover, scan)

    def wrap(self, anim, t=0.0):
        """Put everything drawn so far inside one group that animates as a unit."""
        self.body = [self.el("g", "".join(self.body), anim=anim, t=t)]

    def text(self, x, y, s, *, font="sans", size=14, weight=400, fill=TEXT, anchor="start", anim="rise", t=0.0):
        self.used[font].update(s)
        self.add(
            "text", escape(s), cls=font, x=x, y=y, font_size=size, font_weight=weight,
            fill=fill, text_anchor=anchor, anim=anim, t=t,
        )

    def rect(self, x, y, w, h, *, rx=0, fill="none", stroke=None, anim="", t=0.0, **extra):
        attrs = {"x": x, "y": y, "width": w, "height": h, "rx": rx, "fill": fill}
        if stroke:
            attrs.update(stroke=stroke, stroke_width=1)
        self.add("rect", None, anim=anim, t=t, **attrs, **extra)

    def frame(self):
        self.rect(0.5, 0.5, self.width - 1, self.height - 1, rx=18, fill=CARD, stroke=LINE)

    def svg(self):
        faces = "".join(font_face(k, self.used[k]) for k in FONTS if self.used[k])
        css = faces + FONT_CLASSES + ("" if STATIC else ANIM_CSS)
        body = "".join(self.body)
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.width}" height="{self.height}" '
            f'viewBox="0 0 {self.width} {self.height}">'
            f"<title>{escape(self.label)}</title><style>{css}</style>{body}</svg>\n"
        )


def fmt_day(d):
    return f"{MONTHS[d.month - 1]} {d.day}"


def plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def streaks(days):
    counts = [c for _, c in days]
    i = len(counts) - 1
    if i >= 0 and counts[i] == 0:
        i -= 1  # today can still be empty while the day is in progress
    current = 0
    while i >= 0 and counts[i] > 0:
        current += 1
        i -= 1
    longest = run = 0
    for c in counts:
        run = run + 1 if c else 0
        longest = max(longest, run)
    return current, longest


def monthly(days):
    totals = {}
    for d, c in days:
        totals[(d.year, d.month)] = totals.get((d.year, d.month), 0) + c
    return [(y, m, totals[(y, m)]) for y, m in sorted(totals)[-12:]]


def levels(days):
    nonzero = sorted(c for _, c in days if c) or [1]
    cuts = [nonzero[min(len(nonzero) - 1, len(nonzero) * q // 4)] for q in (1, 2, 3)]

    def level(c):
        return 0 if not c else 1 + sum(c > cut for cut in cuts)

    return level


def build_hero(at):
    c = Card("hero", 280, f"{NAME}: {ABOUT}", at)
    c.frame()
    photo = portrait_uri()
    eyebrow_w = advance("sans", EYEBROW, 14)
    about_w = advance("sans", ABOUT, 19)
    if photo:
        cx, cy, r = 130, 140, 74
        c.add("defs", f'<clipPath id="portrait"><circle cx="{cx}" cy="{cy}" r="{r}"/></clipPath>')
        c.add("image", None, href=photo, x=cx - r, y=cy - r, width=2 * r, height=2 * r,
              preserve_aspect_ratio="xMidYMid slice", clip_path="url(#portrait)", anim="pop")
        c.add("circle", None, cx=cx, cy=cy, r=r + 4, fill="none", stroke=BRAND, stroke_width=2)
        c.overlay(c.el("circle", None, cx=cx, cy=cy, r=r + 10, fill="none", stroke=BRAND, stroke_width=1,
                       anim="ring", t=0.6))
        left, anchor, mid = 262, "start", 262
    else:
        left, anchor, mid = (W - (20 + eyebrow_w)) / 2, "middle", W / 2

    c.add("circle", None, cx=left + 4, cy=87, r=4, fill=BRAND, anim="pulse")
    c.text(left + 20, 92, EYEBROW, size=14, weight=600, fill=BRAND, anim="fade", t=0.1)
    c.text(mid, 166, NAME, font="serif", size=64, anchor=anchor, anim="slidein", t=0.25)
    c.text(mid, 208, ABOUT, size=19, fill=MUTED, anchor=anchor, anim="fade", t=0.45)

    # blinking caret after the line: six blinks, then it stays hidden (SMIL animate on the overlay layer)
    caret_x = left + about_w + 6 if photo else mid + about_w / 2 + 6
    blink = c.el("animate", None, attributeName="opacity", values="1;0", calcMode="discrete", dur="1s",
                 repeatCount="6", fill="freeze", begin=f"{at + 1.0:.2f}s")
    c.overlay(c.el("rect", blink, x=f"{caret_x:.1f}", y=193, width=2, height=18, fill=BRAND))

    # one light sweep across the card after it has landed
    shine = (
        f'<linearGradient id="shine" x1="0" x2="1" y1="0" y2="0">'
        f'<stop offset="0" stop-color="{TEXT}" stop-opacity="0"/>'
        f'<stop offset=".5" stop-color="{TEXT}" stop-opacity=".06"/>'
        f'<stop offset="1" stop-color="{TEXT}" stop-opacity="0"/></linearGradient>'
        f'<clipPath id="frame"><rect width="{W}" height="280" rx="18"/></clipPath>'
    )
    sweep = c.el("rect", None, x=0, y=-20, width=120, height=320, fill="url(#shine)", anim="sheen", t=1.6)
    c.overlay(shine, c.el("g", sweep, clip_path="url(#frame)"))
    return c


def build_tile(label, sub, at):
    c = Card(f"tile-{label}", 150, f"{label}: {sub}", at, width=280)
    c.frame()
    c.text(24, 74, label, font="serif", size=36, anim="slidein", t=0.15)
    c.text(256, 74, "→", size=24, fill=BRAND, anchor="end", anim="arrowin", t=0.35)
    c.text(24, 102, sub, size=13, fill=MUTED, anim="fade", t=0.3)
    c.rect(24, 120, 28, 3, rx=1.5, fill=BRAND, anim="growx", t=0.5)
    c.wrap("tilein")
    return c


def build_chart(days, at):
    total = sum(c for _, c in days)
    current, longest = streaks(days)
    active = sum(1 for _, c in days if c)
    busy_day, busy = max(days, key=lambda d: d[1])
    months = monthly(days)
    peak = max(v for _, _, v in months) or 1

    c = Card("chart-monthly", 344, f"Contributions per month: {total} in the last year", at)
    c.frame()
    c.text(40, 56, "contributions", font="serif", size=32, anim="rise")
    c.text(40, 80, f"last 12 months · {total:,} total", size=14, fill=MUTED, anim="fade", t=0.1)
    stats = [
        ("streak", plural(current, "day"), f"best {plural(longest, 'day')}", BRAND),
        ("active days", str(active), f"of {len(days)} days", TEXT),
        ("busiest day", fmt_day(busy_day), plural(busy, "contribution"), TEXT),
    ]
    for i, (label, value, sub, colour) in enumerate(stats):
        x = 40 + i * 280
        c.text(x, 120, label, size=13, weight=600, fill=MUTED, anim="fade", t=0.15 + i * 0.1)
        c.text(x, 158, value, font="serif", size=36, fill=colour, anim="rise", t=0.25 + i * 0.1)
        c.text(x, 180, sub, size=13, fill=MUTED, anim="fade", t=0.3 + i * 0.1)

    slot = (W - 80) / len(months)
    base, bar = 300, 34
    for i, (_, m, v) in enumerate(months):
        x = 40 + i * slot + (slot - bar) / 2
        if v:
            h = v / peak * 92
            c.rect(x, base - h, bar, h, rx=6, fill=BRAND)
            c.text(x + bar / 2, base - h - 8, str(v), size=12, fill=TEXT, anchor="middle")
        else:
            c.rect(x, base - 2, bar, 2, rx=1, fill=LINE)
        c.text(x + bar / 2, base + 22, MONTHS[m - 1], size=12, fill=MUTED, anchor="middle")
    c.wipe(40, 184, W - 80, 146, t=0.4, dur=1.4)
    return c


def build_graph(days, at):
    total = sum(c for _, c in days)
    first = days[0][0]
    start = first - dt.timedelta(days=(first.weekday() + 1) % 7)  # weeks run Sunday to Saturday
    cols = (days[-1][0] - start).days // 7 + 1
    pitch = (W - 80 + 3) / cols
    cell = pitch - 3
    y0 = 136
    ly = y0 + 6 * pitch + cell + 18  # legend row
    level = levels(days)

    c = Card("graph-contributions", int(ly + cell + 24), f"Contribution graph: {total} in the last year", at)
    c.frame()
    c.text(40, 56, "contribution graph", font="serif", size=32)
    c.text(40, 80, f"{total:,} contributions in the last year", size=14, fill=MUTED, anim="fade", t=0.1)

    def swatch(x, y, k):
        if k:
            c.rect(x, y, cell, cell, rx=2.5, fill=BRAND, fill_opacity=OPACITY[k])
        else:
            c.rect(x, y, cell, cell, rx=2.5, fill=EMPTY)

    seen = set()
    for d, count in days:
        col = (d - start).days // 7
        row = (d.weekday() + 1) % 7
        swatch(40 + col * pitch, y0 + row * pitch, level(count))
        if d.day <= 7 and (d.year, d.month) not in seen:
            seen.add((d.year, d.month))
            c.text(40 + col * pitch, y0 - 12, MONTHS[d.month - 1], size=12, fill=MUTED, anim="fade", t=0.2)

    sq_end = W - 40 - 36
    sq_start = sq_end - (5 * pitch - 3)
    c.text(sq_start - 8, ly + 10, "less", size=12, fill=MUTED, anchor="end", anim="fade", t=0.8)
    for k in range(5):
        swatch(sq_start + k * pitch, ly, k)
    c.text(W - 40, ly + 10, "more", size=12, fill=MUTED, anchor="end", anim="fade", t=0.8)

    c.wipe(40, y0, W - 80, 6 * pitch + cell, t=0.3, dur=1.6)
    return c


def build_repo(repo, blurb, at):
    name = repo["name"]
    c = Card(f"repo-{name.lower()}", 150, f"{name}: {blurb}", at)
    c.frame()
    c.text(36, 60, name, font="serif", size=34, anim="slidein")
    c.text(W - 36, 60, "→", size=22, fill=BRAND, anchor="end", anim="nudge", t=0.4)
    for i, line in enumerate(wrap(blurb, "sans", 15, W - 72, 2)):
        c.text(36, 90 + i * 21, line, size=15, fill=MUTED, anim="rise", t=0.2 + i * 0.05)
    lang = repo.get("language")
    if lang:
        c.add("circle", None, cx=41, cy=128, r=5, fill=repo.get("language_color") or MUTED, anim="pop", t=0.35)
        c.text(54, 132, lang, size=13, fill=MUTED, anim="fade", t=0.4)
    c.text(200, 132, f"{repo.get('stars', 0)} stars", size=13, fill=MUTED, anim="fade", t=0.45)
    c.text(300, 132, f"{repo.get('forks', 0)} forks", size=13, fill=MUTED, anim="fade", t=0.5)
    return c


def load():
    if not DATA.exists():
        sys.exit(f"missing {DATA.relative_to(ROOT)}: run the 'profile cards' workflow or add the file")
    raw = json.loads(DATA.read_text(encoding="utf-8"))
    days = sorted((dt.date.fromisoformat(d["date"]), int(d["count"])) for d in raw["days"])
    return raw, days


def main():
    raw, days = load()
    cards = [build_hero(0.0)]
    cards += [build_tile(label, sub, STEP + i * 0.12) for i, (label, sub) in enumerate(TILES)]
    cards.append(build_chart(days, 2 * STEP))
    cards.append(build_graph(days, 3 * STEP))

    repos = {r["name"].lower(): r for r in raw.get("repos", [])}
    for i, (name, blurb) in enumerate(FEATURED.items()):
        if name not in repos:
            print(f"warning: featured repo {name} not in {DATA.name}, skipped", file=sys.stderr)
            continue
        cards.append(build_repo(repos[name], blurb, (4 + i) * STEP))

    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.svg"):
        old.unlink()
    for card in cards:
        (OUT / f"{card.name}.svg").write_text(card.svg(), encoding="utf-8", newline="\n")
    mode = " (STATIC final frame)" if STATIC else ""
    print(f"wrote {len(cards)} cards to {OUT.relative_to(ROOT)}/{mode}")


if __name__ == "__main__":
    main()
