import os, sys
import importlib
import types
import numpy as np
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QFileDialog, QLabel, QMessageBox, QComboBox, QSpinBox, QShortcut,
    QListView, QSlider, QSizePolicy, QAction, QDialog,
    QProgressDialog, QApplication, QFrame,
    QLineEdit, QFormLayout, QGraphicsDropShadowEffect, QDialogButtonBox,
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
import matplotlib.patheffects as pe
import matplotlib.cm as cm
import matplotlib.colors as mcolors
import math
import zipfile
import time
import numpy as np
from functools import partial
class _LazyModule(types.ModuleType):
    def __init__(self, name):
        super().__init__(name)
        self.__dict__["_lazy_name"] = name
        self.__dict__["_lazy_module"] = None

    def _load(self):
        module = self.__dict__.get("_lazy_module")
        if module is None:
            module = importlib.import_module(self.__dict__["_lazy_name"])
            self.__dict__["_lazy_module"] = module
            self.__dict__.update(module.__dict__)
        return module

    def __getattr__(self, item):
        return getattr(self._load(), item)

    def __dir__(self):
        return dir(self._load())


def _lazy_shift(*args, **kwargs):
    from scipy.ndimage import shift as _shift
    return _shift(*args, **kwargs)


def _lazy_gaussian_filter(*args, **kwargs):
    from scipy.ndimage import gaussian_filter as _gaussian_filter
    return _gaussian_filter(*args, **kwargs)


def _lazy_savgol_filter(*args, **kwargs):
    from scipy.signal import savgol_filter as _savgol_filter
    return _savgol_filter(*args, **kwargs)


shift = _lazy_shift
gaussian_filter = _lazy_gaussian_filter
savgol_filter = _lazy_savgol_filter

pd = _LazyModule("pandas")
tifffile = _LazyModule("tifffile")
read_roi = _LazyModule("read_roi")
from pathlib import Path
from ..canvases import (
    KymoCanvas, MovieCanvas,
    IntensityCanvas, TrajectoryCanvas, HistogramCanvas,
    VelocityCanvas
    )
from ..canvas_tools import (
    ContrastControlsWidget, ToggleSwitch,
    ChannelAxisDialog, SetScaleDialog,
    KymoLineOptionsDialog, CustomSplitter, 
    RoundedFrame, AxesRectAnimator, SaveKymographDialog,
    ClickableLabel, RadiusDialog, BubbleTipFilter,
    CenteredBubbleFilter, AnimatedIconButton,
    StepSettingsDialog, KymoContrastControlsWidget,
    DiffusionSettingsDialog, ShortcutsDialog
)
from tracy import __version__
from ..tools.gaussian_tools import perform_gaussian_fit, filterX, find_minima, find_maxima
from ..tools.roi_tools import is_point_near_roi, convert_roi_to_binary, parse_roi_blob, generate_multipoint_roi_bytes
from ..tools.track_tools import calculate_velocities

# from hmmlearn.hmm import GaussianHMM
# from sklearn.preprocessing import StandardScaler
