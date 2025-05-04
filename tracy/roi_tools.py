import numpy as np
import struct
import read_roi
from roifile import ImagejRoi, ROI_TYPE


def compute_roi_point(roi, kymo_xdata):
    roi_x = np.array(roi["x"], dtype=float)
    roi_y = np.array(roi["y"], dtype=float)
    if roi_x.size < 2:
        return (roi_x[0], roi_y[0])
    
    # Compute segment lengths and cumulative lengths
    diffs = np.sqrt(np.diff(roi_x)**2 + np.diff(roi_y)**2)
    cum_lengths = np.concatenate(([0], np.cumsum(diffs)))
    total_length = cum_lengths[-1]
    
    # Compute the fractional distance along the ROI (keep as float)
    roi_x = np.array(roi["x"], dtype=float)
    roi_y = np.array(roi["y"], dtype=float)
    lengths = np.hypot(np.diff(roi_x), np.diff(roi_y))
    total_length = lengths.sum()
    kymo_width = max(int(total_length), 2)
    frac = kymo_xdata / kymo_width
    target_dist = frac * total_length
    
    # Use np.interp for smooth interpolation along ROI
    x_orig = np.interp(target_dist, cum_lengths, roi_x)
    y_orig = np.interp(target_dist, cum_lengths, roi_y)
    return (x_orig, y_orig)

def is_point_near_roi(point, roi, search_radius=5):
    """
    Returns True if the given point (x,y) is within search_radius of any segment of the ROI.
    roi: dictionary with keys "x" and "y" (lists or arrays)
    search_radius: a numeric value (e.g. from self.searchWindowSpin.value())
    """
    
    def distance_point_to_segment(point, A, B):
        """
        Return the distance from point P to the line segment AB.
        point: (px, py)
        A: (ax, ay)
        B: (bx, by)
        """
        px, py = point
        ax, ay = A
        bx, by = B
        vx = bx - ax
        vy = by - ay
        if vx == 0 and vy == 0:
            return np.hypot(px - ax, py - ay)
        # Projection factor, clamped to [0, 1]
        t = ((px - ax) * vx + (py - ay) * vy) / (vx * vx + vy * vy)
        t = max(0, min(1, t))
        proj_x = ax + t * vx
        proj_y = ay + t * vy
        return np.hypot(px - proj_x, py - proj_y)

    roi_x = np.array(roi["x"], dtype=float)
    roi_y = np.array(roi["y"], dtype=float)
    if roi_x.size < 2:
        # Not enough points to form a segment.
        return False
    min_dist = float('inf')
    for i in range(len(roi_x) - 1):
        A = (roi_x[i], roi_y[i])
        B = (roi_x[i+1], roi_y[i+1])
        dist = distance_point_to_segment(point, A, B)
        if dist < min_dist:
            min_dist = dist
    return min_dist <= search_radius

def convert_roi_to_binary(roi):
    """
    Convert an ROI dictionary with keys "points", "x", and "y" into a binary ImageJ ROI
    representing a segmented (poly)line.
    """

    # Get the list of points; ensure there are enough points.
    pts = roi.get("points")
    if pts is None or len(pts) < 2:
        raise ValueError("Not enough points to form an ROI.")

    # Create a NumPy array from the list of points.
    pts = np.array(pts, dtype=float)

    # Create an ImagejRoi instance using the frompoints constructor.
    imagej_roi = ImagejRoi.frompoints(pts)
    # Change the ROI type from FREEHAND (default in frompoints) to POLYLINE,
    # which is appropriate for a segmented line.
    imagej_roi.roitype = ROI_TYPE.POLYLINE

    # Optionally, if your points have subpixel precision,
    # the frompoints method will set the SUB_PIXEL_RESOLUTION option.
    # You could also manually set options here if needed:
    # imagej_roi.options |= ROI_OPTIONS.SUB_PIXEL_RESOLUTION

    # Return the binary representation of the ROI.
    return imagej_roi.tobytes()

def parse_roi_blob(blob):
    # Parse ImageJ multipoint ROI blob into absolute (x,y) points
    # Header: 64 bytes
    _, _, _, _ = struct.unpack('>4sHBB', blob[:8])
    top, left, bottom, right, npts = struct.unpack('>hhhhh', blob[8:18])
    # Extract offsets
    off = blob[64:64 + 4 * npts]
    xs = struct.unpack(f'>{npts}h', off[:2*npts])
    ys = struct.unpack(f'>{npts}h', off[2*npts:])
    # Reconstruct absolute points
    return [(left + dx, top + dy) for dx, dy in zip(xs, ys)]

def generate_multipoint_roi_bytes(points):
    """
    Generate an ImageJ multipoint (point) ROI blob with a header matching
    the ground-truth structure but dynamically setting top/left/bottom/right
    and number of points.
    """
    if not points:
        return b''

    # Round coords, compute bounding box
    xs = [int(round(x)) for x, y in points]
    ys = [int(round(y)) for x, y in points]
    left, top = min(xs), min(ys)
    max_x, max_y = max(xs), max(ys)

    # Exclusive bottom/right
    width = max_x - left + 1
    height = max_y - top + 1
    bottom = top + height   # max_y + 1
    right = left + width    # max_x + 1
    npoints = len(points)

    # Static header template (indices 0–63 from ground-truth example)
    header = bytearray([
         73,111,117,116, 0,228,10,  0,
          0,  0,  0,  0,  0,  0,  0,  0,
          0,  0,  0,  0,  0,  0,  0,  0,
          0,  0,  0,  0,  0,  0,  0,  0,
          0,  0,  0,  0,  0,  0,  0,  0,
          0,  0,  0,  0,  0,  0,  0,  0,
          0,  0,  0,  0,  0,  0,  0,  0,
          0,  0,  0,  0,  0,  0,  0,  0
    ])
    # Override dynamic fields
    header[4:6] = struct.pack('>h', 228)         # version
    header[6]   = 10                              # ROI type = point
    header[7]   = 0                               # options
    header[8:10] = struct.pack('>h', top)         # top
    header[10:12] = struct.pack('>h', left)       # left
    header[12:14] = struct.pack('>h', bottom)     # bottom (exclusive)
    header[14:16] = struct.pack('>h', right)      # right  (exclusive)
    header[16:18] = struct.pack('>h', npoints)    # number of points

    # Build coordinate arrays
    coords = bytearray()
    for x, y in points:
        coords.extend(struct.pack('>h', int(round(x)) - left))
    for x, y in points:
        coords.extend(struct.pack('>h', int(round(y)) - top))

    return bytes(header) + bytes(coords)