"""The theme studio: turn a wallpaper into a complete theme, watching it come together.

Four steps on the left, a large live preview on the right that follows every choice:
the wallpaper, the colours taken from it, the panels and how the desktop moves.
Everything is analysed on this computer; nothing is uploaded.
"""

from __future__ import annotations

import random
from pathlib import Path

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFormLayout,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from plasmify import generate, panels
from plasmify.capture import slugify
from plasmify.gui.panel_editor import wallpaper_gradient
from plasmify.gui.preview import PreviewWidget, paint_panels
from plasmify.gui.safe import plain_label
from plasmify.gui.widgets import IMAGE_SUFFIXES, ChoiceTiles, DropZone, SliderSpin, guard_wheel, muted, tip
from plasmify.presets import Preset

STEPS = (
    ("Wallpaper", "preferences-desktop-wallpaper", "Pick the picture everything starts from."),
    ("Colours", "preferences-desktop-color", "Dark or light, and the accent taken from your picture."),
    ("Style", "preferences-desktop", "Panels, corners, motion and fonts."),
    ("Finish", "dialog-ok-apply", "Name it and make it yours."),
)
MOODS = [
    ("Calm", "calm", "Gentle fades and a soft blur. Relaxed and quiet."),
    ("Lively", "lively", "Windows zoom open and minimise with the magic lamp."),
    ("Playful", "playful", "Everything lively does, plus wobbly windows."),
]
STUDIO_STARTERS = {
    "topbar-dock": "Bar + dock",
    "bottom-panel": "Classic",
    "top-panel": "Top bar",
    "left-dock": "Side dock",
    "islands": "Islands",
    "dock-only": "Just a dock",
}


def title_font(widget: QWidget, scale: float = 1.6) -> QFont:
    font = widget.font()
    font.setPointSizeF(font.pointSizeF() * scale)
    font.setWeight(QFont.Weight.Bold)
    return font


class HeroBanner(QWidget):
    """The step title: plain text in the window's own colours, with a thin line under it."""

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(64)
        self.title = ""
        self.subtitle = ""

    def set_text(self, title: str, subtitle: str) -> None:
        self.title, self.subtitle = title, subtitle
        self.update()

    def set_palette(self, palette: dict[str, str]) -> None:
        """Kept for callers; the title no longer takes colours from the theme being built."""

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt API)
        p = QPainter(self)
        rect = QRectF(self.rect()).adjusted(4, 0, -4, -1)
        p.setPen(self.palette().windowText().color())
        p.setFont(title_font(self, 1.45))
        p.drawText(rect.adjusted(0, 4, 0, -rect.height() / 2), Qt.AlignmentFlag.AlignVCenter, self.title)
        p.setFont(self.font())
        p.setPen(self.palette().placeholderText().color())
        p.drawText(rect.adjusted(0, rect.height() / 2, 0, -6), Qt.AlignmentFlag.AlignVCenter, self.subtitle)
        line = QColor(self.palette().windowText().color())
        line.setAlpha(50)
        p.setPen(line)
        p.drawLine(rect.bottomLeft(), rect.bottomRight())
        p.end()


class StepRail(QWidget):
    """The numbered steps on the left; finished steps can be clicked to go back."""

    def __init__(self, on_pick) -> None:
        super().__init__()
        self.setFixedWidth(210)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 8, 8)
        layout.setSpacing(4)
        self.buttons: list[QToolButton] = []
        for index, (name, icon, hint) in enumerate(STEPS):
            button = QToolButton()
            button.setText(f"  {index + 1}.  {name}")
            button.setIcon(QIcon.fromTheme(icon))
            button.setIconSize(QSize(22, 22))
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setCheckable(True)
            button.setToolTip(hint)
            button.setStyleSheet(
                "QToolButton { text-align: left; padding: 10px 8px; border-radius: 10px; border: none;"
                " font-weight: 600; }"
                "QToolButton:checked { background: palette(highlight); color: palette(highlighted-text); }"
                "QToolButton:hover:!checked { background: rgba(127,127,127,0.14); }"
                "QToolButton:disabled { color: palette(placeholder-text); }"
            )
            button.clicked.connect(lambda _=False, i=index: on_pick(i))
            layout.addWidget(button)
            self.buttons.append(button)
        layout.addStretch(1)
        layout.addWidget(muted("Everything is analysed on this computer. Nothing is uploaded."))

    def show_step(self, current: int, reached: int) -> None:
        for index, button in enumerate(self.buttons):
            button.setChecked(index == current)
            button.setEnabled(index <= reached)


