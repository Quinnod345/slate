"""Motion between two frames, measured with numpy and scipy alone: one global shift, a dense flow field, and the warp
that makes a frame in between.

The films repaired their plates' timing with OpenCV's DIS optical flow (pipeline/tools/plate_repair.py). Slate does the
same job without OpenCV, so nothing new has to be installed:

- `shift(a, b)` is the films' iterative Lucas-Kanade on one crop (stability_probe.lk): the single (dx, dy) that maps
  crop a onto crop b, to about 0.02 px. It measures how far a plate's moving band travels in one step.
- `dense(a, b)` is a coarse-to-fine dense Lucas-Kanade: at each level of an image pyramid, every pixel's motion is
  refined by solving the brightness-constancy equations over a Gaussian window around it, after warping b by the
  motion found so far. Flat areas (a still sky) carry the coarser level's answer instead of noise, and a median
  filter at each level throws out the odd vector. On the slow, smooth moves generated plates make, it lands within a
  few hundredths of a pixel of the truth (tests/gencap/test_gencap_repair.py measures it).
- `between(fa, fb, ab, ba, f)` makes the frame a fraction f of the way from fa to fb: each is warped to the in-between
  time along the flow and the two are blended by their distance, the way the films' repair did it.

Flow follows OpenCV's convention: a(p) ≈ b(p + F(p)), with F[..., 0] the x component and F[..., 1] the y component, in
pixels of the frame given.
"""

from __future__ import annotations

import numpy as np
from scipy import ndimage as ndi

# Dense flow: the pyramid stops when its short side would drop under this; the window is a Gaussian of this sigma (px
# at each level); a little smoothing before the gradients; the median filter's size at each level.
MIN_SIDE = 24
WINDOW_SIGMA = 2.5
PRE_SIGMA = 0.8
MEDIAN = 5
ITERATIONS = 4
# Regularisation, relative to the frame's typical gradient energy: where there's too little texture to measure
# (a flat sky), the update is damped to nothing and the coarser level's motion stands.
DAMPING = 0.02
# The most one pass may move a vector, in px at its level.
STEP_MAX = 1.0


def shift(a: np.ndarray, b: np.ndarray, *, iters: int = 8, sigma: float = 0.8) -> tuple[float, float]:
    """The shift (dx, dy) that maps crop a onto crop b (b(x) ≈ a(x − d)), by iterative Lucas-Kanade, Hann-weighted so
    the crop's edges don't decide it. The films' stability_probe.lk, unchanged."""
    a = ndi.gaussian_filter(np.asarray(a, dtype=np.float64), sigma)
    b = ndi.gaussian_filter(np.asarray(b, dtype=np.float64), sigma)
    gy, gx = np.gradient(a)
    w = np.outer(np.hanning(a.shape[0]), np.hanning(a.shape[1]))
    m = np.array([[(w * gx * gx).sum(), (w * gx * gy).sum()], [(w * gx * gy).sum(), (w * gy * gy).sum()]])
    d = np.zeros(2)
    for _ in range(iters):
        e = ndi.shift(b, (d[1], d[0]), order=3, mode="nearest") - a
        g = -np.array([(w * gx * e).sum(), (w * gy * e).sum()])
        try:
            step = np.linalg.solve(m, g)
        except np.linalg.LinAlgError:
            break
        d = d - step
        if np.abs(step).max() < 1e-4:
            break
    return float(-d[0]), float(-d[1])


