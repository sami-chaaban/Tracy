from ._shared import *
from .. import __version__
from .ui import NavigatorUiMixin
from .io import NavigatorIOMixin
from .kymo import NavigatorKymoMixin
from .movie import NavigatorMovieMixin
from .analysis import NavigatorAnalysisMixin
from .roi import NavigatorRoiMixin
from .input import NavigatorInputMixin
from .colors import NavigatorColorMixin


class KymographNavigator(
    NavigatorUiMixin,
    NavigatorIOMixin,
    NavigatorKymoMixin,
    NavigatorMovieMixin,
    NavigatorAnalysisMixin,
    NavigatorRoiMixin,
    NavigatorInputMixin,
    NavigatorColorMixin,
    QMainWindow,
):
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
        self.kymographs_log = {}
        self.kymo_log_contrast_settings = {}
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
        QTimer.singleShot(1500, self._schedule_update_check)

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

        self.categorize_diffusion = True
        self._EPS = 1e-12

        # diffusion toggle + settings
        self.show_diffusion = False
        self.diffusion_max_lag = 10     # lags used for MSD fit (1..max_lag)
        self.diffusion_min_pairs = 5    # minimum pairs per lag to accept that lag

        # optional: keep latest-analysis values, like steps
        self.analysis_diffusion_D = None
        self.analysis_diffusion_alpha = None

        # column names (use consistent headers for save/load)
        self._DIFF_D_COL = "D (μm²/s)"
        self._DIFF_A_COL = "α"

        self.connect_all_spots = False

        self.color_by_column = None

        self.flashchannel = True

        self.show_steps=False
        self.min_step=100
        self.W=15
        self.passes=10

        self.motion_colours = {
            "ambiguous":  "grey",
            "paused":     "#EA4343",   # yellow-ish
            "diffusive":  "#E2D138",   # green-ish
            "processive": "#4AC32C",   # red-ish
        }
