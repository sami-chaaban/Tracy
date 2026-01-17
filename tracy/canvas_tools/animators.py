from ._shared import *

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

