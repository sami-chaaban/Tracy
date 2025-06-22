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

class RangeSlider(QtWidgets.QSlider):

    # Signals to notify when lower or upper value changes.
    lowerValueChanged = pyqtSignal(int)
    upperValueChanged = pyqtSignal(int)
    rangeChanged = pyqtSignal(int, int)

    def __init__(self, orientation=Qt.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setOrientation(orientation)
        # Set required attributes first.
        self._handleRadius = 10  # radius of the handle circle
        self._lower = self.minimum()
        self._upper = self.maximum()
        self._activeHandle = None  # Which handle is currently being dragged?
        # Now that _handleRadius is defined, we can set contents margins.
        hw = self._handleRadius
        border = 2  # same as your handle pen width
        self.setContentsMargins(hw + border,
                                hw,
                                hw + border,
                                hw)
        # Enable mouse tracking to update while dragging.
        self.setMouseTracking(True)

    def lowerValue(self):
        return self._lower

    def upperValue(self):
        return self._upper

    def setRangeValues(self, lower, upper):
        # Clamp both values
        lower = max(self.minimum(), min(lower, upper))
        upper = min(self.maximum(), max(upper, lower))
        self.blockSignals(True)
        self._lower = lower
        self._upper = upper
        self.blockSignals(False)
        self.rangeChanged.emit(self._lower, self._upper)
        self.update()

    def sizeHint(self):
        base = super().sizeHint()
        # Ensure the widget is tall enough for the full handle (diameter) plus some padding.
        desiredHeight = 2 * self._handleRadius + 4  
        return base.expandedTo(QtCore.QSize(base.width(), desiredHeight))
    
    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        full_rect = self.rect()
        radius = self._handleRadius

        # Compute the groove extents (space for the handles)
        opt = QtWidgets.QStyleOptionSlider()
        self.initStyleOption(opt)
        groove_rect = self.style().subControlRect(
            QtWidgets.QStyle.CC_Slider, opt, QtWidgets.QStyle.SC_SliderGroove, self
        )
        inner_margin = radius
        slider_min = groove_rect.x() + inner_margin
        slider_max = groove_rect.right() - inner_margin

        # Compute handle positions
        span = self.maximum() - self.minimum() or 1
        lower_pos = slider_min + (self._lower - self.minimum())/span * (slider_max - slider_min)
        upper_pos = slider_min + (self._upper - self.minimum())/span * (slider_max - slider_min)

        # Vertical center line
        line_y = full_rect.center().y()

        # 1) Draw the full-range background line (grey) with rounded caps
        grey_color = QtGui.QColor(208, 208, 208, 150)
        grey_pen   = QtGui.QPen(grey_color, 2, cap=Qt.RoundCap)
        painter.setPen(grey_pen)
        painter.drawLine(slider_min, line_y, slider_max, line_y)

        # 2) Draw the selected range (blue) on top
        blue_pen = QtGui.QPen(QtGui.QColor("#97b4ff"), 6, cap=Qt.RoundCap)
        painter.setPen(blue_pen)
        painter.drawLine(int(lower_pos), line_y, int(upper_pos), line_y)

        # 3) Draw the two circular handles
        handle_brush = QtGui.QBrush(QtGui.QColor("#ffffff"))
        handle_pen   = QtGui.QPen(QtGui.QColor("#d0d0d0"), 1.5)
        painter.setBrush(handle_brush)
        painter.setPen(handle_pen)
        for pos in (lower_pos, upper_pos):
            center = QtCore.QPointF(pos, line_y)
            painter.drawEllipse(center, radius, radius)

    def mousePressEvent(self, event):
        pos = event.pos()
        opt = QtWidgets.QStyleOptionSlider()
        self.initStyleOption(opt)
        groove_rect = self.style().subControlRect(
            QtWidgets.QStyle.CC_Slider, opt, QtWidgets.QStyle.SC_SliderGroove, self)
        margin = self._handleRadius
        adjusted_groove_rect = groove_rect.adjusted(margin, 0, -margin, 0)
        slider_min = adjusted_groove_rect.x()
        slider_max = adjusted_groove_rect.x() + adjusted_groove_rect.width()
        span = self.maximum() - self.minimum()
        lower_pos = slider_min + (self._lower - self.minimum()) / span * adjusted_groove_rect.width()
        upper_pos = slider_min + (self._upper - self.minimum()) / span * adjusted_groove_rect.width()

        # Check distance from click to each handle center.
        dist_to_lower = abs(pos.x() - lower_pos)
        dist_to_upper = abs(pos.x() - upper_pos)
        if dist_to_lower <= self._handleRadius:
            self._activeHandle = 'lower'
        elif dist_to_upper <= self._handleRadius:
            self._activeHandle = 'upper'
        else:
            self._activeHandle = None
        event.accept()

    def mouseMoveEvent(self, event):
        if self._activeHandle is None:
            return

        pos = event.pos()
        opt = QtWidgets.QStyleOptionSlider()
        self.initStyleOption(opt)
        groove_rect = self.style().subControlRect(
            QtWidgets.QStyle.CC_Slider, opt, QtWidgets.QStyle.SC_SliderGroove, self)
        margin = self._handleRadius
        adjusted_groove_rect = groove_rect.adjusted(margin, 0, -margin, 0)
        slider_min = adjusted_groove_rect.x()
        slider_max = adjusted_groove_rect.x() + adjusted_groove_rect.width()

        # Clamp position within the adjusted groove.
        x = max(slider_min, min(pos.x(), slider_max))
        # Convert x position back to a value.
        value = self.minimum() + ((x - slider_min) / adjusted_groove_rect.width()) * (self.maximum() - self.minimum())
        value = int(round(value))

        if self._activeHandle == 'lower':
            # Update lower value and keep current upper value
            self.setRangeValues(value, self._upper)
        elif self._activeHandle == 'upper':
            # Update upper value and keep current lower value
            self.setRangeValues(self._lower, value)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._activeHandle = None
        event.accept()

class ContrastControlsWidget(QWidget):
    def __init__(self, moviecanvas, parent=None):
        super().__init__(parent)
        self.moviecanvas = moviecanvas
        # Store slider settings for each mode, if needed.
        self.sliderSettings = {"norm": None, "sum": None}
        # Create the RangeSlider instance.
        self.contrastRangeSlider = RangeSlider(Qt.Horizontal, self)
        # Connect its rangeChanged signal.
        self.contrastRangeSlider.rangeChanged.connect(self.on_slider_range_changed)
        self.initUI()

    def initUI(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignCenter)
        # Add only the range slider.
        layout.addWidget(self.contrastRangeSlider)
        self.setLayout(layout)

    def on_slider_range_changed(self, low, high):
        if self.moviecanvas.navigator.movie is None:
            return
        # Ensure proper ordering of lower and upper bounds.
        new_low = min(low, high)
        new_high = max(low, high)
        # Update the movie canvas display immediately.
        self.moviecanvas.set_display_range(new_low, new_high)
        
        # Determine the current channel.
        try:
            current_channel = int(self.moviecanvas.navigator.movieChannelCombo.currentText())
        except Exception:
            current_channel = 1

        # Make sure the contrast settings dictionary exists.
        if not hasattr(self.moviecanvas.navigator, "channel_contrast_settings"):
            self.channel_contrast_settings = {}

        # For both multi-channel (4D) and single-channel (3D) movies, update the contrast settings.
        if self.moviecanvas.sum_mode:
            # Update the sum-mode contrast settings dictionary.
            self.moviecanvas.navigator.channel_sum_contrast_settings[current_channel] = {
                'vmin': new_low,
                'vmax': new_high,
                'extended_min': self.contrastRangeSlider.minimum(),
                'extended_max': self.contrastRangeSlider.maximum()
            }
        else:
            if self.moviecanvas.navigator.movie is not None:
                # Update the normal mode contrast settings dictionary.
                self.moviecanvas.navigator.channel_contrast_settings[current_channel] = {
                    'vmin': new_low,
                    'vmax': new_high,
                    'extended_min': self.contrastRangeSlider.minimum(),
                    'extended_max': self.contrastRangeSlider.maximum()
                }

    def update_current_slider_settings(self):
        mode = "sum" if self.moviecanvas.sum_mode else "norm"
        #print(mode)
        #print(self.contrastRangeSlider.minimum(), self.contrastRangeSlider.maximum(), self.contrastRangeSlider.lowerValue(), self.contrastRangeSlider.upperValue())
        self.sliderSettings[mode] = {
            "min": self.contrastRangeSlider.minimum(),
            "max": self.contrastRangeSlider.maximum(),
            "lower": self.contrastRangeSlider.lowerValue(),
            "upper": self.contrastRangeSlider.upperValue(),
        }

class KymoContrastControlsWidget(QWidget):
    def __init__(self, kymocanvas, parent=None):
        super().__init__(parent)
        self.kymocanvas = kymocanvas
        self.contrastRangeSlider = RangeSlider(Qt.Horizontal, self)
        self.contrastRangeSlider.rangeChanged.connect(self.on_slider_range_changed)
        self.initUI()

    def initUI(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignCenter)
        # Add only the range slider.
        layout.addWidget(self.contrastRangeSlider)
        self.setLayout(layout)

    def on_slider_range_changed(self, low, high):
        # Ensure proper ordering of lower and upper bounds.
        new_low = min(low, high)
        new_high = max(low, high)
        # Update the movie canvas display immediately.
        self.kymocanvas.set_display_range(new_low, new_high)

        # Make sure the contrast settings dictionary exists.
        # if not hasattr(self.moviecanvas.navigator, "kymo_contrast_settings"):
        #     self.kymo_contrast_settings = {}

        # self.moviecanvas.navigator.channel_contrast_settings[xxxkymographxxx] = {
        #     'vmin': new_low,
        #     'vmax': new_high,
        #     'extended_min': self.contrastRangeSlider.minimum(),
        #     'extended_max': self.contrastRangeSlider.maximum()
        # }

class ToggleSwitch(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(False)
        # Set the overall widget size
        self._totalWidth = 130
        self._totalHeight = 30
        self.setMinimumSize(self._totalWidth, self._totalHeight)
        
        # Define the area for the switch (the actual toggle region)
        self._switchWidth = 50
        self._switchHeight = self._totalHeight - 8  # leave 4px margin top and bottom
        self._switchMargin = 4
        
        # The handle offset (an integer value)
        self._handle_offset = 0
        
        # Set up an animation for the handleOffset property
        self._animation = QPropertyAnimation(self, b"handleOffset")
        self._animation.setDuration(120)
        self._animation.setEasingCurve(QEasingCurve.Linear)
    
    def sizeHint(self):
        return QSize(self._totalWidth, self._totalHeight)
    
    # The property getter and setter for handleOffset:
    def getHandleOffset(self):
        return self._handle_offset
    
    def setHandleOffset(self, value):
        self._handle_offset = int(value)
        self.update()
    
    handleOffset = pyqtProperty(int, fget=getHandleOffset, fset=setHandleOffset)
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        totalRect = self.rect()
        
        # Compute the switch rectangle centered horizontally
        switchX = (totalRect.width() - self._switchWidth) / 2
        switchRect = QRectF(switchX, self._switchMargin, self._switchWidth, self._switchHeight)
        
        # Use green for ROI (checked) and blue for Spot (unchecked)
        bg_color = QColor("#4CAF50") if self.isChecked() else QColor("#97b4ff")
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg_color)
        painter.drawRoundedRect(switchRect, switchRect.height() / 2, switchRect.height() / 2)
        
        # Draw the handle (a white circle)
        handleDiameter = switchRect.height()
        travel = int(switchRect.width() - handleDiameter)
        if self._animation.state() != QPropertyAnimation.Running:
            self._handle_offset = 0 if not self.isChecked() else travel
        handleX = switchRect.x() + self._handle_offset
        handleRect = QRectF(handleX, switchRect.y(), handleDiameter, handleDiameter)
        painter.setBrush(QColor("white"))
        painter.setPen(QtGui.QPen(QColor("#d0d0d0"), 1.5))
        painter.drawEllipse(handleRect)

        # Assume switchRect is already computed.
        # totalRect is the full widget rect:
        totalRect = self.rect()
        margin = 4  # Reduced margin to bring the labels closer

        # Rectangle for "Spot": extends from the left edge to just before the switch.
        leftTextRect = QRectF(
            totalRect.left(), totalRect.top(),
            switchRect.left() - 2 * margin, totalRect.height()
        )

        # Rectangle for "ROI": extends from just after the switch to the right edge.
        rightTextRect = QRectF(
            switchRect.right() + 2 * margin, totalRect.top(),
            totalRect.right() - (switchRect.right() + 2 * margin), totalRect.height()
        )

        painter.setPen(QColor("black"))

        font = painter.font()
        font.setBold(True)
        painter.setFont(font)

        painter.drawText(leftTextRect, Qt.AlignRight | Qt.AlignVCenter, "Spot")
        painter.drawText(rightTextRect, Qt.AlignLeft | Qt.AlignVCenter, "Line")
    
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            # Toggle the state immediately.
            self.setChecked(not self.isChecked())
            totalRect = self.rect()
            switchX = (totalRect.width() - self._switchWidth) / 2
            switchRect = QRectF(switchX, self._switchMargin, self._switchWidth, self._switchHeight)
            handleDiameter = switchRect.height()
            travel = int(switchRect.width() - handleDiameter)
            start = self._handle_offset
            end = 0 if not self.isChecked() else travel
            self._animation.stop()
            self._animation.setStartValue(start)
            self._animation.setEndValue(end)
            self._animation.start()
            self.update()
        event.accept()
    
    def mouseReleaseEvent(self, event):
        # Do nothing special on release so that the toggled state remains.
        event.accept()


class ChannelAxisDialog(QDialog):
    def __init__(self, axis_options, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Channel Axis")
        layout = QVBoxLayout(self)
        self.combo = QComboBox(self)
        # Populate the combo with the available axis options.
        for ax in axis_options:
            self.combo.addItem(str(ax))
        layout.addWidget(self.combo)
        buttonBox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        layout.addWidget(buttonBox)
        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)

    def selected_axis(self):
        return int(self.combo.currentText())


class SetScaleDialog(QDialog):
    def __init__(self, current_pixel_size, current_frame_interval, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set Scale")
        layout = QVBoxLayout(self)

        # bold_font = QFont()
        # bold_font.setBold(True)

        # Create a label and line edit for Pixel Size (nm)
        self.pixelLabel = QLabel("Pixel size (nm):")
        # self.pixelLabel.setFont(bold_font)
        self.pixelLabel.setAlignment(Qt.AlignCenter)
        self.pixelEdit = QLineEdit()
        self.pixelEdit.setValidator(QDoubleValidator(0.001, 1_000_000, 2, self))
        self.pixelEdit.setStyleSheet("background-color: white;")
        if current_pixel_size is not None:
            # **always** convert to float, then format with two decimals
            try:
                val = float(current_pixel_size)
                self.pixelEdit.setText(f"{val:.2f}")
            except ValueError:
                # fallback if it wasn’t a valid float
                self.pixelEdit.setText("0.00")
        layout.addWidget(self.pixelLabel)
        layout.addWidget(self.pixelEdit)

        # Create a label and line edit for Frame Interval (ms)
        self.frameLabel = QLabel("Frame interval (ms):")
        # self.frameLabel.setFont(bold_font)
        self.frameLabel.setAlignment(Qt.AlignCenter)  
        self.frameEdit = QLineEdit()
        self.frameEdit.setValidator(QDoubleValidator(0.001, 1_000_000, 2, self))
        self.frameEdit.setStyleSheet("background-color: white;")
        if current_frame_interval is not None:
            try:
                val = float(current_frame_interval)
                self.frameEdit.setText(f"{val:.2f}")
            except ValueError:
                self.frameEdit.setText("0.00")
        layout.addWidget(self.frameLabel)
        layout.addWidget(self.frameEdit)

        # Create OK and Cancel buttons
        self.buttonBox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(self.buttonBox)

        # Initially disable OK if either field is empty
        self.buttonBox.button(QDialogButtonBox.Ok).setEnabled(self.inputs_valid())

        # Connect signals to check inputs and accept/reject the dialog.
        self.pixelEdit.textChanged.connect(self.check_inputs)
        self.frameEdit.textChanged.connect(self.check_inputs)
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)

        parent.update_scale_label()

    def inputs_valid(self):
        # Make sure both fields are non-empty and the validators accept the input.
        return bool(self.pixelEdit.text().strip()) and bool(self.frameEdit.text().strip())

    def check_inputs(self):
        self.buttonBox.button(QDialogButtonBox.Ok).setEnabled(self.inputs_valid())

    def get_values(self):
        # Return the entered values as floats.
        return float(self.pixelEdit.text()), float(self.frameEdit.text())
    
class KymoLineOptionsDialog(QDialog):
    def __init__(self, current_line_width, current_method, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Line Options")
        
        layout = QFormLayout(self)
        
        # Spin box for line width.
        self.lineWidthSpin = QSpinBox(self)
        self.lineWidthSpin.setRange(1, 10)
        self.lineWidthSpin.setValue(current_line_width)
        layout.addRow("Line width (pixels):", self.lineWidthSpin)
        
        # Combo box to choose integration method.
        self.methodCombo = QComboBox(self)
        self.methodCombo.addItems(["Max", "Average"])
        # Set current selection based on current_method.
        if current_method.lower() == "average":
            self.methodCombo.setCurrentText("Average")
        else:
            self.methodCombo.setCurrentText("Max")
        layout.addRow("Integration method:", self.methodCombo)
        
        # OK and Cancel buttons.
        buttonBox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)
        layout.addWidget(buttonBox)
        
    def getValues(self):
        # Return the values: line width (int) and method (as lower-case string).
        return self.lineWidthSpin.value(), self.methodCombo.currentText().lower()

class CustomSplitter(QSplitter):
    def __init__(self, orientation, parent=None, handle_y_offset_pct=0.5):
        super().__init__(orientation, parent)
        self._handle_y_offset_pct = handle_y_offset_pct

    def createHandle(self):
        return CustomSplitterHandle(self.orientation(), self, y_offset_pct=self._handle_y_offset_pct)


class CustomSplitterHandle(QSplitterHandle):
    def __init__(self, orientation, parent, y_offset_pct=0.5):
        super().__init__(orientation, parent)
        self._y_offset_pct = y_offset_pct  # store the percentage offset

    @property
    def y_offset_pct(self):
        return self._y_offset_pct

    @y_offset_pct.setter
    def y_offset_pct(self, value):
        # Optionally: validate that value is between 0 and 1
        if 0 <= value <= 1:
            self._y_offset_pct = value
            self.update()  # trigger repaint
        else:
            raise ValueError("y_offset_pct must be between 0 and 1.")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor("#888888"), 0)
        painter.setPen(pen)
        painter.setBrush(QColor("#888888"))
        if self.orientation() == Qt.Horizontal:
            handle_width = 2
            handle_length = 20
            x = int((self.width() - handle_width) / 2)
            available_height = self.height() - handle_length
            # Calculate y from the stored percentage:
            y = int(self._y_offset_pct * available_height)
            painter.drawRoundedRect(x, y, handle_width, handle_length, handle_width/2, handle_width/2)
        else:
            handle_width = 20
            handle_height = 2
            x = int((self.width() - handle_width) / 2)
            y = int((self.height() - handle_height) / 2)
            painter.drawRoundedRect(x, y, handle_width, handle_height, handle_height/2, handle_height/2)
        painter.end()


