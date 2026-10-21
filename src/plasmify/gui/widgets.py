"""Small visual building blocks for the Customize tab.

Everything here is drawn locally from files already on the computer (icon and
cursor themes, the user's own images). Nothing is downloaded to show a preview.
"""

from __future__ import annotations

import struct
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PyQt6.QtCore import (
    QEasingCurve,
    QEvent,
    QObject,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap, QWheelEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QAbstractSlider,
    QAbstractSpinBox,
    QApplication,
    QButtonGroup,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QListWidget,
    QPushButton,
    QScrollBar,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from plasmify.gui.safe import plain_label

IMAGE_FILTER = "Images (*.png *.jpg *.jpeg *.webp *.gif)"
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def tip(widget: QWidget, text: str) -> QWidget:
    """Attach a hover tooltip and return the widget (keeps form code short)."""
    widget.setToolTip(text)
    return widget


def muted(text: str = "", *, wrap: bool = True) -> QLabel:
    label = plain_label(text, wrap=wrap)
    label.setStyleSheet("color: palette(placeholder-text);")
    return label


def subheading(text: str) -> QLabel:
    """A small, quiet heading that groups the rows of a long card."""
    label = muted(text, wrap=False)
    font = label.font()
    font.setPointSizeF(font.pointSizeF() * 0.85)
    font.setWeight(QFont.Weight.DemiBold)
    font.setCapitalization(QFont.Capitalization.AllUppercase)
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 106)
    label.setFont(font)
    label.setContentsMargins(0, 6, 0, 0)
    return label


# ----- the mouse wheel scrolls the page --------------------------------------------
WHEEL_GUARDED = (QComboBox, QAbstractSpinBox, QAbstractSlider)


def has_keyboard_focus(widget: QWidget) -> bool:
    """True while ``widget`` or a part of it (a spin box's text field) has keyboard focus."""
    focus = QApplication.focusWidget()
    return focus is not None and (focus is widget or widget.isAncestorOf(focus))


class WheelGuard(QObject):
    """Keeps the mouse wheel for scrolling: a combo box, spin box or slider under the pointer
    passes the wheel on to the page until it is clicked or tabbed into. Without this, scrolling
    a long form quietly changes whatever setting happens to pass under the pointer."""

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        if event.type() != QEvent.Type.Wheel or not isinstance(watched, QWidget) or has_keyboard_focus(watched):
            return False
        # Ignored and filtered: the control never sees it. Qt then offers a real wheel turn to
        # the parent widgets, up to the scroll area; synthetic ones it does not pass on.
        event.ignore()
        if not event.spontaneous() and pass_wheel_on(watched, event):
            event.accept()  # taken further up: whoever sent it need not offer it again
        return True


def pass_wheel_on(widget: QWidget, event: QWheelEvent) -> bool:
    """Offer ``event`` to the parents of ``widget`` until one takes it, the way Qt passes on a
    real wheel turn (it does not for events sent by programs or tests). True if one took it."""
    position = event.position()
    current = widget
    while not current.isWindow() and current.parentWidget() is not None:
        position += QPointF(current.pos())
        current = current.parentWidget()
        offered = QWheelEvent(
            position,
            event.globalPosition(),
            event.pixelDelta(),
            event.angleDelta(),
            event.buttons(),
            event.modifiers(),
            event.phase(),
            event.inverted(),
        )
        offered.ignore()
        QApplication.sendEvent(current, offered)
        if offered.isAccepted():
            return True
    return False


def guard_wheel(root: QWidget) -> int:
    """Protect every combo box, spin box and slider inside ``root`` from stray wheel turns and
    return how many were newly protected. Calling it again (say, after adding controls) is safe."""
    guard = root.findChild(WheelGuard, "wheel-guard", Qt.FindChildOption.FindDirectChildrenOnly)
    if guard is None:
        guard = WheelGuard(root)
        guard.setObjectName("wheel-guard")
    count = 0
    for widget in root.findChildren(WHEEL_GUARDED):
        if isinstance(widget, QScrollBar) or widget.property("wheel-guarded"):
            continue  # scroll bars are what the wheel is for
        widget.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # the wheel alone no longer focuses it
        widget.installEventFilter(guard)
        widget.setProperty("wheel-guarded", True)
        count += 1
    return count


