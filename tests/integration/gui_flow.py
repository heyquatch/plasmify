"""Runs inside the sandbox (see test_gui_flow.py): the Customize tab, used like a person would.

Picks a panel colour, clicks Apply at once (before any plan refresh), lets the apply dialog
finish, and reports the colour of the real panel on screen while the Plasmify window touches it.
"""

import json
import subprocess
import sys
import time

from PyQt6.QtCore import QThread, QTimer
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

out = sys.argv[1]
app = QApplication(sys.argv)

from plasmify.gui.dialogs import ApplyDialog  # noqa: E402
from plasmify.gui.main_window import MainWindow  # noqa: E402

results = []
window = MainWindow()
window.resize(1300, 900)
window.show()
window.gallery.setCurrentRow(next(i for i, p in enumerate(window.presets) if p.id == "tokyo-night-neon"))


def idle() -> None:
    for thread in window.findChildren(QThread):
        thread.wait(30000)
    for _ in range(5):
        app.processEvents()


def finish_dialog() -> None:
    dialog = QApplication.activeModalWidget()
    if isinstance(dialog, ApplyDialog) and dialog.succeeded:
        dialog.accept()


watcher = QTimer()
watcher.timeout.connect(finish_dialog)
watcher.start(300)


def panel_colour(name: str) -> str:
    path = f"{out}/{name}.png"
    subprocess.run(["spectacle", "-b", "-n", "-f", "-o", path], timeout=30, check=False)
    image = QImage(path)
    return image.pixelColor(image.width() // 3, 18).name()


def choose(data: str) -> None:
    custom = window.customize
    index = custom.panel_colour.findData(data)
    custom.panel_colour.setCurrentIndex(index)
    custom.panel_colour.activated.emit(index)
    app.processEvents()
    window.apply_current()  # at once, like an impatient click
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.05)
        if QApplication.activeModalWidget() is None and window.apply_button.isEnabled():
            break
    idle()
    time.sleep(2)


def run() -> None:
    idle()
    custom = window.customize
    custom.radius.setValue(0)
    custom.panel_opacity.setValue(100)
    idle()
    colours = custom.preset.palette.colors
    for choice in ("accent", "surface"):
        choose(choice)
        results.append({"choice": choice, "expected": colours[choice], "panel": panel_colour(f"gui-{choice}")})
    results.append({"shown_after_apply": window.customize.panel_colour.currentData()})
    print(json.dumps(results), flush=True)
    app.quit()


QTimer.singleShot(1500, run)
app.exec()
