from ._shared import *

class NavigatorMovieMixin:
    def _maybe_flip_movie_ylim(self, ylim):
        if getattr(self, "flip_movie_y", False):
            return (ylim[1], ylim[0])
        return ylim

    def _set_movie_roi_cursor(self, in_image: bool):
        if not hasattr(self, "movieCanvas"):
            return
        if not getattr(self.movieCanvas, "roiAddMode", False):
            return
        if not in_image:
            self.movieCanvas.setCursor(Qt.ArrowCursor)
            return
        cursor = getattr(self, "_movie_roi_cursor", None)
        if cursor is None:
            cursor = self._make_colored_circle_cursor(shade='green')
            self._movie_roi_cursor = cursor
        self.movieCanvas.setCursor(cursor)

    def on_frame_slider_changed(self, frame_idx):
        """
        Called when the user drags or clicks the frame slider.
        We'll display that frame in the MovieCanvas, plus update the label.
        """
        self.set_current_frame(frame_idx)

    def set_current_frame(self, frame_number):
        if self.movie is None:
            return
        max_frame = self.movie.shape[0]
        frame_number = max(0, min(frame_number, max_frame - 1))
        
        # Save current view limits.
        current_xlim = self.movieCanvas.ax.get_xlim()
        current_ylim = self.movieCanvas.ax.get_ylim()
        
        # Update slider and label.
        self.frameSlider.blockSignals(True)
        self.frameSlider.setValue(frame_number)
        self.frameSlider.blockSignals(False)
        self.frameNumberLabel.setText(f"{frame_number + 1}")
        
        # Get the new frame.
        if self.movieCanvas.sum_mode:
            self.movieCanvas.display_sum_frame()  # You may wish to modify display_sum_frame too.
        else:
            frame_image = self.get_movie_frame(frame_number)
            if frame_image is not None:
                # Update only the image data (without recalculating view limits)
                self.movieCanvas.update_image_data(frame_image)
        
        # Restore the saved view limits (thus preserving the manual zoom)
        self.movieCanvas.ax.set_xlim(current_xlim)
        self.movieCanvas.ax.set_ylim(current_ylim)

        # Refresh overlays so frame-dependent styling (e.g., fades) stays in sync.
        try:
            self.movieCanvas.draw_trajectories_on_movie()
        except Exception:
            pass

        self.movieCanvas.draw_idle()
        canvas = self.movieCanvas.figure.canvas
        self.movieCanvas._bg = canvas.copy_from_bbox(self.movieCanvas.ax.bbox)

    def jump_to_analysis_point(self, index, animate="ramp", zoom=False):

        # ——— Early exits & locals ———
        if not self.analysis_frames or not self.analysis_search_centers:
            return
        n = len(self.analysis_frames)
        if index < 0 or index >= n:
            return

        self.cancel_left_click_sequence()
        if self.sumBtn.isChecked():
            self.sumBtn.setChecked(False)
        if self.refBtn.isChecked():
            self.refBtn.setChecked(False)

        mc = self.movieCanvas
        kc = self.kymoCanvas
        ic = getattr(self, 'intensityCanvas', None)
        hc = getattr(self, 'histogramCanvas', None)

        # cache arrays once
        centers = np.asarray(self.analysis_search_centers)  # shape (N,2)
        frame = self.analysis_frames[index]
        cx, cy = centers[index]

        # block widgets
        self.frameSlider.blockSignals(True)
        if hasattr(self, 'analysisSlider'):
            self.analysisSlider.blockSignals(True)

        # disable repaint until we're done
        mc.setUpdatesEnabled(False)
        kc.setUpdatesEnabled(False)

        try:
            # ——— 1) Compute new limits ———
            cur_xlim = mc.ax.get_xlim()
            cur_ylim = mc.ax.get_ylim()
            w = abs(cur_xlim[1] - cur_xlim[0])
            h = abs(cur_ylim[1] - cur_ylim[0])

            if zoom:
                r     = self.searchWindowSpin.value()

                # 1) get the container’s current size and pixel aspect ratio
                cont = self.movieDisplayContainer
                pw   = cont.width()   # pixel width
                ph   = cont.height()  # pixel height
                if ph == 0:
                    aspect = 1.0
                else:
                    aspect = pw / ph   # e.g. 16/9 = 1.78

                # 2) define zoom height in data units
                fov_y = 10 * r
                #    then compute the matching width
                fov_x = fov_y * aspect

                half_x = fov_x / 2.0
                half_y = fov_y / 2.0

                new_xlim = (cx - half_x, cx + half_x)
                new_ylim = self._maybe_flip_movie_ylim((cy - half_y, cy + half_y))

                # 3) mark manual zoom & animate or set directly
                mc.manual_zoom = True

                if animate == "ramp":
                    self.animate_axes_transition(new_xlim, new_ylim, duration=300)
                elif animate == "linear":
                    self.animate_view_transition(new_xlim, new_ylim, duration=15)
                else:
                    mc.ax.set_xlim(new_xlim)
                    mc.ax.set_ylim(new_ylim)
                    mc.zoom_center = ((new_xlim[0] + new_xlim[1]) / 2,
                                    (new_ylim[0] + new_ylim[1]) / 2)

            else:
                new_xlim = (cx - w/2, cx + w/2)
                new_ylim = self._maybe_flip_movie_ylim((cy - h/2, cy + h/2))
                if animate == "ramp":
                    self.animate_axes_transition(new_xlim, new_ylim, duration=300)
                elif animate == "linear":
                    self.animate_view_transition(new_xlim, new_ylim, duration=15)
                else:
                    mc.ax.set_xlim(new_xlim)
                    mc.ax.set_ylim(new_ylim)
                    mc.zoom_center = ((new_xlim[0]+new_xlim[1])/2,
                                    (new_ylim[0]+new_ylim[1])/2)

            # ——— 3) Update the image frame ———
            # print("jump_to_analysis_point analysis_channel", self.analysis_channel)

            if self.analysis_channel is not None:
                self._select_channel(self.analysis_channel)

            frame_img = self.get_movie_frame(frame)
            if frame_img is None:
                return
            mc.update_image_data(frame_img)

            # ——— 4) Restore manual zoom limits if needed ———
            if animate != "discrete" and mc.manual_zoom and not zoom:
                mc.ax.set_xlim(cur_xlim)
                mc.ax.set_ylim(cur_ylim)

            # ——— 5) Overlays ———
            mc.overlay_rectangle(cx, cy, int(2*self.searchWindowSpin.value()))
            mc.remove_gaussian_circle()

            # draw fit circle & intensity highlight
            if hasattr(self, "analysis_fit_params") and index < len(self.analysis_fit_params):
                fc, fs, pk = self.analysis_fit_params[index]
            else:
                fc = fs = pk = None

            if fc is not None and fs is not None:
                if ic: ic.highlight_current_point()
            elif ic:
                ic.highlight_current_point(override=True)

            ic.current_index = index
            pointcolor = ic.get_current_point_color()
            mc.add_gaussian_circle(fc, fs, pointcolor)

            # ——— 6) Inset & kymo ———
            intensity = getattr(self, "analysis_intensities", [None])[index]
            background = getattr(self, "analysis_background", [None])[index]
            center_for_inset = fc if fc is not None else (cx, cy)
            mc.update_inset(frame_img, center_for_inset,
                            int(self.insetViewSize.value()), zoom_factor=2,
                            fitted_center=fc,
                            fitted_sigma=fs,
                            fitted_peak=pk,
                            intensity_value=intensity,
                            offset = background,
                            pointcolor = pointcolor)

            # only overlay kymo marker if ROI present
            kymo_name = self.kymoCombo.currentText()
            # look up its channel in the map
            info = self.kymo_roi_map.get(kymo_name, {})
            current_kymo_ch = info.get("channel", None)
            if self.analysis_channel == current_kymo_ch or self.analysis_channel is None:
                kymo_name = self.kymoCombo.currentText()
                if kymo_name and kymo_name in self.kymographs and self.rois:
                    roi = self.rois[self.roiCombo.currentText()]
                    xk = None
                    # check fit‐center first, then raw center
                    if fc is not None and is_point_near_roi(fc, roi):
                        xk = self.compute_kymo_x_from_roi(
                            roi, fc[0], fc[1],
                            self.kymographs[kymo_name].shape[1]
                        )
                    elif is_point_near_roi((cx, cy), roi):
                        xk = self.compute_kymo_x_from_roi(
                            roi, cx, cy,
                            self.kymographs[kymo_name].shape[1]
                        )
                    if xk is not None:
                        disp_frame = (self.movie.shape[0] - 1) - frame
                        kc.add_circle(
                            xk, disp_frame,
                            color=pointcolor if fc is not None else 'grey'
                        )

            # ——— 7) Histogram & sliders ———
            if hc:
                center_hist = fc if fc is not None else (cx, cy)
                hc.update_histogram(frame_img, center_hist,
                                    int(2*self.searchWindowSpin.value()),
                                    sigma=fs, intensity=intensity, background=background,
                                    peak=pk, pointcolor=pointcolor)
                
            self.frameSlider.setValue(frame)
            self.frameNumberLabel.setText(f"{frame+1}")
            if hasattr(self, 'analysisSlider'):
                self.analysisSlider.setValue(index)

            # refresh movie overlays so frame-dependent styling (e.g., fades) stays in sync
            mc.draw_trajectories_on_movie()

        finally:
            mc.setUpdatesEnabled(True)
            kc.setUpdatesEnabled(True)

            # 1) draw the movie axes so that the new frame + overlays are on screen
            self.movieCanvas.draw()

            # 2) recapture the blit background for the movie axes
            canvas = mc.figure.canvas
            mc._bg = canvas.copy_from_bbox(mc.ax.bbox)
            # mc._roi_bg = canvas.copy_from_bbox(mc.ax.bbox)

            # 3) draw any other canvases as needed
            self.kymoCanvas.draw()

            self.frameSlider.blockSignals(False)
            if hasattr(self, 'analysisSlider'):
                self.analysisSlider.blockSignals(False)

    def animate_view_transition(self, new_xlim, new_ylim, duration=20, steps=1):
        # Reset the flag at the start of the animation.
        self._stop_animation = False

        initial_xlim = self.movieCanvas.ax.get_xlim()
        initial_ylim = self.movieCanvas.ax.get_ylim()
        delay = duration // steps

        def step(i):
            # If the stop flag is set, abort the animation.
            if self._stop_animation:
                return

            # If manual zoom has become active (and we are not looping), abort.
            if getattr(self.movieCanvas, "manual_zoom", False) and not self.looping:
                self._stop_animation = True  # signal to stop further steps
                return

            mc = self.movieCanvas
            
            if i > steps:
                mc.ax.set_xlim(new_xlim)
                mc.ax.set_ylim(new_ylim)
                cx_new = (new_xlim[0] + new_xlim[1]) / 2.0
                cy_new = (new_ylim[0] + new_ylim[1]) / 2.0
                # set the logical center & draw
                
                mc.zoom_center = (cx_new, cy_new)
                mc.draw_idle()
                # grab clean background
                canvas = mc.figure.canvas
                mc._bg     = canvas.copy_from_bbox(mc.ax.bbox)
                mc._roi_bg = canvas.copy_from_bbox(mc.ax.bbox)
                # recompute scale so future scrolls/pans start here
                w = mc.width() or 1
                mc.scale = (new_xlim[1] - new_xlim[0]) / w
            else:
                t = i / steps
                interp_xlim = (initial_xlim[0]*(1-t) + new_xlim[0]*t,
                            initial_xlim[1]*(1-t) + new_xlim[1]*t)
                interp_ylim = (initial_ylim[0]*(1-t) + new_ylim[0]*t,
                            initial_ylim[1]*(1-t) + new_ylim[1]*t)
                mc.ax.set_xlim(interp_xlim)
                mc.ax.set_ylim(interp_ylim)
                mc.draw_idle()
                QTimer.singleShot(delay, lambda: step(i + 1))
        step(0)

    def animate_axes_transition(self, new_xlim, new_ylim, duration=250):
        """
        Animate the axes limits transition from the current limits to new_xlim/new_ylim.
        new_xlim and new_ylim should each be a two-element tuple: (min, max).
        """
        # Create the new target rectangle from the new axes limits.
        new_rect = QRectF(new_xlim[0], new_ylim[0], new_xlim[1] - new_xlim[0], new_ylim[1] - new_ylim[0])
        
        # Create our animator object wrapping the matplotlib axes.
        animator = AxesRectAnimator(self.movieCanvas.ax)
        
        # Create a QPropertyAnimation on the 'axesRect' property.
        anim = QPropertyAnimation(animator, b"axesRect")
        anim.setDuration(duration)
        anim.setStartValue(animator.getAxesRect())
        anim.setEndValue(new_rect)
        anim.setEasingCurve(QEasingCurve.InOutQuad) #anim.setEasingCurve(QEasingCurve.Linear)

        anim.finished.connect(self._capture_movie_bg)

        anim.start()
        
        # Keep a reference to avoid garbage collection.
        self._axes_anim = anim

    def analyze_spot_at_event(self, event):
        if self.kymoCanvas.image is None or event.xdata is None or event.ydata is None:
            return
        if self.movie is None:
            return

        num_frames = self.movie.shape[0]
        frame_idx = (num_frames - 1) - int(round(event.ydata))
        frame_image = self.get_movie_frame(frame_idx)
        if frame_image is None:
            return

        kymoName = self.kymoCombo.currentText()
        if not kymoName:
            return
        roi_key = self.roiCombo.currentText() if self.roiCombo.count() > 0 else kymoName
        if roi_key not in self.rois:
            return
        roi = self.rois[roi_key]
        if "x" not in roi or "y" not in roi:
            return

        x_orig, y_orig = self.compute_roi_point(roi, event.xdata)
        search_crop_size = int(2 * self.searchWindowSpin.value())
        zoom_crop_size = int(self.insetViewSize.value())

        bg_guess = None
        # Define crop boundaries around current center guess
        H, W = frame_image.shape
        half = search_crop_size // 2
        cx_int = int(round(x_orig))
        cy_int = int(round(y_orig))
        x1 = max(0, cx_int - half)
        x2 = min(W, cx_int + half)
        y1 = max(0, cy_int - half)
        y2 = min(H, cy_int + half)
        sub = frame_image[y1:y2, x1:x2]
        if sub.size == 0:
            bg_guess = None
        else:
            # Estimate background and initial p0
            counts, bins = np.histogram(sub, bins=50)
            centers = (bins[:-1] + bins[1:]) / 2
            cut = sub.min() + 0.5 * (sub.max() - sub.min())
            bg_guess = np.median(sub[sub < cut]) if np.any(sub < cut) else sub.min()

        # Perform a Gaussian fit on the current frame.
        fitted_center, fitted_sigma, intensity, peak, bkgr = perform_gaussian_fit(frame_image, (x_orig, y_orig), search_crop_size, bg_fixed=bg_guess, pixelsize = self.pixel_size)

        if not getattr(self, "hide_inset", False):
            self.zoomInsetFrame.setVisible(True)
        self.movieCanvas.update_inset(frame_image, (x_orig, y_orig), zoom_crop_size, zoom_factor=2,
                                    fitted_center=fitted_center,
                                    fitted_sigma=fitted_sigma,
                                    fitted_peak=peak,
                                    intensity_value=intensity,
                                    offset = bkgr)

        if hasattr(self, "histogramCanvas"):
            # Use the fitted center if available; if not, fall back to the original search center (cx, cy)
            center_for_hist = fitted_center if fitted_center is not None else (x_orig, y_orig)
            self.histogramCanvas.update_histogram(frame_image, center_for_hist, search_crop_size, sigma=fitted_sigma, intensity=intensity, peak=peak)

        self.movieCanvas.remove_gaussian_circle()

        if fitted_center is not None and fitted_sigma is not None:
            self.movieCanvas.add_gaussian_circle(fitted_center, fitted_sigma)

        self.movieCanvas.draw_idle()

    def on_movie_click(self, event):
        roi_mode = self.movieCanvas.roiAddMode
        if not roi_mode:
            if (
                event.button == 3
                and event.inaxes == self.movieCanvas.ax
                and self.traj_overlay_button.isChecked()
            ):
                for artist in getattr(self.movieCanvas, "movie_trajectory_markers", []):
                    hit, _info = artist.contains(event)
                    if not hit:
                        continue
                    traj_idx = getattr(artist, "traj_idx", None)
                    if traj_idx is None:
                        continue
                    if not hasattr(artist, "get_text"):
                        continue
                    gui_evt = getattr(event, "guiEvent", None)
                    if isinstance(gui_evt, QMouseEvent):
                        global_pos = gui_evt.globalPos()
                    else:
                        global_pos = QCursor.pos()
                    self._show_movie_context_menu(traj_idx, global_pos)
                    return

            if (
                event.button == 1
                and event.inaxes == self.movieCanvas.ax
                and self.traj_overlay_button.isChecked()
                and len(self.analysis_points) <= 1
            ):
                # Loop through all trajectory‐artists (annotations and scatter) that we stored
                for artist in getattr(self.movieCanvas, "movie_trajectory_markers", []):
                    hit, info = artist.contains(event)
                    if not hit:
                        continue

                    # We clicked one of our annotations or scatter points.
                    # First, stop any looping.
                    if self.looping:
                        self.stoploop()

                    self.cancel_left_click_sequence()

                    # Grab the trajectory index from the artist
                    traj_idx = getattr(artist, "traj_idx", None)
                    if traj_idx is None:
                        continue

                    # 1) If they clicked a new trajectory (different row), update table selection
                    current_row = self.trajectoryCanvas.table_widget.currentRow()
                    if traj_idx != current_row:
                        tbl = self.trajectoryCanvas.table_widget
                        tbl.blockSignals(True)
                        tbl.selectRow(traj_idx)
                        tbl.blockSignals(False)
                        # trigger whatever happens when a trajectory is selected:
                        self.trajectoryCanvas.on_trajectory_selected_by_index(traj_idx)

                    # 2) If they clicked on a scatter‐dot (info["ind"] exists), jump to that point:
                    #    info["ind"][0] is the index into traj["spot_centers"].
                    point_idx = info.get("ind", [None])[0]
                    if point_idx is not None:
                        self.jump_to_analysis_point(point_idx)
                        if self.sumBtn.isChecked():
                            self.sumBtn.setChecked(False)
                        self.intensityCanvas.current_index = point_idx
                        self.intensityCanvas.highlight_current_point()

                    # Consume this click (don’t let it fall through).
                    return
        if (
            event.button == 1
            and getattr(event, 'guiEvent', None) is not None
            and (event.guiEvent.modifiers() & Qt.MetaModifier)
        ):
            return
        # — only if click was inside the image —
        if (self.movieCanvas.image is None or 
            event.xdata is None or event.ydata is None):
            return
        H, W = self.movieCanvas.image.shape[:2]
        if not (0 <= event.xdata <= W and 0 <= event.ydata <= H):
            return
        # Ensure canvas transform is up to date before using event.xdata/ydata
        self.movieCanvas.draw()
        QApplication.processEvents()
        # Only respond if the click landed inside the movie axes
        if event.inaxes != self.movieCanvas.ax:
            return
        if self.looping:
            self.stoploop()
        if self.movieCanvas.roiAddMode:
            if event.button == 1:  # left click
                if not hasattr(self.movieCanvas, 'roiPoints') or not self.movieCanvas.roiPoints:
                    self.movieCanvas.clear_temporary_roi_markers()
                    self.movieCanvas.roiPoints = []
                # On double-click, finish using the first click position to avoid
                # adding a tiny extra segment if the mouse moved between clicks.
                skip_add = bool(event.dblclick) and bool(self.movieCanvas.roiPoints)
                if not skip_add:
                    self.movieCanvas.roiPoints.append((event.xdata, event.ydata))
                    self.movieCanvas.update_roi_drawing(current_pos=(event.xdata, event.ydata))
                if event.dblclick:
                    # On double-click, now finalize the ROI (after adding the current click)
                    self.kymoCanvas.manual_zoom = False
                    self.movieCanvas.clear_temporary_roi_markers()
                    self.movieCanvas.finalize_roi()
            return
        
        else:

            if event.button == 2:
                return

            if self.intensityCanvas is not None:
                self.intensityCanvas.clear_highlight()

            if self.kymoCanvas is not None:
                self.clear_temporary_analysis_markers()
                self.kymoCanvas.remove_circle()

            # Only respond if the click is in the movie canvas and has valid coordinates.
            if event.inaxes != self.movieCanvas.ax or event.xdata is None or event.ydata is None:
                return

            frame_image = self.movieCanvas.image
            if frame_image is None:
                return
            x_click, y_click = event.xdata, event.ydata
            search_crop_size = int(2 * self.searchWindowSpin.value())
            zoom_crop_size = int(self.insetViewSize.value())
            # Draw blue rectangle for the search area.
            frame_number = self.frameSlider.value()+1
            self.movieCanvas.overlay_rectangle(x_click, y_click, search_crop_size)

            bg_guess = None
            # Define crop boundaries around current center guess
            H, W = frame_image.shape
            half = search_crop_size // 2
            cx_int = int(round(x_click))
            cy_int = int(round(y_click))
            x1 = max(0, cx_int - half)
            x2 = min(W, cx_int + half)
            y1 = max(0, cy_int - half)
            y2 = min(H, cy_int + half)
            sub = frame_image[y1:y2, x1:x2]
            if sub.size == 0:
                bg_guess = None
            else:
                # Estimate background and initial p0
                counts, bins = np.histogram(sub, bins=50)
                centers = (bins[:-1] + bins[1:]) / 2
                cut = sub.min() + 0.5 * (sub.max() - sub.min())
                bg_guess = np.median(sub[sub < cut]) if np.any(sub < cut) else sub.min()
            
            # Perform Gaussian fit analysis.
            fitted_center, fitted_sigma, intensity, peak, bkgr = perform_gaussian_fit(
                frame_image, (x_click, y_click), search_crop_size, bg_fixed=bg_guess, pixelsize = self.pixel_size
            )

            if not getattr(self, "hide_inset", False):
                self.zoomInsetFrame.setVisible(True)
            self.movieCanvas.update_inset(
                frame_image, (x_click, y_click), zoom_crop_size, zoom_factor=2,
                fitted_center=fitted_center,
                fitted_sigma=fitted_sigma,
                fitted_peak=peak,
                intensity_value=intensity,
                offset = bkgr
            )
            center_to_use = fitted_center if fitted_center is not None else (x_click, y_click)
            if hasattr(self, "histogramCanvas"):
                self.histogramCanvas.update_histogram(frame_image, center_to_use, search_crop_size, fitted_sigma, intensity=intensity, peak=peak)
            # Remove any previous gaussian circle and draw a new one if fit succeeded.
            if fitted_center is None or fitted_sigma is None:
                self.movieCanvas.remove_gaussian_circle()
            else:
                self.movieCanvas.remove_gaussian_circle()
                self.movieCanvas.add_gaussian_circle(fitted_center, fitted_sigma)

            if event.button == 1:
                # LEFT CLICK: Process accumulation of points (without drawing an extra marker).
                self.on_movie_left_click(event)

            if event.button in [1, 3]:
                if fitted_center is not None:
                    self.drift_reference = fitted_center
                    self.spot_frame = self.frameSlider.value()

            self.movieCanvas.draw_idle()

    def on_movie_hover(self, event):
        if self.looping:
            self.pixelValueLabel.setText("")
            return
        in_image = False
        # Check that the event is in the movie canvas and has valid coordinates.
        if event.inaxes == self.movieCanvas.ax and event.xdata is not None and event.ydata is not None:
            # Convert floating point data coordinates to integer indices.
            image = self.movieCanvas.image
            if image is not None:
                in_image = (0 <= event.xdata < image.shape[1] and 0 <= event.ydata < image.shape[0])
                x = int(round(event.xdata))
                y = int(round(event.ydata))
            else:
                x = y = None
            if image is not None and x is not None and y is not None and 0 <= x < image.shape[1] and 0 <= y < image.shape[0]:
                pixel_val = image[y, x]
                current_frame = self.frameSlider.value() + 1
                text = f"F: {current_frame} X: {x} Y: {y} I: {pixel_val}"
                self.pixelValueLabel.setText(text)
            else:
                self.pixelValueLabel.setText("")

            self._last_hover_xy = (event.xdata, event.ydata)
            if not getattr(self, "analysis_points", None):
                self.movieCanvas._manual_marker_active = False
                self.movieCanvas.clear_manual_marker()
        else:
            self.pixelValueLabel.setText("")
        self._set_movie_roi_cursor(in_image)

        self._last_hover_xy = (event.xdata, event.ydata)

        if not self.movieCanvas.roiAddMode or not self.movieCanvas.roiPoints:
            return

        # throttle to ~50 Hz
        now = time.perf_counter()
        if now - getattr(self, '_last_roi_motion', 0) < 0.02:
            return
        self._last_roi_motion = now

        # build xs, ys from roiPoints + (event.xdata, event.ydata)
        canvas = self.movieCanvas.figure.canvas
        pts = self.movieCanvas.roiPoints + [(event.xdata, event.ydata)]
        xs, ys = zip(*pts)

        # fast blit loop
        canvas.restore_region(self.movieCanvas._roi_bg)
        if getattr(self.movieCanvas, "tempRoiLine", None) is not None:
            self.movieCanvas.tempRoiLine.set_data(xs, ys)
            self.movieCanvas.ax.draw_artist(self.movieCanvas.tempRoiLine)        
        canvas.blit(self.movieCanvas._roi_bbox)

    def on_movie_leave(self, _event):
        self.pixelValueLabel.setText("")
        self._last_hover_xy = None
        self._set_movie_roi_cursor(False)

    def on_movie_left_click(self, event):
        # Get the current frame index from the frame slider.
        frame_idx = self.frameSlider.value()
        x_click, y_click = event.xdata, event.ydata
        # self.last_anchor_type = 'movie'

        # If we already have a sequence, decide whether to update the last point or start a new one.
        if hasattr(self, "analysis_points") and self.analysis_points:
            last_frame, last_x, last_y = self.analysis_points[-1]
            if frame_idx == last_frame:
                # Same frame: update the last point's coordinates.
                self.analysis_points[-1] = (frame_idx, x_click, y_click)
            elif frame_idx < last_frame:
                # New left click on an earlier frame: start a new sequence.
                self.analysis_points = [(frame_idx, x_click, y_click)]
            else:
                # Otherwise, append the new point.
                self.analysis_points.append((frame_idx, x_click, y_click))
        else:
            self.analysis_points = [(frame_idx, x_click, y_click)]

        # # deactivate the dotted‐line while we draw our X
        # self.movieCanvas._manual_marker_active = True
        # self.movieCanvas._manual_marker_pos = (x_click, y_click)

        # # draw the marker once onto the axes
        # self.movieCanvas.draw_manual_marker()

        # Update the temporary dotted line connecting the left-click points.
        self.movieCanvas._manual_marker_active = False
        self.update_movie_analysis_line()

        # Rebuild the blit background after any click updates (line/overlays/circles).
        self._rebuild_movie_blit_background()

        # if it was a double‐click, finish sequence
        if event.dblclick:
            self.endMovieClickSequence()

    def on_movie_release(self, event):
        if event.button == 2 and event.inaxes == self.movieCanvas.ax:
            # pan just ended → redraw & recapture
            self.movieCanvas.update_view()

    def on_movie_motion(self, event):
        # Fast‐blit update for the temporary analysis line.
        line = getattr(self, "temp_movie_analysis_line", None)
        # Nothing to draw if there’s no temporary line.
        if not line:
            return

        # If we’re panning or zooming, do a full redraw & snapshot (hiding the line while snapshotting).
        if self.movieCanvas._is_panning or self.movieCanvas.manual_zoom:
            self.movieCanvas._bg = None
            self._rebuild_movie_blit_background()
            self.movieCanvas.manual_zoom = False
            return

        # Normal motion: restore background and draw only the line.
        canvas = self.movieCanvas.figure.canvas
        if getattr(self.movieCanvas, "_bg", None) is None:
            self._rebuild_movie_blit_background()
            return
        canvas.restore_region(self.movieCanvas._bg)
        self.movieCanvas.ax.draw_artist(line)
        canvas.blit(self.movieCanvas.ax.bbox)

    def update_movie_analysis_line(self):
        if not hasattr(self, "analysis_points") or not self.analysis_points:
            return

        points = sorted(self.analysis_points, key=lambda pt: pt[0])
        xs = [pt[1] for pt in points]
        ys = [pt[2] for pt in points]

        # If there’s already a temporary line, remove it
        if hasattr(self, "temp_movie_analysis_line") and self.temp_movie_analysis_line is not None:
            try:
                self.temp_movie_analysis_line.remove()
            except Exception:
                pass

        # 1) create a new dotted line
        self.temp_movie_analysis_line, = self.movieCanvas.ax.plot(
            xs, ys,
            color='#7da1ff', linewidth=1.5, linestyle='--'
        )

        self.temp_movie_analysis_line.set_animated(True)
        self._rebuild_movie_blit_background()

        # NB: NO draw_idle() here — we’ll blit in on_movie_motion

    def _rebuild_movie_blit_background(self):
        """Rebuild the MovieCanvas blit background.

        We treat `temp_movie_analysis_line` as an animated artist. The cached background
        must be captured *without* that line, otherwise restore_region() can wipe or
        double-draw overlays.
        """
        canvas = self.movieCanvas.figure.canvas
        line = getattr(self, "temp_movie_analysis_line", None)

        if line is not None:
            try:
                line.set_animated(True)
                line.set_visible(False)
            except Exception:
                pass

        # Full draw to make sure all "static" artists (image, overlays, circles, etc.) are baked in.
        self.movieCanvas.draw()

        # Snapshot background without the animated line.
        self.movieCanvas._bg = canvas.copy_from_bbox(self.movieCanvas.ax.bbox)

        # Make the line visible again and draw it once so the user sees it immediately.
        if line is not None:
            try:
                line.set_visible(True)
                self.movieCanvas.ax.draw_artist(line)
                canvas.blit(self.movieCanvas.ax.bbox)
            except Exception:
                pass

    def _show_movie_context_menu(self, row, global_pos: QPoint):
        if not self.trajectoryCanvas.custom_columns:
            return
        if row is None or row < 0 or row >= len(self.trajectoryCanvas.trajectories):
            return

        traj = self.trajectoryCanvas.trajectories[row]
        cf = traj.get("custom_fields", {})
        refresh_needed = {"value": False}

        def _mark_binary(r, c):
            self.trajectoryCanvas._mark_custom(r, c)
            if self.color_by_column == c:
                refresh_needed["value"] = True

        def _unmark_binary(r, c):
            self.trajectoryCanvas._unmark_custom(r, c)
            if self.color_by_column == c:
                refresh_needed["value"] = True

        menu = QMenu(self.movieCanvas)
        menu.setWindowFlags(menu.windowFlags() | Qt.FramelessWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground)

        if getattr(self, "check_colocalization", False) and self.movie is not None and self.movie.ndim == 4:
            ref_ch = traj.get("channel")
            missing = any(
                col.endswith(" co. %") and
                not col.endswith(f"{ref_ch} co. %") and
                not cf.get(col, "").strip()
                for col in self.trajectoryCanvas.custom_columns
            )
            if missing:
                act = menu.addAction("Check colocalization")
                act.triggered.connect(lambda _chk=False, r=row:
                                      self._compute_colocalization_for_row(r))
                menu.addSeparator()

        cols = [
            c for c in self.trajectoryCanvas.custom_columns
            if self.trajectoryCanvas._column_types.get(c) in ("binary", "value")
        ]
        unique_cols = []
        for c in cols:
            if c not in unique_cols:
                unique_cols.append(c)

        tbl = self.trajectoryCanvas.table_widget
        for col in unique_cols:
            col_type = self.trajectoryCanvas._column_types.get(col, "binary")
            table_col_index = self.trajectoryCanvas._col_index[col]
            item = tbl.item(row, table_col_index)
            text = item.text().strip() if item else ""

            if col_type == "binary":
                marked = (text.lower() == "yes")
                if marked:
                    action_text = f"Unmark as {col}"
                    callback = lambda _chk=False, r=row, c=col: _unmark_binary(r, c)
                else:
                    action_text = f"Mark as {col}"
                    callback = lambda _chk=False, r=row, c=col: _mark_binary(r, c)
            else:
                action_text = f"Edit {col} value" if text else f"Add {col} value"
                callback = lambda _chk=False, r=row, c=col: self._prompt_and_add_kymo_value(c, r)

            menu.addAction(action_text, callback)

        menu.exec_(global_pos)

        if refresh_needed["value"]:
            self.refresh_color_by()
            return

        self._update_legends()
        self.kymoCanvas.draw_trajectories_on_kymo()
        self.kymoCanvas.draw_idle()
        self.movieCanvas.draw_trajectories_on_movie()
        self.movieCanvas.draw_idle()

    def escape_left_click_sequence(self):
        self.cancel_left_click_sequence()
        self.movieCanvas.draw()
        self.kymoCanvas.draw()

    def cancel_left_click_sequence(self):
        # If we are in ROI mode, clear the temporary ROI drawing state.
        if self.movieCanvas.roiAddMode:
            # Clear any temporarily drawn ROI line
            if hasattr(self.movieCanvas, 'tempRoiLine') and self.movieCanvas.tempRoiLine is not None:
                try:
                    self.movieCanvas.tempRoiLine.remove()
                except Exception:
                    pass
                self.movieCanvas.tempRoiLine = None
            # Clear any x-markers (drawn with add_gaussian_circle)
            self.movieCanvas.clear_temporary_roi_markers()
            # Reset the list of ROI points so the user can start fresh.
            self.movieCanvas.roiPoints = []

        # Otherwise, perform the existing cancellation for analysis sequences.
        # Clear any temporary movie analysis line (if used)
        if hasattr(self, "temp_movie_analysis_line") and self.temp_movie_analysis_line is not None:
            try:
                self.temp_movie_analysis_line.remove()
            except Exception:
                pass
            self.temp_movie_analysis_line = None

        # Clear the temporary dotted line used in the kymograph (temp_analysis_line)
        if hasattr(self, "temp_analysis_line") and self.temp_analysis_line is not None:
            try:
                self.temp_analysis_line.remove()
            except Exception:
                pass
            self.temp_analysis_line = None

        # Also clear any additional dotted lines stored in leftclick_temp_lines
        if hasattr(self, "leftclick_temp_lines"):
            for line in self.leftclick_temp_lines:
                try:
                    line.remove()
                except Exception:
                    pass
            self.leftclick_temp_lines = []

        # Clear the blue markers stored in analysis_markers
        if hasattr(self, "analysis_markers") and self.analysis_markers:
            for marker in self.analysis_markers:
                try:
                    if hasattr(marker, '__iter__'):
                        for m in marker:
                            m.remove()
                    else:
                        marker.remove()
                except Exception:
                    pass
            self.analysis_markers = []

        # Also clear the permanent dotted line if it exists
        if hasattr(self, "permanent_analysis_line") and self.permanent_analysis_line is not None:
            try:
                self.permanent_analysis_line.remove()
            except Exception:
                pass
            self.permanent_analysis_line = None

        # Also clear any inter-anchor dotted segments
        if hasattr(self, 'permanent_analysis_lines'):
            for seg in self.permanent_analysis_lines:
                try:
                    seg.remove()
                except Exception:
                    pass
            self.permanent_analysis_lines = []

        if hasattr(self.movieCanvas, "rect_overlay") and self.movieCanvas.rect_overlay is not None:
            try:    self.movieCanvas.rect_overlay.remove()
            except: pass
            self.movieCanvas.rect_overlay = None

        self.movieCanvas.remove_gaussian_circle()
        self.kymoCanvas.remove_circle()

        # Clear accumulated left-click points.
        self.analysis_points = []
        self.analysis_anchors = []
        self.analysis_roi = None
        # self.kymoCanvas.unsetCursor()
        if hasattr(self, "_set_kymo_sequence_cursor"):
            self._set_kymo_sequence_cursor(False)

        # self.kymoCanvas.draw_idle()
        # self.movieCanvas.draw_idle()
