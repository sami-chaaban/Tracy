from ._shared import *
from .base import ImageCanvas
from matplotlib.textpath import TextPath
from matplotlib.font_manager import FontProperties
from matplotlib.transforms import Affine2D
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401 - registers Matplotlib's 3D projection
from PyQt5.QtGui import QCursor
import traceback

class MovieCanvas(ImageCanvas):
    def __init__(self, parent=None, navigator=None):
        super().__init__(parent)
        # Initialize panning and display attributes.
        self._is_panning = False
        self._pan_start = None
        self._orig_xlim = None
        self._orig_ylim = None

        self.fig.patch.set_alpha(0)
        self.ax.patch.set_alpha(0)

        self._im = None
        self.image = None
        self._vmin = None
        self._vmax = None
        self._default_vmin = None
        self._default_vmax = None

        self.enableInteraction = True

        self._update_pending = False
        self._view_redraw_timer = QTimer(self)
        self._view_redraw_timer.setSingleShot(True)
        self._view_redraw_timer.timeout.connect(self._perform_deferred_view_draw)
        self._deferred_cache_background = False
        self._resize_finalize_timer = QTimer(self)
        self._resize_finalize_timer.setSingleShot(True)
        self._resize_finalize_timer.timeout.connect(self._finish_movie_resize_draw)
        self._inset_update_pending = False
        self._last_inset_params = None

        # New attributes for zooming:
        self.scale = 1.0  # Data units per pixel (uniform in x and y)
        self.padding = 1.25
        self.zoom_center = None  # in data (image) coordinates

        # Connect mouse events.
        self.mpl_connect("scroll_event", self.on_scroll)
        self.mpl_connect("button_press_event", self.on_mouse_press)
        self.mpl_connect("motion_notify_event", self.on_mouse_move)
        self.mpl_connect("button_release_event", self.on_mouse_release)

        self._manual_marker_active = False  # flag for key-controlled marker
        self._manual_marker_pos = None      # will hold [x, y]
        self._manual_marker_artist = None   # store the drawn marker

        self.sum_mode = False
        self.sum_frame_cache = {}

        self._norm_slider_settings = None  # to store normal mode slider settings
        self._sum_slider_settings = None 

        self.tempRoiLine = None
        self._roi_bg = None
        self.roiAddMode = False
        self.roiPoints = [] 

        self.navigator = navigator

        self.last_fitted_center = None
        self.last_fitted_sigma = None
        self.last_intensity_value = None

        self.manual_zoom = False
        self.movie_trajectory_markers = []
        self.movie_selected_trajectory_markers = []
        self._movie_base_clickable_artists = []
        self._movie_selected_clickable_artists = []
        self._movie_base_label_artists = []
        self._movie_selected_label_artists = []
        self.movie_clickable_artists = []
        self.movie_label_artists = []
        self._movie_base_label_bboxes: dict[Text, Bbox] = {}
        self._movie_selected_label_bboxes: dict[Text, Bbox] = {}
        self._movie_base_label_bbox_signature = None
        self._movie_selected_label_bbox_signature = None
        self._movie_label_bboxes: dict[Text, Bbox] = {}
        self._movie_base_cullable_collections = []
        self._movie_base_cull_generation = 0
        self._movie_base_cull_signature = None
        self._movie_base_overlay_signature = None

        self._ctrl_panning = False
        self._last_pan = 0.0
        self._idle_timer = None
        self._idle_active = False
        self._idle_scatter = None
        self._idle_positions = None
        self._idle_velocities = None
        self._idle_sizes = None
        self._idle_rng = np.random.default_rng()
        self._idle_color = "#7da1ff"
        self._idle_drift = np.array([0.0002, 0.0001])
        self._idle_cursor = None
        self._idle_cursor_strength = 0.0
        self._idle_cursor_prev = None
        self._idle_cursor_last = None
        self._idle_tracy_path = None
        self._idle_tracy_purple = "#8b7dff"
        self._idle_base_speeds = None
        self._bg_refresh_timer = None
        self._inset_owner = None
        self._suppress_inset_enter = False
        self._inset_event_filter_targets = []
        self._inset_rotation_suspended = False
        self._inset_rotation_step_degrees = 0.35
        self._inset_rotation_timer = QTimer(self)
        self._inset_rotation_timer.setInterval(80)
        self._inset_rotation_timer.timeout.connect(self._advance_inset_auto_rotation)

        self.setAcceptDrops(True)

    def draw(self, *args, **kwargs):
        # Limit changes are sometimes animated directly on the Axes. Cull at
        # the draw boundary so those paths restore automatically as they enter
        # view, even when update_view() was not the caller.
        if hasattr(self, "_movie_base_cullable_collections"):
            self._update_movie_base_overlay_visibility()
        return super().draw(*args, **kwargs)

    def enterEvent(self, event):
        owner = getattr(self, "_inset_owner", None)
        if owner is not None:
            if not getattr(owner, "_suppress_inset_enter", False):
                owner._show_threed_inset()
            return
        try:
            super().enterEvent(event)
        except Exception:
            pass

    def leaveEvent(self, event):
        owner = getattr(self, "_inset_owner", None)
        if owner is not None:
            owner._schedule_hide_threed_inset()
            return
        try:
            super().leaveEvent(event)
        except Exception:
            pass

    def eventFilter(self, obj, event):
        targets = getattr(self, "_inset_event_filter_targets", None) or []
        if obj in targets:
            event_type = event.type()
            if event_type in (QEvent.Enter, QEvent.HoverEnter):
                self._show_threed_inset()
                return False
            if event_type in (QEvent.Leave, QEvent.HoverLeave):
                self._schedule_hide_threed_inset()
                return False
            if (
                event_type == QEvent.MouseButtonPress
                and obj is getattr(self.navigator, "zoomInsetWidget", None)
                and event.button() == Qt.LeftButton
            ):
                # Stop before Matplotlib receives the same press and begins its
                # normal Axes3D drag handling.
                self._stop_inset_auto_rotation(user_interaction=True)
                return False
        return False

    def _flip_y_enabled(self):
        return bool(getattr(self.navigator, "flip_movie_y", False))

    def _dropped_load_file(self, mime_data):
        if mime_data is None or not mime_data.hasUrls():
            return None, None, "Drop one local movie, ROI, or trajectory file."

        local_paths = []
        for url in mime_data.urls():
            if url.isLocalFile():
                local_paths.append(url.toLocalFile())

        if len(local_paths) != 1:
            return None, None, "Please drop exactly one local file."

        path = local_paths[0]
        if not path or not os.path.isfile(path):
            return None, None, "Dropped item is not a valid file."

        ext = os.path.splitext(path)[1].lower()
        if ext in (".roi", ".zip"):
            return path, "rois", None
        if ext not in (".tif", ".tiff"):
            if ext in (".xlsx", ".csv"):
                return path, "trajectories", None
            return None, None, (
                "Unsupported file type. Drop a movie (.tif/.tiff), line ROIs (.roi/.zip), "
                "or trajectories (.xlsx/.csv)."
            )

        return path, "movie", None

    def dragEnterEvent(self, event):
        dropped_path, _kind, _err = self._dropped_load_file(event.mimeData())
        if dropped_path:
            event.acceptProposedAction()
            return
        event.ignore()

    def dragMoveEvent(self, event):
        dropped_path, _kind, _err = self._dropped_load_file(event.mimeData())
        if dropped_path:
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event):
        dropped_path, kind, err = self._dropped_load_file(event.mimeData())
        if not dropped_path:
            event.ignore()
            QMessageBox.warning(self.navigator or self, "Invalid file", err or "Invalid file drop.")
            return

        event.acceptProposedAction()
        self._load_dropped_file(dropped_path, kind, message_parent=self)

    def _load_dropped_file(self, dropped_path, kind, message_parent=None):
        message_parent = message_parent or self
        navigator = getattr(self, "navigator", None)
        if kind == "movie":
            if navigator is None or not hasattr(navigator, "handle_movie_load"):
                QMessageBox.warning(message_parent, "Load failed", "Movie loader is unavailable.")
                return
            def _load_dropped_movie(path=dropped_path, nav=navigator):
                try:
                    nav.handle_movie_load(fname=path)
                except Exception as exc:
                    traceback.print_exc()
                    QMessageBox.critical(nav or message_parent, "Load failed", f"Could not load movie:\n{exc}")

            QTimer.singleShot(0, _load_dropped_movie)
            return

        if kind == "rois":
            if navigator is None or not hasattr(navigator, "load_roi"):
                QMessageBox.warning(message_parent, "Load failed", "ROI loader is unavailable.")
                return

            def _load_dropped_rois(path=dropped_path, nav=navigator):
                try:
                    nav.load_roi(files=[path])
                except Exception as exc:
                    traceback.print_exc()
                    QMessageBox.critical(nav or message_parent, "Load failed", f"Could not load line ROIs:\n{exc}")

            QTimer.singleShot(0, _load_dropped_rois)
            return

        if kind == "trajectories":
            traj_canvas = getattr(navigator, "trajectoryCanvas", None) if navigator is not None else None
            if traj_canvas is None or not hasattr(traj_canvas, "load_trajectories"):
                QMessageBox.warning(message_parent, "Load failed", "Trajectory loader is unavailable.")
                return
            def _load_dropped_trajectories(path=dropped_path, canvas=traj_canvas):
                try:
                    canvas.load_trajectories(filename=path)
                except Exception as exc:
                    traceback.print_exc()
                    QMessageBox.critical(canvas, "Load failed", f"Could not load trajectories:\n{exc}")

            QTimer.singleShot(0, _load_dropped_trajectories)
            return

        QMessageBox.warning(message_parent, "Load failed", "Unsupported dropped file.")

    def _image_origin(self):
        return "lower"

    def _image_extent(self, w, h):
        return (-0.5, w - 0.5, -0.5, h - 0.5)

    def _extent_from_bounds(self, x1, x2, y1, y2):
        return [x1, x2, y1, y2]

    def _inset_pixel_centers(self, x1, x2, y1, y2, width, height):
        width = max(int(width), 1)
        height = max(int(height), 1)
        dx = (x2 - x1) / width
        dy = (y2 - y1) / height
        x = x1 + (np.arange(width, dtype=np.float64) + 0.5) * dx
        y = y1 + (np.arange(height, dtype=np.float64) + 0.5) * dy
        return x, y

    def apply_flip_y(self):
        if self._im is None or self.image is None:
            return
        self.update_view()

    def mousePressEvent(self, event):
        # Ctrl+Left → pretend it was Middle
        if event.button() == Qt.LeftButton and (event.modifiers() & Qt.ControlModifier):
            self._ctrl_panning = True
            fake = QMouseEvent(
                event.type(),
                event.pos(),
                Qt.MiddleButton,        # button
                Qt.MiddleButton,        # buttons (pressed state)
                event.modifiers()
            )
            super().mousePressEvent(fake)
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

    def leaveEvent(self, event):
        self._ctrl_panning = False
        self._idle_cursor = None
        self._idle_cursor_prev = None
        self._idle_cursor_last = None
        self._idle_cursor_strength = 0.0
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        # — finish fake Ctrl+Left pan —
        if self._ctrl_panning and event.button() == Qt.LeftButton:
            # 1) clear both flags
            self._ctrl_panning = False
            self._is_panning    = False

            # 2) send Matplotlib the “middle‐button released” so it can do its cleanup
            fake = QMouseEvent(
                event.type(),
                event.pos(),
                Qt.MiddleButton,
                Qt.NoButton,
                event.modifiers()
            )
            super().mouseReleaseEvent(fake)
            self.update_view(cache_background=True)
            return

        # — finish real middle‐button pan —
        if event.button() == Qt.MiddleButton:
            self._is_panning = False
            super().mouseReleaseEvent(event)
            self.update_view(cache_background=True)
            return

        # — everything else —
        super().mouseReleaseEvent(event)

    def display_image(self, image, title=""):
        """
        Show a 2D image with its native data extent.
        We also initialize our zoom center and scale.
        """
        if image is None:
            return
        self.stop_idle_animation()
        self.image = image
        h, w = image.shape
        # Set the image extent to the full image.
        extent = self._image_extent(w, h)
        origin = self._image_origin()
        # Initialize zoom_center to the image center if not set yet.
        if self.zoom_center is None:
            self.zoom_center = (w/2, h/2)
        # Set an initial scale such that the entire image is visible.
        # We choose scale so that the data width equals the widget width, unless the widget is not yet sized.
        widget_w = self.width() if self.width() > 0 else w
        widget_h = self.height() if self.height() > 0 else h
        # To show the entire image without zooming, choose the maximum scale needed to cover both dimensions.
        base = max(w / widget_w, h / widget_h)
        self.max_scale = base * self.padding
        self.scale = base
        
        # Draw or update the image.
        if self._im is None:
            self.ax.clear()
            cmap = "gray_r" if getattr(self.navigator, "inverted_cmap", False) else "gray"
            self._im = self.ax.imshow(image, cmap=cmap, vmin=self._vmin, vmax=self._vmax,
                                       origin=origin, extent=extent)
            self.ax.axis("off")
        else:
            self._im.set_data(image)
            self._im.set_extent(extent)

        # Apply final limits and render once. The old path drew immediately and
        # then queued the same full labelled render again one millisecond later.
        self.update_view()

    def update_image_data(self, image, *, draw=True):
        """Update only the image data without changing the current axes limits."""
        if image is None:
            return
        self.image = image
        if self._im is None:
            # if never drawn, fall back
            self.display_image(image)
        else:
            # update pixels…
            self._im.set_data(image)
            if not draw:
                # The caller is composing more overlay changes and will issue
                # one final draw.  Any old blit background contains old pixels.
                self._bg = None
                self._roi_bg = None
                return
            # …draw…
            self.draw()
            # …then recapture blit backgrounds so hover/blit won’t restore an old frame
            self._capture_movie_view_background()
            self._draw_temp_movie_analysis_line()

    def _draw_temp_movie_analysis_line(self):
        lines = (
            getattr(self.navigator, "temp_movie_analysis_line", None),
            getattr(self, "tempRoiLine", None),
        )
        drawn = False
        seen = set()
        for line in lines:
            if line is None or id(line) in seen:
                continue
            seen.add(id(line))
            try:
                if not line.get_visible():
                    continue
                self.ax.draw_artist(line)
                drawn = True
            except Exception:
                pass
        if drawn:
            try:
                self.figure.canvas.blit(self.ax.bbox)
            except Exception:
                pass

    def _capture_movie_view_background(self):
        canvas = self.figure.canvas
        self._roi_bbox = self.ax.bbox
        # Both consumers restore the same clean axes pixels. BufferRegion is
        # read-only during restore, so one capture safely serves both caches.
        background = canvas.copy_from_bbox(self._roi_bbox)
        self._bg = background
        self._roi_bg = background
        self._refresh_movie_label_bboxes(base=False, selected=True)

    def _render_movie_view(self, *, cache_background):
        self.draw()
        if cache_background:
            self._capture_movie_view_background()
        else:
            self._bg = None
            self._roi_bg = None
        # Animated analysis lines are intentionally absent from the clean
        # background; paint the same artist back without another full render.
        self._draw_temp_movie_analysis_line()

    def _schedule_deferred_view_draw(self, cache_background=False):
        self._deferred_cache_background = (
            self._deferred_cache_background or bool(cache_background)
        )
        if not self._view_redraw_timer.isActive():
            self._view_redraw_timer.start(8)

    def _perform_deferred_view_draw(self):
        cache_background = bool(self._deferred_cache_background)
        self._deferred_cache_background = False
        self._render_movie_view(cache_background=cache_background)
        if not self._is_panning:
            self.manual_zoom = False

    def _set_movie_view_limits(self):
        if getattr(self, "image", None) is None or getattr(self, "zoom_center", None) is None:
            return False
        widget_w = self.width()
        widget_h = self.height()
        view_w = widget_w * self.scale
        view_h = widget_h * self.scale
        cx, cy = self.zoom_center
        self.ax.set_xlim(cx - view_w / 2, cx + view_w / 2)
        if self._flip_y_enabled():
            self.ax.set_ylim(cy + view_h / 2, cy - view_h / 2)
        else:
            self.ax.set_ylim(cy - view_h / 2, cy + view_h / 2)
        return True

    def _finish_movie_resize_draw(self):
        # FigureCanvasQT queues its own coalesced draw from resizeEvent. Wait
        # until that render completes, then capture its pixels without drawing
        # the labelled overlays a second time.
        if getattr(self, "_draw_pending", False):
            self._resize_finalize_timer.start(0)
            return
        if self.image is None or self.width() <= 1 or self.height() <= 1:
            return
        self._capture_movie_view_background()
        self._draw_temp_movie_analysis_line()
        self.manual_zoom = False

    def update_view(self, cache_background=True, defer=False):
        if not self._set_movie_view_limits():
            return

        if defer:
            self._bg = None
            self._roi_bg = None
            self._schedule_deferred_view_draw(cache_background=cache_background)
            return

        if self._view_redraw_timer.isActive():
            self._view_redraw_timer.stop()
        self._deferred_cache_background = False
        self._render_movie_view(cache_background=cache_background)

        self.manual_zoom = False

    def fit_to_full_image(self):
        """Reset pan/zoom so the whole movie frame fills the canvas."""
        if self.image is None:
            return
        if self._view_redraw_timer.isActive():
            self._view_redraw_timer.stop()
        self._deferred_cache_background = False
        self._is_panning = False
        self._pan_start = None
        self._orig_xlim = None
        self._orig_ylim = None

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
        if not self.enableInteraction or self.image is None or event.inaxes != self.ax:
            return
        
        if self.navigator.looping:
            self.manual_zoom = True
            self.navigator.stoploop(prompt=True)
        # 1) grab mouse‐data coords & old scale
        mx, my     = event.xdata, event.ydata
        old_scale  = self.scale
        if mx is None or my is None:
            return

        # 2) compute new scale
        base = 1.2
        if event.button == 'up':
            new_scale = old_scale / base
        else:
            new_scale = old_scale * base

        # clamp to max if set
        if hasattr(self, 'max_scale'):
            new_scale = min(new_scale, self.max_scale)

        # 3) recompute zoom_center so that (mx,my) stays stationary
        cx, cy = self.zoom_center
        ratio  = new_scale / old_scale
        new_cx = mx + (cx - mx) * ratio
        new_cy = my + (cy - my) * ratio

        # 4) store & schedule redraw
        self.scale       = new_scale
        self.zoom_center = (new_cx, new_cy)
        self.update_view(cache_background=True, defer=True)
        # schedule a single zoom/pan update per event loop
    #     if not self._update_pending:
    #         self._update_pending = True
    #         QTimer.singleShot(1, self._perform_throttled_update)

    # def _perform_throttled_update(self):
    #     """
    #     Perform the zoom/pan update in a throttled manner.
    #     """
    #     # full view update then clear the pending flag
    #     self.update_view()
    #     self._update_pending = False
        
    def on_mouse_press(self, event):
        self.manual_zoom = True
        self.navigator._stop_animation = True  
        if not self.enableInteraction:
            return
        if event.inaxes != self.ax:
            return
        if event.button == 2:  # middle-click for panning
            self._is_panning = True
            self._pan_start = (event.x, event.y)
            self._orig_xlim = self.ax.get_xlim()
            self._orig_ylim = self.ax.get_ylim()
            self._last_pan = time.perf_counter()

    def on_mouse_move(self, event):
        if self._idle_active:
            if event.inaxes == self.ax and event.xdata is not None and event.ydata is not None:
                now = time.perf_counter()
                cursor = np.array([event.xdata, event.ydata], dtype=float)
                if self._idle_cursor_prev is not None and self._idle_cursor_last is not None:
                    dt = max(now - self._idle_cursor_last, 1e-3)
                    delta = cursor - self._idle_cursor_prev
                    dist = np.linalg.norm(delta)
                    speed = 0.0 if dist < 1e-5 else dist / dt
                    target = min(speed * 0.06, 1.0)
                    self._idle_cursor_strength = 0.8 * self._idle_cursor_strength + 0.2 * target
                else:
                    self._idle_cursor_strength = 0.0
                self._idle_cursor_prev = cursor
                self._idle_cursor_last = now
                self._idle_cursor = (event.xdata, event.ydata)
            else:
                self._idle_cursor = None
                self._idle_cursor_prev = None
                self._idle_cursor_last = None
                self._idle_cursor_strength = 0.0
        if not self._is_panning or event.inaxes != self.ax:
            return
        # throttle pan updates to ~50 Hz
        now = time.perf_counter()
        if now - self._last_pan < 0.02:
            return
        self._last_pan = now

        inv = self.ax.transData.inverted()
        prev_data = inv.transform(self._pan_start)
        current_data = inv.transform((event.x, event.y))
        dx = current_data[0] - prev_data[0]
        dy = current_data[1] - prev_data[1]

        # update the zoom center
        cx, cy = self.zoom_center
        self.zoom_center = (cx - dx, cy - dy)

        # update pan origin for next delta
        self._pan_start = (event.x, event.y)
        self.manual_zoom = True
        
        self.update_view(cache_background=False, defer=True)
        # schedule a single, throttled redraw
        # if not self._update_pending:
        #     self._update_pending = True
        #     QTimer.singleShot(0, self._perform_throttled_update)

    def on_mouse_release(self, event):
        # this is the Matplotlib MouseEvent handler — do NOT call the Qt super()
        if event.button == 2 and event.inaxes == self.ax:
            # The Qt release wrapper performs the single final draw after all
            # Matplotlib release callbacks have run.
            self._is_panning = False

    def resizeEvent(self, event):
        # 1) remember current view center in data coords
        cur_xlim = self.ax.get_xlim()
        cur_ylim = self.ax.get_ylim()
        self.zoom_center = (
            (cur_xlim[0] + cur_xlim[1]) * 0.5,
            (cur_ylim[0] + cur_ylim[1]) * 0.5
        )

        # 2) do the normal resize
        super().resizeEvent(event)
        # Recompute max_scale on resize so zoom-out still fills the canvas
        if hasattr(self, 'image') and self.image is not None:
            h, w = self.image.shape[:2]
            widget_w = self.width() if self.width() > 0 else w
            widget_h = self.height() if self.height() > 0 else h
            base = max(w / widget_w, h / widget_h)
            self.max_scale = base * self.padding

        # FigureCanvasQT already queued one coalesced draw in super(). Apply
        # the final limits now and capture that draw once it completes.
        if self._set_movie_view_limits():
            self._bg = None
            self._roi_bg = None
            self._resize_finalize_timer.start(0)

    def _inset_subpixel_crop(self, image, x1, x2, y1, y2, output_shape):
        """Bilinear crop for inset previews without depending on scipy.ndimage."""
        out_h, out_w = (int(output_shape[0]), int(output_shape[1]))
        if image is None or out_h <= 0 or out_w <= 0:
            return np.empty((0, 0), dtype=float)
        arr = np.asarray(image)
        if arr.ndim < 2:
            return np.empty((0, 0), dtype=float)
        h, w = arr.shape[:2]
        if h <= 0 or w <= 0:
            return np.empty((0, 0), dtype=float)

        out_y, out_x = np.indices((out_h, out_w), dtype=np.float64)
        step_x = (x2 - x1) / out_w
        step_y = (y2 - y1) / out_h
        in_x = x1 + (out_x + 0.5) * step_x
        in_y = y1 + (out_y + 0.5) * step_y
        in_x = np.clip(in_x, 0, w - 1)
        in_y = np.clip(in_y, 0, h - 1)

        x0 = np.floor(in_x).astype(np.intp)
        y0 = np.floor(in_y).astype(np.intp)
        x_next = np.minimum(x0 + 1, w - 1)
        y_next = np.minimum(y0 + 1, h - 1)
        dx = in_x - x0
        dy = in_y - y0
        if arr.ndim > 2:
            dx = dx[..., None]
            dy = dy[..., None]

        top = arr[y0, x0] * (1.0 - dx) + arr[y0, x_next] * dx
        bottom = arr[y_next, x0] * (1.0 - dx) + arr[y_next, x_next] * dx
        return top * (1.0 - dy) + bottom * dy

    def _inset_zoom_nearest(self, image, zoom_factor):
        arr = np.asarray(image)
        if arr.ndim < 2:
            return arr
        try:
            factor = float(zoom_factor)
        except (TypeError, ValueError):
            factor = 1.0
        if factor <= 0:
            factor = 1.0
        h, w = arr.shape[:2]
        out_h = max(1, int(round(h * factor)))
        out_w = max(1, int(round(w * factor)))
        if out_h == h and out_w == w:
            return arr
        y_idx = np.minimum((np.arange(out_h) / factor).astype(np.intp), h - 1)
        x_idx = np.minimum((np.arange(out_w) / factor).astype(np.intp), w - 1)
        return arr[y_idx[:, None], x_idx[None, :]]

    def _log_inset_3d_error(self):
        try:
            log_path = os.path.join(os.path.expanduser("~"), "tracy_inset_3d_error.log")
            with open(log_path, "a", encoding="utf-8") as handle:
                handle.write(traceback.format_exc())
                handle.write("\n")
        except Exception:
            pass

    @staticmethod
    def _format_inset_measurement(prefix, value):
        return (
            f'<span style="font-size: 10px;">{prefix}:</span> '
            f'<span style="font-size: 16px;">{float(value):.1f}</span>'
        )

    @classmethod
    def _format_inset_coordinates(cls, fitted_center):
        if fitted_center is None:
            return ""
        return (
            cls._format_inset_measurement("X", fitted_center[0])
            + "&nbsp;&nbsp;"
            + cls._format_inset_measurement("Y", fitted_center[1])
        )

    def _set_inset_labels(self, fitted_center, intensity_value):
        self.navigator.zoomInsetLabel.setText(
            self._format_inset_coordinates(fitted_center)
        )
        intensity_text = ""
        if fitted_center is not None and intensity_value is not None:
            intensity_text = self._format_inset_measurement("I", intensity_value)
        self.navigator.zoomInsetIntensityLabel.setText(intensity_text)
        
    def update_inset(self, image, center, crop_size, zoom_factor=2,
                    fitted_center=None, fitted_sigma=None,
                    fitted_peak=None, offset=None, intensity_value=None, pointcolor="magenta"):
        # store params
        self._last_inset_params = (image, center, crop_size, zoom_factor,
                                fitted_center, fitted_sigma,
                                fitted_peak, offset, intensity_value, pointcolor)

        if getattr(self.navigator, "hide_inset", False):
            self._stop_inset_auto_rotation()
            self._inset_rotation_suspended = False
            if hasattr(self, "inset_ax3d"):
                self.inset_ax3d.set_visible(False)
            widget = getattr(self.navigator, "zoomInsetWidget", None)
            if widget is not None:
                widget.ax.set_visible(True)
            if hasattr(self.navigator, "zoomInsetFrame"):
                self.navigator.zoomInsetFrame.setVisible(False)
            return
        
        widget = self.navigator.zoomInsetWidget
        widget._inset_owner = self
        widget.setMouseTracking(True)
        fig = widget.figure

        frame = self.navigator.zoomInsetFrame
        self._install_inset_event_filters()
        # record default size once
        if not hasattr(self, '_default_inset_size'):
            self._default_inset_size = (frame.width(), frame.height())
        # ensure frame & widget can freely resize
        # frame.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        # self.navigator.zoomInsetWidget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        # ensure 3D axes exists
        if not hasattr(self, 'inset_ax3d'):
            self.inset_ax3d = fig.add_subplot(111, projection='3d')
            self.inset_ax3d.set_axis_off()
            self.inset_ax3d.set_visible(False)
            try:
                # Keep left-drag rotation, disable right-click zoom/pan.
                self.inset_ax3d.mouse_init(rotate_btn=1, pan_btn=[], zoom_btn=[])
            except Exception:
                pass

            # hook enter/leave
            cid = fig.canvas
            self._enter_cid = cid.mpl_connect('axes_enter_event', self._on_inset_enter)
            self._leave_cid = cid.mpl_connect('axes_leave_event', self._on_inset_leave)
            self._scroll3d_cid = cid.mpl_connect('scroll_event', self._on_inset_scroll)
            self._press3d_cid = cid.mpl_connect(
                'button_press_event', self._on_inset_button_press
            )

        if not getattr(self, '_inset_update_pending', False):
            self._inset_update_pending = True
            # if the inset is currently visible, fire ASAP; otherwise wait 400 ms
            delay = 0 if self.navigator.zoomInsetFrame.isVisible() else 400
            QTimer.singleShot(delay, self._throttled_update_inset)

    def _on_inset_enter(self, event):
        if getattr(self, "_suppress_inset_enter", False):
            return
        if event.inaxes is self.inset_ax3d or event.inaxes is self.navigator.zoomInsetWidget.ax:
            self._show_threed_inset()

    def _install_inset_event_filters(self):
        targets = [
            getattr(self.navigator, "zoomInsetFrame", None),
            getattr(self.navigator, "zoomInsetWidget", None),
            getattr(self.navigator, "zoomInsetLabel", None),
            getattr(self.navigator, "zoomInsetIntensityLabel", None),
        ]
        for target in targets:
            if target is None or target in self._inset_event_filter_targets:
                continue
            try:
                target.setMouseTracking(True)
                target.installEventFilter(self)
                self._inset_event_filter_targets.append(target)
            except Exception:
                pass

    def _cursor_inside_inset_frame(self):
        frame = getattr(self.navigator, "zoomInsetFrame", None)
        if frame is None:
            return False
        try:
            return frame.isVisible() and frame.rect().contains(frame.mapFromGlobal(QCursor.pos()))
        except Exception:
            return False

    def _schedule_hide_threed_inset(self, delay_ms=80):
        def hide_if_outside():
            if self._cursor_inside_inset_frame():
                return
            self._hide_threed_inset()

        QTimer.singleShot(delay_ms, hide_if_outside)

    def _inset_frame_is_visible(self):
        frame = getattr(self.navigator, "zoomInsetFrame", None)
        if frame is None:
            return True
        try:
            return bool(frame.isVisible())
        except Exception:
            return True

    def _start_inset_auto_rotation(self):
        if self._inset_rotation_suspended:
            return
        if getattr(self.navigator, "hide_inset", False):
            return
        ax3d = getattr(self, "inset_ax3d", None)
        if ax3d is None or not ax3d.get_visible() or not self._inset_frame_is_visible():
            return
        if not self._inset_rotation_timer.isActive():
            self._inset_rotation_timer.start()

    def _stop_inset_auto_rotation(self, user_interaction=False):
        self._inset_rotation_timer.stop()
        if user_interaction:
            self._inset_rotation_suspended = True

    def _advance_inset_auto_rotation(self):
        ax3d = getattr(self, "inset_ax3d", None)
        if (
            self._inset_rotation_suspended
            or getattr(self.navigator, "hide_inset", False)
            or ax3d is None
            or not ax3d.get_visible()
            or not self._inset_frame_is_visible()
        ):
            self._stop_inset_auto_rotation()
            return

        try:
            elevation = float(ax3d.elev)
            azimuth = (float(ax3d.azim) + self._inset_rotation_step_degrees) % 360.0
            ax3d.view_init(elev=elevation, azim=azimuth)
            self.navigator.zoomInsetWidget.draw_idle()
        except Exception:
            self._stop_inset_auto_rotation()

    def _on_inset_button_press(self, event):
        button = getattr(event, "button", None)
        button_value = getattr(button, "value", button)
        if event.inaxes is self.inset_ax3d and button_value == 1:
            self._stop_inset_auto_rotation(user_interaction=True)

    def _show_threed_inset(self, refresh=False):
        if getattr(self, "_suppress_inset_enter", False):
            return
        if getattr(self.navigator, "hide_inset", False):
            self._stop_inset_auto_rotation()
            return
        if not hasattr(self, "inset_ax3d"):
            self._stop_inset_auto_rotation()
            return

        was_visible = (
            self.inset_ax3d.get_visible() and self._inset_frame_is_visible()
        )
        if was_visible and not refresh:
            # One physical hover can produce several nested Qt/Matplotlib enter
            # events. Do not rebuild the surface or reset the user's camera.
            self._start_inset_auto_rotation()
            return

        preserved_view = None
        if refresh and was_visible:
            try:
                preserved_view = (float(self.inset_ax3d.elev), float(self.inset_ax3d.azim))
            except Exception:
                preserved_view = None
        if not was_visible:
            # A fresh hover gets one automatic rotation session. A click only
            # suspends it until the pointer leaves this inset.
            self._inset_rotation_suspended = False

        if self.navigator.looping:
            self.navigator.stoploop()
        self.navigator.zoomInsetWidget.ax.set_visible(False)
        self.inset_ax3d.set_visible(True)
        try:
            self._draw_threed_inset()
            if preserved_view is not None:
                self.inset_ax3d.view_init(
                    elev=preserved_view[0], azim=preserved_view[1]
                )
                self.navigator.zoomInsetWidget.draw_idle()
            self._start_inset_auto_rotation()
        except Exception:
            self._stop_inset_auto_rotation()
            self._log_inset_3d_error()
            try:
                self.inset_ax3d.set_visible(False)
                self.navigator.zoomInsetWidget.ax.set_visible(True)
                self._throttled_update_inset()
            except Exception:
                pass

    def _hide_threed_inset(self):
        self._stop_inset_auto_rotation()
        self._inset_rotation_suspended = False
        if not hasattr(self, "inset_ax3d"):
            return
        if not self.inset_ax3d.get_visible():
            return
        frame = self.navigator.zoomInsetFrame
        if hasattr(self, "_default_inset_size"):
            w0, h0 = self._default_inset_size
            geom = frame.geometry()
            new_x = geom.x() + geom.width() - w0
            frame.move(new_x, geom.y())
            frame.resize(w0, h0)
            frame.layout().invalidate()
            frame.layout().activate()
        self.inset_ax3d.set_visible(False)
        self.navigator.zoomInsetWidget.ax.set_visible(True)
        self._suppress_inset_enter = True
        try:
            self._throttled_update_inset()
        finally:
            self._suppress_inset_enter = False

    def _clear_threed_inset(self):
        """Erase the 3D inset and hide its frame."""
        self._stop_inset_auto_rotation()
        self._inset_rotation_suspended = False
        ax3d = self.inset_ax3d
        ax3d.cla()                # clear contents
        ax3d.set_axis_off()       # hide axes
        ax3d.set_visible(False)
        self.navigator.zoomInsetWidget.ax.set_visible(True)
        self.navigator.zoomInsetFrame.setVisible(False)
        self.navigator.zoomInsetWidget.draw()

    def _draw_threed_inset(self):

        """
        The heavy-lifting routine: crops, zooms, builds the 3D intensity
        surface plus the Gaussian cap, and then makes everything visible.
        """
        params = self._last_inset_params
        if not params or len(params) != 10:
            return
        (image, center, crop_size, zoom_factor,
        fitted_center, fitted_sigma,
        fitted_peak, offset, intensity_value, pointcolor) = params
        

        # sanity
        if image is None or center is None or np.isnan(center[0]) or np.isnan(center[1]):
            return

        # --- crop & zoom ---
        half = crop_size/2.0
        cx, cy = center
        x1, x2 = cx-half, cx+half
        y1, y2 = cy-half, cy+half
        out_shape = (int(round(y2-y1)), int(round(x2-x1)))
        cropped = self._inset_subpixel_crop(image, x1, x2, y1, y2, out_shape)
        zoomed = self._inset_zoom_nearest(cropped, zoom_factor)
        if zoomed.size == 0:
            return

        # build/reset axes
        ax3d = self.inset_ax3d
        ax3d.cla()
        ax3d.set_axis_off()

        # grid & surface
        h,w = zoomed.shape
        x, y = self._inset_pixel_centers(x1, x2, y1, y2, w, h)
        X,Y = np.meshgrid(x,y)
        raw = np.asarray(zoomed, dtype=np.float64)
        finite = np.isfinite(raw)
        if not finite.any():
            return
        if not finite.all():
            fill_value = float(np.nanmin(raw[finite]))
            raw = np.where(finite, raw, fill_value)
        try:
            baseline = float(offset) if offset is not None else 0.0
        except (TypeError, ValueError):
            baseline = 0.0
        if not np.isfinite(baseline):
            baseline = 0.0

        Z = raw - baseline
        z_bottom = float(np.min(Z))
        z_top = float(np.max(Z))
        if not (np.isfinite(z_bottom) and np.isfinite(z_top)):
            return
        z_bottom = min(z_bottom, 0.0)
        z_top = max(z_top, 0.0)
        if z_top <= z_bottom:
            z_bottom -= 0.5
            z_top += 0.5

        norm_min = float(np.min(Z))
        norm_max = float(np.max(Z))
        if norm_max <= norm_min:
            norm_max = norm_min + 1.0
        norm = mcolors.Normalize(vmin=norm_min, vmax=norm_max)
        cmap_name = "gray_r" if self.navigator.inverted_cmap else "gray"
        cmap = cm.get_cmap(cmap_name)
        facecolors = cmap(norm(Z))
        facecolors[..., 3] = 1.0

        surface = ax3d.plot_surface(
            X, Y, Z,
            facecolors=facecolors,
            rstride=1,
            cstride=1,
            linewidth=0,
            antialiased=False,
            shade=False,
        )
        surface.set_sort_zpos(float(z_top))

        # Gaussian cap
        if fitted_center is not None and fitted_sigma is not None and fitted_peak is not None:
            x0,y0 = fitted_center;  A = fitted_peak;  σ = fitted_sigma
            G = A*np.exp(-(((X-x0)**2+(Y-y0)**2)/(2*σ**2)))
            mesh_color = pointcolor or "#FF4FA3"
            mesh_rgba = mcolors.to_rgba(mesh_color, 0.65)
            fill_rgba = mcolors.to_rgba(mesh_color, 0.18)

            surf = ax3d.plot_surface(
                X, Y, G,
                color=fill_rgba,
                rstride=1, cstride=1,
                linewidth=0,
                antialiased=True,
                shade=True,
                alpha=fill_rgba[3],
            )
            surf.set_sort_zpos(float(G.max()))

            wf = ax3d.plot_wireframe(
                X, Y, G,
                color=mesh_rgba,
                rstride=2, cstride=2,
                linewidth=0.8,
                alpha=mesh_rgba[3],
            )
            wf.set_zorder(10)
            g_min = float(np.nanmin(G))
            g_max = float(np.nanmax(G))
            if np.isfinite(g_min):
                z_bottom = min(z_bottom, g_min)
            if np.isfinite(g_max):
                z_top = max(z_top, g_max)

        # style & zoom
        ax3d.view_init(elev=60, azim=275)
        ax3d.set_axis_off()
        ax3d.set_facecolor((0,0,0,0));  ax3d.set_xlim(x1,x2);  ax3d.set_ylim(y1,y2)
        if not (np.isfinite(z_bottom) and np.isfinite(z_top)) or z_top <= z_bottom:
            # fallback to a small positive range, or let Matplotlib autoscale
            try:
                ax3d.autoscale(z=True)
            except Exception:
                pass
        else:
            ax3d.set_zlim(z_bottom, z_top)
        for a in (ax3d.xaxis, ax3d.yaxis, ax3d.zaxis):
            a.pane.fill = False;  a.pane.set_edgecolor('none')
        ax3d.grid(False)

        # finally make it visible
        self.navigator.zoomInsetWidget.draw()
        self.navigator.zoomInsetFrame.setVisible(True)

        self._set_inset_labels(fitted_center, intensity_value)


    def _on_inset_scroll(self, event):
        if event.inaxes is self.navigator.zoomInsetWidget.ax:
            self._show_threed_inset()
            return
        if event.inaxes is not self.inset_ax3d:
            return

        frame = self.navigator.zoomInsetFrame
        base   = 1.15
        factor = base if event.button == 'up' else 1/base

        x, y, w, h = frame.geometry().getRect()
        new_w = max(self._default_inset_size[0], min(800, int(w * factor)))
        new_h = max(self._default_inset_size[1], min(800, int(h * factor)))

        # keep top-right fixed
        new_x = x + w - new_w

        # just move + resize the frame
        frame.move(new_x, y)
        frame.resize(new_w, new_h)

        # tell Qt to redo the layout for its children
        frame.layout().invalidate()
        frame.layout().activate()
        # defer redraw so resize stays responsive
        self._schedule_inset_redraw()

    def _schedule_inset_redraw(self, delay_ms=40):
        if not hasattr(self, "_inset_resize_timer"):
            self._inset_resize_timer = QTimer(self)
            self._inset_resize_timer.setSingleShot(True)
            self._inset_resize_timer.timeout.connect(self._redraw_inset_after_resize)
        self._inset_resize_timer.start(delay_ms)

    def _redraw_inset_after_resize(self):
        if getattr(self.navigator, "hide_inset", False):
            return
        # redraw with existing artists; avoid recomputing the 3D surface
        self.navigator.zoomInsetWidget.draw_idle()

    def _on_inset_leave(self, event):
        if event.inaxes in (self.inset_ax3d, self.navigator.zoomInsetWidget.ax):
            self._schedule_hide_threed_inset()

    def _throttled_update_inset(self):
        """Perform the heavy inset update using the most recent parameters."""
        self._inset_update_pending = False
        if getattr(self.navigator, "hide_inset", False):
            self._stop_inset_auto_rotation()
            if hasattr(self.navigator, "zoomInsetFrame"):
                self.navigator.zoomInsetFrame.setVisible(False)
            return
        params = self._last_inset_params
        if not params or not isinstance(params, tuple) or len(params) !=10:
            return
        
        image, center, crop_size, zoom_factor, fitted_center, fitted_sigma, fitted_peak, offset, intensity_value, pointcolor = self._last_inset_params

        if image is None:
            return
        if center is None or np.isnan(center[0]) or np.isnan(center[1]):
            print("Warning: update_inset received invalid center:", center)
            return
        if hasattr(self, "inset_ax3d") and self.inset_ax3d.get_visible():
            self._show_threed_inset(refresh=True)
            return
        half = crop_size / 2.0
        x_center, y_center = center[0], center[1]
        x1, x2 = x_center - half, x_center + half
        y1, y2 = y_center - half, y_center + half
        output_shape = (int(round(y2 - y1)), int(round(x2 - x1)))
        cropped = self._inset_subpixel_crop(image, x1, x2, y1, y2, output_shape)
        zoomed = self._inset_zoom_nearest(cropped, zoom_factor)
        if zoomed.size == 0:
            return
        self.source_image = image
        self.zoom_extent = (x1, x2, y1, y2)
        
        if hasattr(self.navigator, "zoomInsetWidget"):
            # Update the inset widget’s axes.
            inset_ax = self.navigator.zoomInsetWidget.ax
            inset_ax.clear()
            self.navigator.zoomInsetWidget._im_inset = inset_ax.imshow(
                zoomed,
                cmap=("gray_r" if self.navigator.inverted_cmap else "gray"),
                origin=self._image_origin(),
                extent=self._extent_from_bounds(x1, x2, y1, y2)
            )
            if self._flip_y_enabled():
                inset_ax.set_ylim(y2, y1)
            else:
                inset_ax.set_ylim(y1, y2)
            inset_ax.set_xticks([])
            inset_ax.set_yticks([])
            inset_ax.axis('off')

            # Optionally, draw magenta circles if fit parameters are provided.
            if fitted_center is not None and fitted_sigma is not None and intensity_value is not None:
                self.inset_circle = Circle(fitted_center, radius=fitted_sigma * 2, 
                                edgecolor=pointcolor, facecolor='none', linewidth=2, alpha=1)
                inset_ax.add_patch(self.inset_circle)

            self._set_inset_labels(fitted_center, intensity_value)
            self.navigator.zoomInsetWidget.draw()
            # Finally, show the whole zoom inset frame.
            self.navigator.zoomInsetFrame.setVisible(True)
        else:
            self.ax.clear()
            cmap = "gray_r" if getattr(self.navigator, "inverted_cmap", False) else "gray"
            self.ax.imshow(
                zoomed,
                cmap=cmap,
                origin=self._image_origin(),
                extent=self._extent_from_bounds(x1, x2, y1, y2),
            )
            if self._flip_y_enabled():
                self.ax.set_ylim(y2, y1)
            else:
                self.ax.set_ylim(y1, y2)
            self.ax.axis('off')

    def update_roi_drawing(self, current_pos):
        pts = list(self.roiPoints) + ([current_pos] if current_pos else [])
        if len(pts) < 2 or not self.roiAddMode:
            return

        xs, ys = zip(*pts)
        canvas = self.figure.canvas     # the QtAgg FigureCanvas

        if self.tempRoiLine is None:
            # 1) draw static image+axes
            canvas.draw()
            # 2) snapshot full axes region (no ROI)
            self._roi_bbox = self.ax.bbox
            self._roi_bg   = canvas.copy_from_bbox(self._roi_bbox)
            # 3) create the line artist (but don’t redraw full figure)
            self.tempRoiLine, = self.ax.plot(xs, ys, '--', linewidth=1.5, color='#81C784')
            # Keep this transient line out of every clean full-render background;
            # the ROI motion loop paints it with draw_artist() just like the
            # temporary analysis line.
            self.tempRoiLine.set_animated(True)
        else:
            # restore the clean background
            canvas.restore_region(self._roi_bg)
            # update the line
            self.tempRoiLine.set_data(xs, ys)
            # draw just that artist
            self.ax.draw_artist(self.tempRoiLine)
            # blit only the axes region
            canvas.blit(self._roi_bbox)

    def finalize_roi(self, suppress_display: bool = False, channels=None):
        # Make sure we have at least two pointsf
        if not self.roiPoints or len(self.roiPoints) < 2:
            print("Not enough points to finalize ROI.")
            return
        
        #print("ROI points:", self.roiPoints)

        # Build the ROI dictionary using all collected points.
        # Use 'x' and 'y' keys expected by the conversion function.
        # and also store the full list as 'points' for any later processing.
        roi = {
            "type": "line",  # or "segmented_line"
            "x": [pt[0] for pt in self.roiPoints],
            "y": [pt[1] for pt in self.roiPoints],
            "points": self.roiPoints.copy()
        }

        # Combine keys from both dictionaries.
        all_names = set(self.navigator.rois.keys()) | set(self.navigator.kymographs.keys())
        numeric_names = []
        for name in all_names:
            try:
                # Only append if conversion to int is possible.
                numeric_names.append(int(name))
            except (ValueError, TypeError):
                # If a key isn’t numeric, skip it.
                pass

        if numeric_names:
            max_num = max(numeric_names)
            next_num = max_num + 1
        else:
            next_num = 1
        name = f"{next_num:03d}"        

        # Store the ROI and kymograph with the same name.
        self.navigator.rois[name] = roi
        self.navigator.roiCombo.addItem(name)
        self.navigator.roiCombo.setEnabled(True)
        if not suppress_display:
            self.navigator.roiCombo.setCurrentText(name)
        self.navigator.update_roilist_visibility()

        if self.navigator.movie.ndim == 4:
            n_chan = self.navigator.movie.shape[self.navigator._channel_axis]
        else:
            n_chan = 1

        if channels is None:
            channel_numbers = list(range(1, n_chan + 1))
        else:
            channel_numbers = []
            for ch in channels:
                try:
                    ch_num = int(ch)
                except (TypeError, ValueError):
                    continue
                if 1 <= ch_num <= n_chan and ch_num not in channel_numbers:
                    channel_numbers.append(ch_num)
            if not channel_numbers:
                channel_numbers = list(range(1, n_chan + 1))

        for ch_num in channel_numbers:
            kymo = self.generate_kymograph(roi, channel_override=ch_num)
            kymo_name = f"ch{ch_num}-{name}"
            self.navigator.kymographs[kymo_name] = kymo
            self.navigator.kymo_roi_map[kymo_name] = {
                "roi":      name,
                "channel":  ch_num,
                "orphaned": False
            }
            if hasattr(self.navigator, "_mark_kymo_needs_default_view"):
                self.navigator._mark_kymo_needs_default_view(kymo_name)

        # self.navigator.last_kymo_by_channel[ch+1] = kymo_name

        self.navigator._last_roi = name
        self.navigator.kymoCombo.setEnabled(True)


        # Clear the temporary ROI markers and the stored points.
        self.roiPoints = []
        if self.tempRoiLine is not None:
            try:
                self.tempRoiLine.remove()
            except Exception:
                pass
            self.tempRoiLine = None

        if not suppress_display:
            self.navigator.update_kymo_list_for_channel()
            self.navigator.kymo_changed()
            self.navigator.update_kymo_visibility()
            self.navigator.update_kymo_list_for_channel()

        if not suppress_display:
            self.draw()

    def generate_kymograph(self, roi, channel_override=None):
        # --- Compute the ROI sample positions along the drawn line ---
        xs = np.array(roi["x"], dtype=float)
        ys = np.array(roi["y"], dtype=float)
        
        # Compute cumulative distances along the ROI.
        distances = np.sqrt(np.diff(xs)**2 + np.diff(ys)**2)
        cum_dist = np.concatenate(([0], np.cumsum(distances)))
        total_length = cum_dist[-1]
        
        # Determine the number of sample positions (at least 2)
        num_samples = max(int(total_length), 2)
        sample_positions = np.linspace(0, total_length, num_samples)
        
        # Interpolate the ROI coordinates at these positions.
        sample_x = np.interp(sample_positions, cum_dist, xs)
        sample_y = np.interp(sample_positions, cum_dist, ys)
        
        # --- Compute the tangent and then the normal direction at each sample point ---
        # Use finite differences (np.gradient) to approximate the tangent.
        tangent_dx = np.gradient(sample_x)
        tangent_dy = np.gradient(sample_y)
        # A unit normal can be taken as (-dy, dx)
        normal_x = -tangent_dy
        normal_y = tangent_dx
        norm = np.sqrt(normal_x**2 + normal_y**2)
        norm[norm == 0] = 1  # avoid division by zero if any
        normal_x /= norm
        normal_y /= norm

        # --- Obtain integration parameters ---
        # n pixels in either direction perpendicular to the line
        line_width = getattr(self.navigator, "line_width", 2)
        # Method: "max" (default) or "average"
        line_method = getattr(self.navigator, "line_integration_method", "max").lower()

        # Create an array of offsets along the normal direction.
        # This gives a 1D array from -line_width to +line_width.
        offsets = np.arange(-line_width, line_width + 1, dtype=float)  # shape (n_offsets,)
        n_offsets = offsets.size

        # For each sample point, compute its neighborhood along the normal.
        # These arrays have shape (num_samples, n_offsets).
        sample_x_full = sample_x[:, None] + normal_x[:, None] * offsets[None, :]
        sample_y_full = sample_y[:, None] + normal_y[:, None] * offsets[None, :]

        # Flatten the coordinate arrays so they can be passed to map_coordinates.
        # Note: the first row are y coordinates (rows) and the second row x coordinates.
        coords = np.vstack((sample_y_full.ravel(), sample_x_full.ravel()))

        # --- Retrieve the movie ---
        if hasattr(self, "navigator") and self.navigator.movie is not None:
            movie = self.navigator.movie
        else:
            return None

        # --- Process each frame using vectorized interpolation per frame ---
        kymo_rows = []
        n_frames = movie.shape[0]
        for i in range(n_frames):
            # For multi–channel movies, extract the 2D frame for the chosen channel.
            if movie.ndim == 4:
                frame = movie[i]
                if channel_override is not None:
                    channel_index = int(channel_override) - 1
                elif hasattr(self.navigator, "movieChannelCombo") and self.navigator.movieChannelCombo.isEnabled():
                    channel_index = int(self.navigator.movieChannelCombo.currentText()) - 1
                else:
                    channel_index = 0
                n_channels = movie.shape[self.navigator._channel_axis]
                if channel_index < 0 or channel_index >= n_channels:
                    raise IndexError(
                        f"Channel {channel_index + 1} is out of range for movie with {n_channels} channels"
                    )
                if self.navigator._channel_axis == 1:
                    frame_2d = frame[channel_index]
                else:
                    frame_2d = frame[..., channel_index]
            else:
                # For 3D movies (single channel), take the frame directly.
                frame_2d = movie[i]

            # Use map_coordinates to extract the pixel values at all normal offsets,
            # in one vectorized call.
            patch_values = map_coordinates(frame_2d, coords, order=1, mode='reflect')
            # Reshape so that each row corresponds to one sample point (along the ROI)
            # and each column corresponds to one offset along the normal.
            patch_values = patch_values.reshape(num_samples, n_offsets)

            # Integrate along the normal direction based on the selected method.
            if line_method == "average":
                profile = np.mean(patch_values, axis=1)
            else:
                profile = np.max(patch_values, axis=1)
            kymo_rows.append(profile)

        kymo = np.vstack(kymo_rows)

        return kymo

    def clear_temporary_roi_markers(self):
        # Clear any temporary ROI dotted line.
        if hasattr(self, 'tempRoiLine') and self.tempRoiLine is not None:
            try:
                self.tempRoiLine.remove()
            except Exception:
                pass
            self.tempRoiLine = None

        self.draw()

    def _max_projection_frame(self, movie_stack):
        projection = np.max(movie_stack, axis=0)
        if not np.issubdtype(movie_stack.dtype, np.integer) or projection.size == 0:
            return projection

        dtype_info = np.iinfo(movie_stack.dtype)
        ceiling = dtype_info.max
        saturated_fraction = float(np.mean(projection >= ceiling))
        if saturated_fraction < 0.25:
            return projection

        # Saturated camera pixels dominate long max projections. For display,
        # use the brightest non-saturated sample when a pixel hit the ceiling.
        # Reduce one frame at a time so this fallback does not allocate a
        # temporary array as large as the entire movie.
        robust = np.full(projection.shape, dtype_info.min, dtype=movie_stack.dtype)
        all_saturated = np.ones(projection.shape, dtype=bool)
        for frame in movie_stack:
            valid = frame < ceiling
            np.maximum(robust, frame, out=robust, where=valid)
            np.logical_not(valid, out=valid)
            np.logical_and(all_saturated, valid, out=all_saturated)
        if np.any(all_saturated):
            robust[all_saturated] = ceiling
        return robust.astype(projection.dtype, copy=False)

    def display_sum_frame(self):
        if self.navigator is None or self.navigator.movie is None:
            return

        movie = self.navigator.movie

        # figure out which channel key to use
        if movie.ndim == 4:
            try:
                ch = int(self.navigator.movieChannelCombo.currentText()) - 1
            except Exception:
                ch = 0

            if ch in self.sum_frame_cache:
                sum_frame = self.sum_frame_cache[ch]
            else:
                channel_axis = self.navigator._channel_axis
                idx = [slice(None)] * movie.ndim
                idx[channel_axis] = ch
                channel_movie = movie[tuple(idx)]
                sum_frame = self._max_projection_frame(channel_movie)
                self.sum_frame_cache[ch] = sum_frame

        elif movie.ndim == 3:
            # Use the same cache for single-channel movies. The projection is
            # independent of the current frame, so slider/playback refreshes
            # should not scan the full movie again.
            cache_key = 0
            if cache_key in self.sum_frame_cache:
                sum_frame = self.sum_frame_cache[cache_key]
            else:
                if movie.shape[0] <= 4:
                    sum_frame = movie[0]
                else:
                    sum_frame = self._max_projection_frame(movie)
                self.sum_frame_cache[cache_key] = sum_frame
        else:
            sum_frame = movie

        # now render exactly like before
        self.image = sum_frame
        cmap = "gray_r" if getattr(self.navigator, "inverted_cmap", False) else "gray"
        if self._im is None:
            self.ax.clear()
            h, w = sum_frame.shape[:2]
            self._im = self.ax.imshow(
                sum_frame,
                cmap=cmap,
                vmin=self._vmin,
                vmax=self._vmax,
                origin=self._image_origin(),
                extent=self._image_extent(w, h),
            )
            self.ax.axis("off")
            self.draw()
        else:
            self._im.set_data(sum_frame)
            self._im.set_cmap(cmap)
            if self._vmin is not None and self._vmax is not None:
                self._im.set_clim(self._vmin, self._vmax)
            h, w = sum_frame.shape[:2]
            self._im.set_extent(self._image_extent(w, h))
            self.draw()

        # ── recapture blit backgrounds so future blits use the sum‐mode image ──
        self._capture_movie_view_background()
        self._draw_temp_movie_analysis_line()

    def clear_sum_cache(self, channel=None):
        """
        If channel is None, flush everything (e.g. on new movie).
        Otherwise just remove that one channel’s cache.
        """
        if channel is None:
            self.sum_frame_cache.clear()
        else:
            self.sum_frame_cache.pop(channel, None)

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
            self._schedule_bg_refresh()

    def _schedule_bg_refresh(self):
        if self._bg_refresh_timer is None:
            self._bg_refresh_timer = QTimer(self)
            self._bg_refresh_timer.setSingleShot(True)
            self._bg_refresh_timer.timeout.connect(self._refresh_blit_background)
        self._bg_refresh_timer.start(30)

    def _refresh_blit_background(self):
        # Rebuild blit backgrounds so contrast changes don't restore stale frames.
        try:
            if self.navigator is not None and getattr(self.navigator, "temp_movie_analysis_line", None) is not None:
                self.navigator._rebuild_movie_blit_background()
            else:
                self.draw()
                self._capture_movie_view_background()
                self._draw_temp_movie_analysis_line()
        except Exception:
            pass

    def overlay_rectangle(self, cx, cy, size, color='#7da1ff'):
        # remove old
        if getattr(self, "rect_overlay", None) is not None:
            try:    self.rect_overlay.remove()
            except: pass

        half = size / 2.0
        x0, x1 = cx - half, cx + half
        y0, y1 = cy - half, cy + half

        # a closed polyline (4 corners + back to first)
        verts_x = [x0, x1, x1, x0, x0]
        verts_y = [y0, y0, y1, y1, y0]

        line, = self.ax.plot(
            verts_x,
            verts_y,
            color=color,
            linewidth=2,
            zorder=6,
            linestyle='-'
        )
        self.rect_overlay = line

    def is_zoomed_in(self):
        if self.image is None or not hasattr(self, "full_extent"):
            return False
        full_left, full_right, full_bottom, full_top = self.full_extent
        cur_xlim = self.ax.get_xlim()
        cur_ylim = self.ax.get_ylim()
        tol = 1e-6
        # Check if current limits are nearly the same as the full extent.
        return not (abs(cur_xlim[0] - full_left) < tol and abs(cur_xlim[1] - full_right) < tol and
                    abs(cur_ylim[0] - full_bottom) < tol and abs(cur_ylim[1] - full_top) < tol)

    def clear_canvas(self):
        """Clear the canvas by removing all overlays and resetting internal state."""
        self.stop_idle_animation()
        self.clear_movie_trajectory_markers(draw_idle=False)
        # Clear the axes.
        self.ax.cla()
        # Remove stored image, marker, and any overlay objects.
        self._im = None
        self._marker = None
        # Clear any gaussian circle if present.
        self.remove_gaussian_circle()
        # Reset panning and manual zoom state.
        self._is_panning = False
        self._pan_start = None
        self._orig_xlim = None
        self._orig_ylim = None
        self.manual_zoom = False
        self.image = None

    def start_idle_animation(self, count=90):
        if self.image is not None or self._idle_active:
            return
        self._idle_active = True
        rng = self._idle_rng
        if self._idle_tracy_path is None:
            self._idle_tracy_path = self._build_idle_tracy_path()
        self._idle_positions = rng.random((count, 2))
        angles = rng.uniform(0, 2 * np.pi, size=count)
        speeds = rng.uniform(0.00008, 0.0006, size=count)
        self._idle_velocities = np.column_stack(
            (np.cos(angles) * speeds, np.sin(angles) * speeds)
        )
        self._idle_base_speeds = speeds
        self._idle_sizes = rng.uniform(10, 40, size=count)
        self.ax.clear()
        self.ax.set_xlim(0, 1)
        self.ax.set_ylim(0, 1)
        self.ax.axis("off")
        self._idle_scatter = self.ax.scatter(
            self._idle_positions[:, 0],
            self._idle_positions[:, 1],
            s=self._idle_sizes,
            color=self._idle_color,
            alpha=0.25,
            edgecolors="none"
        )
        if self._idle_timer is None:
            self._idle_timer = QTimer(self)
            self._idle_timer.timeout.connect(self._update_idle_animation)
        self._idle_timer.start(40)
        self.draw_idle()

    def stop_idle_animation(self):
        if self._idle_timer is not None:
            self._idle_timer.stop()
        self._idle_active = False
        self._idle_cursor = None
        if self._idle_scatter is not None:
            try:
                self._idle_scatter.remove()
            except Exception:
                pass
        self._idle_scatter = None
        self._idle_positions = None
        self._idle_velocities = None
        self._idle_sizes = None
        self._idle_tracy_path = None
        self._idle_base_speeds = None

    def _build_idle_tracy_path(self):
        props = FontProperties(weight="bold")
        text_path = TextPath((0, 0), "TRACY", size=1, prop=props)
        bbox = text_path.get_extents()
        if bbox.width <= 0 or bbox.height <= 0:
            return None
        target = 0.8
        sx = target / bbox.width
        sy = target / bbox.height
        left = (1.0 - target) * 0.5
        bottom = (1.0 - target) * 0.5
        tx = left - bbox.x0 * sx
        ty = bottom - bbox.y0 * sy
        transform = Affine2D().scale(sx, sy).translate(tx, ty)
        return text_path.transformed(transform)

    def _update_idle_animation(self):
        if not self._idle_active or self.image is not None:
            self.stop_idle_animation()
            return
        if self._idle_scatter is None or self._idle_positions is None:
            return
        rng = self._idle_rng
        pos = self._idle_positions
        vel = self._idle_velocities
        vel += rng.normal(scale=0.00004, size=vel.shape)
        influence = None
        diff = None
        dist = None
        strength = 0.0
        if self._idle_cursor is not None and self._idle_cursor_strength > 0.0:
            cursor = np.array(self._idle_cursor)
            diff = cursor - pos
            dist = np.linalg.norm(diff, axis=1)
            influence = dist < 0.12
            strength = self._idle_cursor_strength
        if self._idle_tracy_path is not None:
            tracy_mask = self._idle_tracy_path.contains_points(pos)
        else:
            tracy_mask = None
        speed = np.linalg.norm(vel, axis=1)
        if self._idle_base_speeds is not None and speed.size == self._idle_base_speeds.size:
            jitter = rng.normal(scale=0.05, size=self._idle_base_speeds.shape)
            target = self._idle_base_speeds * (1.0 + jitter)
            target = np.clip(target, 0.00005, 0.0012)
            scale = target / np.maximum(speed, 1e-6)
            if influence is not None:
                scale = np.where(influence, 1.0, scale)
            # Ease velocities back toward their base speeds to avoid snapping.
            relax = 0.12
            vel = vel * (1.0 + relax * (scale - 1.0))[:, None]
        if influence is not None and np.any(influence) and strength > 0.0:
            pull = (0.025 * strength) / (0.02 + dist[influence])
            vel[influence] += diff[influence] * pull[:, None]
            max_speed = 0.01
            vel_infl = vel[influence]
            speed_infl = np.linalg.norm(vel_infl, axis=1)
            over = speed_infl > max_speed
            if np.any(over):
                vel_infl[over] *= (max_speed / speed_infl[over])[:, None]
                vel[influence] = vel_infl
        noise = rng.normal(scale=0.0002, size=pos.shape)
        pos = (pos + vel + noise + self._idle_drift) % 1.0
        self._idle_positions = pos
        self._idle_velocities = vel
        self._idle_scatter.set_offsets(pos)
        if tracy_mask is not None:
            colors = np.full(pos.shape[0], self._idle_color, dtype=object)
            colors[tracy_mask] = self._idle_tracy_purple
            self._idle_scatter.set_color(colors)
        self.draw_idle()

    def draw_manual_marker(self):
        """Draw a translucent circle at the current manual position."""
        # Remove any existing manual marker
        if getattr(self, "_manual_marker_artist", None) is not None:
            try:
                self._manual_marker_artist.remove()
            except Exception:
                pass

        # Draw a semi‑transparent circle
        x, y = self._manual_marker_pos
        radius = 3  # adjust as desired
        circ = Circle(
            (x, y),
            radius=radius,
            edgecolor='#7da1ff',
            facecolor='#7da1ff',
            alpha=0.6,
            linewidth=1.5
        )
        self._manual_marker_artist = circ
        self.ax.add_patch(circ)

    def clear_manual_marker(self):
        """Remove the manual marker circle from the canvas."""
        if getattr(self, "_manual_marker_artist", None) is not None:
            try:
                self._manual_marker_artist.remove()
            except Exception:
                pass
            self._manual_marker_artist = None

    def add_gaussian_circle(self, fitted_center, fitted_sigma, color="magenta"):
        if fitted_center is not None and fitted_sigma is not None:
            self.gaussian_circle = Circle(
                    fitted_center,
                    radius=2 * fitted_sigma,
                    edgecolor=color,
                    facecolor='none',
                    linewidth=2
                )
            self.ax.add_patch(self.gaussian_circle)

    def remove_gaussian_circle(self):
        removed = False
        if hasattr(self, "gaussian_circle") and self.gaussian_circle is not None:
            try:
                self.gaussian_circle.remove()
                removed = True
            except Exception as e:
                print("Error removing gaussian circle:", e)
            self.gaussian_circle = None
        return removed

    def _refresh_movie_clickable_artists(self):
        self.movie_clickable_artists = (
            list(getattr(self, "_movie_selected_clickable_artists", []))
            + list(getattr(self, "_movie_base_clickable_artists", []))
        )
        self.movie_label_artists = (
            list(getattr(self, "_movie_selected_label_artists", []))
            + list(getattr(self, "_movie_base_label_artists", []))
        )

    @staticmethod
    def _movie_collection_segment_bounds(segments):
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

    def _invalidate_movie_base_culling(self):
        self._movie_base_cull_generation = (
            int(getattr(self, "_movie_base_cull_generation", 0)) + 1
        )
        self._movie_base_cull_signature = None

    def _register_movie_base_collection(self, collection, segments, colors=None):
        source_segments = tuple(np.asarray(segment, dtype=float) for segment in segments)
        collection._tracy_movie_source_segments = source_segments
        collection._tracy_movie_segment_bounds = self._movie_collection_segment_bounds(
            source_segments
        )
        collection._tracy_movie_source_colors = (
            tuple(colors) if colors is not None and len(colors) == len(source_segments)
            else None
        )
        collection._tracy_movie_visible_indices = None
        collection._tracy_movie_visible_index_array = None
        self._movie_base_cullable_collections.append(collection)
        self._invalidate_movie_base_culling()

    def _movie_culling_margin_pixels(self):
        radius_points = 0.0
        for collection in list(
            getattr(self, "_movie_base_cullable_collections", []) or []
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
        try:
            radius_pixels = float(
                self.figure.canvas.get_renderer().points_to_pixels(radius_points)
            )
        except Exception:
            radius_pixels = radius_points * float(self.figure.dpi) / 72.0
        return max(4.0, radius_pixels + 2.0)

    def _update_movie_base_overlay_visibility(self):
        try:
            signature = (
                tuple(float(value) for value in self.ax.get_xlim()),
                tuple(float(value) for value in self.ax.get_ylim()),
                tuple(float(value) for value in self.ax.bbox.bounds),
                float(self.figure.dpi),
                int(getattr(self, "_movie_base_cull_generation", 0)),
            )
        except Exception:
            signature = None
        if signature is not None and signature == self._movie_base_cull_signature:
            return
        labels = list(getattr(self, "_movie_base_label_artists", []) or [])
        if labels:
            try:
                anchors = np.asarray([label.xy for label in labels], dtype=float)
                display = self.ax.transData.transform(anchors)
                visible = self.ax.patch.contains_points(display, radius=1.0)
                for label, is_visible in zip(labels, visible):
                    label.set_visible(bool(is_visible))
            except Exception:
                # Annotation's own clipping remains the exact fallback.
                for label in labels:
                    label.set_visible(True)

        collections = list(
            getattr(self, "_movie_base_cullable_collections", []) or []
        )
        if not collections:
            self._movie_base_cull_signature = signature
            return
        try:
            xlim = self.ax.get_xlim()
            ylim = self.ax.get_ylim()
            x_low, x_high = sorted((float(xlim[0]), float(xlim[1])))
            y_low, y_high = sorted((float(ylim[0]), float(ylim[1])))
            bbox = self.ax.bbox
            margin_pixels = self._movie_culling_margin_pixels()
            x_margin = margin_pixels * (x_high - x_low) / max(float(bbox.width), 1.0)
            y_margin = margin_pixels * (y_high - y_low) / max(float(bbox.height), 1.0)
        except Exception:
            return

        for collection in collections:
            segments = getattr(collection, "_tracy_movie_source_segments", ())
            bounds = getattr(collection, "_tracy_movie_segment_bounds", None)
            if bounds is None or len(bounds) != len(segments):
                continue
            finite = np.isfinite(bounds).all(axis=1)
            intersects = (
                finite
                & (bounds[:, 1] >= x_low - x_margin)
                & (bounds[:, 0] <= x_high + x_margin)
                & (bounds[:, 3] >= y_low - y_margin)
                & (bounds[:, 2] <= y_high + y_margin)
            )
            index_array = np.flatnonzero(intersects)
            previous = getattr(
                collection, "_tracy_movie_visible_index_array", None
            )
            if previous is not None and np.array_equal(index_array, previous):
                continue
            collection.set_segments([segments[int(i)] for i in index_array])
            colors = getattr(collection, "_tracy_movie_source_colors", None)
            if colors is not None:
                collection.set_color([colors[int(i)] for i in index_array])
            collection._tracy_movie_visible_index_array = index_array.copy()
            collection._tracy_movie_visible_indices = tuple(
                int(i) for i in index_array
            )
        self._movie_base_cull_signature = signature

    def _refresh_movie_label_bboxes(self, *, base=True, selected=True):
        groups = []
        if base:
            # Hundreds of renderer-derived text extents can cost more than the
            # trajectories themselves.  Base labels are hit-tested lazily from
            # their current transformed anchors; only two selected extents are
            # worth materializing eagerly.
            self._movie_base_label_bboxes.clear()
            self._movie_base_label_bbox_signature = None
        if selected:
            groups.append((
                list(getattr(self, "_movie_selected_label_artists", []) or []),
                self._movie_selected_label_bboxes,
            ))
        for _labels, bbox_map in groups:
            bbox_map.clear()
        if not groups:
            return
        try:
            renderer = self.figure.canvas.get_renderer()
        except Exception:
            return
        for labels, bbox_map in groups:
            for label in labels:
                try:
                    bbox = label.get_window_extent(renderer)
                    label.update_bbox_position_size(renderer)
                    bbox = label.get_window_extent(renderer)
                    patch = label.get_bbox_patch()
                    if patch is not None:
                        try:
                            bbox = Bbox.union([
                                bbox, patch.get_window_extent(renderer)
                            ])
                        except Exception:
                            pass
                    bbox_map[label] = bbox.expanded(1.5, 1.5)
                except Exception:
                    pass
        signature = self._movie_label_view_signature()
        if selected:
            self._movie_selected_label_bbox_signature = signature

    def _movie_label_view_signature(self):
        try:
            return (
                tuple(float(value) for value in self.ax.get_xlim()),
                tuple(float(value) for value in self.ax.get_ylim()),
                tuple(float(value) for value in self.ax.bbox.bounds),
                float(self.figure.dpi),
            )
        except Exception:
            return None

    def _movie_label_hit_current_transform(self, labels, event):
        """Narrow stale-cache hits cheaply, then ask Matplotlib exactly."""
        labels = [label for label in (labels or []) if label.get_visible()]
        if not labels:
            return None
        try:
            point_scale = float(self.figure.dpi) / 72.0
            anchors = np.asarray([label.xy for label in labels], dtype=float)
            offsets = np.asarray(
                [label.get_position() for label in labels], dtype=float
            ) * point_scale
            centers = self.ax.transData.transform(anchors) + offsets
            font_px = np.asarray(
                [float(label.get_fontsize()) for label in labels], dtype=float
            ) * point_scale
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

    def movie_label_hit(self, event):
        signature = self._movie_label_view_signature()
        groups = (
            (
                self._movie_selected_label_bboxes,
                self._movie_selected_label_artists,
                self._movie_selected_label_bbox_signature,
            ),
            (
                self._movie_base_label_bboxes,
                self._movie_base_label_artists,
                self._movie_base_label_bbox_signature,
            ),
        )
        for bbox_map, labels, cached_signature in groups:
            if cached_signature != signature:
                label = self._movie_label_hit_current_transform(labels, event)
                if label is not None:
                    return label
                continue
            for label, bbox in bbox_map.items():
                try:
                    if bbox.contains(event.x, event.y):
                        return label
                except Exception:
                    continue
        return None

    def _remove_movie_artists(self, artists):
        for marker in artists:
            try:
                marker.remove()
            except Exception:
                pass

    def _current_movie_channel(self):
        try:
            return int(self.navigator.movieChannelCombo.currentText())
        except (ValueError, AttributeError):
            return None

    def _movie_point_alphas(self, frames, n_points):
        fade_prev = 10
        fade_next = 0
        min_alpha_prev = 0.05
        min_alpha_next = 0.05
        try:
            current_frame = int(self.navigator.frameSlider.value())
        except Exception:
            current_frame = None
        if (
            current_frame is None
            or not isinstance(frames, (list, tuple))
            or len(frames) != n_points
        ):
            return None

        point_alphas = []
        for f in frames:
            try:
                delta = int(f) - current_frame
            except Exception:
                delta = -fade_prev
            if delta == 0:
                alpha = 1.0
            elif delta < 0:
                dist = -delta
                if fade_prev <= 0 or dist >= fade_prev:
                    alpha = min_alpha_prev
                else:
                    alpha = 1.0 - (dist / fade_prev) * (1.0 - min_alpha_prev)
            else:
                dist = delta
                if fade_next <= 0 or dist >= fade_next:
                    alpha = min_alpha_next
                else:
                    alpha = 1.0 - (dist / fade_next) * (1.0 - min_alpha_next)
            point_alphas.append(alpha)
        return point_alphas

    def _add_movie_endpoint_labels(
        self, idx, x0, y0, x1, y1, markers, label_artists, *, highlighted
    ):
        traj = self.navigator.trajectoryCanvas.trajectories[idx]
        traj_label = traj.get("file_index") or str(traj["trajectory_number"])
        disp_a = self.ax.transData.transform((x0, y0))
        disp_b = self.ax.transData.transform((x1, y1))
        vector = disp_b - disp_a
        norm = float(np.hypot(*vector))
        unit = vector / norm if norm else np.array([1.0, 0.0])

        for (cx, cy, suffix), sign in (
            ((x0, y0, "A"), -1),
            ((x1, y1, "B"), +1),
        ):
            dx, dy = unit * (15 * sign)
            label = self.ax.annotate(
                f"{traj_label}{suffix}",
                xy=(cx, cy),
                xytext=(dx, dy),
                textcoords="offset points",
                color=("white" if highlighted else "black"),
                fontsize=8,
                fontweight="bold",
                ha="center",
                va="center",
                bbox=dict(
                    boxstyle="circle,pad=0.3",
                    facecolor=("#7da1ff" if highlighted else "#cbd9ff"),
                    alpha=(0.9 if highlighted else 0.6),
                    linewidth=(1.5 if highlighted else 1.0),
                ),
                zorder=(10 if highlighted else 2),
            )
            label.traj_idx = idx
            markers.append(label)
            label_artists.append(label)

    def _draw_movie_base_trajectories_batched(
        self, markers, label_artists, *, current_ch, trajectory_indices=None
    ):
        """Draw all unhighlighted paths with two Matplotlib collections."""
        search_paths = []
        search_colors = []
        spot_segments = []
        spot_colors = []
        hide_spots = getattr(self.navigator, "hide_movie_spots", False)

        trajectories = self.navigator.trajectoryCanvas.trajectories
        if trajectory_indices is None:
            trajectory_indices = range(len(trajectories))
        for idx in trajectory_indices:
            if not (0 <= int(idx) < len(trajectories)):
                continue
            idx = int(idx)
            traj = trajectories[idx]
            traj_ch = traj.get("channel")
            if traj_ch is not None and traj_ch != current_ch:
                continue

            original_coords = traj.get("original_coords", [])
            if len(original_coords) > 0:
                try:
                    original = np.asarray(original_coords, dtype=float)
                except (TypeError, ValueError):
                    original = np.empty((0, 2), dtype=float)
                if original.ndim == 2 and original.shape[0] and original.shape[1] >= 2:
                    original = original[:, :2]
                    search_paths.append(original)
                    search_colors.append(
                        self.navigator._get_uniform_traj_color(traj) or "#7da1ff"
                    )
                    self._add_movie_endpoint_labels(
                        idx,
                        original[0, 0], original[0, 1],
                        original[-1, 0], original[-1, 1],
                        markers,
                        label_artists,
                        highlighted=False,
                    )

            if hide_spots:
                continue
            spots = traj.get("spot_centers", [])
            if len(spots) < 2:
                continue
            points = np.asarray([
                (point[0], point[1])
                if isinstance(point, (tuple, list, np.ndarray)) and len(point) >= 2
                else (np.nan, np.nan)
                for point in spots
            ], dtype=float)
            valid = np.isfinite(points).all(axis=1)
            scatter_kwargs, line_color = self.navigator._get_traj_colors(traj)
            point_colors = scatter_kwargs.get("c")
            per_point = (
                isinstance(point_colors, (list, tuple, np.ndarray))
                and len(point_colors) == len(points)
            )
            uniform_color = mcolors.to_rgba(line_color, 0.7)
            if per_point:
                normalized_colors = [
                    mcolors.to_rgba(color, 0.7) for color in point_colors
                ]
                if all(
                    color == normalized_colors[0]
                    for color in normalized_colors[1:]
                ):
                    per_point = False
                    uniform_color = normalized_colors[0]

            if per_point:
                segment_indices = np.nonzero(valid[:-1] & valid[1:])[0]
                if not len(segment_indices):
                    continue
                spot_segments.extend(np.stack(
                    [points[segment_indices], points[segment_indices + 1]], axis=1
                ))
                spot_colors.extend(
                    [normalized_colors[point_idx] for point_idx in segment_indices]
                )
            else:
                padded = np.concatenate(([False], valid, [False]))
                changes = np.diff(padded.astype(np.int8))
                run_starts = np.nonzero(changes == 1)[0]
                run_ends = np.nonzero(changes == -1)[0]
                for run_start, run_end in zip(run_starts, run_ends):
                    if run_end - run_start < 2:
                        continue
                    spot_segments.append(points[run_start:run_end])
                    spot_colors.append(uniform_color)

        if search_paths:
            search_color_arg = search_colors
            if all(color == search_colors[0] for color in search_colors[1:]):
                search_color_arg = search_colors[0]
            collection = LineCollection(
                search_paths,
                colors=search_color_arg,
                linewidths=1.5,
                linestyles="--",
                alpha=0.6,
                zorder=1,
            )
            try:
                collection.set_capstyle("round")
            except Exception:
                pass
            self.ax.add_collection(collection)
            self._register_movie_base_collection(
                collection,
                search_paths,
                None if isinstance(search_color_arg, str) else search_colors,
            )
            markers.append(collection)

        if spot_segments:
            spot_color_arg = spot_colors
            if all(color == spot_colors[0] for color in spot_colors[1:]):
                spot_color_arg = spot_colors[0]
            collection = LineCollection(
                spot_segments,
                colors=spot_color_arg,
                linewidths=1.5,
                zorder=3,
            )
            self.ax.add_collection(collection)
            self._register_movie_base_collection(
                collection,
                spot_segments,
                None if not isinstance(spot_color_arg, list) else spot_colors,
            )
            markers.append(collection)

    def _movie_base_overlay_cache_signature(self, trajectory_count):
        nav = getattr(self, "navigator", None)
        if nav is None:
            return None
        try:
            mode = nav.get_movie_traj_overlay_mode()
        except Exception:
            mode = "all"
        return (
            int(trajectory_count),
            self._current_movie_channel(),
            mode,
            bool(getattr(nav, "hide_movie_spots", False)),
        )

    def append_trajectory_to_movie_base(self, trajectory_index):
        """Append one new movie base overlay without rebuilding old paths."""
        nav = getattr(self, "navigator", None)
        if nav is None:
            return False
        trajectories = nav.trajectoryCanvas.trajectories
        idx = int(trajectory_index)
        if not (0 <= idx < len(trajectories)):
            return False

        mode = nav.get_movie_traj_overlay_mode()
        if mode != "all":
            self._movie_base_overlay_signature = (
                self._movie_base_overlay_cache_signature(len(trajectories))
            )
            return True

        current_signature = self._movie_base_overlay_cache_signature(
            len(trajectories)
        )
        if self._movie_base_overlay_signature == current_signature:
            return True
        expected_signature = self._movie_base_overlay_cache_signature(idx)
        if self._movie_base_overlay_signature != expected_signature:
            return False

        markers = []
        labels = []
        self._draw_movie_base_trajectories_batched(
            markers,
            labels,
            current_ch=self._current_movie_channel(),
            trajectory_indices=(idx,),
        )
        self.movie_trajectory_markers.extend(markers)
        self._movie_base_label_artists.extend(labels)
        self._refresh_movie_clickable_artists()
        self._refresh_movie_label_bboxes(base=True, selected=False)
        self._update_movie_base_overlay_visibility()
        self._movie_base_overlay_signature = current_signature
        return True

    def _draw_movie_trajectory(
        self,
        idx,
        markers,
        clickables,
        label_artists,
        *,
        highlighted=False,
        include_scatter=False,
        fade_current_frame=False,
        current_ch=None,
    ):
        traj = self.navigator.trajectoryCanvas.trajectories[idx]
        traj_ch = traj.get("channel", None)
        if traj_ch is not None and traj_ch != current_ch:
            return

        original_coords = traj.get("original_coords", [])
        lw_search = 2.0 if highlighted else 1.5
        alpha_search = 0.9 if highlighted else 0.6
        z_search = 8 if highlighted else 1

        if len(original_coords) > 0:
            xs = [pt[0] for pt in original_coords]
            ys = [pt[1] for pt in original_coords]
            search_line_color = self.navigator._get_uniform_traj_color(traj) or "#7da1ff"

            dotted_line, = self.ax.plot(
                xs, ys,
                color=search_line_color,
                linestyle='--',
                linewidth=lw_search,
                alpha=alpha_search,
                zorder=z_search,
                solid_capstyle='round',
                dash_capstyle='round'
            )
            markers.append(dotted_line)

            self._add_movie_endpoint_labels(
                idx,
                xs[0], ys[0], xs[-1], ys[-1],
                markers,
                label_artists,
                highlighted=highlighted,
            )

        spot_centers = traj.get('spot_centers', [])
        xs_pts = [pt[0] if pt is not None else np.nan for pt in spot_centers]
        ys_pts = [pt[1] if pt is not None else np.nan for pt in spot_centers]
        frames = traj.get("frames", [])
        point_alphas = (
            self._movie_point_alphas(frames, len(xs_pts))
            if fade_current_frame else None
        )

        scatter_kwargs, line_color = self.navigator._get_traj_colors(traj)
        scatter_kwargs = scatter_kwargs.copy()
        scatter_kwargs.pop('zorder', None)

        lw_line = (2.0 if highlighted else 1.5)
        alpha_line = (0.9 if highlighted else 0.7)
        z_line = (9 if highlighted else 3)

        hide_spots = getattr(self.navigator, "hide_movie_spots", False)
        pts_colors = scatter_kwargs.get("c")
        segs = []
        seg_colors = []
        if not hide_spots:
            for i in range(len(xs_pts) - 1):
                if (np.isnan(xs_pts[i]) or np.isnan(ys_pts[i])
                        or np.isnan(xs_pts[i + 1]) or np.isnan(ys_pts[i + 1])):
                    continue
                segs.append([[xs_pts[i], ys_pts[i]], [xs_pts[i + 1], ys_pts[i + 1]]])
                if isinstance(pts_colors, (list, tuple, np.ndarray)) and len(pts_colors) == len(xs_pts):
                    base_color = pts_colors[i]
                else:
                    base_color = line_color
                seg_alpha = alpha_line
                if point_alphas is not None:
                    try:
                        seg_alpha = min(point_alphas[i], point_alphas[i + 1])
                    except Exception:
                        seg_alpha = alpha_line
                seg_colors.append(mcolors.to_rgba(base_color, seg_alpha))

        if segs:
            line = LineCollection(
                segs,
                colors=seg_colors,
                linewidths=lw_line,
                zorder=z_line
            )
            self.ax.add_collection(line)
            markers.append(line)

        if include_scatter and not hide_spots and not getattr(self.navigator, "kymo_anchor_edit_mode", False):
            scatter_kwargs.update(s=15, edgecolors='black', linewidths=0.5)
            if point_alphas is not None:
                if isinstance(pts_colors, (list, tuple, np.ndarray)) and len(pts_colors) == len(xs_pts):
                    base_colors = pts_colors
                else:
                    base = scatter_kwargs.get("color", line_color)
                    base_colors = [base] * len(xs_pts)
                rgba = []
                for i, c in enumerate(base_colors):
                    try:
                        rgba.append(mcolors.to_rgba(c, point_alphas[i]))
                    except Exception:
                        rgba.append(mcolors.to_rgba(c))
                scatter_kwargs.pop("color", None)
                scatter_kwargs["c"] = rgba
                edge_rgba = []
                for i in range(len(xs_pts)):
                    try:
                        edge_rgba.append(mcolors.to_rgba("black", point_alphas[i]))
                    except Exception:
                        edge_rgba.append(mcolors.to_rgba("black"))
                scatter_kwargs["edgecolors"] = edge_rgba

            scatter = self.ax.scatter(
                xs_pts, ys_pts,
                picker=True,
                zorder=10,
                **scatter_kwargs
            )
            scatter.traj_idx = idx
            markers.append(scatter)
            clickables.append(scatter)

    def draw_selected_trajectory_on_movie(self, draw_idle=True):
        self.clear_movie_selected_trajectory_markers(draw_idle=False)
        if self.navigator is None:
            return
        overlay_mode = self.navigator.get_movie_traj_overlay_mode()
        if overlay_mode == "off":
            return

        selected_idx = self.navigator.trajectoryCanvas.table_widget.currentRow()
        trajectories = self.navigator.trajectoryCanvas.trajectories
        if selected_idx < 0 or selected_idx >= len(trajectories):
            return

        markers = []
        clickables = []
        labels = []
        self._draw_movie_trajectory(
            selected_idx,
            markers,
            clickables,
            labels,
            highlighted=True,
            include_scatter=True,
            fade_current_frame=True,
            current_ch=self._current_movie_channel(),
        )
        self.movie_selected_trajectory_markers = markers
        self._movie_selected_clickable_artists = clickables
        self._movie_selected_label_artists = labels
        self._refresh_movie_clickable_artists()
        self._refresh_movie_label_bboxes(base=False, selected=True)
        if draw_idle:
            self.ax.figure.canvas.draw_idle()

    def draw_trajectories_on_movie(self, draw_idle=True):
        self.clear_movie_trajectory_markers(draw_idle=False)
        if self.navigator is None:
            return

        overlay_mode = self.navigator.get_movie_traj_overlay_mode()
        if overlay_mode == "off":
            return

        current_ch = self._current_movie_channel()
        if overlay_mode == "all":
            markers = []
            clickables = []
            labels = []
            self._draw_movie_base_trajectories_batched(
                markers, labels, current_ch=current_ch
            )
            self.movie_trajectory_markers = markers
            self._movie_base_clickable_artists = clickables
            self._movie_base_label_artists = labels
            self._refresh_movie_clickable_artists()
            self._refresh_movie_label_bboxes(base=True, selected=False)

        self.draw_selected_trajectory_on_movie(draw_idle=False)
        self._movie_base_overlay_signature = (
            self._movie_base_overlay_cache_signature(
                len(self.navigator.trajectoryCanvas.trajectories)
            )
        )
        if draw_idle:
            self.ax.figure.canvas.draw_idle()

    def clear_movie_selected_trajectory_markers(self, draw_idle=False):
        self._remove_movie_artists(getattr(self, "movie_selected_trajectory_markers", []))
        self.movie_selected_trajectory_markers = []
        self._movie_selected_clickable_artists = []
        self._movie_selected_label_artists = []
        self._refresh_movie_clickable_artists()
        self._refresh_movie_label_bboxes(base=False, selected=True)
        if draw_idle:
            self.ax.figure.canvas.draw_idle()

    def clear_movie_trajectory_markers(self, draw_idle=False):
        self._remove_movie_artists(getattr(self, "movie_trajectory_markers", []))
        self._remove_movie_artists(getattr(self, "movie_selected_trajectory_markers", []))
        self.movie_trajectory_markers = []
        self.movie_selected_trajectory_markers = []
        self._movie_base_clickable_artists = []
        self._movie_selected_clickable_artists = []
        self._movie_base_label_artists = []
        self._movie_selected_label_artists = []
        self._refresh_movie_clickable_artists()
        self._movie_base_label_bboxes.clear()
        self._movie_selected_label_bboxes.clear()
        self._movie_base_label_bbox_signature = None
        self._movie_selected_label_bbox_signature = None
        self._movie_label_bboxes.clear()
        self._movie_base_cullable_collections = []
        self._invalidate_movie_base_culling()
        self._movie_base_overlay_signature = None
        if draw_idle:
            self.ax.figure.canvas.draw_idle()
    
    def remove_inset_circle(self):
        if hasattr(self, "inset_circle"):
            try:
                self.inset_circle.remove()
            except Exception as e:
                print("Error removing inset circle during invalidation:", e)
            self.inset_circle = None
