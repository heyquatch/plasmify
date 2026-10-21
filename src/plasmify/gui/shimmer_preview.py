"""A small live preview of Shimmer for the Customize tab.

The preview is made by plasmify.shimmer in a background thread (about a third of a second), so
changing a setting never freezes the window, and is played from memory with QMovie. Nothing is
downloaded: it uses a wallpaper already on the computer, or a plain background in the theme's
colours until the wallpapers are downloaded.
"""

from __future__ import annotations

import atexit
from pathlib import Path
from typing import Any

from PyQt6.QtCore import (
    QBuffer,
    QByteArray,
    QCoreApplication,
    QIODevice,
    QRectF,
    QSize,
    Qt,
    QTemporaryDir,
    QThread,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QMovie, QPainter, QPainterPath, QPalette, QRadialGradient
from PyQt6.QtWidgets import QSizePolicy, QVBoxLayout, QWidget

from plasmify.gui.widgets import muted

# What is rendered; the screen scales it down to fit.
SIZE = QSize(480, 270)
WIDEST = 420
# Settings often change in bursts (a combo scrolled by keyboard): wait for a pause first.
SETTLE_MS = 300

# Renders still running. They are kept here, not on a widget, so a thread never outlives the
# Python object that holds it; the application waits for them before it quits.
_RUNNING: set[_Render] = set()
_hooked: list[bool] = []
# Folders of plain backgrounds. Python never frees the widgets that made them before the program
# ends, so they are removed explicitly when it quits.
_SCENES: list[QTemporaryDir] = []


def stop_renders() -> None:
    """Wait for the previews still being made (a thread must never be destroyed while it runs)."""
    for job in list(_RUNNING):
        job.wait(5000)


def remove_scenes() -> None:
    """Delete the plain backgrounds (the program is quitting; renders were waited for first)."""
    while _SCENES:
        _SCENES.pop().remove()


class _Render(QThread):
    done = pyqtSignal(int, object, str)  # generation, the WebP bytes (or None), a problem

    def __init__(self, generation: int, source: Path, options: dict[str, Any]) -> None:
        super().__init__()
        self._generation = generation
        self._source = source
        self._options = options

    def run(self) -> None:
        try:
            from plasmify import shimmer

            if not shimmer.available():
                raise RuntimeError("Qt cannot write WebP pictures on this system, so Shimmer is not available")
            # As long as the wallpaper's loop, so the glint and the motion keep their real speed.
            data = shimmer.preview(
                self._source, size=(SIZE.width(), SIZE.height()), seconds=6.0, fps=8, **self._options
            )
        except (ImportError, ValueError, RuntimeError, OSError) as exc:
            self.done.emit(self._generation, None, str(exc))
        else:
            self.done.emit(self._generation, data, "")


def plain_scene(target: Path, base: str, glow: str) -> bool:
    """Write a plain background in the theme's colours to show the effect on (no wallpaper
    yet); False when it cannot be written."""
    image = QImage(SIZE, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    bright = QColor(glow)
    dark = QColor(base)
    sky = QLinearGradient(0, 0, 0, SIZE.height())
    sky.setColorAt(0.0, dark.darker(140))
    sky.setColorAt(1.0, dark.lighter(130))
    painter.fillRect(image.rect(), sky)
    halo = QRadialGradient(SIZE.width() * 0.5, SIZE.height() * 0.62, SIZE.width() * 0.45)
    bright.setAlpha(110)
    halo.setColorAt(0.0, bright)
    bright.setAlpha(0)
    halo.setColorAt(1.0, bright)
    painter.fillRect(image.rect(), halo)
    painter.end()
    return image.save(str(target), "PNG")


class _Screen(QWidget):
    """Plays the preview at 16:9, as wide as there is room for (up to WIDEST)."""

    def __init__(self) -> None:
        super().__init__()
        self.movie: QMovie | None = None
        self.note = ""
        policy = QSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt name)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt name)
        return round(min(width, WIDEST) * 9 / 16)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt name)
        return QSize(320, 180)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 (Qt name)
        return QSize(96, 54)

    def _frame(self) -> QRectF:
        width = min(self.width(), WIDEST)
        height = min(self.height(), round(width * 9 / 16))
        width = round(height * 16 / 9)
        return QRectF((self.width() - width) / 2, 0, width, height)

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt name)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        frame = self._frame()
        clip = QPainterPath()
        clip.addRoundedRect(frame, 8, 8)
        painter.setClipPath(clip)
        picture = self.movie.currentPixmap() if self.movie is not None else None
        if picture is not None and not picture.isNull():
            painter.drawPixmap(frame, picture, QRectF(picture.rect()))
            return
        painter.fillRect(frame, self.palette().color(QPalette.ColorRole.AlternateBase))
        if self.note:
            painter.setPen(self.palette().color(QPalette.ColorRole.PlaceholderText))
            flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap
            painter.drawText(frame.adjusted(12, 8, -12, -8), int(flags), self.note)


