"""
Holds the 2D Gaussian fitting function used by curve_fit.
"""

import numpy as np
from scipy.optimize import curve_fit

def gaussian2d_flat(coords, A, x0, y0, sigma_x, sigma_y, offset):
    """
    coords is (x_indices, y_indices).
    Return the flattened 2D Gaussian values:
       A * exp(-( (x - x0)^2/(2 sigma_x^2) + (y - y0)^2/(2 sigma_y^2))) + offset
    """
    x, y = coords
    g = A * np.exp(-(
        ((x - x0)**2)/(2*sigma_x**2) +
        ((y - y0)**2)/(2*sigma_y**2)
    )) + offset
    return g.ravel()


_fit_cache = {}


def prepare_gaussian_fit_patch(frame_image, center, crop_size, margin=5):
    """Extract the small image region needed by both fitting passes.

    Each pass constrains the center to at most four pixels from its starting
    point. A five-pixel margin therefore preserves exactly the pixels that a
    full-frame fit can reach while keeping process-transfer payloads tiny.
    """
    H, W = frame_image.shape
    half = crop_size // 2
    nominal_x1 = int(round(center[0])) - half
    nominal_y1 = int(round(center[1])) - half
    first_x1 = max(0, nominal_x1)
    first_y1 = max(0, nominal_y1)
    patch_x1 = max(0, first_x1 - margin)
    patch_y1 = max(0, first_y1 - margin)
    patch_x2 = min(W, first_x1 + crop_size + margin)
    patch_y2 = min(H, first_y1 + crop_size + margin)
    patch = np.ascontiguousarray(frame_image[patch_y1:patch_y2, patch_x1:patch_x2])
    local_center = (center[0] - patch_x1, center[1] - patch_y1)
    return patch, local_center, (patch_x1, patch_y1)


def perform_gaussian_fit_job_batch(batch):
    """Fit one compact batch in a worker process and restore global centers."""
    output = []
    for job in batch:
        (
            idx, frame, cx, cy, patch, local_center, origin,
            crop_size, pixel_size, bg_fixed,
        ) = job
        fc, sigma, intensity, peak, bkgr = perform_gaussian_fit(
            patch,
            local_center,
            crop_size,
            pixelsize=pixel_size,
            bg_fixed=bg_fixed,
        )
        if fc is not None:
            fc = (fc[0] + origin[0], fc[1] + origin[1])
        output.append((idx, frame, cx, cy, fc, sigma, intensity, peak, bkgr))
    return output