class RoundedFrame(QFrame):
    def __init__(self, parent=None, radius=10, bg_color="#FAFBFF", border_color=None):
        super().__init__(parent)
        self.radius = radius
        self.bg_color = QColor(bg_color)
        self.border_color = QColor(border_color) if border_color else QColor(0, 0, 0, 0)
        self.setAttribute(Qt.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFrameStyle(QFrame.NoFrame)

    def setBorderColor(self, color_str):
        self.border_color = QColor(color_str)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        # Only draw in the widget’s rectangle.
        painter.setClipRect(self.rect())
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QBrush(self.bg_color))
        painter.setPen(QPen(self.border_color, 6))
        painter.drawRoundedRect(self.rect(), self.radius, self.radius)
        super().paintEvent(event)

class AxesRectAnimator(QObject):
    def __init__(self, ax):
        super().__init__()
        self._ax = ax

    def getAxesRect(self):
        # Get the current x and y limits of the axes.
        xmin, xmax = self._ax.get_xlim()
        ymin, ymax = self._ax.get_ylim()
        return QRectF(xmin, ymin, xmax - xmin, ymax - ymin)

    def setAxesRect(self, rect):
        # Update the axes limits using the QRectF values.
        self._ax.set_xlim(rect.x(), rect.x() + rect.width())
        self._ax.set_ylim(rect.y(), rect.y() + rect.height())
        # Redraw the canvas.
        self._ax.figure.canvas.draw_idle()

    axesRect = pyqtProperty(QRectF, fget=getAxesRect, fset=setAxesRect)

