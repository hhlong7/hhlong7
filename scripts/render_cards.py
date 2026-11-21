#!/usr/bin/env python3
"""Render the profile README cards as animated SVGs into cards/.

Each card is shown through an <img> tag on github.com, so the SVGs only use
what works there: one inline <style> block (CSS keyframes), SMIL <animate>
elements, and fonts subset with pyftsubset and embedded as base64 WOFF data URIs.

  python scripts/render_cards.py            -> cards/*.svg (animated, follows light/dark mode)
  STATIC=1 python scripts/render_cards.py   -> cards-static/*.svg + preview.html
                                               (final frame, no animation, for screenshots)
  THEME=light|dark                          -> force one palette instead of following the browser

Animations are written so the *natural* style is the final frame: keyframes
only declare the "from" state. Turning animation off (STATIC=1, or
prefers-reduced-motion) therefore always shows the finished card.
SMIL elements are purely decorative and carry class "smil", which the
reduced-motion media query hides (SMIL itself cannot be switched off by CSS).

Data: data/contributions.json (refreshed daily by scripts/fetch_contributions.py).
"""
import base64
import datetime as dt
import functools
import html
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
STATIC = os.environ.get("STATIC") == "1"
OUT = ROOT / ("cards-static" if STATIC else "cards")
DATA = ROOT / "data" / "contributions.json"
FONT_DIR = ROOT / "scripts" / "fonts"

# ---------------------------------------------------------------------------
# Palettes
# ---------------------------------------------------------------------------
PALETTES = {
    # dark: GitHub dark background, gold accent
    "dark": dict(card="#0E1116", border="#30363D", accent="#D4A359",
                 text="#E6EDF3", muted="#8B949E", empty="#161B22", repo="#D4A359"),
    # light: white with the GitHub-style green
    "light": dict(card="#FFFFFF", border="#D1D9E0", accent="#428646",
                  text="#1F2328", muted="#59636E", empty="#EBEDF0", repo="#428646"),
}
C = dict(PALETTES["dark"])
WHITE = "#FFFFFF"
# project cards use the same cream in light and dark mode
CREAM, CREAM_LINE = "#F7F5EE", "#E3DFD2"
REPO_INK, REPO_MUTED = "#1F2328", "#59636E"  # accent on these cards is C["repo"]


def use_palette(name):
    C.clear()
    C.update(PALETTES[name])


# THEME=auto (default): follow the browser/OS via prefers-color-scheme.
# THEME=dark or THEME=light forces one palette (handy for screenshots).
THEME = os.environ.get("THEME", "auto")

# key -> (css family, weight, style, file, fallback stack)
FONTS = {
    "d7": ("Instrument Serif", 400, "normal", "serif-400.woff", "Georgia,serif"),
    "d7i": ("Instrument Serif", 400, "italic", "serif-400-italic.woff", "Georgia,serif"),
    "m4": ("Geist Mono", 400, "normal", "geist-mono-400.woff", "ui-monospace,monospace"),
    "m5": ("Geist Mono", 500, "normal", "geist-mono-500.woff", "ui-monospace,monospace"),
    "b4": ("Inter", 400, "normal", "inter-400.woff", "system-ui,sans-serif"),
    "b5": ("Inter", 500, "normal", "inter-500.woff", "system-ui,sans-serif"),
}

# ---------------------------------------------------------------------------
# Content: edit here
# ---------------------------------------------------------------------------
ABOUT = dict(
    label="ABOUT ME",
    line1="I build at the boundary of",
    line2="hardware and software.",
    body="Third-year Electrical Engineering student at Cal Poly SLO (class of 2028) with a CS minor. "
         "I work on CPU cores, accelerators, and the schedulers that feed them.",
    links=[("Portfolio", "hhlong7.github.io"),
           ("LinkedIn", "in/longhh605"),
           ("Email", "hhlong605@gmail.com")],
)

