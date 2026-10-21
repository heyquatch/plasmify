"""Build your own panels: pick a screen edge, choose bar or dock, drag widgets around.

The editor works on the long form of ``plasmify.panels`` (a list of panel dicts) and
emits ``changed`` whenever the user edits something. It never talks to Plasma itself.
"""

from __future__ import annotations

import copy
from typing import Any

from PyQt6.QtCore import QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from plasmify import panels as model
from plasmify.gui.preview import paint_panels
from plasmify.gui.widgets import AdaptiveRow, ChoiceTiles, SliderSpin, muted, tip

WIDGET_ICONS = {
    "launcher": "start-here-kde",
    "menu": "application-menu",
    "dashboard": "view-app-grid-symbolic",
    "tasks": "preferences-system-windows",
    "tasks_labels": "view-list-details",
    "windowlist": "window",
    "pager": "virtual-desktops",
    "spacer": "distribute-horizontal-x",
    "separator": "distribute-horizontal",
    "clock": "clock",
    "analog_clock": "clock",
    "tray": "preferences-desktop-notification",
    "media": "media-playback-start",
    "cpu": "cpu",
    "memory": "memory",
    "network": "network-wired",
    "showdesktop": "user-desktop",
    "trash": "user-trash",
    "logout": "system-log-out",
    "weather": "weather-clear",
    "colorpicker": "color-picker",
}
STARTER_LABELS = {
    "topbar-dock": "Bar + dock",
    "bottom-panel": "Classic",
    "top-panel": "Top bar",
    "top-bottom": "Top + bottom",
    "left-dock": "Side dock",
    "side-bar": "Side bar",
    "islands": "Islands",
    "dock-only": "Just a dock",
}
EDGE_NAMES = {"top": "Top", "bottom": "Bottom", "left": "Left", "right": "Right"}
ISLANDS_HINT = (
    "Islands: each Flexible space in the widget list starts a new piece. Before the first space is the "
    "start of the edge, between two spaces the centre, after the last one the end. Up to three pieces."
)
HIDING_LABELS = {
    "Always visible": "always",
    "Hides when a window touches it": "dodge",
    "Auto-hide (shows at the screen edge)": "autohide",
}


def wallpaper_gradient(rect: QRectF, colours: dict[str, str]) -> QLinearGradient:
    """A stand-in wallpaper in the palette's colours."""
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    accent = QColor(colours.get("accent", "#88c0d0"))
    accent.setAlpha(150)
    gradient.setColorAt(0, QColor(colours.get("crust", "#111111")))
    gradient.setColorAt(0.6, QColor(colours.get("base", "#222222")))
    gradient.setColorAt(1, accent)
    return gradient


def widget_icon(widget: str) -> QIcon:
    icon = QIcon.fromTheme(WIDGET_ICONS.get(widget, "plasmagik"))
    return icon if not icon.isNull() else QIcon.fromTheme("preferences-plugin")


class EdgePicker(QWidget):
    """A tiny screen. Click one of its edges to edit the panel there."""

    picked = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setFixedSize(176, 112)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click an edge of the screen to add or edit the panel there.")
        self.present: set[str] = set()
        self.selected = "top"
        self.accent = QColor("#88c0d0")
        self.colours: dict[str, str] = {}

    def zones(self) -> dict[str, QRectF]:
        w, h, t = self.width(), self.height(), 16.0
        return {
            "top": QRectF(t, 0, w - 2 * t, t),
            "bottom": QRectF(t, h - t, w - 2 * t, t),
            "left": QRectF(0, t, t, h - 2 * t),
            "right": QRectF(w - t, t, t, h - 2 * t),
        }

    def mousePressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        for edge, zone in self.zones().items():
            if zone.adjusted(-4, -4, 4, 4).contains(event.position()):
                self.selected = edge
                self.picked.emit(edge)
                self.update()
                return

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt API)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()
        screen = QRectF(self.rect()).adjusted(18, 18, -18, -18)
        path = QPainterPath()
        path.addRoundedRect(screen, 6, 6)
        p.fillPath(path, wallpaper_gradient(screen, self.colours))
        p.setPen(QPen(pal.mid().color(), 1))
        p.drawPath(path)
        for edge, zone in self.zones().items():
            bar = zone.adjusted(3, 3, -3, -3)
            if edge in self.present:
                colour = QColor(self.accent)
                colour.setAlpha(230 if edge == self.selected else 140)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(colour)
            else:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(pal.mid().color(), 1.2, Qt.PenStyle.DashLine))
            p.drawRoundedRect(bar, 4, 4)
            if edge == self.selected:
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(pal.highlight().color(), 2))
                p.drawRoundedRect(zone.adjusted(1, 1, -1, -1), 6, 6)
        p.end()