def perform_gaussian_fit(frame_image,
                         center,
                         crop_size,
                         pixelsize=None,
                         max_nfev=200,
                         iterations=2,
                         bg_fixed=None):
    """
    Perform a 2D Gaussian fit on a subimage around `center`, then optionally recrop
    around the fitted center and refit for improved accuracy.
    This version caches xi, yi and sigma_arr per crop_size, and uses a percentile
    for background instead of a full histogram.
    """
    if center is None or any(c is None or np.isnan(c) for c in center):
        return (None, None, None, None, None)
    # Early SNR check on a minimal patch
    H, W = frame_image.shape
    half = crop_size // 2
    cx0, cy0 = center
    x0, y0 = int(round(cx0)), int(round(cy0))
    sub0 = frame_image[
        max(0, y0-half):min(H, y0+half),
        max(0, x0-half):min(W, x0+half)
    ]
    if sub0.size == 0 or (sub0.max() - np.median(sub0)) < 4 * sub0.std():
        return (None, None, None, None, None)
    # Prepare min/max sigma
    """
    A Gaussian’s FWHM is related to its standard deviation σ by:
    FWHM = 2 sqrt(2ln(2))*sigma
    The leading factor of 2 enforces a minimum width of twice the PSF σ,
    to guard against spuriously sharp fits that would be smaller than what optics can actually resolve
    """
    sigma_min = 1.0
    if pixelsize is not None:
        sigma_min = 2*(200/2.355)/pixelsize
    sigma_max = crop_size/4.0

    # Cache grids & sigma_arr keyed by crop_size
    if crop_size not in _fit_cache:
        yi, xi = np.indices((crop_size, crop_size))
        d2 = (xi - crop_size//2)**2 + (yi - crop_size//2)**2
        w_sigma = crop_size/10.0
        sigma_arr = 1.0/np.sqrt(np.exp(-d2/(2*w_sigma**2)) + 1e-6)
        _fit_cache[crop_size] = (xi, yi, sigma_arr)

    xi_full, yi_full, sigma_arr_full = _fit_cache[crop_size]

    fitted_center = center
    previous_popt = None
    for it in range(iterations):
        cx, cy = fitted_center
        x1 = max(0, int(round(cx)) - half)
        y1 = max(0, int(round(cy)) - half)
        x2 = min(W, x1 + crop_size)
        y2 = min(H, y1 + crop_size)

        sub = frame_image[y1:y2, x1:x2]
        if sub.shape[0] != crop_size or sub.shape[1] != crop_size:
            # pad to full crop_size if at border
            pad_y = crop_size - sub.shape[0]
            pad_x = crop_size - sub.shape[1]
            bg = bg_fixed if bg_fixed is not None else float(np.percentile(sub,20))
            sub = np.pad(sub, ((0,pad_y),(0,pad_x)), mode='constant', constant_values=bg)

        # compute border width = 25% of the smaller dimension (larger sampling region)
        h_sub, w_sub = sub.shape
        border_fraction = 0.25  # sample 20-30% of edges for background estimate
        border = max(1, int(min(h_sub, w_sub) * border_fraction))

        # extract the four edge strips
        edges = np.concatenate([
            sub[:border, :].ravel(),     # top
            sub[-border:, :].ravel(),    # bottom
            sub[:, :border].ravel(),     # left
            sub[:, -border:].ravel()     # right
        ])

        # use the median of those border pixels as the background
        bg_guess = float(np.median(edges))

        A0 = float(sub.max() - bg_guess)
        if A0 < 4*sub.std():
            return (None,)*5

        # initial parameters and bounds
        x0_guess = float(np.clip(cx - x1, 0, crop_size - 1))
        y0_guess = float(np.clip(cy - y1, 0, crop_size - 1))
        x0_min = max(0.0, x0_guess - 4.0)
        x0_max = min(float(crop_size), x0_guess + 4.0)
        y0_min = max(0.0, y0_guess - 4.0)
        y0_max = min(float(crop_size), y0_guess + 4.0)
        # parameters now: if bg_fixed is None → [A, x0, y0, sx, sy, off]
        #                else           → [A, x0, y0, sx, sy]
        if bg_fixed is None:
            p0 = [A0, x0_guess, y0_guess,
                  crop_size/8, crop_size/8, bg_guess]
            lb = [0, x0_min, y0_min, sigma_min, sigma_min, -np.inf]
            ub = [np.inf, x0_max, y0_max, sigma_max, sigma_max, np.inf]
        else:
            p0 = [A0, x0_guess, y0_guess,
                  crop_size/8, crop_size/8]
            lb = [0, x0_min, y0_min, sigma_min, sigma_min]
            ub = [np.inf, x0_max, y0_max, sigma_max, sigma_max]

        generic_p0 = p0.copy()
        # Keep free-background fits on their original initialization path.
        # Those fits are often used for thresholded colocalization decisions,
        # where even a rare alternate local solution can flip a binary result.
        used_warm_start = previous_popt is not None and bg_fixed is not None

        # A recentered second pass is the same fit with a better crop. Reuse
        # the shape and brightness already found by the first pass instead of
        # making the optimizer rediscover them from generic guesses. Keep the
        # recentered x/y guesses above because those coordinates are local to
        # the new crop.
        if used_warm_start:
            p0[0] = previous_popt[0]
            p0[3] = previous_popt[3]
            p0[4] = previous_popt[4]

        # choose which model function / fitting tuple to call
        if bg_fixed is None:
            fit_func = gaussian2d_flat  # expects 6 params
            bounds = (lb, ub)
        else:
            # wrap a 5-parameter version:
            def gaussian5_flat(xy, A, x0, y0, sx, sy):
                return gaussian2d_flat(
                    xy, A, x0, y0, sx, sy, bg_fixed
                )

            fit_func = gaussian5_flat
            bounds = (lb, ub)

        def _solve(initial_parameters):
            popt, _ = curve_fit(
                fit_func,
                (xi_full, yi_full),
                sub.ravel(),
                p0=initial_parameters,
                bounds=bounds,
                sigma=sigma_arr_full.ravel(),
                max_nfev=max_nfev,
                method='trf'
            )
            return popt

        used_generic_fallback = False
        try:
            popt = _solve(p0)
        except Exception:
            if not used_warm_start:
                return (None,)*5
            try:
                popt = _solve(generic_p0)
                used_generic_fallback = True
            except Exception:
                return (None,)*5

        tol = 4
        acceptable = (
            tol < popt[1] < crop_size - tol and
            tol < popt[2] < crop_size - tol and
            (it != iterations - 1 or popt[0] >= 4.0)
        )
        if used_warm_start and not used_generic_fallback and not acceptable:
            try:
                popt = _solve(generic_p0)
            except Exception:
                return (None,)*5

        previous_popt = popt

        # unpack the fit
        if bg_fixed is None:
            A, x0_fit, y0_fit, sx, sy, off = popt
        else:
            A, x0_fit, y0_fit, sx, sy = popt
            off = bg_fixed

        # reject edge / bad fits
        if not (tol < x0_fit < crop_size - tol and tol < y0_fit < crop_size - tol):
            return (None, None, None, None, None)
        
        # map back to full-image coords, compute intensity/peak…
        fitted_center = (x1 + x0_fit, y1 + y0_fit)
        avg_sig = 0.5*(sx + sy)
        intensity = 2*np.pi * A * sx * sy
        peak = A

        if it == iterations-1:

            if A<4.0:
                return (None, None, None, None, None)
            
            return (fitted_center, avg_sig, float(intensity),
                    float(peak), float(off))

    return (None,)*5

def find_minima(x: np.ndarray) -> np.ndarray:
    """
    Return indices i where x[i] is a local minimum.
    Equivalent to: upordown = sign(diff(x)), and look for -1→+1.
    """
    dx = np.diff(x)
    upordown = np.sign(dx)  # +1 => rising, -1 => falling, 0 => flat
    # an index i (1≤i≤len(x)-2) is a local minimum if upordown[i-1]<0 and upordown[i]>0
    # We also include boundaries if they “look like” minima.
    # Build boolean flags for each index in the original x:
    # Note: len(upordown) = len(x)-1, so we shift indexes by 1.
    first_flag = (upordown[0] > 0)       # treat x[0] as min if it starts by rising
    last_flag  = (upordown[-1] < 0)      # treat x[-1] as min if it ends by falling
    middle_flags = np.concatenate((
        [False],
        (np.diff(upordown) > 0),
        [False]
    ))
    # Combine them into a single boolean array of length len(x)
    flags = np.empty(len(x), dtype=bool)
    flags[:] = False
    flags[0] = first_flag
    flags[-1] = last_flag
    flags[1:-1] = middle_flags[1:-1]
    return np.nonzero(flags)[0]

def find_maxima(x: np.ndarray) -> np.ndarray:
    """
    Return indices i where x[i] is a local maximum.
    Equivalent to: upordown = sign(diff(x)), and look for +1→-1.
    """
    dx = np.diff(x)
    upordown = np.sign(dx)
    first_flag = (upordown[0] < 0)
    last_flag  = (upordown[-1] > 0)
    middle_flags = np.concatenate((
        [False],
        (np.diff(upordown) < 0),
        [False]
    ))
    flags = np.empty(len(x), dtype=bool)
    flags[:] = False
    flags[0] = first_flag
    flags[-1] = last_flag
    flags[1:-1] = middle_flags[1:-1]
    return np.nonzero(flags)[0]


def filterX(x0: np.ndarray, W: int, passes: int) -> dict:
    """
    Edge-preserving smoother + pseudo-derivative.
    - x0: 1D array of raw intensities (no NaNs).
    - W: half-window size.
    - passes: number of smoothing iterations.
    Returns a dict with:
      'I0': original x0,
      'I' : smoothed result,
      'Px': pseudo-derivative = forward_mean - backward_mean
    """
    r = 10
    NT = x0.size
    x = x0.copy().astype(float)

    for _ in range(passes):
        # Pad on both ends by repeating the endpoint W times
        left_pad  = np.full(W, x[0])
        right_pad = np.full(W, x[-1])
        xpad = np.concatenate((left_pad, x, right_pad))

        # Pre-allocate arrays
        xavefor = np.empty(NT)
        xvarfor = np.empty(NT)
        xavebak = np.empty(NT)
        xvarbak = np.empty(NT)

        # For each i in [0 .. NT-1], correspond to xpad index i+W
        for i in range(NT):
            forward_slice  = xpad[i+W : i+W+W+1]    # length = W+1
            backward_slice = xpad[i : i+W+1]        # length = W+1
            xavefor[i] = np.mean(forward_slice)
            xvarfor[i] = np.var(forward_slice, ddof=0)
            xavebak[i] = np.mean(backward_slice)
            xvarbak[i] = np.var(backward_slice, ddof=0)

        rsp = np.power(xvarfor, r)
        rsm = np.power(xvarbak, r)
        denom = rsp + rsm
        # avoid divide-by-zero: if denom==0, give equal weight
        zero_denom = (denom == 0)
        denom[zero_denom] = 1.0
        gm = rsp / denom
        gp = rsm / denom
        x = gp * xavefor + gm * xavebak

    # After the last pass, recompute forward/backward means once more for Px:
    left_pad  = np.full(W, x[0])
    right_pad = np.full(W, x[-1])
    xpad = np.concatenate((left_pad, x, right_pad))
    xavefor = np.empty(NT)
    xavebak = np.empty(NT)
    for i in range(NT):
        xavefor[i] = np.mean(xpad[i+W : i+W+W+1])
        xavebak[i] = np.mean(xpad[i : i+W+1])

    Px = xavefor - xavebak

    return {"I0": x0.astype(float), "I": x, "Px": Px}
