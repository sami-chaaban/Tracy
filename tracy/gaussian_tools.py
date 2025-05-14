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
    250nm is the assumed full width at half maximum (FWHM) of your optical PSF
    (i.e. the diameter of the blur spot produced by diffraction in your microscope,
    which is often on the order of 200–300 nm for visible light).
    A Gaussian’s FWHM is related to its standard deviation σ by:
    FWHM = 2 sqrt(2ln(2))*sigma
    The leading factor of 2 enforces a minimum width of twice the PSF σ (so ~212 nm),
    to guard against spuriously sharp fits that would be smaller than what optics can actually resolve
    """
    sigma_min = 1.0
    if pixelsize is not None:
        sigma_min = 2*(250/2.355)/pixelsize
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
        # parameters now: if bg_fixed is None → [A, x0, y0, sx, sy, off]
        #                else           → [A, x0, y0, sx, sy]
        if bg_fixed is None:
            p0 = [A0, crop_size/2, crop_size/2,
                  crop_size/8, crop_size/8, bg_guess]
            lb = [0, crop_size/2-4, crop_size/2-4, sigma_min, sigma_min, -np.inf]
            ub = [np.inf, crop_size/2+4, crop_size/2+4, sigma_max, sigma_max, np.inf]
        else:
            p0 = [A0, crop_size/2, crop_size/2,
                  crop_size/8, crop_size/8]
            lb = [0, crop_size/2-4, crop_size/2-4, sigma_min, sigma_min]
            ub = [np.inf, crop_size/2+4, crop_size/2+4, sigma_max, sigma_max]

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

        try:
            popt, _ = curve_fit(
                fit_func,
                (xi_full, yi_full),
                sub.ravel(),
                p0=p0,
                bounds=bounds,
                sigma=sigma_arr_full.ravel(),
                max_nfev=max_nfev,
                method='trf'
            )
        except Exception:
            return (None,)*5

        # unpack the fit
        if bg_fixed is None:
            A, x0_fit, y0_fit, sx, sy, off = popt
        else:
            A, x0_fit, y0_fit, sx, sy = popt
            off = bg_fixed

        tol=4
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
