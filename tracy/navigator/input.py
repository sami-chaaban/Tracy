from ._shared import *

class NavigatorInputMixin:
    def toggleTracking(self):
        modes = ["Independent", "Tracked", "Smooth"] #, "Same center"
        try:
            i = modes.index(self.tracking_mode)
        except ValueError:
            i = 0
        new_mode = modes[(i + 1) % len(modes)]
        self.tracking_mode = new_mode

        # Update combo box if present.
        if hasattr(self, "trackingModeCombo"):
            self.trackingModeCombo.setCurrentText(new_mode)

        self.flash_message(f"Tracking Mode: {new_mode}")

    def _select_channel(self, requested_channel):
        """Handle 1–8 numeric shortcuts."""
        if (self.movie is not None
                and getattr(self.movie, "ndim", 0) == 4):
            max_ch = self.movie.shape[self._channel_axis]
            if 1 <= requested_channel <= max_ch:
                if self.flashchannel and requested_channel != int(self.movieChannelCombo.currentText()):
                    self.flash_message(f"Channel {requested_channel}")
                # This will emit currentIndexChanged → on_channel_changed(index)
                self.movieChannelCombo.setCurrentIndex(requested_channel - 1)

    def _move_manual_marker(self, dx, dy):
        if self.movie is None or self.movieCanvas.roiAddMode:
            return

        canvas = self.movieCanvas

        # initialize on first WASD press
        if not getattr(canvas, "_manual_marker_active", False):
            # 1) last analysis point?
            if getattr(self, "analysis_points", None):
                _, x0, y0 = self.analysis_points[-1]

            else:
                # 2a) hover point?
                hv = getattr(self, "_last_hover_xy", None)
                h, w = canvas.image.shape
                xmin, xmax = -0.5, w - 0.5
                ymin, ymax = -0.5, h - 0.5

                # only use hover if both coordinates are numeric and within bounds
                valid_hv = (
                    isinstance(hv, (tuple, list))
                    and len(hv) == 2
                    and hv[0] is not None and hv[1] is not None
                    and xmin <= hv[0] <= xmax
                    and ymin <= hv[1] <= ymax
                )
                if valid_hv:
                    x0, y0 = hv

                # 2b) last manual marker?
                elif getattr(canvas, "_manual_marker_pos", None):
                    x0, y0 = canvas._manual_marker_pos

                # 2c) true center
                else:
                    x0 = 0.5 * (xmin + xmax)
                    y0 = 0.5 * (ymin + ymax)

            canvas._manual_marker_pos    = [x0, y0]
            canvas._manual_marker_active = True

        # then nudge by (dx,dy)
        canvas._manual_marker_pos[0] += dx
        canvas._manual_marker_pos[1] += dy
        canvas.draw_manual_marker()
        canvas.draw_idle()

    def _simulate_left_click(self):
        if self.movie is None or self.movieCanvas.roiAddMode:
            return
        canvas = self.movieCanvas  # FigureCanvasQTAgg

        # 1) figure out data‐space coords (xdata,ydata) via either manual marker or cursor
        if getattr(canvas, "_manual_marker_active", False):
            xdata, ydata = canvas._manual_marker_pos
            # we’ll still compute pixel coords from xdata,ydata below
        else:
            # Get the cursor’s global (screen) position, then map into widget‐space
            pos = canvas.mapFromGlobal(QtGui.QCursor.pos())
            x_w, y_w = pos.x(), pos.y()  # in logical points

            # 2) convert from logical points → device (physical) pixels
            dpr = canvas.devicePixelRatioF()  # usually 2.0 on Retina
            x_phys = x_w * dpr
            # Flip Y: Qt’s (0,0) is top‐left in points, Matplotlib’s (0,0) is bottom‐left in pixels
            height_pts = canvas.height()
            height_phys = height_pts * dpr
            y_phys = height_phys - (y_w * dpr)

            # 3) invert from display (pixels) → data (xdata, ydata)
            xdata, ydata = canvas.ax.transData.inverted().transform((x_phys, y_phys))

        # 4) build a fake event that has both x/y (pixels) and xdata/ydata
        evt = type("Evt", (), {})()
        evt.xdata    = xdata
        evt.ydata    = ydata

        # If we came via manual_marker, compute physical pixels similarly:
        if getattr(canvas, "_manual_marker_active", False):
            # Transform (xdata,ydata) → display‐pixel coords
            x_phys, y_phys = canvas.ax.transData.transform((xdata, ydata))
        evt.x = x_phys
        evt.y = y_phys

        evt.button   = 1
        evt.dblclick = False
        evt.inaxes   = canvas.ax
        evt.guiEvent = None

        # 5) now calling artist.contains(evt) will see the correct pixel coords
        self.on_movie_click(evt)

    def _prev_frame(self):
        """Go to previous frame (J)."""
        if self.looping:
            self.stoploop()        
        cur = self.frameSlider.value()
        self.set_current_frame(max(0, cur - 1))

    def _next_frame(self):
        if self.looping:
            self.stoploop()
        """Go to next frame (L)."""
        cur = self.frameSlider.value()
        self.set_current_frame(min(self.movie.shape[0] - 1, cur + 1))

    def keyReleaseEvent(self, event):
        if event.key()==Qt.Key_R and self._radiusPopup:
            new_val = self._radiusSpinLive.value()
            self.searchWindowSpin.setValue(new_val)

            self._radiusPopup.close()
            self._radiusPopup = None
            self._radiusSpinLive = None

            # return focus to main window
            self.activateWindow()
            self.setFocus()

            event.accept()
            return

        super().keyReleaseEvent(event)

    def update_table_visibility(self, adjust_splitter=True):
        has_rows = (self.trajectoryCanvas.table_widget.rowCount() > 0)

        # initialize the “last” flag on first call
        if not hasattr(self, "_last_table_has_rows"):
            self._last_table_has_rows = not has_rows  # force an update on first run

        if has_rows and getattr(self, "_right_panel_auto_show_pending", False):
            self._right_panel_auto_show_pending = False
            self._show_right_panel_if_collapsed()

        if adjust_splitter and has_rows != self._last_table_has_rows:
            total_height = self.rightVerticalSplitter.height()
            if not has_rows:
                # hide table
                self.rightVerticalSplitter.setSizes([total_height, 0])
                self.mainSplitter.handle_y_offset_pct = 0.4955
            else:
                # show table
                self.rightVerticalSplitter.setSizes(
                    [int(0.75 * total_height), int(0.25 * total_height)]
                )
                self.mainSplitter.handle_y_offset_pct = 0.1

        # store for next call
        self._last_table_has_rows = has_rows

        # now update the buttons & columns as before
        self.traj_overlay_button.setVisible(has_rows)
        if hasattr(self, "traj_overlay_container"):
            self.traj_overlay_container.setVisible(has_rows)
        if hasattr(self, "kymo_traj_overlay_button"):
            self.kymo_traj_overlay_button.setVisible(has_rows)
        if hasattr(self, "kymo_traj_overlay_container"):
            self.kymo_traj_overlay_container.setVisible(has_rows)
        self.delete_button.setVisible(has_rows)
        if hasattr(self, "delete_container"):
            self.delete_container.setVisible(has_rows)
        self.clear_button.setVisible(has_rows)
        if hasattr(self, "clear_container"):
            self.clear_container.setVisible(has_rows)
        self.trajectoryCanvas.hide_empty_columns()
        self._ensure_traj_overlay_mode_valid(redraw=False)
        if hasattr(self, "_ensure_kymo_traj_overlay_mode_valid"):
            self._ensure_kymo_traj_overlay_mode_valid(redraw=False)

    def _collapse_right_panel_on_startup(self):
        if getattr(self, "_right_panel_startup_done", False):
            return
        splitter = getattr(self, "topRightSplitter", None)
        if splitter is None:
            return
        total_width = splitter.width()
        if total_width <= 0:
            QTimer.singleShot(0, self._collapse_right_panel_on_startup)
            return
        splitter.setSizes([total_width, 0])
        self._right_panel_startup_done = True

    def _show_right_panel_if_collapsed(self):
        splitter = getattr(self, "topRightSplitter", None)
        if splitter is None:
            return
        sizes = splitter.sizes()
        if len(sizes) < 2:
            return
        total_width = sum(sizes)
        if total_width <= 0:
            total_width = splitter.width()
        if total_width <= 0:
            QTimer.singleShot(0, self._show_right_panel_if_collapsed)
            return
        if getattr(self, "rightPanel", None) is not None:
            self.rightPanel.setVisible(True)
        target_width = getattr(self, "_right_panel_width", 0) or 0
        if target_width <= 0:
            target_width = int(0.35 * total_width)
        target_width = max(0, min(int(target_width), total_width))
        splitter.setSizes([max(0, total_width - target_width), target_width])

    def _remember_right_panel_width(self, *_args):
        splitter = getattr(self, "topRightSplitter", None)
        if splitter is None:
            return
        sizes = splitter.sizes()
        if len(sizes) < 2:
            return
        right_w = int(sizes[1])
        min_w = getattr(self, "_right_panel_min_width", 0) or 0
        if right_w >= max(1, min_w):
            self._right_panel_width = right_w

    def _enforce_right_panel_min_width(self, *_args):
        splitter = getattr(self, "topRightSplitter", None)
        if splitter is None:
            return
        sizes = splitter.sizes()
        if len(sizes) < 2:
            return
        total_width = sum(sizes)
        if total_width <= 0:
            return
        right_w = int(sizes[1])
        min_w = getattr(self, "_right_panel_min_width", 0) or 0
        if min_w <= 0:
            return
        if 0 < right_w < min_w:
            splitter.setSizes([total_width, 0])

    # def eventFilter(self, obj, event):
    #     # intercept wheel events when our radius dialog is up
    #     if (self._radiusDialog is not None 
    #             and self._radiusDialog.isVisible() 
    #             and event.type() == QEvent.Wheel):
    #         # up/down?
    #         delta = event.angleDelta().y()
    #         step  = self.searchWindowSpin.singleStep()
    #         cur   = self.searchWindowSpin.value()
    #         if delta > 0:
    #             self.searchWindowSpin.setValue(cur + step)
    #         else:
    #             self.searchWindowSpin.setValue(cur - step)
    #         return True    # eat it
    #     return super().eventFilter(obj, event)

    def eventFilter(self, obj, ev):

        ch_overlay = getattr(self, "_ch_overlay", None)
        if ch_overlay is not None and obj is ch_overlay and ev.type() == ev.Show:
            self._reposition_legend()

        movie_container = getattr(self, "movieDisplayContainer", None)
        if movie_container is not None and obj is movie_container and ev.type() in (ev.Resize, ev.Move):
            self._reposition_legend()

        kymo_legend = getattr(self, "kymoLegendWidget", None)
        movie_legend = getattr(self, "movieLegendWidget", None)
        if obj in (kymo_legend, movie_legend):
            if ev.type() == QEvent.Enter:
                self._schedule_show_legend_popup(obj)
            elif ev.type() == QEvent.Leave:
                self._schedule_hide_legend_popup()

        return super().eventFilter(obj, ev)

    def reset_contrast(self):
        image = self.movieCanvas.image
        if image is None:
            #print("No movie loaded; cannot reset contrast.")
            return
        p15, p99 = np.percentile(image, (15, 99))
        if self.movieCanvas.sum_mode:
            new_vmin, new_vmax = int(p15 * 1.05), int(p99 * 1.2)
        else:
            new_vmin, new_vmax = int(p15), int(p99 * 1.1)
            
        delta = new_vmax - new_vmin
        new_extended_min = new_vmin - int(0.7 * delta)
        new_extended_max = new_vmax + int(1.4 * delta)
        
        # Update the slider.
        self.contrastControlsWidget.contrastRangeSlider.blockSignals(True)
        self.contrastControlsWidget.contrastRangeSlider.setMinimum(new_extended_min)
        self.contrastControlsWidget.contrastRangeSlider.setMaximum(new_extended_max)
        self.contrastControlsWidget.contrastRangeSlider.setRangeValues(new_vmin, new_vmax)
        self.contrastControlsWidget.contrastRangeSlider.blockSignals(False)
        self.contrastControlsWidget.contrastRangeSlider.update()
        
        try:
            # Use the navigator's movieChannelCombo to obtain the correct current channel.
            current_channel = int(self.movieChannelCombo.currentText())
        except Exception:
            current_channel = 1

        if self.movieCanvas.sum_mode:
            self.channel_sum_contrast_settings[current_channel] = {
                'vmin': new_vmin,
                'vmax': new_vmax,
                'extended_min': new_extended_min,
                'extended_max': new_extended_max
            }
        else:
            self.channel_contrast_settings[current_channel] = {
                'vmin': new_vmin,
                'vmax': new_vmax,
                'extended_min': new_extended_min,
                'extended_max': new_extended_max
            }
                
        # Update MovieCanvas contrast attributes.
        self.movieCanvas._default_vmin = new_vmin
        self.movieCanvas._default_vmax = new_vmax
        self.movieCanvas._vmin = new_vmin
        self.movieCanvas._vmax = new_vmax
        
        self.movieCanvas._im.set_clim(new_vmin, new_vmax)
        self.movieCanvas.draw_idle()


    def reset_kymo_contrast(self):
        image = self.kymoCanvas.image
        if image is None:
            #print("No movie loaded; cannot reset contrast.")
            return
        kymo_name = self.kymoCombo.currentText()
        if not kymo_name:
            return
        use_log = getattr(self, "applylogfilter", False)
        if use_log:
            p15, p99 = np.percentile(image, (35, 98))
            new_vmin, new_vmax = int(p15), int(p99)
        else:
            p15, p99 = np.percentile(image, (15, 99))
            new_vmin, new_vmax = int(p15), int(p99 * 1.1)
            
        delta = new_vmax - new_vmin
        new_extended_min = new_vmin - int(0.7 * delta)
        new_extended_max = new_vmax + int(1.4 * delta)
        
        # Update the slider.
        self.kymocontrastControlsWidget.contrastRangeSlider.blockSignals(True)
        self.kymocontrastControlsWidget.contrastRangeSlider.setMinimum(new_extended_min)
        self.kymocontrastControlsWidget.contrastRangeSlider.setMaximum(new_extended_max)
        self.kymocontrastControlsWidget.contrastRangeSlider.setRangeValues(new_vmin, new_vmax)
        self.kymocontrastControlsWidget.contrastRangeSlider.blockSignals(False)
        self.kymocontrastControlsWidget.contrastRangeSlider.update()

        if use_log:
            if not hasattr(self, "kymo_log_contrast_settings"):
                self.kymo_log_contrast_settings = {}
            store = self.kymo_log_contrast_settings
        else:
            if not hasattr(self, "kymo_contrast_settings"):
                self.kymo_contrast_settings = {}
            store = self.kymo_contrast_settings
        store[kymo_name] = {
            'vmin': new_vmin,
            'vmax': new_vmax,
            'extended_min': new_extended_min,
            'extended_max': new_extended_max
        }
        
        # self.channel_contrast_settings[current_channel] = {
        #     'vmin': new_vmin,
        #     'vmax': new_vmax,
        #     'extended_min': new_extended_min,
        #     'extended_max': new_extended_max
        # }
                
        # # Update internal contrast attributes:
        # self.kymoCanvas._default_vmin = new_vmin
        # self.kymoCanvas._default_vmax = new_vmax
        # self.kymoCanvas._vmin = new_vmin
        # self.kymoCanvas._vmax = new_vmax
        
        self.kymoCanvas._im.set_clim(new_vmin, new_vmax)
        self.kymoCanvas.draw_idle()

    def _apply_kymo_contrast_settings(self, kymo_name):
        if not kymo_name:
            return
        image = self.kymoCanvas.image
        if image is None:
            return
        use_log = getattr(self, "applylogfilter", False)
        if use_log:
            if not hasattr(self, "kymo_log_contrast_settings"):
                self.kymo_log_contrast_settings = {}
            store = self.kymo_log_contrast_settings
        else:
            if not hasattr(self, "kymo_contrast_settings"):
                self.kymo_contrast_settings = {}
            store = self.kymo_contrast_settings

        settings = store.get(kymo_name)
        if settings is None:
            if use_log:
                p15, p99 = np.percentile(image, (35, 98))
                new_vmin, new_vmax = int(p15), int(p99)
            else:
                p15, p99 = np.percentile(image, (15, 99))
                new_vmin, new_vmax = int(p15), int(p99 * 1.1)
            delta = new_vmax - new_vmin
            settings = {
                'vmin': new_vmin,
                'vmax': new_vmax,
                'extended_min': new_vmin - int(0.7 * delta),
                'extended_max': new_vmax + int(1.4 * delta)
            }
            store[kymo_name] = settings

        slider = self.kymocontrastControlsWidget.contrastRangeSlider
        slider.blockSignals(True)
        slider.setMinimum(settings['extended_min'])
        slider.setMaximum(settings['extended_max'])
        slider.setRangeValues(settings['vmin'], settings['vmax'])
        slider.blockSignals(False)
        slider.update()

        self.kymoCanvas._vmin = settings['vmin']
        self.kymoCanvas._vmax = settings['vmax']
        if self.kymoCanvas._im is not None:
            self.kymoCanvas._im.set_clim(settings['vmin'], settings['vmax'])

    def on_sum_toggled(self):
        try:
            current_channel = int(self.movieChannelCombo.currentText())
        except Exception:
            current_channel = 1

        if self.sumBtn.isChecked():
            # ----- Sum mode ON -----
            if self.refBtn.isChecked():
                self.refBtn.setChecked(False)
            self.movieCanvas.sum_mode = True

            if self.movie is None:
                return


            self.movieCanvas.display_sum_frame()  # This method should be modified if necessary so that it doesn't call update_view()

            # Compute sum-mode contrast settings if they don’t already exist.
            sum_image = self.movieCanvas.image
            if current_channel not in self.channel_sum_contrast_settings:
                if sum_image is not None:
                    p15, p99 = np.percentile(sum_image, (15, 99))
                    new_vmin = int(p15 * 1.05)
                    new_vmax = int(p99 * 1.2)
                    delta = new_vmax - new_vmin
                    settings = {
                        'vmin': new_vmin,
                        'vmax': new_vmax,
                        'extended_min': new_vmin - int(0.7 * delta),
                        'extended_max': new_vmax + int(1.4 * delta)
                    }
                    self.channel_sum_contrast_settings[current_channel] = settings
                else:
                    settings = {'vmin': 0, 'vmax': 255, 'extended_min': 0, 'extended_max': 255}
                    self.channel_sum_contrast_settings[current_channel] = settings
            else:
                settings = self.channel_sum_contrast_settings[current_channel]

            # Update the movie canvas’s sum‑mode contrast defaults:
            self.movieCanvas._default_vmin = settings['vmin']
            self.movieCanvas._default_vmax = settings['vmax']
            self.movieCanvas._vmin = settings['vmin']
            self.movieCanvas._vmax = settings['vmax']

            # Update the contrast slider accordingly.
            self.contrastControlsWidget.contrastRangeSlider.blockSignals(True)
            self.contrastControlsWidget.contrastRangeSlider.setMinimum(settings['extended_min'])
            self.contrastControlsWidget.contrastRangeSlider.setMaximum(settings['extended_max'])
            self.contrastControlsWidget.contrastRangeSlider.setRangeValues(settings['vmin'], settings['vmax'])
            self.contrastControlsWidget.contrastRangeSlider.blockSignals(False)

            # after settings computed and slider updated
            self.movieCanvas.set_display_range(settings['vmin'], settings['vmax'])

            # base pixels/contrast changed -> invalidate/rebuild bg for blitting
            self.movieCanvas._bg = None
            if getattr(self, "temp_movie_analysis_line", None) is not None:
                self._rebuild_movie_blit_background()

            #self.movieCanvas.draw_idle()

        else:
            # ----- Sum mode OFF (restore normal mode) -----
            self.sumBtn.setStyleSheet("")
            self.movieCanvas.sum_mode = False

            if self.movie is None:
                return

            # Get the current (normal) frame.
            frame = self.get_movie_frame(self.frameSlider.value())

            # Retrieve stored normal-mode settings (or compute if missing):
            if current_channel in self.channel_contrast_settings:
                settings = self.channel_contrast_settings[current_channel]
            else:
                p15, p99 = np.percentile(frame, (15, 99))
                default_vmin = int(p15)
                default_vmax = int(p99 * 1.1)
                delta = default_vmax - default_vmin
                settings = {
                    'vmin': default_vmin,
                    'vmax': default_vmax,
                    'extended_min': default_vmin - int(0.7 * delta),
                    'extended_max': default_vmax + int(1.4 * delta)
                }
                self.channel_contrast_settings[current_channel] = settings

            # Reset the movie canvas’s internal contrast settings.
            self.movieCanvas._default_vmin = settings['vmin']
            self.movieCanvas._default_vmax = settings['vmax']
            self.movieCanvas._vmin = settings['vmin']
            self.movieCanvas._vmax = settings['vmax']

            # Update the contrast slider.
            self.contrastControlsWidget.contrastRangeSlider.blockSignals(True)
            self.contrastControlsWidget.contrastRangeSlider.setMinimum(settings['extended_min'])
            self.contrastControlsWidget.contrastRangeSlider.setMaximum(settings['extended_max'])
            self.contrastControlsWidget.contrastRangeSlider.setRangeValues(settings['vmin'], settings['vmax'])
            self.contrastControlsWidget.contrastRangeSlider.blockSignals(False)

            self.movieCanvas.update_image_data(frame)
            self.movieCanvas.set_display_range(settings['vmin'], settings['vmax'])

            self.movieCanvas._bg = None
            if getattr(self, "temp_movie_analysis_line", None) is not None:
                self._rebuild_movie_blit_background()

            # self.movieCanvas.update_image_data(frame)
            # self.movieCanvas.draw_idle()