class PaletteStrip(QWidget):
    """Every palette colour as one rounded strip."""

    KEYS = ("crust", "mantle", "base", "surface", "overlay", "subtext", "text", "accent",
            "red", "orange", "yellow", "green", "cyan", "blue", "magenta")  # fmt: skip

    def __init__(self) -> None:
        super().__init__()
        self.setFixedHeight(30)
        self.colours: dict[str, str] = {}
        self.setToolTip("The full palette: backgrounds, text, accent and the terminal colours.")

    def set_colours(self, colours: dict[str, str]) -> None:
        self.colours = colours
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802 (Qt API)
        if not self.colours:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        clip = QPainterPath()
        clip.addRoundedRect(rect, 10, 10)
        p.setClipPath(clip)
        width = rect.width() / len(self.KEYS)
        for index, key in enumerate(self.KEYS):
            p.fillRect(
                QRectF(rect.left() + index * width, rect.top(), width + 1, rect.height()), QColor(self.colours[key])
            )
        p.end()


class WallpaperWizard(QDialog):
    """Kept under this name for the main window; it is the theme studio."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Theme studio")
        self.resize(1180, 760)
        self.images: list[Path] = []
        self.swatches: list = []
        self.palette: dict[str, str] = {}
        self.wallpaper: QPixmap | None = None
        self.step = 0
        self.reached = 0

        root = QHBoxLayout(self)
        self.rail = StepRail(self.go_to)
        root.addWidget(self.rail)
        main = QVBoxLayout()
        root.addLayout(main, 1)

        self.banner = HeroBanner()
        main.addWidget(self.banner)

        body = QHBoxLayout()
        main.addLayout(body, 1)
        self.stack = QStackedWidget()
        self.stack.setMinimumWidth(430)
        body.addWidget(self.stack, 5)
        side = QVBoxLayout()
        self.preview = PreviewWidget()
        self.preview.setMinimumSize(448, 252)
        side.addWidget(self.preview)
        self.strip = PaletteStrip()
        side.addWidget(self.strip)
        self.contrast = plain_label()
        side.addWidget(self.contrast)
        side.addStretch(1)
        body.addLayout(side, 6)

        self.stack.addWidget(self._wallpaper_page())
        self.stack.addWidget(self._colours_page())
        self.stack.addWidget(self._style_page())
        self.stack.addWidget(self._finish_page())

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.back = QPushButton(QIcon.fromTheme("go-previous"), "Back")
        self.back.clicked.connect(lambda: self.go_to(self.step - 1))
        self.next = QPushButton(QIcon.fromTheme("go-next"), "Next")
        self.next.setDefault(True)
        self.next.setMinimumWidth(150)
        self.next.clicked.connect(self._next)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        buttons.addWidget(self.back)
        buttons.addWidget(self.next)
        main.addLayout(buttons)
        guard_wheel(self)  # the wheel never changes a slider the pointer happens to pass
        self.go_to(0)

    # ----- pages ---------------------------------------------------------------------
    def _wallpaper_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.drop = DropZone("Drop one picture, or several for a slideshow", multiple=True, height=230)
        self.drop.picked.connect(self.add_images)
        self.drop.cleared.connect(self.clear_images)
        layout.addWidget(self.drop)
        row = QHBoxLayout()
        current = generate.current_wallpaper()
        use_current = QPushButton(QIcon.fromTheme("preferences-desktop-wallpaper"), "Use my current wallpaper")
        use_current.setEnabled(current is not None)
        tip(use_current, str(current) if current else "Your current wallpaper is not a plain picture.")
        use_current.clicked.connect(lambda: self.add_images([current]) if current else None)
        clear = QPushButton(QIcon.fromTheme("edit-clear"), "Start over")
        clear.clicked.connect(self.clear_images)
        row.addWidget(use_current)
        row.addWidget(clear)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addWidget(muted("Tip: right-click the box to empty it. Up to 30 pictures, PNG, JPEG, WebP or GIF."))
        layout.addStretch(1)
        return page

    def _colours_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.mode = ChoiceTiles(
            [("Dark", True, "Dark backgrounds, light text."), ("Light", False, "Light backgrounds, dark text.")],
            QSize(150, 84),
        )
        self.mode.changed.connect(self.refresh_candidates)
        layout.addWidget(plain_label("Dark or light"))
        layout.addWidget(self.mode)
        layout.addSpacing(6)
        layout.addWidget(plain_label("Accent, taken from your picture"))
        self.accent_row = QHBoxLayout()
        self.accent_row.setSpacing(10)
        self.accent_group = QButtonGroup(self)
        self.accent_group.idClicked.connect(lambda _: self.update_preview())
        holder = QHBoxLayout()
        holder.addLayout(self.accent_row)
        holder.addStretch(1)
        layout.addLayout(holder)
        layout.addSpacing(6)
        self.vibrancy = SliderSpin(40, 130, " %")
        self.vibrancy.setValue(100)
        self.vibrancy.setToolTip("Lower is calmer and greyer, higher is more colourful.")
        self.vibrancy.valueChanged.connect(lambda _: self.update_preview())
        form = QFormLayout()
        form.addRow("Colourfulness:", self.vibrancy)
        layout.addLayout(form)
        layout.addStretch(1)
        return page

    def _style_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(plain_label("Panels"))
        self.layout_tiles = ChoiceTiles(
            [(label, n, panels.STARTERS[n][0]) for n, label in STUDIO_STARTERS.items()], QSize(110, 62), columns=3
        )
        self.layout_tiles.changed.connect(self.update_preview)
        layout.addWidget(self.layout_tiles)
        form = QFormLayout()
        self.corners = SliderSpin(0, 14, " px")
        self.corners.setValue(10)
        self.corners.setToolTip("Rounded panel corners. Fine tune everything later in Customize.")
        self.corners.valueChanged.connect(lambda _: self.update_preview())
        self.see_through = SliderSpin(40, 100, " %")
        self.see_through.setValue(85)
        self.see_through.setToolTip("Panel opacity: lower lets the wallpaper shine through.")
        self.see_through.valueChanged.connect(lambda _: self.update_preview())
        form.addRow("Corners:", self.corners)
        form.addRow("Opacity:", self.see_through)
        layout.addLayout(form)
        layout.addWidget(plain_label("Motion"))
        self.mood_tiles = ChoiceTiles(MOODS, QSize(96, 46))
        layout.addWidget(self.mood_tiles)
        layout.addWidget(plain_label("Fonts"))
        self.font_tiles = ChoiceTiles(
            [
                (ui, label, f"{ui} for the interface, {mono} for code.")
                for label, (ui, mono, _) in generate.FONT_CHOICES.items()
            ],  # fmt: skip
            QSize(110, 46),
        )
        layout.addWidget(self.font_tiles)
        surprise = QPushButton(QIcon.fromTheme("media-playlist-shuffle"), "Surprise me")
        tip(
            surprise,
            "Rolls the accent, colourfulness, panels, corners, opacity, motion and fonts. Click as often as you like.",
        )
        surprise.clicked.connect(self.surprise)
        layout.addWidget(surprise, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addStretch(1)
        return page

    def _finish_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setMaxLength(60)
        self.name.setPlaceholderText("Sunset Lofi")
        self.name.textChanged.connect(lambda _: self._sync_buttons())
        self.description = QLineEdit()
        self.description.setMaxLength(300)
        self.description.setPlaceholderText("Warm colours from my favourite wallpaper.")
        form.addRow("Name:", self.name)
        form.addRow("Description:", self.description)
        layout.addLayout(form)
        self.splash = QCheckBox("Matching splash screen while Plasma starts")
        self.splash.setChecked(True)
        tip(self.splash, "Your wallpaper, your accent and a progress bar while you log in.")
        self.apply_now_box = QCheckBox("Apply it right away")
        self.apply_now_box.setChecked(True)
        layout.addWidget(self.splash)
        layout.addWidget(self.apply_now_box)
        self.summary = QFrame()
        self.summary.setObjectName("summary")
        self.summary.setStyleSheet(
            "QFrame#summary { background: palette(alternate-base); border-radius: 12px; border: 1px solid "
            "rgba(127,127,127,0.28); }"
        )
        self.summary_text = plain_label(wrap=True)
        inner = QVBoxLayout(self.summary)
        inner.addWidget(self.summary_text)
        layout.addWidget(self.summary)
        layout.addWidget(
            muted("You can change every detail afterwards in the Customize tab, and share it with friends.")
        )
        layout.addStretch(1)
        return page

    # ----- navigation ------------------------------------------------------------------
    def go_to(self, step: int) -> None:
        if step < 0 or step > self.reached:
            return
        self.step = step
        name, _icon, hint = STEPS[step]
        self.banner.set_text(name, hint)
        self.stack.setCurrentIndex(step)
        self._fade(self.stack.currentWidget())
        self.rail.show_step(step, self.reached)
        if step == 3:
            self._fill_summary()
        self._sync_buttons()

    def _next(self) -> None:
        if self.step == 0:
            if not self.images:
                return
            self._analyse()
        if self.step == len(STEPS) - 1:
            self.accept()
            return
        self.reached = max(self.reached, self.step + 1)
        self.go_to(self.step + 1)

    def _sync_buttons(self) -> None:
        self.back.setEnabled(self.step > 0)
        last = self.step == len(STEPS) - 1
        self.next.setText("Create theme" if last else "Next")
        self.next.setIcon(QIcon.fromTheme("dialog-ok-apply" if last else "go-next"))
        if self.step == 0:
            self.next.setEnabled(bool(self.images))
        elif last:
            text = self.name.text().strip()
            self.next.setEnabled(bool(text) and generate.schema.text(60)(text) is None)
        else:
            self.next.setEnabled(bool(self.palette))

    def _fade(self, widget: QWidget) -> None:
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", widget)
        animation.setDuration(220)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(lambda: widget.setGraphicsEffect(None))
        animation.start()

    # ----- wallpaper ---------------------------------------------------------------------
    def add_images(self, paths: list[Path]) -> None:
        for path in paths:
            usable = path.suffix.lower() in IMAGE_SUFFIXES and path not in self.images
            if usable and len(self.images) < 30 and not QPixmap(str(path)).isNull():
                self.images.append(path)
        self.drop.show_images(self.images, f"{len(self.images)} picture(s)" if self.images else "")
        if self.images:
            self.wallpaper = QPixmap(str(self.images[0])).scaled(
                1280, 720, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
            )
            self.swatches = []
            self.reached = 0
        self._sync_buttons()
        self.rail.show_step(self.step, self.reached)

    def clear_images(self) -> None:
        self.images.clear()
        self.swatches = []
        self.palette = {}
        self.reached = 0
        self.drop.show_caption("")
        self._sync_buttons()
        self.rail.show_step(self.step, self.reached)

    def _analyse(self) -> None:
        if self.swatches:
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            self.swatches = generate.analyse(self.images)
        finally:
            QApplication.restoreOverrideCursor()
        if self.mode.value() is None:
            self.mode.set_value(True)
            self.layout_tiles.set_value("topbar-dock")
            self.mood_tiles.set_value("lively")
            self.font_tiles.set_value(next(iter(generate.FONT_CHOICES)))
        self.refresh_candidates()

    # ----- colours -------------------------------------------------------------------------
    def refresh_candidates(self) -> None:
        if not self.swatches:
            return
        for button in self.accent_group.buttons():
            self.accent_group.removeButton(button)
            button.deleteLater()
        dark = bool(self.mode.value())
        for index, colour in enumerate(generate.accent_candidates(self.swatches, dark, count=6)):
            button = QPushButton()
            button.setCheckable(True)
            button.setFixedSize(46, 46)
            button.setToolTip(f"Accent {colour}")
            button.setAccessibleName(f"Accent {colour}")  # a screen reader has no colour to show
            button.setProperty("colour", colour)
            button.setStyleSheet(
                f"QPushButton {{ background: {colour}; border-radius: 23px; border: 3px solid transparent; }}"
                "QPushButton:checked { border: 4px solid palette(text); }"
                "QPushButton:hover:!checked { border: 3px solid palette(mid); }"
            )
            self.accent_group.addButton(button, index)
            self.accent_row.addWidget(button)
            button.setChecked(index == 0)
        self._paint_mode_tiles()
        self.update_preview()

    def _paint_mode_tiles(self) -> None:
        candidates = generate.accent_candidates(self.swatches, True, count=1) or ["#88c0d0"]
        palettes = {dark: generate.build_palette(self.swatches, candidates[0], dark, 1.0) for dark in (True, False)}

        def draw(p: QPainter, rect: QRectF, dark: bool) -> None:
            colours = palettes[dark]
            box = rect.adjusted(2, 2, -2, -2)
            path = QPainterPath()
            path.addRoundedRect(box, 10, 10)
            p.fillPath(path, QColor(colours["base"]))
            window = QRectF(box.left() + 14, box.top() + 14, box.width() - 28, box.height() - 28)
            inner = QPainterPath()
            inner.addRoundedRect(window, 6, 6)
            p.fillPath(inner, QColor(colours["mantle"]))
            for i, key in enumerate(("text", "subtext", "accent")):
                line = QRectF(window.left() + 10, window.top() + 10 + i * 12, window.width() * (0.7 - i * 0.15), 6)
                bar = QPainterPath()
                bar.addRoundedRect(line, 3, 3)
                p.fillPath(bar, QColor(colours[key]))

        self.mode.paint_with(draw)

    def update_preview(self) -> None:
        button = self.accent_group.checkedButton()
        if button is None or not self.swatches:
            return
        dark = bool(self.mode.value())
        self.palette = generate.build_palette(
            self.swatches, button.property("colour"), dark, self.vibrancy.value() / 100
        )
        preset = self._preview_preset()
        self.preview.set_preset(preset, self.wallpaper)
        self.strip.set_colours(self.palette)
        self.banner.set_palette(self.palette)
        ratio = generate.contrast(self.palette["text"], self.palette["base"])
        grade = "excellent" if ratio >= 7 else "good" if ratio >= 4.5 else "low"
        self.contrast.setText(f"Text contrast {ratio:.1f}:1 ({grade}; 7:1 or more is excellent)")
        self._paint_style_tiles()
        self._sync_buttons()

    def surprise(self) -> None:
        rng = random.Random()  # noqa: S311 (a design suggestion, not security)
        buttons = self.accent_group.buttons()
        if buttons:
            rng.choice(buttons).setChecked(True)
        self.layout_tiles.set_value(rng.choice(list(STUDIO_STARTERS)))
        self.mood_tiles.set_value(rng.choice([m[1] for m in MOODS]))
        self.font_tiles.set_value(rng.choice(list(generate.FONT_CHOICES)))
        self.corners.setValue(rng.randint(0, 14))
        self.see_through.setValue(rng.randint(70, 100))
        self.vibrancy.setValue(rng.randint(80, 125))
        self.update_preview()

    def _preview_preset(self) -> Preset:
        starter = self.layout_tiles.value() or "topbar-dock"
        return Preset(
            {
                "meta": {"id": "preview", "name": "", "description": ""},
                "palette": self.palette,
                "layout": {"panels": panels.compact(panels.starter(starter))},
                "panel_style": self._panel_style(),
            },
            "preview",
        )

    def _panel_style(self) -> dict:
        return {"radius": self.corners.value(), "opacity": self.see_through.value() / 100, "border": "none"}

    def _paint_style_tiles(self) -> None:
        colours, style = self.palette, self._panel_style()

        def layout_tile(p: QPainter, rect: QRectF, name: str) -> None:
            path = QPainterPath()
            path.addRoundedRect(rect.adjusted(2, 2, -2, -2), 6, 6)
            p.fillPath(path, wallpaper_gradient(rect, colours))
            p.setClipPath(path)
            paint_panels(p, rect.adjusted(2, 2, -2, -2), panels.starter(name), colours, style, rect.height() / 300)

        def mood_tile(p: QPainter, rect: QRectF, mood: str) -> None:
            box = rect.adjusted(2, 2, -2, -2)
            path = QPainterPath()
            path.addRoundedRect(box, 8, 8)
            p.fillPath(path, QColor(colours["mantle"]))
            p.setPen(Qt.PenStyle.NoPen)
            for i in range(3):
                colour = QColor(colours[("accent", "blue", "magenta")[i]])
                colour.setAlpha(255 - i * 70)
                size = {"calm": 10, "lively": 14 + i * 3, "playful": 12 + (i % 2) * 8}[mood]
                y = box.center().y() - size / 2 + (i - 1) * (6 if mood == "playful" else 0)
                p.setBrush(colour)
                p.drawRoundedRect(QRectF(box.left() + 14 + i * 26, y, size, size), size / 3, size / 3)

        def font_tile(p: QPainter, rect: QRectF, label: str) -> None:
            ui, mono, _packages = generate.FONT_CHOICES[label]
            box = rect.adjusted(2, 2, -2, -2)
            path = QPainterPath()
            path.addRoundedRect(box, 8, 8)
            p.fillPath(path, QColor(colours["mantle"]))
            p.setPen(QColor(colours["text"]))
            p.setFont(QFont(ui, 15))
            p.drawText(box.adjusted(10, 2, 0, -box.height() / 2), Qt.AlignmentFlag.AlignVCenter, "Aa")
            p.setPen(QColor(colours["accent"]))
            p.setFont(QFont(mono, 9))
            p.drawText(box.adjusted(10, box.height() / 2, 0, -2), Qt.AlignmentFlag.AlignVCenter, "{ }=>")

        self.layout_tiles.paint_with(layout_tile)
        self.mood_tiles.paint_with(mood_tile)
        self.font_tiles.paint_with(font_tile)

    # ----- result ----------------------------------------------------------------------------
    def _fill_summary(self) -> None:
        mood = dict((v, k) for k, v, _ in MOODS).get(self.mood_tiles.value(), "")
        lines = [
            f"{len(self.images)} wallpaper(s){' as a slideshow' if len(self.images) > 1 else ''}",
            f"{'Dark' if self.mode.value() else 'Light'} palette with accent {self.palette.get('accent', '')}",
            f"Panels: {STUDIO_STARTERS.get(self.layout_tiles.value() or '', '').lower()}, "
            f"{self.corners.value()} px corners",
            f"Motion: {mood.lower()}",
            f"Fonts: {self.font_tiles.value()}",
            "Papirus icons with matching folder colours, a themed terminal and prompt",
        ]
        self.summary_text.setText("\n".join(f"•  {line}" for line in lines))

    def result_preset(self) -> tuple[dict, dict[str, Path]]:
        name = self.name.text().strip()
        description = self.description.text().strip() or "Made from my own wallpaper with Plasmify."
        return generate.build_preset(
            name=name,
            description=description,
            images=self.images,
            palette=self.palette,
            dark=bool(self.mode.value()),
            layout="topbar-dock",
            mood=self.mood_tiles.value() or "lively",
            fonts=self.font_tiles.value() or next(iter(generate.FONT_CHOICES)),
            preset_id=slugify(name),
            panels=panels.starter(self.layout_tiles.value() or "topbar-dock"),
            panel_style=self._panel_style(),
            splash=self.splash.isChecked(),
        )

    @property
    def apply_now(self) -> bool:
        return self.apply_now_box.isChecked()
