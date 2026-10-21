"""Plasmify's own window follows the desktop's look while it is open.

KDE tells running apps when the colours or the widget style change, but it only makes a new
widget style when the style's name changes, and Kvantum reads its theme only when it is made.
So after a change from one Kvantum theme to another (glass in the other mode, another
see-through amount) open apps keep painting the old theme over the new colours. Style sheets
that name palette colours keep the colours they were set with, too.

Plasmify therefore makes a fresh instance of the desktop's widget style for itself after an
apply, a light/dark switch or a restore, and whenever the desktop says its look changed: the
window then looks the way a freshly started Plasmify would. Other open apps cannot be reached
like this; with a Kvantum theme they look right again once they are restarted.
"""

from __future__ import annotations

from PyQt6.QtCore import QEvent, QObject, QTimer, pyqtSlot
from PyQt6.QtWidgets import QApplication, QStyleFactory

from plasmify.system import Runner

# The changes of one apply or switch come in a burst (colours, then the style): restyle once,
# after the last one, when KDE's own handling of each has run.
SETTLE_MS = 400
# After an apply or restore said its changes are complete, only late signals are waited for.
RELEASE_MS = 100
# KGlobalSettings change kinds that change the look: the colours and the widget style (icons,
# fonts and the cursor reach the window without a new style).
PALETTE_CHANGED, STYLE_CHANGED = 0, 2


def desktop_style() -> str:
    """The widget style the desktop asks for ("" when unknown)."""
    return Runner().kread("kdeglobals", "KDE", "widgetStyle")


def fresh_style(name: str):
    """A new instance of the style ``name``, else of the one in use (None if neither exists)."""
    app = QApplication.instance()
    for candidate in (name, app.style().name() if app is not None else ""):
        style = QStyleFactory.create(candidate) if candidate else None
        if style is not None:
            return style
    return None


class LookFollower(QObject):
    """Restyles the app when the desktop's look changes, except while held (during an apply,
    whose steps change the look one at a time); releasing restyles once if the look changed."""

    def __init__(self, app: QApplication) -> None:
        super().__init__(app)
        self._held = 0
        self._restyling = False
        self._dirty = False  # the look changed since the last restyle
        self._catching_up = False  # restyle once the timer runs out, even while held
        self.restyles = 0  # how often the app was restyled (for tests)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(SETTLE_MS)
        self._timer.timeout.connect(self.restyle)
        if not self._listen_to_kde():
            # A filter on the application sees every event of the app: only when KDE's signal
            # cannot be heard.
            app.installEventFilter(self)

    def _listen_to_kde(self) -> bool:
        """Hear KDE's change signal, which also covers a new Kvantum theme under the same style
        name (no colour of its own changes then). False without QtDBus or a session bus."""
        try:
            from PyQt6.QtDBus import QDBusConnection
        except ImportError:
            return False
        return QDBusConnection.sessionBus().connect(
            "", "/KGlobalSettings", "org.kde.KGlobalSettings", "notifyChange", self._kde_changed
        )

    @pyqtSlot(int, int)
    def _kde_changed(self, kind: int, _arg: int) -> None:
        if kind in (PALETTE_CHANGED, STYLE_CHANGED):
            self.changed()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        if watched is QApplication.instance() and event.type() == QEvent.Type.ApplicationPaletteChange:
            self.changed()
        return False

    def changed(self) -> None:
        """The desktop's look changed: restyle once things settle."""
        if self._restyling:
            return
        self._dirty = True
        if not self._held or self._catching_up:
            self._timer.start(SETTLE_MS)

    def hold(self) -> None:
        self._held += 1
        self._timer.stop()

    def release(self) -> None:
        self._held = max(self._held - 1, 0)
        if not self._held and self._dirty:
            self._timer.start(RELEASE_MS)

    def catch_up(self) -> None:
        """Restyle soon, also while held: the colours and the style are in place, and what the
        apply still does (icons, wallpapers...) changes neither. A long apply showed new colours
        over the old theme until its last step. KDE's signals about them can arrive after this
        call, so it does not depend on them."""
        self._catching_up = True
        self._timer.start(SETTLE_MS)

    def restyle(self) -> None:
        if self._held and not self._catching_up:
            return
        self._catching_up = False
        self._dirty = False
        style = fresh_style(desktop_style())
        if style is None:
            return
        self._restyling = True  # setStyle changes the palette itself
        try:
            QApplication.setStyle(style)  # also sets every style sheet again
        finally:
            self._restyling = False
        self.restyles += 1


def follower() -> LookFollower | None:
    """The app's follower, made by :func:`follow_desktop` (None before)."""
    app = QApplication.instance()
    return app.findChild(LookFollower) if app is not None else None


def follow_desktop(app: QApplication) -> LookFollower:
    return follower() or LookFollower(app)


def hold() -> None:
    """Restyle only once the changes made from now on are complete (see :func:`release`)."""
    if (watcher := follower()) is not None:
        watcher.hold()


def release() -> None:
    if (watcher := follower()) is not None:
        watcher.release()


def catch_up() -> None:
    """While held: restyle now (see :meth:`LookFollower.catch_up`)."""
    if (watcher := follower()) is not None:
        watcher.catch_up()
