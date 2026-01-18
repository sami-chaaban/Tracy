from ._shared import *
from .base import ImageCanvas

class MovieCanvas(ImageCanvas):
    def __init__(self, parent=None, navigator=None):
        super().__init__(parent)
        # Initialize panning and display attributes.
        self._is_panning = False
        self._pan_start = None
        self._orig_xlim = None
        self._orig_ylim = None

        self.fig.patch.set_alpha(0)
        self.ax.patch.set_alpha(0)

        self._im = None
        self.image = None
        self._vmin = None
        self._vmax = None
        self._default_vmin = None
        self._default_vmax = None

        self.enableInteraction = True

        self._update_pending = False
        self._inset_update_pending = False
        self._last_inset_params = None

        # New attributes for zooming:
        self.scale = 1.0  # Data units per pixel (uniform in x and y)
        self.padding = 1.25
        self.zoom_center = None  # in data (image) coordinates

        # Connect mouse events.
        self.mpl_connect("scroll_event", self.on_scroll)
        self.mpl_connect("button_press_event", self.on_mouse_press)
        self.mpl_connect("motion_notify_event", self.on_mouse_move)
        self.mpl_connect("button_release_event", self.on_mouse_release)

        self._manual_marker_active = False  # flag for key-controlled marker
        self._manual_marker_pos = None      # will hold [x, y]
        self._manual_marker_artist = None   # store the drawn marker

        self.sum_mode = False
        self.sum_frame_cache = {}

        self._norm_slider_settings = None  # to store normal mode slider settings
        self._sum_slider_settings = None 

        self.tempRoiLine = None
        self._roi_bg = None
        self.roiAddMode = False
        self.roiPoints = [] 

        self.navigator = navigator

        self.last_fitted_center = None
        self.last_fitted_sigma = None
        self.last_intensity_value = None

        self.manual_zoom = False

        self._ctrl_panning = False
        self._last_pan = 0.0

    def mousePressEvent(self, event):
        # Ctrl+Left → pretend it was Middle
        if event.button() == Qt.LeftButton and (event.modifiers() & Qt.ControlModifier):
            self._ctrl_panning = True
            fake = QMouseEvent(
                event.type(),
                event.pos(),
                Qt.MiddleButton,        # button
                Qt.MiddleButton,        # buttons (pressed state)
                event.modifiers()
            )
            super().mousePressEvent(fake)
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._ctrl_panning:
            fake = QMouseEvent(
                event.type(),
                event.pos(),
                Qt.MiddleButton,
                Qt.MiddleButton if event.buttons() & Qt.LeftButton else Qt.NoButton,
                event.modifiers()
            )
            super().mouseMoveEvent(fake)
        else:
            super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self._ctrl_panning = False
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event):
        # — finish fake Ctrl+Left pan —
        if self._ctrl_panning and event.button() == Qt.LeftButton:
            # 1) clear both flags
            self._ctrl_panning = False
            self._is_panning    = False

            # 2) send Matplotlib the “middle‐button released” so it can do its cleanup
            fake = QMouseEvent(
                event.type(),
                event.pos(),
                Qt.MiddleButton,
                Qt.NoButton,
                event.modifiers()
            )
            super().mouseReleaseEvent(fake)
            return

        # — finish real middle‐button pan —
        if event.button() == Qt.MiddleButton:
            self._is_panning = False
            super().mouseReleaseEvent(event)
            # do a blocking full draw then snapshot the ROI/marker background
            canvas = self.figure.canvas
            self.draw()                             # synchronous full redraw
            self._roi_bbox = self.ax.bbox           # cache axes bbox
            self._roi_bg   = canvas.copy_from_bbox(self._roi_bbox)
            return

        # — everything else —
        super().mouseReleaseEvent(event)

    def display_image(self, image, title=""):
        """
        Show a 2D image with its native data extent.
        We also initialize our zoom center and scale.
        """
        if image is None:
            return
        self.image = image
        h, w = image.shape
        # Set the image extent to the full image.
        extent = (-0.5, w - 0.5, -0.5, h - 0.5)
        # Initialize zoom_center to the image center if not set yet.
        if self.zoom_center is None:
            self.zoom_center = (w/2, h/2)
        # Set an initial scale such that the entire image is visible.
        # We choose scale so that the data width equals the widget width, unless the widget is not yet sized.
        widget_w = self.width() if self.width() > 0 else w
        widget_h = self.height() if self.height() > 0 else h
        # To show the entire image without zooming, choose the maximum scale needed to cover both dimensions.
        base = max(w / widget_w, h / widget_h)
        self.max_scale = base * self.padding
        self.scale = base
        
        # Draw or update the image.
        if self._im is None:
            self.ax.clear()
            cmap = "gray_r" if getattr(self.navigator, "inverted_cmap", False) else "gray"
            self._im = self.ax.imshow(image, cmap=cmap, vmin=self._vmin, vmax=self._vmax,
                                       origin='lower', extent=extent)
            self.ax.axis("off")
            self.draw()
        else:
            self._im.set_data(image)
            self._im.set_extent(extent)
            self.draw()
            
        # Adjust the view limits based on the new scale and current zoom_center.
        QTimer.singleShot(1, self.update_view)

    def update_image_data(self, image):
        """Update only the image data without changing the current axes limits."""
        if image is None:
            return
        self.image = image
        if self._im is None:
            # if never drawn, fall back
            self.display_image(image)
        else:
            # update pixels…
            self._im.set_data(image)
            # …draw…
            self.draw()
            # …then recapture blit backgrounds so hover/blit won’t restore an old frame
            canvas = self.figure.canvas
            # If navigator is running a blitted temp line, let navigator rebuild a clean bg
            if getattr(self.navigator, "temp_movie_analysis_line", None) is not None:
                self._bg = None
            else:
                self._bg = canvas.copy_from_bbox(self.ax.bbox)
            self._roi_bg = canvas.copy_from_bbox(self.ax.bbox)

    def update_view(self):
        if self.image is None or self.zoom_center is None:
            return

        # 1) compute new limits
        widget_w = self.width()
        widget_h = self.height()
        view_w = widget_w * self.scale
        view_h = widget_h * self.scale
        cx, cy = self.zoom_center
        self.ax.set_xlim(cx - view_w/2, cx + view_w/2)
        self.ax.set_ylim(cy - view_h/2, cy + view_h/2)

        # 2) **blocking** full draw
        self.draw()

        # 3) grab a fresh clean background
        #    (no animated artists in place yet)
        canvas = self.figure.canvas
        # If navigator is running a blitted temp line, let navigator rebuild a clean bg
        if getattr(self.navigator, "temp_movie_analysis_line", None) is not None:
            self._bg = None
        else:
            self._bg = canvas.copy_from_bbox(self.ax.bbox)
        self._roi_bbox = self.ax.bbox
        self._roi_bg   = canvas.copy_from_bbox(self._roi_bbox)

        # reset manual-zoom flag
        self.manual_zoom = False

    def on_scroll(self, event):
        if not self.enableInteraction or self.image is None or event.inaxes != self.ax:
            return
        
        if self.navigator.looping:
            self.manual_zoom = True
            self.navigator.stoploop(prompt=True)
        # 1) grab mouse‐data coords & old scale
        mx, my     = event.xdata, event.ydata
        old_scale  = self.scale
        if mx is None or my is None:
            return

        # 2) compute new scale
        base = 1.2
        if event.button == 'up':
            new_scale = old_scale / base
        else:
            new_scale = old_scale * base

        # clamp to max if set
        if hasattr(self, 'max_scale'):
            new_scale = min(new_scale, self.max_scale)

        # 3) recompute zoom_center so that (mx,my) stays stationary
        cx, cy = self.zoom_center
        ratio  = new_scale / old_scale
        new_cx = mx + (cx - mx) * ratio
        new_cy = my + (cy - my) * ratio

        # 4) store & schedule redraw
        self.scale       = new_scale
        self.zoom_center = (new_cx, new_cy)
        self.update_view()
        # schedule a single zoom/pan update per event loop
    #     if not self._update_pending:
    #         self._update_pending = True
    #         QTimer.singleShot(1, self._perform_throttled_update)

    # def _perform_throttled_update(self):
    #     """
    #     Perform the zoom/pan update in a throttled manner.
    #     """
    #     # full view update then clear the pending flag
    #     self.update_view()
    #     self._update_pending = False
        
    def on_mouse_press(self, event):
        self.manual_zoom = True
        self.navigator._stop_animation = True  
        if not self.enableInteraction:
            return
        if event.inaxes != self.ax:
            return
        if event.button == 2:  # middle-click for panning
            self._is_panning = True
            self._pan_start = (event.x, event.y)
            self._orig_xlim = self.ax.get_xlim()
            self._orig_ylim = self.ax.get_ylim()
            self._last_pan = time.perf_counter()

    def on_mouse_move(self, event):
        if not self._is_panning or event.inaxes != self.ax:
            return
        # throttle pan updates to ~50 Hz
        now = time.perf_counter()
        if now - self._last_pan < 0.02:
            return
        self._last_pan = now

        inv = self.ax.transData.inverted()
        prev_data = inv.transform(self._pan_start)
        current_data = inv.transform((event.x, event.y))
        dx = current_data[0] - prev_data[0]
        dy = current_data[1] - prev_data[1]

        # update the zoom center
        cx, cy = self.zoom_center
        self.zoom_center = (cx - dx, cy - dy)

        # update pan origin for next delta
        self._pan_start = (event.x, event.y)
        self.manual_zoom = True
        
        self.update_view()
        # schedule a single, throttled redraw
        # if not self._update_pending:
        #     self._update_pending = True
        #     QTimer.singleShot(0, self._perform_throttled_update)

    def on_mouse_release(self, event):
        # this is the Matplotlib MouseEvent handler — do NOT call the Qt super()
        if event.button == 2 and event.inaxes == self.ax:
            # finish the pan: full redraw + fresh blit‐background
            self.draw()
            canvas = self.figure.canvas
            self._roi_bbox = self.ax.bbox
            self._roi_bg   = canvas.copy_from_bbox(self._roi_bbox)
            # also update the general bg used for blit‐markers
            self._bg = canvas.copy_from_bbox(self.ax.bbox)
            self.manual_zoom = False

    def resizeEvent(self, event):
        # 1) remember current view center in data coords
        cur_xlim = self.ax.get_xlim()
        cur_ylim = self.ax.get_ylim()
        self.zoom_center = (
            (cur_xlim[0] + cur_xlim[1]) * 0.5,
            (cur_ylim[0] + cur_ylim[1]) * 0.5
        )

        # 2) do the normal resize
        super().resizeEvent(event)
        # Recompute max_scale on resize so zoom-out still fills the canvas
        if hasattr(self, 'image') and self.image is not None:
            h, w = self.image.shape[:2]
            widget_w = self.width() if self.width() > 0 else w
            widget_h = self.height() if self.height() > 0 else h
            base = max(w / widget_w, h / widget_h)
            self.max_scale = base * self.padding

        # 3) redraw using the existing scale & center
        self.update_view()
        
    def update_inset(self, image, center, crop_size, zoom_factor=2,
                    fitted_center=None, fitted_sigma=None,
                    fitted_peak=None, offset=None, intensity_value=None, pointcolor="magenta"):
        # store params
        self._last_inset_params = (image, center, crop_size, zoom_factor,
                                fitted_center, fitted_sigma,
                                fitted_peak, offset, intensity_value, pointcolor)
        
        widget = self.navigator.zoomInsetWidget
        fig = widget.figure

        frame = self.navigator.zoomInsetFrame
        # record default size once
        if not hasattr(self, '_default_inset_size'):
            self._default_inset_size = (frame.width(), frame.height())
        # ensure frame & widget can freely resize
        # frame.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        # self.navigator.zoomInsetWidget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

        # ensure 3D axes exists
        if not hasattr(self, 'inset_ax3d'):
            self.inset_ax3d = fig.add_subplot(111, projection='3d')
            self.inset_ax3d.set_axis_off()
            self.inset_ax3d.set_visible(False)

            # hook enter/leave
            cid = fig.canvas
            self._enter_cid = cid.mpl_connect('axes_enter_event', self._on_inset_enter)
            self._leave_cid = cid.mpl_connect('axes_leave_event', self._on_inset_leave)
            self._scroll3d_cid = cid.mpl_connect('scroll_event', self._on_inset_scroll)

        if not getattr(self, '_inset_update_pending', False):
            self._inset_update_pending = True
            # if the inset is currently visible, fire ASAP; otherwise wait 400 ms
            delay = 0 if self.navigator.zoomInsetFrame.isVisible() else 400
            QTimer.singleShot(delay, self._throttled_update_inset)

    def _on_inset_enter(self, event):
        if self.navigator.looping:
            self.navigator.stoploop()
        # if self.navigator.sumBtn.isChecked():
        #     self.navigator.sumBtn.setChecked(False)
        if event.inaxes is self.inset_ax3d or event.inaxes is self.navigator.zoomInsetWidget.ax:
            # hide 2D
            self.navigator.zoomInsetWidget.ax.set_visible(False)
            # show 3D
            self.inset_ax3d.set_visible(True)
            self._draw_threed_inset()

    def _clear_threed_inset(self):
        """Erase the 3D inset and hide its frame."""
        ax3d = self.inset_ax3d
        ax3d.cla()                # clear contents
        ax3d.set_axis_off()       # hide axes
        self.navigator.zoomInsetFrame.setVisible(False)
        self.navigator.zoomInsetWidget.draw()

    def _draw_threed_inset(self):

        """
        The heavy‐lifting routine: crops, zooms, builds the 3D bars +
        Gaussian cap, and then makes everything visible.
        """
        params = self._last_inset_params
        if not params or len(params) != 10:
            return
        (image, center, crop_size, zoom_factor,
        fitted_center, fitted_sigma,
        fitted_peak, offset, intensity_value, pointcolor) = params
        

        # sanity
        if image is None or center is None or np.isnan(center[0]) or np.isnan(center[1]):
            return

        # --- crop & zoom ---
        half = crop_size/2.0
        cx, cy = center
        x1, x2 = cx-half, cx+half
        y1, y2 = cy-half, cy+half
        out_shape = (int(round(y2-y1)), int(round(x2-x1)))
        cropped = subpixel_crop(image, x1, x2, y1, y2, out_shape)
        zoomed = scipy.ndimage.zoom(cropped, zoom_factor, order=0)

        # build/reset axes
        ax3d = self.inset_ax3d
        ax3d.cla()

        # grid & bars
        h,w = zoomed.shape
        x = np.linspace(x1, x2, w);  y = np.linspace(y1, y2, h)
        X,Y = np.meshgrid(x,y)
        xpos, ypos = X.ravel(), Y.ravel()
        # decide on a norm based on the data range:
        dz_raw = zoomed.ravel()
        dz = dz_raw - (float(offset) if offset is not None else 0.0)
        dx,dy = (x2-x1)/w, (y2-y1)/h

        # determine bar bases & heights
        z0     = np.minimum(dz, 0.0)
        height = np.abs(dz)

        dx = (x2 - x1) / w
        dy = (y2 - y1) / h

        # darkest (black) at min(dz), lightest (white) at max(dz)
        norm = mcolors.Normalize(vmin=dz.min(), vmax=dz.max())
        cmap_name = "gray_r" if self.navigator.inverted_cmap else "gray"
        cmap = cm.get_cmap(cmap_name)
        cols = cmap(norm(dz))
        cols[:, 3] = 1  # alpha = 1

        bars = ax3d.bar3d(
            xpos, ypos, z0,
            dx, dy, height,
            color=cols,
            edgecolor='none',
            linewidth=0,
            shade=False
        )
        bars.set_sort_zpos(0.0)

        # Gaussian cap
        if fitted_center is not None and fitted_sigma is not None and fitted_peak is not None:
            x0,y0 = fitted_center;  A = fitted_peak;  σ = fitted_sigma
            G = A*np.exp(-(((X-x0)**2+(Y-y0)**2)/(2*σ**2)))
            z_lift = dz.max()*0 # off for now
            G += z_lift
            mesh_color = pointcolor or "#FF4FA3"
            mesh_rgba = mcolors.to_rgba(mesh_color, 0.65)
            fill_rgba = mcolors.to_rgba(mesh_color, 0.18)

            surf = ax3d.plot_surface(
                X, Y, G,
                color=fill_rgba,
                rstride=1, cstride=1,
                linewidth=0,
                antialiased=True,
                shade=True,
                alpha=fill_rgba[3],
            )
            surf.set_sort_zpos(float(G.max()))

            wf = ax3d.plot_wireframe(
                X, Y, G,
                color=mesh_rgba,
                rstride=2, cstride=2,
                linewidth=0.8,
                alpha=mesh_rgba[3],
            )
            wf.set_zorder(10)
            z_top = G.max()
        else:
            z_top = dz.max()

        # style & zoom
        ax3d.view_init(elev=60, azim=275)
        ax3d.set_axis_off()
        ax3d.set_facecolor((0,0,0,0));  ax3d.set_xlim(x1,x2);  ax3d.set_ylim(y1,y2)
        if not np.isfinite(z_top) or z_top <= 0:
            # fallback to a small positive range, or let Matplotlib autoscale
            try:
                ax3d.autoscale(z=True)
            except Exception:
                pass
        else:
            ax3d.set_zlim(0, z_top)
        for a in (ax3d.xaxis, ax3d.yaxis, ax3d.zaxis):
            a.pane.fill = False;  a.pane.set_edgecolor('none')
        ax3d.grid(False)

        # finally make it visible
        self.navigator.zoomInsetWidget.draw()
        self.navigator.zoomInsetFrame.setVisible(True)

        # update the labels
        if fitted_center is not None:
            self.navigator.zoomInsetLabel.setText(f"{fitted_center[0]:.2f}, {fitted_center[1]:.2f}")
        else:
            self.navigator.zoomInsetLabel.setText("")
        if fitted_center is not None and intensity_value is not None:
            self.navigator.zoomInsetIntensityLabel.setText(f"{intensity_value:.2f}")
        else:
            self.navigator.zoomInsetIntensityLabel.setText("")


    def _on_inset_scroll(self, event):
        if event.inaxes is not self.inset_ax3d:
            return

        frame = self.navigator.zoomInsetFrame
        base   = 1.15
        factor = base if event.button == 'up' else 1/base

        x, y, w, h = frame.geometry().getRect()
        new_w = max(self._default_inset_size[0], min(800, int(w * factor)))
        new_h = max(self._default_inset_size[1], min(800, int(h * factor)))

        # keep top-right fixed
        new_x = x + w - new_w

        # just move + resize the frame
        frame.move(new_x, y)
        frame.resize(new_w, new_h)

        # tell Qt to redo the layout for its children
        frame.layout().invalidate()
        frame.layout().activate()

    def _on_inset_leave(self, event):
        if event.inaxes in (self.inset_ax3d, self.navigator.zoomInsetWidget.ax):
            frame = self.navigator.zoomInsetFrame
            w0, h0 = self._default_inset_size
            geom = frame.geometry()
            new_x = geom.x() + geom.width() - w0

            frame.move(new_x, geom.y())
            frame.resize(w0, h0)
            frame.layout().invalidate()
            frame.layout().activate()

            # existing visibility logic
            self.inset_ax3d.set_visible(False)
            self.navigator.zoomInsetWidget.ax.set_visible(True)
            self._throttled_update_inset()

    def _throttled_update_inset(self):
        """Perform the heavy inset update using the most recent parameters."""
        self._inset_update_pending = False
        params = self._last_inset_params
        if not params or not isinstance(params, tuple) or len(params) !=10:
            return
        
        image, center, crop_size, zoom_factor, fitted_center, fitted_sigma, fitted_peak, offset, intensity_value, pointcolor = self._last_inset_params

        if image is None:
            return
        if center is None or np.isnan(center[0]) or np.isnan(center[1]):
            print("Warning: update_inset received invalid center:", center)
            return
        half = crop_size / 2.0
        x_center, y_center = center[0], center[1]
        x1, x2 = x_center - half, x_center + half
        y1, y2 = y_center - half, y_center + half
        output_shape = (int(round(y2 - y1)), int(round(x2 - x1)))
        cropped = subpixel_crop(image, x1, x2, y1, y2, output_shape) 
        zoomed = scipy.ndimage.zoom(cropped, zoom_factor, order=0)
        self.source_image = image
        self.zoom_extent = (x1, x2, y1, y2)
        
        if hasattr(self.navigator, "zoomInsetWidget"):
            # Update the inset widget’s axes.
            inset_ax = self.navigator.zoomInsetWidget.ax
            inset_ax.clear()
            self.navigator.zoomInsetWidget._im_inset = inset_ax.imshow(
                zoomed,
                cmap=("gray_r" if self.navigator.inverted_cmap else "gray"),
                origin='lower',
                extent=[x1, x2, y1, y2]
            )
            inset_ax.set_xticks([])
            inset_ax.set_yticks([])
            inset_ax.axis('off')

            # Update the overlay text; center it horizontally.
            if fitted_center is not None:
                fc_x, fc_y = fitted_center
                center_text = f"{fc_x:.2f}, {fc_y:.2f}"
            else:
                center_text = ""
            self.navigator.zoomInsetLabel.setText(center_text)
            # Optionally, draw magenta circles if fit parameters are provided.
            if fitted_center is not None and fitted_sigma is not None and intensity_value is not None:
                self.inset_circle = Circle(fitted_center, radius=fitted_sigma * 2, 
                                edgecolor=pointcolor, facecolor='none', linewidth=2, alpha=1)
                inset_ax.add_patch(self.inset_circle)

            if fitted_center is not None and intensity_value is not None:
                self.navigator.zoomInsetIntensityLabel.setText(f"{intensity_value:.2f}")
            else:
                self.navigator.zoomInsetIntensityLabel.setText("")
            self.navigator.zoomInsetWidget.draw()
            # Finally, show the whole zoom inset frame.
            self.navigator.zoomInsetFrame.setVisible(True)
        else:
            self.ax.clear()
            cmap = "gray_r" if getattr(self.navigator, "inverted_cmap", False) else "gray"
            self.ax.imshow(zoomed, cmap=cmap, origin='lower', extent=[x1, x2, y1, y2])
            self.ax.axis('off')

    def update_roi_drawing(self, current_pos):
        pts = list(self.roiPoints) + ([current_pos] if current_pos else [])
        if len(pts) < 2 or not self.roiAddMode:
            return

        xs, ys = zip(*pts)
        canvas = self.figure.canvas     # the QtAgg FigureCanvas

        if self.tempRoiLine is None:
            # 1) draw static image+axes
            canvas.draw()
            # 2) snapshot full axes region (no ROI)
            self._roi_bbox = self.ax.bbox
            self._roi_bg   = canvas.copy_from_bbox(self._roi_bbox)
            # 3) create the line artist (but don’t redraw full figure)
            self.tempRoiLine, = self.ax.plot(xs, ys, '--', linewidth=1.5, color='#81C784')
        else:
            # restore the clean background
            canvas.restore_region(self._roi_bg)
            # update the line
            self.tempRoiLine.set_data(xs, ys)
            # draw just that artist
            self.ax.draw_artist(self.tempRoiLine)
            # blit only the axes region
            canvas.blit(self._roi_bbox)

    def finalize_roi(self, suppress_display: bool = False):
        # Make sure we have at least two pointsf
        if not self.roiPoints or len(self.roiPoints) < 2:
            print("Not enough points to finalize ROI.")
            return
        
        #print("ROI points:", self.roiPoints)

        # Build the ROI dictionary using all collected points.
        # Use 'x' and 'y' keys expected by the conversion function.
        # and also store the full list as 'points' for any later processing.
        roi = {
            "type": "line",  # or "segmented_line"
            "x": [pt[0] for pt in self.roiPoints],
            "y": [pt[1] for pt in self.roiPoints],
            "points": self.roiPoints.copy()
        }

        # Generate the kymograph from the full ROI.
        kymo = self.generate_kymograph(roi)

        # Combine keys from both dictionaries.
        all_names = set(self.navigator.rois.keys()) | set(self.navigator.kymographs.keys())
        numeric_names = []
        for name in all_names:
            try:
                # Only append if conversion to int is possible.
                numeric_names.append(int(name))
            except (ValueError, TypeError):
                # If a key isn’t numeric, skip it.
                pass

        if numeric_names:
            max_num = max(numeric_names)
            next_num = max_num + 1
        else:
            next_num = 1
        name = f"{next_num:03d}"        

        # Store the ROI and kymograph with the same name.
        self.navigator.rois[name] = roi
        self.navigator.roiCombo.addItem(name)
        self.navigator.roiCombo.setEnabled(True)
        if not suppress_display:
            self.navigator.roiCombo.setCurrentText(name)
        self.navigator.update_roilist_visibility()

        if self.navigator.movie.ndim == 4:
            n_chan = self.navigator.movie.shape[self.navigator._channel_axis]
        else:
            n_chan = 1

        for ch in range(n_chan):
            kymo = self.generate_kymograph(roi, channel_override=ch)
            kymo_name = f"ch{ch+1}-{name}"
            self.navigator.kymographs[kymo_name] = kymo
            self.navigator.kymo_roi_map[kymo_name] = {
                "roi":      name,
                "channel":  ch+1,
                "orphaned": False
            }

        # self.navigator.last_kymo_by_channel[ch+1] = kymo_name

        self.navigator._last_roi = name
        self.navigator.kymoCombo.setEnabled(True)


        # Clear the temporary ROI markers and the stored points.
        self.roiPoints = []
        if self.tempRoiLine is not None:
            try:
                self.tempRoiLine.remove()
            except Exception:
                pass
            self.tempRoiLine = None

        if not suppress_display:
            self.navigator.update_kymo_list_for_channel()
            self.navigator.kymo_changed()
            self.navigator.update_kymo_visibility()
            self.navigator.update_kymo_list_for_channel()

        if not suppress_display:
            self.draw()

    def generate_kymograph(self, roi, channel_override=None):
        # --- Compute the ROI sample positions along the drawn line ---
        xs = np.array(roi["x"], dtype=float)
        ys = np.array(roi["y"], dtype=float)
        
        # Compute cumulative distances along the ROI.
        distances = np.sqrt(np.diff(xs)**2 + np.diff(ys)**2)
        cum_dist = np.concatenate(([0], np.cumsum(distances)))
        total_length = cum_dist[-1]
        
        # Determine the number of sample positions (at least 2)
        num_samples = max(int(total_length), 2)
        sample_positions = np.linspace(0, total_length, num_samples)
        
        # Interpolate the ROI coordinates at these positions.
        sample_x = np.interp(sample_positions, cum_dist, xs)
        sample_y = np.interp(sample_positions, cum_dist, ys)
        
        # --- Compute the tangent and then the normal direction at each sample point ---
        # Use finite differences (np.gradient) to approximate the tangent.
        tangent_dx = np.gradient(sample_x)
        tangent_dy = np.gradient(sample_y)
        # A unit normal can be taken as (-dy, dx)
        normal_x = -tangent_dy
        normal_y = tangent_dx
        norm = np.sqrt(normal_x**2 + normal_y**2)
        norm[norm == 0] = 1  # avoid division by zero if any
        normal_x /= norm
        normal_y /= norm

        # --- Obtain integration parameters ---
        # n pixels in either direction perpendicular to the line
        line_width = getattr(self.navigator, "line_width", 2)
        # Method: "max" (default) or "average"
        line_method = getattr(self.navigator, "line_integration_method", "max").lower()

        # Create an array of offsets along the normal direction.
        # This gives a 1D array from -line_width to +line_width.
        offsets = np.arange(-line_width, line_width + 1, dtype=float)  # shape (n_offsets,)
        n_offsets = offsets.size

        # For each sample point, compute its neighborhood along the normal.
        # These arrays have shape (num_samples, n_offsets).
        sample_x_full = sample_x[:, None] + normal_x[:, None] * offsets[None, :]
        sample_y_full = sample_y[:, None] + normal_y[:, None] * offsets[None, :]

        # Flatten the coordinate arrays so they can be passed to map_coordinates.
        # Note: the first row are y coordinates (rows) and the second row x coordinates.
        coords = np.vstack((sample_y_full.ravel(), sample_x_full.ravel()))

        # --- Retrieve the movie ---
        if hasattr(self, "navigator") and self.navigator.movie is not None:
            movie = self.navigator.movie
        else:
            return None

        # --- Process each frame using vectorized interpolation per frame ---
        kymo_rows = []
        n_frames = movie.shape[0]
        for i in range(n_frames):
            # For multi–channel movies, extract the 2D frame for the chosen channel.
            if movie.ndim == 4:
                frame = movie[i]
                if hasattr(self.navigator, "movieChannelCombo") and self.navigator.movieChannelCombo.isEnabled():
                    if channel_override is not None:
                        channel_index = channel_override
                    else:
                        channel_index = int(self.navigator.movieChannelCombo.currentText()) - 1
                else:
                    channel_index = 0
                if self.navigator._channel_axis == 1:
                    frame_2d = frame[channel_index]
                else:
                    frame_2d = frame[..., channel_index]
            else:
                # For 3D movies (single channel), take the frame directly.
                frame_2d = movie[i]

            # Use map_coordinates to extract the pixel values at all normal offsets,
            # in one vectorized call.
            patch_values = map_coordinates(frame_2d, coords, order=1, mode='reflect')
            # Reshape so that each row corresponds to one sample point (along the ROI)
            # and each column corresponds to one offset along the normal.
            patch_values = patch_values.reshape(num_samples, n_offsets)

            # Integrate along the normal direction based on the selected method.
            if line_method == "average":
                profile = np.mean(patch_values, axis=1)
            else:
                profile = np.max(patch_values, axis=1)
            kymo_rows.append(profile)

        kymo = np.vstack(kymo_rows)

        if self.navigator.applylogfilter:

            # kymo is the generated 2D NumPy array
            sigma = getattr(self.navigator, 'log_sigma', 1.5)

            # convert to float to avoid clipping
            kymo_f = kymo.astype(np.float32)

            # apply LoG (invert for positive edges)
            log_kymo = -gaussian_laplace(kymo_f, sigma=sigma)

            # normalize back to 1–255
            minv, maxv = log_kymo.min(), log_kymo.max()
            if maxv > minv:
                log_kymo = (log_kymo - minv) / (maxv - minv) * 254 + 1
            else:
                log_kymo = np.ones_like(log_kymo) * 128
            log_kymo = log_kymo.astype(np.uint8)

            # from bm3d import bm3d
            # from skimage.restoration import estimate_sigma
            # sigma_est = estimate_sigma(
            #     log_kymo,
            #     channel_axis=None,     # grayscale
            #     average_sigmas=True    # get one scalar back
            # )
            # denoised = bm3d(log_kymo, sigma_psd=sigma_est)

            # # return log_kymo
            # return overlay_trace_centers(denoised)
        
            return(log_kymo)
        
        else:

            return kymo

    def clear_temporary_roi_markers(self):
        # Clear any temporary ROI dotted line.
        if hasattr(self, 'tempRoiLine') and self.tempRoiLine is not None:
            try:
                self.tempRoiLine.remove()
            except Exception:
                pass
            self.tempRoiLine = None

        self.draw()

    def display_sum_frame(self):
        if self.navigator is None or self.navigator.movie is None:
            return

        movie = self.navigator.movie

        # figure out which channel key to use
        if movie.ndim == 4:
            try:
                ch = int(self.navigator.movieChannelCombo.currentText()) - 1
            except Exception:
                ch = 0

            if ch in self.sum_frame_cache:
                sum_frame = self.sum_frame_cache[ch]
            else:
                channel_axis = self.navigator._channel_axis
                idx = [slice(None)] * movie.ndim
                idx[channel_axis] = ch
                channel_movie = movie[tuple(idx)]
                sum_frame = np.max(channel_movie, axis=0)
                self.sum_frame_cache[ch] = sum_frame

        elif movie.ndim == 3:
            if movie.shape[0] <= 4:
                sum_frame = movie[0]
            else:
                sum_frame = np.max(movie, axis=0)
        else:
            sum_frame = movie

        # now render exactly like before
        self.image = sum_frame
        if self._im is None:
            self.ax.clear()
            self._im = self.ax.imshow(sum_frame, cmap="gray", origin='lower')
            self.ax.axis("off")
            self.draw()
        else:
            self._im.set_data(sum_frame)
            self.draw()

        # ── recapture blit backgrounds so future blits use the sum‐mode image ──
        canvas = self.figure.canvas
        # If navigator is running a blitted temp line, let navigator rebuild a clean bg
        if getattr(self.navigator, "temp_movie_analysis_line", None) is not None:
            self._bg = None
        else:
            self._bg = canvas.copy_from_bbox(self.ax.bbox)
        self._roi_bg = canvas.copy_from_bbox(self.ax.bbox)

    def clear_sum_cache(self, channel=None):
        """
        If channel is None, flush everything (e.g. on new movie).
        Otherwise just remove that one channel’s cache.
        """
        if channel is None:
            self.sum_frame_cache.clear()
        else:
            self.sum_frame_cache.pop(channel, None)

    def set_display_range(self, vmin, vmax):
        """
        Set the current display contrast range without modifying underlying data.
        Ensures that vmin is always less than vmax to avoid errors in normalization.
        """
        #print("displaying:", vmin, vmax)
        if vmin >= vmax:
            # If vmin is not less than vmax, adjust vmax to guarantee a valid range.
            # Here we choose an arbitrary minimal gap of 1.
            #print(f"Warning: vmin ({vmin}) >= vmax ({vmax}). Adjusting vmax to {vmin + 1}.")
            vmax = vmin + 1

        self._vmin = vmin
        self._vmax = vmax
        if self._im is not None:
            self._im.set_clim(self._vmin, self._vmax)
            self.draw_idle()

    def overlay_rectangle(self, cx, cy, size, color='#7da1ff'):
        # remove old
        if getattr(self, "rect_overlay", None) is not None:
            try:    self.rect_overlay.remove()
            except: pass

        half = size / 2.0
        x0, x1 = cx - half, cx + half
        y0, y1 = cy - half, cy + half

        # a closed polyline (4 corners + back to first)
        verts_x = [x0, x1, x1, x0, x0]
        verts_y = [y0, y0, y1, y1, y0]

        line, = self.ax.plot(
            verts_x,
            verts_y,
            color=color,
            linewidth=2,
            zorder=6,
            linestyle='-'
        )
        self.rect_overlay = line

    def is_zoomed_in(self):
        if self.image is None or not hasattr(self, "full_extent"):
            return False
        full_left, full_right, full_bottom, full_top = self.full_extent
        cur_xlim = self.ax.get_xlim()
        cur_ylim = self.ax.get_ylim()
        tol = 1e-6
        # Check if current limits are nearly the same as the full extent.
        return not (abs(cur_xlim[0] - full_left) < tol and abs(cur_xlim[1] - full_right) < tol and
                    abs(cur_ylim[0] - full_bottom) < tol and abs(cur_ylim[1] - full_top) < tol)

    def clear_canvas(self):
        """Clear the canvas by removing all overlays and resetting internal state."""
        # Clear the axes.
        self.ax.cla()
        # Remove stored image, marker, and any overlay objects.
        self._im = None
        self._marker = None
        # Clear any gaussian circle if present.
        self.remove_gaussian_circle()
        # Reset panning and manual zoom state.
        self._is_panning = False
        self._pan_start = None
        self._orig_xlim = None
        self._orig_ylim = None
        self.manual_zoom = False
        self.image = None

    def draw_manual_marker(self):
        """Draw a translucent circle at the current manual position."""
        # Remove any existing manual marker
        if getattr(self, "_manual_marker_artist", None) is not None:
            try:
                self._manual_marker_artist.remove()
            except Exception:
                pass

        # Draw a semi‑transparent circle
        x, y = self._manual_marker_pos
        radius = 3  # adjust as desired
        circ = Circle(
            (x, y),
            radius=radius,
            edgecolor='#7da1ff',
            facecolor='#7da1ff',
            alpha=0.6,
            linewidth=1.5
        )
        self._manual_marker_artist = circ
        self.ax.add_patch(circ)

    def clear_manual_marker(self):
        """Remove the manual marker circle from the canvas."""
        if getattr(self, "_manual_marker_artist", None) is not None:
            try:
                self._manual_marker_artist.remove()
            except Exception:
                pass
            self._manual_marker_artist = None

    def add_gaussian_circle(self, fitted_center, fitted_sigma, color="magenta"):
        if fitted_center is not None and fitted_sigma is not None:
            self.gaussian_circle = Circle(
                    fitted_center,
                    radius=2 * fitted_sigma,
                    edgecolor=color,
                    facecolor='none',
                    linewidth=2
                )
            self.ax.add_patch(self.gaussian_circle)

    def remove_gaussian_circle(self):
        removed = False
        if hasattr(self, "gaussian_circle") and self.gaussian_circle is not None:
            try:
                self.gaussian_circle.remove()
                removed = True
            except Exception as e:
                print("Error removing gaussian circle:", e)
            self.gaussian_circle = None
        return removed

    def draw_trajectories_on_movie(self):

        # 1) Clear any existing movie‐canvas markers
        self.clear_movie_trajectory_markers()

        # 2) If the overlay toggle is off, do nothing
        if not self.navigator.traj_overlay_button.isChecked():
            return

        # 3) Which table row is currently selected?
        selected_idx = self.navigator.trajectoryCanvas.table_widget.currentRow()

        # 4) Which movie channel is active? (so we skip mismatched trajectories)
        try:
            current_ch = int(self.navigator.movieChannelCombo.currentText())
        except (ValueError, AttributeError):
            current_ch = None

        # 5) Loop over all trajectories and draw them
        for idx, traj in enumerate(self.navigator.trajectoryCanvas.trajectories):
            # 5a) Skip if trajectory has a channel that doesn't match
            traj_ch = traj.get("channel", None)
            if traj_ch is not None and traj_ch != current_ch:
                continue

            # 5b) Are we highlighting this one?
            is_hl = (idx == selected_idx)

            # Build a label string like "3A"/"3B"
            traj_label = traj.get("file_index") or str(traj["trajectory_number"])

            # 5c) Draw the dashed "search‐center" line if original_coords exist
            original_coords = traj.get("original_coords", [])
            if original_coords:
                xs = [pt[0] for pt in original_coords]
                ys = [pt[1] for pt in original_coords]

                lw_search = 2.0 if is_hl else 1.5
                alpha_search = 0.9 if is_hl else 0.6
                z_search = 5 if is_hl else 1

                dotted_line, = self.ax.plot(
                    xs, ys,
                    color='#7da1ff',
                    linestyle='--',
                    linewidth=lw_search,
                    alpha=alpha_search,
                    zorder=z_search,
                    solid_capstyle='round',
                    dash_capstyle='round'
                )
                self.movie_trajectory_markers.append(dotted_line)

                # 5c-i) Annotate "A" at first point and "B" at last point, picker=True
                dispA = self.ax.transData.transform((xs[0], ys[0]))
                dispB = self.ax.transData.transform((xs[-1], ys[-1]))
                v = dispB - dispA
                norm = (v[0]**2 + v[1]**2) ** 0.5
                u = (v / norm) if norm else np.array([1.0, 0.0])
                offset_px = 15

                for (cx, cy, suffix), sign in [((xs[0], ys[0], 'A'), -1),
                                               ((xs[-1], ys[-1], 'B'), +1)]:
                    dx, dy = u * (offset_px * sign)
                    lbl = self.ax.annotate(
                        f"{traj_label}{suffix}",
                        xy=(cx, cy),
                        xytext=(dx, dy),
                        textcoords="offset points",
                        color=('white' if is_hl else 'black'),
                        fontsize=8,
                        fontweight="bold",
                        ha="center",
                        va="center",
                        bbox=dict(
                            boxstyle='circle,pad=0.3',
                            facecolor=('#7da1ff' if is_hl else '#cbd9ff'),
                            alpha=(0.9 if is_hl else 0.6),
                            linewidth=(1.5 if is_hl else 1.0)
                        ),
                        picker=True,      # Make the label clickable
                        zorder=(7 if is_hl else 2)
                    )
                    # Attach custom attribute so pick_event tells us which row was clicked:
                    lbl.traj_idx = idx
                    self.movie_trajectory_markers.append(lbl)

            # 5d) Draw the solid connecting line through spot_centers for every trajectory
            spot_centers = traj.get('spot_centers', [])
            xs_pts = [pt[0] if pt is not None else np.nan for pt in spot_centers]
            ys_pts = [pt[1] if pt is not None else np.nan for pt in spot_centers]

            scatter_kwargs, line_color = self.navigator._get_traj_colors(traj)

            # Remove any pre‐existing zorder in scatter_kwargs so we can supply our own
            scatter_kwargs = scatter_kwargs.copy()
            scatter_kwargs.pop('zorder', None)

            # Style for the connecting line
            lw_line = (2.0 if is_hl else 1.5)
            alpha_line = (0.9 if is_hl else 0.7)
            z_line = (6 if is_hl else 3)

            line = None
            pts_colors = scatter_kwargs.get("c")
            if isinstance(pts_colors, (list, tuple, np.ndarray)) and len(pts_colors) == len(xs_pts):
                segs = []
                seg_colors = []
                for i in range(len(xs_pts) - 1):
                    if (np.isnan(xs_pts[i]) or np.isnan(ys_pts[i])
                            or np.isnan(xs_pts[i + 1]) or np.isnan(ys_pts[i + 1])):
                        continue
                    segs.append([[xs_pts[i], ys_pts[i]], [xs_pts[i + 1], ys_pts[i + 1]]])
                    seg_colors.append(pts_colors[i])
                if segs:
                    line = LineCollection(
                        segs,
                        colors=seg_colors,
                        linewidths=lw_line,
                        alpha=alpha_line,
                        zorder=z_line
                    )
                    self.ax.add_collection(line)

            if line is None:
                line, = self.ax.plot(
                    xs_pts, ys_pts,
                    linestyle='-',
                    color=(line_color),
                    linewidth=lw_line,
                    alpha=alpha_line,
                    zorder=z_line
                )
            self.movie_trajectory_markers.append(line)

        # 5e) Draw scatter points ONLY for the highlighted trajectory
            if is_hl and not getattr(self.navigator, "kymo_anchor_edit_mode", False):
                # Bump size and add black edge
                scatter_kwargs.update(s=15, edgecolors='black', linewidths=0.5)
                z_scatter = 6
                scatter = self.ax.scatter(
                    xs_pts, ys_pts,
                    picker=True,
                    zorder=z_scatter,
                    **scatter_kwargs
                )
                scatter.traj_idx = idx
                self.movie_trajectory_markers.append(scatter)

        # 6) Finally, request a redraw
        self.ax.figure.canvas.draw_idle()

    def clear_movie_trajectory_markers(self):
        if hasattr(self, "movie_trajectory_markers"):
            for marker in self.movie_trajectory_markers:
                try:
                    marker.remove()
                except Exception:
                    pass
            self.movie_trajectory_markers = []
        else:
            self.movie_trajectory_markers = []
    
    def remove_inset_circle(self):
        if hasattr(self, "inset_circle"):
            try:
                self.inset_circle.remove()
            except Exception as e:
                print("Error removing inset circle during invalidation:", e)
            self.inset_circle = None
