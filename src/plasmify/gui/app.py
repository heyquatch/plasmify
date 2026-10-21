"""GUI entry point."""

from __future__ import annotations

import sys
import traceback
from importlib import resources

from PyQt6 import sip
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from plasmify import APP_ID, APP_NAME, __version__
from plasmify.gui import restyle
from plasmify.gui.main_window import ERROR_LOG, MainWindow
from plasmify.gui.safe import message


def _install_safety_net() -> None:
    """Unexpected errors are logged and shown instead of closing the app.

    PyQt ends the program when a slot raises; with this hook a bug costs one action, not
    the whole session, and leaves a traceback in ~/.local/state/plasmify/errors.log."""
    shown = {"count": 0}

    def hook(kind, value, tb) -> None:
        ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ERROR_LOG.open("a") as handle:
            handle.write("--- unexpected error\n" + "".join(traceback.format_exception(kind, value, tb)) + "\n")
        sys.__excepthook__(kind, value, tb)
        if shown["count"] < 3:  # never flood the user with dialogs
            shown["count"] += 1
            message(
                None,
                "Something went wrong",
                f"Plasmify hit an unexpected error: {value}\n\nNothing was applied by this action. "
                f"Details were saved to {ERROR_LOG}; please attach them to a bug report.",
            )

    sys.excepthook = hook


def name_the_app() -> None:
    """Name the app before QApplication exists. KDE's platform theme opens the app's settings
    (which include the desktop's colours) while QApplication starts up, under the name the app
    has then; named later, Breeze opened a second copy under the new name that nothing reloads,
    so the menu bar kept the colours of the theme Plasmify was started with."""
    QApplication.setApplicationName(APP_NAME)
    QApplication.setApplicationVersion(__version__)
    # The Wayland app id: the task manager finds the menu entry and its icon by this name.
    QApplication.setDesktopFileName(APP_ID)


def run() -> int:
    name_the_app()
    app = QApplication(sys.argv)
    _install_safety_net()
    icon_path = resources.files("plasmify") / "assets" / "plasmify.svg"
    app.setWindowIcon(QIcon.fromTheme(APP_ID, QIcon(str(icon_path))))
    restyle.follow_desktop(app)
    window = MainWindow()
    window.show()
    code = app.exec()
    # Destroy the window while the application still exists. Left to Python, the application
    # could go first, and its teardown sometimes crashed on the window's widgets.
    sip.delete(window)
    return code
