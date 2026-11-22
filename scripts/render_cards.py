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
ANIM_CSS = (
    ".rise{animation:rise .7s cubic-bezier(.2,.7,.2,1) both;animation-delay:var(--d,0s)}"
    ".pop{transform-box:fill-box;transform-origin:center;animation:pop .45s cubic-bezier(.2,.8,.2,1) both;animation-delay:var(--d,0s)}"
    ".grow{transform-box:fill-box;transform-origin:50% 100%;animation:grow .9s cubic-bezier(.2,.7,.2,1) both;animation-delay:var(--d,0s)}"
    ".growx{transform-box:fill-box;transform-origin:0 50%;animation:growx .8s cubic-bezier(.2,.7,.2,1) both;animation-delay:var(--d,0s)}"
    ".ping{animation:ping 2.4s ease-in-out infinite}"
    "@keyframes rise{from{opacity:0;transform:translateY(12px)}}"
    "@keyframes pop{from{opacity:0;transform:scale(.3)}}"
    "@keyframes grow{from{transform:scaleY(0)}}"
    "@keyframes growx{from{transform:scaleX(0)}}"
    "@keyframes ping{50%{opacity:.35}}"
    "@media (prefers-reduced-motion:reduce){*{animation:none!important}}"
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
        pairs = "".join(
            f' {k.replace("_", "-")}="{escape(str(v), {chr(34): "&quot;"})}"' for k, v in attrs.items()
        )
        if content is None:
            return f"<{tag}{pairs}/>"
        return f"<{tag}{pairs}>{content}</{tag}>"

    def add(self, tag, content=None, **kw):
        self.body.append(self.el(tag, content, **kw))

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
    if photo:
        cx, cy, r = 130, 140, 74
        c.add("defs", f'<clipPath id="portrait"><circle cx="{cx}" cy="{cy}" r="{r}"/></clipPath>')
        c.add("image", None, href=photo, x=cx - r, y=cy - r, width=2 * r, height=2 * r,
              preserve_aspect_ratio="xMidYMid slice", clip_path="url(#portrait)", anim="pop")
        c.add("circle", None, cx=cx, cy=cy, r=r + 4, fill="none", stroke=BRAND, stroke_width=2)
        left, anchor, mid = 262, "start", 262
    else:
        left, anchor, mid = (W - (20 + eyebrow_w)) / 2, "middle", W / 2
    c.add("circle", None, cx=left + 4, cy=87, r=4, fill=BRAND, anim="ping")
    c.text(left + 20, 92, EYEBROW, size=14, weight=600, fill=BRAND, t=0.1)
    c.text(mid, 166, NAME, font="serif", size=64, anchor=anchor, t=0.25)
    c.text(mid, 208, ABOUT, size=19, fill=MUTED, anchor=anchor, t=0.45)
    return c


def build_tile(label, sub, at):
    c = Card(f"tile-{label}", 150, f"{label}: {sub}", at, width=280)
    c.frame()
    c.text(24, 74, label, font="serif", size=36, t=0.1)
    c.text(256, 74, "→", size=24, fill=BRAND, anchor="end", t=0.2)
    c.text(24, 102, sub, size=13, fill=MUTED, t=0.25)
    c.rect(24, 120, 28, 3, rx=1.5, fill=BRAND, anim="growx", t=0.35)
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
    c.text(40, 56, "contributions", font="serif", size=32)
    c.text(40, 80, f"last 12 months · {total:,} total", size=14, fill=MUTED, t=0.1)
    stats = [
        ("streak", plural(current, "day"), f"best {plural(longest, 'day')}", BRAND),
        ("active days", str(active), f"of {len(days)} days", TEXT),
        ("busiest day", fmt_day(busy_day), plural(busy, "contribution"), TEXT),
    ]
    for i, (label, value, sub, colour) in enumerate(stats):
        x = 40 + i * 280
        c.text(x, 120, label, size=13, weight=600, fill=MUTED, t=0.15 + i * 0.1)
        c.text(x, 158, value, font="serif", size=36, fill=colour, t=0.2 + i * 0.1)
        c.text(x, 180, sub, size=13, fill=MUTED, t=0.25 + i * 0.1)

    slot = (W - 80) / len(months)
    base, bar = 300, 34
    for i, (_, m, v) in enumerate(months):
        x = 40 + i * slot + (slot - bar) / 2
        t = 0.4 + i * 0.05
        if v:
            h = v / peak * 92
            c.rect(x, base - h, bar, h, rx=6, fill=BRAND, anim="grow", t=t)
            c.text(x + bar / 2, base - h - 8, str(v), size=12, fill=TEXT, anchor="middle", t=t + 0.3)
        else:
            c.rect(x, base - 2, bar, 2, rx=1, fill=LINE)
        c.text(x + bar / 2, base + 22, MONTHS[m - 1], size=12, fill=MUTED, anchor="middle", t=t)
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
    c.text(40, 80, f"{total:,} contributions in the last year", size=14, fill=MUTED, t=0.1)

    def swatch(x, y, k, t):
        if k:
            c.rect(x, y, cell, cell, rx=2.5, fill=BRAND, fill_opacity=OPACITY[k], anim="pop", t=t)
        else:
            c.rect(x, y, cell, cell, rx=2.5, fill=EMPTY, anim="pop", t=t)

    seen = set()
    for d, count in days:
        col = (d - start).days // 7
        row = (d.weekday() + 1) % 7
        t = 0.2 + col * 0.018
        swatch(40 + col * pitch, y0 + row * pitch, level(count), t)
        if d.day <= 7 and (d.year, d.month) not in seen:
            seen.add((d.year, d.month))
            c.text(40 + col * pitch, y0 - 12, MONTHS[d.month - 1], size=12, fill=MUTED, t=t)

    sq_end = W - 40 - 36
    sq_start = sq_end - (5 * pitch - 3)
    c.text(sq_start - 8, ly + 10, "less", size=12, fill=MUTED, anchor="end", t=0.9)
    for k in range(5):
        swatch(sq_start + k * pitch, ly, k, 0.9)
    c.text(W - 40, ly + 10, "more", size=12, fill=MUTED, anchor="end", t=0.9)
    return c


def build_repo(repo, blurb, at):
    name = repo["name"]
    c = Card(f"repo-{name.lower()}", 150, f"{name}: {blurb}", at)
    c.frame()
    c.text(36, 60, name, font="serif", size=34)
    c.text(W - 36, 60, "→", size=22, fill=BRAND, anchor="end", t=0.1)
    for i, line in enumerate(wrap(blurb, "sans", 15, W - 72, 2)):
        c.text(36, 90 + i * 21, line, size=15, fill=MUTED, t=0.2 + i * 0.05)
    lang = repo.get("language")
    if lang:
        c.add("circle", None, cx=41, cy=128, r=5, fill=repo.get("language_color") or MUTED, anim="pop", t=0.35)
        c.text(54, 132, lang, size=13, fill=MUTED, t=0.35)
    c.text(200, 132, f"{repo.get('stars', 0)} stars", size=13, fill=MUTED, t=0.4)
    c.text(300, 132, f"{repo.get('forks', 0)} forks", size=13, fill=MUTED, t=0.4)
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
