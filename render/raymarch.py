"""
A CPU signed-distance-field raymarcher, vectorised over numpy.

Renders a sphere-clipped gyroid -- the triply periodic minimal surface

    g(x, y, z) = sin x cos y + sin y cos z + sin z cos x = 0

Every visual parameter of the scene (cell frequency, shell thickness, palette
phase) is driven by live GitHub statistics; see stats.py for the mapping.

Nothing here is a library call. The intersector, the normals, the soft
shadows, the ambient occlusion and the tone map are all written out longhand,
because the point of the page this feeds is that the equations printed under
the picture are the ones that produced it.
"""

from __future__ import annotations

import numpy as np

TAU = 2.0 * np.pi

# |grad g| <= 2*sqrt(3)*f for the gyroid field at cell frequency f, which is
# the Lipschitz bound that turns the implicit surface into a safe step length.
LIPSCHITZ = 2.0 * np.sqrt(3.0)


# --------------------------------------------------------------------------
# small vector helpers -- everything operates on (N, 3) float arrays
# --------------------------------------------------------------------------

def normalize(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=-1, keepdims=True)
    return v / np.maximum(n, 1e-12)


def dot(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.einsum("...i,...i->...", a, b)


def cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.cross(a, b)


def saturate(x):
    return np.clip(x, 0.0, 1.0)


# --------------------------------------------------------------------------
# the scene
# --------------------------------------------------------------------------

class Gyroid:
    """A gyroid shell of half-thickness `thickness`, intersected with a ball.

    The shell is  | g(f*p + phase) | - thickness <= 0,  divided by the
    Lipschitz bound so that marching by the returned value never overshoots
    the surface. The intersection with the ball is the usual max() of two
    distance estimates, which stays conservative.
    """

    def __init__(self, freq: float, thickness: float, radius: float, phase: float):
        self.freq = float(freq)
        self.thickness = float(thickness)
        self.radius = float(radius)
        self.phase = float(phase)

    def field(self, p: np.ndarray) -> np.ndarray:
        q = p * self.freq + self.phase
        s, c = np.sin(q), np.cos(q)
        return s[..., 0] * c[..., 1] + s[..., 1] * c[..., 2] + s[..., 2] * c[..., 0]

    def sdf(self, p: np.ndarray) -> np.ndarray:
        shell = (np.abs(self.field(p)) - self.thickness) / (LIPSCHITZ * self.freq)
        ball = np.linalg.norm(p, axis=-1) - self.radius
        return np.maximum(shell, ball)


# --------------------------------------------------------------------------
# the intersector
# --------------------------------------------------------------------------

def march(scene, ro: np.ndarray, rd: np.ndarray, steps: int,
          tmin: float, tmax: float, eps: float, step_scale: float = 0.85):
    """Sphere tracing with an active-ray mask.

    Rays that have converged or escaped drop out of the working set, so the
    per-iteration cost falls off sharply after the first dozen steps.

    `step_scale` shortens each step below the guaranteed-safe length. The
    Lipschitz bound is worst-case and grazing rays skim the shell for a long
    way, where a full-length step can tunnel through it and speckle the
    silhouette; 0.85 costs a few percent and removes them.
    """
    n = ro.shape[0]
    t = np.full(n, tmin, dtype=np.float64)
    hit = np.zeros(n, dtype=bool)
    active = np.ones(n, dtype=bool)

    for _ in range(steps):
        idx = np.flatnonzero(active)
        if idx.size == 0:
            break
        p = ro[idx] + rd[idx] * t[idx, None]
        d = scene.sdf(p)

        converged = d < eps
        if converged.any():
            hit[idx[converged]] = True
            active[idx[converged]] = False

        t[idx] += np.maximum(d * step_scale, eps * 0.5)
        escaped = t[idx] > tmax
        if escaped.any():
            active[idx[escaped]] = False

    return t, hit


def normals(scene, p: np.ndarray, h: float) -> np.ndarray:
    """Surface normal as the gradient of the distance field, by central
    differences:  n = normalize( [ d(p+he_i) - d(p-he_i) ]_i )."""
    out = np.empty_like(p)
    for i in range(3):
        e = np.zeros(3)
        e[i] = h
        out[:, i] = scene.sdf(p + e) - scene.sdf(p - e)
    return normalize(out)


def soft_shadow(scene, ro: np.ndarray, rd: np.ndarray, k: float,
                steps: int, tmin: float, tmax: float) -> np.ndarray:
    """Inigo Quilez's penumbra estimate: the closest approach of the shadow
    ray to the surface, scaled by distance travelled, approximates the
    fraction of the light disc that stays visible."""
    n = ro.shape[0]
    res = np.ones(n)
    t = np.full(n, tmin)
    active = np.ones(n, dtype=bool)

    for _ in range(steps):
        idx = np.flatnonzero(active)
        if idx.size == 0:
            break
        d = scene.sdf(ro[idx] + rd[idx] * t[idx, None])
        res[idx] = np.minimum(res[idx], k * d / np.maximum(t[idx], 1e-4))
        t[idx] += np.clip(d, 0.012, 0.18)
        active[idx] = (res[idx] > 0.002) & (t[idx] < tmax)

    return saturate(res)


def occlusion(scene, p: np.ndarray, n: np.ndarray, samples: int = 5) -> np.ndarray:
    """Ambient occlusion by comparing marched distance against step distance
    along the normal -- where the field falls short, geometry is nearby."""
    occ = np.zeros(p.shape[0])
    sca = 1.0
    for i in range(samples):
        hd = 0.012 + 0.11 * i / max(samples - 1, 1)
        d = scene.sdf(p + n * hd)
        occ += (hd - d) * sca
        sca *= 0.92
    return saturate(1.0 - 2.6 * occ)


# --------------------------------------------------------------------------
# shading
# --------------------------------------------------------------------------

def cosine_palette(t: np.ndarray, a, b, c, d) -> np.ndarray:
    """Quilez's cosine gradient:  colour(t) = a + b * cos( 2*pi*(c*t + d) )."""
    a, b, c, d = (np.asarray(v, dtype=np.float64) for v in (a, b, c, d))
    return a + b * np.cos(TAU * (c * t[..., None] + d))


def aces(x: np.ndarray) -> np.ndarray:
    """Narkowicz's analytic fit to the ACES filmic tone curve."""
    a, b, c, d, e = 2.51, 0.03, 2.43, 0.59, 0.14
    return saturate((x * (a * x + b)) / (x * (c * x + d) + e))


def aces_inverse(y: np.ndarray) -> np.ndarray:
    """Exact inverse of aces().

    The background has to survive the tone map unchanged, otherwise a colour
    specified as GitHub's canvas comes out darker than the page and the
    rendered rectangle shows its edges. The ACES fit is a ratio of quadratics,
    so it inverts in closed form: solving y = x(ax+b) / (x(cx+d)+e) for x
    gives (yc - a)x^2 + (yd - b)x + ye = 0.
    """
    a, b, c, d, e = 2.51, 0.03, 2.43, 0.59, 0.14
    y = np.asarray(y, dtype=np.float64)
    A = y * c - a
    B = y * d - b
    C = y * e
    disc = np.maximum(B * B - 4.0 * A * C, 0.0)
    root = np.sqrt(disc)
    x1 = (-B + root) / (2.0 * A)
    x2 = (-B - root) / (2.0 * A)
    # A < 0 across the displayable range, so of the two roots the physical one
    # is the smallest that is still non-negative.
    cand = np.stack([x1, x2])
    cand = np.where(cand >= 0.0, cand, np.inf)
    out = cand.min(axis=0)
    return np.where(np.isfinite(out), out, 0.0)


def camera_rays(width: int, height: int, eye: np.ndarray, target: np.ndarray,
                fov: float):
    """Pinhole camera. Returns origins and unit directions, one per pixel."""
    fwd = normalize(target - eye)
    right = normalize(cross(fwd, np.array([0.0, 1.0, 0.0])))
    up = cross(right, fwd)

    aspect = width / height
    half = np.tan(fov * 0.5)
    xs = ((np.arange(width) + 0.5) / width * 2.0 - 1.0) * aspect * half
    ys = (1.0 - (np.arange(height) + 0.5) / height * 2.0) * half
    gx, gy = np.meshgrid(xs, ys)

    rd = fwd + right * gx[..., None] + up * gy[..., None]
    rd = normalize(rd.reshape(-1, 3))
    ro = np.repeat(eye[None, :], rd.shape[0], axis=0)
    return ro, rd
