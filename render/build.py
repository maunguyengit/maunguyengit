#!/usr/bin/env python3
"""
Build every asset the profile page needs, then rewrite the numbers embedded
in the README so the printed parameters always match the picture above them.

    python render/build.py --user <login>

Frames are rendered in worker processes; the scene is fully described by a
handful of floats, so there is nothing to share and nothing to lock.
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import io
import json
import os
import pathlib
import re
import sys
import time

import numpy as np
from PIL import Image

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import stats as stats_mod          # noqa: E402
from card import terminal_card     # noqa: E402
from scene import render_frame     # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _frame(job):
    w, h, theme, u, p, steps, shadow_steps, ss = job
    return render_frame(w, h, theme, u=u, steps=steps,
                        shadow_steps=shadow_steps, ss=ss, **p)


def assert_seamless(params, args):
    """The README claims the loop closes exactly. Check it rather than
    asserting it in prose: frame N is frame 0 by construction, so any drift
    means a symmetry got broken upstream."""
    small = dict(steps=112, shadow_steps=28, ss=1)
    a = render_frame(180, 101, "dark", u=0.0, **small, **params)
    b = render_frame(180, 101, "dark", u=1.0, **small, **params)
    delta = int(np.abs(a.astype(int) - b.astype(int)).max())
    if delta != 0:
        raise SystemExit(f"loop is not seamless: max channel delta {delta}")
    print(f"  [loop] frame(N) == frame(0), max channel delta {delta}")


def render_loop(theme, params, args, jobs) -> bytes:
    work = [(args.width, args.height, theme, i / args.frames, params,
             args.steps, args.shadow_steps, args.ss) for i in range(args.frames)]
    t0 = time.time()
    if jobs > 1:
        with cf.ProcessPoolExecutor(max_workers=jobs) as ex:
            imgs = list(ex.map(_frame, work))
    else:
        imgs = [_frame(j) for j in work]
    print(f"  [{theme}] {args.frames} frames in {time.time() - t0:.1f}s "
          f"({(time.time() - t0) / args.frames:.2f}s/frame, {jobs} workers)")

    pil = [Image.fromarray(a) for a in imgs]
    buf = io.BytesIO()
    pil[0].save(buf, "WEBP", save_all=True, append_images=pil[1:],
                quality=args.quality, method=5, loop=0,
                duration=round(args.duration * 1000 / args.frames))
    return buf.getvalue()


PARAM_BLOCK = """<!-- gyroid:params -->
| symbol | meaning | driven by | current |
|:--|:--|:--|--:|
| $f$ | cell frequency of the gyroid lattice | public repositories | `{freq:.4f}` |
| $\\alpha$ | tilt of the lattice inside the ball | stars received | `{tilt:.4f}` |
| $\\delta$ | palette rotation | commits $\\times \\varphi \\bmod 1$ | `{palette_shift:.4f}` |
| $\\tau$ | shell half-thickness | pinned — see below | `{thickness:.4f}` |
| $R$ | clipping ball radius | pinned | `{radius:.4f}` |

<sub>Rendered {when} UTC from {source} · {frames} frames · {width}×{height} at {ss}× supersampling · {steps} march steps</sub>
<!-- /gyroid:params -->"""


def patch_readme(params, args, st, when):
    path = ROOT / "README.md"
    if not path.exists():
        print("  [readme] not found, skipping patch")
        return
    block = PARAM_BLOCK.format(
        **params, when=when,
        source="live GitHub data" if st.live else "fallback parameters (no token)",
        frames=args.frames, width=args.width, height=args.height,
        ss=args.ss, steps=args.steps,
    )
    text = path.read_text()
    new = re.sub(r"<!-- gyroid:params -->.*?<!-- /gyroid:params -->",
                 lambda _: block, text, flags=re.S)
    if new != text:
        path.write_text(new)
        print("  [readme] parameter block updated")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default=os.environ.get("PROFILE_USER", "octocat"))
    ap.add_argument("--out", default=str(ROOT / "assets"))
    ap.add_argument("--width", type=int, default=1080)
    ap.add_argument("--height", type=int, default=608)
    ap.add_argument("--frames", type=int, default=40)
    ap.add_argument("--duration", type=float, default=5.0, help="loop seconds")
    ap.add_argument("--steps", type=int, default=176)
    ap.add_argument("--shadow-steps", type=int, default=44)
    ap.add_argument("--ss", type=int, default=2)
    ap.add_argument("--quality", type=int, default=82)
    ap.add_argument("--jobs", type=int, default=0, help="0 = cpu_count")
    ap.add_argument("--quick", action="store_true",
                    help="fast, ugly preview: quarter size, few frames")
    args = ap.parse_args()

    if args.quick:
        args.width, args.height = 360, 203
        args.frames, args.steps, args.ss = 10, 112, 1

    jobs = args.jobs or min(os.cpu_count() or 2, 8)
    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    print(f"[stats] fetching {args.user}")
    st = stats_mod.fetch(args.user)
    params = stats_mod.scene_params(st)
    print(f"  live={st.live} " +
          " ".join(f"{k}={v:.4f}" for k, v in params.items()))

    assert_seamless(params, args)

    total = 0
    for theme in ("dark", "light"):
        data = render_loop(theme, params, args, jobs)
        p = out / f"gyroid-{theme}.webp"
        p.write_bytes(data)
        total += len(data)
        print(f"  [{theme}] wrote {p.name}  {len(data) / 1024:.0f} KB")

        card = terminal_card(st, params, theme, args.frames,
                             f"{args.width}x{args.height}", args.steps)
        (out / f"card-{theme}.svg").write_text(card)

    when = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
    (out / "stats.json").write_text(json.dumps(
        st.to_dict() | {"scene": params, "generated": when}, indent=2))
    patch_readme(params, args, st, when)
    print(f"[done] {total / 1024:.0f} KB of animation")


if __name__ == "__main__":
    main()
