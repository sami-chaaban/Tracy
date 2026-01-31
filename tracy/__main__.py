#!/usr/bin/env python3

import glob
import os
import sys
import tempfile
import threading
import time
from PyQt5.QtCore import QFile, QTextStream, Qt, QTimer
from PyQt5.QtGui import QFontDatabase, QIcon
from PyQt5.QtWidgets import QApplication, QDialog, QLabel, QProgressBar, QVBoxLayout, QMessageBox


def _is_writable_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
    except Exception:
        return False
    if not os.path.isdir(path):
        return False
    if not os.access(path, os.W_OK):
        return False
    test_file = os.path.join(path, ".tracy_write_test")
    try:
        with open(test_file, "w") as handle:
            handle.write("ok")
        os.remove(test_file)
    except Exception:
        return False
    return True


def _pick_mpl_cache_dir():
    override = os.environ.get("TRACY_MPLCONFIGDIR")
    if override and _is_writable_dir(override):
        return override

    home = os.path.expanduser("~")
    tmp_base = tempfile.gettempdir()
    tmp_path = os.path.join(tmp_base, "tracy-matplotlib")
    if _is_writable_dir(tmp_path):
        return tmp_path

    if sys.platform == "darwin":
        candidates = [
            os.path.join(home, "Library", "Application Support", "Tracy", "matplotlib"),
            os.path.join(home, "Library", "Caches", "Tracy", "matplotlib"),
            os.path.join(home, ".cache", "tracy", "matplotlib"),
        ]
    else:
        candidates = [
            os.path.join(home, ".cache", "tracy", "matplotlib"),
            os.path.join(home, ".matplotlib"),
        ]

    for path in candidates:
        if _is_writable_dir(path):
            return path

    return tmp_path


def _configure_matplotlib_cache():
    cache_dir = _pick_mpl_cache_dir()
    os.environ["MPLCONFIGDIR"] = cache_dir

def _startup_logger():
    enabled = os.environ.get("TRACY_STARTUP_TRACE", "").lower() in ("1", "true", "yes")
    t0 = time.perf_counter()
    def _log(message):
        if enabled:
            dt = time.perf_counter() - t0
            print(f"[startup] {dt:7.3f}s {message}", flush=True)
    return _log

class _StartupDialog(QDialog):
    def __init__(self):
        super().__init__(None)
        self.setWindowTitle("Starting Tracy")
        self.setWindowFlags(Qt.Dialog | Qt.CustomizeWindowHint | Qt.WindowTitleHint)
        self.setModal(False)
        self.setFixedWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        self._label = QLabel("Starting Tracy…")
        self._label.setWordWrap(True)
        layout.addWidget(self._label)

        self._progress = QProgressBar()
        self._progress.setRange(0, 0)  # indeterminate
        layout.addWidget(self._progress)

    def set_message(self, text: str):
        self._label.setText(text)

def _ensure_mpl_font_cache(log, mpl, font_manager):
    try:
        cache_dir = mpl.get_cachedir()
        os.makedirs(cache_dir, exist_ok=True)
    except Exception as exc:
        log(f"matplotlib cache dir error: {exc}")
        return

    existing = sorted(glob.glob(os.path.join(cache_dir, "fontlist-v*.json")))
    if existing:
        log(f"font cache present: {existing[-1]}")
        return

    version = getattr(getattr(font_manager, "fontManager", None), "_version", None)
    if version is None:
        version = getattr(font_manager, "_fmcache", None)
    if version is None:
        version = mpl.__version__.split(".")[0]

    cache_file = os.path.join(cache_dir, f"fontlist-v{version}.json")
    try:
        font_manager.json_dump(font_manager.fontManager, cache_file)
        log(f"font cache written: {cache_file}")
    except Exception as exc:
        log(f"font cache write failed: {exc}")

