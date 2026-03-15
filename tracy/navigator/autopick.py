from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from ._shared import *


class AutoPickCanceled(RuntimeError):
    pass


def _raise_if_autopick_canceled(cancel_requested):
    if callable(cancel_requested) and bool(cancel_requested()):
        raise AutoPickCanceled()


def _trackset_quality_impl(tracks: list[dict]) -> float:
    if not tracks:
        return 0.0
    scores = sorted((float(tr.get("score", 0.0)) for tr in tracks), reverse=True)
    tspans = sorted((float(tr.get("t_span", 0.0)) for tr in tracks), reverse=True)
    max_span = tspans[0] if tspans else 0.0
    return float(
        sum(scores[:4])
        + 0.12 * sum(tspans[:4])
        + 0.35 * max_span
        + 0.03 * len(tracks)
    )


def _merge_track_sets_impl(track_sets: list[list[dict]], max_tracks: int, params: dict) -> list[dict]:
    from kymo_autopick.postprocess import _dedupe_tracks

    merged: list[dict] = []
    for tracks in track_sets:
        for tr in tracks or []:
            merged.append(dict(tr))

    if not merged:
        return []

    merged = _dedupe_tracks(merged, max_mean_dx=1.3, min_overlap_frac=0.65)
    merged = _dedupe_tracks(
        merged,
        max_mean_dx=float(max(0.2, params.get("dedupe_max_mean_dx", 0.6))),
        min_overlap_frac=float(np.clip(params.get("dedupe_min_overlap_frac", 0.88), 0.5, 0.99)),
    )
    merged.sort(key=lambda tr: float(tr.get("score", 0.0)), reverse=True)

    if max_tracks and max_tracks > 0 and len(merged) > int(max_tracks):
        merged = merged[: int(max_tracks)]

    return merged


def _postprocess_with_fallback_impl(
    prob_move: np.ndarray,
    params: dict,
    embedding_map: np.ndarray | None = None,
    cancel_requested=None,
):
    from kymo_autopick.postprocess import postprocess_prob_to_tracks

    _raise_if_autopick_canceled(cancel_requested)

    base = dict(params or {})
    disable_fallback = bool(base.pop("disable_fallback", False))
    base_prob = float(base.get("prob_thresh", 0.35))
    base_min_t_span = int(max(2, round(float(base.get("min_t_span", 6)))))
    base_min_mean_prob = float(base.get("min_mean_prob", 0.12))
    base_max_tracks = int(base.get("max_tracks", 12) or 12)
    per_mode_cap = int(max(24, min(220, max(base_max_tracks, 48))))

    strict_params = {**base, "max_tracks": per_mode_cap}
    strict_debug: dict = {}
    strict_tracks = postprocess_prob_to_tracks(
        prob_move,
        embedding_map=embedding_map,
        debug=strict_debug,
        **strict_params,
    )
    strict_quality = _trackset_quality_impl(strict_tracks)
    strict_count = int(len(strict_tracks))
    strict_med_prob = (
        float(np.median([float(tr.get("mean_prob", 0.0)) for tr in strict_tracks]))
        if strict_tracks
        else 0.0
    )

    strict_target = int(max(8, min(24, round(0.16 * max(24, base_max_tracks)))))
    if strict_tracks and strict_count >= strict_target and strict_med_prob >= max(0.11, 0.85 * base_min_mean_prob):
        return strict_tracks, "strict", strict_params, strict_quality, strict_debug

    if disable_fallback:
        return strict_tracks, "single-pass", strict_params, strict_quality, strict_debug

    # Keep fallback close to the user's requested threshold. The earlier hard
    # caps at 0.88/0.80 made high thresholds like 0.97 jump into much looser
    # modes, which flooded the result set; disabling fallback entirely then
    # over-corrected and missed obvious tracks. Staying near the user threshold
    # preserves the useful recovery passes without making the control behave
    # erratically at the top end.
    recover_prob = float(np.clip(base_prob - 0.04, 0.55, 0.999))
    rescue_prob = float(np.clip(base_prob - 0.08, 0.45, 0.999))
    ladders = [
        (
            "recover",
            {
                **base,
                "prob_thresh": recover_prob,
                "min_len": min(base.get("min_len", 2.0), 2.4),
                "vmin": min(base.get("vmin", 0.0), 0.0),
                "min_component_size": max(1, min(int(base.get("min_component_size", 1)), 2)),
                "min_t_span": max(3, min(base_min_t_span, 5)),
                "min_mean_prob": max(0.09, base_min_mean_prob - 0.02),
                "max_tracks": per_mode_cap,
                "smooth_sigma_t": min(float(base.get("smooth_sigma_t", 0.05)), 0.04),
                "smooth_sigma_x": min(float(base.get("smooth_sigma_x", 0.25)), 0.20),
                "merge_gap_t": max(1, min(int(base.get("merge_gap_t", 4)), 3)),
                "merge_max_dx": min(max(float(base.get("merge_max_dx", 5.5)), 4.0), 6.0),
                "merge_min_bridge_prob": max(float(base.get("merge_min_bridge_prob", 0.27)), 0.28),
                "merge_min_bridge_coverage": max(float(base.get("merge_min_bridge_coverage", 0.80)), 0.80),
                "dedupe_max_mean_dx": max(float(base.get("dedupe_max_mean_dx", 0.75)), 0.75),
                "dedupe_min_overlap_frac": min(float(base.get("dedupe_min_overlap_frac", 0.70)), 0.80),
                "min_directionality": min(base.get("min_directionality", 0.0), 0.0),
                "min_net_velocity": max(base.get("min_net_velocity", 0.0), 0.0),
            },
        ),
        (
            "rescue",
            {
                **base,
                "prob_thresh": rescue_prob,
                "min_len": min(base.get("min_len", 2.0), 2.2),
                "vmin": min(base.get("vmin", 0.0), 0.0),
                "min_component_size": max(1, min(int(base.get("min_component_size", 1)), 2)),
                "min_t_span": max(3, min(base_min_t_span, 4)),
                "min_mean_prob": max(0.08, base_min_mean_prob - 0.04),
                "max_tracks": per_mode_cap,
                "smooth_sigma_t": 0.0,
                "smooth_sigma_x": min(float(base.get("smooth_sigma_x", 0.25)), 0.16),
                "merge_gap_t": max(1, min(int(base.get("merge_gap_t", 4)), 2)),
                "merge_max_dx": min(max(float(base.get("merge_max_dx", 5.5)), 4.6), 6.4),
                "merge_min_bridge_prob": max(float(base.get("merge_min_bridge_prob", 0.27)), 0.30),
                "merge_min_bridge_coverage": max(float(base.get("merge_min_bridge_coverage", 0.80)), 0.82),
                "merge_min_embed_sim": max(float(base.get("merge_min_embed_sim", 0.18)), 0.22),
                "dedupe_max_mean_dx": max(float(base.get("dedupe_max_mean_dx", 0.75)), 0.80),
                "dedupe_min_overlap_frac": min(float(base.get("dedupe_min_overlap_frac", 0.70)), 0.78),
                "min_directionality": min(base.get("min_directionality", 0.0), 0.0),
                "min_net_velocity": min(base.get("min_net_velocity", 0.0), 0.0),
            },
        ),
    ]

    mode_results = []
    merged_inputs: list[list[dict]] = []
    selected_debug = strict_debug
    if strict_tracks:
        merged_inputs.append(strict_tracks)
        mode_results.append(("strict", strict_params, strict_quality, strict_count))

    for mode, mode_params in ladders:
        _raise_if_autopick_canceled(cancel_requested)
        mode_debug: dict = {}
        tracks = postprocess_prob_to_tracks(
            prob_move,
            embedding_map=embedding_map,
            debug=mode_debug,
            **mode_params,
        )
        if not tracks:
            continue
        quality = _trackset_quality_impl(tracks)
        count = int(len(tracks))
        med_prob = float(np.median([float(tr.get("mean_prob", 0.0)) for tr in tracks]))

        if strict_tracks:
            count_limit = max(strict_count + 24, int(round(1.6 * strict_count)))
            if count > count_limit and quality <= (1.20 * strict_quality):
                continue
            prob_floor = max(0.85 * base_min_mean_prob, 0.75 * strict_med_prob, 0.08)
            if med_prob < prob_floor:
                continue

        merged_inputs.append(tracks)
        mode_results.append((mode, mode_params, quality, count))
        selected_debug = mode_debug

    _raise_if_autopick_canceled(cancel_requested)
    if merged_inputs:
        merged_tracks = _merge_track_sets_impl(merged_inputs, max_tracks=base_max_tracks, params=base)
        if merged_tracks:
            if len(mode_results) == 1 and mode_results[0][0] == "strict":
                return merged_tracks, "strict", strict_params, _trackset_quality_impl(merged_tracks), strict_debug
            return merged_tracks, "mixed", dict(base), _trackset_quality_impl(merged_tracks), selected_debug

    if strict_tracks:
        return strict_tracks, "strict", strict_params, strict_quality, strict_debug
    return [], "strict", dict(base), 0.0, strict_debug


class AutoPickWorker(QtCore.QObject):
    finished = pyqtSignal(object)
    error = pyqtSignal(str)
    canceled = pyqtSignal()
    stage_changed = pyqtSignal(str)

    def __init__(self, kymo_array: np.ndarray, onnx_path: str, postprocess_params: dict | None = None):
        super().__init__()
        self.kymo_array = np.asarray(kymo_array)
        self.onnx_path = str(onnx_path)
        self.postprocess_params = dict(postprocess_params or {})
        self._cancel_requested = False

    def cancel(self):
        self._cancel_requested = True

    @pyqtSlot()
    def run(self):
        try:
            from kymo_autopick.infer_onnx import infer_prob_move_and_embeddings_onnx

            cancel_requested = lambda: bool(self._cancel_requested)
            _raise_if_autopick_canceled(cancel_requested)

            self.stage_changed.emit("Running model...")
            prob_bright, emb_bright = infer_prob_move_and_embeddings_onnx(
                self.kymo_array,
                self.onnx_path,
                time_axis="y",
            )

            _raise_if_autopick_canceled(cancel_requested)
            self.stage_changed.emit("Finding anchors...")

            tracks_b, mode_b, _params_b, q_b, debug_b = _postprocess_with_fallback_impl(
                prob_bright,
                self.postprocess_params,
                embedding_map=np.asarray(emb_bright, dtype=np.float32) if emb_bright is not None else None,
                cancel_requested=cancel_requested,
            )
            chosen_prob = np.asarray(prob_bright, dtype=np.float32)
            tracks = tracks_b
            mode = mode_b
            source = "bright"

            _raise_if_autopick_canceled(cancel_requested)

            self.finished.emit(
                {
                    "prob_move": chosen_prob,
                    "embedding_map": np.asarray(emb_bright, dtype=np.float32) if emb_bright is not None else None,
                    "tracks": tracks,
                    "mode": mode,
                    "source": source,
                    "debug": debug_b,
                }
            )
        except AutoPickCanceled:
            self.canceled.emit()
        except Exception as exc:
            self.error.emit(str(exc))


class _AutoPickReviewRecalcSignals(QtCore.QObject):
    finished = pyqtSignal(int, object)
    error = pyqtSignal(int, str)


class _AutoPickReviewRecalcTask(QtCore.QRunnable):
    def __init__(
        self,
        request_id: int,
        prob_move: np.ndarray,
        params: dict,
        embedding_map: np.ndarray | None = None,
    ):
        super().__init__()
        self.request_id = int(request_id)
        self.prob_move = np.asarray(prob_move, dtype=np.float32)
        self.params = dict(params or {})
        self.embedding_map = (
            np.asarray(embedding_map, dtype=np.float32) if embedding_map is not None else None
        )
        self.signals = _AutoPickReviewRecalcSignals()
        self.setAutoDelete(True)

    @pyqtSlot()
    def run(self):
        try:
            tracks, _mode, _params, _quality, _debug = _postprocess_with_fallback_impl(
                self.prob_move,
                self.params,
                embedding_map=self.embedding_map,
            )
            self.signals.finished.emit(int(self.request_id), list(tracks or []))
        except Exception as exc:
            self.signals.error.emit(int(self.request_id), str(exc))


class _AutoPickTrackRow(QtWidgets.QFrame):
    hovered = pyqtSignal(object)
    unhovered = pyqtSignal(object)
    clicked = pyqtSignal(object)
    delete_requested = pyqtSignal(object)

    def __init__(self, track_id: int, color: str, text: str, delete_icon: QIcon | None = None, parent=None):
        super().__init__(parent)
        self.track_id = int(track_id)
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName("autoPickTrackRow")
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.setStyleSheet(
            """
            QFrame#autoPickTrackRow {
                background: #f5f7fb;
                border: 1px solid #d5ddea;
                border-radius: 8px;
            }
            QFrame#autoPickTrackRow QLabel {
                background: transparent;
            }
            QFrame#autoPickTrackRow QToolButton {
                background: transparent;
                border: none;
                border-radius: 0px;
                color: #697584;
                padding: 0px;
            }
            QFrame#autoPickTrackRow QToolButton:hover {
                color: #1e2935;
            }
            """
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 5, 6, 5)
        layout.setSpacing(6)

        swatch = QLabel(self)
        swatch.setFixedSize(10, 10)
        swatch.setStyleSheet(
            f"background: {color}; border: 1px solid rgba(0,0,0,0.35); border-radius: 5px;"
        )
        layout.addWidget(swatch, 0, Qt.AlignVCenter)

        self._label = QLabel(text, self)
        self._label.setStyleSheet("color: #17212b; font-size: 13px; font-weight: 600;")
        self._label.setWordWrap(False)
        layout.addWidget(self._label, 0, Qt.AlignVCenter)

        self._delete_button = QtWidgets.QToolButton(self)
        self._delete_button.setCursor(Qt.PointingHandCursor)
        self._delete_button.setAutoRaise(True)
        self._delete_button.setToolTip("Remove this found item")
        self._delete_button.setFixedSize(16, 16)
        self._delete_button.setIconSize(QSize(12, 12))
        if delete_icon is not None and not delete_icon.isNull():
            self._delete_button.setIcon(delete_icon)
        else:
            self._delete_button.setText("x")
        self._delete_button.clicked.connect(lambda: self.delete_requested.emit(self.track_id))
        layout.addWidget(self._delete_button, 0, Qt.AlignVCenter)

    def set_selected(self, selected: bool):
        if selected:
            self.setStyleSheet(
                """
                QFrame#autoPickTrackRow {
                    background: #e9f4ff;
                    border: 1px solid #5f9fe8;
                    border-radius: 8px;
                }
                QFrame#autoPickTrackRow QLabel {
                    background: transparent;
                }
                QFrame#autoPickTrackRow QToolButton {
                    background: transparent;
                    border: none;
                    border-radius: 0px;
                    color: #697584;
                    padding: 0px;
                }
                QFrame#autoPickTrackRow QToolButton:hover {
                    color: #1e2935;
                }
                """
            )
        else:
            self.setStyleSheet(
                """
                QFrame#autoPickTrackRow {
                    background: #f5f7fb;
                    border: 1px solid #d5ddea;
                    border-radius: 8px;
                }
                QFrame#autoPickTrackRow QLabel {
                    background: transparent;
                }
                QFrame#autoPickTrackRow QToolButton {
                    background: transparent;
                    border: none;
                    border-radius: 0px;
                    color: #697584;
                    padding: 0px;
                }
                QFrame#autoPickTrackRow QToolButton:hover {
                    color: #1e2935;
                }
                """
            )

    def set_text(self, text: str):
        self._label.setText(text)

    def enterEvent(self, event):
        self.hovered.emit(self.track_id)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.unhovered.emit(self.track_id)
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.track_id)
        super().mousePressEvent(event)


