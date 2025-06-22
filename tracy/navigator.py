import os, sys
import numpy as np
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QFileDialog, QLabel, QMessageBox, QComboBox, QSpinBox, QShortcut,
    QListView, QSlider, QSizePolicy, QAction, QDialog,
    QProgressDialog, QApplication, QFrame,
    QLineEdit, QFormLayout, QGraphicsDropShadowEffect,
    QInputDialog, QMenu, QLayout
)

from PyQt5 import QtCore, QtWidgets, QtGui
from PyQt5.QtCore import (
    Qt, QTimer, QSize, QRectF, QPropertyAnimation, QEvent,
    QEasingCurve, QPropertyAnimation, QPoint, pyqtSlot, pyqtSignal,
    QThreadPool, QRect)
from PyQt5.QtGui import (
    QKeySequence, QIcon, QColor, QCursor, QMouseEvent
    )

from concurrent.futures import ThreadPoolExecutor
import matplotlib.pyplot as plt
import matplotlib.patheffects as pe
import matplotlib.cm as cm
import matplotlib.colors as mcolors
import tifffile
from scipy.ndimage import shift, gaussian_filter
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
    ClickableLabel, RadiusDialog, BubbleTipFilter,
    CenteredBubbleFilter, AnimatedIconButton,
    StepSettingsDialog, KymoContrastControlsWidget
)
from tracy import __version__
from .gaussian_tools import perform_gaussian_fit, filterX, find_minima, find_maxima
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
        self.setWindowTitle(f"Tracy {__version__}")
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
        self._is_canceled=False

        self.tracking_mode = "Independent"

        # Store data from the last analysis run.
        self.analysis_frames = []
        self.analysis_original_coords = []
        self.analysis_search_centers = []
        self.analysis_colocalized = []

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
        self.cancelShortcut.activated.connect(self.escape_left_click_sequence)

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

        # bind period → next kymograph
        self.nextKymoSc = QShortcut(QKeySequence(Qt.Key_Period), self)
        self.nextKymoSc.activated.connect(self._select_next_kymo)
        
        # bind comma → previous kymograph
        self.prevKymoSc = QShortcut(QKeySequence(Qt.Key_Comma), self)
        self.prevKymoSc.activated.connect(self._select_prev_kymo)

        self.roi_overlay_active = False

        self.temp_analysis_line = None 
        self._bg = None
        self.clear_flag = False

        self.applylogfilter = False

        self.save_and_load_routine = False

        # For adding a trajectory (Ctrl+Enter)
        self.addTrajectoryShortcut = QShortcut(QKeySequence(Qt.Key_Return), self)
        self.addTrajectoryShortcut.setContext(Qt.ApplicationShortcut)
        self.addTrajectoryShortcut.activated.connect(self.add_or_recalculate)

        self.analysis_avg = None
        self.analysis_median = None
        self.analysis_velocities = []
        self.analysis_average_velocity = None

        self._radiusPopup = None
        self._radiusSpinLive = None

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

        # self.last_kymo_by_channel = {}
        self._roi_zoom_states = {}
        self._last_roi = None

        self._last_kymo_artist = None
        self._skip_next_right = False

        self.avoid_previous_spot = False
        self.same_spot_threshold = 6
        self.past_centers = []

        self.check_colocalization = False
        self.colocalization_threshold = 4

        self.connect_all_spots = False

        self.color_by_column = None

        self.flashchannel = True

        self.show_steps=False
        self.min_step=100
        self.W=15
        self.passes=10

    def create_ui(self):
        # Create the central widget and overall layout.
        central = QWidget()
        central.setObjectName("centralContainer")
        self.setCentralWidget(central)
        containerLayout = QVBoxLayout(central)

        # videoiconpath = self.resource_path('icons/video-camera.svg')
        crossiconpath = self.resource_path('icons/cross-small.svg')
        crossdoticonpath = self.resource_path('icons/cross-dot.svg')
        resetcontrastpath = self.resource_path('icons/contrast.svg')
        maxiconpath = self.resource_path('icons/max.svg')
        referenceiconpath = self.resource_path('icons/reference.svg')
        trajoverlayiconpath = self.resource_path('icons/overlay_traj.svg')
        roioverlayiconpath = self.resource_path('icons/overlay.svg')

        # --- Top Controls Section ---
        topWidget = QWidget()
        topLayout = QHBoxLayout(topWidget)
        topLayout.setSpacing(5)
        topLayout.setContentsMargins(20, 6, 0, 0)
        topLayout.setAlignment(Qt.AlignLeft)

        self.movieNameLabel = ClickableLabel("Load movie")
        self.movieNameLabel.setObjectName("movieNameLabel")
        self.movieNameLabel.setProperty("pressed", False)
        self.movieNameLabel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.movieNameLabel.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.movieNameLabel.clicked.connect(self.handle_movie_load)
        self.movieNameLabel.setStyleSheet("""
        #movieNameLabel {
            background: transparent;
            color: black;
            font-size: 14px;
            border: 1px solid #DCE6FF;
            border-radius: 8px;
            padding: 6px 12px
        }
        #movieNameLabel:hover {
            background: rgb(215, 225, 252);
        }
        #movieNameLabel[pressed="true"] {
            background: #DCE6FF;
        }
        """)
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
        self.pixelValueLabel.setStyleSheet("color: #444444; background: transparent; padding-right: 25px;")
        topLayout.addWidget(self.pixelValueLabel)

        self.scaleLabel = ClickableLabel("")
        self.scaleLabel.setObjectName("scaleLabel")
        self.scaleLabel.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.scaleLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.scaleLabel.setStyleSheet("""
        #scaleLabel {
            color: black;
            background: transparent;
            font-size: 14px;
            border-radius: 8px;
            padding: 6px 6px 6px 6px
        }
        #scaleLabel:hover {
            background: rgb(215, 225, 252);
        }
        #scaleLabel[pressed="true"] {
            background: #DCE6FF;
        }
        """)
        self.scaleLabel.clicked.connect(self.open_set_scale_dialog)
        topLayout.addWidget(self.scaleLabel)
        containerLayout.addWidget(topWidget)

        topLayout.addSpacing(25)
        
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
        self.kymoCombo.currentIndexChanged.connect(self.kymo_changed)
        kymoControlLayout.addWidget(self.kymoCombo)

        # Add the kymograph delete and clear buttons
        self.kymoDeleteBtn = QPushButton("")
        self.kymoDeleteBtn.setIcon(QIcon(crossiconpath))
        self.kymoDeleteBtn.setIconSize(QSize(14, 14))
        # self.kymoDeleteBtn.setToolTip("Delete selected ROI")
        deletekymo_filter = BubbleTipFilter("Delete this kymograph", self)
        self.kymoDeleteBtn.installEventFilter(deletekymo_filter)
        self.kymoDeleteBtn._bubble_filter = deletekymo_filter
        self.kymoDeleteBtn.setObjectName("Passive")
        self.kymoDeleteBtn.setFixedWidth(32)
        self.kymoDeleteBtn.clicked.connect(self.delete_current_kymograph)
        kymoControlLayout.addWidget(self.kymoDeleteBtn)

        self.clearKymoBtn = QPushButton("")
        self.clearKymoBtn.setIcon(QIcon(crossdoticonpath))
        self.clearKymoBtn.setIconSize(QSize(14, 14))
        # self.clearKymoBtn.setToolTip("Clear kymographs")
        clearkymo_filter = BubbleTipFilter("Delete all kymographs", self)
        self.clearKymoBtn.installEventFilter(clearkymo_filter)
        self.clearKymoBtn._bubble_filter = clearkymo_filter
        self.clearKymoBtn.setObjectName("Passive")
        self.clearKymoBtn.setFixedWidth(32)
        self.clearKymoBtn.clicked.connect(self.clear_kymographs)
        kymoControlLayout.addWidget(self.clearKymoBtn)


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
        self.roiDeleteBtn = AnimatedIconButton("")
        self.roiDeleteBtn.setIcon(QIcon(crossiconpath))
        self.roiDeleteBtn.setIconSize(QSize(14, 14))
        # self.roiDeleteBtn.setToolTip("Delete selected ROI")
        roidelete_filter = BubbleTipFilter("Delete selected line ROI", self)
        self.roiDeleteBtn.installEventFilter(roidelete_filter)
        self.roiDeleteBtn._bubble_filter = roidelete_filter
        self.roiDeleteBtn.setObjectName("Passive")
        self.roiDeleteBtn.setFixedWidth(32)
        self.roiDeleteBtn.clicked.connect(self.delete_current_roi)
        roiControlLayout.addWidget(self.roiDeleteBtn)

        self.clearROIBtn = AnimatedIconButton("")
        self.clearROIBtn.setIcon(QIcon(crossdoticonpath))
        self.clearROIBtn.setIconSize(QSize(14, 14))
        # self.clearROIBtn.setToolTip("Clear ROIs")
        clearroi_filter = BubbleTipFilter("Delete all line ROIs", self)
        self.clearROIBtn.installEventFilter(clearroi_filter)
        self.clearROIBtn._bubble_filter = clearroi_filter
        self.clearROIBtn.setObjectName("Passive")
        self.clearROIBtn.setFixedWidth(32)
        self.clearROIBtn.clicked.connect(self.clear_rois)
        roiControlLayout.addWidget(self.clearROIBtn)

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
        
        # 1) make a legend container as a child of the rounded frame
        self.kymoLegendWidget = QWidget(parent=roundedContainer)
        self.kymoLegendWidget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        self.kymoLegendWidget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.kymoLegendWidget.hide()  # start hidden

        # 2) style / position it absolutely
        self.kymoLegendWidget.setStyleSheet("background: white;  border-radius: 8px; ")
        self.kymoLegendWidget.move(10, 10)  # 10px from top-left of roundedContainer
        kymolegendshadow = QGraphicsDropShadowEffect(self.kymoLegendWidget)
        kymolegendshadow.setBlurRadius(10)
        kymolegendshadow.setColor(QColor(0, 0, 0, 120))
        kymolegendshadow.setOffset(0, 0)
        self.kymoLegendWidget.setGraphicsEffect(kymolegendshadow)
        self.kymoLegendLayout = QHBoxLayout(self.kymoLegendWidget)
        self.kymoLegendLayout.setSizeConstraint(QLayout.SetFixedSize)
        self.kymoLegendLayout.setContentsMargins(5,5,5,5)
        self.kymoLegendLayout.setSpacing(5)

        # leftLayout.addSpacing(10)
        # kymocontrastwidget = QWidget()
        # kymocontrastLayout = QHBoxLayout(kymocontrastwidget)
        # kymocontrastLayout.setContentsMargins(0, 0, 0, 0)
        # kymocontrastLayout.setSpacing(10)
        # self.kymocontrastControlsWidget = KymoContrastControlsWidget(self.kymoCanvas)
        # kymocontrastsliderfilter = BubbleTipFilter("Adjust contrast range", self, placement="left")
        # self.kymocontrastControlsWidget.installEventFilter(kymocontrastsliderfilter)
        # self.kymocontrastControlsWidget._bubble_filter = kymocontrastsliderfilter
        # self.kymocontrastControlsWidget.setMinimumWidth(50)
        # self.kymocontrastControlsWidget.setMaximumWidth(200)
        # kymocontrastLayout.addWidget(self.kymocontrastControlsWidget)
        # self.kymoresetBtn = AnimatedIconButton("")
        # self.kymoresetBtn.setIcon(QIcon(resetcontrastpath))
        # self.kymoresetBtn.setIconSize(QSize(16, 16))
        # kymocontrastresetfilter = BubbleTipFilter("Reset contrast", self, placement="right")
        # self.kymoresetBtn.installEventFilter(kymocontrastresetfilter)
        # self.kymoresetBtn._bubble_filter = kymocontrastresetfilter
        # self.kymoresetBtn.clicked.connect(self.reset_kymo_contrast)
        # self.kymoresetBtn.setObjectName("Passive")
        # self.kymoresetBtn.setFixedWidth(40)
        # kymocontrastLayout.addWidget(self.kymoresetBtn)
        # leftLayout.addWidget(kymocontrastwidget, alignment=Qt.AlignCenter)

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
        self.movieDisplayContainer.setMinimumSize(100, 100)
        self.movieDisplayContainer.setFrameStyle(QFrame.Box)
        self.movieDisplayContainer.setLineWidth(2)
        self.movieDisplayContainer.setStyleSheet("QFrame { border: 6px solid transparent; }")
        movieDisplayLayout = QVBoxLayout(self.movieDisplayContainer)
        movieDisplayLayout.setContentsMargins(6, 5, 6, 5)

        self.movieChannelCombo = QComboBox()
        self.movieChannelCombo.setView(QListView())
        self.movieChannelCombo.setFixedWidth(40)
        self.movieChannelCombo.setFixedHeight(24)
        self.movieChannelCombo.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.movieChannelCombo.setStyleSheet("QComboBox { min-width: 40px; background: transparent; margin: 0px; border: 1px solid #ccc;  color: #444444 }")
        self.movieChannelCombo.setEnabled(False)
        self.movieChannelCombo.currentIndexChanged.connect(self.on_channel_changed)


        # Initially hide the container until a movie is loaded
        self.channelControlContainer = RoundedFrame(self.movieDisplayContainer, radius=10, bg_color = self.settings['widget-bg'])
        self.channelControlContainer.setParent(self.movieDisplayContainer)
        self.channelControlContainer.setStyleSheet("background: transparent;")
        self.channelControlContainer.move(10, 10)   # tweak x/y offsets as you
        self.channelControlContainer.raise_()
        self.channelControlContainer.setVisible(False)

        self.movieCanvas = MovieCanvas(self, navigator=self)
        self.movieCanvas.setStyleSheet(f"background-color: {self.settings['widget-bg']}")
        self.movieCanvas.mpl_connect("scroll_event", self.movieCanvas.on_scroll)
        # self.movieCanvas.mpl_connect("scroll_event", self.on_movie_scroll)
        self.movieCanvas.mpl_connect("button_press_event", self.on_movie_click)
        self.movieCanvas.mpl_connect("button_release_event", self.on_movie_release)
        self.movieCanvas.mpl_connect("motion_notify_event", self.on_movie_motion)
        movieDisplayLayout.addWidget(self.movieCanvas, stretch=1)
        self.movieDisplayContainer.setLayout(movieDisplayLayout)
        movieLayout.addWidget(self.movieDisplayContainer, stretch=1)

        # in create_ui, replace the overlay QLabel with:
        self._ch_overlay = ClickableLabel("", parent=self.movieDisplayContainer)
        channellabelfilter = BubbleTipFilter("Change channel (shortcut: 1, 2...)", self)
        self._ch_overlay.installEventFilter(channellabelfilter)
        self._ch_overlay._bubble_filter = channellabelfilter
        self._ch_overlay.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._ch_overlay.setStyleSheet("""
            background: white;
            border-radius: 12px;
            color: black;
            font-size: 26px;
            font-weight: bold;
        """)
        chshadow = QGraphicsDropShadowEffect(self._ch_overlay)
        chshadow.setBlurRadius(10)
        chshadow.setColor(QColor(0, 0, 0, 120))
        chshadow.setOffset(0, 0)
        self._ch_overlay.setGraphicsEffect(chshadow)
        self._ch_overlay.setMinimumSize(0, 0)
        self._ch_overlay.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self._ch_overlay.hide()
        self._ch_overlay.clicked.connect(self._on_overlay_clicked)

        # 1) make a legend container as a child of the rounded frame
        self.movieLegendWidget = QWidget(parent=self.movieDisplayContainer)
        self.movieLegendWidget.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Minimum)
        self.movieLegendWidget.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.movieLegendWidget.hide()  # start hidden

        # 2) style / position it absolutely
        self.movieLegendWidget.setStyleSheet("background: white;  border-radius: 8px; ")
        self.movieLegendWidget.move(0, 0)  # 10px from top-left of roundedContainer
        movielegendshadow = QGraphicsDropShadowEffect(self.movieLegendWidget)
        movielegendshadow.setBlurRadius(10)
        movielegendshadow.setColor(QColor(0, 0, 0, 120))
        movielegendshadow.setOffset(0, 0)
        self.movieLegendWidget.setGraphicsEffect(movielegendshadow)
        # 3) give it a simple horizontal layout
        self.movieLegendLayout = QHBoxLayout(self.movieLegendWidget)
        self.movieLegendLayout.setSizeConstraint(QLayout.SetFixedSize)
        self.movieLegendLayout.setContentsMargins(5,0,5,0)
        self.movieLegendLayout.setSpacing(8)

        self.movieLegendWidget.stackUnder(self._ch_overlay)
        self._ch_overlay.installEventFilter(self)
        self.movieDisplayContainer.installEventFilter(self)

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
        contrastsliderfilter = BubbleTipFilter("Adjust contrast range", self, placement="left")
        self.contrastControlsWidget.installEventFilter(contrastsliderfilter)
        self.contrastControlsWidget._bubble_filter = contrastsliderfilter
        self.contrastControlsWidget.setMinimumWidth(100)
        contrastLayout.addWidget(self.contrastControlsWidget)
        self.resetBtn = AnimatedIconButton("")
        self.resetBtn.setIcon(QIcon(resetcontrastpath))
        self.resetBtn.setIconSize(QSize(16, 16))
        # self.resetBtn.setToolTip("Reset contrast")
        contrastresetfilter = BubbleTipFilter("Reset contrast", self, placement="left")
        self.resetBtn.installEventFilter(contrastresetfilter)
        self.resetBtn._bubble_filter = contrastresetfilter
        self.resetBtn.clicked.connect(self.reset_contrast)
        self.resetBtn.setObjectName("Passive")
        self.resetBtn.setFixedWidth(40)
        contrastLayout.addWidget(self.resetBtn)
        self.sumBtn = AnimatedIconButton("", self)
        self.sumBtn.setIcon(QIcon(maxiconpath))
        self.sumBtn.setIconSize(QSize(16, 16))
        self.sumBtn.setCheckable(True)
        self.sumBtn.setFixedWidth(40)
        # self.sumBtn.setToolTip("Show the maximum projection (shortcut: m)")
        sumfilter = BubbleTipFilter("Maximum projection (shortcut: m)", self)
        self.sumBtn.installEventFilter(sumfilter)
        self.sumBtn._bubble_filter = sumfilter
        self.sumBtn.toggled.connect(self.on_sum_toggled)
        self.sumBtn.setObjectName("Toggle")
        contrastLayout.addWidget(self.sumBtn)

        self.refBtn = AnimatedIconButton("")
        self.refBtn.setIcon(QIcon(referenceiconpath))
        self.refBtn.setIconSize(QSize(16, 16))
        # self.refBtn.setToolTip("Show the reference image")
        reffilter = BubbleTipFilter("Reference image", self)
        self.refBtn.installEventFilter(reffilter)
        self.refBtn._bubble_filter = reffilter
        self.refBtn.setCheckable(True)
        self.refBtn.setFixedWidth(40)
        self.refBtn.setVisible(False)
        self.refBtn.toggled.connect(self.on_ref_toggled)
        self.refBtn.setObjectName("Toggle")
        contrastLayout.addWidget(self.refBtn)

        self.traj_overlay_button = AnimatedIconButton("")
        # self.traj_overlay_button.setToolTip("Overlay trajectories (shortcut: o)")
        traj_filter = BubbleTipFilter("Overlay trajectories (shortcut: o)", self)
        self.traj_overlay_button.installEventFilter(traj_filter)
        self.traj_overlay_button._bubble_filter = traj_filter
        self.traj_overlay_button.setIcon(QIcon(trajoverlayiconpath))
        self.traj_overlay_button.setIconSize(QSize(16, 16))
        self.traj_overlay_button.setFixedWidth(40)
        self.traj_overlay_button.setCheckable(True)
        self.traj_overlay_button.setChecked(True)
        self.traj_overlay_button.setObjectName("Toggle")
        # self.update_overlay_button_style(self.traj_overlay_button.isChecked())
        # self.traj_overlay_button.toggled.connect(self.update_overlay_button_style)
        contrastLayout.addWidget(self.traj_overlay_button)

        self.modeSwitch = ToggleSwitch()
        self.modeSwitch.toggled.connect(lambda state: self.onModeChanged("roi" if state else "spot"))
        contrastLayout.addWidget(self.modeSwitch)
        switch_filter = BubbleTipFilter("Switch between finding spots and drawing kymographs (shortcut: n)", self)
        self.modeSwitch.installEventFilter(switch_filter)
        # keep a ref so Python doesn’t garbage‐collect it
        self.modeSwitch._bubble_filter = switch_filter

        self.roi_overlay_button = AnimatedIconButton("")
        self.roi_overlay_button.setIcon(QIcon(roioverlayiconpath))
        self.roi_overlay_button.setIconSize(QSize(16, 16))
        # self.roi_overlay_button.setToolTip("Overlay ROI onto the movie")
        overlayroi_filter = BubbleTipFilter("Overlay the kymograph ROIs onto the movie", self)
        self.roi_overlay_button.installEventFilter(overlayroi_filter)
        self.roi_overlay_button._bubble_filter = overlayroi_filter
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

        self.delete_button = AnimatedIconButton("")
        # self.delete_button.setToolTip("Delete selected trajectory")
        deletetraj_filter = BubbleTipFilter("Delete selected trajectory", self)
        self.delete_button.installEventFilter(deletetraj_filter)
        self.delete_button._bubble_filter = deletetraj_filter
        self.delete_button.setIcon(QIcon(crossiconpath))
        self.delete_button.setIconSize(QSize(16, 16))
        self.delete_button.setFixedWidth(40)
        self.delete_button.setObjectName("Passive")
        contrastLayout.addWidget(self.delete_button)

        self.clear_button = AnimatedIconButton("")
        # self.clear_button.setToolTip("Delete all trajectories")
        deletealltraj_filter = BubbleTipFilter("Delete all trajectories", self)
        self.clear_button.installEventFilter(deletealltraj_filter)
        self.clear_button._bubble_filter = deletealltraj_filter
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
        rightPanel.setFixedWidth(500)
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

        self.histogramCanvas.setMouseTracking(True)
        CenteredBubbleFilter(
            text="Pixel intensities within the search range",
            parent=self,
            delay_ms=1000,
            visible_ms=3000
        ).attachTo(self.histogramCanvas)

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

        self.intensityCanvas.setMouseTracking(True)
        # CenteredBubbleFilter(
        #     text="Spot intensities for the trajectory",
        #     parent=self,
        #     delay_ms=1000,
        #     visible_ms=3000
        # ).attachTo(self.intensityCanvas)

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

        self.velocityCanvas.setMouseTracking(True)
        CenteredBubbleFilter(
            text="Frame-to-frame speeds",
            parent=self,
            delay_ms=1000,
            visible_ms=3000
        ).attachTo(self.velocityCanvas)

        # --- Analysis Slider
        # self.analysisSlider = QSlider(Qt.Horizontal)
        # analysissliderfilter = BubbleTipFilter("Slide through trajectory points", self, placement="left")
        # self.analysisSlider.installEventFilter(analysissliderfilter)
        # self.analysisSlider._bubble_filter = analysissliderfilter
        # self.analysisSlider.setMinimum(0)
        # self.analysisSlider.setMaximum(0)  # Will be updated later when analysis data is computed
        # self.analysisSlider.setValue(0)
        # self.analysisSlider.valueChanged.connect(self.on_analysis_slider_changed)
        # self.analysisSlider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        # rightPanelLayout.addWidget(self.analysisSlider, stretch=0)

        # rightPanelLayout.addSpacing(-2)

        # self.trajectoryControlButtons = TrajectoryControlButtons(self)
        # rightPanelLayout.addWidget(self.trajectoryControlButtons, stretch=0)

        rightPanel.setLayout(rightPanelLayout)
        self.topRightSplitter.addWidget(rightPanel)
        # Optional: adjust stretch factors for the topRightSplitter:
        self.topRightSplitter.setStretchFactor(0, 3)  # movie widget
        self.topRightSplitter.setStretchFactor(1, 2)  # right panel
        self.topRightSplitter.setCollapsible(1, True)
        self.topRightSplitter.setCollapsible(0, True)

        # Add the top row (movie + right panel) as the upper widget in the vertical splitter.
        self.rightVerticalSplitter.addWidget(self.topRightSplitter)

        # BOTTOM of RIGHT: Trajectory Canvas that now spans the full width of the right side.
        self.trajectoryCanvas = TrajectoryCanvas(self, self.kymoCanvas, self.movieCanvas, self.intensityCanvas, navigator=self)
        self.rightVerticalSplitter.addWidget(self.trajectoryCanvas)

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
                background: white;
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
                color: #666666;
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
                color: rgba(255, 0, 255, 130);
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
                background-color: white;
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
        pos = parent.mapToGlobal(QPoint((pw-lw)//2, (ph-lh)//62))
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

    def _showRadiusDialog(self):
        # don’t open if it’s already up
        if self._radiusPopup:
            return

        dlg = RadiusDialog(self.searchWindowSpin.value(), self)
        dlg.adjustSize()
        pw, ph = self.width(), self.height()
        lw, lh = dlg.width(), dlg.height()
        x = (pw - lw) // 2
        y = (ph - lh) // 90
        dlg.move(self.mapToGlobal(QPoint(x, y)))
        dlg.show()

        self._radiusPopup    = dlg
        self._radiusSpinLive = dlg._spin

    def handleGlobalX(self):
        """
        Global handler for the X key: invalidate or re-validate the current point,
        update all analysis buffers, trajectory model, table UI, and redraw everything.
        """
        # ── 0) Basic guards ──
        if not getattr(self, "intensityCanvas", None) or not self.intensityCanvas.point_highlighted:
            return
        if not self.analysis_frames or not self.analysis_original_coords:
            return
        if self.looping:
            self.stoploop()

        # ── 1) Figure out which point & trajectory row we’re on ──
        idx = self.intensityCanvas.current_index
        row_sel = self.trajectoryCanvas.table_widget.selectionModel().selectedRows()
        if not row_sel:
            return
        row = row_sel[0].row()
        traj = self.trajectoryCanvas.trajectories[row]

        # ── 2) Capture “was valid?” before we clobber it ──
        was_valid = (self.analysis_intensities[idx] is not None)

        # ── 3) Prepare some locals ──
        frame       = self.analysis_frames[idx]
        search_ctr  = self.analysis_search_centers[idx]
        crop_size   = int(2 * self.searchWindowSpin.value())
        frame_image = self.get_movie_frame(frame)

        # ── 4) Invalidate vs re-validate ──
        if was_valid:
            # ---- invalidation: clear fit & intensities ----
            self.analysis_fit_params[idx] = (None, None, None)
            self.analysis_intensities[idx]  = None

            # clear any colocalization flags
            if getattr(self, "check_colocalization", False) and self.movie.ndim == 4:
                traj["colocalization_any"][idx] = None
                for lst in traj["colocalization_by_ch"].values():
                    lst[idx] = None
                # also wipe navigator’s buffers so percentages recalc skip this point
                self.analysis_colocalized[idx] = False
                for lst in self.analysis_colocalized_by_ch.values():
                    lst[idx] = False

            self.movieCanvas.remove_gaussian_circle()
            self.movieCanvas.remove_inset_circle()

            self.flash_message("Remove")
            # (marker drawing moved below)

        else:
            # ---- re-analysis: Gaussian fit + recalc colocalization ----
            fitted, sigma, intensity, peak, bkgr = perform_gaussian_fit(
                frame_image, search_ctr, crop_size,
                pixelsize=self.pixel_size,
                bg_fixed=self.analysis_background[idx]
            )
            self.analysis_fit_params[idx] = (fitted, sigma, peak)
            self.analysis_intensities[idx] = intensity

            # re-run colocalization for this frame if needed
            if getattr(self, "check_colocalization", False) and self.movie.ndim == 4:
                # pull back the trajectory’s dict
                traj = self.trajectoryCanvas.trajectories[row]

                # 1) compute flags for this single frame & its fitted center
                any_flag, per_ch = self._coloc_flags_for_frame(frame, fitted)

                # 2) store them in both the trajectory and your navigator buffers
                traj["colocalization_any"][idx] = any_flag
                self.analysis_colocalized[idx] = any_flag

                for ch, flag in per_ch.items():
                    traj["colocalization_by_ch"][ch][idx] = flag
                    self.analysis_colocalized_by_ch[ch][idx] = flag

            self.flash_message("Reattempt")
            # (marker drawing moved below)

        # ── 5) Recompute velocities, averages, etc. ──
        centers = [p for p,_,_ in self.analysis_fit_params]
        self.analysis_velocities = calculate_velocities(centers)
        # avg velocity (px/frame)
        vels = [v for v in self.analysis_velocities if v is not None]
        self.average_velocity = float(np.mean(vels)) if vels else None
        # avg & median intensity
        ints = [v for v in self.analysis_intensities if v not in (None, 0)]
        self.analysis_avg    = float(np.mean(ints))   if ints else None
        self.analysis_median = float(np.median(ints)) if ints else None

        # ── 6) Update trajectory dict with new arrays ──
        traj["spot_centers"][idx] = self.analysis_fit_params[idx][0]
        traj["sigmas"][idx]       = self.analysis_fit_params[idx][1]
        traj["peaks"][idx]        = self.analysis_fit_params[idx][2]
        traj["intensities"][idx]  = self.analysis_intensities[idx]
        traj["velocities"]        = self.analysis_velocities
        traj["average_velocity"]  = self.average_velocity
        traj["average"]           = self.analysis_avg
        traj["median"]            = self.analysis_median
        traj["background"]        = self.analysis_background

        # ── 7) Rewrite the entire row of the table ──
        # valid %
        pct_valid = int(
            100 * len(ints) /
            len(self.analysis_frames)
        ) if self.analysis_frames else 0
        self.trajectoryCanvas.writeToTable(row, "valid", str(pct_valid))
        # median intensity
        medtxt = "" if self.analysis_median is None else f"{self.analysis_median:.2f}"
        self.trajectoryCanvas.writeToTable(row, "medintensity", medtxt)

        # colocalization % columns
        if getattr(self, "check_colocalization", False) and self.movie.ndim == 4:
            n_chan = self.movie.shape[self._channel_axis]
            # compute new percentages
            any_flags = traj["colocalization_any"]
            valid_any = [s for s in any_flags if s is not None]
            pct_any   = (
                f"{100*sum(1 for s in valid_any if s=='Yes')/len(valid_any):.1f}"
                if valid_any else ""
            )
            by_ch = traj["colocalization_by_ch"]
            pct_by_ch = {
                ch: (
                    f"{100*sum(1 for s in lst if s=='Yes')/len([s for s in lst if s is not None]):.1f}"
                    if any(s is not None for s in lst) else ""
                )
                for ch, lst in by_ch.items()
            }

            for ch in range(1, n_chan+1):
                hdr = f"Ch. {ch} co. %"
                if ch == traj["channel"]:
                    val = ""
                elif n_chan == 2:
                    val = pct_any
                else:
                    val = pct_by_ch.get(ch, "")
                self.trajectoryCanvas._mark_custom(row, hdr, val)

         # ── 7.5) Steps ──
        if getattr(self, "show_steps", False):
            # compute_steps_for_data should return (step_indices, step_medians).
            step_idxs, step_meds = self.compute_steps_for_data(
                self.analysis_frames,
                self.analysis_intensities,
            )
            traj["step_indices"] = step_idxs
            traj["step_medians"] = step_meds
        else:
            # if steps aren’t shown, clear them out
            traj["step_indices"] = None
            traj["step_medians"] = None

        # ── 8) Refresh all canvases ──
        self.kymoCanvas.update_view()
        self.kymoCanvas.clear_kymo_trajectory_markers()
        self.kymoCanvas.remove_circle()

        # re-draw overlays if toggled
        self.movieCanvas.draw_trajectories_on_movie()
        self.kymoCanvas.draw_trajectories_on_kymo()

        # re-draw intensity / velocity plots
        self.intensityCanvas.plot_intensity(
            self.analysis_frames,
            self.analysis_intensities,
            avg_intensity=self.analysis_avg,
            median_intensity=self.analysis_median,
            colors=self._get_traj_colors(traj)[0]
        )
        self.intensityCanvas.highlight_current_point()
        self.velocityCanvas.plot_velocity_histogram(self.analysis_velocities)

        # ── 9) Draw the invalidation/validation circles ──
        if was_valid:
            # grey on kymo at search center
            sx, sy = search_ctr
            kymo_name = self.kymoCombo.currentText()
            if kymo_name and kymo_name in self.kymographs and self.rois:
                roi = self.rois[self.roiCombo.currentText()]
                xk = self.compute_kymo_x_from_roi(
                    roi, sx, sy,
                    self.kymographs[kymo_name].shape[1]
                )
                if xk is not None:
                    disp_frame = (self.movie.shape[0] - 1) - frame
                    self.kymoCanvas.add_circle(xk, disp_frame, color='grey')
        else:
            # magenta on movie at fitted center
            if fitted is not None:
                pointcolor = self.get_point_color()
                self.movieCanvas.add_gaussian_circle(fitted, sigma, pointcolor)
                center_for_zoom = fitted if fitted is not None else search_ctr
                self.movieCanvas.update_inset(
                    frame_image, center_for_zoom, int(self.insetViewSize.value()), 2,
                    fitted_center=fitted,
                    fitted_sigma=sigma,
                    fitted_peak=peak,
                    intensity_value=intensity,
                    offset = bkgr,
                    pointcolor = pointcolor
                )
            # magenta on kymo at fitted center (or search if fit failed)
            fx, fy = (fitted if fitted is not None else search_ctr)
            kymo_name = self.kymoCombo.currentText()
            if kymo_name and kymo_name in self.kymographs and self.rois:
                roi = self.rois[self.roiCombo.currentText()]
                xk = self.compute_kymo_x_from_roi(
                    roi, fx, fy,
                    self.kymographs[kymo_name].shape[1]
                )
                if xk is not None:
                    disp_frame = (self.movie.shape[0] - 1) - frame
                    self.kymoCanvas.add_circle(xk, disp_frame, color=self.get_point_color())

        # final idle draws
        self.zoomInsetWidget.draw_idle()
        self.kymoCanvas.draw_idle()
        self.movieCanvas.draw_idle()

    def get_point_color(self):
        return self.intensityCanvas.get_current_point_color()

    # In create_menu(), add a new menu action:
    def create_menu(self):
        menubar = self.menuBar()

        # Create File-related menus
        loadMenu = menubar.addMenu("Load")

        loadMovieAction = QAction("Movie", self)
        loadMovieAction.triggered.connect(self.handle_movie_load)
        loadMenu.addAction(loadMovieAction)

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

        self.spotMenu = menubar.addMenu("Spot")
        searchRadiusAction = QAction("Search Radius", self)
        searchRadiusAction.triggered.connect(self.set_search_radius)
        self.spotMenu.addAction(searchRadiusAction)

        # Create a QComboBox for Tracking Mode and wrap it in a QWidgetAction.
        trackingModeAction = QAction("Tracking Mode", self)
        trackingModeAction.triggered.connect(self.set_tracking_mode)
        self.spotMenu.addAction(trackingModeAction)

        avoidOldSpotsAction = QAction("Avoid previous spots", self, checkable=True)
        avoidOldSpotsAction.setChecked(False)
        avoidOldSpotsAction.setStatusTip("Skip any spot centers that were already analysed")
        avoidOldSpotsAction.toggled.connect(lambda checked: setattr(self, "avoid_previous_spot", checked))
        self.spotMenu.addAction(avoidOldSpotsAction)

        kymoMenu = menubar.addMenu("Kymograph")

        kymoLoGAction = QAction("Apply LoG filter", self, checkable=True)
        kymoLoGAction.setChecked(False)
        kymoLoGAction.toggled.connect(self.on_toggle_log_filter)
        kymoMenu.addAction(kymoLoGAction)

        kymopreferencesAction = QAction("Line options", self)
        kymopreferencesAction.triggered.connect(self.open_kymopreferences_dialog)
        kymoMenu.addAction(kymopreferencesAction)

        kymoGenerateFromTrajAction = QAction("Draw from trajectories", self)
        kymoGenerateFromTrajAction.triggered.connect(self.generate_rois_from_trajectories)
        kymoMenu.addAction(kymoGenerateFromTrajAction)

        connectgapsAction = QAction("Connect spot gaps", self, checkable=True)
        connectgapsAction.setChecked(False)
        connectgapsAction.toggled.connect(self.on_connect_spot_gaps_toggled)
        kymoMenu.addAction(connectgapsAction)

        trajMenu = menubar.addMenu("Trajectories")

        recalcAction = QAction("Recalculate selected", self)
        recalcAction.triggered.connect(self.trajectoryCanvas.recalculate_trajectory)
        trajMenu.addAction(recalcAction)

        recalcAction = QAction("Recalculate all", self)
        recalcAction.triggered.connect(self.trajectoryCanvas.recalculate_all_trajectories)
        trajMenu.addAction(recalcAction)

        binColumnAct = QAction("Add binary column", self)
        trajMenu.addAction(binColumnAct)
        binColumnAct.triggered.connect(self.trajectoryCanvas._add_binary_column_dialog)
        
        valColumnAct = QAction("Add value column", self)
        trajMenu.addAction(valColumnAct)
        valColumnAct.triggered.connect(self.trajectoryCanvas._add_value_column_dialog)

        self.showStepsAction = QAction("Calculate Steps", self, checkable=True)
        self.showStepsAction.setChecked(False)
        self.showStepsAction.toggled.connect(self.on_show_steps_toggled)
        trajMenu.addAction(self.showStepsAction)

        self._colorBySeparator = trajMenu.addSeparator()
        self._colorByActions   = []
        self.trajMenu          = trajMenu

        viewMenu = menubar.addMenu("View")

        self.invertAct = QAction("Invert", self, checkable=True)
        self.invertAct.triggered.connect(self.toggle_invert_cmap)
        viewMenu.addAction(self.invertAct)

        zoomAction = QAction("Inset size", self)
        zoomAction.triggered.connect(self.open_zoom_dialog)
        viewMenu.addAction(zoomAction)

    def _rebuild_color_by_actions(self):
        # 1) clear old
        for act in self._colorByActions:
            self.trajMenu.removeAction(act)
        self._colorByActions.clear()

        # 2) add custom columns (binary/value)
        for col in self.trajectoryCanvas.custom_columns:
            ctype = self.trajectoryCanvas._column_types[col]
            if ctype in ("binary", "value"):
                act = QAction(f"Color by {col}", self, checkable=True)
                act.setData(col)   # ← store the real key
                act.toggled.connect(lambda on, a=act: 
                    self._on_color_by_toggled(a.data(), a, on)
                )
                # if already selected, show its checkmark
                if self.color_by_column == col:
                    act.setChecked(True)

                self.trajMenu.insertAction(self._colorBySeparator, act)
                self._colorByActions.append(act)

        if self.movie is None:
            return
        
        # 3) count channels
        if self.movie.ndim == 4 and self._channel_axis is not None:
            n_chan = self.movie.shape[self._channel_axis]
        else:
            n_chan = 1

        # 4) add colocalization actions
        if n_chan == 2:
            key = "colocalization"
            act = QAction("Color by colocalization", self, checkable=True)
            act.setData(key)
            act.toggled.connect(lambda on, a=act: 
                self._on_color_by_toggled(a.data(), a, on)
            )
            if self.color_by_column == key:
                act.setChecked(True)
            self.trajMenu.insertAction(self._colorBySeparator, act)
            self._colorByActions.append(act)

        elif n_chan > 2:
            for tgt in range(1, n_chan+1):
                key  = f"coloc_ch{tgt}"
                text = f"Color by Ch. {tgt} coloc"
                act = QAction(text, self, checkable=True)
                act.setData(key)
                act.toggled.connect(lambda on, a=act: 
                    self._on_color_by_toggled(a.data(), a, on)
                )
                if self.color_by_column == key:
                    act.setChecked(True)
                self.trajMenu.insertAction(self._colorBySeparator, act)
                self._colorByActions.append(act)

    def _on_color_by_toggled(self, column_name, action, checked):
        if checked and (column_name == "colocalization" or column_name.startswith("coloc_ch")):
            # colocalizationAction is the QAction you created earlier:
            if not self.colocalizationAction.isChecked():
                # this will both check its box and fire your on_colocalization_toggled handler
                self.colocalizationAction.setChecked(True)

        if checked:
            # uncheck the other color‐by actions
            for act in self._colorByActions:
                if act is not action:
                    act.setChecked(False)
            self.set_color_by(column_name)
        else:
            # if you untoggle a color‐by, clear it
            self.set_color_by(None)

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
            image, center, old_crop, zoom_factor, fcenter, fsigma, fpeak, bkgr, ivalue, pointcolor = \
                self.movieCanvas._last_inset_params

            # 3) re‑fire update_inset with the NEW crop_size
            self.movieCanvas.update_inset(
                image, center, spinbox.value(), zoom_factor,
                fitted_center=fcenter,
                fitted_sigma=fsigma,
                fitted_peak=fpeak,
                intensity_value=ivalue,
                offset = bkgr,
                pointcolor = pointcolor
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

        self.show_steps = False
        self.showStepsAction.setChecked(False)

        if (self.rois or self.kymographs or
            (hasattr(self, 'trajectoryCanvas') and self.trajectoryCanvas.trajectories)):
            reply = QMessageBox.question(
                self,
                "Clear existing data?",
                "Clear existing data before loading a new movie?",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Cancel:
                # user chose cancel → abort load_movie entirely
                return
            # user chose Yes → clear all existing data
            self.clear_flag = True

        if fname:
            self._last_dir = os.path.dirname(fname)
            # try:
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

            self.movieNameLabel.setText("")
            self.movieNameLabel.setStyleSheet("background: transparent; color: black; font-size: 16px; font-weight: bold")
            self.movieNameLabel.setText(os.path.basename(fname))
            self.movieNameLabel.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            

            # ── blank out the histogram canvas entirely ────────────────────────────────
            if hasattr(self, 'histogramCanvas'):
                self.histogramCanvas.ax.cla()
                self.histogramCanvas.ax.axis("off")
                self.histogramCanvas.draw_idle()
            # ── blank out the intensity/plot canvas (two sub‐axes) ────────────────────
            if hasattr(self, 'intensityCanvas'):
                ic = self.intensityCanvas

                # 1) clear any old highlights & scatter
                ic.clear_highlight()
                ic.scatter_obj_top = None
                ic.scatter_obj_bottom = None
                ic.point_highlighted = False
                ic._last_plot_args   = None

                # 2) completely wipe both axes
                ic.ax_top.cla()
                ic.ax_bottom.cla()
                ic.ax_top.axis('off')
                ic.ax_bottom.axis('off')

                # 3) redraw & grab a fresh background
                ic.draw()
                ic._background = ic.copy_from_bbox(ic.fig.bbox)
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

            if self.clear_flag:
                self.clear_rois()
                self.clear_kymographs()
                self.trajectoryCanvas.clear_trajectories(prompt=False)
                self.clear_flag = False

            self.movieCanvas.draw_idle()
            self.movieCanvas.clear_sum_cache()

            self.update_scale_label()

            if self.pixel_size is None or self.frame_interval is None:
                self.set_scale()

            # self.last_kymo_by_channel = {}

            # If there are any existing custom columns, ask whether to clear them now
            # only ask if there are any custom‐columns other than the auto‐added colocalization % ones
            non_coloc = [
                name for name in self.trajectoryCanvas.custom_columns
                if self.trajectoryCanvas._column_types.get(name) != "coloc"
            ]
            if non_coloc:
                reply = QMessageBox.question(
                    self,
                    "Clear custom columns?",
                    "Do you want to clear all user-defined custom columns?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes
                )
                if reply == QMessageBox.Yes:
                    # remove only those non-coloc columns
                    for name in non_coloc:
                        idx = self.trajectoryCanvas._col_index.get(name)
                        if idx is not None:
                            self.trajectoryCanvas._remove_custom_column(idx, name)
                    # rebuild the menu so it no longer shows those entries
            
            coloc_cols = [
                name for name in self.trajectoryCanvas.custom_columns
                if self.trajectoryCanvas._column_types.get(name) == "coloc"
            ]
            for name in coloc_cols:
                idx = self.trajectoryCanvas._col_index.get(name)
                if idx is not None:
                    self.trajectoryCanvas._remove_custom_column(idx, name)
            self._rebuild_color_by_actions()

            if self.movie.ndim == 4:
                # — multi-channel: build co-localization columns —
                # first, clear any old co. % columns if reloading
                old = [c for c in self.trajectoryCanvas.custom_columns if c.endswith(" co. %")]
                for name in old:
                    idx = self.trajectoryCanvas._col_index.get(name)
                    if idx is not None:
                        self.trajectoryCanvas._remove_custom_column(idx, name)

                # now add fresh ones
                self.trajectoryCanvas.custom_columns = [c for c in self.trajectoryCanvas.custom_columns if not c.endswith(" co. %")]
                n_chan = self.movie.shape[self._channel_axis]
                for ch in range(1, n_chan+1):
                    col_name = f"Ch. {ch} co. %"
                    self.trajectoryCanvas._add_custom_column(col_name, col_type="coloc")

                # print("load_movie custom_columns, after add_custom_column", self.trajectoryCanvas.custom_columns)

                if not hasattr(self, 'colocalizationAction'):
                    self.colocalizationAction = QAction("Check colocalization", self, checkable=True)
                    self.colocalizationAction.setChecked(False)
                    self.colocalizationAction.toggled.connect(self.on_colocalization_toggled)
                    # spotMenu must be the same QMenu you created originally
                    self.spotMenu.addAction(self.colocalizationAction)

            else:
                # single-channel: ensure no stray co. % columns remain
                for name in [c for c in self.trajectoryCanvas.custom_columns if c.endswith(" co. %")]:
                    idx = self.trajectoryCanvas._col_index.get(name)
                    if idx is not None:
                        self.trajectoryCanvas._remove_custom_column(idx, name)

                self.check_colocalization = False
                # if we previously added the colocalization action, uncheck & remove it
                if hasattr(self, 'colocalizationAction'):
                    # 1) uncheck so UI state is clean
                    self.colocalizationAction.setChecked(False)
                    # 2) disconnect its signal to avoid any stray callbacks
                    try:
                        self.colocalizationAction.toggled.disconnect(self.on_colocalization_toggled)
                    except TypeError:
                        pass
                    # 3) remove it from the spot‐menu
                    self.spotMenu.removeAction(self.colocalizationAction)
                    # 4) delete the attribute so next time we know it’s gone
                    del self.colocalizationAction

            # Always untoggle any active “Color by …” before we potentially reload columns
            self.set_color_by(None)

            self.flash_message("Loaded movie")

            if self.movie.ndim == 4:
                filt = self._ch_overlay._bubble_filter
                filt._wobj = self._ch_overlay
                QTimer.singleShot(2000, lambda: filt._showBubble(force=True))

            # except Exception as e:
            #     QMessageBox.critical(self, "Error", f"Could not load movie:\n{str(e)}")

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
                selected_chan = channel_override-1
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

        if self.looping:
            self.stoploop()

        if self.refBtn.isChecked():
            self.refBtn.setChecked(False)

        self.cancel_left_click_sequence()

        # 1) figure out which channel we’re on
        ch = index + 1

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

        self._ch_overlay.setText(f"ch{ch}")
        self._ch_overlay.adjustSize()
        self._reposition_channel_overlay()
        self._ch_overlay.show()

        # 10) clear & redraw trajectories on the movie, now that channel has changed
        self.movieCanvas.clear_movie_trajectory_markers()
        self.movieCanvas.draw_trajectories_on_movie()

        if self.intensityCanvas.point_highlighted and ch == self.analysis_channel and self.intensityCanvas._last_plot_args is not None:

            ic_index = self.intensityCanvas.current_index

            # cache arrays once
            centers = np.asarray(self.analysis_search_centers)  # shape (N,2)
            cx, cy = centers[ic_index]

            mc.overlay_rectangle(cx, cy, int(2*self.searchWindowSpin.value()))
            mc.remove_gaussian_circle()

            fc = fs = pk = None
            # draw fit circle & intensity highlight
            if hasattr(self, "analysis_fit_params") and ic_index < len(self.analysis_fit_params):
                fc, fs, pk = self.analysis_fit_params[ic_index]

            pointcolor = self.intensityCanvas.get_current_point_color()
            mc.add_gaussian_circle(fc, fs, pointcolor)

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
                        disp_frame = (self.movie.shape[0] - 1) - self.frameSlider.value()
                        self.kymoCanvas.add_circle(
                            xk, disp_frame,
                            color=pointcolor if fc is not None else 'grey'
                        )


        self.movieCanvas.draw()

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
                            [f"Ch. {i+1}" for i in range(channels)], 0, False
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

            reffilt = self.refBtn._bubble_filter
            reffilt._wobj = self.refBtn
            QTimer.singleShot(1000, lambda: reffilt._showBubble(force=True))

        except Exception as e:
            QMessageBox.critical(self, "Load Error", f"Could not load reference image:\n{str(e)}")


    def on_ref_toggled(self, checked):
        if checked:
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
                            "It must be a 2D image or a 3D image with 1, 3, or 4 channels."
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
                kymo_name = f"ch{ch+1}-{roi_name}"
                if resp == QMessageBox.Yes:
                    # Generate kymograph for this channel
                    kymo = self.movieCanvas.generate_kymograph(
                        roi, channel_override=ch+1
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

    def _select_next_kymo(self):
        """Advance the kymo combo one step (if possible)."""
        idx = self.kymoCombo.currentIndex()
        if idx >= 0 and idx < self.kymoCombo.count() - 1:
            self.kymoCombo.setCurrentIndex(idx + 1)

    def _select_prev_kymo(self):
        """Go back one step in the kymo combo (if possible)."""
        idx = self.kymoCombo.currentIndex()
        if idx > 0:
            self.kymoCombo.setCurrentIndex(idx - 1)

    def update_kymo_list_for_channel(self):
        ch = int(self.movieChannelCombo.currentText())
        self.kymoCombo.blockSignals(True)
        self.kymoCombo.clear()

        # 1) Populate only this channel’s items
        for name, info in self.kymo_roi_map.items():
            if info["channel"] == ch and not info.get("orphaned", False):
                self.kymoCombo.addItem(name)
        self.kymoCombo.blockSignals(False)

        # Get all names in this channel
        names = [self.kymoCombo.itemText(i) for i in range(self.kymoCombo.count())]

        # 2) If there are no kymographs at all, clear and return
        if not names:
            self.kymoCombo.setCurrentIndex(-1)
            self.kymoCanvas.ax.cla()
            self.kymoCanvas.ax.axis("off")
            self.kymoCanvas.draw_idle()
            # self._last_roi = None
            return

        # 3) Try to find a “sister” matching the last ROI
        sel = None
        last_roi = self._last_roi
        if last_roi is not None:
            for name in names:
                if self.kymo_roi_map[name]["roi"] == last_roi:
                    sel = name
                    break

        # 4) If no sister found, we want a blank canvas
        if sel is None:
            self.kymoCombo.setCurrentIndex(-1)
            self.kymoCanvas.ax.cla()
            self.kymoCanvas.ax.axis("off")
            self.kymoCanvas.draw_idle()
            # don’t change self._last_roi — so that if the user later switches
            # back to a channel where a sister *does* exist, it’ll pop right in.
            return

        # 5) Otherwise select & display the sister
        self.kymoCombo.setCurrentText(sel)
        self.kymo_changed()

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

    def kymo_changed(self):

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

        # 1) Remove mapping and drop any zoom state for its ROI
        mapping = self.kymo_roi_map.pop(current, None)
        if mapping:
            roi_name = mapping["roi"]
            # drop zoom/pan state
            self._roi_zoom_states.pop(roi_name, None)
            # if this was the last ROI we saw, clear it
            if self._last_roi == roi_name:
                self._last_roi = None
            # remove the ROI itself if nobody else references it
            if not any(info["roi"] == roi_name for info in self.kymo_roi_map.values()):
                self.rois.pop(roi_name, None)
                idx = self.roiCombo.findText(roi_name)
                if idx >= 0:
                    self.roiCombo.removeItem(idx)

        # 2) Delete the kymograph
        self.kymographs.pop(current, None)

        # 3) Remove it from the combo
        old_index = self.kymoCombo.currentIndex()
        self.kymoCombo.removeItem(old_index)

        # 4) Show next one or clear
        if self.kymoCombo.count() > 0:
            new_index = old_index - 1 if old_index > 0 else 0
            self.kymoCombo.setCurrentIndex(new_index)
        else:
            self.kymoCanvas.ax.cla()
            self.kymoCanvas.ax.axis("off")
            self.kymoCanvas.draw_idle()

        # 5) Re-run selection & visibility
        self.kymo_changed()
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

        self.kymographs.clear()
        self.kymo_roi_map.clear()
        self._roi_zoom_states.clear()
        self._last_roi = None
        self.kymoCombo.clear()
        self.kymoCanvas.ax.cla()
        self.kymoCanvas.ax.axis("off")
        self.kymoCanvas.draw_idle()
        self.update_kymo_visibility()
        self.update_roilist_visibility()

    def clear_rois(self):
        # Clear the ROI combo box and the dictionary.
        self.rois.clear()
        self.roiCombo.clear()

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

        self._roi_zoom_states.clear()
        self._last_roi = None
        self.movieCanvas.draw_idle()
        self.update_kymo_visibility()
        self.update_roilist_visibility()
        
    def on_kymo_click(self, event):

        if event.button == 3 and self._skip_next_right:
            # we just showed the menu for a label—don’t do live updates
            self._skip_next_right = False
            return

        if event.button == 1 and event.inaxes is self.kymoCanvas.ax and self.traj_overlay_button.isChecked() and len(self.analysis_points) == 0:
            current_row = self.trajectoryCanvas.table_widget.currentRow()
            for scatter in self.kymoCanvas.scatter_objs_traj:
                hit, info = scatter.contains(event)
                if not hit:
                    continue

                if self.looping:
                    self.stoploop()

                traj_idx  = scatter.traj_idx  # or lookup from your dict
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

        # — record the anchor in both kymo‐space & movie‐space —
        self.analysis_anchors.append((frame_idx, event.xdata, event.ydata))
        self.analysis_points.append((frame_idx, x_orig, y_orig))

        # — draw a small circle there —
        marker = self.kymoCanvas.temporary_circle(event.xdata, event.ydata,
                                              size=8, color='#7da1ff')
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

        # Compute crop sizes based on your UI spinboxes.
        search_crop_size = int(2 * self.searchWindowSpin.value())
        zoom_crop_size = int(self.insetViewSize.value())

        # then also clear any magenta gaussian circle on the movie canvas
        removed = self.movieCanvas.remove_gaussian_circle()
        if removed:
            self.movieCanvas.draw_idle()

        # Update the MovieCanvas overlay for visual feedback.
        frame_number = frame_idx+1
        self.movieCanvas.overlay_rectangle(x_orig, y_orig, search_crop_size)

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
            self._last_kymo_artist = artist
            # if it was a left click, select the row immediately
            if event.mouseevent.button == 1:
                row = self._kymo_label_to_row[artist]
                tbl = self.trajectoryCanvas.table_widget
                tbl.setCurrentCell(row, 0)
                tbl.scrollToItem(tbl.item(row, 0))
                self._ignore_next_kymo_click = True
                self._skip_next_right = True

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
        display_text = f"F: {int(frame_val)} X: {real_x_fortxt:.1f} Y: {real_y_fortxt:.1f} V: {pixel_val}"
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
        canvas = self.movieCanvas.figure.canvas
        self.movieCanvas._bg = canvas.copy_from_bbox(self.movieCanvas.ax.bbox)

    def jump_to_analysis_point(self, index, animate="ramp", zoom=False):

        # ——— Early exits & locals ———
        if not self.analysis_frames or not self.analysis_search_centers:
            return
        n = len(self.analysis_frames)
        if index < 0 or index >= n:
            return

        self.cancel_left_click_sequence()

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

            if zoom:
                r     = self.searchWindowSpin.value()

                # 1) get the container’s current size and pixel aspect ratio
                cont = self.movieDisplayContainer
                pw   = cont.width()   # pixel width
                ph   = cont.height()  # pixel height
                if ph == 0:
                    aspect = 1.0
                else:
                    aspect = pw / ph   # e.g. 16/9 = 1.78

                # 2) define your zoom height in data units
                fov_y = 10 * r
                #    then compute the matching width
                fov_x = fov_y * aspect

                half_x = fov_x / 2.0
                half_y = fov_y / 2.0

                new_xlim = (cx - half_x, cx + half_x)
                new_ylim = (cy - half_y, cy + half_y)

                # 3) mark manual zoom & animate or set directly
                mc.manual_zoom = True

                if animate == "ramp":
                    self.animate_axes_transition(new_xlim, new_ylim, duration=300)
                elif animate == "linear":
                    self.animate_view_transition(new_xlim, new_ylim, duration=15)
                else:
                    mc.ax.set_xlim(new_xlim)
                    mc.ax.set_ylim(new_ylim)
                    mc.zoom_center = ((new_xlim[0] + new_xlim[1]) / 2,
                                    (new_ylim[0] + new_ylim[1]) / 2)

            else:
                new_xlim = (cx - w/2, cx + w/2)
                new_ylim = (cy - h/2, cy + h/2)
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
            # print("jump_to_analysis_point analysis_channel", self.analysis_channel)

            if self.analysis_channel is not None:
                self._select_channel(self.analysis_channel)

            if mc.sum_mode:
                mc.display_sum_frame()
                frame_img = mc.image
            else:
                frame_img = self.get_movie_frame(frame)
                if frame_img is None:
                    return
                mc.update_image_data(frame_img)

            # ——— 4) Restore manual zoom limits if needed ———
            if animate != "discrete" and mc.manual_zoom and not zoom:
                mc.ax.set_xlim(cur_xlim)
                mc.ax.set_ylim(cur_ylim)

            # ——— 5) Overlays ———
            mc.overlay_rectangle(cx, cy, int(2*self.searchWindowSpin.value()))
            mc.remove_gaussian_circle()

            # draw fit circle & intensity highlight
            if hasattr(self, "analysis_fit_params") and index < len(self.analysis_fit_params):
                fc, fs, pk = self.analysis_fit_params[index]
            else:
                fc = fs = pk = None

            if fc is not None and fs is not None:
                if ic: ic.highlight_current_point()
            elif ic:
                ic.highlight_current_point(override=True)

            ic.current_index = index
            pointcolor = ic.get_current_point_color()
            mc.add_gaussian_circle(fc, fs, pointcolor)

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
                            offset = background,
                            pointcolor = pointcolor)

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
                        kc.add_circle(
                            xk, disp_frame,
                            color=pointcolor if fc is not None else 'grey'
                        )

            # ——— 7) Histogram & sliders ———
            if hc:
                center_hist = fc if fc is not None else (cx, cy)
                hc.update_histogram(frame_img, center_hist,
                                    int(2*self.searchWindowSpin.value()),
                                    sigma=fs, intensity=intensity, background=background,
                                    peak=pk, pointcolor=pointcolor)
                
            self.frameSlider.setValue(frame)
            self.frameNumberLabel.setText(f"{frame+1}")
            if hasattr(self, 'analysisSlider'):
                self.analysisSlider.setValue(index)

        finally:
            mc.setUpdatesEnabled(True)
            kc.setUpdatesEnabled(True)

            # 1) draw the movie axes so that the new frame + overlays are on screen
            self.movieCanvas.draw()

            # 2) recapture the blit background for the movie axes
            canvas = mc.figure.canvas
            mc._bg = canvas.copy_from_bbox(mc.ax.bbox)
            # mc._roi_bg = canvas.copy_from_bbox(mc.ax.bbox)

            # 3) draw any other canvases as needed
            self.kymoCanvas.draw()

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

            mc = self.movieCanvas
            
            if i > steps:
                mc.ax.set_xlim(new_xlim)
                mc.ax.set_ylim(new_ylim)
                cx_new = (new_xlim[0] + new_xlim[1]) / 2.0
                cy_new = (new_ylim[0] + new_ylim[1]) / 2.0
                # set the logical center & draw
                
                mc.zoom_center = (cx_new, cy_new)
                mc.draw_idle()
                # grab clean background
                canvas = mc.figure.canvas
                mc._bg     = canvas.copy_from_bbox(mc.ax.bbox)
                mc._roi_bg = canvas.copy_from_bbox(mc.ax.bbox)
                # recompute scale so future scrolls/pans start here
                w = mc.width() or 1
                mc.scale = (new_xlim[1] - new_xlim[0]) / w
            else:
                t = i / steps
                interp_xlim = (initial_xlim[0]*(1-t) + new_xlim[0]*t,
                            initial_xlim[1]*(1-t) + new_xlim[1]*t)
                interp_ylim = (initial_ylim[0]*(1-t) + new_ylim[0]*t,
                            initial_ylim[1]*(1-t) + new_ylim[1]*t)
                mc.ax.set_xlim(interp_xlim)
                mc.ax.set_ylim(interp_ylim)
                mc.draw_idle()
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

        anim.finished.connect(self._capture_movie_bg)

        anim.start()
        
        # Keep a reference to avoid garbage collection.
        self._axes_anim = anim

    def _capture_movie_bg(self):
        """Called when axes‐transition animation completes."""
        mc     = self.movieCanvas
        canvas = mc.figure.canvas
        # grab the clean background for blitting
        mc._bg     = canvas.copy_from_bbox(mc.ax.bbox)
        mc._roi_bg = canvas.copy_from_bbox(mc.ax.bbox)

        # **recompute** zoom_center & scale from the *actual* new xlim/ylim**
        x0, x1 = mc.ax.get_xlim()
        y0, y1 = mc.ax.get_ylim()
        mc.zoom_center = ((x0 + x1) * 0.5, (y0 + y1) * 0.5)
        w = mc.width() or 1
        # horizontal data‐span divided by widget width gives new scale
        mc.scale = (x1 - x0) / w

    def compute_trajectory_background(self, get_frame, points, crop_size):
        half = crop_size // 2
        all_values = []

        for f, cx, cy in points:
            img = get_frame(f)
            if img is None:
                continue
            H, W = img.shape
            x0, y0 = int(round(cx)), int(round(cy))

            # Compute the slice indices and also flags for truncation
            x_start = x0 - half
            x_end   = x0 + half
            y_start = y0 - half
            y_end   = y0 + half

            left_trunc   = x_start < 0
            right_trunc  = x_end   > W
            top_trunc    = y_start < 0
            bottom_trunc = y_end   > H

            sub = img[
                max(0, y_start):min(H, y_end),
                max(0, x_start):min(W, x_end)
            ]
            if sub.size == 0:
                continue

            # collect border pixels only from the *un*-truncated sides
            h_sub, w_sub = sub.shape
            border = max(1, int(min(h_sub, w_sub) * 0.1))

            edges = []
            if not top_trunc:
                edges.append(sub[:border, :].ravel())
            if not bottom_trunc:
                edges.append(sub[-border:, :].ravel())
            if not left_trunc:
                edges.append(sub[:, :border].ravel())
            if not right_trunc:
                edges.append(sub[:, -border:].ravel())

            if edges:
                all_values.append(np.concatenate(edges))

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
            frames, coords, search_centers, ints, fits, background = self._compute_analysis(points, trajectory_background)
        except Exception as e:
            QMessageBox.warning(self, "", "There was an error adding computing this trajectory. Please try again (consider a longer trajectory or different radius).")
            print(f"_compute failed: {e}")
            self._is_canceled = True
        
        if self._is_canceled:
            return
        
        self.analysis_start, self.analysis_end = points[0], points[-1]
        self.analysis_frames, self.analysis_original_coords, self.analysis_search_centers = frames, coords, search_centers
        self.analysis_intensities, self.analysis_fit_params, self.analysis_background = ints, fits, background
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

        if getattr(self, 'check_colocalization', False) and self.movie.ndim == 4:
            self._compute_colocalization()
        else:
            # fill with Nones if turned off or single‐channel
            N = len(self.analysis_frames)
            self.analysis_colocalized = [None] * N
            # *also* define per-channel dict of None‐lists
            ref_ch    = self.analysis_channel
            n_chan    = self.movie.shape[self._channel_axis] if self._channel_axis is not None else 1
            self.analysis_colocalized_by_ch = {
                ch: [None]*N for ch in range(1, n_chan+1) if ch != ref_ch
            }

        if getattr(self, "show_steps", False):
            (
                self.analysis_step_indices,
                self.analysis_step_medians
            ) = self.compute_steps_for_data(
                self.analysis_frames,
                self.analysis_intensities
            )
        else:
            self.analysis_step_indices = None
            self.analysis_step_medians = None

        # slider
        if hasattr(self, 'analysisSlider'):
            s = self.analysisSlider
            s.blockSignals(True)
            s.setRange(0, len(frames)-1)
            s.setValue(0)
            s.blockSignals(False)

        self.trajectoryCanvas.hide_empty_columns()

    def _compute_analysis(self, points, bg=None, showprogress=True):
        mode = self.tracking_mode
        if mode == "Independent":
            return self._compute_independent(points, bg, showprogress)
        elif mode == "Tracked":
            return self._compute_tracked(points, bg, showprogress)
        elif mode == "Smooth":
            # 1) do the independent pass
            try:
                frames, coords, search_centers, ints, fit_params, background = (
                    self._compute_independent(points, bg, showprogress)
                )
            except Exception as e:
                print(f"_compute_independent failed: {e}")
                self._is_canceled = True #REMOVE THIS?
                return None, None, None, None, None, None
            return self._postprocess_smooth(frames, coords, ints, fit_params, background, bg)
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
                    background[idx]            = max(0, bkgr)
                    fit_params[idx]            = (fc, sigma, peak)
                    integrated_intensities[idx] = max(0, intensity)
            # otherwise leave None/grey

            # update progress
            if progress:
                progress.setValue(idx+1)
                QApplication.processEvents()
                if progress.wasCanceled():
                    self._is_canceled = True
                    progress.close()
                    break

        if progress:
            progress.close()

        return all_frames, all_coords, all_coords, integrated_intensities, fit_params, background

    def _compute_independent(self, points, bg=None, showprogress=True):  

        # print("compute", self._is_canceled)

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
            if getattr(self, "_is_canceled", False):
                if progress:
                    progress.close()
                return all_frames, all_coords, all_coords, integrated_intensities, fit_params, background
            f1, x1, y1 = points[i]
            f2, x2, y2 = points[i+1]
            seg = list(range(f1, f2+1)) if i==0 else list(range(f1+1, f2+1))
            n = len(seg)

            for j, f in enumerate(seg):
                if getattr(self, "_is_canceled", False):
                    if progress:
                        progress.close()
                    return all_frames, all_coords, all_coords, integrated_intensities, fit_params, background
                # compute independent center
                t = j/(n-1) if n>1 else 0
                cx = x1 + t*(x2-x1)
                cy = y1 + t*(y2-y1)
                all_coords.append((cx, cy))
                img = frame_cache[f]
                fc, sigma, intensity, peak, bkgr = None, None, None, None, None
                if img is not None:
                    fc, sigma, intensity, peak, bkgr = perform_gaussian_fit(
                        img, (cx, cy), int(2 * self.searchWindowSpin.value()), pixelsize = self.pixel_size, bg_fixed=bg
                    )
                if fc is not None:
                    is_retrack = (
                        self.avoid_previous_spot
                        and any(
                            pf == f and
                            np.hypot(fc[0] - px, fc[1] - py) < self.same_spot_threshold
                            for pf, px, py in self.past_centers
                        )
                    )
                    if not is_retrack:
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
                        self._is_canceled = True
                        progress.close()
                        return all_frames, all_coords, all_coords, integrated_intensities, fit_params, background

        if progress:
            progress.close()

        return all_frames, all_coords, all_coords, integrated_intensities, fit_params, background

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
                new_center, fc, sigma, intensity, peak, bkgr = \
                    self._track_frame(
                        f,
                        frame_cache[f],
                        icx, icy,
                        current_center,
                        search_radius,
                        pixel_size,
                        bg
                    )

                new_centers.append(new_center)
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
                        self._is_canceled = True
                        progress.close()
                        return (
                            all_frames,
                            independent_centers,
                            new_centers,
                            integrated_intensities,
                            fit_params,
                            background,
                        )

            if self._is_canceled:
                break

        # 6) clean up progress dialog
        if progress:
            progress.setValue(total_frames)
            progress.close()

        # 7) return frames, independent centers, blended centers, and fit results
        return all_frames, independent_centers, new_centers, integrated_intensities, fit_params, background

    def _track_frame(self, framenum, img, icx, icy, current, radius, pixel_size, bg=None):
        
        nc = ((current[0]+icx)/2, (current[1]+icy)/2)

        if img is None:
            # fallback to midpoint
            return nc, None, None, None, None, None

        fc, sigma, intensity, peak, bkgr = perform_gaussian_fit(
            img, current, radius,
            pixelsize=pixel_size,
            bg_fixed=bg
        )
        if fc is None:
            return nc, None, None, None, None, None

        if self.avoid_previous_spot and fc is not None:
            for pf, px, py in self.past_centers:
                if pf == framenum and np.hypot(fc[0] - px, fc[1] - py) < self.same_spot_threshold:
                    return (nc, None, None, None, None, None)

        dx, dy = fc[0]-icx, fc[1]-icy
        d       = np.hypot(dx, dy)
        w       = np.exp(-0.5*(d/radius))
        nc      = (w*fc[0] + (1-w)*icx, w*fc[1] + (1-w)*icy)

        return nc, fc, sigma, intensity, peak, bkgr

    def _postprocess_smooth(self, all_frames, all_coords, ints, fit_params, background, bg_fixed=None):
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
            return all_frames, all_coords, ints, fit_params, background

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
            else:
                fit_params[i]       = (None, None, None)
                ints[i]             = None
                background[i]       = None

        # new_centers = np.array([
        #     (fc[0], fc[1]) if fc is not None else (np.nan, np.nan)
        #     for fc, _, _ in fit_params
        # ], dtype=float)  # shape (N,2)
        # self.debugPlotRequested.emit(
        #     spot_centers.tolist(),
        #     smooth_centers.tolist(),
        #     new_centers.tolist()
        # )

        return all_frames, all_coords, all_coords, ints, fit_params, background

    def _coloc_flags_for_frame(self, frame, center):
        """
        Returns a tuple (any_flag, {ch:flag, ...}) for a single frame & ref‐center.
        Flags are "Yes"/"No"/None.
        """
        ref_ch = self.analysis_channel
        n_chan = self.movie.shape[self._channel_axis]

        # initialize
        flags_by_ch = {}
        any_flag = None

        if center is None:
            # no fit → all None
            return None, {ch: None for ch in range(1, n_chan+1) if ch != ref_ch}

        x0,y0 = center
        per_ch = {}
        for tgt_ch in range(1, n_chan+1):
            if tgt_ch == ref_ch:
                continue
            img = self.get_movie_frame(frame, channel_override=tgt_ch)
            ok = False
            if img is not None:
                fc2, *_ = perform_gaussian_fit(
                    img, (x0,y0),
                    crop_size=int(2*self.searchWindowSpin.value()),
                    pixelsize=self.pixel_size,
                    bg_fixed=None
                )
                if fc2 is not None and np.hypot(fc2[0]-x0, fc2[1]-y0) <= self.colocalization_threshold:
                    ok = True
            per_ch[tgt_ch] = "Yes" if ok else "No"

        # overall any
        any_flag = "Yes" if any(v=="Yes" for v in per_ch.values()) else "No"
        return any_flag, per_ch
    
    def _compute_colocalization(self, showprogress=True):
        frames      = self.analysis_frames
        centers     = [fp[0] for fp in self.analysis_fit_params]
        ref_ch      = self.analysis_channel
        n_chan      = self.movie.shape[self._channel_axis]
        N           = len(frames)

        # storage
        any_list      = [None]*N
        results_by_ch = {ch: [None]*N for ch in range(1, n_chan+1) if ch != ref_ch}

        progress = None
        if showprogress and N > 20:
            progress = QProgressDialog("Checking colocalization…", "Cancel", 0, N, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.show()

        for i, (frame, center) in enumerate(zip(frames, centers)):
            if progress:
                progress.setValue(i)
                QApplication.processEvents()
                if progress.wasCanceled():
                    break

            any_flag, per_ch = self._coloc_flags_for_frame(frame, center)
            any_list[i] = any_flag
            for ch, flag in per_ch.items():
                results_by_ch[ch][i] = flag

        if progress:
            progress.setValue(N)
            progress.close()

        self.analysis_colocalized       = any_list
        self.analysis_colocalized_by_ch = results_by_ch

    def _compute_colocalization_for_row(self, row: int):
        traj = self.trajectoryCanvas.trajectories[row]

        # 1) stash old context
        old_frames  = self.analysis_frames
        old_params  = self.analysis_fit_params
        old_channel = self.analysis_channel

        # 2) point “analysis_*” at this trajectory
        self.analysis_frames     = traj["frames"]
        self.analysis_fit_params = list(zip(
            traj["spot_centers"],
            traj["sigmas"],
            traj["peaks"]
        ))
        self.analysis_channel    = traj["channel"]

        # 3) compute all flags
        self._compute_colocalization(showprogress=True)

        # 4) **store the raw lists** on the traj dict
        traj["colocalization_any"]       = list(self.analysis_colocalized)
        traj["colocalization_by_ch"]     = {
            ch: list(flags)
            for ch, flags in self.analysis_colocalized_by_ch.items()
        }

        # 5) now write percentages back into custom_fields & table
        cf      = traj.setdefault("custom_fields", {})
        n_chan  = self.movie.shape[self._channel_axis]
        valid_any = [f for f in self.analysis_colocalized if f is not None]
        pct_any   = (
            f"{100*sum(1 for f in valid_any if f=='Yes')/len(valid_any):.1f}"
            if valid_any else ""
        )

        for ch in range(1, n_chan+1):
            col = f"Ch. {ch} co. %"
            if ch == self.analysis_channel:
                cf[col] = ""
            elif n_chan == 2:
                cf[col] = pct_any
            else:
                flags = self.analysis_colocalized_by_ch.get(ch, [])
                valid = [f for f in flags if f is not None]
                cf[col] = (
                    f"{100*sum(1 for f in valid if f=='Yes')/len(valid):.1f}"
                    if valid else ""
                )
            self.trajectoryCanvas._mark_custom(row, col, cf[col])

        # 6) restore old context
        self.analysis_frames, self.analysis_fit_params, self.analysis_channel = (
            old_frames, old_params, old_channel
        )

    def _remove_past_centers(self, centers_to_remove):
        """
        Remove any (pf,px,py) in self.past_centers that lies within
        self.same_spot_threshold of any (cf,cx,cy) in centers_to_remove *and* pf==cf.
        """
        if not centers_to_remove:
            return

        # 1) Build valid (frame,x,y) list
        valid = [
            (f, x, y)
            for f, x, y in centers_to_remove
            if isinstance(f, (int,float))
            and isinstance(x, (int,float))
            and isinstance(y, (int,float))
        ]
        if not valid:
            return

        # 2) Keep only those past-centers that are either a different frame
        #    or are farther than threshold on the same frame.
        kept = []
        for pf, px, py in self.past_centers:
            drop = False
            for cf, cx, cy in valid:
                if pf == cf and np.hypot(px - cx, py - cy) < self.same_spot_threshold:
                    drop = True
                    break
            if not drop:
                kept.append((pf, px, py))
        self.past_centers = kept

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
            self.jump_to_analysis_point(self.loop_index, animate="discrete")
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

        image, center, crop_size, sigma, intensity, background, peak, pointcolor = self.histogramCanvas._last_histogram_params
        self.histogramCanvas._do_update_histogram(image, center, crop_size, sigma, intensity, background, peak, pointcolor)

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

    def finalizeTrajectory(self, analysis_points, trajid=None):
        if not analysis_points or len(analysis_points) < 2:
            return
        
        if self.sumBtn.isChecked():
            self.sumBtn.setChecked(False)

        analysis_points.sort(key=lambda pt: pt[0])
        self.analysis_points = analysis_points
        self.analysis_start, self.analysis_end = analysis_points[0], analysis_points[-1]
        
        self.run_analysis_points()

        if self._is_canceled:
            self._is_canceled=False
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
        # # now plotmn
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

        is_roi = self.modeSwitch.isChecked()
        if is_roi:
            self.set_roi_mode(False)

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
        # self.cancel_left_click_sequence()
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
            self.histogramCanvas.update_histogram(frame_image, center_for_hist, search_crop_size, sigma=fitted_sigma, intensity=intensity, peak=peak)

        # Optionally, you can also add a magenta circle overlay on the MovieCanvas here.
        self.movieCanvas.remove_gaussian_circle()

        if fitted_center is not None and fitted_sigma is not None:
            self.movieCanvas.add_gaussian_circle(fitted_center, fitted_sigma)

        self.movieCanvas.draw_idle()

    def on_movie_click(self, event):
        if (
            event.button == 1
            and event.inaxes == self.movieCanvas.ax
            and self.traj_overlay_button.isChecked()
            and len(self.analysis_points) <= 1
        ):
            # Loop through all trajectory‐artists (annotations and scatter) that we stored
            for artist in getattr(self.movieCanvas, "movie_trajectory_markers", []):
                hit, info = artist.contains(event)
                if not hit:
                    continue

                # We clicked one of our annotations or scatter points.
                # First, stop any looping.
                if self.looping:
                    self.stoploop()

                self.cancel_left_click_sequence()

                # Grab the trajectory index from the artist
                traj_idx = getattr(artist, "traj_idx", None)
                if traj_idx is None:
                    continue

                # 1) If they clicked a new trajectory (different row), update table selection
                current_row = self.trajectoryCanvas.table_widget.currentRow()
                if traj_idx != current_row:
                    tbl = self.trajectoryCanvas.table_widget
                    tbl.blockSignals(True)
                    tbl.selectRow(traj_idx)
                    tbl.blockSignals(False)
                    # trigger whatever happens when a trajectory is selected:
                    self.trajectoryCanvas.on_trajectory_selected_by_index(traj_idx)

                # 2) If they clicked on a scatter‐dot (info["ind"] exists), jump to that point:
                #    info["ind"][0] is the index into traj["spot_centers"].
                point_idx = info.get("ind", [None])[0]
                if point_idx is not None:
                    self.jump_to_analysis_point(point_idx)
                    if self.sumBtn.isChecked():
                        self.sumBtn.setChecked(False)
                    self.intensityCanvas.current_index = point_idx
                    self.intensityCanvas.highlight_current_point()

                # Consume this click (don’t let it fall through).
                return
        if (
            event.button == 1
            and getattr(event, 'guiEvent', None) is not None
            and (event.guiEvent.modifiers() & Qt.MetaModifier)
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
                    self.kymoCanvas.manual_zoom = False
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
                self.kymoCanvas.remove_circle()

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
            self.movieCanvas.overlay_rectangle(x_click, y_click, search_crop_size)

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
                self.histogramCanvas.update_histogram(frame_image, center_to_use, search_crop_size, fitted_sigma, intensity=intensity, peak=peak)
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

        # Update the temporary dotted line connecting the left-click points.
        self.movieCanvas._manual_marker_active = False
        self.update_movie_analysis_line()

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

        points = sorted(self.analysis_points, key=lambda pt: pt[0])
        xs = [pt[1] for pt in points]
        ys = [pt[2] for pt in points]

        # If there’s already a temporary line, remove it
        if hasattr(self, "temp_movie_analysis_line") and self.temp_movie_analysis_line is not None:
            try:
                self.temp_movie_analysis_line.remove()
            except Exception:
                pass

        # 1) create a new dotted line
        self.temp_movie_analysis_line, = self.movieCanvas.ax.plot(
            xs, ys,
            color='#7da1ff', linewidth=1.5, linestyle='--'
        )

        # 2) Immediately draw it so the user sees it now
        canvas = self.movieCanvas.figure.canvas
        canvas.draw()                       # full redraw, shows the new line

        # 3) Store a “clean” background without the animated portion (if you still want to blit later)
        self.movieCanvas._bg = canvas.copy_from_bbox(self.movieCanvas.ax.bbox)

        # NB: NO draw_idle() here — we’ll blit in on_movie_motion

    def escape_left_click_sequence(self):
        self.cancel_left_click_sequence()
        self.movieCanvas.draw()
        self.kymoCanvas.draw()

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
            # Clear any x-markers (drawn with add_gaussian_circle)
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
        self.kymoCanvas.remove_circle()

        # Clear accumulated left-click points.
        self.analysis_points = []
        self.analysis_anchors = []
        self.analysis_roi = None
        # self.kymoCanvas.unsetCursor()

        # self.kymoCanvas.draw_idle()
        # self.movieCanvas.draw_idle()

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
                if self.flashchannel and requested_channel != int(self.movieChannelCombo.currentText()):
                    self.flash_message(f"Channel {requested_channel}")
                # This will emit currentIndexChanged → on_channel_changed(index)
                self.movieChannelCombo.setCurrentIndex(requested_channel - 1)

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

                # only use hover if both coordinates are numeric and within bounds
                valid_hv = (
                    isinstance(hv, (tuple, list))
                    and len(hv) == 2
                    and hv[0] is not None and hv[1] is not None
                    and xmin <= hv[0] <= xmax
                    and ymin <= hv[1] <= ymax
                )
                if valid_hv:
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
        if self.movie is None or self.movieCanvas.roiAddMode:
            return
        canvas = self.movieCanvas  # your FigureCanvasQTAgg

        # 1) figure out data‐space coords (xdata,ydata) via either manual marker or cursor
        if getattr(canvas, "_manual_marker_active", False):
            xdata, ydata = canvas._manual_marker_pos
            # we’ll still compute pixel coords from xdata,ydata below
        else:
            # Get the cursor’s global (screen) position, then map into widget‐space
            pos = canvas.mapFromGlobal(QtGui.QCursor.pos())
            x_w, y_w = pos.x(), pos.y()  # in logical points

            # 2) convert from logical points → device (physical) pixels
            dpr = canvas.devicePixelRatioF()  # usually 2.0 on Retina
            x_phys = x_w * dpr
            # Flip Y: Qt’s (0,0) is top‐left in points, Matplotlib’s (0,0) is bottom‐left in pixels
            height_pts = canvas.height()
            height_phys = height_pts * dpr
            y_phys = height_phys - (y_w * dpr)

            # 3) invert from display (pixels) → data (xdata, ydata)
            xdata, ydata = canvas.ax.transData.inverted().transform((x_phys, y_phys))

        # 4) build a fake event that has both x/y (pixels) and xdata/ydata
        evt = type("Evt", (), {})()
        evt.xdata    = xdata
        evt.ydata    = ydata

        # If we came via manual_marker, compute physical pixels similarly:
        if getattr(canvas, "_manual_marker_active", False):
            # Transform (xdata,ydata) → display‐pixel coords
            x_phys, y_phys = canvas.ax.transData.transform((xdata, ydata))
        evt.x = x_phys
        evt.y = y_phys

        evt.button   = 1
        evt.dblclick = False
        evt.inaxes   = canvas.ax
        evt.guiEvent = None

        # 5) now calling artist.contains(evt) will see the correct pixel coords
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

        # initialize the “last” flag on first call
        if not hasattr(self, "_last_table_has_rows"):
            self._last_table_has_rows = not has_rows  # force an update on first run

        if adjust_splitter and has_rows != self._last_table_has_rows:
            total_height = self.rightVerticalSplitter.height()
            if not has_rows:
                # hide table
                self.rightVerticalSplitter.setSizes([total_height, 0])
                self.mainSplitter.handle_y_offset_pct = 0.4955
            else:
                # show table
                self.rightVerticalSplitter.setSizes(
                    [int(0.75 * total_height), int(0.25 * total_height)]
                )
                self.mainSplitter.handle_y_offset_pct = 0.1

        # store for next call
        self._last_table_has_rows = has_rows

        # now update the buttons & columns as before
        self.traj_overlay_button.setVisible(has_rows)
        self.delete_button.setVisible(has_rows)
        self.clear_button.setVisible(has_rows)
        self.trajectoryCanvas.hide_empty_columns()

    # def eventFilter(self, obj, event):
    #     # intercept wheel events when our radius dialog is up
    #     if (self._radiusDialog is not None 
    #             and self._radiusDialog.isVisible() 
    #             and event.type() == QEvent.Wheel):
    #         # up/down?
    #         delta = event.angleDelta().y()
    #         step  = self.searchWindowSpin.singleStep()
    #         cur   = self.searchWindowSpin.value()
    #         if delta > 0:
    #             self.searchWindowSpin.setValue(cur + step)
    #         else:
    #             self.searchWindowSpin.setValue(cur - step)
    #         return True    # eat it
    #     return super().eventFilter(obj, event)

    def eventFilter(self, obj, ev):

        if obj is self._ch_overlay and ev.type() == ev.Show:  
            self._reposition_legend()
        if obj is self.movieDisplayContainer and ev.type() in (ev.Resize, ev.Move):
            self._reposition_legend()

        return super().eventFilter(obj, ev)

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
            current_channel = int(self.movieChannelCombo.currentText())
        except Exception:
            current_channel = 1

        if self.movieCanvas.sum_mode:
            self.channel_sum_contrast_settings[current_channel] = {
                'vmin': new_vmin,
                'vmax': new_vmax,
                'extended_min': new_extended_min,
                'extended_max': new_extended_max
            }
        else:
            self.channel_contrast_settings[current_channel] = {
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


    def reset_kymo_contrast(self):
        image = self.kymoCanvas.image
        if image is None:
            #print("No movie loaded; cannot reset contrast.")
            return
        p15, p99 = np.percentile(image, (15, 99))
        
        new_vmin, new_vmax = int(p15), int(p99 * 1.1)
            
        delta = new_vmax - new_vmin
        new_extended_min = new_vmin - int(0.7 * delta)
        new_extended_max = new_vmax + int(1.4 * delta)
        
        # Update the slider.
        self.kymocontrastControlsWidget.contrastRangeSlider.blockSignals(True)
        self.kymocontrastControlsWidget.contrastRangeSlider.setMinimum(new_extended_min)
        self.kymocontrastControlsWidget.contrastRangeSlider.setMaximum(new_extended_max)
        self.kymocontrastControlsWidget.contrastRangeSlider.setRangeValues(new_vmin, new_vmax)
        self.kymocontrastControlsWidget.contrastRangeSlider.blockSignals(False)
        self.kymocontrastControlsWidget.contrastRangeSlider.update()
        
        # self.channel_contrast_settings[current_channel] = {
        #     'vmin': new_vmin,
        #     'vmax': new_vmax,
        #     'extended_min': new_extended_min,
        #     'extended_max': new_extended_max
        # }
                
        # # **New Step:** Update internal contrast attributes:
        # self.kymoCanvas._default_vmin = new_vmin
        # self.kymoCanvas._default_vmax = new_vmax
        # self.kymoCanvas._vmin = new_vmin
        # self.kymoCanvas._vmax = new_vmax
        
        # Finally, redraw (using display_image or by updating the colormap limits).
        # If you have a method to apply these values, you could also do:
        self.kymoCanvas._im.set_clim(new_vmin, new_vmax)
        self.kymoCanvas.draw_idle()

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


            self.movieCanvas.display_sum_frame()  # This method should be modified if necessary so that it doesn't call update_view()

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

        # clear any existing selection
        tw = self.trajectoryCanvas.table_widget
        tw.clearSelection()
        tw.setCurrentCell(-1, -1)

        # 2) ask user where/how to save
        all_items = list(self.kymographs.items())
        base_name = os.path.splitext(self.movieNameLabel.text())[0]
        dlg       = SaveKymographDialog(base_name, all_items, parent=self)
        if dlg.exec_() != QDialog.Accepted:
            return
        opts       = dlg.getOptions()
        directory  = opts["directory"]
        sel_names  = opts["selected"]
        ft         = opts["filetype"]
        do_overlay = opts["overlay"]
        use_pref   = opts.get("use_prefix", False)
        mid        = opts.get("middle", "")
        custom     = opts.get("custom", False)
        cname      = opts.get("custom_name", "")

        # 3) progress bar
        total = len(sel_names)
        prog  = QProgressDialog("Saving kymographs…", "Cancel", 0, total, self)
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.show()

        tw = self.trajectoryCanvas.table_widget
        tw.clearSelection()
        tw.setCurrentCell(-1, -1)

        # remember the current kymo so we can re-select it at the end
        current = self.kymoCombo.currentText()

        # cache figure size for high-DPI save
        fig = self.kymoCanvas.fig
        orig_size = fig.get_size_inches().copy()

        try:
            for i, name in enumerate(sel_names):
                if prog.wasCanceled():
                    break
                prog.setValue(i)

                # build filename
                if custom:
                    fname = cname or name
                else:
                    parts = ([base_name] if use_pref else []) + ([mid] if mid else []) + [name]
                    fname = "-".join(parts)
                out_path = os.path.join(directory, f"{fname}.{ft}")

                if do_overlay:
                    print([name])
                    # 1) load & flip the raw kymo
                    kymo = np.flipud(self.kymographs[name])

                    # 2) ensure display_image does a full reset
                    self.kymoCanvas.manual_zoom = False
                    self.kymoCanvas.display_image(kymo)

                    # 3) switch ROI & channel
                    if name in self.kymographs:
                        self.kymoCombo.setCurrentText(name)
                        self.kymo_changed()

                    # 5) now draw a skinny overlay (axes already off, full‐frame)
                    self.kymoCanvas.draw_trajectories_on_kymo(
                        showsearchline=False,
                        skinny=True
                    )
                    self.kymoCanvas.fig.canvas.draw()

                    # 6) save
                    fig = self.kymoCanvas.fig
                    fig.set_size_inches(orig_size)
                    fig.savefig(out_path, dpi=300,
                                facecolor=fig.get_facecolor(),
                                edgecolor="none",
                                bbox_inches="tight")

                else:
                    # plain export
                    kymo = self.kymographs[name]
                    if ft == "tif":
                        tifffile.imsave(out_path, kymo)
                    else:
                        p15, p99 = np.percentile(kymo, (15, 99))
                        disp     = np.clip((kymo - p15)/(p99 - p15), 0, 1)
                        disp     = (disp*255).astype(np.uint8)
                        cmap     = "gray_r" if getattr(self, "inverted_cmap", False) else "gray"
                        disp     = np.flipud(disp)
                        plt.imsave(out_path, disp, cmap=cmap, origin="lower")

            prog.setValue(total)

        finally:
            prog.close()
            # just re-select the original kymo; that will reset ROI, channel, contrast, overlays, etc.
            if current in self.kymographs:
                self.kymoCombo.setCurrentText(current)
                self.kymo_changed()

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
                                "Please click a stationary spot that can be found in all frames first.")
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
            # static file-picker; parent is 'dialog'
            fname, _ = QFileDialog.getSaveFileName(
                dialog,
                "Save Drift-Corrected Movie",
                "",
                "TIFF Files (*.tif *.tiff)"
            )
            if not fname:
                # user clicked Cancel in the file-chooser → do nothing
                return False
            try:
                # actually write it out
                tifffile.imwrite(
                    fname,
                    np.array(corrected_frames),
                    imagej=True,
                    metadata=getattr(self, "movie_metadata", {})
                )
                saved_file["path"] = fname
                return True
            except Exception as e:
                QMessageBox.critical(dialog, "Save Error", f"Error saving movie:\n{e}")
                return False

        def save_and_load_movie():
            # bail out if the user canceled (or if save_movie hit an error)
            if not save_movie():
                return

            # at this point, saved_file["path"] must be set
            try:
                self.save_and_load_routine = True
                self.handle_movie_load(
                    saved_file["path"],
                    pixelsize=self.pixel_size,
                    frameinterval=self.frame_interval
                )
                QMessageBox.information(
                    dialog, "Loaded",
                    "The corrected movie has been loaded into the main window."
                )
                self.zoomInsetFrame.setVisible(False)
            except Exception as e:
                QMessageBox.critical(dialog, "Load Error", f"Error loading movie:\n{e}")
                return

            # only now close the corrected-movie popup
            dialog.accept()

        def cancel():
            dialog.accept()

        def on_save_clicked():
            if save_movie():
                # only close the corrected‐movie popup if we actually saved
                dialog.accept()

        btn_save.clicked.connect(on_save_clicked)
        btn_save_load.clicked.connect(save_and_load_movie)
        btn_cancel.clicked.connect(dialog.reject)

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
                    callback    = lambda _chk=False, r=row, c=col: \
                                self.trajectoryCanvas._unmark_custom(r, c)
                else:
                    action_text = f"Mark as {col}"
                    callback    = lambda _chk=False, r=row, c=col: \
                                self.trajectoryCanvas._mark_custom(r, c)
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

        self._update_legends()
        self.kymoCanvas.draw_trajectories_on_kymo()
        self.kymoCanvas.draw_idle()

    def on_connect_spot_gaps_toggled(self, checked: bool):
        # store it on the navigator (that's what your kymo‐drawing code reads)
        self.connect_all_spots = checked
        # then force a redraw
        self.kymoCanvas.draw_trajectories_on_kymo()
        self.kymoCanvas.draw_idle()

    def on_toggle_log_filter(self, checked: bool):
        self.applylogfilter = checked

    def on_colocalization_toggled(self, checked: bool):
        if self.movie.ndim != 4:
            return

        self.check_colocalization = checked
        if not checked:
            return
        
        orig_channel = getattr(self, "analysis_channel", None)

        # Figure out how many channels and the custom‐field names
        n_chan    = self.movie.shape[self._channel_axis]
        coloc_cols = [f"Ch. {ch} co. %" for ch in range(1, n_chan+1)]
        missing   = []

        # Find all trajectories that have at least one missing co‐% field
        for r, traj in enumerate(self.trajectoryCanvas.trajectories):
            ch_ref = traj["channel"]
            cf     = traj.get("custom_fields", {})
            for col in coloc_cols:
                # skip the reference‐channel column entirely
                if col.endswith(f"{ch_ref} co. %"):
                    continue
                if not cf.get(col, "").strip():
                    missing.append(r)
                    break

        if not missing:
            return

        # Ask user
        cnt = len(missing)
        msg = f"{cnt} trajector{'ies are' if cnt>1 else 'y is'} missing colocalization data, calculate {'them' if cnt>1 else 'it'}?"
        yn  = QMessageBox.question(self, "Compute Colocalization", msg,
                                QMessageBox.Yes|QMessageBox.No,
                                QMessageBox.Yes)
        if yn != QMessageBox.Yes:
            return

        progress = QProgressDialog("Computing colocalization…", "Cancel", 0, len(missing), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        for idx, r in enumerate(missing):
            progress.setValue(idx)
            QApplication.processEvents()
            if progress.wasCanceled():
                break

            traj = self.trajectoryCanvas.trajectories[r]
            ch_ref = traj["channel"]
            n_chan = self.movie.shape[self._channel_axis]

            # 1) set up frames & fit params as before
            self.analysis_frames     = traj["frames"]
            self.analysis_fit_params = list(zip(
                traj["spot_centers"],
                traj["sigmas"],
                traj["peaks"]
            ))

            # 2) run exactly one colocalization pass in the ref channel
            ch_ref = traj["channel"]
            self.analysis_channel = ch_ref
            self._compute_colocalization(showprogress=False)

            # 3) grab the “any” list and the per‐channel dict
            any_flags = list(self.analysis_colocalized)
            by_ch     = {
                tgt: list(flags)
                for tgt, flags in self.analysis_colocalized_by_ch.items()
            }

            traj["colocalization_any"]    = any_flags
            traj["colocalization_by_ch"]  = by_ch

            # 4) write them into custom_fields exactly as you already do
            cf = traj.setdefault("custom_fields", {})
            # overall‐any (only meaningful for two-channel movies)
            valid_any = [s for s in any_flags if s is not None]
            pct_any   = (f"{100*sum(1 for s in valid_any if s=='Yes')/len(valid_any):.1f}"
                        if valid_any else "")

            for ch in range(1, n_chan+1):
                col_name = f"Ch. {ch} co. %"
                if ch == ch_ref:
                    cf[col_name] = ""
                elif n_chan == 2:
                    cf[col_name] = pct_any
                else:
                    flags = by_ch.get(ch, [])
                    valid = [s for s in flags if s is not None]
                    cf[col_name] = (
                        f"{100*sum(1 for s in valid if s=='Yes')/len(valid):.1f}"
                        if valid else ""
                    )

            # now write them back into the table
            for ch in range(1, n_chan+1):
                self.trajectoryCanvas._mark_custom(r, f"Ch. {ch} co. %", cf[f"Ch. {ch} co. %"])

        progress.setValue(len(missing))
        progress.close()

        if orig_channel is not None:
            self.analysis_channel = orig_channel

        # finally redraw
        self.kymoCanvas.draw_trajectories_on_kymo()
        self.kymoCanvas.draw()
        self.movieCanvas.draw_trajectories_on_movie()
        self.movieCanvas.draw()

        self.trajectoryCanvas.hide_empty_columns()

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

    def _get_traj_colors(self, traj):
        """
        Decide how to color one trajectory.  
        Returns a dict of kwargs for scatter() and a single line_color.
        If no color_by is set, uses traj['colors'] as the scatter 'c' argument.
        """
        col = self.color_by_column
        all_trajs = self.trajectoryCanvas.trajectories

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
            main = "#AA80FF"
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
            main = "#AA80FF"
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

        # y position can stay where you like relative to top of container
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
                if w: w.setParent(None)

        # detect channel count & color_mode/target
        n_chan = (self.movie.shape[self._channel_axis]
                  if (self.movie.ndim == 4 and
                      self._channel_axis is not None)
                  else 1)
        col = self.color_by_column
        if n_chan == 2 and col == "colocalization":
            mode, tgt = "coloc", None
        elif n_chan > 2 and col and col.startswith("coloc_ch"):
            mode, tgt = "coloc_multi", int(col.split("coloc_ch",1)[1])
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
                    sw.setFixedSize(12,12)
                    sw.setStyleSheet(f"background-color:{sw_color};"
                                     "border:1px solid #333;"
                                     "border-radius:2px")
                    lbl = QLabel(label, widget)
                    lbl.setStyleSheet("color:#222;font-size:14px;"
                                      "background: transparent;")
                    layout.addWidget(sw, 0, Qt.AlignVCenter)
                    layout.addWidget(lbl,0, Qt.AlignVCenter)

            # show & adjust both
            for widget in (self.kymoLegendWidget,
                           self.movieLegendWidget):
                widget.show()
                widget.adjustSize()
        else:
            self.movieLegendWidget.hide()
            self.kymoLegendWidget.hide()
            
    def on_show_steps_toggled(self, checked: bool):
        self.show_steps = checked

        if not checked:
            self._refresh_intensity_canvas()
            return

        # 0) detect whether any trajectory exists at all
        has_any_trajectory = bool(self.trajectoryCanvas.trajectories)

        # 1) pop the SETTINGS dialog, passing that flag
        dlg = StepSettingsDialog(
            current_W=self.W,
            current_min_step=self.min_step,
            can_calculate_all=has_any_trajectory,
            parent=self
        )
        if dlg.exec_() != QDialog.Accepted:
            # cancelled → undo toggle
            self.show_steps = False
            if isinstance(self.sender(), QAction):
                self.sender().setChecked(False)
            return

        # 2) apply new parameters
        self.W        = dlg.new_W
        self.min_step = dlg.new_min_step

        # 3) if they chose “Set and Calculate”, recompute *all* trajectories
        if dlg.calculate_all:
            all_idxs = range(len(self.trajectoryCanvas.trajectories))
            progress = QProgressDialog("Computing steps…", "Cancel", 0, len(self.trajectoryCanvas.trajectories), self)
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.show()

            for i, traj_idx in enumerate(all_idxs):
                progress.setValue(i)
                QApplication.processEvents()
                if progress.wasCanceled():
                    break
                self._compute_steps_for_trajectory(traj_idx)

            progress.setValue(len(self.trajectoryCanvas.trajectories))
            progress.close()

        # 4) finally redraw (with or without steps)
        self._refresh_intensity_canvas()

    def _refresh_intensity_canvas(self):
        """
        Re‐draw whatever trajectory is currently selected in the IntensityCanvas.
        """
        idx = self.trajectoryCanvas.current_index
        if idx is None or idx < 0 or idx >= len(self.trajectoryCanvas.trajectories):
            return

        traj = self.trajectoryCanvas.trajectories[idx]
        frames = traj["frames"]
        intensities = traj["intensities"]
        colors = self._get_traj_colors(traj)[0]
        avg_int = None
        med_int = None

        # If you normally pass avg/median or max_frame, do so here:
        self.intensityCanvas.plot_intensity(
            frames=frames,
            intensities=intensities,
            avg_intensity=avg_int,
            median_intensity=med_int,
            colors=colors,
            max_frame=None
        )

    def _compute_steps_for_trajectory(self, traj_idx: int):
        """
        Look up trajectory #traj_idx (which lives in self.trajectoryCanvas.trajectories),
        pull out its frames/intensities, call compute_steps_for_data(...), then store
        the results back onto traj["step_indices"] and traj["step_medians"].
        """
        traj = self.trajectoryCanvas.trajectories[traj_idx]
        frames      = traj["frames"]
        intensities = traj["intensities"]
        # assume self.W, self.passes, self.min_step exist:
        step_idxs, medians = self.compute_steps_for_data(frames, intensities)
        traj["step_indices"] = step_idxs        # now a List[int], not None
        traj["step_medians"] = medians          # now a List[(start,end,median)]

    def compute_steps_for_data(self, frames, intensities):
        """
        Given a list of frame‐indices and a list of (possibly‐gapped) intensities,
        return (step_indices, step_medians).  Neither argument is modified.
        """

        W = self.W
        passes = self.passes
        min_step = self.min_step

        frame_arr = np.array(frames, dtype=int)
        intensity_arr = np.array(
            [np.nan if (v is None or (isinstance(v, float) and np.isnan(v))) else float(v)
            for v in intensities],
            dtype=float
        )
        valid_mask   = ~np.isnan(intensity_arr)
        valid_frames = frame_arr[valid_mask]
        valid_ints   = intensity_arr[valid_mask]

        # If too few points, return empty lists:
        if valid_ints.size < 2:
            return [], []

        fx = filterX(valid_ints, W=W, passes=passes)
        I_smooth = fx["I"]
        P        = fx["Px"]

        # find local minima/maxima in P
        min_idx = find_minima(P)
        max_idx = find_maxima(P)

        M = P.size
        Pmin = np.zeros(M, dtype=float)
        Pmax = np.zeros(M, dtype=float)
        if min_idx.size > 0:
            Pmin[min_idx] = P[min_idx]
        if max_idx.size > 0:
            Pmax[max_idx] = P[max_idx]
        Pedge = Pmin + Pmax

        # threshold:
        thresh = min_step
        step_compact_idxs = np.where(np.abs(Pedge) > thresh)[0]
        step_frames = sorted({int(valid_frames[j]) for j in step_compact_idxs})

        first = int(valid_frames[0])
        step_frames = [f for f in step_frames if f != first] #remove edge artefact

        # build segment boundaries from first→steps→last
        first_valid = int(valid_frames[0])
        last_valid  = int(valid_frames[-1])
        if step_frames:
            boundaries = [first_valid] + [f for f in step_frames if f != first_valid]
            if boundaries[-1] != last_valid:
                boundaries.append(last_valid)
        else:
            boundaries = [first_valid, last_valid]

        seg_medians = []
        for i in range(len(boundaries) - 1):
            start_f = boundaries[i]
            end_f   = boundaries[i+1]
            mask = (valid_frames >= start_f) & (valid_frames <= end_f)
            if not np.any(mask):
                continue
            vals = I_smooth[mask]
            if vals.size == 0:
                continue
            med = float(np.median(vals))
            seg_medians.append((start_f, end_f, med))

        return step_frames, seg_medians