class RecalcDialog(QDialog):
    def __init__(self, current_mode, current_radius, message = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Recalculate")
        self.new_mode = current_mode
        self.new_radius = current_radius

        # Main layout
        layout = QVBoxLayout()

        self.setStyleSheet(QApplication.instance().styleSheet())

        # Display message with the number of trajectories needing recalculation.
        message_label = QLabel(message)
        layout.addWidget(message_label)

        # Tracking Mode dropdown
        mode_layout = QHBoxLayout()
        mode_label = QLabel("Tracking mode:")
        mode_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Independent", "Tracked", "Smooth"]) #, "Same center"
        self.mode_combo.setCurrentText(current_mode)
        mode_layout.addWidget(mode_label)
        mode_layout.addWidget(self.mode_combo)
        layout.addLayout(mode_layout)

        # Search radius spin box
        radius_layout = QHBoxLayout()
        radius_label = QLabel("Search Radius:")
        # radius_label.setStyleSheet(
        #     "font-weight: bold"
        # )
        radius_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.radius_spin = QSpinBox()
        self.radius_spin.setRange(8, 50)
        self.radius_spin.setValue(current_radius)
        radius_layout.addWidget(radius_label)
        radius_layout.addWidget(self.radius_spin)
        layout.addLayout(radius_layout)

        # OK and Cancel buttons
        button_layout = QHBoxLayout()
        ok_button = QPushButton("Process")
        ok_button.setAutoDefault(True)
        ok_button.setDefault(True)
        cancel_button = QPushButton("Cancel")
        ok_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)
        button_layout.addWidget(ok_button)
        layout.addLayout(button_layout)

        self.setLayout(layout)

    def accept(self):
        # Store the chosen values
        self.new_mode = self.mode_combo.currentText()
        self.new_radius = self.radius_spin.value()
        super().accept()

