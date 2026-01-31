from ._shared import *
from .extra_calculations import ExtraCalculationSpec

class NavigatorAnalysisMixin:
    def _extra_calc_specs(self):
        return [
            ExtraCalculationSpec(
                key="steps",
                label="Calculate Steps",
                action_attr="showStepsAction",
                toggle_handler="on_show_steps_toggled",
                has_popup=True,
                checks_existing=True,
                supports_segments=True,
            ),
            ExtraCalculationSpec(
                key="diffusion",
                label="Calculate Diffusion (D, α)",
                action_attr="showDiffusionAction",
                toggle_handler="on_show_diffusion_toggled",
                has_popup=True,
                checks_existing=True,
                supports_segments=True,
            ),
            ExtraCalculationSpec(
                key="colocalization",
                label="Calculate Colocalization",
                action_attr="colocalizationAction",
                toggle_handler="on_colocalization_toggled",
                has_popup=False,
                checks_existing=True,
                supports_segments=True,
            ),
        ]
    def _capture_movie_bg(self):
        """Called when axes‐transition animation completes."""
        mc     = self.movieCanvas
        canvas = mc.figure.canvas
        # grab the clean background for blitting
        mc._bg     = canvas.copy_from_bbox(mc.ax.bbox)
        mc._roi_bg = canvas.copy_from_bbox(mc.ax.bbox)

        # **recompute** zoom_center & scale from the *actual* new xlim/ylim**
        x0, x1 = mc.ax.get_xlim()
        y0, y1 = mc.ax.get_ylim()
        mc.zoom_center = ((x0 + x1) * 0.5, (y0 + y1) * 0.5)
        w = mc.width() or 1
        # horizontal data‐span divided by widget width gives new scale
        mc.scale = (x1 - x0) / w

    def compute_trajectory_background(self, get_frame, points, crop_size):
        half = crop_size // 2
        all_values = []

        for f, cx, cy in points:
            img = get_frame(f)
            if img is None:
                continue
            H, W = img.shape
            x0, y0 = int(round(cx)), int(round(cy))

            # Compute the slice indices and also flags for truncation
            x_start = x0 - half
            x_end   = x0 + half
            y_start = y0 - half
            y_end   = y0 + half

            left_trunc   = x_start < 0
            right_trunc  = x_end   > W
            top_trunc    = y_start < 0
            bottom_trunc = y_end   > H

            sub = img[
                max(0, y_start):min(H, y_end),
                max(0, x_start):min(W, x_end)
            ]
            if sub.size == 0:
                continue

            # collect border pixels only from the *un*-truncated sides
            h_sub, w_sub = sub.shape
            border = max(1, int(min(h_sub, w_sub) * 0.1))

            edges = []
            if not top_trunc:
                edges.append(sub[:border, :].ravel())
            if not bottom_trunc:
                edges.append(sub[-border:, :].ravel())
            if not left_trunc:
                edges.append(sub[:, :border].ravel())
            if not right_trunc:
                edges.append(sub[:, -border:].ravel())

            if edges:
                all_values.append(np.concatenate(edges))

        if not all_values:
            return None
        all_values = np.concatenate(all_values)
        return float(np.median(all_values))

    def run_analysis_points(self):
        if not hasattr(self, "analysis_points") or len(self.analysis_points) < 2:
            return
        points = sorted(self.analysis_points, key=lambda pt: pt[0])

        trajectory_background = self.compute_trajectory_background(
            self.get_movie_frame,
            self.analysis_points,
            crop_size=int(2 * self.searchWindowSpin.value())
        )

        try:
            frames, coords, search_centers, ints, fits, background = self._compute_analysis(points, trajectory_background)
        except Exception as e:
            QMessageBox.warning(self, "", "There was an error adding computing this trajectory. Please try again (consider a longer trajectory or different radius).")
            print(f"_compute failed: {e}")
            self._is_canceled = True
        
        if self._is_canceled:
            return
        
        self.analysis_start, self.analysis_end = points[0], points[-1]
        self.analysis_frames, self.analysis_original_coords, self.analysis_search_centers = frames, coords, search_centers
        self.analysis_intensities, self.analysis_fit_params, self.analysis_background = ints, fits, background
        self.analysis_trajectory_background = trajectory_background

        # compute avg & median
        valid = [v for v in ints if v is not None and v > 0]
        self.analysis_avg = float(np.mean(valid)) if valid else None
        self.analysis_median = float(np.median(valid)) if valid else None

        # last fit summary
        if fits and fits[-1] is not None:
            _, self.analysis_sigma, self.analysis_peak = fits[-1]
        else:
            self.analysis_sigma = self.analysis_peak = None

        spot_centers = [p for (p,_,_) in fits]  # list of (x,y) or None
        self.analysis_velocities = calculate_velocities(spot_centers)
        valid_velocities = [v for v in self.analysis_velocities if v is not None]
        self.analysis_average_velocity = float(np.mean(valid_velocities)) if valid_velocities else None

        if getattr(self, 'check_colocalization', False) and self.movie.ndim == 4:
            self._compute_colocalization()
        else:
            # fill with Nones if turned off or single‐channel
            N = len(self.analysis_frames)
            self.analysis_colocalized = [None] * N
            # *also* define per-channel dict of None‐lists
            ref_ch    = self.analysis_channel
            n_chan    = self.movie.shape[self._channel_axis] if self._channel_axis is not None else 1
            self.analysis_colocalized_by_ch = {
                ch: [None]*N for ch in range(1, n_chan+1) if ch != ref_ch
            }

        if getattr(self, "show_steps", False):
            (
                self.analysis_step_indices,
                self.analysis_step_medians
            ) = self.compute_steps_for_data(
                self.analysis_frames,
                self.analysis_intensities
            )
        else:
            self.analysis_step_indices = None
            self.analysis_step_medians = None

        if getattr(self, "show_diffusion", False):
            D, alpha = self.compute_diffusion_for_data(self.analysis_frames, spot_centers)
            self.analysis_diffusion_D = D
            self.analysis_diffusion_alpha = alpha
        else:
            self.analysis_diffusion_D = None
            self.analysis_diffusion_alpha = None

        # slider
        if hasattr(self, 'analysisSlider'):
            s = self.analysisSlider
            s.blockSignals(True)
            s.setRange(0, len(frames)-1)
            s.setValue(0)
            s.blockSignals(False)

        self.trajectoryCanvas.hide_empty_columns()
        self.analysis_motion_state = None
        self.analysis_motion_segments = None

    def _compute_analysis(self, points, bg=None, showprogress=True):
        def _normalize_result(result):
            if result is None:
                return (None, None, None, None, None, None)
            if not isinstance(result, (tuple, list)):
                return (None, None, None, None, None, None)
            if len(result) == 6:
                return result
            if len(result) == 5:
                frames, coords, centers, ints, fit_params = result
                background = None
                try:
                    background = [None] * len(ints) if ints is not None else None
                except Exception:
                    background = None
                return frames, coords, centers, ints, fit_params, background
            raise ValueError(f"_compute_analysis returned {len(result)} values; expected 6.")

        mode = self.tracking_mode
        if mode == "Independent":
            return _normalize_result(self._compute_independent(points, bg, showprogress))
        elif mode == "Tracked":
            return _normalize_result(self._compute_tracked(points, bg, showprogress))
        elif mode == "Smooth":
            # 1) do the independent pass
            try:
                frames, coords, search_centers, ints, fit_params, background = (
                    self._compute_independent(points, bg, showprogress)
                )
            except Exception as e:
                print(f"_compute_independent failed: {e}")
                self._is_canceled = True #REMOVE THIS?
                return None, None, None, None, None, None
            return _normalize_result(self._postprocess_smooth(frames, coords, ints, fit_params, background, bg))
        elif mode == "Same center":
            return _normalize_result(self._compute_same_center(points, bg, showprogress))
        else:
            raise ValueError(f"Unknown mode {mode!r}")

    def _compute_same_center(self, points, bg=None, showprogress=True):
        """
        points: list of (frame, cx, cy) tuples.
        Refit a Gaussian at each (cx,cy) in exactly each frame.
        """
        # 1) Collect all frames
        all_frames = [f for f,_,_ in points]

        # 2) Preload images
        frame_cache = {f: self.get_movie_frame(f) for f in set(all_frames)}

        # 3) Prepare outputs
        N = len(all_frames)
        all_coords             = []
        integrated_intensities = [None] * N
        background             = [None] * N
        fit_params             = [(None, None, None)] * N

        # 4) (Optional) progress dialog
        progress = None
        if showprogress and N > 50 and not getattr(self, "_suppress_internal_progress", False):
            progress = QProgressDialog("Re‑fitting at same centers…", "Cancel", 0, N, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.show()

        # 5) Loop once per point, exactly at the provided cx,cy
        for idx, (f, cx, cy) in enumerate(points):
            all_coords.append((cx, cy))
            img = frame_cache.get(f)
            if img is not None:
                # reuse gaussian fit call
                fc, sigma, intensity, peak, bkgr = perform_gaussian_fit(
                    img,
                    (cx, cy),
                    int(2 * self.searchWindowSpin.value()),
                    pixelsize=self.pixel_size,
                    bg_fixed=bg
                )
                if fc is not None:
                    background[idx]            = max(0, bkgr)
                    fit_params[idx]            = (fc, sigma, peak)
                    integrated_intensities[idx] = max(0, intensity)
            # otherwise leave None/grey

            # update progress
            if progress:
                progress.setValue(idx+1)
                QApplication.processEvents()
                if progress.wasCanceled():
                    self._is_canceled = True
                    progress.close()
                    break

        if progress:
            progress.close()

        return all_frames, all_coords, all_coords, integrated_intensities, fit_params, background

    def _compute_independent(self, points, bg=None, showprogress=True):  

        # print("compute", self._is_canceled)

        all_frames = []
        for i in range(len(points)-1):
            f1, _, _ = points[i]
            f2, _, _ = points[i+1]
            seg = list(range(f1, f2+1)) if i==0 else list(range(f1+1, f2+1))
            all_frames.extend(seg)
        frame_cache = {f: self.get_movie_frame(f) for f in set(all_frames)}

        # 2) Prepare outputs
        N = len(all_frames)
        all_coords = []
        integrated_intensities = [None]*N
        background             = [None] * N
        fit_params            = [(None,None,None)]*N

        # 3) Progress dialog once N > 50
        progress = None
        if showprogress and N > 50 and not getattr(self, "_suppress_internal_progress", False):
            progress = QProgressDialog("Processing...", "Cancel", 0, N, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setAutoClose(False)
            progress.show()

        # 4) Walk each segment in turn
        idx = 0
        for i in range(len(points)-1):
            if getattr(self, "_is_canceled", False):
                if progress:
                    progress.close()
                return all_frames, all_coords, all_coords, integrated_intensities, fit_params, background
            f1, x1, y1 = points[i]
            f2, x2, y2 = points[i+1]
            seg = list(range(f1, f2+1)) if i==0 else list(range(f1+1, f2+1))
            n = len(seg)

            for j, f in enumerate(seg):
                if getattr(self, "_is_canceled", False):
                    if progress:
                        progress.close()
                    return all_frames, all_coords, all_coords, integrated_intensities, fit_params, background
                # compute independent center
                t = j/(n-1) if n>1 else 0
                cx = x1 + t*(x2-x1)
                cy = y1 + t*(y2-y1)
                all_coords.append((cx, cy))
                img = frame_cache[f]
                fc, sigma, intensity, peak, bkgr = None, None, None, None, None
                if img is not None:
                    fc, sigma, intensity, peak, bkgr = perform_gaussian_fit(
                        img, (cx, cy), int(2 * self.searchWindowSpin.value()), pixelsize = self.pixel_size, bg_fixed=bg
                    )
                if fc is not None:
                    is_retrack = (
                        self.avoid_previous_spot
                        and any(
                            pf == f and
                            np.hypot(fc[0] - px, fc[1] - py) < self.same_spot_threshold
                            for pf, px, py in self.past_centers
                        )
                    )
                    if not is_retrack:
                        fit_params[idx]            = (fc, sigma, peak)
                        background[idx]            = max(0, bkgr)
                        integrated_intensities[idx] = max(0, intensity)
                # else: leave None / grey
                # t1 = time.perf_counter()
                # print(f"1 {(t1 - t0)*1000:.2f} ms")
                idx += 1
                # update progress & allow cancel
                if progress:
                    progress.setValue(idx)
                    QApplication.processEvents()
                    if progress.wasCanceled():
                        self._is_canceled = True
                        progress.close()
                        return all_frames, all_coords, all_coords, integrated_intensities, fit_params, background

        if progress:
            progress.close()

        return all_frames, all_coords, all_coords, integrated_intensities, fit_params, background

    def _compute_tracked(self, points, bg=None, showprogress=True):

        search_radius = int(2 * self.searchWindowSpin.value())
        pixel_size    = self.pixel_size
        points_pairs  = zip(points, points[1:])

        # ---------------- Tracked Mode ----------------
        # 1) Build the full list of frames to process
        segments = []
        for (f1, *_), (f2, *_) in points_pairs:
            start = f1
            end   = f2
            # include f1 only on the first segment
            if segments:
                start += 1
            segments.append(range(start, end+1))

        all_frames = [f for seg in segments for f in seg]
        frame_cache = {f: self.get_movie_frame(f) for f in set(all_frames)}

        # 3) Prepare output containers
        independent_centers    = []
        new_centers            = []
        integrated_intensities = []
        fit_params             = []
        background             = []

        # 4) Progress dialog
        total_frames = len(all_frames)
        progress = None
        if showprogress and total_frames > 50 and not getattr(self, "_suppress_internal_progress", False):
            progress = QProgressDialog("Processing...", "Cancel", 0, total_frames, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setAutoClose(False)
            progress.show()

        # 5) Sequentially track through each segment
        current_center = (points[0][1], points[0][2])
        processed = 0

        for i in range(len(points) - 1):
            f1, x1, y1 = points[i]
            f2, x2, y2 = points[i+1]
            seg_frames = (
                range(f1, f2+1)
                if i == 0
                else range(f1+1, f2+1)
            )
            n = len(seg_frames)

            for j, f in enumerate(seg_frames):
                # — compute interpolated center
                t = j/(n-1) if n > 1 else 0
                icx = x1 + t*(x2 - x1)
                icy = y1 + t*(y2 - y1)
                independent_centers.append((icx, icy))

                # — do the fit & blend
                new_center, fc, sigma, intensity, peak, bkgr = \
                    self._track_frame(
                        f,
                        frame_cache[f],
                        icx, icy,
                        current_center,
                        search_radius,
                        pixel_size,
                        bg
                    )

                new_centers.append(new_center)
                if fc is not None:
                    integrated_intensities.append(max(0, intensity))
                    fit_params.append((fc, sigma, peak))
                    background.append(bkgr)
                else:
                    integrated_intensities.append(None)
                    fit_params.append((None, None, None))
                    background.append(None)

                current_center = new_center

                # — update progress per frame
                processed += 1
                if progress:
                    progress.setValue(processed)
                    QApplication.processEvents()
                    if progress.wasCanceled():
                        self._is_canceled = True
                        progress.close()
                        return (
                            all_frames,
                            independent_centers,
                            new_centers,
                            integrated_intensities,
                            fit_params,
                            background,
                        )

            if self._is_canceled:
                break

        # 6) clean up progress dialog
        if progress:
            progress.setValue(total_frames)
            progress.close()

        # 7) return frames, independent centers, blended centers, and fit results
        return all_frames, independent_centers, new_centers, integrated_intensities, fit_params, background

    def _track_frame(self, framenum, img, icx, icy, current, radius, pixel_size, bg=None):
        
        nc = ((current[0]+icx)/2, (current[1]+icy)/2)

        if img is None:
            # fallback to midpoint
            return nc, None, None, None, None, None

        fc, sigma, intensity, peak, bkgr = perform_gaussian_fit(
            img, current, radius,
            pixelsize=pixel_size,
            bg_fixed=bg
        )
        if fc is None:
            return nc, None, None, None, None, None

        if self.avoid_previous_spot and fc is not None:
            for pf, px, py in self.past_centers:
                if pf == framenum and np.hypot(fc[0] - px, fc[1] - py) < self.same_spot_threshold:
                    return (nc, None, None, None, None, None)

        dx, dy = fc[0]-icx, fc[1]-icy
        d       = np.hypot(dx, dy)
        w       = np.exp(-0.5*(d/radius))
        nc      = (w*fc[0] + (1-w)*icx, w*fc[1] + (1-w)*icy)

        return nc, fc, sigma, intensity, peak, bkgr

    def _postprocess_smooth(self, all_frames, all_coords, ints, fit_params, background, bg_fixed=None):
        N = len(fit_params)
        # 1) pull out raw spot centers (None → nan, nan)
        spot_centers = np.array([
            (fc[0], fc[1]) if fc is not None else (np.nan, np.nan)
            for fc, _, _ in fit_params
        ], dtype=float)  # shape (N,2)

        # 2) linearly interpolate over gaps
        idx = np.arange(N)
        valid = ~np.isnan(spot_centers[:,0])
        if valid.sum() < 2:
            # Not enough valid points to interpolate → bail out
            return all_frames, all_coords, all_coords, ints, fit_params, background

        x_filled = np.interp(idx, idx[valid], spot_centers[valid,0])
        y_filled = np.interp(idx, idx[valid], spot_centers[valid,1])
        filled_centers = np.vstack([x_filled, y_filled]).T

        window = 11 if N >= 11 else (N // 2) * 2 + 1
        polyorder = 2

        sx = savgol_filter(x_filled, window_length=window,
                        polyorder=polyorder, mode='interp')
        sy = savgol_filter(y_filled, window_length=window,
                        polyorder=polyorder, mode='interp')
        smooth_centers = np.vstack([sx, sy]).T

        # 4) compute deviations between raw & smoothed
        deviations = np.linalg.norm(filled_centers - smooth_centers, axis=1)

        # 5) threshold (e.g. min(3px, 2×σ_good))
        sigmas = np.array([p[1] for p in fit_params], float)
        good_sigma = np.nanmean(sigmas)
        pix_thresh   = 3.0
        sigma_thresh = 2.0 * good_sigma
        thresh = min(pix_thresh, sigma_thresh)

        # 6) find anomalies
        anomalies = np.where(deviations > thresh)[0]

        # 7) re‑fit each anomalous frame at the smoothed center
        for i in anomalies:
            cx, cy = smooth_centers[i]
            # all_coords[i] = (cx, cy)    # use smoothed for next pass
            radius = int(np.ceil(4 * good_sigma))
            img = self.get_movie_frame(all_frames[i])
            if img is None:
                continue

            fc, sx, intensity, peak, bkgr = perform_gaussian_fit(
                img, (cx, cy),
                crop_size=radius,
                pixelsize=self.pixel_size,
                bg_fixed=bg_fixed
            )
            if fc is not None:
                # overwrite spot_centers and returned list
                # all_coords[i]       = tuple(fc)
                fit_params[i]       = (fc, sx, peak)
                ints[i]             = intensity
                background[i]       = bkgr
            else:
                fit_params[i]       = (None, None, None)
                ints[i]             = None
                background[i]       = None

        # new_centers = np.array([
        #     (fc[0], fc[1]) if fc is not None else (np.nan, np.nan)
        #     for fc, _, _ in fit_params
        # ], dtype=float)  # shape (N,2)
        # self.debugPlotRequested.emit(
        #     spot_centers.tolist(),
        #     smooth_centers.tolist(),
        #     new_centers.tolist()
        # )

        return all_frames, all_coords, all_coords, ints, fit_params, background

    def _coloc_flags_for_frame(self, frame, center):
        """
        Returns a tuple (any_flag, {ch:flag, ...}) for a single frame & ref‐center.
        Flags are "Yes"/"No"/None.
        """
        ref_ch = self.analysis_channel
        n_chan = self.movie.shape[self._channel_axis]

        # initialize
        flags_by_ch = {}
        any_flag = None

        if center is None:
            # no fit → all None
            return None, {ch: None for ch in range(1, n_chan+1) if ch != ref_ch}

        x0,y0 = center
        per_ch = {}
        for tgt_ch in range(1, n_chan+1):
            if tgt_ch == ref_ch:
                continue
            img = self.get_movie_frame(frame, channel_override=tgt_ch)
            ok = False
            if img is not None:
                fc2, *_ = perform_gaussian_fit(
                    img, (x0,y0),
                    crop_size=int(2*self.searchWindowSpin.value()),
                    pixelsize=self.pixel_size,
                    bg_fixed=None
                )
                if fc2 is not None and np.hypot(fc2[0]-x0, fc2[1]-y0) <= self.colocalization_threshold:
                    ok = True
            per_ch[tgt_ch] = "Yes" if ok else "No"

        # overall any
        any_flag = "Yes" if any(v=="Yes" for v in per_ch.values()) else "No"
        return any_flag, per_ch
    
    def _compute_colocalization(self, showprogress=True):
        frames      = self.analysis_frames
        centers     = [fp[0] for fp in self.analysis_fit_params]
        ref_ch      = self.analysis_channel
        n_chan      = self.movie.shape[self._channel_axis]
        N           = len(frames)

        # storage
        any_list      = [None]*N
        results_by_ch = {ch: [None]*N for ch in range(1, n_chan+1) if ch != ref_ch}

        progress = None
        if showprogress and N > 20:
            progress = QProgressDialog("Checking colocalization…", "Cancel", 0, N, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.show()

        for i, (frame, center) in enumerate(zip(frames, centers)):
            if progress:
                progress.setValue(i)
                QApplication.processEvents()
                if progress.wasCanceled():
                    break

            any_flag, per_ch = self._coloc_flags_for_frame(frame, center)
            any_list[i] = any_flag
            for ch, flag in per_ch.items():
                results_by_ch[ch][i] = flag

        if progress:
            progress.setValue(N)
            progress.close()

        self.analysis_colocalized       = any_list
        self.analysis_colocalized_by_ch = results_by_ch

    def _compute_colocalization_for_row(self, row: int):
        traj = self.trajectoryCanvas.trajectories[row]

        # 1) stash old context
        old_frames  = self.analysis_frames
        old_params  = self.analysis_fit_params
        old_channel = self.analysis_channel

        # 2) point “analysis_*” at this trajectory
        self.analysis_frames     = traj["frames"]
        self.analysis_fit_params = list(zip(
            traj["spot_centers"],
            traj["sigmas"],
            traj["peaks"]
        ))
        self.analysis_channel    = traj["channel"]

        # 3) compute all flags
        self._compute_colocalization(showprogress=True)

        # 4) **store the raw lists** on the traj dict
        traj["colocalization_any"]       = list(self.analysis_colocalized)
        traj["colocalization_by_ch"]     = {
            ch: list(flags)
            for ch, flags in self.analysis_colocalized_by_ch.items()
        }

        # 5) now write percentages back into custom_fields & table
        cf      = traj.setdefault("custom_fields", {})
        n_chan  = self.movie.shape[self._channel_axis]
        valid_any = [f for f in self.analysis_colocalized if f is not None]
        pct_any   = (
            f"{100*sum(1 for f in valid_any if f=='Yes')/len(valid_any):.1f}"
            if valid_any else ""
        )

        for ch in range(1, n_chan+1):
            col = f"Ch. {ch} co. %"
            if ch == self.analysis_channel:
                cf[col] = ""
            elif n_chan == 2:
                cf[col] = pct_any
            else:
                flags = self.analysis_colocalized_by_ch.get(ch, [])
                valid = [f for f in flags if f is not None]
                cf[col] = (
                    f"{100*sum(1 for f in valid if f=='Yes')/len(valid):.1f}"
                    if valid else ""
                )
            self.trajectoryCanvas._mark_custom(row, col, cf[col])

        # 6) restore old context
        self.analysis_frames, self.analysis_fit_params, self.analysis_channel = (
            old_frames, old_params, old_channel
        )

    def _remove_past_centers(self, centers_to_remove):
        """
        Remove any (pf,px,py) in self.past_centers that lies within
        self.same_spot_threshold of any (cf,cx,cy) in centers_to_remove *and* pf==cf.
        """
        if not centers_to_remove:
            return

        # 1) Build valid (frame,x,y) list
        valid = [
            (f, x, y)
            for f, x, y in centers_to_remove
            if isinstance(f, (int,float))
            and isinstance(x, (int,float))
            and isinstance(y, (int,float))
        ]
        if not valid:
            return

        # 2) Keep only those past-centers that are either a different frame
        #    or are farther than threshold on the same frame.
        kept = []
        for pf, px, py in self.past_centers:
            drop = False
            for cf, cx, cy in valid:
                if pf == cf and np.hypot(px - cx, py - cy) < self.same_spot_threshold:
                    drop = True
                    break
            if not drop:
                kept.append((pf, px, py))
        self.past_centers = kept

    # @pyqtSlot(list, list, list)
    # def debug_plot_track_smoothing(self, spot_centers, smooth_centers, new_centers):
    #     """
    #     Show raw vs. rolling‐average smoothed track in a Qt dialog,
    #     so it runs safely on the Qt main thread.
    #     """
    #     spotcenters = np.array(spot_centers, dtype=float)
    #     smoothed = np.array(smooth_centers, dtype=float)
    #     newcenters = np.array(new_centers, dtype=float)

    #     # build a Matplotlib Figure (no plt.show())
    #     fig = plt.Figure(figsize=(12,4))
    #     ax1 = fig.add_subplot(1,3,1)
    #     ax2 = fig.add_subplot(1,3,2)
    #     ax3 = fig.add_subplot(1,3,3)

    #     # raw
    #     ax1.plot(spotcenters[:,0], spotcenters[:,1], '-o', markersize=6)
    #     ax1.set_title("Raw Track")
    #     ax1.set_xlabel("X"); ax1.set_ylabel("Y")
    #     ax1.grid(True)

    #     # smoothed
    #     ax2.plot(smoothed[:,0], smoothed[:,1], '-o', markersize=6)
    #     ax2.set_title(f"Smoothed")
    #     ax2.set_xlabel("X"); ax2.set_ylabel("Y")
    #     ax2.grid(True)

    #     # smoothed
    #     ax3.plot(newcenters[:,0], newcenters[:,1], '-o', markersize=6)
    #     ax3.set_title(f"New")
    #     ax3.set_xlabel("X"); ax2.set_ylabel("Y")
    #     ax3.grid(True)

    #     # embed in a Qt dialog
    #     dlg = QDialog(self)
    #     dlg.setWindowTitle("Track Smoothing Debug")
    #     layout = QVBoxLayout(dlg)
    #     canvas = FigureCanvas(fig)
    #     layout.addWidget(canvas)
    #     dlg.setLayout(layout)

    #     # draw & show
    #     canvas.draw()
    #     dlg.exec_()

    def toggle_looping(self):
        self.set_roi_mode(False)
        self.movieCanvas.manual_zoom = False
        if len(self.trajectoryCanvas.trajectories) == 0:
            return
        if self.looping:
            self.stoploop()
            self.jump_to_analysis_point(self.loop_index-1, animate="discrete")
        else:
            if self.sumBtn.isChecked():
                self.sumBtn.setChecked(False)
            if hasattr(self.intensityCanvas, "current_index") and self.intensityCanvas.current_index is not None:
                self.loop_index = int(self.intensityCanvas.current_index)
            else: 
                self.loop_index = 0
            self.jump_to_analysis_point(self.loop_index, animate="discrete")
            self.loopTimer.start()
            self.looping = True
            self.flash_message("Playback started")

    def loop_points(self):
        # Only loop if both start and end have been set.
        if self.analysis_start is None or self.analysis_end is None:
            return
        if not self.analysis_frames:
            return
        self.jump_to_analysis_point(self.loop_index, animate="discrete")
        self.intensityCanvas.current_index = self.loop_index
        self.loop_index = (self.loop_index + 1) % len(self.analysis_frames)

    def stoploop(self, prompt=True):
        self.looping = False
        self.loopTimer.stop()
        if prompt and self.movie is not None:
            self.flash_message("Playback stopped")

        # ——— 1) Full redraw ———
        # Force everything to repaint so the axes+lines are current
        self.intensityCanvas.fig.canvas.draw()

        # ——— 2) Rebuild the background for blitting ———
        # Grab a clean snapshot of the plot (no highlight) for future restore
        self.intensityCanvas._bg = self.intensityCanvas.fig.canvas.copy_from_bbox(
            self.intensityCanvas.ax_bottom.bbox
        )

        # ——— 3) Highlight the current point ———
        # Now that _bg is valid, this draws the marker
        self.intensityCanvas.highlight_current_point()

        # ——— 4) Ensure the canvas actually shows it ———
        self.intensityCanvas.fig.canvas.blit(self.intensityCanvas.ax_bottom.bbox)

        image, center, crop_size, sigma, intensity, background, peak, pointcolor = self.histogramCanvas._last_histogram_params
        self.histogramCanvas._do_update_histogram(image, center, crop_size, sigma, intensity, background, peak, pointcolor)

    # def compute_step_features(self, spot_centers, frame_interval_ms, pixel_size_nm):
    #     """
    #     Given spot_centers = list of (x,y) or None,
    #     returns:
    #     times     : array of frame times (s)
    #     feats     : array shape (N_steps,2) of [speed (nm/s), persistence]
    #     valid_idx : list of step-indices i corresponding to feats[i]
    #     """
    #     dt = frame_interval_ms/1000.0
    #     # build list of valid positions
    #     idxs, pts = [], []
    #     for i,c in enumerate(spot_centers):
    #         if c is not None:
    #             idxs.append(i); pts.append(np.array(c))
    #     pts = np.vstack(pts)
    #     # compute frame times
    #     times = np.array(idxs)*dt

    #     # displacements (dx,dy) for each step
    #     d = pts[1:] - pts[:-1]              # shape (M-1,2)
    #     speeds = np.linalg.norm(d,axis=1)*pixel_size_nm/dt

    #     # persistence: cosine between consecutive displacements
    #     # (first step has no previous, set persistence=0)
    #     pers = np.zeros_like(speeds)
    #     vprev = d[0]
    #     for i in range(1, len(d)):
    #         vcur = d[i]
    #         # dot/|v||v| → cos(theta)
    #         denom = np.linalg.norm(vprev)*np.linalg.norm(vcur)
    #         if denom>0:
    #             pers[i] = np.dot(vprev, vcur)/denom
    #         else:
    #             pers[i] = 0.0
    #         vprev = vcur

    #     # assemble feature matrix
    #     feats = np.column_stack([speeds, pers])
    #     # step i in feats corresponds to frame idxs[i]→idxs[i+1], let's record target frames
    #     valid_idx = idxs[1:]
    #     return times, feats, valid_idx

    # def smooth_track(self, spot_centers, window=5, polyorder=2):
    #     """
    #     Apply Savitzky-Golay smoothing *within* each contiguous sub-track.
    #     Gaps (None) are left untouched.
    #     """
    #     xs = np.array([c[0] if c is not None else np.nan for c in spot_centers])
    #     ys = np.array([c[1] if c is not None else np.nan for c in spot_centers])

    #     # find contiguous non-NaN runs
    #     isn = ~np.isnan(xs)
    #     for start in np.where((~isn[:-1]) & (isn[1:]))[0]+1:
    #         pass
    #     # simpler: use pandas
    #     import pandas as pd
    #     df = pd.DataFrame({'x': xs, 'y': ys})
    #     df['x'] = df['x'].interpolate().fillna(method='bfill').fillna(method='ffill')
    #     df['y'] = df['y'].interpolate().fillna(method='bfill').fillna(method='ffill')

    #     df['xs'] = savgol_filter(df['x'], window, polyorder)
    #     df['ys'] = savgol_filter(df['y'], window, polyorder)

    #     # re-insert NaNs at original gaps
    #     df.loc[~isn, ['xs','ys']] = np.nan
    #     return list(zip(df['xs'], df['ys']))

    # def segment_track_hmm(
    #     self, spot_centers, frame_interval_ms, pixel_size_nm,
    #     n_states=3, p_self=0.95, min_dwell=5,
    #     random_state=0, n_iter=500
    # ):
    #     """
    #     Sticky HMM on (speed, persistence) with:
    #     – dropping NaN‐rows before fitting
    #     – standard scaling of features
    #     – full→diag fallback covariance
    #     – posterior‐based re‐assignment of <min_dwell flickers
    #     – per‐track label_map stored on model
    #     Returns:
    #     state_seq (T,), segments list, model, disp_full, vel_full, posteriors (N_steps,n_states)
    #     """
    #     # 0) smooth
    #     spot_centers = self.smooth_track(spot_centers)
    #     # 1) compute raw features
    #     times, feats, step_frames = self.compute_step_features(
    #         spot_centers, frame_interval_ms, pixel_size_nm
    #     )
    #     T = len(spot_centers)

    #     # 2) drop any rows with NaN in feats
    #     valid = ~np.isnan(feats).any(axis=1)
    #     feats = feats[valid]
    #     step_frames = [sf for sf, ok in zip(step_frames, valid) if ok]

    #     if feats.shape[0] < n_states:
    #         # too few valid steps
    #         return (np.full(T, np.nan), [], None,
    #                 np.full(T, np.nan), np.full(T-1, np.nan), None)

    #     # 3) standardize (guard zero‐variance)
    #     scaler = StandardScaler().fit(feats)
    #     scaler.scale_[scaler.scale_ == 0] = 1.0
    #     feats_scaled = scaler.transform(feats)

    #     # 4) HMM fit w/ sticky prior and fallback
    #     lengths = [len(feats_scaled)]
    #     def make_model(cov_type):
    #         m = GaussianHMM(
    #             n_components=n_states,
    #             covariance_type=cov_type,
    #             n_iter=n_iter,
    #             random_state=random_state,
    #             init_params='st',
    #             params='stmc'
    #         )
    #         m.startprob_ = np.ones(n_states) / n_states
    #         trans = np.full((n_states, n_states), (1 - p_self) / (n_states - 1))
    #         np.fill_diagonal(trans, p_self)
    #         m.transmat_ = trans
    #         return m

    #     model = make_model('full')
    #     try:
    #         model.fit(feats_scaled, lengths)
    #     except Exception:
    #         model = make_model('diag')
    #         model.fit(feats_scaled, lengths)

    #     # 5) decode + posterior
    #     posteriors = model.predict_proba(feats_scaled)
    #     hidden     = model.predict(feats_scaled)

    #     # 6) build full-length outputs
    #     disp_full = np.full(T, np.nan)
    #     vel_full  = np.full(T-1, np.nan)
    #     state_seq = np.full(T,   np.nan)

    #     # cumulative displacement
    #     disp, last = 0.0, None
    #     for i, c in enumerate(spot_centers):
    #         if c is None:
    #             last = None
    #         else:
    #             if last is None:
    #                 disp_full[i] = 0.0
    #             else:
    #                 step = np.hypot(c[0]-last[0], c[1]-last[1]) * pixel_size_nm
    #                 disp += step
    #                 disp_full[i] = disp
    #             last = c

    #     # fill velocity and raw state_seq at step-frames
    #     for k, f in enumerate(step_frames):
    #         vel_full[f-1] = feats[k, 0]
    #         state_seq[f]  = hidden[k]

    #     # 7) segment the hidden path
    #     raw = []
    #     prev_k, prev_s = 0, hidden[0]
    #     for k, s in enumerate(hidden[1:], start=1):
    #         if s != prev_s:
    #             raw.append({
    #                 'start_frame': step_frames[prev_k],
    #                 'end_frame':   step_frames[k],
    #                 'state':       prev_s,
    #                 'idxs':        list(range(prev_k, k))
    #             })
    #             prev_k, prev_s = k, s
    #     raw.append({
    #         'start_frame': step_frames[prev_k],
    #         'end_frame':   step_frames[-1]+1,
    #         'state':       prev_s,
    #         'idxs':        list(range(prev_k, len(hidden)))
    #     })

    #     # 8) reassign any too-short bursts
    #     for i, seg in enumerate(raw):
    #         if len(seg['idxs']) < min_dwell:
    #             neigh = []
    #             if i>0:        neigh.append(raw[i-1]['state'])
    #             if i<len(raw)-1: neigh.append(raw[i+1]['state'])
    #             if neigh:
    #                 scores = {c: posteriors[seg['idxs'], c].sum() for c in neigh}
    #                 seg['state'] = max(scores, key=scores.get)

    #     # 9) merge contiguous same-state
    #     segments = []
    #     for seg in raw:
    #         if not segments or seg['state'] != segments[-1]['state']:
    #             segments.append({
    #                 'start': seg['start_frame'],
    #                 'end':   seg['end_frame'],
    #                 'state': seg['state']
    #             })
    #         else:
    #             segments[-1]['end'] = seg['end_frame']

    #     # 10) per-track label map
    #     mus   = model.means_
    #     order = np.argsort(mus[:,0] + mus[:,1])
    #     label_map = {order[0]:'static',
    #                 order[1]:'diffusive',
    #                 order[2]:'processive'}
    #     for seg in segments:
    #         seg['label'] = label_map[seg['state']]

    #     # finalize full state_seq
    #     state_seq[:] = np.nan
    #     for seg in segments:
    #         state_seq[seg['start']:seg['end']] = seg['state']

    #     # stash for later
    #     model.scaler_    = scaler
    #     model.label_map_ = label_map

    #     return state_seq, segments, model, disp_full, vel_full, posteriors

    # @pyqtSlot(np.ndarray, np.ndarray, np.ndarray, list, np.ndarray)
    # def debug_plot_hmm_segmentation(
    #     self, times, vel, disp, segments, posteriors=None
    # ):
    #     """
    #     Plot HMM segmentation with colored backgrounds for each regime.
    #     If posteriors are provided, confidence can be overlaid.
    #     """
    #     colormap = {
    #         'static':     'gray',
    #         'diffusive':  'blue',
    #         'processive': 'green'
    #     }

    #     fig, (ax_v, ax_d) = plt.subplots(2,1,figsize=(8,6),sharex=True)

    #     # shaded regimes + point‐wise plots
    #     for seg in segments:
    #         s, e, lbl = seg['start'], seg['end'], seg['label']
    #         c = colormap.get(lbl, 'black')
    #         # shade background
    #         ax_v.add_patch(Rectangle(
    #             (times[s], ax_v.get_ylim()[0]),
    #             times[e-1]-times[s],
    #             ax_v.get_ylim()[1]-ax_v.get_ylim()[0],
    #             color=c, alpha=0.1, zorder=0
    #         ))
    #         ax_d.add_patch(Rectangle(
    #             (times[s], ax_d.get_ylim()[0]),
    #             times[e-1]-times[s],
    #             ax_d.get_ylim()[1]-ax_d.get_ylim()[0],
    #             color=c, alpha=0.1, zorder=0
    #         ))

    #         # velocity trace if ≥2 points
    #         if e - s >= 2:
    #             ax_v.plot(times[s+1:e], vel[s:e-1], '-o',
    #                     color=c, markersize=4)
    #         # displacement
    #         ax_d.plot(times[s:e], disp[s:e], '-o',
    #                 color=c, markersize=4)

    #     ax_v.set_ylabel("velocity (nm/s)")
    #     ax_v.grid(True)
    #     handles = [
    #         plt.Line2D([],[],color=col,marker='o',linestyle='-')
    #         for col in colormap.values()
    #     ]
    #     ax_v.legend(handles, list(colormap.keys()), loc='upper right')

    #     ax_d.set_ylabel("disp (nm)")
    #     ax_d.set_xlabel("time (s)")
    #     ax_d.grid(True)

    #     # show dialog as before
    #     dlg = QDialog(self)
    #     dlg.setWindowTitle("HMM Motion Segmentation")
    #     layout = QVBoxLayout(dlg)
    #     canvas = FigureCanvas(plt.gcf())
    #     layout.addWidget(canvas)
    #     dlg.setLayout(layout)
    #     canvas.draw()
    #     dlg.exec_()

    def finalize_trajectory(self, analysis_points, trajid=None):
        if not analysis_points or len(analysis_points) < 2:
            return
        
        if self.sumBtn.isChecked():
            self.sumBtn.setChecked(False)

        analysis_points.sort(key=lambda pt: pt[0])
        self.analysis_points = analysis_points
        self.analysis_start, self.analysis_end = analysis_points[0], analysis_points[-1]
        
        self.run_analysis_points()

        if self._is_canceled:
            self._is_canceled=False
            return

        # add to trajectory canvas
        self.trajectoryCanvas.add_trajectory_from_navigator(trajid=trajid)

        # compute HMM segmentation + full disp/vel
        # state_seq, segments, model, disp_full, vel_full, posteriors = self.segment_track_hmm(
        #     spot_centers=spot_centers,
        #     frame_interval_ms=self.frame_interval,
        #     pixel_size_nm=self.pixel_size
        # )
        # # build absolute times array
        # times = np.array(self.analysis_frames) * (self.frame_interval / 1000.0)
        # # now plotmn
        # self.debug_plot_hmm_segmentation(times, vel_full, disp_full, segments, posteriors)

        self.trajectory_finalized = True
        self.new_sequence_start   = True
        
        self.intensityCanvas.current_index = 0
        self.loop_index = 0
        self.analysis_points = []
        self.analysis_anchors = []
        self.analysis_roi = None
        self.update_movie_analysis_line()
        self.movieCanvas.clear_manual_marker()
        # self.movieCanvas.clear_manual_marker()
        self.movieCanvas._manual_marker_active = False

        is_roi = self.modeSwitch.isChecked()
        if is_roi:
            self.set_roi_mode(False)

    def endKymoClickSequence(self):
        anchors = self.analysis_anchors
        roi = self.analysis_roi

        full_pts = []
        if len(anchors) < 2:
            return

        for i in range(len(anchors) - 1):
            f1, xk1, _yk1 = anchors[i]
            f2, xk2, _yk2 = anchors[i + 1]

            seg = list(range(f1, f2+1)) if i==0 else list(range(f1+1, f2+1))
            n   = len(seg)
            # guaranteed endpoints
            xs = np.linspace(xk1, xk2, n, endpoint=True)

            for j, f in enumerate(seg):
                xk = xs[j]
                mx, my = self.compute_roi_point(roi, xk)
                full_pts.append((f, mx, my))

        # hand off to finalize
        self.analysis_points = full_pts

        kymo_name = self.kymoCombo.currentText()
        # look up its channel in the map
        info = self.kymo_roi_map.get(kymo_name, {})
        current_kymo_ch = info.get("channel", None)

        self.analysis_channel = current_kymo_ch
        self.finalize_trajectory(self.analysis_points)
        # self.kymoCanvas.unsetCursor()

    def endMovieClickSequence(self):
        if not hasattr(self, "analysis_points") or not self.analysis_points or len(self.analysis_points) < 2:
            return
        self.analysis_anchors = []
        self.analysis_roi = None
        self.analysis_channel = int(self.movieChannelCombo.currentText()) #1 indexed
        self.finalize_trajectory(self.analysis_points)
        # self.cancel_left_click_sequence()
        # self.movieCanvas.draw_idle()

    def hasMovieClickSequence(self):
        return (
            hasattr(self, "analysis_points")
            and len(self.analysis_points) >= 2
            and not self.hasKymoClickSequence()
        )

    def hasKymoClickSequence(self):
        return (
            hasattr(self, "analysis_anchors")
            and isinstance(self.analysis_anchors, list)
            and len(self.analysis_anchors) >= 2
            and getattr(self, "analysis_roi", None) is not None
        )

    def add_or_recalculate(self):
        if self.looping:
            self.stoploop()
        if self.hasMovieClickSequence():
            self.endMovieClickSequence()
        elif self.hasKymoClickSequence():
            self.endKymoClickSequence()
        else:
            self.trajectoryCanvas.shortcut_recalculate()

    def clear_temporary_analysis_markers(self):
        # Remove the temporary analysis line (not part of a saved trajectory)
        if hasattr(self, "temp_analysis_line") and self.temp_analysis_line is not None:
            try:
                self.temp_analysis_line.remove()
            except Exception:
                pass
            self.temp_analysis_line = None

        if hasattr(self, "leftclick_temp_lines"):
            for line in self.leftclick_temp_lines:
                try:
                    line.remove()
                except Exception:
                    pass
            self.leftclick_temp_lines = []

        # Remove temporary left-click markers (if any)
        if hasattr(self, "analysis_markers") and self.analysis_markers:
            for marker in self.analysis_markers:
                if hasattr(marker, '__iter__'):
                    for m in marker:
                        try:
                            m.remove()
                        except Exception:
                            pass
                else:
                    try:
                        marker.remove()
                    except Exception:
                        pass
            self.analysis_markers = []
        if hasattr(self, "permanent_analysis_line") and self.permanent_analysis_line is not None:
            try:
                self.permanent_analysis_line.remove()
            except Exception:
                pass
            self.permanent_analysis_line = None
        # Remove any in-between dotted segments stored in permanent_analysis_lines
        if hasattr(self, "permanent_analysis_lines"):
            for seg in self.permanent_analysis_lines:
                try:
                    seg.remove()
                except Exception:
                    pass
            self.permanent_analysis_lines = []

        if hasattr(self, "temp_movie_analysis_line") and self.temp_movie_analysis_line is not None:
            try:
                self.temp_movie_analysis_line.remove()
            except Exception:
                pass

        self.movieCanvas.draw_idle()
        self.kymoCanvas.draw_idle()

    def on_show_steps_toggled(self, checked: bool):
        self.show_steps = checked

        if not checked:
            self._refresh_intensity_canvas()
            return

        # 0) detect whether any trajectory exists at all
        has_any_trajectory = bool(self.trajectoryCanvas.trajectories)

        # 1) pop the SETTINGS dialog, passing that flag
        dlg = StepSettingsDialog(
            current_W=self.W,
            current_min_step=self.min_step,
            can_calculate_all=has_any_trajectory,
            parent=self
        )
        if dlg.exec_() != QDialog.Accepted:
            # cancelled → undo toggle
            self.show_steps = False
            if isinstance(self.sender(), QAction):
                self.sender().setChecked(False)
            return

        # 2) apply new parameters
        self.W        = dlg.new_W
        self.min_step = dlg.new_min_step

        # 3) check for missing steps and optionally compute them
        self._maybe_compute_missing_steps()

        # 4) finally redraw (with or without steps)
        self._refresh_intensity_canvas()

    def _refresh_intensity_canvas(self):
        """
        Re‐draw whatever trajectory is currently selected in the IntensityCanvas.
        """
        idx = self.trajectoryCanvas.current_index
        if idx is None or idx < 0 or idx >= len(self.trajectoryCanvas.trajectories):
            return

        traj = self.trajectoryCanvas.trajectories[idx]
        frames = traj["frames"]
        intensities = traj["intensities"]
        colors = self._get_traj_colors(traj)[0]
        avg_int = None
        med_int = None

        # Pass avg/median or max_frame if used.
        self.intensityCanvas.plot_intensity(
            frames=frames,
            intensities=intensities,
            avg_intensity=avg_int,
            median_intensity=med_int,
            colors=colors,
            max_frame=None
        )

    def _compute_steps_for_trajectory(self, traj_idx: int):
        """
        Look up trajectory #traj_idx (which lives in self.trajectoryCanvas.trajectories),
        pull out its frames/intensities, call compute_steps_for_data(...), then store
        the results back onto traj["step_indices"] and traj["step_medians"].
        """
        traj = self.trajectoryCanvas.trajectories[traj_idx]
        frames      = traj["frames"]
        intensities = traj["intensities"]
        # assume self.W, self.passes, self.min_step exist:
        step_idxs, medians = self.compute_steps_for_data(frames, intensities)
        traj["step_indices"] = step_idxs        # now a List[int], not None
        traj["step_medians"] = medians          # now a List[(start,end,median)]

    def compute_steps_for_data(self, frames, intensities):
        """
        Given a list of frame‐indices and a list of (possibly‐gapped) intensities,
        return (step_indices, step_medians).  Neither argument is modified.
        """

        W = self.W
        passes = self.passes
        min_step = self.min_step

        frame_arr = np.array(frames, dtype=int)
        intensity_arr = np.array(
            [np.nan if (v is None or (isinstance(v, float) and np.isnan(v))) else float(v)
            for v in intensities],
            dtype=float
        )
        valid_mask   = ~np.isnan(intensity_arr)
        valid_frames = frame_arr[valid_mask]
        valid_ints   = intensity_arr[valid_mask]

        # If too few points, return empty lists:
        if valid_ints.size < 2:
            return [], []

        fx = filterX(valid_ints, W=W, passes=passes)
        I_smooth = fx["I"]
        P        = fx["Px"]

        # find local minima/maxima in P
        min_idx = find_minima(P)
        max_idx = find_maxima(P)

        M = P.size
        Pmin = np.zeros(M, dtype=float)
        Pmax = np.zeros(M, dtype=float)
        if min_idx.size > 0:
            Pmin[min_idx] = P[min_idx]
        if max_idx.size > 0:
            Pmax[max_idx] = P[max_idx]
        Pedge = Pmin + Pmax

        # threshold:
        thresh = min_step
        step_compact_idxs = np.where(np.abs(Pedge) > thresh)[0]
        step_frames = sorted({int(valid_frames[j]) for j in step_compact_idxs})

        first = int(valid_frames[0])
        step_frames = [f for f in step_frames if f != first] #remove edge artefact

        # build segment boundaries from first→steps→last
        first_valid = int(valid_frames[0])
        last_valid  = int(valid_frames[-1])
        if step_frames:
            boundaries = [first_valid] + [f for f in step_frames if f != first_valid]
            if boundaries[-1] != last_valid:
                boundaries.append(last_valid)
        else:
            boundaries = [first_valid, last_valid]

        seg_medians = []
        for i in range(len(boundaries) - 1):
            start_f = boundaries[i]
            end_f   = boundaries[i+1]
            mask = (valid_frames >= start_f) & (valid_frames <= end_f)
            if not np.any(mask):
                continue
            vals = I_smooth[mask]
            if vals.size == 0:
                continue
            med = float(np.median(vals))
            seg_medians.append((start_f, end_f, med))

        return step_frames, seg_medians

    def compute_diffusion_for_data(self, frames, spot_centers):
        """
        Estimate anomalous diffusion parameters from MSD:
            MSD(dt) = 4 * D * dt^alpha   (2D)
        Returns (D, alpha).

        Requires calibration (pixel size + frame interval). If scale is not set,
        this function raises a ValueError.
        """

        if self.pixel_size is None or self.frame_interval is None:
            raise ValueError(
                "Scale not set: please set pixel size and frame interval (Movie > Set Scale) before computing diffusion."
            )

        # gather valid points: (frame, x_px, y_px)
        pts = [
            (int(f), float(c[0]), float(c[1]))
            for f, c in zip(frames, spot_centers)
            if isinstance(c, (tuple, list)) and c[0] is not None and c[1] is not None
        ]
        if len(pts) < 3:
            return None, None

        pts.sort(key=lambda t: t[0])
        fr = np.array([p[0] for p in pts], dtype=int)
        xy = np.array([[p[1], p[2]] for p in pts], dtype=float)

        max_lag = int(getattr(self, "diffusion_max_lag", 10))
        min_pairs = int(getattr(self, "diffusion_min_pairs", 5))
        eps = float(getattr(self, "_EPS", 1e-12))

        n = len(xy)
        msd = []
        dts = []

        # Use actual frame deltas, not "lag * dt" blindly
        for lag in range(1, min(max_lag, n - 1) + 1):
            disp = xy[lag:] - xy[:-lag]
            sq = disp[:, 0] ** 2 + disp[:, 1] ** 2
            if sq.size < min_pairs:
                continue

            m = float(np.nanmean(sq))
            if not np.isfinite(m):
                continue

            # Clamp instead of dropping; helps keep >=2 points for the fit
            m = max(m, eps)

            # dt in "frames" from actual frame indices
            dt_frames = fr[lag:] - fr[:-lag]
            if dt_frames.size < min_pairs:
                continue
            dt = float(np.nanmean(dt_frames))  # average frame delta for this lag
            if not np.isfinite(dt) or dt <= 0:
                continue

            msd.append(m)
            dts.append(dt)

        if len(msd) < 2:
            return None, None

        msd = np.array(msd, float)
        dts = np.array(dts, float)

        # Fit log(MSD) = log(4D) + alpha*log(dt)
        x = np.log(np.maximum(dts, eps))
        y = np.log(np.maximum(msd, eps))

        A = np.vstack([np.ones_like(x), x]).T
        coef, *_ = np.linalg.lstsq(A, y, rcond=None)
        intercept, alpha = float(coef[0]), float(coef[1])

        if not np.isfinite(alpha):
            return None, None

        # Prefactor in px^2 / frame^alpha
        D = float(np.exp(intercept) / 4.0)
        if not np.isfinite(D):
            return None, alpha

        # Convert to um^2 / s^alpha if possible
        if self.pixel_size is not None:
            px_um = float(self.pixel_size) / 1000.0
            D *= (px_um ** 2)

        if self.frame_interval is not None:
            dt_frame_s = float(self.frame_interval) / 1000.0
            # since dt was in frames, convert time base: (frames)^alpha -> (seconds)^alpha
            D /= (dt_frame_s ** alpha)

        return D, alpha

    def _confirm_missing_calculation(self, label: str, count: int) -> bool:
        noun = "trajectory" if count == 1 else "trajectories"
        verb = "is" if count == 1 else "are"
        obj = "it" if count == 1 else "them"
        msg = f"{count} {noun} {verb} missing {label} data, calculate {obj}?"
        title = f"Compute {label.capitalize()}"
        return QMessageBox.question(
            self,
            title,
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        ) == QMessageBox.Yes

    def _trajectory_has_segments(self, traj: dict) -> bool:
        nodes = traj.get("nodes") or []
        anchors = traj.get("anchors") or []
        return len(nodes) >= 2 or len(anchors) >= 2

    def _find_missing_steps(self) -> list:
        missing = []
        for idx, traj in enumerate(self.trajectoryCanvas.trajectories):
            if traj.get("step_indices") is None or traj.get("step_medians") is None:
                missing.append(idx)
        return missing

    def _compute_steps_for_indices(self, indices: list) -> None:
        if not indices:
            return
        progress = QProgressDialog("Computing steps…", "Cancel", 0, len(indices), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        for i, traj_idx in enumerate(indices):
            progress.setValue(i)
            QApplication.processEvents()
            if progress.wasCanceled():
                break
            self._compute_steps_for_trajectory(traj_idx)

        progress.setValue(len(indices))
        progress.close()

    def _maybe_compute_missing_steps(self) -> None:
        if not self.trajectoryCanvas.trajectories:
            return
        missing = self._find_missing_steps()
        if not missing:
            return
        if not self._confirm_missing_calculation("steps", len(missing)):
            return
        self._compute_steps_for_indices(missing)

    def _find_missing_diffusion(self) -> tuple:
        d_col = self._DIFF_D_COL
        a_col = self._DIFF_A_COL
        missing_vals = []
        missing_segments = []
        for idx, traj in enumerate(self.trajectoryCanvas.trajectories):
            cf = traj.get("custom_fields", {})
            d_val = cf.get(d_col)
            a_val = cf.get(a_col)
            has_vals = (
                d_val is not None
                and a_val is not None
                and not (isinstance(d_val, str) and not d_val.strip())
                and not (isinstance(a_val, str) and not a_val.strip())
                and not (isinstance(d_val, float) and math.isnan(d_val))
                and not (isinstance(a_val, float) and math.isnan(a_val))
            )
            if not has_vals:
                missing_vals.append(idx)
            if self._trajectory_has_segments(traj) and not traj.get("segment_diffusion"):
                missing_segments.append(idx)
        return missing_vals, missing_segments

    def _compute_diffusion_for_trajectory(self, traj_idx: int) -> bool:
        traj = self.trajectoryCanvas.trajectories[traj_idx]
        try:
            D, alpha = self.compute_diffusion_for_data(
                traj["frames"],
                traj.get("spot_centers", [])
            )
        except ValueError as e:
            QMessageBox.critical(self, "Diffusion Error", str(e))
            return False

        cf = traj.setdefault("custom_fields", {})
        cf[self._DIFF_D_COL] = "" if D is None else f"{D:.4g}"
        cf[self._DIFF_A_COL] = "" if alpha is None else f"{alpha:.3f}"
        try:
            traj["segment_diffusion"] = self.trajectoryCanvas._compute_segment_diffusion(
                traj, self
            )
        except Exception:
            traj["segment_diffusion"] = []
        self.trajectoryCanvas.updateTableRow(traj_idx, traj)
        return True

    def _compute_segment_diffusion_for_trajectory(self, traj_idx: int) -> None:
        traj = self.trajectoryCanvas.trajectories[traj_idx]
        try:
            traj["segment_diffusion"] = self.trajectoryCanvas._compute_segment_diffusion(
                traj, self
            )
        except Exception:
            traj["segment_diffusion"] = []
        self.trajectoryCanvas.updateTableRow(traj_idx, traj)

    def _compute_diffusion_for_indices(self, missing_vals: list, missing_segments: list) -> None:
        indices = sorted(set(missing_vals) | set(missing_segments))
        if not indices:
            return

        missing_vals_set = set(missing_vals)
        progress = QProgressDialog("Computing diffusion…", "Cancel", 0, len(indices), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        for i, traj_idx in enumerate(indices):
            progress.setValue(i)
            QApplication.processEvents()
            if progress.wasCanceled():
                break
            if traj_idx in missing_vals_set:
                ok = self._compute_diffusion_for_trajectory(traj_idx)
                if not ok:
                    break
            else:
                self._compute_segment_diffusion_for_trajectory(traj_idx)

        progress.setValue(len(indices))
        progress.close()
        self._rebuild_color_by_actions()

    def _maybe_compute_missing_diffusion(self) -> None:
        if not self.trajectoryCanvas.trajectories:
            return
        missing_vals, missing_segments = self._find_missing_diffusion()
        if not missing_vals and not missing_segments:
            return
        count = len(set(missing_vals) | set(missing_segments))
        if not self._confirm_missing_calculation("diffusion", count):
            return
        self._compute_diffusion_for_indices(missing_vals, missing_segments)

    def _find_missing_colocalization(self) -> list:
        if self.movie is None or self.movie.ndim != 4:
            return []
        if self._channel_axis is None:
            return []

        n_chan = self.movie.shape[self._channel_axis]
        coloc_cols = [f"Ch. {ch} co. %" for ch in range(1, n_chan + 1)]
        missing = []

        for r, traj in enumerate(self.trajectoryCanvas.trajectories):
            ch_ref = traj["channel"]
            cf = traj.get("custom_fields", {})
            for col in coloc_cols:
                if col.endswith(f"{ch_ref} co. %"):
                    continue
                if not cf.get(col, "").strip():
                    missing.append(r)
                    break

        return missing

    def _compute_colocalization_for_indices(self, indices: list) -> None:
        if not indices:
            return

        orig_channel = getattr(self, "analysis_channel", None)
        progress = QProgressDialog("Computing colocalization…", "Cancel", 0, len(indices), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        for idx, r in enumerate(indices):
            progress.setValue(idx)
            QApplication.processEvents()
            if progress.wasCanceled():
                break

            traj = self.trajectoryCanvas.trajectories[r]
            ch_ref = traj["channel"]
            n_chan = self.movie.shape[self._channel_axis]

            self.analysis_frames = traj["frames"]
            self.analysis_fit_params = list(zip(
                traj["spot_centers"],
                traj["sigmas"],
                traj["peaks"]
            ))
            self.analysis_channel = ch_ref
            self._compute_colocalization(showprogress=False)

            any_flags = list(self.analysis_colocalized)
            by_ch = {
                tgt: list(flags)
                for tgt, flags in self.analysis_colocalized_by_ch.items()
            }

            traj["colocalization_any"] = any_flags
            traj["colocalization_by_ch"] = by_ch

            cf = traj.setdefault("custom_fields", {})
            valid_any = [s for s in any_flags if s is not None]
            pct_any = (
                f"{100*sum(1 for s in valid_any if s=='Yes')/len(valid_any):.1f}"
                if valid_any else ""
            )

            for ch in range(1, n_chan + 1):
                col_name = f"Ch. {ch} co. %"
                if ch == ch_ref:
                    cf[col_name] = ""
                elif n_chan == 2:
                    cf[col_name] = pct_any
                else:
                    flags = by_ch.get(ch, [])
                    valid = [s for s in flags if s is not None]
                    cf[col_name] = (
                        f"{100*sum(1 for s in valid if s=='Yes')/len(valid):.1f}"
                        if valid else ""
                    )

            for ch in range(1, n_chan + 1):
                self.trajectoryCanvas._mark_custom(r, f"Ch. {ch} co. %", cf[f"Ch. {ch} co. %"])

        progress.setValue(len(indices))
        progress.close()

        if orig_channel is not None:
            self.analysis_channel = orig_channel

    def on_show_diffusion_toggled(self, checked: bool):
        self.show_diffusion = checked

        # Ensure columns exist (as VALUE custom columns → auto-save/load)
        D_COL = self._DIFF_D_COL
        A_COL = self._DIFF_A_COL
        tc = self.trajectoryCanvas

        if checked:
            # Require calibration for diffusion parameters
            if self.pixel_size is None or self.frame_interval is None:
                QMessageBox.warning(
                    self,
                    "Scale not set",
                    "Please set pixel size and frame interval (Set Scale) before computing diffusion."
                )
                self.show_diffusion = False
                if isinstance(self.sender(), QAction):
                    self.sender().setChecked(False)
                return
            # add columns once
            if D_COL not in tc.custom_columns:
                tc._add_custom_column(D_COL, col_type="value")
            if A_COL not in tc.custom_columns:
                tc._add_custom_column(A_COL, col_type="value")

            has_any = bool(tc.trajectories)
            dlg = DiffusionSettingsDialog(
                current_max_lag=self.diffusion_max_lag,
                current_min_pairs=self.diffusion_min_pairs,
                can_calculate_all=has_any,
                parent=self
            )
            if dlg.exec_() != QDialog.Accepted:
                # undo toggle
                self.show_diffusion = False
                if isinstance(self.sender(), QAction):
                    self.sender().setChecked(False)
                return

            self.diffusion_max_lag = dlg.new_max_lag
            self.diffusion_min_pairs = dlg.new_min_pairs

            self._maybe_compute_missing_diffusion()
        else:
            # Turning off diffusion should NOT erase existing values.
            # It only stops future diffusion (re)calculation.
            # Keep any previously computed D/alpha in custom_fields and keep them visible in the table.
            pass

        if not checked:
            has_seg_diff = any(
                isinstance(t.get("segment_diffusion"), (list, tuple)) and t.get("segment_diffusion")
                for t in tc.trajectories
            )
            if (not has_seg_diff and isinstance(self.color_by_column, str)
                    and self.color_by_column.endswith(" (per segment)")):
                self.set_color_by(None)

        tc.hide_empty_columns()
        self._rebuild_color_by_actions()

    def _compute_diffusion_for_all_trajectories(self):
        tc = self.trajectoryCanvas
        D_COL = self._DIFF_D_COL
        A_COL = self._DIFF_A_COL

        if not tc.trajectories:
            return

        progress = QProgressDialog("Computing diffusion…", "Cancel", 0, len(tc.trajectories), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        for i, traj in enumerate(tc.trajectories):
            progress.setValue(i)
            QApplication.processEvents()
            if progress.wasCanceled():
                break

            try:
                D, alpha = self.compute_diffusion_for_data(
                    traj["frames"],
                    traj.get("spot_centers", [])
                )
            except ValueError as e:
                progress.close()
                QMessageBox.critical(self, "Diffusion Error", str(e))
                return
            cf = traj.setdefault("custom_fields", {})
            cf[D_COL] = "" if D is None else f"{D:.4g}"
            cf[A_COL] = "" if alpha is None else f"{alpha:.3f}"
            try:
                traj["segment_diffusion"] = tc._compute_segment_diffusion(traj, self)
            except Exception:
                traj["segment_diffusion"] = []
            tc.updateTableRow(i, traj)

        progress.setValue(len(tc.trajectories))
        progress.close()
        self._rebuild_color_by_actions()
