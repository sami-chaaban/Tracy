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
    QEasingCurve, QObject, QPropertyAnimation, pyqtSlot
    )
from PyQt5.QtGui import (
    QPainter, QColor, QFont, QDoubleValidator, QPen, QBrush
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
        blue_pen = QtGui.QPen(QtGui.QColor("#C8D2EB"), 6, cap=Qt.RoundCap)
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
        bg_color = QColor("#4CAF50") if self.isChecked() else QColor("#C8D2EB")
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

        bold_font = QFont()
        bold_font.setBold(True)

        # Create a label and line edit for Pixel Size (nm)
        self.pixelLabel = QLabel("Pixel size (nm):")
        self.pixelLabel.setFont(bold_font)
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
        self.frameLabel.setFont(bold_font)
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
        pen = QPen(QColor("#555555"), 0)
        painter.setPen(pen)
        painter.setBrush(QColor("#555555"))
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
        radius_label.setStyleSheet(
            "font-weight: bold"
        )
        radius_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.radius_spin = QSpinBox()
        self.radius_spin.setRange(1, 40)  # adjust range as necessary
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
    progress = pyqtSignal(int)           # emits how many frames processed so far
    finished = pyqtSignal(list)          # emits a list of (row, new_traj) pairs
    canceled = pyqtSignal()

    def __init__(self, rows, trajectories, navigator):
        super().__init__()
        self._rows         = rows
        self._trajectories = trajectories
        self._navigator    = navigator
        self._is_canceled  = False

    @pyqtSlot()
    def run(self):
        total_frames = sum(len(self._trajectories[r]["frames"]) for r in self._rows)
        count = 0
        results = []

        for row in self._rows:
            if self._is_canceled:
                self.canceled.emit()
                return

            old = self._trajectories[row]

            # decide whether to show the internal progress bar
            single = (len(self._rows) == 1)
            pts    = self._build_pts_for(old)

            frames, _, centers, ints, fit, colors = \
                self._navigator._compute_analysis(
                    pts,
                    showprogress=single
                )

            new_traj = self._recalculate_one(old)
            results.append((row, new_traj))

            count += len(old["frames"])
            self.progress.emit(count)

        self.finished.emit(results)

    def cancel(self):
        self._is_canceled = True

    def _recalculate_one(self, old):
        # copy exactly your existing per‑trajectory logic:
        anchors, roi = old["anchors"], old["roi"]
        if len(anchors)>1 and roi is not None:
            pts = []
            for i in range(len(anchors)-1):
                f1,x1,_ = anchors[i]
                f2,x2,_ = anchors[i+1]
                seg = (range(f1,f2+1) if i==0 else range(f1+1,f2+1))
                xs = np.linspace(x1,x2,len(seg),endpoint=True)
                for j,f in enumerate(seg):
                    mx,my = self._navigator.compute_roi_point(roi, xs[j])
                    pts.append((f,mx,my))
        else:
            pts = [(f,x,y) for f,(x,y) in zip(old["frames"], old["original_coords"])]

        frames, _, centers, ints, fit, colors = \
            self._navigator._compute_analysis(pts, showprogress=False)

        if getattr(self._navigator, "debug", False):
            print(f"RECALC[{old['trajectory_number']}] → frames:{old['frames'][:3]}…{old['frames'][-3:]}, coords:{old['original_coords'][:3]}…{old['original_coords'][-3:]}")

        spots  = [p[0] for p in fit]
        sigmas = [p[1] for p in fit]
        peaks  = [p[2] for p in fit]

        valid_ints = [v for v,s in zip(ints, spots) if v and v>0 and s]
        avg = float(np.mean(valid_ints)) if valid_ints else None
        med = float(np.median(valid_ints)) if valid_ints else None

        # recalc velocities
        vels = []
        for i in range(1, len(frames)):
            p0, p1 = spots[i-1], spots[i]
            vels.append(None if (p0 is None or p1 is None)
                        else np.hypot(p1[0]-p0[0], p1[1]-p0[1]))
        good = [v for v in vels if v is not None]
        avg_vpf = float(np.mean(good)) if good else None

        start = (frames[0],   old["original_coords"][0][0],  old["original_coords"][0][1])
        end   = (frames[-1],  old["original_coords"][-1][0], old["original_coords"][-1][1])

        new_traj = {
            "trajectory_number": old["trajectory_number"],
            "start":    old["start"],
            "end":      old["end"],
            "anchors":  anchors,
            "roi":      roi,
            "spot_centers": spots,
            "sigmas":      sigmas,
            "peaks":       peaks,
            "frames":      old["frames"],
            "original_coords":      old["original_coords"],
            "search_centers": centers,
            "intensities": ints,
            "average":     avg,
            "median":      med,
            "colors":      colors,
            "velocities":  vels,
            "average_velocity": avg_vpf
        }

        return new_traj
    
    def _build_pts_for(self, old):
        anchors, roi = old["anchors"], old["roi"]
        pts = []
        if len(anchors) > 1 and roi is not None:
            for i in range(len(anchors) - 1):
                f1, x1, _ = anchors[i]
                f2, x2, _ = anchors[i+1]
                seg = range(f1, f2+1) if i == 0 else range(f1+1, f2+1)
                xs = np.linspace(x1, x2, len(seg), endpoint=True)
                for j, f in enumerate(seg):
                    mx, my = self._navigator.compute_roi_point(roi, xs[j])
                    pts.append((f, mx, my))
        else:
            for f, (x, y) in zip(old["frames"], old["original_coords"]):
                pts.append((f, x, y))
        return pts

class ClickableLabel(QLabel):
    clicked = pyqtSignal()
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # show a hand cursor on hover
        #self.setCursor(Qt.PointingHandCursor)
    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(ev)

class RadiusDialog(QDialog):
    def __init__(self, current_radius, parent=None):
        # Popup + frameless so it grabs focus but has no titlebar
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("""
            QDialog {
                background-color: #F1F5FF;
                border-radius: 10px;
            }
            QLabel {
                border-radius: 8px;
                padding: 4px;
                font-size: 14px;
            }
            QSpinBox {
                min-width: 15px;
                border: 1px solid #ccc
            }
            QSpinBox QLineEdit {
                background-color: '#F5F7FF';
            }
        """)

        self.setAttribute(Qt.WA_ShowWithoutActivating)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8,4,8,4)
        radiuslabel = QLabel("Search Radius:")
        radiuslabel.setStyleSheet("font-weight:bold")
        lay.addWidget(radiuslabel)
        spin = QSpinBox(self)
        spin.setStyleSheet("background: #F5F7FF")
        spin.setRange(8, 50)
        spin.setValue(current_radius)
        spin.setAlignment(Qt.AlignCenter)
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

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet("""
            QDialog {
                background-color: #F1F5FF;
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
            QPushButton {
                background-color: #DCE4FF;
                border-radius: 6px;
                padding: 6px 12px;
            }
            QPushButton:hover {
                background-color: #C0D0FF;
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
        self.directory  = os.getcwd()
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
        ok = QPushButton("Save");   ok.clicked.connect(self.accept)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        btn_h.addWidget(ok); btn_h.addWidget(cancel); btn_h.addStretch()
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

