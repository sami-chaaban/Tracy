import os, sys
import numpy as np
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QFileDialog, QLabel, QMessageBox, QComboBox, QSpinBox, QShortcut,
    QListView, QSlider, QSizePolicy, QAction, QDialog,
    QProgressDialog, QApplication, QFrame,
    QLineEdit, QFormLayout, QGraphicsDropShadowEffect,
    QInputDialog, QMenu
)

from PyQt5 import QtCore, QtWidgets, QtGui
from PyQt5.QtCore import (
    Qt, QTimer, QSize, QRectF, QPropertyAnimation, QEvent,
    QEasingCurve, QPropertyAnimation, QPoint, pyqtSlot, pyqtSignal,
    QThreadPool)
from PyQt5.QtGui import (
    QKeySequence, QIcon, QColor, QCursor, QMouseEvent
    )

import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
import tifffile
from scipy.ndimage import shift
from scipy.signal import savgol_filter
import zipfile
import time
import read_roi
import numpy as np
from functools import partial
import pandas as pd
from pathlib import Path
from .canvases import (
    KymoCanvas, MovieCanvas,
    IntensityCanvas, TrajectoryCanvas, HistogramCanvas,
    VelocityCanvas
    )
from .canvas_tools import (
    ContrastControlsWidget, ToggleSwitch,
    ChannelAxisDialog, SetScaleDialog,
    KymoLineOptionsDialog, CustomSplitter, 
    RoundedFrame, AxesRectAnimator, SaveKymographDialog,
    ClickableLabel, RadiusDialog
)
from .gaussian_tools import perform_gaussian_fit
from .roi_tools import is_point_near_roi, convert_roi_to_binary, parse_roi_blob, generate_multipoint_roi_bytes
from .track_tools import calculate_velocities

# from hmmlearn.hmm import GaussianHMM
# from sklearn.preprocessing import StandardScaler

QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)

