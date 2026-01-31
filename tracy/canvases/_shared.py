import importlib
import numpy as np
import os
import types
from PyQt5.QtCore import Qt, QTimer, QThread, QEvent
from PyQt5.QtWidgets import (QVBoxLayout, QApplication, QDialog,
                             QWidget, QFileDialog, QMessageBox, QTableWidget,
                             QTableWidgetItem, QMessageBox, QProgressDialog,
                             QHeaderView, QMenu, QInputDialog, QLineEdit, QLabel,
                             QFrame, QSizePolicy)
from PyQt5.QtGui import QPainter,QMouseEvent
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.patches import Circle, Rectangle
from matplotlib.ticker import MaxNLocator
from matplotlib.lines import Line2D
from matplotlib.collections import LineCollection
from matplotlib.text import Text
import matplotlib.cm as cm
import matplotlib.colors as mcolors
from matplotlib.gridspec import GridSpec
from matplotlib.transforms import Bbox
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


def _lazy_map_coordinates(*args, **kwargs):
    from scipy.ndimage import map_coordinates as _map_coordinates
    return _map_coordinates(*args, **kwargs)


def _lazy_gaussian_laplace(*args, **kwargs):
    from scipy.ndimage import gaussian_laplace as _gaussian_laplace
    return _gaussian_laplace(*args, **kwargs)


pd = _LazyModule("pandas")
scipy = _LazyModule("scipy")
map_coordinates = _lazy_map_coordinates
gaussian_laplace = _lazy_gaussian_laplace
import time
import copy
import math
import json
import warnings
import re
from typing import Optional, List
from ..tools.roi_tools import is_point_near_roi, compute_roi_point
from ..canvas_tools import RecalcDialog, RecalcWorker, RecalcAllWorker, subpixel_crop
from ..tools.gaussian_tools import filterX, find_minima, find_maxima
# from .kymotrace import prune_skeleton, overlay_trace_centers, extract_main_path

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
    message=".*divide by zero encountered in scalar divide.*",
    category=RuntimeWarning,
    module="matplotlib\\.layout_engine",
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
