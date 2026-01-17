"""Canvas widgets for Tracy."""

from .base import ImageCanvas
from .kymo import KymoCanvas
from .movie import MovieCanvas
from .intensity import IntensityCanvas
from .trajectory import TrajectoryCanvas
from .histogram import HistogramCanvas
from .velocity import VelocityCanvas

__all__ = [
    "ImageCanvas",
    "KymoCanvas",
    "MovieCanvas",
    "IntensityCanvas",
    "TrajectoryCanvas",
    "HistogramCanvas",
    "VelocityCanvas",
]
