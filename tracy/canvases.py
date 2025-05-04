"""
All custom matplotlib-based Canvas classes:
    - ImageCanvas
    - MovieCanvas
    - KymoCanvas
    - IntensityCanvas
    - TrajectoryCanvas
"""
import numpy as np
import os
import pandas as pd
from PyQt5.QtCore import Qt, QTimer, QThread, QEvent
from PyQt5.QtWidgets import (QVBoxLayout, QApplication, QDialog,
                             QWidget, QFileDialog, QMessageBox, QTableWidget,
                             QTableWidgetItem, QMessageBox, QProgressDialog,
                             QHeaderView, QMenu, QInputDialog)
from PyQt5.QtGui import QPainter,QMouseEvent
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.patches import Circle, Rectangle
from matplotlib.ticker import MaxNLocator
from matplotlib.lines import Line2D
from matplotlib.text import Text
import matplotlib.cm as cm
import matplotlib.colors as mcolors
import scipy
from scipy.ndimage import map_coordinates
from matplotlib.gridspec import GridSpec
import time
import copy
import math
import json
import warnings
import re
from typing import Optional, List
from .roi_tools import is_point_near_roi, compute_roi_point
from .track_tools import calculate_velocities
from .canvas_tools import RecalcDialog, RecalcWorker, subpixel_crop

warnings.filterwarnings(
    "ignore",
    message=".*layout engine that is incompatible with subplots_adjust and/or tight_layout.*",
    category=UserWarning,
)
warnings.filterwarnings(
    "ignore",
    message=".*constrained_layout not applied because axes sizes collapsed to zero.*",
    category=UserWarning,
)
warnings.filterwarnings(
    "ignore",
    message=".*Attempting to set identical low and high xlims makes transformation singular.*"
)
warnings.filterwarnings(
    "ignore",
    message=".*Tight layout not applied\\. The left and right margins cannot be made large enough to accommodate all Axes decorations\\.*",
    category=UserWarning,
)
warnings.filterwarnings(
    "ignore",
    message=".*No artists with labels found to put in legend.*",
    category=UserWarning,
)

# -----------------------------
# Basic image canvas
# -----------------------------
class ImageCanvas(FigureCanvas):
    def __init__(self, parent=None):
        self.fig = Figure()
        self.ax = self.fig.add_subplot(111)
        self.fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        self.ax.axis("off")
        super().__init__(self.fig)
        self.setParent(parent)
        # self.setAttribute(Qt.WA_OpaquePaintEvent)
        # self.setAttribute(Qt.WA_NoSystemBackground)
        self.image = None

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setClipRect(self.rect())  # This restricts drawing to the visible area.
        super().paintEvent(event)

    def display_image(self, image, title=""):
        self.ax.clear()
        self.image = image
        self.ax.imshow(image, cmap="gray")
        self.ax.set_title(title)
        self.ax.axis('off')
        self.draw()

    def set_cmap(self, cmap):
        # if somebody’s already painted an image into `self._im` or `self.image`:
        im = getattr(self, "_im", None) or getattr(self, "image", None)
        # if your canvases store the AxesImage in `self._im`, use that:
        if hasattr(self, "_im") and self._im is not None:
            self._im.set_cmap(cmap)
            self.draw()

