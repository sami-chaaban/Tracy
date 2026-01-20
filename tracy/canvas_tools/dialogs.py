from ._shared import *

class ChannelAxisDialog(QDialog):
    def __init__(self, axis_options, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Channel Axis")
        layout = QVBoxLayout(self)
        self.combo = QComboBox(self)
        # Populate the combo with the available axis options.
        for ax in axis_options:
            self.combo.addItem(str(ax))
        layout.addWidget(self.combo)
        buttonBox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        layout.addWidget(buttonBox)
        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)

    def selected_axis(self):
        return int(self.combo.currentText())


class SetScaleDialog(QDialog):
    def __init__(self, current_pixel_size, current_frame_interval, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set Scale")
        layout = QVBoxLayout(self)

        # bold_font = QFont()
        # bold_font.setBold(True)

        # Create a label and line edit for Pixel Size (nm)
        self.pixelLabel = QLabel("Pixel size (nm):")
        # self.pixelLabel.setFont(bold_font)
        self.pixelLabel.setAlignment(Qt.AlignCenter)
        self.pixelEdit = QLineEdit()
        self.pixelEdit.setValidator(QDoubleValidator(0.001, 1_000_000, 2, self))
        self.pixelEdit.setStyleSheet("background-color: white;")
        if current_pixel_size is not None:
            # **always** convert to float, then format with two decimals
            try:
                val = float(current_pixel_size)
                self.pixelEdit.setText(f"{val:.2f}")
            except ValueError:
                # fallback if it wasn’t a valid float
                self.pixelEdit.setText("0.00")
        layout.addWidget(self.pixelLabel)
        layout.addWidget(self.pixelEdit)

        # Create a label and line edit for Frame Interval (ms)
        self.frameLabel = QLabel("Frame interval (ms):")
        # self.frameLabel.setFont(bold_font)
        self.frameLabel.setAlignment(Qt.AlignCenter)  
        self.frameEdit = QLineEdit()
        self.frameEdit.setValidator(QDoubleValidator(0.001, 1_000_000, 2, self))
        self.frameEdit.setStyleSheet("background-color: white;")
        if current_frame_interval is not None:
            try:
                val = float(current_frame_interval)
                self.frameEdit.setText(f"{val:.2f}")
            except ValueError:
                self.frameEdit.setText("0.00")
        layout.addWidget(self.frameLabel)
        layout.addWidget(self.frameEdit)

        # Create OK and Cancel buttons
        self.buttonBox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(self.buttonBox)

        # Initially disable OK if either field is empty
        self.buttonBox.button(QDialogButtonBox.Ok).setEnabled(self.inputs_valid())

        # Connect signals to check inputs and accept/reject the dialog.
        self.pixelEdit.textChanged.connect(self.check_inputs)
        self.frameEdit.textChanged.connect(self.check_inputs)
        self.buttonBox.accepted.connect(self.accept)
        self.buttonBox.rejected.connect(self.reject)

        parent.update_scale_label()

    def inputs_valid(self):
        # Make sure both fields are non-empty and the validators accept the input.
        return bool(self.pixelEdit.text().strip()) and bool(self.frameEdit.text().strip())

    def check_inputs(self):
        self.buttonBox.button(QDialogButtonBox.Ok).setEnabled(self.inputs_valid())

    def get_values(self):
        # Return the entered values as floats.
        return float(self.pixelEdit.text()), float(self.frameEdit.text())
    
class KymoLineOptionsDialog(QDialog):
    def __init__(self, current_line_width, current_method, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Line Options")
        
        layout = QFormLayout(self)
        
        # Spin box for line width.
        self.lineWidthSpin = QSpinBox(self)
        self.lineWidthSpin.setRange(1, 10)
        self.lineWidthSpin.setValue(current_line_width)
        layout.addRow("Line width (pixels):", self.lineWidthSpin)
        
        # Combo box to choose integration method.
        self.methodCombo = QComboBox(self)
        self.methodCombo.addItems(["Max", "Average"])
        # Set current selection based on current_method.
        if current_method.lower() == "average":
            self.methodCombo.setCurrentText("Average")
        else:
            self.methodCombo.setCurrentText("Max")
        layout.addRow("Integration method:", self.methodCombo)
        
        # OK and Cancel buttons.
        buttonBox = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttonBox.accepted.connect(self.accept)
        buttonBox.rejected.connect(self.reject)
        layout.addWidget(buttonBox)
        
    def getValues(self):
        # Return the values: line width (int) and method (as lower-case string).
        return self.lineWidthSpin.value(), self.methodCombo.currentText().lower()

class RadiusDialog(QDialog):
    def __init__(self, current_radius, parent=None):
        # Popup + frameless so it grabs focus but has no titlebar
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)

        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_StyledBackground, True)

        self.setStyleSheet("""
            QDialog {
                background-color: transparent;
            }
            QLabel {
                background-color: white;
                border-radius: 12px;
                padding: 8px;
                font-size: 14px;
                border: 1px solid #ccc;
            }
            QSpinBox {
                background-color: white;
                border: 1px solid #ccc;
                border-radius: 12px;
                padding: 4px 0px 4px 16px;
                font-size: 14px;
                min-height: 26px;
            }

            QSpinBox QLineEdit {
                background: transparent;
                border: none;
                padding: 0;
                text-align: center;
            }
        """)

        self.setAttribute(Qt.WA_ShowWithoutActivating)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(8,4,8,4)
        radiuslabel = QLabel("Search Radius")
        # radiuslabel.setStyleSheet("font-weight:bold")
        lay.addWidget(radiuslabel)
        spin = QSpinBox(self)
        spin.setRange(8, 50)
        spin.setValue(current_radius)
        spin.setAlignment(Qt.AlignCenter)
        spin.lineEdit().setAlignment(Qt.AlignCenter)
        lay.addWidget(spin)
        self._spin = spin
        spin.setFocus()

    def showEvent(self, event):
        super().showEvent(event)
        # immediately clear focus so no cursor/frame is drawn
        self._spin.clearFocus()

    def wheelEvent(self, event):
        # forward any wheel to the spin‑box
        self._spin.wheelEvent(event)
        le = self._spin.lineEdit()
        le.deselect()
        self._spin.clearFocus()
        event.accept()

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            return
        if event.key() == Qt.Key_R:
            # write value back to the main window
            val = self._spin.value()
            # assume parent has attribute searchWindowSpin
            self.parent().searchWindowSpin.setValue(val)
            self.close()
        else:
            super().keyReleaseEvent(event)

    def closeEvent(self, ev):
        # tell the parent that we’re gone
        p = self.parent()
        p._radiusPopup = None
        p._radiusSpinLive = None
        super().closeEvent(ev)