class RecalcWorker(QObject):
    progress = pyqtSignal(int)
    finished = pyqtSignal(list)
    canceled = pyqtSignal()

    def __init__(self, rows, trajectories, navigator):
        super().__init__()
        self._rows         = rows
        self._trajectories = trajectories
        self._navigator    = navigator
        self._is_canceled  = False

    @pyqtSlot()
    def run(self):
        total = sum(len(self._trajectories[r]["frames"]) for r in self._rows)
        count = 0
        results = []
        for row in self._rows:
            if self._is_canceled:
                self.canceled.emit()
                return

            old = self._trajectories[row]
            new_traj = self._navigator. _rebuild_one_trajectory(old, self._navigator)
            results.append((row, new_traj))

            count += len(old["frames"])
            self.progress.emit(count)

        self.finished.emit(results)

    def cancel(self):
        self._is_canceled = True

class RecalcAllWorker(QObject):
    progress = pyqtSignal(int)        # emits number of frames processed so far
    finished = pyqtSignal(dict)       # emits a dict mapping row → new trajectory
    canceled = pyqtSignal()

    def __init__(self, backup_trajectories: list, navigator):
        super().__init__()
        self._backup = backup_trajectories
        self._navigator = navigator
        # Use exactly one flag for cancellation:
        self._navigator._is_canceled = False

    @pyqtSlot()
    def run(self):
        # print("▶ run() entered; initial _is_canceled =", self._navigator._is_canceled)
        processed = 0
        results = {}

        for row_index, old in enumerate(self._backup):
            # print(f"worker (top of loop) _is_canceled = {self._navigator._is_canceled}")
            # 1) Check the one shared flag at the top of each iteration
            if self._navigator._is_canceled:
                self.canceled.emit()
                return

            # 2) Build “pts” list, checking the same flag inside any nested loops
            if len(old["anchors"]) > 1 and old.get("roi") is not None:
                pts = []
                anchors, roi = old["anchors"], old["roi"]
                for i in range(len(anchors) - 1):
                    if self._navigator._is_canceled:
                        self.canceled.emit()
                        return

                    f1, x1, y1 = anchors[i]
                    f2, x2, y2 = anchors[i+1]
                    seg = range(f1, f2+1) if i == 0 else range(f1+1, f2+1)
                    xs = np.linspace(x1, x2, len(seg), endpoint=True)
                    for j, f in enumerate(seg):
                        if self._navigator._is_canceled:
                            self.canceled.emit()
                            return
                        mx, my = self._navigator.compute_roi_point(roi, xs[j])
                        pts.append((f, mx, my))
            else:
                pts = []
                for f, (x, y) in zip(old["frames"], old["original_coords"]):
                    if self._navigator._is_canceled:
                        self.canceled.emit()
                        return
                    pts.append((f, x, y))

            # 3) Just before calling _compute_analysis, check again
            if self._navigator._is_canceled:
                self.canceled.emit()
                return
            
            # print(f"compute (just before calling _compute_analysis) _is_canceled = {self._navigator._is_canceled}")

            # 4) Run compute_analysis (no GUI), catch exceptions
            try:
                traj_background = self._navigator.compute_trajectory_background(
                    self._navigator.get_movie_frame,
                    pts,
                    crop_size=int(2 * self._navigator.searchWindowSpin.value())
                )
                frames, coords, search_centers, ints, fit, background = (
                    self._navigator._compute_analysis(
                        pts,
                        traj_background,
                        showprogress=False
                    )
                )
            except Exception:
                # skip this trajectory but still bump the progress bar
                processed += len(old["frames"])
                self.progress.emit(processed)
                continue

            # print(f"compute returned; now checking cancellation → {self._navigator._is_canceled}")

            # 5) Immediately after compute_analysis, check cancellation again
            if self._navigator._is_canceled:
                self.canceled.emit()
                return

            # 6) Unpack & rebuild new_traj (same as before)
            spots  = [p[0] for p in fit]
            sigmas = [p[1] for p in fit]
            peaks  = [p[2] for p in fit]
            valid_ints = [v for v, s in zip(ints, spots) if v and v > 0 and s]
            avg_int = float(np.mean(valid_ints)) if valid_ints else None
            med_int = float(np.median(valid_ints)) if valid_ints else None

            vels = []
            for i in range(1, len(spots)):
                p0, p1 = spots[i-1], spots[i]
                if p0 is None or p1 is None:
                    vels.append(None)
                else:
                    vels.append(float(np.hypot(p1[0]-p0[0], p1[1]-p0[1])))
            good_vels = [v for v in vels if v is not None]
            avg_vpf   = float(np.mean(good_vels)) if good_vels else None

            full_centers, full_sigmas, full_peaks, full_ints = [], [], [], []
            for f in old["frames"]:
                if f in frames:
                    idx = frames.index(f)
                    full_centers.append(spots[idx])
                    full_sigmas.append(sigmas[idx])
                    full_peaks.append(peaks[idx])
                    full_ints.append(ints[idx])
                else:
                    full_centers.append(None)
                    full_sigmas.append(None)
                    full_peaks.append(None)
                    full_ints.append(None)

            new_traj = {
                "trajectory_number": old["trajectory_number"],
                "channel":           old["channel"],
                "start":             old["start"],
                "end":               old["end"],
                "anchors":           old["anchors"],
                "roi":               old["roi"],
                "spot_centers":      full_centers,
                "sigmas":            full_sigmas,
                "peaks":             full_peaks,
                "fixed_background":  traj_background,
                "background":        background,
                "frames":            old["frames"],
                "original_coords":   old["original_coords"],
                "search_centers":    search_centers,
                "intensities":       full_ints,
                "average":           avg_int,
                "median":           med_int,
                "velocities":        vels,
                "average_velocity":  avg_vpf
            }

            # 7) Recompute colocalization exactly as before
            if getattr(self._navigator, "check_colocalization", False) and self._navigator.movie.ndim == 4:
                nav = self._navigator
                nav.analysis_frames     = new_traj["frames"]
                nav.analysis_fit_params = list(zip(
                    new_traj["spot_centers"],
                    new_traj["sigmas"],
                    new_traj["peaks"]
                ))
                nav.analysis_channel = new_traj["channel"]
                nav._compute_colocalization(showprogress=False)
                any_list = list(nav.analysis_colocalized)
                by_ch = {
                    ch: list(flags)
                    for ch, flags in nav.analysis_colocalized_by_ch.items()
                }
            else:
                if getattr(self._navigator, "movie", None) is None or self._navigator._channel_axis is None:
                    n_chan = 1
                else:
                    n_chan = self._navigator.movie.shape[self._navigator._channel_axis]

                N = len(new_traj["frames"])
                any_list = [None]*N
                by_ch    = { ch: [None]*N
                             for ch in range(1, n_chan+1)
                             if ch != new_traj["channel"] }

            new_traj["colocalization_any"]   = any_list
            new_traj["colocalization_by_ch"] = by_ch

            # 8) Optionally recompute steps
            if getattr(self._navigator, "show_steps", False):
                idxs, meds = self._navigator.compute_steps_for_data(
                    new_traj["frames"],
                    new_traj["intensities"]
                )
                new_traj["step_indices"] = idxs
                new_traj["step_medians"] = meds
            else:
                new_traj["step_indices"] = None
                new_traj["step_medians"] = None

            # 9) Preserve custom_fields
            new_traj["custom_fields"] = old.get("custom_fields", {}).copy()

            # 10) Store in results and bump progress
            results[row_index] = new_traj
            processed += len(old["frames"])
            self.progress.emit(processed)

        # 11) Finished without cancellation
        print("▶ run() finished all trajectories without seeing a cancel")
        self.finished.emit(results)

    def cancel(self):
        print("cancel() called, setting flag → True")
        # Called when the user clicks “Cancel” on the QProgressDialog:
        self._navigator._is_canceled = True

