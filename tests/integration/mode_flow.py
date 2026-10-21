"""Runs inside the sandbox (see test_mode_switch.py): the light/dark button, clicked twice.

Applies Nord Minimal (dark), then clicks the switch button, lets the apply window finish and
reports the real colour scheme after each click.
"""

import json
import subprocess
import sys
import time

from PyQt6.QtCore import QThread, QTimer
from PyQt6.QtWidgets import QApplication

out = sys.argv[1]
app = QApplication(sys.argv)

from plasmify import engine, presets  # noqa: E402
from plasmify.gui.dialogs import ApplyDialog  # noqa: E402
from plasmify.gui.main_window import MainWindow  # noqa: E402
from plasmify.system import DATA_HOME, Runner  # noqa: E402

nord = presets.find("nord-minimal")
engine.apply(engine.make_plan(nord, ["colors", "style", "icons"], check_packages=False), Runner(), log=lambda m: None)

window = MainWindow()
window.resize(1300, 860)
window.show()
results = []


def finish_dialog() -> None:
    dialog = QApplication.activeModalWidget()
    if isinstance(dialog, ApplyDialog) and dialog.succeeded:
        dialog.accept()


watcher = QTimer()
watcher.timeout.connect(finish_dialog)
watcher.start(300)


def wait(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.05)


def scheme() -> dict:
    name = Runner().kread("kdeglobals", "General", "ColorScheme")
    text = (DATA_HOME / "color-schemes" / f"{name}.colors").read_text()
    block = text.split("[Colors:Window]", 1)[1]
    window_bg = next(line.split("=", 1)[1] for line in block.splitlines() if line.startswith("BackgroundNormal="))
    return {
        "scheme": name,
        "window": window_bg,
        "icons": Runner().kread("kdeglobals", "Icons", "Theme"),
        "style": Runner().kread("kdeglobals", "KDE", "widgetStyle"),
        "button": window.mode_button.text(),
    }


def click(name: str) -> None:
    window.mode_button.click()
    deadline = time.monotonic() + 300
    wait(1)
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.05)
        if QApplication.activeModalWidget() is None and window.mode_button.isEnabled():
            break
    for thread in window.findChildren(QThread):
        thread.wait(30000)
    wait(2)
    subprocess.run(["spectacle", "-b", "-n", "-f", "-o", f"{out}/{name}.png"], timeout=30, check=False)
    results.append(scheme())


def run() -> None:
    results.append(scheme())
    click("light")
    click("dark")
    print(json.dumps(results), flush=True)
    app.quit()


QTimer.singleShot(1500, run)
app.exec()
