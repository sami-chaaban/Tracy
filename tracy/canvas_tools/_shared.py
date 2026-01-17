from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QLabel, QComboBox, QSpinBox, QSplitter,
    QAbstractButton, QFrame, QDialogButtonBox,
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

import numpy as np
import os
from scipy.ndimage import map_coordinates

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
