import numpy as np

def calculate_velocities(spot_centers):
    """
    Create a velocity list that is the same length as spot_centers.
    The first spot center always gets a velocity of None.
    For every subsequent spot center, if the current and previous spot centers exist,
    compute the Euclidean distance (pixels/frame); otherwise, assign None.
    """
    n = len(spot_centers)
    velocities = [None] * n  # Create a list with the same number of elements.
    for i in range(1, n):
        prev = spot_centers[i - 1]
        curr = spot_centers[i]
        if prev is None or curr is None:
            velocities[i] = None
        else:
            dx = curr[0] - prev[0]
            dy = curr[1] - prev[1]
            velocities[i] = np.hypot(dx, dy)
    return velocities