class WidgetList(QListWidget):
    """The widgets of one panel; drag to reorder, Delete to remove."""

    remove_requested = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setSpacing(4)
        self.setIconSize(QSize(22, 22))
        self.setMinimumHeight(86)
        self.setMaximumHeight(130)
        self.setToolTip("Drag widgets to reorder them. Select one and press Delete (or −) to remove it.")

    def keyPressEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            self.remove_requested.emit()  # the editor knows which widgets may go
            return
        super().keyPressEvent(event)


class PanelEditor(QWidget):
    changed = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self._panels: dict[str, dict[str, Any]] = {}
        self._edge = "top"
        self._loading = False
        self._colours: dict[str, str] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        outer.addWidget(muted("Start from a layout, then change anything: click a screen edge to edit its panel."))
        # Four starting points per row, fewer on a narrow window.
        self.starters = ChoiceTiles(
            [(STARTER_LABELS.get(name, name), name, hint) for name, (hint, _) in model.STARTERS.items()],
            QSize(84, 52),
            columns=4,
        )
        self.starters.setToolTip("Starting points. Picking one replaces your panels; you can edit everything after.")
        self.starters.changed.connect(self._use_starter)
        outer.addWidget(self.starters)

        # The little screen beside the panel's options, or above them when the card is narrow.
        row = AdaptiveRow(lead_min=176, lead_max=176, rest_min=250, spacing=12, share=0.3)
        self.edge_row = row
        self.edges = EdgePicker()
        self.edges.picked.connect(self._select_edge)
        row.addWidget(self.edges)
        side = QVBoxLayout()
        self.enabled = QCheckBox("Panel on the top edge")
        tip(self.enabled, "Add or remove the panel on this edge.")
        self.enabled.toggled.connect(self._toggle_panel)
        side.addWidget(self.enabled)
        self.form = QFormLayout()
        self.style_box = self._combo(
            {"Bar (full panel)": "bar", "Dock (floating island)": "dock", "Islands (separate pieces)": "islands"}
        )
        tip(
            self.style_box,
            "A bar is a classic panel; a dock is a floating island sized to its icons; islands split the "
            "panel into separate pieces at the flexible spaces (say, the clock in the middle and the tray "
            "on its own at the end).",
        )
        self.length_box = self._combo({"Full length": "fill", "Fit the widgets": "fit"})
        tip(self.length_box, "Span the whole edge, or only as long as the widgets need (an island).")
        self.align_box = self._combo({"Start": "start", "Centre": "center", "End": "end"})
        tip(self.align_box, "Where a fitted panel sits along its edge.")
        self.thickness = SliderSpin(*model.THICKNESS, " px")
        self.thickness.setToolTip("How thick the panel is. Thicker panels allow rounder corners.")
        self.thickness.valueChanged.connect(lambda _: self._edit())
        self.floating = QCheckBox("Floating (gap around it)")
        tip(self.floating, "A small gap around the panel. Needed for visible rounded corners.")
        self.floating.toggled.connect(lambda _: self._edit())
        self.hiding_box = self._combo(HIDING_LABELS)
        tip(self.hiding_box, "Always visible panels keep their space; hiding panels let windows use the whole screen.")
        self.form.addRow("Style:", self.style_box)
        self.form.addRow("Length:", self.length_box)
        self.form.addRow("Position:", self.align_box)
        self.form.addRow("Thickness:", self.thickness)
        self.form.addRow(self.floating)
        self.form.addRow("Visibility:", self.hiding_box)
        side.addLayout(self.form)
        self.islands_hint = muted(ISLANDS_HINT)
        side.addWidget(self.islands_hint)
        row.add_layout(side)
        outer.addLayout(row)

        widgets_row = QHBoxLayout()
        self.widget_list = WidgetList()
        self.widget_list.remove_requested.connect(self._remove_widget)
        self.widget_list.model().rowsMoved.connect(lambda *_: self._edit())
        self.widget_list.model().layoutChanged.connect(lambda *_: self._edit())
        self.widget_list.model().rowsRemoved.connect(lambda *_: self._edit())
        widgets_row.addWidget(self.widget_list, 1)
        buttons = QVBoxLayout()
        self.add_button = QToolButton()
        self.add_button.setIcon(QIcon.fromTheme("list-add"))
        self.add_button.setText("Add")
        self.add_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.add_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        tip(self.add_button, "Add a widget to this panel. Every widget ships with Plasma.")
        menu = QMenu(self)
        for widget, (_plugin, label, description, addon) in model.WIDGETS.items():
            action = QAction(widget_icon(widget), label, menu)
            action.setToolTip(description)
            if addon and not model.widget_available(widget):
                action.setText(f"{label} (needs kdeplasma-addons)")
                action.setEnabled(False)
            action.triggered.connect(lambda _=False, w=widget: self._add_widget(w))
            menu.addAction(action)
        menu.setToolTipsVisible(True)
        self.add_button.setMenu(menu)
        remove = QToolButton()
        remove.setIcon(QIcon.fromTheme("list-remove"))
        remove.setText("Remove")
        remove.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        tip(remove, "Remove the selected widget.")
        remove.clicked.connect(self._remove_widget)
        for button in (self.add_button, remove):
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            buttons.addWidget(button)
        buttons.addStretch(1)
        widgets_row.addLayout(buttons)
        outer.addWidget(muted("Widgets, in order (drag to rearrange):", wrap=False))
        outer.addLayout(widgets_row)

    # ----- public ------------------------------------------------------------------
    def set_colours(self, colours: dict[str, str], style: dict) -> None:
        self._colours = colours
        self.edges.accent = QColor(colours.get("accent", "#88c0d0"))
        self.edges.colours = colours
        self.edges.update()

        def draw(p: QPainter, rect: QRectF, name: str) -> None:
            path = QPainterPath()
            path.addRoundedRect(rect.adjusted(2, 2, -2, -2), 6, 6)
            p.fillPath(path, wallpaper_gradient(rect, colours))
            p.setClipPath(path)
            paint_panels(p, rect.adjusted(2, 2, -2, -2), model.starter(name), colours, style, rect.height() / 300)

        self.starters.paint_with(draw)

    def set_panels(self, panels: list[dict[str, Any]]) -> None:
        self._loading = True
        try:
            self._panels = {p["edge"]: copy.deepcopy(model.normalise(p)) for p in panels}
            self.starters.blockSignals(True)
            self.starters.clear_selection()
            self.starters.blockSignals(False)
            first = next(iter(self._panels), "top")
            self._select_edge(first)
            self.edges.selected = first
        finally:
            self._loading = False

    def panels(self) -> list[dict[str, Any]]:
        order = [e for e in model.EDGES if e in self._panels]
        return [copy.deepcopy(self._panels[e]) for e in order]

    def edit_all(self, change) -> None:
        """Let ``change(panels_by_edge)`` edit the panels in place (the "fix it" buttons)."""
        change(self._panels)
        self._select_edge(self._edge)
        self.changed.emit()

    # ----- internals ---------------------------------------------------------------
    def _combo(self, choices: dict[str, str]) -> QComboBox:
        box = QComboBox()
        for label, value in choices.items():
            box.addItem(label, value)
        box.currentIndexChanged.connect(lambda _: self._edit())
        return box

    def _use_starter(self) -> None:
        name = self.starters.value()
        if name is None:
            return
        self._panels = {p["edge"]: p for p in model.starter(name)}
        self._loading = True
        try:
            first = next(iter(self._panels))
            self.edges.selected = first
            self._select_edge(first)
        finally:
            self._loading = False
        self.changed.emit()

    def _select_edge(self, edge: str) -> None:
        self._edge = edge
        loading, self._loading = self._loading, True
        try:
            panel = self._panels.get(edge)
            self.enabled.setText(f"Panel on the {edge} edge")
            self.enabled.setChecked(panel is not None)
            self.enabled.setEnabled(panel is None or len(self._panels) > 1)
            self.enabled.setToolTip(
                "Every desktop keeps at least one panel." if panel and len(self._panels) == 1 else "Add or remove it."
            )
            shown = model.normalise(panel) if panel else model.normalise({"edge": edge})
            for box, key in (
                (self.style_box, "style"),
                (self.length_box, "length"),
                (self.align_box, "align"),
                (self.hiding_box, "hiding"),
            ):
                box.setCurrentIndex(max(box.findData(shown[key]), 0))
            self.thickness.setValue(int(shown["thickness"]))
            self.floating.setChecked(bool(shown["floating"]))
            self.widget_list.clear()
            for widget in shown["widgets"]:
                self._list_item(widget)
            for widget in (
                self.style_box,
                self.length_box,
                self.thickness,
                self.floating,
                self.hiding_box,
                self.widget_list,
                self.add_button,
            ):
                widget.setEnabled(panel is not None)
            self._sync()
        finally:
            self._loading = loading
        self.edges.present = set(self._panels)
        self.edges.update()

    def _sync(self) -> None:
        style = self.style_box.currentData()
        dock, islands = style == "dock", style == "islands"
        present = self._edge in self._panels
        fit = dock or self.length_box.currentData() == "fit"
        # Islands size and place each piece by itself: length and position do not apply.
        self.length_box.setEnabled(not dock and not islands and present)
        self.align_box.setEnabled(fit and not islands and present)
        self.floating.setEnabled(not dock and present)
        self.islands_hint.setVisible(islands and present)
        if islands and present:
            spaces = sum(
                1
                for i in range(self.widget_list.count())
                if self.widget_list.item(i).data(Qt.ItemDataRole.UserRole) == "spacer"
            )
            extra = " Too many spaces: remove some, only three pieces fit on an edge." if spaces > 2 else ""
            self.islands_hint.setText(ISLANDS_HINT + extra)

    def _list_item(self, widget: str) -> None:
        label = model.WIDGETS[widget][1]
        item = QListWidgetItem(widget_icon(widget), label)
        item.setData(Qt.ItemDataRole.UserRole, widget)
        item.setToolTip(model.WIDGETS[widget][2])
        self.widget_list.addItem(item)

    def _toggle_panel(self, on: bool) -> None:
        if self._loading:
            return
        if on and self._edge not in self._panels:
            vertical = self._edge in ("left", "right")
            widgets = ["launcher", "tasks"] if vertical else ["launcher", "tasks", "spacer", "tray", "clock"]
            self._panels[self._edge] = model.normalise({"edge": self._edge, "widgets": widgets})
        elif not on and len(self._panels) > 1:
            self._panels.pop(self._edge, None)
        self._select_edge(self._edge)
        self.changed.emit()

    def _edit(self) -> None:
        if self._loading or self._edge not in self._panels:
            return
        panel = self._panels[self._edge]
        panel["style"] = self.style_box.currentData()
        if panel["style"] == "dock":
            panel["length"] = "fit"
        else:
            panel["length"] = self.length_box.currentData()
        panel["align"] = self.align_box.currentData()
        panel["thickness"] = self.thickness.value()
        panel["floating"] = self.floating.isChecked() or panel["style"] == "dock"
        panel["hiding"] = self.hiding_box.currentData()
        widgets = [w for w in self._listed() if w] or ["spacer"]
        if panel["style"] == "islands":
            fixed = islands_widgets(widgets)
            if fixed != widgets:
                # Shown once the list has finished the change that called us.
                QTimer.singleShot(0, lambda edge=self._edge: self._edge == edge and self._select_edge(edge))
            widgets = fixed
        panel["widgets"] = widgets
        self._sync()
        self.changed.emit()

    def _listed(self) -> list[str]:
        return [self.widget_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.widget_list.count())]

    def _add_widget(self, widget: str) -> None:
        if self._edge not in self._panels or self.widget_list.count() >= model.MAX_WIDGETS:
            return
        islands = self.style_box.currentData() == "islands"
        if islands and widget == "spacer" and self._listed().count("spacer") >= model.MAX_ISLANDS - 1:
            return  # a fourth piece does not fit on an edge
        self._list_item(widget)
        self._edit()

    def _remove_widget(self) -> None:
        selected = self.widget_list.selectedItems()
        if not selected or self._edge not in self._panels:
            return
        rows = {self.widget_list.row(item) for item in selected}
        left = [w for row, w in enumerate(self._listed()) if row not in rows]
        if self.style_box.currentData() == "islands" and all(w == "spacer" for w in left):
            return  # islands need at least one widget besides the spaces
        for item in selected:
            self.widget_list.takeItem(self.widget_list.row(item))
        self._edit()


def islands_widgets(widgets: list[str]) -> list[str]:
    """``widgets`` as an islands panel can hold them: at most two flexible spaces (three pieces)
    and at least one widget besides them."""
    fixed: list[str] = []
    for widget in widgets:
        if widget != "spacer" or fixed.count("spacer") < model.MAX_ISLANDS - 1:
            fixed.append(widget)
    if all(widget == "spacer" for widget in fixed):
        fixed.append("clock")
    return fixed