class ClickableLabel(QLabel):
    clicked = pyqtSignal()
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMouseTracking(True)

    def mousePressEvent(self, ev):
        self.setProperty("pressed", True)
        self.style().unpolish(self); self.style().polish(self)
        super().mousePressEvent(ev)

    def mouseReleaseEvent(self, ev):
        self.setProperty("pressed", False)
        self.style().unpolish(self); self.style().polish(self)
        super().mouseReleaseEvent(ev)
        self.clicked.emit()

class RadiusDialog(QDialog):
    def __init__(self, current_radius, parent=None):
        # Popup + frameless so it grabs focus but has no titlebar
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_StyledBackground, True)

        self.setStyleSheet("""
            QDialog {
                background-color: transparent;
            }
            QLabel {
                background-color: white;
                border-radius: 12px;
                padding: 8px;
                font-size: 14px;
                border: 1px solid #ccc;
            }
            QSpinBox {
                background-color: white;
                border: 1px solid #ccc;
                border-radius: 12px;
                padding: 4px 0px 4px 16px;
                font-size: 14px;
                min-height: 26px;
            }

            QSpinBox QLineEdit {
                background: transparent;
                border: none;
                padding: 0;
                text-align: center;
            }
        """)

        self.setAttribute(Qt.WA_ShowWithoutActivating)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8,4,8,4)
        radiuslabel = QLabel("Search Radius")
        # radiuslabel.setStyleSheet("font-weight:bold")
        lay.addWidget(radiuslabel)
        spin = QSpinBox(self)
        spin.setRange(8, 50)
        spin.setValue(current_radius)
        spin.setAlignment(Qt.AlignCenter)
        spin.lineEdit().setAlignment(Qt.AlignCenter)
        lay.addWidget(spin)
        self._spin = spin
        spin.setFocus()

    def showEvent(self, event):
        super().showEvent(event)
        # immediately clear focus so no cursor/frame is drawn
        self._spin.clearFocus()

    def wheelEvent(self, event):
        # forward any wheel to the spin‑box
        self._spin.wheelEvent(event)
        le = self._spin.lineEdit()
        le.deselect()
        self._spin.clearFocus()
        event.accept()

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            return
        if event.key() == Qt.Key_R:
            # write value back to the main window
            val = self._spin.value()
            # assume parent has attribute searchWindowSpin
            self.parent().searchWindowSpin.setValue(val)
            self.close()
        else:
            super().keyReleaseEvent(event)

    def closeEvent(self, ev):
        # tell the parent that we’re gone
        p = self.parent()
        p._radiusPopup = None
        p._radiusSpinLive = None
        super().closeEvent(ev)