class ShimmerPreview(QWidget):
    """Shows what the chosen Shimmer settings look like. Call :meth:`show_settings`."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.view = _Screen()
        self.caption = muted()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(self.view)
        layout.addWidget(self.caption)
        self._buffer: QBuffer | None = None
        self._generation = 0
        self._wanted: tuple[Path, tuple] | None = None  # what should be on screen
        self._made: tuple[Path, tuple] | None = None  # what is on screen or being made
        self._busy = False
        self._scenes: QTemporaryDir | None = None  # plain backgrounds, removed when the program quits
        self._timer = QTimer(self, singleShot=True, interval=SETTLE_MS)
        self._timer.timeout.connect(self._start)
        app = QCoreApplication.instance()
        if app is not None and not _hooked:
            app.aboutToQuit.connect(stop_renders)
            app.aboutToQuit.connect(remove_scenes)  # after stop_renders: no render reads them any more
            atexit.register(remove_scenes)  # also when the event loop never ran (scripts, tests)
            _hooked.append(True)

    def show_settings(self, source: Path | None, options: dict[str, Any], caption: str = "") -> None:
        """Preview ``options`` (plasmify.shimmer.preview's) on ``source``; None uses a plain
        background in the theme's colours (``options`` must then hold "base" and "colour")."""
        options = dict(options)
        base = options.pop("base", "#1e1e2e")
        if source is None:
            source = self._plain_scene(base, options["colour"])
            if source is None:
                self._show_note("No wallpaper to preview yet")
                return
        self.caption.setText(caption)
        self.caption.setVisible(bool(caption))
        self._wanted = (source, tuple(sorted(options.items())))
        if self._wanted != self._made:
            self._timer.start()

    def _plain_scene(self, base: str, colour: str) -> Path | None:
        if self._scenes is None:
            self._scenes = QTemporaryDir()
            _SCENES.append(self._scenes)
        if not self._scenes.isValid():
            return None
        target = Path(self._scenes.path()) / f"scene-{base.lstrip('#')}-{colour.lstrip('#')}.png"
        return target if target.is_file() or plain_scene(target, base, colour) else None

    def stop(self) -> None:
        """No preview (Shimmer is off): free the movie."""
        self._timer.stop()
        self._wanted = self._made = None
        self._generation += 1
        self._drop_movie()
        self.view.note = ""

    def _show_note(self, text: str) -> None:
        self._drop_movie()
        self._wanted = self._made = None
        self.view.note = text
        self.caption.setVisible(False)
        self.view.update()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt name)
        super().showEvent(event)
        if self.view.movie is not None:
            self.view.movie.setPaused(False)
        if self._wanted is not None and self._wanted != self._made:
            self._timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt name)
        super().hideEvent(event)
        if self.view.movie is not None:
            self.view.movie.setPaused(True)

    def _start(self) -> None:
        if self._wanted is None or self._busy or not self.isVisible():
            return  # a finished render starts the next one; showing the tab starts it too
        self._made = self._wanted
        self._generation += 1
        self._busy = True
        if self.view.movie is None:
            self.view.note = "Making a preview…"
            self.view.update()
        source, options = self._made
        job = _Render(self._generation, source, dict(options))
        job.done.connect(self._finished)
        job.finished.connect(lambda: (_RUNNING.discard(job), job.deleteLater()))
        _RUNNING.add(job)
        job.start(QThread.Priority.LowPriority)

    def _finished(self, generation: int, data: bytes | None, problem: str) -> None:
        self._busy = False
        if generation == self._generation:
            if data is None:
                # Settings changed while this render ran (another picture, another preset): their
                # turn is next, the note only stays until that one is ready.
                newer = self._wanted if self._wanted != self._made else None
                self._show_note(f"No preview: {problem}")
                if newer is not None:
                    self._wanted = newer
                    self.caption.setVisible(bool(self.caption.text()))  # the newer settings' caption
            else:
                self._play(data)
        if self._wanted is not None and self._wanted != self._made:
            self._timer.start()

    def _play(self, data: bytes) -> None:
        self._drop_movie()
        buffer = QBuffer(self)
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        movie = QMovie(buffer, QByteArray(b"webp"), self)
        movie.setCacheMode(QMovie.CacheMode.CacheAll)  # the WebP reader cannot jump back smoothly
        if not movie.isValid():
            buffer.deleteLater()
            movie.deleteLater()
            self._show_note("No preview: Qt cannot play WebP animations here")
            return
        self._buffer = buffer
        self.view.movie = movie
        self.view.note = ""
        movie.frameChanged.connect(lambda _: self.view.update())
        movie.start()
        if not self.isVisible():
            movie.setPaused(True)

    def _drop_movie(self) -> None:
        movie, self.view.movie = self.view.movie, None
        if movie is not None:
            movie.stop()
            movie.deleteLater()
        if self._buffer is not None:
            self._buffer.deleteLater()
            self._buffer = None
        self.view.update()
