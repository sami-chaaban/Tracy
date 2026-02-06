from ._shared import *
from scipy.ndimage import gaussian_laplace

class NavigatorIOMixin:
    def infer_axes_from_shape(shape):
        """
        Build an ImageJ-style axes string from a NumPy shape tuple:
        - T (time)   for the first dim if >1
        - Z (z-slice) for the next if >1
        - C (channel) for the next if >1
        - Y, X always the last two dims
        """
        axes = []
        letter_map = ['T', 'Z', 'C']
        for i, length in enumerate(shape[:-2]):
            axes.append(letter_map[i] if length > 1 else letter_map[i].lower())
        axes += ['Y', 'X']
        # Only keep the “real” axes (uppercase)
        return ''.join(ax for ax in axes if ax.isupper())

    def handle_movie_load(self, fname=None, pixelsize=None, frameinterval=None):
        load_timer = getattr(self, "_load_tip_timer", None)
        if load_timer is not None and load_timer.isActive():
            load_timer.stop()
        load_tip = getattr(self, "_load_tip_filter", None)
        if load_tip is not None:
            load_tip._timer.stop()
            load_tip._hideBubble()
        # If save_and_load_routine is active, don't open the dialog.
        if self.save_and_load_routine:
            # Reset the flag so it only applies once.
            self.save_and_load_routine = False
        else:
            # Open the file dialog.
            fname, _ = QFileDialog.getOpenFileName(
                self, "Open Movie TIFF", self._last_dir, "TIFF Files (*.tif *.tiff)"
            )
        
        # If no file was chosen, exit.
        if not fname:
            return

        # Pass the chosen filename to load_movie.
        self.load_movie(fname, pixelsize=pixelsize, frameinterval=frameinterval)

    def load_movie(self, fname=None, pixelsize=None, frameinterval=None):

        self.clear_flag = False
        self.cancel_left_click_sequence()

        self.show_steps = False
        self.showStepsAction.setChecked(False)

        if (self.rois or self.kymographs or
            (hasattr(self, 'trajectoryCanvas') and self.trajectoryCanvas.trajectories)):
            reply = QMessageBox.question(
                self,
                "Clear existing data?",
                "Clear existing data before loading a new movie?",
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Cancel:
                # user chose cancel → abort load_movie entirely
                return
            # user chose Yes → clear all existing data
            self.clear_flag = True

        if fname:
            self._last_dir = os.path.dirname(fname)
            # try:
            with tifffile.TiffFile(fname) as tif:
                temp_movie = tif.asarray()
                self.movie_metadata = tif.imagej_metadata or {}
                page = tif.pages[0]

            tags = page.tags
            description = page.description

            if pixelsize is not None and frameinterval is not None:
                self.pixel_size = pixelsize
                self.frame_interval = frameinterval

            else:
                
                y_size = None
                self.pixel_size = None
                self.frame_interval = None

                import re
                
                if description:
                    # Look for a pattern like "Voxel size: 0.1100x0.1100x1"
                    m = re.search(r'Voxel size:\s*([\d\.]+)[xX]([\d\.]+)[xX]([\d\.]+)', description)
                    if m:
                        try:
                            # Convert the strings to floats.
                            # The typical order in ImageJ is "x_size x y_size x z_size".
                            #x_size = float(m.group(1))
                            y_size = float(m.group(2))
                            #z_size = float(m.group(3))
                            # Return as [z, y, x]
                            self.pixel_size = y_size*1000
                        except Exception as e:
                            print("Error parsing voxel size from ImageDescription:", e)

                if self.pixel_size is None:
                    if 'YResolution' in tags:
                        value = tags['YResolution'].value
                        try:
                            # If value is a tuple, compute pixels per micron:
                            num, denom = value
                            # pixels per micron = num/denom; thus pixel size in microns is 1/(num/denom)
                            self.pixel_size = float(denom)*1000 / float(num)
                        except Exception:
                            try:
                                # Otherwise try to convert directly to a float.
                                self.pixel_size = float(value)*1000
                            except Exception:
                                pass

                desc = tif.pages[0].tags["ImageDescription"].value
                try:
                    match = re.search(r'finterval=([\d\.]+)', desc)
                    if match:
                        self.frame_interval = float(match.group(1))*1000
                except Exception:
                    pass

                if self.pixel_size is not None:
                    if self.pixel_size < 0.1:
                        self.pixel_size = None
                if self.frame_interval is not None:
                    if self.frame_interval < 0.1:
                        self.frame_interval = None

            try:
                shape = temp_movie.shape
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Invalid movie : {e}")
                return

            if temp_movie.ndim not in (3, 4):
                QMessageBox.critical(self, "Error", f"Invalid movie shape: {temp_movie.shape}")
                return

            self.referenceImage = None
            self.refBtn.setVisible(False)
            if hasattr(self, "ref_container"):
                self.ref_container.setVisible(False)
            if hasattr(self, "ref_spacer"):
                self.ref_spacer.setVisible(False)
            self.refBtn.setChecked(False)
            self.sumBtn.setChecked(False)
            self.zoomInsetFrame.setVisible(False)
            self.movieCanvas._last_inset_params = None
            self.movieCanvas._inset_update_pending = False

            # Initialize (or reset) contrast settings for multi‑channel movies.
            self.channel_contrast_settings = {}      # for “normal” mode
            self.channel_sum_contrast_settings = {}   # for sum‐mode
            self.reference_contrast_settings = {}

            self.movieNameLabel.setText("")
            self.movieNameLabel.setStyleSheet("background: transparent; color: black; font-size: 16px; font-weight: bold")
            self.movieNameLabel.setText(os.path.basename(fname))
            self.movieNameLabel.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            if hasattr(self, "movieLoadButton"):
                self.movieLoadButton.setVisible(True)
            

            # ── blank out the histogram canvas entirely ────────────────────────────────
            if hasattr(self, 'histogramCanvas'):
                self.histogramCanvas.ax.cla()
                self.histogramCanvas.ax.axis("off")
                self.histogramCanvas.draw_idle()
            # ── blank out the intensity/plot canvas (two sub‐axes) ────────────────────
            if hasattr(self, 'intensityCanvas'):
                ic = self.intensityCanvas

                # 1) clear any old highlights & scatter
                ic.clear_highlight()
                ic.scatter_obj_top = None
                ic.scatter_obj_bottom = None
                ic.point_highlighted = False
                ic._last_plot_args   = None

                # 2) completely wipe both axes
                ic.ax_top.cla()
                ic.ax_bottom.cla()
                ic.ax_top.axis('off')
                ic.ax_bottom.axis('off')

                # 3) redraw & grab a fresh background
                ic.draw()
                ic._background = ic.copy_from_bbox(ic.fig.bbox)
            # ── blank out the velocity histogram canvas ───────────────────────────────
            if hasattr(self, 'velocityCanvas'):
                self.velocityCanvas.ax_vel.cla()
                self.velocityCanvas.ax_vel.axis("off")
                self.velocityCanvas.draw_idle()

            # ── blank out the zoom‐inset widget ─────────────────────────────────────────
            if hasattr(self, "zoomInsetFrame"):
                # hide the whole frame
                self.zoomInsetFrame.setVisible(False)

            if hasattr(self, "zoomInsetWidget"):
                # clear its axes
                self.zoomInsetWidget.ax.clear()
                self.zoomInsetWidget.ax.axis("off")
                self.zoomInsetWidget.draw_idle()

            self.movieCanvas.stop_idle_animation()
            self.movieCanvas.clear_canvas()
            self.movie = temp_movie
            self.original_movie = self.movie.copy()

            # Reset the frame cache whenever a new movie is loaded.
            self.frame_cache = {}

            if self.movie.ndim == 4:
                # 4D movie (multi‑channel)
                if self.movie_metadata and "axes" in self.movie_metadata:
                    axes_str = self.movie_metadata["axes"]  # e.g., "TXYC" or "TCYX"
                    # For example, if the letter "C" is found, use that index:
                    self._channel_axis = axes_str.find("C")
                else:
                    self.update_channel_axis_options()
                self.update_movie_channel_combo()
                first_frame = self.get_movie_frame(0)
            else:
                # 3D movie (single channel)
                self._channel_axis = None
                self.movieChannelCombo.blockSignals(True)
                self.movieChannelCombo.clear()
                self.movieChannelCombo.addItem("1")
                self.movieChannelCombo.blockSignals(False)
                # Now call update_movie_channel_combo even for 3D movies:
                self.update_movie_channel_combo()
                first_frame = self.movie[0]

            if hasattr(self, "channelAxisAction"):
                self.channelAxisAction.setEnabled(self.movie.ndim == 4)

            max_frame = self.movie.shape[0]
            self.frameSlider.setMinimum(0)
            self.frameSlider.setMaximum(max_frame - 1)
            self.frameSlider.setValue(0)
            self.frameNumberLabel.setText("1")

            margin = 0
            full_width = self.movieCanvas.image.shape[1]
            full_height = self.movieCanvas.image.shape[0]
            self.movieCanvas.zoom_center = (full_width/2, full_height/2)
            self.movieCanvas.display_image(first_frame)

            if self.clear_flag:
                self.clear_rois()
                self.clear_kymographs(prompt=False)
                self.trajectoryCanvas.clear_trajectories(prompt=False)
                self.clear_flag = False

            self.movieCanvas.draw_idle()
            self.movieCanvas.clear_sum_cache()

            self.update_scale_label()

            if self.pixel_size is None or self.frame_interval is None:
                prev_px = self.pixel_size
                prev_ft = self.frame_interval
                self.set_scale()
                if self.pixel_size is None or self.frame_interval is None:
                    if prev_px is not None or prev_ft is not None:
                        self.pixel_size = None
                        self.frame_interval = None
                        self.update_scale_label()

            # self.last_kymo_by_channel = {}

            # If there are any existing custom columns, ask whether to clear them now
            # only ask if there are any custom‐columns other than the auto‐added colocalization % ones
            d_col = getattr(self, "_DIFF_D_COL", None)
            a_col = getattr(self, "_DIFF_A_COL", None)
            auto_cols = {c for c in (d_col, a_col) if c}
            non_coloc = [
                name for name in self.trajectoryCanvas.custom_columns
                if self.trajectoryCanvas._column_types.get(name) != "coloc"
                and name not in auto_cols
            ]
            if non_coloc:
                reply = QMessageBox.question(
                    self,
                    "Clear custom columns?",
                    "Do you want to clear all user-defined custom columns?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes
                )
                if reply == QMessageBox.Yes:
                    # remove only those non-coloc columns
                    for name in non_coloc:
                        idx = self.trajectoryCanvas._col_index.get(name)
                        if idx is not None:
                            self.trajectoryCanvas._remove_custom_column(idx, name)
                    # rebuild the menu so it no longer shows those entries
            
            coloc_cols = [
                name for name in self.trajectoryCanvas.custom_columns
                if self.trajectoryCanvas._column_types.get(name) == "coloc"
            ]
            for name in coloc_cols:
                idx = self.trajectoryCanvas._col_index.get(name)
                if idx is not None:
                    self.trajectoryCanvas._remove_custom_column(idx, name)
            self._rebuild_color_by_actions()

            if self.movie.ndim == 4:
                # — multi-channel: build co-localization columns —
                # first, clear any old co. % columns if reloading
                old = [c for c in self.trajectoryCanvas.custom_columns if c.endswith(" co. %")]
                for name in old:
                    idx = self.trajectoryCanvas._col_index.get(name)
                    if idx is not None:
                        self.trajectoryCanvas._remove_custom_column(idx, name)

                # now add fresh ones
                self.trajectoryCanvas.custom_columns = [c for c in self.trajectoryCanvas.custom_columns if not c.endswith(" co. %")]
                n_chan = self.movie.shape[self._channel_axis]
                for ch in range(1, n_chan+1):
                    col_name = f"Ch. {ch} co. %"
                    self.trajectoryCanvas._add_custom_column(col_name, col_type="coloc")

                # print("load_movie custom_columns, after add_custom_column", self.trajectoryCanvas.custom_columns)

                if hasattr(self, 'colocalizationAction'):
                    self.colocalizationAction.setEnabled(True)
                    self.colocalizationAction.setChecked(False)

            else:
                # single-channel: ensure no stray co. % columns remain
                for name in [c for c in self.trajectoryCanvas.custom_columns if c.endswith(" co. %")]:
                    idx = self.trajectoryCanvas._col_index.get(name)
                    if idx is not None:
                        self.trajectoryCanvas._remove_custom_column(idx, name)

                self.check_colocalization = False
                # if we previously enabled colocalization, disable it
                if hasattr(self, 'colocalizationAction'):
                    self.colocalizationAction.setChecked(False)
                    self.colocalizationAction.setEnabled(False)

            # Always untoggle any active “Color by …” before we potentially reload columns
            self.set_color_by(None)

            self.flash_message("Loaded movie")

            if self.movie.ndim == 4:
                filt = self._ch_overlay._bubble_filter
                filt._wobj = self._ch_overlay
                QTimer.singleShot(2000, lambda: filt._showBubble(force=True))

            # except Exception as e:
            #     QMessageBox.critical(self, "Error", f"Could not load movie:\n{str(e)}")

    def get_movie_frame(self, frame_idx, channel_override=None):
        if self.movie is None:
            return None

        # For 4D movies, include selected channel and channel axis in the cache key.
        if self.movie.ndim == 4:
            if channel_override is None:
                # Use the main GUI's channel selection.
                selected_chan = int(self.movieChannelCombo.currentText()) - 1
            else:
                # Use the override value.
                selected_chan = channel_override-1
            channel_axis = self._channel_axis
            cache_key = (frame_idx, selected_chan, channel_axis)
        else:
            cache_key = frame_idx

        # Initialize cache if it doesn't exist.
        if not hasattr(self, "frame_cache"):
            self.frame_cache = {}

        # Return the frame from the cache if available.
        if cache_key in self.frame_cache:
            return self.frame_cache[cache_key]

        try:
            if self.movie.ndim == 4:
                idx = [0] * self.movie.ndim
                idx[0] = frame_idx
                for ax in range(1, self.movie.ndim):
                    idx[ax] = selected_chan if ax == channel_axis else slice(None)
                frame = self.movie[tuple(idx)]
            else:
                frame = self.movie[frame_idx]
            # Store the computed frame in the cache.
            self.frame_cache[cache_key] = frame
            return frame
        except IndexError:
            print(f"index {frame_idx} out of bounds.")
            return None

    def on_channel_axis_changed(self):
        """
        Called when the user changes the channel axis selection.
        Verify that the selected axis is valid for the loaded movie.
        If the axis is invalid, display an error popup and revert to the previous working axis.
        """
        new_axis = self._channel_axis  # set by ChannelAxisDialog
        old_axis = getattr(self, "_channel_axis", 1)

        # If no movie is loaded, simply update the stored axis.
        if self.movie is None:
            self._channel_axis = new_axis
            return

        try:
            # Try accessing the movie dimension with the new axis.
            _ = self.movie.shape[new_axis]
            # If valid, update the stored channel axis.
            self._channel_axis = new_axis
            # Update the channel dropdown that depends on the channel axis.
            self.update_movie_channel_combo()
        except Exception as e:
            # If the axis is invalid, show an error and revert to the previous value.
            QMessageBox.critical(self, "Error", f"Invalid channel axis: {new_axis}.\nError: {str(e)}")
            self._channel_axis = old_axis

    def update_channel_axis_options(self):
        """
        Update the stored channel axis using the movie's available axes (excluding axis 0).
        Choose as default the axis with the smallest size (typically the channel axis).
        """
        if self.movie is None or self.movie.ndim != 4:
            return

        # List candidate axes: all axes except axis 0.
        candidate_axes = list(range(1, self.movie.ndim))
        # Pick the axis with the smallest size.
        default_axis = min(candidate_axes, key=lambda ax: self.movie.shape[ax])
        self._channel_axis = default_axis

        # Update the channel combo box options.
        self.update_movie_channel_combo()

    def on_channel_changed(self, index):
        # only multi-channel movies
        if self.movie is None or self.movie.ndim != 4:
            return

        if self.looping:
            self.stoploop()

        if self.refBtn.isChecked():
            self.refBtn.setChecked(False)

        self.cancel_left_click_sequence()

        # 1) figure out which channel we’re on
        ch = index + 1

        # 2) refresh kymographs
        self.update_kymo_list_for_channel()

        # 3) pick the right contrast-settings dict
        if self.sumBtn.isChecked():
            settings_store = self.channel_sum_contrast_settings
            display_fn     = self.movieCanvas.display_sum_frame
        else:
            settings_store = self.channel_contrast_settings
            display_fn     = None  # we’ll use update_image_data below

        # 4) if first time for this channel, compute & stash defaults
        if ch not in settings_store:
            # grab the very first frame of this channel
            frame0 = self.get_movie_frame(0)
            p15, p99 = np.percentile(frame0, (15, 99))
            vmin0    = int(p15 * (1.05 if self.sumBtn.isChecked() else 1.0))
            vmax0    = int(p99 * (1.20 if self.sumBtn.isChecked() else 1.10))
            delta    = vmax0 - vmin0
            settings_store[ch] = {
                'vmin':         vmin0,
                'vmax':         vmax0,
                'extended_min': vmin0 - int(0.7 * delta),
                'extended_max': vmax0 + int(1.4 * delta)
            }

        # 5) pull out the stored settings
        s = settings_store[ch]

        # 6) update the slider so it “sticks”
        slider = self.contrastControlsWidget.contrastRangeSlider
        slider.blockSignals(True)
        slider.setMinimum(s['extended_min'])
        slider.setMaximum(s['extended_max'])
        slider.setRangeValues(s['vmin'], s['vmax'])
        slider.blockSignals(False)

        # 7) push the contrast into the MovieCanvas
        mc = self.movieCanvas
        mc._default_vmin = s['vmin']
        mc._default_vmax = s['vmax']
        mc._vmin         = s['vmin']
        mc._vmax         = s['vmax']
        if mc._im is not None:
            mc._im.set_clim(s['vmin'], s['vmax'])

        # 8) redraw either sum‐mode or normal‐mode
        if self.sumBtn.isChecked():
            mc.clear_sum_cache()
            mc.display_sum_frame()
        else:
            # keep the same time‐point
            frame_idx = self.frameSlider.value()
            frame     = self.get_movie_frame(frame_idx)
            mc.update_image_data(frame)

        self._ch_overlay.setText(f"ch{ch}")
        self._ch_overlay.adjustSize()
        self._reposition_channel_overlay()
        self._ch_overlay.show()

        # 10) clear & redraw trajectories on the movie, now that channel has changed
        self.movieCanvas.clear_movie_trajectory_markers()
        self.movieCanvas.draw_trajectories_on_movie()

        if self.intensityCanvas.point_highlighted and ch == self.analysis_channel and self.intensityCanvas._last_plot_args is not None:

            ic_index = self.intensityCanvas.current_index

            # cache arrays once
            centers = np.asarray(self.analysis_search_centers)  # shape (N,2)
            cx, cy = centers[ic_index]

            mc.overlay_rectangle(cx, cy, int(2*self.searchWindowSpin.value()))
            mc.remove_gaussian_circle()

            fc = fs = pk = None
            # draw fit circle & intensity highlight
            if hasattr(self, "analysis_fit_params") and ic_index < len(self.analysis_fit_params):
                fc, fs, pk = self.analysis_fit_params[ic_index]

            pointcolor = self.intensityCanvas.get_current_point_color()
            mc.add_gaussian_circle(fc, fs, pointcolor)

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
                        disp_frame = (self.movie.shape[0] - 1) - self.frameSlider.value()
                        self.kymoCanvas.add_circle(
                            xk, disp_frame,
                            color=pointcolor if fc is not None else 'grey'
                        )


        self.movieCanvas.draw()

    def _on_overlay_clicked(self):
        # build a stand-alone QMenu
        menu = QMenu(None)
        menu.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint)
        menu.setAttribute(Qt.WA_TranslucentBackground)

        # populate it
        n_channels = (self.movie.shape[self._channel_axis]
                    if self.movie is not None and self.movie.ndim == 4 else 1)
        for i in range(n_channels):
            action = menu.addAction(f"ch{i+1}")
            action.setData(i)

        # compute global positions
        lbl = self._ch_overlay
        lbl_global = lbl.mapToGlobal(QPoint(0, 0))
        lbl_width  = lbl.width()
        # measure menu width from its sizeHint
        menu_width = menu.sizeHint().width()

        # center the menu’s x under the label
        x = lbl_global.x() + (lbl_width  - menu_width)//2
        # drop it just below the label
        y = lbl_global.y() + lbl.height()

        chosen = menu.exec_(QPoint(x, y))
        if chosen:
            idx = chosen.data()
            self.movieChannelCombo.setCurrentIndex(idx)

    def _reposition_channel_overlay(self):
        lbl = self._ch_overlay
        # 10px from left, 10px from top
        x = 10
        y = 10
        lbl.move(x, y)

    def update_movie_channel_combo(self, flash=False):
        if self.movie is None:
            self.channelControlContainer.setVisible(False)
            self._ch_overlay.hide()
            return

        current_channel = 1
        if self.movie.ndim == 4:
            self.channelControlContainer.setVisible(False) #OVERRIDE
            channel_axis = self._channel_axis
            try:
                current_channel = int(self.movieChannelCombo.currentText())
            except Exception:
                current_channel = 1

            self._ch_overlay.setText(f"ch{current_channel}")
            self._ch_overlay.adjustSize()
            self._reposition_channel_overlay()
            self._ch_overlay.show()

            self.movieChannelCombo.blockSignals(True)
            self.movieChannelCombo.clear()
            num_channels = self.movie.shape[channel_axis]
            for i in range(num_channels):
                self.movieChannelCombo.addItem(str(i + 1))
            self.movieChannelCombo.setCurrentIndex(current_channel - 1)
            self.movieChannelCombo.blockSignals(False)
            self.movieChannelCombo.setEnabled(True)

            # Get the first frame for the selected channel if needed.
            first_frame = self.get_movie_frame(0)
        else:
            self.channelControlContainer.setVisible(False)
            self._ch_overlay.hide()
            self.movieChannelCombo.blockSignals(True)
            self.movieChannelCombo.clear()
            self.movieChannelCombo.addItem("1")
            self.movieChannelCombo.blockSignals(False)
            self.movieChannelCombo.setEnabled(False)
            first_frame = self.movie[0]
            # Set default current_channel for 3D movies:
            current_channel = 1
    
        # Branch on the current mode (sum vs. normal) and obtain contrast settings.
        if self.sumBtn.isChecked():
            self.movieCanvas.clear_sum_cache()
            self.movieCanvas.display_sum_frame()
            # Sum mode – use channel_sum_contrast_settings.
            if current_channel not in self.channel_sum_contrast_settings:
                p15, p99 = np.percentile(first_frame, (15, 99))
                default_vmin = int(p15 * 1.05)
                default_vmax = int(p99 * 1.2)
                delta = default_vmax - default_vmin
                settings = {
                    'vmin': default_vmin,
                    'vmax': default_vmax,
                    'extended_min': default_vmin - int(0.7 * delta),
                    'extended_max': default_vmax + int(1.4 * delta)
                }
                self.channel_sum_contrast_settings[current_channel] = settings
            else:
                settings = self.channel_sum_contrast_settings[current_channel]
        else:
            # Normal mode – use channel_contrast_settings.
            if current_channel not in self.channel_contrast_settings:
                p15, p99 = np.percentile(first_frame, (15, 99))
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
            else:
                settings = self.channel_contrast_settings[current_channel]

        if flash:
            self.flash_message(f"Channel {current_channel}")
        #print(current_channel, settings)

        # Update the movie canvas's internal contrast defaults.
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
        self.contrastControlsWidget.contrastRangeSlider.update()

        # Finally, display the first frame with the correct contrast.
        self.movieCanvas.update_image_data(first_frame)

    def load_reference(self):
        if self.movie is None:
            QMessageBox.warning(self, "", 
                "Please load a movie before loading a reference.")
            return
        fname, _ = QFileDialog.getOpenFileName(
            self, "Open Reference Image", "", "Image Files (*.tif *.tiff *.png *.jpg)"
        )
        if not fname:
            return
        try:
            ref_img = tifffile.imread(fname)
            if ref_img.ndim == 3:
                # Heuristic to decide if the image is multi‐channel:
                small_first = ref_img.shape[0] <= 4 and ref_img.shape[0] > 1
                small_last = ref_img.shape[-1] <= 4 and ref_img.shape[-1] > 1

                if small_first and small_last:
                    choice, ok = QtWidgets.QInputDialog.getItem(
                        self, "Channel Axis Ambiguity",
                        "Is the reference image stored as channels-first (axis 0) or channels-last (last axis)?",
                        ["Channels-first", "Channels-last"],
                        0, False
                    )
                    if not ok:
                        return
                    channel_axis = 0 if choice == "Channels-first" else -1
                elif small_first:
                    channel_axis = 0
                elif small_last:
                    channel_axis = -1
                else:
                    channel_axis = None

                if channel_axis is not None:
                    if channel_axis == 0:
                        channels = ref_img.shape[0]
                        prompt = "Reference image has multiple channels (channels-first). Choose one:"
                    else:
                        channels = ref_img.shape[-1]
                        prompt = "Reference image has multiple channels (channels-last). Choose one:"
                    if channels > 1:
                        channel_str, ok = QtWidgets.QInputDialog.getItem(
                            self, "Select Channel", prompt,
                            [f"Ch. {i+1}" for i in range(channels)], 0, False
                        )
                        if not ok:
                            return
                        chosen_channel = int(channel_str.split()[-1]) - 1
                        if channel_axis == 0:
                            ref_img = ref_img[chosen_channel, :, :]
                        else:
                            ref_img = ref_img[:, :, chosen_channel]
            ref_img = np.squeeze(ref_img)

            # (Optionally, verify that its dimensions match the current movie frame.)
            if self.movie is not None:
                movie_frame = self.movieCanvas.image
                if movie_frame is None:
                    movie_frame = self.get_movie_frame(0)
                if movie_frame.shape[0:2] != ref_img.shape[0:2]:
                    QMessageBox.warning(
                        self,
                        "Dimension Mismatch",
                        "The reference image x/y dimensions do not match the currently displayed movie frame."
                    )
                    return

            self.referenceImage = ref_img
            self.referenceImage_raw = ref_img
            self._ref_dx = 0
            self._ref_dy = 0

            # *** Compute reference contrast settings ***
            p15, p99 = np.percentile(ref_img, (15, 99))
            ref_vmin = int(p15)
            ref_vmax = int(p99 * 1.1)
            delta = ref_vmax - ref_vmin
            self.reference_contrast_settings = {
                'vmin': ref_vmin,
                'vmax': ref_vmax,
                'extended_min': ref_vmin - int(0.7 * delta),
                'extended_max': ref_vmax + int(1.4 * delta)
            }
            # Make the Ref. button visible.
            self.refBtn.setVisible(False)
            if hasattr(self, "ref_container"):
                self.ref_container.setVisible(False)
            if hasattr(self, "ref_spacer"):
                self.ref_spacer.setVisible(False)
            self.refBtn.blockSignals(True)
            self.refBtn.setChecked(False)
            self.refBtn.blockSignals(False)
            self.refBtn.setVisible(True)
            if hasattr(self, "ref_container"):
                self.ref_container.setVisible(True)
            if hasattr(self, "ref_spacer"):
                self.ref_spacer.setVisible(True)

            reffilt = self.refBtn._bubble_filter
            reffilt._wobj = self.refBtn
            self.refBtn.setChecked(True)
            QTimer.singleShot(1000, lambda: reffilt._showBubble(force=True))

        except Exception as e:
            QMessageBox.critical(self, "Load Error", f"Could not load reference image:\n{str(e)}")


    def on_ref_toggled(self, checked):
        if checked:
            # Turn off sum mode if active.
            if self.sumBtn.isChecked():
                self.sumBtn.blockSignals(True)
                self.sumBtn.setChecked(False)
                self.sumBtn.blockSignals(False)
                self.sumBtn.setStyleSheet("")
                self.movieCanvas.sum_mode = False
            # Apply the reference image and its contrast.
            settings = getattr(self, "reference_contrast_settings", None) or {}

            # Use the raw reference and apply the persistent translation.
            ref_raw = getattr(self, "referenceImage_raw", None)
            if ref_raw is None:
                ref_raw = getattr(self, "referenceImage", None)
            if ref_raw is None:
                return

            if not settings or 'vmin' not in settings or 'vmax' not in settings:
                p15, p99 = np.percentile(ref_raw, (15, 99))
                ref_vmin = int(p15)
                ref_vmax = int(p99 * 1.1)
                delta = ref_vmax - ref_vmin
                settings = {
                    'vmin': ref_vmin,
                    'vmax': ref_vmax,
                    'extended_min': ref_vmin - int(0.7 * delta),
                    'extended_max': ref_vmax + int(1.4 * delta)
                }
                self.reference_contrast_settings = settings

            dx = int(getattr(self, "_ref_dx", 0))
            dy = int(getattr(self, "_ref_dy", 0))
            ref_show = self._shift_image_no_wrap(ref_raw, dx, dy, fill_value=0)

            self.movieCanvas.image = ref_show
            if self.movieCanvas._im is not None:
                self.movieCanvas._im.set_data(ref_show)

            self.movieCanvas._default_vmin = settings['vmin']
            self.movieCanvas._default_vmax = settings['vmax']
            self.movieCanvas.set_display_range(settings['vmin'], settings['vmax'])
            slider = self.contrastControlsWidget.contrastRangeSlider
            slider.blockSignals(True)
            slider.setMinimum(settings['extended_min'])
            slider.setMaximum(settings['extended_max'])
            slider.setRangeValues(settings['vmin'], settings['vmax'])
            slider.blockSignals(False)
            slider.update()
                
            self.movieCanvas._bg = None
            self._rebuild_movie_blit_background()

        else:
            # Persist the current reference-contrast settings before switching away.
            if hasattr(self, "contrastControlsWidget"):
                slider = self.contrastControlsWidget.contrastRangeSlider
                vmin = slider.lowerValue()
                vmax = slider.upperValue()
                self.reference_contrast_settings = {
                    'vmin': vmin,
                    'vmax': vmax,
                    'extended_min': slider.minimum(),
                    'extended_max': slider.maximum()
                }
            self.refBtn.setStyleSheet("")
            # Only revert if sum mode is off.
            if not self.sumBtn.isChecked():
                frame = self.get_movie_frame(self.frameSlider.value())
                if frame is not None:
                    # Determine the appropriate contrast settings.
                    if self.movie.ndim == 4:
                        try:
                            current_channel = int(self.movieChannelCombo.currentText())
                        except Exception:
                            current_channel = 1
                        # If the contrast settings haven't been set for the current channel, compute defaults.
                        if current_channel not in self.channel_contrast_settings:
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
                        else:
                            settings = self.channel_contrast_settings[current_channel]
                    else:
                        # For a single-channel (3D) movie, we always use channel 1.
                        if 1 not in self.channel_contrast_settings:
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
                            self.channel_contrast_settings[1] = settings
                        else:
                            settings = self.channel_contrast_settings[1]
                    # Now apply the contrast to the movie canvas.
                    self.movieCanvas.set_display_range(settings['vmin'], settings['vmax'])
                    slider = self.contrastControlsWidget.contrastRangeSlider
                    slider.blockSignals(True)
                    slider.setMinimum(settings['extended_min'])
                    slider.setMaximum(settings['extended_max'])
                    slider.setRangeValues(settings['vmin'], settings['vmax'])
                    slider.blockSignals(False)
                    slider.update()
                    self.movieCanvas.image = frame
                    if self.movieCanvas._im is not None:
                        self.movieCanvas._im.set_data(frame)

                    self.movieCanvas._bg = None
                    if getattr(self, "temp_movie_analysis_line", None) is not None:
                        self._rebuild_movie_blit_background()
                    else:
                        self.movieCanvas.draw_idle()

    def _shift_image_no_wrap(self, img, dx, dy, fill_value=0):
        """Shift an image by (dx, dy) without wraparound.

        dx > 0 shifts right, dy > 0 shifts down. Areas moved in are filled.
        Works for 2D arrays and for arrays with trailing channel dimensions.
        """
        if img is None:
            return None
        dx = int(dx)
        dy = int(dy)
        if dx == 0 and dy == 0:
            return img

        out = np.empty_like(img)
        out[...] = fill_value

        h, w = img.shape[0], img.shape[1]

        if dx >= 0:
            src_x0, src_x1 = 0, max(0, w - dx)
            dst_x0, dst_x1 = dx, w
        else:
            src_x0, src_x1 = -dx, w
            dst_x0, dst_x1 = 0, max(0, w + dx)

        if dy >= 0:
            src_y0, src_y1 = 0, max(0, h - dy)
            dst_y0, dst_y1 = dy, h
        else:
            src_y0, src_y1 = -dy, h
            dst_y0, dst_y1 = 0, max(0, h + dy)

        if (src_x1 <= src_x0) or (src_y1 <= src_y0) or (dst_x1 <= dst_x0) or (dst_y1 <= dst_y0):
            return out

        out[dst_y0:dst_y1, dst_x0:dst_x1, ...] = img[src_y0:src_y1, src_x0:src_x1, ...]
        return out

    def _refresh_reference_view(self):
        """Re-render the reference with the current stored translation, if ref mode is active."""
        if not hasattr(self, "refBtn") or not self.refBtn.isChecked():
            return

        ref_raw = getattr(self, "referenceImage_raw", None)
        if ref_raw is None:
            ref_raw = getattr(self, "referenceImage", None)
        if ref_raw is None:
            return

        dx = int(getattr(self, "_ref_dx", 0))
        dy = int(getattr(self, "_ref_dy", 0))
        ref_show = self._shift_image_no_wrap(ref_raw, dx, dy, fill_value=0)

        settings = getattr(self, "reference_contrast_settings", None)
        if settings:
            self.movieCanvas.set_display_range(settings['vmin'], settings['vmax'])

        self.movieCanvas.image = ref_show
        if self.movieCanvas._im is not None:
            self.movieCanvas._im.set_data(ref_show)
        self.movieCanvas.draw_idle()

    def _nudge_reference_translation(self, dx_step, dy_step):
        """Adjust the stored reference translation and redraw if reference is shown."""
        self._ref_dx = int(getattr(self, "_ref_dx", 0)) + int(dx_step)
        self._ref_dy = int(getattr(self, "_ref_dy", 0)) + int(dy_step)
        self._refresh_reference_view()

    def load_kymographs(self):
        fnames, _ = QFileDialog.getOpenFileNames(
            self, "Open Kymograph TIFF(s)", "", "TIFF Files (*.tif *.tiff)"
        )
        if fnames:
            for fname in fnames:
                try:
                    kymo = tifffile.imread(fname)
                    # Check if the kymograph has an invalid shape.
                    if kymo.ndim == 3 and kymo.shape[-1] not in (1, 3, 4):
                        QMessageBox.warning(
                            self, "Invalid Kymograph",
                            f"File '{os.path.basename(fname)}' has an invalid shape {kymo.shape}.\n"
                            "It must be a 2D image or a 3D image with 1, 3, or 4 channels."
                        )
                        continue  # Skip this file.
                        
                    # Generate a unique key for the kymograph.
                    base = os.path.basename(fname)
                    unique_name = base
                    suffix = 1
                    while unique_name in self.kymographs:
                        suffix += 1
                        unique_name = f"{base}-{suffix}"
                    self.kymographs[unique_name] = kymo
                    self.kymoCombo.insertItem(0, unique_name)
                    self.kymoCombo.setEnabled(self.kymoCombo.count() > 0)
                    self.kymoCombo.setCurrentIndex(0)
                except Exception as e:
                    QMessageBox.critical(self, "Error", f"Could not load kymograph {fname}:\n{str(e)}")
        
        self.update_kymo_visibility()

    def load_kymograph_with_overlays(self):
        """
        Load a single kymograph TIFF and its ImageJ multipoint overlay,
        convert into start/end trajectory points, build a DataFrame,
        and hand off to trajectory loader logic.
        """
        fname, _ = QFileDialog.getOpenFileName(
            self,
            "Open Kymograph TIFF", "",
            "TIFF Files (*.tif *.tiff)"
        )
        if not fname:
            return

        try:
            # 1) load image array and validate shape
            kymo = tifffile.imread(fname)
            if kymo.ndim == 3 and kymo.shape[-1] not in (1, 3, 4):
                QMessageBox.warning(
                    self, "Invalid Kymograph",
                    f"File '{os.path.basename(fname)}' has invalid shape {kymo.shape}."
                )
                return

            # store in UI
            base = os.path.basename(fname)
            unique = base
            i = 1
            while unique in self.kymographs:
                i += 1
                unique = f"{base}-{i}"
            self.kymographs[unique] = kymo
            self.kymoCombo.insertItem(0, unique)
            self.kymoCombo.setCurrentIndex(0)

            # 2) extract ROI blob from ImageJ metadata or raw tag
            with tifffile.TiffFile(fname) as tif:
                ij = tif.imagej_metadata or {}
                blob = None
                if 'Overlays' in ij and ij['Overlays']:
                    blob = ij['Overlays'][0]
                elif 'ROI' in ij:
                    blob = ij['ROI']
                else:
                    tag = tif.pages[0].tags.get(50838)
                    blob = tag.value if tag else None

            if blob is None:
                QMessageBox.information(
                    self, "No Overlay",
                    f"No multipoint ROI found in '{base}'."
                )
                return

            # 3) parse blob into list of (x,y)
            points = parse_roi_blob(blob)
            # 4) group into trajectories: two points = one trajectory
            rows = []
            for idx in range(0, len(points), 2):
                sx, sy = points[idx]      # sy is already measured from top
                ex, ey = points[idx+1]
                fs = int(round(sy))
                fe = int(round(ey))
                # map x-axis point back into movie coords
                xs, ys = self.compute_roi_point(self.rois[self.roiCombo.currentText()], sx)
                xe, ye = self.compute_roi_point(self.rois[self.roiCombo.currentText()], ex)
                traj_id = idx//2 + 1
                rows.append({
                    'Trajectory': traj_id,
                    'Frame': fs,
                    'Search Center X': xs,
                    'Search Center Y': ys
                })
                rows.append({
                    'Trajectory': traj_id,
                    'Frame': fe,
                    'Search Center X': xe,
                    'Search Center Y': ye
                })

            df = pd.DataFrame(rows)

            # 5) hand off to a helper that processes a DataFrame
            # You should refactor load_trajectories() into load_trajectories_from_df(df)
            self.trajectoryCanvas.load_trajectories_from_df(df)
            self.kymoCombo.setEnabled(True)

        except Exception as e:
            QMessageBox.critical(self, "Load Error", f"Failed to load overlays: {e}")

        self.update_table_visibility()

    def load_roi(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "Open ROI File(s)", "", "ROI Files (*.roi *.zip)"
        )
        if not files:
            return

        # 1) Read all ROIs
        rois = {}
        for file in files:
            if file.lower().endswith('.zip'):
                rois.update(read_roi.read_roi_zip(file))
            else:
                rois.update(read_roi.read_roi_file(file))

        # 2) Replace internal ROI store & rebuild the ROI combo
        self.rois = rois
        self.roiCombo.clear()
        for roi_name in sorted(rois):
            self.roiCombo.addItem(roi_name)
        self.roiCombo.setEnabled(bool(rois))
        self.update_roilist_visibility()

        # 3) Ask user if they want to generate kymographs now
        resp = QMessageBox.question(
            self,
            "Generate Kymographs",
            "Generate kymographs for these ROIs across all channels?",
            QMessageBox.Yes | QMessageBox.No
        )

        # 4) Determine how many channels we have
        if self.movie.ndim == 4:
            n_chan = self.movie.shape[self._channel_axis]
        else:
            n_chan = 1

        # 5) For each ROI, loop over ALL channels
        for roi_name, roi in rois.items():
            for ch in range(n_chan):
                kymo_name = f"ch{ch+1}-{roi_name}"
                if resp == QMessageBox.Yes:
                    # Generate kymograph for this channel
                    kymo = self.movieCanvas.generate_kymograph(
                        roi, channel_override=ch+1
                    )
                    self.kymographs[kymo_name] = kymo
                    self.kymo_roi_map[kymo_name] = {
                        "roi":      roi_name,
                        "channel":  ch+1,
                        "orphaned": False
                    }
                else:
                    # Register as orphaned
                    self.kymo_roi_map[kymo_name] = {
                        "roi":      roi_name,
                        "channel":  ch+1,
                        "orphaned": True
                    }

        # 6) Rebuild & show only current channel’s list
        self.update_kymo_list_for_channel()
        self.update_kymo_visibility()

    def _select_next_kymo(self):
        """Advance the kymo combo one step (if possible)."""
        idx = self.kymoCombo.currentIndex()
        if idx >= 0 and idx < self.kymoCombo.count() - 1:
            self.kymoCombo.setCurrentIndex(idx + 1)

    def _select_prev_kymo(self):
        """Go back one step in the kymo combo (if possible)."""
        idx = self.kymoCombo.currentIndex()
        if idx > 0:
            self.kymoCombo.setCurrentIndex(idx - 1)

    def update_kymo_list_for_channel(self):
        ch = int(self.movieChannelCombo.currentText())
        self.kymoCombo.blockSignals(True)
        self.kymoCombo.clear()

        # 1) Populate only this channel’s items
        for name, info in self.kymo_roi_map.items():
            if info["channel"] == ch and not info.get("orphaned", False):
                self.kymoCombo.addItem(name)
        self.kymoCombo.blockSignals(False)

        # Get all names in this channel
        names = [self.kymoCombo.itemText(i) for i in range(self.kymoCombo.count())]

        # 2) If there are no kymographs at all, clear and return
        if not names:
            self.kymoCombo.setCurrentIndex(-1)
            self.kymoCanvas.ax.cla()
            self.kymoCanvas.ax.axis("off")
            self.kymoCanvas.draw_idle()
            # self._last_roi = None
            return

        # 3) Try to find a “sister” matching the last ROI
        sel = None
        last_roi = self._last_roi
        if last_roi is not None:
            for name in names:
                if self.kymo_roi_map[name]["roi"] == last_roi:
                    sel = name
                    break

        # 4) If no sister found, we want a blank canvas
        if sel is None:
            self.kymoCombo.setCurrentIndex(-1)
            self.kymoCanvas.ax.cla()
            self.kymoCanvas.ax.axis("off")
            self.kymoCanvas.draw_idle()
            # don’t change self._last_roi — so that if the user later switches
            # back to a channel where a sister *does* exist, it’ll pop right in.
            return

        # 5) Otherwise select & display the sister
        self.kymoCombo.setCurrentText(sel)
        self.kymo_changed()

    def _save_zoom_for_roi(self, roiName):
        """Stash the current scale & center under this ROI."""
        c = self.kymoCanvas
        self._roi_zoom_states[roiName] = (c.scale, c.zoom_center)

    def _restore_zoom_for_roi(self, roiName):
        """Re-apply stored scale & center (pan) for this ROI, if any."""
        if roiName in self._roi_zoom_states:
            scale, center = self._roi_zoom_states[roiName]
            c = self.kymoCanvas
            c.scale       = scale
            c.zoom_center = center
            c.manual_zoom = True
            c.update_view()

    def kymo_changed(self):

        self.cancel_left_click_sequence()
        
        # — Save last ROI’s view if user did a manual zoom/pan
        if self._last_roi and self.kymoCanvas.manual_zoom:
            self._save_zoom_for_roi(self._last_roi)
            self.kymoCanvas.manual_zoom = False

        # — Grab the new selection
        kymoName = self.kymoCombo.currentText()
        info     = self.kymo_roi_map.get(kymoName)
        if not info:
            # no valid kymo → clear
            self.kymoCanvas.ax.cla()
            self.kymoCanvas.ax.axis("off")
            self.kymoCanvas.draw_idle()
            self._last_roi = None
            return

        roiName = info["roi"]

        # — Sync the ROI & channel controls
        self.roiCombo.setCurrentText(roiName)
        self.movieChannelCombo.blockSignals(True)
        self.movieChannelCombo.setCurrentIndex(info["channel"] - 1)
        self.movieChannelCombo.blockSignals(False)

        # — Display the kymograph (raw or LoG)
        img = self.kymographs.get(kymoName)
        if img is None:
            return
        if getattr(self, "applylogfilter", False):
            img = self._get_log_kymograph(kymoName, base=img)
        if img is None:
            return
        img = np.flipud(img)
        self.kymoCanvas.display_image(img)
        if hasattr(self, "kymocontrastControlsWidget"):
            self._apply_kymo_contrast_settings(kymoName)
        self.kymoCanvas.draw_trajectories_on_kymo()
        self.kymoCanvas.draw_idle()

        # — Restore pan+zoom for this ROI (if any)
        self._restore_zoom_for_roi(roiName)

        # — Remember for next save
        self._last_roi = roiName

    def _get_log_kymograph(self, kymo_name, base=None):
        if not kymo_name:
            return None
        if not hasattr(self, "kymographs_log"):
            self.kymographs_log = {}
        cached = self.kymographs_log.get(kymo_name)
        if cached is not None:
            return cached
        if base is None:
            base = self.kymographs.get(kymo_name)
        if base is None:
            return None
        log_kymo = self._compute_log_kymograph(base)
        self.kymographs_log[kymo_name] = log_kymo
        return log_kymo

    def _compute_log_kymograph(self, kymo):
        sigma = getattr(self, "log_sigma", 1.5)
        kymo_f = kymo.astype(np.float32)
        log_kymo = -gaussian_laplace(kymo_f, sigma=sigma)
        minv, maxv = log_kymo.min(), log_kymo.max()
        if maxv > minv:
            log_kymo = (log_kymo - minv) / (maxv - minv) * 254 + 1
        else:
            log_kymo = np.ones_like(log_kymo) * 128
        return log_kymo.astype(np.uint8)

    def delete_current_kymograph(self):
        current = self.kymoCombo.currentText()
        if not current:
            return

        # 1) Remove mapping and drop any zoom state for its ROI
        mapping = self.kymo_roi_map.pop(current, None)
        if mapping:
            roi_name = mapping["roi"]
            # drop zoom/pan state
            self._roi_zoom_states.pop(roi_name, None)
            # if this was the last ROI we saw, clear it
            if self._last_roi == roi_name:
                self._last_roi = None
            # remove the ROI itself if nobody else references it
            if not any(info["roi"] == roi_name for info in self.kymo_roi_map.values()):
                self.rois.pop(roi_name, None)
                idx = self.roiCombo.findText(roi_name)
                if idx >= 0:
                    self.roiCombo.removeItem(idx)

        # 2) Delete the kymograph
        self.kymographs.pop(current, None)
        if hasattr(self, "kymographs_log"):
            self.kymographs_log.pop(current, None)
        if hasattr(self, "kymo_contrast_settings"):
            self.kymo_contrast_settings.pop(current, None)
        if hasattr(self, "kymo_log_contrast_settings"):
            self.kymo_log_contrast_settings.pop(current, None)

        # 3) Remove it from the combo
        old_index = self.kymoCombo.currentIndex()
        self.kymoCombo.removeItem(old_index)

        # 4) Show next one or clear
        if self.kymoCombo.count() > 0:
            new_index = old_index - 1 if old_index > 0 else 0
            self.kymoCombo.setCurrentIndex(new_index)
        else:
            self.kymoCanvas.ax.cla()
            self.kymoCanvas.ax.axis("off")
            self.kymoCanvas.draw_idle()

        # 5) Re-run selection & visibility
        self.kymo_changed()
        self.update_kymo_visibility()
        self.update_roilist_visibility()
    def invert_current_kymograph(self):
        current = self.kymoCombo.currentText()
        if not current:
            return
        info = self.kymo_roi_map.get(current)
        if not info:
            return
        roi_name = info.get("roi")
        roi = self.rois.get(roi_name)
        if not roi:
            return

        def _roi_signature(roi_dict):
            if not isinstance(roi_dict, dict):
                return None
            try:
                xs = tuple(float(x) for x in roi_dict.get("x", []))
                ys = tuple(float(y) for y in roi_dict.get("y", []))
                pts = tuple((float(p[0]), float(p[1])) for p in roi_dict.get("points", []))
            except Exception:
                return None
            return (roi_dict.get("type"), xs, ys, pts)

        sig_before = _roi_signature(roi)

        # Flip ROI direction (reverse points along the line).
        if "x" in roi:
            roi["x"] = list(reversed(roi["x"]))
        if "y" in roi:
            roi["y"] = list(reversed(roi["y"]))
        if "points" in roi:
            roi["points"] = list(reversed(roi["points"]))

        # Flip all kymographs associated with this ROI.
        for name, mapping in self.kymo_roi_map.items():
            if mapping.get("roi") == roi_name and name in self.kymographs:
                self.kymographs[name] = np.fliplr(self.kymographs[name])
            if mapping.get("roi") == roi_name and hasattr(self, "kymographs_log"):
                if name in self.kymographs_log:
                    self.kymographs_log[name] = np.fliplr(self.kymographs_log[name])

        # Determine kymograph width for anchor inversion.
        kymo_width = None
        for name, mapping in self.kymo_roi_map.items():
            if mapping.get("roi") == roi_name and name in self.kymographs:
                try:
                    kymo_width = int(self.kymographs[name].shape[1])
                    break
                except Exception:
                    pass
        if kymo_width is None:
            try:
                xs = np.asarray(roi.get("x", []), float)
                ys = np.asarray(roi.get("y", []), float)
                if xs.size >= 2:
                    seg_lengths = np.hypot(np.diff(xs), np.diff(ys))
                    total_length = float(seg_lengths.sum())
                    kymo_width = max(int(total_length), 2)
            except Exception:
                kymo_width = None

        sig_after = _roi_signature(roi)

        def _roi_matches(traj_roi):
            if traj_roi is roi:
                return True
            if sig_before is not None and _roi_signature(traj_roi) == sig_before:
                return True
            if sig_after is not None and _roi_signature(traj_roi) == sig_after:
                return True
            return False

        # Update trajectories tied to this ROI (anchors are in kymo coordinates).
        trajs = getattr(self, "trajectoryCanvas", None)
        if trajs is not None:
            for traj in trajs.trajectories:
                traj_roi = traj.get("roi")
                if isinstance(traj_roi, dict):
                    if not _roi_matches(traj_roi):
                        continue
                    if traj_roi is not roi:
                        traj_roi["type"] = roi.get("type", traj_roi.get("type"))
                        traj_roi["x"] = list(roi.get("x", []))
                        traj_roi["y"] = list(roi.get("y", []))
                        if "points" in roi:
                            traj_roi["points"] = list(roi.get("points", []))
                else:
                    # For trajectories without an ROI attached, fall back to the same
                    # proximity test used for kymo overlays.
                    if not self._traj_matches_current_kymo(traj, roi):
                        continue
                anchors = traj.get("anchors") or []
                if anchors and kymo_width is not None:
                    new_anchors = []
                    for frame, xk, yk in anchors:
                        if xk is None:
                            new_anchors.append((frame, xk, yk))
                            continue
                        new_xk = (kymo_width - 1) - float(xk)
                        new_anchors.append((int(frame), float(new_xk), float(yk)))
                    traj["anchors"] = new_anchors

        # Update any active analysis anchors on this ROI.
        analysis_roi = getattr(self, "analysis_roi", None)
        if isinstance(analysis_roi, dict) and _roi_matches(analysis_roi):
            if analysis_roi is not roi:
                analysis_roi["type"] = roi.get("type", analysis_roi.get("type"))
                analysis_roi["x"] = list(roi.get("x", []))
                analysis_roi["y"] = list(roi.get("y", []))
                if "points" in roi:
                    analysis_roi["points"] = list(roi.get("points", []))
            anchors = getattr(self, "analysis_anchors", None) or []
            if anchors and kymo_width is not None:
                new_anchors = []
                for frame, xk, yk in anchors:
                    if xk is None:
                        new_anchors.append((frame, xk, yk))
                        continue
                    new_xk = (kymo_width - 1) - float(xk)
                    new_anchors.append((int(frame), float(new_xk), float(yk)))
                self.analysis_anchors = new_anchors

        if getattr(self, "roi_overlay_active", False):
            self.overlay_all_rois()

        # Refresh current kymograph view and overlays.
        if self.kymoCombo.currentText():
            self.kymo_changed()

    def clear_kymographs(self, prompt=True):
        reply = QMessageBox.Yes
        if prompt:
            reply = QMessageBox.question(
                self,
                "Delete Kymographs",
                "Are you sure you want to delete all kymographs?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
        if reply != QMessageBox.Yes and prompt:
            return

        # 1) First, remove any ROIs associated with kymographs
        for mapping in list(self.kymo_roi_map.values()):
            # extract the ROI name whether mapping is a dict or a plain string
            if isinstance(mapping, dict):
                roi_name = mapping.get("roi")
            else:
                roi_name = mapping

            if roi_name and roi_name in self.rois:
                # delete from the internal dict
                del self.rois[roi_name]
                # remove from the ROI combo box
                idx = self.roiCombo.findText(roi_name)
                if idx >= 0:
                    self.roiCombo.removeItem(idx)

        self.kymographs.clear()
        if hasattr(self, "kymographs_log"):
            self.kymographs_log.clear()
        self.kymo_roi_map.clear()
        self._roi_zoom_states.clear()
        self._last_roi = None
        if hasattr(self, "kymo_contrast_settings"):
            self.kymo_contrast_settings.clear()
        if hasattr(self, "kymo_log_contrast_settings"):
            self.kymo_log_contrast_settings.clear()
        self.kymoCombo.clear()
        self.kymoCanvas.ax.cla()
        self.kymoCanvas.ax.axis("off")
        self.kymoCanvas.draw_idle()
        self.update_kymo_visibility()
        self.update_roilist_visibility()

    def clear_rois(self):
        # Clear the ROI combo box and the dictionary.
        self.rois.clear()
        self.roiCombo.clear()

        # Remove the ROI overlay line (if any) from the movie canvas.
        if hasattr(self.movieCanvas, "roi_line") and self.movieCanvas.roi_line is not None:
            try:
                self.movieCanvas.roi_line.remove()
            except Exception:
                pass
            self.movieCanvas.roi_line = None

        # Also remove any additional ROI lines and text annotations.
        if hasattr(self.movieCanvas, "roi_lines"):
            for line in self.movieCanvas.roi_lines:
                try:
                    line.remove()
                except Exception:
                    pass
            self.movieCanvas.roi_lines = []
        if hasattr(self.movieCanvas, "roi_texts"):
            for txt in self.movieCanvas.roi_texts:
                try:
                    txt.remove()
                except Exception:
                    pass
            self.movieCanvas.roi_texts = []

        self._roi_zoom_states.clear()
        self._last_roi = None
        self.movieCanvas.draw_idle()
        self.update_kymo_visibility()
        self.update_roilist_visibility()
        
    def save_rois(self):
        # Ask user where to save the ZIP file.
        filename, _ = QFileDialog.getSaveFileName(
            self, "Save ROIs as ImageJ ROI Zip", "", "ZIP Files (*.zip)"
        )
        if not filename:
            return

        # Ensure the file has a .zip extension.
        if not filename.lower().endswith('.zip'):
            filename += '.zip'

        try:
            with zipfile.ZipFile(filename, 'w') as zf:
                # Iterate over all ROIs stored in self.rois.
                for roi_name, roi in self.rois.items():
                    # Convert ROI dictionary into ImageJ binary format.
                    roi_bytes = convert_roi_to_binary(roi)
                    file_name = f"{roi_name:03}.roi"
                    zf.writestr(file_name, roi_bytes)
            # Optionally, show a message to the user that the file was saved.
            # QMessageBox.information(self, "Saved", f"ROIs successfully saved to {filename}")
        except Exception as e:
            QMessageBox.critical(self, "Save Error", f"Could not save ROIs:\n{str(e)}")

    def save_kymographs(self):
        import matplotlib.pyplot as plt

        # 1) Nothing to save?
        if not self.kymographs:
            QMessageBox.information(self, "No Kymographs", "Nothing to save.")
            return

        # clear any existing selection
        tw = self.trajectoryCanvas.table_widget
        tw.clearSelection()
        tw.setCurrentCell(-1, -1)

        # 2) ask user where/how to save
        all_items = list(self.kymographs.items())
        base_name = os.path.splitext(self.movieNameLabel.text())[0]
        dlg       = SaveKymographDialog(base_name, all_items, parent=self)
        if dlg.exec_() != QDialog.Accepted:
            return
        opts       = dlg.getOptions()
        directory  = opts["directory"]
        sel_names  = opts["selected"]
        ft         = opts["filetype"]
        do_overlay = opts["overlay"]
        do_labels  = opts.get("labels", True)
        do_scalebars = opts.get("scalebars", False)
        lut_label  = opts.get("lut", "Greys")
        current_inv = bool(getattr(self, "inverted_cmap", False))
        lut_cmap   = SaveKymographDialog.lut_to_cmap(
            lut_label,
            current_inverted=current_inv
        )
        lut_table = SaveKymographDialog.lut_to_table(
            lut_label,
            current_inverted=current_inv
        )

        def _log_kymo_save_error(err, stage, out_path=None, extra=None):
            log_path = os.path.join(os.path.expanduser("~"), "tracy_kymograph_save_error.log")
            try:
                ts = time.strftime("%Y-%m-%d %H:%M:%S")
                tif_ver = getattr(tifffile, "__version__", "unknown")
                np_ver = getattr(np, "__version__", "unknown")
                parts = [
                    f"[{ts}]",
                    f"stage={stage}",
                    f"error={repr(err)}",
                    f"path={out_path}",
                    f"tifffile={tif_ver}",
                    f"numpy={np_ver}",
                ]
                if extra is not None:
                    parts.append(f"extra={extra}")
                with open(log_path, "a", encoding="utf-8") as fh:
                    fh.write(" | ".join(parts) + "\n")
            except Exception:
                pass
            return log_path

        def _build_imagej_lut():
            return lut_table
        use_pref   = opts.get("use_prefix", False)
        mid        = opts.get("middle", "")
        custom     = opts.get("custom", False)
        cname      = opts.get("custom_name", "")

        # 3) progress bar
        total = len(sel_names)
        prog  = QProgressDialog("Saving kymographs…", "Cancel", 0, total, self)
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.show()

        tw = self.trajectoryCanvas.table_widget
        tw.clearSelection()
        tw.setCurrentCell(-1, -1)

        # remember the current kymo so we can re-select it at the end
        current = self.kymoCombo.currentText()

        # cache figure size for high-DPI save
        fig = self.kymoCanvas.fig
        orig_size = fig.get_size_inches().copy()
        layout_dpi = 600
        raster_scale = 3  # boost raster exports without scaling text
        pdf_raster_scale = raster_scale
        pdf_label_offset_scale = 0.5
        overlay_label_scale = 0.58
        overlay_marker_scale = 0.3
        overlay_line_scale = 0.65

        prev_kymo_overlay_mode = None
        if do_overlay:
            prev_kymo_overlay_mode = self.get_kymo_traj_overlay_mode()
            # Force kymo overlays on for export, independent of current UI overlay mode.
            self.kymo_traj_overlay_mode = "all"

        try:
            for i, name in enumerate(sel_names):
                if prog.wasCanceled():
                    break
                prog.setValue(i)

                # build filename
                if custom:
                    fname = cname or name
                else:
                    parts = ([base_name] if use_pref else []) + ([mid] if mid else []) + [name]
                    fname = "-".join(parts)
                out_path = os.path.join(directory, f"{fname}.{ft}")

                if do_overlay:
                    
                    # 1) match kymo canvas/preview orientation
                    kymo = np.flipud(self.kymographs[name])

                    # 2) ensure display_image does a full reset
                    self.kymoCanvas.manual_zoom = False
                    self.kymoCanvas.display_image(kymo)
                    self.kymoCanvas.set_cmap(lut_cmap)

                    # 3) switch ROI & channel
                    if name in self.kymographs:
                        self.kymoCombo.setCurrentText(name)
                        self.kymo_changed()

                    # 5) now draw a skinny overlay (axes already off, full‐frame)
                    self.kymoCanvas.draw_trajectories_on_kymo(
                        showsearchline=False,
                        skinny=True,
                        show_labels=do_labels
                    )
                    self.kymoCanvas.fig.canvas.draw()
                    ax = self.kymoCanvas.ax
                    dot_markers = []
                    try:
                        from matplotlib.collections import PathCollection
                        from matplotlib.markers import MarkerStyle
                    except Exception:
                        PathCollection = None
                    if PathCollection is not None:
                        circle_marker = None
                        try:
                            marker = MarkerStyle("o")
                            circle_marker = marker.get_path().transformed(marker.get_transform())
                        except Exception:
                            circle_marker = None
                        for coll in ax.collections:
                            if isinstance(coll, PathCollection):
                                try:
                                    orig_sizes = coll.get_sizes()
                                    orig_paths = coll.get_paths()
                                    orig_edge = coll.get_edgecolors()
                                    orig_lw = coll.get_linewidths()
                                    orig_aa = coll.get_antialiaseds()
                                    if circle_marker is not None:
                                        coll.set_paths([circle_marker])
                                    coll.set_edgecolors("none")
                                    coll.set_linewidths(0)
                                    try:
                                        coll.set_antialiaseds(True)
                                    except Exception:
                                        pass
                                    dot_markers.append((coll, orig_sizes, orig_paths, orig_edge, orig_lw, orig_aa))
                                except Exception:
                                    pass
                    preview_ax = self.kymoCanvas.ax
                    preview_w_px = float(preview_ax.bbox.width) if preview_ax.bbox.width else 0.0
                    preview_h_px = float(preview_ax.bbox.height) if preview_ax.bbox.height else 0.0

                    # 6) save
                    fig = self.kymoCanvas.fig
                    dpi = layout_dpi
                    save_dpi = dpi
                    if ft in ("png", "jpg"):
                        save_dpi = int(dpi * raster_scale)
                    elif ft == "pdf":
                        save_dpi = int(dpi * pdf_raster_scale)
                    fig.set_size_inches(kymo.shape[1] / dpi, kymo.shape[0] / dpi)
                    scale_artists = []
                    scaled_artists = []
                    pad_xlim = None
                    pad_ylim = None
                    if not do_scalebars:
                        ax = self.kymoCanvas.ax
                        h, w = kymo.shape[:2]
                        pad = max(8, int(0.06 * min(w, h)))
                        text_pad = max(8, int(0.04 * min(w, h)))
                        outer_pad = pad + text_pad * 4
                        x0, x1 = ax.get_xlim()
                        y0, y1 = ax.get_ylim()
                        x_inc = x1 >= x0
                        y_inc = y1 >= y0
                        right_x = w - 1
                        x_min, x_max = -outer_pad, right_x + outer_pad
                        y_min, y_max = -outer_pad, h - 1 + outer_pad
                        pad_xlim = (x0, x1)
                        pad_ylim = (y0, y1)
                        ax.set_xlim(x_min, x_max) if x_inc else ax.set_xlim(x_max, x_min)
                        ax.set_ylim(y_min, y_max) if y_inc else ax.set_ylim(y_max, y_min)
                    export_w_px = kymo.shape[1] * (float(save_dpi) / float(layout_dpi))
                    export_h_px = kymo.shape[0] * (float(save_dpi) / float(layout_dpi))
                    scale_factor = 1.0
                    if export_w_px > 0 and export_h_px > 0 and preview_w_px > 0 and preview_h_px > 0:
                        scale_factor = min(preview_w_px / export_w_px, preview_h_px / export_h_px)
                    apply_overlay_scale = (
                        scale_factor != 1.0
                        or overlay_marker_scale != 1.0
                        or overlay_label_scale != 1.0
                        or overlay_line_scale != 1.0
                        or (ft == "pdf" and save_dpi != fig.dpi)
                    )
                    if apply_overlay_scale:
                        ax = self.kymoCanvas.ax
                        try:
                            from matplotlib.collections import LineCollection
                        except Exception:
                            LineCollection = None
                        solid_line_styles = ("-", "solid")
                        target_lw = None
                        for line in ax.lines:
                            try:
                                if line.get_linestyle() in solid_line_styles:
                                    alpha = line.get_alpha()
                                    if alpha is None or alpha >= 0.7:
                                        lw = line.get_linewidth() * scale_factor * overlay_line_scale
                                        target_lw = lw if target_lw is None else max(target_lw, lw)
                            except Exception:
                                pass
                        if LineCollection is not None:
                            for coll in ax.collections:
                                if isinstance(coll, LineCollection):
                                    try:
                                        lws = coll.get_linewidths()
                                        if lws is None:
                                            continue
                                        max_lw = float(np.max(lws)) if len(lws) else float(lws)
                                        lw = max_lw * scale_factor * overlay_line_scale
                                        target_lw = lw if target_lw is None else max(target_lw, lw)
                                    except Exception:
                                        pass
                        if target_lw is None:
                            for line in ax.lines:
                                try:
                                    if line.get_linestyle() in solid_line_styles:
                                        lw = line.get_linewidth() * scale_factor * overlay_line_scale
                                        target_lw = lw if target_lw is None else max(target_lw, lw)
                                except Exception:
                                    pass
                        for line in ax.lines:
                            try:
                                orig = line.get_linewidth()
                                line.set_linewidth(orig * scale_factor * overlay_line_scale)
                                scaled_artists.append((line, orig, "lw"))
                            except Exception:
                                pass
                            try:
                                alpha = line.get_alpha()
                                if (
                                    target_lw is not None
                                    and line.get_linestyle() in solid_line_styles
                                    and alpha is not None
                                    and alpha <= 0.5
                                ):
                                    line.set_linewidth(target_lw)
                                    scaled_artists.append((line, alpha, "alpha"))
                                    line.set_alpha(0.8)
                            except Exception:
                                pass
                            try:
                                marker = line.get_marker()
                                if marker not in (None, "", "None"):
                                    orig_ms = line.get_markersize()
                                    line.set_markersize(orig_ms * scale_factor * overlay_marker_scale)
                                    scaled_artists.append((line, orig_ms, "ms"))
                            except Exception:
                                pass
                        line_collection_styles = []
                        for coll in ax.collections:
                            if LineCollection is not None and isinstance(coll, LineCollection):
                                try:
                                    orig_lw = coll.get_linewidths()
                                    orig_cap = coll.get_capstyle()
                                    orig_join = coll.get_joinstyle()
                                    try:
                                        coll.set_linewidths(orig_lw * scale_factor * overlay_line_scale)
                                        scaled_artists.append((coll, orig_lw, "lc_lw"))
                                    except Exception:
                                        pass
                                    coll.set_capstyle("round")
                                    coll.set_joinstyle("round")
                                    line_collection_styles.append((coll, orig_cap, orig_join))
                                except Exception:
                                    pass
                            try:
                                sizes = coll.get_sizes()
                                if sizes is not None and len(sizes):
                                    coll.set_sizes(sizes * (scale_factor ** 2) * (overlay_marker_scale ** 2))
                                    scaled_artists.append((coll, sizes, "sizes"))
                            except Exception:
                                pass
                        def _scale_text_obj(txt):
                            try:
                                orig = txt.get_fontsize()
                                txt.set_fontsize(orig * scale_factor * overlay_label_scale)
                                scaled_artists.append((txt, orig, "fs"))
                            except Exception:
                                pass
                            try:
                                bbox = txt.get_bbox_patch()
                                if bbox is not None:
                                    orig_lw = bbox.get_linewidth()
                                    bbox.set_linewidth(orig_lw * scale_factor * overlay_label_scale)
                                    scaled_artists.append((bbox, orig_lw, "lw"))
                            except Exception:
                                pass
                            try:
                                if ft == "pdf":
                                    try:
                                        from matplotlib.text import Annotation
                                    except Exception:
                                        Annotation = None
                                    if Annotation is not None and isinstance(txt, Annotation):
                                        textcoords = txt.get_anncoords()
                                        if textcoords == "offset pixels":
                                            orig_pos = txt.get_position()
                                            orig_tc = textcoords
                                            try:
                                                px_to_pt = 72.0 / float(fig.dpi)
                                            except Exception:
                                                px_to_pt = 0.75
                                            new_pos = (
                                                orig_pos[0] * px_to_pt * pdf_label_offset_scale,
                                                orig_pos[1] * px_to_pt * pdf_label_offset_scale,
                                            )
                                            try:
                                                txt.set_anncoords("offset points")
                                            except Exception:
                                                pass
                                            txt.set_position(new_pos)
                                            scaled_artists.append((txt, orig_pos, "pos"))
                                            scaled_artists.append((txt, orig_tc, "anncoords"))
                            except Exception:
                                pass
                        for text in ax.texts:
                            _scale_text_obj(text)
                        for artist in ax.artists:
                            if hasattr(artist, "get_fontsize") and hasattr(artist, "set_fontsize"):
                                _scale_text_obj(artist)
                    if do_scalebars:
                        ax = self.kymoCanvas.ax
                        prev_xlim = ax.get_xlim()
                        prev_ylim = ax.get_ylim()
                        scale_artists = SaveKymographDialog.draw_scale_bars(
                            ax,
                            kymo.shape,
                            origin="upper",
                            pixel_size_nm=getattr(self, "pixel_size", None),
                            frame_interval_ms=getattr(self, "frame_interval", None),
                            set_outer_pad=False,
                            dpi=layout_dpi,
                            size_scale=SaveKymographDialog.SCALEBAR_SIZE_SCALE,
                        )
                    fig.savefig(out_path, dpi=save_dpi,
                                facecolor=fig.get_facecolor(),
                                edgecolor="none",
                                bbox_inches="tight")
                    fig.set_size_inches(orig_size)
                    for artist in scale_artists:
                        try:
                            artist.remove()
                        except Exception:
                            pass
                    for artist, val, kind in scaled_artists:
                        try:
                            if kind == "lw":
                                artist.set_linewidth(val)
                            elif kind == "sizes":
                                artist.set_sizes(val)
                            elif kind == "fs":
                                artist.set_fontsize(val)
                            elif kind == "ms":
                                artist.set_markersize(val)
                            elif kind == "lc_lw":
                                artist.set_linewidths(val)
                            elif kind == "alpha":
                                artist.set_alpha(val)
                            elif kind == "pos":
                                artist.set_position(val)
                            elif kind == "anncoords":
                                try:
                                    from matplotlib.text import Annotation
                                except Exception:
                                    Annotation = None
                                if Annotation is not None and isinstance(artist, Annotation):
                                    try:
                                        artist.set_anncoords(val)
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                    if line_collection_styles:
                        for coll, cap, join in line_collection_styles:
                            try:
                                coll.set_capstyle(cap)
                                coll.set_joinstyle(join)
                            except Exception:
                                pass
                    if dot_markers:
                        for coll, sizes, paths, ec, lw, aa in dot_markers:
                            try:
                                if sizes is not None and len(sizes):
                                    coll.set_sizes(sizes)
                                if paths is not None:
                                    coll.set_paths(paths)
                                coll.set_edgecolors(ec)
                                coll.set_linewidths(lw)
                                if aa is not None:
                                    coll.set_antialiaseds(aa)
                            except Exception:
                                pass
                    if pad_xlim is not None and pad_ylim is not None:
                        ax = self.kymoCanvas.ax
                        ax.set_xlim(pad_xlim)
                        ax.set_ylim(pad_ylim)
                    if do_scalebars:
                        ax.set_xlim(prev_xlim)
                        ax.set_ylim(prev_ylim)

                else:
                    # plain export
                    kymo = self.kymographs[name]
                    if ft == "tif":
                        lut = _build_imagej_lut()
                        lut = np.ascontiguousarray(lut, dtype=np.uint8)
                        settings = getattr(self, "kymo_contrast_settings", {}).get(name)
                        # ImageJ metadata/LUTs are ignored for unsupported dtypes (e.g., float64).
                        # Prefer the movie's integer dtype when possible; otherwise fall back to float32.
                        kymo_to_save = kymo
                        if np.issubdtype(kymo_to_save.dtype, np.floating):
                            target_dtype = None
                            movie = getattr(self, "movie", None)
                            if movie is not None and np.issubdtype(movie.dtype, np.integer):
                                target_dtype = movie.dtype
                            else:
                                target_dtype = np.float32

                            if np.issubdtype(target_dtype, np.integer):
                                info = np.iinfo(target_dtype)
                                kymo_to_save = np.nan_to_num(
                                    kymo_to_save,
                                    nan=0.0,
                                    posinf=info.max,
                                    neginf=info.min,
                                )
                                kymo_to_save = np.clip(
                                    np.rint(kymo_to_save),
                                    info.min,
                                    info.max,
                                ).astype(target_dtype)
                            else:
                                kymo_to_save = kymo_to_save.astype(target_dtype)
                        # Compute display range (ImageJ "Ranges") for this kymograph.
                        disp_vmin = None
                        disp_vmax = None
                        if settings:
                            disp_vmin = settings.get("vmin", None)
                            disp_vmax = settings.get("vmax", None)
                        if (disp_vmin is None or disp_vmax is None) and name == current:
                            try:
                                if self.kymoCanvas._im is not None:
                                    disp_vmin, disp_vmax = self.kymoCanvas._im.get_clim()
                            except Exception:
                                pass
                        if disp_vmin is not None and disp_vmax is not None:
                            # Kymograph display uses an 8-bit preview stretched between p15 and p99.
                            # Map the preview range back into raw intensity units.
                            p15, p99 = np.percentile(kymo_to_save, (15, 99))
                            denom = p99 - p15
                            if denom == 0:
                                denom = 1
                            vmin = p15 + (float(disp_vmin) / 255.0) * denom
                            vmax = p15 + (float(disp_vmax) / 255.0) * denom
                        else:
                            p15, p99 = np.percentile(kymo_to_save, (15, 99))
                            vmin = float(p15)
                            vmax = float(p99 * 1.1)
                        if vmin is None or vmax is None:
                            vmin, vmax = 0, 0
                        if vmin >= vmax:
                            vmax = vmin + 1
                        if np.issubdtype(kymo_to_save.dtype, np.integer):
                            info = np.iinfo(kymo_to_save.dtype)
                            vmin = max(info.min, min(info.max, int(vmin)))
                            vmax = max(info.min, min(info.max, int(vmax)))
                        else:
                            vmin = float(vmin)
                            vmax = float(vmax)
                        # If a non-gray LUT is chosen, also embed a ColorMap.
                        # ImageJ expects colormap shape (3, 256) in ImageJ mode.
                        colormap = None
                        if lut_label not in ("Greys", "Greys (inv.)"):
                            colormap = (lut.astype(np.uint16) * 257)
                        mode = "grayscale" if lut_label in ("Greys", "Greys (inv.)") else "color"
                        if lut_label == "Greys (inv.)":
                            current_lut = "greys (inv.)"
                        else:
                            current_lut = lut_label.lower().replace(" (inv.)", "")
                        write_kwargs = {}
                        if lut_label == "Greys (inv.)":
                            # Ensure ImageJ reports an inverting grayscale LUT.
                            write_kwargs["photometric"] = "miniswhite"
                        metadata = {
                            "mode": mode,
                            "axes": "YX",
                            "Info": f"CurrentLUT={current_lut}\n",
                            # ImageJ expects list of per-channel ranges.
                            "Ranges": [(float(vmin), float(vmax))],
                            # Also write explicit min/max for single-channel ImageJ.
                            "min": float(vmin),
                            "max": float(vmax),
                        }
                        # For inverting grayscale, rely on Photometric=miniswhite and omit LUTs
                        # so ImageJ reports an inverting grayscale LUT.
                        if lut_label != "Greys (inv.)":
                            metadata["LUTs"] = [lut]
                        try:
                            tifffile.imwrite(
                                out_path,
                                kymo_to_save,
                                imagej=True,
                                colormap=colormap,
                                metadata=metadata,
                                **write_kwargs,
                            )
                        except Exception as e:
                            _log_kymo_save_error(
                                e,
                                stage="tifffile.imwrite(imagej)",
                                out_path=out_path,
                                extra={
                                    "shape": getattr(kymo_to_save, "shape", None),
                                    "dtype": str(getattr(kymo_to_save, "dtype", "")),
                                    "mode": mode,
                                    "lut": lut_label,
                                },
                            )
                            # Fallback: write a minimal TIFF without ImageJ metadata/LUTs.
                            try:
                                tifffile.imwrite(out_path, kymo_to_save)
                            except Exception as e2:
                                _log_kymo_save_error(
                                    e2,
                                    stage="tifffile.imwrite(fallback)",
                                    out_path=out_path,
                                    extra={
                                        "shape": getattr(kymo_to_save, "shape", None),
                                        "dtype": str(getattr(kymo_to_save, "dtype", "")),
                                    },
                                )
                                raise
                    else:
                        settings = getattr(self, "kymo_contrast_settings", {}).get(name)
                        if settings:
                            p15, p99 = np.percentile(kymo, (15, 99))
                            denom = p99 - p15
                            if denom == 0:
                                denom = 1
                            base = np.clip((kymo - p15) / denom, 0, 1) * 255.0
                            vmin = settings.get("vmin", 0)
                            vmax = settings.get("vmax", 255)
                            if vmin >= vmax:
                                vmax = vmin + 1
                            disp = np.clip((base - vmin) / (vmax - vmin), 0, 1)
                        else:
                            p15, p99 = np.percentile(kymo, (15, 99))
                            denom = p99 - p15
                            if denom == 0:
                                denom = 1
                            disp = np.clip((kymo - p15) / denom, 0, 1)
                        disp = (disp * 255).astype(np.uint8)
                        cmap = lut_cmap
                        if ft == "pdf":
                            dpi = layout_dpi
                            save_dpi = int(dpi * pdf_raster_scale)
                            fig = plt.figure(frameon=False)
                            fig.set_size_inches(disp.shape[1] / dpi, disp.shape[0] / dpi)
                            ax = fig.add_axes([0, 0, 1, 1])
                            ax.imshow(disp, cmap=cmap, origin="upper")
                            ax.set_xlim(0, disp.shape[1])
                            ax.set_ylim(disp.shape[0], 0)
                            ax.axis("off")
                            if do_scalebars:
                                SaveKymographDialog.draw_scale_bars(
                                    ax,
                                    disp.shape,
                                    origin="upper",
                                    pixel_size_nm=getattr(self, "pixel_size", None),
                                    frame_interval_ms=getattr(self, "frame_interval", None),
                                    set_outer_pad=False,
                                    dpi=layout_dpi,
                                    size_scale=SaveKymographDialog.SCALEBAR_SIZE_SCALE,
                                )
                            fig.savefig(out_path, dpi=save_dpi, facecolor="white", edgecolor="none")
                            plt.close(fig)
                        elif do_scalebars:
                            dpi = layout_dpi
                            save_dpi = dpi
                            if ft in ("png", "jpg"):
                                save_dpi = int(dpi * raster_scale)
                            fig = plt.figure(frameon=False)
                            fig.set_size_inches(disp.shape[1] / dpi, disp.shape[0] / dpi)
                            ax = fig.add_axes([0, 0, 1, 1])
                            ax.imshow(disp, cmap=cmap, origin="upper")
                            ax.set_xlim(0, disp.shape[1])
                            ax.set_ylim(disp.shape[0], 0)
                            ax.axis("off")
                            SaveKymographDialog.draw_scale_bars(
                                ax,
                                disp.shape,
                                origin="upper",
                                pixel_size_nm=getattr(self, "pixel_size", None),
                                frame_interval_ms=getattr(self, "frame_interval", None),
                                set_outer_pad=False,
                                dpi=layout_dpi,
                                size_scale=SaveKymographDialog.SCALEBAR_SIZE_SCALE,
                            )
                            fig.savefig(out_path, dpi=save_dpi, facecolor="white", edgecolor="none")
                            plt.close(fig)
                        else:
                            if ft in ("png", "jpg"):
                                scale = int(raster_scale)
                                if scale > 1:
                                    disp = np.repeat(np.repeat(disp, scale, axis=0), scale, axis=1)
                            plt.imsave(out_path, disp, cmap=cmap, origin="upper")

            prog.setValue(total)

        except Exception as e:
            log_path = _log_kymo_save_error(e, stage="save_kymographs")
            QMessageBox.critical(
                self,
                "Save Error",
                "Failed to save kymographs.\n"
                f"Details were written to:\n{log_path}\n\n"
                f"Error:\n{e}",
            )

        finally:
            prog.close()
            if prev_kymo_overlay_mode is not None:
                self.kymo_traj_overlay_mode = prev_kymo_overlay_mode
            # just re-select the original kymo; that will reset ROI, channel, contrast, overlays, etc.
            if current in self.kymographs:
                self.kymoCombo.setCurrentText(current)
                self.kymo_changed()

    #UNUSED
    def save_kymograph_with_rois(self):
        """
        Save the selected kymo as a TIFF that ImageJ will open
        with multipoint overlay drawn.
        """

        if not self.kymographs:
            QMessageBox.information(self, "", "Nothing to save.")
            return

        # 1) Ask where to save
        fname, _ = QFileDialog.getSaveFileName(
            self, "Save Kymograph with Overlays", "", "TIFF Files (*.tif *.tiff)"
        )
        if not fname:
            return

        # 2) Grab kymo & movie
        kymo_name = self.kymoCombo.currentText()
        if kymo_name not in self.kymographs:
            QMessageBox.warning(self, "", "Select a kymograph first.")
            return
        kymo = self.kymographs[kymo_name]
        width = kymo.shape[1]
        if self.movie is None:
            QMessageBox.warning(self, "", "Load a movie first.")
            return
        nframes = self.movie.shape[0]

        # 3) Resolve ROI
        roi_key = (
            self.roiCombo.currentText()
            if self.roiCombo.count() > 0
            else kymo_name
        )
        if roi_key not in self.rois:
            QMessageBox.warning(self, "", f"ROI '{roi_key}' not found.")
            return
        roi = self.rois[roi_key]

        # 4) Build overlay points
        pts = []
        for traj in self.trajectoryCanvas.trajectories:
            frames = traj.get("frames", [])
            coords = traj.get('original_coords', [])
            if not frames or not coords:
                continue
            # start
            f0, (x0, y0) = frames[0], coords[0]
            kx0 = self.compute_kymo_x_from_roi(roi, x0, y0, width)
            ky0 = int(round(f0))
            # end
            fn, (xn, yn) = frames[-1], coords[-1]
            kxn = self.compute_kymo_x_from_roi(roi, xn, yn, width)
            kyn = int(round(fn))
            pts.extend([(kx0, ky0), (kxn, kyn)])
        if not pts:
            QMessageBox.information(self, "No Trajectories", "Nothing to save.")
            return

        # 5) Build the ROI blob
        blob = generate_multipoint_roi_bytes(pts)
        print(f"DEBUG: writing ROI blob length {len(blob)} bytes")

        # 6) Build the ImageJ ImageDescription text
        imgdesc = "\n".join([
            "ImageJ=1.53a",
            "images=1",
            "channels=1",
            "slices=1",
            "frames=1",
            "hyperstack=true",
            "overlays=1",
        ]) + "\n"
        desc_bytes = imgdesc.encode('ascii')

        # 7) Write both tags explicitly
        extratags = [
            # tag 270 = ImageDescription
            (270, 's', len(desc_bytes), desc_bytes, True),
            # tag 50838 = ROI
            (50838, 'B', len(blob), blob, True),
        ]
        tifffile.imwrite(
            fname,
            kymo,
            imagej=True,
            metadata={'ROI': blob},  # ← use 'ROI' exactly as ImageJ does
            bigtiff=False
        )

        QMessageBox.information(
            self, "Saved",
            f"Wrote {fname} with {len(pts)//2} trajectories ({len(pts)} points)."
        )

    def show_channel_axis_dialog(self):
        # Check if a movie is loaded and if it is 4-D (with channel options)
        if self.movie is None or self.movie.ndim != 4:
            QMessageBox.information(self, "No extra axes", 
                                    "There are no axes to choose from")
            return

        # Build a list of available axis options (for example, all axes except the time axis)
        # Here we assume axis 0 is time so valid axes are 1, 2, ... movie.ndim-1.
        available_axes = list(range(1, self.movie.ndim))
        dialog = ChannelAxisDialog(available_axes, self)
        if dialog.exec_() == QDialog.Accepted:
            selected_axis = dialog.selected_axis()
            # Set channel axis.
            self._channel_axis = selected_axis
            # Update the channel combo box.
            self.update_movie_channel_combo()

    def set_scale(self):
        # Open the Set Scale dialog prefilled with the current pixel_size and frame_interval values (if any)
        dialog = SetScaleDialog(self.pixel_size, self.frame_interval, parent=self)
        if dialog.exec_() == QDialog.Accepted:
            pixel_size, frame_interval = dialog.get_values()
            if (pixel_size is None) != (frame_interval is None):
                QMessageBox.warning(
                    self,
                    "Incomplete scale",
                    "Both pixel size and frame time are required. Please set both values.",
                )
                return
            self.pixel_size = pixel_size  # in nm
            self.frame_interval = frame_interval  # in ms
            self.update_scale_label()
            self.flash_message("Scale set")
            # Optionally, update any UI elements or print to console:
            #print(f"Set pixel size to {self.pixel_size} nm and frame interval to {self.frame_interval} ms")
            
            # Update velocity information in all trajectories.
            tc = self.trajectoryCanvas  # shortcut to the trajectory canvas
            for row in range(tc.table_widget.rowCount()):
                traj = tc.trajectories[row]
                # Calculate velocities (in pixels per frame)
                velocities = calculate_velocities(traj["spot_centers"])
                valid_velocities = [v for v in velocities if v is not None]
                if valid_velocities:
                    average_velocity = np.mean(valid_velocities)
                else:
                    average_velocity = None
                # Convert average_velocity from pixels/frame to micro meters per second and per minute.
                if self.pixel_size is not None and self.frame_interval is not None and average_velocity is not None:
                    # Here, pixel_size is in nm and frame_interval in ms; the conversion to um/s is:
                    # (average_velocity (px/frame) * pixel_size (nm/px)) / (frame_interval (ms)) 
                    # and then convert nm/ms to um/s by dividing by 1000.
                    velocity_nm_per_ms = (average_velocity * self.pixel_size) / self.frame_interval
                    avg_vel_um_s_txt = f"{velocity_nm_per_ms:.2f}"
                    avg_vel_um_min_txt = f"{velocity_nm_per_ms*60.0:.2f}"
                else:
                    avg_vel_um_s_txt = ""
                    avg_vel_um_min_txt = ""

                dx = traj["end"][1] - traj["start"][1]
                dy = traj["end"][2] - traj["start"][2]
                distance_px = np.hypot(dx, dy)
                time_fr = traj["end"][0] - traj["start"][0]
                distance_um_txt = ""
                time_s_txt = ""
                overall_vel_um_s_txt = ""
                if self.pixel_size is not None and self.frame_interval is not None and time_fr > 0:
                    distance_um = distance_px * self.pixel_size / 1000
                    time_s = time_fr * self.frame_interval / 1000
                    overall_vel_um_s = distance_um/time_s
                    distance_um_txt = f"{distance_um:.2f}"
                    time_s_txt = f"{time_s:.2f}"
                    overall_vel_um_s_txt = f"{overall_vel_um_s:.2f}"

                tc.writeToTable(row, "distance", distance_um_txt)
                tc.writeToTable(row, "time", time_s_txt)                
                tc.writeToTable(row, "netspeed", overall_vel_um_s_txt)
            
            # Update the displayed velocity plot (for the currently selected trajectory)
            selected_rows = tc.table_widget.selectionModel().selectedRows()
            if selected_rows:
                current_row = selected_rows[0].row()
            elif tc.table_widget.rowCount() > 0:
                current_row = 0
                tc.table_widget.selectRow(current_row)
            else:
                current_row = None
            if current_row is not None:
                current_traj = tc.trajectories[current_row]
                self.velocityCanvas.plot_velocity_histogram(current_traj["velocities"])

        self.trajectoryCanvas.hide_empty_columns()

    def correct_drift(self):
        """
        Corrects the drift in the currently loaded movie using spot tracking.

        For multi–channel movies the analysis is performed on the currently selected
        channel but the correction is applied to the full frame so that the original
        movie shape (including channel axis) is preserved. New areas are padded with black.

        The tracking uses the spot center from one frame as the search center for the next.
        If no spot is found, the same displacement as in the previous frame is applied.
        At the end, any gaps are filled in via linear interpolation.
        """
        if self.movie is None:
            QMessageBox.warning(self, "", "Please load a movie first.")
            return

        if not hasattr(self, "drift_reference") or self.drift_reference is None:
            QMessageBox.warning(self, "",
                                "Please click a stationary spot that can be found in all frames first.")
            return

        reply = QMessageBox.question(
            self,
            "Confirm Drift Tracking Spot",
            "Is the currently selected spot suitable for drift tracking?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel
        )
        if reply != QMessageBox.Yes:
            return

        ref_spot = self.drift_reference  # (x, y)
        n_frames = self.movie.shape[0]
        multi_channel = (self.movie.ndim == 4)

        # Initialize a list to hold the tracked spot centers.
        spot_centers = [None] * n_frames
        spot_centers[self.spot_frame] = ref_spot

        crop_size = int(2 * self.searchWindowSpin.value())

        def get_analysis_frame(full_frame):
            if not multi_channel:
                return full_frame
            current_chan = int(self.movieChannelCombo.currentText()) - 1
            if self._channel_axis == 1:  # channels-first: (channels, H, W)
                return full_frame[current_chan]
            else:  # channels-last: (H, W, channels)
                return full_frame[..., current_chan]

        # --- Create a single progress dialog for both tracking and shifting ---
        total_tracking = (n_frames - self.spot_frame - 1) + self.spot_frame
        total_shifts = n_frames
        total_steps = total_tracking + total_shifts

        progress = QProgressDialog("Tracking spot and applying shift...", "Cancel", 0, total_steps, self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        current_progress = 0

        # --- Forward Tracking ---
        current_spot = ref_spot
        last_disp = (0, 0)
        for i in range(self.spot_frame + 1, n_frames):
            if progress.wasCanceled():
                return
            full_frame = self.movie[i] if multi_channel else self.get_movie_frame(i)
            if full_frame is None:
                spot_centers[i] = current_spot
                current_progress += 1
                progress.setValue(current_progress)
                continue

            analysis_frame = get_analysis_frame(full_frame)
            analysis_frame = np.atleast_2d(analysis_frame)
            if analysis_frame.ndim != 2:
                spot_centers[i] = current_spot
                current_progress += 1
                progress.setValue(current_progress)
                continue

            try:
                fitted_center, sigma, intensity, peak, bkgr = perform_gaussian_fit(analysis_frame, current_spot, crop_size, pixelsize = self.pixel_size)
            except Exception as e:
                print(f"Forward Gaussian fit error at frame {i}: {e}")
                fitted_center = None

            if fitted_center is not None:
                new_spot = fitted_center
                last_disp = (new_spot[0] - current_spot[0], new_spot[1] - current_spot[1])
            else:
                new_spot = (current_spot[0] + last_disp[0], current_spot[1] + last_disp[1])
            spot_centers[i] = new_spot
            current_spot = new_spot
            current_progress += 1
            progress.setValue(current_progress)

        # --- Backward Tracking ---
        current_spot = ref_spot
        last_disp = (0, 0)
        for i in range(self.spot_frame - 1, -1, -1):
            if progress.wasCanceled():
                return
            full_frame = self.movie[i] if multi_channel else self.get_movie_frame(i)
            if full_frame is None:
                spot_centers[i] = current_spot
                current_progress += 1
                progress.setValue(current_progress)
                continue

            analysis_frame = get_analysis_frame(full_frame)
            analysis_frame = np.atleast_2d(analysis_frame)
            if analysis_frame.ndim != 2:
                spot_centers[i] = current_spot
                current_progress += 1
                progress.setValue(current_progress)
                continue

            try:
                fitted_center, sigma, intensity, peak, bkgr = perform_gaussian_fit(analysis_frame, current_spot, crop_size, pixelsize = self.pixel_size)
            except Exception as e:
                print(f"Backward Gaussian fit error at frame {i}: {e}")
                fitted_center = None

            if fitted_center is not None:
                new_spot = fitted_center
                last_disp = (new_spot[0] - current_spot[0], new_spot[1] - current_spot[1])
            else:
                new_spot = (current_spot[0] + last_disp[0], current_spot[1] + last_disp[1])
            spot_centers[i] = new_spot
            current_spot = new_spot
            current_progress += 1
            progress.setValue(current_progress)
        # End of tracking phase.

        # --- Fill in Gaps with Linear Interpolation ---
        for i in range(n_frames):
            if spot_centers[i] is None:
                prev = i - 1
                while prev >= 0 and spot_centers[prev] is None:
                    prev -= 1
                nxt = i + 1
                while nxt < n_frames and spot_centers[nxt] is None:
                    nxt += 1
                if prev >= 0 and nxt < n_frames:
                    t = (i - prev) / (nxt - prev)
                    x_interp = spot_centers[prev][0] + t * (spot_centers[nxt][0] - spot_centers[prev][0])
                    y_interp = spot_centers[prev][1] + t * (spot_centers[nxt][1] - spot_centers[prev][1])
                    spot_centers[i] = (x_interp, y_interp)
                elif prev >= 0:
                    spot_centers[i] = spot_centers[prev]
                elif nxt < n_frames:
                    spot_centers[i] = spot_centers[nxt]
                else:
                    spot_centers[i] = ref_spot

        displacements = [(sc[0] - ref_spot[0], sc[1] - ref_spot[1]) for sc in spot_centers]

        # --- Apply Correction (Shifting) ---
        corrected_frames = [None] * n_frames

        for i in range(n_frames):
            if progress.wasCanceled():
                return
            full_frame = self.movie[i] if multi_channel else self.get_movie_frame(i)
            if full_frame is None:
                corrected_frames[i] = None
                current_progress += 1
                progress.setValue(current_progress)
                continue

            if full_frame.ndim == 2:
                shift_vector = [-displacements[i][1], -displacements[i][0]]
            elif full_frame.ndim == 3:
                if multi_channel:
                    if self._channel_axis == 1:  # channels-first: (channels, H, W)
                        shift_vector = [0, -displacements[i][1], -displacements[i][0]]
                    else:  # channels-last: (H, W, channels)
                        shift_vector = [-displacements[i][1], -displacements[i][0], 0]
                else:
                    shift_vector = [-displacements[i][1], -displacements[i][0]]
            else:
                QMessageBox.critical(self, "Drift Correction Error",
                                    "Unexpected movie dimensions; cannot apply drift correction.")
                progress.close()
                return

            try:
                # Use order=0 (nearest neighbor) so that pixel values are unchanged.
                corrected_frames[i] = shift(full_frame, shift=shift_vector, order=0, mode='constant', cval=0)
            except Exception as e:
                QMessageBox.critical(self, "Drift Correction Error",
                                    f"Error applying shift on frame {i}:\n{e}")
                corrected_frames[i] = full_frame
            current_progress += 1
            progress.setValue(current_progress)
        progress.close()

        # --- Display the Corrected Movie in a Popup Dialog ---
        dialog = QDialog(self)
        dialog.setWindowTitle("Drift-Corrected Movie")
        dialog_layout = QVBoxLayout(dialog)

        # Create a new MovieCanvas for the dialog:
        corrected_canvas = MovieCanvas(dialog, navigator=self)

        # First, determine the contrast settings for the current channel and mode:
        if self.movie.ndim == 4:
            try:
                current_channel = int(self.movieChannelCombo.currentText())
            except Exception:
                current_channel = 1
            settings = self.channel_contrast_settings.get(current_channel)
        else:
            settings = self.channel_contrast_settings.get(1)
        
        # If settings exist, assign them to corrected_canvas:
        if settings is not None:
            corrected_canvas._default_vmin = settings['vmin']
            corrected_canvas._default_vmax = settings['vmax']
            corrected_canvas._vmin = settings['vmin']
            corrected_canvas._vmax = settings['vmax']

        # Add the corrected_canvas to the dialog.
        dialog_layout.addWidget(corrected_canvas)

        # Create a slider for frame navigation.
        slider = QSlider(Qt.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(len(corrected_frames) - 1)
        dialog_layout.addWidget(slider)

        # Create a channel dropdown if the movie has multiple channels.
        channel_dropdown = None
        if self.movie.ndim == 4:
            channel_dropdown = QComboBox()
            n_channels = self.movie.shape[self._channel_axis]
            for ch in range(n_channels):
                channel_dropdown.addItem(f"Channel {ch+1}")
            # Set the initial channel to match the main GUI.
            channel_dropdown.setCurrentIndex(int(self.movieChannelCombo.currentText()) - 1)
            dialog_layout.addWidget(channel_dropdown)

        # Define a function to update the displayed frame:
        def update_frame(val):
            frame = corrected_frames[val]
            if frame is None:
                return
            if self.movie.ndim == 4 and channel_dropdown is not None:
                ch_index = channel_dropdown.currentIndex()
                if self._channel_axis == 1:
                    display_frame = frame[ch_index]
                else:
                    display_frame = frame[..., ch_index]
            else:
                display_frame = frame

            corrected_canvas.update_image_data(display_frame)

        slider.valueChanged.connect(update_frame)

        # 2) Now define the channel‐change callback, using the same 1‑based keys
        def on_channel_dropdown(ch0):
            # ch0 is zero-based; settings dict uses 1-based keys.
            chan_key = ch0 + 1

            # ensure we have defaults for this channel
            # get a slice of the first corrected frame
            first_frame = corrected_frames[0]
            if self.movie.ndim == 4:
                if self._channel_axis == 1:
                    sample = first_frame[ch0]
                else:
                    sample = first_frame[..., ch0]
            else:
                sample = first_frame

            if chan_key not in self.channel_contrast_settings and sample is not None:
                p15, p99 = np.percentile(sample, (15, 99))
                vmin = int(p15)
                vmax = int(p99 * 1.1)
                d = vmax - vmin
                self.channel_contrast_settings[chan_key] = {
                    'vmin': vmin,
                    'vmax': vmax,
                    'extended_min': vmin - int(0.7*d),
                    'extended_max': vmax + int(1.4*d)
                }
            settings = self.channel_contrast_settings[chan_key]

            corrected_canvas._default_vmin = settings["vmin"]
            corrected_canvas._default_vmax = settings["vmax"]
            corrected_canvas._vmin = settings["vmin"]
            corrected_canvas._vmax = settings["vmax"]

            # finally, repaint current slider frame with new contrast
            update_frame(slider.value())

        if channel_dropdown is not None:
            channel_dropdown.currentIndexChanged.connect(on_channel_dropdown)

        # 3) Kick it off once at startup
        update_frame(0)

        # Add dialog buttons.
        btn_layout = QHBoxLayout()
        btn_save = QPushButton("Save Movie")
        btn_save_load = QPushButton("Save and Load Movie")
        btn_cancel = QPushButton("Cancel")
        btn_layout.addWidget(btn_save)
        btn_layout.addWidget(btn_save_load)
        btn_layout.addWidget(btn_cancel)
        dialog_layout.addLayout(btn_layout)

        saved_file = {"path": None}

        def save_movie():
            # static file-picker; parent is 'dialog'
            fname, _ = QFileDialog.getSaveFileName(
                dialog,
                "Save Drift-Corrected Movie",
                "",
                "TIFF Files (*.tif *.tiff)"
            )
            if not fname:
                # user clicked Cancel in the file-chooser → do nothing
                return False
            try:
                # actually write it out
                tifffile.imwrite(
                    fname,
                    np.array(corrected_frames),
                    #bigtiff=True, NEEDS TESTING
                    imagej=True,
                    metadata=getattr(self, "movie_metadata", {})
                )
                saved_file["path"] = fname
                return True
            except Exception as e:
                QMessageBox.critical(dialog, "Save Error", f"Error saving movie:\n{e}")
                return False

        def save_and_load_movie():
            # bail out if the user canceled (or if save_movie hit an error)
            if not save_movie():
                return

            # at this point, saved_file["path"] must be set
            try:
                self.save_and_load_routine = True
                self.handle_movie_load(
                    saved_file["path"],
                    pixelsize=self.pixel_size,
                    frameinterval=self.frame_interval
                )
                QMessageBox.information(
                    dialog, "Loaded",
                    "The corrected movie has been loaded into the main window."
                )
                self.zoomInsetFrame.setVisible(False)
            except Exception as e:
                QMessageBox.critical(dialog, "Load Error", f"Error loading movie:\n{e}")
                return

            # only now close the corrected-movie popup
            dialog.accept()

        def cancel():
            dialog.accept()

        def on_save_clicked():
            if save_movie():
                # only close the corrected‐movie popup if we actually saved
                dialog.accept()

        btn_save.clicked.connect(on_save_clicked)
        btn_save_load.clicked.connect(save_and_load_movie)
        btn_cancel.clicked.connect(dialog.reject)

        dialog.exec_()
