"""
Shading and frame composition.

The loop is seamless by construction rather than by crossfade: over one
period the object turns through exactly 2*pi about the y axis, and the
gyroid phase advances by exactly one lattice vector. Both are exact
symmetries of the scene, so frame N and frame 0 are the same image.
"""

from __future__ import annotations

import numpy as np

from raymarch import (TAU, Gyroid, aces, aces_inverse, camera_rays,
                      cosine_palette, dot,
                      march, normalize, normals, occlusion, saturate,
                      soft_shadow)

# --------------------------------------------------------------------------
# themes -- the page ships a dark and a light render and lets <picture> pick
# --------------------------------------------------------------------------

def srgb_to_linear(c):
    """Theme colours are written as sRGB so they can be matched against
    GitHub's own chrome by eye; shading happens in linear light."""
    return np.asarray(c, dtype=np.float64) ** 2.2


THEMES = {
    "dark": dict(
        canvas=(0.051, 0.067, 0.090),   # #0d1117 exactly -- GitHub dark
        glow=(0.180, 0.330, 0.580),
        glow_gain=0.62,
        glow_width=0.155,
        key=(1.00, 0.94, 0.86),
        key_gain=2.60,
        fill=(0.20, 0.40, 0.85),
        fill_gain=0.30,
        rim=(0.35, 0.80, 1.00),
        rim_gain=0.75,
        ambient=0.055,
        fog_start=3.9,
        fog_range=3.4,
        exposure=1.10,
        palette=dict(
            a=(0.48, 0.42, 0.52), b=(0.44, 0.40, 0.46),
            c=(1.00, 1.00, 1.00), d=(0.58, 0.66, 0.80),
        ),
    ),
    "light": dict(
        canvas=(1.000, 1.000, 1.000),   # #ffffff exactly -- GitHub light
        glow=(0.560, 0.660, 0.870),
        glow_gain=0.20,
        glow_width=0.150,
        key=(1.00, 0.96, 0.90),
        key_gain=2.05,
        fill=(0.42, 0.55, 0.88),
        fill_gain=0.34,
        rim=(0.18, 0.45, 0.85),
        rim_gain=0.30,
        ambient=0.11,
        fog_start=5.2,
        fog_range=3.0,
        exposure=0.96,
        palette=dict(
            a=(0.44, 0.40, 0.50), b=(0.44, 0.42, 0.46),
            c=(1.00, 1.00, 1.00), d=(0.60, 0.68, 0.82),
        ),
    ),
}

# camera -- fixed; all motion belongs to the object
EYE = np.array([0.0, 1.42, 5.02])
TARGET = np.array([0.0, -0.02, 0.0])
FOV = 0.62

KEY_DIR = normalize(np.array([[-0.55, 0.78, 0.42]]))[0]
FILL_DIR = normalize(np.array([[0.72, 0.18, 0.35]]))[0]


def rotation_y(theta: float) -> np.ndarray:
    c, s = np.cos(theta), np.sin(theta)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def rotation_x(alpha: float) -> np.ndarray:
    c, s = np.cos(alpha), np.sin(alpha)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


class RotatedGyroid(Gyroid):
    """Gyroid sampled in a rotated frame. A rotation is an isometry, so the
    Lipschitz bound -- and therefore the safe step length -- is unchanged."""

    def __init__(self, freq, thickness, radius, phase, theta, tilt=0.0):
        super().__init__(freq, thickness, radius, phase)
        # tilt orients the lattice inside the clipping ball; theta is the
        # animation. Composing them leaves the loop exact, because theta is
        # still the only term that varies over the period.
        self.rot = rotation_y(theta) @ rotation_x(tilt)

    def sdf(self, p):
        pr = p @ self.rot.T
        shell = (np.abs(self.field(pr)) - self.thickness) / (
            2.0 * np.sqrt(3.0) * self.freq)
        ball = np.linalg.norm(p, axis=-1) - self.radius
        return np.maximum(shell, ball)


