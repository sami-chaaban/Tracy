from ._shared import *
from .base import ImageCanvas

class KymoCanvas(ImageCanvas):
    def __init__(self, parent=None, navigator=None):
        super().__init__(parent)
        self._im = None
        self._marker = None
        self._is_panning = False
        self._pan_start = None
        self._orig_xlim = None
        self._orig_ylim = None
        self._last_pan = 0.0
        self._interaction_finish_timer = QTimer(self)
        self._interaction_finish_timer.setSingleShot(True)
        self._interaction_finish_timer.timeout.connect(self._finish_kymo_interaction)
        self._view_redraw_timer = QTimer(self)
        self._view_redraw_timer.setSingleShot(True)
        self._view_redraw_timer.timeout.connect(self._perform_deferred_view_draw)
        self._deferred_cache_background = False
        self.scale = 1.0  # Data units per pixel (uniform in x and y)
        self.padding = 1.25
        self.zoom_center = None  # in data coordinates
        self.manual_zoom = False
        self._update_pending = False
        self.manual_zoom = False

        self._kymo_label_bboxes: dict[Text, Bbox] = {}

        self.navigator = navigator
        self._apply_kymo_canvas_background()

        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocus()

        self.mpl_connect("scroll_event", self.on_scroll)
        self.mpl_connect("button_press_event", self.on_mouse_press)
        self.mpl_connect("motion_notify_event", self.on_mouse_move)
        self.mpl_connect("button_release_event", self.on_mouse_release)
        # self.mpl_connect("pick_event", self.on_pick_event)

        self.kymo_trajectory_markers = []
        self.kymo_selected_trajectory_markers = []
        self._kymo_base_scatter_artists = []
        self._kymo_selected_scatter_artists = []
        self._kymo_base_pick_entries = []
        self._kymo_selected_pick_entries = []
        self._kymo_pick_points = np.empty((0, 2), dtype=float)
        self._kymo_pick_rows = np.empty((0,), dtype=int)
        self._kymo_pick_indices = np.empty((0,), dtype=int)
        self._kymo_interaction_hidden_artists = {}
        self.scatter_objs_traj = []

        self._ctrl_panning = False

    def _kymo_interaction_bg_color(self):
        nav = getattr(self, "navigator", None)
        settings = getattr(nav, "settings", {}) if nav is not None else {}
        color = settings.get("widget-bg") if isinstance(settings, dict) else None
        if color:
            return color
        try:
            return self.palette().window().color().name()
        except Exception:
            return "white"

    def _apply_kymo_canvas_background(self):
        color = self._kymo_interaction_bg_color()
        try:
            self.setAttribute(Qt.WA_OpaquePaintEvent, True)
            self.setStyleSheet(f"background-color: {color};")
        except Exception:
            pass
        for patch in (self.fig.patch, self.ax.patch):
            try:
                patch.set_facecolor(color)
                patch.set_alpha(1.0)
            except Exception:
                pass

    def _finish_kymo_interaction(self, redraw=True):
        if hasattr(self, "_interaction_finish_timer") and self._interaction_finish_timer.isActive():
            self._interaction_finish_timer.stop()
        if hasattr(self, "_view_redraw_timer") and self._view_redraw_timer.isActive():
            self._view_redraw_timer.stop()
        self._deferred_cache_background = False
        self._restore_kymo_dense_artists_after_interaction()

        if redraw:
            self.update_view(cache_background=True)

    def _schedule_kymo_interaction_finish(self, delay_ms=90):
        if self._slow_interaction_mode_enabled() and not self._is_panning:
            self._interaction_finish_timer.start(delay_ms)

    def _slow_interaction_mode_enabled(self):
        return bool(getattr(self.navigator, "slow_computer_mode", False))

    def _iter_kymo_dense_artists(self):
        seen = set()
        markers = (
            list(getattr(self, "kymo_trajectory_markers", None) or [])
            + list(getattr(self, "kymo_selected_trajectory_markers", None) or [])
        )
        for artist in markers:
            if not getattr(artist, "_tracy_kymo_dense_artist", False):
                continue
            ident = id(artist)
            if ident in seen:
                continue
            seen.add(ident)
            yield artist

    def _iter_kymo_overlay_artists(self):
        seen = set()
        markers = (
            list(getattr(self, "kymo_trajectory_markers", None) or [])
            + list(getattr(self, "kymo_selected_trajectory_markers", None) or [])
        )
        marker = getattr(self, "_marker", None)
        if marker is not None:
            markers.append(marker)
        for artist in markers:
            ident = id(artist)
            if ident in seen:
                continue
            seen.add(ident)
            yield artist

    def _hide_kymo_dense_artists_for_interaction(self):
        if not self._slow_interaction_mode_enabled():
            return
        hidden = getattr(self, "_kymo_interaction_hidden_artists", None)
        if hidden is None:
            hidden = {}
            self._kymo_interaction_hidden_artists = hidden
        for artist in self._iter_kymo_dense_artists():
            if artist in hidden:
                continue
            try:
                hidden[artist] = artist.get_visible()
                artist.set_visible(False)
            except Exception:
                pass

    def _hide_kymo_overlay_artists_for_interaction(self):
        if not self._slow_interaction_mode_enabled():
            return
        hidden = getattr(self, "_kymo_interaction_hidden_artists", None)
        if hidden is None:
            hidden = {}
            self._kymo_interaction_hidden_artists = hidden
        for artist in self._iter_kymo_overlay_artists():
            if artist in hidden:
                continue
            try:
                hidden[artist] = artist.get_visible()
                artist.set_visible(False)
            except Exception:
                pass

    def _restore_kymo_dense_artists_after_interaction(self):
        hidden = getattr(self, "_kymo_interaction_hidden_artists", None)
        if not hidden:
            return
        for artist, visible in list(hidden.items()):
            try:
                artist.set_visible(visible)
            except Exception:
                pass
        hidden.clear()

    @staticmethod
    def _mark_kymo_dense_artist(artist):
        try:
            artist._tracy_kymo_dense_artist = True
        except Exception:
            pass
        return artist

    def _schedule_deferred_view_draw(self, cache_background=False):
        self._deferred_cache_background = (
            self._deferred_cache_background or bool(cache_background)
        )
        if not self._view_redraw_timer.isActive():
            self._view_redraw_timer.start(8)

    def _perform_deferred_view_draw(self):
        cache_background = bool(self._deferred_cache_background)
        self._deferred_cache_background = False
        self.draw()
        if cache_background:
            self._bg = self.copy_from_bbox(self.ax.bbox)
            self._refresh_kymo_label_bboxes()

    def _refresh_kymo_label_bboxes(self):
        self._kymo_label_bboxes.clear()
        markers = (
            list(getattr(self, "kymo_trajectory_markers", None) or [])
            + list(getattr(self, "kymo_selected_trajectory_markers", None) or [])
        )
        if not markers:
            return
        try:
            renderer = self.figure.canvas.get_renderer()
        except Exception:
            return
        for marker in markers:
            if not isinstance(marker, Text):
                continue
            try:
                bbox = marker.get_window_extent(renderer)
                patch = marker.get_bbox_patch()
                if patch is not None:
                    try:
                        bbox = bbox.union(patch.get_window_extent(renderer))
                    except Exception:
                        pass
                self._kymo_label_bboxes[marker] = bbox.expanded(1.5, 1.5)
            except Exception:
                pass

    def mousePressEvent(self, event):
        # ⇨ Ctrl+Left should act like Middle
        if event.button() == Qt.LeftButton and (event.modifiers() & Qt.ControlModifier):
            self._ctrl_panning = True
            self.manual_zoom = True
            fake = QMouseEvent(
                event.type(),
                event.pos(),
                Qt.MiddleButton,        # pretend it’s middle
                Qt.MiddleButton,
                event.modifiers()
            )
            super().mousePressEvent(fake)
        elif event.button() == Qt.MiddleButton:
            if self.navigator.looping:
                self.navigator.stoploop()
                self.manual_zoom = True
                if self.navigator.looping:
                    self.navigator.stoploop()
                self._is_panning = True
            super().mousePressEvent(event)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._ctrl_panning:
            fake = QMouseEvent(
                event.type(),
                event.pos(),
                Qt.MiddleButton,
                Qt.MiddleButton if event.buttons() & Qt.LeftButton else Qt.NoButton,
                event.modifiers()
            )
            super().mouseMoveEvent(fake)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._ctrl_panning and event.button() == Qt.LeftButton:
            fake = QMouseEvent(
                event.type(),
                event.pos(),
                Qt.MiddleButton,
                Qt.NoButton,
                event.modifiers()
            )
            super().mouseReleaseEvent(fake)
            self._ctrl_panning = False
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Shift:
            try:
                self.navigator.cancel_left_click_sequence()
                self.navigator._set_kymo_anchor_edit_mode(True)
            except Exception:
                pass
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == Qt.Key_Shift:
            try:
                self.navigator._finish_kymo_anchor_edit(force_recalc=False)
            except Exception:
                pass
        super().keyReleaseEvent(event)

    def reset_canvas(self):
        self.ax.cla()
        self._im = None
        self._marker = None
        self._is_panning = False
        self._pan_start = None
        self._orig_xlim = None
        self._orig_ylim = None
        self._last_pan = 0.0
        self._finish_kymo_interaction(redraw=False)
        self.zoom_center = None
        self.scale = 1.0

    def display_image(self, image):
        """Show kymo image but preserve zoom if user has panned or scrolled."""
        if image is None:
            return

        p15, p99 = np.percentile(image, (15, 99))
        if p99 > p15:
            img8 = np.clip((image - p15) / (p99 - p15), 0, 1) * 255
        else:
            img8 = np.zeros_like(image, dtype=np.float32)
        img8 = img8.astype(np.uint8)
        h, w = img8.shape

        # If we’re already in a manual zoom state, just update the data
        if self._im is not None and self.manual_zoom:
            self._im.set_data(img8)
            self.image = img8
            widget_w = max(self.width(), 1)
            widget_h = max(self.height(), 1)
            self.max_scale = max(w / widget_w, h / widget_h) * self.padding
            self.draw()
            return

        # Otherwise do the initial full reset + fit
        self.reset_canvas()
        # if img8.ndim == 3:
        #     h, w, _ = img8.shape
        # else:
        #     h, w = img8.shape

        self.ax.set_xlim(0, w)
        self.ax.set_ylim(0, h)
        self.ax.set_aspect('auto')
        cmap      = "gray_r" if self.navigator.inverted_cmap else "gray"
        self._im  = self.ax.imshow(img8, cmap=cmap)
        self.ax.axis("off")
        self.draw()
        self.image = img8

        # initial zoom parameters only once
        self.zoom_center = (w/2, h/2)
        widget_w = max(self.width(), 1)
        widget_h = max(self.height(),1)
        self.scale     = max(w/widget_w, h/widget_h)
        self.max_scale = self.scale * self.padding
        self.update_view()
    #     QTimer.singleShot(0, self._init_scale_and_view)

    # def _init_scale_and_view(self):
    #     # now the widget has its proper size
    #     widget_w = self.width()
    #     widget_h = self.height()
    #     w, h = self.image.shape[1], self.image.shape[0]
    #     self.scale = max(w / widget_w, h / widget_h)
    #     self.max_scale = self.scale * self.padding
    #     self.update_view()

    def update_view(self, cache_background=True, defer=False, blit=False):
        if self.image is None or self.zoom_center is None:
            return

        # 1) compute the new data‐limits exactly as before
        widget_w = self.width()
        widget_h = self.height()
        view_w   = widget_w * self.scale
        view_h   = widget_h * self.scale
        outer_pad = getattr(self.ax, "_outer_pad", 0) or 0
        if outer_pad:
            view_w += 2 * outer_pad
            view_h += 2 * outer_pad
        cx, cy   = self.zoom_center

        self.ax.set_xlim(cx - view_w/2, cx + view_w/2)
        force_origin = getattr(self, "_force_origin", None)
        if force_origin == "upper":
            self.ax.set_ylim(cy + view_h/2, cy - view_h/2)
        else:
            self.ax.set_ylim(cy - view_h/2, cy + view_h/2)

        if defer or blit:
            self._schedule_deferred_view_draw(cache_background=cache_background)
            return

        marker = getattr(self, "_marker", None)
        marker_visible = None
        if marker is not None:
            try:
                marker_visible = marker.get_visible()
                marker.set_visible(False)
            except Exception:
                marker_visible = None

        # 2) redraw everything (synchronous)
        if self._view_redraw_timer.isActive():
            self._view_redraw_timer.stop()
            self._deferred_cache_background = False
        self.draw()

        if cache_background:
            # Grab a fresh background for blit loops. During pan this is deferred
            # until release because copying the bbox on every drag event is costly.
            self._bg = self.copy_from_bbox(self.ax.bbox)
            self._refresh_kymo_label_bboxes()

        if marker is not None and marker_visible:
            try:
                marker.set_visible(True)
                self.ax.draw_artist(marker)
                self.fig.canvas.blit(self.ax.bbox)
            except Exception:
                pass

    def invalidate_blit_background(self):
        self._bg = None

    def fit_to_full_image(self):
        if self.image is None:
            return
        if self._interaction_finish_timer.isActive():
            self._interaction_finish_timer.stop()
        if self._view_redraw_timer.isActive():
            self._view_redraw_timer.stop()
        self._deferred_cache_background = False
        self._is_panning = False
        self._pan_start = None
        self._orig_xlim = None
        self._orig_ylim = None
        self._last_pan = 0.0
        self._restore_kymo_dense_artists_after_interaction()

        h, w = self.image.shape[:2]
        widget_w = max(self.width(), 1)
        widget_h = max(self.height(), 1)
        base = max(w / widget_w, h / widget_h)
        self.scale = base
        self.max_scale = base * self.padding
        self.zoom_center = (w / 2, h / 2)
        self.manual_zoom = False
        self.update_view(cache_background=True)

    def on_scroll(self, event):
        # only zoom when we have an image and are over the axes
        if self.image is None or event.inaxes != self.ax:
            return

        self.manual_zoom = True

        # 1) Get the mouse’s data‐coordinates and the old scale
        mx, my    = event.xdata, event.ydata
        old_scale = self.scale
        # if we somehow didn’t get a data‐coord, bail
        if mx is None or my is None:
            return
        if self.navigator.looping:
            self.navigator.stoploop(prompt=True)

        # 2) Compute the new scale factor
        base_scale = 1.15
        if event.button == 'up':      # wheel up → zoom in
            new_scale = old_scale / base_scale
        elif event.button == 'down':  # wheel down → zoom out
            new_scale = old_scale * base_scale
        else:
            return

        # Clamp to max scale.
        if hasattr(self, 'max_scale'):
            new_scale = min(new_scale, self.max_scale)

        # 3) Recompute zoom_center so that (mx,my) stays fixed
        cx, cy = self.zoom_center
        ratio  = new_scale / old_scale
        new_cx = mx + (cx - mx) * ratio
        new_cy = my + (cy - my) * ratio

        # 4) Store and schedule the redraw
        self.scale       = new_scale
        self.zoom_center = (new_cx, new_cy)
        if self._slow_interaction_mode_enabled():
            self._hide_kymo_dense_artists_for_interaction()
            self.update_view(cache_background=False, defer=True)
            self._schedule_kymo_interaction_finish()
        else:
            self.update_view(cache_background=True)
        # schedule a single zoom/pan update per event loop
    #     if not self._update_pending:
    #         self._update_pending = True
    #         QTimer.singleShot(0, self._perform_throttled_update)

    # def _perform_throttled_update(self):
    #     """
    #     Perform the zoom/pan update in a throttled manner.
    #     """
    #     # full view update then clear the pending flag
    #     self.update_view()
    #     self._update_pending = False

    def on_mouse_press(self, event):
        if event.inaxes != self.ax:
            return
        self.setFocus(Qt.MouseFocusReason)
        if event.button == 2:
            self._is_panning = True
            self.manual_zoom = True
            self._pan_start = (event.x, event.y)
            self._orig_xlim = self.ax.get_xlim()
            self._orig_ylim = self.ax.get_ylim()
            self._last_pan = time.perf_counter()
            if self._slow_interaction_mode_enabled():
                self._hide_kymo_dense_artists_for_interaction()
            if self._interaction_finish_timer.isActive():
                self._interaction_finish_timer.stop()
        elif event.button == 1:
            if hasattr(self.parent(), 'on_kymo_left_click'):
                self.parent().on_kymo_left_click(event)

    def on_mouse_move(self, event):
        if self._is_panning and event.inaxes == self.ax:
            now = time.perf_counter()
            if now - self._last_pan < 0.008:
                return
            self._last_pan = now
            self.manual_zoom = True
            inv = self.ax.transData.inverted()
            start_data = inv.transform(self._pan_start)
            current_data = inv.transform((event.x, event.y))
            ddata = (current_data[0] - start_data[0], current_data[1] - start_data[1])
            new_xlim = (self._orig_xlim[0] - ddata[0], self._orig_xlim[1] - ddata[0])
            new_ylim = (self._orig_ylim[0] - ddata[1], self._orig_ylim[1] - ddata[1])
            # Also update the zoom_center to match the new center.
            cx = (new_xlim[0] + new_xlim[1]) / 2.0
            cy = (new_ylim[0] + new_ylim[1]) / 2.0
            self.zoom_center = (cx, cy)
            if self._slow_interaction_mode_enabled():
                self._hide_kymo_dense_artists_for_interaction()
                self.update_view(cache_background=False, defer=True)
            else:
                self.update_view(cache_background=True)
            # Update pan origin for incremental panning (prevents anchor sticking)
            self._pan_start = (event.x, event.y)
            self._orig_xlim = self.ax.get_xlim()
            self._orig_ylim = self.ax.get_ylim()

    def on_mouse_release(self, event):
        was_panning = bool(self._is_panning)
        self._is_panning = False
        if was_panning:
            if self._slow_interaction_mode_enabled():
                self._finish_kymo_interaction(redraw=True)
            else:
                self.update_view(cache_background=True)
        else:
            # Force a final synchronous redraw and update the background
            self.update_view()

    def resizeEvent(self, event):
        slow_mode = self._slow_interaction_mode_enabled()
        if slow_mode:
            self._hide_kymo_overlay_artists_for_interaction()
        super().resizeEvent(event)
        if self.image is None:
            if slow_mode:
                self._finish_kymo_interaction(redraw=False)
            return
        # Recompute max_scale on resize so zoom-out still fills the new widget size
        h, w = self.image.shape[:2]
        widget_w = self.width()
        widget_h = self.height()
        if widget_w > 1 and widget_h > 1:
            base = max(w / widget_w, h / widget_h)
            self.max_scale = base * self.padding
            if not self.manual_zoom:
                # Auto-fit when not in a manual zoom/pan state.
                self.scale = base
                self.zoom_center = (w / 2, h / 2)
        if slow_mode:
            # While the splitter is moving, redraw only the image/axes. Restore
            # trajectory overlays after resize events stop arriving.
            self.update_view(cache_background=False, defer=True)
            self._schedule_kymo_interaction_finish(delay_ms=140)
        else:
            self.update_view(cache_background=True)

    def add_circle(self, x, y, size=12, color='grey'):
        """
        Draw a hollow circle marker at (x, y) using blitting for performance.
        """
        old_marker = getattr(self, "_marker", None)

        # Prepare blitting background
        if not hasattr(self, "_bg") or self._bg is None:
            if old_marker is not None:
                try:
                    old_marker.remove()
                except Exception:
                    pass
                self._marker = None
                old_marker = None
            self.draw()
            self._bg = self.copy_from_bbox(self.ax.bbox)

        self.fig.canvas.restore_region(self._bg)

        if old_marker is not None:
            try:
                old_marker.remove()
            except Exception:
                pass
            self._marker = None

        # Create a hollow circle via a Line2D
        marker, = self.ax.plot(
            [x], [y],
            linestyle='none',
            marker='o',
            markersize=size,
            markeredgecolor=color,
            markerfacecolor='none',
            markeredgewidth=2,
            zorder=6
        )
        self._marker = marker

        # Draw just the marker and blit
        self.ax.draw_artist(marker)
        self.fig.canvas.blit(self.ax.bbox)

    def temporary_circle(self, x, y, size=12, color='blue'):
        """
        Add a transient marker circle at (x, y) in *data* coords,
        but with a fixed radius of `size` points.
        """
        if x is None or y is None:
            print("Warning: x or y is None in temporary_circle, skipping marker addition.")
            return None

        # 's' is marker area in points^2, so area ~ (diameter_in_pts)^2
        marker = self.ax.scatter(
            [x], [y],
            s=(size**2),
            c=color,
            alpha=0.6,
            linewidths=0  # no edge
        )
        self.draw()
        return [marker]

    def _refresh_kymo_clickable_artists(self):
        self.scatter_objs_traj = (
            list(getattr(self, "_kymo_selected_scatter_artists", []))
            + list(getattr(self, "_kymo_base_scatter_artists", []))
        )
        entries = (
            list(getattr(self, "_kymo_selected_pick_entries", []))
            + list(getattr(self, "_kymo_base_pick_entries", []))
        )
        if entries:
            self._kymo_pick_points = np.concatenate([entry[0] for entry in entries])
            self._kymo_pick_rows = np.concatenate([entry[1] for entry in entries])
            self._kymo_pick_indices = np.concatenate([entry[2] for entry in entries])
        else:
            self._kymo_pick_points = np.empty((0, 2), dtype=float)
            self._kymo_pick_rows = np.empty((0,), dtype=int)
            self._kymo_pick_indices = np.empty((0,), dtype=int)

    def _make_kymo_pick_entry(self, traj_idx, xs_pts, ys_pts):
        xs = np.asarray(xs_pts, dtype=float)
        ys = np.asarray(ys_pts, dtype=float)
        valid = np.isfinite(xs) & np.isfinite(ys)
        if not np.any(valid):
            return None
        point_indices = np.nonzero(valid)[0].astype(int)
        points = np.column_stack([xs[valid], ys[valid]])
        rows = np.full(len(point_indices), int(traj_idx), dtype=int)
        return points, rows, point_indices

    def pick_kymo_trajectory_point(self, event, max_px=8):
        points = getattr(self, "_kymo_pick_points", None)
        if points is None or len(points) == 0:
            return None
        if event.inaxes is not self.ax:
            return None
        try:
            display_points = self.ax.transData.transform(points)
            dx = display_points[:, 0] - float(event.x)
            dy = display_points[:, 1] - float(event.y)
            dist2 = dx * dx + dy * dy
        except Exception:
            return None
        limit2 = float(max_px) * float(max_px)
        nearest = int(np.argmin(dist2))
        if dist2[nearest] > limit2:
            return None
        return (
            int(self._kymo_pick_rows[nearest]),
            int(self._kymo_pick_indices[nearest]),
        )

    def _remove_kymo_artists(self, artists):
        label_map = getattr(self.navigator, "_kymo_label_to_row", None)
        for marker in artists:
            if label_map is not None:
                try:
                    label_map.pop(marker, None)
                except Exception:
                    pass
            try:
                marker.remove()
            except Exception:
                pass

    def _kymo_overlay_context(self, invert_y=True):
        if self.navigator is None:
            return None
        kymo_name = self.navigator.kymoCombo.currentText()
        info = self.navigator.kymo_roi_map.get(kymo_name, {})
        current_kymo_ch = info.get("channel")
        roi_key = (
            self.navigator.roiCombo.currentText()
            if self.navigator.roiCombo.count() > 0
            else kymo_name
        )
        if roi_key not in self.navigator.rois or self.image is None:
            return None
        roi = self.navigator.rois[roi_key]
        kymo_w = self.image.shape[1]
        num_frames = (
            self.navigator.movie.shape[0]
            if self.navigator.movie is not None else 0
        )
        num_frames_m1 = num_frames - 1

        def frame_to_y(frame):
            return num_frames_m1 - frame if invert_y else frame

        roi_cache = self.navigator._compute_roi_cache(roi)
        return roi, kymo_w, current_kymo_ch, frame_to_y, roi_cache

    def _project_movie_point(self, roi, kymo_w, x, y):
        return self._project_movie_point_cached(
            self.navigator._compute_roi_cache(roi),
            kymo_w,
            x,
            y,
        )

    def _project_movie_point_cached(self, roi_cache, kymo_w, x, y):
        try:
            xk = self.navigator._compute_kymo_x(roi_cache, x, y, kymo_w)
            if xk is None:
                return None
            xk = float(xk)
        except (TypeError, ValueError):
            return None
        return xk if np.isfinite(xk) else None

    def _draw_kymo_anchor_edit_overlay(
        self,
        selected_idx,
        roi,
        kymo_w,
        current_kymo_ch,
        frame_to_y,
        markers,
        *,
        showsearchline=True,
        show_anchors=True,
    ):
        trajectories = self.navigator.trajectoryCanvas.trajectories
        if selected_idx < 0 or selected_idx >= len(trajectories):
            return
        traj = trajectories[selected_idx]
        ch = traj.get("channel")
        if ch is not None and ch != current_kymo_ch:
            return
        if not self.navigator._traj_matches_current_kymo(traj, roi):
            return

        anchors = traj.get("anchors", []) or []
        xs_disp, ys_disp = [], []
        if anchors:
            xs_disp = [xk for _f, xk, _yk in anchors]
            ys_disp = [yk for _f, _xk, yk in anchors]
        else:
            nodes = traj.get("nodes", []) or []
            for f, x, y in nodes:
                xk = self._project_movie_point(roi, kymo_w, x, y)
                if xk is None:
                    continue
                xs_disp.append(xk)
                ys_disp.append(frame_to_y(f))

        if not xs_disp or not ys_disp:
            return

        if showsearchline:
            search_line_color = self.navigator._get_uniform_traj_color(traj) or "#7da1ff"
            dotted, = self.ax.plot(
                xs_disp, ys_disp,
                color=search_line_color, linestyle="--", linewidth=2,
                alpha=0.8, zorder=2,
                solid_capstyle='round', dash_capstyle='round'
            )
            markers.append(dotted)

        if show_anchors:
            anchor_scatter = self.ax.scatter(
                xs_disp, ys_disp,
                s=(8**2),
                c="#4f6bdc",
                alpha=0.7,
                linewidths=0,
                zorder=6
            )
            markers.append(anchor_scatter)

    def _kymo_can_batch_uniform_base_trajectories(self):
        nav = getattr(self, "navigator", None)
        if nav is None:
            return False
        return True

    def _kymo_group_color(self, color, fallback="magenta"):
        if color is None:
            return fallback
        try:
            return mcolors.to_hex(color, keep_alpha=True)
        except Exception:
            return color if isinstance(color, str) else fallback

    def _add_kymo_endpoint_labels(self, idx, x0, y0, x1, y1, highlighted, markers):
        traj = self.navigator.trajectoryCanvas.trajectories[idx]
        traj_label = traj.get("file_index", str(traj["trajectory_number"]))
        face = "#7da1ff" if highlighted else "#cbd9ff"
        textcolor = "white" if highlighted else "black"
        alpha_lbl = 0.8 if highlighted else 0.6

        dispA = self.ax.transData.transform((x0, y0))
        dispB = self.ax.transData.transform((x1, y1))
        v = dispB - dispA
        norm = np.hypot(*v)
        u = v / norm if norm else np.array([1.0, 0.0])
        offset = 15
        for (cx, cy, suf), sign in [((x0, y0, 'A'), -1), ((x1, y1, 'B'), +1)]:
            dx, dy = u * (offset * sign)
            lbl = self.ax.annotate(
                f"{traj_label}{suf}",
                xy=(cx, cy),
                xytext=(dx, dy),
                textcoords='offset pixels',
                ha='center', va='center',
                color=textcolor, fontsize=8, fontweight='bold',
                bbox=dict(
                    boxstyle='circle,pad=0.3',
                    facecolor=face,
                    edgecolor='black',
                    linewidth=1.5,
                    alpha=alpha_lbl
                ),
                zorder=(8 if highlighted else 4)
            )
            self.navigator._kymo_label_to_row[lbl] = idx
            markers.append(lbl)

    def _draw_kymo_base_trajectories_batched(
        self,
        selected_idx,
        roi,
        kymo_w,
        current_kymo_ch,
        frame_to_y,
        roi_cache,
        markers,
        scatter_artists,
        pick_entries,
        *,
        showsearchline=True,
        skinny=False,
        show_labels=True,
        show_anchors=True,
    ):
        if not self._kymo_can_batch_uniform_base_trajectories():
            return False
        if getattr(self.navigator, "connect_all_spots", False):
            return False

        scattersize = 3 if skinny else 9
        linesize = 0.5 if skinny else 1.5
        hide_spots = getattr(self.navigator, "hide_kymo_spots", False)
        compute_x = self.navigator._compute_kymo_x
        search_segments_by_color = {}
        spot_segments_by_color = {}
        scatter_points_by_color = {}

        trajectories = self.navigator.trajectoryCanvas.trajectories
        for idx, traj in enumerate(trajectories):
            ch = traj.get("channel")
            if ch is not None and ch != current_kymo_ch:
                continue

            sf, sx, sy = traj["start"]
            ef, ex, ey = traj["end"]
            if not (is_point_near_roi((sx, sy), roi) and
                    is_point_near_roi((ex, ey), roi)):
                continue

            x0 = compute_x(roi_cache, sx, sy, kymo_w)
            y0 = frame_to_y(sf)
            x1 = compute_x(roi_cache, ex, ey, kymo_w)
            y1 = frame_to_y(ef)
            if x0 is None or x1 is None:
                continue

            frames = traj["frames"]
            orig = traj["original_coords"]
            anchors = traj.get("anchors", []) or []
            traj_roi = traj.get("roi")
            anchors_match_current = (
                bool(anchors)
                and isinstance(traj_roi, dict)
                and self.navigator._roi_matches(traj_roi, roi)
            )

            scatter_kwargs, line_color = self.navigator._get_traj_colors(traj)
            uniform_color = self._kymo_group_color(
                scatter_kwargs.get("color") or line_color,
                fallback="magenta",
            )
            pts_colors = scatter_kwargs.get("c")
            point_colors = None
            if isinstance(pts_colors, (list, tuple, np.ndarray)) and len(pts_colors) == len(frames):
                point_colors = [self._kymo_group_color(c, fallback=uniform_color) for c in pts_colors]
            search_line_color = self._kymo_group_color(
                scatter_kwargs.get("color"),
                fallback="#7da1ff",
            )

            spots = traj.get("spot_centers", [None] * len(frames))
            pts = []
            for f, spot in zip(frames, spots):
                yy = frame_to_y(f)
                if isinstance(spot, (tuple, list, np.ndarray)) and len(spot) >= 2:
                    xx = self._project_movie_point_cached(roi_cache, kymo_w, spot[0], spot[1])
                    pts.append((xx, yy) if xx is not None else (np.nan, np.nan))
                else:
                    pts.append((np.nan, np.nan))
            if not pts:
                continue
            xs_pts, ys_pts = (np.asarray(vals, dtype=float) for vals in zip(*pts))

            if showsearchline:
                if anchors_match_current:
                    xs_disp = [xk for _f, xk, _yk in anchors]
                    ys_disp = [yk for _f, _xk, yk in anchors]
                else:
                    disp = []
                    for f, point in zip(frames, orig):
                        if not isinstance(point, (tuple, list)) or len(point) < 2:
                            continue
                        xx = self._project_movie_point_cached(roi_cache, kymo_w, point[0], point[1])
                        if xx is None:
                            continue
                        disp.append((xx, frame_to_y(f)))
                    if disp:
                        xs_disp, ys_disp = zip(*disp)
                    else:
                        xs_disp, ys_disp = [], []
                if len(xs_disp) >= 2 and len(ys_disp) >= 2:
                    segment = np.column_stack([xs_disp, ys_disp])
                    search_segments_by_color.setdefault(search_line_color, []).append(segment)

            if not hide_spots:
                for i in range(len(xs_pts) - 1):
                    if (np.isnan(xs_pts[i]) or np.isnan(ys_pts[i])
                            or np.isnan(xs_pts[i + 1]) or np.isnan(ys_pts[i + 1])):
                        continue
                    segment_color = point_colors[i] if point_colors is not None else uniform_color
                    spot_segments_by_color.setdefault(segment_color, []).append([
                        [xs_pts[i], ys_pts[i]],
                        [xs_pts[i + 1], ys_pts[i + 1]],
                    ])

                valid = np.isfinite(xs_pts) & np.isfinite(ys_pts)
                if np.any(valid):
                    valid_indices = np.nonzero(valid)[0]
                    if point_colors is None:
                        scatter_points_by_color.setdefault(uniform_color, []).extend(
                            np.column_stack([xs_pts[valid], ys_pts[valid]]).tolist()
                        )
                    else:
                        for point_idx in valid_indices:
                            point_color = point_colors[point_idx]
                            scatter_points_by_color.setdefault(point_color, []).append(
                                [xs_pts[point_idx], ys_pts[point_idx]]
                            )

                pick_entry = self._make_kymo_pick_entry(idx, xs_pts, ys_pts)
                if pick_entry is not None:
                    pick_entries.append(pick_entry)

            if show_labels:
                self._add_kymo_endpoint_labels(idx, x0, y0, x1, y1, False, markers)

        for color, segments in search_segments_by_color.items():
            if not segments:
                continue
            collection = LineCollection(
                segments,
                colors=color,
                linewidths=2,
                linestyles="--",
                alpha=0.8,
                zorder=2
            )
            self.ax.add_collection(collection)
            markers.append(collection)

        for color, segments in spot_segments_by_color.items():
            if not segments:
                continue
            collection = LineCollection(
                segments,
                colors=color,
                linewidths=linesize,
                alpha=0.8,
                zorder=3
            )
            self.ax.add_collection(collection)
            self._mark_kymo_dense_artist(collection)
            markers.append(collection)

        for color, chunks in scatter_points_by_color.items():
            if not chunks:
                continue
            points = np.asarray(chunks, dtype=float)
            if points.ndim != 2 or points.shape[0] == 0:
                continue
            scatter = self.ax.scatter(
                points[:, 0],
                points[:, 1],
                s=scattersize,
                color=color,
                zorder=4
            )
            self._mark_kymo_dense_artist(scatter)
            markers.append(scatter)
            scatter_artists.append(scatter)

        return True

    def _draw_kymo_trajectory(
        self,
        idx,
        roi,
        kymo_w,
        current_kymo_ch,
        frame_to_y,
        roi_cache,
        markers,
        scatter_artists,
        pick_entries,
        *,
        highlighted=False,
        showsearchline=True,
        skinny=False,
        show_labels=True,
        show_anchors=True,
    ):
        traj = self.navigator.trajectoryCanvas.trajectories[idx]
        ch = traj.get("channel")
        if ch is not None and ch != current_kymo_ch:
            return
        sf, sx, sy = traj["start"]
        ef, ex, ey = traj["end"]

        if not (is_point_near_roi((sx, sy), roi) and
                is_point_near_roi((ex, ey), roi)):
            return

        compute_x = self.navigator._compute_kymo_x
        x0 = compute_x(roi_cache, sx, sy, kymo_w)
        y0 = frame_to_y(sf)
        x1 = compute_x(roi_cache, ex, ey, kymo_w)
        y1 = frame_to_y(ef)
        if x0 is None or x1 is None:
            return

        halo_lw = 10 if highlighted else 0

        frames = traj["frames"]
        orig = traj["original_coords"]
        anchors = traj.get("anchors", []) or []
        traj_roi = traj.get("roi")
        anchors_match_current = (
            bool(anchors)
            and isinstance(traj_roi, dict)
            and self.navigator._roi_matches(traj_roi, roi)
        )

        scattersize = 9
        linesize = 1.5
        if skinny:
            scattersize = 3
            linesize = 0.5

        if showsearchline:
            search_line_color = self.navigator._get_uniform_traj_color(traj) or "#7da1ff"
            if anchors_match_current:
                xs_disp = [xk for _f, xk, _yk in anchors]
                ys_disp = [yk for _f, _xk, yk in anchors]
            else:
                disp = []
                for f, point in zip(frames, orig):
                    if not isinstance(point, (tuple, list)) or len(point) < 2:
                        continue
                    xx = self._project_movie_point_cached(roi_cache, kymo_w, point[0], point[1])
                    if xx is None:
                        continue
                    disp.append((xx, frame_to_y(f)))
                if not disp:
                    xs_disp, ys_disp = [], []
                else:
                    xs_disp, ys_disp = zip(*disp)

            if xs_disp and ys_disp:
                dotted, = self.ax.plot(
                    xs_disp, ys_disp,
                    color=search_line_color, linestyle="--", linewidth=2,
                    alpha=0.8, zorder=(4 if highlighted else 2),
                    solid_capstyle='round', dash_capstyle='round'
                )
                markers.append(dotted)

        spots = traj.get("spot_centers", [None]*len(frames))
        pts = []
        for f, spot in zip(frames, spots):
            yy = frame_to_y(f)
            if isinstance(spot, (tuple, list, np.ndarray)) and len(spot) >= 2:
                xx = self._project_movie_point_cached(roi_cache, kymo_w, spot[0], spot[1])
                if xx is not None:
                    pts.append((xx, yy))
                else:
                    pts.append((np.nan, np.nan))
            else:
                pts.append((np.nan, np.nan))
        if not pts:
            return
        xs_pts, ys_pts = (np.asarray(vals, dtype=float) for vals in zip(*pts))

        hide_spots = getattr(self.navigator, "hide_kymo_spots", False)
        if not hide_spots:
            scatter_kwargs, line_color = self.navigator._get_traj_colors(traj)

            line = None
            pts_colors = scatter_kwargs.get("c")
            if isinstance(pts_colors, (list, tuple, np.ndarray)) and len(pts_colors) == len(xs_pts):
                segs = []
                seg_colors = []
                for i in range(len(xs_pts) - 1):
                    if (np.isnan(xs_pts[i]) or np.isnan(ys_pts[i])
                            or np.isnan(xs_pts[i + 1]) or np.isnan(ys_pts[i + 1])):
                        continue
                    segs.append([[xs_pts[i], ys_pts[i]], [xs_pts[i + 1], ys_pts[i + 1]]])
                    seg_colors.append(pts_colors[i])
                if segs:
                    line = LineCollection(
                        segs,
                        colors=seg_colors,
                        linewidths=linesize,
                        alpha=0.8,
                        zorder=(5 if highlighted else 3)
                    )
                    self.ax.add_collection(line)
                    self._mark_kymo_dense_artist(line)

            if line is None:
                line, = self.ax.plot(
                    xs_pts, ys_pts, linestyle='-', color=line_color,
                    linewidth=linesize, alpha=0.8,
                    zorder=(5 if highlighted else 3)
                )
                self._mark_kymo_dense_artist(line)

            markers.append(line)

            if getattr(self.navigator, "connect_all_spots", False):
                valid_idxs = [i for i, (x, y) in enumerate(pts) if not np.isnan(x)]

                if valid_idxs:
                    first_valid = valid_idxs[0]
                    if first_valid != 0:
                        if orig and isinstance(orig[0], (tuple, list, np.ndarray)) and len(orig[0]) >= 2:
                            x0_orig, y0_orig = orig[0]
                            xx0 = self._project_movie_point_cached(roi_cache, kymo_w, x0_orig, y0_orig)
                            if xx0 is not None:
                                yy0 = frame_to_y(frames[0])
                                gx1, gy1 = pts[first_valid]
                                gap_line, = self.ax.plot(
                                    [xx0, gx1], [yy0, gy1],
                                    linestyle='-', color=line_color,
                                    linewidth=linesize, alpha=0.8, zorder=2
                                )
                                self._mark_kymo_dense_artist(gap_line)
                                markers.append(gap_line)

                    for a, b in zip(valid_idxs, valid_idxs[1:]):
                        if b != a + 1:
                            gx0, gy0 = pts[a]
                            gx1, gy1 = pts[b]
                            gap_line, = self.ax.plot(
                                [gx0, gx1], [gy0, gy1],
                                linestyle='-', color=line_color,
                                linewidth=1.1, alpha=0.4, zorder=2
                            )
                            self._mark_kymo_dense_artist(gap_line)
                            markers.append(gap_line)

                    last_valid = valid_idxs[-1]
                    if last_valid != len(pts) - 1:
                        if orig and isinstance(orig[-1], (tuple, list, np.ndarray)) and len(orig[-1]) >= 2:
                            xN_orig, yN_orig = orig[-1]
                            xxN = self._project_movie_point_cached(roi_cache, kymo_w, xN_orig, yN_orig)
                            if xxN is not None:
                                yyN = frame_to_y(frames[-1])
                                gx0, gy0 = pts[last_valid]
                                gap_line, = self.ax.plot(
                                    [gx0, xxN], [gy0, yyN],
                                    linestyle='-', color=line_color,
                                    linewidth=1.1, alpha=0.4, zorder=2
                                )
                                self._mark_kymo_dense_artist(gap_line)
                                markers.append(gap_line)

            scatter = self.ax.scatter(xs_pts, ys_pts, s=scattersize, **scatter_kwargs)
            scatter.traj_idx = idx
            self._mark_kymo_dense_artist(scatter)
            markers.append(scatter)
            scatter_artists.append(scatter)
            pick_entry = self._make_kymo_pick_entry(idx, xs_pts, ys_pts)
            if pick_entry is not None:
                pick_entries.append(pick_entry)

        if highlighted and show_anchors:
            ax_xs, ax_ys = [], []
            if anchors_match_current:
                ax_xs = [xk for _f, xk, _yk in anchors]
                ax_ys = [yk for _f, _xk, yk in anchors]
            else:
                nodes = traj.get("nodes", []) or []
                for frame, x, y in nodes:
                    xx = self._project_movie_point_cached(roi_cache, kymo_w, x, y)
                    if xx is None:
                        continue
                    ax_xs.append(xx)
                    ax_ys.append(frame_to_y(frame))

            if ax_xs and ax_ys:
                anchor_scatter = self.ax.scatter(
                    ax_xs, ax_ys,
                    s=(8**2),
                    c="#4f6bdc",
                    alpha=0.7,
                    linewidths=0,
                    zorder=6
                )
                markers.append(anchor_scatter)

        if not hide_spots and highlighted and halo_lw:
            halo, = self.ax.plot(
                xs_pts, ys_pts,
                linestyle='-', color="#7da1ff",
                solid_capstyle='round', solid_joinstyle='round',
                linewidth=halo_lw, alpha=0.5, zorder=1
            )
            self._mark_kymo_dense_artist(halo)
            markers.append(halo)

        if show_labels:
            self._add_kymo_endpoint_labels(idx, x0, y0, x1, y1, highlighted, markers)

    def draw_selected_trajectory_on_kymo(
        self,
        draw_idle=True,
        showsearchline=True,
        skinny=False,
        show_labels=True,
        invert_y=True,
    ):
        self.invalidate_blit_background()
        self.clear_kymo_selected_trajectory_markers(draw_idle=False)
        if self.navigator is None:
            return
        overlay_mode = self.navigator.get_kymo_traj_overlay_mode()
        if overlay_mode == "off":
            return
        ctx = self._kymo_overlay_context(invert_y=invert_y)
        if ctx is None:
            return
        roi, kymo_w, current_kymo_ch, frame_to_y, roi_cache = ctx
        selected_idx = self.navigator.trajectoryCanvas.table_widget.currentRow()
        trajectories = self.navigator.trajectoryCanvas.trajectories
        if selected_idx < 0 or selected_idx >= len(trajectories):
            return

        show_anchors = True
        anchor_btn = getattr(self.navigator, "kymo_anchor_overlay_button", None)
        if anchor_btn is not None and not anchor_btn.isChecked():
            show_anchors = False

        markers = []
        scatters = []
        pick_entries = []
        if getattr(self.navigator, "kymo_anchor_edit_mode", False):
            self._draw_kymo_anchor_edit_overlay(
                selected_idx,
                roi,
                kymo_w,
                current_kymo_ch,
                frame_to_y,
                markers,
                showsearchline=showsearchline,
                show_anchors=show_anchors,
            )
        else:
            self._draw_kymo_trajectory(
                selected_idx,
                roi,
                kymo_w,
                current_kymo_ch,
                frame_to_y,
                roi_cache,
                markers,
                scatters,
                pick_entries,
                highlighted=True,
                showsearchline=showsearchline,
                skinny=skinny,
                show_labels=show_labels,
                show_anchors=show_anchors,
            )
        self.kymo_selected_trajectory_markers = markers
        self._kymo_selected_scatter_artists = scatters
        self._kymo_selected_pick_entries = pick_entries
        self._refresh_kymo_clickable_artists()
        self._refresh_kymo_label_bboxes()
        self.invalidate_blit_background()
        if draw_idle:
            self.draw_idle()

    def draw_trajectories_on_kymo(self, showsearchline=True, skinny=False, show_labels=True, invert_y=True):
        self._finish_kymo_interaction(redraw=False)
        self.invalidate_blit_background()
        self.clear_kymo_trajectory_markers(draw_idle=False)
        if self.navigator is None:
            return

        overlay_mode = self.navigator.get_kymo_traj_overlay_mode()
        if overlay_mode == "off":
            return

        ctx = self._kymo_overlay_context(invert_y=invert_y)
        if ctx is None:
            return
        roi, kymo_w, current_kymo_ch, frame_to_y, roi_cache = ctx

        selected_idx = self.navigator.trajectoryCanvas.table_widget.currentRow()

        show_anchors = True
        anchor_btn = getattr(self.navigator, "kymo_anchor_overlay_button", None)
        if anchor_btn is not None and not anchor_btn.isChecked():
            show_anchors = False

        if getattr(self.navigator, "kymo_anchor_edit_mode", False):
            self.draw_selected_trajectory_on_kymo(
                draw_idle=False,
                showsearchline=showsearchline,
                skinny=skinny,
                show_labels=show_labels,
                invert_y=invert_y,
            )
            self._refresh_kymo_label_bboxes()
            return

        markers = []
        scatters = []
        pick_entries = []
        if overlay_mode == "all":
            batched = self._draw_kymo_base_trajectories_batched(
                selected_idx,
                roi,
                kymo_w,
                current_kymo_ch,
                frame_to_y,
                roi_cache,
                markers,
                scatters,
                pick_entries,
                showsearchline=showsearchline,
                skinny=skinny,
                show_labels=show_labels,
                show_anchors=show_anchors,
            )
            if not batched:
                for idx in range(len(self.navigator.trajectoryCanvas.trajectories)):
                    self._draw_kymo_trajectory(
                        idx,
                        roi,
                        kymo_w,
                        current_kymo_ch,
                        frame_to_y,
                        roi_cache,
                        markers,
                        scatters,
                        pick_entries,
                        highlighted=False,
                        showsearchline=showsearchline,
                        skinny=skinny,
                        show_labels=show_labels,
                        show_anchors=show_anchors,
                    )

        self.kymo_trajectory_markers = markers
        self._kymo_base_scatter_artists = scatters
        self._kymo_base_pick_entries = pick_entries
        self._refresh_kymo_clickable_artists()

        if selected_idx >= 0:
            self.draw_selected_trajectory_on_kymo(
                draw_idle=False,
                showsearchline=showsearchline,
                skinny=skinny,
                show_labels=show_labels,
                invert_y=invert_y,
            )

        self._refresh_kymo_label_bboxes()
        self.invalidate_blit_background()

    def clear_kymo_selected_trajectory_markers(self, draw_idle=False):
        self.invalidate_blit_background()
        self._remove_kymo_artists(getattr(self, "kymo_selected_trajectory_markers", []))
        self.kymo_selected_trajectory_markers = []
        self._kymo_selected_scatter_artists = []
        self._kymo_selected_pick_entries = []
        self._refresh_kymo_clickable_artists()
        self._refresh_kymo_label_bboxes()
        if draw_idle:
            self.draw_idle()

    def clear_kymo_trajectory_markers(self, draw_idle=False):
        self._finish_kymo_interaction(redraw=False)
        self.invalidate_blit_background()
        # Remove start/end circle markers and annotations.
        if self.navigator is not None and hasattr(self.navigator, "trajectory_markers"):
            for marker in self.navigator.trajectory_markers:
                try:
                    marker.remove()
                except Exception as e:
                    pass
            self.navigator.trajectory_markers = []

        if self.navigator is not None and getattr(self.navigator, "_kymo_label_to_row", None) is not None:
            self.navigator._kymo_label_to_row.clear()

        self._remove_kymo_artists(getattr(self, "kymo_trajectory_markers", []))
        self._remove_kymo_artists(getattr(self, "kymo_selected_trajectory_markers", []))
        self.kymo_trajectory_markers = []
        self.kymo_selected_trajectory_markers = []
        self._kymo_base_scatter_artists = []
        self._kymo_selected_scatter_artists = []
        self._kymo_base_pick_entries = []
        self._kymo_selected_pick_entries = []
        self._refresh_kymo_clickable_artists()
        self._kymo_label_bboxes.clear()
        if draw_idle:
            self.draw_idle()

    def remove_circle(self):
        marker = getattr(self, "_marker", None)
        if marker is not None:
            if getattr(self, "_bg", None) is not None:
                try:
                    self.fig.canvas.restore_region(self._bg)
                    self.fig.canvas.blit(self.ax.bbox)
                except Exception:
                    pass
            try:
                marker.remove()
            except Exception as e:
                pass
            self._marker = None

    def set_display_range(self, vmin, vmax):
        """
        Set the current display contrast range without modifying underlying data.
        Ensures that vmin is always less than vmax to avoid errors in normalization.
        """
        #print("displaying:", vmin, vmax)
        if vmin >= vmax:
            # If vmin is not less than vmax, adjust vmax to guarantee a valid range.
            # Here we choose an arbitrary minimal gap of 1.
            #print(f"Warning: vmin ({vmin}) >= vmax ({vmax}). Adjusting vmax to {vmin + 1}.")
            vmax = vmin + 1

        self._vmin = vmin
        self._vmax = vmax
        if self._im is not None:
            self._im.set_clim(self._vmin, self._vmax)
            self.draw_idle()
