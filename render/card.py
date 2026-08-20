"""
The footer terminal card, emitted as hand-written SVG.

GitHub strips scripts from SVG but honours declarative animation, so the
cursor blinks via a CSS keyframe. Columns are placed at explicit x offsets
rather than padded with spaces, so the layout survives whatever monospace
font the reader's machine actually resolves.
"""

from __future__ import annotations

from xml.sax.saxutils import escape

MONO = "ui-monospace,SFMono-Regular,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"

PALETTES = {
    "dark": dict(
        bg="#0d1117", chrome="#161b22", border="#30363d",
        fg="#c9d1d9", dim="#6e7681", prompt="#7ee787",
        cmd="#79c0ff", key="#d2a8ff", val="#ffa657", accent="#39c5cf",
        bar="#2ea043", bar_dim="#1f6f36",
    ),
    "light": dict(
        bg="#ffffff", chrome="#f6f8fa", border="#d0d7de",
        fg="#1f2328", dim="#818b98", prompt="#1a7f37",
        cmd="#0550ae", key="#8250df", val="#953800", accent="#0969da",
        bar="#2ea043", bar_dim="#aceebb",
    ),
}

W, H = 860, 336
PAD = 26
LINE = 19.5
FS = 13
TOP = 74


def _fmt(n) -> str:
    if not isinstance(n, int):
        return str(n)
    return f"{n:,}"


def sparkline(values, x, y, width, height, pal) -> str:
    """Last-N contribution bars. Drawn as rects so the shape does not depend
    on block-glyph coverage in the reader's font."""
    if not values:
        return ""
    n = len(values)
    peak = max(values) or 1
    gap = 2.0
    bw = (width - gap * (n - 1)) / n
    out = []
    for i, v in enumerate(values):
        h = max(1.6, height * (v / peak))
        bx = x + i * (bw + gap)
        by = y + height - h
        colour = pal["bar"] if v else pal["bar_dim"]
        out.append(
            f'<rect x="{bx:.2f}" y="{by:.2f}" width="{bw:.2f}" '
            f'height="{h:.2f}" rx="1.2" fill="{colour}" '
            f'opacity="{0.95 if v else 0.5}"/>'
        )
    return "".join(out)


def _prompt(y, user, cmd, pal):
    """Shell prompt as one <text> of consecutive tspans.

    Consecutive tspans flow inline, so the layout needs no assumption about
    the advance width of whatever monospace font the reader actually has --
    which is the thing that breaks when you position columns by character
    count and the reader is on Consolas instead of Menlo.
    """
    parts = [(f"{user}@github", "prompt"), ("  ~", "accent"),
             ("  %  ", "dim"), (cmd, "cmd")]
    spans = "".join(f'<tspan fill="{pal[c]}">{escape(t)}</tspan>'
                    for t, c in parts)
    return f'<text x="{PAD}" y="{y:.1f}" xml:space="preserve">{spans}</text>'


def _cursor_prompt(y, user, pal):
    spans = (f'<tspan fill="{pal["prompt"]}">{escape(user)}@github</tspan>'
             f'<tspan fill="{pal["accent"]}">  ~</tspan>'
             f'<tspan fill="{pal["dim"]}">  %  </tspan>'
             f'<tspan class="cur" fill="{pal["fg"]}">\u2588</tspan>')
    return f'<text x="{PAD}" y="{y:.1f}" xml:space="preserve">{spans}</text>'


def _row(y, cols, pal):
    """cols: list of (x, text, colour-key)."""
    return "".join(
        f'<text x="{x}" y="{y:.1f}" fill="{pal[c]}">{escape(str(t))}</text>'
        for x, t, c in cols
    )


def terminal_card(st, params, theme: str, frames: int, res: str,
                  steps: int) -> str:
    pal = PALETTES[theme]
    user = st.login
    live = st.live

    def v(x, needs_auth=False):
        if not live or (needs_auth and not st.has_contributions):
            return "—"      # unknown, which is not the same as zero
        return _fmt(x)

    y = TOP
    body = []

    # --- block 1: the renderer describing itself ------------------------
    body.append(_prompt(y, user, "./gyroid --explain", pal))
    y += LINE * 1.5
    for k, val in (
        ("surface", "gyroid  ·  sin x cos y + sin y cos z + sin z cos x = 0"),
        ("sampler", f"{steps}-step sphere tracer  ·  numpy  ·  {res} @ 2x SS"),
        ("loop", f"{frames} frames  ·  seamless by symmetry, not by crossfade"),
    ):
        body.append(_row(y, [(PAD + 18, k, "key"), (PAD + 118, val, "fg")], pal))
        y += LINE

    # --- block 2: the numbers that shaped it ----------------------------
    y += LINE * 0.85
    body.append(_prompt(y, user, f"gh api /users/{user}", pal))
    y += LINE * 1.5
    cells = [("repos", st.repos, False), ("stars", st.stars, False),
             ("followers", st.followers, False), ("commits", st.commits, True),
             ("prs", st.prs, True), ("lang", st.top_language, False)]
    for r in range(2):
        cols = []
        for c in range(3):
            k, val, auth = cells[r * 3 + c]
            cx = PAD + 18 + c * 232
            cols.append((cx, k, "key"))
            cols.append((cx + 82,
                         (val if live else "—") if k == "lang" else v(val, auth),
                         "val"))
        body.append(_row(y, cols, pal))
        y += LINE

    # --- block 3: the calendar ------------------------------------------
    y += LINE * 0.9
    tail = st.calendar[-49:] if st.calendar else []
    body.append(_row(y, [(PAD + 18, "last 49d", "key")], pal))
    if tail:
        body.append(sparkline(tail, PAD + 118, y - 13, 596, 17, pal))
        body.append(_row(y, [(PAD + 736, f"Σ {_fmt(sum(tail))}", "accent")], pal))
    else:
        body.append(_row(y, [(PAD + 118, "calendar needs an authenticated token",
                              "dim")], pal))

    # --- prompt + cursor -------------------------------------------------
    y += LINE * 1.6
    body.append(_cursor_prompt(y, user, pal))

    dots = "".join(
        f'<circle cx="{PAD + 4 + i * 19}" cy="26" r="6" fill="{c}"/>'
        for i, c in enumerate(("#ff5f57", "#febc2e", "#28c840"))
    )

    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}"
     viewBox="0 0 {W} {H}" role="img"
     aria-label="Terminal card: live GitHub statistics for {escape(user)}">
  <style>
    text {{ font-family: {MONO}; font-size: {FS}px; dominant-baseline: alphabetic; }}
    .cur {{ animation: blink 1.06s steps(1, end) infinite; }}
    tspan {{ font-family: {MONO}; }}
    @keyframes blink {{ 0%,50% {{ opacity: 1 }} 50.01%,100% {{ opacity: 0 }} }}
  </style>
  <rect width="{W}" height="{H}" rx="10" fill="{pal['bg']}"
        stroke="{pal['border']}"/>
  <path d="M0 10a10 10 0 0 1 10-10h{W - 20}a10 10 0 0 1 10 10v42H0z"
        fill="{pal['chrome']}"/>
  <line x1="0" y1="52" x2="{W}" y2="52" stroke="{pal['border']}"/>
  {dots}
  <text x="{W / 2}" y="31" text-anchor="middle" fill="{pal['dim']}"
        font-size="12">{escape(user)} — gyroid.py</text>
  {"".join(body)}
</svg>'''