# ----- layouts that wrap instead of widening the window -------------------------------
class _ItemLayout(QLayout):
    """Shared plumbing for the layouts below: items in a list, heights that follow the width."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802 (Qt API)
        self._items.append(item)

    def add_layout(self, layout: QLayout) -> None:
        self.addChildLayout(layout)
        self.addItem(layout)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 (Qt API)
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802 (Qt API)
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 (Qt API)
        return Qt.Orientation.Horizontal

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt API)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt API)
        return self._place(QRect(0, 0, max(width, 1), 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 (Qt API)
        super().setGeometry(rect)
        self._place(rect, apply=True)

    def minimumSize(self) -> QSize:  # noqa: N802 (Qt API)
        size = QSize(0, 0)
        for item in self._visible():
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _visible(self) -> list[QLayoutItem]:
        return [item for item in self._items if not item.isEmpty()]

    def _inner(self, rect: QRect) -> QRect:
        margins = self.contentsMargins()
        return rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())

    def _vertical_margins(self) -> int:
        margins = self.contentsMargins()
        return margins.top() + margins.bottom()

    @staticmethod
    def _height(item: QLayoutItem, width: int) -> int:
        return item.heightForWidth(width) if item.hasHeightForWidth() else item.sizeHint().height()

    def _place(self, rect: QRect, apply: bool) -> int:
        raise NotImplementedError


class TileGrid(_ItemLayout):
    """Tiles of equal width in as many columns as fit, at most ``columns`` (0: all in one row),
    so a row of picture tiles wraps onto more rows on a narrow window. ``balanced`` tiles that
    wrap share the rows evenly: two rows of two rather than three and one left alone."""

    def __init__(
        self, columns: int = 0, spacing: int = 6, parent: QWidget | None = None, *, balanced: bool = False
    ) -> None:
        super().__init__(parent)
        self._most = max(int(columns), 0)
        self._gap = max(int(spacing), 0)
        self._balanced = balanced

    def columns_for(self, width: int) -> int:
        items = self._visible()
        if not items:
            return 0
        cell = max(max(item.sizeHint().width() for item in items), 1)
        fit = max(1, (max(width, 0) + self._gap) // (cell + self._gap))
        columns = max(1, min(len(items), self._most or len(items), fit))
        if self._balanced and columns < len(items):
            rows = -(-len(items) // columns)
            columns = -(-len(items) // rows)
        return columns

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        items = self._visible()
        if not items:
            return QSize(0, 0)
        columns = min(len(items), self._most or len(items))
        cell = max(item.sizeHint().width() for item in items)
        margins = self.contentsMargins()
        width = columns * cell + self._gap * (columns - 1) + margins.left() + margins.right()
        return QSize(width, self.heightForWidth(width))

    def _place(self, rect: QRect, apply: bool) -> int:
        inner = self._inner(rect)
        items = self._visible()
        if not items:
            return self._vertical_margins()
        columns = self.columns_for(inner.width())
        width = max((inner.width() - self._gap * (columns - 1)) // columns, 1)
        top = inner.y()
        for start in range(0, len(items), columns):
            row = items[start : start + columns]
            height = max(item.sizeHint().height() for item in row)
            if apply:
                for index, item in enumerate(row):
                    item.setGeometry(QRect(inner.x() + index * (width + self._gap), top, width, height))
            top += height + self._gap
        return top - self._gap - inner.y() + self._vertical_margins()


class FlowLayout(_ItemLayout):
    """Items at their natural size from left to right, starting a new line when one is full."""

    def __init__(self, spacing: int = 6, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._gap = max(int(spacing), 0)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        items = self._visible()
        margins = self.contentsMargins()
        width = sum(item.sizeHint().width() for item in items) + self._gap * max(len(items) - 1, 0)
        height = max((item.sizeHint().height() for item in items), default=0)
        return QSize(width + margins.left() + margins.right(), height + self._vertical_margins())

    def _place(self, rect: QRect, apply: bool) -> int:
        inner = self._inner(rect)
        x, y, line = inner.x(), inner.y(), 0
        for item in self._visible():
            hint = item.sizeHint()
            width = min(hint.width(), max(inner.width(), 1))
            if x > inner.x() and x + width > inner.x() + inner.width():
                x, y, line = inner.x(), y + line + self._gap, 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), QSize(width, hint.height())))
            x += width + self._gap
            line = max(line, hint.height())
        return y + line - inner.y() + self._vertical_margins()


class AdaptiveRow(_ItemLayout):
    """A lead item (a preview) and the rest beside it while both fit, one above the other on a
    narrow window. It only ever asks for the width of the wider item, so the window can shrink
    and the two stack instead of scrolling sideways. The lead takes ``share`` of a wide row,
    between ``lead_min`` and ``lead_max`` pixels."""

    def __init__(
        self,
        lead_min: int,
        lead_max: int,
        rest_min: int,
        spacing: int = 12,
        share: float = 0.6,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.lead_min = max(int(lead_min), 1)
        self.lead_max = max(int(lead_max), self.lead_min)
        self.rest_min = max(int(rest_min), 1)
        self._gap = max(int(spacing), 0)
        self._share = min(max(float(share), 0.1), 0.9)

    def is_wide(self, width: int) -> bool:
        """Whether the items sit beside each other at this width (margins excluded)."""
        return len(self._visible()) > 1 and width >= self.lead_min + self._gap + self.rest_min

    def lead_width(self, width: int) -> int:
        if not self.is_wide(width):
            return max(min(width, self.lead_max), 1)
        lead = min(max(int(width * self._share), self.lead_min), self.lead_max)
        return max(min(lead, width - self._gap - self.rest_min), self.lead_min)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        margins = self.contentsMargins()
        width = self.lead_max + self._gap + self.rest_min + margins.left() + margins.right()
        return QSize(width, self.heightForWidth(width))

    def _place(self, rect: QRect, apply: bool) -> int:
        inner = self._inner(rect)
        items = self._visible()
        if not items:
            return self._vertical_margins()
        lead = items[0]
        lead_width = self.lead_width(inner.width())
        lead_height = self._height(lead, lead_width)
        if apply:
            lead.setGeometry(QRect(inner.x(), inner.y(), lead_width, lead_height))
        if len(items) == 1:
            return lead_height + self._vertical_margins()
        rest = items[1]
        if self.is_wide(inner.width()):
            rest_width = inner.width() - lead_width - self._gap
            rest_height = self._height(rest, rest_width)
            if apply:
                rest.setGeometry(QRect(inner.x() + lead_width + self._gap, inner.y(), rest_width, rest_height))
            return max(lead_height, rest_height) + self._vertical_margins()
        rest_height = self._height(rest, inner.width())
        if apply:
            rest.setGeometry(QRect(inner.x(), inner.y() + lead_height + self._gap, inner.width(), rest_height))
        return lead_height + self._gap + rest_height + self._vertical_margins()


# ----- slider with a number box ---------------------------------------------
class SliderSpin(QWidget):
    """A slider and a number box that stay in sync. Drag for feel, type for precision."""

    valueChanged = pyqtSignal(int)  # noqa: N815 (Qt naming)

    def __init__(self, low: int, high: int, suffix: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.spin = QSpinBox()
        self.spin.setSuffix(suffix)
        self.spin.setMinimumWidth(78)
        self.setRange(low, high)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.slider, 1)
        row.addWidget(self.spin)
        self.slider.valueChanged.connect(self._from_slider)
        self.spin.valueChanged.connect(self._from_spin)

    def setRange(self, low: int, high: int) -> None:  # noqa: N802 (Qt naming)
        for widget in (self.slider, self.spin):
            widget.blockSignals(True)
            widget.setRange(low, high)
            widget.blockSignals(False)

    def maximum(self) -> int:
        return self.slider.maximum()

    def _from_slider(self, value: int) -> None:
        self.spin.blockSignals(True)
        self.spin.setValue(value)
        self.spin.blockSignals(False)
        self.valueChanged.emit(value)

    def _from_spin(self, value: int) -> None:
        self.slider.blockSignals(True)
        self.slider.setValue(value)
        self.slider.blockSignals(False)
        self.valueChanged.emit(value)

    def value(self) -> int:
        return self.slider.value()

    def setValue(self, value: int) -> None:  # noqa: N802 (Qt naming)
        self.slider.setValue(value)
        self.spin.blockSignals(True)
        self.spin.setValue(self.slider.value())
        self.spin.blockSignals(False)

    def setToolTip(self, text: str) -> None:  # noqa: N802 (Qt naming)
        super().setToolTip(text)
        self.slider.setToolTip(text)
        self.spin.setToolTip(text)


# ----- picture tiles ------------------------------------------------------------
class ChoiceTiles(QWidget):
    """A row of picture buttons; exactly one is selected. Pictures are redrawn on demand."""

    changed = pyqtSignal()

    def __init__(
        self,
        choices: list[tuple[str, Any, str]],
        size: QSize | None = None,
        columns: int = 0,
        *,
        balanced: bool = False,
    ) -> None:
        """``choices``: (label, value, tooltip). ``columns``: at most this many tiles per row
        (0: all of them); a narrow window wraps them onto more rows, evenly when ``balanced``."""
        super().__init__()
        size = size or QSize(112, 70)
        self._size = size
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: list[tuple[QToolButton, Any]] = []
        self.grid = TileGrid(columns, 6, self, balanced=balanced)
        self.grid.setContentsMargins(0, 0, 0, 0)
        for label, value, hint in choices:
            button = QToolButton()
            button.setText(label)
            button.setToolTip(hint)
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.setIconSize(size)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setStyleSheet(
                "QToolButton { border: 2px solid transparent; border-radius: 8px; padding: 4px; }"
                "QToolButton:checked { border-color: palette(highlight); background: palette(alternate-base); }"
                "QToolButton:hover:!checked { border-color: palette(mid); }"
            )
            self._group.addButton(button)
            self._buttons.append((button, value))
            self.grid.addWidget(button)
        self._group.buttonToggled.connect(lambda _b, on: on and self.changed.emit())

    def paint_with(self, draw: Callable[[QPainter, QRectF, Any], None]) -> None:
        """Redraw every tile picture with ``draw(painter, rect, value)``."""
        ratio = self.devicePixelRatioF() or 1.0
        for button, value in self._buttons:
            pixmap = QPixmap(self._size * ratio)
            pixmap.setDevicePixelRatio(ratio)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            draw(painter, QRectF(0, 0, self._size.width(), self._size.height()), value)
            painter.end()
            button.setIcon(QIcon(pixmap))

    def value(self) -> Any:
        for button, value in self._buttons:
            if button.isChecked():
                return value
        return None

    def set_value(self, value: Any) -> None:
        for button, candidate in self._buttons:
            if candidate == value:
                button.setChecked(True)
                return
        if self._buttons:
            self._buttons[0][0].setChecked(True)

    def clear_selection(self) -> None:
        """No tile selected (the current value is the user's own design)."""
        self._group.setExclusive(False)
        for button, _value in self._buttons:
            button.setChecked(False)
        self._group.setExclusive(True)

    def set_enabled_value(self, value: Any, enabled: bool) -> None:
        for button, candidate in self._buttons:
            if candidate == value:
                button.setEnabled(enabled)


# ----- drag and drop images ---------------------------------------------------------
def image_paths(mime) -> list[Path]:
    paths = []
    for url in mime.urls() if mime.hasUrls() else []:
        if url.isLocalFile():
            path = Path(url.toLocalFile())
            if path.suffix.lower() in IMAGE_SUFFIXES and path.is_file():
                paths.append(path)
    return paths


class DropZone(QFrame):
    """Drop images here or click to browse. Shows what is currently chosen."""

    picked = pyqtSignal(list)  # list[Path]
    cleared = pyqtSignal()

    def __init__(self, prompt: str, *, multiple: bool = False, height: int = 96) -> None:
        super().__init__()
        self._prompt = prompt
        self._multiple = multiple
        self._pixmaps: list[QPixmap] = []
        self._caption = ""
        self._hover = False
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip(
            f"{prompt}\nDrag image files here from Dolphin, or click to choose. "
            "PNG, JPEG, WebP and GIF work. Right-click to go back to the default."
        )
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(lambda _: self.cleared.emit())

    def show_images(self, paths: list[Path], caption: str = "") -> None:
        self._pixmaps = []
        for path in paths[:6]:
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                self._pixmaps.append(
                    pixmap.scaled(
                        320, 200, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
                    )
                )
        self._caption = caption
        self.update()

    def show_caption(self, caption: str) -> None:
        self._pixmaps = []
        self._caption = caption
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        start = str(Path.home() / "Pictures")
        if self._multiple:
            paths, _ = QFileDialog.getOpenFileNames(self, self._prompt, start, IMAGE_FILTER)
        else:
            path, _ = QFileDialog.getOpenFileName(self, self._prompt, start, IMAGE_FILTER)
            paths = [path] if path else []
        if paths:
            self.picked.emit([Path(p) for p in paths])

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if image_paths(event.mimeData()):
            event.acceptProposedAction()
            self._hover = True
            self.update()

    def dragLeaveEvent(self, _event) -> None:  # noqa: N802 (Qt API)
        self._hover = False
        self.update()

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt API)
        self._hover = False
        paths = image_paths(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self.picked.emit(paths if self._multiple else paths[:1])
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        accent = pal.highlight().color()
        border = accent if self._hover else pal.mid().color()
        fill = QColor(accent) if self._hover else pal.alternateBase().color()
        if self._hover:
            fill.setAlpha(40)
        painter.setBrush(fill)
        pen = QPen(border, 1.5, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.drawRoundedRect(rect, 10, 10)

        text_rect = rect.adjusted(10, 6, -10, -6)
        if self._pixmaps:
            caption_h = 18 if self._caption else 0
            area = rect.adjusted(8, 8, -8, -8 - caption_h)
            count = len(self._pixmaps)
            gap = 6
            width = (area.width() - gap * (count - 1)) / count
            for index, pixmap in enumerate(self._pixmaps):
                cell = QRectF(area.left() + index * (width + gap), area.top(), width, area.height())
                scaled = pixmap.scaled(
                    int(cell.width()),
                    int(cell.height()),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                target = QRectF(
                    cell.center().x() - scaled.width() / 2,
                    cell.center().y() - scaled.height() / 2,
                    scaled.width(),
                    scaled.height(),
                )
                clip = QPainterPath()
                clip.addRoundedRect(target, 6, 6)
                painter.save()
                painter.setClipPath(clip)
                painter.drawPixmap(target.toRect(), scaled)
                painter.restore()
            text_rect = QRectF(rect.left() + 10, rect.bottom() - caption_h - 4, rect.width() - 20, caption_h)
        painter.setPen(pal.text().color() if self._caption else pal.placeholderText().color())
        icon = QIcon.fromTheme("document-open").pixmap(22, 22)
        lines = self._caption if self._pixmaps else f"{self._caption}\n{self._prompt}".strip()
        if not self._pixmaps and not icon.isNull():
            painter.drawPixmap(QPointF(rect.center().x() - 11, rect.top() + 10), icon)
            text_rect = text_rect.adjusted(0, 24, 0, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, lines)
        painter.end()


# ----- icon theme samples -------------------------------------------------------------
SAMPLE_ICONS = (
    "folder",
    "user-home",
    "folder-download",
    "utilities-terminal",
    "system-file-manager",
    "preferences-system",
    "internet-web-browser",
    "accessories-text-editor",
)
ICON_ROOTS = (Path.home() / ".local/share/icons", Path.home() / ".icons", Path("/usr/share/icons"))


def _theme_dir(name: str) -> Path | None:
    for root in ICON_ROOTS:
        candidate = root / name
        if (candidate / "index.theme").is_file():
            return candidate
    return None


def _theme_index(folder: Path) -> tuple[list[str], list[str]]:
    """(folders that hold icons around 48 px, inherited theme names)."""
    folders: list[tuple[int, str]] = []
    inherits: list[str] = []
    current = None
    size = 0
    try:
        lines = (folder / "index.theme").read_text(errors="replace").splitlines()
    except OSError:
        return [], []
    for line in lines:
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            if current and size:
                folders.append((abs(size - 48), current))
            current, size = line[1:-1], 0
            if current == "Icon Theme":
                current = None
        elif "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            if current is None and key == "Inherits":
                inherits = [v.strip() for v in value.split(",") if v.strip()]
            elif current and key == "Size":
                try:
                    size = int(value)
                except ValueError:
                    size = 0
    if current and size:
        folders.append((abs(size - 48), current))
    folders.sort()
    return [name for _, name in folders], inherits


def find_theme_icon(theme: str, icon: str, depth: int = 0) -> Path | None:
    folder = _theme_dir(theme)
    if folder is None or depth > 3:
        return None
    folders, inherits = _theme_index(folder)
    for sub in folders:
        for suffix in (".svg", ".png"):
            path = folder / sub / f"{icon}{suffix}"
            if path.exists():
                return path
    for parent in inherits:
        if parent.lower() == "hicolor":
            continue
        found = find_theme_icon(parent, icon, depth + 1)
        if found:
            return found
    return None


def icon_theme_strip(theme: str | None, size: int = 32) -> QPixmap:
    """A row of sample icons from ``theme`` (the system theme when None)."""
    width = len(SAMPLE_ICONS) * (size + 8)
    pixmap = QPixmap(width, size + 4)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    for index, name in enumerate(SAMPLE_ICONS):
        path = find_theme_icon(theme, name) if theme else None
        icon = QIcon(str(path)) if path else QIcon.fromTheme(name)
        painter.drawPixmap(index * (size + 8), 2, icon.pixmap(size, size))
    painter.end()
    return pixmap


# ----- cursor samples (Xcursor files) ----------------------------------------------------
CURSOR_ROOTS = (Path.home() / ".local/share/icons", Path.home() / ".icons", Path("/usr/share/icons"))
SAMPLE_CURSORS = ("left_ptr", "hand2", "xterm", "watch", "fleur")
XCURSOR_IMAGE = 0xFFFD0002


def read_xcursor(path: Path, want: int = 32) -> QImage | None:
    """The image closest to ``want`` px from an Xcursor file (a simple, documented format)."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 16 or data[:4] != b"Xcur":
        return None
    try:
        _magic, header, _version, count = struct.unpack_from("<4sIII", data, 0)
        best = None
        for index in range(min(count, 256)):
            kind, subtype, position = struct.unpack_from("<III", data, header + index * 12)
            if kind == XCURSOR_IMAGE and (best is None or abs(subtype - want) < abs(best[0] - want)):
                best = (subtype, position)
        if best is None:
            return None
        _size, _kind, _sub, _ver, width, height = struct.unpack_from("<IIIIII", data, best[1])
        if not (0 < width <= 512 and 0 < height <= 512):
            return None
        start = best[1] + 36
        pixels = data[start : start + width * height * 4]
        if len(pixels) != width * height * 4:
            return None
    except struct.error:
        return None
    return QImage(pixels, width, height, width * 4, QImage.Format.Format_ARGB32_Premultiplied).copy()


def _cursor_file(theme: str, name: str, depth: int = 0) -> Path | None:
    if depth > 3:
        return None
    for root in CURSOR_ROOTS:
        candidate = root / theme / "cursors" / name
        if candidate.exists():
            return candidate
    for root in CURSOR_ROOTS:
        index = root / theme / "index.theme"
        if index.is_file():
            for line in index.read_text(errors="replace").splitlines():
                if line.strip().startswith("Inherits="):
                    for parent in line.split("=", 1)[1].split(","):
                        found = _cursor_file(parent.strip(), name, depth + 1)
                        if found:
                            return found
    return None


def cursor_strip(theme: str | None, size: int = 32) -> QPixmap:
    pixmap = QPixmap(len(SAMPLE_CURSORS) * (size + 10), size + 4)
    pixmap.fill(Qt.GlobalColor.transparent)
    if not theme:
        return pixmap
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    for index, name in enumerate(SAMPLE_CURSORS):
        path = _cursor_file(theme, name)
        image = read_xcursor(path, size) if path else None
        if image is not None:
            image = image.scaled(
                size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
            )
            painter.drawImage(index * (size + 10), 2, image)
    painter.end()
    return pixmap


# ----- colour swatches ---------------------------------------------------------------------
class SwatchRow(QWidget):
    """Round colour buttons. Clicking one emits its colour."""

    chosen = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._row = QHBoxLayout(self)
        self._row.setContentsMargins(0, 0, 0, 0)
        self._row.setSpacing(4)
        self._current = ""

    def set_colours(self, colours: list[tuple[str, str]], current: str) -> None:
        """``colours``: (name, #hex)."""
        while self._row.count():
            item = self._row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._current = current.lower()
        for name, colour in colours:
            button = QToolButton()
            button.setFixedSize(26, 26)
            selected = colour.lower() == self._current
            ring = "palette(text)" if selected else "rgba(127,127,127,0.45)"
            button.setStyleSheet(
                f"QToolButton {{ background: {colour}; border: {3 if selected else 1}px solid {ring}; "
                "border-radius: 13px; }"
            )
            button.setToolTip(f"{name} ({colour})")
            button.clicked.connect(lambda _=False, c=colour: self.chosen.emit(c))
            self._row.addWidget(button)
        self._row.addStretch(1)


def section_title(text: str, icon: str = "") -> QWidget:
    holder = QWidget()
    row = QHBoxLayout(holder)
    row.setContentsMargins(0, 4, 0, 0)
    if icon:
        picture = QLabel()
        picture.setPixmap(QIcon.fromTheme(icon).pixmap(22, 22))
        row.addWidget(picture)
    label = plain_label(text)
    font = label.font()
    font.setPointSizeF(font.pointSizeF() * 1.15)
    font.setBold(True)
    label.setFont(font)
    row.addWidget(label)
    row.addStretch(1)
    return holder


class ElidedButton(QToolButton):
    """A tool button that shortens its text with … instead of widening the layout around it."""

    def __init__(self) -> None:
        super().__init__()
        self._full = ""
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)

    def full_text(self) -> str:
        return self._full

    def set_full_text(self, text: str) -> None:
        self._full = text
        self._elide()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().resizeEvent(event)
        self._elide()

    def _elide(self) -> None:
        room = max(40, self.width() - self.iconSize().width() - 28)
        text = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, room)
        if text != self.text():
            self.setText(text)


class Card(QFrame):
    """A rounded box that groups related options. Collapsed until its header is clicked;
    the header shows a one-line summary of the current settings."""

    toggled = pyqtSignal(bool)

    def __init__(self, title: str, icon: str = "", hint: str = "", *, open_: bool = False) -> None:
        super().__init__()
        self.setObjectName("card")
        self.setStyleSheet(
            "QFrame#card { background: palette(alternate-base); border: 1px solid rgba(127,127,127,0.28); "
            "border-radius: 12px; }"
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 6)
        outer.setSpacing(0)
        # A long summary is shortened instead of widening the card (and with it the window).
        self.header = ElidedButton()
        self.header.setCheckable(True)
        self.header.setChecked(open_)
        self.header.setAutoRaise(True)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setMinimumWidth(180)
        self.header.setIconSize(QSize(22, 22))
        self.header.setStyleSheet(
            "QToolButton { text-align: left; border: none; padding: 6px 4px; font-weight: 600; }"
            "QToolButton:hover { background: rgba(127,127,127,0.14); border-radius: 8px; }"
        )
        self._title, self._icon, self._hint = title, icon, hint
        self.header.toggled.connect(self._toggle)
        outer.addWidget(self.header)
        self.content = QWidget()
        self.body = QVBoxLayout(self.content)
        self.body.setContentsMargins(10, 4, 10, 10)
        self.body.setSpacing(8)
        outer.addWidget(self.content)
        self._summary = ""
        self._toggle(open_)

    def set_summary(self, text: str) -> None:
        self._summary = text
        self._refresh()

    def is_open(self) -> bool:
        return self.header.isChecked()

    def set_open(self, open_: bool) -> None:
        self.header.setChecked(open_)

    def _toggle(self, open_: bool) -> None:
        self.content.setVisible(open_)
        self._refresh()
        self.toggled.emit(open_)

    def _refresh(self) -> None:
        arrow = "▾" if self.header.isChecked() else "▸"
        summary = f"   ·   {self._summary}" if self._summary and not self.header.isChecked() else ""
        self.header.set_full_text(f"{arrow}  {self._title}{summary}")
        lines = [self._hint, f"Now: {self._summary}" if self._summary else "", "Click to show or hide these options."]
        self.header.setToolTip("\n".join(line for line in lines if line))
        if self._icon:
            self.header.setIcon(QIcon.fromTheme(self._icon))


THUMB = QSize(144, 81)


def wallpaper_thumb(path: Path | None, number: int, scale: int = 2) -> QIcon:
    """A cropped thumbnail with its slideshow position painted in the corner."""
    size = THUMB * scale
    pixmap = QPixmap(size)
    pixmap.fill(QColor(127, 127, 127, 60))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    picture = QPixmap(str(path)) if path is not None else QPixmap()
    if not picture.isNull():
        picture = picture.scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                 Qt.TransformationMode.SmoothTransformation)  # fmt: skip
        painter.drawPixmap(0, 0, picture, (picture.width() - size.width()) // 2,
                           (picture.height() - size.height()) // 2, size.width(), size.height())  # fmt: skip
    else:
        icon = QIcon.fromTheme("folder-download", QIcon.fromTheme("image-x-generic"))
        side = 28 * scale
        icon.paint(painter, (size.width() - side) // 2, (size.height() - side) // 2, side, side)
    badge = QRectF(6 * scale, 6 * scale, 22 * scale, 18 * scale)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 170))
    painter.drawRoundedRect(badge, 5 * scale, 5 * scale)
    font = painter.font()
    font.setPixelSize(11 * scale)
    font.setBold(True)
    painter.setFont(font)
    painter.setPen(QColor("white"))
    painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(number))
    painter.end()
    return QIcon(pixmap)


