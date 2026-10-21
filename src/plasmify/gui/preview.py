"""A miniature desktop painted from a preset's palette and layout.

Used as the preset preview, so previews are always accurate and need no
screenshots or downloads.
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QSizePolicy, QWidget

from plasmify.panels import layout_panels, pieces
from plasmify.presets import Preset

ANSI = ("red", "green", "yellow", "blue", "magenta", "cyan")
MAX_HEIGHT = 340


def _c(preset: Preset, key: str, alpha: int = 255) -> QColor:
    color = QColor(preset.palette.colors[key])
    color.setAlpha(alpha)
    return color


def _rounded(painter: QPainter, rect: QRectF, radius: float, color: QColor) -> None:
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    painter.fillPath(path, color)


# Length of each widget along the panel, in "icon" units (spacers share what is left).
WIDGET_UNITS = {"tasks": 4.4, "tasks_labels": 6.0, "clock": 2.6, "tray": 2.4, "pager": 1.8, "separator": 0.5,
                "media": 3.0, "cpu": 1.8, "memory": 1.8, "network": 1.8, "weather": 2.2}  # fmt: skip
TASK_COLOURS = ("blue", "accent", "green", "orange", "subtext")


def paint_panels(
    p: QPainter, rect: QRectF, panels: list[dict], colours: dict[str, str], style: dict, scale: float, draw: bool = True
) -> QRectF:
    """Draw ``panels`` (long form) inside ``rect``; returns the area left for windows.

    ``scale`` converts panel pixels to preview pixels (previews exaggerate a little so
    a 32 px bar is still visible in a thumbnail)."""

    def colour(key: str, alpha: int = 255) -> QColor:
        value = QColor(colours.get(key, key if key.startswith("#") else "#888888"))
        value.setAlpha(alpha)
        return value

    # The panel style step's defaults. A theme without [panel_style] keeps the user's own Plasma
    # style (see keep_own_style): drawn the same way, it looks close to Breeze in the theme's colours.
    alpha = int(255 * float(style.get("opacity", 0.85)))
    fill = colour(style.get("colour", "mantle"), alpha)
    border_key = {"accent": "accent", "subtle": "overlay"}.get(style.get("border", "none"))
    radius_px = float(style.get("radius", 6))
    free = QRectF(rect)
    # Horizontal panels first, so side panels sit between them (as in Plasma).
    for entry in sorted(panels, key=lambda q: q["edge"] in ("left", "right")):
        area = QRectF(free)  # islands: every piece is placed on the same edge of the same area
        parts = pieces(entry)
        squeeze, offsets, _lengths = _fit_islands(parts, area, scale) if len(parts) > 1 else (1.0, [None], [])
        for panel, offset in zip(parts, offsets, strict=True):
            _paint_piece(p, panel, area, colour, fill, border_key, radius_px, scale, draw, squeeze, offset)
        thick = max(entry["thickness"] * scale, 4.0)
        gap = 4 * scale if entry["floating"] or entry["style"] == "dock" else 0.0
        _reserve(free, entry, thick + 2 * gap)
    return free


def _fit_islands(parts: list[dict], free: QRectF, scale: float) -> tuple[float, list[float], list[float]]:
    """Icon scale, offsets and lengths for the pieces of one islands panel, so they never overlap.

    Previews draw panels larger than life (a thumbnail still shows a 32 px bar), so pieces
    that sit well apart on a real screen can collide here: shrink their icons until they fit
    with a gap, and keep the middle piece between its neighbours."""
    first = parts[0]
    vertical = first["edge"] in ("left", "right")
    thick = max(first["thickness"] * scale, 4.0)
    gap = 4 * scale if first["floating"] or first["style"] == "dock" else 0.0
    room = (free.height() if vertical else free.width()) - 2 * gap
    sep = max(gap, thick * 0.3)
    icon_runs = [
        sum(WIDGET_UNITS.get(w, 1.0) for w in q["widgets"] if w != "spacer") * thick * 0.62 * 1.18 for q in parts
    ]
    fixed = len(parts) * thick * 0.5 + sep * (len(parts) - 1)
    squeeze = 1.0
    if sum(icon_runs) and fixed + sum(icon_runs) > room:
        squeeze = max(0.3, (room - fixed) / sum(icon_runs))
    lengths = {q["align"]: run * squeeze + thick * 0.5 for q, run in zip(parts, icon_runs, strict=True)}
    offsets = []
    for q in parts:
        length = lengths[q["align"]]
        if q["align"] == "start":
            offsets.append(0.0)
        elif q["align"] == "end":
            offsets.append(max(0.0, room - length))
        else:
            low = lengths["start"] + sep if "start" in lengths else 0.0
            high = room - length - (lengths["end"] + sep if "end" in lengths else 0.0)
            offsets.append(min(max((room - length) / 2, low), max(high, low)))
    return squeeze, offsets, [lengths[q["align"]] for q in parts]


def _paint_piece(
    p: QPainter,
    panel: dict,
    free: QRectF,
    colour,
    fill,
    border_key,
    radius_px,
    scale,
    draw,
    squeeze: float = 1.0,
    offset: float | None = None,
) -> None:
    """One Plasma panel (an islands panel is several) inside ``free``. ``squeeze`` and
    ``offset`` come from _fit_islands for the pieces of an islands panel."""
    vertical = panel["edge"] in ("left", "right")
    thick = max(panel["thickness"] * scale, 4.0)
    gap = 4 * scale if panel["floating"] or panel["style"] == "dock" else 0.0
    units = sum(WIDGET_UNITS.get(w, 1.0) for w in panel["widgets"] if w != "spacer")
    icon = thick * 0.62 * squeeze
    span = free.height() if vertical else free.width()
    fit = panel["style"] == "dock" or panel["length"] == "fit"
    length = min(units * icon * 1.18 + thick * 0.5, span - 2 * gap) if fit else span - 2 * gap
    start_room = span - 2 * gap - length
    if offset is None:
        offset = {"start": 0.0, "center": start_room / 2, "end": start_room}[panel["align"]] if fit else 0.0
    if vertical:
        x = free.left() + gap if panel["edge"] == "left" else free.right() - gap - thick
        box = QRectF(x, free.top() + gap + offset, thick, length)
    else:
        y = free.top() + gap if panel["edge"] == "top" else free.bottom() - gap - thick
        box = QRectF(free.left() + gap + offset, y, length, thick)
    radius = min(radius_px * scale * 1.4, thick / 2) if gap else 0.0
    if not draw:
        return
    _rounded(p, box, radius, fill)
    if border_key:
        path = QPainterPath()
        path.addRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
        p.strokePath(path, QPen(colour(border_key, 200), max(scale, 1.0)))
    # Widgets along the panel.
    spacers = panel["widgets"].count("spacer")
    inner = (box.height() if vertical else box.width()) - thick * 0.5
    extra = max(inner - units * icon * 1.18, 0) / spacers if spacers else 0
    pos = thick * 0.25
    tasks_drawn = 0
    for widget in panel["widgets"]:
        size = WIDGET_UNITS.get(widget, 1.0) * icon * 1.18 if widget != "spacer" else extra
        if widget != "spacer":
            cross = (thick - icon) / 2
            if vertical:
                cell = QRectF(box.left() + cross, box.top() + pos, icon, size - icon * 0.18)
            else:
                cell = QRectF(box.left() + pos, box.top() + cross, size - icon * 0.18, icon)
            _paint_widget(p, widget, cell, icon, colour, vertical, tasks_drawn)
            tasks_drawn += widget in ("tasks", "tasks_labels")
        pos += size


def _reserve(free: QRectF, panel: dict, reserve: float) -> None:
    """Shrink the window area for panels that keep their space (always visible)."""
    if panel["hiding"] != "always":
        return
    if panel["edge"] == "top":
        free.setTop(free.top() + reserve)
    elif panel["edge"] == "bottom":
        free.setBottom(free.bottom() - reserve)
    elif panel["edge"] == "left":
        free.setLeft(free.left() + reserve)
    else:
        free.setRight(free.right() - reserve)


def _paint_widget(p: QPainter, widget: str, cell: QRectF, icon: float, colour, vertical: bool, nth: int) -> None:
    r = icon * 0.28
    if widget in ("launcher", "menu", "dashboard"):
        _rounded(p, QRectF(cell.left(), cell.top(), icon, icon), icon / 2, colour("accent"))
    elif widget in ("tasks", "tasks_labels"):
        count = 4 if widget == "tasks" else 3
        step = (cell.height() if vertical else cell.width()) / count
        for i in range(count):
            key = TASK_COLOURS[(i + nth) % len(TASK_COLOURS)]
            if vertical:
                box = QRectF(cell.left(), cell.top() + i * step, icon, min(step * 0.86, icon))
            else:
                box = QRectF(
                    cell.left() + i * step, cell.top(), step * 0.86 if widget == "tasks_labels" else icon, icon
                )
            _rounded(p, box, r, colour(key, 230))
    elif widget in ("clock", "analog_clock", "media", "weather"):
        bar = QRectF(cell.left(), cell.center().y() - icon * 0.22, cell.width(), icon * 0.44)
        if vertical:
            bar = QRectF(cell.center().x() - icon * 0.22, cell.top(), icon * 0.44, cell.height())
        _rounded(p, bar, icon * 0.22, colour("subtext", 210))
    elif widget == "tray":
        for i in range(3):
            if vertical:
                dot = QRectF(cell.left() + icon * 0.3, cell.top() + i * icon * 0.9, icon * 0.4, icon * 0.4)
            else:
                dot = QRectF(cell.left() + i * icon * 0.9, cell.top() + icon * 0.3, icon * 0.4, icon * 0.4)
            _rounded(p, dot, icon * 0.2, colour("subtext", 200))
    elif widget == "pager":
        for i in range(2):
            if vertical:
                box = QRectF(cell.left(), cell.top() + i * icon * 0.9, icon, icon * 0.7)
            else:
                box = QRectF(cell.left() + i * icon * 0.9, cell.top() + icon * 0.15, icon * 0.7, icon * 0.7)
            _rounded(p, box, icon * 0.12, colour("overlay", 220 if i == 0 else 130))
    elif widget in ("cpu", "memory", "network"):
        key = {"cpu": "green", "memory": "yellow", "network": "cyan"}[widget]
        _rounded(p, QRectF(cell.left(), cell.top() + icon * 0.2, cell.width(), icon * 0.6), r, colour(key, 160))
    elif widget != "separator":
        _rounded(p, QRectF(cell.left(), cell.top(), icon, icon), r, colour("overlay", 200))


def paint_preview(painter: QPainter, rect: QRectF, preset: Preset, wallpaper: QPixmap | None = None) -> None:
    p = painter
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(rect, 10, 10)
    p.setClipPath(clip)

    w, h = rect.width(), rect.height()
    unit = h / 100

    # Wallpaper: the real one when available, otherwise a palette gradient.
    if wallpaper is not None and not wallpaper.isNull():
        scaled = wallpaper.scaled(
            rect.size().toSize(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        p.drawPixmap(rect.topLeft(), scaled)
    else:
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0, _c(preset, "crust"))
        gradient.setColorAt(0.55, _c(preset, "base"))
        gradient.setColorAt(1, _c(preset, "accent", 140))
        p.fillRect(rect, gradient)

    layout_section = preset.section("layout")
    panel_list = layout_panels(layout_section if layout_section else {"name": "bottom-panel"})
    panel_style = preset.section("panel_style")
    area = paint_panels(p, rect, panel_list, preset.palette.colors, panel_style, h / 560, draw=False)
    top, bottom = area.top() + unit, area.bottom() - unit
    left_edge, right_edge = area.left(), area.right()
    w = right_edge - left_edge

    # A file manager-ish window
    win = QRectF(left_edge + 6 * unit, top + 5 * unit, w * 0.48, (bottom - top) - 10 * unit)
    _rounded(p, win.adjusted(-0.4 * unit, -0.4 * unit, 0.4 * unit, 0.4 * unit), 2 * unit, _c(preset, "crust", 160))
    _rounded(p, win, 2 * unit, _c(preset, "base"))
    title = QRectF(win.left(), win.top(), win.width(), 6 * unit)
    _rounded(p, title, 2 * unit, _c(preset, "mantle"))
    for i, key in enumerate(("yellow", "green", "red")):
        _rounded(
            p,
            QRectF(title.right() - (i + 1) * 3.6 * unit - unit, title.top() + 1.8 * unit, 2.4 * unit, 2.4 * unit),
            1.2 * unit,
            _c(preset, key),
        )
    side = QRectF(win.left(), title.bottom(), win.width() * 0.28, win.height() - title.height())
    p.fillRect(side, _c(preset, "mantle"))
    _rounded(
        p,
        QRectF(side.left() + unit, side.top() + 2 * unit, side.width() - 2 * unit, 4 * unit),
        unit,
        _c(preset, "accent", 90),
    )
    for i in range(4):
        _rounded(
            p,
            QRectF(side.left() + 2 * unit, side.top() + (9 + 5 * i) * unit, side.width() * 0.6, 1.6 * unit),
            0.8 * unit,
            _c(preset, "subtext", 150),
        )
    grid_left = side.right() + 3 * unit
    cell = 9 * unit
    for row in range(2):
        for col in range(3):
            x = grid_left + col * (cell + 3 * unit)
            y = title.bottom() + 4 * unit + row * (cell + 5 * unit)
            if x + cell < win.right() - unit and y + cell < win.bottom():
                _rounded(p, QRectF(x, y, cell, cell * 0.8), 1.2 * unit, _c(preset, "accent", 210))

    # A terminal
    term = QRectF(win.right() + 4 * unit, top + 12 * unit, right_edge - win.right() - 9 * unit, (bottom - top) * 0.62)
    opacity = int(255 * float(preset.section("terminal").get("opacity", 0.92)))
    _rounded(p, term, 2 * unit, _c(preset, "base", opacity))
    _rounded(p, QRectF(term.left(), term.top(), term.width(), 4.5 * unit), 2 * unit, _c(preset, "mantle", opacity))
    y = term.top() + 7.5 * unit
    _rounded(p, QRectF(term.left() + 2 * unit, y, 9 * unit, 2.6 * unit), 1.3 * unit, _c(preset, "accent"))
    _rounded(p, QRectF(term.left() + 11.5 * unit, y, 10 * unit, 2.6 * unit), 1.3 * unit, _c(preset, "blue"))
    for i, key in enumerate(("text", "subtext", "green", "subtext")):
        y += 4.5 * unit
        width = term.width() * (0.75 - 0.12 * (i % 3))
        _rounded(
            p, QRectF(term.left() + 2 * unit, y, max(width, 4 * unit), 1.6 * unit), 0.8 * unit, _c(preset, key, 210)
        )
    y += 5.5 * unit
    for i, key in enumerate(ANSI):
        x = term.left() + 2 * unit + i * 4.2 * unit
        if x + 3 * unit < term.right():
            _rounded(p, QRectF(x, y, 3 * unit, 3 * unit), 1.5 * unit, _c(preset, key))

    # Panels last: docks that hide float above windows.
    paint_panels(p, rect, panel_list, preset.palette.colors, panel_style, h / 560)

    p.restore()


class PreviewWidget(QWidget):
    """Scales the mock desktop to its size, keeping a 16:9 ratio."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.preset: Preset | None = None
        self.wallpaper: QPixmap | None = None
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumHeight(200)
        self.setMaximumHeight(MAX_HEIGHT)

    def set_preset(self, preset: Preset, wallpaper: QPixmap | None = None) -> None:
        self.preset = preset
        self.wallpaper = wallpaper
        self.update()

    def hasHeightForWidth(self) -> bool:  # noqa: N802 (Qt API)
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 (Qt API)
        return min(int(width * 9 / 16), MAX_HEIGHT)

    def sizeHint(self) -> QSize:  # noqa: N802 (Qt API)
        return QSize(640, 360)

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt API)
        if self.preset is None:
            return
        width = self.width()
        height = min(self.height(), int(width * 9 / 16))
        width = int(height * 16 / 9)
        rect = QRectF(0, 0, width, height)
        painter = QPainter(self)
        paint_preview(painter, rect, self.preset, self.wallpaper)
        painter.end()


def preview_pixmap(preset: Preset, size: QSize, wallpaper: QPixmap | None = None) -> QPixmap:
    pixmap = QPixmap(size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    paint_preview(painter, QRectF(0, 0, size.width(), size.height()), preset, wallpaper)
    painter.end()
    return pixmap