# -----------------------------
# KymoCanvas
# -----------------------------

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
        self.zoom_center = None  # in data coordinates
        self.manual_zoom = False
        self._update_pending = False
        self.x_marker = []
        self.manual_zoom = False
        self.color_by_column = None

        self._kymo_label_bboxes: Dict[Text, Bbox] = {}

        self.fig.patch.set_alpha(0)
        self.ax.patch.set_alpha(0)

        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocus()

        self.navigator = navigator
        
        self.mpl_connect("scroll_event", self.on_scroll)
        self.mpl_connect("button_press_event", self.on_mouse_press)
        self.mpl_connect("motion_notify_event", self.on_mouse_move)
        self.mpl_connect("button_release_event", self.on_mouse_release)

        self._ctrl_panning = False

    def set_color_by(self, column_name):
        for act in self.navigator._colorByActions:
            act.setChecked(act.text() == f"Color by {column_name}")
        self.color_by_column = column_name
        self.draw_trajectories_on_kymo()

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
        self.reset_canvas()
        p15, p99 = np.percentile(image, (15, 99))
        image = np.clip((image - p15) / (p99 - p15), 0, 1)
        image = (image * 255).astype(np.uint8)
        h, w = image.shape
        self.ax.set_xlim(0, w)
        self.ax.set_ylim(0, h)
        self.ax.set_aspect('auto')
        cmap = "gray_r" if getattr(self.navigator, "inverted_cmap", False) else "gray"
        self._im = self.ax.imshow(image, cmap=cmap)
        self.ax.axis('off')
        self.draw()
        self.image = image

        self.zoom_center = (w / 2.0, h / 2.0)
        widget_w = self.size().width() if self.size().width() > 0 else w
        widget_h = self.size().height() if self.size().height() > 0 else h
        self.scale = max(w / widget_w, h / widget_h)
        self.max_scale = self.scale
        self.update_view()

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

        # 3) grab a fresh background for your blit loop
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

        # clamp to any maximum you set
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
        # schedule a single zoom/pan update per event loop
        if not self._update_pending:
            self._update_pending = True
            QTimer.singleShot(0, self._perform_throttled_update)

    def _perform_throttled_update(self):
        """
        Perform the zoom/pan update in a throttled manner.
        """
        # full view update then clear the pending flag
        self.update_view()
        self._update_pending = False

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
            self.max_scale = max(w / widget_w, h / widget_h)
        # When resizing, update the view so that the zoom_center and scale are maintained.
        self.update_view()

    def overlay_spot_center(self, x, y, size=12, color='red'):
        """
        Draw a semi‑transparent circle marker at (x, y) using blitting for performance.
        """
        # Ensure any previous marker is removed
        if getattr(self, "_marker", None) is not None:
            try:
                self._marker.remove()
            except Exception:
                pass

        # If we have a cached background, use blitting
        if hasattr(self, "_bg") and self._bg is not None:
            # Restore the background (clears old marker)
            self.fig.canvas.restore_region(self._bg)
            self.fig.canvas.blit(self.ax.bbox)

            # Create and draw the new marker
            radius = size / 2.0
            circ = Circle((x, y),
                          radius=radius,
                          edgecolor=color,
                          facecolor=color,
                          alpha=0.6,
                          linewidth=2,
                          zorder=6)
            self.ax.add_patch(circ)
            self._marker = circ

            # Draw only the new marker artist and blit it
            self.ax.draw_artist(circ)
            self.fig.canvas.blit(self.ax.bbox)
        else:
            # Fallback: clear any old marker visually, then blit new one
            if hasattr(self, "_bg") and self._bg is not None:
                # Restore the clean background (with static overlays but no marker)
                self.fig.canvas.restore_region(self._bg)
            else:
                # No background snapshot yet: do a full redraw to clear marker
                self.draw()
                # Capture a fresh background for future blits
                self._bg = self.copy_from_bbox(self.ax.bbox)

            # Create and add the new marker patch
            radius = size / 2.0
            circ = Circle((x, y),
                          radius=radius,
                          edgecolor=color,
                          facecolor=color,
                          alpha=0.6,
                          linewidth=2,
                          zorder=6)
            self.ax.add_patch(circ)
            self._marker = circ

            # Draw only the new marker artist and blit it
            self.ax.draw_artist(circ)
            self.fig.canvas.blit(self.ax.bbox)

    def temporary_circle(self, x, y, size=12, color='red'):
        """
        Add a transient marker circle at (x, y); returns a list of patches so
        callers can manage them individually.
        """
        if x is None or y is None:
            print("Warning: x or y is None in temporary_circle, skipping marker addition.")
            return None

        radius = size / 2.0
        circ = Circle(
            (x, y),
            radius=radius,
            edgecolor=color,
            facecolor=color,
            alpha=0.6,
            linewidth=2
        )
        self.ax.add_patch(circ)
        self.draw()
        return [circ]

    def draw_trajectories_on_kymo(self):

        kymo_name = self.navigator.kymoCombo.currentText()
        info = self.navigator.kymo_roi_map.get(kymo_name, {})
        current_kymo_ch = info.get("channel")
        self._kymo_label_bboxes.clear()

        # 1) clear any existing overlays
        self.clear_kymo_trajectory_markers()

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
            face = "#7da1ff" if is_hl else "black"
            alpha_lbl = 0.8 if is_hl else 0.6

            main_color = "magenta"
            if self.color_by_column is not None:
                val = traj["custom_fields"].get(self.color_by_column, "")
                main_color = "yellow" if val else "magenta"

            # build display points
            frames = traj["frames"]
            orig = traj["original_coords"]
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

            # 5b) magenta line through spot centers (with NaN breaks)
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

            line, = ax.plot(
                xs_pts, ys_pts,
                linestyle='-', color=main_color,
                linewidth=1.1, alpha=0.8, zorder=3
            )
            markers.append(line)

            scatter = ax.scatter(
                xs_pts, ys_pts,
                s=1, marker='o',
                color=main_color, alpha=0.8,
                linewidth=1.1, zorder=4
            )
            markers.append(scatter)

            # 5c) optional halo behind
            if is_hl and halo_lw:
                halo, = ax.plot(
                    xs_pts, ys_pts,
                    linestyle='-', color="#7da1ff",
                    linewidth=halo_lw, alpha=0.2, zorder=1
                )
                markers.append(halo)

            # 5d) annotate A/B
            # compute pixel‐space offsets once
            dispA = ax.transData.transform((x0, y0))
            dispB = ax.transData.transform((x1, y1))
            v = dispB - dispA
            norm = np.hypot(*v)
            u = v / norm if norm else np.array([1.0, 0.0])
            offset = 10
            for (cx, cy, suf), sign in [((x0, y0, 'A'), -1), ((x1, y1, 'B'), +1)]:
                dx, dy = u * (offset * sign)
                lbl = ax.annotate(
                    f"{traj_label}{suf}",
                    xy=(cx, cy),
                    xytext=(dx, dy),
                    textcoords='offset pixels',
                    ha='center', va='center',
                    color='white', fontsize=8,
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
        canvas.draw()
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

        if hasattr(self, 'x_marker') and self.x_marker is not None:
            markers = self.x_marker
            # Normalize to a list
            if not isinstance(markers, (list, tuple)):
                markers = [markers]
            for marker in markers:
                try:
                    marker.remove()
                except Exception:
                    pass
            # Reset the attribute
            self.x_marker = None

# -----------------------------
# MovieCanvas
# -----------------------------
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
        self._inset_update_pending = False
        self._last_inset_params = None

        # New attributes for zooming:
        self.scale = 1.0  # Data units per pixel (uniform in x and y)
        self.zoom_center = None  # in data (image) coordinates

        # Connect mouse events.
        self.mpl_connect("scroll_event", self.on_scroll)
        self.mpl_connect("button_press_event", self.on_mouse_press)
        self.mpl_connect("motion_notify_event", self.on_mouse_move)
        self.mpl_connect("button_release_event", self.on_mouse_release)

        self._manual_x_marker_active = False  # flag for key-controlled marker
        self._manual_x_marker_pos = None      # will hold [x, y]
        self._manual_x_marker_artist = None   # store the drawn marker

        self.sum_mode = False
        self.sum_frame_cache = None

        self._norm_slider_settings = None  # to store normal mode slider settings
        self._sum_slider_settings = None 

        self.tempRoiLine = None
        self._roi_bg = None
        self.roiAddMode = False
        self.roiPoints = [] 
        # self.x_markers = []

        self.navigator = navigator

        self.last_fitted_center = None
        self.last_fitted_sigma = None
        self.last_intensity_value = None

        self.manual_zoom = False

        self._ctrl_panning = False
        self._last_pan = 0.0

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
            return

        # — finish real middle‐button pan —
        if event.button() == Qt.MiddleButton:
            self._is_panning = False
            super().mouseReleaseEvent(event)
            # do a blocking full draw then snapshot the ROI/marker background
            canvas = self.figure.canvas
            self.draw()                             # synchronous full redraw
            self._roi_bbox = self.ax.bbox           # cache axes bbox
            self._roi_bg   = canvas.copy_from_bbox(self._roi_bbox)
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
        self.image = image
        h, w = image.shape
        # Set the image extent to the full image.
        extent = (-0.5, w - 0.5, -0.5, h - 0.5)
        # Initialize zoom_center to the image center if not set yet.
        if self.zoom_center is None:
            self.zoom_center = (w/2, h/2)
        # Set an initial scale such that the entire image is visible.
        # We choose scale so that the data width equals the widget width, unless the widget is not yet sized.
        widget_w = self.width() if self.width() > 0 else w
        widget_h = self.height() if self.height() > 0 else h
        # To show the entire image without zooming, choose the maximum scale needed to cover both dimensions.
        self.scale = max(w / widget_w, h / widget_h)
        self.max_scale = self.scale
        
        # Draw or update the image.
        if self._im is None:
            self.ax.clear()
            cmap = "gray_r" if getattr(self.navigator, "inverted_cmap", False) else "gray"
            self._im = self.ax.imshow(image, cmap=cmap, vmin=self._vmin, vmax=self._vmax,
                                       origin='lower', extent=extent)
            self.ax.axis("off")
            self.draw()
        else:
            self._im.set_data(image)
            self._im.set_extent(extent)
            self.draw()
            
        # Adjust the view limits based on the new scale and current zoom_center.
        QTimer.singleShot(1, self.update_view)

    def update_image_data(self, image):
        """Update only the image data without changing the current axes limits."""
        if image is None:
            return
        self.image = image
        if self._im is None:
            # If the image has never been drawn, fallback to display_image()
            self.display_image(image)
        else:
            self._im.set_data(image)
            self.draw()

    def update_view(self):
        if self.image is None or self.zoom_center is None:
            return

        # 1) compute new limits
        widget_w = self.width()
        widget_h = self.height()
        view_w = widget_w * self.scale
        view_h = widget_h * self.scale
        cx, cy = self.zoom_center
        self.ax.set_xlim(cx - view_w/2, cx + view_w/2)
        self.ax.set_ylim(cy - view_h/2, cy + view_h/2)

        # 2) **blocking** full draw
        self.draw()

        # 3) **then** grab a fresh clean background  
        #    (no animated artists in place because you haven’t drawn them yet)
        canvas = self.figure.canvas
        self._bg       = canvas.copy_from_bbox(self.ax.bbox)
        self._roi_bbox = self.ax.bbox
        self._roi_bg   = canvas.copy_from_bbox(self._roi_bbox)

        # reset your manual‐zoom flag
        self.manual_zoom = False

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
        base = 1.15
        if event.button == 'up':
            new_scale = old_scale / base
        else:
            new_scale = old_scale * base

        # clamp to your max if you have one
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
        # schedule a single zoom/pan update per event loop
        if not self._update_pending:
            self._update_pending = True
            QTimer.singleShot(0, self._perform_throttled_update)

    def _perform_throttled_update(self):
        """
        Perform the zoom/pan update in a throttled manner.
        """
        # full view update then clear the pending flag
        self.update_view()
        self._update_pending = False
        
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

        # schedule a single, throttled redraw
        if not self._update_pending:
            self._update_pending = True
            QTimer.singleShot(0, self._perform_throttled_update)

    def on_mouse_release(self, event):
        # this is the Matplotlib MouseEvent handler — do NOT call the Qt super()
        if event.button == 2 and event.inaxes == self.ax:
            # finish the pan: full redraw + fresh blit‐background
            self.draw()
            canvas = self.figure.canvas
            self._roi_bbox = self.ax.bbox
            self._roi_bg   = canvas.copy_from_bbox(self._roi_bbox)
            # also update the general bg used for blit‐markers
            self._bg = canvas.copy_from_bbox(self.ax.bbox)
            self.manual_zoom = False

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
            self.max_scale = max(w / widget_w, h / widget_h)

        # 3) redraw using the existing scale & center
        self.update_view()
        
    def update_inset(self, image, center, crop_size, zoom_factor=2,
                    fitted_center=None, fitted_sigma=None,
                    fitted_peak=None, offset=None, intensity_value=None):
        # store params
        self._last_inset_params = (image, center, crop_size, zoom_factor,
                                fitted_center, fitted_sigma,
                                fitted_peak, offset, intensity_value)
        
        widget = self.navigator.zoomInsetWidget
        fig = widget.figure

        frame = self.navigator.zoomInsetFrame
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

            # hook enter/leave
            cid = fig.canvas
            self._enter_cid = cid.mpl_connect('axes_enter_event', self._on_inset_enter)
            self._leave_cid = cid.mpl_connect('axes_leave_event', self._on_inset_leave)
            self._scroll3d_cid = cid.mpl_connect('scroll_event', self._on_inset_scroll)

        if not getattr(self, '_inset_update_pending', False):
            self._inset_update_pending = True
            # if the inset is currently visible, fire ASAP; otherwise wait 400 ms
            delay = 0 if self.navigator.zoomInsetFrame.isVisible() else 400
            QTimer.singleShot(delay, self._throttled_update_inset)

    def _on_inset_enter(self, event):
        if self.navigator.looping:
            self.navigator.stoploop()
        if self.navigator.sumBtn.isChecked():
            self.navigator.sumBtn.setChecked(False)
        if event.inaxes is self.inset_ax3d or event.inaxes is self.navigator.zoomInsetWidget.ax:
            # hide 2D
            self.navigator.zoomInsetWidget.ax.set_visible(False)
            # show 3D
            self.inset_ax3d.set_visible(True)
            self._draw_threed_inset()

    def _clear_threed_inset(self):
        """Erase the 3D inset and hide its frame."""
        ax3d = self.inset_ax3d
        ax3d.cla()                # clear contents
        ax3d.set_axis_off()       # hide axes
        self.navigator.zoomInsetFrame.setVisible(False)
        self.navigator.zoomInsetWidget.draw()

    def _draw_threed_inset(self):

        """
        The heavy‐lifting routine: crops, zooms, builds the 3D bars +
        Gaussian cap, and then makes everything visible.
        """
        params = self._last_inset_params
        if not params or len(params) != 9:
            return
        (image, center, crop_size, zoom_factor,
        fitted_center, fitted_sigma,
        fitted_peak, offset, intensity_value) = params
        

        # sanity
        if image is None or center is None or np.isnan(center[0]) or np.isnan(center[1]):
            return

        # --- crop & zoom ---
        half = crop_size/2.0
        cx, cy = center
        x1, x2 = cx-half, cx+half
        y1, y2 = cy-half, cy+half
        out_shape = (int(round(y2-y1)), int(round(x2-x1)))
        cropped = subpixel_crop(image, x1, x2, y1, y2, out_shape)
        zoomed = scipy.ndimage.zoom(cropped, zoom_factor, order=0)

        # build/reset axes
        ax3d = self.inset_ax3d
        ax3d.cla()

        # grid & bars
        h,w = zoomed.shape
        x = np.linspace(x1, x2, w);  y = np.linspace(y1, y2, h)
        X,Y = np.meshgrid(x,y)
        xpos, ypos = X.ravel(), Y.ravel()
        # decide on a norm based on the data range:
        dz_raw = zoomed.ravel()
        dz = dz_raw - (float(offset) if offset is not None else 0.0)
        dx,dy = (x2-x1)/w, (y2-y1)/h

        # determine bar bases & heights
        z0     = np.minimum(dz, 0.0)
        height = np.abs(dz)

        dx = (x2 - x1) / w
        dy = (y2 - y1) / h

        # darkest (black) at min(dz), lightest (white) at max(dz)
        norm = mcolors.Normalize(vmin=dz.min(), vmax=dz.max())
        cmap_name = "gray_r" if self.navigator.inverted_cmap else "gray"
        cmap = cm.get_cmap(cmap_name)
        cols = cmap(norm(dz))
        cols[:, 3] = 1  # adjust alpha if you like

        bars = ax3d.bar3d(
            xpos, ypos, z0,
            dx, dy, height,
            color=cols,
            edgecolor='none',
            linewidth=0,
            shade=False
        )
        bars.set_sort_zpos(0.0)

        # Gaussian cap
        if fitted_center is not None and fitted_sigma is not None and fitted_peak is not None:
            x0,y0 = fitted_center;  A = fitted_peak;  σ = fitted_sigma
            G = A*np.exp(-(((X-x0)**2+(Y-y0)**2)/(2*σ**2)))
            z_lift = dz.max()*0 # off for now
            G += z_lift
            # surf = ax3d.plot_surface(X, Y, G, color='magenta', alpha=0.3,
            #                         shade=False, rstride=2, cstride=2, linewidth=0)
            # surf.set_sort_zpos(float(G.max()))
            wf = ax3d.plot_wireframe(X, Y, G, color='magenta', alpha=0.3,
                                    rstride=2, cstride=2, linewidth=1.2)
            wf.set_zorder(10)
            z_top = G.max()
        else:
            z_top = dz.max()

        # style & zoom
        ax3d.view_init(elev=60, azim=275)
        ax3d.set_axis_off()
        ax3d.set_facecolor((0,0,0,0));  ax3d.set_xlim(x1,x2);  ax3d.set_ylim(y1,y2)
        if not np.isfinite(z_top) or z_top <= 0:
            # fallback to a small positive range, or let Matplotlib autoscale
            try:
                ax3d.autoscale(z=True)
            except Exception:
                pass
        else:
            ax3d.set_zlim(0, z_top)
        for a in (ax3d.xaxis, ax3d.yaxis, ax3d.zaxis):
            a.pane.fill = False;  a.pane.set_edgecolor('none')
        ax3d.grid(False)

        # finally make it visible
        self.navigator.zoomInsetWidget.draw()
        self.navigator.zoomInsetFrame.setVisible(True)

        # update the labels
        if fitted_center is not None:
            self.navigator.zoomInsetLabel.setText(f"{fitted_center[0]:.2f}, {fitted_center[1]:.2f}")
        else:
            self.navigator.zoomInsetLabel.setText("")
        if fitted_center is not None and intensity_value is not None:
            self.navigator.zoomInsetIntensityLabel.setText(f"{intensity_value:.2f}")
        else:
            self.navigator.zoomInsetIntensityLabel.setText("")


    def _on_inset_scroll(self, event):
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

    def _on_inset_leave(self, event):
        if event.inaxes in (self.inset_ax3d, self.navigator.zoomInsetWidget.ax):
            frame = self.navigator.zoomInsetFrame
            w0, h0 = self._default_inset_size
            geom = frame.geometry()
            new_x = geom.x() + geom.width() - w0

            frame.move(new_x, geom.y())
            frame.resize(w0, h0)
            frame.layout().invalidate()
            frame.layout().activate()

            # existing visibility logic
            self.inset_ax3d.set_visible(False)
            self.navigator.zoomInsetWidget.ax.set_visible(True)
            self._throttled_update_inset()

    def _throttled_update_inset(self):
        """Perform the heavy inset update using the most recent parameters."""
        self._inset_update_pending = False
        params = self._last_inset_params
        if not params or not isinstance(params, tuple) or len(params) !=9:
            return
        
        image, center, crop_size, zoom_factor, fitted_center, fitted_sigma, fitted_peak, offset, intensity_value = self._last_inset_params

        if image is None:
            return
        if center is None or np.isnan(center[0]) or np.isnan(center[1]):
            print("Warning: update_inset received invalid center:", center)
            return
        half = crop_size / 2.0
        x_center, y_center = center[0], center[1]
        x1, x2 = x_center - half, x_center + half
        y1, y2 = y_center - half, y_center + half
        output_shape = (int(round(y2 - y1)), int(round(x2 - x1)))
        cropped = subpixel_crop(image, x1, x2, y1, y2, output_shape) 
        zoomed = scipy.ndimage.zoom(cropped, zoom_factor, order=0)
        self.source_image = image
        self.zoom_extent = (x1, x2, y1, y2)
        
        if hasattr(self.navigator, "zoomInsetWidget"):
            # Update the inset widget’s axes.
            inset_ax = self.navigator.zoomInsetWidget.ax
            inset_ax.clear()
            self.navigator.zoomInsetWidget._im_inset = inset_ax.imshow(
                zoomed,
                cmap=("gray_r" if self.navigator.inverted_cmap else "gray"),
                origin='lower',
                extent=[x1, x2, y1, y2]
            )
            inset_ax.set_xticks([])
            inset_ax.set_yticks([])
            inset_ax.axis('off')

            # Update the overlay text; center it horizontally.
            if fitted_center is not None:
                fc_x, fc_y = fitted_center
                center_text = f"{fc_x:.2f}, {fc_y:.2f}"
            else:
                center_text = ""
            self.navigator.zoomInsetLabel.setText(center_text)
            # Optionally, draw magenta circles if fit parameters are provided.
            if fitted_center is not None and fitted_sigma is not None and intensity_value is not None:
                self.inset_circle = Circle(fitted_center, radius=fitted_sigma * 2, 
                                edgecolor='magenta', facecolor='none', linewidth=1.5, alpha=1)
                inset_ax.add_patch(self.inset_circle)

            if fitted_center is not None and intensity_value is not None:
                self.navigator.zoomInsetIntensityLabel.setText(f"{intensity_value:.2f}")
            else:
                self.navigator.zoomInsetIntensityLabel.setText("")
            self.navigator.zoomInsetWidget.draw()
            # Finally, show the whole zoom inset frame.
            self.navigator.zoomInsetFrame.setVisible(True)
        else:
            self.ax.clear()
            cmap = "gray_r" if getattr(self.navigator, "inverted_cmap", False) else "gray"
            self.ax.imshow(zoomed, cmap=cmap, origin='lower', extent=[x1, x2, y1, y2])
            self.ax.axis('off')

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
                if hasattr(self.navigator, "movieChannelCombo") and self.navigator.movieChannelCombo.isEnabled():
                    if channel_override is not None:
                        channel_index = channel_override
                    else:
                        channel_index = int(self.navigator.movieChannelCombo.currentText()) - 1
                else:
                    channel_index = 0
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
            self.tempRoiLine, = self.ax.plot(xs, ys, '--', linewidth=1.5, color='#4CAF50')
        else:
            # restore the clean background
            canvas.restore_region(self._roi_bg)
            # update the line
            self.tempRoiLine.set_data(xs, ys)
            # draw just that artist
            self.ax.draw_artist(self.tempRoiLine)
            # blit only the axes region
            canvas.blit(self._roi_bbox)

    def finalize_roi(self):
        # Make sure we have at least two pointsf
        if not self.roiPoints or len(self.roiPoints) < 2:
            print("Not enough points to finalize ROI.")
            return
        
        #print("ROI points:", self.roiPoints)

        # Build the ROI dictionary using all collected points.
        # We use the keys 'x' and 'y' (as expected by your conversion function)
        # and also store the full list as 'points' for any later processing.
        roi = {
            "type": "line",  # or "segmented_line" if you prefer to be explicit
            "x": [pt[0] for pt in self.roiPoints],
            "y": [pt[1] for pt in self.roiPoints],
            "points": self.roiPoints.copy()
        }

        # Generate the kymograph from the full ROI.
        kymo = self.generate_kymograph(roi)

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
        self.navigator.roiCombo.setCurrentText(name)
        self.navigator.update_roilist_visibility()

        if self.navigator.movie.ndim == 4:
            n_chan = self.navigator.movie.shape[self.navigator._channel_axis]
        else:
            n_chan = 1

        for ch in range(n_chan):
            kymo = self.generate_kymograph(roi, channel_override=ch)
            kymo_name = f"C{ch+1}-{name}"
            self.navigator.kymographs[kymo_name] = kymo
            self.navigator.kymo_roi_map[kymo_name] = {
                "roi":      name,
                "channel":  ch+1,
                "orphaned": False
            }

            self.navigator.last_kymo_by_channel[ch+1] = kymo_name

        self.navigator.update_kymo_list_for_channel()
        self.navigator.kymoCombo.setEnabled(True)


        # Clear the temporary ROI markers and the stored points.
        self.roiPoints = []
        if self.tempRoiLine is not None:
            try:
                self.tempRoiLine.remove()
            except Exception:
                pass
            self.tempRoiLine = None

        self.navigator.update_kymo_visibility()
        self.navigator.kymoCanvas.draw_trajectories_on_kymo()
        self.draw()

    def clear_temporary_roi_markers(self):
        # Clear any temporary ROI dotted line.
        if hasattr(self, 'tempRoiLine') and self.tempRoiLine is not None:
            try:
                self.tempRoiLine.remove()
            except Exception:
                pass
            self.tempRoiLine = None
        # Now remove all stored x markers.
        # markers = getattr(self, 'x_markers', []) or []
        # for marker in markers:
        #     try: marker.remove()
        #     except: pass
        # # Reset the list.
        # self.x_markers = []
        self.draw()

    def display_sum_frame(self):
        if self.navigator is None or self.navigator.movie is None:
            return

        # If a sum frame is already cached, use it.
        if self.sum_frame_cache is not None:
            sum_frame = self.sum_frame_cache
        else:
            movie = self.navigator.movie

            # For multi-channel movies.
            if movie.ndim == 4:
                channel_axis = self.navigator._channel_axis
                try:
                    current_channel = int(self.navigator.movieChannelCombo.currentText()) - 1
                except Exception:
                    current_channel = 0  # default to channel 0 if something goes wrong
                # Build an index for the selected channel.
                idx = [0] * movie.ndim
                idx[0] = slice(None)  # all frames
                for ax in range(1, movie.ndim):
                    idx[ax] = current_channel if ax == channel_axis else slice(None)
                channel_movie = movie[tuple(idx)]
                sum_frame = np.max(channel_movie, axis=0)
            
            # For single-channel (3D) movies.
            elif movie.ndim == 3:
                # Determine if the first axis is channel or time.
                # If the size along axis 0 is small (<=4), then assume channels.
                if movie.shape[0] <= 4:
                    idx = [0]  # select the first (or only) channel
                    sum_frame = movie[tuple(idx)]
                else:
                    sum_frame = np.max(movie, axis=0)
            else:
                sum_frame = movie

            self.sum_frame_cache = sum_frame

        self.image = sum_frame
        if self._im is None:
            self.ax.clear()
            self._im = self.ax.imshow(sum_frame, cmap="gray", origin='lower')
            self.ax.axis("off")
            self.draw()
        else:
            self._im.set_data(sum_frame)
            self.draw()

    def clear_sum_cache(self):
        # Call this when a new movie is loaded.
        self.sum_frame_cache = None

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

    def overlay_rectangle(self, center_x, center_y, size, frame_number=None, color='#7da1ff'):
        # Remove any existing rectangle
        if hasattr(self, "rect_overlay") and self.rect_overlay is not None:
            try:    self.rect_overlay.remove()
            except: pass
            self.rect_overlay = None

        # Draw the rectangle
        lower_left = (center_x - size/2, center_y - size/2)
        rect = Rectangle(lower_left, size, size,
                        edgecolor=color, facecolor='none', linewidth=1.5)
        self.ax.add_patch(rect)
        self.rect_overlay = rect

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

    def draw_manual_x_marker(self):
        """Draw a translucent circle at the current manual position."""
        # Remove any existing manual marker
        if getattr(self, "_manual_x_marker_artist", None) is not None:
            try:
                self._manual_x_marker_artist.remove()
            except Exception:
                pass

        # Draw a semi‑transparent circle
        x, y = self._manual_x_marker_pos
        radius = 3  # adjust as desired
        circ = Circle(
            (x, y),
            radius=radius,
            edgecolor='#7da1ff',
            facecolor='#7da1ff',
            alpha=0.6,
            linewidth=1.5
        )
        self._manual_x_marker_artist = circ
        self.ax.add_patch(circ)

    def clear_manual_x_marker(self):
        """Remove the manual marker circle from the canvas."""
        if getattr(self, "_manual_x_marker_artist", None) is not None:
            try:
                self._manual_x_marker_artist.remove()
            except Exception:
                pass
            self._manual_x_marker_artist = None

    def clear_x_marker(self):
        """Remove the blue overlay circle from the movie canvas, if present."""
        if getattr(self, "_x_marker", None) is not None:
            try:
                self._x_marker.remove()
            except Exception:
                pass
            self._x_marker = None

    def add_gaussian_circle(self, fitted_center, fitted_sigma):
        self.gaussian_circle = Circle(
                fitted_center,
                radius=2 * fitted_sigma,
                edgecolor='magenta',
                facecolor='none',
                linewidth=1.5
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

    def draw_trajectories_on_movie(self):
        # Clear any existing movie canvas trajectory markers.
        self.clear_movie_trajectory_markers()
        
        idx = self.navigator.trajectoryCanvas.table_widget.currentRow()

        if idx < 0:
            return
        
        traj = self.navigator.trajectoryCanvas.trajectories[idx]

        try:
            current_ch = int(self.navigator.movieChannelCombo.currentText())
        except ValueError:
            current_ch = None

        traj_ch = traj.get("channel", None)
        # print(idx, traj_ch, current_ch)
        if traj_ch != current_ch and traj_ch is not None:
            # not the right channel → leave canvas clear
            return

        traj_label = traj.get("file_index") if traj.get("file_index") else str(traj["trajectory_number"])
        
        # Draw dotted line for search centers.
        original_coords = traj.get('original_coords', [])
        if original_coords:
            xs = [pt[0] for pt in original_coords]
            ys = [pt[1] for pt in original_coords]
            dotted_line, = self.ax.plot(
                xs, ys, color='#7da1ff', linestyle='--', linewidth=1.5, zorder=1,
                solid_capstyle='round',
                dash_capstyle='round')
            self.movie_trajectory_markers.append(dotted_line)
            if xs[0] < xs[-1]:
                start_offset = (-10, 10)
                end_offset   = ( 10, 10)
            else:
                start_offset = ( 10, 10)
                end_offset   = (-10, 10)
            start_label = self.ax.annotate(
                f"{traj_label}A",
                xy=(xs[0], ys[0]-0.1),
                xytext=start_offset,
                textcoords="offset points",
                color='white',
                fontsize=10,
                ha="center",
                va="center",
                bbox=dict(boxstyle='circle,pad=0.3', facecolor='#7da1ff', alpha=0.7, linewidth=1.5),
                zorder=3
            )
            end_label = self.ax.annotate(
                f"{traj_label}B",
                xy=(xs[-1], ys[-1]-0.1),
                xytext=end_offset,
                textcoords="offset points",
                color='white',
                fontsize=10,
                ha="center",
                va="center",
                bbox=dict(boxstyle='circle,pad=0.3', facecolor='#7da1ff', alpha=0.7, linewidth=1.5),
                zorder=2
            ) 
            self.movie_trajectory_markers.extend([start_label, end_label])
        
        # extract your x/y lists, turning None → np.nan
        xs = [pt[0] if pt is not None else np.nan for pt in traj['spot_centers']]
        ys = [pt[1] if pt is not None else np.nan for pt in traj['spot_centers']]

        # 2) overlay a  circle at eac
        scatter = self.ax.scatter(
            xs, ys,
            s=1,
            color='magenta',
            alpha=0.8,
            lw=1.5,
            zorder=5)
        
        self.movie_trajectory_markers.append(scatter)

        # draws lines
        line, = self.ax.plot(
            xs, ys,
            linestyle='-',
            color='magenta',
            linewidth=1.5,    # same stroke for line
            markersize=4,     # tweak diameter until it feels right
            markeredgewidth=1.5,
            markeredgecolor='magenta',
            markerfacecolor='magenta',
            zorder=4
        )

        self.movie_trajectory_markers.append(line)


    def clear_movie_trajectory_markers(self):
        if hasattr(self, "movie_trajectory_markers"):
            for marker in self.movie_trajectory_markers:
                try:
                    marker.remove()
                except Exception:
                    pass
            self.movie_trajectory_markers = []
        else:
            self.movie_trajectory_markers = []
    
    def remove_inset_circle(self):
        if hasattr(self, "inset_circle"):
            try:
                self.inset_circle.remove()
            except Exception as e:
                print("Error removing inset circle during invalidation:", e)
            self.inset_circle = None

# -----------------------------
# IntensityCanvas: for the integrated intensity plot
# -----------------------------
class IntensityCanvas(FigureCanvas):
    def __init__(self, parent=None, navigator=None):
        # 1) Create the Figure *before* calling super()
        self.fig = Figure(figsize=(5.5, 4), constrained_layout=False)
        
        # 2) Now initialize the FigureCanvas with that figure
        super().__init__(self.fig)
        self.setParent(parent)
        self.navigator = navigator
        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocus()

        # 3) Build your two‐panel layout
        gs = GridSpec(2, 1, height_ratios=[0.1, 0.9], figure=self.fig)
        self.ax_top = self.fig.add_subplot(gs[0])
        self.ax_bottom = self.fig.add_subplot(gs[1], sharex=self.ax_top)
        self.ax_top.axis("off")
        self.ax_bottom.axis("off")

        self.fig.patch.set_alpha(0)
        self.ax_bottom.patch.set_alpha(0)
        self.ax_bottom.set_facecolor('none')

        # 4) Create the highlight artists *once* and leave them invisible
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

        # 5) Draw *once* to populate the renderer and axes
        self.draw()
        # 6) Capture the background for blitting
        self._background = self.copy_from_bbox(self.fig.bbox)

        # 7) Hook draw_event so we re‐capture on any full redraw (e.g. resize)
        self.mpl_connect("draw_event", self._on_full_draw)
        # 8) Hook pick_event for your scatter dots
        self.mpl_connect("pick_event", self.on_pick_event)

        self.current_index = 0
        self.scatter_obj_top = None
        self.scatter_obj_bottom = None
        self.point_highlighted=False

    def plot_intensity(self, frames, intensities, avg_intensity=None, median_intensity=None,
                    colors=None, max_frame=None):
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
        
        bar_height = 0.2
        y0 = 0.5 - bar_height/2
        radius = bar_height / 2.0

        n = len(frames_display)
        if n == 0:
            return

        for i, frame in enumerate(frames_display):
            # pick colour
            col = colors[i] if colors and i < len(colors) else "magenta"

            # compute segment width
            if i < n - 1:
                width = frames_display[i+1] - frame
            else:
                width = 1

            if i == 0:
                # Leftmost: round only the left edge
                # 1) draw a half-circle at the left
                circ = Circle(
                    (frame + radius, y0 + radius),
                    radius,
                    facecolor=col,
                    edgecolor="none"
                )
                # 2) draw the rest as a rectangle
                rect = Rectangle(
                    (frame + radius, y0),
                    width - radius,
                    bar_height,
                    facecolor=col,
                    edgecolor="none"
                )
                self.ax_top.add_patch(circ)
                self.ax_top.add_patch(rect)

            elif i == n - 1:
                # Rightmost: round only the right edge
                # 1) draw the main rectangle
                rect = Rectangle(
                    (frame, y0),
                    width - radius,
                    bar_height,
                    facecolor=col,
                    edgecolor="none"
                )
                # 2) draw a half-circle at the right
                circ = Circle(
                    (frame + width - radius, y0 + radius),
                    radius,
                    facecolor=col,
                    edgecolor="none"
                )
                self.ax_top.add_patch(rect)
                self.ax_top.add_patch(circ)

            else:
                # Middle segments: plain rectangles
                rect = Rectangle(
                    (frame, y0),
                    width,
                    bar_height,
                    facecolor=col,
                    edgecolor="none"
                )
                self.ax_top.add_patch(rect)

        self.ax_bottom.set_ylabel("Intensity (AU)", fontsize=12)
        self.ax_bottom.set_xlabel("Frame", fontsize=12)
        self.ax_bottom.tick_params(axis='both', which='major', labelsize=12)
        if colors is not None:
            self.scatter_obj_bottom = self.ax_bottom.scatter(frames_display, intensities,s=20, c=colors, picker=True)
        else:
            self.scatter_obj_bottom = self.ax_bottom.scatter(frames_display, intensities,s=20, c='magenta', picker=True)
        try:
            self.ax_bottom.axhline(avg_intensity, color='grey', linestyle='--', linewidth=1.5,
                solid_capstyle='round',
                dash_capstyle='round',
                label=f"Avg: {avg_intensity:.2f}")
            self.ax_bottom.axhline(median_intensity, color='magenta', linestyle='--', linewidth=1.5,
                solid_capstyle='round',
                dash_capstyle='round',
                label=f"Med: {median_intensity:.2f}")
        except np.linalg.LinAlgError as e: 
            pass
            #print("Warning: could not plot horizontal line due to singular transform matrix:", e)
        #self.ax_bottom.legend(loc="upper right", fontsize=12, frameon=True)
        self.ax_bottom.set_xlim(min(frames_display), max(frames_display))
        self.ax_bottom.xaxis.set_major_locator(MaxNLocator(integer=True))

        legend = self.ax_bottom.legend(
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

        if colors is not None:
            valid_intensities = [val for val, col in zip(intensities, colors) if col != 'grey']
            if valid_intensities:
                ymin = min(valid_intensities)
                ymax = max(valid_intensities)
                margin = 0.1 * (ymax - ymin) if ymax > ymin else 1
                self.ax_bottom.set_ylim(ymin - margin, ymax + margin)
                self.highlight_current_point()
        
        try:
            self.fig.tight_layout(
                pad=0.1,      # space around the whole figure
                w_pad=0.05,   # horizontal padding between subplots
                h_pad=0.05,   # vertical padding between subplots
                rect=[0.02, 0.02, 0.98, 0.98]  # left, bottom, right, top fractions
            )
        except np.linalg.LinAlgError as e:
            pass
            #print("Warning: could not plot run tight_layout on IntensityCanvas:", e)

        self.fig.canvas.draw()                       # synchronous full draw
        self._background = self.copy_from_bbox(self.fig.bbox)
         
    def on_pick_event(self, event):
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
        # force a full redraw so axes, legend, etc. get laid out
        self.fig.canvas.draw_idle()
        # now replay the highlight exactly as before
        if self.point_highlighted:
            self.highlight_current_point(self._last_highlight_override)

    def clear_highlight(self):
        self.highlight_marker_bottom.set_visible(False)
        self.highlight_marker_top.set_visible(False)
        self.top_vline.set_visible(False)

        # restore background and blit clean
        self.fig.canvas.restore_region(self._background)
        self.fig.canvas.blit(self.fig.bbox)

# -----------------------------
# TrajectoryCanvas
# -----------------------------

class TrajectoryCanvas(QWidget):
    def __init__(self, parent=None, kymo_canvas=None, movie_canvas=None, intensity_canvas=None, navigator=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.StrongFocus)
        self.intensity_canvas = intensity_canvas
        self.kymoCanvas = kymo_canvas
        self.movieCanvas = movie_canvas
        self.navigator = navigator
        self.trajectories = []  # List of trajectory dicts.
        self._trajectory_counter = 1

        # Table widget for displaying trajectory summary information.
        self.table_widget = QTableWidget()
        # 1) Enable alternating row colors
        self.table_widget.setAlternatingRowColors(True)

        # 2) Tweak header appearance
        header = self.table_widget.horizontalHeader()
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self._on_header_context_menu)
        header.setSectionsClickable(False)
        header.setHighlightSections(False)
        header.setDefaultAlignment(Qt.AlignCenter)

        # 3) Apply a flat, modern stylesheet
        self.table_widget.setStyleSheet("""
            /* overall table */
            QTableWidget {
                background-color: #F5F5F5;
                gridline-color: #E0E0E0;
                font-family: 'Segoe UI', Arial, sans-serif;
                font-size: 11pt;
                border: none;
            }
            /* header */
            QHeaderView::section {
                background-color: #2C3E50;
                color: #ECF0F1;
                padding: 8px;
                border: none;
            }
            /* rows */
            QTableWidget::item {
                padding: 6px;
            }
            /* alternating row color */
            QTableWidget::item:alternate {
                background-color: #FFFFFF;
            }
            QTableWidget::item:!alternate {
                background-color: #FAFAFA;
            }
            /* selection */
            QTableWidget::item:selected {
                background-color: #3498DB;
                color: #FFFFFF;
            }
        """)

        # 4) hide the grid or only show horizontal lines:
        self.table_widget.setShowGrid(False)
        self.table_widget.setAlternatingRowColors(True)
        self.table_widget.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_widget.setColumnCount(13)
        self.table_widget.setMinimumHeight(50)
        self._headers = [
            "", "Channel", "Frame A", "Frame B",
            "Start X,Y", "End X,Y",
            "Distance μm", "Time s", "Net Speed μm/s",
            "Total", "Valid %",
            "Med. Intensity", "Avg. Intensity",
            "Avg. Speed μm/s"
        ]
        self.table_widget.setColumnCount(len(self._headers))
        self.table_widget.setHorizontalHeaderLabels(self._headers)

        # 2) map header text → column index
        self._col_index = { hdr: idx
                            for idx, hdr in enumerate(self._headers) }

        # 3) aliases
        self._aliases = {
            "channel":      "Channel",
            "startframe":   "Frame A",
            "endframe":     "Frame B",
            "startcoord":   "Start X,Y",
            "endcoord":     "End X,Y",
            "distance":     "Distance μm",
            "time":         "Time s",
            "netspeed":     "Net Speed μm/s",
            "total":        "Total",
            "valid":        "Valid %",
            "medintensity":"Med. Intensity",
            "avgintensity":"Avg. Intensity",
            "avgspeed":     "Avg. Speed μm/s",
        }
        self.table_widget.setStyleSheet("QTableWidget::item { text-align: center; }")
        self.table_widget.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
        self.table_widget.verticalHeader().setDefaultAlignment(Qt.AlignCenter)
        self.table_widget.verticalHeader().setVisible(False)
        self.table_widget.setSelectionBehavior(QTableWidget.SelectRows)
        self.table_widget.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table_widget.itemSelectionChanged.connect(self.on_trajectory_selected_by_table)
        # For the first column, adjust its width to fit its contents:
        self.table_widget.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)

        # For the rest of the columns (indexes 1 to n), set them as interactive:
        for i in range(1, self.table_widget.columnCount()): 
            if i in [1,2,3,7,9,10]:
                self.table_widget.horizontalHeader().setSectionResizeMode(i, QHeaderView.Interactive)
                self.table_widget.setColumnWidth(i, 70)
            elif i in [4,5,6,12]:
                self.table_widget.horizontalHeader().setSectionResizeMode(i, QHeaderView.Interactive)
                self.table_widget.setColumnWidth(i, 100)              
            elif i in [8,11]:
                self.table_widget.horizontalHeader().setSectionResizeMode(i, QHeaderView.Interactive)
                self.table_widget.setColumnWidth(i, 120)              
            else:
                self.table_widget.horizontalHeader().setSectionResizeMode(i, QHeaderView.Interactive)
                self.table_widget.setColumnWidth(i, 130)

        # turn on mouse‑tracking so we get hover events
        # self.table_widget.setMouseTracking(True)
        # # cellEntered gives us (row, col) whenever the cursor enters a cell
        # self.table_widget.cellEntered.connect(self.on_table_cell_hovered)
        # install an event filter on the viewport so we can catch Leave

        self.table_widget.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table_widget.customContextMenuRequested.connect(self.open_context_menu)
        self.table_widget.viewport().installEventFilter(self)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.table_widget)
        self.setLayout(layout)

        self.custom_columns = []
        self._column_types = {}

    def eventFilter(self, source, event):
        # intercept right-button presses on the table’s viewport
        if source is self.table_widget.viewport() and \
           event.type() == QEvent.MouseButtonPress and \
           event.button() == Qt.RightButton:
            # open your menu at the click position
            self.open_context_menu(event.pos())
            return True   # <— swallow the event so Qt doesn’t select that row
        return super().eventFilter(source, event)

    def makeCenteredItem(self, text):
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        return item

    def writeToTable(self, row, key, text):
        """
        key can be either the exact header text or one of your aliases.
        """
        # special case for column 0 (if you really need it)
        if key == "num":
            col = 0
        else:
            # normalize lookup
            key_lower = key.lower()

            # 1) try alias
            header = self._aliases.get(key_lower)
            # 2) fallback to exact header match
            if header is None:
                if key in self._col_index:
                    header = key
                else:
                    raise KeyError(f"No column or alias found for '{key}'")

            col = self._col_index[header]

        item = self.makeCenteredItem(text)
        if col == 0:
            font = item.font()
            font.setBold(True)
            item.setFont(font)

        # set the item
        self.table_widget.setItem(row, col, item)

    # def change_trajectory_id(self, rows):
    #     """
    #     rows: list of table‐row indices to rename.
    #     For simplicity we only allow renaming *one* at a time:
    #     """
    #     if len(rows) != 1:
    #         QMessageBox.information(
    #             self, "Bulk rename not supported",
    #             "Please select exactly one trajectory to rename."
    #         )
    #         return

    #     row = rows[0]
    #     old_id_item = self.table_widget.item(row, 0)
    #     old_id = int(old_id_item.text())

    #     # Ask for a new ID
    #     new_id, ok = QInputDialog.getInt(
    #         self, "Change Trajectory ID",
    #         f"Enter new ID for trajectory {old_id}:",
    #         value=old_id,
    #         min=0,
    #         max=1_000_000
    #     )
    #     if not ok or new_id == old_id:
    #         return

    #     # Check for collisions
    #     existing_ids = {traj["trajectory_number"] for traj in self.trajectories}
    #     if new_id in existing_ids:
    #         QMessageBox.warning(
    #             self, "ID Already Exists",
    #             f"A trajectory with ID {new_id} already exists.\n"
    #             "Please choose a different ID."
    #         )
    #         return

    #     # Update the in‐memory data
    #     for traj in self.trajectories:
    #         if traj["trajectory_number"] == old_id:
    #             traj["trajectory_number"] = new_id
    #             break

    #     # Refresh the whole table so rows sort by the new ID order
    #     self.refresh_trajectory_table()

    # def refresh_trajectory_table(self):
    #     #NEED TO WRITE

    def save_selected_trajectories(self):
        """
        Gather selected rows and call the unified export helper.
        """
        rows = [idx.row() for idx in self.table_widget.selectionModel().selectedRows()]
        if not rows:
            return

        # delegate to the single-export method
        self.save_trajectories(rows)


    def save_trajectories(self, rows: Optional[List[int]] = None):
        """
        Export either all trajectories (rows=None) or only those at the given row‐indices.
        """
        # pick the subset
        traj_list = self.trajectories if rows is None else [self.trajectories[r] for r in rows]
        if not traj_list:
            QMessageBox.warning(self, "", "There are no trajectories to save.")
            return

        # ask for filename
        filename, _ = QFileDialog.getSaveFileName(self, "Save Trajectories", "", "Excel Files (*.xlsx)")
        if not filename:
            return

        try:
            summary_rows = []
            data_rows = []
            for traj in self.trajectories:
                channel = int(traj["channel"])
                fixed_background = traj["fixed_background"]
                save_start_frame = int(traj["start"][0]) + 1
                save_end_frame = int(traj["end"][0]) + 1
                frames_list = traj.get("frames", [])
                num_points = len(frames_list)
                # Compute number of valid points: valid if intensity exists and is greater than 0.
                intensities_list = traj.get("intensities", [])
                valid_points = sum(1 for val in intensities_list if val is not None and val > 0)
                percent_valid = (100 * valid_points / num_points) if num_points > 0 else 0

                avg_vel_px_fr_txt = ""
                avg_vel_um_s_txt = ""
                avg_vel_um_min_txt = ""
                if self.navigator.pixel_size is not None and self.navigator.frame_interval is not None and traj['average_velocity'] is not None:
                    avg_vel_px_fr_txt = f"{traj['average_velocity']:.2f}"
                    velocity_nm_per_ms = (traj['average_velocity'] * self.navigator.pixel_size) / self.navigator.frame_interval
                    avg_vel_um_s_txt = f"{velocity_nm_per_ms:.2f}"
                    avg_vel_um_min_txt = f"{velocity_nm_per_ms * 60.0:.2f}"

                dx = traj['end'][1] - traj['start'][1]
                dy = traj['end'][2] - traj['start'][2]
                distance_px = np.hypot(dx, dy)
                time_fr = traj['end'][0]-traj['start'][0]
                distance_um_txt = ""
                time_s_txt = ""
                overall_vel_px_fr_txt = ""
                overall_vel_um_s_txt = ""
                overall_vel_um_min_txt = ""
                if self.navigator.pixel_size is not None and self.navigator.frame_interval is not None and time_fr > 0:
                    distance_um = distance_px * self.navigator.pixel_size / 1000
                    time_s = time_fr * self.navigator.frame_interval / 1000
                    overall_vel_px_fr = distance_px / time_fr
                    overall_vel_um_s = distance_um/time_s
                    overall_vel_um_min = overall_vel_um_s * 60.0
                    distance_um_txt = f"{distance_um:.2f}"
                    time_s_txt = f"{time_s:.2f}"
                    overall_vel_px_fr_txt = f"{overall_vel_px_fr:.2f}"
                    overall_vel_um_s_txt = f"{overall_vel_um_s:.2f}"
                    overall_vel_um_min_txt = f"{overall_vel_um_min:.2f}"

                # 1) Serialize anchors
                anchors = traj.get("anchors", [])
                if anchors:
                    # convert to pure-Python types
                    anchors_py = [
                        (int(frame), float(x), float(y))
                        for frame, x, y in anchors
                    ]
                    anchors_str = json.dumps(anchors_py)
                else:
                    anchors_str = ""  # empty

                # 2) Serialize ROI
                roi = traj.get("roi", None)
                if roi:
                    # convert all np.floats to floats
                    roi_clean = {
                        "type": roi["type"],
                        "x": [float(xx) for xx in roi.get("x", [])],
                        "y": [float(yy) for yy in roi.get("y", [])],
                        "points": [
                            (float(px), float(py)) for px, py in roi.get("points", [])
                        ]
                    }
                    roi_str = json.dumps(roi_clean)
                else:
                    roi_str = "" 

                summary_rows.append({
                    "Movie": self.navigator.movieNameLabel.text(),
                    "Trajectory": traj.get("trajectory_number", "?"),
                    "Channel": channel,
                    "Start_Frame": save_start_frame,
                    "End_Frame": save_end_frame,
                    "Anchors": anchors_str,
                    "ROI":    roi_str,
                    "Num_Points": num_points,
                    "Valid_Points": valid_points,
                    "Percent Valid": percent_valid,
                    "Search Center X Start": float(traj["start"][1]),
                    "Search Center Y Start": float(traj["start"][2]),
                    "Search Center X End": float(traj["end"][1]),
                    "Search Center Y End": float(traj["end"][2]),
                    "Distance (μm)": distance_um_txt,
                    "Time (s)": time_s_txt,
                    "Background": fixed_background,
                    "Average Intensity": "" if traj["average"] is None else traj["average"],
                    "Median Intensity": "" if traj["median"] is None else traj["median"],
                    "Net Speed (px/frame)": overall_vel_px_fr_txt,
                    "Net Speed (μm/s)": overall_vel_um_s_txt,
                    "Net Speed (μm/min)": overall_vel_um_min_txt,
                    "Avg. Speed (px/frame)": avg_vel_px_fr_txt,
                    "Avg. Speed (μm/s)": avg_vel_um_s_txt,
                    "Avg. Speed (μm/min)": avg_vel_um_min_txt,
                })

                for col in self.custom_columns:
                    col_type = self._column_types.get(col, "binary")
                    suffixed = f"{col} [{col_type}]"
                    summary_rows[-1][suffixed] = traj.get("custom_fields", {}).get(col, "")

                traj_name = str(traj.get("trajectory_number", "?"))
                coords_list = traj.get("original_coords", [])
                centers_list = traj.get("search_centers", [])

                # Ensure we have the intensities list from above.
                for i in range(len(frames_list)):

                    vel_nm_per_ms = ""
                    vel_um_min = ""
                    velocity = ""
                    coord_x, coord_y = "", ""
                    search_x, search_y = "", ""
                    spot_x, spot_y = "", ""
                    sigma_val = ""
                    peak_val = ""
                    background_val = ""

                    intensity_val = intensities_list[i] if (intensities_list[i] is not None and intensities_list[i] > 0) else ""
                    if i < len(coords_list):
                        coord_x, coord_y = coords_list[i]
                    else:
                        coord_x, coord_y = "", ""
                    if i < len(centers_list):
                        search_x, search_y = centers_list[i]
                    else:
                        search_x, search_y = "", ""
                    if "spot_centers" in traj and len(traj["spot_centers"]) > i and traj["spot_centers"][i] is not None:
                        spot_x, spot_y = traj["spot_centers"][i]
                    else:
                        spot_x, spot_y = "", ""
                    sigma_val = ""
                    if "sigmas" in traj and len(traj["sigmas"]) > i and traj["sigmas"][i] is not None:
                        sigma_val = traj["sigmas"][i]
                    peak_val = ""
                    if "peaks" in traj and len(traj["peaks"]) > i and traj["peaks"][i] is not None:
                        peak_val = traj["peaks"][i]
                    if "background" in traj and len(traj["background"]) > i and traj["background"][i] is not None:
                        background_val = traj["background"][i]
                    if "velocities" in traj and len(traj["velocities"]) > i and traj["velocities"][i] is not None:
                        velocity = traj["velocities"][i]
                        if self.navigator.pixel_size is not None and self.navigator.frame_interval is not None:
                            vel_nm_per_ms = (velocity * self.navigator.pixel_size) / self.navigator.frame_interval
                            vel_um_min = vel_nm_per_ms  * 60.0
                        else:
                            vel_um_min = ""
                    else:
                        velocity = ""
                        vel_nm_per_ms = ""
                        vel_um_min = ""

                    fixedstr = "No"
                    if fixed_background is not None:
                        fixedstr = "Yes"

                    data_rows.append({
                        "Trajectory": traj_name,
                        "Channel": channel,
                        "Frame": frames_list[i] + 1,
                        "Original Coordinate X": coord_x,
                        "Original Coordinate Y": coord_y,
                        "Search Center X": search_x,
                        "Search Center Y": search_y,
                        "Spot Center X": spot_x,
                        "Spot Center Y": spot_y,
                        "Intensity": intensity_val,
                        "Sigma": sigma_val,
                        "Peak": peak_val,
                        "Background from trajectory": fixedstr,
                        "Background": background_val,
                        "Speed (px/frame)": velocity,
                        "Speed (μm/s)": vel_nm_per_ms,
                        "Speed (μm/min)": vel_um_min
                    })

            df_summary = pd.DataFrame(summary_rows)
            df_data = pd.DataFrame(data_rows)

            # --- Per-ROI sheet ---
            # Copy and convert columns to numeric
            df_roi = df_summary.copy()
            for col in ["Distance (μm)", "Time (s)", "Net Speed (μm/s)", "Avg. Speed (μm/s)", "Average Intensity", "Median Intensity"]:
                df_roi[col] = pd.to_numeric(df_roi[col], errors="coerce")

            # Pixel size conversion nm->µm
            pixel_size_um = self.navigator.pixel_size / 1000.0 if self.navigator.pixel_size is not None else None

            per_roi_list = []
            # Group by ROI string
            for roi_val, grp in df_roi.groupby("ROI"):
                n_trajs = len(grp)
                total_time_s = grp["Time (s)"].sum()
                if pixel_size_um and total_time_s > 0:
                    events_per_um_per_min = n_trajs / (total_time_s / 60.0) / pixel_size_um
                else:
                    events_per_um_per_min = float('nan')

                per_roi_list.append({
                    "ROI": roi_val,
                    "Number of trajectories": n_trajs,
                    "Events (/μm/min)": events_per_um_per_min,
                    "Average net speed (μm/s)": grp["Net Speed (μm/s)"].mean(),
                    "Average average speed (μm/s)": grp["Avg. Speed (μm/s)"].mean(),
                    "Average run length (μm)": grp["Distance (μm)"].mean(),
                    "Average run time (s)": grp["Time (s)"].mean(),
                    "Average median intensity": grp["Median Intensity"].mean(),
                    "Average average intensity": grp["Average Intensity"].mean(),
                })

            df_per_roi = pd.DataFrame(per_roi_list)

            with pd.ExcelWriter(filename) as writer:
                df_data.to_excel(writer, sheet_name="Data Points", index=False)
                df_summary.to_excel(writer, sheet_name="Per-trajectory", index=False)
                df_per_roi.to_excel(writer, sheet_name="Per-ROI", index=False)

        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Failed to save trajectories: {str(e)}")

    def hide_empty_columns(self):
        # columns to never hide, regardless of content
        always_visible = {
            "Frame A",
            "Frame B",
            "Start X,Y",
            "End X,Y",
            "Total",
            "Valid %",
            "Med. Intensity",
            "Avg. Intensity"
        }

        for col in range(self.table_widget.columnCount()):
            hdr = self._headers[col]

            # always keep ID, custom columns, and any in always_visible
            if col == 0 or hdr in self.custom_columns or hdr in always_visible:
                self.table_widget.setColumnHidden(col, False)
                continue

            # otherwise, only show if at least one non-empty cell
            has_value = False
            for row in range(self.table_widget.rowCount()):
                item = self.table_widget.item(row, col)
                if item and item.text().strip():
                    has_value = True
                    break
            self.table_widget.setColumnHidden(col, not has_value)

    def on_trajectory_selected_by_table(self):
        selected_rows = self.table_widget.selectionModel().selectedRows()
        if not selected_rows:
            return
        index = selected_rows[0].row()
        self.on_trajectory_selected_by_index(index)

    def on_trajectory_selected_by_index(self, index):
        if index < 0 or index >= len(self.trajectories):
            return

        # ——— locals & block redraws ———
        nav = self.navigator
        mc  = self.movieCanvas
        kc  = self.kymoCanvas
        ic  = nav.intensityCanvas
        vc  = nav.velocityCanvas

        traj = self.trajectories[index]
        ch = traj.get("channel", None)
        if ch is not None:
            nav._select_channel(ch)
        # block Qt/Matplotlib repaints
        mc.setUpdatesEnabled(False)
        kc.setUpdatesEnabled(False)

        try:
            # ——— 1) extract trajectory once ———
            nav.analysis_channel         = traj["channel"]
            nav.analysis_start           = traj["start"]
            nav.analysis_end             = traj["end"]
            nav.analysis_roi             = traj["roi"]
            nav.analysis_frames          = traj["frames"]
            nav.analysis_search_centers  = traj["search_centers"]
            nav.analysis_original_coords = traj["original_coords"]
            nav.analysis_intensities     = traj["intensities"]
            nav.analysis_background      = traj["background"]
            nav.analysis_fit_params      = list(zip(traj["spot_centers"],
                                                    traj["sigmas"],
                                                    traj["peaks"]))
            nav.analysis_colors          = traj["colors"]
            nav.analysis_velocities      = traj["velocities"]
            nav.analysis_trajectory_background = traj["fixed_background"]
            nav.analysis_background = traj["background"]

            # ——— 2) update the small canvases ———
            # assume these methods are fast or already optimized internally
            ic.plot_intensity(traj["frames"],
                            traj["intensities"],
                            avg_intensity=traj["average"],
                            median_intensity=traj["median"],
                            colors=traj.get("colors"))
            vc.plot_velocity_histogram(traj["velocities"])

            # ——— 3) reset looping & zoom state ———
            if self.navigator.looping:
                self.navigator.stoploop()
            mc.manual_zoom   = False
            nav.loop_index   = 0
            nav.jump_to_analysis_point(0, animate="ramp")

            ic.current_index = 0
            ic.highlight_current_point()

            # ——— 4) update the analysis slider ———
            if hasattr(nav, 'analysisSlider'):
                sld = nav.analysisSlider
                sld.blockSignals(True)
                sld.setRange(0, len(nav.analysis_frames) - 1)
                sld.setValue(0)
                sld.blockSignals(False)

            # ——— 5) (Re)draw trajectories, but reuse artists ———
            if nav.traj_overlay_button.isChecked():
                mc.clear_movie_trajectory_markers()
                mc.draw_trajectories_on_movie()
                kc.clear_kymo_trajectory_markers()
                kc.draw_trajectories_on_kymo()

        finally:
            # ——— 6) single redraw and re-enable updates ———
            mc.setUpdatesEnabled(True)
            kc.setUpdatesEnabled(True)
            mc.draw()
            kc.draw()

    def add_trajectory_from_navigator(self, trajid=None):
        """
        Gathers the current analysis data from the navigator and adds a new trajectory.
        """
        if (self.navigator is None or
            self.navigator.analysis_start is None or 
            self.navigator.analysis_end is None or 
            not self.navigator.analysis_frames or 
            not self.navigator.analysis_original_coords):
            # print("❌ No trajectory data found!")
            return

        channel = self.navigator.analysis_channel
        traj_background = self.navigator.analysis_trajectory_background
        start = self.navigator.analysis_start
        end = self.navigator.analysis_end
        frames = self.navigator.analysis_frames
        anchors = self.navigator.analysis_anchors
        roi = self.navigator.analysis_roi
        intensities = self.navigator.analysis_intensities
        original_coords = self.navigator.analysis_original_coords
        if self.navigator.analysis_search_centers:
            search_centers = self.navigator.analysis_search_centers
        else:
            search_centers = self.navigator.analysis_original_coords
        avg_intensity = self.navigator.analysis_avg
        median_intensity = self.navigator.analysis_median
        average_velocity = self.navigator.analysis_average_velocity
        velocities = self.navigator.analysis_velocities

        # Store the list of refined spot centers along the trajectory.
        spot_centers = []
        sigmas = []
        peaks = []
        if hasattr(self.navigator, "analysis_fit_params"):
            for fit in self.navigator.analysis_fit_params:
                spot_centers.append(fit[0])  # This may be None if the fit failed
                sigmas.append(fit[1])
                peaks.append(fit[2])

        colors = self.navigator.analysis_colors if hasattr(self.navigator, "analysis_colors") else None
        background = self.navigator.analysis_background if hasattr(self.navigator, "analysis_background") else None

        if trajid is None:
            trajid = self._trajectory_counter

        traj_data = {
            "trajectory_number": trajid,
            "channel": channel,
            "start": start,
            "end": end,
            "anchors": anchors,
            "roi": roi,
            "spot_centers": spot_centers,
            "sigmas": sigmas,
            "peaks": peaks,
            "fixed_background": traj_background,
            "background": background,
            "frames": frames,
            "original_coords": original_coords,
            "search_centers": search_centers,
            "intensities": intensities,
            "average": avg_intensity,
            "median": median_intensity,
            "colors": colors,
            "velocities": velocities,
            "average_velocity": average_velocity
        } 

        traj_data["custom_fields"] = {}

        if len(frames) == 0 or len(intensities) == 0:
            print("Error: no frames or intensities to add!")
            return

        self.trajectories.append(traj_data)

        # Assuming you computed average_velocity in pixels/frame as traj["average_velocity"]
        if self.navigator.pixel_size is not None and self.navigator.frame_interval is not None and average_velocity is not None:
            velocity_nm_per_ms = (average_velocity * self.navigator.pixel_size) / self.navigator.frame_interval
            avg_vel_um_s = f"{velocity_nm_per_ms:.2f}"
            # avg_vel_um_min = f"{velocity_nm_per_ms * 60.0:.2f}"
        else:
            avg_vel_um_s = ""
            # avg_vel_um_min = ""

        num_points = len(frames)
        valid_points = sum(1 for val in intensities if val is not None and val > 0)
        percent_valid = int(100 * valid_points / num_points) if num_points > 0 else 0

        dx = end[1] - start[1]
        dy = end[2] - start[2]
        distance_px = np.hypot(dx, dy)
        time_fr = end[0]-start[0]
        distance_um_txt = ""
        time_s_txt = ""
        overall_vel_um_s_txt = ""
        if self.navigator.pixel_size is not None and self.navigator.frame_interval is not None and time_fr > 0:
            distance_um = distance_px * self.navigator.pixel_size / 1000
            time_s = time_fr * self.navigator.frame_interval / 1000
            overall_vel_um_s = distance_um/time_s
            distance_um_txt = f"{distance_um:.2f}"
            time_s_txt = f"{time_s:.2f}"
            overall_vel_um_s_txt = f"{overall_vel_um_s:.2f}"

        # Add a new row to the table.
        row = self.table_widget.rowCount()
        self.table_widget.insertRow(row)
        self.writeToTable(row, "num", str(trajid))
        if self.navigator.movieChannelCombo.count() > 1:
            self.writeToTable(row, "channel", str(channel))
        self.writeToTable(row, "startframe", str(int(start[0]) + 1))
        self.writeToTable(row, "endframe", str(int(end[0]) + 1))
        self.writeToTable(row, "startcoord", f"{start[1]:.1f}, {start[2]:.1f}")
        self.writeToTable(row, "endcoord", f"{end[1]:.1f}, {end[2]:.1f}")
        self.writeToTable(row, "distance", distance_um_txt)
        self.writeToTable(row, "time", time_s_txt)
        self.writeToTable(row, "netspeed", overall_vel_um_s_txt)  
        self.writeToTable(row, "total", str(num_points))
        self.writeToTable(row, "valid", str(percent_valid))        
        median_text = "" if median_intensity is None else f"{median_intensity:.2f}"
        avg_text = "" if avg_intensity is None else f"{avg_intensity:.2f}"
        self.writeToTable(row, "medintensity", median_text)
        self.writeToTable(row, "avgintensity", avg_text)
        self.writeToTable(row, "avgspeed", avg_vel_um_s)
        self._trajectory_counter += 1

        self.navigator.update_table_visibility()

        # Set the table's selection to the newly added trajectory.
        new_row = self.table_widget.rowCount() - 1
        self.table_widget.blockSignals(True)
        self.table_widget.selectRow(new_row)
        self.table_widget.blockSignals(False)

        # Also update the current index in the plot canvas and trigger a selection update.
        self.intensity_canvas.current_index = new_row
        self.on_trajectory_selected_by_index(new_row)

        self.navigator.update_table_visibility()
        
    def update_trajectory(self, idx, fitted_center, sigma, peak, intensity):
        # 1) Figure out which trajectory row is active
        selected = self.table_widget.selectionModel().selectedRows()
        if not selected:
            return
        row = selected[0].row()

        # 2) Toggle: if intensity was None we’re re-validating, else we’re invalidating
        if self.navigator.analysis_intensities[idx] is None:
            # re-analysis: store new fit
            if fitted_center is not None:
                self.navigator.analysis_fit_params[idx] = (fitted_center, sigma, peak)
                self.navigator.analysis_intensities[idx] = intensity
                self.navigator.analysis_colors[idx] = "magenta"
        else:
            # invalidation: clear this point
            self.navigator.analysis_fit_params[idx] = (None, None, None)
            self.navigator.analysis_intensities[idx] = None
            self.navigator.analysis_colors[idx] = "grey"

        # 3) Unpack all fit params and rebuild velocity list
        centers, sigmas, peaks = zip(*self.navigator.analysis_fit_params)
        self.navigator.analysis_velocities = calculate_velocities(centers)
        # compute average velocity (px/frame)
        valid_vels = [v for v in self.navigator.analysis_velocities if v is not None]
        avg_vpf = float(np.mean(valid_vels)) if valid_vels else None
        self.navigator.average_velocity = avg_vpf

        # 4) Recompute average & median intensity
        ints = self.navigator.analysis_intensities
        valid_ints = [v for v in ints if v is not None and v > 0]
        new_avg    = float(np.mean(valid_ints))   if valid_ints else None
        new_median = float(np.median(valid_ints)) if valid_ints else None
        self.navigator.analysis_avg    = new_avg
        self.navigator.analysis_median = new_median

        # 5) Update “percent valid” for table
        total_pts    = len(self.navigator.analysis_frames)
        n_valid      = len(valid_ints)
        pct_valid    = int(100 * n_valid / total_pts) if total_pts else 0

        # 6) Prepare human‐readable speed (µm/s) if you have pixel_size & frame_interval
        avg_um_s_txt = ""
        if avg_vpf is not None and self.navigator.pixel_size and self.navigator.frame_interval:
            nm_per_ms = (avg_vpf * self.navigator.pixel_size) / self.navigator.frame_interval
            avg_um_s_txt = f"{nm_per_ms:.2f}"

        # 7) Reflect everything back into your trajectory model
        traj = self.trajectories[row]
        traj["spot_centers"][idx]       = centers[idx]
        traj["sigmas"][idx]             = sigmas[idx]
        traj["peaks"][idx]              = peaks[idx]
        traj["intensities"][idx]        = ints[idx]
        traj["colors"][idx]             = self.navigator.analysis_colors[idx]
        traj["velocities"]              = self.navigator.analysis_velocities
        traj["average_velocity"]        = self.navigator.average_velocity
        traj["average"]                 = new_avg
        traj["median"]                  = new_median
        traj["fixed_background"]        = self.navigator.analysis_trajectory_background
        traj["background"]              = self.navigator.analysis_background

        # 8) Update the table
        self.writeToTable(row, "valid",        str(pct_valid))
        self.writeToTable(row, "medintensity", "" if new_median is None else f"{new_median:.2f}")
        self.writeToTable(row, "avgintensity", "" if new_avg    is None else f"{new_avg:.2f}")
        self.writeToTable(row, "avgspeed",     avg_um_s_txt)

    def load_trajectories(self):
        if self.navigator.movie is None:
            QMessageBox.warning(self, "", 
                "Please load a movie before loading trajectories.")
            return
        
        self.navigator.cancel_left_click_sequence()

        # Get the current movie's base name.
        movie_base = self.navigator.movieNameLabel.text()  # assuming this holds the filename, e.g. "my_movie.tif"
        # Remove the current extension and add ".csv"
        default_filename = os.path.splitext(movie_base)[0] + ".csv"
        # Use default_filename as the third argument.
        filename, _ = QFileDialog.getOpenFileName(
            self, 
            "Load Trajectories File", 
            default_filename,  # <-- default file name here
            "Excel and CSV Files (*.xlsx *.csv)"
        )
        if not filename:
            return
        
        try:
            ext = os.path.splitext(filename)[1].lower()

            anchors_map = {}
            roi_map     = {}

            # ----- Excel branch -----
            if ext == ".xlsx":
                xls = pd.ExcelFile(filename)
                if "Data Points" not in xls.sheet_names:
                    QMessageBox.critical(self, "Error", "Sheet 'Data Points' not found.")
                    return

                if "Per-trajectory" in xls.sheet_names:
                    summary_df = pd.read_excel(xls, sheet_name="Per-trajectory")
                    for _, row in summary_df.iterrows():
                        traj_id = int(row["Trajectory"])

                        # parse anchors
                        a = row.get("Anchors", "")
                        if isinstance(a, str) and a.strip():
                            try:
                                anchors_py = json.loads(a)
                            except json.JSONDecodeError:
                                anchors_py = []
                        else:
                            anchors_py = []
                        anchors_map[traj_id] = anchors_py

                        # parse ROI
                        r = row.get("ROI", "")
                        if isinstance(r, str) and r.strip():
                            try:
                                roi_clean = json.loads(r)
                                # make sure points are tuples
                                roi_clean["points"] = [
                                    tuple(pt) for pt in roi_clean.get("points", [])
                                ]
                            except json.JSONDecodeError:
                                roi_clean = None
                        else:
                            roi_clean = None
                        roi_map[traj_id] = roi_clean

                # — detect any extra columns —
                known = {"Movie","Trajectory","Channel","Start_Frame","End_Frame",
                        "Anchors","ROI","Num_Points","Valid_Points","Percent Valid",
                        "Search Center X Start","Search Center Y Start",
                        "Search Center X End","Search Center Y End",
                        "Distance (μm)","Time (s)","Background",
                        "Average Intensity","Median Intensity",
                        "Net Speed (px/frame)","Net Speed (μm/s)","Net Speed (μm/min)",
                        "Avg. Speed (px/frame)","Avg. Speed (μm/s)","Avg. Speed (μm/min)"}
                # full headers as they appear in the sheet, e.g. "Foo [value]"
                full_extra = [c for c in summary_df.columns if c not in known]

                # build the load-map using the full names
                custom_map = {}
                for _, row in summary_df.iterrows():
                    tid = int(row["Trajectory"])
                    d = {}
                    for full in full_extra:
                        # strip off the suffix so our key is the plain name
                        m = re.match(r"(.+)\s\[(?:binary|value)\]$", full)
                        name = m.group(1) if m else full

                        val = row[full]
                        if pd.isna(val):
                            d[name] = ""
                        elif isinstance(val, float):
                            # turn 2.0 → "2", but leave 2.5 as "2.5"
                            if val.is_integer():
                                d[name] = str(int(val))
                            else:
                                d[name] = str(val)
                        else:
                            # covers ints, strings, etc.
                            d[name] = str(val)

                    custom_map[tid] = d

                self._custom_load_map = custom_map

                # parse off “[binary]” or “[value]”
                parsed = []
                self._column_types.clear()
                for full in full_extra:
                    m = re.match(r"(.+)\s\[(binary|value)\]$", full)
                    if m:
                        name, typ = m.group(1), m.group(2)
                    else:
                        # no suffix → assume binary
                        name, typ = full, "binary"
                    parsed.append(name)
                    self._column_types[name] = typ

                self.custom_columns = []
                for name in parsed:
                    if name not in self._col_index:
                        self._add_custom_column(name, col_type=self._column_types[name])
                # at this point, _add_custom_column has built self.custom_columns correctly
                self.navigator._rebuild_color_by_actions()

                # finally read the Data Points sheet
                df = pd.read_excel(xls, sheet_name="Data Points")
            
            # ----- CSV branch -----
            elif ext == ".csv":
                # Read CSV assuming first row is the header.
                df_temp = pd.read_csv(filename, header=0, engine="python")

                # Helper: check if value is numeric.
                def is_numeric(x):
                    try:
                        float(x)
                        return True
                    except (ValueError, TypeError):
                        return False

                # Find first row index where the FRAME column is numeric.
                data_start = None
                for idx, val in df_temp["FRAME"].items():
                    if is_numeric(val):
                        data_start = idx
                        break
                if data_start is None:
                    QMessageBox.critical(self, "Error", "No numeric data found in the 'FRAME' column.")
                    return
                # Keep only the rows from that index onward.
                df_temp = df_temp.loc[data_start:].reset_index(drop=True)

                # Verify expected columns.
                required_csv_cols = {"TRACK_ID", "FRAME", "POSITION_X", "POSITION_Y"}
                if not required_csv_cols.issubset(set(df_temp.columns)):
                    QMessageBox.critical(
                        self, "Error",
                        "CSV is missing one or more required columns: TRACK_ID, FRAME, POSITION_X, POSITION_Y"
                    )
                    return

                # Check if the pixel size hasn't been set yet.
                if self.navigator.pixel_size is None:
                    self.set_scale()  # This will open the Set Scale dialog
                    # If pixel_size is still not set, bail out.
                    if self.navigator.pixel_size is None:
                        return

                # By here, you should have a pixel size already.
                pixelsize = self.navigator.pixel_size
                conversion_factor = float(pixelsize) / 1000.0

                # Convert POSITION columns to numeric.
                df_temp["POSITION_X"] = pd.to_numeric(df_temp["POSITION_X"], errors="coerce")
                df_temp["POSITION_Y"] = pd.to_numeric(df_temp["POSITION_Y"], errors="coerce")
                # (If necessary, drop an extra row of NaNs)
                df_temp = df_temp.iloc[1:].reset_index(drop=True)

                # Build new DataFrame with expected column names.
                new_data = {
                    "Trajectory": df_temp["TRACK_ID"],
                    "Frame": pd.to_numeric(df_temp["FRAME"], errors="coerce").astype(int),
                    "Original Coordinate X": df_temp["POSITION_X"] / conversion_factor,
                    "Original Coordinate Y": df_temp["POSITION_Y"] / conversion_factor,
                }
                df = pd.DataFrame(new_data)
                df["Trajectory"] = pd.to_numeric(df["Trajectory"], errors="coerce")
                df["Frame"] = pd.to_numeric(df["Frame"], errors="coerce")
                df = df.sort_values(by=["Trajectory", "Frame"]).reset_index(drop=True)
            else:
                raise Exception("Unsupported file type.")

            self.load_trajectories_from_df(df, anchors_map=anchors_map, roi_map=roi_map)

        except Exception as e:
            QMessageBox.critical(self, "Load Error", f"Failed to load trajectories: {str(e)}")

        self.navigator.update_table_visibility()

    def load_trajectories_from_df(self, df, anchors_map=None, roi_map=None, forcerecalc=False):

        # 1) Always require these two columns
        if "Trajectory" not in df.columns or "Frame" not in df.columns:
            QMessageBox.critical(
                self, "Error",
                "Missing columns: both 'Trajectory' and 'Frame' are required."
            )
            return

        have_coords = "Original Coordinate X" in df.columns and "Original Coordinate Y" in df.columns
        have_centers = "Search Center X" in df.columns and "Search Center Y" in df.columns

        if not have_coords and not have_centers:
            QMessageBox.critical(
                self, "Error",
                "You must provide 'Original Coordinate X'/'Original Coordinate Y' or 'Search Center X'/'Search Center Y' columns."
            )
            return
        
        if have_coords and not have_centers:
            df["Search Center X"] = df["Original Coordinate X"]
            df["Search Center Y"] = df["Original Coordinate Y"]
        elif have_centers and not have_coords:
            df["Original Coordinate X"] = df["Search Center X"]
            df["Original Coordinate Y"]  = df["Search Center Y"]

        # Determine whether intensity, sigma, and peak columns exist.
        intensity_exists = "Intensity" in df.columns
        sigma_exists = "Sigma" in df.columns
        peak_exists = "Peak" in df.columns
        background_exists = "Background" in df.columns

        if anchors_map is None:
            anchors_map = {}
        if roi_map is None:
            roi_map = {}

        fixed_background = None

        trajectories_need_recalc = 0
        for traj_num, group in df.groupby("Trajectory"):
            group = group.sort_values("Frame")
            frames = group["Frame"].tolist()
            expected_frames = list(range(min(frames), max(frames) + 1))
            if expected_frames != frames or (not intensity_exists) or (not sigma_exists) or (not peak_exists) or (not background_exists):
                trajectories_need_recalc += 1


        if not forcerecalc:
            recalc_mode = False

        if trajectories_need_recalc > 0:
            # Instead of a simple question, show the custom dialog.
            current_mode = self.navigator.tracking_mode if hasattr(self.navigator, "tracking_mode") else "Independent"
            current_radius = self.navigator.searchWindowSpin.value()
            message = (f"{trajectories_need_recalc} trajectory(ies) have missing points or spot parameters.")
            recalc_dialog = RecalcDialog(current_mode, current_radius, message=message, parent=self)
            result = recalc_dialog.exec_()
            if result != QDialog.Accepted:
                return
            # Retrieve new selections from the dialog.
            new_mode = recalc_dialog.new_mode
            new_radius = recalc_dialog.new_radius
            # Update the GUI state.
            self.navigator.searchWindowSpin.setValue(new_radius)
            self.navigator.tracking_mode = new_mode
            if hasattr(self.navigator, "trackingModeCombo"):
                self.trackingModeCombo.setCurrentText(new_mode)

            recalc_mode = True

        # --- Process each trajectory ---
        # Create a list of groups so we can report progress.
        groups = list(df.groupby("Trajectory"))
        progress = QProgressDialog("Processing...", "Cancel", 0, len(groups), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()

        loaded = []

        for t, (traj_num, group) in enumerate(groups):

            if progress.wasCanceled():
                break

            # —— new: extract & validate channel column ——
            if "Channel" in group.columns:
                chans = set(group["Channel"].tolist())
                if len(chans) == 1:
                    channel = chans.pop()
                else:
                    QMessageBox.critical(
                        self,
                        "Ambiguous Channel",
                        f"Ambiguous channel for trajectory {traj_num}: found {sorted(chans)}"
                    )
                    return
            else:
                channel = None

            group = group.sort_values("Frame")
            anchors = anchors_map.get(traj_num, [])
            roi     = roi_map.get(traj_num, None)
            frames = [x-1 for x in group["Frame"].tolist()]

            # fallback to the DataFrame’s Search Centers
            x_coords = group["Original Coordinate X"].tolist()
            y_coords = group["Original Coordinate Y"].tolist()
            points = [
                (frame, float(x), float(y))
                for frame, x, y in zip(frames, x_coords, y_coords)
            ]

            if recalc_mode:
                # --- Recalculation branch ---
                frames = [f - 1 for f in group["Frame"].tolist()]
                full_frames = list(range(min(frames), max(frames) + 1))

                intensities = []
                spot_centers = []
                sigmas = []
                peaks = []
                background = []
                fixed_background = None

                # 2) check for a consistent “yes” background flag
                if "Background from trajectory" in group:
                    flags = (
                        group["Background from trajectory"]
                        .dropna()
                        .astype(str)
                        .str.strip()
                        .str.lower()
                    )
                    if not flags.empty and flags.eq("yes").all():
                        # pull all numeric backgrounds in this group
                        if "Background" in group:
                            uniq_bg = group["Background"].dropna().unique()
                            if len(uniq_bg) == 1:
                                fixed_background = float(uniq_bg[0])
                            else:
                                # ambiguous numeric values → discard
                                fixed_background = None

                if len(anchors) >= 2 and roi is not None:
                    points = []
                    for i in range(len(anchors) - 1):
                        f1, xk1, _ = anchors[i]
                        f2, xk2, _ = anchors[i + 1]

                        # include f1 on the first segment, then skip it
                        seg = list(range(f1, f2 + 1)) if i == 0 else list(range(f1+1, f2+1))
                        n   = len(seg)
                        xs  = np.linspace(xk1, xk2, n, endpoint=True)

                        for j, f in enumerate(seg):
                            xk = xs[j]
                            mx, my = compute_roi_point(roi, xk)
                            points.append((f, mx, my))

                    # now unpack pts into the same variables your downstream code expects:
                    frames_used = [p[0] for p in points]
                    traj_coords = [(p[1], p[2]) for p in points]

                if fixed_background is None:
                    fixed_background = self.navigator.compute_trajectory_background(
                        self.navigator.get_movie_frame,
                        points,
                        crop_size=int(2 * self.navigator.searchWindowSpin.value())
                    )

                # Call the analysis function.
                frames_used, traj_coords, traj_centers, intensities, fit_params, background, colors = self.navigator._compute_analysis(points, bg=fixed_background, showprogress=False)

                spot_centers = [fp[0] for fp in fit_params]
                sigmas = [fp[1] for fp in fit_params]
                peaks = [fp[2] for fp in fit_params]

                # You can use frames_used and traj_coords (which are the refined trajectory coordinates)
                # to calculate additional measures (e.g. velocities) if needed.
                start = (full_frames[0], x_coords[0], y_coords[0])
                end = (full_frames[-1], x_coords[-1], y_coords[-1])
                frames_used = full_frames
                valid_ints = [val for val, spot in zip(intensities, spot_centers)
                            if val is not None and val > 0 and spot is not None]
                avg = float(np.mean(valid_ints)) if valid_ints else None
                med = float(np.median(valid_ints)) if valid_ints else None
                velocities = []
                for i in range(1, len(full_frames)):
                    if spot_centers[i-1] is None or spot_centers[i] is None:
                        velocities.append(None)
                    else:
                        dx = spot_centers[i][0] - spot_centers[i-1][0]
                        dy = spot_centers[i][1] - spot_centers[i-1][1]
                        velocities.append(np.hypot(dx, dy))

                if velocities:
                    valid_vels = [v for v in velocities if v is not None]
                    average_velocity = float(np.mean(valid_vels)) if valid_vels else None
                else:
                    average_velocity = None
            else:
                sigmas = group["Sigma"].tolist() if "Sigma" in group.columns else []
                peaks = group["Peak"].tolist() if "Peak" in group.columns else []
                background = group["Background"].tolist() if "Background" in group.columns else []
                if "Intensity" in group.columns:
                    # Replace NaN with None
                    intensities = [None if pd.isna(val) else val for val in group["Intensity"].tolist()]
                else:
                    intensities = []
                valid_ints = [val for val in intensities if val is not None and val > 0]
                avg = float(np.mean(valid_ints)) if valid_ints else None
                med = float(np.median(valid_ints)) if valid_ints else None
                row_first = group.iloc[0]
                row_last = group.iloc[-1]
                start = (frames[0], float(row_first["Original Coordinate X"]), float(row_first["Original Coordinate Y"]))
                end = (frames[-1], float(row_last["Original Coordinate X"]), float(row_last["Original Coordinate Y"]))
                if "Spot Center X" in group.columns and "Spot Center Y" in group.columns:
                    spot_centers = []
                    for x, y in zip(group["Spot Center X"], group["Spot Center Y"]):
                        sx = float(x) if pd.notnull(x) and x != "" else None
                        sy = float(y) if pd.notnull(y) and y != "" else None
                        spot_centers.append((sx, sy) if sx is not None and sy is not None else None)
                else:
                    spot_centers = []
                traj_coords = list(zip(group["Original Coordinate X"], group["Original Coordinate Y"]))
                try:
                    traj_centers = list(zip(group["Search Center X"], group["Search Center Y"]))
                except:
                    traj_centers = traj_coords
                frames_used = frames

                # 2) check for a consistent “yes” background flag
                if "Background from trajectory" in group:
                    flags = (
                        group["Background from trajectory"]
                        .dropna()
                        .astype(str)
                        .str.strip()
                        .str.lower()
                    )
                    if not flags.empty and flags.eq("yes").all():
                        # pull all numeric backgrounds in this group
                        if "Background" in group:
                            uniq_bg = group["Background"].dropna().unique()
                            if len(uniq_bg) == 1:
                                fixed_background = float(uniq_bg[0])
                            else:
                                # ambiguous numeric values → discard
                                fixed_background = None

                # Attempt to retrieve velocities from the imported file.
                if ("Avg. Speed (px/frame)" in group.columns) and ("Speed (px/frame)" in group.columns):
                    # Use the value from the first row (or any row) as the summary,
                    # and obtain the list from the 'Speed (px/frame)' column.
                    try:
                        average_velocity = float(row_first["Avg. Speed (px/frame)"])
                    except Exception:
                        average_velocity = None
                    # Convert the velocities column to a list.
                    velocities = group["Speed (px/frame)"].tolist()
                else:
                    # Recalculate velocities based on spot centers.
                    velocities = []
                    for i in range(1, len(frames_used)):
                        if spot_centers[i-1] is None or spot_centers[i] is None:
                            velocities.append(None)
                        else:
                            dx = spot_centers[i][0] - spot_centers[i-1][0]
                            dy = spot_centers[i][1] - spot_centers[i-1][1]
                            velocities.append(np.hypot(dx, dy))
                    if velocities:
                        valid_vels = [v for v in velocities if v is not None]
                        average_velocity = float(np.mean(valid_vels)) if valid_vels else None
                    else:
                        average_velocity = None

            colors = ['magenta' if sc is not None else 'grey' for sc in spot_centers]
            traj = {
                "trajectory_number": int(traj_num),
                "channel": channel,
                "start": start,
                "end": end,
                "anchors": anchors,
                "roi": roi,
                "spot_centers": spot_centers,
                "sigmas": sigmas,
                "peaks": peaks,
                "fixed_background": fixed_background,
                "background": background,
                "frames": frames_used,
                "original_coords": traj_coords,
                "search_centers": traj_centers,
                "intensities": intensities,
                "average": avg,
                "median": med,
                "colors": colors,
                "velocities": velocities,
                "average_velocity": average_velocity
            }

            traj_id = traj["trajectory_number"]
            # copy in any loaded‐back user fields:
            traj["custom_fields"] = self._custom_load_map.get(traj_id, {})

            loaded.append(traj)
            progress.setValue(t+1)
            QApplication.processEvents()

        progress.close()

        self.trajectories = loaded

        self.table_widget.setRowCount(0)

        for traj in loaded:

            avg_vel_um_s_txt = ""
            if self.navigator.pixel_size is not None and self.navigator.frame_interval is not None and traj["average_velocity"] is not None:
                velocity_nm_per_ms = (traj["average_velocity"] * self.navigator.pixel_size) / self.navigator.frame_interval
                avg_vel_um_s_txt = f"{velocity_nm_per_ms:.2f}"

            dx = traj['end'][1] - traj['start'][1]
            dy = traj['end'][2] - traj['start'][2]
            distance_px = np.hypot(dx, dy)
            time_fr = traj['end'][0]-traj['start'][0]
            distance_um_txt = ""
            time_s_txt = ""
            overall_vel_um_s_txt = ""
            if self.navigator.pixel_size is not None and self.navigator.frame_interval is not None and time_fr > 0:
                distance_um = distance_px * self.navigator.pixel_size / 1000
                time_s = time_fr * self.navigator.frame_interval / 1000
                overall_vel_um_s = distance_um/time_s
                distance_um_txt = f"{distance_um:.2f}"
                time_s_txt = f"{time_s:.2f}"
                overall_vel_um_s_txt = f"{overall_vel_um_s:.2f}"

            num_points = len(traj['frames'])
            valid_points = sum(1 for val in traj['intensities'] if val is not None and val > 0)
            percent_valid = int(100 * valid_points / num_points) if num_points > 0 else 0

            ch = traj.get("channel")
            channel_str = str(ch) if ch is not None else ""

            row = self.table_widget.rowCount()
            
            self.table_widget.insertRow(row)
            self.writeToTable(row, "num", str(traj["trajectory_number"]))
            if self.navigator.movieChannelCombo.count() > 1:
                self.writeToTable(row, "channel", channel_str)
            self.writeToTable(row, "startframe", str(int(traj["start"][0]) + 1))
            self.writeToTable(row, "endframe",   str(int(traj["end"][0])   + 1))
            start_xy = f"{traj['start'][1]:.1f}, {traj['start'][2]:.1f}" if traj['start'][1] is not None else "N/A"
            end_xy   = f"{traj['end'][1]:.1f}, {traj['end'][2]:.1f}" if traj['end'][1] is not None else "N/A"
            self.writeToTable(row, "startcoord", start_xy)
            self.writeToTable(row, "endcoord", end_xy)
            self.writeToTable(row, "distance", distance_um_txt)
            self.writeToTable(row, "time", time_s_txt)
            self.writeToTable(row, "netspeed", overall_vel_um_s_txt)  
            self.writeToTable(row, "total", str(num_points))
            self.writeToTable(row, "valid", str(percent_valid))
            median_text = "" if traj['median'] is None else f"{traj['median']:.2f}"
            avg_text = "" if traj['average'] is None else f"{traj['average']:.2f}"
            self.writeToTable(row, "medintensity", median_text)
            self.writeToTable(row, "avgintensity", avg_text)
            self.writeToTable(row, "avgspeed", avg_vel_um_s_txt)
            for col in self.custom_columns:
                val = traj.get("custom_fields", {}).get(col, "")
                if val is None or (isinstance(val, float) and math.isnan(val)) or pd.isna(val):
                    val = ""
                # Make sure the column really exists in the table:
                if col in self._col_index:
                    self.writeToTable(row, col, str(val))

        self._trajectory_counter = max(traj["trajectory_number"] for traj in loaded) + 1

        if self.table_widget.rowCount() > 0:
            self.table_widget.selectRow(0)

        if self.navigator.traj_overlay_button.isChecked():
            self.movieCanvas.draw_trajectories_on_movie()
            self.kymoCanvas.draw_trajectories_on_kymo()

        self.movieCanvas.draw()
        self.kymoCanvas.draw()

    def updateTableRow(self, row, traj):
        """
        1) replace the dict in self.trajectories[row]
        2) clear that row in the QTableWidget
        3) recompute summary fields and write them back
        """
        try:
            self.trajectories[row] = traj
        except Exception as e:
            QMessageBox.critical(self, "", f"Error: {str(e)}")
            return
        # clear old items
        for col in range(self.table_widget.columnCount()):
            self.table_widget.takeItem(row, col)

        # grab everything back out…
        start = traj["start"]
        end   = traj["end"]
        frames = traj["frames"]
        ints   = traj["intensities"]
        avg_int = traj["average"]
        med_int = traj["median"]
        avg_vel_pf = traj["average_velocity"]

        # compute μm distance / time / speed
        dx = end[1] - start[1]; dy = end[2] - start[2]
        px_dist = np.hypot(dx, dy)
        dt_fr   = end[0] - start[0]

        if (self.navigator.pixel_size is not None and
            self.navigator.frame_interval is not None and
            dt_fr > 0):
            μm_dist = px_dist * self.navigator.pixel_size / 1000
            secs    = dt_fr * self.navigator.frame_interval / 1000
            net_sp  = μm_dist / secs
            dist_txt   = f"{μm_dist:.2f}"
            time_txt   = f"{secs:.2f}"
            netspeed_txt = f"{net_sp:.2f}"
        else:
            dist_txt = time_txt = netspeed_txt = ""

        total_pts = len(frames)
        valid_pts = sum(1 for v in ints if v and v>0)
        valid_pct = int(100 * valid_pts / total_pts) if total_pts else 0

        avg_txt = "" if avg_int is None else f"{avg_int:.2f}"
        med_txt = "" if med_int is None else f"{med_int:.2f}"
        avvel_txt = ""
        if (self.navigator.pixel_size is not None and
            self.navigator.frame_interval is not None and
            avg_vel_pf is not None):
            μmps = (avg_vel_pf * self.navigator.pixel_size) / self.navigator.frame_interval
            avvel_txt = f"{μmps:.2f}"

        ch = traj.get("channel")
        channel_str = str(ch) if ch is not None else ""

        # write back exactly the same columns you do in add_trajectory…
        self.writeToTable(row, "num",          str(traj["trajectory_number"]))
        if self.navigator.movieChannelCombo.count() > 1:
            self.writeToTable(row, "channel", channel_str)
        self.writeToTable(row, "startframe",   str(int(start[0]) + 1))
        self.writeToTable(row, "endframe",     str(int(end[0])   + 1))
        self.writeToTable(row, "startcoord",   f"{start[1]:.1f}, {start[2]:.1f}")
        self.writeToTable(row, "endcoord",     f"{end[1]:.1f}, {end[2]:.1f}")
        self.writeToTable(row, "distance",     dist_txt)
        self.writeToTable(row, "time",         time_txt)
        self.writeToTable(row, "netspeed",     netspeed_txt)
        self.writeToTable(row, "total",        str(total_pts))
        self.writeToTable(row, "valid",        str(valid_pct))
        self.writeToTable(row, "medintensity", med_txt)
        self.writeToTable(row, "avgintensity", avg_txt)
        self.writeToTable(row, "avgspeed",     avvel_txt)

        for col in self.custom_columns:
            # get the saved string (or "")
            val = traj.get("custom_fields", {}).get(col, "")
            # write it back into the table
            self.writeToTable(row, col, str(val))

    def shortcut_recalculate(self):
        # figure out how many are selected
        selected = self.table_widget.selectionModel().selectedRows()
        if len(selected) > 1:
            # ask user before proceeding
            reply = QMessageBox.question(
                self,
                "Recalculate Trajectories",
                "Recalculate selected trajectories?",
                QMessageBox.Ok | QMessageBox.Cancel,
                QMessageBox.Ok
            )
            if reply != QMessageBox.Ok:
                return
        # now do the recalc without showing the RecalcDialog again
        self.recalculate_trajectory(prompt=False)
        self.navigator.flash_message("Recalculated")

    def recalculate_trajectory(self, prompt=True):
        # 1) gather selection
        rows = [idx.row() for idx in self.table_widget.selectionModel().selectedRows()]
        if not rows:
            QMessageBox.warning(self, "", "Select at least one trajectory to recalculate")
            return

        # --- BACKUP originals ---
        originals = {r: copy.deepcopy(self.trajectories[r]) for r in rows}

        # 2) single‐trajectory: do it inline on the GUI thread
        if len(rows) == 1:
            row = rows[0]
            old = self.trajectories[row]

            # — build the (frame,x,y) list exactly as in your worker's _build_pts_for —
            anchors, roi = old["anchors"], old["roi"]
            if len(anchors) > 1 and roi is not None:
                pts = []
                for i in range(len(anchors) - 1):
                    f1, x1, _ = anchors[i]
                    f2, x2, _ = anchors[i+1]
                    seg = range(f1, f2+1) if i == 0 else range(f1+1, f2+1)
                    xs = np.linspace(x1, x2, len(seg), endpoint=True)
                    for j, f in enumerate(seg):
                        mx, my = compute_roi_point(roi, xs[j])
                        pts.append((f, mx, my))
            else:
                pts = [
                    (f, x, y)
                    for f, (x, y) in zip(old["frames"], old["original_coords"])
                ]

            try:
                trajectory_background = self.navigator.compute_trajectory_background(
                    self.navigator.get_movie_frame,
                    pts,
                    crop_size=int(2 * self.navigator.searchWindowSpin.value())
                )
                # — call compute_analysis on the GUI thread so its own dialog appears —
                frames, _, centers, ints, fit, background, colors = \
                    self.navigator._compute_analysis(pts, trajectory_background, showprogress=True)
            except Exception as e:
                print(f"recalculate _compute failed: {e}")
                self.navigator.cancelled = True

            if self.navigator.cancelled:
                self.navigator.cancelled = False
                return

            # — rebuild the trajectory dict exactly as in _recalculate_one —
            spots   = [p[0] for p in fit]
            sigmas  = [p[1] for p in fit]
            peaks   = [p[2] for p in fit]
            valid   = [v for v,s in zip(ints, spots) if v and v>0 and s]
            avg_int = float(np.mean(valid)) if valid else None
            med_int = float(np.median(valid)) if valid else None

            vels = []
            for i in range(1, len(spots)):
                p0, p1 = spots[i-1], spots[i]
                if p0 is None or p1 is None:
                    vels.append(None)
                else:
                    vels.append(np.hypot(p1[0]-p0[0], p1[1]-p0[1]))
            good_vels = [v for v in vels if v is not None]
            avg_vpf   = float(np.mean(good_vels)) if good_vels else None

            traj_data = {
                "trajectory_number": old["trajectory_number"],
                "channel": old["channel"],
                "start":    old["start"],
                "end":      old["end"],
                "anchors":  anchors,
                "roi":      roi,
                "spot_centers": spots,
                "sigmas":      sigmas,
                "peaks":       peaks,
                "fixed_background": trajectory_background,
                "background": background,
                "frames":      old["frames"],
                "original_coords": old["original_coords"],
                "search_centers":  centers,
                "intensities":     ints,
                "average":         avg_int,
                "median":          med_int,
                "colors":          colors,
                "velocities":      vels,
                "average_velocity":avg_vpf
            }

            traj_data["custom_fields"] = originals[row].get("custom_fields", {}).copy()

            # — swap it in and refresh UI —
            self.updateTableRow(row, traj_data)
            if self.navigator.traj_overlay_button.isChecked():
                self.on_trajectory_selected_by_index(rows[0])
            return


        # 3) multi‐trajectory: optionally show RecalcDialog
        if prompt:
            mode = getattr(self.navigator, "tracking_mode", "Independent")
            rad  = self.navigator.searchWindowSpin.value()
            dlg  = RecalcDialog(mode, rad,
                                message=f"{len(rows)} trajectories need recalc",
                                parent=self)
            if dlg.exec_() != QDialog.Accepted:
                return
            # apply any mode/radius changes
            self.navigator.searchWindowSpin.setValue(dlg.new_radius)
            self.navigator.tracking_mode = dlg.new_mode
            if hasattr(self, "trackingModeCombo"):
                self.trackingModeCombo.setCurrentText(dlg.new_mode)

        # 4) set up master progress + thread
        total_frames = sum(len(self.trajectories[r]["frames"]) for r in rows)
        master = QProgressDialog("Recalculating", "Cancel", 0, total_frames, self)
        master.setWindowModality(Qt.WindowModal)
        master.setMinimumDuration(0)
        master.show()

        self.navigator._suppress_internal_progress = True

        worker = RecalcWorker(rows, self.trajectories, self.navigator)
        thread = QThread(self)
        worker.moveToThread(thread)
        self._recalc_thread = thread
        self._recalc_worker = worker

        worker.progress.connect(master.setValue)
        master.canceled.connect(worker.cancel)

        def on_finished(results):
            # apply only the successful traj_data
            for row, traj_data in results:
                self.trajectories[row] = traj_data
                self.updateTableRow(row, traj_data)
            cleanup()

        worker.finished.connect(on_finished)

        def on_canceled():
            # restore backups
            for row, old in originals.items():
                self.trajectories[row] = old
                self.updateTableRow(row, old)
            cleanup()

        worker.canceled.connect(on_canceled)

        def cleanup():
            master.close()
            self.navigator._suppress_internal_progress = False
            thread.quit()
            thread.wait()
            # redraw if overlay is on
            if self.navigator.traj_overlay_button.isChecked():
                self.on_trajectory_selected_by_index(rows[0])

        thread.started.connect(worker.run)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    def recalculate_all_trajectories(self):
        if not self.trajectories:
            QMessageBox.warning(self, "", "No Trajectories.")
            return

        # — same dialog logic —
        mode = getattr(self.navigator, "tracking_mode", "Independent")
        rad  = self.navigator.searchWindowSpin.value()
        dlg  = RecalcDialog(mode, rad, message="", parent=self)
        if dlg.exec_() != QDialog.Accepted:
            return
        # apply any mode/radius changes
        self.navigator.searchWindowSpin.setValue(dlg.new_radius)
        self.navigator.tracking_mode = dlg.new_mode
        if hasattr(self, "trackingModeCombo"):
            self.trackingModeCombo.setCurrentText(dlg.new_mode)

        # backup original trajectories
        backup = copy.deepcopy(self.trajectories)

        # prepare progress dialog
        total_frames = sum(len(t["frames"]) for t in backup)
        master = QProgressDialog("Recalculating", "Cancel", 0,
                                total_frames, self)
        master.setWindowModality(Qt.WindowModal)
        master.setMinimumDuration(0)
        master.show()

        # suppress any internal dialogs
        self.navigator._suppress_internal_progress = True
        count = 0
        canceled = False

        try:
            for row, old in enumerate(backup):
                # build pts with explicit if/elif/else
                if mode == "Same center":
                    pts = [
                        (f, x0, y0)
                        for f, spot in zip(old["frames"], old["spot_centers"])
                        if spot is not None
                        for x0, y0 in [spot]
                    ]
                elif len(old["anchors"]) > 1 and old.get("roi") is not None:
                    anchors, roi = old["anchors"], old["roi"]
                    pts = []
                    for i in range(len(anchors) - 1):
                        f1, x1, y1 = anchors[i]
                        f2, x2, y2 = anchors[i+1]
                        seg = range(f1, f2+1) if i == 0 else range(f1+1, f2+1)
                        xs = np.linspace(x1, x2, len(seg), endpoint=True)
                        for j, f in enumerate(seg):
                            mx, my = compute_roi_point(roi, xs[j])
                            pts.append((f, mx, my))
                else:
                    pts = [
                        (f, x, y)
                        for f, (x, y) in zip(old["frames"], old["original_coords"])
                    ]

                # perform analysis

                trajectory_background = self.navigator.compute_trajectory_background(
                    self.navigator.get_movie_frame,
                    pts,
                    crop_size=int(2 * self.navigator.searchWindowSpin.value())
                )

                try:
                    frames, _, search_centers, ints, fit, background, colors = \
                        self.navigator._compute_analysis(pts, trajectory_background, showprogress=False)
                except Exception as e:
                    print(f"_compute failed on recalc all: {e} at index {row}")
                    continue

                # unpack & stats
                spots  = [p[0] for p in fit]
                sigmas = [p[1] for p in fit]
                peaks  = [p[2] for p in fit]
                valid_ints = [v for v, s in zip(ints, spots) if v and v > 0 and s]
                avg_int = float(np.mean(valid_ints)) if valid_ints else None
                med_int = float(np.median(valid_ints)) if valid_ints else None

                # build full lists
                full_centers, full_sigmas, full_peaks = [], [], []
                full_ints, full_colors = [], []
                for f in old["frames"]:
                    if f in frames:
                        idx = frames.index(f)
                        full_centers.append(spots[idx])
                        full_sigmas.append(sigmas[idx])
                        full_peaks.append(peaks[idx])
                        full_ints.append(ints[idx])
                        full_colors.append(colors[idx])
                    else:
                        full_centers.append(None)
                        full_sigmas.append(None)
                        full_peaks.append(None)
                        full_ints.append(None)
                        full_colors.append("grey")

                # velocities
                vels = []
                for i in range(1, len(spots)):
                    p0, p1 = spots[i-1], spots[i]
                    if p0 is None or p1 is None:
                        vels.append(None)
                    else:
                        vels.append(np.hypot(p1[0]-p0[0], p1[1]-p0[1]))
                good_vels = [v for v in vels if v is not None]
                avg_vpf   = float(np.mean(good_vels)) if good_vels else None

                # assemble new trajectory
                traj_data = {
                    "trajectory_number": old["trajectory_number"],
                    "channel":           old["channel"],
                    "start":             old["start"],
                    "end":               old["end"],
                    "anchors":           old["anchors"],
                    "roi":               old["roi"],
                    "spot_centers":      spots,
                    "sigmas":            sigmas,
                    "peaks":             peaks,
                    "fixed_background":  trajectory_background,
                    "background":        background,
                    "frames":            old["frames"],
                    "original_coords":   old["original_coords"],
                    "search_centers":    search_centers,
                    "intensities":       ints,
                    "average":           avg_int,
                    "median":            med_int,
                    "colors":            colors,
                    "velocities":        vels,
                    "average_velocity":  avg_vpf
                }

                traj_data["custom_fields"] = old.get("custom_fields", {}).copy()

                # update model + table
                self.trajectories[row] = traj_data
                self.updateTableRow(row, traj_data)

                # update progress & check cancel
                count += len(old["frames"])
                master.setValue(count)
                QApplication.processEvents()
                if master.wasCanceled():
                    canceled = True
                    break
        finally:
            # always restore internal state & close dialog
            self.navigator._suppress_internal_progress = False
            master.close()
            if canceled:
                # restore backup on cancel
                for r, orig in backup.items():
                    self.trajectories[r] = orig
                    self.updateTableRow(r, orig)

        # only reset and redraw if not canceled
        if not canceled:
            self._trajectory_counter = (
                max(t["trajectory_number"] for t in self.trajectories) + 1
            )
            self.on_trajectory_selected_by_index(0)
            self.table_widget.selectRow(0)

    def toggle_trajectory_markers(self):
        if self.navigator.traj_overlay_button.isChecked():
            self.kymoCanvas.draw_trajectories_on_kymo()
            if self.navigator is not None:
                self.movieCanvas.draw_trajectories_on_movie()
        else:
            self.kymoCanvas.clear_kymo_trajectory_markers()
            self.movieCanvas.clear_movie_trajectory_markers()
        
        self.movieCanvas.draw()
        self.kymoCanvas.draw()

    def delete_selected_trajectory(self):
        selected_rows = self.table_widget.selectionModel().selectedRows()
        if not selected_rows:
            # print("❌ No trajectory selected to delete!")
            return
        row = selected_rows[0].row()
        self.trajectories.pop(row)
        self.table_widget.removeRow(row)

        row_count = self.table_widget.rowCount()
        if row_count > 0:
            # if we deleted a later row, move up one; otherwise stay at 0
            new_row = row - 1 if row > 0 else 0
            # clamp to the last row if we deleted the last row
            new_row = min(new_row, row_count - 1)
            # select that row
            self.table_widget.selectRow(new_row)

        self.movieCanvas.draw_trajectories_on_movie()

        if row_count == 0:
            self._trajectory_counter = 1
            
        if self.navigator is not None:
            self.kymoCanvas.draw_trajectories_on_kymo()

        self.navigator.update_table_visibility()

        self.movieCanvas.draw()
        self.kymoCanvas.draw()

    def clear_trajectories(self, prompt=True):
        reply = QMessageBox.Yes
        if prompt:
            reply = QMessageBox.question(
                self,
                "Clear Trajectories",
                "Are you sure you want to clear all trajectories?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
        if reply == QMessageBox.Yes:
            for traj in self.trajectories:
                for key in ('frames', 'original_coords', 'search_centers', 'intensities', 'spot_centers',
                            'sigmas', 'peaks', 'colors', 'average_velocity',
                            'velocities'):
                    if key in traj:
                        traj[key] = None
            self.trajectories = []
            self.table_widget.setRowCount(0)
            self._trajectory_counter = 1
            if self.navigator is not None:
                self.kymoCanvas.clear_kymo_trajectory_markers()
                self.movieCanvas.clear_movie_trajectory_markers()
            self.navigator.update_table_visibility()

            self.kymoCanvas.draw()
            self.movieCanvas.draw()

    def update_trajectory_visibility(self):
        has_rows = self.table_widget.rowCount() > 0
        if not has_rows:
            # Hide (collapse) the trajectory canvas by setting its size to zero
            total_height = self.navigator.vertSplitter.height()
            self.navigator.vertSplitter.setSizes([total_height, 0])
        else:
            # Give some proportion (e.g., 70% to main content, 30% to trajectory canvas)
            total_height = self.navigator.vertSplitter.height()
            self.navigator.vertSplitter.setSizes([int(0.7 * total_height), int(0.3 * total_height)])


    def open_context_menu(self, pos):
        # 1) Figure out which row was clicked on
        index_under = self.table_widget.indexAt(pos)
        if not index_under.isValid():
            return
        row_under = index_under.row()

        # 2) Use current multi-selection only if it includes that row,
        #    otherwise, just operate on the clicked row.
        selected_indexes = self.table_widget.selectionModel().selectedRows()
        if any(idx.row() == row_under for idx in selected_indexes):
            rows = [idx.row() for idx in selected_indexes]
        else:
            rows = [row_under]
        n = len(rows)
        save_label = (f"Save trajectory {rows[0]+1}" 
                    if n == 1 
                    else "Save selected trajectories")
        menu  = QMenu(self.table_widget)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground)
        menu.setStyleSheet("""
            /* the menu “shell” */
            QMenu {
                background-color: #FFFFFF;
                border: 1px solid #CCCCCC;
                border-radius: 8px;
                padding: 4px;          /* space around items */
            }
            /* each menu item */
            QMenu::item {
                padding: 4px;
                color: #333333;
                background: transparent;
            }
            /* hovered or selected item */
            QMenu::item:selected {
                background-color: #E8F0FE;  /* light blue */
                color: #333333;             /* keep text dark */
            }
        """)
        # Save action
        act_save   = menu.addAction(save_label)
        act_save.triggered.connect(lambda: self.save_trajectories(rows))


        # 2) If exactly one row is selected, add Go→kymograph entries
        if len(rows) == 1:
            r = rows[0]
            traj = self.trajectories[r]
            sf, sx, sy = traj["start"]
            ef, ex, ey = traj["end"]

            # 1) find any ROI that contains both start & end
            for roi_name, roi in self.navigator.rois.items():
                if (is_point_near_roi((sx, sy), roi)
                and is_point_near_roi((ex, ey), roi)):

                    # 2) now find all kymographs for that ROI
                    for kymo_name, info in self.navigator.kymo_roi_map.items():
                        if info["roi"] == roi_name:
                            action = menu.addAction(f"Go to kymograph {kymo_name}")
                            def make_cb(kn=kymo_name, row=r):
                                def cb():
                                    self.on_trajectory_selected_by_index(row)
                                    idx = self.navigator.kymoCombo.findText(kn)
                                    if idx >= 0:
                                        self.navigator.kymoCombo.setCurrentIndex(idx)
                                        self.navigator.kymograph_changed()
                                return cb
                            action.triggered.connect(make_cb())

                    # only need the first matching ROI
                    break

        # Change ID action
        # change_label = "Change trajectory ID" if n == 1 else "Change trajectories' IDs"
        # act_change = menu.addAction(change_label)
        # act_change.triggered.connect(lambda: self.change_trajectory_id(rows))

        if self.custom_columns:
            menu.addSeparator()
            seen = set()
            for col_name in self.custom_columns:
                col_type = self._column_types.get(col_name, "binary")

                if col_type == "binary":
                    marked_flags = [
                        bool(self.trajectories[r]["custom_fields"].get(col_name, ""))
                        for r in rows
                    ]
                    if len(rows) == 1:
                        act_text = (
                            f"Unmark as {col_name}"
                            if marked_flags[0]
                            else f"Mark as {col_name}"
                        )
                    else:
                        act_text = (
                            f"Unmark selected as {col_name}"
                            if all(marked_flags)
                            else f"Mark selected as {col_name}"
                        )

                    # skip duplicates
                    if act_text in seen:
                        continue
                    seen.add(act_text)

                    act = menu.addAction(act_text)
                    act.triggered.connect(
                        lambda _, name=col_name, sel=rows: self._toggle_binary_column(name, sel)
                    )

                else:  # “value” column
                    if len(rows) == 1:
                        act_text = f"Set {col_name}"
                    else:
                        act_text = f"Set all {col_name}"

                    # skip duplicates
                    if act_text in seen:
                        continue
                    seen.add(act_text)

                    act = menu.addAction(act_text)
                    act.triggered.connect(
                        lambda _, name=col_name, sel=rows: self._set_value_column(name, sel)
                    )

        menu.exec_(self.table_widget.viewport().mapToGlobal(pos))

    def _toggle_binary_column(self, col_name, rows):
        """
        For each row in `rows`, either mark or unmark the binary column `col_name`.
        """
        # decide whether to mark (True) or unmark (False)
        current = [
            bool(self.trajectories[r]["custom_fields"].get(col_name, ""))
            for r in rows
        ]
        mark = not all(current)

        for r in rows:
            if mark:
                # set it to “Yes” (or whatever non‐empty marker you prefer)
                self._mark_custom(r, col_name, "Yes")
            else:
                self._unmark_custom(r, col_name)

        self.kymoCanvas.draw_trajectories_on_kymo()

    def _set_value_column(self, col_name, rows):
        """
        Pop up a dialog to get a new value, then set it on every row in `rows`.
        """
        prompt = f"Enter value for {col_name}:"
        val, ok = QInputDialog.getText(self, f"Set {col_name}", prompt)
        if not ok:
            return
        for r in rows:
            # store in your model
            self.trajectories[r].setdefault("custom_fields", {})[col_name] = val
            # update the table
            self.writeToTable(r, col_name, val)

    def _on_header_context_menu(self, pos):
        header = self.table_widget.horizontalHeader()
        col = header.logicalIndexAt(pos)
        if col < 0:
            return

        col_name = self._headers[col]
        menu = QMenu(self.table_widget)
        # make it frameless + translucent
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground)
        # apply the custom stylesheet
        menu.setStyleSheet("""
            /* the menu “shell” */
            QMenu {
                background-color: #FFFFFF;
                border: 1px solid #CCCCCC;
                border-radius: 8px;
                padding: 4px;          /* space around items */
            }
            /* each menu item */
            QMenu::item {
                padding: 6px 12px;
                color: #333333;
                background: transparent;
            }
            /* hovered or selected item */
            QMenu::item:selected {
                background-color: #E8F0FE;  /* light blue */
                color: #333333;             /* keep text dark */
            }
        """)

        # always allow adding a new column
        # 1) Add binary column
        bin_act = menu.addAction("Add binary column")
        bin_act.triggered.connect(self._add_binary_column_dialog)

        # 2) Add value column
        val_act = menu.addAction("Add value column")
        val_act.triggered.connect(self._add_value_column_dialog)

        # if this is one of the custom columns, allow removal
        if col_name in self.custom_columns:
            # check for non-empty cells first in _ask_remove_column
            remove_act = menu.addAction(f"Remove column “{col_name}”")
            remove_act.triggered.connect(lambda _, c=col, name=col_name: self._ask_remove_column(c, name))

        # finally show the menu right at the header click
        menu.exec_(header.mapToGlobal(pos))

    def _add_binary_column_dialog(self):
        name, ok = QInputDialog.getText(self, "Add Binary Column", "Name:")
        if not (ok and name.strip()):
            return
        name = name.strip()
        if name.lower() in {h.lower() for h in self._headers}:
            QMessageBox.warning(self, "Duplicate", f"“{name}” already exists.")
            return
        # pass col_type="binary" so we default every cell to "Yes"
        self._add_custom_column(name, col_type="binary")

    def _add_value_column_dialog(self):
        name, ok = QInputDialog.getText(self, "Add Value Column", "Name:")
        if not (ok and name.strip()):
            return
        name = name.strip()
        if name.lower() in {h.lower() for h in self._headers}:
            QMessageBox.warning(self, "Duplicate", f"“{name}” already exists.")
            return
        # pass col_type="value" so we leave cells empty for user to fill
        self._add_custom_column(name, col_type="value")

    def _add_column_dialog(self):
        """
        Ask for a new column name, but only accept it if it doesn't
        case-insensitively collide with any existing header.
        """
        existing = {h.lower() for h in self._headers}
        name, ok = QInputDialog.getText(self, "Add Column", "Column name:")
        if not ok:
            return
        name = name.strip()
        if not name:
            return
        if name.lower() in existing:
            QMessageBox.warning(self, "Duplicate column", f"'{name}' already exists.")
            return

        self._add_custom_column(name)

    def _add_custom_column(self, name, *, col_type="binary"):
        # ——— 0) save current row selection ———
        selected_rows = [idx.row()
                         for idx in self.table_widget.selectionModel().selectedRows()]
        
        self._column_types[name] = col_type

        # 1) track it
        self.custom_columns.append(name)

        # 2) extend your header lists & mappings
        self._headers.append(name)
        idx = len(self._headers) - 1
        self._col_index[name] = idx
        self._aliases[name.lower()] = name

        # 3) insert the column into the table widget
        self.table_widget.insertColumn(idx)
        self.table_widget.setHorizontalHeaderLabels(self._headers)

        # 4) give it a reasonable default width & make it resizable
        self.table_widget.horizontalHeader().setSectionResizeMode(
            idx, QHeaderView.Interactive)
        self.table_widget.setColumnWidth(idx, 100)

        # 5) initialize existing rows to empty string *and* store in data model
        default = "" if col_type=="binary" else ""
        for row in range(self.table_widget.rowCount()):
            self.writeToTable(row, name, default)
            self.trajectories[row].setdefault("custom_fields", {})[name] = default

        # 6) re-apply full-row selection so that the new column is highlighted
        for row in selected_rows:
            self.table_widget.selectRow(row)

        # 7) final layout tweaks
        self.table_widget.viewport().update()
        self.table_widget.horizontalHeader().resizeSections(
            QHeaderView.ResizeToContents)

        if col_type == "binary":
            self.navigator._rebuild_color_by_actions()

    def _ask_remove_column(self, col_idx, col_name):
        # Check for any non-empty cells
        has_values = any(
            (self.table_widget.item(r, col_idx) or QTableWidgetItem("")).text().strip()
            for r in range(self.table_widget.rowCount())
        )
        if has_values:
            reply = QMessageBox.question(
                self,
                f"Remove “{col_name}”?",
                f"Column “{col_name}” still has values. Remove and lose all data?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return

        self._remove_custom_column(col_idx, col_name)

    def _remove_custom_column(self, col_idx, col_name):
        # 1) remove from data model
        self.custom_columns.remove(col_name)
        self._headers.pop(col_idx)
        self._col_index.pop(col_name, None)
        self._aliases.pop(col_name.lower(), None)

        # 2) drop from each trajectory
        for traj in self.trajectories:
            traj.get("custom_fields", {}).pop(col_name, None)

        # 3) remove from widget
        self.table_widget.removeColumn(col_idx)
        self.table_widget.setHorizontalHeaderLabels(self._headers)
        self.table_widget.viewport().update()

        self.navigator._rebuild_color_by_actions()

    def _mark_custom(self, row, col_name, value="Yes"):
        # 1) update data model
        self.trajectories[row].setdefault("custom_fields", {})[col_name] = value
        # 2) update UI
        self.writeToTable(row, col_name, value)

    def _unmark_custom(self, row, column_name):
        # 1) update data-model: clear or remove the custom_fields entry
        cf = self.trajectories[row].get("custom_fields", {})
        if column_name in cf:
            # either delete it entirely...
            cf.pop(column_name)
            # ...or just set to empty string if you need to keep the key:
            # cf[column_name] = ""
        # 2) update the widget
        self.writeToTable(row, column_name, "")

# -----------------------------
# HistogramCanvas
# -----------------------------

class HistogramCanvas(FigureCanvas):

    def __init__(self, navigator, parent=None):
        self.navigator = navigator
        self.fig = Figure(figsize=(5.5, 3), constrained_layout=True)
        self.ax = self.fig.add_subplot(111)
        self.fig.patch.set_facecolor("white")
        self.ax.set_facecolor("white")
        super().__init__(self.fig)
        self.setParent(parent)
        self.current_background = None
        # Initially, hide the axes to appear blank.
        self.ax.axis("off")
        self.draw()
        self.fig.patch.set_alpha(0)
        self.ax.patch.set_alpha(0)
        self.ax.set_facecolor('none')
        self._update_pending = False
        self._last_histogram_params = (None, None, None, None, None, None)

    def resizeEvent(self, event):
        # Optionally, recalculate any subplot parameters here based on self.width() or self.height()
        # self.fig.subplots_adjust(left=0.16, right=0.95, bottom=0.3, top=0.9)
        # self.fig.tight_layout()  # Or set constrained_layout=True initially
        super().resizeEvent(event)

    def update_histogram(self, image, center, crop_size, sigma=None, intensity=None, background=None):
        """
        Instead of updating immediately, store the parameters and schedule an update
        only once per 16 ms (about 60 FPS).
        """
        throttletime = 2
        if not self.isVisible() or self.width() == 0 or self.height() == 0:
            throttletime = 400
        # Save the latest parameters
        self._last_histogram_params = (image, center, crop_size, sigma, intensity, background)
        if not self._update_pending:
            self._update_pending = True
            # Schedule a delayed update
            QTimer.singleShot(throttletime, self._throttled_histogram_update)

    def _throttled_histogram_update(self):
        """This method is called after a short delay to perform the heavy update."""
        self._update_pending = False
        image, center, crop_size, sigma, intensity, background = self._last_histogram_params
        self._do_update_histogram(image, center, crop_size, sigma, intensity, background)

    def _do_update_histogram(self, image, center, crop_size, sigma=None, intensity=None, background=None):
        # --- Decide which portion of the image to use.
        if center is None or np.isnan(center[0]) or np.isnan(center[1]):
            subimage = image
            criteria_counts = None  # no criteria histogram
        else:
            H, W = image.shape
            half = crop_size // 2
            cx_int = int(round(center[0]))
            cy_int = int(round(center[1]))
            x1_crop = max(0, cx_int - half)
            x2_crop = min(W, cx_int + half)
            y1_crop = max(0, cy_int - half)
            y2_crop = min(H, cy_int + half)
            subimage = image[y1_crop:y2_crop, x1_crop:x2_crop]

        if subimage.size == 0:
            return

        # --- Compute histogram on the subimage.
        counts, bin_edges = np.histogram(subimage, bins=50)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

        # --- background
        self.current_background = background

        # --- Optionally compute a criteria histogram if sigma is provided.
        if (center is not None and not np.isnan(center[0]) and not np.isnan(center[1])
                and sigma is not None):
            sub_center_x = cx_int - x1_crop
            sub_center_y = cy_int - y1_crop
            y_indices, x_indices = np.indices(subimage.shape)
            dist = np.sqrt((x_indices - sub_center_x)**2 + (y_indices - sub_center_y)**2)
            sigma_mask = dist <= 2 * sigma
            criteria_pixels = subimage[sigma_mask]
            criteria_counts, _ = np.histogram(criteria_pixels, bins=bin_edges)
        else:
            criteria_counts = None

        # --- Clear axis, plot the histogram, overlay criteria if available.
        self.ax.clear()
        self.ax.set_facecolor("white")
        self.ax.set_xlabel("Pixel Intensity", fontsize=12)
        self.ax.set_ylabel("Count", fontsize=12)
        self.ax.tick_params(axis='both', which='major', labelsize=12)
        
        bar_width = bin_edges[1] - bin_edges[0]
        self.ax.bar(bin_centers, counts, width=bar_width, color='#7da1ff', edgecolor='black')
        if criteria_counts is not None:
            self.ax.bar(bin_centers, criteria_counts, width=bar_width, color='magenta', edgecolor='black')
        
        try:
            if background is not None:
                # --- draw background threshold line only ---
                self.ax.axvline(
                    background,
                    color='black', linestyle='--', linewidth=1.5,
                    solid_capstyle='round',
                    dash_capstyle='round',
                    label=f"Background: {background:.2f}"
                )
                legend = self.ax.legend(
                    loc="upper right",
                    fontsize=10,
                    frameon=True,
                    labelspacing=0.5,
                    handlelength=2
                )
                # style the box
                frame = legend.get_frame()
                frame.set_facecolor("white")
                frame.set_alpha(0.8)
                frame.set_edgecolor("none")
                frame.set_boxstyle("round,pad=0.2")

            if not getattr(self.fig, "layout_engine", None):
                self.fig.subplots_adjust(left=0.17, right=0.95, bottom=0.3, top=0.9)
            self.draw()

        except np.linalg.LinAlgError:
            pass
            #print("Warning: Singular matrix encountered when drawing axvline")

# -----------------------------
# VelocityCanvas
# -----------------------------

class VelocityCanvas(FigureCanvas):
    def __init__(self, parent=None, navigator=None):
        # Create a figure with a white background.
        self.fig = Figure(figsize=(5.5, 3), constrained_layout=True)
        # Create a single axes.
        self.ax_vel = self.fig.add_subplot(111)
        # Set the axes background to white.
        self.ax_vel.set_facecolor('white')
        # Optionally remove all spines so no borders show:
        self.ax_vel.axis("off")
        self.fig.patch.set_alpha(0)
        self.ax_vel.patch.set_alpha(0)
        self.ax_vel.set_facecolor('none')
        # Initialize the FigureCanvas with this figure.
        super().__init__(self.fig)
        self.setParent(parent)
        self.current_index = 0

        self.navigator = navigator

        self.draw_idle()

    def resizeEvent(self, event):
        # Optionally, recalculate any subplot parameters here based on self.width() or self.height()
        # self.fig.subplots_adjust(left=0.16, right=0.95, bottom=0.3, top=0.9)
        # self.fig.tight_layout()  # Or set constrained_layout=True initially
        super().resizeEvent(event)
        self.draw_idle()

    def plot_velocity_histogram(self, velocities, ax=None):
        """
        Plot a histogram of velocities on the provided axes (or self.ax_vel).
        Also draws a vertical line showing the average speed
        and a vertical line showing the net speed (distance from first to last point divided by total frame difference)
        converted to μm/s.
        """
        if ax is None:
            ax = self.ax_vel

        # Filter out None values.
        valid_velocities = [v for v in velocities if v is not None]

        # Clear the axes.
        ax.clear()

        # --- Compute the net speed ---
        net_speed = None
        if (self.navigator is not None and
            hasattr(self.navigator, 'analysis_frames') and len(self.navigator.analysis_frames) >= 2 and
            hasattr(self.navigator, 'analysis_original_coords') and len(self.navigator.analysis_original_coords) >= 2):
            start_frame, start_x, start_y = self.navigator.analysis_start
            end_frame,   end_x,   end_y   = self.navigator.analysis_end
            dx = end_x - start_x
            dy = end_y - start_y
            dt = end_frame - start_frame
            dist_px = np.hypot(dx, dy)
            if (self.navigator.pixel_size is not None and
                self.navigator.frame_interval is not None):
                # Convert from pixels to micrometers.
                dist_um = dist_px * self.navigator.pixel_size / 1000.0
                dt_ms = dt * self.navigator.frame_interval
                dt_s = dt_ms / 1000.0
                net_speed = dist_um / dt_s if dt_s > 0 else 0
            else:
                # Fallback: use pixels per frame.
                net_speed = np.hypot(dx, dy) / (end_frame - start_frame)
        else:
            net_speed = 0  # or leave as None

        # Only draw the net speed line if we have some valid velocity data.
        if valid_velocities:
            try:
                # Attempt to draw the net speed vertical line.
                ax.axvline(net_speed, color='black', linestyle='--', linewidth=1.5,
                    solid_capstyle='round',
                    dash_capstyle='round',
                    label=f"Net: {net_speed:.2f}")
            except np.linalg.LinAlgError:
                pass
                # If the axis transform is singular, you can skip this drawing.
                #print("Warning: Cannot draw net speed line; transformation matrix is singular.")

        # --- Determine the units for the velocities ---
        if valid_velocities and self.navigator.pixel_size is not None and self.navigator.frame_interval is not None:
            conversion = self.navigator.pixel_size / self.navigator.frame_interval
            valid_velocities = np.array(valid_velocities) * conversion
            ax.set_xlabel("Speed (μm/s)", fontsize=12)
        else:
            ax.set_xlabel("Speed (px/frame)", fontsize=12)

        ax.set_ylabel("Count", fontsize=12)
        ax.tick_params(axis='both', which='major', labelsize=12)

        if len(valid_velocities) > 0:
            counts, bin_edges, _ = ax.hist(valid_velocities, bins='auto',
                                        color='#7da1ff', edgecolor='black')
            avg_velocity = np.mean(valid_velocities)
            try:
                # Attempt to draw the net speed vertical line.
                ax.axvline(avg_velocity, color='grey', linestyle='--', linewidth=1.5,
                    solid_capstyle='round',
                    dash_capstyle='round',
                    label=f"Avg: {avg_velocity:.2f}")
            except np.linalg.LinAlgError:
                # If the axis transform is singular, you can skip this drawing.
                print("Warning: Cannot draw net speed line; transformation matrix is singular.")
        else:
            avg_velocity = None
            ax.text(0.5, 0.5, "No valid speed data", ha='center', va='center',
                    transform=self.fig.transFigure, color='grey')
            ax.axis("off")

        legend = ax.legend(
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

        ax.figure.subplots_adjust(left=0.17, right=0.95, bottom=0.3, top=0.9)
        self.draw_idle()
        