class _AutoPickReviewCanvas(KymoCanvas):
    def __init__(self, owner, navigator, parent=None):
        super().__init__(parent=parent, navigator=navigator)
        self.owner = owner

    def keyPressEvent(self, event):
        event.ignore()

    def keyReleaseEvent(self, event):
        event.ignore()

    def display_prepared_image(self, image, *, cmap, vmin, vmax):
        arr = np.asarray(image)
        if arr.ndim != 2:
            raise ValueError("Auto-pick review expects a 2D kymograph image")

        self.reset_canvas()
        h, w = arr.shape
        self.ax.set_xlim(0, w)
        self.ax.set_ylim(0, h)
        self.ax.set_aspect("equal", adjustable="box")
        self._im = self.ax.imshow(
            arr,
            cmap=cmap,
            vmin=vmin,
            vmax=vmax,
            origin="upper",
            interpolation="nearest",
            aspect="equal",
        )
        self.ax.axis("off")
        self.draw()
        self.image = arr

        self.zoom_center = (w / 2, h / 2)
        widget_w = max(self.width(), 1)
        widget_h = max(self.height(), 1)
        self.scale = max(w / widget_w, h / widget_h)
        self.max_scale = self.scale * self.padding
        self.update_view()

    def on_mouse_press(self, event):
        if event.inaxes != self.ax:
            return
        self.setFocus(Qt.MouseFocusReason)
        if event.button == 2:
            return super().on_mouse_press(event)
        if event.button == 1 and self.owner is not None:
            self.owner.on_canvas_left_press(event)
        elif event.button == 3 and self.owner is not None:
            self.owner.on_canvas_right_press(event)

    def on_mouse_move(self, event):
        if self._is_panning and event.inaxes == self.ax:
            return super().on_mouse_move(event)
        if self.owner is not None:
            self.owner.on_canvas_mouse_move(event)

    def on_mouse_release(self, event):
        was_panning = bool(self._is_panning)
        super().on_mouse_release(event)
        if not was_panning and event.button == 1 and self.owner is not None:
            self.owner.on_canvas_left_release(event)