class SaveKymographDialog(QDialog):
    # Class‐level storage of the last settings
    _last_use_prefix = False
    _last_middle    = ""
    _last_custom    = ""

    def __init__(self, movie_name, kymo_items, parent=None):
        super().__init__(parent)

        self.setStyleSheet("""
            QDialog {
                border-radius: 10px;
            }
            QLabel, QCheckBox {
                background-color: transparent;
                border-radius: 8px;
                padding: 4px;
                font-size: 14px;
            }
            QLineEdit {
                border: 1px solid #AAB4D4;
                border-radius: 6px;
                padding: 4px 6px;
                background-color: white;
            }

            QSpinBox {
                min-width: 15px;
            }
        """)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self.setWindowTitle("Save Kymographs")
        self.movie_name = movie_name
        self.kymo_list  = [n for n, _ in kymo_items]
        self.selected   = []

        if parent and hasattr(parent, "_last_dir"):
            self.directory = parent._last_dir
        else:
            self.directory = os.getcwd()
        self.dir_le = QLineEdit(self.directory)
 
        self._all_formats = ["tif","png","jpg"]

        main_v = QVBoxLayout(self)

        # ── “All” checkbox ─────────────────────────────
        self.all_cb = QCheckBox("All")
        self.all_cb.toggled.connect(self._on_all_toggled)
        main_v.addWidget(self.all_cb, alignment=Qt.AlignHCenter)

        # ── Kymo list ──────────────────────────────────
        self.list_w = QListWidget()
        self.list_w.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for name in self.kymo_list:
            it = QListWidgetItem(name)
            self.list_w.addItem(it)
        self.list_w.selectAll()
        self.list_w.itemSelectionChanged.connect(self._on_list_change)
        list_layout = QHBoxLayout()
        list_layout.setContentsMargins(20, 0, 20, 0)  # left, top, right, bottom
        list_layout.addWidget(self.list_w)
        main_v.addLayout(list_layout)
        
        # ── Directory chooser ─────────────────────────
        dir_h = QHBoxLayout()
        dir_h.addStretch()
        dir_h.addWidget(QLabel("Directory:"))
        self.dir_le = QLineEdit(self.directory)
        self.dir_le.setMinimumWidth(400)
        dir_h.addWidget(self.dir_le)
        browse = QPushButton("…")
        browse.clicked.connect(self._browse_dir)
        browse.setAutoDefault(False)
        dir_h.addWidget(browse)
        dir_h.addStretch()
        main_v.addLayout(dir_h)

        # ── File‑type dropdown ────────────────────────
        ft_h = QHBoxLayout()
        ft_h.addStretch()
        ft_h.addWidget(QLabel("File type:"))
        self.ft_combo = QComboBox()
        for ext in self._all_formats:
            self.ft_combo.addItem(ext)
        self.ft_combo.currentTextChanged.connect(self._on_filetype_changed)
        self.ft_combo.currentTextChanged.connect(self._update_preview)
        ft_h.addWidget(self.ft_combo)
        ft_h.addStretch()
        main_v.addLayout(ft_h)

        # ── Overlay checkbox ──────────────────────────
        self.overlay_cb = QCheckBox("Overlay trajectories")
        self.overlay_cb.toggled.connect(self._on_overlay_toggled)
        main_v.addWidget(self.overlay_cb, alignment=Qt.AlignHCenter)

        # ── Naming controls stack ──────────────────────
        self.controls_stack = QStackedWidget()
        self.controls_stack.setAttribute(Qt.WA_StyledBackground, True)
        self.controls_stack.setStyleSheet("background-color: transparent;")
        main_v.addWidget(self.controls_stack)

        # Page0: multi‑select naming
        page_multi = QWidget()
        # page_multi.setAttribute(Qt.WA_StyledBackground, True)
        # page_multi.setStyleSheet("background-color: transparent;")
        vbox_multi = QVBoxLayout(page_multi)

        # 1) Centered “Use movie name as prefix”
        self.prefix_cb = QCheckBox("Use movie name as prefix", parent=page_multi)
        self.prefix_cb.toggled.connect(self._update_preview)
        # restore last state
        self.prefix_cb.setChecked(self.__class__._last_use_prefix)
        h_ctr = QHBoxLayout()
        h_ctr.addStretch()
        h_ctr.addWidget(self.prefix_cb)
        h_ctr.addStretch()
        vbox_multi.addLayout(h_ctr)

        # 2) Kymograph middle prefix
        fm_multi = QFormLayout()
        fm_multi.setLabelAlignment(Qt.AlignRight)
        self.middle_le = QLineEdit(parent=page_multi)
        self.middle_le.setStyleSheet("""
            background-color: white;
            border: 1px solid #AAB4D4;
            border-radius: 6px;
            padding: 4px 6px;
        """)
        self.middle_le.textChanged.connect(self._update_preview)
        # restore last text
        self.middle_le.setText(self.__class__._last_middle)
        fm_multi.addRow(QLabel("Kymograph prefix:"), self.middle_le)
        vbox_multi.addLayout(fm_multi)

        self.controls_stack.addWidget(page_multi)

        # Page1: single‑select naming
        page_single = QWidget()
        # page_single.setAttribute(Qt.WA_StyledBackground, True)
        # page_single.setStyleSheet("background-color: transparent;")
        fm_single = QFormLayout(page_single)
        fm_single.setLabelAlignment(Qt.AlignRight)
        self.custom_le = QLineEdit(parent=page_single)
        self.custom_le.setStyleSheet("""
            background-color: white;
            border: 1px solid #AAB4D4;
            border-radius: 6px;
            padding: 4px 6px;
        """)
        self.custom_le.setMinimumWidth(400)
        self.custom_le.setAlignment(Qt.AlignHCenter)
        # restore last custom name
        self.custom_le.setText(self.__class__._last_custom)
        fm_single.addRow(QLabel("Filename:"), self.custom_le)
        self.controls_stack.addWidget(page_single)

        # ── Preview label ──────────────────────────────
        self.preview = QLabel("", alignment=Qt.AlignCenter)
        main_v.addWidget(self.preview)

        # ── Save/Cancel ────────────────────────────────
        btn_h = QHBoxLayout()
        btn_h.addStretch()
        ok = QPushButton("Save")
        ok.clicked.connect(self.accept)
        ok.setDefault(True)
        ok.setAutoDefault(True)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        cancel.setAutoDefault(False)
        btn_h.addWidget(cancel)
        btn_h.addWidget(ok)
        btn_h.addStretch()
        main_v.addLayout(btn_h)

        # ── Initial sync ───────────────────────────────
        self._on_filetype_changed(self.ft_combo.currentText())
        self._on_list_change()


    def getOptions(self):
        # … your existing code to build opts …
        opts = {
            "directory":  self.directory,
            "selected":   self.selected,
            "overlay":    self.overlay_cb.isChecked(),
            "filetype":   self.ft_combo.currentText(),
        }
        if len(self.selected) > 1:
            opts.update({
                "use_prefix":  self.prefix_cb.isChecked(),
                "middle":      self.middle_le.text().strip(),
                "custom":      False,
                "custom_name": ""
            })
        else:
            opts.update({
                "use_prefix":  False,
                "middle":      "",
                "custom":      True,
                "custom_name": self.custom_le.text().strip()
            })

        # Store for next time
        SaveKymographDialog._last_use_prefix = opts["use_prefix"]
        SaveKymographDialog._last_middle    = opts["middle"]
        SaveKymographDialog._last_custom    = opts["custom_name"]

        return opts

    def _on_all_toggled(self, checked):
        for i in range(self.list_w.count()):
            self.list_w.item(i).setSelected(checked)
        # signal fires _on_list_change automatically


    def _on_list_change(self):
        self.selected = [it.text() for it in self.list_w.selectedItems()]

        self.all_cb.blockSignals(True)
        self.all_cb.setChecked(len(self.selected) == len(self.kymo_list))
        self.all_cb.blockSignals(False)

        if len(self.selected) > 1:
            # ── multi‑select page ───────────────────────
            self.controls_stack.setCurrentIndex(0)
            self.preview.show()
            self._update_preview()

        else:
            # ── single‑select page ──────────────────────
            self.controls_stack.setCurrentIndex(1)

            # ---------- default filename ----------
            if self.selected:               # there is exactly one item
                default_name = f"{self.movie_name}-{self.selected[0]}"
                self.custom_le.setText(default_name)
            # -------------------------------------------

            self.preview.hide()

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Directory", self.directory)
        if d:
            self.directory = d
            self.dir_le.setText(d)
            # write it back to the navigator
            parent = self.parent()
            if parent and hasattr(parent, "_last_dir"):
                parent._last_dir = d
                
    def _update_preview(self):
        # only update in multi‑select mode
        if self.controls_stack.currentIndex() != 0:
            return

        parts = []
        if self.prefix_cb.isChecked():
            parts.append(self.movie_name)
        mid = self.middle_le.text().strip()
        if mid:
            parts.append(mid)
        parts.append(self.selected[0] if self.selected else "")
        ext = self.ft_combo.currentText()
        self.preview.setText(f"Example: {'-'.join(parts)}.{ext}")

    def _on_overlay_toggled(self, checked):
        self._update_preview()

    def _on_filetype_changed(self, ext: str):
        """
        - If TIFF is selected, disable and uncheck the overlay option.
        - For PNG/JPG, enable the overlay checkbox.
        """
        is_tif = ext.lower() == "tif"

        if is_tif:
            self.overlay_cb.blockSignals(True)
            self.overlay_cb.setChecked(False)
            self.overlay_cb.setEnabled(False)
            self.overlay_cb.blockSignals(False)
        else:
            self.overlay_cb.setEnabled(True)

        self._update_preview()