def main():
    log = _startup_logger()
    log("main start")
    _configure_matplotlib_cache()
    log(f"matplotlib cache configured: {os.environ.get('MPLCONFIGDIR')}")

    # Must be set before QApplication is created.
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)

    app = QApplication(sys.argv)
    log("QApplication created")

    startup = _StartupDialog()
    startup.set_message("Starting Tracy…")
    startup.show()

    result = {}
    status = {"stage": None}

    def _load_background():
        try:
            result["stage"] = "matplotlib"
            import matplotlib as mpl
            from matplotlib import font_manager

            mpl.rcParams.update({
                'font.family': ['Figtree', 'Helvetica', 'Arial', 'sans-serif'],  # fallback list
                'font.size': 14,
            })
            log(f"matplotlib imported/rcParams set (cachedir={mpl.get_cachedir()})")

            result["stage"] = "fonts"
            font_dir = resource_path("fonts")
            font_files = [os.path.join(font_dir, fname)
                          for fname in ("Figtree-Regular.ttf", "Figtree-Bold.ttf")]

            for fpath in font_files:
                font_manager.fontManager.addfont(fpath)

            mpl.rcParams['font.family'] = 'Figtree'
            log("matplotlib fonts registered")
            _ensure_mpl_font_cache(log, mpl, font_manager)

            result["stage"] = "navigator"
            from tracy.navigator import KymographNavigator
            result["navigator_cls"] = KymographNavigator
        except Exception as exc:
            result["error"] = exc

    def _update_startup_text():
        stage = result.get("stage")
        if stage == status["stage"]:
            return
        status["stage"] = stage
        if stage == "matplotlib":
            startup.set_message("Loading graphics…")
        elif stage == "fonts":
            startup.set_message("Preparing fonts…")
        elif stage == "navigator":
            startup.set_message("Loading tools and workspace…")

    def _finalize_ui():
        app.setApplicationName("Tracy")

        app.setWindowIcon(QIcon(resource_path("icons/tracyicon.png")))
        log("window icon set")

        # For below, need qss font to be font-family: Helvetica Neue Lt Std, Helvetica Neue, Helvetica, Arial, sans-serif;
        # also, package data needs to include the fonts
        font_db = QFontDatabase()
        family = None
        for fname in ("Figtree-Regular.ttf", "Figtree-Bold.ttf"):
            path = resource_path(f"fonts/{fname}")
            fid = font_db.addApplicationFont(path)
            if fid == -1:
                print(f"⚠️ Failed to load font: {path}")
            else:
                fams = font_db.applicationFontFamilies(fid)
                if fams:
                    family = fams[0]
        if family:
            default_font = font_db.font(family, "Regular", 14)
            app.setFont(default_font)

        log("fonts loaded")

        qss_path = resource_path('style.qss')
        app.setStyleSheet(load_stylesheet(qss_path))
        log("stylesheet applied")

    def _poll_import():
        _update_startup_text()
        if "error" in result:
            err = result["error"]
            log(f"navigator import failed: {err}")
            QMessageBox.critical(None, "Tracy failed to start", str(err))
            QApplication.instance().quit()
            return
        if "navigator_cls" not in result:
            QTimer.singleShot(100, _poll_import)
            return

        log("navigator module imported")
        startup.set_message("Preparing workspace…")
        _finalize_ui()
        navigator = result["navigator_cls"]()
        log("navigator initialized")

        navigator.showMaximized()
        log("showMaximized called")

        navigator.show()
        log("show called")

        startup.close()

    def _start_background():
        startup.set_message("Loading graphics…")
        threading.Thread(target=_load_background, daemon=True).start()
        QTimer.singleShot(100, _poll_import)

    QTimer.singleShot(0, _start_background)

    sys.exit(app.exec_())

def resource_path(relative):
    if getattr(sys, "frozen", False):
        base = os.path.join(sys._MEIPASS, "tracy")
        candidate = os.path.join(base, relative)
        if not os.path.exists(candidate):
            base = sys._MEIPASS
    else:
        base = os.path.dirname(__file__)
    return os.path.join(base, relative)

def load_stylesheet(path):
    file = QFile(path)
    file.open(QFile.ReadOnly | QFile.Text)
    stream = QTextStream(file)
    return stream.readAll()

if __name__ == "__main__":
    main()