class SaveKymographDialog(QDialog):
    # Class‐level storage of the last settings
    _last_use_prefix = False
    _last_middle    = ""
    _last_custom    = ""

    def __init__(self, movie_name, kymo_items, parent=None):
        super().__init__(parent)

        self.setStyleSheet("""
            QDialog {
                border-radius: 10px;
            }
            QLabel, QCheckBox {
                background-color: transparent;
                border-radius: 8px;
                padding: 4px;
                font-size: 14px;
            }
            QLineEdit {
                border: 1px solid #AAB4D4;
                border-radius: 6px;
                padding: 4px 6px;
                background-color: white;
            }

            QSpinBox {
                min-width: 15px;
            }
        """)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self.setWindowTitle("Save Kymographs")
        self.movie_name = movie_name
        self.kymo_list  = [n for n, _ in kymo_items]
        self.selected   = []

        if parent and hasattr(parent, "_last_dir"):
            self.directory = parent._last_dir
        else:
            self.directory = os.getcwd()
        self.dir_le = QLineEdit(self.directory)
 
        self._all_formats = ["tif","png","jpg"]

        main_v = QVBoxLayout(self)

        # ── “All” checkbox ─────────────────────────────
        self.all_cb = QCheckBox("All")
        self.all_cb.toggled.connect(self._on_all_toggled)
        main_v.addWidget(self.all_cb, alignment=Qt.AlignHCenter)

        # ── Kymo list ──────────────────────────────────
        self.list_w = QListWidget()
        self.list_w.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for name in self.kymo_list:
            it = QListWidgetItem(name)
            self.list_w.addItem(it)
        self.list_w.selectAll()
        self.list_w.itemSelectionChanged.connect(self._on_list_change)
        list_layout = QHBoxLayout()
        list_layout.setContentsMargins(20, 0, 20, 0)  # left, top, right, bottom
        list_layout.addWidget(self.list_w)
        main_v.addLayout(list_layout)
        
        # ── Directory chooser ─────────────────────────
        dir_h = QHBoxLayout()
        dir_h.addStretch()
        dir_h.addWidget(QLabel("Directory:"))
        self.dir_le = QLineEdit(self.directory)
        self.dir_le.setMinimumWidth(400)
        dir_h.addWidget(self.dir_le)
        browse = QPushButton("…")
        browse.clicked.connect(self._browse_dir)
        browse.setAutoDefault(False)
        dir_h.addWidget(browse)
        dir_h.addStretch()
        main_v.addLayout(dir_h)

        # ── File‑type dropdown ────────────────────────
        ft_h = QHBoxLayout()
        ft_h.addStretch()
        ft_h.addWidget(QLabel("File type:"))
        self.ft_combo = QComboBox()
        for ext in self._all_formats:
            self.ft_combo.addItem(ext)
        self.ft_combo.currentTextChanged.connect(self._on_filetype_changed)
        self.ft_combo.currentTextChanged.connect(self._update_preview)
        ft_h.addWidget(self.ft_combo)
        ft_h.addStretch()
        main_v.addLayout(ft_h)

        # ── Overlay checkbox ──────────────────────────
        self.overlay_cb = QCheckBox("Overlay trajectories")
        self.overlay_cb.toggled.connect(self._on_overlay_toggled)
        main_v.addWidget(self.overlay_cb, alignment=Qt.AlignHCenter)

        # ── Naming controls stack ──────────────────────
        self.controls_stack = QStackedWidget()
        self.controls_stack.setAttribute(Qt.WA_StyledBackground, True)
        self.controls_stack.setStyleSheet("background-color: transparent;")
        main_v.addWidget(self.controls_stack)

        # Page0: multi‑select naming
        page_multi = QWidget()
        # page_multi.setAttribute(Qt.WA_StyledBackground, True)
        # page_multi.setStyleSheet("background-color: transparent;")
        vbox_multi = QVBoxLayout(page_multi)

        # 1) Centered “Use movie name as prefix”
        self.prefix_cb = QCheckBox("Use movie name as prefix", parent=page_multi)
        self.prefix_cb.toggled.connect(self._update_preview)
        # restore last state
        self.prefix_cb.setChecked(self.__class__._last_use_prefix)
        h_ctr = QHBoxLayout()
        h_ctr.addStretch()
        h_ctr.addWidget(self.prefix_cb)
        h_ctr.addStretch()
        vbox_multi.addLayout(h_ctr)

        # 2) Kymograph middle prefix
        fm_multi = QFormLayout()
        fm_multi.setLabelAlignment(Qt.AlignRight)
        self.middle_le = QLineEdit(parent=page_multi)
        self.middle_le.setStyleSheet("""
            background-color: white;
            border: 1px solid #AAB4D4;
            border-radius: 6px;
            padding: 4px 6px;
        """)
        self.middle_le.textChanged.connect(self._update_preview)
        # restore last text
        self.middle_le.setText(self.__class__._last_middle)
        fm_multi.addRow(QLabel("Kymograph prefix:"), self.middle_le)
        vbox_multi.addLayout(fm_multi)

        self.controls_stack.addWidget(page_multi)

        # Page1: single‑select naming
        page_single = QWidget()
        # page_single.setAttribute(Qt.WA_StyledBackground, True)
        # page_single.setStyleSheet("background-color: transparent;")
        fm_single = QFormLayout(page_single)
        fm_single.setLabelAlignment(Qt.AlignRight)
        self.custom_le = QLineEdit(parent=page_single)
        self.custom_le.setStyleSheet("""
            background-color: white;
            border: 1px solid #AAB4D4;
            border-radius: 6px;
            padding: 4px 6px;
        """)
        self.custom_le.setMinimumWidth(400)
        self.custom_le.setAlignment(Qt.AlignHCenter)
        # restore last custom name
        self.custom_le.setText(self.__class__._last_custom)
        fm_single.addRow(QLabel("Filename:"), self.custom_le)
        self.controls_stack.addWidget(page_single)

        # ── Preview label ──────────────────────────────
        self.preview = QLabel("", alignment=Qt.AlignCenter)
        main_v.addWidget(self.preview)

        # ── Save/Cancel ────────────────────────────────
        btn_h = QHBoxLayout()
        btn_h.addStretch()
        ok = QPushButton("Save")
        ok.clicked.connect(self.accept)
        ok.setDefault(True)
        ok.setAutoDefault(True)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        cancel.setAutoDefault(False)
        btn_h.addWidget(cancel)
        btn_h.addWidget(ok)
        btn_h.addStretch()
        main_v.addLayout(btn_h)

        # ── Initial sync ───────────────────────────────
        self._on_filetype_changed(self.ft_combo.currentText())
        self._on_list_change()


    def getOptions(self):
        # Build options dict.
        opts = {
            "directory":  self.directory,
            "selected":   self.selected,
            "overlay":    self.overlay_cb.isChecked(),
            "filetype":   self.ft_combo.currentText(),
        }
        if len(self.selected) > 1:
            opts.update({
                "use_prefix":  self.prefix_cb.isChecked(),
                "middle":      self.middle_le.text().strip(),
                "custom":      False,
                "custom_name": ""
            })
        else:
            opts.update({
                "use_prefix":  False,
                "middle":      "",
                "custom":      True,
                "custom_name": self.custom_le.text().strip()
            })

        # Store for next time
        SaveKymographDialog._last_use_prefix = opts["use_prefix"]
        SaveKymographDialog._last_middle    = opts["middle"]
        SaveKymographDialog._last_custom    = opts["custom_name"]

        return opts

    def _on_all_toggled(self, checked):
        for i in range(self.list_w.count()):
            self.list_w.item(i).setSelected(checked)
        # signal fires _on_list_change automatically


    def _on_list_change(self):
        self.selected = [it.text() for it in self.list_w.selectedItems()]

        self.all_cb.blockSignals(True)
        self.all_cb.setChecked(len(self.selected) == len(self.kymo_list))
        self.all_cb.blockSignals(False)

        if len(self.selected) > 1:
            # ── multi‑select page ───────────────────────
            self.controls_stack.setCurrentIndex(0)
            self.preview.show()
            self._update_preview()

        else:
            # ── single‑select page ──────────────────────
            self.controls_stack.setCurrentIndex(1)

            # ---------- default filename ----------
            if self.selected:               # there is exactly one item
                default_name = f"{self.movie_name}-{self.selected[0]}"
                self.custom_le.setText(default_name)
            # -------------------------------------------

            self.preview.hide()

    def _browse_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Select Directory", self.directory)
        if d:
            self.directory = d
            self.dir_le.setText(d)
            # write it back to the navigator
            parent = self.parent()
            if parent and hasattr(parent, "_last_dir"):
                parent._last_dir = d
                
    def _update_preview(self):
        # only update in multi‑select mode
        if self.controls_stack.currentIndex() != 0:
            return

        parts = []
        if self.prefix_cb.isChecked():
            parts.append(self.movie_name)
        mid = self.middle_le.text().strip()
        if mid:
            parts.append(mid)
        parts.append(self.selected[0] if self.selected else "")
        ext = self.ft_combo.currentText()
        self.preview.setText(f"Example: {'-'.join(parts)}.{ext}")

    def _on_overlay_toggled(self, checked):
        self._update_preview()

    def _on_filetype_changed(self, ext: str):
        """
        - If TIFF is selected, disable and uncheck the overlay option.
        - For PNG/JPG, enable the overlay checkbox.
        """
        is_tif = ext.lower() == "tif"

        if is_tif:
            self.overlay_cb.blockSignals(True)
            self.overlay_cb.setChecked(False)
            self.overlay_cb.setEnabled(False)
            self.overlay_cb.blockSignals(False)
        else:
            self.overlay_cb.setEnabled(True)

        self._update_preview()


