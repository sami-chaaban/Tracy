from ._shared import *

class RangeSlider(QtWidgets.QSlider):

    # Signals to notify when lower or upper value changes.
    lowerValueChanged = pyqtSignal(int)
    upperValueChanged = pyqtSignal(int)
    rangeChanged = pyqtSignal(int, int)

    def __init__(self, orientation=Qt.Horizontal, parent=None):
        super().__init__(orientation, parent)
        self.setOrientation(orientation)
        # Set required attributes first.
        self._handleRadius = 7  # radius of the handle circle
        self._lower = self.minimum()
        self._upper = self.maximum()
        self._activeHandle = None  # Which handle is currently being dragged?
        # Now that _handleRadius is defined, we can set contents margins.
        hw = self._handleRadius
        border = 2  # same as handle pen width
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
            self.moviecanvas.navigator.channel_contrast_settings = {}

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

        nav = getattr(self.kymocanvas, "navigator", None)
        if nav is None:
            return
        kymo_name = ""
        try:
            kymo_name = nav.kymoCombo.currentText()
        except Exception:
            kymo_name = ""
        if not kymo_name:
            return

        if not hasattr(nav, "kymo_contrast_settings"):
            nav.kymo_contrast_settings = {}

        nav.kymo_contrast_settings[kymo_name] = {
            'vmin': new_low,
            'vmax': new_high,
            'extended_min': self.contrastRangeSlider.minimum(),
            'extended_max': self.contrastRangeSlider.maximum()
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
        self._switchHeight = self._totalHeight - 10  # leave 5px margin top and bottom
        self._switchMargin = 5
        
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
        font.setPointSize(11)
        painter.setFont(font)

        painter.drawText(leftTextRect, Qt.AlignRight | Qt.AlignVCenter, "SPOT")
        painter.drawText(rightTextRect, Qt.AlignLeft | Qt.AlignVCenter, "KYMO")
    
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
