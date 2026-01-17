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
        self.scale = 1.0  # Data units per pixel (uniform in x and y)
        self.padding = 1.25
        self.zoom_center = None  # in data coordinates
        self.manual_zoom = False
        self._update_pending = False
        self.manual_zoom = False

        self._kymo_label_bboxes: dict[Text, Bbox] = {}

        self.fig.patch.set_alpha(0)
        self.ax.patch.set_alpha(0)

        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocus()

        self.navigator = navigator
        
        self.mpl_connect("scroll_event", self.on_scroll)
        self.mpl_connect("button_press_event", self.on_mouse_press)
        self.mpl_connect("motion_notify_event", self.on_mouse_move)
        self.mpl_connect("button_release_event", self.on_mouse_release)
        # self.mpl_connect("pick_event", self.on_pick_event)

        self.scatter_objs_traj = []

        self._ctrl_panning = False

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

    def reset_canvas(self):
        self.ax.cla()
        self._im = None
        self._marker = None
        self._is_panning = False
        self._pan_start = None
        self._orig_xlim = None
        self._orig_ylim = None
        self.zoom_center = None
        self.scale = 1.0

    def display_image(self, image):
        """Show kymo image but preserve zoom if user has panned or scrolled."""
        if image is None:
            return

        # If we’re already in a manual zoom state, just update the data
        if self._im is not None and self.manual_zoom:
            self._im.set_data(image)
            self.draw()
            return

        # Otherwise do the initial full reset + fit
        self.reset_canvas()
        p15, p99 = np.percentile(image, (15, 99))
        img8     = np.clip((image - p15)/(p99-p15), 0, 1)*255
        img8     = img8.astype(np.uint8)
        h, w     = img8.shape
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

    def update_view(self):
        if self.image is None or self.zoom_center is None:
            return

        # 1) compute the new data‐limits exactly as before
        widget_w = self.width()
        widget_h = self.height()
        view_w   = widget_w * self.scale
        view_h   = widget_h * self.scale
        cx, cy   = self.zoom_center

        self.ax.set_xlim(cx - view_w/2, cx + view_w/2)
        self.ax.set_ylim(cy - view_h/2, cy + view_h/2)

        # 2) redraw everything (synchronous)
        self.draw()

        # 3) grab a fresh background for blit loop
        #    this background now includes the image + any permanent lines
        self._bg = self.copy_from_bbox(self.ax.bbox)

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
        self.update_view()
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
        elif event.button == 1:
            if hasattr(self.parent(), 'on_kymo_left_click'):
                self.parent().on_kymo_left_click(event)

    def on_mouse_move(self, event):
        if self._is_panning and event.inaxes == self.ax:
            self.manual_zoom = True
            inv = self.ax.transData.inverted()
            start_data = inv.transform(self._pan_start)
            current_data = inv.transform((event.x, event.y))
            ddata = (current_data[0] - start_data[0], current_data[1] - start_data[1])
            new_xlim = (self._orig_xlim[0] - ddata[0], self._orig_xlim[1] - ddata[0])
            new_ylim = (self._orig_ylim[0] - ddata[1], self._orig_ylim[1] - ddata[1])
            self.ax.set_xlim(new_xlim)
            self.ax.set_ylim(new_ylim)
            # Also update the zoom_center to match the new center.
            cx = (new_xlim[0] + new_xlim[1]) / 2.0
            cy = (new_ylim[0] + new_ylim[1]) / 2.0
            self.zoom_center = (cx, cy)
            self.update_view()
            # Update pan origin for incremental panning (prevents anchor sticking)
            self._pan_start = (event.x, event.y)
            self._orig_xlim = self.ax.get_xlim()
            self._orig_ylim = self.ax.get_ylim()

    def on_mouse_release(self, event):
        self._is_panning = False
        # Force a final synchronous redraw and update the background
        self.update_view()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Recompute max_scale on resize so zoom-out still fills the new widget size
        if self.image is not None:
            h, w = self.image.shape[:2]
            widget_w = self.width() if self.width() > 0 else w
            widget_h = self.height() if self.height() > 0 else h
            base = max(w / widget_w, h / widget_h)
            self.max_scale = base * self.padding
        # When resizing, update the view so that the zoom_center and scale are maintained.
        self.update_view()

    def add_circle(self, x, y, size=12, color='grey'):
        """
        Draw a hollow circle marker at (x, y) using blitting for performance.
        """
        # Remove previous marker
        if getattr(self, "_marker", None) is not None:
            try:
                self._marker.remove()
            except Exception:
                pass

        # Prepare blitting background
        if not hasattr(self, "_bg") or self._bg is None:
            # first time: full draw and cache
            self.draw()
            self._bg = self.copy_from_bbox(self.ax.bbox)

        # Restore background to clear old marker
        self.fig.canvas.restore_region(self._bg)

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

    def draw_trajectories_on_kymo(self, showsearchline=True, skinny=False):
        
        ax = self.ax

        kymo_name = self.navigator.kymoCombo.currentText()
        info = self.navigator.kymo_roi_map.get(kymo_name, {})
        current_kymo_ch = info.get("channel")
        self._kymo_label_bboxes.clear()

        # 1) clear any existing overlays
        self.clear_kymo_trajectory_markers()

        if not self.navigator.traj_overlay_button.isChecked():
            return

        # 2) fetch ROI & image info
        roi_key = (self.navigator.roiCombo.currentText()
                if self.navigator.roiCombo.count() > 0
                else kymo_name)
        if roi_key not in self.navigator.rois or self.image is None:
            return
        roi = self.navigator.rois[roi_key]
        kymo_w = self.image.shape[1]
        num_frames = (self.navigator.movie.shape[0]
                    if self.navigator.movie is not None else 0)
        num_frames_m1 = num_frames - 1

        # 3) which trajectory is highlighted?
        selected_idx = self.navigator.trajectoryCanvas.table_widget.currentRow()

        # cache transforms & functions
        roi_cache = self.navigator._compute_roi_cache(roi)
        compute_x = self.navigator._compute_kymo_x
        compute_x_roi = self.navigator.compute_kymo_x_from_roi
        ax = self.ax

        # 4) prepare marker storage
        self.scatter_objs_traj= []
        markers = []
        self.kymo_trajectory_markers = markers

        # 5) loop once
        for idx, traj in enumerate(self.navigator.trajectoryCanvas.trajectories):
            ch = traj.get("channel")
            if ch is not None and ch != current_kymo_ch:
                continue
            sf, sx, sy = traj["start"]
            ef, ex, ey = traj["end"]

            # skip outside ROI
            if not (is_point_near_roi((sx, sy), roi) and
                    is_point_near_roi((ex, ey), roi)):
                continue

            # compute kymo coords
            x0 = compute_x(roi_cache, sx, sy, kymo_w)
            y0 = num_frames_m1 - sf
            x1 = compute_x(roi_cache, ex, ey, kymo_w)
            y1 = num_frames_m1 - ef

            # styling
            is_hl = (idx == selected_idx)
            halo_lw = 10 if is_hl else 0
            traj_label = traj.get("file_index", str(traj["trajectory_number"]))
            face = "#7da1ff" if is_hl else "#cbd9ff"
            textcolor = "white" if is_hl else "black"
            alpha_lbl = 0.8 if is_hl else 0.6


            # build display points
            frames = traj["frames"]
            orig = traj["original_coords"]

            scattersize = 9
            linesize = 1.5
            if skinny:
                scattersize = 3
                linesize = 0.5


            if showsearchline:
                disp = [
                    (compute_x_roi(roi, x, y, kymo_w), num_frames_m1 - f)
                    for f, (x, y) in zip(frames, orig)
                ]
                xs_disp, ys_disp = zip(*disp)

                # 5a) dotted start/end connector
                dotted, = ax.plot(
                    xs_disp, ys_disp,
                    color="#7da1ff", linestyle="--", linewidth=2,
                    alpha=0.8, zorder=2,
                    solid_capstyle='round', dash_capstyle='round'
                )
                markers.append(dotted)

            # 5b) magenta line through spot centers
            spots = traj.get("spot_centers", [None]*len(frames))
            pts = []
            for (x_o, y_o), f, spot in zip(orig, frames, spots):
                yy = num_frames_m1 - f
                xo = compute_x_roi(roi, x_o, y_o, kymo_w)
                if spot is not None:
                    xx = compute_x_roi(roi, spot[0], spot[1], kymo_w)
                    pts.append((xx, yy))
                else:
                    pts.append((np.nan, np.nan))
            xs_pts, ys_pts = map(np.array, zip(*pts))

            scatter_kwargs, line_color = self.navigator._get_traj_colors(traj)

            line, = ax.plot(xs_pts, ys_pts, linestyle='-', color=line_color,
                            linewidth=linesize, alpha=0.8, zorder=3)

            markers.append(line)

            sx0, sy0 = x0, y0
            sx1, sy1 = x1, y1

            if getattr(self.navigator, "connect_all_spots", False):
                # find the indices of all actually-fitted spots
                valid_idxs = [i for i,(x,y) in enumerate(pts) if not np.isnan(x)]

                if valid_idxs:
                    # ––– connect from VERY FIRST search-center to first valid spot –––
                    first_valid = valid_idxs[0]
                    if first_valid != 0:
                        # get display coord of orig[0]
                        x0_orig, y0_orig = orig[0]
                        xx0 = compute_x_roi(roi, x0_orig, y0_orig, kymo_w)
                        yy0 = num_frames_m1 - frames[0]
                        # get display coord of the first valid spot
                        gx1, gy1 = pts[first_valid]
                        gap_line, = ax.plot(
                            [xx0, gx1], [yy0, gy1],
                            linestyle='-', color=line_color,
                            linewidth=linesize, alpha=0.8, zorder=2
                        )
                        markers.append(gap_line)

                    # ––– gaps BETWEEN valid spots (as before) –––
                    for a, b in zip(valid_idxs, valid_idxs[1:]):
                        if b != a + 1:
                            gx0, gy0 = pts[a]
                            gx1, gy1 = pts[b]
                            gap_line, = ax.plot(
                                [gx0, gx1], [gy0, gy1],
                                linestyle='-', color=line_color,
                                linewidth=1.1, alpha=0.4, zorder=2
                            )
                            markers.append(gap_line)

                    # ––– connect from last valid spot to VERY LAST search-center –––
                    last_valid = valid_idxs[-1]
                    if last_valid != len(pts) - 1:
                        # get display coord of orig[-1]
                        xN_orig, yN_orig = orig[-1]
                        xxN = compute_x_roi(roi, xN_orig, yN_orig, kymo_w)
                        yyN = num_frames_m1 - frames[-1]
                        # get display coord of the last valid spot
                        gx0, gy0 = pts[last_valid]
                        gap_line, = ax.plot(
                            [gx0, xxN], [gy0, yyN],
                            linestyle='-', color=line_color,
                            linewidth=1.1, alpha=0.4, zorder=2
                        )
                        markers.append(gap_line)
                
            # per-point coloring
            scatter = ax.scatter(xs_pts, ys_pts, s=scattersize, picker=True, **scatter_kwargs)
            
            scatter.traj_idx = idx
            markers.append(scatter)
            self.scatter_objs_traj.append(scatter)

            # 5c) optional halo behind
            if is_hl and halo_lw:
                halo, = ax.plot(
                    xs_pts, ys_pts,
                    linestyle='-', color="#7da1ff",
                    solid_capstyle='round', solid_joinstyle='round',
                    linewidth=halo_lw, alpha=0.5, zorder=1
                )
                markers.append(halo)

            # 5d) annotate A/B
            # compute pixel‐space offsets once
            dispA = ax.transData.transform((sx0, sy0))
            dispB = ax.transData.transform((sx1, sy1))
            v = dispB - dispA
            norm = np.hypot(*v)
            u = v / norm if norm else np.array([1.0, 0.0])
            offset = 15
            for (cx, cy, suf), sign in [((x0, y0, 'A'), -1), ((x1, y1, 'B'), +1)]:
                dx, dy = u * (offset * sign)
                lbl = ax.annotate(
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
                    picker=10
                )
                self.navigator._kymo_label_to_row[lbl] = idx
                markers.append(lbl)

        # --- single draw + compute all label bboxes at once ---
        canvas = self.figure.canvas
        # canvas.draw()
        renderer = canvas.get_renderer()
        for m in markers:
            if isinstance(m, Text):
                self._kymo_label_bboxes[m] = m.get_window_extent(renderer)

    def clear_kymo_trajectory_markers(self):
        # Remove start/end circle markers and annotations.
        if hasattr(self.navigator, "trajectory_markers"):
            for marker in self.navigator.trajectory_markers:
                try:
                    marker.remove()
                except Exception as e:
                    pass
            self.navigator.trajectory_markers = []

        # Remove magenta analysis marker lines.
        if hasattr(self, "kymo_trajectory_markers"):
            for line in self.kymo_trajectory_markers:
                try:
                    line.remove()
                except Exception as e:
                    pass
            self.kymo_trajectory_markers = []

    def remove_circle(self):
        if hasattr(self, "_marker"):
            try:
                self._marker.remove()
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
