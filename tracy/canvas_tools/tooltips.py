from ._shared import *

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