class _AutoPickReviewDialog(QDialog):
    _CONF_SLIDER_MAX = 1000
    _TUNING_SLIDER_MAX = 1000
    _TUNING_SPECS = (
        {"key": "merge_min_bridge_prob", "label": "Merge min bridge prob", "min": 0.0, "max": 1.0, "decimals": 2},
        {"key": "merge_min_bridge_coverage", "label": "Merge min bridge coverage", "min": 0.0, "max": 1.0, "decimals": 2},
        {"key": "merge_min_embed_sim", "label": "Merge min embed sim", "min": 0.0, "max": 1.0, "decimals": 2},
        {"key": "dedupe_min_overlap_frac", "label": "Dedupe min overlap", "min": 0.0, "max": 1.0, "decimals": 2},
        {"key": "min_directionality", "label": "Min directionality", "min": 0.0, "max": 1.0, "decimals": 2},
    )

    def __init__(
        self,
        navigator,
        *,
        display_image: np.ndarray,
        display_cmap,
        display_vmin,
        display_vmax,
        prepared_tracks: list[dict],
        preview_colors: list[str],
        prob_move: np.ndarray | None = None,
        embedding_map: np.ndarray | None = None,
        postprocess_params: dict | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.navigator = navigator
        self._image = np.asarray(display_image)
        self._display_cmap = display_cmap
        self._display_vmin = display_vmin
        self._display_vmax = display_vmax
        self._tracks = []
        self._track_rows = {}
        self._selected_track_id = None
        self._hover_track_id = None
        self._drag = None
        self._overlay_artists = {}
        self._overlay_bg = None
        self._overlay_flush_pending = False
        self._last_drag_redraw_at = 0.0
        self._deleted_track_refs: list[dict] = []
        self._confidence_threshold = 0.0
        self._preview_confidence_threshold = 0.0
        self._confidence_preview_active = False
        self._committed_confidence_slider_value = 0
        self._recalc_prob_move = np.asarray(prob_move, dtype=np.float32) if prob_move is not None else None
        self._recalc_embedding_map = np.asarray(embedding_map, dtype=np.float32) if embedding_map is not None else None
        self._recalc_base_params = dict(postprocess_params or {})
        self._recalc_tasks: dict[int, _AutoPickReviewRecalcTask] = {}
        self._recalc_request_seq = 0
        self._recalc_inflight_request_id: int | None = None
        self._recalc_inflight_params: dict | None = None
        self._recalc_pending_params: dict | None = None
        self._recalc_running = False
        self._tuning_controls: dict[str, dict] = {}
        self._status_default = (
            "Drag an anchor to move it. Right-click an anchor to remove it. "
            "Right-click a line segment to add an anchor. "
            "Use the backspace or delete key to remove a trajectory."
        )
        self._row_delete_icon = QIcon(self.navigator.resource_path("icons/cross-small.svg"))

        self.setWindowTitle("Review anchors")
        self.setWindowModality(Qt.ApplicationModal)
        self.setWindowFlag(Qt.Window, True)
        self.setWindowFlag(Qt.Dialog, True)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        sheet_flag = getattr(Qt, "Sheet", None)
        if sheet_flag is not None:
            self.setWindowFlag(sheet_flag, False)

        self._load_prepared_tracks(prepared_tracks, preview_colors, preserve_deleted=False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(10)

        title = QLabel("Review anchors before adding trajectories", self)
        title.setStyleSheet("font-size: 16px; font-weight: 600; color: #111111;")
        outer.addWidget(title)

        self._tuning_widget = None
        if bool(getattr(self.navigator, "debug_mode", False)):
            tuning_widget = QWidget(self)
            tuning_layout = QHBoxLayout(tuning_widget)
            tuning_layout.setContentsMargins(0, 0, 0, 0)
            tuning_layout.setSpacing(10)
            for spec in self._TUNING_SPECS:
                group = QWidget(tuning_widget)
                group.setMinimumWidth(122)
                group_layout = QVBoxLayout(group)
                group_layout.setContentsMargins(0, 0, 0, 0)
                group_layout.setSpacing(3)

                label = QLabel(str(spec["label"]), group)
                label.setWordWrap(True)
                label.setAlignment(Qt.AlignHCenter | Qt.AlignBottom)
                label.setStyleSheet("font-size: 11px; font-weight: 600; color: #17212b;")
                group_layout.addWidget(label)

                value_label = QLabel(group)
                value_label.setAlignment(Qt.AlignHCenter | Qt.AlignVCenter)
                value_label.setStyleSheet("font-size: 11px; color: #616d7a;")
                group_layout.addWidget(value_label)

                slider = QSlider(Qt.Horizontal, group)
                slider.setRange(0, self._TUNING_SLIDER_MAX)
                slider.setTracking(False)
                slider.valueChanged.connect(lambda value, kk=str(spec["key"]): self._on_tuning_slider_value_changed(kk, value))
                slider.sliderMoved.connect(lambda value, kk=str(spec["key"]): self._on_tuning_slider_preview(kk, value))
                slider.sliderReleased.connect(lambda kk=str(spec["key"]): self._on_tuning_slider_released(kk))
                group_layout.addWidget(slider)
                tuning_layout.addWidget(group, 1)

                self._tuning_controls[str(spec["key"])] = {
                    "spec": dict(spec),
                    "widget": group,
                    "label": label,
                    "value_label": value_label,
                    "slider": slider,
                    "committed_slider_value": 0,
                }
            outer.addWidget(tuning_widget)
            self._tuning_widget = tuning_widget

        body = QHBoxLayout()
        body.setSpacing(12)
        outer.addLayout(body, 1)

        left = QWidget(self)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        self.canvas = _AutoPickReviewCanvas(owner=self, navigator=navigator, parent=left)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.canvas.mpl_connect("draw_event", self._on_canvas_draw_event)
        self.canvas.display_prepared_image(
            self._image,
            cmap=self._display_cmap,
            vmin=self._display_vmin,
            vmax=self._display_vmax,
        )
        left_layout.addWidget(self.canvas, 1)

        self.status_label = QLabel(self._status_default, left)
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #616d7a;")
        left_layout.addWidget(self.status_label)
        body.addWidget(left, 1)

        right = QWidget(self)
        right.setMinimumWidth(180)
        right.setMaximumWidth(240)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(8)

        filter_header = QLabel("Minimum confidence", right)
        filter_header.setStyleSheet("font-size: 12px; font-weight: 600; color: #17212b;")
        right_layout.addWidget(filter_header)

        self.confidence_value_label = QLabel(right)
        self.confidence_value_label.setStyleSheet("color: #616d7a;")
        right_layout.addWidget(self.confidence_value_label)

        self.confidence_slider = QSlider(Qt.Horizontal, right)
        self.confidence_slider.setRange(0, self._CONF_SLIDER_MAX)
        self.confidence_slider.setValue(0)
        self.confidence_slider.setTracking(False)
        self.confidence_slider.valueChanged.connect(self._on_confidence_slider_value_changed)
        self.confidence_slider.sliderMoved.connect(self._on_confidence_slider_preview)
        self.confidence_slider.sliderReleased.connect(self._on_confidence_slider_released)
        right_layout.addWidget(self.confidence_slider)

        self.track_count_label = QLabel(right)
        self.track_count_label.setStyleSheet("color: #616d7a;")
        right_layout.addWidget(self.track_count_label)

        self.list_scroll = QScrollArea(right)
        self.list_scroll.setWidgetResizable(True)
        self.list_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        self.list_container = QWidget(self.list_scroll)
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch(1)
        self.list_scroll.setWidget(self.list_container)
        right_layout.addWidget(self.list_scroll, 1)
        body.addWidget(right, 0)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok, parent=self)
        self.add_button = buttons.button(QDialogButtonBox.Ok)
        self.discard_button = buttons.button(QDialogButtonBox.Cancel)
        if self.add_button is not None:
            self.add_button.setText("Add Trajectories")
        if self.discard_button is not None:
            self.discard_button.setText("Discard")
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

        self._delete_shortcuts = []
        for seq in (QKeySequence(Qt.Key_Delete), QKeySequence(Qt.Key_Backspace)):
            shortcut = QShortcut(seq, self)
            shortcut.activated.connect(self._delete_selected_track)
            self._delete_shortcuts.append(shortcut)

        self._sync_confidence_slider_bounds()
        self._init_tuning_controls()
        self._update_confidence_widgets()
        self._rebuild_track_rows()
        self._apply_initial_dialog_size()
        self._redraw_canvas()

    def _available_screen_geometry(self):
        screen = self.screen()
        if screen is None:
            handle = self.windowHandle()
            if handle is not None:
                screen = handle.screen()
        if screen is None:
            screen = QApplication.primaryScreen()
        if screen is None:
            return QtCore.QRect(0, 0, 1440, 900)
        return screen.availableGeometry()

    def _apply_initial_dialog_size(self):
        screen_geom = self._available_screen_geometry()
        screen_w = max(800, int(screen_geom.width()))
        screen_h = max(640, int(screen_geom.height()))

        image_h = max(1, int(self._image.shape[0]))
        image_w = max(1, int(self._image.shape[1]))
        image_aspect = float(image_w) / float(image_h)

        sidebar_w = 220
        if hasattr(self, "list_scroll"):
            try:
                sidebar_w = int(np.clip(self.list_scroll.sizeHint().width() + 24, 190, 250))
            except Exception:
                sidebar_w = 220

        canvas_h = int(np.clip(screen_h * 0.68, 360, 760))
        canvas_w = int(np.clip(canvas_h * image_aspect, 320, screen_w * 0.72))

        title_h = 0
        status_h = 0
        button_h = 0
        try:
            title_h = int(self.findChildren(QLabel)[0].sizeHint().height())
        except Exception:
            title_h = 28
        if hasattr(self, "status_label"):
            status_h = int(self.status_label.sizeHint().height())
        if hasattr(self, "add_button") and self.add_button is not None:
            button_h = int(self.add_button.parentWidget().sizeHint().height())

        chrome_h = 12 + 12 + 10 + 10 + title_h + status_h + button_h + 36
        dialog_w = int(np.clip(canvas_w + sidebar_w + 48, 620, screen_w * 0.92))
        if hasattr(self, "_tuning_widget"):
            try:
                dialog_w = max(dialog_w, int(self._tuning_widget.sizeHint().width()) + 48)
            except Exception:
                pass
        dialog_h = int(np.clip(canvas_h + chrome_h, 560, screen_h * 0.90))
        self.resize(dialog_w, dialog_h)

    def edited_anchor_sets(self) -> list[list[tuple[int, float, float]]]:
        out = []
        for track in self._filtered_tracks():
            anchors = sorted(track["anchors"], key=lambda node: int(node[0]))
            if len(anchors) >= 2:
                out.append([(int(t), float(x), float(y)) for t, x, y in anchors])
        return out

    def _active_tracks(self) -> list[dict]:
        return [track for track in self._tracks if not track.get("deleted")]

    def _filtered_tracks(self, threshold: float | None = None) -> list[dict]:
        if threshold is None:
            threshold = float(self._confidence_threshold)
        return [
            track for track in self._active_tracks()
            if float(track.get("confidence", 0.0)) >= threshold
        ]

    def _confidence_to_slider(self, value: float) -> int:
        clipped = float(np.clip(float(value), 0.0, 1.0))
        return int(round(clipped * float(self._CONF_SLIDER_MAX)))

    def _slider_to_confidence(self, value: int) -> float:
        return float(np.clip(float(value) / float(self._CONF_SLIDER_MAX), 0.0, 1.0))

    def _confidence_bounds(self) -> tuple[float, float]:
        active_tracks = self._active_tracks()
        if not active_tracks:
            return 0.0, 1.0
        values = [
            float(np.clip(float(track.get("confidence", 0.0)), 0.0, 1.0))
            for track in active_tracks
        ]
        return min(values), max(values)

    def _sync_confidence_slider_bounds(self):
        if not hasattr(self, "confidence_slider"):
            return
        min_conf, _max_conf = self._confidence_bounds()
        min_slider = self._confidence_to_slider(min_conf)
        max_slider = self._CONF_SLIDER_MAX
        current_slider = int(self.confidence_slider.value())
        if current_slider < min_slider:
            current_slider = min_slider
        current_slider = int(np.clip(current_slider, min_slider, max_slider))
        self.confidence_slider.blockSignals(True)
        self.confidence_slider.setRange(min_slider, max_slider)
        self.confidence_slider.setValue(current_slider)
        self.confidence_slider.blockSignals(False)
        self._confidence_threshold = self._slider_to_confidence(current_slider)
        self._preview_confidence_threshold = self._confidence_threshold
        self._committed_confidence_slider_value = int(current_slider)

    def _tuning_spec(self, key: str) -> dict:
        control = self._tuning_controls.get(str(key)) or {}
        return dict(control.get("spec") or {})

    def _tuning_value_to_slider(self, key: str, value: float) -> int:
        spec = self._tuning_spec(key)
        lo = float(spec.get("min", 0.0))
        hi = float(spec.get("max", 1.0))
        if hi <= lo:
            return 0
        clipped = float(np.clip(float(value), lo, hi))
        frac = (clipped - lo) / (hi - lo)
        return int(round(frac * float(self._TUNING_SLIDER_MAX)))

    def _tuning_slider_to_value(self, key: str, slider_value: int) -> float:
        spec = self._tuning_spec(key)
        lo = float(spec.get("min", 0.0))
        hi = float(spec.get("max", 1.0))
        if hi <= lo:
            return lo
        frac = float(np.clip(float(slider_value) / float(self._TUNING_SLIDER_MAX), 0.0, 1.0))
        return lo + frac * (hi - lo)

    def _format_tuning_value(self, key: str, value: float) -> str:
        spec = self._tuning_spec(key)
        decimals = int(max(0, spec.get("decimals", 2)))
        return f"{float(value):.{decimals}f}"

    def _current_tuning_params(self) -> dict:
        params = dict(self._recalc_base_params or {})
        for key, control in self._tuning_controls.items():
            slider_value = int(control.get("committed_slider_value", control["slider"].value()))
            params[str(key)] = float(self._tuning_slider_to_value(str(key), slider_value))
        return params

    def _load_prepared_tracks(
        self,
        prepared_tracks: list[dict],
        preview_colors: list[str],
        *,
        preserve_deleted: bool,
    ):
        old_selected = self._selected_track()
        old_selected_raw = dict(old_selected.get("raw_track")) if old_selected and isinstance(old_selected.get("raw_track"), dict) else None
        deleted_refs = list(self._deleted_track_refs) if preserve_deleted else []

        self._drag = None
        self._hover_track_id = None
        self._selected_track_id = None
        self._clear_overlay_artists()

        next_tracks = []
        selected_match_id = None
        for idx, prepared in enumerate(prepared_tracks or [], start=1):
            anchors = [
                (int(t), float(x), float(y))
                for t, x, y in sorted(prepared.get("anchors") or [], key=lambda node: int(node[0]))
            ]
            if len(anchors) < 2:
                continue
            raw_track = prepared.get("raw_track")
            raw_track = dict(raw_track) if isinstance(raw_track, dict) else None
            deleted = False
            if raw_track is not None and deleted_refs:
                deleted = any(
                    self.navigator._autopick_tracks_match(raw_track, ref)
                    for ref in deleted_refs
                    if isinstance(ref, dict)
                )
            track = {
                "id": int(idx),
                "anchors": anchors,
                "confidence": float(np.clip(float(prepared.get("confidence", 0.0)), 0.0, 1.0)),
                "color": str(preview_colors[(idx - 1) % max(1, len(preview_colors))]) if preview_colors else "#377eb8",
                "deleted": bool(deleted),
                "raw_track": raw_track,
            }
            if selected_match_id is None and old_selected_raw is not None and raw_track is not None:
                try:
                    if self.navigator._autopick_tracks_match(old_selected_raw, raw_track):
                        selected_match_id = int(idx)
                except Exception:
                    pass
            next_tracks.append(track)

        self._tracks = next_tracks
        self._deleted_track_refs = [
            dict(track["raw_track"])
            for track in self._tracks
            if track.get("deleted") and isinstance(track.get("raw_track"), dict)
        ]

        if selected_match_id is not None:
            self._selected_track_id = int(selected_match_id)
        else:
            active_tracks = self._active_tracks()
            self._selected_track_id = int(active_tracks[0]["id"]) if active_tracks else None

    def _init_tuning_controls(self):
        has_prob = self._recalc_prob_move is not None
        has_embed = self._recalc_embedding_map is not None
        for key, control in self._tuning_controls.items():
            slider = control["slider"]
            value_label = control["value_label"]
            value = float(self._recalc_base_params.get(key, self._tuning_slider_to_value(key, 0)))
            slider_value = self._tuning_value_to_slider(key, value)
            control["committed_slider_value"] = int(slider_value)
            slider.blockSignals(True)
            slider.setValue(int(slider_value))
            slider.blockSignals(False)
            if key == "merge_min_embed_sim" and not has_embed:
                slider.setEnabled(False)
                value_label.setText("n/a")
            else:
                slider.setEnabled(bool(has_prob))
                value_label.setText(self._format_tuning_value(key, value))

    def _set_recalc_running(self, running: bool):
        self._recalc_running = bool(running)
        if self.add_button is not None:
            self.add_button.setEnabled((not self._recalc_running) and len(self._filtered_tracks()) > 0)
        if self._recalc_running:
            self._set_status("Recalculating anchors...")
        elif self._drag is None:
            self._set_status(None)

    def _start_tuning_recalc(self, params: dict):
        if self._recalc_prob_move is None:
            return
        self._recalc_request_seq += 1
        request_id = int(self._recalc_request_seq)
        task = _AutoPickReviewRecalcTask(
            request_id,
            prob_move=self._recalc_prob_move,
            params=params,
            embedding_map=self._recalc_embedding_map,
        )
        task.signals.finished.connect(self._on_tuning_recalc_finished)
        task.signals.error.connect(self._on_tuning_recalc_error)
        self._recalc_tasks[request_id] = task
        self._recalc_inflight_request_id = request_id
        self._recalc_inflight_params = dict(params)
        self._set_recalc_running(True)
        QtCore.QThreadPool.globalInstance().start(task)

    def _request_tuning_recalc(self):
        if self._recalc_prob_move is None:
            return
        params = self._current_tuning_params()
        if self._recalc_running:
            self._recalc_pending_params = dict(params)
            return
        if self._recalc_inflight_params == params:
            return
        self._recalc_pending_params = None
        self._start_tuning_recalc(params)

    def _finish_tuning_recalc_cycle(self, request_id: int):
        self._recalc_tasks.pop(int(request_id), None)
        self._recalc_inflight_request_id = None
        self._recalc_inflight_params = None
        self._set_recalc_running(False)

    def _on_tuning_recalc_finished(self, request_id: int, tracks: object):
        if self._recalc_inflight_request_id is not None and int(request_id) != int(self._recalc_inflight_request_id):
            self._recalc_tasks.pop(int(request_id), None)
            return
        inflight_params = dict(self._recalc_inflight_params or {})
        pending = dict(self._recalc_pending_params) if isinstance(self._recalc_pending_params, dict) else None
        self._finish_tuning_recalc_cycle(request_id)
        if pending is not None and pending != inflight_params:
            self._recalc_pending_params = None
            self._start_tuning_recalc(pending)
            return

        raw_tracks = list(tracks or [])
        prepared_tracks = self.navigator._prepare_autopick_review_tracks(raw_tracks)
        preview_colors = self.navigator._autopick_preview_colors([track.get("anchors") or [] for track in prepared_tracks])
        self._load_prepared_tracks(prepared_tracks, preview_colors, preserve_deleted=True)
        self._sync_confidence_slider_bounds()
        self._update_confidence_widgets()
        self._rebuild_track_rows()
        self._redraw_canvas()

    def _on_tuning_recalc_error(self, request_id: int, message: str):
        if self._recalc_inflight_request_id is not None and int(request_id) != int(self._recalc_inflight_request_id):
            self._recalc_tasks.pop(int(request_id), None)
            return
        pending = dict(self._recalc_pending_params) if isinstance(self._recalc_pending_params, dict) else None
        self._finish_tuning_recalc_cycle(request_id)
        if pending is not None:
            self._recalc_pending_params = None
            self._start_tuning_recalc(pending)
            return
        self._set_status(f"Recalculation failed: {message}")

    def _on_tuning_slider_preview(self, key: str, slider_value: int):
        control = self._tuning_controls.get(str(key))
        if not control:
            return
        if str(key) == "merge_min_embed_sim" and self._recalc_embedding_map is None:
            return
        value = self._tuning_slider_to_value(str(key), int(slider_value))
        control["value_label"].setText(self._format_tuning_value(str(key), value))

    def _on_tuning_slider_value_changed(self, key: str, slider_value: int):
        control = self._tuning_controls.get(str(key))
        if not control:
            return
        if str(key) == "merge_min_embed_sim" and self._recalc_embedding_map is None:
            return
        value = self._tuning_slider_to_value(str(key), int(slider_value))
        control["value_label"].setText(self._format_tuning_value(str(key), value))
        if control["slider"].isSliderDown():
            return
        if int(slider_value) == int(control.get("committed_slider_value", slider_value)):
            return
        control["committed_slider_value"] = int(slider_value)
        self._request_tuning_recalc()

    def _on_tuning_slider_released(self, key: str):
        control = self._tuning_controls.get(str(key))
        if not control:
            return
        slider_value = int(control["slider"].value())
        value = self._tuning_slider_to_value(str(key), slider_value)
        control["value_label"].setText(self._format_tuning_value(str(key), value))
        if int(slider_value) == int(control.get("committed_slider_value", slider_value)):
            return
        control["committed_slider_value"] = int(slider_value)
        self._request_tuning_recalc()

    def _display_confidence_threshold(self) -> float:
        if self._confidence_preview_active:
            return float(self._preview_confidence_threshold)
        return float(self._confidence_threshold)

    def _track_by_id(self, track_id):
        for track in self._tracks:
            if int(track["id"]) == int(track_id):
                return track
        return None

    def _selected_track(self):
        if self._selected_track_id is None:
            return None
        track = self._track_by_id(self._selected_track_id)
        if track is None or track.get("deleted"):
            return None
        if float(track.get("confidence", 0.0)) < float(self._confidence_threshold):
            return None
        return track

    def _visible_tracks(self) -> list[dict]:
        threshold = self._display_confidence_threshold()
        return self._filtered_tracks(threshold)

    def _track_summary_text(self, track: dict, ordinal: int) -> str:
        return f"{int(ordinal)}  ({float(track.get('confidence', 0.0)):.2f})"

    def _clear_track_rows(self):
        self._track_rows = {}
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild_track_rows(self):
        self._clear_track_rows()
        filtered_tracks = self._filtered_tracks()
        if self._selected_track() is None:
            self._selected_track_id = int(filtered_tracks[0]["id"]) if filtered_tracks else None

        for ordinal, track in enumerate(filtered_tracks, start=1):
            row = _AutoPickTrackRow(
                int(track["id"]),
                str(track["color"]),
                self._track_summary_text(track, ordinal),
                self._row_delete_icon,
                self.list_container,
            )
            row.hovered.connect(self._on_row_hovered)
            row.unhovered.connect(self._on_row_unhovered)
            row.clicked.connect(self._select_track)
            row.delete_requested.connect(self._delete_track)
            row.set_selected(int(track["id"]) == self._selected_track_id)
            self.list_layout.insertWidget(self.list_layout.count() - 1, row, 0, Qt.AlignLeft)
            self._track_rows[int(track["id"])] = row

        if self.add_button is not None:
            self.add_button.setEnabled(len(filtered_tracks) > 0)
        self._update_confidence_widgets()

    def _set_status(self, text: str | None):
        self.status_label.setText(text or self._status_default)

    def _update_confidence_widgets(self, threshold: float | None = None):
        if threshold is None:
            threshold = self._display_confidence_threshold()
        total = len(self._active_tracks())
        shown = len(self._filtered_tracks(threshold))
        if hasattr(self, "confidence_value_label"):
            self.confidence_value_label.setText(f"{float(threshold):.3f}")
        if hasattr(self, "track_count_label"):
            self.track_count_label.setText(f"Showing {shown} of {total}")

    def _on_confidence_slider_preview(self, value: int):
        self._confidence_preview_active = True
        self._preview_confidence_threshold = self._slider_to_confidence(value)
        if self._hover_track_id is not None:
            hovered = self._track_by_id(self._hover_track_id)
            if (
                hovered is None
                or hovered.get("deleted")
                or float(hovered.get("confidence", 0.0)) < float(self._preview_confidence_threshold)
            ):
                self._hover_track_id = None
        self._update_confidence_widgets(self._preview_confidence_threshold)
        self._redraw_canvas(recompute_geometry=False)

    def _apply_confidence_threshold(self, value: int):
        value = int(np.clip(int(value), self.confidence_slider.minimum(), self.confidence_slider.maximum()))
        self._committed_confidence_slider_value = value
        self._confidence_threshold = self._slider_to_confidence(value)
        self._preview_confidence_threshold = self._confidence_threshold
        self._confidence_preview_active = False
        if self._hover_track_id is not None:
            hovered = self._track_by_id(self._hover_track_id)
            if (
                hovered is None
                or hovered.get("deleted")
                or float(hovered.get("confidence", 0.0)) < float(self._confidence_threshold)
            ):
                self._hover_track_id = None
        self._rebuild_track_rows()
        self._redraw_canvas()

    def _on_confidence_slider_released(self):
        value = int(self.confidence_slider.value())
        if value == int(self._committed_confidence_slider_value):
            self._confidence_preview_active = False
            self._preview_confidence_threshold = self._confidence_threshold
            self._update_confidence_widgets(self._confidence_threshold)
            self._redraw_canvas(recompute_geometry=False)
            return
        self._apply_confidence_threshold(value)

    def _on_confidence_slider_value_changed(self, value: int):
        if self.confidence_slider.isSliderDown():
            return
        if int(value) == int(self._committed_confidence_slider_value):
            self._confidence_preview_active = False
            self._preview_confidence_threshold = self._confidence_threshold
            self._update_confidence_widgets(self._confidence_threshold)
            return
        self._apply_confidence_threshold(value)

    def _select_track(self, track_id):
        track = self._track_by_id(track_id)
        if (
            track is None
            or track.get("deleted")
            or float(track.get("confidence", 0.0)) < float(self._confidence_threshold)
        ):
            return
        self._selected_track_id = int(track_id)
        for row_id, row in self._track_rows.items():
            row.set_selected(int(row_id) == self._selected_track_id)
        self._redraw_canvas()

    def _on_row_hovered(self, track_id):
        self._hover_track_id = int(track_id)
        self._redraw_canvas()

    def _on_row_unhovered(self, track_id):
        if self._hover_track_id == int(track_id):
            self._hover_track_id = None
            self._redraw_canvas()

    def _delete_track(self, track_id):
        track = self._track_by_id(track_id)
        if track is None or track.get("deleted"):
            return
        track["deleted"] = True
        raw_track = track.get("raw_track")
        if isinstance(raw_track, dict):
            already_deleted = any(
                self.navigator._autopick_tracks_match(raw_track, ref)
                for ref in self._deleted_track_refs
                if isinstance(ref, dict)
            )
            if not already_deleted:
                self._deleted_track_refs.append(dict(raw_track))
        self._hover_track_id = None if self._hover_track_id == int(track_id) else self._hover_track_id
        if self._selected_track_id == int(track_id):
            remaining = self._active_tracks()
            self._selected_track_id = int(remaining[0]["id"]) if remaining else None
        self._sync_confidence_slider_bounds()
        self._rebuild_track_rows()
        self._redraw_canvas()

    def _delete_selected_track(self):
        if self._selected_track_id is None:
            return
        self._delete_track(int(self._selected_track_id))

    def _clear_overlay_artists(self):
        for artists in list(self._overlay_artists.values()):
            for artist in artists:
                try:
                    artist.remove()
                except Exception:
                    pass
        self._overlay_artists = {}
        self._overlay_bg = None

    def _remove_track_overlay(self, track_id: int):
        artists = self._overlay_artists.pop(int(track_id), None)
        if not artists:
            return
        for artist in artists:
            try:
                artist.remove()
            except Exception:
                pass

    def _ensure_track_overlay(self, track_id: int):
        tid = int(track_id)
        artists = self._overlay_artists.get(tid)
        if artists is not None:
            return artists
        ax = self.canvas.ax
        (halo,) = ax.plot(
            [],
            [],
            color="#7da1ff",
            linewidth=8.5,
            linestyle="-",
            alpha=0.42,
            zorder=18,
            solid_capstyle="round",
            solid_joinstyle="round",
            animated=True,
        )
        (line,) = ax.plot(
            [],
            [],
            color="#377eb8",
            linewidth=1.45,
            linestyle="--",
            alpha=0.9,
            zorder=15,
            animated=True,
        )
        scatter = ax.scatter(
            [],
            [],
            s=[],
            facecolors=["#377eb8"],
            edgecolors=["#1f1f1f"],
            alpha=0.9,
            linewidths=0.8,
            zorder=16,
            animated=True,
        )
        self._overlay_artists[tid] = (halo, line, scatter)
        return halo, line, scatter

    def _sync_overlay_artists(self, recompute_geometry: bool = True):
        active_tracks = self._active_tracks()
        active_ids = {int(track["id"]) for track in active_tracks}
        visible_ids = {int(track["id"]) for track in self._visible_tracks()}

        for track_id in list(self._overlay_artists):
            if track_id not in active_ids:
                self._remove_track_overlay(track_id)

        for track in active_tracks:
            anchors = sorted(track.get("anchors", []), key=lambda node: int(node[0]))
            halo, line, scatter = self._ensure_track_overlay(int(track["id"]))
            if len(anchors) < 2:
                halo.set_visible(False)
                line.set_visible(False)
                scatter.set_visible(False)
                continue
            xs = [float(node[1]) for node in anchors]
            ys = [float(node[2]) for node in anchors]
            is_selected = (
                int(track["id"]) == int(self._selected_track_id)
                if self._selected_track_id is not None
                else False
            )
            is_hovered = (
                int(track["id"]) == int(self._hover_track_id)
                if self._hover_track_id is not None
                else False
            )
            is_highlighted = bool(is_selected or is_hovered)
            is_visible = int(track["id"]) in visible_ids
            color = str(track["color"])
            linewidth = 2.0 if is_highlighted else 1.45
            markersize = 5.2 if is_highlighted else 4.2
            alpha = 1.0 if is_highlighted else 0.90
            z_line = 20 if is_highlighted else 15
            z_scatter = 21 if is_highlighted else 16
            halo.set_visible(is_visible and is_highlighted)
            halo.set_zorder(19 if is_selected else 18)
            line.set_visible(is_visible)
            scatter.set_visible(is_visible)
            if not recompute_geometry:
                continue
            halo.set_data(xs, ys)
            line.set_data(xs, ys)
            line.set_color(color)
            line.set_linewidth(linewidth)
            line.set_alpha(alpha)
            line.set_zorder(z_line)
            scatter.set_offsets(np.column_stack([xs, ys]))
            scatter.set_sizes(np.full(len(xs), (markersize * markersize) * 2.0))
            face_rgba = np.tile(mcolors.to_rgba(color, alpha), (len(xs), 1))
            edge_rgba = np.tile(mcolors.to_rgba("#1f1f1f", alpha), (len(xs), 1))
            scatter.set_facecolors(face_rgba)
            scatter.set_edgecolors(edge_rgba)
            scatter.set_linewidths(1.0 if is_selected else 0.8)
            scatter.set_alpha(alpha)
            scatter.set_zorder(z_scatter)

    def _visible_overlay_artists(self):
        visible = []
        for track in self._visible_tracks():
            artists = self._overlay_artists.get(int(track["id"]))
            if not artists:
                continue
            halo, line, scatter = artists
            if halo.get_visible() or line.get_visible() or scatter.get_visible():
                visible.append((track, halo, line, scatter))
        visible.sort(
            key=lambda item: (
                1 if self._hover_track_id is not None and int(item[0]["id"]) == int(self._hover_track_id) else 0,
                1 if self._selected_track_id is not None and int(item[0]["id"]) == int(self._selected_track_id) else 0,
            )
        )
        return visible

    def _blit_overlay(self):
        canvas = self.canvas.figure.canvas
        if self._overlay_bg is None:
            return
        canvas.restore_region(self._overlay_bg)
        for _track, halo, line, scatter in self._visible_overlay_artists():
            if halo.get_visible():
                self.canvas.ax.draw_artist(halo)
            if line.get_visible():
                self.canvas.ax.draw_artist(line)
            if scatter.get_visible():
                self.canvas.ax.draw_artist(scatter)
        canvas.blit(self.canvas.ax.bbox)

    def _schedule_overlay_flush(self):
        if self._overlay_flush_pending:
            return
        self._overlay_flush_pending = True
        QtCore.QTimer.singleShot(0, self._flush_overlay)

    def _flush_overlay(self):
        self._overlay_flush_pending = False
        if self.canvas is None or self._overlay_bg is None:
            return
        if not self.isVisible():
            return
        self._blit_overlay()

    def _on_canvas_draw_event(self, event):
        if event is None or event.canvas is not self.canvas.figure.canvas:
            return
        self._overlay_bg = event.canvas.copy_from_bbox(self.canvas.ax.bbox)
        self._schedule_overlay_flush()

    def _redraw_canvas(self, recompute_geometry: bool = True):
        self._sync_overlay_artists(recompute_geometry=recompute_geometry)
        if self._overlay_bg is None:
            self.canvas.draw_idle()
            return
        self._blit_overlay()

    def _event_display_coords(self, event):
        if event.inaxes != self.canvas.ax or event.xdata is None or event.ydata is None:
            return None
        return float(event.xdata), float(event.ydata)

    def _pixel_distance(self, x0, y0, x1, y1):
        ax = self.canvas.ax
        p0x, p0y = ax.transData.transform((x0, y0))
        p1x, p1y = ax.transData.transform((x1, y1))
        return float(np.hypot(p0x - p1x, p0y - p1y))

    def _segment_distance_pixels(self, px, py, ax0, ay0, ax1, ay1):
        vx, vy = ax1 - ax0, ay1 - ay0
        wx, wy = px - ax0, py - ay0
        c1 = vx * wx + vy * wy
        if c1 <= 0:
            return float(np.hypot(px - ax0, py - ay0))
        c2 = vx * vx + vy * vy
        if c2 <= c1:
            return float(np.hypot(px - ax1, py - ay1))
        t = c1 / c2
        projx = ax0 + t * vx
        projy = ay0 + t * vy
        return float(np.hypot(px - projx, py - projy))

    def _find_anchor_hit(self, event, threshold_px: float = 8.0):
        coords = self._event_display_coords(event)
        if coords is None:
            return None
        ex, ey = coords
        best = None
        best_dist = float("inf")
        for track in self._visible_tracks():
            anchors = sorted(track.get("anchors", []), key=lambda node: int(node[0]))
            for idx, (_frame, x, y) in enumerate(anchors):
                dist = self._pixel_distance(ex, ey, float(x), float(y))
                if dist < best_dist:
                    best = (track, idx)
                    best_dist = dist
        if best is None or best_dist > threshold_px:
            return None
        return best

    def _find_segment_hit(self, event, threshold_px: float = 6.0):
        coords = self._event_display_coords(event)
        if coords is None:
            return None
        ex, ey = coords
        ax = self.canvas.ax
        ex_px, ey_px = ax.transData.transform((ex, ey))
        best = None
        best_dist = float("inf")
        for track in self._visible_tracks():
            anchors = sorted(track.get("anchors", []), key=lambda node: int(node[0]))
            if len(anchors) < 2:
                continue
            for idx in range(len(anchors) - 1):
                _t0, x0, y0 = anchors[idx]
                _t1, x1, y1 = anchors[idx + 1]
                p0x, p0y = ax.transData.transform((float(x0), float(y0)))
                p1x, p1y = ax.transData.transform((float(x1), float(y1)))
                dist = self._segment_distance_pixels(ex_px, ey_px, p0x, p0y, p1x, p1y)
                if dist < best_dist:
                    best = (track, idx)
                    best_dist = dist
        if best is None or best_dist > threshold_px:
            return None
        return best

    def _clamp_anchor_position(self, anchors, index, x, y):
        x = float(np.clip(x, 0.0, max(0.0, float(self._image.shape[1] - 1))))
        y = float(np.clip(y, 0.0, max(0.0, float(self._image.shape[0] - 1))))
        frame = int(round((self._image.shape[0] - 1) - y))
        if index > 0:
            prev_frame = int(anchors[index - 1][0]) + 1
            frame = max(frame, prev_frame)
        if index < len(anchors) - 1:
            next_frame = int(anchors[index + 1][0]) - 1
            frame = min(frame, next_frame)
        frame = int(np.clip(frame, 0, max(0, self._image.shape[0] - 1)))
        y = float((self._image.shape[0] - 1) - frame)
        return frame, x, y

    def on_canvas_left_press(self, event):
        hit = self._find_anchor_hit(event)
        if hit is None:
            return
        track, index = hit
        self._select_track(int(track["id"]))
        self._drag = {"track_id": int(track["id"]), "index": int(index), "dirty": False}
        self._last_drag_redraw_at = 0.0

    def on_canvas_mouse_move(self, event):
        if self._drag is None:
            return
        coords = self._event_display_coords(event)
        if coords is None:
            return
        track = self._track_by_id(self._drag["track_id"])
        if track is None or track.get("deleted"):
            return
        anchors = sorted(track.get("anchors", []), key=lambda node: int(node[0]))
        idx = int(self._drag["index"])
        if idx < 0 or idx >= len(anchors):
            return
        old_anchor = anchors[idx]
        frame, x, y = self._clamp_anchor_position(anchors, idx, coords[0], coords[1])
        new_anchor = (int(frame), float(x), float(y))
        if old_anchor == new_anchor:
            return
        anchors[idx] = new_anchor
        anchors.sort(key=lambda node: int(node[0]))
        track["anchors"] = anchors
        self._drag["dirty"] = True
        now = time.perf_counter()
        if now - self._last_drag_redraw_at < (1.0 / 90.0):
            return
        self._last_drag_redraw_at = now
        self._redraw_canvas()

    def on_canvas_left_release(self, _event):
        drag = self._drag
        self._drag = None
        if drag and drag.get("dirty"):
            self._rebuild_track_rows()
            self._redraw_canvas()
        self._set_status(None)

    def on_canvas_right_press(self, event):
        anchor_hit = self._find_anchor_hit(event)
        if anchor_hit is not None:
            track, index = anchor_hit
            anchors = sorted(track.get("anchors", []), key=lambda node: int(node[0]))
            if len(anchors) <= 2:
                self._set_status("Need at least two anchors.")
                return
            anchors.pop(int(index))
            track["anchors"] = anchors
            self._select_track(int(track["id"]))
            self._rebuild_track_rows()
            self._redraw_canvas()
            self._set_status("Anchor removed.")
            return

        seg_hit = self._find_segment_hit(event)
        coords = self._event_display_coords(event)
        if seg_hit is None or coords is None:
            return
        track, seg_idx = seg_hit
        anchors = sorted(track.get("anchors", []), key=lambda node: int(node[0]))
        prev_frame = int(anchors[seg_idx][0])
        next_frame = int(anchors[seg_idx + 1][0])
        frame = int(round((self._image.shape[0] - 1) - coords[1]))
        if frame <= prev_frame or frame >= next_frame:
            self._set_status("Bad anchor order.")
            return
        x = float(np.clip(coords[0], 0.0, max(0.0, float(self._image.shape[1] - 1))))
        y = float((self._image.shape[0] - 1) - frame)
        anchors.insert(seg_idx + 1, (int(frame), float(x), float(y)))
        anchors.sort(key=lambda node: int(node[0]))
        track["anchors"] = anchors
        self._select_track(int(track["id"]))
        self._rebuild_track_rows()
        self._redraw_canvas()

    def _accept_if_valid(self):
        edited = self.edited_anchor_sets()
        if not edited:
            self._set_status("No found items remain to add.")
            return
        self.accept()

class NavigatorAutoPickMixin:
    def _packaged_autopick_model_path(self) -> str:
        resolver = getattr(self, "resource_path", None)
        if resolver is None:
            return ""
        try:
            candidate = Path(resolver("models/model.onnx")).expanduser()
        except Exception:
            return ""
        if candidate.exists():
            return str(candidate)
        return ""

    def init_autopick(self):
        env_model_path = os.environ.get("TRACY_AUTOPICK_ONNX", "").strip()
        self.autopick_onnx_path = env_model_path or self._packaged_autopick_model_path()
        self.cached_prob_move = None
        self.cached_autopick_embedding_map = None
        self._autopick_thread = None
        self._autopick_worker = None
        self._autopick_progress_dialog = None
        self._autopick_cancel_requested = False
        self._autopick_tracks = []
        self._autopick_preview_artists = []
        self._autopick_running = False
        self._autopick_pending_close = False
        self.autopick_postprocess_params = {
            # Single-pass defaults chosen to approximate the previous mixed
            # fallback behavior without hidden recover/rescue passes.
            "prob_thresh": 0.97,
            "min_len": 4.0,
            "vmin": 0.0,
            "simplify_eps": 2.4,
            "max_anchors": 24,
            "min_component_size": 1,
            "min_t_span": 3,
            "min_mean_prob": 0.21,
            "max_tracks": 240,
            "smooth_sigma_t": 0.0,
            "smooth_sigma_x": 0.0,
            "merge_gap_t": 3,
            "merge_max_dx": 2.0,
            "merge_min_bridge_prob": 0.30,
            "merge_min_bridge_coverage": 0.70,
            "merge_min_embed_sim": 0.30,
            # Only collapse near-identical overlaps; keep nearby parallel identities.
            "dedupe_max_mean_dx": 4.0,
            "dedupe_min_overlap_frac": 0.1,
            "min_directionality": 0.2,
            "min_net_velocity": 0.0,
            # Explicit single-pass mode: do not run hidden recover/rescue passes.
            "disable_fallback": True,
        }
        self.autopick_default_postprocess_params = dict(self.autopick_postprocess_params)
        self._autopick_shutdown = False

    def _autopick_settings_specs(self):
        return [
            (
                "Detection",
                [
                    {
                        "key": "prob_thresh",
                        "label": "Probability threshold",
                        "hint": "Higher catches faint tracks; lower reduces false positives.",
                        "kind": "float",
                        "min": 0.0,
                        "max": 1.0,
                        "step": 0.01,
                        "decimals": 3,
                        "tip": "Base confidence threshold for candidate moving pixels.",
                    },
                    {
                        "key": "min_mean_prob",
                        "label": "Min mean probability",
                        "hint": "Average confidence required across the full candidate track.",
                        "kind": "float",
                        "min": 0.0,
                        "max": 1.0,
                        "step": 0.01,
                        "decimals": 3,
                        "tip": "Reject tracks whose average confidence is below this value.",
                    },
                    {
                        "key": "min_component_size",
                        "label": "Min component size",
                        "hint": "Remove tiny detections before linking into trajectories.",
                        "kind": "int",
                        "min": 1,
                        "max": 200,
                        "step": 1,
                        "tip": "Minimum connected-component size before tracking.",
                    },
                    {
                        "key": "min_t_span",
                        "label": "Min time span (frames)",
                        "hint": "Reject trajectories that last fewer than this many frames.",
                        "kind": "int",
                        "min": 2,
                        "max": 400,
                        "step": 1,
                        "tip": "Drop short runs below this temporal span.",
                    },
                    {
                        "key": "min_len",
                        "label": "Min path length",
                        "hint": "Reject short paths even if confidence is high.",
                        "kind": "float",
                        "min": 1.0,
                        "max": 300.0,
                        "step": 0.1,
                        "decimals": 2,
                        "tip": "Minimum geometric path length for accepted tracks.",
                    },
                    {
                        "key": "max_tracks",
                        "label": "Max tracks",
                        "hint": "Limit total returned trajectories to avoid crowded output.",
                        "kind": "int",
                        "min": 1,
                        "max": 5000,
                        "step": 1,
                        "tip": "Hard cap on number of returned tracks.",
                    },
                    {
                        "key": "vmin",
                        "label": "Min velocity",
                        "unit_type": "px_per_frame",
                        "hint": "Ignore near-static runs below this speed.",
                        "kind": "float",
                        "min": 0.0,
                        "max": 30.0,
                        "step": 0.01,
                        "decimals": 3,
                        "tip": "Minimum per-step velocity used in candidate filtering.",
                    },
                ],
            ),
            (
                "Smoothing / Anchors",
                [
                    {
                        "key": "smooth_sigma_t",
                        "label": "Smooth sigma T",
                        "hint": "Temporal blur before tracing, helpful for tiny time gaps.",
                        "kind": "float",
                        "min": 0.0,
                        "max": 5.0,
                        "step": 0.01,
                        "decimals": 3,
                        "tip": "Temporal smoothing before extracting tracks.",
                    },
                    {
                        "key": "smooth_sigma_x",
                        "label": "Smooth sigma X",
                        "hint": "Spatial blur before tracing, reduces side-to-side jitter.",
                        "kind": "float",
                        "min": 0.0,
                        "max": 5.0,
                        "step": 0.01,
                        "decimals": 3,
                        "tip": "Spatial smoothing before extracting tracks.",
                    },
                    {
                        "key": "simplify_eps",
                        "label": "Anchor simplify epsilon",
                        "hint": "Simplify each path; higher values keep fewer anchors.",
                        "kind": "float",
                        "min": 0.0,
                        "max": 20.0,
                        "step": 0.05,
                        "decimals": 3,
                        "tip": "Higher values reduce anchor count by stronger simplification.",
                    },
                    {
                        "key": "max_anchors",
                        "label": "Max anchors per track",
                        "hint": "Cap anchor count per trajectory after simplification.",
                        "kind": "int",
                        "min": 2,
                        "max": 500,
                        "step": 1,
                        "tip": "Upper bound on anchors kept for each trajectory.",
                    },
                ],
            ),
            (
                "Merging / Identity",
                [
                    {
                        "key": "merge_gap_t",
                        "label": "Merge max temporal gap",
                        "unit_type": "frame",
                        "hint": "Allow merge across this temporal gap.",
                        "kind": "int",
                        "min": 0,
                        "max": 30,
                        "step": 1,
                        "tip": "Maximum frame gap allowed when bridging two fragments.",
                    },
                    {
                        "key": "merge_max_dx",
                        "label": "Merge max dx",
                        "unit_type": "px",
                        "hint": "Allow merge only when fragments are this close in x.",
                        "kind": "float",
                        "min": 0.0,
                        "max": 40.0,
                        "step": 0.05,
                        "decimals": 3,
                        "tip": "Maximum spatial offset for merging neighboring fragments.",
                    },
                    {
                        "key": "merge_min_bridge_prob",
                        "label": "Merge min bridge probability",
                        "hint": "Bridge pixels between fragments must exceed this confidence.",
                        "kind": "float",
                        "min": 0.0,
                        "max": 1.0,
                        "step": 0.01,
                        "decimals": 3,
                        "tip": "Required confidence along the bridge region.",
                    },
                    {
                        "key": "merge_min_bridge_coverage",
                        "label": "Merge min bridge coverage",
                        "hint": "Required bridge-pixel fraction above bridge confidence.",
                        "kind": "float",
                        "min": 0.0,
                        "max": 1.0,
                        "step": 0.01,
                        "decimals": 3,
                        "tip": "Fraction of bridge pixels that must pass bridge confidence.",
                    },
                    {
                        "key": "merge_min_embed_sim",
                        "label": "Merge min embedding similarity",
                        "hint": "Require similar identity embeddings before merging fragments.",
                        "kind": "float",
                        "min": -1.0,
                        "max": 1.0,
                        "step": 0.01,
                        "decimals": 3,
                        "tip": "Higher values enforce stronger identity consistency across merges.",
                    },
                    {
                        "key": "dedupe_max_mean_dx",
                        "label": "Dedupe max mean dx",
                        "unit_type": "px",
                        "hint": "Treat almost-overlapping paths as duplicates below this mean x separation.",
                        "kind": "float",
                        "min": 0.0,
                        "max": 20.0,
                        "step": 0.05,
                        "decimals": 3,
                        "tip": "Collapse near-identical tracks whose mean separation is below this value.",
                    },
                    {
                        "key": "dedupe_min_overlap_frac",
                        "label": "Dedupe min overlap fraction",
                        "hint": "Only dedupe when tracks overlap by at least this fraction.",
                        "kind": "float",
                        "min": 0.0,
                        "max": 1.0,
                        "step": 0.01,
                        "decimals": 3,
                        "tip": "Required overlap before dedupe is considered.",
                    },
                    {
                        "key": "min_directionality",
                        "label": "Min directionality",
                        "hint": "Filter zig-zag trajectories; 0 keeps all directions.",
                        "kind": "float",
                        "min": 0.0,
                        "max": 1.0,
                        "step": 0.01,
                        "decimals": 3,
                        "tip": "Lower values allow more diffusive or zig-zag trajectories.",
                    },
                    {
                        "key": "min_net_velocity",
                        "label": "Min net velocity",
                        "unit_type": "px_per_frame",
                        "hint": "Optional net drift-speed filter; leave 0 to disable.",
                        "kind": "float",
                        "min": 0.0,
                        "max": 30.0,
                        "step": 0.01,
                        "decimals": 3,
                        "tip": "Optional downstream velocity filter; 0 disables.",
                    },
                ],
            ),
        ]

    def _set_find_settings_widgets(self, widgets: dict, params: dict):
        for key, widget_info in widgets.items():
            if key not in params:
                continue
            widget = widget_info.get("widget") if isinstance(widget_info, dict) else widget_info
            to_display = widget_info.get("to_display") if isinstance(widget_info, dict) else None
            value = params.get(key)
            try:
                value = float(value)
            except Exception:
                continue
            if callable(to_display):
                value = float(to_display(value))
            if isinstance(widget, QSpinBox):
                widget.setValue(int(round(float(value))))
            else:
                widget.setValue(float(value))

    def open_find_settings_dialog(self):
        if not getattr(self, "debug_mode", False):
            return
        if not hasattr(self, "autopick_postprocess_params"):
            return

        pixel_size_nm = None
        frame_interval_ms = None
        try:
            px = float(getattr(self, "pixel_size", None))
            if np.isfinite(px) and px > 0.0:
                pixel_size_nm = px
        except Exception:
            pixel_size_nm = None
        try:
            dt = float(getattr(self, "frame_interval", None))
            if np.isfinite(dt) and dt > 0.0:
                frame_interval_ms = dt
        except Exception:
            frame_interval_ms = None

        has_scale = pixel_size_nm is not None and frame_interval_ms is not None
        um_per_px = (float(pixel_size_nm) / 1000.0) if has_scale else None
        sec_per_frame = (float(frame_interval_ms) / 1000.0) if has_scale else None

        def _unit_label(unit_type: str | None) -> str:
            if not unit_type:
                return ""
            if has_scale:
                return {
                    "px": "um",
                    "frame": "s",
                    "px_per_frame": "um/s",
                }.get(unit_type, "")
            return {
                "px": "px",
                "frame": "frames",
                "px_per_frame": "px/frame",
            }.get(unit_type, "")

        def _to_display(value: float, unit_type: str | None) -> float:
            v = float(value)
            if not has_scale or not unit_type:
                return v
            if unit_type == "px":
                return float(v * float(um_per_px))
            if unit_type == "frame":
                return float(v * float(sec_per_frame))
            if unit_type == "px_per_frame":
                return float(v * float(um_per_px) / float(sec_per_frame))
            return v

        def _to_internal(value: float, unit_type: str | None) -> float:
            v = float(value)
            if not has_scale or not unit_type:
                return v
            if unit_type == "px":
                return float(v / float(um_per_px))
            if unit_type == "frame":
                return float(v / float(sec_per_frame))
            if unit_type == "px_per_frame":
                return float(v * float(sec_per_frame) / float(um_per_px))
            return v

        dialog = QDialog(self)
        dialog.setWindowTitle("Edit Find settings")
        dialog.resize(760, 720)
        dialog.setMinimumWidth(700)
        dialog.setStyleSheet(
            """
            QLabel#findSettingsHelp,
            QLabel#findRowLabel,
            QLabel#findHintLabel,
            QWidget#findSettingsRow {
                background: transparent;
                background-color: transparent;
                border: none;
            }
            QLabel#findSettingsHelp,
            QLabel#findRowLabel {
                color: #111111;
            }
            QLabel#findHintLabel {
                color: #7E8792;
                padding: 0px;
            }
            QToolButton#findResetButton {
                background-color: transparent;
                border: 1px solid #C9D2DD;
                border-radius: 10px;
                padding: 0px;
            }
            QToolButton#findResetButton:hover {
                background-color: #EEF4FB;
                border-color: #8FAECD;
            }
            QToolButton#findResetButton:disabled {
                color: #A9B2BC;
                border-color: #D9DFE6;
            }
            """
        )
        layout = QVBoxLayout(dialog)

        if has_scale:
            help_text = (
                f"Adjust trajectory finding postprocessing parameters. "
                f"Scale-aware units enabled ({pixel_size_nm:.3g} nm/px, {frame_interval_ms:.3g} ms/frame); "
                f"values are converted internally to px and frames."
            )
        else:
            help_text = (
                "Adjust trajectory finding postprocessing parameters. "
                "Scale is not set, so values are shown in px, px/frame, and frames."
            )
        help_label = QLabel(help_text)
        help_label.setObjectName("findSettingsHelp")
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        scroll = QScrollArea(dialog)
        scroll.setWidgetResizable(True)
        content = QWidget(scroll)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(10)

        defaults_internal = dict(getattr(self, "autopick_default_postprocess_params", {}) or {})
        if not defaults_internal:
            defaults_internal = dict(self.autopick_postprocess_params)

        widgets = {}
        for section_name, specs in self._autopick_settings_specs():
            group = QGroupBox(section_name, content)
            form = QFormLayout(group)
            form.setLabelAlignment(Qt.AlignLeft)
            form.setFormAlignment(Qt.AlignTop)
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            form.setHorizontalSpacing(18)
            for spec in specs:
                key = spec["key"]
                unit_type = str(spec.get("unit_type", "")).strip() or None
                display_kind = str(spec["kind"])
                if has_scale and unit_type == "frame":
                    display_kind = "float"

                value_internal = float(self.autopick_postprocess_params.get(key, 0.0))
                value_display = float(_to_display(value_internal, unit_type))
                min_display = float(_to_display(float(spec["min"]), unit_type))
                max_display = float(_to_display(float(spec["max"]), unit_type))
                step_display = float(_to_display(float(spec["step"]), unit_type))

                if display_kind == "int":
                    box = QSpinBox(group)
                    box.setRange(int(round(min_display)), int(round(max_display)))
                    box.setSingleStep(max(1, int(round(step_display))))
                    box.setValue(int(round(value_display)))
                else:
                    box = QDoubleSpinBox(group)
                    decimals = int(spec.get("decimals", 3))
                    if has_scale and unit_type in {"frame", "px", "px_per_frame"}:
                        decimals = max(decimals, 4)
                    box.setDecimals(decimals)
                    box.setRange(float(min_display), float(max_display))
                    min_step = 10 ** (-decimals)
                    box.setSingleStep(max(float(step_display), float(min_step)))
                    box.setValue(float(value_display))
                box.setButtonSymbols(QAbstractSpinBox.NoButtons)
                box.setFixedWidth(92)
                box.setToolTip(str(spec.get("tip", "")))
                unit_lbl = _unit_label(unit_type)
                row_label_text = str(spec["label"])
                if unit_lbl:
                    row_label_text = f"{row_label_text} ({unit_lbl})"
                row_label = QLabel(row_label_text, group)
                row_label.setObjectName("findRowLabel")
                row_label.setStyleSheet("padding: 0px;")
                row_widget = QWidget(group)
                row_widget.setObjectName("findSettingsRow")
                row_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
                row_widget.setAutoFillBackground(False)
                row_widget.setAttribute(Qt.WA_TranslucentBackground, True)
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_layout.setSpacing(10)
                row_layout.addWidget(box, 0, Qt.AlignLeft | Qt.AlignVCenter)
                reset_btn = QtWidgets.QToolButton(row_widget)
                reset_btn.setObjectName("findResetButton")
                reset_btn.setIcon(dialog.style().standardIcon(QtWidgets.QStyle.SP_BrowserReload))
                reset_btn.setToolTip("Reset this setting to its default value")
                reset_btn.setAutoRaise(False)
                reset_btn.setCursor(Qt.PointingHandCursor)
                reset_btn.setFixedSize(22, 22)
                reset_btn.setIconSize(QSize(12, 12))
                row_layout.addWidget(reset_btn, 0, Qt.AlignLeft | Qt.AlignVCenter)
                hint_text = str(spec.get("hint") or "").strip()
                if unit_lbl:
                    if has_scale:
                        hint_text = f"{hint_text} Displayed in {unit_lbl}."
                    else:
                        hint_text = f"{hint_text} Unit: {unit_lbl}."
                if hint_text:
                    hint_label = QLabel(hint_text, row_widget)
                    hint_label.setObjectName("findHintLabel")
                    hint_label.setAutoFillBackground(False)
                    hint_label.setAttribute(Qt.WA_TranslucentBackground, True)
                    hint_label.setWordWrap(True)
                    hint_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
                    row_layout.addWidget(hint_label, 1)
                form.addRow(row_label, row_widget)
                widgets[key] = {
                    "widget": box,
                    "reset_button": reset_btn,
                    "kind": str(spec["kind"]),
                    "min": float(spec["min"]),
                    "max": float(spec["max"]),
                    "step": float(spec["step"]),
                    "to_display": (lambda v, ut=unit_type: _to_display(v, ut)),
                    "to_internal": (lambda v, ut=unit_type: _to_internal(v, ut)),
                }
            content_layout.addWidget(group)

        def _widget_internal_value(widget_info: dict) -> float:
            widget = widget_info.get("widget")
            if widget is None:
                return 0.0
            raw_value = float(widget.value())
            to_internal = widget_info.get("to_internal")
            return float(to_internal(raw_value)) if callable(to_internal) else raw_value

        def _set_widget_changed_style(widget: QWidget, changed: bool):
            if changed:
                widget.setStyleSheet(
                    """
                    QSpinBox, QDoubleSpinBox {
                        background-color: #FFF7C7;
                        border: 1px solid #D9C87A;
                        border-radius: 6px;
                        padding: 3px 2px 3px 3px;
                    }
                    """
                )
            else:
                widget.setStyleSheet(
                    """
                    QSpinBox, QDoubleSpinBox {
                        background-color: #FFFFFF;
                        border: 1px solid #CCCCCC;
                        border-radius: 6px;
                        padding: 3px 2px 3px 3px;
                    }
                    """
                )

        def _update_widget_changed_state(key: str):
            widget_info = widgets.get(key)
            if not isinstance(widget_info, dict):
                return
            widget = widget_info.get("widget")
            if widget is None:
                return
            reset_button = widget_info.get("reset_button")
            kind = str(widget_info.get("kind", "float"))
            step = float(widget_info.get("step", 0.01))
            default_internal = float(defaults_internal.get(key, self.autopick_postprocess_params.get(key, 0.0)))
            current_internal = _widget_internal_value(widget_info)
            if kind == "int":
                changed = int(round(current_internal)) != int(round(default_internal))
            else:
                tol = max(1e-9, 0.5 * step)
                changed = abs(current_internal - default_internal) > tol
            _set_widget_changed_style(widget, bool(changed))
            if reset_button is not None:
                reset_button.setEnabled(bool(changed))

        def _reset_widget_default(key: str):
            widget_info = widgets.get(key)
            if not isinstance(widget_info, dict):
                return
            default_value = defaults_internal.get(key, self.autopick_postprocess_params.get(key, 0.0))
            self._set_find_settings_widgets({key: widget_info}, {key: default_value})

        for key, widget_info in widgets.items():
            widget = widget_info.get("widget") if isinstance(widget_info, dict) else None
            if widget is None:
                continue
            reset_button = widget_info.get("reset_button") if isinstance(widget_info, dict) else None
            if isinstance(widget, QSpinBox):
                widget.valueChanged.connect(lambda _v, kk=key: _update_widget_changed_state(kk))
            else:
                widget.valueChanged.connect(lambda _v, kk=key: _update_widget_changed_state(kk))
            if reset_button is not None:
                reset_button.clicked.connect(lambda _checked=False, kk=key: _reset_widget_default(kk))
            _update_widget_changed_state(key)

        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        buttons_row = QHBoxLayout()
        reset_btn = QPushButton("Reset to defaults", dialog)
        buttons_row.addWidget(reset_btn)
        buttons_row.addStretch(1)
        box = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok, parent=dialog)
        ok_btn = box.button(QDialogButtonBox.Ok)
        if ok_btn is not None:
            ok_btn.setText("Apply")
        buttons_row.addWidget(box)
        layout.addLayout(buttons_row)

        def _reset_defaults():
            defaults = dict(getattr(self, "autopick_default_postprocess_params", {}) or {})
            if not defaults:
                defaults = dict(self.autopick_postprocess_params)
            self._set_find_settings_widgets(widgets, defaults)

        def _apply_and_close():
            for key, widget_info in widgets.items():
                widget = widget_info.get("widget") if isinstance(widget_info, dict) else widget_info
                kind = widget_info.get("kind") if isinstance(widget_info, dict) else ("int" if isinstance(widget, QSpinBox) else "float")
                to_internal = widget_info.get("to_internal") if isinstance(widget_info, dict) else None
                raw_value = float(widget.value())
                internal_value = float(to_internal(raw_value)) if callable(to_internal) else float(raw_value)
                if isinstance(widget_info, dict):
                    internal_value = float(
                        np.clip(
                            internal_value,
                            float(widget_info.get("min", internal_value)),
                            float(widget_info.get("max", internal_value)),
                        )
                    )
                if str(kind) == "int":
                    self.autopick_postprocess_params[key] = int(round(internal_value))
                else:
                    self.autopick_postprocess_params[key] = float(internal_value)
            self.flash_message("Find settings updated")
            dialog.accept()

        reset_btn.clicked.connect(_reset_defaults)
        box.rejected.connect(dialog.reject)
        box.accepted.connect(_apply_and_close)

        dialog.exec_()

    def _show_autopick_progress_dialog(self):
        self._close_autopick_progress_dialog()
        dialog = QProgressDialog("Running model...", "Cancel", 0, 0, self)
        dialog.setWindowTitle("Finding")
        dialog.setWindowModality(Qt.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.setValue(0)
        dialog.canceled.connect(self._request_autopick_cancel)
        dialog.show()
        self._autopick_progress_dialog = dialog

    def _close_autopick_progress_dialog(self):
        dialog = getattr(self, "_autopick_progress_dialog", None)
        if dialog is None:
            return
        try:
            dialog.canceled.disconnect(self._request_autopick_cancel)
        except Exception:
            pass
        dialog.close()
        dialog.deleteLater()
        self._autopick_progress_dialog = None

    def _request_autopick_cancel(self):
        if not self._autopick_running:
            return

        self._autopick_cancel_requested = True
        worker = getattr(self, "_autopick_worker", None)
        if worker is not None and hasattr(worker, "cancel"):
            worker.cancel()
        thread = getattr(self, "_autopick_thread", None)
        if thread is not None:
            thread.requestInterruption()

        dialog = getattr(self, "_autopick_progress_dialog", None)
        if dialog is not None:
            dialog.setLabelText("Canceling finding...")
            button = dialog.cancelButton()
            if button is not None:
                button.setEnabled(False)

    def _trackset_quality(self, tracks: list[dict]) -> float:
        return _trackset_quality_impl(tracks)

    def _merge_track_sets(self, track_sets: list[list[dict]], max_tracks: int) -> list[dict]:
        return _merge_track_sets_impl(track_sets, max_tracks, self.autopick_postprocess_params)

    def _postprocess_with_fallback(self, prob_move: np.ndarray, embedding_map: np.ndarray | None = None):
        return _postprocess_with_fallback_impl(
            prob_move,
            self.autopick_postprocess_params,
            embedding_map=embedding_map,
        )

    def _select_autopick_model_path(self) -> str:
        start_dir = str(Path.home())
        if self.autopick_onnx_path:
            try:
                start_dir = str(Path(self.autopick_onnx_path).expanduser().resolve().parent)
            except Exception:
                start_dir = str(Path.home())

        onnx_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Auto-pick ONNX Model",
            start_dir,
            "ONNX files (*.onnx)",
        )
        if onnx_path:
            self.autopick_onnx_path = onnx_path
        return self.autopick_onnx_path

    def _current_kymo_raw(self):
        kymo_name = self.kymoCombo.currentText()
        if not kymo_name:
            return None, None
        kymo = self.kymographs.get(kymo_name)
        if kymo is None:
            return None, None
        # Auto-pick always uses the underlying unfiltered kymograph.
        # LoG is a visualization toggle only.
        return kymo_name, np.asarray(kymo)

    def on_autopick_clicked(self):
        if self._autopick_running:
            return
        if self._autopick_thread is not None and self._autopick_thread.isRunning():
            self.flash_message("Auto-pick already running")
            return
        if self.movie is None:
            self.flash_message("Load a movie first")
            return

        kymo_name, raw_kymo = self._current_kymo_raw()
        if raw_kymo is None:
            self.flash_message("No kymograph selected")
            return

        self.cancel_left_click_sequence()
        self._clear_autopick_preview_overlay()

        onnx_path = self.autopick_onnx_path
        if not onnx_path or not Path(onnx_path).exists():
            bundled_path = self._packaged_autopick_model_path()
            if bundled_path:
                onnx_path = bundled_path
                self.autopick_onnx_path = bundled_path
        if not onnx_path or not Path(onnx_path).exists():
            onnx_path = self._select_autopick_model_path()
        if not onnx_path:
            self.flash_message("Auto-pick canceled")
            return
        if not Path(onnx_path).exists():
            QMessageBox.warning(self, "", f"Model not found:\n{onnx_path}")
            return

        self._autopick_cancel_requested = False
        self._autopick_running = True
        if hasattr(self, "autopick_button"):
            self.autopick_button.setEnabled(False)
        self._show_autopick_progress_dialog()

        self._autopick_thread = QtCore.QThread()
        self._autopick_worker = AutoPickWorker(
            raw_kymo.copy(),
            onnx_path,
            dict(self.autopick_postprocess_params),
        )
        if self._autopick_cancel_requested:
            self._autopick_worker.cancel()
        self._autopick_worker.moveToThread(self._autopick_thread)

        self._autopick_thread.started.connect(self._autopick_worker.run)
        self._autopick_worker.stage_changed.connect(self._on_autopick_stage_changed)
        self._autopick_worker.finished.connect(self._on_autopick_finished)
        self._autopick_worker.error.connect(self._on_autopick_error)
        self._autopick_worker.canceled.connect(self._on_autopick_canceled)

        self._autopick_worker.finished.connect(self._autopick_thread.quit)
        self._autopick_worker.error.connect(self._autopick_thread.quit)
        self._autopick_worker.canceled.connect(self._autopick_thread.quit)
        self._autopick_thread.finished.connect(self._on_autopick_thread_finished)

        self._autopick_thread.start()

    def _finish_autopick_ui(self):
        self._autopick_running = False
        self._close_autopick_progress_dialog()
        if hasattr(self, "autopick_button"):
            self.autopick_button.setEnabled(True)

    def _cleanup_autopick_thread_objects(self, thread=None, worker=None):
        thread = thread if thread is not None else getattr(self, "_autopick_thread", None)
        worker = worker if worker is not None else getattr(self, "_autopick_worker", None)
        if thread is self._autopick_thread:
            self._autopick_thread = None
        if worker is self._autopick_worker:
            self._autopick_worker = None
        if worker is not None:
            try:
                worker.deleteLater()
            except Exception:
                pass
        if thread is not None:
            try:
                thread.deleteLater()
            except Exception:
                pass

    def _on_autopick_thread_finished(self):
        thread = self._autopick_thread
        worker = self._autopick_worker
        self._cleanup_autopick_thread_objects(thread=thread, worker=worker)
        if getattr(self, "_autopick_pending_close", False):
            self._autopick_pending_close = False
            QtCore.QTimer.singleShot(0, self.close)

    def _on_autopick_stage_changed(self, text: str):
        dialog = getattr(self, "_autopick_progress_dialog", None)
        if dialog is None:
            return
        try:
            dialog.setLabelText(str(text))
            QApplication.processEvents()
        except Exception:
            pass

    def _track_to_kymo_anchors(self, track: dict, num_frames: int) -> list[tuple[int, float, float]]:
        anchors = track.get("anchors", [])
        out = []
        for a in anchors:
            try:
                t = int(round(float(a.get("t"))))
                x = float(a.get("x"))
            except Exception:
                continue
            if t < 0 or t >= num_frames:
                continue
            y_disp = float((num_frames - 1) - t)
            out.append((t, x, y_disp))

        out.sort(key=lambda node: node[0])
        dedup = []
        seen = set()
        for node in out:
            if node[0] in seen:
                continue
            dedup.append(node)
            seen.add(node[0])
        return dedup

    def _prepare_autopick_anchor_sets(self, tracks: list[dict]) -> list[list[tuple[int, float, float]]]:
        num_frames = int(self.movie.shape[0]) if self.movie is not None else 0
        if num_frames <= 1:
            return []

        prepared_anchors = []
        for track in tracks:
            anchors = self._track_to_kymo_anchors(track, num_frames=num_frames)
            if len(anchors) >= 2:
                prepared_anchors.append(anchors)
        return prepared_anchors

    def _prepare_autopick_review_tracks(self, tracks: list[dict]) -> list[dict]:
        num_frames = int(self.movie.shape[0]) if self.movie is not None else 0
        if num_frames <= 1:
            return []

        prepared_tracks: list[dict] = []
        for track in tracks:
            anchors = self._track_to_kymo_anchors(track, num_frames=num_frames)
            if len(anchors) < 2:
                continue
            try:
                confidence = float(track.get("mean_prob", 0.0))
            except Exception:
                confidence = 0.0
            prepared_tracks.append(
                {
                    "anchors": anchors,
                    "confidence": float(np.clip(confidence, 0.0, 1.0)),
                    "raw_track": dict(track),
                }
            )
        return prepared_tracks

    def _prepare_autopick_path_sets(self, tracks: list[dict]) -> list[list[tuple[int, float, float]]]:
        num_frames = int(self.movie.shape[0]) if self.movie is not None else 0
        if num_frames <= 1:
            return []

        prepared_paths: list[list[tuple[int, float, float]]] = []
        for track in tracks:
            raw_path = list(track.get("path") or [])
            pts: list[tuple[int, float, float]] = []
            for node in raw_path:
                try:
                    if isinstance(node, dict):
                        t = int(round(float(node.get("t"))))
                        x = float(node.get("x"))
                    else:
                        t = int(round(float(node[0])))
                        x = float(node[1])
                except Exception:
                    continue
                if t < 0 or t >= num_frames:
                    continue
                y_disp = float((num_frames - 1) - t)
                pts.append((t, x, y_disp))
            pts.sort(key=lambda node: node[0])
            dedup: list[tuple[int, float, float]] = []
            seen = set()
            for node in pts:
                if node[0] in seen:
                    continue
                dedup.append(node)
                seen.add(node[0])
            if len(dedup) >= 2:
                prepared_paths.append(dedup)
        return prepared_paths

    def _current_kymo_review_display(self):
        canvas = getattr(self, "kymoCanvas", None)
        if canvas is not None and getattr(canvas, "_im", None) is not None:
            try:
                arr = np.asarray(canvas._im.get_array())
                cmap = canvas._im.get_cmap()
                vmin, vmax = canvas._im.get_clim()
                return arr, cmap, vmin, vmax
            except Exception:
                pass

        kymo_name = self.kymoCombo.currentText() if getattr(self, "kymoCombo", None) is not None else ""
        if not kymo_name:
            return None, None, None, None
        img = self.kymographs.get(kymo_name)
        if img is None:
            return None, None, None, None
        if getattr(self, "applylogfilter", False):
            img = self._get_log_kymograph(kymo_name, base=img)
        if img is None:
            return None, None, None, None
        arr = np.asarray(np.flipud(img))
        p15, p99 = np.percentile(arr, (15, 99))
        preview = np.clip((arr - p15) / max(p99 - p15, 1e-6), 0, 1) * 255.0
        cmap = "gray_r" if getattr(self, "inverted_cmap", True) else "gray"
        return preview.astype(np.uint8), cmap, 0, 255

    def _review_autopick_preview(
        self,
        prepared_tracks: list[dict],
        *,
        prob_move: np.ndarray | None = None,
        embedding_map: np.ndarray | None = None,
        postprocess_params: dict | None = None,
    ) -> list[list[tuple[int, float, float]]] | None:
        display_image, display_cmap, display_vmin, display_vmax = self._current_kymo_review_display()
        if display_image is None:
            out = []
            for track in prepared_tracks:
                anchors = track.get("anchors") or []
                if len(anchors) >= 2:
                    out.append([(int(t), float(x), float(y)) for t, x, y in anchors])
            return out

        dialog = _AutoPickReviewDialog(
            self,
            display_image=display_image,
            display_cmap=display_cmap,
            display_vmin=display_vmin,
            display_vmax=display_vmax,
            prepared_tracks=prepared_tracks,
            preview_colors=self._autopick_preview_colors([track.get("anchors") or [] for track in prepared_tracks]),
            prob_move=prob_move,
            embedding_map=embedding_map,
            postprocess_params=postprocess_params,
            parent=None,
        )
        try:
            if getattr(self, "windowHandle", None) is not None and self.windowHandle() is not None:
                dialog.windowHandle().setTransientParent(self.windowHandle())
        except Exception:
            pass
        app = QApplication.instance()
        if app is not None:
            try:
                dialog.setStyleSheet(app.styleSheet())
            except Exception:
                pass
        if dialog.exec_() != QDialog.Accepted:
            return None
        return dialog.edited_anchor_sets()

    def _autopick_path_mask(
        self,
        prepared_paths: list[list[tuple[int, float, float]]],
        shape: tuple[int, int] | None,
        *,
        x_radius: int = 1,
    ) -> np.ndarray | None:
        if not prepared_paths or shape is None or len(shape) != 2:
            return None
        tdim, xdim = int(shape[0]), int(shape[1])
        if tdim <= 0 or xdim <= 0:
            return None
        mask = np.zeros((tdim, xdim), dtype=np.uint8)
        xr = int(max(0, x_radius))
        for path in prepared_paths:
            for node in path:
                try:
                    t = int(node[0])
                    x = int(round(float(node[1])))
                except Exception:
                    continue
                if t < 0 or t >= tdim:
                    continue
                x0 = max(0, x - xr)
                x1 = min(xdim, x + xr + 1)
                if x0 < x1:
                    mask[t, x0:x1] = 1
        return mask

    def _autopick_tracks_match(self, a: dict, b: dict) -> bool:
        try:
            from kymo_autopick.postprocess import _track_overlap_stats
        except Exception:
            return False
        try:
            mean_dx, overlap, p90_dx, start_dx, end_dx, overlap_union, _span_ratio = _track_overlap_stats(a, b)
        except Exception:
            return False
        return bool(
            (overlap >= 0.45 and mean_dx <= 2.2 and p90_dx <= 3.4)
            or (overlap_union >= 0.35 and start_dx <= 2.8 and end_dx <= 2.8 and p90_dx <= 4.0)
        )

    def _autopick_partition_premerge_fates(
        self,
        premerge_tracks: list[dict],
        post_merge_tracks: list[dict],
        post_extend_tracks: list[dict],
        final_tracks: list[dict],
    ) -> dict[str, list[dict]]:
        fates = {
            "lost_before_post_merge": [],
            "lost_before_post_extend": [],
            "lost_before_final": [],
            "survived_final": [],
        }
        for tr in premerge_tracks or []:
            in_post_merge = any(self._autopick_tracks_match(tr, other) for other in post_merge_tracks or [])
            if not in_post_merge:
                fates["lost_before_post_merge"].append(tr)
                continue
            in_post_extend = any(self._autopick_tracks_match(tr, other) for other in post_extend_tracks or [])
            if not in_post_extend:
                fates["lost_before_post_extend"].append(tr)
                continue
            in_final = any(self._autopick_tracks_match(tr, other) for other in final_tracks or [])
            if not in_final:
                fates["lost_before_final"].append(tr)
                continue
            fates["survived_final"].append(tr)
        return fates

    def _clear_autopick_preview_overlay(self):
        artists = list(getattr(self, "_autopick_preview_artists", []) or [])
        self._autopick_preview_artists = []
        if not artists:
            return
        for artist in artists:
            try:
                artist.remove()
            except Exception:
                pass
        if getattr(self, "kymoCanvas", None) is not None:
            self.kymoCanvas.draw_idle()

    def _autopick_preview_colors(self, prepared_anchors: list[list[tuple[int, float, float]]]) -> list[str]:
        if not prepared_anchors:
            return []
        palette = [
            "#e41a1c",
            "#377eb8",
            "#4daf4a",
            "#984ea3",
            "#ff7f00",
            "#a65628",
            "#f781bf",
            "#66c2a5",
            "#fc8d62",
            "#8da0cb",
            "#e78ac3",
            "#a6d854",
            "#ffd92f",
            "#e5c494",
            "#1b9e77",
            "#d95f02",
            "#7570b3",
            "#e7298a",
            "#66a61e",
            "#e6ab02",
            "#a6761d",
            "#1f78b4",
            "#33a02c",
            "#fb9a99",
        ]
        seed = 2166136261
        for anchors in prepared_anchors:
            if not anchors:
                continue
            t0, x0, _ = anchors[0]
            t1, x1, _ = anchors[-1]
            seed = (seed ^ int(round(float(t0) * 17.0))) * 16777619
            seed = (seed ^ int(round(float(x0) * 31.0))) * 16777619
            seed = (seed ^ int(round(float(t1) * 43.0))) * 16777619
            seed = (seed ^ int(round(float(x1) * 59.0))) * 16777619
            seed &= 0xFFFFFFFF
        rng = np.random.default_rng(seed)
        shuffled = list(palette)
        rng.shuffle(shuffled)
        return [shuffled[i % len(shuffled)] for i in range(len(prepared_anchors))]

    def _show_autopick_preview_overlay(self, prepared_anchors: list[list[tuple[int, float, float]]]):
        self._clear_autopick_preview_overlay()
        if not prepared_anchors or getattr(self, "kymoCanvas", None) is None:
            return

        ax = self.kymoCanvas.ax
        artists = []
        preview_colors = self._autopick_preview_colors(prepared_anchors)
        for anchors, color in zip(prepared_anchors, preview_colors):
            xs = [float(x) for _t, x, _y in anchors]
            ys = [float(y) for _t, _x, y in anchors]
            (line,) = ax.plot(
                xs,
                ys,
                color=color,
                linewidth=1.45,
                linestyle="--",
                marker="o",
                markersize=3.6,
                markerfacecolor=color,
                markeredgecolor="#202020",
                markeredgewidth=0.8,
                alpha=0.95,
                zorder=12,
            )
            artists.append(line)

        self._autopick_preview_artists = artists
        self.kymoCanvas.draw_idle()

    def _autopick_mode_label(self, mode: str, source: str) -> str:
        mode_txt = str(mode or "strict").strip() or "strict"
        source_txt = str(source or "bright").strip() or "bright"
        return f"mode={mode_txt}, source={source_txt}"

    def _debug_track_count_text(self, debug_info: dict | None) -> str:
        if not isinstance(debug_info, dict):
            return ""
        counts = debug_info.get("counts")
        if not isinstance(counts, dict):
            return ""
        return (
            f"component={int(counts.get('component', 0))}, "
            f"skeleton={int(counts.get('skeleton', 0))}, "
            f"premerge={int(counts.get('premerge', 0))}, "
            f"post_merge={int(counts.get('post_merge', 0))}, "
            f"post_extend={int(counts.get('post_extend', 0))}, "
            f"final={int(counts.get('final', 0))}"
        )

    def _debug_reject_counts_text(self, reject_counts: dict | None) -> str:
        if not isinstance(reject_counts, dict):
            return ""
        order = [
            "components_seen",
            "accepted",
            "rescued_path_short",
            "reject_row_span_median",
            "reject_row_span_p90",
            "reject_path_short",
            "reject_path_short_single_row",
            "reject_path_short_multi_row",
            "reject_t_span",
            "reject_length",
            "reject_vmin",
            "reject_directionality",
            "reject_net_velocity",
            "reject_mean_prob",
            "reject_simplified_short",
        ]
        parts = [
            f"{key.replace('_', ' ')}={int(reject_counts.get(key, 0))}"
            for key in order
            if int(reject_counts.get(key, 0)) > 0
        ]
        return ", ".join(parts)

    def _debug_reject_text(self, debug_info: dict | None) -> str:
        if not isinstance(debug_info, dict):
            return ""
        return self._debug_reject_counts_text(debug_info.get("component_rejects"))

    def _debug_skeleton_reject_text(self, debug_info: dict | None) -> str:
        if not isinstance(debug_info, dict):
            return ""
        return self._debug_reject_counts_text(debug_info.get("skeleton_rejects"))

    def _debug_mask_text(self, debug_info: dict | None) -> str:
        if not isinstance(debug_info, dict):
            return ""
        stats = debug_info.get("mask_stats")
        if not isinstance(stats, dict):
            return ""
        return (
            f"high>={float(stats.get('high_thresh', 0.0)):.3f}, "
            f"seed>={float(stats.get('seed_thresh', 0.0)):.3f}, "
            f"low>={float(stats.get('low_thresh', 0.0)):.3f}, "
            f"high_px={int(stats.get('high_pixels', 0))}, "
            f"seed_px={int(stats.get('seed_pixels', 0))}, "
            f"mask_px={int(stats.get('mask_pixels', 0))}, "
            f"density={float(stats.get('mask_density', 0.0)):.4f}"
        )

    def _debug_stage_delta_text(self, debug_info: dict | None) -> str:
        if not isinstance(debug_info, dict):
            return ""
        counts = debug_info.get("counts")
        if not isinstance(counts, dict):
            return ""
        component = int(counts.get("component", 0))
        skeleton = int(counts.get("skeleton", 0))
        premerge = int(counts.get("premerge", 0))
        post_merge = int(counts.get("post_merge", 0))
        post_extend = int(counts.get("post_extend", 0))
        final = int(counts.get("final", 0))
        raw_total = component + skeleton
        parts = []
        if raw_total > 0:
            parts.append(f"raw={raw_total} -> premerge={premerge} ({premerge - raw_total:+d})")
        parts.append(f"premerge -> post_merge ({post_merge - premerge:+d})")
        parts.append(f"post_merge -> post_extend ({post_extend - post_merge:+d})")
        parts.append(f"post_extend -> final ({final - post_extend:+d})")
        return ", ".join(parts)

    def _track_stage_summary_line(self, label: str, tracks: list[dict] | None) -> str:
        if not tracks:
            return ""

        def _vals(key: str) -> list[float]:
            out = []
            for tr in tracks:
                try:
                    out.append(float(tr.get(key, 0.0)))
                except Exception:
                    continue
            return out

        tspans = _vals("t_span")
        lengths = _vals("length_px")
        probs = _vals("mean_prob")
        if not tspans and not lengths and not probs:
            return ""

        def _med(vals: list[float]) -> float:
            return float(np.median(np.asarray(vals, dtype=np.float32))) if vals else 0.0

        def _p10(vals: list[float]) -> float:
            return float(np.percentile(np.asarray(vals, dtype=np.float32), 10.0)) if vals else 0.0

        return (
            f"{label}: n={len(tracks)} "
            f"span_med={_med(tspans):.1f} "
            f"len_med={_med(lengths):.1f} "
            f"prob_med={_med(probs):.3f} "
            f"prob_p10={_p10(probs):.3f}"
        )

    def _debug_stage_stats_text(self, debug_info: dict | None) -> str:
        if not isinstance(debug_info, dict):
            return ""
        lines = []
        stage_keys = [
            ("component", "component_tracks"),
            ("skeleton", "skeleton_tracks"),
            ("premerge", "premerge_tracks"),
            ("post_merge", "post_merge_tracks"),
            ("post_extend", "post_extend_tracks"),
            ("final", "final_tracks"),
        ]
        for label, key in stage_keys:
            line = self._track_stage_summary_line(label, list(debug_info.get(key) or []))
            if line:
                lines.append(line)
        return "\n".join(lines)

    def _debug_reason_legend_text(self, debug_info: dict | None) -> str:
        if not isinstance(debug_info, dict):
            return ""
        legend = debug_info.get("reason_legend")
        if not isinstance(legend, dict):
            return ""
        items = []
        for code in sorted(int(k) for k in legend.keys()):
            label = str(legend.get(code, "")).replace("_", " ")
            items.append(f"{code}={label}")
        return ", ".join(items)

    def _autopick_params_debug_text(self) -> str:
        params = dict(getattr(self, "autopick_postprocess_params", {}) or {})
        if not params:
            return ""

        def _fmt(value):
            if isinstance(value, float):
                return f"{value:.6g}"
            return str(value)

        keys = sorted(params.keys())
        return ", ".join(f"{key}={_fmt(params[key])}" for key in keys)

    def _autopick_diagnostics_text(
        self,
        *,
        track_count: int,
        mode: str,
        source: str,
        prob_move: np.ndarray | None = None,
        debug_info: dict | None = None,
    ) -> str:
        lines = [
            f"Detection: {self._autopick_mode_label(mode, source)}",
            f"Found tracks: {int(track_count)}",
        ]
        if prob_move is not None:
            prob = np.asarray(prob_move, dtype=np.float32)
            lines.append(
                f"Probability map: max={float(np.max(prob)):.3f}, p99={float(np.percentile(prob, 99.0)):.3f}"
            )
        debug_txt = self._debug_track_count_text(debug_info)
        if debug_txt:
            lines.append(f"Stage counts: {debug_txt}")
        delta_txt = self._debug_stage_delta_text(debug_info)
        if delta_txt:
            lines.append(f"Stage deltas: {delta_txt}")
        mask_txt = self._debug_mask_text(debug_info)
        if mask_txt:
            lines.append(f"Mask: {mask_txt}")
        reject_txt = self._debug_reject_text(debug_info)
        if reject_txt:
            lines.append(f"Component gates: {reject_txt}")
        skeleton_reject_txt = self._debug_skeleton_reject_text(debug_info)
        if skeleton_reject_txt:
            lines.append(f"Skeleton gates: {skeleton_reject_txt}")
        stage_stats_txt = self._debug_stage_stats_text(debug_info)
        if stage_stats_txt:
            lines.append("Stage stats:")
            lines.extend(stage_stats_txt.splitlines())
        reason_legend_txt = self._debug_reason_legend_text(debug_info)
        if reason_legend_txt:
            lines.append(f"Reason legend: {reason_legend_txt}")
        params_txt = self._autopick_params_debug_text()
        if params_txt:
            lines.append(f"Find params: {params_txt}")
        return "\n".join(lines)

    def _confirm_autopick_preview(
        self,
        track_count: int,
        mode: str,
        source: str,
        prob_move: np.ndarray | None = None,
        prepared_anchors: list[list[tuple[int, float, float]]] | None = None,
        debug_info: dict | None = None,
    ) -> bool:
        from ..canvases._shared import FigureCanvas, Figure

        noun = "track" if track_count == 1 else "tracks"
        dialog = QDialog(None)
        dialog.setWindowTitle("Review found anchors")
        dialog.resize(1320, 860)
        dialog.setWindowModality(Qt.ApplicationModal)
        dialog.setWindowFlag(Qt.Window, True)
        sheet_flag = getattr(Qt, "Sheet", None)
        if sheet_flag is not None:
            dialog.setWindowFlag(sheet_flag, False)
        dialog.setWindowFlag(Qt.Dialog, True)
        dialog.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        layout = QVBoxLayout(dialog)

        try:
            if getattr(self, "windowHandle", None) is not None and self.windowHandle() is not None:
                dialog.windowHandle().setTransientParent(self.windowHandle())
        except Exception:
            pass

        title = QLabel(f"Found {track_count} candidate {noun}.", dialog)
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(title)

        body = QHBoxLayout()
        layout.addLayout(body, 1)

        info_text = (
            f"Detection: {self._autopick_mode_label(mode, source)}\n\n"
            "Dashed candidate anchors are overlaid on the current kymograph in different colors.\n"
            "Use the stage tabs on the right to inspect the probability map, mask, labels, "
            "skeleton, and track overlays at different points in postprocessing."
        )

        diagnostics_text = self._autopick_diagnostics_text(
            track_count=track_count,
            mode=mode,
            source=source,
            prob_move=prob_move,
            debug_info=debug_info,
        )

        left_scroll = QScrollArea(dialog)
        left_scroll.setWidgetResizable(True)
        left_scroll.setMinimumWidth(420)
        left_scroll.setMaximumWidth(520)
        left_panel = QWidget(left_scroll)
        left_layout = QVBoxLayout(left_panel)

        info = QLabel(info_text, left_panel)
        info.setWordWrap(True)
        left_layout.addWidget(info)

        diag_box = QtWidgets.QPlainTextEdit(left_panel)
        diag_box.setReadOnly(True)
        diag_box.setPlainText(diagnostics_text)
        diag_box.setMinimumHeight(520)
        diag_box.setStyleSheet(
            "QPlainTextEdit {"
            " background: #f3f6fa;"
            " border: 1px solid #c7d1dd;"
            " border-radius: 10px;"
            " padding: 8px;"
            " font-family: Menlo, Consolas, monospace;"
            " font-size: 12px;"
            "}"
        )
        left_layout.addWidget(diag_box, 1)
        left_layout.addStretch(1)
        left_scroll.setWidget(left_panel)
        body.addWidget(left_scroll, 0)

        tabs = QtWidgets.QTabWidget(dialog)
        body.addWidget(tabs, 1)

        stage_prob = np.asarray((debug_info or {}).get("prob_work") if isinstance(debug_info, dict) and debug_info.get("prob_work") is not None else prob_move, dtype=np.float32) if prob_move is not None or (isinstance(debug_info, dict) and debug_info.get("prob_work") is not None) else None
        mask_arr = np.asarray((debug_info or {}).get("mask_array"), dtype=np.float32) if isinstance(debug_info, dict) and debug_info.get("mask_array") is not None else None
        component_labels = np.asarray((debug_info or {}).get("component_labels"), dtype=np.int32) if isinstance(debug_info, dict) and debug_info.get("component_labels") is not None else None
        skeleton_arr = np.asarray((debug_info or {}).get("skeleton_array"), dtype=np.float32) if isinstance(debug_info, dict) and debug_info.get("skeleton_array") is not None else None
        skeleton_labels = np.asarray((debug_info or {}).get("skeleton_labels"), dtype=np.int32) if isinstance(debug_info, dict) and debug_info.get("skeleton_labels") is not None else None
        component_reason_map = np.asarray((debug_info or {}).get("component_reason_map"), dtype=np.int32) if isinstance(debug_info, dict) and debug_info.get("component_reason_map") is not None else None
        skeleton_reason_map = np.asarray((debug_info or {}).get("skeleton_reason_map"), dtype=np.int32) if isinstance(debug_info, dict) and debug_info.get("skeleton_reason_map") is not None else None
        reason_legend_txt = self._debug_reason_legend_text(debug_info)

        component_anchors = self._prepare_autopick_anchor_sets(list((debug_info or {}).get("component_tracks") or []))
        skeleton_anchors = self._prepare_autopick_anchor_sets(list((debug_info or {}).get("skeleton_tracks") or []))
        premerge_tracks = list((debug_info or {}).get("premerge_tracks") or [])
        post_merge_tracks = list((debug_info or {}).get("post_merge_tracks") or [])
        post_extend_tracks = list((debug_info or {}).get("post_extend_tracks") or [])
        final_tracks_dbg = list((debug_info or {}).get("final_tracks") or [])
        premerge_anchors = self._prepare_autopick_anchor_sets(premerge_tracks)
        post_merge_anchors = self._prepare_autopick_anchor_sets(post_merge_tracks)
        post_extend_anchors = self._prepare_autopick_anchor_sets(post_extend_tracks)
        component_paths = self._prepare_autopick_path_sets(list((debug_info or {}).get("component_tracks") or []))
        skeleton_paths = self._prepare_autopick_path_sets(list((debug_info or {}).get("skeleton_tracks") or []))
        premerge_paths = self._prepare_autopick_path_sets(premerge_tracks)
        final_paths = self._prepare_autopick_path_sets(final_tracks_dbg)
        base_shape = tuple(stage_prob.shape) if stage_prob is not None and np.asarray(stage_prob).ndim == 2 else None
        component_path_mask = self._autopick_path_mask(component_paths, base_shape)
        skeleton_path_mask = self._autopick_path_mask(skeleton_paths, base_shape)
        premerge_path_mask = self._autopick_path_mask(premerge_paths, base_shape)
        final_path_mask = self._autopick_path_mask(final_paths, base_shape)
        premerge_fates = self._autopick_partition_premerge_fates(
            premerge_tracks,
            post_merge_tracks,
            post_extend_tracks,
            final_tracks_dbg,
        )
        fate_premerge_paths = self._prepare_autopick_path_sets(premerge_fates["lost_before_post_merge"])
        fate_postextend_paths = self._prepare_autopick_path_sets(premerge_fates["lost_before_post_extend"])
        fate_finaldrop_paths = self._prepare_autopick_path_sets(premerge_fates["lost_before_final"])
        fate_survivor_paths = self._prepare_autopick_path_sets(premerge_fates["survived_final"])

        def _plot_anchor_sets(ax, anchor_sets, *, color, linewidth=1.1, alpha=0.9):
            for anchors in anchor_sets or []:
                xs = [float(x) for t, x, y in anchors]
                ts = [float(t) for t, x, y in anchors]
                ax.plot(xs, ts, color=color, linewidth=linewidth, alpha=alpha)

        def _add_stage_tab(
            label: str,
            image,
            *,
            title_text: str,
            cmap="magma",
            vmin=None,
            vmax=None,
            mask_zeros=False,
            overlays=None,
            add_colorbar=False,
            footer_text: str = "",
        ):
            if image is None:
                return
            widget = QWidget(dialog)
            tab_layout = QVBoxLayout(widget)
            fig = Figure(figsize=(6.6, 5.8), constrained_layout=True)
            canvas = FigureCanvas(fig)
            ax = fig.add_subplot(111)
            arr = np.asarray(image)
            if mask_zeros:
                arr = np.ma.masked_where(arr <= 0, arr)
            im = ax.imshow(
                arr,
                cmap=cmap,
                origin="upper",
                aspect="auto",
                interpolation="nearest",
                vmin=vmin,
                vmax=vmax,
            )
            if add_colorbar:
                fig.colorbar(im, ax=ax, fraction=0.046, pad=0.03)
            if overlays:
                for overlay in overlays:
                    _plot_anchor_sets(
                        ax,
                        overlay.get("anchors"),
                        color=str(overlay.get("color", "#1f8f3a")),
                        linewidth=float(overlay.get("linewidth", 1.1)),
                        alpha=float(overlay.get("alpha", 0.9)),
                    )
            ax.set_title(title_text)
            ax.set_xlabel("x (px)")
            ax.set_ylabel("time (frames)")
            tab_layout.addWidget(canvas, 1)
            if footer_text:
                footer = QLabel(footer_text, widget)
                footer.setWordWrap(True)
                footer.setStyleSheet("color: #666666; font-size: 11px;")
                tab_layout.addWidget(footer)
            tabs.addTab(widget, label)

        _add_stage_tab(
            "Probability",
            stage_prob,
            title_text="Probability map | premerge=light green, final=green",
            cmap="magma",
            vmin=0.0,
            vmax=1.0,
            overlays=[
                {"anchors": premerge_anchors, "color": "#8fd19e", "linewidth": 0.9, "alpha": 0.55},
                {"anchors": prepared_anchors or [], "color": "#1f8f3a", "linewidth": 1.2, "alpha": 0.95},
            ],
            add_colorbar=True,
        )

        _add_stage_tab(
            "Mask",
            mask_arr,
            title_text="Binary mask | component=yellow, final=green",
            cmap="gray",
            vmin=0.0,
            vmax=1.0,
            overlays=[
                {"anchors": component_anchors, "color": "#f2c94c", "linewidth": 0.9, "alpha": 0.75},
                {"anchors": prepared_anchors or [], "color": "#1f8f3a", "linewidth": 1.2, "alpha": 0.95},
            ],
        )

        _add_stage_tab(
            "Components",
            component_labels,
            title_text="Connected components | final=green",
            cmap="nipy_spectral",
            mask_zeros=True,
            overlays=[
                {"anchors": prepared_anchors or [], "color": "#1f8f3a", "linewidth": 1.15, "alpha": 0.95},
            ],
        )

        _add_stage_tab(
            "Skeleton",
            skeleton_arr,
            title_text="Skeleton mask | skeleton=cyan, final=green",
            cmap="gray",
            vmin=0.0,
            vmax=1.0,
            overlays=[
                {"anchors": skeleton_anchors, "color": "#56ccf2", "linewidth": 0.9, "alpha": 0.80},
                {"anchors": prepared_anchors or [], "color": "#1f8f3a", "linewidth": 1.2, "alpha": 0.95},
            ],
        )

        _add_stage_tab(
            "Skeleton Labels",
            skeleton_labels,
            title_text="Skeleton components | final=green",
            cmap="nipy_spectral",
            mask_zeros=True,
            overlays=[
                {"anchors": prepared_anchors or [], "color": "#1f8f3a", "linewidth": 1.15, "alpha": 0.95},
            ],
        )

        _add_stage_tab(
            "Component Paths",
            component_path_mask,
            title_text="Accepted component paths | premerge=yellow, final=green",
            cmap="gray",
            vmin=0.0,
            vmax=1.0,
            overlays=[
                {"anchors": component_paths, "color": "#56ccf2", "linewidth": 1.0, "alpha": 0.80},
                {"anchors": premerge_paths, "color": "#f2c94c", "linewidth": 0.95, "alpha": 0.72},
                {"anchors": final_paths or prepared_anchors or [], "color": "#1f8f3a", "linewidth": 1.15, "alpha": 0.95},
            ],
            footer_text="Only traced accepted paths are shown here; this excludes the rest of each connected label.",
        )

        _add_stage_tab(
            "Skeleton Paths",
            skeleton_path_mask,
            title_text="Accepted skeleton paths | premerge=yellow, final=green",
            cmap="gray",
            vmin=0.0,
            vmax=1.0,
            overlays=[
                {"anchors": skeleton_paths, "color": "#56ccf2", "linewidth": 1.0, "alpha": 0.80},
                {"anchors": premerge_paths, "color": "#f2c94c", "linewidth": 0.95, "alpha": 0.72},
                {"anchors": final_paths or prepared_anchors or [], "color": "#1f8f3a", "linewidth": 1.15, "alpha": 0.95},
            ],
            footer_text="Only traced accepted paths are shown here; if a blue label in Skeleton Reasons has no cyan here, the tracer followed a different corridor in the same label.",
        )

        _add_stage_tab(
            "Premerge Fate",
            stage_prob,
            title_text="red=lost before post_merge | orange=lost before post_extend | magenta=lost before final | green=final",
            cmap="magma",
            vmin=0.0,
            vmax=1.0,
            overlays=[
                {"anchors": fate_premerge_paths, "color": "#d64545", "linewidth": 1.0, "alpha": 0.82},
                {"anchors": fate_postextend_paths, "color": "#f2994a", "linewidth": 1.0, "alpha": 0.82},
                {"anchors": fate_finaldrop_paths, "color": "#bb6bd9", "linewidth": 1.0, "alpha": 0.82},
                {"anchors": fate_survivor_paths, "color": "#1f8f3a", "linewidth": 1.15, "alpha": 0.95},
            ],
            add_colorbar=True,
            footer_text="This classifies each premerge track by the first later stage where it disappears.",
        )

        _add_stage_tab(
            "Component Reasons",
            component_reason_map,
            title_text="Component reject reasons | accepted path=cyan, premerge=yellow, final=green",
            cmap="tab20",
            mask_zeros=True,
            overlays=[
                {"anchors": component_paths, "color": "#56ccf2", "linewidth": 1.0, "alpha": 0.80},
                {"anchors": premerge_paths, "color": "#f2c94c", "linewidth": 0.95, "alpha": 0.72},
                {"anchors": final_paths or prepared_anchors or [], "color": "#1f8f3a", "linewidth": 1.15, "alpha": 0.95},
            ],
            add_colorbar=True,
            footer_text=f"{reason_legend_txt}\nBlue background = whole accepted label region; cyan line = traced accepted path within that label.",
        )

        _add_stage_tab(
            "Skeleton Reasons",
            skeleton_reason_map,
            title_text="Skeleton reject reasons | accepted path=cyan, premerge=yellow, final=green",
            cmap="tab20",
            mask_zeros=True,
            overlays=[
                {"anchors": skeleton_paths, "color": "#56ccf2", "linewidth": 1.0, "alpha": 0.80},
                {"anchors": premerge_paths, "color": "#f2c94c", "linewidth": 0.95, "alpha": 0.72},
                {"anchors": final_paths or prepared_anchors or [], "color": "#1f8f3a", "linewidth": 1.15, "alpha": 0.95},
            ],
            add_colorbar=True,
            footer_text=f"{reason_legend_txt}\nBlue background = whole accepted label region; cyan line = traced accepted path within that label.",
        )

        _add_stage_tab(
            "Stages",
            stage_prob,
            title_text="premerge=yellow | post_merge=orange | post_extend=light green | final=green",
            cmap="magma",
            vmin=0.0,
            vmax=1.0,
            overlays=[
                {"anchors": premerge_anchors, "color": "#f2c94c", "linewidth": 0.8, "alpha": 0.55},
                {"anchors": post_merge_anchors, "color": "#f2994a", "linewidth": 0.9, "alpha": 0.65},
                {"anchors": post_extend_anchors, "color": "#8fd19e", "linewidth": 1.0, "alpha": 0.70},
                {"anchors": prepared_anchors or [], "color": "#1f8f3a", "linewidth": 1.2, "alpha": 0.95},
            ],
            add_colorbar=True,
        )

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel | QDialogButtonBox.Ok, parent=dialog)
        copy_button = buttons.addButton("Copy diagnostics", QDialogButtonBox.ActionRole)
        add_button = buttons.button(QDialogButtonBox.Ok)
        cancel_button = buttons.button(QDialogButtonBox.Cancel)
        if add_button is not None:
            add_button.setText("Add trajectories")
            add_button.setDefault(True)
        if cancel_button is not None:
            cancel_button.setText("Discard")
        if copy_button is not None:
            def _copy_diagnostics():
                QApplication.clipboard().setText(
                    self._autopick_diagnostics_text(
                        track_count=track_count,
                        mode=mode,
                        source=source,
                        prob_move=prob_move,
                        debug_info=debug_info,
                    )
                )
                copy_button.setText("Copied")
                QtCore.QTimer.singleShot(1200, lambda: copy_button.setText("Copy diagnostics"))

            copy_button.clicked.connect(_copy_diagnostics)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        return dialog.exec_() == QDialog.Accepted

    def _add_autopick_anchor_sets_to_trajectories(
        self,
        prepared_anchors: list[list[tuple[int, float, float]]],
    ) -> tuple[int, bool]:
        if not prepared_anchors:
            return 0, False

        kymo_name = self.kymoCombo.currentText()
        if not kymo_name:
            return 0, False

        roi_key = None
        info = self.kymo_roi_map.get(kymo_name) if hasattr(self, "kymo_roi_map") else None
        if isinstance(info, dict):
            roi_key = info.get("roi")
        if not roi_key:
            roi_key = self.roiCombo.currentText() if self.roiCombo.count() > 0 else kymo_name
        roi = self.rois.get(roi_key)
        if roi is None:
            return 0, False

        before = len(self.trajectoryCanvas.trajectories)
        prev_suppress = getattr(self, "_suppress_internal_progress", False)
        self._suppress_internal_progress = True
        progress = QProgressDialog(
            "Adding trajectories...",
            "Cancel",
            0,
            len(prepared_anchors),
            self,
        )
        progress.setWindowTitle("Finding")
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setAutoClose(False)
        progress.setAutoReset(False)
        progress.show()
        canceled = False

        try:
            for idx, anchors in enumerate(prepared_anchors, start=1):
                if progress.wasCanceled():
                    canceled = True
                    break
                self.analysis_points = []
                self.analysis_anchors = anchors
                self.analysis_roi = roi
                self.analysis_click_source_override = "tracy-ai"
                try:
                    self.endKymoClickSequence()
                finally:
                    self.analysis_click_source_override = None
                progress.setValue(idx)
                QApplication.processEvents()
        finally:
            if not canceled:
                progress.setValue(len(prepared_anchors))
            progress.close()
            progress.deleteLater()
            self._suppress_internal_progress = prev_suppress

        added = len(self.trajectoryCanvas.trajectories) - before
        return max(0, added), canceled

    def _add_autopick_tracks_to_trajectories(self, tracks: list[dict]) -> tuple[int, bool]:
        return self._add_autopick_anchor_sets_to_trajectories(
            self._prepare_autopick_anchor_sets(tracks)
        )

    def _on_autopick_finished(self, result):
        try:
            if self._autopick_cancel_requested:
                self.flash_message("Finding canceled")
                return

            chosen_prob = None
            embedding_map = None
            tracks = []
            mode = "strict"
            source = "bright"
            debug_info = None
            if isinstance(result, dict):
                prob_value = result.get("prob_move")
                if prob_value is not None:
                    chosen_prob = np.asarray(prob_value, dtype=np.float32)
                embedding_value = result.get("embedding_map")
                if embedding_value is not None:
                    embedding_map = np.asarray(embedding_value, dtype=np.float32)
                tracks = list(result.get("tracks") or [])
                mode = str(result.get("mode") or mode)
                source = str(result.get("source") or source)
                if isinstance(result.get("debug"), dict):
                    debug_info = dict(result.get("debug"))

            if chosen_prob is None:
                raise RuntimeError("Auto-pick returned no probability map")

            self._close_autopick_progress_dialog()
            self.cached_prob_move = chosen_prob
            self.cached_autopick_embedding_map = embedding_map
            self._autopick_tracks = tracks
            prepared_anchors = self._prepare_autopick_anchor_sets(tracks)

            if prepared_anchors:
                if getattr(self, "debug_mode", False):
                    self._show_autopick_preview_overlay(prepared_anchors)
                    QApplication.processEvents()
                    confirmed = self._confirm_autopick_preview(
                        len(prepared_anchors),
                        mode,
                        source,
                        prob_move=chosen_prob,
                        prepared_anchors=prepared_anchors,
                        debug_info=debug_info,
                    )
                    self._clear_autopick_preview_overlay()
                    if not confirmed:
                        noun = "track" if len(prepared_anchors) == 1 else "tracks"
                        diag = self._autopick_mode_label(mode, source)
                        self.flash_message(f"Found {len(prepared_anchors)} candidate {noun} [{diag}]; nothing added")
                        return
                review_tracks = self._prepare_autopick_review_tracks(tracks)
                edited_anchors = self._review_autopick_preview(
                    review_tracks,
                    prob_move=chosen_prob,
                    embedding_map=embedding_map,
                    postprocess_params=self.autopick_postprocess_params,
                )
                if edited_anchors is None:
                    noun = "track" if len(prepared_anchors) == 1 else "tracks"
                    if getattr(self, "debug_mode", False):
                        diag = self._autopick_mode_label(mode, source)
                        self.flash_message(f"Found {len(prepared_anchors)} candidate {noun} [{diag}]; nothing added")
                    else:
                        self.flash_message(f"Found {len(prepared_anchors)} candidate {noun}; nothing added")
                    return
                prepared_anchors = list(edited_anchors)
                if not prepared_anchors:
                    self.flash_message("No found items remain after review; nothing added")
                    return

            added, apply_canceled = self._add_autopick_anchor_sets_to_trajectories(prepared_anchors)

            if apply_canceled:
                if added > 0:
                    noun = "trajectory" if added == 1 else "trajectories"
                    diag = self._autopick_mode_label(mode, source)
                    self.flash_message(f"Finding stopped after adding {added} {noun} [{diag}]")
                else:
                    diag = self._autopick_mode_label(mode, source)
                    self.flash_message(f"Finding stopped before adding trajectories [{diag}]")
            elif added > 0:
                noun = "trajectory" if added == 1 else "trajectories"
                diag = self._autopick_mode_label(mode, source)
                self.flash_message(f"Auto-picked {added} {noun} [{diag}]")
            else:
                pmax = float(np.max(self.cached_prob_move))
                p99 = float(np.percentile(self.cached_prob_move, 99))
                diag = self._autopick_mode_label(mode, source)
                if tracks:
                    self.flash_message(
                        f"Found {len(tracks)} candidate tracks [{diag}], but none were added (max p={pmax:.2f}, p99={p99:.2f})"
                    )
                else:
                    self.flash_message(f"No tracks found [{diag}] (max p={pmax:.2f}, p99={p99:.2f})")

            if added > 0:
                self.kymoCanvas.draw_trajectories_on_kymo()
                self.kymoCanvas.draw_idle()
                self.movieCanvas.draw_trajectories_on_movie()
                self.movieCanvas.draw_idle()
        except Exception as exc:
            self._clear_autopick_preview_overlay()
            if self._autopick_cancel_requested:
                self.flash_message("Finding canceled")
                return
            QMessageBox.warning(self, "", f"Auto-pick failed:\n{exc}")
        finally:
            self._finish_autopick_ui()

    def _on_autopick_error(self, message: str):
        self._clear_autopick_preview_overlay()
        if self._autopick_cancel_requested:
            self.flash_message("Finding canceled")
            self._finish_autopick_ui()
            return
        QMessageBox.warning(self, "", f"Auto-pick failed:\n{message}")
        self._finish_autopick_ui()

    def _on_autopick_canceled(self):
        self._clear_autopick_preview_overlay()
        self.flash_message("Finding canceled")
        self._finish_autopick_ui()

    def shutdown_autopick_thread(self, timeout_ms: int = 3000) -> bool:
        self._autopick_shutdown = True
        thread = self._autopick_thread
        if thread is None:
            return True
        worker = self._autopick_worker
        if thread.isRunning():
            self._autopick_cancel_requested = True
            if worker is not None and hasattr(worker, "cancel"):
                try:
                    worker.cancel()
                except Exception:
                    pass
            try:
                thread.requestInterruption()
            except Exception:
                pass
            thread.quit()
            if timeout_ms > 0 and thread.wait(int(timeout_ms)):
                self._cleanup_autopick_thread_objects(thread=thread, worker=worker)
                return True
            # Last-resort stop attempt. If it still doesn't stop, keep references
            # alive so Qt does not destroy a running thread.
            thread.terminate()
            if thread.wait(1500):
                self._cleanup_autopick_thread_objects(thread=thread, worker=worker)
                return True
            return False
        self._cleanup_autopick_thread_objects(thread=thread, worker=worker)
        return True
