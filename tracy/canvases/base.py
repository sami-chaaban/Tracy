from ._shared import *

class ImageCanvas(FigureCanvas):
    def __init__(self, parent=None):
        self.fig = Figure()
        self.ax = self.fig.add_subplot(111)
        self.fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        self.ax.axis("off")
        super().__init__(self.fig)
        self.setParent(parent)
        self._draw_deferred = False
        # self.setAttribute(Qt.WA_OpaquePaintEvent)
        # self.setAttribute(Qt.WA_NoSystemBackground)
        self.image = None

    def draw(self, *args, **kwargs):
        if self.width() <= 1 or self.height() <= 1:
            if not self._draw_deferred:
                self._draw_deferred = True
                QTimer.singleShot(0, self._retry_draw)
            return
        self._draw_deferred = False
        return super().draw(*args, **kwargs)

    def _retry_draw(self):
        self._draw_deferred = False
        if self.width() <= 1 or self.height() <= 1:
            return
        super().draw()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setClipRect(self.rect())  # This restricts drawing to the visible area.
        super().paintEvent(event)

    def display_image(self, image, title=""):
        self.ax.clear()
        self.image = image
        self.ax.imshow(image, cmap="gray")
        self.ax.set_title(title)
        self.ax.axis('off')
        self.draw()

    def set_cmap(self, cmap):
        # if somebody’s already painted an image into `self._im` or `self.image`:
        im = getattr(self, "_im", None) or getattr(self, "image", None)
        # If AxesImage is stored in `self._im`, use that.
        if hasattr(self, "_im") and self._im is not None:
            self._im.set_cmap(cmap)
            self.draw()