REPOS = [
    dict(file="repo-rv32imacz", repo="hhlong7/RV32IMACZ", title="RV32IMACZ",
         label="SYSTEMVERILOG / OUT-OF-ORDER CPU", motif="ooo",
         desc="Two-wide out-of-order RISC-V core with Tomasulo scheduling and a 16-entry reorder buffer. "
              "Runs FreeRTOS and CoreMark on a Zybo Z7-20 FPGA."),
    dict(file="repo-chillitpu", repo="hhlong7/chilliTPU", title="chilliTPU",
         label="SYSTEMVERILOG / UVM / ACCELERATOR", motif="systolic",
         desc="Multi-tenant INT8 systolic array accelerator with a dynamic partition controller, "
              "verified with UVM and prototyped on a Zybo Z7-20."),
    dict(file="repo-scheduler", repo="hhlong7/ai-accelerator", title="Contention-Aware Task Scheduler",
         label="PYTHON / ILP / SOC SCHEDULING", motif="gantt",
         desc="Models DRAM contention between CPU cores and an accelerator, and is compared "
              "against FIFO, HEFT, Qilin and ESHCS in a simulator."),
    dict(file="repo-kvcache", repo="hhlong7/llm-research-mac", title="KV-Cache Research",
         label="PYTHON / LLM INFERENCE RESEARCH", motif="kv",
         desc="Research on LLM inference memory and KV-cache behavior, with GPT-2 and Qwen2.5 "
              "experiments and a paper reading list."),
]

# Staggered start times (seconds) so the page plays top to bottom.
T = dict(about=0.0, contrib=0.45, langs=0.6, graph=1.0, repos=(1.7, 1.85, 2.0, 2.15))

INS = 5  # transparent gutter around every card, so cards sit 10px apart with no CSS

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
_missing = set()
_font_cache = {}


def _font(k):
    if k not in _font_cache:
        t = TTFont(FONT_DIR / FONTS[k][3])
        _font_cache[k] = (t.getBestCmap(), t["hmtx"], t["head"].unitsPerEm)
    return _font_cache[k]


def tw(k, s, size, ls=0.0):
    """Approximate rendered width of s (no kerning)."""
    cmap, hm, upm = _font(k)
    w = 0
    for ch in s:
        g = cmap.get(ord(ch))
        if g is None:
            _missing.add((k, ch))
            continue
        w += hm[g][0]
    return w * size / upm + ls * len(s)


def wrap(k, s, size, maxw):
    lines, cur = [], ""
    for word in s.split():
        trial = (cur + " " + word).strip()
        if tw(k, trial, size) <= maxw or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def esc(s):
    return html.escape(s, quote=True)


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def mix(a, b, t):
    ra, rb = hex_rgb(a), hex_rgb(b)
    return "#%02x%02x%02x" % tuple(round(x + (y - x) * t) for x, y in zip(ra, rb))


def fmt(n):
    return f"{n:g}" if isinstance(n, float) else str(n)


def subset_b64(key, chars):
    return _subset_b64(key, "".join(sorted(chars | {" "})))


@functools.lru_cache(maxsize=None)
def _subset_b64(key, text):
    fname = FONTS[key][3]
    with tempfile.TemporaryDirectory() as td:
        tf = Path(td) / "t.txt"
        tf.write_text(text, encoding="utf-8")
        out = Path(td) / "o.woff"
        subprocess.run(
            [sys.executable, "-m", "fontTools.subset", str(FONT_DIR / fname),
             f"--text-file={tf}", "--flavor=woff", "--layout-features=kern,liga",
             "--no-hinting", f"--output-file={out}"],
            check=True, capture_output=True)
        return base64.b64encode(out.read_bytes()).decode()


KEYFRAMES = """
@keyframes fu{from{opacity:0;transform:translateY(8px)}}
@keyframes fi{from{opacity:0}}
@keyframes gr{from{transform:scaleY(0)}}
@keyframes sc{from{opacity:0;transform:scale(.3)}}
@keyframes dr{from{stroke-dashoffset:1}}
@keyframes rd{from{transform:scale(0)}}
.fu{animation:fu .7s cubic-bezier(.2,.7,.2,1) backwards}
.fi{animation:fi .8s ease-out backwards}
.gr{transform-box:fill-box;transform-origin:50% 100%;animation:gr .8s cubic-bezier(.2,.7,.2,1) backwards}
.sc{transform-box:fill-box;transform-origin:50% 50%;animation:sc .45s ease-out backwards}
.dr{animation:dr 1.1s ease-out backwards}
.rd{animation:rd 1s cubic-bezier(.2,.8,.2,1) backwards}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}.smil{display:none}}
"""


