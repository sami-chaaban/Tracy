from ._shared import *

class RecalcDialog(QDialog):
    def __init__(self, current_mode, current_radius, message = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Recalculate")
        self.new_mode = current_mode
        self.new_radius = current_radius

        # Main layout
        layout = QVBoxLayout()

        self.setStyleSheet(QApplication.instance().styleSheet())

        # Display message with the number of trajectories needing recalculation.
        message_label = QLabel(message)
        layout.addWidget(message_label)

        # Tracking Mode dropdown
        mode_layout = QHBoxLayout()
        mode_label = QLabel("Tracking mode:")
        mode_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Independent", "Tracked", "Smooth"]) #, "Same center"
        self.mode_combo.setCurrentText(current_mode)
        mode_layout.addWidget(mode_label)
        mode_layout.addWidget(self.mode_combo)
        layout.addLayout(mode_layout)

        # Search radius spin box
        radius_layout = QHBoxLayout()
        radius_label = QLabel("Search Radius:")
        # radius_label.setStyleSheet(
        #     "font-weight: bold"
        # )
        radius_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.radius_spin = QSpinBox()
        self.radius_spin.setRange(8, 50)
        self.radius_spin.setValue(current_radius)
        radius_layout.addWidget(radius_label)
        radius_layout.addWidget(self.radius_spin)
        layout.addLayout(radius_layout)

        # OK and Cancel buttons
        button_layout = QHBoxLayout()
        ok_button = QPushButton("Process")
        ok_button.setAutoDefault(True)
        ok_button.setDefault(True)
        cancel_button = QPushButton("Cancel")
        ok_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)
        button_layout.addWidget(ok_button)
        layout.addLayout(button_layout)

        self.setLayout(layout)

    def accept(self):
        # Store the chosen values
        self.new_mode = self.mode_combo.currentText()
        self.new_radius = self.radius_spin.value()
        super().accept()

class RecalcWorker(QObject):
    progress = pyqtSignal(int)
    finished = pyqtSignal(list)
    canceled = pyqtSignal()

    def __init__(self, rows, trajectories, navigator):
        super().__init__()
        self._rows         = rows
        self._trajectories = trajectories
        self._navigator    = navigator
        self._is_canceled  = False

    @pyqtSlot()
    def run(self):
        total = sum(len(self._trajectories[r]["frames"]) for r in self._rows)
        count = 0
        results = []
        # If diffusion is enabled, require scale to be set (nm/px and ms)
        if getattr(self._navigator, "show_diffusion", False):
            if getattr(self._navigator, "pixel_size", None) is None or getattr(self._navigator, "frame_interval", None) is None:
                raise ValueError(
                    "Diffusion requires scale: set Pixel size (nm) and Frame interval (ms) before recalculating."
                )
        for row in self._rows:
            if self._is_canceled:
                self.canceled.emit()
                return
            old = self._trajectories[row]
            new_traj = self._navigator._rebuild_one_trajectory(old, self._navigator)

            # Preserve / merge custom_fields
            if "custom_fields" not in new_traj:
                new_traj["custom_fields"] = old.get("custom_fields", {}).copy()
            else:
                merged = old.get("custom_fields", {}).copy()
                merged.update(new_traj.get("custom_fields") or {})
                new_traj["custom_fields"] = merged

            # Optionally recompute diffusion (D, alpha) and store into custom_fields
            if getattr(self._navigator, "show_diffusion", False):
                try:
                    D, alpha = self._navigator.compute_diffusion_for_data(
                        new_traj.get("frames", []),
                        new_traj.get("spot_centers", [])
                    )
                except Exception:
                    D, alpha = (None, None)

                cf = new_traj.setdefault("custom_fields", {})

                # Prefer explicit navigator-provided column names if present
                d_key = (
                    getattr(self._navigator, "_DIFF_D_COL", None)
                    or getattr(self._navigator, "diffusion_D_col", None)
                    or getattr(self._navigator, "diffusion_d_col", None)
                )
                a_key = (
                    getattr(self._navigator, "_DIFF_A_COL", None)
                    or getattr(self._navigator, "diffusion_alpha_col", None)
                    or getattr(self._navigator, "diffusion_a_col", None)
                )

                # Fall back to existing custom_fields keys if they already exist
                if d_key is None or a_key is None:
                    for k in list(cf.keys()):
                        lk = k.lower()
                        if a_key is None and ("alpha" in lk or "α" in k):
                            a_key = k
                        if d_key is None and (lk.strip() == "d" or "diffusion" in lk):
                            if "alpha" not in lk and "α" not in k:
                                d_key = k

                # Final fallbacks
                if d_key is None:
                    d_key = "D"
                if a_key is None:
                    a_key = "alpha"

                # Formatting: keep D compact (4 significant figures) and alpha consistent (4 decimals)
                cf[d_key] = "" if D is None else f"{float(D):.4f}"
                cf[a_key] = "" if alpha is None else f"{float(alpha):.3f}"

            results.append((row, new_traj))

            count += len(old["frames"])
            self.progress.emit(count)

        self.finished.emit(results)

    def cancel(self):
        self._is_canceled = True

