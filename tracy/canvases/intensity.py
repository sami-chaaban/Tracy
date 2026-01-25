from ._shared import *

class IntensityCanvas(FigureCanvas):
    def __init__(self, parent=None, navigator=None):
        self.fig = Figure(figsize=(5.5, 4), constrained_layout=False)
        
        super().__init__(self.fig)
        self.setParent(parent)
        self.navigator = navigator
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocus()

        gs = GridSpec(2, 1, height_ratios=[0.1, 0.9], figure=self.fig)
        self.ax_top = self.fig.add_subplot(gs[0])
        self.ax_bottom = self.fig.add_subplot(gs[1], sharex=self.ax_top)
        self.ax_top.axis("off")
        self.ax_bottom.axis("off")

        self.fig.patch.set_alpha(0)
        self.ax_bottom.patch.set_alpha(0)
        self.ax_bottom.set_facecolor('none')

        self.highlight_marker_bottom, = self.ax_bottom.plot(
            [], [], marker='s', markersize=12,
            markerfacecolor='none', markeredgecolor='#7da1ff',
            markeredgewidth=2, linestyle='none',
            visible=False
        )
        self.highlight_marker_top, = self.ax_top.plot(
            [], [], marker='s', markersize=12,
            markerfacecolor='none', markeredgecolor='#7da1ff',
            markeredgewidth=2, linestyle='none',
            visible=False
        )
        self.top_vline = Line2D(
            [], [], color='#7da1ff', linewidth=1.5,
            clip_on=False, visible=False
        )
        self.ax_top.add_line(self.top_vline)

        self.draw()

        # Capture the background for blitting
        self._background = self.copy_from_bbox(self.fig.bbox)

        # Hook draw_event so we re‐capture on any full redraw (e.g. resize)
        self.mpl_connect("draw_event", self._on_full_draw)
        # Hook pick_event for scatter dots
        self.mpl_connect("pick_event", self.on_pick_event)

        self._legend = None
        self.mpl_connect("axes_enter_event", self._on_axes_enter)
        self.mpl_connect("axes_leave_event", self._on_axes_leave)

        self.current_index = 0
        self.scatter_obj_top = None
        self.scatter_obj_bottom = None
        self.point_highlighted=False
        self._last_plot_args = None

        self._mouse_inside = False
        self._resize_layout_timer = QTimer(self)
        self._resize_layout_timer.setSingleShot(True)
        self._resize_layout_timer.timeout.connect(self._deferred_resize_layout)
        self._resize_highlight_timer = QTimer(self)
        self._resize_highlight_timer.setSingleShot(True)
        self._resize_highlight_timer.timeout.connect(self._deferred_resize_highlight)

    # def showEvent(self, event):
    #     super().showEvent(event)
    #     QTimer.singleShot(0, lambda: self.plot_intensity(**self._last_plot_args))

    def plot_intensity(self, frames, intensities, avg_intensity=None, median_intensity=None,
                    colors=None, max_frame=None, *, relayout=True):
        # Check if there is valid intensity data
        if not intensities or all(val is None for val in intensities):
            self.ax_top.clear()
            self.ax_bottom.clear()
            self.ax_top.axis("off")
            self.ax_bottom.axis("off")
            self.ax_bottom.text(0.5, 0.5, "No valid intensity data", 
                                ha='center', va='center',
                                transform=self.fig.transFigure, color='grey')
            self.draw()
            return

        # Filter out None values
        valid_intensities = [val for val in intensities if val is not None]
        if valid_intensities:
            avg_intensity = np.mean(valid_intensities)
            median_intensity = np.median(valid_intensities)
        else:
            avg_intensity, median_intensity = None, None
        
        frames_display = [f + 1 for f in frames]
        # Ensure frames and intensities have matching lengths to avoid scatter errors
        if len(frames_display) != len(intensities):
            n = min(len(frames_display), len(intensities))
            frames_display = frames_display[:n]
            intensities = intensities[:n]
            if colors is not None:
                colors = colors[:n]

        # Clear both axes.
        self.ax_top.clear()
        self.ax_bottom.clear()
        
        if not frames or not intensities:
            for ax in (self.ax_top, self.ax_bottom):
                ax.text(0.5, 0.5, "No intensity data available", ha='center', va='center',
                        transform=ax.transAxes)
            self.draw()
            return
        
        if avg_intensity is None:
            avg_intensity = np.mean(intensities)
        if median_intensity is None:
            median_intensity = np.median(intensities)
        
        # --- Top subplot: Draw a segmented horizontal status bar ---
        # Position the top axis so that its height is 0.035 and its bottom is at 0.86.
        # Thus, the top edge is at 0.86 + 0.035 = 0.895 (leaving a top margin of 0.105).
        self.ax_top.set_visible(True)
        # Set internal coordinates: x spans the frames and y spans [0, 1].
        self.ax_top.set_xlim(min(frames_display), max(frames_display) + 1)
        self.ax_top.set_ylim(0, 1)
        self.ax_top.axis("off")

        self.ax_top.xaxis.set_major_locator(MaxNLocator(integer=True))
        
        bar_height = 0.3
        y0 = 0.5 - bar_height/2
        radius = bar_height / 2.0

        n = len(frames_display)
        if n == 0:
            return

        def _normalize_colors_list(raw, count):
            seq = list(raw) if raw is not None else []
            if len(seq) < count:
                seq = seq + ["magenta"] * (count - len(seq))
            else:
                seq = seq[:count]
            return seq

        # --- Build a canonical colors list of exactly length n ---
        if colors and "c" in colors:
            colors_list = _normalize_colors_list(colors["c"], n)
        elif colors and "color" in colors:
            colors_list = [colors["color"]] * n
        else:
            colors_list = ["magenta"] * n

        for i, frame in enumerate(frames_display):
            # pick colour
            col = colors_list[i]

            # compute segment width
            if i < n - 1:
                width = frames_display[i+1] - frame
            else:
                width = 1

            # draw a plain rectangle with square corners
            rect = Rectangle(
                (frame, y0),
                width,
                bar_height,
                facecolor=col,
                edgecolor="none"
            )
            self.ax_top.add_patch(rect)

        self.ax_bottom.set_ylabel("Intensity (A.U.)", fontsize=12)
        self.ax_bottom.set_xlabel("Frame", fontsize=12)
        self.ax_bottom.tick_params(axis='both', which='major', labelsize=12)
        self.ax_bottom.tick_params(axis='x', which='major', labelsize=10)

        # === build scatter_args correctly ===
        if isinstance(colors, dict):
            # kwargs provided by _get_traj_colors
            scatter_args = dict(colors)              # clone it
            if "c" in scatter_args:
                scatter_args["c"] = _normalize_colors_list(scatter_args["c"], n)
            scatter_args.update(picker=True, s=20, edgecolors="black", linewidths=0.5)   # add these defaults
        else:
            if isinstance(colors, (list, tuple, np.ndarray)):
                colors = _normalize_colors_list(colors, n)
            # colors should be a list of hex strings (or None)
            scatter_args = {
                "c": colors if colors else "magenta",
                "picker": True,
                "s": 20,
                "edgecolors": "black",
                "linewidths": 0.5
            }

        self.scatter_obj_bottom = self.ax_bottom.scatter(frames_display, intensities, **scatter_args)

        handles = []

        if self.navigator.show_steps:
            # make sure the current_index really points at something valid
            idx = self.navigator.trajectoryCanvas.current_index
            trajs = self.navigator.trajectoryCanvas.trajectories
            if idx is None or idx < 0 or idx >= len(trajs):
                # nothing to draw
                return

            traj      = trajs[idx]
            step_idxs = traj.get("step_indices", [])
            seg_meds  = traj.get("step_medians", [])
            # 2) Draw each segment’s horizontal line at its cached median:
            if seg_meds is not None:
                for (start_f, end_f, med) in seg_meds:
                    x0 = start_f + 1          # convert to 1-based if needed
                    x1 = end_f   + 1
                    if x0 == x1:
                        dx = 0.5
                        self.ax_bottom.hlines(
                            y=med,
                            xmin=x0 - dx,
                            xmax=x0 + dx,
                            color="#4CAF50",
                            linewidth=2,
                            zorder=4,
                            label="_nolegend_"
                        )
                    else:
                        self.ax_bottom.hlines(
                            y=med,
                            xmin=x0,
                            xmax=x1,
                            color="#4CAF50",
                            linewidth=2,
                            zorder=4,
                            label="_nolegend_"
                        )

                for i in range(len(seg_meds)-1):
                    _, end_f, med1 = seg_meds[i]
                    start_f2, _, med2 = seg_meds[i+1]
                    x_vert = end_f + 1  # same x‐position as end of segment i
                    self.ax_bottom.vlines(
                        x=x_vert,
                        ymin=min(med1, med2),
                        ymax=max(med1, med2),
                        color="#4CAF50",
                        linewidth=2,
                        zorder=4,
                        label="_nolegend_"
                    )

                # 3) One single legend entry for “Steps”:
                step_proxy = Line2D([0], [0], color="#4CAF50", linewidth=1.5, label="Steps")
                handles.append(step_proxy)

        try:
            med_line = self.ax_bottom.axhline(median_intensity, color='magenta', linestyle='--', linewidth=1.5,
                solid_capstyle='round',
                dash_capstyle='round',
                label=f"Med: {median_intensity:.2f}")
            avg_line = self.ax_bottom.axhline(avg_intensity, color='grey', linestyle='--', linewidth=1.5,
                solid_capstyle='round',
                dash_capstyle='round',
                label=f"Avg: {avg_intensity:.2f}")
            handles.extend([med_line, avg_line])
        except np.linalg.LinAlgError as e: 
            pass
            #print("Warning: could not plot horizontal line due to singular transform matrix:", e)
        #self.ax_bottom.legend(loc="upper right", fontsize=12, frameon=True)
        self.ax_bottom.set_xlim(min(frames_display), max(frames_display))
        self.ax_bottom.xaxis.set_major_locator(MaxNLocator(integer=True))

        legend = self.ax_bottom.legend(
            handles=handles,
            loc="upper right",
            fontsize=10,
            frameon=True,
            labelspacing=0.5,
            handlelength=2
        )
        frame = legend.get_frame()
        frame.set_facecolor("white")
        frame.set_alpha(0.8)
        frame.set_edgecolor("none")
        frame.set_boxstyle("round,pad=0.2")

        self._legend = legend

        if self._mouse_inside:
            self._legend.set_visible(False)

        valid_vals = [
            val for val, col in zip(intensities, colors_list)
            if col != "grey" and val is not None
        ]

        if valid_vals:
            ymin, ymax = min(valid_vals), max(valid_vals)
            # if there’s any real spread, pad by 10%; otherwise default to ±1
            margin = 0.1 * (ymax - ymin) if ymax > ymin else 1
            self.ax_bottom.set_ylim(ymin - margin, ymax + margin)
        
        if relayout:
            self._apply_intensity_layout()

        self.fig.canvas.draw()                       # synchronous full draw
        self._background = self.copy_from_bbox(self.fig.bbox)

        self._last_plot_args = dict(
            frames=frames,
            intensities=intensities,
            avg_intensity=avg_intensity,
            median_intensity=median_intensity,
            colors=colors,
            max_frame=max_frame
        )
        
        # self.highlight_current_point()

    def on_pick_event(self, event):

        # artist = event.artist

        # # 1) If it’s one of our labels or a scatter point, it carries traj_idx:
        # if hasattr(artist, "traj_idx"):
        #     idx = artist.traj_idx
        #     # Select that row:
        #     table = self.navigator.trajectoryCanvas.table_widget
        #     table.selectRow(idx)
        #     return

        # ignore middle-click picks
        if hasattr(event, 'mouseevent') and getattr(event.mouseevent, 'button', None) == 2:
            return

        # Accept picks from either scatter if available.
        if event.artist not in [self.scatter_obj_top, self.scatter_obj_bottom]:
            return
        ind = event.ind
        if len(ind) < 1:
            return

        self.current_index = ind[0]
        if self.navigator is not None:
            self.navigator.jump_to_analysis_point(self.current_index)
            if self.navigator.sumBtn.isChecked():
                self.navigator.sumBtn.setChecked(False)
        self.highlight_current_point()
        if self.navigator is not None:
            try:
                self.navigator.kymoCanvas.setFocus()
            except Exception:
                try:
                    self.navigator.setFocus()
                except Exception:
                    pass
        
    def highlight_current_point(self, override=False):
        if self.scatter_obj_bottom is None:
            return

        # get offsets
        offsets = None
        if self.ax_top.get_visible() and self.scatter_obj_top is not None:
            offsets = self.scatter_obj_top.get_offsets()
        elif self.scatter_obj_bottom is not None:
            offsets = self.scatter_obj_bottom.get_offsets()
        if offsets is None or len(offsets) <= self.current_index:
            return

        # clear any old highlight
        self.clear_highlight()

        x, y = offsets[self.current_index]
        self.point_highlighted = True

        # --- 1) always draw the top marker + v-line ---
        if self.ax_top.get_visible():
            self.highlight_marker_top.set_data([x], [y])
            self.highlight_marker_top.set_visible(True)
            self.ax_top.draw_artist(self.highlight_marker_top)

            self.top_vline.set_data([x, x], [0, 1])
            self.top_vline.set_visible(True)
            self.ax_top.draw_artist(self.top_vline)

        # --- 2) draw the bottom marker only if not override ---
        if not override:
            self.highlight_marker_bottom.set_data([x], [y])
            self.highlight_marker_bottom.set_visible(True)
            self.ax_bottom.draw_artist(self.highlight_marker_bottom)

        # --- 3) blit once for both axes ---
        self.fig.canvas.blit(self.fig.bbox)

        self._last_highlight_override = override

    def _on_full_draw(self, event):
        # whenever the canvas does a full draw, grab the background
        self._background = self.copy_from_bbox(self.fig.bbox)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Defer layout to avoid lag; keep data as-is during resize.
        self._resize_layout_timer.start(30)
        if self.point_highlighted:
            self._resize_highlight_timer.start(80)

    def _deferred_resize_layout(self):
        self._apply_intensity_layout()
        self.fig.canvas.draw_idle()

    def _deferred_resize_highlight(self):
        if self.point_highlighted:
            self.highlight_current_point(self._last_highlight_override)

    def _apply_intensity_layout(self):
        # Skip tight_layout when collapsed to (near) zero size to avoid empty bbox errors.
        if self.width() <= 2 or self.height() <= 2:
            return
        try:
            self.fig.tight_layout(
                pad=0.1,      # space around the whole figure
                w_pad=0.05,   # horizontal padding between subplots
                h_pad=0.05,   # vertical padding between subplots
                rect=[0.02, 0.02, 0.98, 0.98]  # left, bottom, right, top fractions
            )
        except (np.linalg.LinAlgError, ValueError):
            pass
            #print("Warning: could not plot run tight_layout on IntensityCanvas:", e)

    def clear_highlight(self):
        # 1) Hide the markers
        self.highlight_marker_bottom.set_visible(False)
        self.highlight_marker_top.set_visible(False)
        self.top_vline.set_visible(False)

        # 2) Reset internal state so we no longer think a point is highlighted
        self.point_highlighted = False
        self._last_highlight_override = False

        # 3) Restore the “no‐highlight” background and blit
        self.fig.canvas.restore_region(self._background)
        self.fig.canvas.blit(self.fig.bbox)

        # 4) Optional: if blitting again before a full draw, refresh background.
        # self.fig.canvas.draw()
        # self._background = self.copy_from_bbox(self.fig.bbox)

    def get_current_point_color(self):
        # only return a color if a point is currently highlighted
        # if not getattr(self, "point_highlighted", False):
        #     return "magenta"

        try:
            pc = self.scatter_obj_bottom
            facecols = pc.get_facecolors()
            cols = facecols if facecols.shape[0] > 0 else pc.get_edgecolors()
            if len(cols) == 1:
                rgba = cols[0]
                return mcolors.to_hex(rgba)
            elif self.current_index < len(cols):
                rgba = cols[self.current_index]
                return mcolors.to_hex(rgba)
            return "magenta"
        except Exception as e:
            # print(f"could not get color: {e}")
            return "magenta"

    def _on_axes_enter(self, event):
        """Hide the legend as soon as the mouse enters the bottom axes."""
        if event.inaxes is not self.ax_bottom or self._legend is None:
            return
        
        self._mouse_inside = True

        # 1) Hide the legend
        self._legend.set_visible(False)

        # 2) Force a full redraw and recapture the background
        self.fig.canvas.draw()
        self._background = self.copy_from_bbox(self.fig.bbox)

        # 3) If a point was already highlighted, re‐draw it on top
        if self.point_highlighted:
            self.highlight_current_point(override=self._last_highlight_override)


    def _on_axes_leave(self, event):
        """Show the legend again as soon as the mouse leaves the bottom axes."""
        if event.inaxes is not self.ax_bottom or self._legend is None:
            return
        
        self._mouse_inside = False

        # 1) Show the legend
        self._legend.set_visible(True)

        # 2) Force a full redraw and recapture the background
        self.fig.canvas.draw()
        self._background = self.copy_from_bbox(self.fig.bbox)

        # 3) If a point was highlighted, re‐draw it on top
        if self.point_highlighted:
            self.highlight_current_point(override=self._last_highlight_override)