class Card:
    """A card with outer size (ow x oh); the drawn card is (ow-2*INS) x (oh-2*INS)."""

    def __init__(self, name, ow, oh, base, label):
        self.name, self.ow, self.oh, self.base, self.label = name, ow, oh, base, label
        self.w, self.h = ow - 2 * INS, oh - 2 * INS
        self.body, self.defs = [], []
        self.chars = {k: set() for k in FONTS}
        self.uid = name.replace("-", "")

    # -- animation attributes -------------------------------------------------
    def an(self, kind, delay=0.0):
        if STATIC or not kind:
            return "", ""
        return f" {kind}", f' style="animation-delay:{self.base + delay:.2f}s"'

    def add(self, s):
        self.body.append(s)

    def frame(self, fill=None, stroke=None):
        r = 14
        self.defs.append(f'<clipPath id="{self.uid}c"><rect width="{self.w}" height="{self.h}" rx="{r}"/></clipPath>')
        cls, sty = self.an("fi", 0)
        ca = f' class="{cls.strip()}"' if cls else ""
        self.add(f'<rect x=".5" y=".5" width="{self.w - 1}" height="{self.h - 1}" rx="{r}" '
                 f'fill="{fill or C["card"]}" stroke="{stroke or C["border"]}"{ca}{sty}/>')

    def text(self, x, y, s, font, size, fill, anchor="start", ls=0.0, anim="fu", delay=0.0):
        self.chars[font].update(s)
        cls, sty = self.an(anim, delay)
        lsa = f' letter-spacing="{ls}"' if ls else ""
        self.add(f'<text x="{fmt(round(x, 2))}" y="{fmt(round(y, 2))}" class="{font}{cls}" font-size="{size}" '
                 f'fill="{fill}" text-anchor="{anchor}"{lsa}{sty}>{esc(s)}</text>')

    def runs(self, x, y, parts, size, anim="fu", delay=0.0):
        """parts: (text, font, fill, gap_after_px). Returns the x after the last part."""
        for s, font, fill, gap in parts:
            self.text(x, y, s, font, size, fill, anim=anim, delay=delay)
            x += tw(font, s, size) + gap
        return x

    def el(self, tag, attrs, anim=None, delay=0.0, inner=""):
        cls, sty = self.an(anim, delay)
        a = " ".join(f'{k}="{v}"' for k, v in attrs.items())
        c = f' class="{cls.strip()}"' if cls else ""
        end = f">{inner}</{tag}>" if inner else "/>"
        self.add(f"<{tag} {a}{c}{sty}{end}")

    def render(self):
        faces, rules = [], []
        for k, chars in self.chars.items():
            if not chars:
                continue
            fam, wt, style, _, fb = FONTS[k]
            b64 = subset_b64(k, chars)
            faces.append(f"@font-face{{font-family:'{fam}';font-weight:{wt};font-style:{style};"
                         f"src:url(data:font/woff;base64,{b64}) format('woff')}}")
            rules.append(f".{k}{{font-family:'{fam}',{fb};font-weight:{wt};font-style:{style}}}")
        css = "".join(faces) + "".join(rules)
        if not STATIC:
            css += KEYFRAMES
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.ow}" height="{self.oh}" '
                f'viewBox="{-INS} {-INS} {self.ow} {self.oh}" role="img" aria-label="{esc(self.label)}">'
                f'<title>{esc(self.label)}</title><style>{css}</style>'
                f'<defs>{"".join(self.defs)}</defs>'
                f'<g clip-path="url(#{self.uid}c)">{"".join(self.body)}</g></svg>')