class StepSettingsDialog(QDialog):
    def __init__(self, current_W, current_min_step, can_calculate_all: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Step-finding parameters")
        self.new_W = current_W
        self.new_min_step = current_min_step
        self.calculate_all = False

        layout = QVBoxLayout(self)
        self.setStyleSheet(QApplication.instance().styleSheet())

        # Rolling average window
        win_layout = QHBoxLayout()
        win_label = QLabel("Rolling average window:")
        win_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.win_spin = QSpinBox()
        self.win_spin.setRange(1, 999)
        self.win_spin.setValue(current_W)
        win_layout.addWidget(win_label)
        win_layout.addWidget(self.win_spin)
        layout.addLayout(win_layout)

        # Minimum step threshold
        step_layout = QHBoxLayout()
        step_label = QLabel("Minimum step size:")
        step_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.step_spin = QSpinBox()
        self.step_spin.setRange(0, 999999)
        self.step_spin.setValue(current_min_step)
        step_layout.addWidget(step_label)
        step_layout.addWidget(self.step_spin)
        layout.addLayout(step_layout)

        # bottom buttons
        btns = QHBoxLayout()
        btns.addWidget(QPushButton("Cancel", clicked=self.reject))
        btn_set = QPushButton("Set", clicked=self._on_set)
        btn_set.setDefault(True)
        btns.addWidget(btn_set)

        layout.addLayout(btns)

    def _on_set(self):
        self.new_W        = self.win_spin.value()
        self.new_min_step = self.step_spin.value()
        self.calculate_all = False
        self.accept()

class DiffusionSettingsDialog(QDialog):
    def __init__(self, current_max_lag, current_min_pairs, can_calculate_all: bool, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Diffusion fit parameters")
        self.new_max_lag = current_max_lag
        self.new_min_pairs = current_min_pairs
        self.calculate_all = False

        layout = QVBoxLayout(self)
        self.setStyleSheet(QApplication.instance().styleSheet())

        lag_layout = QHBoxLayout()
        lag_label = QLabel("Max lag (frames):")
        lag_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.lag_spin = QSpinBox()
        self.lag_spin.setRange(2, 999)
        self.lag_spin.setValue(current_max_lag)
        lag_layout.addWidget(lag_label)
        lag_layout.addWidget(self.lag_spin)
        layout.addLayout(lag_layout)

        pair_layout = QHBoxLayout()
        pair_label = QLabel("Min pairs per lag:")
        pair_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pair_spin = QSpinBox()
        self.pair_spin.setRange(1, 9999)
        self.pair_spin.setValue(current_min_pairs)
        pair_layout.addWidget(pair_label)
        pair_layout.addWidget(self.pair_spin)
        layout.addLayout(pair_layout)

        btns = QHBoxLayout()
        btns.addWidget(QPushButton("Cancel", clicked=self.reject))
        btn_set = QPushButton("Set", clicked=self._on_set)
        btn_set.setDefault(True)
        btns.addWidget(btn_set)

        layout.addLayout(btns)

    def _on_set(self):
        self.new_max_lag = self.lag_spin.value()
        self.new_min_pairs = self.pair_spin.value()
        self.calculate_all = False
        self.accept()


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Shortcuts")
        self.setMinimumWidth(560)
        self.setMinimumHeight(520)
        self.setStyleSheet(QApplication.instance().styleSheet())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QLabel(
            "<div style='font-size:16px;'><b>Keyboard and Mouse Shortcuts</b></div>"
            "<div style='color:#5b6776;'>"
            "Shortcuts depend on the current view and selection."
            "</div>"
        )
        header.setWordWrap(True)
        header.setTextFormat(Qt.RichText)
        layout.addWidget(header)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        layout.addWidget(scroll)

        content = QWidget()
        scroll.setWidget(content)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        sections = [
            ("Navigation", [
                ("Left / Right", "Step through points on the current trajectory."),
                ("Up / Down", "Select previous/next trajectory in the table."),
                ("J / L", "Previous/next frame."),
                (", / .", "Previous/next kymograph."),
                ("1-8", "Switch active movie channel (multi-channel movies)."),
            ]),
            ("Trajectory", [
                ("Enter", "Add trajectory (if drawing) or recalculate selected."),
                ("X", "Invalidate/revalidate highlighted point."),
                ("Space", "Toggle looping through the trajectory."),
                ("T", "Cycle tracking mode."),
                ("Backspace", "Delete selected trajectory."),
                ("O", "Cycle trajectory overlay (off -> all -> selected; selected shows one trajectory)."),
            ]),
            ("Kymograph / ROI", [
                ("N", "Toggle line ROI mode."),
                ("Shift (hold)", "Anchor edit mode for selected trajectory in the current kymograph."),
                ("Esc", "Cancel active click sequence."),
            ]),
            ("Movie / View", [
                ("M", "Toggle maximum projection."),
                ("R", "Open the search radius popup."),
                ("Ctrl/Cmd+S", "Save trajectories."),
                ("Ctrl+drag or Middle-drag", "Pan movie or kymograph."),
                ("Mouse wheel", "Zoom movie or kymograph."),
                ("W/A/S/D", "Move the manual marker."),
                ("K", "Simulate click at marker or cursor."),
                ("Shift+Arrows", "Nudge reference image (when visible)."),
            ]),
        ]

        for title, rows in sections:
            section = QFrame()
            section.setStyleSheet(
                "QFrame {"
                "  background: #f7f9fc;"
                "  border: 1px solid #e1e7ef;"
                "  border-radius: 10px;"
                "}"
            )
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(12, 10, 12, 12)
            section_layout.setSpacing(8)

            title_label = QLabel(title)
            title_label.setStyleSheet("font-weight: 600; color: #2a2f36;")
            section_layout.addWidget(title_label)

            grid = QGridLayout()
            grid.setHorizontalSpacing(12)
            grid.setVerticalSpacing(8)
            grid.setColumnStretch(1, 1)

            for row_idx, (keys, desc) in enumerate(rows):
                key_label = QLabel(keys)
                key_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                key_label.setStyleSheet(
                    "QLabel {"
                    "  background: #eef2f7;"
                    "  border: 1px solid #d6dde7;"
                    "  border-radius: 6px;"
                    "  padding: 4px 8px;"
                    "  color: #1f2d3d;"
                    "  font-family: Menlo, Courier, monospace;"
                    "}"
                )
                desc_label = QLabel(desc)
                desc_label.setWordWrap(True)
                desc_label.setStyleSheet("color: #2f3742;")

                grid.addWidget(key_label, row_idx, 0)
                grid.addWidget(desc_label, row_idx, 1)

            section_layout.addLayout(grid)
            content_layout.addWidget(section)

        content_layout.addStretch(1)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.accept)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)