def background(rd: np.ndarray, th: dict) -> np.ndarray:
    """Flat canvas plus a tight halo behind the subject.

    The flat part is GitHub's own canvas colour, so the rendered rectangle has
    no visible border on the profile page -- the object appears to float in
    the README rather than sit in a box. The halo is gaussian in the angle off
    the view axis and is chosen narrow enough (~2% of peak at the frame
    corner) that it cannot give the edge away either.
    """
    # pre-invert the tone map so the flat area lands on the canvas colour
    # exactly after aces() and the gamma encode downstream
    base = aces_inverse(srgb_to_linear(th["canvas"])) / th["exposure"]
    axis = normalize(TARGET - EYE)
    ang = np.arccos(np.clip(dot(rd, axis), -1.0, 1.0))
    halo = np.exp(-(ang / th["glow_width"]) ** 2)[:, None]
    return base + halo * srgb_to_linear(th["glow"]) * th["glow_gain"]


def render_frame(width: int, height: int, theme: str, *, freq: float,
                 thickness: float, radius: float, u: float,
                 palette_shift: float, tilt: float = 0.0, steps: int = 128,
                 shadow_steps: int = 40, ss: int = 2) -> np.ndarray:
    """Render one frame at phase u in [0, 1). Returns uint8 RGB (h, w, 3)."""
    th = THEMES[theme]
    W, H = width * ss, height * ss

    scene = RotatedGyroid(freq, thickness, radius,
                          phase=TAU * u, theta=TAU * u, tilt=tilt)

    ro, rd = camera_rays(W, H, EYE, TARGET, FOV)
    col = background(rd, th)

    tmax = 9.0
    # (eps is scaled to the pixel footprint; see march() for step_scale)
    eps = 0.0009
    t, hit = march(scene, ro, rd, steps, tmin=0.0, tmax=tmax, eps=eps)

    idx = np.flatnonzero(hit)
    if idx.size:
        p = ro[idx] + rd[idx] * t[idx, None]
        n = normals(scene, p, 0.0016)
        view = -rd[idx]

        ao = occlusion(scene, p, n)
        sh = soft_shadow(scene, p + n * 0.006,
                         np.repeat(KEY_DIR[None, :], idx.size, axis=0),
                         k=15.0, steps=shadow_steps, tmin=0.02, tmax=4.5)

        # palette parameter: depth into the ball, tilted by surface normal
        rr = saturate(np.linalg.norm(p, axis=-1) / radius)
        tint = saturate(0.62 * rr + 0.38 * (0.5 + 0.5 * n[:, 1])) + palette_shift
        base = saturate(cosine_palette(tint, **th["palette"]))

        ndl = np.maximum(dot(n, KEY_DIR), 0.0)
        key = base * (ndl * sh * th["key_gain"])[:, None] * np.array(th["key"])

        fillterm = saturate(0.5 + 0.5 * dot(n, FILL_DIR)) * ao
        fill = base * (fillterm * th["fill_gain"])[:, None] * np.array(th["fill"])

        amb = base * (ao * th["ambient"])[:, None]

        half = normalize(KEY_DIR + view)
        spec = (np.maximum(dot(n, half), 0.0) ** 52.0) * ndl * sh
        spec = spec[:, None] * np.array(th["key"]) * 0.55

        fres = (1.0 - saturate(dot(n, view))) ** 5.0
        rim = (fres * ao * th["rim_gain"])[:, None] * np.array(th["rim"])

        shaded = key + fill + amb + spec + rim

        # distance fog blends the far side of the object into the background
        fog = saturate((t[idx] - th["fog_start"]) / th["fog_range"])[:, None]
        col[idx] = shaded * (1.0 - fog) + background(rd[idx], th) * fog

    col = aces(col * th["exposure"])
    col = col ** (1.0 / 2.2)

    img = col.reshape(H, W, 3)
    if ss > 1:  # box downsample -- the anti-aliasing
        img = img.reshape(height, ss, width, ss, 3).mean(axis=(1, 3))

    return (saturate(img) * 255.0 + 0.5).astype(np.uint8)