def smil(card, animated, static=""):
    """Decorative SMIL: animated in normal mode, a fixed frame in STATIC mode."""
    card.add(static if STATIC else animated)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def load_data():
    d = json.loads(DATA.read_text())
    if d.get("sample"):
        print("WARNING: data/contributions.json is SAMPLE data; run the Action (or "
              "scripts/fetch_contributions.py) to use real numbers.", file=sys.stderr)
    days = sorted((dt.date.fromisoformat(x["date"]), x["count"]) for x in d["days"])
    cdays = sorted((dt.date.fromisoformat(x["date"]), x.get("commits", x["count"])) for x in d["days"])
    langs = sorted(d.get("languages", []), key=lambda x: -x["bytes"])
    end = days[-1][0]
    by_date = dict(days)

    months = []
    y, m = end.year, end.month
    for _ in range(12):
        months.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    months.reverse()
    monthly = [sum(c for (dd, c) in days if (dd.year, dd.month) == ym) for ym in months]

    # current streak: consecutive active days ending today (or yesterday if today is empty)
    cur, day = 0, end
    if by_date.get(day, 0) == 0:
        day -= dt.timedelta(days=1)
    while by_date.get(day, 0) > 0:
        cur += 1
        day -= dt.timedelta(days=1)
    longest = run = 0
    for _, c in days:
        run = run + 1 if c > 0 else 0
        longest = max(longest, run)
    active = sum(1 for _, c in days if c > 0)
    busiest_date, busiest = max(days, key=lambda x: (x[1], x[0]))[0], max(c for _, c in days)
    bd = d["breakdown"]
    total_b = sum(bd.values()) or 1
    pct = {k: round(100 * v / total_b) for k, v in bd.items()}
    return dict(days=days, cdays=cdays, langs=langs, end=end, months=months, monthly=monthly, streak=cur, longest=longest,
                active=active, ndays=len(days), busiest=busiest, busiest_date=busiest_date,
                pct=pct, total=d.get("total", sum(c for _, c in days)))


GRAPH_TITLE = "Contributions"
MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ---------------------------------------------------------------------------
# Cards
# ---------------------------------------------------------------------------
def stat_block(c, x, top, big_parts, caption, rule_to=None, delay=0.0):
    """Reference-style stat: big serif number, small sans caption, hairline below."""
    c.runs(x, top + 32, big_parts, 36, delay=delay)
    c.text(x, top + 50, caption, "b4", 11.5, C["muted"], delay=delay + 0.05)
    if rule_to:
        c.el("line", dict(x1=x, x2=rule_to, y1=top + 60, y2=top + 60, stroke=C["border"]), "fi", delay + 0.1)


def card_about():
    c = Card("about", 880, 218, T["about"], "About me: " + ABOUT["body"])
    c.frame()
    L = 28
    c.text(L, 32, ABOUT["label"], "m5", 10.5, C["muted"], ls=1.6, delay=0.0)
    ink, mut = C["text"], C["muted"]
    c.text(L, 88, ABOUT["line1"], "d7", 46, ink, delay=0.1)
    c.text(L, 134, ABOUT["line2"], "d7i", 46, ink, delay=0.2)
    for j, ln in enumerate(wrap("b4", ABOUT["body"], 13.5, 520)):
        c.text(L, 168 + j * 20, ln, "b4", 13.5, mut, delay=0.3 + j * 0.05)
    x0, x1 = 612, c.w - 28
    for i, (title, handle) in enumerate(ABOUT["links"]):
        top = 26 + i * 60
        c.text(x0, top + 30, title, "d7", 32, ink, delay=0.2 + i * 0.1)
        c.text(x0, top + 48, handle, "b4", 12.5, mut, delay=0.25 + i * 0.1)
        if i < len(ABOUT["links"]) - 1:
            c.el("line", dict(x1=x0, x2=x1, y1=top + 57, y2=top + 57, stroke=C["border"]), "fi", 0.3 + i * 0.1)
    return c


