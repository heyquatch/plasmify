"""Widgets for showing text that comes from preset files.

Qt labels and message boxes interpret HTML automatically. Preset text is
untrusted, so it is always shown as plain text (no fake links or markup).
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QLabel, QMessageBox, QSizePolicy, QWidget

from plasmify.plaintext import plain_tooltip

__all__ = ["ElidedLabel", "confirm", "message", "plain_label", "plain_tooltip"]


def plain_label(text: str = "", parent: QWidget | None = None, *, wrap: bool = False) -> QLabel:
    label = QLabel(parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(wrap)
    label.setText(text)
    return label


class ElidedLabel(QLabel):
    """One line of plain text that ends in … when it does not fit, with the whole text in the
    tooltip. Unlike a wrapping label it never widens the window: a long name may have no
    space to break at."""

    def __init__(self, parent: QWidget | None = None, *, minimum: int = 120, shrink: float = 1.0) -> None:
        """``shrink`` below 1 lets the text get that much smaller to fit before it is cut short."""
        super().__init__(parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(minimum)
        self._full = ""
        self._shrink = shrink
        self._base = QFont(self.font())
        self._fitting = False

    def full_text(self) -> str:
        return self._full

    def setFont(self, font: QFont) -> None:  # noqa: N802 (Qt API)
        self._base = QFont(font)
        super().setFont(font)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt API)
        self._full = text
        self._elide()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().changeEvent(event)
        if event.type() == event.Type.FontChange and not self._fitting:
            self._elide()

    def _fit(self) -> None:
        """The base font, made smaller (down to ``shrink``) when the text is too wide for it."""
        font = QFont(self._base)
        wide = QFontMetrics(font).horizontalAdvance(self._full)
        if self._shrink < 1 and wide > self.width() > 0 and font.pointSizeF() > 0:
            font.setPointSizeF(font.pointSizeF() * max(self._shrink, 0.98 * self.width() / wide))
        if font != self.font():
            self._fitting = True
            try:
                super().setFont(font)
            finally:
                self._fitting = False

    def _elide(self) -> None:
        self._fit()
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(1, self.width()))
        if shown != super().text():
            super().setText(shown)
        self.setToolTip(plain_tooltip(self._full) if shown != self._full else "")


def message(parent: QWidget | None, title: str, text: str, icon=QMessageBox.Icon.Warning) -> None:
    box = QMessageBox(icon, title, text, QMessageBox.StandardButton.Ok, parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.exec()


def confirm(parent: QWidget | None, title: str, text: str) -> bool:
    box = QMessageBox(
        QMessageBox.Icon.Warning,
        title,
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        parent,
    )
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes
