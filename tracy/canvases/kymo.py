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
        self._resize_finalize_timer = QTimer(self)
        self._resize_finalize_timer.setSingleShot(True)
        self._resize_finalize_timer.timeout.connect(self._finish_kymo_resize_draw)
        self._kymo_resize_hidden_marker = None
        self._kymo_resize_marker_visibility = None
        self.scale = 1.0  # Data units per pixel (uniform in x and y)
        self.padding = 1.25
        self.zoom_center = None  # in data coordinates
        self.manual_zoom = False
        self._update_pending = False
        self.manual_zoom = False

        # Base overlays are expensive and change much less often than the
        # selected highlight.  Keep their hit-test caches separate so a frame
        # or selection-only redraw never walks every trajectory again.
        self._kymo_base_label_bboxes: dict[Text, Bbox] = {}
        self._kymo_selected_label_bboxes: dict[Text, Bbox] = {}
        self._kymo_base_label_grid = {}
        self._kymo_selected_label_grid = {}
        self._kymo_base_label_bbox_signature = None
        self._kymo_selected_label_bbox_signature = None
        self._kymo_label_bboxes: dict[Text, Bbox] = {}
        self._kymo_base_labels_by_row = {}
        self._kymo_label_style_signature = None

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
        self._kymo_base_pick_index = self._empty_kymo_pick_index()
        self._kymo_selected_pick_index = self._empty_kymo_pick_index()
        self._kymo_pick_points = np.empty((0, 2), dtype=float)
        self._kymo_pick_rows = np.empty((0,), dtype=int)
        self._kymo_pick_indices = np.empty((0,), dtype=int)
        self._kymo_interaction_hidden_artists = {}
        self._kymo_base_cullable_collections = []
        self._kymo_base_cullable_scatters = []
        self._kymo_base_cull_generation = 0
        self._kymo_base_cull_signature = None
        self._kymo_base_overlay_signature = None
        self.scatter_objs_traj = []

        self._ctrl_panning = False
        self.setAcceptDrops(True)

    def draw(self, *args, **kwargs):
        # Keep full-resolution sources cached, but hand Agg only the artists
        # that can contribute pixels to the current view. This also covers
        # callers that change limits directly before draw_idle().
        if hasattr(self, "_kymo_base_cullable_collections"):
            self._update_kymo_base_artist_visibility()
        return super().draw(*args, **kwargs)

    def _movie_drop_delegate(self):
        nav = getattr(self, "navigator", None)
        movie_canvas = getattr(nav, "movieCanvas", None) if nav is not None else None
        if movie_canvas is None:
            return None
        if not hasattr(movie_canvas, "_dropped_load_file"):
            return None
        if not hasattr(movie_canvas, "_load_dropped_file"):
            return None
        return movie_canvas

    def _accepted_drop_path(self, event):
        delegate = self._movie_drop_delegate()
        if delegate is None:
            return None
        dropped_path, _kind, _err = delegate._dropped_load_file(event.mimeData())
        return dropped_path

    def dragEnterEvent(self, event):
        if self._accepted_drop_path(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event):
        if self._accepted_drop_path(event):
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event):
        delegate = self._movie_drop_delegate()
        if delegate is None:
            event.ignore()
            QMessageBox.warning(self.navigator or self, "Load failed", "Movie drop loader is unavailable.")
            return

        dropped_path, kind, err = delegate._dropped_load_file(event.mimeData())
        if not dropped_path:
            event.ignore()
            QMessageBox.warning(self.navigator or self, "Invalid file", err or "Invalid file drop.")
            return

        event.acceptProposedAction()
        delegate._load_dropped_file(dropped_path, kind, message_parent=self)

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
        changed = False
        for artist in self._iter_kymo_dense_artists():
            if artist in hidden:
                continue
            try:
                hidden[artist] = artist.get_visible()
                artist.set_visible(False)
                changed = True
            except Exception:
                pass
        if changed:
            self._invalidate_kymo_base_culling()

    def _hide_kymo_overlay_artists_for_interaction(self):
        if not self._slow_interaction_mode_enabled():
            return
        hidden = getattr(self, "_kymo_interaction_hidden_artists", None)
        if hidden is None:
            hidden = {}
            self._kymo_interaction_hidden_artists = hidden
        changed = False
        for artist in self._iter_kymo_overlay_artists():
            if artist in hidden:
                continue
            try:
                hidden[artist] = artist.get_visible()
                artist.set_visible(False)
                changed = True
            except Exception:
                pass
        if changed:
            self._invalidate_kymo_base_culling()

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
        self._invalidate_kymo_base_culling()

    @staticmethod
    def _mark_kymo_dense_artist(artist):
        try:
            artist._tracy_kymo_dense_artist = True
        except Exception:
            pass
        return artist

    @staticmethod
    def _kymo_segment_bounds(segments):
        bounds = np.full((len(segments), 4), np.nan, dtype=float)
        for index, segment in enumerate(segments):
            points = np.asarray(segment, dtype=float)
            if points.ndim != 2 or points.shape[1] < 2:
                continue
            finite = np.isfinite(points[:, 0]) & np.isfinite(points[:, 1])
            if not np.any(finite):
                continue
            x = points[finite, 0]
            y = points[finite, 1]
            bounds[index] = (np.min(x), np.max(x), np.min(y), np.max(y))
        return bounds

    def _invalidate_kymo_base_culling(self):
        self._kymo_base_cull_generation = (
            int(getattr(self, "_kymo_base_cull_generation", 0)) + 1
        )
        self._kymo_base_cull_signature = None

    def _register_kymo_base_collection(self, collection, segments, colors=None):
        source_segments = tuple(np.asarray(segment, dtype=float) for segment in segments)
        collection._tracy_kymo_source_segments = source_segments
        collection._tracy_kymo_segment_bounds = self._kymo_segment_bounds(
            source_segments
        )
        collection._tracy_kymo_source_colors = (
            tuple(colors) if colors is not None and len(colors) == len(source_segments)
            else None
        )
        collection._tracy_kymo_visible_indices = None
        collection._tracy_kymo_visible_index_array = None
        self._kymo_base_cullable_collections.append(collection)
        self._invalidate_kymo_base_culling()

    def _register_kymo_base_scatter(self, scatter, points):
        points = np.asarray(points, dtype=float)
        scatter._tracy_kymo_source_offsets = points
        facecolors = np.asarray(scatter.get_facecolors()).copy()
        edgecolors = np.asarray(scatter.get_edgecolors()).copy()
        scatter._tracy_kymo_source_facecolors = (
            facecolors if len(facecolors) == len(points) else None
        )
        scatter._tracy_kymo_source_edgecolors = (
            edgecolors if len(edgecolors) == len(points) else None
        )
        scatter._tracy_kymo_visible_indices = None
        scatter._tracy_kymo_visible_index_array = None
        self._kymo_base_cullable_scatters.append(scatter)
        self._invalidate_kymo_base_culling()

    def _kymo_culling_margin_pixels(self):
        radius_points = 0.0
        for collection in list(
            getattr(self, "_kymo_base_cullable_collections", []) or []
        ):
            try:
                widths = np.asarray(collection.get_linewidths(), dtype=float)
                finite = widths[np.isfinite(widths)]
                if finite.size:
                    radius_points = max(
                        radius_points, 0.5 * float(np.max(np.abs(finite)))
                    )
            except Exception:
                pass
        for scatter in list(
            getattr(self, "_kymo_base_cullable_scatters", []) or []
        ):
            try:
                sizes = np.asarray(scatter.get_sizes(), dtype=float)
                finite_sizes = sizes[np.isfinite(sizes) & (sizes >= 0)]
                marker_radius = (
                    0.5 * math.sqrt(float(np.max(finite_sizes)))
                    if finite_sizes.size else 0.0
                )
                widths = np.asarray(scatter.get_linewidths(), dtype=float)
                finite_widths = widths[np.isfinite(widths)]
                edge_radius = (
                    0.5 * float(np.max(np.abs(finite_widths)))
                    if finite_widths.size else 0.0
                )
                radius_points = max(radius_points, marker_radius + edge_radius)
            except Exception:
                pass
        try:
            radius_pixels = float(
                self.figure.canvas.get_renderer().points_to_pixels(radius_points)
            )
        except Exception:
            radius_pixels = radius_points * float(self.figure.dpi) / 72.0
        # Two extra renderer pixels cover antialiasing at the clipping edge.
        return max(4.0, radius_pixels + 2.0)

    def _kymo_view_bounds_with_margin(self):
        try:
            xlim = self.ax.get_xlim()
            ylim = self.ax.get_ylim()
            x_low, x_high = sorted((float(xlim[0]), float(xlim[1])))
            y_low, y_high = sorted((float(ylim[0]), float(ylim[1])))
            bbox = self.ax.bbox
            margin_pixels = self._kymo_culling_margin_pixels()
            x_margin = margin_pixels * (x_high - x_low) / max(float(bbox.width), 1.0)
            y_margin = margin_pixels * (y_high - y_low) / max(float(bbox.height), 1.0)
            return (
                x_low - x_margin,
                x_high + x_margin,
                y_low - y_margin,
                y_high + y_margin,
            )
        except Exception:
            return None

    def _update_kymo_base_artist_visibility(self):
        try:
            signature = (
                tuple(float(value) for value in self.ax.get_xlim()),
                tuple(float(value) for value in self.ax.get_ylim()),
                tuple(float(value) for value in self.ax.bbox.bounds),
                float(self.figure.dpi),
                int(getattr(self, "_kymo_base_cull_generation", 0)),
            )
        except Exception:
            signature = None
        if signature is not None and signature == self._kymo_base_cull_signature:
            return
        view = self._kymo_view_bounds_with_margin()
        if view is None:
            return
        x_low, x_high, y_low, y_high = view

        labels = [
            marker
            for marker in list(getattr(self, "kymo_trajectory_markers", []) or [])
            if getattr(marker, "_tracy_kymo_label", False)
        ]
        if labels:
            hidden = {
                entry[0]
                for entry in (getattr(self, "_hidden_kymo_base_labels", []) or [])
                if entry
            }
            hidden.update(
                (getattr(self, "_kymo_interaction_hidden_artists", {}) or {}).keys()
            )
            try:
                anchors = np.asarray([label.xy for label in labels], dtype=float)
                display = self.ax.transData.transform(anchors)
                in_axes = self.ax.patch.contains_points(display, radius=1.0)
                for label, is_visible in zip(labels, in_axes):
                    should_show = bool(is_visible) and label not in hidden
                    was_visible = bool(label.get_visible())
                    label.set_visible(should_show)
                    if (
                        should_show
                        and not was_visible
                        and label not in self._kymo_base_label_bboxes
                    ):
                        self._kymo_base_label_bbox_signature = None
            except Exception:
                # Annotation's native clipping remains the exact fallback.
                for label in labels:
                    if label not in hidden:
                        label.set_visible(True)

        for collection in list(
            getattr(self, "_kymo_base_cullable_collections", []) or []
        ):
            segments = getattr(collection, "_tracy_kymo_source_segments", ())
            bounds = getattr(collection, "_tracy_kymo_segment_bounds", None)
            if bounds is None or len(bounds) != len(segments):
                continue
            finite = np.isfinite(bounds).all(axis=1)
            intersects = (
                finite
                & (bounds[:, 1] >= x_low)
                & (bounds[:, 0] <= x_high)
                & (bounds[:, 3] >= y_low)
                & (bounds[:, 2] <= y_high)
            )
            index_array = np.flatnonzero(intersects)
            previous = getattr(
                collection, "_tracy_kymo_visible_index_array", None
            )
            if previous is not None and np.array_equal(index_array, previous):
                continue
            collection.set_segments([segments[int(i)] for i in index_array])
            colors = getattr(collection, "_tracy_kymo_source_colors", None)
            if colors is not None:
                collection.set_color([colors[int(i)] for i in index_array])
            collection._tracy_kymo_visible_index_array = index_array.copy()
            collection._tracy_kymo_visible_indices = tuple(
                int(i) for i in index_array
            )

        for scatter in list(
            getattr(self, "_kymo_base_cullable_scatters", []) or []
        ):
            points = getattr(scatter, "_tracy_kymo_source_offsets", None)
            if points is None or len(points) == 0:
                continue
            finite = np.isfinite(points).all(axis=1)
            visible = (
                finite
                & (points[:, 0] >= x_low)
                & (points[:, 0] <= x_high)
                & (points[:, 1] >= y_low)
                & (points[:, 1] <= y_high)
            )
            index_array = np.flatnonzero(visible)
            previous = getattr(scatter, "_tracy_kymo_visible_index_array", None)
            if previous is not None and np.array_equal(index_array, previous):
                continue
            scatter.set_offsets(points[index_array] if index_array.size else np.empty((0, 2)))
            facecolors = getattr(scatter, "_tracy_kymo_source_facecolors", None)
            if facecolors is not None:
                scatter.set_facecolors(facecolors[index_array])
            edgecolors = getattr(scatter, "_tracy_kymo_source_edgecolors", None)
            if edgecolors is not None:
                scatter.set_edgecolors(edgecolors[index_array])
            scatter._tracy_kymo_visible_index_array = index_array.copy()
            scatter._tracy_kymo_visible_indices = tuple(
                int(i) for i in index_array
            )
        self._kymo_base_cull_signature = signature

    def _schedule_deferred_view_draw(self, cache_background=False):
        self._deferred_cache_background = (
            self._deferred_cache_background or bool(cache_background)
        )
        if not self._view_redraw_timer.isActive():
            self._view_redraw_timer.start(8)

    def _perform_deferred_view_draw(self):
        cache_background = bool(self._deferred_cache_background)
        self._deferred_cache_background = False
        self._apply_kymo_label_zoom_scale()
        self.draw()
        if cache_background:
            self._bg = self.copy_from_bbox(self.ax.bbox)
            self._refresh_kymo_label_bboxes()

    def _refresh_kymo_label_bboxes(self, *, base=True, selected=True):
        signature = self._kymo_label_view_signature()
        if base:
            self._kymo_base_label_bboxes.clear()
            self._kymo_base_label_grid.clear()
            self._kymo_base_labels_by_row.clear()
            self._kymo_base_label_bbox_signature = None
            label_to_row = getattr(self.navigator, "_kymo_label_to_row", None)
            labels = []
            for marker in list(getattr(self, "kymo_trajectory_markers", None) or []):
                if not isinstance(marker, Text) or not getattr(
                    marker, "_tracy_kymo_label", False
                ):
                    continue
                if label_to_row is not None:
                    row = label_to_row.get(marker, -1)
                    if row is not None and row >= 0:
                        self._kymo_base_labels_by_row.setdefault(int(row), []).append(marker)
                try:
                    if marker.get_visible():
                        labels.append(marker)
                except Exception:
                    labels.append(marker)

            cache_valid = True
            if labels:
                try:
                    anchors = np.asarray([label.xy for label in labels], dtype=float)
                    offsets = np.asarray(
                        [label.get_position() for label in labels], dtype=float
                    )
                    centers = self.ax.transData.transform(anchors) + offsets
                    font_px = np.asarray(
                        [float(label.get_fontsize()) for label in labels], dtype=float
                    ) * (float(self.figure.dpi) / 72.0)
                    text_lengths = np.asarray(
                        [len(label.get_text()) for label in labels], dtype=float
                    )
                    # Conservative boxes are cheap to build and only prefilter
                    # candidates; Annotation.contains() performs the exact hit.
                    half_extents = np.maximum(
                        24.0, 0.62 * text_lengths * font_px + 12.0
                    )
                    for label, center, half_extent in zip(
                        labels, centers, half_extents
                    ):
                        bbox = Bbox.from_extents(
                            float(center[0] - half_extent),
                            float(center[1] - half_extent),
                            float(center[0] + half_extent),
                            float(center[1] + half_extent),
                        )
                        self._kymo_base_label_bboxes[label] = bbox
                        self._add_label_to_hit_grid(
                            self._kymo_base_label_grid, label, bbox
                        )
                except Exception:
                    self._kymo_base_label_bboxes.clear()
                    self._kymo_base_label_grid.clear()
                    cache_valid = False
            self._kymo_base_label_bbox_signature = signature if cache_valid else None

        if selected:
            self._kymo_selected_label_bboxes.clear()
            self._kymo_selected_label_grid.clear()
            self._kymo_selected_label_bbox_signature = None
            try:
                renderer = self.figure.canvas.get_renderer()
            except Exception:
                renderer = None
            cache_valid = renderer is not None
            for marker in list(
                getattr(self, "kymo_selected_trajectory_markers", None) or []
            ):
                if not isinstance(marker, Text) or not getattr(
                    marker, "_tracy_kymo_label", False
                ):
                    continue
                try:
                    if not marker.get_visible():
                        continue
                except Exception:
                    pass
                if renderer is None:
                    continue
                try:
                    bbox = marker.get_window_extent(renderer)
                    marker.update_bbox_position_size(renderer)
                    bbox = marker.get_window_extent(renderer)
                    patch = marker.get_bbox_patch()
                    if patch is not None:
                        try:
                            bbox = Bbox.union([
                                bbox, patch.get_window_extent(renderer)
                            ])
                        except Exception:
                            pass
                    bbox = bbox.expanded(1.5, 1.5)
                    self._kymo_selected_label_bboxes[marker] = bbox
                    self._add_label_to_hit_grid(
                        self._kymo_selected_label_grid, marker, bbox
                    )
                except Exception:
                    cache_valid = False
            self._kymo_selected_label_bbox_signature = (
                signature if cache_valid else None
            )

    @staticmethod
    def _label_hit_grid_cells(bbox, cell_size=32):
        try:
            x0 = int(math.floor(float(bbox.x0) / cell_size))
            x1 = int(math.floor(float(bbox.x1) / cell_size))
            y0 = int(math.floor(float(bbox.y0) / cell_size))
            y1 = int(math.floor(float(bbox.y1) / cell_size))
        except Exception:
            return ()
        return (
            (gx, gy)
            for gx in range(x0, x1 + 1)
            for gy in range(y0, y1 + 1)
        )

    @classmethod
    def _add_label_to_hit_grid(cls, grid, label, bbox):
        for cell in cls._label_hit_grid_cells(bbox):
            grid.setdefault(cell, []).append(label)

    @classmethod
    def _remove_label_from_hit_grid(cls, grid, label, bbox):
        for cell in cls._label_hit_grid_cells(bbox):
            labels = grid.get(cell)
            if not labels:
                continue
            try:
                labels.remove(label)
            except ValueError:
                continue
            if not labels:
                grid.pop(cell, None)

    def kymo_label_hit(self, event):
        """Return the topmost cached endpoint label under a mouse event."""
        if self._is_panning:
            return None
        try:
            cell = (int(math.floor(float(event.x) / 32)),
                    int(math.floor(float(event.y) / 32)))
        except Exception:
            return None
        signature = self._kymo_label_view_signature()
        stale_labels = []
        for bbox_map, grid, cached_signature, markers in (
            (
                self._kymo_selected_label_bboxes,
                self._kymo_selected_label_grid,
                self._kymo_selected_label_bbox_signature,
                list(getattr(self, "kymo_selected_trajectory_markers", None) or []),
            ),
            (
                self._kymo_base_label_bboxes,
                self._kymo_base_label_grid,
                self._kymo_base_label_bbox_signature,
                list(getattr(self, "kymo_trajectory_markers", None) or []),
            ),
        ):
            if cached_signature != signature:
                stale_labels.extend(
                    marker for marker in markers
                    if getattr(marker, "_tracy_kymo_label", False)
                )
                continue
            for label in grid.get(cell, ()):
                bbox = bbox_map.get(label)
                if bbox is None:
                    continue
                try:
                    if not label.get_visible() or not bbox.contains(event.x, event.y):
                        continue
                    # The expanded box and grid are only a cheap prefilter.
                    # Preserve Annotation's exact circular-patch hit behavior.
                    renderer = self.figure.canvas.get_renderer()
                    label.get_window_extent(renderer)
                    label.update_bbox_position_size(renderer)
                    hit, _details = label.contains(event)
                    if hit:
                        return label
                except Exception:
                    continue
        return self._kymo_label_hit_current_transform(stale_labels, event)

    def _kymo_label_hit_current_transform(self, labels, event):
        labels = [label for label in labels if label.get_visible()]
        if not labels:
            return None
        try:
            anchors = np.asarray([label.xy for label in labels], dtype=float)
            offsets = np.asarray(
                [label.get_position() for label in labels], dtype=float
            )
            centers = self.ax.transData.transform(anchors) + offsets
            font_px = np.asarray(
                [float(label.get_fontsize()) for label in labels], dtype=float
            ) * (float(self.figure.dpi) / 72.0)
            text_lengths = np.asarray(
                [len(label.get_text()) for label in labels], dtype=float
            )
            # circle boxstyle expands both axes with long text; use a generous
            # square prefilter and let Annotation.contains reject false hits.
            half_extents = np.maximum(
                24.0, 0.62 * text_lengths * font_px + 12.0
            )
            nearby = np.nonzero(
                (np.abs(centers[:, 0] - float(event.x)) <= half_extents)
                & (np.abs(centers[:, 1] - float(event.y)) <= half_extents)
            )[0]
        except Exception:
            nearby = range(len(labels))
        for label_idx in nearby:
            label = labels[int(label_idx)]
            try:
                renderer = self.figure.canvas.get_renderer()
                label.get_window_extent(renderer)
                label.update_bbox_position_size(renderer)
                label.get_window_extent(renderer)
                hit, _details = label.contains(event)
                if hit:
                    return label
            except Exception:
                continue
        return None

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
        if self._resize_finalize_timer.isActive():
            self._resize_finalize_timer.stop()
        self.ax.cla()
        self._im = None
        self.image = None
        self.manual_zoom = False
        self._marker = None
        self._is_panning = False
        self._pan_start = None
        self._orig_xlim = None
        self._orig_ylim = None
        self._last_pan = 0.0
        self._finish_kymo_interaction(redraw=False)
        self.kymo_trajectory_markers = []
        self.kymo_selected_trajectory_markers = []
        self._kymo_base_scatter_artists = []
        self._kymo_selected_scatter_artists = []
        self._kymo_base_pick_entries = []
        self._kymo_selected_pick_entries = []
        self._kymo_base_pick_index = self._empty_kymo_pick_index()
        self._kymo_selected_pick_index = self._empty_kymo_pick_index()
        self._kymo_pick_points = np.empty((0, 2), dtype=float)
        self._kymo_pick_rows = np.empty((0,), dtype=int)
        self._kymo_pick_indices = np.empty((0,), dtype=int)
        self._kymo_base_label_bboxes.clear()
        self._kymo_selected_label_bboxes.clear()
        self._kymo_base_label_grid.clear()
        self._kymo_selected_label_grid.clear()
        self._kymo_base_labels_by_row.clear()
        self._kymo_label_bboxes.clear()
        self._hidden_kymo_base_labels = []
        self._kymo_base_cullable_collections = []
        self._kymo_base_cullable_scatters = []
        self._kymo_base_label_bbox_signature = None
        self._kymo_selected_label_bbox_signature = None
        self._kymo_label_style_signature = None
        self._kymo_resize_hidden_marker = None
        self._kymo_resize_marker_visibility = None
        self.scatter_objs_traj = []
        if self.navigator is not None:
            label_map = getattr(self.navigator, "_kymo_label_to_row", None)
            if label_map is not None:
                label_map.clear()
            if hasattr(self.navigator, "trajectory_markers"):
                self.navigator.trajectory_markers = []
        self._invalidate_kymo_base_culling()
        self.invalidate_blit_background()
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

    def _set_kymo_view_limits(self):
        if getattr(self, "image", None) is None or getattr(self, "zoom_center", None) is None:
            return False
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
        return True

    def _finish_kymo_resize_draw(self):
        if getattr(self, "_draw_pending", False):
            self._resize_finalize_timer.start(0)
            return
        if self.image is None or self.width() <= 1 or self.height() <= 1:
            return
        self._bg = self.copy_from_bbox(self.ax.bbox)
        self._refresh_kymo_label_bboxes()

        marker = self._kymo_resize_hidden_marker
        marker_visible = self._kymo_resize_marker_visibility
        self._kymo_resize_hidden_marker = None
        self._kymo_resize_marker_visibility = None
        if marker is not None:
            try:
                marker.set_visible(bool(marker_visible))
                if marker_visible:
                    self.ax.draw_artist(marker)
                    self.fig.canvas.blit(self.ax.bbox)
            except Exception:
                pass

    def update_view(self, cache_background=True, defer=False, blit=False):
        if not self._set_kymo_view_limits():
            return

        self._apply_kymo_label_zoom_scale()
        if defer or blit:
            if not cache_background:
                self.invalidate_blit_background()
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

    def _sync_zoom_state_from_axes(self, widget_width=None):
        if self.image is None:
            return
        try:
            x0, x1 = self.ax.get_xlim()
            y0, y1 = self.ax.get_ylim()
            self.zoom_center = ((float(x0) + float(x1)) * 0.5,
                                (float(y0) + float(y1)) * 0.5)
            if widget_width is None or int(widget_width) <= 1:
                widget_width = self.width()
            widget_w = max(int(widget_width), 1)
            span_x = abs(float(x1) - float(x0))
            if span_x > 0:
                self.scale = span_x / widget_w
        except Exception:
            pass

    def _invalidate_resize_backgrounds(self):
        self.invalidate_blit_background()
        nav = getattr(self, "navigator", None)
        if nav is None:
            return
        for attr in ("_kymo_bg", "_kymo_sequence_bg_view",
                     "_kymo_anchor_bg", "_kymo_anchor_bg_view"):
            try:
                setattr(nav, attr, None)
            except Exception:
                pass

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
            # Left-click handling lives in NavigatorKymoMixin.on_kymo_click.
            # Calling it here as well duplicates anchor placement and redraw work.
            return

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
                # Pan motion changes only the view transform. Coalesce rapid
                # pointer events and defer expensive background/hit-cache work
                # until the one final draw on release.
                self.update_view(cache_background=False, defer=True)
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
            # A normal click should not redraw the full kymograph. The navigator
            # handles any click-specific marker updates with blitting.
            return

    def resizeEvent(self, event):
        slow_mode = self._slow_interaction_mode_enabled()
        if self.image is not None and self.manual_zoom:
            old_width = None
            try:
                old_width = event.oldSize().width()
            except Exception:
                old_width = None
            self._sync_zoom_state_from_axes(widget_width=old_width)
        self._invalidate_resize_backgrounds()
        if slow_mode:
            self._hide_kymo_overlay_artists_for_interaction()
        else:
            marker = getattr(self, "_marker", None)
            if marker is not None and self._kymo_resize_hidden_marker is None:
                try:
                    self._kymo_resize_hidden_marker = marker
                    self._kymo_resize_marker_visibility = marker.get_visible()
                    marker.set_visible(False)
                except Exception:
                    self._kymo_resize_hidden_marker = None
                    self._kymo_resize_marker_visibility = None
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
        self._set_kymo_view_limits()
        self._apply_kymo_label_zoom_scale()
        if slow_mode:
            # FigureCanvasQT already queued the lightweight draw with dense
            # overlays hidden. Restore everything once resize events settle.
            self._schedule_kymo_interaction_finish(delay_ms=140)
        else:
            # Capture the one native resize draw after it completes; do not
            # issue a second full Matplotlib render here.
            self._resize_finalize_timer.start(0)

    def add_circle(self, x, y, size=12, color='grey'):
        """
        Draw a hollow circle marker at (x, y) using blitting for performance.
        """
        old_marker = getattr(self, "_marker", None)

        # Prepare blitting background
        if not hasattr(self, "_bg") or self._bg is None:
            if old_marker is not None:
                try:
                    old_marker.set_visible(False)
                except Exception:
                    pass
            self.draw()
            self._bg = self.copy_from_bbox(self.ax.bbox)

        self.fig.canvas.restore_region(self._bg)

        if old_marker is not None:
            try:
                old_marker.set_data([x], [y])
                old_marker.set_markersize(size)
                old_marker.set_markeredgecolor(color)
                old_marker.set_visible(True)
            except Exception:
                old_marker = None

        if old_marker is None:
            # Create the marker once; subsequent drag events only move it.
            old_marker, = self.ax.plot(
                [x], [y],
                linestyle='none',
                marker='o',
                markersize=size,
                markeredgecolor=color,
                markerfacecolor='none',
                markeredgewidth=2,
                zorder=6
            )
            self._marker = old_marker

        # Draw just the marker and blit
        self.ax.draw_artist(old_marker)
        self.fig.canvas.blit(self.ax.bbox)

    def temporary_circle(self, x, y, size=12, color='blue', draw=True, animated=False):
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
        if animated:
            try:
                marker.set_animated(True)
            except Exception:
                pass
        if draw:
            self.draw()
        return [marker]

    @staticmethod
    def _empty_kymo_pick_index():
        return (
            np.empty((0, 2), dtype=float),
            np.empty((0,), dtype=int),
            np.empty((0,), dtype=int),
        )

    @classmethod
    def _build_kymo_pick_index(cls, entries):
        if not entries:
            return cls._empty_kymo_pick_index()
        points = np.concatenate([entry[0] for entry in entries])
        rows = np.concatenate([entry[1] for entry in entries])
        point_indices = np.concatenate([entry[2] for entry in entries])
        if len(points) > 1:
            order = np.argsort(points[:, 0], kind="stable")
            points = points[order]
            rows = rows[order]
            point_indices = point_indices[order]
        return points, rows, point_indices

    def _refresh_kymo_clickable_artists(
        self, *, rebuild_base=True, rebuild_selected=True
    ):
        self.scatter_objs_traj = (
            list(getattr(self, "_kymo_selected_scatter_artists", []))
            + list(getattr(self, "_kymo_base_scatter_artists", []))
        )
        if rebuild_base:
            self._kymo_base_pick_index = self._build_kymo_pick_index(
                getattr(self, "_kymo_base_pick_entries", [])
            )
        if rebuild_selected:
            self._kymo_selected_pick_index = self._build_kymo_pick_index(
                getattr(self, "_kymo_selected_pick_entries", [])
            )

        # Retain the old attributes for callers that inspect them, but avoid
        # concatenating the entire base index during selected-only redraws.
        index = (
            self._kymo_base_pick_index
            if len(self._kymo_base_pick_index[0])
            else self._kymo_selected_pick_index
        )
        self._kymo_pick_points, self._kymo_pick_rows, self._kymo_pick_indices = index

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

    def _pick_kymo_index(self, index, event, max_px):
        points, rows, point_indices = index
        if points is None or len(points) == 0:
            return None
        if event.inaxes is not self.ax:
            return None
        try:
            xdata = float(event.xdata)
            ydata = float(event.ydata)
            x0, x1 = self.ax.get_xlim()
            y0, y1 = self.ax.get_ylim()
            bbox = self.ax.bbox
            px_per_x = float(bbox.width) / max(abs(float(x1) - float(x0)), 1e-9)
            px_per_y = float(bbox.height) / max(abs(float(y1) - float(y0)), 1e-9)
            max_dx = float(max_px) / max(px_per_x, 1e-9)
            max_dy = float(max_px) / max(px_per_y, 1e-9)
            left = int(np.searchsorted(points[:, 0], xdata - max_dx, side="left"))
            right = int(np.searchsorted(points[:, 0], xdata + max_dx, side="right"))
            if left >= right:
                return None
            candidate_indices = np.arange(left, right, dtype=int)
            candidate_points = points[candidate_indices]
            near_y = np.abs(candidate_points[:, 1] - ydata) <= max_dy
            if not np.any(near_y):
                return None
            candidate_indices = candidate_indices[near_y]
            candidate_points = candidate_points[near_y]
            dx = (candidate_points[:, 0] - xdata) * px_per_x
            dy = (candidate_points[:, 1] - ydata) * px_per_y
            dist2 = dx * dx + dy * dy
        except Exception:
            return None
        limit2 = float(max_px) * float(max_px)
        nearest_local = int(np.argmin(dist2))
        if dist2[nearest_local] > limit2:
            return None
        nearest = int(candidate_indices[nearest_local])
        return (
            int(rows[nearest]),
            int(point_indices[nearest]),
            float(dist2[nearest_local]),
        )

    def pick_kymo_trajectory_point(self, event, max_px=8):
        selected_hit = self._pick_kymo_index(
            self._kymo_selected_pick_index, event, max_px
        )
        base_hit = self._pick_kymo_index(
            self._kymo_base_pick_index, event, max_px
        )
        if selected_hit is None:
            best = base_hit
        elif base_hit is None or selected_hit[2] <= base_hit[2]:
            best = selected_hit
        else:
            best = base_hit
        if best is None:
            return None
        return best[0], best[1]

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

    def _should_show_kymo_anchors(self):
        if self.navigator is None:
            return False
        if getattr(self.navigator, "kymo_anchor_edit_mode", False):
            return True
        anchor_btn = getattr(self.navigator, "kymo_anchor_overlay_button", None)
        return anchor_btn is None or anchor_btn.isChecked()

    def _kymo_overlay_modes(self):
        navigator = getattr(self, "navigator", None)
        if navigator is None:
            return "off", "off"
        traj_getter = getattr(navigator, "get_kymo_traj_overlay_mode", None)
        spot_getter = getattr(navigator, "get_kymo_spot_overlay_mode", None)
        traj_mode = traj_getter() if callable(traj_getter) else "all"
        spot_mode = spot_getter() if callable(spot_getter) else "all"
        if traj_mode not in ("off", "selected", "all"):
            traj_mode = "all"
        if spot_mode not in ("off", "selected", "all"):
            spot_mode = "all"
        return traj_mode, spot_mode

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

        # Edit handles must remain visible even when the regular anchor overlay
        # button is off; otherwise Shift-edit mode has nothing to grab.
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

    def _kymo_label_style_for_zoom(self, highlighted=False):
        base_size = 7.5 if highlighted else 6.5
        try:
            if self.image is None:
                raise ValueError
            h, w = self.image.shape[:2]
            widget_w = max(int(self.width()), 1)
            widget_h = max(int(self.height()), 1)
            fit_scale = max(float(w) / widget_w, float(h) / widget_h)
            current_scale = max(float(getattr(self, "scale", fit_scale)), 1e-9)
            zoom_factor = max(fit_scale / current_scale, 0.02)
            font_size = base_size * (zoom_factor ** 0.85)
        except Exception:
            font_size = base_size
        max_size = 10.0 if highlighted else 9.0
        font_size = float(np.clip(font_size, 2.75, max_size))
        offset = float(np.clip(15.0 * (font_size / 8.0), 5.0, 18.0))
        linewidth = float(np.clip(1.5 * (font_size / 8.0), 0.45, 1.8))
        return font_size, offset, linewidth

    def _iter_kymo_label_artists(self):
        markers = (
            list(getattr(self, "kymo_trajectory_markers", None) or [])
            + list(getattr(self, "kymo_selected_trajectory_markers", None) or [])
        )
        for marker in markers:
            if getattr(marker, "_tracy_kymo_label", False):
                yield marker

    def _restore_hidden_kymo_base_labels(self):
        hidden = getattr(self, "_hidden_kymo_base_labels", None)
        if not hidden:
            self._hidden_kymo_base_labels = []
            return
        current_signature = self._kymo_label_view_signature()
        for entry in hidden:
            label, visible, bbox = entry[:3]
            saved_signature = entry[3] if len(entry) > 3 else None
            try:
                label.set_visible(visible)
            except Exception:
                pass
            if visible and bbox is not None and saved_signature == current_signature:
                self._kymo_base_label_bboxes[label] = bbox
                self._add_label_to_hit_grid(self._kymo_base_label_grid, label, bbox)
            elif visible and saved_signature != current_signature:
                # Force the exact current-transform fallback until the next
                # finalized draw refreshes the grid.
                self._kymo_base_label_bbox_signature = None
        self._hidden_kymo_base_labels = []
        self._invalidate_kymo_base_culling()

    def _hide_kymo_base_labels_for_row(self, row):
        self._restore_hidden_kymo_base_labels()
        if row is None or row < 0:
            return
        hidden = []
        labels = self._kymo_base_labels_by_row.get(int(row), ())
        for label in labels:
            try:
                visible = label.get_visible()
                label.set_visible(False)
                bbox = self._kymo_base_label_bboxes.pop(label, None)
                if bbox is not None:
                    self._remove_label_from_hit_grid(
                        self._kymo_base_label_grid, label, bbox
                    )
                hidden.append((
                    label,
                    visible,
                    bbox,
                    self._kymo_base_label_bbox_signature,
                ))
            except Exception:
                pass
        self._hidden_kymo_base_labels = hidden
        if hidden:
            self._invalidate_kymo_base_culling()

    def _apply_kymo_label_zoom_scale(self):
        try:
            image_shape = tuple(int(v) for v in self.image.shape[:2])
        except Exception:
            image_shape = None
        try:
            xlim = self.ax.get_xlim()
            ylim = self.ax.get_ylim()
            direction = (xlim[1] >= xlim[0], ylim[1] >= ylim[0])
        except Exception:
            direction = None
        signature = (
            image_shape,
            round(float(getattr(self, "scale", 1.0)), 12),
            int(self.width()),
            int(self.height()),
            round(float(self.figure.dpi), 6),
            float(getattr(self.ax, "_outer_pad", 0) or 0),
            direction,
        )
        if signature == self._kymo_label_style_signature:
            return

        styles = {
            False: self._kymo_label_style_for_zoom(False),
            True: self._kymo_label_style_for_zoom(True),
        }
        for label in self._iter_kymo_label_artists():
            highlighted = bool(getattr(label, "_tracy_kymo_label_highlighted", False))
            font_size, offset, linewidth = styles[highlighted]
            try:
                label.set_fontsize(font_size)
            except Exception:
                pass
            unit = None
            segment = getattr(label, "_tracy_kymo_label_segment", None)
            sign = getattr(label, "_tracy_kymo_label_sign", None)
            if segment is not None and sign is not None:
                try:
                    x0, y0, x1, y1 = segment
                    dispA = self.ax.transData.transform((x0, y0))
                    dispB = self.ax.transData.transform((x1, y1))
                    v = dispB - dispA
                    norm = np.hypot(*v)
                    u = v / norm if norm else np.array([1.0, 0.0])
                    unit = (float(u[0]) * float(sign), float(u[1]) * float(sign))
                except Exception:
                    unit = None
            if unit is None:
                unit = getattr(label, "_tracy_kymo_label_offset_unit", None)
            if unit is not None:
                try:
                    label.set_position((float(unit[0]) * offset, float(unit[1]) * offset))
                except Exception:
                    pass
            try:
                patch = label.get_bbox_patch()
                if patch is not None:
                    patch.set_linewidth(linewidth)
            except Exception:
                pass
        self._kymo_label_style_signature = signature

    def _kymo_label_view_signature(self):
        try:
            return (
                tuple(float(value) for value in self.ax.get_xlim()),
                tuple(float(value) for value in self.ax.get_ylim()),
                tuple(float(value) for value in self.ax.bbox.bounds),
                float(self.figure.dpi),
            )
        except Exception:
            return None

    def _add_kymo_endpoint_labels(self, idx, x0, y0, x1, y1, highlighted, markers):
        traj = self.navigator.trajectoryCanvas.trajectories[idx]
        traj_label = traj.get("file_index", str(traj["trajectory_number"]))
        face = "#7da1ff" if highlighted else "#cbd9ff"
        textcolor = "white" if highlighted else "black"
        alpha_lbl = 0.8 if highlighted else 0.6
        font_size, offset, linewidth = self._kymo_label_style_for_zoom(highlighted)

        dispA = self.ax.transData.transform((x0, y0))
        dispB = self.ax.transData.transform((x1, y1))
        v = dispB - dispA
        norm = np.hypot(*v)
        u = v / norm if norm else np.array([1.0, 0.0])
        for (cx, cy, suf), sign in [((x0, y0, 'A'), -1), ((x1, y1, 'B'), +1)]:
            dx, dy = u * (offset * sign)
            lbl = self.ax.annotate(
                f"{traj_label}{suf}",
                xy=(cx, cy),
                xytext=(dx, dy),
                textcoords='offset pixels',
                ha='center', va='center',
                color=textcolor, fontsize=font_size, fontweight='bold',
                bbox=dict(
                    boxstyle='circle,pad=0.3',
                    facecolor=face,
                    edgecolor='black',
                    linewidth=linewidth,
                    alpha=alpha_lbl
                ),
                zorder=(8 if highlighted else 4)
            )
            lbl._tracy_kymo_label = True
            lbl._tracy_kymo_label_highlighted = bool(highlighted)
            lbl._tracy_kymo_label_offset_unit = (float(u[0]) * sign, float(u[1]) * sign)
            lbl._tracy_kymo_label_segment = (float(x0), float(y0), float(x1), float(y1))
            lbl._tracy_kymo_label_sign = float(sign)
            self.navigator._kymo_label_to_row[lbl] = idx
            markers.append(lbl)
            if not highlighted:
                self._kymo_base_labels_by_row.setdefault(int(idx), []).append(lbl)

    @staticmethod
    def _kymo_anchor_endpoint_labels(x0, y0, x1, y1, anchors_match_current, anchors):
        if not anchors_match_current or not anchors or len(anchors) < 2:
            return x0, y0, x1, y1
        try:
            first = anchors[0]
            last = anchors[-1]
            return (
                float(first[1]), float(first[2]),
                float(last[1]), float(last[2])
            )
        except Exception:
            return x0, y0, x1, y1

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
        show_spots=True,
        skinny=False,
        show_labels=True,
        show_anchors=True,
        trajectory_indices=None,
    ):
        if not self._kymo_can_batch_uniform_base_trajectories():
            return False
        if getattr(self.navigator, "connect_all_spots", False):
            return False

        scattersize = 3 if skinny else 9
        linesize = 0.5 if skinny else 1.5
        hide_spots = not bool(show_spots)
        compute_many = self.navigator._compute_kymo_x_many
        search_segments = []
        search_colors = []
        spot_segments = []
        spot_segment_colors = []
        scatter_points = []
        scatter_colors = []

        trajectories = self.navigator.trajectoryCanvas.trajectories
        if trajectory_indices is None:
            trajectory_indices = range(len(trajectories))
        for idx in trajectory_indices:
            if not (0 <= int(idx) < len(trajectories)):
                continue
            idx = int(idx)
            traj = trajectories[idx]
            ch = traj.get("channel")
            if ch is not None and ch != current_kymo_ch:
                continue

            sf, sx, sy = traj["start"]
            ef, ex, ey = traj["end"]
            if not (is_point_near_roi((sx, sy), roi) and
                    is_point_near_roi((ex, ey), roi)):
                continue

            endpoint_x = compute_many(
                roi_cache, [sx, ex], [sy, ey], kymo_w
            )
            x0, x1 = float(endpoint_x[0]), float(endpoint_x[1])
            y0 = frame_to_y(sf)
            y1 = frame_to_y(ef)
            if not (np.isfinite(x0) and np.isfinite(x1)):
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
                relevant_colors = point_colors[:min(len(frames), len(traj.get("spot_centers", [])))]
                if relevant_colors and all(
                    color == relevant_colors[0] for color in relevant_colors[1:]
                ):
                    uniform_color = relevant_colors[0]
                    point_colors = None
            search_line_color = self._kymo_group_color(
                scatter_kwargs.get("color"),
                fallback="#7da1ff",
            )

            spots = traj.get("spot_centers", [None] * len(frames))
            point_count = min(len(frames), len(spots))
            xs_pts = np.empty((0,), dtype=float)
            ys_pts = np.empty((0,), dtype=float)
            if not hide_spots and point_count > 0:
                movie_points = np.full((point_count, 2), np.nan, dtype=float)
                for point_idx, spot in enumerate(spots[:point_count]):
                    if isinstance(spot, (tuple, list, np.ndarray)) and len(spot) >= 2:
                        try:
                            movie_points[point_idx] = (float(spot[0]), float(spot[1]))
                        except (TypeError, ValueError):
                            pass
                xs_pts = compute_many(
                    roi_cache, movie_points[:, 0], movie_points[:, 1], kymo_w
                )
                ys_pts = np.asarray(
                    [frame_to_y(frame) for frame in frames[:point_count]], dtype=float
                )

            if showsearchline:
                if anchors_match_current:
                    xs_disp = [xk for _f, xk, _yk in anchors]
                    ys_disp = [yk for _f, _xk, yk in anchors]
                else:
                    orig_frames = []
                    orig_points = []
                    for f, point in zip(frames, orig):
                        if not isinstance(point, (tuple, list, np.ndarray)) or len(point) < 2:
                            continue
                        try:
                            orig_points.append((float(point[0]), float(point[1])))
                            orig_frames.append(f)
                        except (TypeError, ValueError):
                            continue
                    if orig_points:
                        orig_points = np.asarray(orig_points, dtype=float)
                        projected = compute_many(
                            roi_cache,
                            orig_points[:, 0],
                            orig_points[:, 1],
                            kymo_w,
                        )
                        valid_orig = np.isfinite(projected)
                        xs_disp = projected[valid_orig]
                        ys_all = np.asarray(
                            [frame_to_y(frame) for frame in orig_frames], dtype=float
                        )
                        ys_disp = ys_all[valid_orig]
                    else:
                        xs_disp, ys_disp = [], []
                if len(xs_disp) >= 2 and len(ys_disp) >= 2:
                    segment = np.column_stack([xs_disp, ys_disp])
                    search_segments.append(segment)
                    search_colors.append(search_line_color)

            if not hide_spots:
                valid = np.isfinite(xs_pts) & np.isfinite(ys_pts)
                points = np.column_stack([xs_pts, ys_pts])
                if point_colors is None:
                    padded = np.concatenate(([False], valid, [False]))
                    changes = np.diff(padded.astype(np.int8))
                    run_starts = np.nonzero(changes == 1)[0]
                    run_ends = np.nonzero(changes == -1)[0]
                    for run_start, run_end in zip(run_starts, run_ends):
                        if run_end - run_start < 2:
                            continue
                        spot_segments.append(points[run_start:run_end])
                        spot_segment_colors.append(uniform_color)
                else:
                    segment_indices = np.nonzero(valid[:-1] & valid[1:])[0]
                    if len(segment_indices):
                        spot_segments.extend(np.stack([
                            points[segment_indices], points[segment_indices + 1]
                        ], axis=1))
                        spot_segment_colors.extend(
                            [point_colors[i] for i in segment_indices]
                        )

                if np.any(valid):
                    valid_indices = np.nonzero(valid)[0]
                    scatter_points.extend(
                        np.column_stack([xs_pts[valid], ys_pts[valid]])
                    )
                    if point_colors is None:
                        scatter_colors.extend([uniform_color] * len(valid_indices))
                    else:
                        scatter_colors.extend(
                            [point_colors[point_idx] for point_idx in valid_indices]
                        )

                pick_entry = self._make_kymo_pick_entry(idx, xs_pts, ys_pts)
                if pick_entry is not None:
                    pick_entries.append(pick_entry)

            if show_labels:
                lx0, ly0, lx1, ly1 = self._kymo_anchor_endpoint_labels(
                    x0, y0, x1, y1,
                    anchors_match_current,
                    anchors,
                )
                self._add_kymo_endpoint_labels(idx, lx0, ly0, lx1, ly1, False, markers)

        if search_segments:
            search_color_arg = search_colors
            if all(color == search_colors[0] for color in search_colors[1:]):
                search_color_arg = search_colors[0]
            collection = LineCollection(
                search_segments,
                colors=search_color_arg,
                linewidths=2,
                linestyles="--",
                alpha=0.8,
                zorder=2
            )
            self.ax.add_collection(collection)
            self._register_kymo_base_collection(
                collection,
                search_segments,
                search_colors if isinstance(search_color_arg, list) else None,
            )
            markers.append(collection)

        if spot_segments:
            spot_color_arg = spot_segment_colors
            if all(
                color == spot_segment_colors[0]
                for color in spot_segment_colors[1:]
            ):
                spot_color_arg = spot_segment_colors[0]
            collection = LineCollection(
                spot_segments,
                colors=spot_color_arg,
                linewidths=linesize,
                alpha=0.8,
                zorder=3
            )
            self.ax.add_collection(collection)
            self._mark_kymo_dense_artist(collection)
            self._register_kymo_base_collection(
                collection,
                spot_segments,
                spot_segment_colors if isinstance(spot_color_arg, list) else None,
            )
            markers.append(collection)

        if scatter_points:
            points = np.asarray(scatter_points, dtype=float)
            scatter_kwargs = {"c": scatter_colors}
            if all(color == scatter_colors[0] for color in scatter_colors[1:]):
                scatter_kwargs = {"color": scatter_colors[0]}
            scatter = self.ax.scatter(
                points[:, 0], points[:, 1],
                s=scattersize, zorder=4, **scatter_kwargs
            )
            self._mark_kymo_dense_artist(scatter)
            self._register_kymo_base_scatter(scatter, points)
            markers.append(scatter)
            scatter_artists.append(scatter)

        return True

    def _kymo_base_overlay_cache_signature(self, trajectory_count):
        nav = getattr(self, "navigator", None)
        if nav is None:
            return None

        def _combo_text(name):
            combo = getattr(nav, name, None)
            getter = getattr(combo, "currentText", None)
            try:
                return str(getter()) if callable(getter) else ""
            except Exception:
                return ""

        traj_mode, spot_mode = self._kymo_overlay_modes()
        return (
            int(trajectory_count),
            _combo_text("kymoCombo"),
            _combo_text("roiCombo"),
            traj_mode,
            spot_mode,
            bool(getattr(nav, "connect_all_spots", False)),
            bool(getattr(nav, "kymo_anchor_edit_mode", False)),
            id(getattr(self, "image", None)),
        )

    def append_trajectory_to_kymo_base(self, trajectory_index):
        """Append one new base overlay using the exact full-draw renderer."""
        nav = getattr(self, "navigator", None)
        if nav is None:
            return False
        trajectories = nav.trajectoryCanvas.trajectories
        idx = int(trajectory_index)
        if not (0 <= idx < len(trajectories)):
            return False

        traj_mode, spot_mode = self._kymo_overlay_modes()
        anchor_edit_mode = bool(getattr(nav, "kymo_anchor_edit_mode", False))
        if anchor_edit_mode or (traj_mode != "all" and spot_mode != "all"):
            # Selected-only/off modes have no base geometry to extend.
            self._kymo_base_overlay_signature = (
                self._kymo_base_overlay_cache_signature(len(trajectories))
            )
            return True

        current_signature = self._kymo_base_overlay_cache_signature(len(trajectories))
        if self._kymo_base_overlay_signature == current_signature:
            # A channel/kymograph refresh may already have included this row.
            return True
        expected_signature = self._kymo_base_overlay_cache_signature(idx)
        if self._kymo_base_overlay_signature != expected_signature:
            return False

        ctx = self._kymo_overlay_context(invert_y=True)
        if ctx is None:
            # No kymograph is displayed, so there is no missing visible base.
            self._kymo_base_overlay_signature = current_signature
            return True
        roi, kymo_w, current_kymo_ch, frame_to_y, roi_cache = ctx
        selected_idx = nav.trajectoryCanvas.table_widget.currentRow()
        show_anchors = self._should_show_kymo_anchors()
        markers = []
        scatters = []
        pick_entries = []
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
            showsearchline=traj_mode == "all",
            show_spots=spot_mode == "all",
            skinny=False,
            show_labels=traj_mode == "all",
            show_anchors=show_anchors and traj_mode == "all",
            trajectory_indices=(idx,),
        )
        if not batched:
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
                showsearchline=traj_mode == "all",
                show_spots=spot_mode == "all",
                skinny=False,
                show_labels=traj_mode == "all",
                show_anchors=show_anchors and traj_mode == "all",
            )

        self.kymo_trajectory_markers.extend(markers)
        self._kymo_base_scatter_artists.extend(scatters)
        self._kymo_base_pick_entries.extend(pick_entries)
        self._refresh_kymo_clickable_artists(
            rebuild_base=True, rebuild_selected=False
        )
        self._update_kymo_base_artist_visibility()
        self._refresh_kymo_label_bboxes(base=True, selected=False)
        self._kymo_base_overlay_signature = current_signature
        self.invalidate_blit_background()
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
        show_spots=True,
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

        hide_spots = not bool(show_spots)
        spots = traj.get("spot_centers", [None]*len(frames))
        pts = []
        if not hide_spots:
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
        if pts:
            xs_pts, ys_pts = (np.asarray(vals, dtype=float) for vals in zip(*pts))
        else:
            xs_pts = np.empty((0,), dtype=float)
            ys_pts = np.empty((0,), dtype=float)

        if not hide_spots and len(xs_pts):
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

        if not hide_spots and len(xs_pts) and highlighted and halo_lw:
            halo, = self.ax.plot(
                xs_pts, ys_pts,
                linestyle='-', color="#7da1ff",
                solid_capstyle='round', solid_joinstyle='round',
                linewidth=halo_lw, alpha=0.5, zorder=1
            )
            self._mark_kymo_dense_artist(halo)
            markers.append(halo)

        if show_labels:
            lx0, ly0, lx1, ly1 = self._kymo_anchor_endpoint_labels(
                x0, y0, x1, y1,
                anchors_match_current,
                anchors,
            )
            self._add_kymo_endpoint_labels(idx, lx0, ly0, lx1, ly1, highlighted, markers)

    def draw_selected_trajectory_on_kymo(
        self,
        draw_idle=True,
        showsearchline=True,
        skinny=False,
        show_labels=True,
        invert_y=True,
        refresh_base_label_grid=True,
    ):
        self.invalidate_blit_background()
        self.clear_kymo_selected_trajectory_markers(draw_idle=False)
        if self.navigator is None:
            self._restore_hidden_kymo_base_labels()
            return
        traj_mode, spot_mode = self._kymo_overlay_modes()
        anchor_edit_mode = bool(
            getattr(self.navigator, "kymo_anchor_edit_mode", False)
        )
        if traj_mode == "off" and spot_mode == "off" and not anchor_edit_mode:
            self._restore_hidden_kymo_base_labels()
            return
        ctx = self._kymo_overlay_context(invert_y=invert_y)
        if ctx is None:
            self._restore_hidden_kymo_base_labels()
            return
        roi, kymo_w, current_kymo_ch, frame_to_y, roi_cache = ctx
        selected_idx = self.navigator.trajectoryCanvas.table_widget.currentRow()
        trajectories = self.navigator.trajectoryCanvas.trajectories
        if selected_idx < 0 or selected_idx >= len(trajectories):
            self._restore_hidden_kymo_base_labels()
            return
        self._hide_kymo_base_labels_for_row(selected_idx)

        show_anchors = self._should_show_kymo_anchors()
        show_traj = traj_mode != "off"
        show_selected_spots = spot_mode != "off"

        markers = []
        scatters = []
        pick_entries = []
        if anchor_edit_mode:
            self._draw_kymo_anchor_edit_overlay(
                selected_idx,
                roi,
                kymo_w,
                current_kymo_ch,
                frame_to_y,
                markers,
                showsearchline=showsearchline,
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
                showsearchline=showsearchline and show_traj,
                show_spots=show_selected_spots,
                skinny=skinny,
                show_labels=show_labels and show_traj,
                show_anchors=show_anchors and show_traj,
            )
        self.kymo_selected_trajectory_markers = markers
        self._kymo_selected_scatter_artists = scatters
        self._kymo_selected_pick_entries = pick_entries
        self._refresh_kymo_clickable_artists(
            rebuild_base=False, rebuild_selected=True
        )
        self._update_kymo_base_artist_visibility()
        self._refresh_kymo_label_bboxes(
            base=refresh_base_label_grid, selected=True
        )
        self.invalidate_blit_background()
        if draw_idle:
            self.draw_idle()

    def draw_trajectories_on_kymo(self, showsearchline=True, skinny=False, show_labels=True, invert_y=True):
        self._finish_kymo_interaction(redraw=False)
        self.invalidate_blit_background()
        self.clear_kymo_trajectory_markers(draw_idle=False)
        if self.navigator is None:
            return

        traj_mode, spot_mode = self._kymo_overlay_modes()
        anchor_edit_mode = bool(
            getattr(self.navigator, "kymo_anchor_edit_mode", False)
        )
        if traj_mode == "off" and spot_mode == "off" and not anchor_edit_mode:
            return

        ctx = self._kymo_overlay_context(invert_y=invert_y)
        if ctx is None:
            return
        roi, kymo_w, current_kymo_ch, frame_to_y, roi_cache = ctx

        selected_idx = self.navigator.trajectoryCanvas.table_widget.currentRow()

        show_anchors = self._should_show_kymo_anchors()

        if anchor_edit_mode:
            self.draw_selected_trajectory_on_kymo(
                draw_idle=False,
                showsearchline=showsearchline,
                skinny=skinny,
                show_labels=show_labels,
                invert_y=invert_y,
                refresh_base_label_grid=False,
            )
            self._refresh_kymo_label_bboxes()
            return

        markers = []
        scatters = []
        pick_entries = []
        draw_base = traj_mode == "all" or spot_mode == "all"
        if draw_base:
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
                showsearchline=showsearchline and traj_mode == "all",
                show_spots=spot_mode == "all",
                skinny=skinny,
                show_labels=show_labels and traj_mode == "all",
                show_anchors=show_anchors and traj_mode == "all",
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
                        showsearchline=showsearchline and traj_mode == "all",
                        show_spots=spot_mode == "all",
                        skinny=skinny,
                        show_labels=show_labels and traj_mode == "all",
                        show_anchors=show_anchors and traj_mode == "all",
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
                refresh_base_label_grid=False,
            )

        self._update_kymo_base_artist_visibility()
        self._refresh_kymo_label_bboxes()
        self._kymo_base_overlay_signature = (
            self._kymo_base_overlay_cache_signature(
                len(self.navigator.trajectoryCanvas.trajectories)
            )
        )
        self.invalidate_blit_background()

    def clear_kymo_selected_trajectory_markers(self, draw_idle=False):
        self.invalidate_blit_background()
        self._remove_kymo_artists(getattr(self, "kymo_selected_trajectory_markers", []))
        self.kymo_selected_trajectory_markers = []
        self._kymo_selected_scatter_artists = []
        self._kymo_selected_pick_entries = []
        self._refresh_kymo_clickable_artists(
            rebuild_base=False, rebuild_selected=True
        )
        self._refresh_kymo_label_bboxes(base=False, selected=True)
        if draw_idle:
            self.draw_idle()

    def clear_kymo_trajectory_markers(self, draw_idle=False):
        self._finish_kymo_interaction(redraw=False)
        self.invalidate_blit_background()
        self._hidden_kymo_base_labels = []
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
        self._kymo_base_cullable_collections = []
        self._kymo_base_cullable_scatters = []
        self._invalidate_kymo_base_culling()
        self._refresh_kymo_clickable_artists()
        self._kymo_base_label_bboxes.clear()
        self._kymo_selected_label_bboxes.clear()
        self._kymo_base_label_grid.clear()
        self._kymo_selected_label_grid.clear()
        self._kymo_base_label_bbox_signature = None
        self._kymo_selected_label_bbox_signature = None
        self._kymo_base_labels_by_row.clear()
        self._kymo_label_bboxes.clear()
        self._kymo_label_style_signature = None
        self._kymo_base_overlay_signature = None
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