def card_contrib(d):
    c = Card("contributions", 640, 240, T["contrib"], "Contributions per month")
    c.frame()
    ink, mut, acc = C["text"], C["muted"], C["accent"]
    c.text(24, 30, "THE LAST 12 MONTHS ON GITHUB", "m5", 10, mut, ls=1.4, delay=0.0)
    c.runs(24, 84, [(f'{d["total"]:,}', "d7", ink, 10), ("contributions", "d7i", ink, 0)], 54, delay=0.1)

    # bars
    x0, x1, y0, y1 = 24, 410, 112, 196
    top = max(d["monthly"] + [1])
    hi = d["monthly"].index(max(d["monthly"]))
    slot = (x1 - x0) / 12
    bw = slot * 0.64
    for i, v in enumerate(d["monthly"]):
        bx = x0 + i * slot + (slot - bw) / 2
        h = max((y1 - y0) * v / top, 2)
        fill = acc if i == hi else (mix(C["card"], ink, 0.22) if i == 11 else mix(C["card"], ink, 0.78))
        c.el("rect", dict(x=round(bx, 1), y=round(y1 - h, 1), width=round(bw, 1), height=round(h, 1),
                          rx=4, fill=fill), "gr", 0.3 + i * 0.05)
        c.text(bx + bw / 2, y1 + 18, MONTH[d["months"][i][1] - 1], "m4", 10, mut, "middle", delay=0.3 + i * 0.03)
        if i == hi:
            c.text(bx + bw / 2, y1 - h - 7, f"{v:,}", "m4", 11, acc, "middle", delay=0.7)

    # stat column
    sx, ex = 446, c.w - 24
    same = d["streak"] == d["longest"]
    stat_block(c, sx, 20, [(str(d["streak"]), "d7", ink, 7), ("days" if d["streak"] != 1 else "day", "d7i", ink, 0)],
               "current streak, and the longest" if same else f'current streak, longest {d["longest"]}',
               rule_to=ex, delay=0.15)
    stat_block(c, sx, 82, [(str(d["active"]), "d7", ink, 8), ("/", "d7", mut, 8), (str(d["ndays"]), "d7", mut, 0)],
               "days with a contribution", rule_to=ex, delay=0.25)
    bd = d["busiest_date"]
    stat_block(c, sx, 144, [(str(d["busiest"]), "d7", ink, 0)],
               f"on the busiest day, {MONTH[bd.month - 1]} {bd.day}", delay=0.35)
    return c


def card_languages(d):
    c = Card("languages", 240, 240, T["langs"], "Most used languages")
    c.frame()
    ink, mut, acc = C["text"], C["muted"], C["accent"]
    langs = d["langs"]
    assert langs, "no language data in data/contributions.json"
    total = sum(x["bytes"] for x in langs) or 1
    top = langs[:5]
    P = 22
    c.text(P, 30, "MOST USED LANGUAGES", "m5", 10, mut, ls=1.4, delay=0)
    size = 36
    while tw("d7", top[0]["name"], size) > c.w - 2 * P and size > 20:
        size -= 1
    c.text(P, 74, top[0]["name"], "d7", size, ink, delay=0.1)
    c.runs(P, 95, [(f'{round(100 * top[0]["bytes"] / total)}%', "m5", acc, 6), ("of the code you write", "b4", mut, 0)],
           11.5, delay=0.15)
    shades = [mix(C["card"], acc, t) for t in (1.0, 0.75, 0.55, 0.38, 0.24)]
    bx, by, bw, bh = P, 108, c.w - 2 * P, 8
    c.defs.append(f'<clipPath id="{c.uid}b"><rect x="{bx}" y="{by}" width="{bw}" height="{bh}" rx="4"/></clipPath>')
    c.el("rect", dict(x=bx, y=by, width=bw, height=bh, rx=4, fill=C["empty"]), "fi", 0.2)
    segs, x = [], bx
    for i, lg in enumerate(top):
        w = bw * lg["bytes"] / total
        segs.append(f'<rect x="{x:.1f}" y="{by}" width="{max(w - 1.5, 1):.1f}" height="{bh}" fill="{shades[i]}"/>')
        x += w
    cls, sty = c.an("fi", 0.3)
    c.add(f'<g clip-path="url(#{c.uid}b)"{(" class=" + chr(34) + cls.strip() + chr(34)) if cls else ""}{sty}>{"".join(segs)}</g>')
    for i, lg in enumerate(top):
        y = 138 + i * 20
        c.el("rect", dict(x=P, y=y - 8, width=8, height=8, rx=2.5, fill=shades[i]), "sc", 0.35 + i * 0.06)
        c.text(P + 16, y, lg["name"], "b4", 12.5, ink, delay=0.35 + i * 0.06)
        p = 100 * lg["bytes"] / total
        c.text(c.w - P, y, f"{p:.0f}%" if p >= 1 else "<1%", "m4", 11, mut, "end", delay=0.4 + i * 0.06)
    return c


