from ._shared import *

class NavigatorColorMixin:
    def set_color_by(self, column_name):
        for act in self._colorByActions:
            # look at the `data()`, not `text()`
            act.setChecked(act.data() == column_name)
        self.color_by_column = column_name

        # redraw the trajectories
        self.kymoCanvas.remove_circle()
        self.kymoCanvas.clear_kymo_trajectory_markers()
        self.kymoCanvas.draw_trajectories_on_kymo()
        self.movieCanvas.remove_gaussian_circle()
        self.movieCanvas.clear_movie_trajectory_markers()
        self.movieCanvas.draw_trajectories_on_movie()

        self.kymoCanvas.draw()
        self.movieCanvas.draw()

        # update the legends on both canvases
        self._update_legends()

        if self.intensityCanvas._last_plot_args:
            # find the current trajectory
            idx = self.trajectoryCanvas.table_widget.currentRow()
            if idx >= 0:
                traj = self.trajectoryCanvas.trajectories[idx]
                scatter_kwargs, _ = self._get_traj_colors(traj)

                # patch in the new kwargs
                args = self.intensityCanvas._last_plot_args
                args['colors'] = scatter_kwargs

                # re-draw
                self.intensityCanvas.plot_intensity(**args)

        if self.intensityCanvas.point_highlighted and self.intensityCanvas._last_plot_args is not None:
            self.jump_to_analysis_point(self.intensityCanvas.current_index)

    def refresh_color_by(self):
        """
        Re-apply the current color-by without touching menu check states.
        Use after mutating custom fields while a color-by is active.
        """
        # redraw the trajectories
        self.kymoCanvas.remove_circle()
        self.kymoCanvas.clear_kymo_trajectory_markers()
        self.kymoCanvas.draw_trajectories_on_kymo()
        self.movieCanvas.remove_gaussian_circle()
        self.movieCanvas.clear_movie_trajectory_markers()
        self.movieCanvas.draw_trajectories_on_movie()

        self.kymoCanvas.draw()
        self.movieCanvas.draw()

        # update legends
        self._update_legends()

        if self.intensityCanvas._last_plot_args:
            idx = self.trajectoryCanvas.table_widget.currentRow()
            if idx >= 0:
                traj = self.trajectoryCanvas.trajectories[idx]
                scatter_kwargs, _ = self._get_traj_colors(traj)
                args = dict(self.intensityCanvas._last_plot_args)
                args["colors"] = scatter_kwargs
                self.intensityCanvas.plot_intensity(**args)

        if self.intensityCanvas.point_highlighted and self.intensityCanvas._last_plot_args is not None:
            self.jump_to_analysis_point(self.intensityCanvas.current_index, animate="discrete", zoom=False)

    def _get_traj_colors(self, traj):
        """
        Decide how to color one trajectory.  
        Returns a dict of kwargs for scatter() and a single line_color.
        If no color_by is set, uses traj['colors'] as the scatter 'c' argument.
        """
        col = self.color_by_column
        all_trajs = self.trajectoryCanvas.trajectories

        seg_suffix = " (per segment)"
        if isinstance(col, str) and col.endswith(seg_suffix):
            base = col[:-len(seg_suffix)].strip()
            spec = self._range_spec_for_column(base)
            if spec is None:
                return {"color": "grey", "zorder": 4}, "grey"

            d_col = getattr(self, "_DIFF_D_COL", "D")
            a_col = getattr(self, "_DIFF_A_COL", "alpha")
            base_lk = base.lower().strip()
            value_key = "alpha" if (base == a_col or base_lk in ("alpha", "α")) else "D"

            seg_values = {}
            for entry in (traj.get("segment_diffusion") or []):
                if not isinstance(entry, dict):
                    continue
                seg_idx = entry.get("segment")
                if seg_idx is None:
                    continue
                try:
                    seg_values[int(seg_idx)] = entry.get(value_key)
                except Exception:
                    continue

            nodes = traj.get("nodes", []) or []
            anchor_frames = sorted({
                int(n[0]) for n in nodes
                if isinstance(n, (list, tuple)) and len(n) >= 1
            })
            if len(anchor_frames) < 2:
                anchors = traj.get("anchors", []) or []
                anchor_frames = sorted({
                    int(f) for f, _xk, _yk in anchors
                    if isinstance(f, (int, float))
                })
            if len(anchor_frames) < 2:
                return {"color": "grey", "zorder": 4}, "grey"

            def _segment_for_frame(frame):
                for idx in range(len(anchor_frames) - 1):
                    if frame <= anchor_frames[idx + 1]:
                        return idx + 1
                return len(anchor_frames) - 1

            frames = traj.get("frames", [])
            colors = []
            for f in frames:
                seg_idx = _segment_for_frame(f)
                v = seg_values.get(seg_idx)
                c, _label = self._color_for_binned_value(v, spec)
                colors.append(c)

            if not colors:
                return {"color": "grey", "zorder": 4}, "grey"
            return {"c": colors, "zorder": 4}, colors[0]

        # fallback: color points magenta if intensity exists, grey if None
        if not col:
            # use per-point intensities to decide color
            intensities = traj.get("intensities", [])
            if isinstance(intensities, (list, tuple)) and intensities:
                colors = ["magenta" if val is not None else "grey" for val in intensities]
            else:
                # fallback if no intensities list: use existing colors or uniform magenta
                existing = traj.get("colors", None)
                colors = existing if isinstance(existing, (list, tuple)) else ["magenta"]
            return {"c": colors, "zorder": 4}, "magenta"

        # determine mode & target channel
        movie = self.movie
        ch_ax = self._channel_axis
        n_chan = movie.shape[ch_ax] if movie.ndim == 4 and ch_ax is not None else 1

        if n_chan == 2 and col == "colocalization":
            mode, tgt = "coloc", None
        elif n_chan > 2 and col.startswith("coloc_ch"):
            mode, tgt = "coloc_multi", int(col.split("coloc_ch",1)[1])
        else:
            mode, tgt = self.trajectoryCanvas._column_types.get(col), None

        spec = self._range_spec_for_column(col)
        if spec is not None:
            raw = traj.get("custom_fields", {}).get(col)
            v = self._safe_float(raw)
            c, _label = self._color_for_binned_value(v, spec)
            # One color per trajectory (D/alpha are per-trajectory)
            return {"color": c, "zorder": 4}, c

        # binary / value modes need a global map for "value"
        if mode == "value":
            # collect unique vals
            seen = []
            for t in all_trajs:
                v = t.get("custom_fields", {}).get(col)
                if v and v not in seen:
                    seen.append(v)
            # build large color list
            def cmap_hex(name):
                cmap = cm.get_cmap(name)
                return [mcolors.to_hex(cmap(i)) for i in range(cmap.N)]
            palette = cmap_hex("Accent") + cmap_hex("tab10") + cmap_hex("tab20")
            color_map = {v: palette[i % len(palette)] for i,v in enumerate(seen)}
        else:
            color_map = {}

        # now pick main_color/point-colors
        if mode == "binary":
            flag = traj["custom_fields"].get(col, False)
            main = "#FFC107" if flag else "#0088A6"
            scatter_kwargs = {"color": main, "zorder": 4}
        elif mode == "value":
            val = traj["custom_fields"].get(col)
            c = color_map.get(val, "#7DA1FF")
            scatter_kwargs = {"color": c, "zorder": 4}
            main = c
        elif mode == "coloc":
            flags = traj.get("colocalization_any", [])
            pts = [
                "#FFC107" if f == "Yes" else
                "#339CBF" if f == "No"  else
                "grey"
                for f in flags
            ]
            scatter_kwargs = {"c": pts, "zorder": 4}
            main = "#339CBF"
        elif mode == "coloc_multi":
            by_ch = traj.get("colocalization_by_ch", {})
            flags = by_ch.get(tgt, [None]*len(traj["frames"]))
            pts = [
                "#FFC107" if f == "Yes" else
                "#339CBF" if f == "No"  else
                "grey"
                for f in flags
            ]
            scatter_kwargs = {"c": pts, "zorder": 4}
            main = "#339CBF"
        else:
            # no special mode → uniform magenta
            scatter_kwargs = {"color": "magenta", "zorder": 4}
            main = "magenta"

        return scatter_kwargs, main

    def _reposition_legend(self, margin=7, left_margin=10):
        """
        Place legend to the right of the overlay (with a bit of breathing room),
        or all the way to the left if the overlay is hidden.
        """
        if self._ch_overlay.isVisible():
            o = self._ch_overlay.geometry()
            legend_x = o.x() + o.width() + margin
        else:
            # no overlay → stick to a fixed left inset
            legend_x = left_margin

        legend_y = left_margin + 7
        self.movieLegendWidget.move(legend_x, legend_y)

    def _update_legends(self):

        if self.movie is None:
            return

        # clear both layouts
        for layout in (self.kymoLegendLayout,
                       self.movieLegendLayout):
            for i in reversed(range(layout.count())):
                w = layout.itemAt(i).widget()
                if w:
                    w.setParent(None)

        # detect channel count
        n_chan = (self.movie.shape[self._channel_axis]
                  if (self.movie.ndim == 4 and
                      self._channel_axis is not None)
                  else 1)

        col = self.color_by_column

        # --- NEW: build legend entries robustly (avoid UnboundLocalError) ---
        entries = []
        spec = self._range_spec_for_column(col)
        if spec is not None:
            # Range-binned legend (e.g. diffusion D / alpha)
            entries = [(color, label) for (_lo, _hi, color, label) in spec]
        else:
            # determine color mode/target
            if n_chan == 2 and col == "colocalization":
                mode, tgt = "coloc", None
            elif n_chan > 2 and col and col.startswith("coloc_ch"):
                mode, tgt = "coloc_multi", int(col.split("coloc_ch", 1)[1])
            else:
                mode, tgt = (self.trajectoryCanvas._column_types.get(col), None)

            # build a small list of (color, label) entries for this mode
            if mode == "coloc":
                entries = [("#FFC107", "Colocalized")]
            elif mode == "coloc_multi":
                entries = [("#FFC107", f"Ch. {tgt} coloc.")]
            elif mode == "value":
                # collect unique vals & map them
                seen = []
                for t in self.trajectoryCanvas.trajectories:
                    v = t.get("custom_fields", {}).get(col)
                    if v and v not in seen:
                        seen.append(v)

                # build palette
                def cmap_hex(name):
                    cmap = cm.get_cmap(name)
                    return [mcolors.to_hex(cmap(i)) for i in range(cmap.N)]

                palette = cmap_hex("Accent") + cmap_hex("tab10") + cmap_hex("tab20")
                color_map = {v: palette[i % len(palette)] for i, v in enumerate(seen)}
                entries = [(color_map[v], v) for v in seen]
            elif mode == "binary":
                entries = [("#FFC107", col)]
            else:
                entries = []

        # populate both legend widgets
        if entries:
            for (sw_color, label) in entries:
                for widget, layout in (
                    (self.kymoLegendWidget,  self.kymoLegendLayout),
                    (self.movieLegendWidget, self.movieLegendLayout)
                ):
                    sw = QLabel(widget)
                    sw.setFixedSize(12, 12)
                    sw.setStyleSheet(
                        f"background-color:{sw_color};"
                        "border:1px solid #333;"
                        "border-radius:2px"
                    )
                    lbl = QLabel(label, widget)
                    lbl.setStyleSheet(
                        "color:#222;font-size:14px;"
                        "background: transparent;"
                    )
                    layout.addWidget(sw, 0, Qt.AlignVCenter)
                    layout.addWidget(lbl, 0, Qt.AlignVCenter)

            # show & adjust both
            for widget in (self.kymoLegendWidget,
                           self.movieLegendWidget):
                widget.show()
                widget.adjustSize()
        else:
            self.movieLegendWidget.hide()
            self.kymoLegendWidget.hide()

        self._legend_entries = entries
        self._legend_expand_enabled = spec is not None

    def _ensure_legend_popup(self):
        if getattr(self, "_legend_popup", None) is not None:
            return

        popup = QFrame(None, Qt.ToolTip | Qt.FramelessWindowHint)
        popup.setAttribute(Qt.WA_ShowWithoutActivating)
        popup.setStyleSheet("background: white; border-radius: 8px;")
        popup.setMouseTracking(True)

        shadow = QGraphicsDropShadowEffect(popup)
        shadow.setBlurRadius(10)
        shadow.setColor(QColor(0, 0, 0, 120))
        shadow.setOffset(0, 0)
        popup.setGraphicsEffect(shadow)

        layout = QHBoxLayout(popup)
        layout.setSizeConstraint(QLayout.SetFixedSize)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(8)

        popup.installEventFilter(self)
        self._legend_popup = popup
        self._legend_popup_layout = layout
        self._legend_popup_owner = None

    def _populate_legend_popup(self, entries):
        self._ensure_legend_popup()
        layout = self._legend_popup_layout
        for i in reversed(range(layout.count())):
            w = layout.itemAt(i).widget()
            if w:
                w.setParent(None)

        for (sw_color, label) in entries:
            sw = QLabel(self._legend_popup)
            sw.setFixedSize(12, 12)
            sw.setStyleSheet(
                f"background-color:{sw_color};"
                "border:1px solid #333;"
                "border-radius:2px"
            )
            lbl = QLabel(label, self._legend_popup)
            lbl.setStyleSheet(
                "color:#222;font-size:14px;"
                "background: transparent;"
            )
            layout.addWidget(sw, 0, Qt.AlignVCenter)
            layout.addWidget(lbl, 0, Qt.AlignVCenter)

        self._legend_popup.adjustSize()

    def _show_legend_popup(self, owner_widget):
        if not getattr(self, "_legend_expand_enabled", False):
            return
        if owner_widget is None or not owner_widget.isVisible():
            return

        entries = getattr(self, "_legend_entries", None)
        if not entries:
            return

        self._populate_legend_popup(entries)
        self._legend_popup_owner = owner_widget
        self._cancel_hide_legend_popup()

        pos = owner_widget.mapToGlobal(QPoint(0, 0))
        x = pos.x()
        y = pos.y()

        screen = QApplication.primaryScreen()
        if screen:
            geo = screen.availableGeometry()
            max_x = geo.x() + geo.width() - self._legend_popup.width() - 8
            max_y = geo.y() + geo.height() - self._legend_popup.height() - 8
            x = min(x, max_x)
            y = min(y, max_y)
            x = max(x, geo.x() + 8)
            y = max(y, geo.y() + 8)

        self._legend_popup.move(x, y)
        self._legend_popup.show()

    def _schedule_show_legend_popup(self, owner_widget):
        if not getattr(self, "_legend_expand_enabled", False):
            return
        if owner_widget is None or not owner_widget.isVisible():
            return
        if getattr(self, "_legend_popup", None) is not None and self._legend_popup.isVisible():
            self._legend_popup_owner = owner_widget
            self._cancel_hide_legend_popup()
            return
        if getattr(self, "_legend_popup_show_timer", None) is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._show_legend_popup_from_timer)
            self._legend_popup_show_timer = timer
        self._legend_popup_owner = owner_widget
        self._legend_popup_show_timer.start(80)

    def _show_legend_popup_from_timer(self):
        owner = getattr(self, "_legend_popup_owner", None)
        if owner is None or not owner.isVisible():
            return
        self._show_legend_popup(owner)

    def _schedule_hide_legend_popup(self):
        if getattr(self, "_legend_popup", None) is None:
            return
        timer = getattr(self, "_legend_popup_show_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
        if getattr(self, "_legend_popup_hide_timer", None) is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._hide_legend_popup)
            self._legend_popup_hide_timer = timer
        self._legend_popup_hide_timer.start(150)

    def _cancel_hide_legend_popup(self):
        timer = getattr(self, "_legend_popup_hide_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()

    def _hide_legend_popup(self):
        popup = getattr(self, "_legend_popup", None)
        if popup is None:
            return
        popup.hide()

    def eventFilter(self, obj, ev):
        if obj is getattr(self, "_legend_popup", None):
            if ev.type() == QEvent.Enter:
                self._cancel_hide_legend_popup()
            elif ev.type() == QEvent.Leave:
                self._schedule_hide_legend_popup()
        return super().eventFilter(obj, ev)
            
    def _safe_float(self, x):
        """Parse float from strings like '0.123', return None if empty/invalid."""
        if x is None:
            return None
        if isinstance(x, (int, float)) and math.isfinite(x):
            return float(x)
        if isinstance(x, str):
            s = x.strip()
            if not s:
                return None
            try:
                v = float(s)
                return v if math.isfinite(v) else None
            except Exception:
                return None
        return None

    def _range_spec_for_column(self, col: str):
        """Return a list of (lo, hi, color, label) or None."""
        if not col:
            return None

        seg_suffix = " (per segment)"
        if isinstance(col, str) and col.endswith(seg_suffix):
            col = col[:-len(seg_suffix)].strip()

        d_col = getattr(self, "_DIFF_D_COL", "D")
        a_col = getattr(self, "_DIFF_A_COL", "alpha")

        lk = col.lower().strip()

        # Treat "α" and case variants too
        if col == a_col or lk in ("alpha", "α"):
            return getattr(self, "_ALPHA_RANGES", None)

        # For D: allow exact match and some common variants
        if col == d_col or lk in ("d", "diffusion", "diffusion d", "diffusion_d"):
            return getattr(self, "_D_RANGES", None)

        return None

    def _color_for_binned_value(self, v, spec):
        """
        spec: list of (lo, hi, color, label). lo inclusive, hi exclusive.
        Returns (color, label) or (grey, 'N/A') if missing/out-of-range.
        """
        if v is None or spec is None:
            return ("grey", "N/A")
        for lo, hi, color, label in spec:
            if (lo is None or v >= lo) and (hi is None or v < hi):
                return (color, label)
        return ("grey", "N/A")

    # ---- defaults ----
    # Alpha bins (dimensionless)
    _ALPHA_RANGES = [
        (None, 0.5,  "#D32F2F", "α < 0.5  (confined)"),
        (0.5,  0.9,  "#F57C00", "0.5–0.9  (subdiff.)"),
        (0.9,  1.1,  "#FBC02D", "0.9–1.1  (Brownian)"),
        (1.1,  1.5,  "#8BC34A", "1.1–1.5  (superdiff.)"),
        (1.5,  None, "#388E3C", "α ≥ 1.5  (directed)"),
    ]

    # D bins (units: μm²/s)
    # Generic order-of-magnitude bins; adjust if the D distribution differs.
    _D_RANGES = [
        (None, 0.01, "#D32F2F", "D < 0.01 μm²/s"),
        (0.01, 0.1,  "#F57C00", "0.01–0.1 μm²/s"),
        (0.1,  1.0,  "#FBC02D", "0.1–1 μm²/s"),
        (1.0,  None, "#388E3C", "D ≥ 1 μm²/s"),
    ]
