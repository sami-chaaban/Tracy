from ._shared import *

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