def motif(c, kind, mx, my, bg=CREAM, fg=None):
    """Small line-art on the cream project cards, in the card accent colour."""
    fg = fg or C["repo"]
    line, dim, mid, soft = (mix(bg, fg, t) for t in (0.55, 0.14, 0.30, 0.55))
    if kind == "ooo":
        for i, (dur, off) in enumerate(((2.4, 0.0), (3.1, 0.7), (2.0, 0.3))):
            y = my + 4 + i * 20
            c.el("rect", dict(x=mx, y=y, width=84, height=14, rx=4, fill=dim, stroke=line), "fi", 0.4 + i * 0.1)
            c.el("rect", dict(x=mx + 66, y=y, width=18, height=14, rx=4, fill=mid, stroke=line), "fi", 0.4 + i * 0.1)
            smil(c, f'<circle class="smil" cx="{mx + 8}" cy="{y + 7}" r="3" fill="{fg}">'
                    f'<animate attributeName="cx" values="{mx + 8};{mx + 72}" dur="{dur}s" begin="{off}s" repeatCount="indefinite"/>'
                    f'<animate attributeName="opacity" values="0;1;1;0" keyTimes="0;.1;.85;1" dur="{dur}s" begin="{off}s" repeatCount="indefinite"/></circle>',
                 f'<circle cx="{mx + [20, 46, 34][i]}" cy="{y + 7}" r="3" fill="{fg}"/>')
    elif kind == "systolic":
        for r in range(5):
            for q in range(5):
                x, y = mx + 14 + q * 12, my + r * 12
                c.el("rect", dict(x=x, y=y, width=9, height=9, rx=2, fill=fg, opacity=".28"), "sc", 0.4 + (r + q) * 0.04)
                d = r + q
                smil(c, f'<rect class="smil" x="{x}" y="{y}" width="9" height="9" rx="2" fill="{fg}" opacity="0">'
                        f'<animate attributeName="opacity" values="0;.95;0" dur="2.2s" begin="{c.base + 0.8 + d * 0.18:.2f}s" repeatCount="indefinite"/></rect>',
                     f'<rect x="{x}" y="{y}" width="9" height="9" rx="2" fill="{fg}" opacity="{.95 if d == 4 else 0}"/>')
    elif kind == "gantt":
        bars = [(0, 0, 30, True), (34, 0, 22, False), (60, 0, 20, True),
                (6, 1, 24, False), (36, 1, 34, True),
                (0, 2, 18, True), (22, 2, 20, False), (48, 2, 30, True)]
        for i, (bx, lane, w, cpu) in enumerate(bars):
            c.el("rect", dict(x=mx + bx, y=my + 4 + lane * 20, width=w, height=14, rx=3,
                              fill=fg if cpu else soft, opacity=".92"), "fu", 0.4 + i * 0.05)
        smil(c, f'<rect class="smil" x="{mx}" y="{my}" width="2" height="58" fill="{fg}">'
                f'<animate attributeName="x" values="{mx};{mx + 84}" dur="4s" begin="{c.base + 0.9:.2f}s" repeatCount="indefinite"/></rect>',
             f'<rect x="{mx + 52}" y="{my}" width="2" height="58" fill="{fg}"/>')
    elif kind == "kv":
        slots = [(q, r) for r in range(3) for q in range(6)]
        for i, (q, r) in enumerate(slots):
            x, y = mx + q * 14, my + 4 + r * 20
            filled = i < 10
            c.el("rect", dict(x=x, y=y, width=11, height=14, rx=3, fill=fg if filled else "none",
                              opacity=".6" if filled else "1", stroke="none" if filled else line),
                 "sc", 0.4 + i * 0.03)
        xs = ";".join(str(mx + q * 14) for q, r in slots[10:] + slots[10:11])
        ys = ";".join(str(my + 4 + r * 20) for q, r in slots[10:] + slots[10:11])
        smil(c, f'<rect class="smil" x="{mx + 4 * 14}" y="{my + 24}" width="11" height="14" rx="3" fill="{fg}">'
                f'<animate attributeName="x" values="{xs}" calcMode="discrete" dur="3.2s" begin="{c.base + 0.9:.2f}s" repeatCount="indefinite"/>'
                f'<animate attributeName="y" values="{ys}" calcMode="discrete" dur="3.2s" begin="{c.base + 0.9:.2f}s" repeatCount="indefinite"/></rect>',
             f'<rect x="{mx + 4 * 14}" y="{my + 24}" width="11" height="14" rx="3" fill="{fg}"/>')


def card_repo(i, r):
    c = Card(r["file"], 440, 184, T["repos"][i], f'{r["title"]}: {r["desc"]}')
    c.frame(fill=CREAM, stroke=CREAM_LINE)
    P = 22
    c.text(P, 30, r["label"], "m5", 9.5, REPO_MUTED, ls=1.3, delay=0)
    maxw = c.w - 2 * P - 100
    size = 30
    while tw("d7", r["title"], size) > maxw and size > 20:
        size -= 1
    c.text(P, 30 + 8 + size * 0.78, r["title"], "d7", size, REPO_INK, delay=0.08)
    y_desc = 30 + 8 + size * 0.78 + 31
    lines = wrap("b4", r["desc"], 13, c.w - 2 * P)
    assert len(lines) <= 3, f'{r["file"]}: description wraps to {len(lines)} lines'
    for j, ln in enumerate(lines):
        c.text(P, y_desc + j * 18, ln, "b4", 13, REPO_INK, delay=0.16 + j * 0.05)
    motif(c, r["motif"], c.w - P - 84, 14)
    ry = c.h - 30
    c.el("line", dict(x1=P, x2=c.w - P, y1=ry, y2=ry, stroke=CREAM_LINE), "fi", 0.3)
    c.text(P, ry + 19, "github.com/" + r["repo"], "m4", 10.5, REPO_MUTED, delay=0.4)
    ax = c.w - P - 11
    c.el("path", dict(d=f"M{ax} {ry + 9} L{ax + 10} {ry + 9} L{ax + 10} {ry + 19} M{ax + 10} {ry + 9} L{ax} {ry + 19}",
                      fill="none", stroke=C["repo"],
                      **{"stroke-width": 1.6, "stroke-linecap": "round", "stroke-linejoin": "round"}), "fi", 0.45)
    return c


def card_graph(d):
    """Reference-style 'Every day' graph, one square per day, shaded by commits."""
    c = Card("graph", 880, 232, T["graph"], "Commits per day over the last year")
    c.frame()
    ink, mut, acc = C["text"], C["muted"], C["accent"]
    days = d["cdays"]
    first = days[0][0]
    start = first - dt.timedelta(days=(first.weekday() + 1) % 7)  # Sunday on/before first day
    ncols = ((days[-1][0] - start).days // 7) + 1
    L = 24
    pitch = (c.w - 2 * L) / ncols
    cell = pitch * 0.8
    top = 78
    nz = sorted(cnt for _, cnt in days if cnt > 0) or [1]
    qs = [nz[int(len(nz) * f)] for f in (0.25, 0.5, 0.75)]
    shades = [C["empty"]] + [mix(C["card"], acc, t) for t in (0.3, 0.52, 0.76, 1.0)]
    c.text(L, 52, GRAPH_TITLE, "d7", 40, ink, delay=0)
    c.text(c.w - L, 46, "REFRESHED DAILY", "m4", 10.5, mut, "end", ls=1.6, delay=0.05)
    for dd, cnt in days:
        col, row = (dd - start).days // 7, (dd.weekday() + 1) % 7
        lvl = 0 if cnt == 0 else 1 + sum(cnt > q for q in qs)
        c.el("rect", dict(x=round(L + col * pitch, 1), y=round(top + row * pitch, 1), width=round(cell, 1),
                          height=round(cell, 1), rx=3.5, fill=shades[lvl]), "sc", 0.1 + col * 0.012)
    # legend (bottom right), month labels (bottom left, skipped where the legend sits)
    ly = top + 7 * pitch + 12
    step = cell + 3.5
    lx = c.w - L - 5 * step - tw("m4", "more", 11) - 8
    c.text(lx - 8, ly + cell - 1, "less", "m4", 11, mut, "end", delay=0.5)
    for i, sh in enumerate(shades):
        c.el("rect", dict(x=round(lx + i * step, 1), y=round(ly, 1), width=round(cell, 1), height=round(cell, 1),
                          rx=3.5, fill=sh), "sc", 0.5 + i * 0.05)
    c.text(lx + 5 * step + 4, ly + cell - 1, "more", "m4", 11, mut, "start", delay=0.5)
    legend_left = lx - 8 - tw("m4", "less", 11) - 10
    last_x = -99
    for col in range(ncols):
        dd = start + dt.timedelta(days=col * 7)
        for k in range(7):
            e = dd + dt.timedelta(days=k)
            if e.day == 1 and e >= days[0][0]:
                x = L + col * pitch
                if x - last_x > 38 and x + tw("m4", MONTH[e.month - 1], 11) < legend_left:
                    c.text(x, ly + cell - 1, MONTH[e.month - 1], "m4", 11, mut, delay=0.1 + col * 0.012)
                    last_x = x
                break
    return c


# ---------------------------------------------------------------------------
COLOR_ATTR = re.compile(r'(fill|stroke|stop-color)="(#[0-9a-fA-F]{6})"')


def build_all(d):
    cards = [card_about(), card_contrib(d), card_languages(d), card_graph(d)]
    cards += [card_repo(i, r) for i, r in enumerate(REPOS)]
    return cards


def main():
    """Write one SVG per card per theme: <name>-dark.svg and <name>-light.svg.

    GitHub picks between them with #gh-dark-mode-only / #gh-light-mode-only in the
    README, which follows GitHub's own theme setting (prefers-color-scheme inside an
    <img> SVG follows the OS instead, which is why we don't rely on it).
    """
    OUT.mkdir(exist_ok=True)
    d = load_data()
    themes = [THEME] if THEME in ("dark", "light") else ["dark", "light"]
    written = set()
    for theme in themes:
        use_palette(theme)
        for c in build_all(d):
            svg = c.render()
            fn = f"{c.name}-{theme}.svg"
            (OUT / fn).write_text(svg, encoding="utf-8")
            written.add(fn)
            print(f"{fn}  {len(svg) / 1024:.1f} KB")
    for old in OUT.glob("*.svg"):  # drop cards from older layouts
        if old.name not in written and THEME not in ("dark", "light"):
            old.unlink()
    if _missing:
        print("WARNING: glyphs missing from fonts:", sorted(_missing), file=sys.stderr)
    if STATIC:
        write_preview()


def write_preview():
    """Same markup as the README: one paragraph, rows made by widths, no whitespace between images."""
    img = lambda n, w: (f'<img class="d" src="{n}-dark.svg" width="{w}">'
                        f'<img class="l" src="{n}-light.svg" width="{w}">')
    # preview picks the theme from the browser; on GitHub the README fragments do this
    body_css = ("body{margin:0;background:#fff;display:flex;justify-content:center}.d{display:none}"
                "@media (prefers-color-scheme:dark){body{background:#0d1117}.d{display:inline}.l{display:none}}")
    rows = [img("about", "100%"),
            img("contributions", "72.7%") + img("languages", "27.2%"),
            img("graph", "100%"),
            img("repo-rv32imacz", "49.9%") + img("repo-chillitpu", "49.9%"),
            img("repo-scheduler", "49.9%") + img("repo-kvcache", "49.9%")]
    page = (f'<!doctype html><meta charset="utf-8"><title>profile preview</title>'
            f'<style>{body_css}.w{{width:880px;padding:16px 0;font-size:0;line-height:0}} img{{vertical-align:top}}</style>'
            f'<div class="w">{"".join(rows)}</div>')
    (OUT / "preview.html").write_text(page)


if __name__ == "__main__":
    main()
