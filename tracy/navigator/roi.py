from ._shared import *

class NavigatorRoiMixin:
    def compute_roi_point(self, roi, kymo_xdata):
        roi_x = np.array(roi["x"], dtype=float)
        roi_y = np.array(roi["y"], dtype=float)
        if roi_x.size < 2:
            return (roi_x[0], roi_y[0])
        
        # Compute segment lengths and cumulative lengths
        diffs = np.sqrt(np.diff(roi_x)**2 + np.diff(roi_y)**2)
        cum_lengths = np.concatenate(([0], np.cumsum(diffs)))
        total_length = cum_lengths[-1]
        
        # Compute the fractional distance along the ROI (keep as float)
        roi_x = np.array(roi["x"], dtype=float)
        roi_y = np.array(roi["y"], dtype=float)
        lengths = np.hypot(np.diff(roi_x), np.diff(roi_y))
        total_length = lengths.sum()
        kymo_width = max(int(total_length), 2)
        frac = kymo_xdata / kymo_width
        target_dist = frac * total_length
        
        # Use np.interp for smooth interpolation along ROI
        x_orig = np.interp(target_dist, cum_lengths, roi_x)
        y_orig = np.interp(target_dist, cum_lengths, roi_y)
        return (x_orig, y_orig)

    def overlay_all_rois(self):
        # 1) clear old overlays
        for attr in ("roi_lines", "roi_texts"):
            for obj in getattr(self.movieCanvas, attr, []):
                try: obj.remove()
                except: pass

        self.movieCanvas.roi_lines = []
        self.movieCanvas.roi_texts = []

        # halo style (only for the selected ROI line)
        halo_color = "#7da1ff"
        halo_lw    = 3
        halo_alpha = 0.7
        halo_effects = [
            pe.Stroke(linewidth=halo_lw+2, foreground=halo_color, alpha=halo_alpha),
            pe.Normal()
        ]

        selected_roi = self.roiCombo.currentText()

        for roi_name, roi in self.rois.items():
            if "x" not in roi or "y" not in roi:
                continue

            xs = np.array(roi["x"], dtype=float)
            ys = np.array(roi["y"], dtype=float)

            # draw the core ROI line
            line, = self.movieCanvas.ax.plot(
                xs, ys,
                color="#81C784",
                linewidth=2.5,
                solid_capstyle="round",
                alpha=0.8
            )
            # only give the halo to the currently selected ROI
            if roi_name == selected_roi:
                line.set_path_effects(halo_effects)

            self.movieCanvas.roi_lines.append(line)

            # material green 500
            base_green   = "#81C784"
            # material green 300
            lighter_green = "#81C784"

            if roi_name == selected_roi:
                label_face  = lighter_green   # lighter fill when selected
                label_alpha = 0.8
            else:
                label_face  = base_green      # same dark green when not selected
                label_alpha = 0.4

            # annotate with matching highlight
            cx, cy = xs.mean(), ys.mean()
            txt = self.movieCanvas.ax.annotate(
                roi_name,
                xy=(cx, cy),    # anchor at first ROI coordinate
                xytext=(10, -10),            
                textcoords="offset points",
                color="white", fontsize=10, fontweight='bold',
                ha="center", va="center",
                bbox=dict(
                    boxstyle="round,pad=0.2",
                    facecolor=label_face,
                    alpha=label_alpha
                )
            )
            self.movieCanvas.roi_texts.append(txt)

        self.movieCanvas.draw_idle()

    def toggle_roi_overlay(self):
        if not self.roi_overlay_active:
            # Turn overlay on and draw all ROIs
            self.roi_overlay_active = True
            self.overlay_all_rois()
        else:
            # Turn overlay off: remove all ROI lines and texts.
            self.roi_overlay_active = False
            if hasattr(self.movieCanvas, "roi_lines"):
                for line in self.movieCanvas.roi_lines:
                    try:
                        line.remove()
                    except Exception:
                        pass
                self.movieCanvas.roi_lines = []
            if hasattr(self.movieCanvas, "roi_texts"):
                for txt in self.movieCanvas.roi_texts:
                    try:
                        txt.remove()
                    except Exception:
                        pass
                self.movieCanvas.roi_texts = []
            self.movieCanvas.draw_idle()

    def update_roi_overlay_if_active(self):
        if self.roi_overlay_active:
            self.overlay_all_rois()


    def generate_rois_from_trajectories(self):
        """
        For each unique ROI referenced by a trajectory, re-create that ROI
        on the kymo canvas by setting roiPoints and calling finalize_roi().
        Switches to the correct channel for each ROI before finalizing.
        """

        # 1) collect unique ROI dicts in order
        unique_rois = []
        for traj in self.trajectoryCanvas.trajectories:
            roi = traj.get("roi")
            if not isinstance(roi, dict):
                continue
            if roi not in unique_rois:
                unique_rois.append(roi)

        if not unique_rois:
            QMessageBox.warning(
                self,
                "",
                "No ROIs Found"
            )
            return

        progress = QProgressDialog("Generating kymographs…", "Cancel", 0, len(unique_rois), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        # 2) for each ROI dict...
        for idx, roi in enumerate(unique_rois):
            progress.setValue(idx)
            QApplication.processEvents()
            if progress.wasCanceled():
                break

            # 2a) find its name key in self.rois
            roi_name = None
            for name, roi_data in self.rois.items():
                if roi_data is roi:
                    roi_name = name
                    break

            # 2b) pick channel from the first matching trajectory
            channel = None
            for traj in self.trajectoryCanvas.trajectories:
                if traj.get("roi") is roi and traj.get("channel") is not None:
                    channel = traj["channel"]
                    break


            if channel is not None:
                self._select_channel(channel)

            # 2c) replay the ROI
            self.movieCanvas.roiPoints = roi["points"]
            self.movieCanvas.finalize_roi(suppress_display=True)

        progress.setValue(len(unique_rois))
        progress.close()

        self._last_roi = None
        self.kymoCanvas.manual_zoom = False
        self.update_kymo_list_for_channel()
        if self.kymoCombo.count() > 0:
            self.kymoCombo.blockSignals(True)
            self.kymoCombo.setCurrentIndex(0)
            self.kymoCombo.blockSignals(False)
            self.kymo_changed()
        self.update_kymo_visibility()

        self.kymoCanvas.draw_trajectories_on_kymo()
        self.kymoCanvas.draw_idle()

    def compute_kymo_x_from_roi(self, roi, x_orig, y_orig, kymo_width):
        if x_orig is None:
            return None
        cache = self._compute_roi_cache(roi)   # (xr, yr, cum, total)
        if cache[3] <= 0:
            return None
        return self._compute_kymo_x(cache, x_orig, y_orig, kymo_width)

    def _compute_roi_cache(self, roi):
        xr = np.asarray(roi["x"], float)
        yr = np.asarray(roi["y"], float)
        if xr.size < 2:
            return xr, yr, np.array([0.0]), 0.0
        seg_lengths = np.hypot(np.diff(xr), np.diff(yr))
        cumulative  = np.concatenate(([0.0], np.cumsum(seg_lengths)))
        return xr, yr, cumulative, cumulative[-1]

    def _compute_kymo_x(self, cache, x_orig, y_orig, kymo_width):
        xr, yr, cum, total = cache
        # find best projection
        best_dist = np.inf
        best_along = 0.0
        for i in range(len(xr) - 1):
            xA, yA = xr[i], yr[i]
            xB, yB = xr[i+1], yr[i+1]
            seg_vx, seg_vy = xB-xA, yB-yA
            seg_len_sq = seg_vx**2 + seg_vy**2
            if seg_len_sq == 0:
                continue
            # projection parameter
            t = ((x_orig-xA)*seg_vx + (y_orig-yA)*seg_vy) / seg_len_sq
            t = np.clip(t, 0.0, 1.0)
            xp = xA + t*seg_vx
            yp = yA + t*seg_vy
            d = (xp-x_orig)**2 + (yp-y_orig)**2
            if d < best_dist:
                best_dist = d
                best_along = cum[i] + t * np.sqrt(seg_len_sq)
        frac = best_along / total
        return frac * kymo_width

    # def on_analysis_slider_changed(self, index):
    #     self.movieCanvas.manual_zoom = True
    #     if self.looping:
    #         self.stoploop()
    #     # Sync intensity canvas
    #     self.intensityCanvas.current_index = index

    #     # 1) Full update of movie and kymo contexts
    #     self.jump_to_analysis_point(index, animate="discrete")

    #     mc = self.movieCanvas
    #     mc.draw()  
    #     canvas = mc.figure.canvas
    #     mc._bg     = canvas.copy_from_bbox(mc.ax.bbox)
    #     mc._roi_bg = canvas.copy_from_bbox(mc.ax.bbox)

    #     # 1) redraw static trajectories & cache background
    #     self.kymoCanvas.draw_trajectories_on_kymo()
    #     # remove any existing marker
    #     if getattr(self.kymoCanvas, "_marker", None) is not None:
    #         try:
    #             self.kymoCanvas._marker.remove()
    #         except Exception:
    #             pass
    #         self.kymoCanvas._marker = None
    #     self.kymoCanvas.update_view()

    #     # 2) now overlay just the little magenta/grey X at the current point
    #     if not self.analysis_frames or not self.analysis_search_centers:
    #         return
    #     n = len(self.analysis_frames)
    #     if index < 0 or index >= n:
    #         return
    #     frame = self.analysis_frames[index]
    #     cx, cy = self.analysis_search_centers[index]

    #     # pick fitted vs raw
    #     fc = None
    #     if hasattr(self, "analysis_fit_params") and index < len(self.analysis_fit_params):
    #         fc, sigma, peak = self.analysis_fit_params[index]
    #     use_center = fc if fc is not None else (cx, cy)
    #     x0, y0 = use_center

    #     kymo_name = self.kymoCombo.currentText()
    #     if kymo_name and kymo_name in self.kymographs and self.rois:
    #         roi = self.rois[self.roiCombo.currentText()]
    #         if is_point_near_roi(use_center, roi):
    #             xk = self.compute_kymo_x_from_roi(
    #                 roi, x0, y0, self.kymographs[kymo_name].shape[1]
    #             )
    #             if xk is not None:
    #                 disp_frame = (self.movie.shape[0] - 1) - frame
    #                 color = self.get_point_color() if fc is not None else "grey"
    #                 self.kymoCanvas.add_circle(xk, disp_frame, color=color)
