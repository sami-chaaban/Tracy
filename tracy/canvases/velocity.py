from ._shared import *

class VelocityCanvas(FigureCanvas):
    def __init__(self, parent=None, navigator=None):
        # Create a figure with a white background.
        self.fig = Figure(figsize=(5.5, 3), constrained_layout=True)
        # Create a single axes.
        self.ax_vel = self.fig.add_subplot(111)
        # Set the axes background to white.
        self.ax_vel.set_facecolor('white')
        # Optionally remove all spines so no borders show:
        self.ax_vel.axis("off")
        self.fig.patch.set_alpha(0)
        self.ax_vel.patch.set_alpha(0)
        self.ax_vel.set_facecolor('none')
        # Initialize the FigureCanvas with this figure.
        super().__init__(self.fig)
        self.setParent(parent)
        self.current_index = 0

        self.navigator = navigator

        self.draw_idle()

    def resizeEvent(self, event):
        # Optionally, recalculate any subplot parameters here based on self.width() or self.height()
        # self.fig.subplots_adjust(left=0.16, right=0.95, bottom=0.3, top=0.9)
        # self.fig.tight_layout()  # Or set constrained_layout=True initially
        super().resizeEvent(event)

    def plot_velocity_histogram(self, velocities, ax=None):
        """
        Plot a histogram of velocities on the provided axes (or self.ax_vel).
        Also draws a vertical line showing the average speed
        and a vertical line showing the net speed (distance from first to last point divided by total frame difference)
        converted to μm/s.
        """
        if ax is None:
            ax = self.ax_vel

        # 1) Clear & filter out None / non-finite
        ax.clear()
        valid = np.asarray([v for v in velocities if v is not None])
        valid = valid[np.isfinite(valid)]

        # 2) Compute net_speed exactly as before...
        net_speed = 0
        nav = self.navigator
        if (nav is not None
            and hasattr(nav, 'analysis_frames') and len(nav.analysis_frames) >= 2
            and hasattr(nav, 'analysis_original_coords') and len(nav.analysis_original_coords) >= 2):
            start_frame, sx, sy = nav.analysis_start
            end_frame,   ex, ey = nav.analysis_end
            dx, dy = ex - sx, ey - sy
            dt = end_frame - start_frame
            px_dist = np.hypot(dx, dy)
            if nav.pixel_size is not None and nav.frame_interval is not None:
                um_dist = px_dist * nav.pixel_size / 1000.0
                dt_s = dt * nav.frame_interval / 1000.0
                net_speed = um_dist / dt_s if dt_s > 0 else 0
            else:
                net_speed = px_dist / dt

        # 3) Scale to μm/s if possible
        xlabel = "Speed (px/frame)"
        if valid.size and nav and nav.pixel_size and nav.frame_interval:
            valid = valid * (nav.pixel_size / nav.frame_interval)
            xlabel = r"Speed ($\mathrm{\mu m/s}$)"

        ax.set_xlabel(xlabel, fontsize=12)
        ax.set_ylabel("Count", fontsize=12)
        ax.tick_params(axis='both', which='major', labelsize=12)

        legend_handles = []

        # 4a) If we have data, plot it + avg line + real net line
        if valid.size > 0:
            ax.hist(valid, bins='auto', color='#7da1ff', edgecolor='black')
            avg = np.mean(valid)
            # net line
            net_line = ax.axvline(net_speed,
                                color='black', linestyle='--',
                                linewidth=1.5, solid_capstyle='round',
                                dash_capstyle='round')
            legend_handles.append(Line2D([], [], color='black', linestyle='--',
                                        label=f"Net: {net_speed:.2f}"))
            # avg line
            avg_line = ax.axvline(avg,
                                color='grey', linestyle='--',
                                linewidth=1.5, solid_capstyle='round',
                                dash_capstyle='round')
            legend_handles.append(Line2D([], [], color='grey', linestyle='--',
                                        label=f"Avg: {avg:.2f}"))

        # 4b) If no data, show placeholder + only proxy net legend
        else:
            ax.text(0.5, 0.5, "No valid speed data",
                    ha='center', va='center',
                    transform=ax.transAxes, color='grey')
            ax.axis("off")
            # proxy net legend
            legend_handles.append(Line2D([], [], color='black', linestyle='--',
                                        label=f"Net: {net_speed:.2f}"))

        # 5) Draw the legend from our handles
        legend = ax.legend(handles=legend_handles,
                        loc="upper right", fontsize=10,
                        frameon=True, labelspacing=0.5, handlelength=2)
        frame = legend.get_frame()
        frame.set_facecolor("white")
        frame.set_alpha(0.8)
        frame.set_edgecolor("none")
        frame.set_boxstyle("round,pad=0.2")

        ax.figure.subplots_adjust(left=0.17, right=0.95, bottom=0.3, top=0.9)
        self.draw_idle()
