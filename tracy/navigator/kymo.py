from ._shared import *

class NavigatorKymoMixin:
    def _set_kymo_label_hover_cursor(self, hovering: bool, in_image: bool = False, color=None):
        if not hasattr(self, "kymoCanvas"):
            return
        if not in_image:
            self._kymo_hover_cursor_color = None
            self.kymoCanvas.setCursor(Qt.ArrowCursor)
            return
        if hovering:
            self._kymo_hover_cursor_color = None
            self.kymoCanvas.setCursor(Qt.ArrowCursor)
        else:
            shade = color or "#7DA1FF"
            if getattr(self, "_kymo_hover_cursor_color", None) == shade:
                return
            cursor = self._get_kymo_hover_cursor(shade)
            self._kymo_hover_cursor_color = shade
            self.kymoCanvas.setCursor(cursor)

    def _get_kymo_hover_cursor(self, shade: str):
        cache = getattr(self, "_kymo_hover_cursor_cache", None)
        if cache is None:
            cache = {}
            self._kymo_hover_cursor_cache = cache
        cursor = cache.get(shade)
        if cursor is None:
            cursor = self._make_colored_circle_cursor(shade=shade)
            cache[shade] = cursor
        return cursor

    def _kymo_scatter_color(self, scatter, idx: int):
        try:
            facecolors = scatter.get_facecolors()
            if facecolors is None or len(facecolors) == 0:
                facecolors = scatter.get_edgecolors()
            if facecolors is None or len(facecolors) == 0:
                return None
            if len(facecolors) == 1:
                rgba = facecolors[0]
            else:
                rgba = facecolors[int(idx) % len(facecolors)]
            return mcolors.to_hex(rgba, keep_alpha=False)
        except Exception:
            return None

    def _kymo_hover_spot_color(self, event):
        canvas = getattr(self, "kymoCanvas", None)
        if canvas is None:
            return None
        scatters = getattr(canvas, "scatter_objs_traj", None)
        if not scatters:
            return None

        ordered = list(scatters)
        try:
            selected_idx = self.trajectoryCanvas.table_widget.currentRow()
        except Exception:
            selected_idx = -1
        if selected_idx is not None and selected_idx >= 0:
            sel = None
            for sc in scatters:
                if getattr(sc, "traj_idx", None) == selected_idx:
                    sel = sc
                    break
            if sel is not None:
                ordered = [sel] + [sc for sc in scatters if sc is not sel]

        for sc in ordered:
            if sc is None:
                continue
            try:
                contains, info = sc.contains(event)
            except Exception:
                continue
            if not contains:
                continue
            ind = info.get("ind") if isinstance(info, dict) else None
            if ind is None or len(ind) == 0:
                return self._kymo_scatter_color(sc, 0)
            return self._kymo_scatter_color(sc, ind[0])
        return None

    def _handle_kymo_anchor_edit_right_click(self, event):
        if event.inaxes != self.kymoCanvas.ax or event.xdata is None or event.ydata is None:
            return
        ctx = self._get_selected_kymo_traj_context()
        if ctx is None:
            return
        _, traj, roi, kymo_w, num_frames_m1 = ctx

        anchors = list(traj.get("anchors", []) or [])
        nodes = list(traj.get("nodes", []) or [])
        xs_disp, ys_disp = self._get_anchor_edit_display_points(traj, roi, kymo_w, num_frames_m1)
        if not xs_disp or not ys_disp:
            return

        ax = self.kymoCanvas.ax
        ex, ey = ax.transData.transform((event.xdata, event.ydata))

        def _pt_segment_dist(px, py, ax0, ay0, ax1, ay1):
            vx, vy = ax1 - ax0, ay1 - ay0
            wx, wy = px - ax0, py - ay0
            c1 = vx * wx + vy * wy
            if c1 <= 0:
                return np.hypot(px - ax0, py - ay0)
            c2 = vx * vx + vy * vy
            if c2 <= c1:
                return np.hypot(px - ax1, py - ay1)
            t = c1 / c2
            projx = ax0 + t * vx
            projy = ay0 + t * vy
            return np.hypot(px - projx, py - projy)

        anchor_idx = None
        anchor_dist = float("inf")
        for idx, (xk, yk) in enumerate(zip(xs_disp, ys_disp)):
            px, py = ax.transData.transform((xk, yk))
            dist = np.hypot(px - ex, py - ey)
            if dist < anchor_dist:
                anchor_dist = dist
                anchor_idx = idx

        anchor_thresh = 8.0
        line_thresh = 6.0

        if anchor_idx is not None and anchor_dist <= anchor_thresh:
            if anchors and len(anchors) <= 2:
                self.flash_message("Need at least two anchors")
                return
            if not anchors and len(nodes) <= 2:
                self.flash_message("Need at least two anchors")
                return
            self._kymo_anchor_drag_orig = {
                "anchors": list(anchors),
                "nodes": list(nodes),
            }
            if anchors:
                if anchor_idx < len(anchors):
                    anchors.pop(anchor_idx)
                    traj["anchors"] = anchors
                    if roi is not None:
                        new_nodes = []
                        for frame, ax_x, _ax_y in anchors:
                            mx, my = self.compute_roi_point(roi, ax_x)
                            new_nodes.append((int(frame), float(mx), float(my)))
                        traj["nodes"] = new_nodes
            else:
                if anchor_idx < len(nodes):
                    nodes.pop(anchor_idx)
                    traj["nodes"] = nodes
            self._kymo_anchor_drag_dirty = True
        else:
            if len(xs_disp) < 2:
                return
            best_seg = None
            best_dist = float("inf")
            for idx in range(len(xs_disp) - 1):
                x0, y0 = xs_disp[idx], ys_disp[idx]
                x1, y1 = xs_disp[idx + 1], ys_disp[idx + 1]
                p0x, p0y = ax.transData.transform((x0, y0))
                p1x, p1y = ax.transData.transform((x1, y1))
                dist = _pt_segment_dist(ex, ey, p0x, p0y, p1x, p1y)
                if dist < best_dist:
                    best_dist = dist
                    best_seg = idx

            if best_seg is None or best_dist > line_thresh:
                return

            frame_idx = num_frames_m1 - int(round(event.ydata))
            if anchors:
                f_prev = int(anchors[best_seg][0])
                f_next = int(anchors[best_seg + 1][0])
                if frame_idx <= f_prev or frame_idx >= f_next:
                    self.flash_message("Bad anchor order")
                    return
                self._kymo_anchor_drag_orig = {
                    "anchors": list(anchors),
                    "nodes": list(nodes),
                }
                anchors.insert(best_seg + 1, (int(frame_idx), float(event.xdata), float(event.ydata)))
                traj["anchors"] = anchors
                if roi is not None:
                    new_nodes = []
                    for frame, ax_x, _ax_y in anchors:
                        mx, my = self.compute_roi_point(roi, ax_x)
                        new_nodes.append((int(frame), float(mx), float(my)))
                    traj["nodes"] = new_nodes
                self._kymo_anchor_drag_dirty = True
            else:
                if best_seg + 1 > len(nodes):
                    return
                f_prev = int(nodes[best_seg][0])
                f_next = int(nodes[best_seg + 1][0])
                if frame_idx <= f_prev or frame_idx >= f_next:
                    self.flash_message("Bad anchor order")
                    return
                self._kymo_anchor_drag_orig = {
                    "anchors": list(anchors),
                    "nodes": list(nodes),
                }
                mx, my = self.compute_roi_point(roi, float(event.xdata))
                nodes.insert(best_seg + 1, (int(frame_idx), float(mx), float(my)))
                traj["nodes"] = nodes
                self._kymo_anchor_drag_dirty = True

        xs_disp, ys_disp = self._get_anchor_edit_display_points(traj, roi, kymo_w, num_frames_m1)
        if not xs_disp or not ys_disp:
            self.kymoCanvas.draw_trajectories_on_kymo()
            self.kymoCanvas.draw_idle()
            return
        line = getattr(self, "_kymo_anchor_edit_line", None)
        scatter = getattr(self, "_kymo_anchor_edit_scatter", None)
        if line is None or scatter is None:
            self._build_kymo_anchor_edit_artists()
            line = getattr(self, "_kymo_anchor_edit_line", None)
            scatter = getattr(self, "_kymo_anchor_edit_scatter", None)
        if line is None or scatter is None:
            self.kymoCanvas.draw_trajectories_on_kymo()
            self.kymoCanvas.draw_idle()
            return
        line.set_data(xs_disp, ys_disp)
        scatter.set_offsets(np.column_stack([xs_disp, ys_disp]))
        self._capture_kymo_anchor_bg()
        self.kymoCanvas.draw_idle()

    def _set_kymo_sequence_cursor(self, enabled: bool):
        if not hasattr(self, "kymoCanvas"):
            return
        cursor = getattr(self, "_kymo_sequence_cursor", None)
        if cursor is None:
            cursor = self._make_colored_circle_cursor(shade='blue')
            self._kymo_sequence_cursor = cursor
        self.kymoCanvas.setCursor(cursor)

    def _set_kymo_anchor_edit_mode(self, enabled):
        if getattr(self, "kymo_anchor_edit_mode", False) == enabled:
            return
        self.kymo_anchor_edit_mode = enabled
        self._kymo_anchor_drag = None
        self._kymo_anchor_drag_dirty = False
        self._kymo_anchor_edit_orig = None
        if not enabled:
            self._kymo_anchor_edit_line = None
            self._kymo_anchor_edit_scatter = None
            self._kymo_anchor_bg = None
        if enabled:
            try:
                self.movieCanvas.remove_gaussian_circle()
                self.movieCanvas.clear_manual_marker()
            except Exception:
                pass
            try:
                self.kymoCanvas.remove_circle()
            except Exception:
                pass
        if enabled:
            table = self.trajectoryCanvas.table_widget
            if table.currentRow() < 0 and table.rowCount() > 0:
                row = table.rowCount() - 1
                table.blockSignals(True)
                table.selectRow(row)
                table.blockSignals(False)
                self.trajectoryCanvas.on_trajectory_selected_by_index(row)
            ctx = self._get_selected_kymo_traj_context()
            if ctx is not None:
                _, traj, _roi, _kymo_w, _num_frames_m1 = ctx
                self._kymo_anchor_edit_orig = {
                    "traj": traj,
                    "anchors": list(traj.get("anchors", []) or []),
                    "nodes": list(traj.get("nodes", []) or []),
                }
        kymo_on = getattr(self, "kymo_traj_overlay_button", None) and self.kymo_traj_overlay_button.isChecked()
        movie_on = getattr(self, "traj_overlay_button", None) and self.traj_overlay_button.isChecked()
        if kymo_on:
            if enabled:
                self._build_kymo_anchor_edit_artists()
            else:
                self.kymoCanvas.draw_trajectories_on_kymo()
                self.kymoCanvas.draw_idle()
        if movie_on:
            try:
                self.movieCanvas.draw_trajectories_on_movie()
                self.movieCanvas.draw_idle()
            except Exception:
                pass

    def toggle_kymo_anchor_overlay(self):
        try:
            self.kymoCanvas.draw_trajectories_on_kymo()
            self.kymoCanvas.draw_idle()
        except Exception:
            pass

    def _finish_kymo_anchor_edit(self, force_recalc=False):
        if not getattr(self, "kymo_anchor_edit_mode", False):
            return
        was_dirty = getattr(self, "_kymo_anchor_drag_dirty", False)
        if force_recalc or was_dirty:
            ok, msg = self._validate_kymo_anchor_edit()
            if not ok:
                self._restore_kymo_anchor_drag_original()
                self.flash_message(msg)
                self._set_kymo_anchor_edit_mode(False)
                self.kymoCanvas.draw_trajectories_on_kymo()
                self.kymoCanvas.draw_idle()
                try:
                    self.movieCanvas.draw_trajectories_on_movie()
                    self.movieCanvas.draw_idle()
                except Exception:
                    pass
                return
            ctx = self._get_selected_kymo_traj_context()
            if ctx is not None:
                _, traj, _roi, _kymo_w, _num_frames_m1 = ctx
                orig = getattr(self, "_kymo_anchor_edit_orig", None)
                if orig and orig.get("traj") is traj:
                    anchors = list(traj.get("anchors", []) or [])
                    nodes = list(traj.get("nodes", []) or [])
                    if anchors == orig.get("anchors") and nodes == orig.get("nodes"):
                        was_dirty = False
                        self._kymo_anchor_drag_dirty = False
        self._set_kymo_anchor_edit_mode(False)
        if force_recalc or was_dirty:
            self.add_or_recalculate()
        else:
            self._restore_anchor_edit_view()

    def _restore_anchor_edit_view(self):
        try:
            idx = getattr(self.intensityCanvas, "current_index", 0)
            if not getattr(self, "analysis_frames", None):
                return
            if idx < 0 or idx >= len(self.analysis_frames):
                return

            self.intensityCanvas.highlight_current_point()

            centers = getattr(self, "analysis_search_centers", None)
            if centers and idx < len(centers):
                cx, cy = centers[idx]
                self.movieCanvas.overlay_rectangle(
                    cx,
                    cy,
                    int(2 * self.searchWindowSpin.value())
                )

            fit_params = getattr(self, "analysis_fit_params", None)
            if fit_params and idx < len(fit_params):
                fc, fs, _pk = fit_params[idx]
                self.movieCanvas.remove_gaussian_circle()
                self.movieCanvas.add_gaussian_circle(
                    fc,
                    fs,
                    self.intensityCanvas.get_current_point_color()
                )
            else:
                fc = None

            # Restore kymo marker for the current point (cleared during anchor edit).
            try:
                kymo_name = self.kymoCombo.currentText()
                info = self.kymo_roi_map.get(kymo_name, {})
                current_kymo_ch = info.get("channel", None)
                if self.analysis_channel == current_kymo_ch or self.analysis_channel is None:
                    if (
                        kymo_name
                        and kymo_name in self.kymographs
                        and self.rois
                        and hasattr(self, "analysis_frames")
                        and idx < len(self.analysis_frames)
                    ):
                        roi = self.rois[self.roiCombo.currentText()]
                        frame = self.analysis_frames[idx]
                        xk = None
                        if fc is not None and is_point_near_roi(fc, roi):
                            xk = self.compute_kymo_x_from_roi(
                                roi, fc[0], fc[1],
                                self.kymographs[kymo_name].shape[1]
                            )
                        elif centers and idx < len(centers) and is_point_near_roi((cx, cy), roi):
                            xk = self.compute_kymo_x_from_roi(
                                roi, cx, cy,
                                self.kymographs[kymo_name].shape[1]
                            )
                        if xk is not None and self.movie is not None:
                            disp_frame = (self.movie.shape[0] - 1) - frame
                            self.kymoCanvas.add_circle(
                                xk, disp_frame,
                                color=self.intensityCanvas.get_current_point_color() if fc is not None else 'grey'
                            )
            except Exception:
                pass

            self.movieCanvas.draw_idle()
            self.kymoCanvas.draw_idle()
        except Exception:
            pass

    def _get_selected_kymo_traj_context(self):
        if self.kymoCanvas.image is None:
            return None
        selected_idx = self.trajectoryCanvas.table_widget.currentRow()
        if selected_idx < 0 or selected_idx >= len(self.trajectoryCanvas.trajectories):
            return None
        kymo_name = self.kymoCombo.currentText()
        if not kymo_name:
            return None
        roi_key = (self.roiCombo.currentText()
                   if self.roiCombo.count() > 0
                   else kymo_name)
        if roi_key not in self.rois:
            return None
        roi = self.rois[roi_key]
        kymo_w = self.kymoCanvas.image.shape[1]
        num_frames = (self.movie.shape[0] if self.movie is not None else 0)
        num_frames_m1 = num_frames - 1
        traj = self.trajectoryCanvas.trajectories[selected_idx]
        if not self._traj_matches_current_kymo(traj, roi):
            return None
        return selected_idx, traj, roi, kymo_w, num_frames_m1

    def _traj_matches_current_kymo(self, traj: dict, roi: dict) -> bool:
        if not isinstance(traj, dict) or not isinstance(roi, dict):
            return False
        traj_roi = traj.get("roi")
        if not isinstance(traj_roi, dict):
            # Allow movie/TrackMate anchors to be edited if the trajectory lies on this ROI.
            try:
                radius = int(self.searchWindowSpin.value())
            except Exception:
                radius = 5
            start = traj.get("start")
            end = traj.get("end")
            if (
                isinstance(start, (list, tuple)) and len(start) >= 3
                and isinstance(end, (list, tuple)) and len(end) >= 3
            ):
                try:
                    sx, sy = float(start[1]), float(start[2])
                    ex, ey = float(end[1]), float(end[2])
                except Exception:
                    return False
                return (
                    is_point_near_roi((sx, sy), roi, search_radius=radius)
                    and is_point_near_roi((ex, ey), roi, search_radius=radius)
                )
            return False
        return traj_roi == roi

    def _start_kymo_anchor_drag(self, event):
        if event.inaxes != self.kymoCanvas.ax:
            return
        ctx = self._get_selected_kymo_traj_context()
        if ctx is None:
            return
        _, traj, roi, kymo_w, num_frames_m1 = ctx
        anchors = traj.get("anchors", []) or []
        nodes = traj.get("nodes", []) or []
        candidates = []

        if anchors:
            for idx, (frame, xk, _yk) in enumerate(anchors):
                try:
                    yk = num_frames_m1 - int(frame)
                except Exception:
                    continue
                candidates.append(("kymo", idx, xk, yk))
        else:
            for idx, (frame, x, y) in enumerate(nodes):
                xk = self.compute_kymo_x_from_roi(roi, x, y, kymo_w)
                if xk is None:
                    continue
                try:
                    yk = num_frames_m1 - int(frame)
                except Exception:
                    continue
                candidates.append(("movie", idx, xk, yk))

        if not candidates or event.x is None or event.y is None:
            return

        ax = self.kymoCanvas.ax
        hit = None
        best_dist = float("inf")
        for src, idx, xk, yk in candidates:
            px, py = ax.transData.transform((xk, yk))
            dist = np.hypot(px - event.x, py - event.y)
            if dist < best_dist:
                best_dist = dist
                hit = (src, idx)

        if hit is None or best_dist > 8:
            return

        self._kymo_anchor_drag = {
            "source": hit[0],
            "index": hit[1],
        }
        self._kymo_anchor_drag_orig = {
            "anchors": list(anchors),
            "nodes": list(nodes),
        }
        self._kymo_anchor_drag_dirty = False

    def _update_kymo_anchor_drag(self, event):
        if not self._kymo_anchor_drag:
            return
        if event.inaxes != self.kymoCanvas.ax or event.xdata is None or event.ydata is None:
            return
        ctx = self._get_selected_kymo_traj_context()
        if ctx is None:
            return
        _, traj, roi, kymo_w, num_frames_m1 = ctx
        xk = float(event.xdata)
        frame_idx = num_frames_m1 - int(round(event.ydata))

        if self._kymo_anchor_drag["source"] == "kymo":
            anchors = list(traj.get("anchors", []) or [])
            idx = self._kymo_anchor_drag["index"]
            if idx >= len(anchors):
                return
            anchors[idx] = (int(frame_idx), float(xk), float(event.ydata))
            traj["anchors"] = anchors
            if roi is not None:
                nodes = []
                for frame, ax_x, _ax_y in anchors:
                    mx, my = self.compute_roi_point(roi, ax_x)
                    nodes.append((int(frame), float(mx), float(my)))
                traj["nodes"] = nodes
        else:
            nodes = list(traj.get("nodes", []) or [])
            idx = self._kymo_anchor_drag["index"]
            if idx >= len(nodes):
                return
            mx, my = self.compute_roi_point(roi, xk)
            nodes[idx] = (int(frame_idx), float(mx), float(my))
            traj["nodes"] = nodes

        self._kymo_anchor_drag_dirty = True
        xs_disp, ys_disp = self._get_anchor_edit_display_points(traj, roi, kymo_w, num_frames_m1)
        if not xs_disp or not ys_disp:
            return
        line = getattr(self, "_kymo_anchor_edit_line", None)
        scatter = getattr(self, "_kymo_anchor_edit_scatter", None)
        if line is None or scatter is None:
            self._build_kymo_anchor_edit_artists()
            line = getattr(self, "_kymo_anchor_edit_line", None)
            scatter = getattr(self, "_kymo_anchor_edit_scatter", None)
        if line is None or scatter is None:
            self.kymoCanvas.draw_trajectories_on_kymo()
            self.kymoCanvas.draw_idle()
            return
        line.set_data(xs_disp, ys_disp)
        scatter.set_offsets(np.column_stack([xs_disp, ys_disp]))

        if self.kymoCanvas._is_panning or self.kymoCanvas.manual_zoom or self._kymo_anchor_bg is None:
            self.kymoCanvas.manual_zoom = False
            self._capture_kymo_anchor_bg()
            return

        canvas = self.kymoCanvas.figure.canvas
        canvas.restore_region(self._kymo_anchor_bg)
        self.kymoCanvas.ax.draw_artist(line)
        self.kymoCanvas.ax.draw_artist(scatter)
        canvas.blit(self.kymoCanvas.ax.bbox)

    def _build_kymo_anchor_edit_artists(self):
        if not getattr(self, "kymo_anchor_edit_mode", False):
            return
        self.kymoCanvas.draw_trajectories_on_kymo()
        markers = getattr(self.kymoCanvas, "kymo_trajectory_markers", []) or []
        line = None
        scatter = None
        for marker in markers:
            if line is None and hasattr(marker, "set_data"):
                line = marker
            elif scatter is None and hasattr(marker, "set_offsets"):
                scatter = marker
        self._kymo_anchor_edit_line = line
        self._kymo_anchor_edit_scatter = scatter
        if line is None or scatter is None:
            self.kymoCanvas.draw_idle()
            return
        self._capture_kymo_anchor_bg()

    def _capture_kymo_anchor_bg(self):
        line = getattr(self, "_kymo_anchor_edit_line", None)
        scatter = getattr(self, "_kymo_anchor_edit_scatter", None)
        if line is None or scatter is None:
            return
        line.set_animated(False)
        scatter.set_animated(False)
        line.set_visible(False)
        scatter.set_visible(False)
        self.kymoCanvas.draw()
        canvas = self.kymoCanvas.figure.canvas
        self._kymo_anchor_bg = canvas.copy_from_bbox(self.kymoCanvas.ax.bbox)
        line.set_visible(True)
        scatter.set_visible(True)
        canvas.restore_region(self._kymo_anchor_bg)
        self.kymoCanvas.ax.draw_artist(line)
        self.kymoCanvas.ax.draw_artist(scatter)
        canvas.blit(self.kymoCanvas.ax.bbox)

    def _get_anchor_edit_display_points(self, traj, roi, kymo_w, num_frames_m1):
        anchors = traj.get("anchors", []) or []
        xs_disp, ys_disp = [], []
        if anchors:
            xs_disp = [xk for _f, xk, _yk in anchors]
            ys_disp = [yk for _f, _xk, yk in anchors]
        else:
            nodes = traj.get("nodes", []) or []
            for f, x, y in nodes:
                xk = self.compute_kymo_x_from_roi(roi, x, y, kymo_w)
                if xk is None:
                    continue
                xs_disp.append(xk)
                ys_disp.append(num_frames_m1 - f)
        return xs_disp, ys_disp

    def _end_kymo_anchor_drag(self, _event):
        self._kymo_anchor_drag = None

    def _validate_kymo_anchor_edit(self):
        ctx = self._get_selected_kymo_traj_context()
        if ctx is None:
            return True, ""
        _, traj, roi, kymo_w, num_frames_m1 = ctx
        anchors = traj.get("anchors", []) or []
        if anchors:
            frames = []
            for frame, xk, yk in anchors:
                try:
                    f = int(frame)
                    x = float(xk)
                    y = float(yk)
                except Exception:
                    return False, "Out of bounds"
                if f < 0 or f > num_frames_m1 or x < 0 or x > kymo_w or y < 0 or y > num_frames_m1:
                    return False, "Out of bounds"
                frames.append(f)
            for prev, curr in zip(frames, frames[1:]):
                if curr <= prev:
                    return False, "Bad anchor order"
            return True, ""

        nodes = traj.get("nodes", []) or []
        frames = []
        for frame, x, y in nodes:
            try:
                f = int(frame)
                mx = float(x)
                my = float(y)
            except Exception:
                return False, "Out of bounds"
            if f < 0 or f > num_frames_m1:
                return False, "Out of bounds"
            xk = self.compute_kymo_x_from_roi(roi, mx, my, kymo_w)
            if xk is None or xk < 0 or xk > kymo_w:
                return False, "Out of bounds"
            frames.append(f)
        for prev, curr in zip(frames, frames[1:]):
            if curr <= prev:
                return False, "Bad anchor order"
        return True, ""

    def _restore_kymo_anchor_drag_original(self):
        orig = getattr(self, "_kymo_anchor_drag_orig", None)
        if not orig:
            return
        ctx = self._get_selected_kymo_traj_context()
        if ctx is None:
            return
        _, traj, _roi, _kymo_w, _num_frames_m1 = ctx
        if "anchors" in orig:
            traj["anchors"] = list(orig["anchors"])
        if "nodes" in orig:
            traj["nodes"] = list(orig["nodes"])

    def on_kymo_click(self, event):

        if event.button == 3 and self._skip_next_right:
            # we just showed the menu for a label—don’t do live updates
            self._skip_next_right = False
            return

        if getattr(self, "kymo_anchor_edit_mode", False) and event.button == 1:
            self._start_kymo_anchor_drag(event)
            return
        if getattr(self, "kymo_anchor_edit_mode", False) and event.button == 3:
            self._handle_kymo_anchor_edit_right_click(event)
            return

        if (event.button == 1 and event.inaxes is self.kymoCanvas.ax
                and self.kymo_traj_overlay_button.isChecked()
                and len(self.analysis_points) == 0):
            current_row = self.trajectoryCanvas.table_widget.currentRow()
            for scatter in self.kymoCanvas.scatter_objs_traj:
                hit, info = scatter.contains(event)
                if not hit:
                    continue

                if self.looping:
                    self.stoploop()

                traj_idx  = scatter.traj_idx  # or lookup from dict
                point_idx = info["ind"][0]

                # 1) If we clicked a different trajectory:
                if traj_idx != current_row:
                    tbl = self.trajectoryCanvas.table_widget
                    # block signals so we don’t re‐enter on_trajectory_selected_by_table
                    tbl.blockSignals(True)
                    tbl.selectRow(traj_idx)  # or tbl.setCurrentCell(traj_idx, 0)
                    tbl.blockSignals(False)

                    # now update everything else
                    self.trajectoryCanvas.on_trajectory_selected_by_index(traj_idx)

                # 2) Same trajectory → pick the point:
                self.jump_to_analysis_point(point_idx)
                if self.sumBtn.isChecked():
                    self.sumBtn.setChecked(False)
                self.intensityCanvas.current_index = point_idx
                self.intensityCanvas.highlight_current_point()
                return
        
        if self.looping:
            self.stoploop()

        self.kymoCanvas.manual_zoom = False

        # — only if click was inside the image —
        if (self.kymoCanvas.image is None or 
            event.xdata is None or event.ydata is None):
            if event.button == 1 and getattr(self, "analysis_anchors", None):
                self.flash_message("Out of bounds")
            return
        H, W = self.kymoCanvas.image.shape[:2]
        if not (0 <= event.xdata <= W and 0 <= event.ydata <= H):
            if event.button == 1 and getattr(self, "analysis_anchors", None):
                self.flash_message("Out of bounds")
            return
        
        # 1) if we just picked a label, consume this click and reset the flag
        if self._ignore_next_kymo_click:
            self._ignore_next_kymo_click = False
            return

        # 2) standard early-outs
        if self.kymoCanvas.image is None or event.xdata is None or event.ydata is None:
            return

        if event.button == 3:
            # normal right-click away from a label
            self.live_update_mode = True
            self.on_kymo_right_click(event)
        elif event.button == 1:
            self.on_kymo_left_click(event)


    def on_kymo_left_click(self, event):
        # — ensure we have focus on the kymo canvas —
        self.kymoCanvas.setFocus(Qt.MouseFocusReason)
        if not self.kymoCanvas.hasFocus() or self.movie is None:
            return

        if event.button != 1:  # left‐button only
            return

        # — start a fresh sequence? clear everything —
        if getattr(self, "new_sequence_start", False):
            self.clear_temporary_analysis_markers()
            self.analysis_markers = []
            self.analysis_points  = []
            self.analysis_anchors = []
            # reset both line‐lists
            self.permanent_analysis_lines = []
            self.temp_analysis_line      = None
            self.analysis_roi = None
            self.new_sequence_start = False

        # — if we’d finished last trajectory, reset —
        if getattr(self, "trajectory_finalized", False):
            self.analysis_points  = []
            self.analysis_anchors = []
            self.permanent_analysis_lines = []
            self.temp_analysis_line      = None
            self.trajectory_finalized = False

        # — map y to frame index —
        num_frames = self.movie.shape[0]
        frame_idx = (num_frames - 1) - int(round(event.ydata))
        if frame_idx < 0 or frame_idx >= num_frames:
            self.flash_message("Out of bounds")
            return

        # — look up ROI →
        kymoName = self.kymoCombo.currentText()
        if not kymoName:
            return
        roi_key = (self.roiCombo.currentText()
                   if self.roiCombo.count() else kymoName)
        roi = self.rois.get(roi_key)
        if not roi or "x" not in roi or "y" not in roi:
            return

        # — convert to movie coords & update slider —
        x_orig, y_orig = self.compute_roi_point(roi, event.xdata)
        # if self.get_movie_frame(frame_idx) is not None:
        #     self.frameSlider.blockSignals(True)
        #     self.frameSlider.setValue(frame_idx)
        #     self.frameSlider.blockSignals(False)
        #     self.frameNumberLabel.setText(f"{frame_idx+1}")

        force_add = False
        if event.dblclick and self.analysis_anchors:
            if len(self.analysis_anchors) >= 2:
                self.analysis_anchors.pop()
                if self.analysis_points:
                    self.analysis_points.pop()
                if getattr(self, "analysis_markers", None):
                    last_marker = self.analysis_markers.pop()
                    for obj in last_marker:
                        try:
                            obj.remove()
                        except Exception:
                            pass
                if getattr(self, "permanent_analysis_lines", None):
                    if self.permanent_analysis_lines:
                        seg = self.permanent_analysis_lines.pop()
                        try:
                            seg.remove()
                        except Exception:
                            pass
            force_add = True

        if self.analysis_anchors:
            last_f, _last_x, _last_y = self.analysis_anchors[-1]
            if frame_idx <= last_f:
                self.flash_message("Bad anchor order")
                return

        should_add = True
        if not force_add and self.analysis_anchors:
            last_f, last_x, last_y = self.analysis_anchors[-1]
            if (last_f == frame_idx
                and math.isclose(last_x, event.xdata, abs_tol=1e-6)
                and math.isclose(last_y, event.ydata, abs_tol=1e-6)):
                should_add = False

        # — record the anchor in both kymo‐space & movie‐space —
        if should_add:
            self.analysis_anchors.append((frame_idx, event.xdata, event.ydata))
            self.analysis_points.append((frame_idx, x_orig, y_orig))
            self._last_kymo_anchor_time = time.perf_counter()
            self._set_kymo_sequence_cursor(True)

        # — draw a small circle there —
        if should_add:
            marker = self.kymoCanvas.temporary_circle(event.xdata, event.ydata,
                                                  size=8, color='#7da1ff')
            self.analysis_markers.append(marker)

        # — initialize the live temp line once —
        if should_add and self.temp_analysis_line is None:
            # last anchor in kymo‐coords
            _, x0, y0 = self.analysis_anchors[-1]

            # 1) create the animated temp‐line artist
            self.temp_analysis_line, = self.kymoCanvas.ax.plot(
                [x0, x0], [y0, y0],
                color='#7da1ff', linewidth=1.5, linestyle='--'
            )
            self.temp_analysis_line.set_animated(True)

            # 2) do one full draw & grab the background
            canvas = self.kymoCanvas.figure.canvas
            canvas.draw()  
            self._kymo_bg = canvas.copy_from_bbox(self.kymoCanvas.ax.bbox)

            # 3) set up a simple throttle
            self._last_kymo_motion = 0.0

        # — add a permanent dotted segment if we have ≥2 anchors —
        if should_add and len(self.analysis_anchors) > 1:
            # get the last two anchors
            _, x_prev, y_prev = self.analysis_anchors[-2]
            _, x_curr, y_curr = self.analysis_anchors[-1]
            seg, = self.kymoCanvas.ax.plot(
                [x_prev, x_curr], [y_prev, y_curr],
                color='#7da1ff', linewidth=1.5, linestyle='--'
            )
            self.permanent_analysis_lines.append(seg)
            # now redraw so this new segment is baked into the blit-background
            canvas = self.kymoCanvas.figure.canvas
            canvas.draw()
            self._kymo_bg = canvas.copy_from_bbox(self.kymoCanvas.ax.bbox)

        # we’ll keep permanent lines in self.permanent_analysis_lines; clear them later.
        self.trajectory_finalized = False

        if event.dblclick:
            self.trajectory_finalized = True
            self.analysis_roi = roi
            self.endKymoClickSequence()
            # reset background snapshot (no more blit)
            # self._bg = None
            # self.kymoCanvas.draw_idle()
            
            # — remove the live temp line —
            if self.temp_analysis_line is not None:
                try:
                    self.temp_analysis_line.remove()
                except Exception:
                    pass
                self.temp_analysis_line = None

            # — remove all permanent dotted segments —
            for seg in getattr(self, 'permanent_analysis_lines', []):
                try:
                    seg.remove()
                except Exception:
                    pass
            self.permanent_analysis_lines = []

            # — clear any circle markers or other temp overlays —
            self.clear_temporary_analysis_markers()

            # — force a full redraw so canvas is clean —
            self.kymoCanvas.draw_idle()
            self._set_kymo_sequence_cursor(False)


    def on_kymo_release(self, event):
        if getattr(self, "kymo_anchor_edit_mode", False):
            if self._kymo_anchor_drag:
                self._end_kymo_anchor_drag(event)
            if not (QApplication.keyboardModifiers() & Qt.ShiftModifier):
                self._finish_kymo_anchor_edit(force_recalc=False)
            return
        H, W = self.kymoCanvas.image.shape[:2]

        x, y = event.xdata, event.ydata
        # 1) bail out if click wasn’t over the image at all
        if x is None or y is None:
            return

        # 2) bail out if click is outside bounds
        if not (0 <= x <= W and 0 <= y <= H):
            return

        # now it’s safe to use x,y
        if event.button == 3:
            self.on_kymo_right_release(event)

        self.live_update_mode = False

        if event.button == 2:
            canvas = self.kymoCanvas.figure.canvas
            # 1) ensure the view is fully redrawn
            self.kymoCanvas.draw()
            # 2) capture a fresh background for our blit‐loop
            self._kymo_bg = canvas.copy_from_bbox(self.kymoCanvas.ax.bbox)

    def on_kymo_right_click(self, event):

        if getattr(self, "_skip_next_right", False):
            self._skip_next_right = False
            return
    
        for lbl, bbox in self.kymoCanvas._kymo_label_bboxes.items():
            if bbox.contains(event.x, event.y):
                # it’s a label: get its trajectory row
                row = self._kymo_label_to_row.get(lbl, -1)
                if row < 0:
                    return

                # build the menu of *value* columns only
                menu = QMenu(self.kymoCanvas)
                for col_name, typ in self.trajectoryCanvas._column_types.items():
                    if typ == "value":
                        act = menu.addAction(f"Add {col_name} value")
                        # capture both col_name and row
                        act.triggered.connect(
                            lambda _, c=col_name, r=row: 
                                self._prompt_and_add_kymo_value(c, r)
                        )

                # show it at the mouse pointer
                menu.exec_(QCursor.pos())
                # skip the rest of this handler
                return

        # If panning or insufficient event data, exit.
        if self.kymoCanvas._is_panning:
            return
        if self.kymoCanvas.image is None or event.xdata is None or event.ydata is None:
            return

        self.cancel_left_click_sequence()
        self.clear_temporary_analysis_markers()
        self.movieCanvas.manual_zoom = True
        self.intensityCanvas.clear_highlight()

        # Compute the frame index from the kymograph y coordinate.
        num_frames = self.movie.shape[0]
        frame_idx = (num_frames - 1) - int(round(event.ydata))
        self.last_frame_index = frame_idx
        if self.movie is None:
            return

        # Even if the frame hasn't changed, force an update.
        self.set_current_frame(frame_idx)
        frame_image = self.get_movie_frame(frame_idx)
        if frame_image is None:
            return

        # Get the ROI key from the current selections.
        kymoName = self.kymoCombo.currentText()
        if not kymoName:
            return
        roi_key = self.roiCombo.currentText() if self.roiCombo.count() > 0 else kymoName
        if roi_key not in self.rois:
            return
        roi = self.rois[roi_key]
        if "x" not in roi or "y" not in roi:
            return

        # Compute the ROI point from the kymograph click.
        x_orig, y_orig = self.compute_roi_point(roi, event.xdata)

        # Compute crop sizes from UI spinboxes.
        search_crop_size = int(2 * self.searchWindowSpin.value())
        zoom_crop_size = int(self.insetViewSize.value())

        # then also clear any magenta gaussian circle on the movie canvas
        removed = self.movieCanvas.remove_gaussian_circle()
        if removed:
            self.movieCanvas.draw_idle()

        # Update the MovieCanvas overlay for visual feedback.
        frame_number = frame_idx+1
        self.movieCanvas.overlay_rectangle(x_orig, y_orig, search_crop_size)

        if not getattr(self, "hide_inset", False):
            self.zoomInsetFrame.setVisible(True)
        self.movieCanvas.update_inset(frame_image, (x_orig, y_orig), zoom_crop_size, zoom_factor=2)

        self.analysis_peak = None
        self.analysis_sigma = None
        if hasattr(self, "histogramCanvas"):
            self.histogramCanvas.update_histogram(frame_image, (x_orig, y_orig), search_crop_size)

        # Optionally re-center the MovieCanvas view if manual zoom is not active.
        current_xlim = self.movieCanvas.ax.get_xlim()
        current_ylim = self.movieCanvas.ax.get_ylim()
        width = current_xlim[1] - current_xlim[0]
        height = current_ylim[1] - current_ylim[0]
        if not getattr(self.movieCanvas, "manual_zoom", False):
            new_xlim = (x_orig - width/2, x_orig + width/2)
            new_ylim = (y_orig - height/2, y_orig + height/2)
            self.movieCanvas.ax.set_xlim(new_xlim)
            self.movieCanvas.ax.set_ylim(new_ylim)
            cx_new = (new_xlim[0] + new_xlim[1]) / 2.0
            cy_new = (new_ylim[0] + new_ylim[1]) / 2.0
            self.movieCanvas.zoom_center = (cx_new, cy_new)

        self.movieCanvas.draw_idle()

        # Prepare kymoCanvas for blit: redraw static overlays and cache background
        self.kymoCanvas.draw_trajectories_on_kymo()
        # Remove any existing marker patch
        if getattr(self.kymoCanvas, "_marker", None) is not None:
            try:
                self.kymoCanvas._marker.remove()
            except Exception:
                pass
            self.kymoCanvas._marker = None
        # Cache the clean background for blitting
        self.kymoCanvas.update_view()
        # Now blit the new marker
        self.kymoCanvas.add_circle(event.xdata, event.ydata, color='#7da1ff')

    def on_kymo_right_release(self, event):
        # Check for valid event data.
        if self.kymoCanvas.image is None or event.xdata is None or event.ydata is None:
            return
        if self.movie is None:
            return

        # Clear the histogram first (which removes any magenta-colored bin centers)
        # self.histogramCanvas.ax.clear()
        # self.histogramCanvas.draw_idle()

        # On release: fully redraw kymo static overlays to clear blitted marker
        # self.kymoCanvas.draw_trajectories_on_kymo()
        # self.kymoCanvas.draw_idle()

        # Now perform the analysis (which will recompute the histogram based on the current spot analysis)
        self.analyze_spot_at_event(event)


    def _on_kymo_label_pick(self, event):
        # whenever any label is picked—left *or* right
        if getattr(self, "analysis_anchors", None) and not getattr(self, "trajectory_finalized", False):
            return
        artist = event.artist
        if artist in self._kymo_label_to_row:
            # if it was a left click, select the row immediately
            if event.mouseevent.button == 1:
                row = self._kymo_label_to_row[artist]
                tbl = self.trajectoryCanvas.table_widget
                tbl.setCurrentCell(row, 0)
                tbl.scrollToItem(tbl.item(row, 0))
                self._ignore_next_kymo_click = True

        if event.mouseevent.button == 3:
            self._last_kymo_artist = event.artist
            self._skip_next_right = True
            gui_evt = getattr(event.mouseevent, "guiEvent", None)
            if isinstance(gui_evt, QMouseEvent):
                # use the real global position
                self._show_kymo_context_menu(gui_evt.globalPos())
            else:
                # fallback for non‐Qt backends
                local = QPoint(int(event.mouseevent.x), int(event.mouseevent.y))
                self._show_kymo_context_menu(self.kymoCanvas.mapToGlobal(local))
            return

    def on_kymo_hover(self, event):
        # Debug output
        #print("on_kymo_hover called. xdata:", event.xdata, "ydata:", event.ydata)
        
        # Check that the event is in the kymograph canvas and has valid data
        if event.inaxes != self.kymoCanvas.ax or event.xdata is None or event.ydata is None:
            self.pixelValueLabel.setText("")
            self._set_kymo_label_hover_cursor(False, in_image=False)
            return

        kymograph = self.kymoCanvas.image
        if kymograph is None:
            self.pixelValueLabel.setText("")
            self._set_kymo_label_hover_cursor(False, in_image=False)
            return

        if self.looping:
            self.pixelValueLabel.setText("")
            self._set_kymo_label_hover_cursor(False, in_image=False)
            return
        
        # Convert floating point coordinates to integer indices for the kymograph
        x = event.xdata
        y = event.ydata
        #print("Computed kymo pixel indices: x =", x, "y =", y)
        
        # Check if the computed indices are within image bounds
        if not (0 <= x < kymograph.shape[1] and 0 <= y < kymograph.shape[0]):
            self.pixelValueLabel.setText("")
            self._set_kymo_label_hover_cursor(False, in_image=False)
            return

        hovering_label = False
        if getattr(self, "analysis_anchors", None) and not getattr(self, "trajectory_finalized", False):
            hovering_label = False
        else:
            for _lbl, bbox in self.kymoCanvas._kymo_label_bboxes.items():
                if bbox.contains(event.x, event.y):
                    hovering_label = True
                    break
        hover_color = None
        if (not hovering_label
                and not getattr(self, "kymo_anchor_edit_mode", False)
                and not (getattr(self, "analysis_anchors", None) and not getattr(self, "trajectory_finalized", False))):
            hover_color = self._kymo_hover_spot_color(event)
        self._set_kymo_label_hover_cursor(hovering_label, in_image=True, color=hover_color)

        if getattr(self, "kymo_anchor_edit_mode", False):
            return
        if getattr(self, "analysis_anchors", None) and not getattr(self, "trajectory_finalized", False):
            return

        if hovering_label:
            return

        # For a vertically flipped kymograph, the frame index is computed as below:
        num_frames = kymograph.shape[0]
        frame_val = num_frames - y

        # If an ROI exists, compute the corresponding movie coordinate based on the ROI.
        if self.roiCombo.count() > 0:
            roi_key = self.roiCombo.currentText()
            if roi_key in self.rois:
                roi = self.rois[roi_key]
                # Compute movie coordinate with compute_roi_point().
                movie_coord = self.compute_roi_point(roi, event.xdata)
            else:
                movie_coord = (x, y)
        else:
            movie_coord = (x, y)

        pixel_val = ""

        real_x = int(movie_coord[0])
        real_y = int(movie_coord[1])

        real_x_fortxt = movie_coord[0]
        real_y_fortxt = movie_coord[1]

        image = self.movieCanvas.image
        if image is not None and 0 <= real_x < image.shape[1] and 0 <= real_y < image.shape[0]:
            pixel_val = image[real_y, real_x]

        # Build the display string (without intensity)
        display_text = f"F: {int(frame_val)} X: {real_x_fortxt:.1f} Y: {real_y_fortxt:.1f} I: {pixel_val}"
        #print("Setting label text to:", display_text)
        
        # Update the label.
        self.pixelValueLabel.setText(display_text)
        self.pixelValueLabel.update()

    def _prompt_and_add_kymo_value(self, col_name, row):
        # 1) get the existing value (may be "")
        existing = self.trajectoryCanvas.trajectories[row]\
                    .get("custom_fields", {}).get(col_name, "")

        # 2) build & configure a styled QInputDialog
        dlg = QInputDialog(self)
        dlg.setWindowTitle(f"Edit {col_name} value")
        dlg.setLabelText(f"{col_name}:")
        dlg.setInputMode(QInputDialog.TextInput)
        dlg.setOkButtonText("OK")
        dlg.setCancelButtonText("Cancel")
        dlg.setTextValue(existing)

        # 3) find its QLineEdit and make it white
        line = dlg.findChild(QLineEdit)
        if line:
            line.setStyleSheet("background-color: white;")

        # 4) exec and grab the result
        if dlg.exec_() != QDialog.Accepted:
            return
        val = dlg.textValue()

        # 5) update model & UI
        self.trajectoryCanvas.trajectories[row]\
            .setdefault("custom_fields", {})[col_name] = val
        self.trajectoryCanvas.writeToTable(row, col_name, val)
        if self.color_by_column == col_name:
            self.refresh_color_by()
            return
        self._update_legends()
        self.kymoCanvas.draw_trajectories_on_kymo()
        self.kymoCanvas.draw()
        self.movieCanvas.draw_trajectories_on_movie()
        self.movieCanvas.draw()

    # def update_analysis_line(self):
    #     """
    #     Draw a permanent dashed line connecting the user‑clicked kymo anchors in order.
    #     """
    #     # Must have at least two anchors
    #     if not hasattr(self, "analysis_anchors") or len(self.analysis_anchors) < 2:
    #         return

    #     # Get display parameters
    #     kymoName = self.kymoCombo.currentText()
    #     if not kymoName:
    #         return

    #     roi_key = (
    #         self.roiCombo.currentText()
    #         if self.roiCombo.count() > 0
    #         else kymoName
    #     )
    #     roi = self.rois.get(roi_key, None)
    #     kymo = self.kymographs.get(kymoName, None)
    #     if kymo is None:
    #         return

    #     # How many frames tall is the movie?
    #     max_frame = self.movie.shape[0]

    #     # Build the lists of display coords directly from anchors:
    #     disp_xs = []
    #     disp_ys = []
    #     for (frame_idx, kx, ky) in self.analysis_anchors:
    #         disp_xs.append(kx)
    #         disp_ys.append(ky)

    #     # Remove any old permanent line
    #     if hasattr(self, "permanent_analysis_line") and self.permanent_analysis_line is not None:
    #         try:
    #             self.permanent_analysis_line.remove()
    #         except Exception:
    #             pass

    #     # Draw a simple dashed line through the anchors
    #     (self.permanent_analysis_line,) = self.kymoCanvas.ax.plot(
    #         disp_xs,
    #         disp_ys,
    #         color='#7da1ff',
    #         linewidth=1.5,
    #         linestyle='--'
    #     )
    #     self.kymoCanvas.draw_idle()

    def on_kymo_motion(self, event):
        if getattr(self, "kymo_anchor_edit_mode", False):
            if self._kymo_anchor_drag:
                self._update_kymo_anchor_drag(event)
            return
        if self.live_update_mode:
            self.on_kymo_right_click(event)
        elif (hasattr(self, "analysis_anchors")
            and self.analysis_anchors
            and not getattr(self, "trajectory_finalized", False)
            and event.xdata is not None
            and event.ydata is not None):

            kymoName = self.kymoCombo.currentText()
            if not kymoName:
                return
            kymo = self.kymographs.get(kymoName)
            if kymo is None:
                return

        if not getattr(self, "analysis_anchors", None) or len(self.analysis_anchors) == 0:
            return

        # Only update if we’re in the middle of a sequence and temp line exists
        if (self.temp_analysis_line is None or
            event.inaxes != self.kymoCanvas.ax or
            getattr(self, "trajectory_finalized", False)):
            return

        # Throttle to ~50 Hz
        now = time.perf_counter()
        if now - self._last_kymo_motion < 0.02:
            return
        self._last_kymo_motion = now

        # Build full preview line through all anchors and current cursor
        pts = [(ax, ay) for (_, ax, ay) in self.analysis_anchors] + [(event.xdata, event.ydata)]
        xs, ys = zip(*pts)
        self.temp_analysis_line.set_data(xs, ys)

        # If the user is panning/zooming, fall back to a one‐off full redraw
        if self.kymoCanvas._is_panning or self.kymoCanvas.manual_zoom:
            # 1) full redraw to apply the new pan/zoom
            self.kymoCanvas.draw()
            # 2) re-snapshot the updated background
            canvas = self.kymoCanvas.figure.canvas
            self._kymo_bg = canvas.copy_from_bbox(self.kymoCanvas.ax.bbox)
            # 3) clear flags so subsequent moves use fast blit
            self.kymoCanvas.manual_zoom = False
            return

        # Otherwise do the fast blit loop
        canvas = self.kymoCanvas.figure.canvas
        canvas.restore_region(self._kymo_bg)
        self.kymoCanvas.ax.draw_artist(self.temp_analysis_line)
        canvas.blit(self.kymoCanvas.ax.bbox)

    def enter_roi_mode(self):
        # Initialize on ROI mode entry.
        if self.temp_analysis_line is None:
            # create an invisible 2-point line initially
            self.temp_analysis_line, = self.kymoCanvas.ax.plot(
                [0, 0], [0, 0],
                color='#7da1ff', linewidth=1.5, linestyle='--'
            )
        # draw it once so we can grab the background
        self.kymoCanvas.draw()
        # grab the clean background:
        self._bg = self.kymoCanvas.copy_from_bbox(self.kymoCanvas.ax.bbox)

    def on_kymo_leave(self, event):
        """Callback for when the mouse leaves the kymograph axes.
        This removes the blue X marker from the movie canvas."""
        self.pixelValueLabel.setText("")
        self._set_kymo_label_hover_cursor(False, in_image=False)
        if self.movieCanvas is not None:
            self.movieCanvas.draw_idle()

    def on_tracking_mode_changed(self, mode):
        # mode is the string from the dropdown ("Independent" or "Tracked")
        self.tracking_mode = mode
        print(f"Tracking mode set to: {self.tracking_mode}")

    # def update_overlay_button_style(self, checked):
    #     if checked:
    #         self.traj_overlay_button.setStyleSheet("background-color: #497ce2;")
    #     else:
    #         self.traj_overlay_button.setStyleSheet("")

    def _show_kymo_context_menu(self, global_pos: QPoint):
        # must have at least one custom column
        if not self.trajectoryCanvas.custom_columns:
            self._last_kymo_artist = None
            return

        artist = self._last_kymo_artist
        if artist is None:
            return

        row = self._kymo_label_to_row.get(artist)
        if row is None:
            return

        traj = self.trajectoryCanvas.trajectories[row]
        cf   = traj.get("custom_fields", {})
        refresh_needed = {"value": False}

        def _mark_binary(r, c):
            self.trajectoryCanvas._mark_custom(r, c)
            if self.color_by_column == c:
                refresh_needed["value"] = True

        def _unmark_binary(r, c):
            self.trajectoryCanvas._unmark_custom(r, c)
            if self.color_by_column == c:
                refresh_needed["value"] = True

        # --- build the menu ---
        menu = QMenu(self.kymoCanvas)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground)

        # --- optional “Check colocalization” entry ---
        if getattr(self, "check_colocalization", False) and self.movie.ndim == 4:
            ref_ch = traj["channel"]
            # find at least one missing co. % for other channels
            missing = any(
                col.endswith(" co. %") and
                not col.endswith(f"{ref_ch} co. %") and
                not cf.get(col, "").strip()
                for col in self.trajectoryCanvas.custom_columns
            )
            if missing:
                act = menu.addAction("Check colocalization")
                act.triggered.connect(lambda _chk=False, r=row: 
                                        self._compute_colocalization_for_row(r))
                menu.addSeparator()

        # --- now the normal binary/value columns ---
        # filter+dedupe
        cols = [
            c for c in self.trajectoryCanvas.custom_columns
            if self.trajectoryCanvas._column_types.get(c) in ("binary","value")
        ]
        unique_cols = []
        for c in cols:
            if c not in unique_cols:
                unique_cols.append(c)

        tbl = self.trajectoryCanvas.table_widget
        for col in unique_cols:
            col_type = self.trajectoryCanvas._column_types.get(col, "binary")
            table_col_index = self.trajectoryCanvas._col_index[col]
            item = tbl.item(row, table_col_index)
            text = item.text().strip() if item else ""

            if col_type == "binary":
                marked = (text.lower() == "yes")
                if marked:
                    action_text = f"Unmark as {col}"
                    callback    = lambda _chk=False, r=row, c=col: _unmark_binary(r, c)
                else:
                    action_text = f"Mark as {col}"
                    callback    = lambda _chk=False, r=row, c=col: _mark_binary(r, c)
            else:  # value column
                if text:
                    action_text = f"Edit {col} value"
                else:
                    action_text = f"Add {col} value"
                callback = lambda _chk=False, r=row, c=col: \
                        self._prompt_and_add_kymo_value(c, r)

            menu.addAction(action_text, callback)

        # --- show it, then reset state & redraw ---
        menu.exec_(global_pos)
        self._last_kymo_artist = None

        if refresh_needed["value"]:
            self.refresh_color_by()
            return

        self._update_legends()
        self.kymoCanvas.draw_trajectories_on_kymo()
        self.kymoCanvas.draw_idle()
