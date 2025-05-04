#!/usr/bin/env python3

import sys, os
import numpy as np
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QFile, QTextStream
from PyQt5.QtGui import QFontDatabase
from tracy.navigator import KymographNavigator

import matplotlib as mpl

mpl.rcParams.update({
    'font.family': ['Helvetica Neue', 'Helvetica', 'Arial', 'sans-serif'],  # fallback list
    'font.size': 14,                  # adjust the size to match your UI
})

def main():
    # Create the application instance.
    app = QApplication(sys.argv)
    
    # Set the application name and display name.
    app.setApplicationName("Tracy")
    try:
        # If your version of PyQt supports it, this will update the menu title on macOS.
        app.setApplicationDisplayName("Tracy")
    except AttributeError:
        pass

    # For below, need qss font to be font-family: Helvetica Neue Lt Std, Helvetica Neue, Helvetica, Arial, sans-serif;
    # also, package data needs to include the fonts

    # font_db = QFontDatabase()
    # family = None
    # for fname in ("HelveticaNeueLTStd-Roman.otf", "HelveticaNeueLTStd-Bd.otf"):
    #     path = resource_path(f"fonts/{fname}")
    #     fid  = font_db.addApplicationFont(path)
    #     if fid == -1:
    #         print(f"⚠️ Failed to load font: {path}")
    #     else:
    #         fams = font_db.applicationFontFamilies(fid)
    #         print(f"✅ Loaded {fname}, registered as: {fams}")
    #         if fams:
    #             family = fams[0]
    # if family:
    #     default_font = font_db.font(family, "55 Roman", 14)
    #     app.setFont(default_font)
    #     print(f"✨ App default font set to: '{default_font.family()}', weight={default_font.weight()}")

    qss_path = resource_path('style.qss')
    app.setStyleSheet(load_stylesheet(qss_path))

    # Create the main window.
    navigator = KymographNavigator()
    
    navigator.showMaximized()

    # Show the navigator window.
    navigator.show()

    # Start the application event loop.
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