def _resize(img: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """A float image resized to (h, w), bilinear, sampling pixel centres (so a flow upsamples without drifting)."""
    h, w = shape
    sh, sw = img.shape[:2]
    if (sh, sw) == (h, w):
        return img.astype(np.float32, copy=False)
    ys = (np.arange(h, dtype=np.float32) + 0.5) * (sh / h) - 0.5
    xs = (np.arange(w, dtype=np.float32) + 0.5) * (sw / w) - 0.5
    yy, xx = np.meshgrid(ys, xs, indexing="ij")
    return ndi.map_coordinates(img.astype(np.float32, copy=False), [yy, xx], order=1, mode="nearest",
                               output=np.float32)


def _pyramid(img: np.ndarray, levels: int) -> list[np.ndarray]:
    out = [img]
    for _ in range(levels - 1):
        prev = out[-1]
        small = ndi.gaussian_filter(prev, 1.0)[::2, ::2]
        out.append(small.astype(np.float32, copy=False))
    return out


def _levels(shape: tuple[int, int]) -> int:
    n, side = 1, min(shape)
    while side // 2 >= MIN_SIDE:
        side //= 2
        n += 1
    return n


def _grid(h: int, w: int) -> tuple[np.ndarray, np.ndarray]:
    yy, xx = np.mgrid[0:h, 0:w]
    return yy.astype(np.float32), xx.astype(np.float32)


def warp(img: np.ndarray, flow: np.ndarray, *, order: int = 1, scale: float = 1.0) -> np.ndarray:
    """img sampled at p + scale·flow(p) for every pixel p (a backward warp), edges held."""
    h, w = img.shape[:2]
    yy, xx = _grid(h, w)
    s = np.float32(scale)
    return ndi.map_coordinates(img.astype(np.float32, copy=False),
                               [yy + s * flow[..., 1], xx + s * flow[..., 0]], order=order, mode="nearest",
                               output=np.float32)


def _refine(a: np.ndarray, b: np.ndarray, flow: np.ndarray, iters: int) -> np.ndarray:
    """One pyramid level: Lucas-Kanade on a Gaussian window around every pixel, iterated with warping.

    Where the warped sample falls outside the frame (content entering at an edge), the pixel can't be compared, so it
    drops out of every window's sums instead of pulling the motion around it; and no single pass moves a vector more
    than a pixel at that level, so a pixel with no answer drifts instead of jumping."""
    a = ndi.gaussian_filter(a, PRE_SIGMA)
    b = ndi.gaussian_filter(b, PRE_SIGMA)
    h, w = a.shape
    yy, xx = _grid(h, w)
    for _ in range(iters):
        sy, sx = yy + flow[..., 1], xx + flow[..., 0]
        inside = ((sx >= 0) & (sx <= w - 1) & (sy >= 0) & (sy <= h - 1)).astype(np.float32)
        bw = ndi.map_coordinates(b, [sy, sx], order=1, mode="nearest", output=np.float32)
        avg = 0.5 * (a + bw)
        gy, gx = np.gradient(avg)
        gx *= inside
        gy *= inside
        e = (bw - a) * inside
        sxx = ndi.gaussian_filter(gx * gx, WINDOW_SIGMA)
        sxy = ndi.gaussian_filter(gx * gy, WINDOW_SIGMA)
        syy = ndi.gaussian_filter(gy * gy, WINDOW_SIGMA)
        sxe = ndi.gaussian_filter(gx * e, WINDOW_SIGMA)
        sye = ndi.gaussian_filter(gy * e, WINDOW_SIGMA)
        lam = np.float32(DAMPING * max(float(np.mean(sxx + syy)), 1e-6))
        sxx = sxx + lam
        syy = syy + lam
        det = sxx * syy - sxy * sxy
        det = np.where(np.abs(det) < 1e-12, np.float32(1e-12), det)
        du = np.clip(-(syy * sxe - sxy * sye) / det, -STEP_MAX, STEP_MAX)
        dv = np.clip(-(sxx * sye - sxy * sxe) / det, -STEP_MAX, STEP_MAX)
        flow = flow + np.stack([du, dv], axis=-1).astype(np.float32)
        if max(float(np.abs(du).max()), float(np.abs(dv).max())) < 1e-3:
            break
    if MEDIAN > 1:
        flow = np.stack([ndi.median_filter(flow[..., k], size=MEDIAN, mode="nearest") for k in (0, 1)], axis=-1)
    return flow.astype(np.float32, copy=False)


def dense(a: np.ndarray, b: np.ndarray, *, scale: float = 0.5, iters: int = ITERATIONS) -> np.ndarray:
    """The dense flow from a to b (2-D arrays, one channel), computed at `scale` of their size and returned at full size
    in full-size pixels: a(p) ≈ b(p + flow(p)), shape (h, w, 2)."""
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    if a.shape != b.shape or a.ndim != 2:
        raise ValueError("dense flow needs two single-channel frames of one size")
    h, w = a.shape
    if scale != 1.0:
        sh, sw = max(MIN_SIDE, int(round(h * scale))), max(MIN_SIDE, int(round(w * scale)))
        a_s, b_s = _resize(ndi.gaussian_filter(a, 0.5 / scale), (sh, sw)), _resize(ndi.gaussian_filter(b, 0.5 / scale),
                                                                                    (sh, sw))
    else:
        sh, sw, a_s, b_s = h, w, a, b
    levels = _levels((sh, sw))
    pa, pb = _pyramid(a_s, levels), _pyramid(b_s, levels)
    flow = np.zeros(pa[-1].shape + (2,), np.float32)
    for lvl in range(levels - 1, -1, -1):
        if flow.shape[:2] != pa[lvl].shape:
            fy, fx = pa[lvl].shape[0] / flow.shape[0], pa[lvl].shape[1] / flow.shape[1]
            flow = np.stack([_resize(flow[..., 0], pa[lvl].shape) * fx, _resize(flow[..., 1], pa[lvl].shape) * fy],
                            axis=-1)
        flow = _refine(pa[lvl], pb[lvl], flow, iters)
    if (sh, sw) != (h, w):
        flow = np.stack([_resize(flow[..., 0], (h, w)) * (w / sw), _resize(flow[..., 1], (h, w)) * (h / sh)], axis=-1)
    return flow.astype(np.float32, copy=False)


def half_flow(flow: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    """A full-size flow for a plane of another size (chroma at half size): resampled, and scaled into its pixels."""
    h, w = flow.shape[:2]
    th, tw = shape
    return np.stack([_resize(flow[..., 0], shape) * (tw / w), _resize(flow[..., 1], shape) * (th / h)], axis=-1)


def between(fa: np.ndarray, fb: np.ndarray, ab: np.ndarray, ba: np.ndarray, f: float, *, order: int = 3,
            prefiltered: tuple[np.ndarray, np.ndarray] | None = None) -> np.ndarray:
    """The frame a fraction f of the way from fa to fb (each (h, w) or (h, w, c), floats). Backward warps: the
    in-between sample at p came from fa at p − f·ab(p) and from fb at p − (1−f)·ba(p); the two are blended by distance.

    `prefiltered` takes the two frames already passed through scipy's cubic spline filter (one per source frame, so a
    frame used by several in-between frames is filtered once)."""
    f32 = np.float32(f)
    one = np.float32(1.0) - f32
    a = np.asarray(fa, dtype=np.float32)
    b = np.asarray(fb, dtype=np.float32)
    channels = a.shape[2] if a.ndim == 3 else 1
    h, w = a.shape[:2]
    yy, xx = _grid(h, w)
    ya, xa = yy - f32 * ab[..., 1], xx - f32 * ab[..., 0]
    yb, xb = yy - one * ba[..., 1], xx - one * ba[..., 0]
    # Content entering at an edge exists in only one of the two frames: there, take it from that one alone.
    va = ((xa >= 0) & (xa <= w - 1) & (ya >= 0) & (ya <= h - 1)).astype(np.float32)
    vb = ((xb >= 0) & (xb <= w - 1) & (yb >= 0) & (yb <= h - 1)).astype(np.float32)
    wa_w, wb_w = one * va, f32 * vb
    total = wa_w + wb_w
    neither = total <= 0
    wa_w = np.where(neither, one, wa_w / np.where(neither, 1, total))
    wb_w = np.where(neither, f32, wb_w / np.where(neither, 1, total))
    out = np.empty_like(a)
    for c in range(channels):
        pa_c = a[..., c] if a.ndim == 3 else a
        pb_c = b[..., c] if b.ndim == 3 else b
        if prefiltered is not None and order > 1:
            ca = prefiltered[0][..., c] if a.ndim == 3 else prefiltered[0]
            cb = prefiltered[1][..., c] if b.ndim == 3 else prefiltered[1]
            wa = ndi.map_coordinates(ca, [ya, xa], order=order, mode="nearest", prefilter=False, output=np.float32)
            wb = ndi.map_coordinates(cb, [yb, xb], order=order, mode="nearest", prefilter=False, output=np.float32)
        else:
            wa = ndi.map_coordinates(pa_c, [ya, xa], order=order, mode="nearest", output=np.float32)
            wb = ndi.map_coordinates(pb_c, [yb, xb], order=order, mode="nearest", output=np.float32)
        mixed = wa_w * wa + wb_w * wb
        if a.ndim == 3:
            out[..., c] = mixed
        else:
            out = mixed
    return out


def spline(frame: np.ndarray, order: int = 3) -> np.ndarray:
    """A frame passed through scipy's spline prefilter, per channel (for `between(..., prefiltered=…)`)."""
    a = np.asarray(frame, dtype=np.float32)
    if a.ndim == 2:
        return ndi.spline_filter(a, order=order, output=np.float32, mode="nearest")
    return np.stack([ndi.spline_filter(a[..., c], order=order, output=np.float32, mode="nearest")
                     for c in range(a.shape[2])], axis=-1)


def blend(fa: np.ndarray, fb: np.ndarray, f: float) -> np.ndarray:
    """The two frames mixed by distance, nothing warped: the fallback when a synthesised frame reads as generated."""
    return (1.0 - np.float32(f)) * np.asarray(fa, dtype=np.float32) + np.float32(f) * np.asarray(fb, dtype=np.float32)
