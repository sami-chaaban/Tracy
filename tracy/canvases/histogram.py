from ._shared import *

class HistogramCanvas(FigureCanvas):

    def __init__(self, navigator, parent=None):
        self.navigator = navigator
        self.fig = Figure(figsize=(5.5, 3), constrained_layout=True)
        self.ax = self.fig.add_subplot(111)
        self.fig.patch.set_facecolor("white")
        self.ax.set_facecolor("white")
        super().__init__(self.fig)
        self.setParent(parent)
        self.current_background = None
        # Initially, hide the axes to appear blank.
        self.ax.axis("off")
        self.draw()
        self.fig.patch.set_alpha(0)
        self.ax.patch.set_alpha(0)
        self.ax.set_facecolor('none')
        self._update_pending = False
        self._last_histogram_params = (None, None, None, None, None, None, None, None)

    def resizeEvent(self, event):
        # Optionally, recalculate any subplot parameters here based on self.width() or self.height()
        # self.fig.subplots_adjust(left=0.16, right=0.95, bottom=0.3, top=0.9)
        # self.fig.tight_layout()  # Or set constrained_layout=True initially
        super().resizeEvent(event)

    def update_histogram(self, image, center, crop_size, sigma=None, intensity=None, background=None, peak=None, pointcolor="magenta"):
        """
        Instead of updating immediately, store the parameters and schedule an update
        only once per 16 ms (about 60 FPS).
        """

        self._last_histogram_params = (image, center, crop_size, sigma, intensity, background, peak, pointcolor)

        throttletime = 1

        if getattr(self.navigator, "looping", False):
            # fade the canvas back to half-opacity
            self.fig.patch.set_alpha(0.5)
            self.ax.clear()
            self.ax.axis("off")
            self.ax.text(
                0.5, 0.5, "Playback in progress",
                ha="center", va="center",
                transform=self.ax.transAxes,
                fontsize=14, color="grey"
            )
            self.draw()
            return
        
        if not self.isVisible():
            throttletime = 400
        # Save the latest parameters
        if not self._update_pending:
            self._update_pending = True
            # Schedule a delayed update
            QTimer.singleShot(throttletime, self._throttled_histogram_update)

    def _throttled_histogram_update(self):
        """This method is called after a short delay to perform the heavy update."""
        self._update_pending = False
        image, center, crop_size, sigma, intensity, background, peak, pointcolor = self._last_histogram_params
        self._do_update_histogram(image, center, crop_size, sigma, intensity, background, peak, pointcolor)

    def _do_update_histogram(self, image, center, crop_size, sigma=None, intensity=None, background=None, peak=None, pointcolor="magenta"):
        # --- Decide which portion of the image to use.
        if center is None or np.isnan(center[0]) or np.isnan(center[1]):
            subimage = image
            criteria_counts = None  # no criteria histogram
        else:
            H, W = image.shape
            half = crop_size // 2
            cx_int = int(round(center[0]))
            cy_int = int(round(center[1]))
            x1_crop = max(0, cx_int - half)
            x2_crop = min(W, cx_int + half)
            y1_crop = max(0, cy_int - half)
            y2_crop = min(H, cy_int + half)
            subimage = image[y1_crop:y2_crop, x1_crop:x2_crop]

        if subimage.size == 0:
            return

        # --- Compute histogram on the subimage.
        counts, bin_edges = np.histogram(subimage, bins=50)
        bin_centers = (bin_edges[:-1] + bin_edges[1:]) / 2

        # --- background
        self.current_background = background

        # --- Optionally compute a criteria histogram if sigma is provided.
        if (center is not None and not np.isnan(center[0]) and not np.isnan(center[1])
                and sigma is not None):
            sub_center_x = cx_int - x1_crop
            sub_center_y = cy_int - y1_crop
            y_indices, x_indices = np.indices(subimage.shape)
            dist = np.sqrt((x_indices - sub_center_x)**2 + (y_indices - sub_center_y)**2)
            sigma_mask = dist <= 2 * sigma
            criteria_pixels = subimage[sigma_mask]
            criteria_counts, _ = np.histogram(criteria_pixels, bins=bin_edges)
        else:
            criteria_counts = None

        # --- Clear axis, plot the histogram, overlay criteria if available.
        self.ax.clear()
        self.fig.patch.set_alpha(1.0)
        self.ax.set_facecolor("white")
        self.ax.set_xlabel("Pixel Intensity (A.U.)", fontsize=12)
        self.ax.set_ylabel("Count", fontsize=12)
        self.ax.tick_params(axis='both', which='major', labelsize=12)
        
        bar_width = bin_edges[1] - bin_edges[0]
        self.ax.bar(bin_centers, counts, width=bar_width, color='#7da1ff', edgecolor='black')
        if criteria_counts is not None:
            self.ax.bar(bin_centers, criteria_counts, width=bar_width, color=pointcolor, edgecolor='black')
        
        try:
            if (
                isinstance(background, (int, float)) and
                isinstance(peak, (int, float)) and
                math.isfinite(background) and
                math.isfinite(peak)
            ):
                actualpeak = peak+background
                # --- draw background threshold line only ---
                self.ax.axvline(
                    background,
                    color='black', linestyle='--', linewidth=1.5,
                    solid_capstyle='round',
                    dash_capstyle='round',
                    label=f"Bkgr: {background:.2f}"
                )
                self.ax.axvline(
                    actualpeak,
                    color=pointcolor, linestyle='--', linewidth=1.5,
                    solid_capstyle='round',
                    dash_capstyle='round',
                    label=f"Peak: {actualpeak:.2f}"
                )
                legend = self.ax.legend(
                    loc="upper right",
                    fontsize=10,
                    frameon=True,
                    labelspacing=0.5,
                    handlelength=2
                )
                # style the box
                frame = legend.get_frame()
                frame.set_facecolor("white")
                frame.set_alpha(0.8)
                frame.set_edgecolor("none")
                frame.set_boxstyle("round,pad=0.2")

            if not getattr(self.fig, "layout_engine", None):
                self.fig.subplots_adjust(left=0.17, right=0.95, bottom=0.3, top=0.9)
            self.draw()

        except np.linalg.LinAlgError:
            pass
            #print("Warning: Singular matrix encountered when drawing axvline")
