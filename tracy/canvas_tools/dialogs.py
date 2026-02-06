from ._shared import *
from matplotlib import cm, colors as mcolors


class _PreviewCombo:
    def __init__(self, text=""):
        self._text = text or ""

    def setText(self, text):
        self._text = text or ""

    def currentText(self):
        return self._text

    def count(self):
        return 1 if self._text else 0


class _EmptyTrajTable:
    def currentRow(self):
        return -1


class _EmptyTrajCanvas:
    def __init__(self):
        self.trajectories = []
        self.table_widget = _EmptyTrajTable()


class _KymoPreviewNavigator:
    def __init__(self, source, kymo_name=""):
        self._source = source
        self.kymoCombo = _PreviewCombo(kymo_name)
        self.roiCombo = _PreviewCombo("")
        self.rois = getattr(source, "rois", {})
        self.kymo_roi_map = getattr(source, "kymo_roi_map", {})
        self.movie = getattr(source, "movie", None)
        self.trajectoryCanvas = getattr(source, "trajectoryCanvas", _EmptyTrajCanvas())
        self.inverted_cmap = getattr(source, "inverted_cmap", False)
        self.connect_all_spots = getattr(source, "connect_all_spots", False)
        self.kymo_anchor_edit_mode = getattr(source, "kymo_anchor_edit_mode", False)
        self.kymo_anchor_overlay_button = getattr(source, "kymo_anchor_overlay_button", None)
        self._kymo_label_to_row = {}
        self.looping = False
        self.force_overlay = False
        self.set_kymo_name(kymo_name)

    def set_kymo_name(self, kymo_name):
        self.kymoCombo.setText(kymo_name or "")
        info = self.kymo_roi_map.get(kymo_name, {}) if kymo_name else {}
        self.roiCombo.setText(info.get("roi", "") or "")

    def get_kymo_traj_overlay_mode(self):
        if self.force_overlay:
            return "all"
        if self._source and hasattr(self._source, "get_kymo_traj_overlay_mode"):
            return self._source.get_kymo_traj_overlay_mode()
        if self._source and hasattr(self._source, "get_traj_overlay_mode"):
            return self._source.get_traj_overlay_mode()
        return "all"

    def get_traj_overlay_mode(self):
        return self.get_kymo_traj_overlay_mode()

    def _compute_roi_cache(self, roi):
        if self._source and hasattr(self._source, "_compute_roi_cache"):
            return self._source._compute_roi_cache(roi)
        return np.array([0.0]), np.array([0.0]), np.array([0.0]), 0.0

    def _compute_kymo_x(self, *args, **kwargs):
        if self._source and hasattr(self._source, "_compute_kymo_x"):
            return self._source._compute_kymo_x(*args, **kwargs)
        return 0.0

    def compute_kymo_x_from_roi(self, *args, **kwargs):
        if self._source and hasattr(self._source, "compute_kymo_x_from_roi"):
            return self._source.compute_kymo_x_from_roi(*args, **kwargs)
        return 0.0

    def _traj_matches_current_kymo(self, *args, **kwargs):
        if self._source and hasattr(self._source, "_traj_matches_current_kymo"):
            return self._source._traj_matches_current_kymo(*args, **kwargs)
        return False

    def _get_traj_colors(self, *args, **kwargs):
        if self._source and hasattr(self._source, "_get_traj_colors"):
            return self._source._get_traj_colors(*args, **kwargs)
        return {}, "#ff00ff"

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
        self.buttonBox.setCenterButtons(True)
        layout.addWidget(self.buttonBox, alignment=Qt.AlignHCenter)

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
    SCALEBAR_SIZE_SCALE = 2.3
    # Class‐level storage of the last settings
    _last_use_prefix = False
    _last_middle    = ""
    _last_custom    = ""
    _last_lut       = "Greys"
    _lut_options    = [
        "Greys (inv.)",
        "Greys",
        "Cyan",
        "Fire",
        "Green",
        "Magenta",
        "Orange",
        "Red",
        "Spectrum",
        "Thermal",
        "Yellow",
    ]
    _lut_single_colors = {
        "Cyan": (0, 255, 255),
        "Green": (0, 255, 0),
        "Magenta": (255, 0, 255),
        "Orange": (255, 165, 0),
        "Red": (255, 0, 0),
        "Yellow": (255, 255, 0),
    }
    _lut_cmap_map = {
        "Cyan": "winter",
        "Fire": "afmhot",
        "Green": "Greens",
        "Magenta": "RdPu",
        "Orange": "Oranges",
        "Red": "Reds",
        "Spectrum": "nipy_spectral",
        "Thermal": "plasma",
        "Yellow": "YlOrBr",
    }

    @classmethod
    def lut_to_table(cls, label, current_inverted=False):
        if label == "Greys":
            ramp = np.linspace(0, 255, 256)
            lut = np.vstack([ramp, ramp, ramp]).astype(np.uint8)
            return lut
        if label == "Greys (inv.)":
            ramp = np.linspace(255, 0, 256)
            lut = np.vstack([ramp, ramp, ramp]).astype(np.uint8)
            return lut

        color = cls._lut_single_colors.get(label)
        if color is not None:
            color = np.array(color, dtype=np.float64) / 255.0
            ramp = np.linspace(0, 1, 256)[:, None]
            colors = ramp * color
            return (colors * 255).astype(np.uint8).T

        cmap_name = cls._lut_cmap_map.get(label, "gray")
        cmap_obj = cm.get_cmap(cmap_name, 256)
        colors = cmap_obj(np.linspace(0, 1, 256))[:, :3]
        colors[0] = 0.0
        return (colors * 255).astype(np.uint8).T

    @classmethod
    def lut_to_cmap(cls, label, current_inverted=False):
        lut = cls.lut_to_table(label, current_inverted=current_inverted)
        colors = (lut.T / 255.0)
        return mcolors.ListedColormap(colors, name=f"tracy_{label}")

    @staticmethod
    def _nice_values():
        vals = []
        for exp in range(-3, 10):
            base = 10 ** exp
            for m in (1, 2, 5):
                vals.append(m * base)
        return vals

    @classmethod
    def _choose_scale_value(cls, target_px, size_px, per_px, min_frac, max_frac):
        min_px = size_px * min_frac
        max_px = size_px * max_frac
        if per_px is None or per_px <= 0:
            per_px = 1.0
        candidates = cls._nice_values()
        best = None
        for val in candidates:
            px = val / per_px
            if px <= 0:
                continue
            if min_px <= px <= max_px:
                diff = abs(px - target_px)
                if best is None or diff < best[0]:
                    best = (diff, val, px)
        if best is None:
            for val in candidates:
                px = val / per_px
                if px <= 0:
                    continue
                diff = abs(px - target_px)
                if best is None or diff < best[0]:
                    best = (diff, val, px)
        return best[1], best[2]

    @staticmethod
    def _format_scale_value(value, unit_base):
        if unit_base == "px":
            return f"{int(round(value))} px"
        v = float(value)
        unit = unit_base
        if unit == "nm":
            if v >= 500:
                v /= 1000.0
                unit = "µm"
            if v >= 500:
                v /= 1000.0
                unit = "mm"
        elif unit == "ms":
            if v >= 500:
                v /= 1000.0
                unit = "s"
            if unit == "s" and v >= 500:
                v /= 60.0
                unit = "min"
        if abs(v - round(v)) < 1e-6:
            v_str = str(int(round(v)))
        else:
            v_str = f"{v:g}"
        return f"{v_str} {unit}"

    @classmethod
    def draw_scale_bars(
        cls,
        ax,
        shape,
        origin="upper",
        pixel_size_nm=None,
        frame_interval_ms=None,
        set_outer_pad=True,
        dpi=None,
        size_scale=3.2,
    ):
        if shape is None or len(shape) < 2:
            return []
        h, w = shape[0], shape[1]
        if h <= 0 or w <= 0:
            return []

        scale = float(size_scale) if size_scale else 1.0
        if scale <= 0:
            scale = 1.0
        pad = max(8, int(0.06 * min(w, h)))
        text_pad = max(8, int(0.04 * min(w, h)))
        label_pad = text_pad * 2.0
        lw_px = max(1.5, min(w, h) * 0.002) * scale
        font_px = max(11, min(18, int(min(w, h) * 0.02))) * scale
        fig = getattr(ax, "figure", None)
        dpi_value = float(dpi) if dpi else float(getattr(fig, "dpi", 100.0))
        pt_per_px = 72.0 / dpi_value
        lw = lw_px * pt_per_px
        font_size = font_px * pt_per_px

        dist_unit = "nm" if pixel_size_nm else "px"
        dist_per_px = pixel_size_nm if pixel_size_nm else 1.0
        if w > h:
            h_target = w * 0.08
            h_min_frac, h_max_frac = 0.05, 0.1
        else:
            h_target = w * 0.25
            h_min_frac, h_max_frac = 0.2, 0.35
        h_value, h_len_px = cls._choose_scale_value(
            h_target, w, dist_per_px, min_frac=h_min_frac, max_frac=h_max_frac
        )
        h_label = cls._format_scale_value(h_value, dist_unit)

        time_unit = "ms" if frame_interval_ms else "px"
        time_per_px = frame_interval_ms if frame_interval_ms else 1.0
        if w > h:
            v_target = h * 0.25
            v_min_frac, v_max_frac = 0.2, 0.35
        else:
            v_target = h * 0.08
            v_min_frac, v_max_frac = 0.05, 0.1
        v_value, v_len_px = cls._choose_scale_value(
            v_target, h, time_per_px, min_frac=v_min_frac, max_frac=v_max_frac
        )
        v_label = cls._format_scale_value(v_value, time_unit)

        right_x = w - 1
        x_v = right_x + pad

        # Place bars relative to current axis direction (bottom is y0).
        y0, y1 = ax.get_ylim()
        y_inc = y1 >= y0
        y_bottom_vis = y0
        y_out = -pad if y_inc else pad
        y_up = v_len_px if y_inc else -v_len_px

        y_h = y_bottom_vis + y_out
        y_v_bottom = y_bottom_vis
        y_v_top = y_v_bottom + y_up
        v_text_y = (y_v_bottom + y_v_top) / 2.0
        v_text_x = x_v + label_pad

        x_end = right_x
        x_start = x_end - h_len_px

        # Expand axes limits to make room for scale bars outside the image.
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        outer_pad = pad + text_pad * 4
        # Expand limits while preserving current axis direction to avoid flips.
        x0, x1 = ax.get_xlim()
        y0, y1 = ax.get_ylim()
        x_inc = x1 >= x0
        y_inc = y1 >= y0
        x_min, x_max = -outer_pad, right_x + outer_pad
        y_min, y_max = -outer_pad, h - 1 + outer_pad
        ax.set_xlim(x_min, x_max) if x_inc else ax.set_xlim(x_max, x_min)
        ax.set_ylim(y_min, y_max) if y_inc else ax.set_ylim(y_max, y_min)
        if set_outer_pad:
            ax._outer_pad = outer_pad

        artists = []
        h_line = ax.plot([x_start, x_end], [y_h, y_h], color="black", lw=lw)[0]
        v_line = ax.plot([x_v, x_v], [y_v_bottom, y_v_top], color="black", lw=lw)[0]
        for line in (h_line, v_line):
            line.set_clip_on(False)
        artists.extend([h_line, v_line])

        text_offset_px = max(4, font_px * 0.45)
        text_offset_pt = text_offset_px * pt_per_px
        h_text = ax.annotate(
            h_label,
            xy=(x_start + h_len_px / 2.0, y_h),
            xytext=(0, -text_offset_pt),
            textcoords="offset points",
            color="black",
            ha="center",
            va="top",
            fontsize=font_size,
        )
        v_text = ax.text(
            v_text_x,
            v_text_y,
            v_label,
            color="black",
            ha="center",
            va="center",
            rotation=-90,
            rotation_mode="anchor",
            fontsize=font_size,
        )
        for txt in (h_text, v_text):
            txt.set_clip_on(False)
        artists.extend([h_text, v_text])

        return artists

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
            QCheckBox:disabled {
                color: #9AA3B2;
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

            QFrame#kymoPreviewFrame {
                background-color: white;
                border: 1px solid #D6DCEA;
                border-radius: 10px;
            }

            QLabel#kymoPreviewTitle {
                font-weight: 600;
                color: #4A5670;
                padding: 6px 0;
            }
        """)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        self.setWindowTitle("Save Kymographs")
        self.movie_name = movie_name
        self.kymo_list  = [n for n, _ in kymo_items]
        self._kymo_map = {n: k for n, k in kymo_items}
        self.selected   = []
        self._preview_nav = _KymoPreviewNavigator(parent, self.kymo_list[0] if self.kymo_list else "")

        if parent and hasattr(parent, "_last_dir"):
            self.directory = parent._last_dir
        else:
            self.directory = os.getcwd()
        self.dir_le = QLineEdit(self.directory)

        self._all_formats = ["tif", "pdf", "png", "jpg"]

        outer_h = QHBoxLayout(self)
        outer_h.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Horizontal, self)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        outer_h.addWidget(splitter)
        left_panel = QWidget(self)
        main_v = QVBoxLayout(left_panel)
        left_panel.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)

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
        self.dir_le.setMinimumWidth(260)
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

        # ── LUT dropdown ──────────────────────────────
        lut_h = QHBoxLayout()
        lut_h.addStretch()
        lut_h.addWidget(QLabel("LUT:"))
        self.lut_combo = QComboBox()
        for name in self._lut_options:
            self.lut_combo.addItem(name)
        parent_inv = bool(getattr(parent, "inverted_cmap", False)) if parent else False
        default_lut = "Greys (inv.)" if parent_inv else "Greys"
        if self.__class__._last_lut in ("Greys", "Greys (inv.)"):
            self.__class__._last_lut = default_lut
        self.lut_combo.setCurrentText(self.__class__._last_lut)
        self.lut_combo.currentTextChanged.connect(self._update_kymo_preview)
        lut_h.addWidget(self.lut_combo)
        lut_h.addStretch()
        main_v.addLayout(lut_h)

        # ── Overlay checkbox ──────────────────────────
        self.overlay_cb = QCheckBox("Overlay trajectories")
        self.overlay_cb.toggled.connect(self._on_overlay_toggled)
        main_v.addWidget(self.overlay_cb, alignment=Qt.AlignHCenter)

        # ── Labels checkbox (only with overlay) ───────
        self.labels_cb = QCheckBox("Labels")
        self.labels_cb.setChecked(True)
        self.labels_cb.setEnabled(False)
        self.labels_cb.toggled.connect(self._update_kymo_preview)
        main_v.addWidget(self.labels_cb, alignment=Qt.AlignHCenter)

        # ── Scale bars checkbox ───────────────────────
        self.scalebar_cb = QCheckBox("Scale bars")
        self.scalebar_cb.toggled.connect(self._update_kymo_preview)
        main_v.addWidget(self.scalebar_cb, alignment=Qt.AlignHCenter)

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
        self.custom_le.setMinimumWidth(260)
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

        splitter.addWidget(left_panel)

        # ── Preview panel ─────────────────────────────
        preview_frame = QFrame(self)
        preview_frame.setObjectName("kymoPreviewFrame")
        preview_frame.setSizePolicy(QtWidgets.QSizePolicy.MinimumExpanding, QtWidgets.QSizePolicy.Expanding)
        preview_v = QVBoxLayout(preview_frame)
        preview_v.setContentsMargins(12, 12, 12, 12)
        preview_v.setSpacing(8)

        from ..canvases.kymo import KymoCanvas
        self.kymo_preview_canvas = KymoCanvas(parent=preview_frame, navigator=self._preview_nav)
        self.kymo_preview_canvas.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.kymo_preview_canvas.setFocusPolicy(Qt.NoFocus)
        self.kymo_preview_canvas.setStyleSheet("background-color: white;")
        self.kymo_preview_canvas.fig.patch.set_alpha(1)
        self.kymo_preview_canvas.fig.patch.set_facecolor("white")
        self.kymo_preview_canvas.ax.patch.set_alpha(1)
        self.kymo_preview_canvas.ax.patch.set_facecolor("white")
        self.kymo_preview_canvas.setMinimumSize(160, 320)
        self.kymo_preview_canvas.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        # Re-apply scale bars after resize to keep padding consistent.
        self.kymo_preview_canvas.mpl_connect("resize_event", lambda _evt: self._update_kymo_preview())
        preview_v.addWidget(self.kymo_preview_canvas, 1)

        splitter.addWidget(preview_frame)
        right_min = (
            self.kymo_preview_canvas.minimumWidth()
            + preview_v.contentsMargins().left()
            + preview_v.contentsMargins().right()
        )
        preview_frame.setMinimumWidth(right_min)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        left_hint = left_panel.sizeHint().width()
        right_hint = right_min
        splitter.setSizes([left_hint, right_hint])
        total_width = left_hint + right_hint + splitter.handleWidth()
        self.resize(max(total_width, 600), self.sizeHint().height())

        # ── Initial sync ───────────────────────────────
        self._on_filetype_changed(self.ft_combo.currentText())
        self._on_list_change()
        self._update_kymo_preview()


    def getOptions(self):
        # Build options dict.
        opts = {
            "directory":  self.directory,
            "selected":   self.selected,
            "overlay":    self.overlay_cb.isChecked(),
            "labels":     self.labels_cb.isChecked(),
            "scalebars":  self.scalebar_cb.isChecked(),
            "lut":        self.lut_combo.currentText() if hasattr(self, "lut_combo") else "Greys",
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
        SaveKymographDialog._last_lut       = opts["lut"]

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

        self._update_kymo_preview()

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
        self.labels_cb.setEnabled(bool(checked))
        self._update_preview()
        self._update_kymo_preview()

    def _on_filetype_changed(self, ext: str):
        """
        - If TIFF is selected, disable and uncheck the overlay option.
        - For PDF/PNG/JPG, enable the overlay checkbox.
        """
        is_tif = ext.lower() == "tif"

        if is_tif:
            self.overlay_cb.blockSignals(True)
            self.overlay_cb.setChecked(False)
            self.overlay_cb.setEnabled(False)
            self.overlay_cb.blockSignals(False)
            self.labels_cb.setEnabled(False)
            self.scalebar_cb.blockSignals(True)
            self.scalebar_cb.setChecked(False)
            self.scalebar_cb.setEnabled(False)
            self.scalebar_cb.blockSignals(False)
        else:
            self.overlay_cb.setEnabled(True)
            self.labels_cb.setEnabled(self.overlay_cb.isChecked())
            self.scalebar_cb.setEnabled(True)

        self._update_preview()
        self._update_kymo_preview()

    def _get_lut_cmap(self):
        parent = self.parent()
        current_inv = bool(getattr(parent, "inverted_cmap", False)) if parent else False
        return self.__class__.lut_to_cmap(
            self.lut_combo.currentText(),
            current_inverted=current_inv
        )

    def _pick_preview_kymo(self):
        if not self.kymo_list:
            return None

        parent = self.parent()
        current = ""
        if parent and hasattr(parent, "kymoCombo"):
            current = parent.kymoCombo.currentText()

        if current and current in self._kymo_map:
            if not self.selected or current in self.selected:
                return current

        if self.selected:
            for name in self.selected:
                if name in self._kymo_map:
                    return name

        if current and current in self._kymo_map:
            return current

        return self.kymo_list[0]

    def _set_preview_image(self, image, cmap="gray", origin="upper"):
        canvas = getattr(self, "kymo_preview_canvas", None)
        if canvas is None or image is None:
            return

        canvas.clear_kymo_trajectory_markers()
        canvas.ax.cla()
        canvas._im = None
        canvas._marker = None

        h, w = image.shape[:2]
        canvas.ax.set_aspect("auto")
        # Match the main kymo canvas orientation (imshow default origin + increasing y-limits).
        canvas._im = canvas.ax.imshow(image, cmap=cmap, origin="upper")
        canvas._force_origin = None
        canvas.ax.set_xlim(0, w)
        canvas.ax.set_ylim(0, h)
        canvas.ax.axis("off")
        canvas.image = image

    def _render_plain_preview(self, name):
        kymo = self._kymo_map.get(name)
        if kymo is None:
            return

        parent = self.parent()
        settings = getattr(parent, "kymo_contrast_settings", {}).get(name) if parent else None
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
        disp = np.flipud(disp)
        cmap = self._get_lut_cmap()
        self._set_preview_image(disp, cmap=cmap, origin="upper")
        if self.scalebar_cb.isChecked():
            parent = self.parent()
            px = getattr(parent, "pixel_size", None) if parent else None
            ms = getattr(parent, "frame_interval", None) if parent else None
            self.__class__.draw_scale_bars(
                self.kymo_preview_canvas.ax,
                disp.shape,
                origin="upper",
                pixel_size_nm=px,
                frame_interval_ms=ms,
                set_outer_pad=True,
                size_scale=self.__class__.SCALEBAR_SIZE_SCALE,
            )
        else:
            self.kymo_preview_canvas.ax._outer_pad = 0

    def _render_overlay_preview(self, name):
        kymo = self._kymo_map.get(name)
        if kymo is None:
            return

        kymo = np.flipud(kymo)
        p15, p99 = np.percentile(kymo, (15, 99))
        denom = p99 - p15
        if denom == 0:
            denom = 1
        img8 = np.clip((kymo - p15) / denom, 0, 1) * 255.0
        img8 = img8.astype(np.uint8)
        cmap = self._get_lut_cmap()
        self._preview_nav.set_kymo_name(name)
        self._set_preview_image(img8, cmap=cmap, origin="upper")

        try:
            self.kymo_preview_canvas.draw_trajectories_on_kymo(
                showsearchline=False,
                skinny=True,
                show_labels=self.labels_cb.isChecked()
            )
        except Exception:
            pass
        if self.scalebar_cb.isChecked():
            parent = self.parent()
            px = getattr(parent, "pixel_size", None) if parent else None
            ms = getattr(parent, "frame_interval", None) if parent else None
            self.__class__.draw_scale_bars(
                self.kymo_preview_canvas.ax,
                img8.shape,
                origin="upper",
                pixel_size_nm=px,
                frame_interval_ms=ms,
                set_outer_pad=True,
                size_scale=self.__class__.SCALEBAR_SIZE_SCALE,
            )
        else:
            self.kymo_preview_canvas.ax._outer_pad = 0

    def _update_kymo_preview(self):
        if not hasattr(self, "kymo_preview_canvas"):
            return

        parent = self.parent()
        if parent:
            self._preview_nav.inverted_cmap = getattr(parent, "inverted_cmap", False)
            self._preview_nav.connect_all_spots = getattr(parent, "connect_all_spots", False)
            self._preview_nav.kymo_anchor_edit_mode = getattr(parent, "kymo_anchor_edit_mode", False)
            self._preview_nav.kymo_anchor_overlay_button = getattr(parent, "kymo_anchor_overlay_button", None)
        self._preview_nav.force_overlay = self.overlay_cb.isChecked()

        name = self._pick_preview_kymo()
        if not name:
            self.kymo_preview_canvas.ax.cla()
            self.kymo_preview_canvas.ax.axis("off")
            self.kymo_preview_canvas.draw()
            return

        if self.overlay_cb.isChecked():
            self._render_overlay_preview(name)
        else:
            self._render_plain_preview(name)

        self.kymo_preview_canvas.draw()


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
