#!/usr/bin/env python3

import sys, os
import numpy as np
import matplotlib as mpl
from matplotlib import font_manager
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QFile, QTextStream
from PyQt5.QtGui import QIcon, QFontDatabase
from tracy.navigator import KymographNavigator


mpl.rcParams.update({
    'font.family': ['Figtree', 'Helvetica', 'Arial', 'sans-serif'],  # fallback list
    'font.size': 14,
})

def main():
    app = QApplication(sys.argv)
    
    app.setApplicationName("Tracy")
    try:
        app.setApplicationDisplayName("Tracy")
    except AttributeError:
        pass

    app.setWindowIcon(QIcon(resource_path("icons/tracyicon.png")))

    # For below, need qss font to be font-family: Helvetica Neue Lt Std, Helvetica Neue, Helvetica, Arial, sans-serif;
    # also, package data needs to include the fonts

    font_db = QFontDatabase()
    family = None
    for fname in ("Figtree-Regular.ttf", "Figtree-Bold.ttf"):
        path = resource_path(f"fonts/{fname}")
        fid  = font_db.addApplicationFont(path)
        if fid == -1:
            print(f"⚠️ Failed to load font: {path}")
        else:
            fams = font_db.applicationFontFamilies(fid)
            # print(f"✅ Loaded {fname}, registered as: {fams}")
            if fams:
                family = fams[0]
    if family:
        default_font = font_db.font(family, "Regular", 14)
        app.setFont(default_font)
        # print(f"✨ App default font set to: '{default_font.family()}', weight={default_font.weight()}")

    font_dir = resource_path("fonts")
    font_files = [os.path.join(font_dir, fname)
                for fname in ("Figtree-Regular.ttf", "Figtree-Bold.ttf")]

    for fpath in font_files:
        font_manager.fontManager.addfont(fpath)

    mpl.rcParams['font.family'] = 'Figtree'

    qss_path = resource_path('style.qss')
    app.setStyleSheet(load_stylesheet(qss_path))

    navigator = KymographNavigator()
    
    navigator.showMaximized()

    navigator.show()

    sys.exit(app.exec_())

def resource_path(relative):
    if getattr(sys, 'frozen', False):
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