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
import math
from scipy.ndimage import shift, gaussian_filter
from scipy.signal import savgol_filter
import zipfile
import time
import read_roi
import numpy as np
from functools import partial
import pandas as pd
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
from ..tools.classification import MotionClassificationConfig, classify_motion_states

# from hmmlearn.hmm import GaussianHMM
# from sklearn.preprocessing import StandardScaler

QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
