import numpy as np
import os
import pandas as pd
from PyQt5.QtCore import Qt, QTimer, QThread, QEvent
from PyQt5.QtWidgets import (QVBoxLayout, QApplication, QDialog,
                             QWidget, QFileDialog, QMessageBox, QTableWidget,
                             QTableWidgetItem, QMessageBox, QProgressDialog,
                             QHeaderView, QMenu, QInputDialog, QLineEdit, QLabel)
from PyQt5.QtGui import QPainter,QMouseEvent
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from matplotlib.patches import Circle, Rectangle
from matplotlib.ticker import MaxNLocator
from matplotlib.lines import Line2D
from matplotlib.text import Text
import matplotlib.cm as cm
import matplotlib.colors as mcolors
from matplotlib.gridspec import GridSpec
from matplotlib.transforms import Bbox
import scipy
from scipy.ndimage import map_coordinates, gaussian_laplace
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