class RecalcAllWorker(QObject):
    progress = pyqtSignal(int)        # emits number of trajectories processed so far
    finished = pyqtSignal(dict)       # emits a dict mapping row → new trajectory
    canceled = pyqtSignal()

    def __init__(self, backup_trajectories: list, navigator):
        super().__init__()
        self._backup = backup_trajectories
        self._navigator = navigator
        # Use exactly one flag for cancellation:
        self._navigator._is_canceled = False

    @pyqtSlot()
    def run(self):
        # print("▶ run() entered; initial _is_canceled =", self._navigator._is_canceled)
        processed = 0
        results = {}
        # If diffusion is enabled, require scale to be set (nm/px and ms)
        if getattr(self._navigator, "show_diffusion", False):
            if getattr(self._navigator, "pixel_size", None) is None or getattr(self._navigator, "frame_interval", None) is None:
                raise ValueError(
                    "Diffusion requires scale: set Pixel size (nm) and Frame interval (ms) before recalculating."
                )

        for row_index, old in enumerate(self._backup):
            # print(f"worker (top of loop) _is_canceled = {self._navigator._is_canceled}")
            # 1) Check the one shared flag at the top of each iteration
            if self._navigator._is_canceled:
                self.canceled.emit()
                return

            # 2) Build “pts” list, checking the same flag inside any nested loops
            if len(old["anchors"]) > 1 and old.get("roi") is not None:
                pts = []
                anchors, roi = old["anchors"], old["roi"]
                for i in range(len(anchors) - 1):
                    if self._navigator._is_canceled:
                        self.canceled.emit()
                        return

                    f1, x1, y1 = anchors[i]
                    f2, x2, y2 = anchors[i+1]
                    seg = range(f1, f2+1) if i == 0 else range(f1+1, f2+1)
                    xs = np.linspace(x1, x2, len(seg), endpoint=True)
                    for j, f in enumerate(seg):
                        if self._navigator._is_canceled:
                            self.canceled.emit()
                            return
                        mx, my = self._navigator.compute_roi_point(roi, xs[j])
                        pts.append((f, mx, my))
            else:
                pts = []
                for f, (x, y) in zip(old["frames"], old["original_coords"]):
                    if self._navigator._is_canceled:
                        self.canceled.emit()
                        return
                    pts.append((f, x, y))

            # 3) Just before calling _compute_analysis, check again
            if self._navigator._is_canceled:
                self.canceled.emit()
                return
            
            # print(f"compute (just before calling _compute_analysis) _is_canceled = {self._navigator._is_canceled}")

            # 4) Run compute_analysis (no GUI), catch exceptions
            try:
                traj_background = self._navigator.compute_trajectory_background(
                    self._navigator.get_movie_frame,
                    pts,
                    crop_size=int(2 * self._navigator.searchWindowSpin.value())
                )
                frames, coords, search_centers, ints, fit, background = (
                    self._navigator._compute_analysis(
                        pts,
                        traj_background,
                        showprogress=False
                    )
                )
            except Exception:
                # skip this trajectory but still bump the progress bar
                processed += 1
                self.progress.emit(processed)
                continue

            # print(f"compute returned; now checking cancellation → {self._navigator._is_canceled}")

            # 5) Immediately after compute_analysis, check cancellation again
            if self._navigator._is_canceled:
                self.canceled.emit()
                return

            # 6) Unpack & rebuild new_traj (same as before)
            spots  = [p[0] for p in fit]
            sigmas = [p[1] for p in fit]
            peaks  = [p[2] for p in fit]
            valid_ints = [v for v, s in zip(ints, spots) if v and v > 0 and s]
            avg_int = float(np.mean(valid_ints)) if valid_ints else None
            med_int = float(np.median(valid_ints)) if valid_ints else None

            vels = []
            for i in range(1, len(spots)):
                p0, p1 = spots[i-1], spots[i]
                if p0 is None or p1 is None:
                    vels.append(None)
                else:
                    vels.append(float(np.hypot(p1[0]-p0[0], p1[1]-p0[1])))
            good_vels = [v for v in vels if v is not None]
            avg_vpf   = float(np.mean(good_vels)) if good_vels else None

            full_centers, full_sigmas, full_peaks, full_ints = [], [], [], []
            for f in old["frames"]:
                if f in frames:
                    idx = frames.index(f)
                    full_centers.append(spots[idx])
                    full_sigmas.append(sigmas[idx])
                    full_peaks.append(peaks[idx])
                    full_ints.append(ints[idx])
                else:
                    full_centers.append(None)
                    full_sigmas.append(None)
                    full_peaks.append(None)
                    full_ints.append(None)

            new_traj = {
                "trajectory_number": old["trajectory_number"],
                "channel":           old["channel"],
                "start":             old["start"],
                "end":               old["end"],
                "anchors":           old["anchors"],
                "roi":               old["roi"],
                "spot_centers":      full_centers,
                "sigmas":            full_sigmas,
                "peaks":             full_peaks,
                "fixed_background":  traj_background,
                "background":        background,
                "frames":            old["frames"],
                "original_coords":   old["original_coords"],
                "search_centers":    search_centers,
                "intensities":       full_ints,
                "average":           avg_int,
                "median":           med_int,
                "velocities":        vels,
                "average_velocity":  avg_vpf
            }

            # 7) Recompute colocalization exactly as before
            if getattr(self._navigator, "check_colocalization", False) and self._navigator.movie.ndim == 4:
                nav = self._navigator
                nav.analysis_frames     = new_traj["frames"]
                nav.analysis_fit_params = list(zip(
                    new_traj["spot_centers"],
                    new_traj["sigmas"],
                    new_traj["peaks"]
                ))
                nav.analysis_channel = new_traj["channel"]
                nav._compute_colocalization(showprogress=False)
                any_list = list(nav.analysis_colocalized)
                by_ch = {
                    ch: list(flags)
                    for ch, flags in nav.analysis_colocalized_by_ch.items()
                }
            else:
                if getattr(self._navigator, "movie", None) is None or self._navigator._channel_axis is None:
                    n_chan = 1
                else:
                    n_chan = self._navigator.movie.shape[self._navigator._channel_axis]

                N = len(new_traj["frames"])
                any_list = [None]*N
                by_ch    = { ch: [None]*N
                             for ch in range(1, n_chan+1)
                             if ch != new_traj["channel"] }

            new_traj["colocalization_any"]   = any_list
            new_traj["colocalization_by_ch"] = by_ch

            # 8) Optionally recompute steps
            if getattr(self._navigator, "show_steps", False):
                idxs, meds = self._navigator.compute_steps_for_data(
                    new_traj["frames"],
                    new_traj["intensities"]
                )
                new_traj["step_indices"] = idxs
                new_traj["step_medians"] = meds
            else:
                new_traj["step_indices"] = None
                new_traj["step_medians"] = None

            # 9) Preserve custom_fields
            new_traj["custom_fields"] = old.get("custom_fields", {}).copy()
            # Optionally recompute diffusion (D, alpha) and store into custom_fields
            if getattr(self._navigator, "show_diffusion", False):
                try:
                    D, alpha = self._navigator.compute_diffusion_for_data(
                        new_traj.get("frames", []),
                        new_traj.get("spot_centers", [])
                    )
                except Exception:
                    D, alpha = (None, None)

                cf = new_traj.setdefault("custom_fields", {})

                # Prefer explicit navigator-provided column names if present
                d_key = (
                    getattr(self._navigator, "_DIFF_D_COL", None)
                    or getattr(self._navigator, "diffusion_D_col", None)
                    or getattr(self._navigator, "diffusion_d_col", None)
                )
                a_key = (
                    getattr(self._navigator, "_DIFF_A_COL", None)
                    or getattr(self._navigator, "diffusion_alpha_col", None)
                    or getattr(self._navigator, "diffusion_a_col", None)
                )

                # Fall back to existing custom_fields keys if they already exist
                if d_key is None or a_key is None:
                    for k in list(cf.keys()):
                        lk = k.lower()
                        if a_key is None and ("alpha" in lk or "α" in k):
                            a_key = k
                        if d_key is None and (lk.strip() == "d" or "diffusion" in lk):
                            if "alpha" not in lk and "α" not in k:
                                d_key = k

                # Final fallbacks
                if d_key is None:
                    d_key = "D"
                if a_key is None:
                    a_key = "alpha"

                # Formatting: keep D compact (4 significant figures) and alpha consistent (4 decimals)
                cf[d_key] = "" if D is None else f"{float(D):.4f}"
                cf[a_key] = "" if alpha is None else f"{float(alpha):.3f}"

            # 10) Store in results and bump progress
            results[row_index] = new_traj
            processed += 1
            self.progress.emit(processed)

        # 11) Finished without cancellation
        #print("▶ run() finished all trajectories without seeing a cancel")
        self.finished.emit(results)

    def cancel(self):
        print("cancel() called, setting flag → True")
        # Called when the user clicks “Cancel” on the QProgressDialog:
        self._navigator._is_canceled = True