class WallpaperList(QListWidget):
    """The slideshow in order: drag thumbnails to reorder, drop pictures to add, Delete removes."""

    files_dropped = pyqtSignal(list)  # list[Path]
    reordered = pyqtSignal()
    remove_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        # List mode laid out left to right: a drag really moves the row (icon mode only moves
        # the picture on screen and keeps the order).
        self.setViewMode(QListWidget.ViewMode.ListMode)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setIconSize(THUMB)
        self.setGridSize(THUMB + QSize(12, 12))
        self.setUniformItemSizes(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setMinimumHeight(THUMB.height() + 30)
        self.setMaximumHeight(3 * (THUMB.height() + 12) + 8)
        self.setAcceptDrops(True)
        self.setToolTip(
            "Your slideshow, in order. Drag pictures to change the order, drop image files here to add "
            "them, select some and press Delete (or Remove) to take them out."
        )
        self._pending = False
        self.model().rowsMoved.connect(lambda *_: self._moved())

    def _moved(self) -> None:
        # Several rows move one by one during a drop: report once, after Qt is done with them.
        if not self._pending:
            self._pending = True
            QTimer.singleShot(0, self._report)

    def _report(self) -> None:
        self._pending = False
        self.reordered.emit()

    def dragEnterEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if image_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if image_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802 (Qt API)
        paths = image_paths(event.mimeData())
        if paths and event.source() is not self:
            event.acceptProposedAction()
            self.files_dropped.emit(paths)
            return
        super().dropEvent(event)  # a real move reports itself through rowsMoved

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.remove_requested.emit()
            return
        super().keyPressEvent(event)


# ----- the main action and the bar for changes not applied yet ------------------------------
PRIMARY_STYLE = (
    "QPushButton#primary { background: palette(highlight); color: palette(highlighted-text);"
    " border: 1px solid palette(highlight); border-radius: 8px; padding: 7px 18px; font-weight: 600; }"
    "QPushButton#primary:hover, QPushButton#primary:focus { border: 1px solid rgba(255,255,255,0.65); }"
    "QPushButton#primary:pressed { padding-top: 8px; padding-bottom: 6px; }"
    "QPushButton#primary:disabled { background: rgba(127,127,127,0.22); color: palette(placeholder-text);"
    " border-color: transparent; }"
)


def primary_button(button: QPushButton) -> QPushButton:
    """Mark ``button`` as the one main action of its area: filled with the accent colour."""
    button.setObjectName("primary")
    button.setStyleSheet(PRIMARY_STYLE)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


class UnsavedBar(QFrame):
    """A rounded dark bar that floats above the bottom edge of ``host`` while there are changes
    that are not applied yet: a short note, a quiet Reset and the main Apply button. It slides
    in and out (instantly when the desktop turns animations off)."""

    reset_requested = pyqtSignal()
    apply_requested = pyqtSignal()
    reserved_changed = pyqtSignal(int)  # the room to keep under the page grew or shrank

    MESSAGE = "You have changes that are not applied yet"
    SHADOW = 12  # room around the bar for its soft shadow
    GAP = 12  # between the bar and the bottom edge
    MAX_WIDTH = 760
    SURFACE = QColor(30, 31, 34, 245)
    TEXT = "#f2f3f5"

    def __init__(self, host: QWidget) -> None:
        super().__init__(host)
        self.setObjectName("unsavedBar")
        self._host = host
        self._wanted = False
        self._reserved = 0
        shadow = self.SHADOW
        row = QHBoxLayout(self)
        row.setContentsMargins(shadow + 18, shadow + 8, shadow + 10, shadow + 8)
        row.setSpacing(8)
        self.icon = QLabel()
        self.icon.setPixmap(QIcon.fromTheme("document-edit", QIcon.fromTheme("dialog-information")).pixmap(18, 18))
        self.message = plain_label(self.MESSAGE, wrap=True)
        self.message.setMinimumWidth(120)
        self.reset_button = QPushButton("Reset")
        self.reset_button.setObjectName("barReset")
        self.reset_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_button.setToolTip("Forget the changes that are not applied yet.")
        self.reset_button.clicked.connect(self.reset_requested.emit)
        self.apply_button = primary_button(QPushButton(QIcon.fromTheme("dialog-ok-apply"), "Apply theme"))
        self.apply_button.setToolTip("Apply the theme with your changes. Restore… puts your earlier settings back.")
        self.apply_button.clicked.connect(self.apply_requested.emit)
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.message, 1)
        row.addWidget(self.reset_button, 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(self.apply_button, 0, Qt.AlignmentFlag.AlignVCenter)
        self.setStyleSheet(
            PRIMARY_STYLE + f"QLabel {{ color: {self.TEXT}; background: transparent; }}"
            "QPushButton#barReset { color: #dbdee1; background: transparent; border: 1px solid transparent;"
            " border-radius: 8px; padding: 7px 12px; }"
            "QPushButton#barReset:hover { color: white; text-decoration: underline; }"
            "QPushButton#barReset:focus { border: 1px solid rgba(255,255,255,0.5); }"
        )
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide.finished.connect(self._slid)
        host.installEventFilter(self)
        self.hide()

    # ----- state ---------------------------------------------------------------------
    def is_shown(self) -> bool:
        """Whether the bar is (or is sliding) in view."""
        return self._wanted

    def set_count(self, count: int) -> None:
        """Name how many settings differ (0: say it in general)."""
        if count <= 0:
            self.message.setText(self.MESSAGE)
        else:
            self.message.setText(
                f"You have {count} change{'s' if count != 1 else ''} that {'is' if count == 1 else 'are'} not applied yet"
            )

    def host_width_needed(self) -> int:
        """The narrowest host that leaves the bar room for its note and both buttons."""
        self.ensurePolished()  # the buttons' style sheet padding counts
        self.layout().invalidate()
        return self.layout().totalMinimumSize().width() - 2 * self.SHADOW + 2 * (self.GAP + 4)

    def reserved_height(self) -> int:
        """How much room the page should leave free under its content while the bar shows."""
        self._reserved = self._size().height() - 2 * self.SHADOW + self.GAP + 8
        return self._reserved

    def set_shown(self, shown: bool) -> None:
        if shown == self._wanted:
            if shown and self._slide.state() != QPropertyAnimation.State.Running:
                self.setGeometry(QRect(self._target(), self._size()))
            return
        self._wanted = shown
        end = self._target() if shown else self._parked()
        if shown and not self.isVisible():
            self.setGeometry(QRect(self._parked(), self._size()))
            self.show()
        self.raise_()
        self._slide.stop()
        duration = self._duration()
        if duration <= 0 or not self._host.isVisible():
            self.move(end)
            self.setVisible(shown)
            return
        self._slide.setDuration(duration)
        self._slide.setStartValue(self.pos())
        self._slide.setEndValue(end)
        self._slide.start()

    def _slid(self) -> None:
        if not self._wanted:
            self.hide()

    def _duration(self) -> int:
        """The desktop's animation length: Breeze follows Plasma's animation speed, 0 is off."""
        hint = self.style().styleHint(QStyle.StyleHint.SH_Widget_Animation_Duration, None, self)
        return min(int(hint * 1.4), 400) if hint > 0 else 0

    # ----- geometry --------------------------------------------------------------------
    def _size(self) -> QSize:
        width = min(self._host.width() - 2 * (self.GAP + 4), self.MAX_WIDTH) + 2 * self.SHADOW
        width = max(width, 2 * self.SHADOW + 1)
        layout = self.layout()
        height = layout.totalHeightForWidth(width) if layout.hasHeightForWidth() else layout.totalSizeHint().height()
        return QSize(width, max(height, layout.totalMinimumSize().height()))

    def _target(self) -> QPoint:
        size = self._size()
        return QPoint(
            (self._host.width() - size.width()) // 2, self._host.height() - size.height() + self.SHADOW - self.GAP
        )

    def _parked(self) -> QPoint:
        return QPoint(self._target().x(), self._host.height())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 (Qt API)
        if watched is self._host and event.type() == QEvent.Type.Resize and self._wanted:
            if self._slide.state() == QPropertyAnimation.State.Running:
                self._slide.setEndValue(self._target())
                self.resize(self._size())
            else:
                self.setGeometry(QRect(self._target(), self._size()))
            before = self._reserved  # reserved_height() stores the new value
            reserved = self.reserved_height()
            if reserved != before:  # the note wraps onto more or fewer lines
                self.reserved_changed.emit(reserved)
        return False

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt API)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        shadow = self.SHADOW
        body = QRectF(self.rect()).adjusted(shadow, shadow, -shadow, -shadow)
        radius = 12.0
        painter.setPen(Qt.PenStyle.NoPen)
        for step in range(shadow, 0, -2):  # a soft shadow, a little below the bar
            painter.setBrush(QColor(0, 0, 0, max(2, int(36 * (1 - step / shadow) ** 2))))
            painter.drawRoundedRect(body.adjusted(-step, -step + 3, step, step + 3), radius + step, radius + step)
        painter.setBrush(self.SURFACE)
        painter.setPen(QPen(QColor(255, 255, 255, 22), 1))
        painter.drawRoundedRect(body, radius, radius)
        painter.end()
