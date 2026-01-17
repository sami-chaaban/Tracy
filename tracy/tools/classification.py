from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.signal import savgol_filter

try:
    # optional; if missing, TV denoise is skipped (still works)
    from skimage.restoration import denoise_tv_chambolle
except Exception:
    denoise_tv_chambolle = None


XY = Tuple[float, float]


# ============================
# Config
# ============================
@dataclass(frozen=True)
class MotionClassificationConfig:
    """
    BI-ADD change-point segmentation + label rules (paused/diffusive/processive).

    Units:
      - positions: pixels (input)
      - pixel_size_nm: nm per pixel
      - directedness thresholds: unitless
      - path thresholds: nm
      - frames: integers
    """

    # ---- chunking ----
    max_frame_gap: int = 10               # split if gap between consecutive VALID detections > this
    min_valid_points: int = 10            # skip chunks with fewer valid points

    # ---- preprocessing (for CP signal only) ----
    smooth_window: int = 5
    smooth_polyorder: int = 2

    # ---- BI-ADD change point detection ----
    cp_window_widths: Tuple[int, ...] = tuple(range(20, 40, 2))
    cp_shift_width: int = 5
    cp_extension_width: int = 100
    cp_threshold: float = 0.25            # higher -> fewer CPs
    cp_localmax_radius: int = 3
    min_segment_length: int = 8           # minimum length after pruning

    # ---- density weighting (BI-ADD) ----
    density_max_neighbors: int = 25
    density_dist_amp: float = 2.0
    density_local_mean_window: int = 5

    # ---- TV denoise (optional, if skimage available) ----
    tv_weight: float = 3.0
    tv_eps: float = 2e-4
    tv_max_iter: int = 100
    sigmoid_beta: float = 3.0

    # ---- directionality features for processive ----
    directedness_window: int = 15
    directedness_min_path_nm: float = 300.0

    # ---- classification gates ----
    static_total_path_nm: float = 300.0           # if segment path < this => paused
    alpha_immobile_max: float = 0.35              # alpha <= -> paused
    alpha_processive_min: float = 1.30            # alpha >= and directional -> processive
    processive_min_directedness: float = 0.70     # mean directedness must exceed
    processive_min_persistence: float = 0.20      # mean persistence must exceed

    # ---- MSD alpha estimation ----
    msd_max_lag: int = 12
    msd_min_points: int = 8
    msd_eps: float = 1e-12

    # What to do if alpha fit fails (NaN/inf): "paused" or "diffusive"
    alpha_fail_label: str = "paused"


# ============================
# Public entrypoint
# ============================
def classify_motion_states(
    *,
    frames: List[int],
    centers: List[Optional[XY]],
    pixel_size_nm: float,
    frame_interval_ms: float,
    cfg: MotionClassificationConfig = MotionClassificationConfig(),
) -> Tuple[List[str], List[Dict[str, int | str]]]:
    """
    Returns:
      motion_state:   list[str] len == len(frames) with {"paused","diffusive","processive","ambiguous"}
      motion_segments: list[dict] with keys {start,end,state,label}
                       start/end are frame numbers, end is exclusive.
                       state mapping: paused=0, diffusive=1, processive=2
    """
    if len(frames) != len(centers):
        raise ValueError("frames and centers must have the same length")

    n = len(frames)
    if n == 0:
        return [], []

    dt_s = float(frame_interval_ms) / 1000.0
    if dt_s <= 0:
        raise ValueError("frame_interval_ms must be > 0")
    if pixel_size_nm is None:
        raise ValueError("pixel_size_nm must be provided")

    motion_state = ["ambiguous"] * n
    motion_segments: List[Dict[str, int | str]] = []

    missing_global = np.array([c is None for c in centers], dtype=bool)

    for a, b in _chunk_by_gaps(frames, centers, cfg.max_frame_gap):
        centers_chunk = centers[a:b]
        frames_chunk = frames[a:b]
        missing = np.array([c is None for c in centers_chunk], dtype=bool)

        if int(np.sum(~missing)) < int(cfg.min_valid_points):
            continue

        x_s, y_s, valid = _interp_and_smooth_chunk(
            centers_chunk,
            smooth_window=cfg.smooth_window,
            smooth_polyorder=cfg.smooth_polyorder,
        )
        if int(valid.sum()) < int(cfg.min_valid_points):
            continue

        cps = _biadd_change_points(x_s, y_s, cfg)

        # classify each segment
        for i in range(1, len(cps)):
            si, sj = int(cps[i - 1]), int(cps[i])
            if sj - si < int(cfg.min_segment_length):
                continue

            # never label across missing stretches
            for bi, bj in _split_on_missing(si, sj, missing):
                if bj - bi < int(cfg.min_segment_length):
                    continue

                x_seg = x_s[bi:bj]
                y_seg = y_s[bi:bj]

                # alpha
                alpha = _estimate_alpha_2d(
                    x_nm=x_seg * float(pixel_size_nm),
                    y_nm=y_seg * float(pixel_size_nm),
                    dt_s=dt_s,
                    cfg=cfg,
                )

                # directionality + path
                mean_pers, mean_dir, total_path_nm = _segment_direction_stats(
                    x_seg, y_seg,
                    pixel_size_nm=float(pixel_size_nm),
                    directedness_window=int(cfg.directedness_window),
                    directedness_min_path_nm=float(cfg.directedness_min_path_nm),
                )

                label = _label_segment(
                    alpha=alpha,
                    total_path_nm=total_path_nm,
                    mean_directedness=mean_dir,
                    mean_persistence=mean_pers,
                    cfg=cfg,
                )

                # per-frame labels
                for k in range(bi, bj):
                    if not missing[k]:
                        motion_state[a + k] = label

                motion_segments.append(
                    {
                        "start": int(frames_chunk[bi]),
                        "end": int(frames_chunk[bj - 1] + 1),
                        "state": int({"paused": 0, "diffusive": 1, "processive": 2}.get(label, -1)),
                        "label": label,
                    }
                )

    # missing points forced ambiguous
    for i in np.where(missing_global)[0]:
        motion_state[int(i)] = "ambiguous"

    return motion_state, motion_segments


# ============================
# Label logic
# ============================
def _label_segment(
    *,
    alpha: float,
    total_path_nm: float,
    mean_directedness: float,
    mean_persistence: float,
    cfg: MotionClassificationConfig,
) -> str:
    # hard static guard
    if total_path_nm < float(cfg.static_total_path_nm):
        return "paused"

    if not np.isfinite(alpha):
        return cfg.alpha_fail_label

    if alpha <= float(cfg.alpha_immobile_max):
        return "paused"

    if (
        alpha >= float(cfg.alpha_processive_min)
        and mean_directedness >= float(cfg.processive_min_directedness)
        and mean_persistence >= float(cfg.processive_min_persistence)
    ):
        return "processive"

    return "diffusive"


# ============================
# Chunking + missing handling
# ============================
def _chunk_by_gaps(frames: List[int], centers: List[Optional[XY]], max_frame_gap: int) -> List[Tuple[int, int]]:
    valid_idxs = [i for i, c in enumerate(centers) if c is not None]
    if not valid_idxs:
        return []

    chunks: List[Tuple[int, int]] = []
    start = valid_idxs[0]
    prev = valid_idxs[0]

    for i in valid_idxs[1:]:
        if (frames[i] - frames[prev]) > int(max_frame_gap):
            chunks.append((start, prev + 1))
            start = i
        prev = i

    chunks.append((start, prev + 1))
    return chunks


def _split_on_missing(start: int, end_excl: int, missing: np.ndarray) -> List[Tuple[int, int]]:
    out: List[Tuple[int, int]] = []
    i = start
    while i < end_excl:
        while i < end_excl and missing[i]:
            i += 1
        if i >= end_excl:
            break
        j = i
        while j < end_excl and not missing[j]:
            j += 1
        out.append((i, j))
        i = j
    return out