class KymographNavigator(QMainWindow):
    # debugPlotRequested = pyqtSignal(list, list, list)
    # debug_plot_motion_segmentation_requested = pyqtSignal(list, list, list, list)
    # debug_plot_hmm_segmentation_requested = pyqtSignal(np.ndarray, np.ndarray, np.ndarray, list, np.ndarray)
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tracy")
        self.resize(1000, 1200)

        # self.debugPlotRequested.connect(self.debug_plot_track_smoothing)
        # self.debug_plot_motion_segmentation_requested.connect(self.debug_plot_motion_segmentation)
        # self.debug_plot_hmm_segmentation_requested.connect(self.debug_plot_hmm_segmentation)

        self.settings = {
            'widget-bg': "#FEFEFF",
        }

        self.setFocusPolicy(Qt.StrongFocus)
        self.setFocus()

        self.pixel_size = None        # in nanometers (nm)
        self.frame_interval = None    # in milliseconds (ms)

        self.movie = None
        self.kymographs = {}
        self.rois = {}
        self.live_update_mode = False
        self.analysis_start = None  # (frame_idx, x, y)
        self.analysis_end = None    # (frame_idx, x, y)
        self.last_frame_index = None
        self.cancelled=False

        self.tracking_mode = "Independent"

        # Store data from the last analysis run.
        self.analysis_frames = []
        self.analysis_original_coords = []
        self.analysis_search_centers = []

        # Set up looping through plot points using space bar.
        self.looping = False
        self.loop_index = 0
        self.loopTimer = QTimer(self)
        self.loopTimer.setInterval(100)  # 0.1 seconds
        self.loopTimer.timeout.connect(self.loop_points)

        self.kymo_roi_map = {}

        self.create_ui()
        self.create_menu()

        self.inverted_cmap = True
        self.invertAct.setChecked(True)
        self.toggle_invert_cmap(True)

        self.cancelShortcut = QShortcut(QKeySequence(Qt.Key_Escape), self)
        self.cancelShortcut.activated.connect(self.cancel_left_click_sequence)

        # Space bar shortcut to toggle looping.
        self.loopShortcut = QShortcut(QKeySequence(Qt.Key_Space), self)
        self.loopShortcut.setContext(Qt.ApplicationShortcut)
        self.loopShortcut.activated.connect(self.toggle_looping)

        self.trackingShortcut = QShortcut(QKeySequence(Qt.Key_T), self)
        self.trackingShortcut.setContext(Qt.ApplicationShortcut)
        self.trackingShortcut.activated.connect(self.toggleTracking)

        self.deleteShortcut = QShortcut(QKeySequence(Qt.Key_Backspace), self)
        self.deleteShortcut.setContext(Qt.ApplicationShortcut)
        self.deleteShortcut.activated.connect(self.trajectoryCanvas.delete_selected_trajectory)

        # Global arrow key shortcuts:
        self.leftArrowShortcut = QShortcut(QKeySequence(Qt.Key_Left), self)
        self.leftArrowShortcut.activated.connect(lambda: self.handleGlobalArrow(Qt.Key_Left))
        self.rightArrowShortcut = QShortcut(QKeySequence(Qt.Key_Right), self)
        self.rightArrowShortcut.activated.connect(lambda: self.handleGlobalArrow(Qt.Key_Right))
        self.upArrowShortcut = QShortcut(QKeySequence(Qt.Key_Up), self)
        self.upArrowShortcut.activated.connect(lambda: self.handleGlobalArrow(Qt.Key_Up))
        self.downArrowShortcut = QShortcut(QKeySequence(Qt.Key_Down), self)
        self.downArrowShortcut.activated.connect(lambda: self.handleGlobalArrow(Qt.Key_Down))

        self.xKeyShortcut = QShortcut(QKeySequence("X"), self)
        self.xKeyShortcut.activated.connect(self.handleGlobalX)
        self._thread_pool = QThreadPool.globalInstance()

        self.roi_overlay_active = False

        self.temp_analysis_line = None 
        self._bg = None
        self.clear_flag = False

        self.save_and_load_routine = False

        # For adding a trajectory (Ctrl+Enter)
        self.addTrajectoryShortcut = QShortcut(QKeySequence(Qt.Key_Return), self)
        self.addTrajectoryShortcut.setContext(Qt.ApplicationShortcut)
        self.addTrajectoryShortcut.activated.connect(self.add_or_recalculate)

        self.analysis_avg = None
        self.analysis_median = None
        self.analysis_velocities = []
        self.analysis_average_velocity = None

        self.hovered_trajectory = None

        self._radiusPopup   = None

        self.radiusShortcut = QShortcut(QKeySequence(Qt.Key_R), self)
        self.radiusShortcut.setContext(Qt.ApplicationShortcut)
        self.radiusShortcut.activated.connect(self._showRadiusDialog)

        self.overlayShortcut = QShortcut(QKeySequence(Qt.Key_O), self)
        self.overlayShortcut.setContext(Qt.ApplicationShortcut)
        self.overlayShortcut.activated.connect(self._on_o_pressed)

        self.maxShortcut = QShortcut(QKeySequence(Qt.Key_M), self)
        self.maxShortcut.setContext(Qt.ApplicationShortcut)
        self.maxShortcut.activated.connect(self._on_m_pressed)

        self.roiShortcut = QShortcut(QKeySequence(Qt.Key_N), self)
        self.roiShortcut.setContext(Qt.ApplicationShortcut)
        self.roiShortcut.activated.connect(self._on_n_pressed)

        self._last_dir = str(Path.home())

        # ─── Channel keys 1–8 ───────────────────────────────────────
        for n in range(1, 9):
            sc = QShortcut(QKeySequence(Qt.Key_0 + n), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(partial(self._select_channel, n))

        # ─── Manual-marker WASD ──────────────────────────────────────
        moves = {
            Qt.Key_A: (-1,  0),
            Qt.Key_D: ( 1,  0),
            Qt.Key_S: ( 0, -1),
            Qt.Key_W: ( 0,  1),
        }
        for key, (dx, dy) in moves.items():
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(partial(self._move_manual_marker, dx, dy))

        # ─── Simulate click “K” ───────────────────────────────────────
        sc = QShortcut(QKeySequence(Qt.Key_K), self)
        sc.setContext(Qt.ApplicationShortcut)
        sc.activated.connect(self._simulate_left_click)

        # ─── Prev/Next frame J / L ────────────────────────────────────
        for key, slot in ((Qt.Key_J, self._prev_frame),
                          (Qt.Key_L, self._next_frame)):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.activated.connect(slot)

        self.new_sequence_start = True
        self.analysis_anchors = []
        self.analysis_points = []
        self.analysis_roi = None
        self._suppress_internal_progress = False

        self._kymo_label_to_row = {}
        self._ignore_next_kymo_click = False
        self._last_hover_xy = None

        self._arrow_cooldown = 0.2  
        self._last_arrow = 0.0

        self.last_kymo_by_channel = {}
        self._roi_zoom_states = {}
        self._last_roi = None

        self._last_kymo_artist = None
        self._skip_next_right = False

    def create_ui(self):
        # Create the central widget and overall layout.
        central = QWidget()
        self.setCentralWidget(central)
        containerLayout = QVBoxLayout(central)

        videoiconpath = self.resource_path('icons/video-camera.svg')
        crossiconpath = self.resource_path('icons/cross-small.svg')
        crossdoticonpath = self.resource_path('icons/cross-dot.svg')
        resetcontrastpath = self.resource_path('icons/contrast.svg')
        maxiconpath = self.resource_path('icons/max.svg')
        referenceiconpath = self.resource_path('icons/reference.svg')
        trajoverlayiconpath = self.resource_path('icons/overlay_traj.svg')
        roioverlayiconpath = self.resource_path('icons/overlay.svg')

        # --- Top Controls Section ---
        topWidget = QWidget()
        # topWidget.setStyleSheet("""
        #     background: qlineargradient(
        #         x1: 0, y1: 0, x2: 0, y2: 1,
        #         stop: 0 #DCE6FF,
        #         stop: 1 rgba(220, 230, 255, 0)
        #     );
        #     padding: 8px;
        #     border-radius: 8px;
        # """)
        topLayout = QHBoxLayout(topWidget)
        topLayout.setSpacing(5)
        topLayout.setContentsMargins(8, 6, 0, 0)
        topLayout.setAlignment(Qt.AlignLeft)
        
        self.btnLoadMovie = QPushButton("")
        self.btnLoadMovie.setFixedWidth(50)
        self.btnLoadMovie.setFixedHeight(30)
        self.btnLoadMovie.setIcon(QIcon(videoiconpath))
        self.btnLoadMovie.setStyleSheet("""
            QPushButton {
                border: 2px solid transparent;
                border-radius: 8px;
                background: transparent;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.5);
            }
            QPushButton:pressed {
                border: 2px solid #ffffff;
                background-color: #ffffff;
            }
        """)
        self.btnLoadMovie.setIconSize(QSize(16, 16))
        self.btnLoadMovie.setObjectName("Passive")
        self.btnLoadMovie.clicked.connect(self.handle_movie_load)
        self.btnLoadMovie.setMinimumWidth(50)
        topLayout.addWidget(self.btnLoadMovie)

        self.movieNameLabel = QLabel("")
        self.movieNameLabel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.movieNameLabel.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.movieNameLabel.setStyleSheet("background: transparent")
        topLayout.addWidget(self.movieNameLabel)

        
        # Search window radius control.
        self.searchWindowSpin = QSpinBox()
        self.searchWindowSpin.setRange(4, 50)
        self.searchWindowSpin.setValue(12)
        self.searchWindowSpin.setFocusPolicy(Qt.NoFocus)
        # Set a fixed size so it's square
        self.searchWindowSpin.setFixedSize(60, 35)
        #topLayout.addWidget(create_pair("Search radius:", self.searchWindowSpin))
        
        self.insetViewSize = QSpinBox()
        self.insetViewSize.setRange(4, 14)
        self.insetViewSize.setValue(10)
        
        # Add stretch and right-aligned labels.
        topLayout.addStretch()
        self.pixelValueLabel = QLabel("")
        self.pixelValueLabel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.pixelValueLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pixelValueLabel.setStyleSheet("background: transparent; padding-right: 25px;")
        topLayout.addWidget(self.pixelValueLabel)
        self.scaleLabel = ClickableLabel("")
        self.scaleLabel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.scaleLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.scaleLabel.setStyleSheet("background: transparent; padding-right: 25px;")
        self.scaleLabel.clicked.connect(self.open_set_scale_dialog)
        topLayout.addWidget(self.scaleLabel)
        containerLayout.addWidget(topWidget)
        

        # --- Main Horizontal Splitter ---
        self.mainSplitter = CustomSplitter(Qt.Horizontal, handle_y_offset_pct=0.4955)
        containerLayout.addWidget(self.mainSplitter, stretch=1)
        
        # LEFT COLUMN: Kymograph and ROI controls.
        self.leftWidget = QWidget()
        leftLayout = QVBoxLayout(self.leftWidget)
        # Instead, create a horizontal layout that puts the label to the left of the dropdown
        kymoControlLayout = QHBoxLayout()


        # Create the kymograph label, right justified
        kymoLabel = QLabel("Kymograph")
        # kymoLabel.setStyleSheet("color: #666666;")
        kymoLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        # Optionally, set a fixed minimum width so that your labels line up:
        kymoLabel.setMinimumWidth(40)
        kymoControlLayout.addWidget(kymoLabel)

        # Add the kymograph combo box
        self.kymoCombo = QComboBox()
        self.kymoCombo.setEnabled(False)
        self.kymoCombo.setView(QListView())
        self.kymoCombo.currentIndexChanged.connect(self.kymograph_changed)
        kymoControlLayout.addWidget(self.kymoCombo)

        # Add the kymograph delete and clear buttons
        kymoDeleteBtn = QPushButton("")
        kymoDeleteBtn.setIcon(QIcon(crossiconpath))
        kymoDeleteBtn.setIconSize(QSize(14, 14))
        kymoDeleteBtn.setToolTip("Delete selected ROI")
        kymoDeleteBtn.setObjectName("Passive")
        kymoDeleteBtn.setFixedWidth(32)
        kymoDeleteBtn.clicked.connect(self.delete_current_kymograph)
        kymoControlLayout.addWidget(kymoDeleteBtn)

        clearKymoBtn = QPushButton("")

        clearKymoBtn.setIcon(QIcon(crossdoticonpath))
        clearKymoBtn.setIconSize(QSize(14, 14))
        clearKymoBtn.setToolTip("Clear kymographs")
        clearKymoBtn.setObjectName("Passive")
        clearKymoBtn.setFixedWidth(32)
        clearKymoBtn.clicked.connect(self.clear_kymographs)
        kymoControlLayout.addWidget(clearKymoBtn)


        # Wrap the horizontal layout in a container widget.
        kymoContainer = QWidget()
        kymoContainer.setLayout(kymoControlLayout)

        # Now add the container widget to the vertical layout with the desired alignment.
        leftLayout.addWidget(kymoContainer, alignment=Qt.AlignCenter)
        
        roiControlLayout = QHBoxLayout()

        # Create the ROI label, right justified
        roiLabel = QLabel("ROI")
        roiLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        roiLabel.setMinimumWidth(40)
        roiControlLayout.addWidget(roiLabel)

        # Add the ROI combo box
        self.roiCombo = QComboBox()
        self.roiCombo.setEnabled(False)
        self.roiCombo.setView(QListView())
        self.roiCombo.currentIndexChanged.connect(self.update_roi_overlay_if_active)
        roiControlLayout.addWidget(self.roiCombo)

        # Add the ROI delete and clear buttons
        roiDeleteBtn = QPushButton("")
        roiDeleteBtn.setIcon(QIcon(crossiconpath))
        roiDeleteBtn.setIconSize(QSize(14, 14))
        roiDeleteBtn.setToolTip("Delete selected ROI")
        roiDeleteBtn.setObjectName("Passive")
        roiDeleteBtn.setFixedWidth(32)
        roiDeleteBtn.clicked.connect(self.delete_current_roi)
        roiControlLayout.addWidget(roiDeleteBtn)

        clearROIBtn = QPushButton("")
        clearROIBtn.setIcon(QIcon(crossdoticonpath))
        clearROIBtn.setIconSize(QSize(14, 14))
        clearROIBtn.setToolTip("Clear ROIs")
        clearROIBtn.setObjectName("Passive")
        clearROIBtn.setFixedWidth(32)
        clearROIBtn.clicked.connect(self.clear_rois)
        roiControlLayout.addWidget(clearROIBtn)

        # Wrap the horizontal layout in a container widget.
        self.roiContainer = QWidget()
        self.roiContainer.setLayout(roiControlLayout)

        # Now add the container widget to the vertical layout with the desired alignment.
        leftLayout.addWidget(self.roiContainer, alignment=Qt.AlignCenter)

        kymoControlLayout.setSpacing(6)
        roiControlLayout.setSpacing(6)
        kymoContainer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.roiContainer.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        kymoContainer.setContentsMargins(0, 0, 0, 0)
        self.roiContainer.setContentsMargins(0, 0, 0, 0)
        kymoContainer.setFixedHeight(50)
        self.roiContainer.setFixedHeight(50)
        leftLayout.setContentsMargins(6, 0, 6, 6)
        leftLayout.setSpacing(0)
        self.roiContainer.setVisible(False)

        # Create a RoundedFrame container with your desired radius and background color.
        # You can adjust "lavender" to any color (or RGBA value) you like.
        roundedContainer = RoundedFrame(parent=self, radius=10, bg_color = self.settings['widget-bg'])
        shadow_effect = QGraphicsDropShadowEffect(roundedContainer)
        shadow_effect.setBlurRadius(10)                # Adjust for a softer or sharper shadow.
        shadow_effect.setColor(QColor(0, 0, 0, 120))     # Semi-transparent black (adjust alpha as needed).
        shadow_effect.setOffset(0, 0)                    # Zero offset for a symmetric shadow.
        # Apply the shadow effect to the container.
        roundedContainer.setGraphicsEffect(shadow_effect)
        # Create your kymograph canvas as usual.
        self.kymoCanvas = KymoCanvas(self, navigator=self)
        self.kymoCanvas.setFocusPolicy(Qt.StrongFocus)
        self.kymoCanvas.setFocus()
        # Connect your kymograph mouse events.
        self.kymoCanvas.mpl_connect("button_press_event", self.on_kymo_click)
        self.kymoCanvas.mpl_connect("motion_notify_event", self.on_kymo_motion)
        self.kymoCanvas.mpl_connect("button_release_event", self.on_kymo_release)
        self.kymoCanvas.mpl_connect("motion_notify_event", self.on_kymo_hover)
        self.kymoCanvas.mpl_connect("axes_leave_event", self.on_kymo_leave)
        self.kymoCanvas.mpl_connect("pick_event", self._on_kymo_label_pick)

        self.kymoCanvas.setContextMenuPolicy(Qt.CustomContextMenu)
        self.kymoCanvas.customContextMenuRequested.connect(self._show_kymo_context_menu)

        # Create a layout for your RoundedFrame and add kymoCanvas to it.
        roundedLayout = QVBoxLayout(roundedContainer)
        roundedContainer.setStyleSheet(f"background-color: {self.settings['widget-bg']}")
        roundedLayout.setContentsMargins(5, 5, 5, 5)  # Optional: adjust the inner margin for spacing
        roundedLayout.addWidget(self.kymoCanvas)

        # Now add the rounded container to your leftLayout instead of directly adding kymoCanvas.
        leftLayout.addWidget(roundedContainer, stretch=1)
        
        # overlayLayout = QHBoxLayout()
        # overlayLayout.setSpacing(10)
        # overlayLayout.setContentsMargins(0, 10, 0, 0)
        # overlayLayout.setAlignment(Qt.AlignCenter)
        # overlayLayout.addWidget(self.roi_overlay_button)
        # leftLayout.addLayout(overlayLayout)

        self.leftWidget.setLayout(leftLayout)
        self.mainSplitter.addWidget(self.leftWidget)

        # RIGHT SPLITTER: Two columns — the movie widget and the right panel.
        self.rightVerticalSplitter = CustomSplitter(Qt.Vertical)
        self.topRightSplitter = CustomSplitter(Qt.Horizontal)
        
        # 2nd Column: Movie widget
        self.movieWidget = QWidget()
        movieLayout = QVBoxLayout(self.movieWidget)
        movieLayout.setContentsMargins(6, 6, 6, 6)
        movieLayout.setSpacing(0)
        self.movieDisplayContainer = RoundedFrame(self, radius=10, bg_color = self.settings['widget-bg'])
        self.movieDisplayContainer.setStyleSheet(
            "QFrame { "
            "  border: 0px solid transparent; "
            "  border-radius: 10px; "
            "}"
        )
        shadow = QGraphicsDropShadowEffect(self.movieDisplayContainer)
        shadow.setBlurRadius(10)
        shadow.setColor(QColor(0, 0, 0, 120))
        shadow.setOffset(0)
        self.movieDisplayContainer.setGraphicsEffect(shadow)
        self.movieDisplayContainer.setMinimumSize(400, 400)
        self.movieDisplayContainer.setFrameStyle(QFrame.Box)
        self.movieDisplayContainer.setLineWidth(2)
        self.movieDisplayContainer.setStyleSheet("QFrame { border: 6px solid transparent; }")
        movieDisplayLayout = QVBoxLayout(self.movieDisplayContainer)
        movieDisplayLayout.setContentsMargins(6, 5, 6, 5)

        # Create the channel control container as a rounded frame with shadow
        self.channelControlContainer = RoundedFrame(self.movieDisplayContainer, radius=10, bg_color = self.settings['widget-bg'])
        # Apply drop shadow effect
        shadow = QGraphicsDropShadowEffect(self.channelControlContainer)
        shadow.setBlurRadius(10)
        shadow.setColor(QColor(0, 0, 0, 120))
        shadow.setOffset(0, 0)
        self.channelControlContainer.setGraphicsEffect(shadow)
        # Semi-transparent white background
        self.channelControlContainer.setStyleSheet("background: transparent;")

        # Create and install a horizontal layout
        channelLayout = QHBoxLayout()
        channelLayout.setContentsMargins(0, 0, 0, 0)
        channelLayout.setSpacing(4)
        self.channelControlContainer.setLayout(channelLayout)

        # Create the "Channel:" label and combo box
        self.channelLabel = QLabel("Channel")
        self.channelLabel.setStyleSheet("""QLabel {background-color: transparent; color: #444444}""")
        self.movieChannelCombo = QComboBox()
        self.movieChannelCombo.setView(QListView())
        self.movieChannelCombo.setFixedWidth(40)
        self.movieChannelCombo.setFixedHeight(24)
        self.movieChannelCombo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.movieChannelCombo.setStyleSheet("QComboBox { min-width: 40px; background: transparent; margin: 0px; border: 1px solid #ccc;  color: #444444 }")
        self.movieChannelCombo.setEnabled(False)
        self.movieChannelCombo.currentIndexChanged.connect(self.on_channel_changed)

        # Add both to the channel layout
        channelLayout.addWidget(self.channelLabel)
        channelLayout.addWidget(self.movieChannelCombo)

        # Size and position the container
        self.channelControlContainer.adjustSize()
        self.channelControlContainer.move(10, 10)
        self.channelControlContainer.raise_()

        # Initially hide the container until a movie is loaded
        self.channelControlContainer.setVisible(False)

        self.movieCanvas = MovieCanvas(self, navigator=self)
        self.movieCanvas.setStyleSheet(f"background-color: {self.settings['widget-bg']}")
        self.movieCanvas.mpl_connect("scroll_event", self.movieCanvas.on_scroll)
        self.movieCanvas.mpl_connect("scroll_event", self.on_movie_scroll)
        self.movieCanvas.mpl_connect("button_press_event", self.on_movie_click)
        self.movieCanvas.mpl_connect("button_release_event", self.on_movie_release)
        self.movieCanvas.mpl_connect("motion_notify_event", self.on_movie_motion)
        movieDisplayLayout.addWidget(self.movieCanvas, stretch=1)
        self.movieDisplayContainer.setLayout(movieDisplayLayout)
        movieLayout.addWidget(self.movieDisplayContainer, stretch=1)

        self.channelControlContainer.setParent(self.movieDisplayContainer)
        self.channelControlContainer.setStyleSheet("background: rgba(255,255,255,0);")
        self.channelControlContainer.move(10, 10)   # tweak x/y offsets as you
        self.channelControlContainer.raise_()

        # in create_ui, replace the overlay QLabel with:
        self._ch_overlay = ClickableLabel("", parent=self.movieDisplayContainer)
        self._ch_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._ch_overlay.setStyleSheet("""
            background: transparent;
            color: #999999;
            font-size: 40pt;
            font-weight: bold;
        """)
        self._ch_overlay.hide()

        # shadow = QGraphicsDropShadowEffect(self._ch_overlay)
        # shadow.setBlurRadius(5)
        # shadow.setColor(QColor(250, 250, 250, 200))
        # shadow.setOffset(0, 0)
        # self._ch_overlay.setGraphicsEffect(shadow)

        self._ch_overlay.clicked.connect(self._on_overlay_clicked)

        movieLayout.addSpacing(10)
        
        sliderWidget = QWidget()
        sliderLayout = QHBoxLayout(sliderWidget)
        sliderLayout.setContentsMargins(6, 5, 6, 5)
        sliderLayout.setSpacing(10)
        self.frameNumberLabel = QLabel("1")
        self.frameNumberLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        sliderLayout.addWidget(self.frameNumberLabel)
        self.frameSlider = QSlider(Qt.Horizontal)
        self.frameSlider.setMinimum(0)
        self.frameSlider.setMaximum(250)  # will be updated after loading movie
        self.frameSlider.setValue(0)
        self.frameSlider.valueChanged.connect(self.on_frame_slider_changed)
        self.frameSlider.setSingleStep(1)
        self.frameSlider.setPageStep(1)
        font_metrics = self.frameNumberLabel.fontMetrics()
        width = font_metrics.width("99999")
        self.frameNumberLabel.setFixedWidth(width)
        sliderLayout.addWidget(self.frameSlider)
        movieLayout.addWidget(sliderWidget)
        
        contrastWidget = QWidget()
        contrastLayout = QHBoxLayout(contrastWidget)
        contrastLayout.setContentsMargins(0, 0, 0, 0)
        contrastLayout.setSpacing(10)
        self.contrastControlsWidget = ContrastControlsWidget(self.movieCanvas)
        self.contrastControlsWidget.setMinimumWidth(150)
        contrastLayout.addWidget(self.contrastControlsWidget)
        resetBtn = QPushButton("")
        resetBtn.setIcon(QIcon(resetcontrastpath))
        resetBtn.setIconSize(QSize(16, 16))
        resetBtn.setToolTip("Reset contrast")
        resetBtn.clicked.connect(self.reset_contrast)
        resetBtn.setObjectName("Passive")
        resetBtn.setFixedWidth(40)
        contrastLayout.addWidget(resetBtn)
        self.sumBtn = QPushButton("", self)
        self.sumBtn.setIcon(QIcon(maxiconpath))
        self.sumBtn.setIconSize(QSize(16, 16))
        self.sumBtn.setCheckable(True)
        self.sumBtn.setFixedWidth(40)
        self.sumBtn.setToolTip("Show the maximum projection (shortcut: m)")
        self.sumBtn.toggled.connect(self.on_sum_toggled)
        contrastLayout.addWidget(self.sumBtn)
        self.refBtn = QPushButton("")
        self.refBtn.setIcon(QIcon(referenceiconpath))
        self.refBtn.setIconSize(QSize(16, 16))
        self.refBtn.setToolTip("Show the reference image")
        self.refBtn.setCheckable(True)
        self.refBtn.setFixedWidth(40)
        self.refBtn.setVisible(False)
        self.refBtn.toggled.connect(self.on_ref_toggled)
        contrastLayout.addWidget(self.refBtn)

        self.traj_overlay_button = QPushButton("")
        self.traj_overlay_button.setToolTip("Overlay trajectories (shortcut: o)")
        self.traj_overlay_button.setIcon(QIcon(trajoverlayiconpath))
        self.traj_overlay_button.setIconSize(QSize(16, 16))
        self.traj_overlay_button.setFixedWidth(40)
        self.traj_overlay_button.setCheckable(True)
        self.traj_overlay_button.setChecked(True)
        self.update_overlay_button_style(self.traj_overlay_button.isChecked())
        self.traj_overlay_button.toggled.connect(self.update_overlay_button_style)
        contrastLayout.addWidget(self.traj_overlay_button)

        self.modeSwitch = ToggleSwitch()
        self.modeSwitch.toggled.connect(lambda state: self.onModeChanged("roi" if state else "spot"))
        contrastLayout.addWidget(self.modeSwitch)

        self.roi_overlay_button = QPushButton("")
        self.roi_overlay_button.setIcon(QIcon(roioverlayiconpath))
        self.roi_overlay_button.setIconSize(QSize(16, 16))
        self.roi_overlay_button.setToolTip("Overlay ROI onto the movie")
        self.roi_overlay_button.setCheckable(True)
        self.roi_overlay_button.setFixedWidth(40)
        self.roi_overlay_button.setStyleSheet("""
            QPushButton {
                background-color: transparent;
            }
            QPushButton:checked, QPushButton:checked:hover {
                background-color: #81C784;
            }
            QPushButton:hover {
                background-color: #D8EDD9;
            }
        """)
        self.roi_overlay_button.clicked.connect(self.toggle_roi_overlay)
        # self.roi_overlay_button.toggled.connect(self.update_roi_overlay_button_style)
        contrastLayout.addWidget(self.roi_overlay_button)

        self.delete_button = QPushButton("")
        self.delete_button.setToolTip("Delete selected trajectory")
        self.delete_button.setIcon(QIcon(crossiconpath))
        self.delete_button.setIconSize(QSize(16, 16))
        self.delete_button.setFixedWidth(40)
        self.delete_button.setObjectName("Passive")
        contrastLayout.addWidget(self.delete_button)

        self.clear_button = QPushButton("")
        self.clear_button.setToolTip("Delete all trajectories")
        self.clear_button.setIcon(QIcon(crossdoticonpath))
        self.clear_button.setIconSize(QSize(16, 16))
        self.clear_button.setFixedWidth(40)
        self.clear_button.setObjectName("Passive")
        contrastLayout.addWidget(self.clear_button)

        movieLayout.addWidget(contrastWidget, alignment=Qt.AlignCenter)
        self.movieWidget.setLayout(movieLayout)
        self.topRightSplitter.addWidget(self.movieWidget)

        # Column 3: Right Panel with additional canvases.
        rightPanel = QWidget()
        rightPanel.setMinimumWidth(350)
        rightPanelLayout = QVBoxLayout(rightPanel)
        rightPanelLayout.setContentsMargins(6, 6, 6, 6)
        rightPanelLayout.setSpacing(10)

        # --- Histogram Canvas in a rounded frame ---
        self.histogramCanvas = HistogramCanvas(self)
        self.histogramCanvas.setStyleSheet(f"background-color: {self.settings['widget-bg']}")
        self.histogramCanvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        roundedHistFrame = RoundedFrame(self, radius=10, bg_color = self.settings['widget-bg'])  # white background, 10px radius
        roundedHistFrame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        hist_shadow = QGraphicsDropShadowEffect(roundedHistFrame)
        hist_shadow.setBlurRadius(10)             # Adjust to get a softer or sharper shadow.
        hist_shadow.setColor(QColor(0, 0, 0, 120))  # Black with 150/255 opacity.
        hist_shadow.setOffset(0, 0)                # Zero offset for a symmetric shadow.
        roundedHistFrame.setGraphicsEffect(hist_shadow)
        # Create a layout for the rounded frame and add the canvas.
        histLayout = QVBoxLayout(roundedHistFrame)
        histLayout.setContentsMargins(5, 5, 5, 5)  # adjust margins as needed
        histLayout.addWidget(self.histogramCanvas)
        roundedHistFrame.setLayout(histLayout)
        rightPanelLayout.addWidget(roundedHistFrame, stretch=1)

        # --- Intensity/Plot Canvas in a rounded frame ---
        self.intensityCanvas = IntensityCanvas(parent=self, navigator=self)
        self.intensityCanvas.setStyleSheet(f"background-color: {self.settings['widget-bg']}")
        self.intensityCanvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        roundedPlotFrame = RoundedFrame(self, radius=10, bg_color = self.settings['widget-bg'])
        roundedPlotFrame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        plot_shadow = QGraphicsDropShadowEffect(roundedPlotFrame)
        plot_shadow.setBlurRadius(10)
        plot_shadow.setColor(QColor(0, 0, 0, 120))
        plot_shadow.setOffset(0, 0)
        roundedPlotFrame.setGraphicsEffect(plot_shadow)
        plotLayout = QVBoxLayout(roundedPlotFrame)
        plotLayout.setContentsMargins(5, 5, 5, 5)
        plotLayout.addWidget(self.intensityCanvas)
        roundedPlotFrame.setLayout(plotLayout)
        rightPanelLayout.addWidget(roundedPlotFrame, stretch=1)

        # --- Velocity Canvas in a rounded frame ---
        self.velocityCanvas = VelocityCanvas(self, navigator=self)
        self.velocityCanvas.setStyleSheet(f"background-color: {self.settings['widget-bg']}")
        self.velocityCanvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        roundedVelFrame = RoundedFrame(self, radius=10, bg_color = self.settings['widget-bg'])
        roundedVelFrame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        vel_shadow = QGraphicsDropShadowEffect(roundedVelFrame)
        vel_shadow.setBlurRadius(10)
        vel_shadow.setColor(QColor(0, 0, 0, 120))
        vel_shadow.setOffset(0, 0)
        roundedVelFrame.setGraphicsEffect(vel_shadow)
        velLayout = QVBoxLayout(roundedVelFrame)
        velLayout.setContentsMargins(5, 5, 5, 5)
        velLayout.addWidget(self.velocityCanvas)
        roundedVelFrame.setLayout(velLayout)
        rightPanelLayout.addWidget(roundedVelFrame, stretch=1)

        # --- Analysis Slider (optional; you can also wrap it similarly if desired)
        self.analysisSlider = QSlider(Qt.Horizontal)
        self.analysisSlider.setMinimum(0)
        self.analysisSlider.setMaximum(0)  # Will be updated later when analysis data is computed
        self.analysisSlider.setValue(0)
        self.analysisSlider.valueChanged.connect(self.on_analysis_slider_changed)
        self.analysisSlider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        rightPanelLayout.addWidget(self.analysisSlider, stretch=0)

        # rightPanelLayout.addSpacing(-2)

        # self.trajectoryControlButtons = TrajectoryControlButtons(self)
        # rightPanelLayout.addWidget(self.trajectoryControlButtons, stretch=0)

        rightPanel.setLayout(rightPanelLayout)
        self.topRightSplitter.addWidget(rightPanel)
        # Optional: adjust stretch factors for the topRightSplitter:
        self.topRightSplitter.setStretchFactor(0, 3)  # movie widget
        self.topRightSplitter.setStretchFactor(1, 2)  # right panel

        # Add the top row (movie + right panel) as the upper widget in the vertical splitter.
        self.rightVerticalSplitter.addWidget(self.topRightSplitter)

        # BOTTOM of RIGHT: Trajectory Canvas that now spans the full width of the right side.
        self.trajectoryCanvas = TrajectoryCanvas(self, self.kymoCanvas, self.movieCanvas, self.intensityCanvas, navigator=self)
        self.rightVerticalSplitter.addWidget(self.trajectoryCanvas)

        # Optionally set stretch factors for the main horizontal splitter (e.g., leave the left narrower)
        self.mainSplitter.setStretchFactor(0, 1)
        self.mainSplitter.setStretchFactor(1, 2)

        self.mainSplitter.addWidget(self.rightVerticalSplitter)

        self.update_table_visibility()
        self.update_kymo_visibility()


        self.clear_button.clicked.connect(
            lambda _checked: self.trajectoryCanvas.clear_trajectories()
            )
        self.traj_overlay_button.clicked.connect(self.trajectoryCanvas.toggle_trajectory_markers)
        self.delete_button.clicked.connect(self.trajectoryCanvas.delete_selected_trajectory)
        
        # self.trajectoryControlButtons.toggleOverlayRequested.connect(
        #     self.trajectoryCanvas.toggle_trajectory_markers)
        # self.trajectoryControlButtons.deleteRequested.connect(
        #     self.trajectoryCanvas.delete_selected_trajectory)
        # self.trajectoryControlButtons.clearRequested.connect(
        #     self.trajectoryCanvas.clear_trajectories)
        
        # Connect additional signals (e.g. for mouse motion over the movie canvas).
        self.movieCanvas.mpl_connect("motion_notify_event", self.on_movie_hover)

        # Create a container (QFrame) for the zoom inset.
        self.zoomInsetFrame = QFrame(self.movieDisplayContainer)
        # Set the overall size and a rounded border.
        self.zoomInsetFrame.setMinimumWidth(140)
        self.zoomInsetFrame.setMinimumHeight(180)

        # right after you create it, stash the default size:
        self._default_inset_size = (
            self.zoomInsetFrame.minimumWidth(),
            self.zoomInsetFrame.minimumHeight()
        )
        self.zoomInsetFrame.setStyleSheet("""
            QFrame {
                border: 2px solid transparent;
                border-radius: 10px;
            }
        """)
        insetshadow = QGraphicsDropShadowEffect(self.zoomInsetFrame)
        insetshadow.setBlurRadius(8)                  # how “soft” the shadow is
        insetshadow.setOffset(0, 0)                   # no offset—even glow
        insetshadow.setColor(QColor(0, 0, 0, 120))    # semi‑transparent black
        self.zoomInsetFrame.setGraphicsEffect(insetshadow)

        # Use a vertical layout to stack the overlay text and the image.

        zoomLayout = QVBoxLayout(self.zoomInsetFrame)
        zoomLayout.setContentsMargins(5, 5, 5, 5)
        zoomLayout.setSpacing(5)

        # Top label: no stretch, stays at its size
        self.zoomInsetLabel = QLabel("", self.zoomInsetFrame)
        self.zoomInsetLabel.setStyleSheet("""
            QLabel {
                background-color: transparent;
                color: #444444;
                border-radius: 5px;
            }
        """)
        zoomLayout.addWidget(
            self.zoomInsetLabel,
            stretch=0,
            alignment=Qt.AlignHCenter | Qt.AlignTop
        )

        # MovieCanvas inset: gets ALL extra vertical space
        self.zoomInsetWidget = MovieCanvas(parent=self.zoomInsetFrame, navigator=self)
        self.zoomInsetWidget.enableInteraction = False
        self.zoomInsetWidget.setSizePolicy(
            QSizePolicy.Expanding,
            QSizePolicy.Expanding
        )
        self.zoomInsetWidget.setMinimumSize(50, 50)  # prevents it collapsing completely
        self.zoomInsetWidget.setStyleSheet(
            "border: 5px solid white; background-color: transparent;"
        )
        zoomLayout.addWidget(
            self.zoomInsetWidget,
            stretch=1,
            alignment=Qt.AlignCenter
        )

        # Bottom intensity label: no stretch, stays at its size
        self.zoomInsetIntensityLabel = QLabel("", self.zoomInsetFrame)
        self.zoomInsetIntensityLabel.setStyleSheet("""
            QLabel {
                background-color: transparent;
                color: rgba(255, 0, 255, 180);
                border-radius: 5px;
            }
        """)
        zoomLayout.addWidget(
            self.zoomInsetIntensityLabel,
            stretch=0,
            alignment=Qt.AlignHCenter | Qt.AlignBottom
        )

        # Initially hide the zoom inset frame.
        self.zoomInsetFrame.setVisible(False)

        # Reposition the zoom inset frame when the movieDisplayContainer is resized.
        original_resize = self.movieDisplayContainer.resizeEvent
        def new_resize(event):
            original_resize(event)
            container_width = self.movieDisplayContainer.width()
            # Position the container 10 pixels from the right and top.
            self.zoomInsetFrame.move(container_width - self.zoomInsetFrame.width() - 10, 10)
        self.movieDisplayContainer.resizeEvent = new_resize

        # Finally, add your central layout as usual.
        central.setLayout(containerLayout)

    def resource_path(self, relative):
        if getattr(sys, 'frozen', False):
            base = sys._MEIPASS
        else:
            base = os.path.dirname(__file__)
        return os.path.join(base, relative)

    def toggle_invert_cmap(self, checked: bool):
        self.inverted_cmap = checked
        # repaint every canvas that has an _im image on it
        for canv in (self.movieCanvas,
                    self.kymoCanvas,
                    getattr(self, "zoomInsetWidget", None)):
            if canv is None: continue
            for attr in ("_im", "_im_inset"):
                im = getattr(canv, attr, None)
                if im:
                    im.set_cmap("gray_r" if checked else "gray")
            canv.draw_idle()

    def update_roilist_visibility(self):
        # Look through all of your kymo→ROI entries for any imported ROIs
        has_orphaned = any(
            info.get("orphaned", False)
            for info in self.kymo_roi_map.values()
        )
        self.roiContainer.setVisible(has_orphaned)

    def open_set_scale_dialog(self):
        # pop up your dialog, initialized with the current values
        self.set_scale()

    def update_scale_label(self):
        if self.pixel_size is not None and self.frame_interval is not None:
            self.scaleLabel.setText(f"{self.pixel_size:.1f} nm/pixel, {self.frame_interval:.1f} ms/frame")
        else:
            self.scaleLabel.setText("Set scale")

    def update_kymo_visibility(self):
        # Check if there is neither an ROI nor a kymograph loaded.
        if self.roiCombo.count() == 0 and self.kymoCombo.count() == 0:
            # Collapse the left column by setting its width to zero.
            total_width = self.mainSplitter.width()
            self.mainSplitter.setSizes([0, total_width])
        else:
            # Otherwise, restore the left column to a default (say 20% of the total width)
            total_width = self.mainSplitter.width()
            left_width = int(0.32 * total_width)
            right_width = total_width - left_width
            self.mainSplitter.setSizes([left_width, right_width])


    def _make_colored_circle_cursor(self, size=12, thickness=2, shade='green'):
        """
        Create a smooth, anti‑aliased circular cursor in either 'green' or 'blue':
          - green → outline & fill from "#81C784"
          - blue  → outline & fill from "#7DA1FF"
        """
        # choose your hex
        color_map = {
            'green': '#81C784',
            'blue':  '#7DA1FF',
        }
        hex_color = color_map.get(shade.lower(), color_map['green'])

        # Create a square ARGB pixmap
        pix = QtGui.QPixmap(size, size)
        pix.fill(QtCore.Qt.transparent)

        painter = QtGui.QPainter(pix)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.HighQualityAntialiasing, True)

        # Outline pen
        pen = QtGui.QPen(QtGui.QColor(hex_color))
        pen.setWidth(thickness)
        painter.setPen(pen)

        # Semi‑transparent fill
        fill_color = QtGui.QColor(hex_color)
        fill_color.setAlpha(80)  
        brush = QtGui.QBrush(fill_color)
        painter.setBrush(brush)

        # Draw centered circle, inset by half the pen
        radius = (size - thickness) / 2
        center = QtCore.QPointF(size / 2, size / 2)
        painter.drawEllipse(center, radius, radius)

        painter.end()

        # Return a QCursor with its hot‑spot in the center
        return QtGui.QCursor(pix, hotX=size // 2, hotY=size // 2)

    def set_roi_mode(self, enabled: bool):
        """Enable or disable ROI-add mode, updating cursor, border, and overlay button."""
        # synchronize the mode switch widget
        self.modeSwitch.setChecked(enabled)
        self.cancel_left_click_sequence()
        self.movieCanvas.roiAddMode = enabled
        if enabled:
            cursor = self._make_colored_circle_cursor(shade='green')
            self.movieCanvas.setCursor(cursor)
            self.movieDisplayContainer.setBorderColor("#81C784")
            if not self.roi_overlay_button.isChecked():
                self.roi_overlay_button.setChecked(True)
                self.toggle_roi_overlay()
        else:
            self.movieCanvas.unsetCursor()
            self.movieDisplayContainer.setBorderColor("transparent")
            if self.roi_overlay_button.isChecked():
                self.roi_overlay_button.setChecked(False)
                self.toggle_roi_overlay()

    def onModeChanged(self, mode):
        if mode == "roi":
            self.set_roi_mode(True)
        else:
            self.set_roi_mode(False)

    def flash_message(parent, text, total_ms=800, fade_ms=200):
        # 1) Create a frameless, always‑on‑top window (no Qt.ToolTip!)
        popup = QWidget(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        popup.setAttribute(Qt.WA_TranslucentBackground)      # let round‑corners show
        popup.setStyleSheet("background: transparent;")      # make sure outer is invisible

        # 2) Inner frame draws the colored, rounded rect
        frame = QFrame(popup)
        frame.setObjectName("flashFrame")
        frame.setStyleSheet("""
            QFrame#flashFrame {
                background-color: #F1F5FF;
                border-radius: 12px;
            }
        """)
        # drop shadow on that frame
        shadow = QGraphicsDropShadowEffect(frame)
        shadow.setBlurRadius(2)
        shadow.setOffset(0, 0)
        shadow.setColor(QColor(0,0,0,120))
        frame.setGraphicsEffect(shadow)

        # 3) put the label inside
        lbl = QLabel(text, frame)
        lbl.setStyleSheet("color: black; font-size: 14px;")
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(12,6,12,6)
        lay.addWidget(lbl)

        # 4) zero‑margin layout on popup
        top_lay = QHBoxLayout(popup)
        top_lay.setContentsMargins(0,0,0,0)
        top_lay.addWidget(frame)
        popup.adjustSize()

        # 5) center it over the parent
        pw, ph = parent.width(), parent.height()
        lw, lh = popup.width(), popup.height()
        pos = parent.mapToGlobal(QPoint((pw-lw)//2, (ph-lh)//35))
        popup.move(pos)

        popup.show()

        # 6) fade out via windowOpacity
        def start_fade():
            anim = QPropertyAnimation(popup, b"windowOpacity", popup)
            anim.setDuration(fade_ms)
            anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            anim.finished.connect(popup.deleteLater)
            anim.start()
            popup._anim = anim

        QTimer.singleShot(total_ms - fade_ms, start_fade)

        # 7) fade out by animating the widget’s windowOpacity (not via another effect)
        def start_fade():
            anim = QPropertyAnimation(popup, b"windowOpacity", popup)
            anim.setDuration(fade_ms)
            anim.setStartValue(1.0)
            anim.setEndValue(0.0)
            anim.finished.connect(popup.deleteLater)
            anim.start()
            popup._anim = anim  # keep reference alive

        QTimer.singleShot(total_ms - fade_ms, start_fade)

    def eventFilter(self, obj, event):
        # intercept wheel events when our radius dialog is up
        if (self._radiusDialog is not None 
                and self._radiusDialog.isVisible() 
                and event.type() == QEvent.Wheel):
            # up/down?
            delta = event.angleDelta().y()
            step  = self.searchWindowSpin.singleStep()
            cur   = self.searchWindowSpin.value()
            if delta > 0:
                self.searchWindowSpin.setValue(cur + step)
            else:
                self.searchWindowSpin.setValue(cur - step)
            return True    # eat it
        return super().eventFilter(obj, event)

    def delete_current_roi(self):
        current = self.roiCombo.currentText()
        if current:
            # Remove from the dictionary.
            if current in self.rois:
                del self.rois[current]
            # Remove from the combo box.
            index = self.roiCombo.currentIndex()
            self.roiCombo.removeItem(index)
            self.kymo_roi_map.pop(current, None)
            # Optionally, reset the selection if needed.
            if self.roiCombo.count() > 0:
                self.roiCombo.setCurrentIndex(0)
            # Also update any ROI overlays if they are active.
            self.update_roi_overlay_if_active()
            self.update_roilist_visibility()

    def _on_o_pressed(self):
        if len(self.trajectoryCanvas.trajectories) == 0:
            return
        btn = self.traj_overlay_button
        btn.setChecked(not btn.isChecked())           # flip its checked state
        self.trajectoryCanvas.toggle_trajectory_markers()

    def _on_m_pressed(self):
        if self.movie is None:
            return
        self.sumBtn.toggle()

    def _on_n_pressed(self):
        if self.movie is None:
            return
        is_roi = self.modeSwitch.isChecked()
        if is_roi:
            self.set_roi_mode(False)
        else:
            self.set_roi_mode(True)


    def handleGlobalArrow(self, key):

        now = time.perf_counter()
        if now - self._last_arrow < self._arrow_cooldown:
            return
        self._last_arrow = now

        if self.movie is None:
            return

        # If we were looping, stop it
        if self.looping:
            self.stoploop()

        # Only handle Left/Right here
        if key in (Qt.Key_Right, Qt.Key_Left) and self.analysis_frames:
            # pick direction (+1 or -1)
            direction = 1 if key == Qt.Key_Right else -1

            # compute and store the new index
            N = len(self.analysis_frames)
            self.loop_index = (self.intensityCanvas.current_index + direction) % N
            self.intensityCanvas.current_index = self.loop_index

            # use exactly the same call your loop uses:
            #   pan instantly, repaint fully, draw the X
            self.jump_to_analysis_point(self.loop_index, animate="discrete")

            return

        table = self.trajectoryCanvas.table_widget
        row_count = table.rowCount()
        if row_count == 0:
            return

        current = table.currentRow()
        if current < 0:
            current = 0

        if key == Qt.Key_Down:
            new_row = (current + 1) % row_count
        elif key == Qt.Key_Up:
            new_row = (current - 1) % row_count

        table.selectRow(new_row)
        # self.jump_to_analysis_point(0, animate="ramp")

    def _showRadiusDialog(self):
        # don’t open if it’s already up
        if self._radiusPopup:
            return

        dlg = RadiusDialog(self.searchWindowSpin.value(), self)
        dlg.adjustSize()
        pw, ph = self.width(), self.height()
        lw, lh = dlg.width(), dlg.height()
        x = (pw - lw) // 2
        y = (ph - lh) // 35
        dlg.move(self.mapToGlobal(QPoint(x, y)))
        dlg.show()

        self._radiusPopup    = dlg
        self._radiusSpinLive = dlg._spin

    def handleGlobalX(self):
        """Global handler for the X key.
        If the current analysis point is valid, mark it invalid (and remove the magenta circle);
        if it is already invalid, re-run the analysis (undo the invalidation) and update all overlays.
        Also update the trajectory metadata, table, and (if overlay is toggled on) redraw the overlay lines.
        """

        if not hasattr(self, "intensityCanvas") or self.intensityCanvas is None:
            print("No intensity canvas available; ignoring X key.")
            return
        if not self.analysis_frames or not self.analysis_original_coords:
            print("No analysis data available; ignoring X key.")
            return
        if not self.intensityCanvas.point_highlighted:
            return

        if self.looping:
            self.stoploop()

        idx = self.intensityCanvas.current_index
        fitted_center = self.analysis_fit_params[idx][0]
        sigma = self.analysis_fit_params[idx][0]
        peak = self.analysis_fit_params[idx][0]
        bkgr = self.analysis_background[idx]
        intensity = self.analysis_intensities[idx]
        search_center = self.analysis_search_centers[idx]
        # Get frame and coordinate for this analysis point.
        frame = self.analysis_frames[idx]
        crop_size = int(2 * self.searchWindowSpin.value())

        frame_image = self.get_movie_frame(frame)
        if frame_image is not None:
            if self.analysis_intensities[idx] is None:
                # Re-analysis branch: re-run Gaussian fit at original fit center
                fitted_center, sigma, intensity, peak, bkgr = perform_gaussian_fit(frame_image, search_center, crop_size, pixelsize = self.pixel_size)
                self.movieCanvas.overlay_rectangle(search_center[0], search_center[1], crop_size, frame_number=frame)
                self.flash_message("Reattempt")
            else:
                self.flash_message("Remove")
                # if getattr(self.kymoCanvas, "_marker", None) is not None:
                #     try:
                #         self.kymoCanvas._marker.remove()
                #     except Exception:
                #         pass
                # # force a fresh background snapshot next time (so it won’t blit the old grey)
                # self.kymoCanvas._bg = None
                # # draw the new marker at the search center
                # x, y = self.analysis_search_centers[idx]
                # self.kymoCanvas.overlay_spot_center(x, y, size=12, color="grey")

        self.trajectoryCanvas.update_trajectory(idx, fitted_center, sigma, peak, intensity)

        if self.analysis_intensities[idx] is None:
            self.movieCanvas.remove_gaussian_circle()
            self.movieCanvas.remove_inset_circle()
            self.kymoCanvas.clear_kymo_trajectory_markers()
        else:
            self.movieCanvas.add_gaussian_circle(fitted_center, sigma)
            center_for_zoom = fitted_center if fitted_center is not None else search_center
            self.movieCanvas.update_inset(
                frame_image, center_for_zoom, int(self.insetViewSize.value()), 2,
                fitted_center=fitted_center,
                fitted_sigma=sigma,
                fitted_peak=peak,
                intensity_value=intensity,
                offset = bkgr
            )

        # If overlay is toggled on, redraw the overlay lines.
        if self.traj_overlay_button.isChecked():
            self.movieCanvas.draw_trajectories_on_movie()
            self.kymoCanvas.draw_trajectories_on_kymo()
        
        # Update the intensity plot.
        self.intensityCanvas.plot_intensity(self.analysis_frames, self.analysis_intensities,
                            avg_intensity=self.analysis_avg,
                            median_intensity=self.analysis_median,
                            colors=self.analysis_colors)
        self.intensityCanvas.highlight_current_point()

        self.velocityCanvas.plot_velocity_histogram(self.analysis_velocities)

        self.zoomInsetWidget.draw_idle()
        self.kymoCanvas.draw_idle()
        self.movieCanvas.draw_idle()

    # In create_menu(), add a new menu action:
    def create_menu(self):
        menubar = self.menuBar()

        # Create File-related menus
        loadMenu = menubar.addMenu("Load")
        loadKymosAction = QAction("Kymographs", self)
        loadKymosAction.triggered.connect(self.load_kymographs)
        loadMenu.addAction(loadKymosAction)
        
        loadROIsAction = QAction("Line ROIs", self)
        loadROIsAction.triggered.connect(self.load_roi)
        loadMenu.addAction(loadROIsAction)
        
        loadTrajectoriesAction = QAction("Trajectories", self)
        loadTrajectoriesAction.triggered.connect(self.trajectoryCanvas.load_trajectories)
        loadMenu.addAction(loadTrajectoriesAction)
        
        loadReferenceAction = QAction("Reference", self)
        loadReferenceAction.triggered.connect(self.load_reference)
        loadMenu.addAction(loadReferenceAction)
        
        loadKymosAction = QAction("Kymograph w/Point-ROIs", self)
        loadKymosAction.triggered.connect(self.load_kymograph_with_overlays)
        loadMenu.addAction(loadKymosAction)

        saveMenu = menubar.addMenu("Save")
        saveTrajectoriesAction = QAction("Trajectories", self)
        saveTrajectoriesAction.triggered.connect(lambda: self.trajectoryCanvas.save_trajectories())
        saveMenu.addAction(saveTrajectoriesAction)

        saveKymosAction = QAction("Kymographs", self)
        saveKymosAction.triggered.connect(self.save_kymographs)
        saveMenu.addAction(saveKymosAction)

        # saveAllKymosAction = QAction("Kymographs (all)", self)
        # saveAllKymosAction.triggered.connect(partial(self.save_kymographs, False))
        # saveMenu.addAction(saveAllKymosAction)

        # saveKymosOverlaysAction = QAction("Kymographs w/Overlays", self)
        # saveKymosOverlaysAction.triggered.connect(self.save_kymographs_with_overlays)
        # saveMenu.addAction(saveKymosOverlaysAction)

        saveROIsAction = QAction("Line ROIs", self)
        saveROIsAction.triggered.connect(self.save_rois)
        saveMenu.addAction(saveROIsAction)

        movieMenu = menubar.addMenu("Movie")
        correctDriftAction = QAction("Correct Drift", self)
        correctDriftAction.triggered.connect(self.correct_drift)
        movieMenu.addAction(correctDriftAction)

        setScaleAction = QAction("Set Scale", self)
        setScaleAction.triggered.connect(self.set_scale)
        movieMenu.addAction(setScaleAction)

        channelAxisAction = QAction("Change Channel Axis", self)
        channelAxisAction.triggered.connect(self.show_channel_axis_dialog)
        movieMenu.addAction(channelAxisAction)

        spotMenu = menubar.addMenu("Spot")
        searchRadiusAction = QAction("Search Radius", self)
        searchRadiusAction.triggered.connect(self.set_search_radius)
        spotMenu.addAction(searchRadiusAction)

        # Create a QComboBox for Tracking Mode and wrap it in a QWidgetAction.
        trackingModeAction = QAction("Tracking Mode", self)
        trackingModeAction.triggered.connect(self.set_tracking_mode)
        spotMenu.addAction(trackingModeAction)

        kymoMenu = menubar.addMenu("Kymograph")
        kymopreferencesAction = QAction("Line options", self)
        kymopreferencesAction.triggered.connect(self.open_kymopreferences_dialog)
        kymoMenu.addAction(kymopreferencesAction)

        kymoGenerateFromTrajAction = QAction("Draw from trajectories", self)
        kymoGenerateFromTrajAction.triggered.connect(self.generate_rois_from_trajectories)
        kymoMenu.addAction(kymoGenerateFromTrajAction)

        self._colorBySeparator = kymoMenu.addSeparator()
        self._colorByActions   = []
        self.kymoMenu          = kymoMenu

        trajMenu = menubar.addMenu("Trajectories")

        recalcAction = QAction("Recalculate selected", self)
        recalcAction.triggered.connect(self.trajectoryCanvas.recalculate_trajectory)
        trajMenu.addAction(recalcAction)

        recalcAction = QAction("Recalculate all", self)
        recalcAction.triggered.connect(self.trajectoryCanvas.recalculate_all_trajectories)
        trajMenu.addAction(recalcAction)

        viewMenu = menubar.addMenu("View")

        self.invertAct = QAction("Invert", self, checkable=True)
        self.invertAct.setStatusTip("Swap foreground/background (black↔white)")
        self.invertAct.triggered.connect(self.toggle_invert_cmap)
        viewMenu.addAction(self.invertAct)

        zoomAction = QAction("Inset zoom", self)
        zoomAction.triggered.connect(self.open_zoom_dialog)
        viewMenu.addAction(zoomAction)

    def _rebuild_color_by_actions(self):
        # 1) Remove *only* the old color-by actions
        for act in self._colorByActions:
            self.kymoMenu.removeAction(act)
        self._colorByActions.clear()

        # 2) Insert one fresh QAction per binary custom column
        for col in self.trajectoryCanvas.custom_columns:
            if self.trajectoryCanvas._column_types.get(col) == "binary":
                act = QAction(f"Color by {col}", self, checkable=True)
                act.toggled.connect(lambda checked, c=col, a=act:
                                    self._on_color_by_toggled(c, a, checked))
                self.kymoMenu.insertAction(self._colorBySeparator, act)
                self._colorByActions.append(act)

        # 3) If the previously-selected column was removed, reset to defaults
        if (self.kymoCanvas.color_by_column
            and self.kymoCanvas.color_by_column
                not in self.trajectoryCanvas.custom_columns):
            self.kymoCanvas.set_color_by(None)
            # also uncheck all actions just in case
            for act in self._colorByActions:
                act.setChecked(False)

    def _on_color_by_toggled(self, column_name, action, checked):
        if checked:
            # uncheck the others
            for act in self._colorByActions:
                if act is not action:
                    act.setChecked(False)
            self.kymoCanvas.set_color_by(column_name)
        else:
            # user untoggled it → go back to defaults
            self.kymoCanvas.set_color_by(None)

    def open_kymopreferences_dialog(self):
        """
        Open a preferences dialog to adjust settings such as the ROI line width
        and the integration method used when sampling the ROI line.
        """
        # Get current values (default line width 2 and method "max")
        current_line_width = getattr(self, "line_width", 2)
        current_method = getattr(self, "line_integration_method", "max")
        
        dialog = KymoLineOptionsDialog(current_line_width, current_method, self)
        if dialog.exec_() == QDialog.Accepted:
            line_width, method = dialog.getValues()
            self.line_width = line_width
            self.line_integration_method = method

    def set_search_radius(self):
        current_radius = self.searchWindowSpin.value()
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Set Search Radius")
        dialog.setLabelText(
            "<b>Search Radius</b><br>"
            "<i>shortcut: r + scroll</i>"
        )
        dialog.setIntRange(4, 50)
        dialog.setIntValue(current_radius)

        # Center the label vertically & horizontally
        label = dialog.findChild(QLabel)
        if label:
            label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)

        if dialog.exec_() == QDialog.Accepted:
            self.searchWindowSpin.setValue(dialog.intValue())

    def set_tracking_mode(self):
        current_mode = getattr(self, "tracking_mode", "Independent")
        options = ["Independent", "Tracked", "Smooth"] #, "Same center"

        dialog = QInputDialog(self)
        dialog.setWindowTitle("Set Tracking Mode")
        dialog.setLabelText(
            "<b>Tracking Mode</b><br>"
            "<i>shortcut: t</i>"
        )
        dialog.setComboBoxItems(options)
        dialog.setComboBoxEditable(False)
        dialog.setTextValue(current_mode)
        dialog.setStyleSheet(QApplication.instance().styleSheet())

        # Center the label vertically & horizontally
        label = dialog.findChild(QLabel)
        if label:
            label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)

        if dialog.exec_() == QDialog.Accepted:
            new_mode = dialog.textValue()
            self.tracking_mode = new_mode
            if hasattr(self, "trackingModeCombo"):
                self.trackingModeCombo.setCurrentText(new_mode)

    def open_zoom_dialog(self):
        # Create a modal dialog to adjust the zoom window size.
        dialog = QDialog(self)
        dialog.setWindowTitle("Set Inset Size")
        layout = QVBoxLayout(dialog)
        
        # Spinbox to choose the value.
        spinbox = QSpinBox(dialog)
        spinbox.setRange(4, 50)
        spinbox.setValue(self.insetViewSize.value())
        layout.addWidget(spinbox)
        
        # Add Set and Cancel buttons.
        buttonLayout = QHBoxLayout()
        setButton = QPushButton("Set", dialog)
        cancelButton = QPushButton("Cancel", dialog)
        buttonLayout.addWidget(setButton)
        buttonLayout.addWidget(cancelButton)
        layout.addLayout(buttonLayout)
        
        # Connect buttons.
        setButton.clicked.connect(lambda: self.set_zoom_window_size(spinbox.value(), dialog))
        cancelButton.clicked.connect(dialog.reject)

        # when they click "Set" we'll 1) commit the new size, 2) close the dialog, 
        # 3) and then force a redraw of the inset using our last‐seen parameters
        def on_set():
            # 1) commit the new window size and close the dialog
            self.set_zoom_window_size(spinbox.value(), dialog)

            # 2) grab the old inset parameters
            image, center, old_crop, zoom_factor, fcenter, fsigma, fpeak, bkgr, ivalue = \
                self.movieCanvas._last_inset_params

            # 3) re‑fire update_inset with the NEW crop_size
            self.movieCanvas.update_inset(
                image, center, spinbox.value(), zoom_factor,
                fitted_center=fcenter,
                fitted_sigma=fsigma,
                fitted_peak=fpeak,
                intensity_value=ivalue,
                offset = bkgr
            )

        setButton.clicked.connect(on_set)

        dialog.exec_()

    def set_zoom_window_size(self, value, dialog):
        # Update the insetViewSize value and close the dialog.
        self.insetViewSize.setValue(value)
        dialog.accept()

    def infer_axes_from_shape(shape):
        """
        Build an ImageJ-style axes string from a NumPy shape tuple:
        - T (time)   for the first dim if >1
        - Z (z-slice) for the next if >1
        - C (channel) for the next if >1
        - Y, X always the last two dims
        """
        axes = []
        letter_map = ['T', 'Z', 'C']
        for i, length in enumerate(shape[:-2]):
            axes.append(letter_map[i] if length > 1 else letter_map[i].lower())
        axes += ['Y', 'X']
        # Only keep the “real” axes (uppercase)
        return ''.join(ax for ax in axes if ax.isupper())

    def handle_movie_load(self, fname=None, pixelsize=None, frameinterval=None):
        # If save_and_load_routine is active, don't open the dialog.
        if self.save_and_load_routine:
            # Reset the flag so it only applies once.
            self.save_and_load_routine = False
        else:
            # Open the file dialog (you can use self as parent for a nicer look)
            fname, _ = QFileDialog.getOpenFileName(
                self, "Open Movie TIFF", self._last_dir, "TIFF Files (*.tif *.tiff)"
            )
        
        # If no file was chosen, exit.
        if not fname:
            return

        # Pass the chosen filename to load_movie.
        self.load_movie(fname, pixelsize=pixelsize, frameinterval=frameinterval)

    def load_movie(self, fname=None, pixelsize=None, frameinterval=None):

        self.clear_flag = False
        self.cancel_left_click_sequence()

        if (self.rois or self.kymographs or
            (hasattr(self, 'trajectoryCanvas') and self.trajectoryCanvas.trajectories)):
            reply = QMessageBox.question(
                self,
                "Clear existing data?",
                "Clear existing data?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.clear_flag = True

        if fname:
            self._last_dir = os.path.dirname(fname)
            try:
                with tifffile.TiffFile(fname) as tif:
                    temp_movie = tif.asarray()
                    self.movie_metadata = tif.imagej_metadata or {}
                    page = tif.pages[0]

                tags = page.tags
                description = page.description

                if pixelsize is not None and frameinterval is not None:
                    self.pixel_size = pixelsize
                    self.frame_interval = frameinterval

                else:
                    
                    y_size = None
                    self.pixel_size = None
                    self.frame_interval = None

                    import re
                    
                    if description:
                        # Look for a pattern like "Voxel size: 0.1100x0.1100x1"
                        m = re.search(r'Voxel size:\s*([\d\.]+)[xX]([\d\.]+)[xX]([\d\.]+)', description)
                        if m:
                            try:
                                # Convert the strings to floats.
                                # The typical order in ImageJ is "x_size x y_size x z_size".
                                #x_size = float(m.group(1))
                                y_size = float(m.group(2))
                                #z_size = float(m.group(3))
                                # Return as [z, y, x]
                                self.pixel_size = y_size*1000
                            except Exception as e:
                                print("Error parsing voxel size from ImageDescription:", e)

                    if self.pixel_size is None:
                        if 'YResolution' in tags:
                            value = tags['YResolution'].value
                            try:
                                # If value is a tuple, compute pixels per micron:
                                num, denom = value
                                # pixels per micron = num/denom; thus pixel size in microns is 1/(num/denom)
                                self.pixel_size = float(denom)*1000 / float(num)
                            except Exception:
                                try:
                                    # Otherwise try to convert directly to a float.
                                    self.pixel_size = float(value)*1000
                                except Exception:
                                    pass

                    desc = tif.pages[0].tags["ImageDescription"].value
                    try:
                        match = re.search(r'finterval=([\d\.]+)', desc)
                        if match:
                            self.frame_interval = float(match.group(1))*1000
                    except Exception:
                        pass

                    if self.pixel_size is not None:
                        if self.pixel_size < 0.1:
                            self.pixel_size = None
                    if self.frame_interval is not None:
                        if self.frame_interval < 0.1:
                            self.frame_interval = None

                try:
                    shape = temp_movie.shape
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Invalid movie : {e}")
                    return

                if temp_movie.ndim not in (3, 4):
                    QMessageBox.critical(self, "Error", f"Invalid movie shape: {temp_movie.shape}")
                    return

                self.referenceImage = None
                self.refBtn.setVisible(False)
                self.refBtn.setChecked(False)
                self.sumBtn.setChecked(False)
                self.zoomInsetFrame.setVisible(False)
                self.movieCanvas._last_inset_params = None
                self.movieCanvas._inset_update_pending = False

                # Initialize (or reset) contrast settings for multi‑channel movies.
                self.channel_contrast_settings = {}      # for “normal” mode
                self.channel_sum_contrast_settings = {}   # for sum‐mode
                self.reference_contrast_settings = {}

                self.movieNameLabel.setText(os.path.basename(fname))

                # ── blank out the histogram canvas entirely ────────────────────────────────
                if hasattr(self, 'histogramCanvas'):
                    self.histogramCanvas.ax.cla()
                    self.histogramCanvas.ax.axis("off")
                    self.histogramCanvas.draw_idle()
                # ── blank out the intensity/plot canvas (two sub‐axes) ────────────────────
                if hasattr(self, 'intensityCanvas'):
                    # top subplot
                    self.intensityCanvas.ax_top.cla()
                    self.intensityCanvas.ax_top.axis("off")
                    # bottom subplot
                    self.intensityCanvas.ax_bottom.cla()
                    self.intensityCanvas.ax_bottom.axis("off")
                    self.intensityCanvas.draw_idle()
                # ── blank out the velocity histogram canvas ───────────────────────────────
                if hasattr(self, 'velocityCanvas'):
                    self.velocityCanvas.ax_vel.cla()
                    self.velocityCanvas.ax_vel.axis("off")
                    self.velocityCanvas.draw_idle()

                # ── blank out the zoom‐inset widget ─────────────────────────────────────────
                if hasattr(self, "zoomInsetFrame"):
                    # hide the whole frame
                    self.zoomInsetFrame.setVisible(False)

                if hasattr(self, "zoomInsetWidget"):
                    # clear its axes
                    self.zoomInsetWidget.ax.clear()
                    self.zoomInsetWidget.ax.axis("off")
                    self.zoomInsetWidget.draw_idle()

                self.movieCanvas.clear_canvas()
                self.movie = temp_movie
                self.original_movie = self.movie.copy()

                # Reset the frame cache whenever a new movie is loaded.
                self.frame_cache = {}

                if self.movie.ndim == 4:
                    # 4D movie (multi‑channel)
                    if self.movie_metadata and "axes" in self.movie_metadata:
                        axes_str = self.movie_metadata["axes"]  # e.g., "TXYC" or "TCYX"
                        # For example, if the letter "C" is found, use that index:
                        self._channel_axis = axes_str.find("C")
                    else:
                        self.update_channel_axis_options()
                    self.update_movie_channel_combo()
                    first_frame = self.get_movie_frame(0)
                else:
                    # 3D movie (single channel)
                    self._channel_axis = None
                    self.movieChannelCombo.blockSignals(True)
                    self.movieChannelCombo.clear()
                    self.movieChannelCombo.addItem("1")
                    self.movieChannelCombo.blockSignals(False)
                    # Now call update_movie_channel_combo even for 3D movies:
                    self.update_movie_channel_combo()
                    first_frame = self.movie[0]

                max_frame = self.movie.shape[0]
                self.frameSlider.setMinimum(0)
                self.frameSlider.setMaximum(max_frame - 1)
                self.frameSlider.setValue(0)
                self.frameNumberLabel.setText("1")

                margin = 0
                full_width = self.movieCanvas.image.shape[1]
                full_height = self.movieCanvas.image.shape[0]
                self.movieCanvas.zoom_center = (full_width/2, full_height/2)
                self.movieCanvas.display_image(first_frame)

                self.movieCanvas.draw_idle()
                self.movieCanvas.clear_sum_cache()

                if self.clear_flag:
                    self.clear_rois()
                    self.clear_kymographs()
                    self.trajectoryCanvas.clear_trajectories(prompt=False)
                    self.clear_flag = False

                self.update_scale_label()

                if self.pixel_size is None or self.frame_interval is None:
                    self.set_scale()

                self.last_kymo_by_channel = {}

                self.flash_message("Loaded movie")

            except Exception as e:

                QMessageBox.critical(self, "Error", f"Could not load movie:\n{str(e)}")

    def get_movie_frame(self, frame_idx, channel_override=None):
        if self.movie is None:
            return None

        # For 4D movies, include selected channel and channel axis in the cache key.
        if self.movie.ndim == 4:
            if channel_override is None:
                # Use the main GUI's channel selection.
                selected_chan = int(self.movieChannelCombo.currentText()) - 1
            else:
                # Use the override value.
                selected_chan = channel_override
            channel_axis = self._channel_axis
            cache_key = (frame_idx, selected_chan, channel_axis)
        else:
            cache_key = frame_idx

        # Initialize cache if it doesn't exist.
        if not hasattr(self, "frame_cache"):
            self.frame_cache = {}

        # Return the frame from the cache if available.
        if cache_key in self.frame_cache:
            return self.frame_cache[cache_key]

        try:
            if self.movie.ndim == 4:
                idx = [0] * self.movie.ndim
                idx[0] = frame_idx
                for ax in range(1, self.movie.ndim):
                    idx[ax] = selected_chan if ax == channel_axis else slice(None)
                frame = self.movie[tuple(idx)]
            else:
                frame = self.movie[frame_idx]
            # Store the computed frame in the cache.
            self.frame_cache[cache_key] = frame
            return frame
        except IndexError:
            print(f"index {frame_idx} out of bounds.")
            return None

    def on_channel_axis_changed(self):
        """
        Called when the user changes the channel axis selection.
        Verify that the selected axis is valid for the loaded movie.
        If the axis is invalid, display an error popup and revert to the previous working axis.
        """
        new_axis = self._channel_axis  # set by your ChannelAxisDialog
        old_axis = getattr(self, "_channel_axis", 1)

        # If no movie is loaded, simply update the stored axis.
        if self.movie is None:
            self._channel_axis = new_axis
            return

        try:
            # Try accessing the movie dimension with the new axis.
            _ = self.movie.shape[new_axis]
            # If valid, update the stored channel axis.
            self._channel_axis = new_axis
            # Update the channel dropdown that depends on the channel axis.
            self.update_movie_channel_combo()
        except Exception as e:
            # If the axis is invalid, show an error and revert to the previous value.
            QMessageBox.critical(self, "Error", f"Invalid channel axis: {new_axis}.\nError: {str(e)}")
            self._channel_axis = old_axis

    def update_channel_axis_options(self):
        """
        Update the stored channel axis using the movie's available axes (excluding axis 0).
        Choose as default the axis with the smallest size (typically the channel axis).
        """
        if self.movie is None or self.movie.ndim != 4:
            return

        # List candidate axes: all axes except axis 0.
        candidate_axes = list(range(1, self.movie.ndim))
        # Pick the axis with the smallest size.
        default_axis = min(candidate_axes, key=lambda ax: self.movie.shape[ax])
        self._channel_axis = default_axis

        # Update the channel combo box options.
        self.update_movie_channel_combo()

    def on_channel_changed(self, index):
        # only multi-channel movies
        if self.movie is None or self.movie.ndim != 4:
            return

        self.cancel_left_click_sequence()

        # 1) figure out which channel we’re on
        ch = index + 1
        self.flash_message(f"Channel {ch}")

        # 2) refresh kymographs
        self.update_kymo_list_for_channel()

        # 3) pick the right contrast-settings dict
        if self.sumBtn.isChecked():
            settings_store = self.channel_sum_contrast_settings
            display_fn     = self.movieCanvas.display_sum_frame
        else:
            settings_store = self.channel_contrast_settings
            display_fn     = None  # we’ll use update_image_data below

        # 4) if first time for this channel, compute & stash defaults
        if ch not in settings_store:
            # grab the very first frame of this channel
            frame0 = self.get_movie_frame(0)
            p15, p99 = np.percentile(frame0, (15, 99))
            vmin0    = int(p15 * (1.05 if self.sumBtn.isChecked() else 1.0))
            vmax0    = int(p99 * (1.20 if self.sumBtn.isChecked() else 1.10))
            delta    = vmax0 - vmin0
            settings_store[ch] = {
                'vmin':         vmin0,
                'vmax':         vmax0,
                'extended_min': vmin0 - int(0.7 * delta),
                'extended_max': vmax0 + int(1.4 * delta)
            }

        # 5) pull out the stored settings
        s = settings_store[ch]

        # 6) update the slider so it “sticks”
        slider = self.contrastControlsWidget.contrastRangeSlider
        slider.blockSignals(True)
        slider.setMinimum(s['extended_min'])
        slider.setMaximum(s['extended_max'])
        slider.setRangeValues(s['vmin'], s['vmax'])
        slider.blockSignals(False)

        # 7) push the contrast into the MovieCanvas
        mc = self.movieCanvas
        mc._default_vmin = s['vmin']
        mc._default_vmax = s['vmax']
        mc._vmin         = s['vmin']
        mc._vmax         = s['vmax']
        if mc._im is not None:
            mc._im.set_clim(s['vmin'], s['vmax'])

        # 8) redraw either sum‐mode or normal‐mode
        if self.sumBtn.isChecked():
            mc.clear_sum_cache()
            mc.display_sum_frame()
        else:
            # keep the same time‐point
            frame_idx = self.frameSlider.value()
            frame     = self.get_movie_frame(frame_idx)
            mc.update_image_data(frame)

        ch = index + 1
        self._ch_overlay.setText(f"ch{ch}")
        self._ch_overlay.adjustSize()
        self._reposition_channel_overlay()
        self._ch_overlay.show()


    def _on_overlay_clicked(self):
        # build a stand-alone QMenu
        menu = QMenu(None)
        menu.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground)

        # populate it
        n_channels = (self.movie.shape[self._channel_axis]
                    if self.movie is not None and self.movie.ndim == 4 else 1)
        for i in range(n_channels):
            action = menu.addAction(f"ch{i+1}")
            action.setData(i)

        # compute global positions
        lbl = self._ch_overlay
        lbl_global = lbl.mapToGlobal(QPoint(0, 0))
        lbl_width  = lbl.width()
        # measure menu width from its sizeHint
        menu_width = menu.sizeHint().width()

        # center the menu’s x under the label
        x = lbl_global.x() + (lbl_width  - menu_width)//2
        # drop it just below the label
        y = lbl_global.y() + lbl.height()

        chosen = menu.exec_(QPoint(x, y))
        if chosen:
            idx = chosen.data()
            self.movieChannelCombo.setCurrentIndex(idx)

    def _reposition_channel_overlay(self):
        lbl = self._ch_overlay
        # 10px from left, 10px from top
        x = 10
        y = 10
        lbl.move(x, y)

    def update_movie_channel_combo(self, flash=False):
        if self.movie is None:
            self.channelControlContainer.setVisible(False)
            self._ch_overlay.hide()
            return

        current_channel = 1
        if self.movie.ndim == 4:
            self.channelControlContainer.setVisible(False) #OVERRIDE
            channel_axis = self._channel_axis
            try:
                current_channel = int(self.movieChannelCombo.currentText())
            except Exception:
                current_channel = 1

            self._ch_overlay.setText(f"ch{current_channel}")
            self._ch_overlay.adjustSize()
            self._reposition_channel_overlay()
            self._ch_overlay.show()

            self.movieChannelCombo.blockSignals(True)
            self.movieChannelCombo.clear()
            num_channels = self.movie.shape[channel_axis]
            for i in range(num_channels):
                self.movieChannelCombo.addItem(str(i + 1))
            self.movieChannelCombo.setCurrentIndex(current_channel - 1)
            self.movieChannelCombo.blockSignals(False)
            self.movieChannelCombo.setEnabled(True)

            # Get the first frame for the selected channel if needed.
            first_frame = self.get_movie_frame(0)
        else:
            self.channelControlContainer.setVisible(False)
            self._ch_overlay.hide()
            self.movieChannelCombo.blockSignals(True)
            self.movieChannelCombo.clear()
            self.movieChannelCombo.addItem("1")
            self.movieChannelCombo.blockSignals(False)
            self.movieChannelCombo.setEnabled(False)
            first_frame = self.movie[0]
            # Set default current_channel for 3D movies:
            current_channel = 1
    
        # Branch on the current mode (sum vs. normal) and obtain contrast settings.
        if self.sumBtn.isChecked():
            self.movieCanvas.clear_sum_cache()
            self.movieCanvas.display_sum_frame()
            # Sum mode – use channel_sum_contrast_settings.
            if current_channel not in self.channel_sum_contrast_settings:
                p15, p99 = np.percentile(first_frame, (15, 99))
                default_vmin = int(p15 * 1.05)
                default_vmax = int(p99 * 1.2)
                delta = default_vmax - default_vmin
                settings = {
                    'vmin': default_vmin,
                    'vmax': default_vmax,
                    'extended_min': default_vmin - int(0.7 * delta),
                    'extended_max': default_vmax + int(1.4 * delta)
                }
                self.channel_sum_contrast_settings[current_channel] = settings
            else:
                settings = self.channel_sum_contrast_settings[current_channel]
        else:
            # Normal mode – use channel_contrast_settings.
            if current_channel not in self.channel_contrast_settings:
                p15, p99 = np.percentile(first_frame, (15, 99))
                default_vmin = int(p15)
                default_vmax = int(p99 * 1.1)
                delta = default_vmax - default_vmin
                settings = {
                    'vmin': default_vmin,
                    'vmax': default_vmax,
                    'extended_min': default_vmin - int(0.7 * delta),
                    'extended_max': default_vmax + int(1.4 * delta)
                }
                self.channel_contrast_settings[current_channel] = settings
            else:
                settings = self.channel_contrast_settings[current_channel]

        if flash:
            self.flash_message(f"Channel {current_channel}")
        #print(current_channel, settings)

        # Update the movie canvas's internal contrast defaults.
        self.movieCanvas._default_vmin = settings['vmin']
        self.movieCanvas._default_vmax = settings['vmax']
        self.movieCanvas._vmin = settings['vmin']
        self.movieCanvas._vmax = settings['vmax']

        # Update the contrast slider.
        self.contrastControlsWidget.contrastRangeSlider.blockSignals(True)
        self.contrastControlsWidget.contrastRangeSlider.setMinimum(settings['extended_min'])
        self.contrastControlsWidget.contrastRangeSlider.setMaximum(settings['extended_max'])
        self.contrastControlsWidget.contrastRangeSlider.setRangeValues(settings['vmin'], settings['vmax'])
        self.contrastControlsWidget.contrastRangeSlider.blockSignals(False)
        self.contrastControlsWidget.contrastRangeSlider.update()

        # Finally, display the first frame with the correct contrast.
        self.movieCanvas.update_image_data(first_frame)



    def load_reference(self):
        if self.movie is None:
            QMessageBox.warning(self, "", 
                "Please load a movie before loading a reference.")
            return
        fname, _ = QFileDialog.getOpenFileName(
            self, "Open Reference Image", "", "Image Files (*.tif *.tiff *.png *.jpg)"
        )
        if not fname:
            return
        try:
            ref_img = tifffile.imread(fname)
            if ref_img.ndim == 3:
                # Heuristic to decide if the image is multi‐channel:
                small_first = ref_img.shape[0] <= 4 and ref_img.shape[0] > 1
                small_last = ref_img.shape[-1] <= 4 and ref_img.shape[-1] > 1

                if small_first and small_last:
                    choice, ok = QtWidgets.QInputDialog.getItem(
                        self, "Channel Axis Ambiguity",
                        "Is the reference image stored as channels-first (axis 0) or channels-last (last axis)?",
                        ["Channels-first", "Channels-last"],
                        0, False
                    )
                    if not ok:
                        return
                    channel_axis = 0 if choice == "Channels-first" else -1
                elif small_first:
                    channel_axis = 0
                elif small_last:
                    channel_axis = -1
                else:
                    channel_axis = None

                if channel_axis is not None:
                    if channel_axis == 0:
                        channels = ref_img.shape[0]
                        prompt = "Reference image has multiple channels (channels-first). Choose one:"
                    else:
                        channels = ref_img.shape[-1]
                        prompt = "Reference image has multiple channels (channels-last). Choose one:"
                    if channels > 1:
                        channel_str, ok = QtWidgets.QInputDialog.getItem(
                            self, "Select Channel", prompt,
                            [f"Channel {i+1}" for i in range(channels)], 0, False
                        )
                        if not ok:
                            return
                        chosen_channel = int(channel_str.split()[-1]) - 1
                        if channel_axis == 0:
                            ref_img = ref_img[chosen_channel, :, :]
                        else:
                            ref_img = ref_img[:, :, chosen_channel]
            ref_img = np.squeeze(ref_img)

            # (Optionally, verify that its dimensions match the current movie frame.)
            if self.movie is not None:
                movie_frame = self.movieCanvas.image
                if movie_frame is None:
                    movie_frame = self.get_movie_frame(0)
                if movie_frame.shape[0:2] != ref_img.shape[0:2]:
                    QMessageBox.warning(
                        self,
                        "Dimension Mismatch",
                        "The reference image x/y dimensions do not match the currently displayed movie frame."
                    )
                    return

            self.referenceImage = ref_img

            # *** Compute reference contrast settings ***
            p15, p99 = np.percentile(ref_img, (15, 99))
            ref_vmin = int(p15)
            ref_vmax = int(p99 * 1.1)
            delta = ref_vmax - ref_vmin
            self.reference_contrast_settings = {
                'vmin': ref_vmin,
                'vmax': ref_vmax,
                'extended_min': ref_vmin - int(0.7 * delta),
                'extended_max': ref_vmax + int(1.4 * delta)
            }
            # Make the Ref. button visible.
            self.refBtn.setVisible(True)
        except Exception as e:
            QMessageBox.critical(self, "Load Error", f"Could not load reference image:\n{str(e)}")


    def on_ref_toggled(self, checked):
        if checked:
            self.refBtn.setStyleSheet("background-color: #375bb5;")
            # Turn off sum mode if active.
            if self.sumBtn.isChecked():
                self.sumBtn.setChecked(False)
            # Apply the reference image and its contrast.
            settings = self.reference_contrast_settings
            self.movieCanvas.set_display_range(settings['vmin'], settings['vmax'])
            self.movieCanvas.image = self.referenceImage
            self.movieCanvas._im.set_data(self.referenceImage)
            self.movieCanvas.draw_idle()
        else:
            self.refBtn.setStyleSheet("")
            # Only revert if sum mode is off.
            if not self.sumBtn.isChecked():
                frame = self.get_movie_frame(self.frameSlider.value())
                if frame is not None:
                    # Determine the appropriate contrast settings.
                    if self.movie.ndim == 4:
                        try:
                            current_channel = int(self.movieChannelCombo.currentText())
                        except Exception:
                            current_channel = 1
                        # If the contrast settings haven't been set for the current channel, compute defaults.
                        if current_channel not in self.channel_contrast_settings:
                            p15, p99 = np.percentile(frame, (15, 99))
                            default_vmin = int(p15)
                            default_vmax = int(p99 * 1.1)
                            delta = default_vmax - default_vmin
                            settings = {
                                'vmin': default_vmin,
                                'vmax': default_vmax,
                                'extended_min': default_vmin - int(0.7 * delta),
                                'extended_max': default_vmax + int(1.4 * delta)
                            }
                            self.channel_contrast_settings[current_channel] = settings
                        else:
                            settings = self.channel_contrast_settings[current_channel]
                    else:
                        # For a single-channel (3D) movie, we always use channel 1.
                        if 1 not in self.channel_contrast_settings:
                            p15, p99 = np.percentile(frame, (15, 99))
                            default_vmin = int(p15)
                            default_vmax = int(p99 * 1.1)
                            delta = default_vmax - default_vmin
                            settings = {
                                'vmin': default_vmin,
                                'vmax': default_vmax,
                                'extended_min': default_vmin - int(0.7 * delta),
                                'extended_max': default_vmax + int(1.4 * delta)
                            }
                            self.channel_contrast_settings[1] = settings
                        else:
                            settings = self.channel_contrast_settings[1]
                    # Now apply the contrast to the movie canvas.
                    self.movieCanvas.set_display_range(settings['vmin'], settings['vmax'])
                    self.movieCanvas.image = frame
                    self.movieCanvas._im.set_data(frame)
                    self.movieCanvas.draw_idle()

    def load_kymographs(self):
        fnames, _ = QFileDialog.getOpenFileNames(
            self, "Open Kymograph TIFF(s)", "", "TIFF Files (*.tif *.tiff)"
        )
        if fnames:
            for fname in fnames:
                try:
                    kymo = tifffile.imread(fname)
                    # Check if the kymograph has an invalid shape.
                    if kymo.ndim == 3 and kymo.shape[-1] not in (1, 3, 4):
                        QMessageBox.warning(
                            self, "Invalid Kymograph",
                            f"File '{os.path.basename(fname)}' has an invalid shape {kymo.shape}.\n"
                            "It must be a 2D image or a 3D image with 1, 3, or 4 channels.\n"
                            "This file will be skipped."
                        )
                        continue  # Skip this file.
                        
                    # Generate a unique key for the kymograph.
                    base = os.path.basename(fname)
                    unique_name = base
                    suffix = 1
                    while unique_name in self.kymographs:
                        suffix += 1
                        unique_name = f"{base}-{suffix}"
                    self.kymographs[unique_name] = kymo
                    self.kymoCombo.insertItem(0, unique_name)
                    self.kymoCombo.setEnabled(self.kymoCombo.count() > 0)
                    self.kymoCombo.setCurrentIndex(0)
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Could not load kymograph {fname}:\n{str(e)}")
        
        self.update_kymo_visibility()

    def load_kymograph_with_overlays(self):
        """
        Load a single kymograph TIFF and its ImageJ multipoint overlay,
        convert into start/end trajectory points, build a DataFrame,
        and hand off to trajectory loader logic.
        """
        fname, _ = QFileDialog.getOpenFileName(
            self,
            "Open Kymograph TIFF", "",
            "TIFF Files (*.tif *.tiff)"
        )
        if not fname:
            return

        try:
            # 1) load image array and validate shape
            kymo = tifffile.imread(fname)
            if kymo.ndim == 3 and kymo.shape[-1] not in (1, 3, 4):
                QMessageBox.warning(
                    self, "Invalid Kymograph",
                    f"File '{os.path.basename(fname)}' has invalid shape {kymo.shape}."
                )
                return

            # store in UI
            base = os.path.basename(fname)
            unique = base
            i = 1
            while unique in self.kymographs:
                i += 1
                unique = f"{base}-{i}"
            self.kymographs[unique] = kymo
            self.kymoCombo.insertItem(0, unique)
            self.kymoCombo.setCurrentIndex(0)

            # 2) extract ROI blob from ImageJ metadata or raw tag
            with tifffile.TiffFile(fname) as tif:
                ij = tif.imagej_metadata or {}
                blob = None
                if 'Overlays' in ij and ij['Overlays']:
                    blob = ij['Overlays'][0]
                elif 'ROI' in ij:
                    blob = ij['ROI']
                else:
                    tag = tif.pages[0].tags.get(50838)
                    blob = tag.value if tag else None

            if blob is None:
                QMessageBox.information(
                    self, "No Overlay",
                    f"No multipoint ROI found in '{base}'."
                )
                return

            # 3) parse blob into list of (x,y)
            points = parse_roi_blob(blob)
            # 4) group into trajectories: two points = one trajectory
            rows = []
            for idx in range(0, len(points), 2):
                sx, sy = points[idx]      # sy is already measured from top
                ex, ey = points[idx+1]
                fs = int(round(sy))
                fe = int(round(ey))
                # map x-axis point back into movie coords
                xs, ys = self.compute_roi_point(self.rois[self.roiCombo.currentText()], sx)
                xe, ye = self.compute_roi_point(self.rois[self.roiCombo.currentText()], ex)
                traj_id = idx//2 + 1
                rows.append({
                    'Trajectory': traj_id,
                    'Frame': fs,
                    'Search Center X': xs,
                    'Search Center Y': ys
                })
                rows.append({
                    'Trajectory': traj_id,
                    'Frame': fe,
                    'Search Center X': xe,
                    'Search Center Y': ye
                })

            df = pd.DataFrame(rows)

            # 5) hand off to a helper that processes a DataFrame
            # You should refactor load_trajectories() into load_trajectories_from_df(df)
            self.trajectoryCanvas.load_trajectories_from_df(df)
            self.kymoCombo.setEnabled(True)

        except Exception as e:
            QMessageBox.critical(self, "Load Error", f"Failed to load overlays: {e}")

        self.update_table_visibility()

    def load_roi(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Open ROI File(s)", "", "ROI Files (*.roi *.zip)"
        )
        if not files:
            return

        # 1) Read all ROIs
        rois = {}
        for file in files:
            if file.lower().endswith('.zip'):
                rois.update(read_roi.read_roi_zip(file))
            else:
                rois.update(read_roi.read_roi_file(file))

        # 2) Replace internal ROI store & rebuild the ROI combo
        self.rois = rois
        self.roiCombo.clear()
        for roi_name in sorted(rois):
            self.roiCombo.addItem(roi_name)
        self.roiCombo.setEnabled(bool(rois))
        self.update_roilist_visibility()

        # 3) Ask user if they want to generate kymographs now
        resp = QMessageBox.question(
            self,
            "Generate Kymographs",
            "Generate kymographs for these ROIs across all channels?",
            QMessageBox.Yes | QMessageBox.No
        )

        # 4) Determine how many channels we have
        if self.movie.ndim == 4:
            n_chan = self.movie.shape[self._channel_axis]
        else:
            n_chan = 1

        # 5) For each ROI, loop over ALL channels
        for roi_name, roi in rois.items():
            for ch in range(n_chan):
                kymo_name = f"C{ch+1}-{roi_name}"
                if resp == QMessageBox.Yes:
                    # Generate kymograph for this channel
                    kymo = self.movieCanvas.generate_kymograph(
                        roi, channel_override=ch
                    )
                    self.kymographs[kymo_name] = kymo
                    self.kymo_roi_map[kymo_name] = {
                        "roi":      roi_name,
                        "channel":  ch+1,
                        "orphaned": False
                    }
                else:
                    # Register as orphaned
                    self.kymo_roi_map[kymo_name] = {
                        "roi":      roi_name,
                        "channel":  ch+1,
                        "orphaned": True
                    }

        # 6) Rebuild & show only current channel’s list
        self.update_kymo_list_for_channel()
        self.update_kymo_visibility()

    def update_kymo_list_for_channel(self):
        ch = int(self.movieChannelCombo.currentText())
        self.kymoCombo.blockSignals(True)
        self.kymoCombo.clear()

        # 1) Populate only this channel’s items
        for name, info in self.kymo_roi_map.items():
            if info["channel"] == ch and not info.get("orphaned", False):
                self.kymoCombo.addItem(name)
        self.kymoCombo.blockSignals(False)

        # Gather current list
        names = [self.kymoCombo.itemText(i) for i in range(self.kymoCombo.count())]
        last  = self.last_kymo_by_channel.get(ch, None)

        # 2) No kymos → clear
        if not names:
            self.kymoCombo.setCurrentIndex(-1)
            self.kymoCanvas.ax.cla()
            self.kymoCanvas.ax.axis("off")
            self.kymoCanvas.draw_idle()
            return

        # 3) If last is present in names, use it
        if last in names:
            sel = last

        # 4) If no last remembered (first time), pick first
        elif last is None:
            sel = names[0]
            # remember this as the last for next time
            self.last_kymo_by_channel[ch] = sel

        # 5) You have a last but it’s not in names → user deleted it ⇒ clear
        else:
            self.kymoCombo.setCurrentIndex(-1)
            self.kymoCanvas.ax.cla()
            self.kymoCanvas.ax.axis("off")
            self.kymoCanvas.draw_idle()
            return

        # 6) Finally, select & display
        self.kymoCombo.setCurrentText(sel)
        self.kymograph_changed()

    def _save_zoom_for_roi(self, roiName):
        """Stash the current scale & center under this ROI."""
        c = self.kymoCanvas
        self._roi_zoom_states[roiName] = (c.scale, c.zoom_center)

    def _restore_zoom_for_roi(self, roiName):
        """Re-apply stored scale & center (pan) for this ROI, if any."""
        if roiName in self._roi_zoom_states:
            scale, center = self._roi_zoom_states[roiName]
            c = self.kymoCanvas
            c.scale       = scale
            c.zoom_center = center
            c.update_view()

    def kymograph_changed(self):

        self.cancel_left_click_sequence()
        
        # — Save last ROI’s view if user did a manual zoom/pan
        if self._last_roi and self.kymoCanvas.manual_zoom:
            self._save_zoom_for_roi(self._last_roi)
            self.kymoCanvas.manual_zoom = False

        # — Grab the new selection
        kymoName = self.kymoCombo.currentText()
        info     = self.kymo_roi_map.get(kymoName)
        if not info:
            # no valid kymo → clear
            self.kymoCanvas.ax.cla()
            self.kymoCanvas.ax.axis("off")
            self.kymoCanvas.draw_idle()
            self._last_roi = None
            return

        roiName = info["roi"]

        # — Sync the ROI & channel controls
        self.roiCombo.setCurrentText(roiName)
        self.movieChannelCombo.blockSignals(True)
        self.movieChannelCombo.setCurrentIndex(info["channel"] - 1)
        self.movieChannelCombo.blockSignals(False)

        # — Display the kymograph
        img = np.flipud(self.kymographs[kymoName])
        self.kymoCanvas.display_image(img)
        if self.traj_overlay_button.isChecked():
            self.kymoCanvas.draw_trajectories_on_kymo()
        self.kymoCanvas.draw_idle()

        # — Restore pan+zoom for this ROI (if any)
        self._restore_zoom_for_roi(roiName)

        # — Remember for next save
        self._last_roi = roiName

    def delete_current_kymograph(self):
        current = self.kymoCombo.currentText()
        if not current:
            return

        # 1) Remove mapping and delete its ROI
        mapping = self.kymo_roi_map.pop(current, None)
        if mapping:
            roi_name = mapping["roi"]
            # check if _any_ other kymos still reference this ROI
            still_refs = any(
                info["roi"] == roi_name
                for info in self.kymo_roi_map.values()
            )
            if not still_refs:
                # safe to delete the ROI itself
                if roi_name in self.rois:
                    del self.rois[roi_name]
                    idx = self.roiCombo.findText(roi_name)
                    if idx >= 0:
                        self.roiCombo.removeItem(idx)
                        # drop saved zoom/pan state
                        if roi_name in self._roi_zoom_states:
                            del self._roi_zoom_states[roi_name]
                        if self._last_roi == roi_name:
                            self._last_roi = None

        # 2) Delete the kymograph itself
        if current in self.kymographs:
            del self.kymographs[current]

        # 3) Remove it from the kymo combo
        old_index = self.kymoCombo.currentIndex()
        self.kymoCombo.removeItem(old_index)

        # 4) Refresh display: show next kymo if any, otherwise clear canvas
        if self.kymoCombo.count() > 0:
            new_index = old_index - 1 if old_index > 0 else 0
            self.kymoCombo.setCurrentIndex(new_index)
        else:
            self.kymoCanvas.ax.cla()
            self.kymoCanvas.ax.axis("off")
            self.kymoCanvas.draw_idle()

        # 5) Update UI visibility in case lists are now empty
        self.kymograph_changed()
        self.update_kymo_visibility()
        self.update_roilist_visibility()

    def clear_kymographs(self):
        # 1) First, remove any ROIs associated with kymographs
        for mapping in list(self.kymo_roi_map.values()):
            # extract the ROI name whether mapping is a dict or a plain string
            if isinstance(mapping, dict):
                roi_name = mapping.get("roi")
            else:
                roi_name = mapping

            if roi_name and roi_name in self.rois:
                # delete from the internal dict
                del self.rois[roi_name]
                # remove from the ROI combo box
                idx = self.roiCombo.findText(roi_name)
                if idx >= 0:
                    self.roiCombo.removeItem(idx)

        # 2) Clear the kymo→ROI map
        self.kymo_roi_map.clear()

        # 3) Now clear all kymographs
        self.kymoCombo.clear()
        self.kymographs = {}
        # drop saved zoom/pan state
        self._roi_zoom_states.clear()
        self._last_roi = None

        # 4) Clear the canvas
        self.kymoCanvas.ax.cla()
        self.kymoCanvas.ax.axis("off")
        self.kymoCanvas.draw_idle()

        # 5) Update UI visibility
        self.update_kymo_visibility()
        self.update_roilist_visibility()

    def clear_rois(self):
        # Clear the ROI combo box and the dictionary.
        self.roiCombo.clear()
        self.rois = {}

        # Remove the ROI overlay line (if any) from the movie canvas.
        if hasattr(self.movieCanvas, "roi_line") and self.movieCanvas.roi_line is not None:
            try:
                self.movieCanvas.roi_line.remove()
            except Exception:
                pass
            self.movieCanvas.roi_line = None

        # Also remove any additional ROI lines and text annotations.
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

        # Redraw the canvas so that no ROI overlays remain.
        self.movieCanvas.draw_idle()

        self.update_kymo_visibility()
        self.update_roilist_visibility()

    def on_kymo_click(self, event):

        if self.looping:
            self.stoploop()

        self.kymoCanvas.manual_zoom = False

        # — only if click was inside the image —
        if (self.kymoCanvas.image is None or 
            event.xdata is None or event.ydata is None):
            return
        H, W = self.kymoCanvas.image.shape[:2]
        if not (0 <= event.xdata <= W and 0 <= event.ydata <= H):
            return
        
        # 1) if we just picked a label, consume this click and reset the flag
        if self._ignore_next_kymo_click:
            self._ignore_next_kymo_click = False
            return

        # 2) your normal early‑outs
        if self.kymoCanvas.image is None or event.xdata is None or event.ydata is None:
            return

        if event.button == 3 and self._skip_next_right:
            # we just showed the menu for a label—don’t do live updates
            self._skip_next_right = False
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

        self.intensityCanvas.clear_highlight()
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
        if self.get_movie_frame(frame_idx) is not None:
            self.frameSlider.blockSignals(True)
            self.frameSlider.setValue(frame_idx)
            self.frameSlider.blockSignals(False)
            self.frameNumberLabel.setText(f"{frame_idx+1}")

        # — record the anchor in both kymo‐space & movie‐space —
        self.analysis_anchors.append((frame_idx, event.xdata, event.ydata))
        self.analysis_points.append((frame_idx, x_orig, y_orig))

        # — draw a small circle there —
        marker = self.kymoCanvas.temporary_circle(event.xdata, event.ydata,
                                              size=6, color='#7da1ff')
        self.analysis_markers.append(marker)

        # — initialize the live temp line once —
        if self.temp_analysis_line is None:
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
        if len(self.analysis_anchors) > 1:
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


    def on_kymo_release(self, event):
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

        # Compute crop sizes based on your UI spinboxes.
        search_crop_size = int(2 * self.searchWindowSpin.value())
        zoom_crop_size = int(self.insetViewSize.value())

        # then also clear any magenta gaussian circle on the movie canvas
        removed = self.movieCanvas.remove_gaussian_circle()
        if removed:
            self.movieCanvas.draw_idle()

        # Update the MovieCanvas overlay for visual feedback.
        frame_number = frame_idx+1
        self.movieCanvas.overlay_rectangle(x_orig, y_orig, search_crop_size, frame_number=frame_number)

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
        self.kymoCanvas.overlay_spot_center(event.xdata, event.ydata, size=6, color='#7da1ff')

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
        self.kymoCanvas.draw_trajectories_on_kymo()
        self.kymoCanvas.draw_idle()

        # Now perform the analysis (which will recompute the histogram based on the current spot analysis)
        self.analyze_spot_at_event(event)

    def on_kymo_hover(self, event):
        # Debug output
        #print("on_kymo_hover called. xdata:", event.xdata, "ydata:", event.ydata)
        
        # Check that the event is in the kymograph canvas and has valid data
        if event.inaxes != self.kymoCanvas.ax or event.xdata is None or event.ydata is None:
            #self.pixelValueLabel.setText("No hover data")
            return

        kymograph = self.kymoCanvas.image
        if kymograph is None:
            #self.pixelValueLabel.setText("No kymograph loaded")
            return

        if self.looping:
            self.pixelValueLabel.setText("")
            return
        
        # Convert floating point coordinates to integer indices for the kymograph
        x = event.xdata
        y = event.ydata
        #print("Computed kymo pixel indices: x =", x, "y =", y)
        
        # Check if the computed indices are within image bounds
        if not (0 <= x < kymograph.shape[1] and 0 <= y < kymograph.shape[0]):
            #self.pixelValueLabel.setText("Coordinates out of bounds")
            return

        # For a vertically flipped kymograph, the frame index is computed as below:
        num_frames = kymograph.shape[0]
        frame_val = num_frames - y

        # If an ROI exists, compute the corresponding movie coordinate based on the ROI.
        if self.roiCombo.count() > 0:
            roi_key = self.roiCombo.currentText()
            if roi_key in self.rois:
                roi = self.rois[roi_key]
                # Compute movie coordinate using your compute_roi_point() function.
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
        display_text = f"F: {int(frame_val)} X: {real_x_fortxt:.2f} Y: {real_y_fortxt:.2f} V: {pixel_val}"
        #print("Setting label text to:", display_text)
        
        # Update the label.
        self.pixelValueLabel.setText(display_text)
        self.pixelValueLabel.update()


    def _prompt_and_add_kymo_value(self, col_name, row):
        # 1) get the existing value (may be "")
        existing = self.trajectoryCanvas.trajectories[row].get("custom_fields", {}).get(col_name, "")

        # 2) open the dialog, pre‐filled with `existing`
        val, ok = QInputDialog.getText(
            self,
            f"Edit {col_name} value",    # window title
            f"{col_name}:",               # label
            QLineEdit.Normal,             # echo mode
            existing                      # <— initial text
        )
        if not ok:
            return

        # 3) update model & UI
        self.trajectoryCanvas.trajectories[row].setdefault("custom_fields", {})[col_name] = val
        self.trajectoryCanvas.writeToTable(row, col_name, val)

    def update_analysis_line(self):
        """
        Draw a permanent dashed line connecting the user‑clicked kymo anchors in order.
        """
        # Must have at least two anchors
        if not hasattr(self, "analysis_anchors") or len(self.analysis_anchors) < 2:
            return

        # Get display parameters
        kymoName = self.kymoCombo.currentText()
        if not kymoName:
            return

        roi_key = (
            self.roiCombo.currentText()
            if self.roiCombo.count() > 0
            else kymoName
        )
        roi = self.rois.get(roi_key, None)
        kymo = self.kymographs.get(kymoName, None)
        if kymo is None:
            return

        # How many frames tall is the movie?
        max_frame = self.movie.shape[0]

        # Build the lists of display coords directly from anchors:
        disp_xs = []
        disp_ys = []
        for (frame_idx, kx, ky) in self.analysis_anchors:
            disp_xs.append(kx)
            disp_ys.append(ky)

        # Remove any old permanent line
        if hasattr(self, "permanent_analysis_line") and self.permanent_analysis_line is not None:
            try:
                self.permanent_analysis_line.remove()
            except Exception:
                pass

        # Draw a simple dashed line through the anchors
        (self.permanent_analysis_line,) = self.kymoCanvas.ax.plot(
            disp_xs,
            disp_ys,
            color='#7da1ff',
            linewidth=1.5,
            linestyle='--'
        )
        self.kymoCanvas.draw_idle()

    def on_kymo_motion(self, event):
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
        # Call this once when you switch into ROI mode:
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

    def on_frame_slider_changed(self, frame_idx):
        """
        Called when the user drags or clicks the frame slider.
        We'll display that frame in the MovieCanvas, plus update the label.
        """
        self.set_current_frame(frame_idx)

    def set_current_frame(self, frame_number):
        if self.movie is None:
            return
        max_frame = self.movie.shape[0]
        frame_number = max(0, min(frame_number, max_frame - 1))
        
        # Save current view limits.
        current_xlim = self.movieCanvas.ax.get_xlim()
        current_ylim = self.movieCanvas.ax.get_ylim()
        
        # Update slider and label.
        self.frameSlider.blockSignals(True)
        self.frameSlider.setValue(frame_number)
        self.frameSlider.blockSignals(False)
        self.frameNumberLabel.setText(f"{frame_number + 1}")
        
        # Get the new frame.
        if self.movieCanvas.sum_mode:
            self.movieCanvas.display_sum_frame()  # You may wish to modify display_sum_frame too.
        else:
            frame_image = self.get_movie_frame(frame_number)
            if frame_image is not None:
                # Update only the image data (without recalculating view limits)
                self.movieCanvas.update_image_data(frame_image)
        
        # Restore the saved view limits (thus preserving the manual zoom)
        self.movieCanvas.ax.set_xlim(current_xlim)
        self.movieCanvas.ax.set_ylim(current_ylim)
        self.movieCanvas.draw_idle()

    def jump_to_analysis_point(self, index, pan_only=True, animate="ramp"):

        t0 = time.perf_counter()  # Stop timing

        # ——— Early exits & locals ———
        if not self.analysis_frames or not self.analysis_search_centers:
            return
        n = len(self.analysis_frames)
        if index < 0 or index >= n:
            return

        mc = self.movieCanvas
        kc = self.kymoCanvas
        ic = getattr(self, 'intensityCanvas', None)
        hc = getattr(self, 'histogramCanvas', None)

        # cache arrays once
        centers = np.asarray(self.analysis_search_centers)  # shape (N,2)
        frame = self.analysis_frames[index]
        cx, cy = centers[index]

        # block widgets
        self.frameSlider.blockSignals(True)
        if hasattr(self, 'analysisSlider'):
            self.analysisSlider.blockSignals(True)

        # disable repaint until we're done
        mc.setUpdatesEnabled(False)
        kc.setUpdatesEnabled(False)

        try:
            # ——— 1) Compute new limits ———
            cur_xlim = mc.ax.get_xlim()
            cur_ylim = mc.ax.get_ylim()
            w, h = cur_xlim[1] - cur_xlim[0], cur_ylim[1] - cur_ylim[0]

            if not mc.manual_zoom:
                if pan_only:
                    new_xlim = (cx - w/2, cx + w/2)
                    new_ylim = (cy - h/2, cy + h/2)
                else:
                    xs, ys = centers[:,0], centers[:,1]
                    M = 20
                    xmin, xmax = xs.min() - M, xs.max() + M
                    ymin, ymax = ys.min() - M, ys.max() + M
                    L = max(xmax - xmin, ymax - ymin, 100)
                    cx0 = (xmin + xmax)/2
                    cy0 = (ymin + ymax)/2
                    new_xlim = (cx0 - L/2, cx0 + L/2)
                    new_ylim = (cy0 - L/2, cy0 + L/2)

                # ——— 2) Pan/zoom once ———
                if animate == "ramp":
                    self.animate_axes_transition(new_xlim, new_ylim, duration=300)
                elif animate == "linear":
                    self.animate_view_transition(new_xlim, new_ylim, duration=15)
                else:
                    mc.ax.set_xlim(new_xlim)
                    mc.ax.set_ylim(new_ylim)
                    mc.zoom_center = ((new_xlim[0]+new_xlim[1])/2,
                                    (new_ylim[0]+new_ylim[1])/2)

            # ——— 3) Update the image frame ———
            if mc.sum_mode:
                mc.display_sum_frame()
                frame_img = mc.image
            else:
                frame_img = self.get_movie_frame(frame)
                if frame_img is None:
                    return
                mc.update_image_data(frame_img)

            # ——— 4) Restore manual zoom limits if needed ———
            if animate != "discrete" and mc.manual_zoom:
                mc.ax.set_xlim(cur_xlim)
                mc.ax.set_ylim(cur_ylim)

            # ——— 5) Overlays ———
            mc.overlay_rectangle(cx, cy, int(2*self.searchWindowSpin.value()), frame_number=frame+1)
            mc.remove_gaussian_circle()

            # draw fit circle & intensity highlight
            if hasattr(self, "analysis_fit_params") and index < len(self.analysis_fit_params):
                fc, fs, pk = self.analysis_fit_params[index]
            else:
                fc = fs = pk = None

            if fc is not None and fs is not None:
                mc.add_gaussian_circle(fc, fs)
                if ic: ic.highlight_current_point()
            elif ic:
                ic.highlight_current_point(override=True)

            # ——— 6) Inset & kymo ———
            intensity = getattr(self, "analysis_intensities", [None])[index]
            background = getattr(self, "analysis_background", [None])[index]
            center_for_inset = fc if fc is not None else (cx, cy)
            mc.update_inset(frame_img, center_for_inset,
                            int(self.insetViewSize.value()), zoom_factor=2,
                            fitted_center=fc,
                            fitted_sigma=fs,
                            fitted_peak=pk,
                            intensity_value=intensity,
                            offset = background)

            # only overlay kymo marker if ROI present
            kymo_name = self.kymoCombo.currentText()
            # look up its channel in the map
            info = self.kymo_roi_map.get(kymo_name, {})
            current_kymo_ch = info.get("channel", None)
            if self.analysis_channel == current_kymo_ch or self.analysis_channel is None:
                kymo_name = self.kymoCombo.currentText()
                if kymo_name and kymo_name in self.kymographs and self.rois:
                    roi = self.rois[self.roiCombo.currentText()]
                    xk = None
                    # check fit‐center first, then raw center
                    if fc is not None and is_point_near_roi(fc, roi):
                        xk = self.compute_kymo_x_from_roi(
                            roi, fc[0], fc[1],
                            self.kymographs[kymo_name].shape[1]
                        )
                    elif is_point_near_roi((cx, cy), roi):
                        xk = self.compute_kymo_x_from_roi(
                            roi, cx, cy,
                            self.kymographs[kymo_name].shape[1]
                        )
                    if xk is not None:
                        disp_frame = (self.movie.shape[0] - 1) - frame
                        kc.overlay_spot_center(
                            xk, disp_frame, size=6,
                            color='magenta' if fc is not None else 'grey'
                        )

            # ——— 7) Histogram & sliders ———
            if hc:
                center_hist = fc if fc is not None else (cx, cy)
                hc.update_histogram(frame_img, center_hist,
                                    int(2*self.searchWindowSpin.value()),
                                    sigma=fs, intensity=intensity, background=background)
                
            self.frameSlider.setValue(frame)
            if hasattr(self, 'analysisSlider'):
                self.analysisSlider.setValue(index)

        finally:
            # re-enable updates & single redraw
            mc.setUpdatesEnabled(True)
            kc.setUpdatesEnabled(True)
            self.kymoCanvas.draw()
            self.movieCanvas.draw()

            self.frameSlider.blockSignals(False)
            if hasattr(self, 'analysisSlider'):
                self.analysisSlider.blockSignals(False)

    def animate_view_transition(self, new_xlim, new_ylim, duration=20, steps=1):
        # Reset the flag at the start of the animation.
        self._stop_animation = False

        initial_xlim = self.movieCanvas.ax.get_xlim()
        initial_ylim = self.movieCanvas.ax.get_ylim()
        delay = duration // steps

        def step(i):
            # If the stop flag is set, abort the animation.
            if self._stop_animation:
                return

            # If manual zoom has become active (and we are not looping), abort.
            if getattr(self.movieCanvas, "manual_zoom", False) and not self.looping:
                self._stop_animation = True  # signal to stop further steps
                return

            if i > steps:
                self.movieCanvas.ax.set_xlim(new_xlim)
                self.movieCanvas.ax.set_ylim(new_ylim)
                cx_new = (new_xlim[0] + new_xlim[1]) / 2.0
                cy_new = (new_ylim[0] + new_ylim[1]) / 2.0
                self.movieCanvas.zoom_center = (cx_new, cy_new)
                self.movieCanvas.draw_idle()
            else:
                t = i / steps
                interp_xlim = (initial_xlim[0]*(1-t) + new_xlim[0]*t,
                            initial_xlim[1]*(1-t) + new_xlim[1]*t)
                interp_ylim = (initial_ylim[0]*(1-t) + new_ylim[0]*t,
                            initial_ylim[1]*(1-t) + new_ylim[1]*t)
                self.movieCanvas.ax.set_xlim(interp_xlim)
                self.movieCanvas.ax.set_ylim(interp_ylim)
                self.movieCanvas.draw_idle()
                QTimer.singleShot(delay, lambda: step(i + 1))
        step(0)

    def animate_axes_transition(self, new_xlim, new_ylim, duration=250):
        """
        Animate the axes limits transition from the current limits to new_xlim/new_ylim.
        new_xlim and new_ylim should each be a two-element tuple: (min, max).
        """
        # Create the new target rectangle from the new axes limits.
        new_rect = QRectF(new_xlim[0], new_ylim[0], new_xlim[1] - new_xlim[0], new_ylim[1] - new_ylim[0])
        
        # Create our animator object wrapping the matplotlib axes.
        animator = AxesRectAnimator(self.movieCanvas.ax)
        
        # Create a QPropertyAnimation on the 'axesRect' property.
        anim = QPropertyAnimation(animator, b"axesRect")
        anim.setDuration(duration)
        anim.setStartValue(animator.getAxesRect())
        anim.setEndValue(new_rect)
        anim.setEasingCurve(QEasingCurve.InOutQuad) #anim.setEasingCurve(QEasingCurve.Linear)
        
        # Start the animation.
        anim.start()
        
        # Keep a reference to avoid garbage collection.
        self._axes_anim = anim

    def compute_trajectory_background(self, get_frame, points, crop_size):
        """
        get_frame: function(frame_index) → 2D numpy array
        points:   list of (frame, cx, cy) tuples
        crop_size: size of the square patch
        returns:  median of the border pixels (outer 10%) of all patch pixels
        """
        half = crop_size // 2
        all_values = []

        for f, cx, cy in points:
            img = get_frame(f)
            if img is None:
                continue
            H, W = img.shape
            x0, y0 = int(round(cx)), int(round(cy))
            sub = img[
                max(0, y0-half):min(H, y0+half),
                max(0, x0-half):min(W, x0+half)
            ]
            if sub.size:
                # collect border pixels (outer 10% of this subimage)
                h_sub, w_sub = sub.shape
                border = max(1, int(min(h_sub, w_sub) * 0.1))
                edges = np.concatenate([
                    sub[:border, :].ravel(),
                    sub[-border:, :].ravel(),
                    sub[:, :border].ravel(),
                    sub[:, -border:].ravel()
                ])
                all_values.append(edges)

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
            frames, coords, search_centers, ints, fits, background, colors = self._compute_analysis(points, trajectory_background)
        except Exception as e:
            QMessageBox.warning(self, "", "There was an error adding computing this trajectory. Please try again (consider a longer trajectory or different radius).")
            print(f"_compute failed: {e}")
            self.cancelled = True
        
        if self.cancelled:
            return
        
        self.analysis_start, self.analysis_end = points[0], points[-1]
        self.analysis_frames, self.analysis_original_coords, self.analysis_search_centers = frames, coords, search_centers
        self.analysis_intensities, self.analysis_fit_params, self.analysis_background = ints, fits, background
        self.analysis_colors = colors
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

        # slider
        if hasattr(self, 'analysisSlider'):
            s = self.analysisSlider
            s.blockSignals(True)
            s.setRange(0, len(frames)-1)
            s.setValue(0)
            s.blockSignals(False)

    def _compute_analysis(self, points, bg=None, showprogress=True):
        mode = self.tracking_mode
        if mode == "Independent":
            return self._compute_independent(points, bg, showprogress)
        elif mode == "Tracked":
            return self._compute_tracked(points, bg, showprogress)
        elif mode == "Smooth":
            # 1) do the independent pass
            try:
                frames, coords, search_centers, ints, fit_params, background, colors = (
                    self._compute_independent(points, bg, showprogress)
                )
            except Exception as e:
                print(f"_compute_independent failed: {e}")
                self.cancelled = True
                return None, None, None, None, None, None
            return self._postprocess_smooth(frames, coords, ints, fit_params, background, colors, bg)
        elif mode == "Same center":
            return self._compute_same_center(points, bg, showprogress)
        else:
            raise ValueError(f"Unknown mode {mode!r}")

    def _compute_same_center(self, points, bg=None, showprogress=True):
        """
        points: list of (frame, cx, cy) tuples.
        We just refit a Gaussian at each (cx,cy) in exactly each frame.
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
        colors                 = ["grey"] * N

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
                # reuse your gaussian fit call
                fc, sigma, intensity, peak, bkgr = perform_gaussian_fit(
                    img,
                    (cx, cy),
                    int(2 * self.searchWindowSpin.value()),
                    pixelsize=self.pixel_size,
                    bg_fixed=bg
                )
                if fc is not None:
                    colors[idx]                = "magenta"
                    background[idx]            = max(0, bkgr)
                    fit_params[idx]            = (fc, sigma, peak)
                    integrated_intensities[idx] = max(0, intensity)
            # otherwise leave None/grey

            # update progress
            if progress:
                progress.setValue(idx+1)
                QApplication.processEvents()
                if progress.wasCanceled():
                    self.cancelled = True
                    progress.close()
                    break

        if progress:
            progress.close()

        return all_frames, all_coords, all_coords, integrated_intensities, fit_params, background, colors

    def _compute_independent(self, points, bg=None, showprogress=True):  

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
        colors                = ["grey"]*N

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
            f1, x1, y1 = points[i]
            f2, x2, y2 = points[i+1]
            seg = list(range(f1, f2+1)) if i==0 else list(range(f1+1, f2+1))
            n = len(seg)

            for j, f in enumerate(seg):
                # compute independent center
                t = j/(n-1) if n>1 else 0
                cx = x1 + t*(x2-x1)
                cy = y1 + t*(y2-y1)
                all_coords.append((cx, cy))
                t0 = time.perf_counter()
                img = frame_cache[f]
                if img is not None:
                    fc, sigma, intensity, peak, bkgr = perform_gaussian_fit(
                        img, (cx, cy), int(2 * self.searchWindowSpin.value()), pixelsize = self.pixel_size, bg_fixed=bg
                    )
                    if fc:
                        colors[idx]                = "magenta"
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
                        self.cancelled = True
                        progress.close()
                        return all_frames, all_coords, all_coords, integrated_intensities, fit_params, background, colors

        if progress:
            progress.close()

        return all_frames, all_coords, all_coords, integrated_intensities, fit_params, background, colors

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
        colors                 = []

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
        canceled  = False

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
                new_center, fc, sigma, intensity, peak, bkgr, color = \
                    self._track_frame(
                        frame_cache[f],
                        icx, icy,
                        current_center,
                        search_radius,
                        pixel_size,
                        bg
                    )

                new_centers.append(new_center)
                colors.append(color)
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
                        self.cancelled = True
                        progress.close()
                        return (
                            all_frames,
                            independent_centers,
                            new_centers,
                            integrated_intensities,
                            fit_params,
                            background,
                            colors
                        )

            if canceled:
                break

        # 6) clean up progress dialog
        if progress:
            progress.setValue(total_frames)
            progress.close()

        # 7) return frames, independent centers, blended centers, and fit results
        return all_frames, independent_centers, new_centers, integrated_intensities, fit_params, background, colors

    def _track_frame(self, img, icx, icy, current, radius, pixel_size, bg=None):
        if img is None:
            # fallback to midpoint
            nc = ((current[0]+icx)/2, (current[1]+icy)/2)
            return nc, None, None, None, None, "grey"

        fc, sigma, intensity, peak, bkgr = perform_gaussian_fit(
            img, current, radius,
            pixelsize=pixel_size,
            bg_fixed=bg
        )
        if fc is None:
            nc = ((current[0]+icx)/2, (current[1]+icy)/2)
            return nc, None, None, None, None, None, "grey"

        dx, dy = fc[0]-icx, fc[1]-icy
        d       = np.hypot(dx, dy)
        w       = np.exp(-0.5*(d/radius))
        nc      = (w*fc[0] + (1-w)*icx, w*fc[1] + (1-w)*icy)
        return nc, fc, sigma, intensity, peak, bkgr, "magenta"

    def _postprocess_smooth(self, all_frames, all_coords, ints, fit_params, background, colors, bg_fixed=None):
        N = len(fit_params)
        # 1) pull out your raw spot centers (None → nan, nan)
        spot_centers = np.array([
            (fc[0], fc[1]) if fc is not None else (np.nan, np.nan)
            for fc, _, _ in fit_params
        ], dtype=float)  # shape (N,2)

        # 2) linearly interpolate over gaps
        idx = np.arange(N)
        valid = ~np.isnan(spot_centers[:,0])
        if valid.sum() < 2:
            # Not enough valid points to interpolate → bail out
            return all_frames, all_coords, ints, fit_params, background, colors

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
            # all_coords[i] = (cx, cy)    # use smoothed for your next pass
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
                # overwrite both spot_centers _and_ your returned list
                # all_coords[i]       = tuple(fc)
                fit_params[i]       = (fc, sx, peak)
                ints[i]             = intensity
                background[i]       = bkgr
                colors[i]           = "magenta"
            else:
                fit_params[i]       = (None, None, None)
                ints[i]             = None
                background[i]       = None
                colors[i]           = "grey"

        # new_centers = np.array([
        #     (fc[0], fc[1]) if fc is not None else (np.nan, np.nan)
        #     for fc, _, _ in fit_params
        # ], dtype=float)  # shape (N,2)
        # self.debugPlotRequested.emit(
        #     spot_centers.tolist(),
        #     smooth_centers.tolist(),
        #     new_centers.tolist()
        # )

        return all_frames, all_coords, all_coords, ints, fit_params, background, colors

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
            self.jump_to_analysis_point(self.loop_index-1, animate="discrete")
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
        # Now that _bg is valid, this will draw your marker
        self.intensityCanvas.highlight_current_point()

        # ——— 4) Ensure the canvas actually shows it ———
        self.intensityCanvas.fig.canvas.blit(self.intensityCanvas.ax_bottom.bbox)


    def finalizeTrajectory(self, analysis_points, trajid=None):
        if not analysis_points or len(analysis_points) < 2:
            return
        
        if self.sumBtn.isChecked():
            self.sumBtn.setChecked(False)

        analysis_points.sort(key=lambda pt: pt[0])
        self.analysis_points = analysis_points
        self.analysis_start, self.analysis_end = analysis_points[0], analysis_points[-1]
        
        self.run_analysis_points()

        if self.cancelled:
            self.cancelled=False
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
        # # now plot
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
    #         pass  # you could also scan starts/ends of runs
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
    #     If you pass `posteriors` (N_steps×n_states), you could overlay confidence.
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

        # hand off to your usual finalize
        self.analysis_points = full_pts

        kymo_name = self.kymoCombo.currentText()
        # look up its channel in the map
        info = self.kymo_roi_map.get(kymo_name, {})
        current_kymo_ch = info.get("channel", None)

        self.analysis_channel = current_kymo_ch
        self.finalizeTrajectory(self.analysis_points)
        # self.kymoCanvas.unsetCursor()

    def endMovieClickSequence(self):
        if not hasattr(self, "analysis_points") or not self.analysis_points or len(self.analysis_points) < 2:
            return
        self.analysis_anchors = []
        self.analysis_roi = None
        self.analysis_channel = int(self.movieChannelCombo.currentText()) #1 indexed
        self.finalizeTrajectory(self.analysis_points)
        self.cancel_left_click_sequence()
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

    def analyze_spot_at_event(self, event):
        if self.kymoCanvas.image is None or event.xdata is None or event.ydata is None:
            return
        if self.movie is None:
            return

        num_frames = self.movie.shape[0]
        frame_idx = (num_frames - 1) - int(round(event.ydata))
        frame_image = self.get_movie_frame(frame_idx)
        if frame_image is None:
            return

        kymoName = self.kymoCombo.currentText()
        if not kymoName:
            return
        roi_key = self.roiCombo.currentText() if self.roiCombo.count() > 0 else kymoName
        if roi_key not in self.rois:
            return
        roi = self.rois[roi_key]
        if "x" not in roi or "y" not in roi:
            return

        x_orig, y_orig = self.compute_roi_point(roi, event.xdata)
        search_crop_size = int(2 * self.searchWindowSpin.value())
        zoom_crop_size = int(self.insetViewSize.value())

        bg_guess = None
        # Define crop boundaries around current center guess
        H, W = frame_image.shape
        half = search_crop_size // 2
        cx_int = int(round(x_orig))
        cy_int = int(round(y_orig))
        x1 = max(0, cx_int - half)
        x2 = min(W, cx_int + half)
        y1 = max(0, cy_int - half)
        y2 = min(H, cy_int + half)
        sub = frame_image[y1:y2, x1:x2]
        if sub.size == 0:
            bg_guess = None
        else:
            # Estimate background and initial p0
            counts, bins = np.histogram(sub, bins=50)
            centers = (bins[:-1] + bins[1:]) / 2
            cut = sub.min() + 0.5 * (sub.max() - sub.min())
            bg_guess = np.median(sub[sub < cut]) if np.any(sub < cut) else sub.min()

        # Perform a Gaussian fit on the current frame.
        fitted_center, fitted_sigma, intensity, peak, bkgr = perform_gaussian_fit(frame_image, (x_orig, y_orig), search_crop_size, bg_fixed=bg_guess, pixelsize = self.pixel_size)

        self.zoomInsetFrame.setVisible(True)
        self.movieCanvas.update_inset(frame_image, (x_orig, y_orig), zoom_crop_size, zoom_factor=2,
                                    fitted_center=fitted_center,
                                    fitted_sigma=fitted_sigma,
                                    fitted_peak=peak,
                                    intensity_value=intensity,
                                    offset = bkgr)

        if hasattr(self, "histogramCanvas"):
            # Use the fitted center if available; if not, fall back to the original search center (cx, cy)
            center_for_hist = fitted_center if fitted_center is not None else (x_orig, y_orig)
            self.histogramCanvas.update_histogram(frame_image, center_for_hist, search_crop_size, sigma=fitted_sigma, intensity=intensity)

        # Optionally, you can also add a magenta circle overlay on the MovieCanvas here.
        self.movieCanvas.remove_gaussian_circle()

        if fitted_center is not None and fitted_sigma is not None:
            self.movieCanvas.add_gaussian_circle(fitted_center, fitted_sigma)

        self.movieCanvas.draw_idle()

    def on_movie_click(self, event):
        if (
            event.button == 1 and
            hasattr(event, 'guiEvent') and
            (event.guiEvent.modifiers() & Qt.MetaModifier)
        ):
            return
        # — only if click was inside the image —
        if (self.movieCanvas.image is None or 
            event.xdata is None or event.ydata is None):
            return
        H, W = self.movieCanvas.image.shape[:2]
        if not (0 <= event.xdata <= W and 0 <= event.ydata <= H):
            return
        # Ensure canvas transform is up to date before using event.xdata/ydata
        self.movieCanvas.draw()
        QApplication.processEvents()
        # Only respond if the click landed inside the movie axes
        if event.inaxes != self.movieCanvas.ax:
            return
        if self.looping:
            self.stoploop()
        if self.movieCanvas.roiAddMode:
            if event.button == 1:  # left click
                # Always add the current point
                if not hasattr(self.movieCanvas, 'roiPoints') or not self.movieCanvas.roiPoints:
                    self.movieCanvas.clear_temporary_roi_markers()
                    self.movieCanvas.roiPoints = []
                self.movieCanvas.roiPoints.append((event.xdata, event.ydata))
                self.movieCanvas.update_roi_drawing(current_pos=(event.xdata, event.ydata))
                if event.dblclick:
                    # On double-click, now finalize the ROI (after adding the current click)
                    self.movieCanvas.clear_temporary_roi_markers()
                    self.movieCanvas.finalize_roi()
            return
        
        else:

            if event.button == 2:
                return

            if self.intensityCanvas is not None:
                self.intensityCanvas.clear_highlight()

            if self.kymoCanvas is not None:
                self.clear_temporary_analysis_markers()

            # Only respond if the click is in the movie canvas and has valid coordinates.
            if event.inaxes != self.movieCanvas.ax or event.xdata is None or event.ydata is None:
                return

            frame_image = self.movieCanvas.image
            if frame_image is None:
                return
            x_click, y_click = event.xdata, event.ydata
            search_crop_size = int(2 * self.searchWindowSpin.value())
            zoom_crop_size = int(self.insetViewSize.value())
            # Draw blue rectangle for the search area.
            frame_number = self.frameSlider.value()+1
            self.movieCanvas.overlay_rectangle(x_click, y_click, search_crop_size, frame_number=frame_number)

            bg_guess = None
            # Define crop boundaries around current center guess
            H, W = frame_image.shape
            half = search_crop_size // 2
            cx_int = int(round(x_click))
            cy_int = int(round(y_click))
            x1 = max(0, cx_int - half)
            x2 = min(W, cx_int + half)
            y1 = max(0, cy_int - half)
            y2 = min(H, cy_int + half)
            sub = frame_image[y1:y2, x1:x2]
            if sub.size == 0:
                bg_guess = None
            else:
                # Estimate background and initial p0
                counts, bins = np.histogram(sub, bins=50)
                centers = (bins[:-1] + bins[1:]) / 2
                cut = sub.min() + 0.5 * (sub.max() - sub.min())
                bg_guess = np.median(sub[sub < cut]) if np.any(sub < cut) else sub.min()
            
            # Perform Gaussian fit analysis.
            fitted_center, fitted_sigma, intensity, peak, bkgr = perform_gaussian_fit(
                frame_image, (x_click, y_click), search_crop_size, bg_fixed=bg_guess, pixelsize = self.pixel_size
            )


            self.zoomInsetFrame.setVisible(True)
            self.movieCanvas.update_inset(
                frame_image, (x_click, y_click), zoom_crop_size, zoom_factor=2,
                fitted_center=fitted_center,
                fitted_sigma=fitted_sigma,
                fitted_peak=peak,
                intensity_value=intensity,
                offset = bkgr
            )
            center_to_use = fitted_center if fitted_center is not None else (x_click, y_click)
            if hasattr(self, "histogramCanvas"):
                self.histogramCanvas.update_histogram(frame_image, center_to_use, search_crop_size, fitted_sigma, intensity=intensity)
            # Remove any previous gaussian circle and draw a new one if fit succeeded.
            if fitted_center is None or fitted_sigma is None:
                self.movieCanvas.remove_gaussian_circle()
            else:
                self.movieCanvas.remove_gaussian_circle()
                self.movieCanvas.add_gaussian_circle(fitted_center, fitted_sigma)

            if event.button == 1:
                # LEFT CLICK: Process accumulation of points (without drawing an extra marker).
                self.on_movie_left_click(event)

            if event.button in [1, 3]:
                if fitted_center is not None:
                    self.drift_reference = fitted_center
                    self.spot_frame = self.frameSlider.value()

            self.movieCanvas.draw_idle()

    def on_movie_hover(self, event):
        if self.looping:
            self.pixelValueLabel.setText("")
            return
        # Check that the event is in the movie canvas and has valid coordinates.
        if event.inaxes == self.movieCanvas.ax and event.xdata is not None and event.ydata is not None:
            # Convert floating point data coordinates to integer indices.
            x = int(round(event.xdata))
            y = int(round(event.ydata))
            image = self.movieCanvas.image
            if image is not None and 0 <= x < image.shape[1] and 0 <= y < image.shape[0]:
                pixel_val = image[y, x]
                current_frame = self.frameSlider.value() + 1
                text = f"F: {current_frame} X: {x} Y: {y} V: {pixel_val}"
                # Use the label's font metrics to elide the text if it exceeds the label's width.
                fm = self.pixelValueLabel.fontMetrics()
                elided_text = fm.elidedText(text, Qt.ElideRight, self.pixelValueLabel.width())
                self.pixelValueLabel.setText(elided_text)
            else:
                self.pixelValueLabel.setText("")

            self._last_hover_xy = (event.xdata, event.ydata)
            if not getattr(self, "analysis_points", None):
                self.movieCanvas._manual_marker_active = False
            self.movieCanvas.clear_manual_marker()
        else:
            self.pixelValueLabel.setText("")

        self._last_hover_xy = (event.xdata, event.ydata)

        if not self.movieCanvas.roiAddMode or not self.movieCanvas.roiPoints:
            return

        # throttle to ~50 Hz
        now = time.perf_counter()
        if now - getattr(self, '_last_roi_motion', 0) < 0.02:
            return
        self._last_roi_motion = now

        # build xs, ys from roiPoints + (event.xdata, event.ydata)
        canvas = self.movieCanvas.figure.canvas
        pts = self.movieCanvas.roiPoints + [(event.xdata, event.ydata)]
        xs, ys = zip(*pts)

        # fast blit loop
        canvas.restore_region(self.movieCanvas._roi_bg)
        if getattr(self.movieCanvas, "tempRoiLine", None) is not None:
            self.movieCanvas.tempRoiLine.set_data(xs, ys)
            self.movieCanvas.ax.draw_artist(self.movieCanvas.tempRoiLine)        
        canvas.blit(self.movieCanvas._roi_bbox)

    def on_movie_scroll(self, event):
        if event.inaxes == self.movieCanvas.ax:
            # let the MovieCanvas scroll logic update scale & center…
            # then ask it to redraw & recapture its clean background
            self.movieCanvas._perform_throttled_update()  

    def on_movie_left_click(self, event):
        # Get the current frame index from the frame slider.
        frame_idx = self.frameSlider.value()
        x_click, y_click = event.xdata, event.ydata
        # self.last_anchor_type = 'movie'

        # If we already have a sequence, decide whether to update the last point or start a new one.
        if hasattr(self, "analysis_points") and self.analysis_points:
            last_frame, last_x, last_y = self.analysis_points[-1]
            if frame_idx == last_frame:
                # Same frame: update the last point's coordinates.
                self.analysis_points[-1] = (frame_idx, x_click, y_click)
            elif frame_idx < last_frame:
                # New left click on an earlier frame: start a new sequence.
                self.analysis_points = [(frame_idx, x_click, y_click)]
            else:
                # Otherwise, append the new point.
                self.analysis_points.append((frame_idx, x_click, y_click))
        else:
            self.analysis_points = [(frame_idx, x_click, y_click)]

        # # deactivate the dotted‐line while we draw our X
        # self.movieCanvas._manual_marker_active = True
        # self.movieCanvas._manual_marker_pos = (x_click, y_click)

        # # draw the marker once onto the axes
        # self.movieCanvas.draw_manual_marker()

        # now do a full draw & snapshot the clean background for blitting
        canvas = self.movieCanvas.figure.canvas
        self.movieCanvas.draw()  
        self.movieCanvas._bg = canvas.copy_from_bbox(self.movieCanvas.ax.bbox)

        # and schedule only the incremental blit on future moves
        self.movieCanvas.draw_idle()

        # if it was a double‐click, finish sequence
        if event.dblclick:
            self.endMovieClickSequence()
        else:
            self.movieCanvas.draw_idle()

    def on_movie_release(self, event):
        if event.button == 2 and event.inaxes == self.movieCanvas.ax:
            # pan just ended → redraw & recapture
            self.movieCanvas.update_view()

    def on_movie_motion(self, event):
        # Fast‐blit update for the temporary analysis line.
        line = getattr(self, "temp_movie_analysis_line", None)
        # Nothing to draw if there’s no temporary line.
        if not line:
            return

        # If we’re panning or zooming, do a full redraw & snapshot (hiding the line while snapshotting).
        if self.movieCanvas._is_panning or self.movieCanvas.manual_zoom:
            line.set_visible(False)
            self.movieCanvas.draw()
            canvas = self.movieCanvas.figure.canvas
            # Rebuild our blit‐background without the line.
            self.movieCanvas._bg = canvas.copy_from_bbox(self.movieCanvas.ax.bbox)
            # Clear the manual‐zoom flag.
            self.movieCanvas.manual_zoom = False
            line.set_visible(True)
            return

        # Normal motion: restore background and draw only the line.
        canvas = self.movieCanvas.figure.canvas
        canvas.restore_region(self.movieCanvas._bg)
        self.movieCanvas.ax.draw_artist(line)
        canvas.blit(self.movieCanvas.ax.bbox)

    def update_movie_analysis_line(self):
        if not hasattr(self, "analysis_points") or not self.analysis_points:
            return
        # Sort the points by frame index.
        points = sorted(self.analysis_points, key=lambda pt: pt[0])
        xs = [pt[1] for pt in points]
        ys = [pt[2] for pt in points]
        # Remove any existing temporary line.
        if hasattr(self, "temp_movie_analysis_line") and self.temp_movie_analysis_line is not None:
            try:
                self.temp_movie_analysis_line.remove()
            except Exception:
                pass
        # — 1) create an animated temp‐line if needed —
        if not hasattr(self, "temp_movie_analysis_line") or self.temp_movie_analysis_line is None:
            self.temp_movie_analysis_line, = self.movieCanvas.ax.plot(
                xs, ys,
                color='#7da1ff', linewidth=1.5, linestyle='--'
            )
            self.temp_movie_analysis_line.set_animated(True)

            # — take one full draw & grab the background now —
            canvas = self.movieCanvas.figure.canvas
            canvas.draw()
            self.movieCanvas._bg = canvas.copy_from_bbox(self.movieCanvas.ax.bbox)
        else:
            # just update data for subsequent blits
            self.temp_movie_analysis_line.set_data(xs, ys)

        # NB: NO draw_idle() here — we’ll blit in on_movie_motion

    def cancel_left_click_sequence(self):
        # If we are in ROI mode, clear the temporary ROI drawing state.
        if self.movieCanvas.roiAddMode:
            # Clear any temporarily drawn ROI line
            if hasattr(self.movieCanvas, 'tempRoiLine') and self.movieCanvas.tempRoiLine is not None:
                try:
                    self.movieCanvas.tempRoiLine.remove()
                except Exception:
                    pass
                self.movieCanvas.tempRoiLine = None
            # Clear any x-markers (drawn with overlay_spot_center)
            self.movieCanvas.clear_temporary_roi_markers()
            # Reset the list of ROI points so the user can start fresh.
            self.movieCanvas.roiPoints = []

        # Otherwise, perform the existing cancellation for analysis sequences.
        # Clear any temporary movie analysis line (if used)
        if hasattr(self, "temp_movie_analysis_line") and self.temp_movie_analysis_line is not None:
            try:
                self.temp_movie_analysis_line.remove()
            except Exception:
                pass
            self.temp_movie_analysis_line = None

        # Clear the temporary dotted line used in the kymograph (temp_analysis_line)
        if hasattr(self, "temp_analysis_line") and self.temp_analysis_line is not None:
            try:
                self.temp_analysis_line.remove()
            except Exception:
                pass
            self.temp_analysis_line = None

        # Also clear any additional dotted lines stored in leftclick_temp_lines
        if hasattr(self, "leftclick_temp_lines"):
            for line in self.leftclick_temp_lines:
                try:
                    line.remove()
                except Exception:
                    pass
            self.leftclick_temp_lines = []

        # Clear the blue markers stored in analysis_markers
        if hasattr(self, "analysis_markers") and self.analysis_markers:
            for marker in self.analysis_markers:
                try:
                    if hasattr(marker, '__iter__'):
                        for m in marker:
                            m.remove()
                    else:
                        marker.remove()
                except Exception:
                    pass
            self.analysis_markers = []

        # Also clear the permanent dotted line if it exists
        if hasattr(self, "permanent_analysis_line") and self.permanent_analysis_line is not None:
            try:
                self.permanent_analysis_line.remove()
            except Exception:
                pass
            self.permanent_analysis_line = None

        # Also clear any inter-anchor dotted segments
        if hasattr(self, 'permanent_analysis_lines'):
            for seg in self.permanent_analysis_lines:
                try:
                    seg.remove()
                except Exception:
                    pass
            self.permanent_analysis_lines = []

        if hasattr(self.movieCanvas, "rect_overlay") and self.movieCanvas.rect_overlay is not None:
            try:    self.movieCanvas.rect_overlay.remove()
            except: pass
            self.movieCanvas.rect_overlay = None

        self.movieCanvas.remove_gaussian_circle()

        # Clear accumulated left-click points.
        self.analysis_points = []
        self.analysis_anchors = []
        self.analysis_roi = None
        # self.kymoCanvas.unsetCursor()

        self.kymoCanvas.draw_idle()
        self.movieCanvas.draw_idle()

    def toggleTracking(self):
        modes = ["Independent", "Tracked", "Smooth"] #, "Same center"
        try:
            i = modes.index(self.tracking_mode)
        except ValueError:
            i = 0
        new_mode = modes[(i + 1) % len(modes)]
        self.tracking_mode = new_mode

        # if you have a combo box for it, update that too
        if hasattr(self, "trackingModeCombo"):
            self.trackingModeCombo.setCurrentText(new_mode)

        self.flash_message(f"Tracking Mode: {new_mode}")

    def _select_channel(self, requested_channel):
        """Handle 1–8 numeric shortcuts."""
        if (self.movie is not None
                and getattr(self.movie, "ndim", 0) == 4):
            max_ch = self.movie.shape[self._channel_axis]
            if 1 <= requested_channel <= max_ch:
                self.movieChannelCombo.setCurrentIndex(requested_channel - 1)
                if self.sumBtn.isChecked():
                    self.movieCanvas.display_sum_frame()

    def _move_manual_marker(self, dx, dy):
        if self.movie is None or self.movieCanvas.roiAddMode:
            return

        canvas = self.movieCanvas

        # initialize on first WASD press
        if not getattr(canvas, "_manual_marker_active", False):
            # 1) last analysis point?
            if getattr(self, "analysis_points", None):
                _, x0, y0 = self.analysis_points[-1]

            else:
                # 2a) hover point?
                hv = getattr(self, "_last_hover_xy", None)
                h, w = canvas.image.shape
                xmin, xmax = -0.5, w - 0.5
                ymin, ymax = -0.5, h - 0.5

                if hv and xmin <= hv[0] <= xmax and ymin <= hv[1] <= ymax:
                    x0, y0 = hv

                # 2b) last manual marker?
                elif getattr(canvas, "_manual_marker_pos", None):
                    x0, y0 = canvas._manual_marker_pos

                # 2c) true center
                else:
                    x0 = 0.5 * (xmin + xmax)
                    y0 = 0.5 * (ymin + ymax)

            canvas._manual_marker_pos    = [x0, y0]
            canvas._manual_marker_active = True

        # then nudge by (dx,dy)
        canvas._manual_marker_pos[0] += dx
        canvas._manual_marker_pos[1] += dy
        canvas.draw_manual_marker()
        canvas.draw_idle()

    def _simulate_left_click(self):
        """Fire your on_movie_click handler at the manual marker or cursor."""
        if self.movie is None or self.movieCanvas.roiAddMode:
            return
        canvas = self.movieCanvas

        self.intensityCanvas.clear_highlight()

        if getattr(canvas, "_manual_marker_active", False):
            x, y = canvas._manual_marker_pos
        else:
            pos = canvas.mapFromGlobal(QtGui.QCursor.pos())
            x, y = canvas.ax.transData.inverted().transform((pos.x(), pos.y()))

        evt = type("Evt", (), {})()
        evt.xdata   = x
        evt.ydata   = y
        evt.button  = 1
        evt.dblclick = False
        evt.inaxes  = canvas.ax
        self.on_movie_click(evt)

    def _prev_frame(self):
        """Go to previous frame (J)."""
        if self.looping:
            self.stoploop()        
        cur = self.frameSlider.value()
        self.set_current_frame(max(0, cur - 1))

    def _next_frame(self):
        if self.looping:
            self.stoploop()
        """Go to next frame (L)."""
        cur = self.frameSlider.value()
        self.set_current_frame(min(self.movie.shape[0] - 1, cur + 1))

    def keyReleaseEvent(self, event):
        if event.key()==Qt.Key_R and self._radiusPopup:
            new_val = self._radiusSpinLive.value()
            self.searchWindowSpin.setValue(new_val)

            self._radiusPopup.close()
            self._radiusPopup = None
            self._radiusSpinLive = None

            # return focus to main window
            self.activateWindow()
            self.setFocus()

            event.accept()
            return

        super().keyReleaseEvent(event)

    def update_table_visibility(self, adjust_splitter=True):
        has_rows = (self.trajectoryCanvas.table_widget.rowCount() > 0)
        if adjust_splitter:
            if not has_rows:
                total_height = self.rightVerticalSplitter.height()
                self.rightVerticalSplitter.setSizes([total_height, 0])
                self.mainSplitter.handle_y_offset_pct = 0.4955
            else:
                total_height = self.rightVerticalSplitter.height()
                self.rightVerticalSplitter.setSizes([int(0.85 * total_height), int(0.15 * total_height)])
                self.mainSplitter.handle_y_offset_pct = 0.1
        # Determine whether there are any trajectories in the table.
        self.traj_overlay_button.setVisible(has_rows)
        self.delete_button.setVisible(has_rows)
        self.clear_button.setVisible(has_rows)
        self.trajectoryCanvas.hide_empty_columns()


    def eventFilter(self, obj, event):
        # optional: intercept wheel here instead of grabbing
        # if you’d rather do it manually
        if (self._radiusPopup and event.type()==QEvent.Wheel):
            delta = event.angleDelta().y()
            step  = self._radiusSpinLive.singleStep()
            cur   = self._radiusSpinLive.value()
            self._radiusSpinLive.setValue(cur + (step if delta>0 else -step))
            return True
        return super().eventFilter(obj, event)

    def _on_kymo_label_pick(self, event):
        # whenever any label is picked—left *or* right
        if getattr(self, "analysis_anchors", None) and not getattr(self, "trajectory_finalized", False):
            return
        artist = event.artist
        if artist in self._kymo_label_to_row:
            self._last_kymo_artist = artist
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

    def reset_contrast(self):
        image = self.movieCanvas.image
        if image is None:
            #print("No movie loaded; cannot reset contrast.")
            return
        p15, p99 = np.percentile(image, (15, 99))
        if self.movieCanvas.sum_mode:
            new_vmin, new_vmax = int(p15 * 1.05), int(p99 * 1.2)
        else:
            new_vmin, new_vmax = int(p15), int(p99 * 1.1)
            
        delta = new_vmax - new_vmin
        new_extended_min = new_vmin - int(0.7 * delta)
        new_extended_max = new_vmax + int(1.4 * delta)
        
        # Update the slider.
        self.contrastControlsWidget.contrastRangeSlider.blockSignals(True)
        self.contrastControlsWidget.contrastRangeSlider.setMinimum(new_extended_min)
        self.contrastControlsWidget.contrastRangeSlider.setMaximum(new_extended_max)
        self.contrastControlsWidget.contrastRangeSlider.setRangeValues(new_vmin, new_vmax)
        self.contrastControlsWidget.contrastRangeSlider.blockSignals(False)
        self.contrastControlsWidget.contrastRangeSlider.update()
        
        try:
            # Use the navigator's movieChannelCombo to obtain the correct current channel.
            current_channel = int(self.movieCanvas.navigator.movieChannelCombo.currentText())
        except Exception:
            current_channel = 1

        if self.movieCanvas.sum_mode:
            self.movieCanvas.navigator.channel_sum_contrast_settings[current_channel] = {
                'vmin': new_vmin,
                'vmax': new_vmax,
                'extended_min': new_extended_min,
                'extended_max': new_extended_max
            }
        else:
            self.movieCanvas.navigator.channel_contrast_settings[current_channel] = {
                'vmin': new_vmin,
                'vmax': new_vmax,
                'extended_min': new_extended_min,
                'extended_max': new_extended_max
            }
                
        # **New Step:** Update MovieCanvas internal contrast attributes:
        self.movieCanvas._default_vmin = new_vmin
        self.movieCanvas._default_vmax = new_vmax
        self.movieCanvas._vmin = new_vmin
        self.movieCanvas._vmax = new_vmax
        
        # Finally, redraw (using display_image or by updating the colormap limits).
        # If you have a method to apply these values, you could also do:
        self.movieCanvas._im.set_clim(new_vmin, new_vmax)
        self.movieCanvas.draw_idle()

    def on_sum_toggled(self):
        try:
            current_channel = int(self.movieChannelCombo.currentText())
        except Exception:
            current_channel = 1

        if self.sumBtn.isChecked():
            # ----- Sum mode ON -----
            if self.refBtn.isChecked():
                self.refBtn.setChecked(False)
            self.movieCanvas.sum_mode = True

            if self.movie is None:
                return

            # Force the sum image to be generated so that movieCanvas.image holds the sum projection.
            # Instead of calling display_sum_frame() (which resets the zoom),
            # save the current view limits, update the image data, then restore the view.
            # current_xlim = self.movieCanvas.ax.get_xlim()
            # current_ylim = self.movieCanvas.ax.get_ylim()

            self.movieCanvas.display_sum_frame()  # This method should be modified if necessary so that it doesn't call update_view()
            # Alternatively, if display_sum_frame() resets the view, you might want to update only the data:
            # sum_img = self.compute_sum_frame()    # a helper method that computes the sum frame without affecting zoom.
            # self.movieCanvas.update_image_data(sum_img)

            # Compute sum-mode contrast settings if they don’t already exist.
            sum_image = self.movieCanvas.image
            if current_channel not in self.channel_sum_contrast_settings:
                if sum_image is not None:
                    p15, p99 = np.percentile(sum_image, (15, 99))
                    new_vmin = int(p15 * 1.05)
                    new_vmax = int(p99 * 1.2)
                    delta = new_vmax - new_vmin
                    settings = {
                        'vmin': new_vmin,
                        'vmax': new_vmax,
                        'extended_min': new_vmin - int(0.7 * delta),
                        'extended_max': new_vmax + int(1.4 * delta)
                    }
                    self.channel_sum_contrast_settings[current_channel] = settings
                else:
                    settings = {'vmin': 0, 'vmax': 255, 'extended_min': 0, 'extended_max': 255}
                    self.channel_sum_contrast_settings[current_channel] = settings
            else:
                settings = self.channel_sum_contrast_settings[current_channel]

            # Update the movie canvas’s sum‑mode contrast defaults:
            self.movieCanvas._default_vmin = settings['vmin']
            self.movieCanvas._default_vmax = settings['vmax']
            self.movieCanvas._vmin = settings['vmin']
            self.movieCanvas._vmax = settings['vmax']

            # Update the contrast slider accordingly.
            self.contrastControlsWidget.contrastRangeSlider.blockSignals(True)
            self.contrastControlsWidget.contrastRangeSlider.setMinimum(settings['extended_min'])
            self.contrastControlsWidget.contrastRangeSlider.setMaximum(settings['extended_max'])
            self.contrastControlsWidget.contrastRangeSlider.setRangeValues(settings['vmin'], settings['vmax'])
            self.contrastControlsWidget.contrastRangeSlider.blockSignals(False)

            # Restore the saved view limits to preserve manual zoom.
            # self.movieCanvas.ax.set_xlim(current_xlim)
            # self.movieCanvas.ax.set_ylim(current_ylim)
            self.movieCanvas.draw_idle()

        else:
            # ----- Sum mode OFF (restore normal mode) -----
            self.sumBtn.setStyleSheet("")
            self.movieCanvas.sum_mode = False

            if self.movie is None:
                return

            # Get the current (normal) frame.
            frame = self.get_movie_frame(self.frameSlider.value())

            # Retrieve stored normal-mode settings (or compute if missing):
            if current_channel in self.channel_contrast_settings:
                settings = self.channel_contrast_settings[current_channel]
            else:
                p15, p99 = np.percentile(frame, (15, 99))
                default_vmin = int(p15)
                default_vmax = int(p99 * 1.1)
                delta = default_vmax - default_vmin
                settings = {
                    'vmin': default_vmin,
                    'vmax': default_vmax,
                    'extended_min': default_vmin - int(0.7 * delta),
                    'extended_max': default_vmax + int(1.4 * delta)
                }
                self.channel_contrast_settings[current_channel] = settings

            # Reset the movie canvas’s internal contrast settings.
            self.movieCanvas._default_vmin = settings['vmin']
            self.movieCanvas._default_vmax = settings['vmax']
            self.movieCanvas._vmin = settings['vmin']
            self.movieCanvas._vmax = settings['vmax']

            # Update the contrast slider.
            self.contrastControlsWidget.contrastRangeSlider.blockSignals(True)
            self.contrastControlsWidget.contrastRangeSlider.setMinimum(settings['extended_min'])
            self.contrastControlsWidget.contrastRangeSlider.setMaximum(settings['extended_max'])
            self.contrastControlsWidget.contrastRangeSlider.setRangeValues(settings['vmin'], settings['vmax'])
            self.contrastControlsWidget.contrastRangeSlider.blockSignals(False)

            # For non‐sum mode, update just the image data without resetting the view.
            # current_xlim = self.movieCanvas.ax.get_xlim()
            # current_ylim = self.movieCanvas.ax.get_ylim()
            self.movieCanvas.update_image_data(frame)
            # self.movieCanvas.ax.set_xlim(current_xlim)
            # self.movieCanvas.ax.set_ylim(current_ylim)
            self.movieCanvas.draw_idle()

        # try:
        #     if self.zoomInsetFrame.isVisible():
        #         frame = self.movieCanvas.image
        #         old_image, center, crop, zoom_factor, fcenter, fsigma, fpeak, bkgr, ivalue = \
        #             self.movieCanvas._last_inset_params

        #         self.movieCanvas.update_inset(
        #             frame, center, crop, zoom_factor,
        #             fitted_center=fcenter,
        #             fitted_sigma=fsigma,
        #             fitted_peak=fpeak,
        #             intensity_value=ivalue,
        #             offset = bkgr
        #         )

        #         if hasattr(self, "histogramCanvas"):
        #             self.histogramCanvas.update_histogram(frame, fcenter, crop, sigma=fsigma, intensity=ivalue, background=bkgr)
        # except Exception as e:
        #     print(f"could not update inset on sum toggle: {e}")
        #     pass

        # self.movieCanvas.draw_idle()

    # def overlay_roi_line(self):
    #     # Remove any existing ROI overlay line.
    #     if hasattr(self.movieCanvas, "roi_line") and self.movieCanvas.roi_line is not None:
    #         try:
    #             self.movieCanvas.roi_line.remove()
    #         except Exception as e:
    #             print("Error removing ROI overlay line:", e)
    #         self.movieCanvas.roi_line = None
    #         self.movieCanvas.draw_idle()
        
    #     # If no ROI is available, do nothing.
    #     if self.roiCombo.count() == 0:
    #         return
    #     roi_key = self.roiCombo.currentText()
    #     if roi_key not in self.rois:
    #         return
    #     roi = self.rois[roi_key]
    #     if "x" not in roi or "y" not in roi:
    #         return

    #     # Convert ROI points to numpy arrays.
    #     roi_x = np.array(roi["x"], dtype=float)
    #     roi_y = np.array(roi["y"], dtype=float)

    #     # Draw a line connecting the ROI points on the movie canvas.
    #     # Use a thicker line (linewidth=3), semi-transparent (alpha=0.8),
    #     # rounded end caps, and a nice yellow color.
    #     line, = self.movieCanvas.ax.plot(roi_x, roi_y,
    #                                     color="#FFCC00",
    #                                     linewidth=3,
    #                                     alpha=0.8,
    #                                     solid_capstyle="round")
    #     self.movieCanvas.roi_line = line
    #     self.movieCanvas.draw_idle()

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
                color="white", fontsize=10,
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

    # def update_roi_overlay_button_style(self, checked):
    #     if checked:
    #         # Change to a different color when the button is toggled on
    #         self.roi_overlay_button.setStyleSheet("background-color: #81C784")
    #     else:
    #         # Revert back to default when toggled off
    #         self.roi_overlay_button.setStyleSheet("")


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

        # 2) for each ROI dict...
        for roi in unique_rois:
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
            self.movieCanvas.finalize_roi()
            
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

    def on_analysis_slider_changed(self, value):
        self.movieCanvas.manual_zoom = True
        if self.looping:
            self.stoploop()
        # Sync intensity canvas
        self.intensityCanvas.current_index = value

        # 1) Full update of movie and kymo contexts
        self.jump_to_analysis_point(value, animate="discrete")

        self._blit_kymo_marker(value)

    def _blit_kymo_marker(self, index):
        """Same blit logic you have in on_analysis_slider_changed."""
        # 1) redraw static trajectories & cache background
        self.kymoCanvas.draw_trajectories_on_kymo()
        # remove any existing marker
        if getattr(self.kymoCanvas, "_marker", None) is not None:
            try:
                self.kymoCanvas._marker.remove()
            except Exception:
                pass
            self.kymoCanvas._marker = None
        self.kymoCanvas.update_view()

        # 2) now overlay just the little magenta/grey X at the current point
        if not self.analysis_frames or not self.analysis_search_centers:
            return
        n = len(self.analysis_frames)
        if index < 0 or index >= n:
            return
        frame = self.analysis_frames[index]
        cx, cy = self.analysis_search_centers[index]

        # pick fitted vs raw
        fc = None
        if hasattr(self, "analysis_fit_params") and index < len(self.analysis_fit_params):
            fc, sigma, peak = self.analysis_fit_params[index]
        use_center = fc if fc is not None else (cx, cy)
        x0, y0 = use_center

        kymo_name = self.kymoCombo.currentText()
        if kymo_name and kymo_name in self.kymographs and self.rois:
            roi = self.rois[self.roiCombo.currentText()]
            if is_point_near_roi(use_center, roi):
                xk = self.compute_kymo_x_from_roi(
                    roi, x0, y0, self.kymographs[kymo_name].shape[1]
                )
                if xk is not None:
                    disp_frame = (self.movie.shape[0] - 1) - frame
                    color = "magenta" if fc is not None else "grey"
                    self.kymoCanvas.overlay_spot_center(xk, disp_frame, size=6, color=color)

    def save_rois(self):
        # Ask user where to save the ZIP file.
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save ROIs as ImageJ ROI Zip", "", "ZIP Files (*.zip)"
        )
        if not filename:
            return

        # Ensure the file has a .zip extension.
        if not filename.lower().endswith('.zip'):
            filename += '.zip'

        try:
            with zipfile.ZipFile(filename, 'w') as zf:
                # Iterate over all ROIs stored in self.rois.
                for roi_name, roi in self.rois.items():
                    # Convert your ROI dictionary into the ImageJ binary format.
                    roi_bytes = convert_roi_to_binary(roi)
                    file_name = f"{roi_name:03}.roi"
                    zf.writestr(file_name, roi_bytes)
            # Optionally, show a message to the user that the file was saved.
            # QMessageBox.information(self, "Saved", f"ROIs successfully saved to {filename}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Could not save ROIs:\n{str(e)}")

    def save_kymographs(self):
        # 1) Nothing to save?
        if not self.kymographs:
            QMessageBox.information(self, "No Kymographs", "Nothing to save.")
            return

        # 2) Show dialog and get options
        all_items = list(self.kymographs.items())
        base_name = os.path.splitext(self.movieNameLabel.text())[0]
        dlg = SaveKymographDialog(base_name, all_items, parent=self)
        if dlg.exec_() != QDialog.Accepted:
            return
        opts      = dlg.getOptions()
        directory = opts["directory"]
        sel_names = opts["selected"]
        ft        = opts["filetype"]
        do_overlay= opts["overlay"]
        use_pref  = opts.get("use_prefix", False)
        mid       = opts.get("middle", "")
        custom    = opts.get("custom", False)
        cname     = opts.get("custom_name", "")

        # 3) Save each kymo
        try:
            for name in sel_names:
                # decide filename
                if custom:
                    fname = cname or name
                else:
                    parts = []
                    if use_pref:
                        parts.append(base_name)
                    if mid:
                        parts.append(mid)
                    parts.append(name)
                    fname = "-".join(parts)
                out_path = os.path.join(directory, f"{fname}.{ft}")

                if do_overlay:
                    # old_vmin, old_vmax = self.movieCanvas._vmin, self.movieCanvas._vmax
                    old_roi         = self.roiCombo.currentText()
                    old_channel_idx = self.movieChannelCombo.currentIndex()
                    old_sel         = self.trajectoryCanvas.table_widget.selectedIndexes()
                    old_hover       = getattr(self, 'hovered_trajectory', None)

                    # ——— Overlay path: draw with Matplotlib and snapshot ———
                    kymo = np.flipud(self.kymographs[name])
                    self.kymoCanvas.display_image(kymo)

                    # restore ROI & channel so your draw_trajectories uses the right mapping
                    info = self.kymo_roi_map.get(name, {})
                    if "roi" in info:
                        self.roiCombo.setCurrentText(info["roi"])
                        self.movieChannelCombo.setCurrentIndex(info["channel"]-1)
                        self.update_movie_channel_combo()
                        if self.sumBtn.isChecked():
                            self.movieCanvas.display_sum_frame()

                    # clear any selection/highlight
                    self.hovered_trajectory = None
                    tw = self.trajectoryCanvas.table_widget
                    tw.clearSelection()
                    tw.setCurrentCell(-1, -1)

                    # make the axes fill the whole figure
                    fig, ax = self.kymoCanvas.fig, self.kymoCanvas.ax
                    fig.subplots_adjust(left=0, right=1, bottom=0, top=1)

                    # draw your trajectories on top
                    self.kymoCanvas.draw_trajectories_on_kymo()

                    # snapshot
                    fig.canvas.draw()
                    fig.savefig(out_path,
                                format=ft,
                                dpi=300,
                                facecolor=fig.get_facecolor(),
                                edgecolor='none',
                                pad_inches=0)
                else:
                    # ——— Plain path: pixel export ———
                    kymo = self.kymographs[name]
                    if ft == "tif":
                        tifffile.imsave(out_path, kymo)
                    else:
                        # replicate your display contrast & cmap & orientation
                        kp15, kp99 = np.percentile(kymo, (15, 99))
                        disp = np.clip((kymo - kp15) / (kp99 - kp15), 0, 1)
                        disp = (disp * 255).astype(np.uint8)
                        cmap = "gray_r" if getattr(self, "inverted_cmap", False) else "gray"
                        disp = np.flipud(disp)
                        plt.imsave(out_path, disp, cmap=cmap, origin='lower')

            # QMessageBox.information(self, "Saved", f"Kymographs written to:\n{directory}")

        finally:
            if do_overlay:
                if old_roi:
                    self.roiCombo.setCurrentText(old_roi)
                self.movieChannelCombo.setCurrentIndex(old_channel_idx)
                self.update_movie_channel_combo()
                self.hovered_trajectory = old_hover
                for idx in old_sel:
                    self.trajectoryCanvas.table_widget.selectRow(idx.row())

    #UNUSED
    def save_kymograph_with_rois(self):
        """
        Save the selected kymo as a TIFF that ImageJ will open
        with your multipoint overlay drawn.
        """

        if not self.kymographs:
            QMessageBox.information(self, "", "Nothing to save.")
            return

        # 1) Ask where to save
        fname, _ = QFileDialog.getSaveFileName(
            self, "Save Kymograph with Overlays", "", "TIFF Files (*.tif *.tiff)"
        )
        if not fname:
            return

        # 2) Grab kymo & movie
        kymo_name = self.kymoCombo.currentText()
        if kymo_name not in self.kymographs:
            QMessageBox.warning(self, "", "Select a kymograph first.")
            return
        kymo = self.kymographs[kymo_name]
        width = kymo.shape[1]
        if self.movie is None:
            QMessageBox.warning(self, "", "Load a movie first.")
            return
        nframes = self.movie.shape[0]

        # 3) Resolve ROI
        roi_key = (
            self.roiCombo.currentText()
            if self.roiCombo.count() > 0
            else kymo_name
        )
        if roi_key not in self.rois:
            QMessageBox.warning(self, "", f"ROI '{roi_key}' not found.")
            return
        roi = self.rois[roi_key]

        # 4) Build overlay points
        pts = []
        for traj in self.trajectoryCanvas.trajectories:
            frames = traj.get("frames", [])
            coords = traj.get('original_coords', [])
            if not frames or not coords:
                continue
            # start
            f0, (x0, y0) = frames[0], coords[0]
            kx0 = self.compute_kymo_x_from_roi(roi, x0, y0, width)
            ky0 = int(round(f0))
            # end
            fn, (xn, yn) = frames[-1], coords[-1]
            kxn = self.compute_kymo_x_from_roi(roi, xn, yn, width)
            kyn = int(round(fn))
            pts.extend([(kx0, ky0), (kxn, kyn)])
        if not pts:
            QMessageBox.information(self, "No Trajectories", "Nothing to save.")
            return

        # 5) Build the ROI blob
        blob = generate_multipoint_roi_bytes(pts)
        print(f"DEBUG: writing ROI blob length {len(blob)} bytes")

        # 6) Build the ImageJ ImageDescription text
        imgdesc = "\n".join([
            "ImageJ=1.53a",
            "images=1",
            "channels=1",
            "slices=1",
            "frames=1",
            "hyperstack=true",
            "overlays=1",
        ]) + "\n"
        desc_bytes = imgdesc.encode('ascii')

        # 7) Write both tags explicitly
        extratags = [
            # tag 270 = ImageDescription
            (270, 's', len(desc_bytes), desc_bytes, True),
            # tag 50838 = ROI
            (50838, 'B', len(blob), blob, True),
        ]
        tifffile.imwrite(
            fname,
            kymo,
            imagej=True,
            metadata={'ROI': blob},  # ← use 'ROI' exactly as ImageJ does
            bigtiff=False
        )

        QMessageBox.information(
            self, "Saved",
            f"Wrote {fname} with {len(pts)//2} trajectories ({len(pts)} points)."
        )

    def show_channel_axis_dialog(self):
        # Check if a movie is loaded and if it is 4-D (with channel options)
        if self.movie is None or self.movie.ndim != 4:
            QMessageBox.information(self, "No extra axes", 
                                    "There are no axes to choose from")
            return

        # Build a list of available axis options (for example, all axes except the time axis)
        # Here we assume axis 0 is time so valid axes are 1, 2, ... movie.ndim-1.
        available_axes = list(range(1, self.movie.ndim))
        dialog = ChannelAxisDialog(available_axes, self)
        if dialog.exec_() == QDialog.Accepted:
            selected_axis = dialog.selected_axis()
            # Set your variable (e.g., self._channel_axis) accordingly.
            self._channel_axis = selected_axis
            # Optionally update your channel combo box (assuming you have a method for that).
            self.update_movie_channel_combo()

    def set_scale(self):
        # Open the Set Scale dialog prefilled with the current pixel_size and frame_interval values (if any)
        dialog = SetScaleDialog(self.pixel_size, self.frame_interval, parent=self)
        if dialog.exec_() == QDialog.Accepted:
            pixel_size, frame_interval = dialog.get_values()
            self.pixel_size = pixel_size  # in nm
            self.frame_interval = frame_interval  # in ms
            self.update_scale_label()
            self.flash_message("Scale set")
            # Optionally, update any UI elements or print to console:
            #print(f"Set pixel size to {self.pixel_size} nm and frame interval to {self.frame_interval} ms")
            
            # Update velocity information in all trajectories.
            tc = self.trajectoryCanvas  # shortcut to the trajectory canvas
            for row in range(tc.table_widget.rowCount()):
                traj = tc.trajectories[row]
                # Calculate velocities (in pixels per frame)
                velocities = calculate_velocities(traj["spot_centers"])
                valid_velocities = [v for v in velocities if v is not None]
                if valid_velocities:
                    average_velocity = np.mean(valid_velocities)
                else:
                    average_velocity = None
                # Convert average_velocity from pixels/frame to micro meters per second and per minute.
                if self.pixel_size is not None and self.frame_interval is not None and average_velocity is not None:
                    # Here, pixel_size is in nm and frame_interval in ms; the conversion to um/s is:
                    # (average_velocity (px/frame) * pixel_size (nm/px)) / (frame_interval (ms)) 
                    # and then convert nm/ms to um/s by dividing by 1000.
                    velocity_nm_per_ms = (average_velocity * self.pixel_size) / self.frame_interval
                    avg_vel_um_s_txt = f"{velocity_nm_per_ms:.2f}"
                    avg_vel_um_min_txt = f"{velocity_nm_per_ms*60.0:.2f}"
                else:
                    avg_vel_um_s_txt = ""
                    avg_vel_um_min_txt = ""

                dx = traj["end"][1] - traj["start"][1]
                dy = traj["end"][2] - traj["start"][2]
                distance_px = np.hypot(dx, dy)
                time_fr = traj["end"][0] - traj["start"][0]
                distance_um_txt = ""
                time_s_txt = ""
                overall_vel_um_s_txt = ""
                if self.pixel_size is not None and self.frame_interval is not None and time_fr > 0:
                    distance_um = distance_px * self.pixel_size / 1000
                    time_s = time_fr * self.frame_interval / 1000
                    overall_vel_um_s = distance_um/time_s
                    distance_um_txt = f"{distance_um:.2f}"
                    time_s_txt = f"{time_s:.2f}"
                    overall_vel_um_s_txt = f"{overall_vel_um_s:.2f}"

                tc.writeToTable(row, "distance", distance_um_txt)
                tc.writeToTable(row, "time", time_s_txt)                
                tc.writeToTable(row, "netspeed", overall_vel_um_s_txt)
                tc.writeToTable(row, "avgspeed", avg_vel_um_s_txt)
            
            # Update the displayed velocity plot (for the currently selected trajectory)
            selected_rows = tc.table_widget.selectionModel().selectedRows()
            if selected_rows:
                current_row = selected_rows[0].row()
            elif tc.table_widget.rowCount() > 0:
                current_row = 0
                tc.table_widget.selectRow(current_row)
            else:
                current_row = None
            if current_row is not None:
                current_traj = tc.trajectories[current_row]
                self.velocityCanvas.plot_velocity_histogram(current_traj["velocities"])

        self.trajectoryCanvas.hide_empty_columns()

    def correct_drift(self):
        """
        Corrects the drift in the currently loaded movie using spot tracking.

        For multi–channel movies the analysis is performed on the currently selected
        channel but the correction is applied to the full frame so that the original
        movie shape (including channel axis) is preserved. New areas are padded with black.

        The tracking uses the spot center from one frame as the search center for the next.
        If no spot is found, the same displacement as in the previous frame is applied.
        At the end, any gaps are filled in via linear interpolation.
        """
        if self.movie is None:
            QMessageBox.warning(self, "", "Please load a movie first.")
            return

        if not hasattr(self, "drift_reference") or self.drift_reference is None:
            QMessageBox.warning(self, "",
                                "No valid spot center found. Please click a spot in the movie.")
            return

        ref_spot = self.drift_reference  # (x, y)
        n_frames = self.movie.shape[0]
        multi_channel = (self.movie.ndim == 4)

        # Initialize a list to hold the tracked spot centers.
        spot_centers = [None] * n_frames
        spot_centers[self.spot_frame] = ref_spot

        crop_size = int(2 * self.searchWindowSpin.value())

        def get_analysis_frame(full_frame):
            if not multi_channel:
                return full_frame
            current_chan = int(self.movieChannelCombo.currentText()) - 1
            if self._channel_axis == 1:  # channels-first: (channels, H, W)
                return full_frame[current_chan]
            else:  # channels-last: (H, W, channels)
                return full_frame[..., current_chan]

        # --- Create a single progress dialog for both tracking and shifting ---
        total_tracking = (n_frames - self.spot_frame - 1) + self.spot_frame
        total_shifts = n_frames
        total_steps = total_tracking + total_shifts

        progress = QProgressDialog("Tracking spot and applying shift...", "Cancel", 0, total_steps, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        current_progress = 0

        # --- Forward Tracking ---
        current_spot = ref_spot
        last_disp = (0, 0)
        for i in range(self.spot_frame + 1, n_frames):
            if progress.wasCanceled():
                return
            full_frame = self.movie[i] if multi_channel else self.get_movie_frame(i)
            if full_frame is None:
                spot_centers[i] = current_spot
                current_progress += 1
                progress.setValue(current_progress)
                continue

            analysis_frame = get_analysis_frame(full_frame)
            analysis_frame = np.atleast_2d(analysis_frame)
            if analysis_frame.ndim != 2:
                spot_centers[i] = current_spot
                current_progress += 1
                progress.setValue(current_progress)
                continue

            try:
                fitted_center, sigma, intensity, peak, bkgr = perform_gaussian_fit(analysis_frame, current_spot, crop_size, pixelsize = self.pixel_size)
            except Exception as e:
                print(f"Forward Gaussian fit error at frame {i}: {e}")
                fitted_center = None

            if fitted_center is not None:
                new_spot = fitted_center
                last_disp = (new_spot[0] - current_spot[0], new_spot[1] - current_spot[1])
            else:
                new_spot = (current_spot[0] + last_disp[0], current_spot[1] + last_disp[1])
            spot_centers[i] = new_spot
            current_spot = new_spot
            current_progress += 1
            progress.setValue(current_progress)

        # --- Backward Tracking ---
        current_spot = ref_spot
        last_disp = (0, 0)
        for i in range(self.spot_frame - 1, -1, -1):
            if progress.wasCanceled():
                return
            full_frame = self.movie[i] if multi_channel else self.get_movie_frame(i)
            if full_frame is None:
                spot_centers[i] = current_spot
                current_progress += 1
                progress.setValue(current_progress)
                continue

            analysis_frame = get_analysis_frame(full_frame)
            analysis_frame = np.atleast_2d(analysis_frame)
            if analysis_frame.ndim != 2:
                spot_centers[i] = current_spot
                current_progress += 1
                progress.setValue(current_progress)
                continue

            try:
                fitted_center, sigma, intensity, peak, bkgr = perform_gaussian_fit(analysis_frame, current_spot, crop_size, pixelsize = self.pixel_size)
            except Exception as e:
                print(f"Backward Gaussian fit error at frame {i}: {e}")
                fitted_center = None

            if fitted_center is not None:
                new_spot = fitted_center
                last_disp = (new_spot[0] - current_spot[0], new_spot[1] - current_spot[1])
            else:
                new_spot = (current_spot[0] + last_disp[0], current_spot[1] + last_disp[1])
            spot_centers[i] = new_spot
            current_spot = new_spot
            current_progress += 1
            progress.setValue(current_progress)
        # End of tracking phase.

        # --- Fill in Gaps with Linear Interpolation ---
        for i in range(n_frames):
            if spot_centers[i] is None:
                prev = i - 1
                while prev >= 0 and spot_centers[prev] is None:
                    prev -= 1
                nxt = i + 1
                while nxt < n_frames and spot_centers[nxt] is None:
                    nxt += 1
                if prev >= 0 and nxt < n_frames:
                    t = (i - prev) / (nxt - prev)
                    x_interp = spot_centers[prev][0] + t * (spot_centers[nxt][0] - spot_centers[prev][0])
                    y_interp = spot_centers[prev][1] + t * (spot_centers[nxt][1] - spot_centers[prev][1])
                    spot_centers[i] = (x_interp, y_interp)
                elif prev >= 0:
                    spot_centers[i] = spot_centers[prev]
                elif nxt < n_frames:
                    spot_centers[i] = spot_centers[nxt]
                else:
                    spot_centers[i] = ref_spot

        displacements = [(sc[0] - ref_spot[0], sc[1] - ref_spot[1]) for sc in spot_centers]

        # --- Apply Correction (Shifting) ---
        corrected_frames = [None] * n_frames

        for i in range(n_frames):
            if progress.wasCanceled():
                return
            full_frame = self.movie[i] if multi_channel else self.get_movie_frame(i)
            if full_frame is None:
                corrected_frames[i] = None
                current_progress += 1
                progress.setValue(current_progress)
                continue

            if full_frame.ndim == 2:
                shift_vector = [-displacements[i][1], -displacements[i][0]]
            elif full_frame.ndim == 3:
                if multi_channel:
                    if self._channel_axis == 1:  # channels-first: (channels, H, W)
                        shift_vector = [0, -displacements[i][1], -displacements[i][0]]
                    else:  # channels-last: (H, W, channels)
                        shift_vector = [-displacements[i][1], -displacements[i][0], 0]
                else:
                    shift_vector = [-displacements[i][1], -displacements[i][0]]
            else:
                QMessageBox.critical(self, "Drift Correction Error",
                                    "Unexpected movie dimensions; cannot apply drift correction.")
                progress.close()
                return

            try:
                # Use order=0 (nearest neighbor) so that pixel values are unchanged.
                corrected_frames[i] = shift(full_frame, shift=shift_vector, order=0, mode='constant', cval=0)
            except Exception as e:
                QMessageBox.critical(self, "Drift Correction Error",
                                    f"Error applying shift on frame {i}:\n{e}")
                corrected_frames[i] = full_frame
            current_progress += 1
            progress.setValue(current_progress)
        progress.close()

        # --- Display the Corrected Movie in a Popup Dialog ---
        dialog = QDialog(self)
        dialog.setWindowTitle("Drift-Corrected Movie")
        dialog_layout = QVBoxLayout(dialog)

        # Create a new MovieCanvas for the dialog:
        corrected_canvas = MovieCanvas(dialog, navigator=self)

        # First, determine the contrast settings for the current channel and mode:
        if self.movie.ndim == 4:
            try:
                current_channel = int(self.movieChannelCombo.currentText())
            except Exception:
                current_channel = 1
            settings = self.channel_contrast_settings.get(current_channel)
        else:
            settings = self.channel_contrast_settings.get(1)
        
        # If settings exist, assign them to corrected_canvas:
        if settings is not None:
            corrected_canvas._default_vmin = settings['vmin']
            corrected_canvas._default_vmax = settings['vmax']
            corrected_canvas._vmin = settings['vmin']
            corrected_canvas._vmax = settings['vmax']

        # Add the corrected_canvas to the dialog.
        dialog_layout.addWidget(corrected_canvas)

        # Create a slider for frame navigation.
        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(len(corrected_frames) - 1)
        dialog_layout.addWidget(slider)

        # Create a channel dropdown if the movie has multiple channels.
        channel_dropdown = None
        if self.movie.ndim == 4:
            channel_dropdown = QComboBox()
            n_channels = self.movie.shape[self._channel_axis]
            for ch in range(n_channels):
                channel_dropdown.addItem(f"Channel {ch+1}")
            # Set the initial channel to match the main GUI.
            channel_dropdown.setCurrentIndex(int(self.movieChannelCombo.currentText()) - 1)
            dialog_layout.addWidget(channel_dropdown)

        # Define a function to update the displayed frame:
        def update_frame(val):
            frame = corrected_frames[val]
            if frame is None:
                return
            if self.movie.ndim == 4 and channel_dropdown is not None:
                ch_index = channel_dropdown.currentIndex()
                if self._channel_axis == 1:
                    display_frame = frame[ch_index]
                else:
                    display_frame = frame[..., ch_index]
            else:
                display_frame = frame

            corrected_canvas.update_image_data(display_frame)

        slider.valueChanged.connect(update_frame)

        # 2) Now define the channel‐change callback, using the same 1‑based keys
        def on_channel_dropdown(ch0):
            # ch0 is zero-based, but your settings dict uses 1-based keys
            chan_key = ch0 + 1

            # ensure we have defaults for this channel
            # get a slice of the first corrected frame
            first_frame = corrected_frames[0]
            if self.movie.ndim == 4:
                if self._channel_axis == 1:
                    sample = first_frame[ch0]
                else:
                    sample = first_frame[..., ch0]
            else:
                sample = first_frame

            if chan_key not in self.channel_contrast_settings and sample is not None:
                p15, p99 = np.percentile(sample, (15, 99))
                vmin = int(p15)
                vmax = int(p99 * 1.1)
                d = vmax - vmin
                self.channel_contrast_settings[chan_key] = {
                    'vmin': vmin,
                    'vmax': vmax,
                    'extended_min': vmin - int(0.7*d),
                    'extended_max': vmax + int(1.4*d)
                }
            settings = self.channel_contrast_settings[chan_key]

            corrected_canvas._default_vmin = settings["vmin"]
            corrected_canvas._default_vmax = settings["vmax"]
            corrected_canvas._vmin = settings["vmin"]
            corrected_canvas._vmax = settings["vmax"]

            # finally, repaint current slider frame with new contrast
            update_frame(slider.value())

        if channel_dropdown is not None:
            channel_dropdown.currentIndexChanged.connect(on_channel_dropdown)

        # 3) Kick it off once at startup
        update_frame(0)

        # Add dialog buttons.
        btn_layout = QHBoxLayout()
        btn_save = QPushButton("Save Movie")
        btn_save_load = QPushButton("Save and Load Movie")
        btn_cancel = QPushButton("Cancel")
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_save_load)
        btn_layout.addWidget(btn_cancel)
        dialog_layout.addLayout(btn_layout)

        saved_file = {"path": None}

        def save_movie():
            fname, _ = QFileDialog.getSaveFileName(dialog, "Save Drift-Corrected Movie", "",
                                                    "TIFF Files (*.tif *.tiff)")
            if fname:
                try:
                    # Use metadata from the originally loaded movie if available.
                    axes = self.movie_metadata.get('axes', None) if hasattr(self, 'movie_metadata') else None
                    if axes is None:
                        # If not available, choose a default. Change as appropriate.
                        axes = 'TYX'
                    tifffile.imwrite(fname, np.array(corrected_frames),
                                    imagej=True,
                                    metadata=self.movie_metadata)
                    saved_file["path"] = fname
                except Exception as e:
                    QMessageBox.critical(dialog, "Save Error", f"Error saving movie:\n{str(e)}")
                    return True
            return False

        def save_and_load_movie():
            if save_movie():
                return
            if saved_file["path"]:
                try:
                    self.save_and_load_routine = True  # Flag for custom load handling.
                    self.handle_movie_load(saved_file["path"], pixelsize = self.pixel_size, frameinterval = self.frame_interval)
                    QMessageBox.information(dialog, "Loaded",
                                            "The corrected movie has been loaded into the main window.")
                    self.zoomInsetFrame.setVisible(False)
                except Exception as e:
                    QMessageBox.critical(dialog, "Load Error", f"Error loading movie:\n{str(e)}")
                dialog.accept()

        def cancel():
            dialog.accept()

        btn_save.clicked.connect(lambda: [save_movie(), dialog.accept()])
        btn_save_load.clicked.connect(save_and_load_movie)
        btn_cancel.clicked.connect(cancel)

        dialog.exec_()

    def on_kymo_leave(self, event):
        """Callback for when the mouse leaves the kymograph axes.
        This removes the blue X marker from the movie canvas."""
        if self.movieCanvas is not None:
            self.movieCanvas.draw_idle()

    def on_tracking_mode_changed(self, mode):
        # mode is the string from the dropdown ("Independent" or "Tracked")
        self.tracking_mode = mode
        print(f"Tracking mode set to: {self.tracking_mode}")

    def update_overlay_button_style(self, checked):
        if checked:
            self.traj_overlay_button.setStyleSheet("background-color: #497ce2;")
        else:
            self.traj_overlay_button.setStyleSheet("")

    def _show_kymo_context_menu(self, global_pos: QPoint):
        cols = self.trajectoryCanvas.custom_columns
        if not cols:
            self._last_kymo_artist = None
            return

        # dedupe, preserving original order
        unique_cols = []
        for c in cols:
            if c not in unique_cols:
                unique_cols.append(c)

        artist = self._last_kymo_artist
        if artist is None:
            return

        row = self._kymo_label_to_row.get(artist)
        if row is None:
            return

        menu = QMenu(self.kymoCanvas)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground)

        tbl = self.trajectoryCanvas.table_widget
        for col in unique_cols:
            col_type = self.trajectoryCanvas._column_types.get(col, "binary")

            #--- binary columns: mark/unmark as before ---
            if col_type == "binary":
                table_col_index = self.trajectoryCanvas._col_index[col]
                item = tbl.item(row, table_col_index)
                marked = bool(item and item.text().strip().lower() == "yes")

                if marked:
                    action_text = f'Unmark as {col}'
                    callback    = lambda _chk=False, r=row, c=col: \
                                  self.trajectoryCanvas._unmark_custom(r, c)
                else:
                    action_text = f'Mark as {col}'
                    callback    = lambda _chk=False, r=row, c=col: \
                                  self.trajectoryCanvas._mark_custom(r, c)

            #--- value columns: pop up a dialog ---
            else:  # col_type == "value"
                table_col_index = self.trajectoryCanvas._col_index[col]
                item = tbl.item(row, table_col_index)
                existing = item.text().strip() if item else ""
                if existing:
                    action_text = f'Edit {col} value'
                else:
                    action_text = f'Add {col} value'

                # call your helper which prompts & writes into the table
                callback = lambda _chk=False, r=row, c=col: \
                            self._prompt_and_add_kymo_value(c, r)

            menu.addAction(action_text, callback)

        menu.exec_(global_pos)
        self._last_kymo_artist = None

        if col_type == "binary":
            self.kymoCanvas.draw_trajectories_on_kymo()