from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QComboBox, QSpinBox, QSplitter,
    QAbstractButton, QFrame, QDialogButtonBox,
    QScrollArea, QGridLayout,
    QLineEdit, QFormLayout, QSplitterHandle, QDialog,
    QApplication, QCheckBox, QListWidget, QFileDialog,
    QListWidgetItem, QAbstractItemView, QStackedWidget
)

from PyQt5 import QtCore, QtWidgets, QtGui
from PyQt5.QtCore import (
    Qt, QObject, pyqtSignal, QSize, QRectF, pyqtProperty, QPropertyAnimation,
    QEasingCurve, QObject, QPropertyAnimation, pyqtSlot, QEvent, QRect, QPointF,
    QTimer, QPoint, QElapsedTimer
    )
from PyQt5.QtGui import (
    QPainter, QColor, QFont, QDoubleValidator, QPen, QBrush, QFontMetrics, QPainterPath,
    QCursor, QPolygonF
    )

import importlib
import numpy as np
import os
import types

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


scipy = _LazyModule("scipy")
map_coordinates = _lazy_map_coordinates

def subpixel_crop(image, x1, x2, y1, y2, output_shape):
    # Create a grid in the output coordinate system.
    out_y, out_x = np.indices(output_shape, dtype=np.float64)
    # Map the output coordinates back to the input coordinate system.
    # For x: from 0 to output_shape[1]-1 should map to [x1, x2]
    # For y: from 0 to output_shape[0]-1 should map to [y1, y2]
    in_x = x1 + (x2 - x1) * (out_x / (output_shape[1] - 1))
    in_y = y1 + (y2 - y1) * (out_y / (output_shape[0] - 1))
    # Sample the input image at these coordinates.
    return map_coordinates(image, [in_y, in_x], order=1, mode='nearest')