def _interp_and_smooth_chunk(
    centers_chunk: List[Optional[XY]],
    *,
    smooth_window: int,
    smooth_polyorder: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    xs = np.array([c[0] if c is not None else np.nan for c in centers_chunk], float)
    ys = np.array([c[1] if c is not None else np.nan for c in centers_chunk], float)
    valid = ~np.isnan(xs) & ~np.isnan(ys)

    if valid.sum() < 2:
        return xs, ys, valid

    idx = np.arange(len(xs))
    x_f = np.interp(idx, idx[valid], xs[valid])
    y_f = np.interp(idx, idx[valid], ys[valid])

    # smoothing
    w = _ensure_odd_window(int(smooth_window), len(x_f))
    if w > 0:
        p = min(int(smooth_polyorder), w - 1)
        x_s = savgol_filter(x_f, window_length=w, polyorder=p, mode="interp")
        y_s = savgol_filter(y_f, window_length=w, polyorder=p, mode="interp")
    else:
        x_s, y_s = x_f, y_f

    return np.asarray(x_s, float), np.asarray(y_s, float), valid


def _ensure_odd_window(w: int, n: int) -> int:
    if n <= 2:
        return 0
    w = min(w, n if (n % 2 == 1) else n - 1)
    w = max(w, 3)
    if w % 2 == 0:
        w -= 1
    return w


# ============================
# Directionality features
# ============================
def _segment_direction_stats(
    x_px: np.ndarray,
    y_px: np.ndarray,
    *,
    pixel_size_nm: float,
    directedness_window: int,
    directedness_min_path_nm: float,
) -> Tuple[float, float, float]:
    if x_px.shape[0] < 2:
        return 0.0, 0.0, 0.0

    pts = np.column_stack([x_px, y_px])
    disp = pts[1:] - pts[:-1]
    step_nm = np.linalg.norm(disp, axis=1) * float(pixel_size_nm)
    total_path_nm = float(step_nm.sum())

    # persistence = cos(turn angle) between consecutive displacement vectors
    pers = np.zeros((disp.shape[0],), dtype=float)
    for i in range(1, disp.shape[0]):
        vprev = disp[i - 1]
        vcur = disp[i]
        denom = float(np.linalg.norm(vprev) * np.linalg.norm(vcur))
        pers[i] = (float(np.dot(vprev, vcur)) / denom) if denom > 0 else 0.0

    directed = _directedness_ratio(
        disp_px=disp,
        step_nm=step_nm,
        w=int(directedness_window),
        min_path_nm=float(directedness_min_path_nm),
    )

    mean_pers = float(np.nanmean(pers)) if pers.size else 0.0
    mean_dir = float(np.nanmean(directed)) if directed.size else 0.0
    return mean_pers, mean_dir, total_path_nm


def _directedness_ratio(*, disp_px: np.ndarray, step_nm: np.ndarray, w: int, min_path_nm: float) -> np.ndarray:
    S = disp_px.shape[0]
    out = np.zeros(S, dtype=float)
    if S == 0:
        return out

    w = max(1, int(w))
    min_path_nm = float(min_path_nm)
    step_len_px = np.linalg.norm(disp_px, axis=1)

    for i in range(S):
        a = max(0, i - w + 1)
        path_nm = float(step_nm[a : i + 1].sum())
        if path_nm < min_path_nm:
            out[i] = 0.0
            continue
        net_px = float(np.linalg.norm(disp_px[a : i + 1].sum(axis=0)))
        path_px = float(step_len_px[a : i + 1].sum())
        out[i] = (net_px / path_px) if path_px > 0 else 0.0

    return out


# ============================
# MSD alpha estimation
# ============================
def _estimate_alpha_2d(*, x_nm: np.ndarray, y_nm: np.ndarray, dt_s: float, cfg: MotionClassificationConfig) -> float:
    n = int(x_nm.shape[0])
    if n < int(cfg.msd_min_points):
        return float("nan")

    max_lag = min(int(cfg.msd_max_lag), n - 1)
    if max_lag < 2:
        return float("nan")

    eps = float(cfg.msd_eps)
    msd = []
    taus = []
    for lag in range(1, max_lag + 1):
        dx = x_nm[lag:] - x_nm[:-lag]
        dy = y_nm[lag:] - y_nm[:-lag]
        v = float(np.mean(dx * dx + dy * dy))
        msd.append(max(v, eps))
        taus.append(float(lag) * float(dt_s))

    lx = np.log(np.asarray(taus))
    ly = np.log(np.asarray(msd))
    a, _b = np.polyfit(lx, ly, 1)
    return float(a)


# ============================
# BI-ADD change points
# ============================
def _biadd_change_points(x: np.ndarray, y: np.ndarray, cfg: MotionClassificationConfig) -> List[int]:
    datas, ext_left = _position_extension(x, y, int(cfg.cp_extension_width))

    signal = _make_signal(datas[0], datas[1], cfg.cp_window_widths)

    density = _density_estimation(
        datas[0], datas[1],
        max_nb=int(cfg.density_max_neighbors) * 2,
        dist_amp=float(cfg.density_dist_amp),
        local_mean_window_size=int(cfg.density_local_mean_window),
    )

    if denoise_tv_chambolle is not None:
        den = denoise_tv_chambolle(
            density,
            weight=float(cfg.tv_weight),
            eps=float(cfg.tv_eps),
            max_num_iter=int(cfg.tv_max_iter),
            channel_axis=None,
        )
    else:
        den = density

    den = _sigmoid_shape(den / float(cfg.density_max_neighbors), beta=float(cfg.sigmoid_beta))
    signal = signal * den

    sliced = _slice_data(signal, ext_left=ext_left, shift_width=int(cfg.cp_shift_width))
    score = _slice_normalize(sliced)

    cps: List[int] = []
    for det_cp in np.where(score > float(cfg.cp_threshold))[0]:
        cps.append(_local_maximum(score, int(det_cp), radius=int(cfg.cp_localmax_radius)))

    cps = sorted(set([c for c in cps if c is not None and c >= 0]))
    cps = sorted(set([0] + cps + [len(x)]))

    # prune cps that create too-short segments
    min_len = int(cfg.min_segment_length)
    while True:
        removed = False
        for i in range(1, len(cps) - 1):
            if (cps[i] - cps[i - 1] < min_len) or (cps[i + 1] - cps[i] < min_len):
                cps.pop(i)
                removed = True
                break
        if not removed:
            break

    return cps


def _subtraction(xs: np.ndarray) -> np.ndarray:
    out = np.zeros_like(xs, dtype=float)
    out[1:] = xs[1:] - xs[:-1]
    return out


def _position_extension(x: np.ndarray, y: np.ndarray, ext_width: int) -> Tuple[np.ndarray, int]:
    datas = []
    ext_left = 0

    for data in (x, y):
        nprev = min(data.shape[0], ext_width)
        nnext = min(data.shape[0], ext_width)

        delta_prev = -_subtraction(data[:nprev])[1:]
        if delta_prev.size:
            delta_prev[0] += float(data[0])
        prev_data = np.cumsum(delta_prev)[::-1] if delta_prev.size else np.array([], dtype=float)

        delta_next = -_subtraction(data[data.shape[0] - nnext:][::-1])[1:]
        if delta_next.size:
            delta_next[0] += float(data[-1])
        next_data = np.cumsum(delta_next) if delta_next.size else np.array([], dtype=float)

        ext_data = np.concatenate((prev_data, data, next_data))
        datas.append(ext_data)
        ext_left = int(prev_data.shape[0])

    return np.array(datas), ext_left


def _make_signal(x_pos: np.ndarray, y_pos: np.ndarray, win_widths: Tuple[int, ...]) -> np.ndarray:
    all_vals = []
    for win_width in win_widths:
        win_width = int(win_width)
        if win_width >= len(x_pos):
            continue

        vals = []
        half = win_width // 2
        for checkpoint in range(half, len(x_pos) - half):
            xs = x_pos[checkpoint - half: checkpoint + half]
            ys = y_pos[checkpoint - half: checkpoint + half]

            xs1 = xs[1: len(xs)//2 + 1] - float(xs[1: len(xs)//2 + 1][0])
            xs2 = xs[len(xs)//2:]       - float(xs[len(xs)//2:][0])
            ys1 = ys[1: len(ys)//2 + 1] - float(ys[1: len(ys)//2 + 1][0])
            ys2 = ys[len(ys)//2:]       - float(ys[len(ys)//2:][0])

            cum_xs1 = np.abs(np.cumsum(np.abs(xs1)))
            cum_xs2 = np.abs(np.cumsum(np.abs(xs2)))
            cum_ys1 = np.abs(np.cumsum(np.abs(ys1)))
            cum_ys2 = np.abs(np.cumsum(np.abs(ys2)))

            xs_max = max(float(np.max(np.abs(cum_xs1))), float(np.max(np.abs(cum_xs2))), 1e-12)
            ys_max = max(float(np.max(np.abs(cum_ys1))), float(np.max(np.abs(cum_ys2))), 1e-12)

            cum_xs1 = cum_xs1 / xs_max
            cum_xs2 = cum_xs2 / xs_max
            cum_ys1 = cum_ys1 / ys_max
            cum_ys2 = cum_ys2 / ys_max

            vals.append(
                (np.abs(cum_xs1[-1] - cum_xs2[-1] + cum_ys1[-1] - cum_ys2[-1]))
                + (max(np.std(xs1), np.std(xs2)) - min(np.std(xs1), np.std(xs2)))
                + (max(np.std(ys1), np.std(ys2)) - min(np.std(ys1), np.std(ys2)))
            )

        vals = np.concatenate((np.zeros(half), np.array(vals), np.zeros(half)))
        all_vals.append(vals)

    return np.array(all_vals, dtype=float) + 1e-5


def _slice_data(signal_seq: np.ndarray, ext_left: int, shift_width: int) -> np.ndarray:
    slices = []
    for i in range(ext_left, signal_seq.shape[1] - ext_left, 1):
        crop = signal_seq[:, i - shift_width // 2: i + shift_width // 2]
        slices.append(crop)
    return np.array(slices)


def _slice_normalize(slices: np.ndarray) -> np.ndarray:
    val = np.mean(np.sum(slices, axis=2).T, axis=0)
    val = val - np.min(val)
    mx = float(np.max(val))
    return val / (mx if mx > 0 else 1.0)


def _local_maximum(signal: np.ndarray, cp: int, radius: int) -> int:
    while True:
        vals = [signal[x] if 0 <= x < signal.shape[0] else -1 for x in range(cp - radius, cp + 1 + radius)]
        if not vals:
            return -1
        new_cp = cp + int(np.argmax(vals)) - int(radius)
        if new_cp == cp:
            return int(new_cp)
        cp = int(new_cp)


def _sigmoid_shape(x: np.ndarray, beta: float) -> np.ndarray:
    x = np.minimum(np.ones_like(x) * 0.999, x)
    x = np.maximum(np.ones_like(x) * 0.001, x)
    return 1.0 / (1.0 + (x / (1.0 - x)) ** (-beta))


def _density_estimation(
    x: np.ndarray,
    y: np.ndarray,
    max_nb: int,
    *,
    dist_amp: float,
    local_mean_window_size: int,
) -> np.ndarray:
    densities = []
    for i in range(x.shape[0]):
        density1 = 0.0
        density2 = 0.0

        # left
        slice_x = x[max(0, i - max_nb // 2):i].copy()
        slice_y = y[max(0, i - max_nb // 2):i].copy()
        if slice_x.size:
            mean_dist = float(np.sqrt(_subtraction(slice_x) ** 2 + _subtraction(slice_y) ** 2).mean()) * float(dist_amp)
            slice_x -= slice_x[len(slice_x) // 2]
            slice_y -= slice_y[len(slice_y) // 2]
            density1 = float(np.sum(np.sqrt(slice_x**2 + slice_y**2) < mean_dist))

        # right
        slice_x = x[i:min(x.shape[0], i + max_nb // 2)].copy()
        slice_y = y[i:min(x.shape[0], i + max_nb // 2)].copy()
        if slice_x.size:
            mean_dist = float(np.sqrt(_subtraction(slice_x) ** 2 + _subtraction(slice_y) ** 2).mean()) * float(dist_amp)
            slice_x -= slice_x[len(slice_x) // 2]
            slice_y -= slice_y[len(slice_y) // 2]
            density2 = float(np.sum(np.sqrt(slice_x**2 + slice_y**2) < mean_dist))

        densities.append(max(density1, density2))

    # local mean
    new_densities = []
    w = int(local_mean_window_size)
    for i in range(len(densities)):
        a = max(0, i - w // 2)
        b = min(len(densities), i + w // 2 + 1)
        new_densities.append(float(np.mean(densities[a:b])))

    return np.array(new_densities, dtype=float)