class BubbleTip(QWidget):
    def __init__(self, text: str, parent=None, placement: str="right"):
        super().__init__(parent, Qt.ToolTip | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self.text      = text
        self.placement = placement  # "right" (bubble to right of widget) or "left"
        self.padding   = 8
        self.triangle  = 10

        fm = QFontMetrics(self.font())
        tw = fm.horizontalAdvance(text)
        th = fm.height()

        # Compute total size: text + padding on both sides + triangle width
        body_w = tw + 2*self.padding
        body_h = th + 2*self.padding
        total_w = body_w + self.triangle
        total_h = body_h

        # Body rect sits *after* the triangle if placement=="right",
        # or at x=0 if placement=="left".
        if self.placement == "right":
            self.body = QRectF(self.triangle, 0, body_w, body_h)
        else:
            self.body = QRectF(0, 0, body_w, body_h)

        self.resize(int(total_w), int(total_h))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # 1) Rounded‐rect for the “body”
        path = QPainterPath()
        path.addRoundedRect(self.body, 8, 8)

        # 2) Triangle arrow
        mid_y = self.body.top() + self.body.height()/2
        t     = self.triangle
        pts   = []

        if self.placement == "right":
            # Bubble is to the right ➔ arrow on left edge of body
            x0 = self.body.left()
            pts = [
                QPointF(x0,       mid_y - t/2),
                QPointF(0,        mid_y),
                QPointF(x0,       mid_y + t/2),
            ]
        else:
            # Bubble is to the left ➔ arrow on right edge of body
            x1 = self.body.right()
            pts = [
                QPointF(x1,       mid_y - t/2),
                QPointF(x1 + t,   mid_y),
                QPointF(x1,       mid_y + t/2),
            ]

        tri = QPainterPath()
        tri.moveTo(pts[0])
        tri.lineTo(pts[1])
        tri.lineTo(pts[2])
        tri.closeSubpath()

        # 3) Merge and draw
        shape = path.united(tri)
        p.fillPath(shape, QColor(255, 255, 255, 230))
        pen = QPen(QColor(200, 200, 200))
        pen.setWidth(1)
        p.setPen(pen)
        p.drawPath(shape)

        # 4) Draw the text inside the body
        p.setPen(Qt.black)
        inner = self.body.adjusted(
            self.padding, self.padding,
           -self.padding, -self.padding
        )
        p.drawText(inner, Qt.AlignCenter, self.text)

class BubbleTipFilter(QObject):
    def __init__(self, text: str, parent=None, placement: str = "right"):
        super().__init__(parent)
        self.text      = text
        self.placement = placement  # "right" or "left"
        self.bubble    = None
        self._wobj     = None

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(800)
        self._timer.timeout.connect(self._showBubble)

        self._force_show = False

        QApplication.instance().installEventFilter(self)

    def _showBubble(self, force: bool = False):
        if force:
            self._force_show = True
        if not self._wobj:
            return
        gp = QCursor.pos()
        btn_tl   = self._wobj.mapToGlobal(QPoint(0,0))
        btn_rect = QRect(btn_tl, self._wobj.size())
        if not force and not btn_rect.contains(gp):
            return

        # create & position bubble
        b = BubbleTip(self.text, parent=self._wobj.window(),
                      placement=self.placement)
        b.installEventFilter(self)
        self.bubble = b

        # compute x based on placement
        if self.placement == "right":
            btn_edge = self._wobj.mapToGlobal(self._wobj.rect().topRight())
            x = btn_edge.x()
        else:
            x = btn_tl.x() - b.width()

        y = btn_tl.y() + (self._wobj.height() - b.height())//2
        b.move(x, y)
        b.show()

        QTimer.singleShot(3000, self._hideBubble)

    def eventFilter(self, obj, ev):
        # — Global mouse‐move: maybe hide
        if ev.type() == QEvent.MouseMove and self.bubble and not self._force_show:
            self._checkHide()

        # — Enter on *our* button?
        if ev.type() == QEvent.Enter and getattr(obj, "_bubble_filter", None) is self:
            self._wobj = obj
            self._timer.start()

        # — Leave or click on *our* button?
        elif ev.type() in (QEvent.Leave, QEvent.MouseButtonPress) \
             and getattr(obj, "_bubble_filter", None) is self:
            self._timer.stop()
            self._hideBubble()

        return False  # Always let events continue

    def _checkHide(self):
        gp = QCursor.pos()
        # button rect
        btn_tl = self._wobj.mapToGlobal(QPoint(0,0))
        btn_rect = QRect(btn_tl, self._wobj.size())
        # bubble rect
        bub_tl = self.bubble.mapToGlobal(QPoint(0,0))
        bub_rect = QRect(bub_tl, self.bubble.size())
        if not (btn_rect.contains(gp) or bub_rect.contains(gp)):
            self._hideBubble()

    def _hideBubble(self):
        self._force_show = False
        if self.bubble:
            self.bubble.hide()
            self.bubble.deleteLater()
            self.bubble = None

class CenteredBubble(QWidget):
    def __init__(self, text: str, parent=None, pen_width=1):
        super().__init__(parent)
        self.text      = text
        self.padding   = 8
        self.pen_width = pen_width

        fm = QFontMetrics(self.font())
        tw = fm.horizontalAdvance(text)
        th = fm.height()
        body_w = tw + 2*self.padding
        body_h = th + 2*self.padding

        half = self.pen_width/2
        self.rect_body = QRectF(half, half, body_w, body_h)

        total_w = int(body_w + self.pen_width)
        total_h = int(body_h + self.pen_width)
        self.resize(total_w, total_h)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        path = QPainterPath()
        path.addRoundedRect(self.rect_body, 8, 8)
        p.fillPath(path, QColor(255,255,255,230))

        pen = QPen(QColor(200,200,200))
        pen.setWidth(self.pen_width)
        p.setPen(pen)
        p.drawPath(path)

        p.setPen(Qt.black)
        inner = self.rect_body.adjusted(
            self.padding, self.padding,
            -self.padding, -self.padding
        )
        p.drawText(inner, Qt.AlignCenter, self.text)

class CenteredBubbleFilter(QObject):
    def __init__(self, text: str,
                 delay_ms: int = 1000,
                 visible_ms: int = 3000,
                 poll_interval: int = 50,
                 parent=None):
        super().__init__(parent)
        self.text        = text
        self.delay_ms    = delay_ms
        self.visible_ms  = visible_ms
        self.target      = None
        self.bubble      = None

        # Poll timer: checks every poll_interval ms
        self._pollTimer = QTimer(self)
        self._pollTimer.setInterval(poll_interval)
        self._pollTimer.timeout.connect(self._checkIdle)

        # High‐resolution timer to measure idle time
        self._idleTimer = QElapsedTimer()
        self._lastPos   = None

        # Auto‐hide timer
        self._hideTimer = QTimer(self)
        self._hideTimer.setSingleShot(True)
        self._hideTimer.setInterval(self.visible_ms)
        self._hideTimer.timeout.connect(self._hide)

    def attachTo(self, widget: QWidget):
        """Start polling; widget is the canvas to watch."""
        self.target = widget
        self._idleTimer.invalidate()
        self._lastPos = None
        self._pollTimer.start()
        return self

    def _checkIdle(self):
        if not self.target:
            return

        # Get canvas rect in global coords
        rect = QRect(
            self.target.mapToGlobal(QPoint(0, 0)),
            self.target.size()
        )
        pos = QCursor.pos()

        # If mouse left the canvas: reset everything & hide
        if not rect.contains(pos):
            self._lastPos = None
            self._idleTimer.invalidate()
            self._hide()
            return

        # If it moved: restart the idle clock
        if self._lastPos is None or pos != self._lastPos:
            self._lastPos = pos
            self._idleTimer.restart()
            # If a bubble was showing, hide it immediately
            self._hide()
            return

        # Still inside and still at same point: check if we've sat still long enough
        if self._idleTimer.hasExpired(self.delay_ms) and self.bubble is None:
            self._show()

    def _show(self):
        # Double‐check we're still inside
        rect = QRect(
            self.target.mapToGlobal(QPoint(0, 0)),
            self.target.size()
        )
        if not rect.contains(QCursor.pos()):
            return

        # Create & center the bubble
        self.bubble = CenteredBubble(self.text, parent=self.target)
        bw, bh = self.bubble.width(), self.bubble.height()
        tw, th = self.target.width(), self.target.height()
        self.bubble.move((tw - bw)//2, (th - bh)//90)
        self.bubble.show()

        # Schedule auto‐hide
        self._hideTimer.start()

    def _hide(self):
        if self.bubble:
            self.bubble.hide()
            self.bubble.deleteLater()
            self.bubble = None

class AnimatedIconButton(QPushButton):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # set up the animation on the built-in iconSize property
        self._anim = QPropertyAnimation(self, b"iconSize", self)
        self._anim.setDuration(100)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        # we'll capture whatever size you set, then grow by 20%
        self._base_size  = self.iconSize()
        self._hover_size = QSize(
            int(self._base_size.width()  * 1.1),
            int(self._base_size.height() * 1.1)
        )

        self.setFixedSize(self.sizeHint())

    def enterEvent(self, event):
        # animate up to hover size
        self._anim.stop()
        self._anim.setStartValue(self.iconSize())
        self._anim.setEndValue(self._hover_size)
        self._anim.start()
        super().enterEvent(event)

    def leaveEvent(self, event):
        # animate back down
        self._anim.stop()
        self._anim.setStartValue(self.iconSize())
        self._anim.setEndValue(self._base_size)
        self._anim.start()
        super().leaveEvent(event)

class StepSettingsDialog(QDialog):
    def __init__(self, current_W, current_min_step, can_calculate_all: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Step-finding parameters")
        self.new_W = current_W
        self.new_min_step = current_min_step
        self.calculate_all = False

        layout = QVBoxLayout(self)
        self.setStyleSheet(QApplication.instance().styleSheet())

        # Rolling average window
        win_layout = QHBoxLayout()
        win_label = QLabel("Rolling average window:")
        win_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.win_spin = QSpinBox()
        self.win_spin.setRange(1, 999)
        self.win_spin.setValue(current_W)
        win_layout.addWidget(win_label)
        win_layout.addWidget(self.win_spin)
        layout.addLayout(win_layout)

        # Minimum step threshold
        step_layout = QHBoxLayout()
        step_label = QLabel("Minimum step size:")
        step_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.step_spin = QSpinBox()
        self.step_spin.setRange(0, 999999)
        self.step_spin.setValue(current_min_step)
        step_layout.addWidget(step_label)
        step_layout.addWidget(self.step_spin)
        layout.addLayout(step_layout)

        # bottom buttons
        btns = QHBoxLayout()
        btns.addWidget(QPushButton("Cancel", clicked=self.reject))
        btn_set = QPushButton("Set", clicked=self._on_set)
        btn_set.setDefault(True)
        btns.addWidget(btn_set)

        # only add the extra button if there's at least one trajectory
        if can_calculate_all:
            btns.addWidget(QPushButton("Set and Calculate", clicked=self._on_setall))

        layout.addLayout(btns)

    def _on_set(self):
        self.new_W        = self.win_spin.value()
        self.new_min_step = self.step_spin.value()
        self.calculate_all = False
        self.accept()

    def _on_setall(self):
        self.new_W        = self.win_spin.value()
        self.new_min_step = self.step_spin.value()
        self.calculate_all = True
        self.accept()