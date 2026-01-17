from ._shared import *

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

class AnimatedIconButton(QPushButton):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # set up the animation on the built-in iconSize property
        self._anim = QPropertyAnimation(self, b"iconSize", self)
        self._anim.setDuration(100)
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

        # Capture base size and grow by 10%.
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
