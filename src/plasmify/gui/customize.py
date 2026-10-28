"""The Customize tab: fine-tune any preset before applying it.

Only options the user actually touches become overrides; everything else keeps
the preset's value (or, for the user's own settings a preset leaves out, such as
tiling, what they had before Plasmify: see plasmify.steps.effects). Overrides
are merged into the preset and validated by the same strict schema as any preset
file. Files the user picks or drops (own wallpapers, logo photo, splash and login
images) are copied into a per-preset folder under ``~/.local/share/plasmify/custom``.
"""

from __future__ import annotations

import dataclasses
import filecmp
import random
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QBoxLayout,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QCompleter,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFontComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from plasmify import asciiart, blockfont, bundle, downloads, generate, glass, presets, record, schema, surprise
from plasmify.gui.panel_editor import PanelEditor
from plasmify.gui.safe import message, plain_label, plain_tooltip
from plasmify.gui.shimmer_preview import ShimmerPreview
from plasmify.gui.widgets import (
    IMAGE_FILTER,
    Card,
    ChoiceTiles,
    DropZone,
    SliderSpin,
    SwatchRow,
    WallpaperList,
    cursor_strip,
    guard_wheel,
    icon_theme_strip,
    muted,
    subheading,
    tip,
    wallpaper_thumb,
)
from plasmify.palette import REQUIRED_KEYS, Palette, is_dark, is_hex
from plasmify.panels import compact, layout_panels, min_thickness, starter
from plasmify.presets import Preset
from plasmify.steps.cursor import installed_cursor_themes
from plasmify.steps.effects import TILING_SWITCH, own_tiling
from plasmify.steps.icons import installed_icon_themes
from plasmify.steps.login import login_manager
from plasmify.steps.shell import (
    DEFAULT_MODULES,
    MODULE_STYLE,
    builtin_logos,
    cell_width,
    coloured_lines,
    fastfetch_logo_rows,
    logo_kind,
    logo_stops,
)
from plasmify.steps.wallpaper import SHIMMER_MAX, shimmer_options
from plasmify.steps.wallpaper import cached_file as cached_wallpaper_file
from plasmify.system import PLASMIFY_DATA, Runner, safe_child

ANIMATION_SPEEDS = {"As before Plasmify": None, "Fast": 0.5, "Normal": 1.0, "Relaxed": 1.5}
ORDERS = {"As listed": "listed", "Shuffle": "shuffle"}
SHIMMERS = {
    "Off": "none",
    "Sparkles": "sparkle",
    "Embers": "embers",
    "Sakura petals": "petals",
    "Snow": "snow",
    "Fireflies": "fireflies",
    "Bokeh": "bokeh",
    "Shooting stars": "starfall",
    "Rain": "rain",
    "Magic circle": "magic",
    "Speed lines": "speedlines",
    "No particles (light only)": "calm",
}
SHIMMER_COLOURS = {"Accent": "accent", "Yellow": "yellow", "Orange": "orange", "Red": "red", "Pink": "magenta",
                   "Green": "green", "Blue": "blue", "Cyan": "cyan", "Text": "text"}  # fmt: skip
SHIMMER_LIGHTS = {
    "Suits the style": "auto",
    "Glint": "glint",
    "Light rays": "rays",
    "Lens flare": "flare",
    "Soft pulse": "pulse",
    "No light": "none",
}
SHIMMER_MOTIONS = {"Suits the style": "auto", "Breathe (slow zoom)": "breathe", "Drift (slow pan)": "drift",
                   "Still": "still"}  # fmt: skip
SHIMMER_DENSITIES = {"Low": "low", "Normal": "normal", "High": "high"}
SHIMMER_FPS = {"12 per second (lighter)": 12, "24 per second (smoother)": 24}
SHIMMER_HINT = (
    "Brings the wallpapers to life: sparkles, embers, sakura petals, snow, fireflies, bokeh lights, shooting "
    "stars, rain, a turning magic circle or speed lines over your pictures, with a light that plays across "
    "them. Made on this computer when you apply (a few seconds per picture); Plasma pauses it while a window "
    "fills the screen."
)
SHIMMER_COLOUR_HINT = "The colour of the particles and the light."
SHIMMER_LIGHT_HINT = (
    "A light that plays over the picture: a glint that sweeps across, rays from a corner, a lens flare or a "
    "soft pulse. Suits the style picks the one that fits."
)
SHIMMER_MOTION_HINT = (
    "How the picture itself moves: a slow zoom in and out, a slow pan, or not at all. Suits the style picks "
    "the one that fits."
)
SHIMMER_DENSITY_HINT = "How many particles there are."
SHIMMER_FPS_HINT = (
    "How many frames per second the living wallpapers play. 24 is smoother; 12 makes files half the size, "
    "renders twice as fast and uses less processor time."
)
GLASS_HINT = (
    "See-through app windows such as Dolphin, System Settings and Plasmify. Panels and the terminal turn "
    "see-through to match, and Frosted and Glass turn blur on."
)
GLASS_TILES = [
    ("off", "Windows stay solid, as they are without glass."),
    ("frosted", "Blurred and a little see-through, so the wallpaper shows only as soft colour behind windows."),
    ("glass", "Clearly see-through with a light blur, so the wallpaper stays recognisable behind windows."),
    ("clear", "See-through without blur, so whatever is behind a window stays sharp."),
]
TRANSPARENCY_HINT = (
    "How see-through window backgrounds are. Each look starts at its own amount, a little lower with light "
    "colours so dark text stays readable. Menus are about half as see-through."
)
FOCUSED_SOLID_HINT = (
    "The window you are working in stays solid and only the windows behind it are see-through, so their text "
    "never shows through yours. On unless you untick it, in every look."
)
GLASS_NOTE = (
    "Glass uses the Kvantum app style (installed when you apply) and Plasma's blur effect. Video, photo and "
    "drawing apps stay solid. Some apps paint their own solid content area, so the window frame, sidebars "
    "and toolbars show the effect most (Dolphin shows it everywhere)."
)
# How much more see-through a tile's little window is drawn than the look's own default, so the
# difference between the looks reads at tile size; and how much its backdrop is blurred.
TILE_SEE_THROUGH = 1.4
TILE_BLUR = {"frosted": 7, "glass": 3}
INTERVALS = {"5 minutes": 300, "15 minutes": 900, "30 minutes": 1800, "1 hour": 3600, "3 hours": 10800}
LOGO_CHOICES = {
    "Distribution logo": ("distro", "The logo of your Linux distribution, as fastfetch draws it."),
    "Built-in logo": ("builtin", "One of about 600 logos that come with fastfetch (arch, debian, tux…)."),
    "The preset's picture": ("preset-image", "The picture this preset ships for the terminal."),
    "My own photo": (
        "photo",
        "Any picture of yours: a real image in Konsole, kitty, Ghostty, WezTerm and foot, coloured blocks elsewhere.",
    ),
    "My own text": ("text", "A word in big block letters, coloured from the palette."),
    "My own drawing": ("ascii", "Draw with characters, like 0-0 faces or the Arch logo. Pick a sample to start."),
}
LOGO_COLOUR_CHOICES = {
    "Original colours": "original",
    "Palette gradient": "gradient",
    "Rainbow": "rainbow",
    "Sunset": "sunset",
    "Ocean": "ocean",
    "Forest": "forest",
    "Fire": "fire",
    "Candy": "candy",
    "Pastel": "pastel",
    "Neon": "neon",
    "Aurora": "aurora",
    "Mono": "mono",
    "Accent": "accent",
    "Text colour": "text",
    "Red": "red",
    "Green": "green",
    "Yellow": "yellow",
    "Blue": "blue",
    "Magenta": "magenta",
    "Cyan": "cyan",
    "Orange": "orange",
}
LOGO_DIRECTION_CHOICES = {
    "Top to bottom": "down",
    "Bottom to top": "up",
    "Left to right": "right",
    "Right to left": "left",
    "Diagonal": "diagonal",
}
TEXT_LOGOS = ("distro", "builtin", "text", "ascii")  # logos made of characters, so they can be coloured
CUSTOM_ROOT = PLASMIFY_DATA / "custom"


def custom_root() -> Path:
    """CUSTOM_ROOT, read at call time."""
    return CUSTOM_ROOT


def custom_folder(preset_id: str) -> Path:
    """Where the user's own files for a preset live (CUSTOM_ROOT is read at call time)."""
    return safe_child(CUSTOM_ROOT, preset_id)


IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
GIF_MAGIC = (b"GIF87a", b"GIF89a")
# A picture must still fit into a shared .plasmify file.
MAX_PICTURE_BYTES = bundle.MAX_ASSET_BYTES
COLOUR_LABELS = {
    "accent": "Accent",
    "base": "Background",
    "mantle": "Darker background",
    "crust": "Darkest background",
    "surface": "Buttons and inputs",
    "overlay": "Muted text",
    "text": "Text",
    "subtext": "Secondary text",
    "red": "Red",
    "green": "Green",
    "yellow": "Yellow",
    "blue": "Blue",
    "magenta": "Magenta",
    "cyan": "Cyan",
    "orange": "Orange",
}
SPLASH_TILES = [
    ("Keep it", "keep", "Leave the splash screen as it is."),
    ("Plasmify", "plasmify", "A splash in your palette with your choice of background, logo and text."),
    ("Breeze", "breeze", "Plasma's own splash screen."),
    ("Off", "none", "No splash screen: the desktop appears as soon as it is ready."),
]
PANEL_CORNER_NOTE = (
    "Rounded corners show while a panel floats. Plasma attaches a panel to the screen edge "
    "whenever a window touches it (always with tiling), and then its corners are square."
)
# Shown while the theme has no [panel_style]: the panel controls then only show where a change
# starts (keep_own_style in steps/panelstyle.py says what the panels get).
PANEL_LOOK_NOTE = (
    "This theme has no panel style of its own, so the panels keep your own Plasma style, as before "
    "Plasmify. Change a setting above to give them Plasmify's corners, opacity, colour and border."
)


# A getter returns this to drop the preset's whole section ("Keep it" for splash and login).
REMOVE = object()
# A style key the table leaves out, and a glass value the user changed since the tab was filled.
MISSING = object()
CHANGED = object()
# Combos whose "As before Plasmify" entry must also override a preset that sets the value.
KEEP_CURRENT = {("effects", "animation_speed"), ("effects", "titlebar_buttons"), ("effects", "window_borders")}
# Files the user dropped: role -> (section, key) of the preset value naming the file.
FILE_ROLES = {
    "logo": ("shell", "logo_file"),
    "splash_background": ("splash", "background_file"),
    "splash_logo": ("splash", "logo_file"),
    "login_background": ("login", "background_file"),
}
PANEL_COLOURS = {
    "mantle": "Theme (darker background)",
    "base": "Theme background",
    "crust": "Darkest",
    "surface": "Button colour",
    "accent": "Accent",
}
MODULE_LABELS = {
    "os": "Operating system", "host": "Computer model", "kernel": "Kernel", "uptime": "Uptime",
    "packages": "Installed packages", "shell": "Shell", "display": "Screens", "de": "Desktop",
    "wm": "Window manager", "theme": "Theme", "icons": "Icons", "font": "Font", "cursor": "Cursor",
    "terminal": "Terminal", "terminalfont": "Terminal font", "cpu": "Processor", "gpu": "Graphics",
    "memory": "Memory", "swap": "Swap", "disk": "Disk", "localip": "Local IP address", "battery": "Battery",
    "locale": "Language", "media": "Now playing", "weather": "Weather", "datetime": "Date and time",
    "colors": "Colour dots",
}  # fmt: skip


def usable_overrides(preset: Preset, overrides: dict, folder: Path | None) -> dict:
    """Saved tweaks that still form a valid preset. A section that no longer validates (say,
    after a preset update) is dropped on its own; the rest of the user's work is kept."""
    try:
        presets.customized(preset, overrides, folder)
        return overrides
    except presets.PresetError:
        pass
    kept: dict = {}
    for section, value in overrides.items():
        try:
            presets.customized(preset, {**kept, section: value}, folder)
        except presets.PresetError:
            continue
        kept[section] = value
    return kept


def dirty_key(section: str, key: str) -> tuple[str, str]:
    """Which control group a saved override belongs to (see ``CustomizePanel._getters``)."""
    if section == "layout":
        return ("layout", "*panels")
    if section in ("palette", "fonts", "splash", "login"):
        return (section, "*")
    if section == "icons":
        return ("icons", "*theme" if key == "theme" else "*folders")
    if section == "cursor" and key in ("name", "url"):
        return ("cursor", "*theme")
    if section == "wallpaper" and key in ("images", "mode", ""):
        return ("wallpaper", "*own")
    if section == "shell" and key.startswith("logo"):
        return ("shell", "*logo")
    if section == "style" and key in glass.CHOICE_KEYS:
        return ("style", "*glass")
    return (section, key)


def colour_icon(colour: str) -> QIcon:
    pixmap = QPixmap(16, 16)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(colour))
    painter.setPen(QPen(QColor(127, 127, 127, 160), 1))
    painter.drawEllipse(QRectF(1, 1, 14, 14))
    painter.end()
    return QIcon(pixmap)


def swatch_style(colour: str) -> str:
    return f"background: {colour}; border: 1px solid rgba(127,127,127,0.5); border-radius: 6px; min-height: 24px;"


def _image_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    return suffix if suffix in IMAGE_SUFFIXES else ".png"


def looks_like_image(path: Path) -> bool:
    """PNG, JPEG, WebP or GIF, judged by the first bytes (never by the name). Only regular files
    are opened (a named pipe would block) and only their first bytes are read."""
    try:
        if not path.is_file():
            return False
        with path.open("rb") as handle:
            head = handle.read(12)
    except OSError:
        return False
    return downloads.is_image_bytes(head) or head.startswith(GIF_MAGIC)


def own_wallpaper_name(number: int, path: Path) -> str:
    """The file name a picture of the user's gets in the custom folder; a GIF stays a GIF."""
    name = "wall-" + generate.wallpaper_file_name(number, path)
    if path.suffix.lower() == ".gif":
        name = name.rsplit(".", 1)[0] + ".gif"
    return name


def dim_value(value: int) -> int:
    """KWin dims inactive windows by 10 to 90 %; a smaller number on the slider means off."""
    if value < 5:
        return 0
    return min(max(value, 10), 90)


def interval_text(seconds: int) -> str:
    """A slideshow interval the menu does not list (set with the command line, a preset's own),
    such as 2 hours."""
    if seconds % 3600 == 0:
        return f"{seconds // 3600} hour{'s' if seconds != 3600 else ''}"
    if seconds % 60 == 0:
        return f"{seconds // 60} minute{'s' if seconds != 60 else ''}"
    return f"{seconds} seconds"


def speed_text(factor: float) -> str:
    """An animation speed the menu does not list (a preset's own, a captured desktop's), such as 1.25."""
    return "Instant (no animations)" if factor == 0 else f"Custom ({factor:g} times as long)"


class ColoursDialog(QDialog):
    """Every palette colour as a button; changes are returned as a dict."""

    def __init__(self, palette: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Colours")
        self.colours = dict(palette)
        grid = QGridLayout()
        for index, key in enumerate(REQUIRED_KEYS):
            button = QPushButton()
            button.setFixedWidth(110)
            button.setStyleSheet(swatch_style(self.colours[key]))
            button.setToolTip(f"{COLOUR_LABELS.get(key, key)}: {self.colours[key]}. Click to change.")
            button.clicked.connect(lambda _=False, k=key, b=button: self._pick(k, b))
            grid.addWidget(plain_label(COLOUR_LABELS.get(key, key)), index // 2, (index % 2) * 2)
            grid.addWidget(button, index // 2, (index % 2) * 2 + 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label("Click a colour to change it. Text contrast is up to you here.", wrap=True))
        layout.addLayout(grid)
        layout.addWidget(buttons)

    def _pick(self, key: str, button: QPushButton) -> None:
        colour = QColorDialog.getColor(QColor(self.colours[key]), self, COLOUR_LABELS.get(key, key))
        if colour.isValid():
            self.colours[key] = colour.name()
            button.setStyleSheet(swatch_style(self.colours[key]))
            button.setToolTip(f"{COLOUR_LABELS.get(key, key)}: {self.colours[key]}. Click to change.")


# ----- pictures for the tiles and the logo preview ----------------------------------------
def draw_splash_tile(painter: QPainter, rect: QRectF, style: str, colours: dict[str, str]) -> None:
    box = rect.adjusted(2, 2, -2, -2)
    shape = QPainterPath()
    shape.addRoundedRect(box, 6, 6)
    if style == "keep":
        painter.fillPath(shape, QColor(colours.get("surface", "#404050")))
        painter.setPen(QPen(QColor(colours.get("text", "#dddddd")), 2))
        centre = box.center()
        painter.drawLine(QPointF(centre.x() - 9, centre.y()), QPointF(centre.x() - 2, centre.y() + 7))
        painter.drawLine(QPointF(centre.x() - 2, centre.y() + 7), QPointF(centre.x() + 10, centre.y() - 7))
        return
    if style == "none":
        painter.fillPath(shape, QColor(colours.get("mantle", "#202030")))
        painter.setPen(QPen(QColor(colours.get("overlay", "#777")), 2))
        centre = box.center()
        painter.drawEllipse(centre, 12, 12)
        painter.drawLine(QPointF(centre.x() - 8, centre.y() + 8), QPointF(centre.x() + 8, centre.y() - 8))
        return
    if style == "breeze":
        painter.fillPath(shape, QColor("#1d1f22"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#3daee9"))
        painter.drawEllipse(box.center(), 10, 10)
        return
    painter.fillPath(shape, QColor(colours.get("crust", "#101018")))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colours.get("accent", "#88c0d0")))
    painter.drawEllipse(QPointF(box.center().x(), box.center().y() - 6), 9, 9)
    track = QRectF(box.center().x() - 26, box.center().y() + 14, 52, 4)
    painter.setBrush(QColor(colours.get("surface", "#404050")))
    painter.drawRoundedRect(track, 2, 2)
    painter.setBrush(QColor(colours.get("accent", "#88c0d0")))
    painter.drawRoundedRect(QRectF(track.left(), track.top(), track.width() * 0.6, 4), 2, 2)


def _blend(a: QColor, b: QColor, share: float) -> QColor:
    """``a`` moved ``share`` of the way to ``b``."""
    return QColor.fromRgbF(*(x + (y - x) * share for x, y in zip(a.getRgbF()[:3], b.getRgbF()[:3], strict=True)))


def glass_backdrop(width: float, height: float, ratio: float, colours: dict[str, str]) -> QImage:
    """A small wallpaper in the palette's colours: a gradient sky, soft glows, a sun and hills.
    Sharp edges and bright spots make it easy to see how much a window over it blurs."""
    image = QImage(max(int(width * ratio), 1), max(int(height * ratio), 1), QImage.Format.Format_ARGB32_Premultiplied)
    image.setDevicePixelRatio(ratio)
    image.fill(Qt.GlobalColor.transparent)
    crust = QColor(colours.get("crust", "#11111b"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    sky = QLinearGradient(0, 0, width, height)
    sky.setColorAt(0, _blend(crust, QColor(colours.get("blue", "#5e81ac")), 0.55))
    sky.setColorAt(1, _blend(crust, QColor(colours.get("magenta", "#b48ead")), 0.6))
    painter.fillRect(QRectF(0, 0, width, height), sky)
    painter.setPen(Qt.PenStyle.NoPen)
    for key, x, y, radius in (("accent", 0.3, 0.38, 0.42), ("orange", 0.82, 0.62, 0.36), ("cyan", 0.6, 0.12, 0.3)):
        colour = QColor(colours.get(key, colours.get("accent", "#88c0d0")))
        glow = QRadialGradient(QPointF(width * x, height * y), height * radius)
        glow.setColorAt(0, colour)
        glow.setColorAt(0.55, QColor(colour.red(), colour.green(), colour.blue(), 150))
        glow.setColorAt(1, QColor(colour.red(), colour.green(), colour.blue(), 0))
        painter.setBrush(glow)
        painter.drawEllipse(QPointF(width * x, height * y), height * radius, height * radius)
    painter.setBrush(QColor(colours.get("yellow", "#ebcb8b")))
    painter.drawEllipse(QPointF(width * 0.84, height * 0.5), height * 0.12, height * 0.12)  # half behind the window
    for share, points in (
        (0.35, ((0, 0.78), (0.22, 0.52), (0.4, 0.7), (0.62, 0.46), (0.86, 0.68), (1, 0.58))),
        (0.12, ((0, 0.9), (0.3, 0.72), (0.55, 0.86), (0.8, 0.7), (1, 0.82))),
    ):
        hills = QPolygonF([QPointF(0, height)] + [QPointF(width * x, height * y) for x, y in points])
        hills.append(QPointF(width, height))
        painter.setBrush(_blend(crust, QColor(colours.get("green", "#a3be8c")), share))
        painter.drawPolygon(hills)
    painter.end()
    return image


def soften(image: QImage, factor: int) -> QImage:
    """``image`` blurred the cheap way: scaled down by ``factor`` and back up again."""
    width, height = image.width(), image.height()
    small = image.scaled(
        max(width // factor, 1),
        max(height // factor, 1),
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    result = small.scaled(
        width, height, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
    )
    result.setDevicePixelRatio(image.devicePixelRatio())
    return result


def draw_glass_tile(painter: QPainter, rect: QRectF, look: str, colours: dict[str, str]) -> None:
    """A wallpaper with a small window on top whose background shows the glass look: solid,
    frosted (mostly opaque over a strong blur), glass (see-through over a light blur) or clear
    (see-through, nothing blurred)."""
    box = rect.adjusted(2, 2, -2, -2)
    shape = QPainterPath()
    shape.addRoundedRect(box, 6, 6)
    device = painter.device()
    ratio = device.devicePixelRatioF() if device is not None else 1.0
    backdrop = glass_backdrop(box.width(), box.height(), ratio, colours)
    base = QColor(colours.get("base", "#1e1e2e"))
    dark = is_dark(base.name())
    look = glass.canonical(look)
    see_through = min(glass.default_transparency(look, dark) / 100 * TILE_SEE_THROUGH, 0.8)
    painter.save()
    painter.setClipPath(shape)
    painter.drawImage(box.topLeft(), backdrop)

    window = QRectF(box.left() + box.width() * 0.16, box.top() + box.height() * 0.2, box.width() * 0.68,
                    box.height() * 0.64)  # fmt: skip
    frame = QPainterPath()
    frame.addRoundedRect(window, 4, 4)
    shadow = QPainterPath()
    shadow.addRoundedRect(window.translated(0, 1.5).adjusted(-1, -1, 1, 1), 5, 5)
    painter.fillPath(shadow.subtracted(frame), QColor(0, 0, 0, 70))  # only outside the window
    painter.setClipPath(frame, Qt.ClipOperation.IntersectClip)
    if look in TILE_BLUR:  # the same blur width in logical pixels on every screen scale
        painter.drawImage(box.topLeft(), soften(backdrop, max(1, round(TILE_BLUR[look] * ratio))))
    opacity = 1 - see_through
    fill = QColor(base)
    fill.setAlphaF(opacity)
    painter.fillRect(window, fill)
    bar = QColor(colours.get("mantle", "#181825"))
    bar.setAlphaF(min(opacity + 0.12, 1))
    title = QRectF(window.left(), window.top(), window.width(), window.height() * 0.2)
    painter.fillRect(title, bar)
    if look == "glass":  # the sheen of classic glass, brightest at the top left
        sheen = QLinearGradient(window.topLeft(), window.bottomRight())
        sheen.setColorAt(0, QColor(255, 255, 255, 70))
        sheen.setColorAt(0.5, QColor(255, 255, 255, 0))
        painter.fillRect(window, sheen)
    painter.setPen(Qt.PenStyle.NoPen)
    dot = title.height() * 0.22
    for index, key in enumerate(("overlay", "overlay", "red")):
        painter.setBrush(QColor(colours.get(key, "#888888")))
        painter.drawEllipse(QPointF(title.right() - dot * (8 - 3 * index), title.center().y()), dot, dot)
    text = QColor(colours.get("text", "#cdd6f4"))
    line = max(window.height() * 0.09, 2.0)
    for row, share in ((0, 0.62), (1, 0.42)):
        top = title.bottom() + window.height() * (0.18 + 0.2 * row)
        painter.setBrush(text)
        painter.drawRoundedRect(QRectF(window.left() + window.width() * 0.1, top, window.width() * share, line),
                                line / 2, line / 2)  # fmt: skip
    painter.restore()
    # Edges drawn without a clip, so the corners stay smooth. Glass catches the light at its edge.
    edge = QColor(colours.get("overlay", "#888888")) if look == "off" else QColor(255, 255, 255, 90 if dark else 170)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(edge, 1))
    painter.drawPath(frame)
    painter.setPen(QPen(QColor(127, 127, 127, 110), 1))
    painter.drawPath(shape)


def logo_picture(lines: list[str], colours: dict[str, str], background: str, size: QSize) -> QPixmap:
    """Draw fastfetch logo lines ($1..$9 colour codes, $$ for $) like a terminal would,
    scaled down to fit ``size``. ``colours``: slot number -> SGR colour, as fastfetch gets them."""
    pixmap = QPixmap(size)
    pixmap.fill(QColor(background))
    rows = [logo_cells(line, colours) for line in lines]
    if not rows:
        return pixmap
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    width = max(sum(cells for _, _, cells in row) for row in rows) or 1
    pixels = min(18.0, (size.width() - 16) / (width * 0.62), (size.height() - 12) / (len(rows) * 1.2))
    font.setPixelSize(max(int(pixels), 4))
    painter = QPainter(pixmap)
    painter.setFont(font)
    metrics = painter.fontMetrics()
    line = metrics.lineSpacing()
    advance = metrics.horizontalAdvance("M")
    top = (size.height() - line * len(rows)) / 2 + metrics.ascent()
    left = max((size.width() - advance * width) / 2, 6)
    for index, row in enumerate(rows):
        column = 0
        for text, colour, cells in row:
            if text.strip():
                painter.setPen(QColor(colour))
                painter.drawText(int(left + column * advance), int(top + index * line), text)
            column += cells
    painter.end()
    return pixmap


def logo_cells(line: str, colours: dict[str, str]) -> list[tuple[str, str, int]]:
    """(text, colour, terminal columns) of each cell of one coded logo line; combining marks
    and joiners stay in the cell of the character before them."""
    hexes = {slot: "#{:02x}{:02x}{:02x}".format(*map(int, sgr.split(";")[2:5])) for slot, sgr in colours.items()}
    colour, cells, index = "#ffffff", [], 0
    while index < len(line):
        if line[index] == "$" and index + 1 < len(line):
            following = line[index + 1]
            if following.isdigit() and following != "0":
                colour = hexes.get(following, colour)
                index += 2
                continue
            if following == "$":
                index += 1
        char = line[index]
        width = cell_width(char)
        if width == 0 and cells:
            text, previous, columns = cells[-1]
            cells[-1] = (text + char, previous, columns)
        else:
            cells.append((char, colour, width))
        index += 1
    return cells


def scheme_icon(stops: list[str]) -> QIcon:
    """A small strip of the colours a logo choice blends."""
    pixmap = QPixmap(28, 14)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    gradient = QLinearGradient(0, 0, 28, 0)
    for index, colour in enumerate(stops):
        gradient.setColorAt(index / max(len(stops) - 1, 1), QColor(colour))
    path = QPainterPath()
    path.addRoundedRect(QRectF(0.5, 0.5, 27, 13), 6, 6)
    painter.fillPath(path, gradient)
    painter.setPen(QPen(QColor(127, 127, 127, 160), 1))
    painter.drawPath(path)
    painter.end()
    return QIcon(pixmap)


def same_file(a: Path, b: Path) -> bool:
    try:
        return a.is_file() and not a.is_symlink() and filecmp.cmp(a, b, shallow=False)
    except OSError:
        return False


def wallpaper_files(preset: Preset) -> list[Path]:
    """The preset's wallpapers that are on this computer already (bundled or downloaded)."""
    found = (cached_wallpaper_file(preset, image) for image in preset.section("wallpaper").get("images", [])[:6])
    return [path for path in found if path is not None]


# ----- the tab ---------------------------------------------------------------------------
class CustomizePanel(QWidget):
    changed = pyqtSignal()
    splash_preview_requested = pyqtSignal()
    # How wide the scroll area around the panel must stay for one column of the open cards.
    width_needed = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.preset: Preset | None = None
        self._dirty: dict[tuple[str, str], Callable[[], Any]] = {}
        self._loading = False
        self._colours: dict[str, str] = {}
        self._wall_entries: list[dict] | None = None  # None: the preset's own wallpapers
        self._files: dict[str, str] = {}  # role -> file name in the custom folder
        self._custom_dir: Path | None = None
        self._logo_names: list[str] | None = None
        self._ui_font = ""
        self._mono_font = ""
        self._tiling_default = False
        self._system_cursor = ""
        self._panel_colour_current = "mantle"
        self._radius_wanted = 6
        self._clamping = False
        # The glass look, transparency and focused_solid as the tab was filled with them (MISSING
        # when left out), each CHANGED once the user picks another; and the light/dark mode the
        # slider shows for.
        self._glass_kept: tuple[Any, Any, Any] = (MISSING, MISSING, MISSING)
        self._glass_loaded: tuple[Any, Any, Any] = (MISSING, MISSING, MISSING)  # as filled, for picking it again
        self._glass_dark = True
        # (section, key) -> function returning the override value; used to restore saved tweaks.
        self._getters: dict[tuple[str, str], Callable[[], Any]] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 6, 0, 0)
        outer.addWidget(
            muted(
                "Click a section to open it. Change anything and watch the preview; untouched options keep the "
                "preset's values. Hover over any option to see what it does. Drop your own images on the dashed boxes."
            )
        )
        outer.addWidget(self._build_toolbar())
        self.advice_box = QFrame()
        self.advice_box.setObjectName("advice")
        self.advice_box.setStyleSheet(
            "QFrame#advice { background: palette(alternate-base); border: 1px solid palette(highlight);"
            " border-radius: 10px; }"
        )
        self.advice_layout = QVBoxLayout(self.advice_box)
        self.advice_layout.setContentsMargins(10, 6, 10, 6)
        self.advice_box.hide()
        outer.addWidget(self.advice_box)
        columns = QHBoxLayout()
        columns.setSpacing(12)
        left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(12)
        right.setSpacing(12)
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        outer.addLayout(columns)

        left.addWidget(self._build_panels())
        left.addWidget(self._build_windows())
        left.addWidget(self._build_glass())
        left.addWidget(self._build_wallpapers())
        left.addWidget(self._build_splash())
        left.addStretch(1)
        right.addWidget(self._build_colours())
        right.addWidget(self._build_icons())
        right.addWidget(self._build_fonts())
        right.addWidget(self._build_terminal())
        right.addWidget(self._build_login())
        right.addStretch(1)
        self.cards = [self.panels_box, self.windows_box, self.glass_box, self.walls_box, self.splash_box,
                      self.colours_box, self.icons_box, self.fonts_box, self.term_box, self.login_box]  # fmt: skip
        # Two columns while both fit beside each other, one below the other otherwise (an open
        # Panels card next to an open Fonts card is wider than the default window).
        self.columns = columns
        self.column_layouts = (left, right)
        self._scroll_area: QScrollArea | None = None
        self._width_needed = 0
        self._opened: Card | None = None
        self._reveal_card: Card | None = None
        # Timers owned by the panel, so nothing fires after it is gone.
        self._arrange_timer = QTimer(self, singleShot=True, interval=0)
        self._arrange_timer.timeout.connect(self._arrange)
        self._reveal_timer = QTimer(self, singleShot=True, interval=0)
        self._reveal_timer.timeout.connect(self._reveal)
        for card in self.cards:
            card.toggled.connect(lambda open_, card=card: self._card_toggled(card, open_))
        self._arrange_timer.start()  # runs once the panel sits in the window
        self._getters.update(
            {
                ("layout", "*panels"): self._panels_value,
                ("palette", "*"): self._palette_value,
                ("icons", "*theme"): self._icon_theme_value,
                ("icons", "*folders"): self._folders_value,
                ("cursor", "*theme"): self._cursor_value,
                ("fonts", "*"): self._fonts_value,
                ("wallpaper", "*own"): self._wallpaper_value,
                ("shell", "*logo"): self._logo_value,
                ("shell", "fastfetch_modules"): self._modules_value,
                ("splash", "*"): self._splash_value,
                ("login", "*"): self._login_value,
                ("style", "*glass"): self._glass_value,
            }
        )

        self.changed.connect(self._show_count)
        self.changed.connect(self._sync_panel_look)
        # Scrolling never changes a setting by accident: the wheel stays with the page until a
        # control is clicked or tabbed into.
        guard_wheel(self)

    # ----- the toolbar that stays in view --------------------------------------------
    def _build_toolbar(self) -> QWidget:
        """Surprise me and Reset in a slim bar above the cards. Inside a scroll area the bar
        floats on the viewport and sticks to its top while the cards scroll under it."""
        self.surprise_button = tip(
            QPushButton(QIcon.fromTheme("media-playlist-shuffle"), "Surprise me"),
            "A random design on top of this preset: panels, corners, colours, motion, terminal logo and "
            "splash. Every surprise is checked so that it triggers no warning. Click again for another.",
        )
        self.surprise_button.clicked.connect(self.surprise)
        self.reset_button = tip(
            QPushButton(QIcon.fromTheme("edit-reset", QIcon.fromTheme("edit-undo")), "Reset to preset"),
            "Forget every change made on this tab and go back to the preset's own settings.",
        )
        self.reset_button.clicked.connect(lambda: self.load(self.preset))
        self.count_label = muted(wrap=False)
        self.toolbar = QFrame()
        self.toolbar.setObjectName("customizeToolbar")
        self.toolbar.setProperty("stuck", False)
        self.toolbar.setStyleSheet(
            "QFrame#customizeToolbar { background: palette(window); border: none; }"
            'QFrame#customizeToolbar[stuck="true"] { border-bottom: 1px solid rgba(127,127,127,0.35); }'
        )
        row = QHBoxLayout(self.toolbar)
        row.setContentsMargins(0, 6, 0, 6)
        row.addWidget(self.surprise_button)
        row.addWidget(self.reset_button)
        row.addStretch(1)
        row.addWidget(self.count_label)
        self._toolbar_slot = QWidget()
        slot = QVBoxLayout(self._toolbar_slot)
        slot.setContentsMargins(0, 0, 0, 0)
        slot.addWidget(self.toolbar)
        self._toolbar_floating = False
        self._toolbar_timer = QTimer(self, singleShot=True, interval=0)
        self._toolbar_timer.timeout.connect(self._place_toolbar)
        return self._toolbar_slot

    def _show_count(self) -> None:
        count = self.change_count
        self.count_label.setText(f"{count} change{'s' if count != 1 else ''} from the preset" if count else "")

    def _float_toolbar(self, area: QScrollArea) -> None:
        self._toolbar_slot.setFixedHeight(self.toolbar.sizeHint().height())
        self._toolbar_slot.layout().removeWidget(self.toolbar)
        self.toolbar.setParent(area.viewport())
        self._toolbar_floating = True
        self._toolbar_slot.installEventFilter(self)
        area.widget().installEventFilter(self)
        area.verticalScrollBar().valueChanged.connect(self._place_toolbar)
        self._place_toolbar()

    def sticky_height(self) -> int:
        """How much of the top of the scroll area the floating toolbar covers."""
        return self._toolbar_slot.height() if self._toolbar_floating else 0

    def _place_toolbar(self, *_args) -> None:
        area = self._scroll_area
        if not self._toolbar_floating or area is None:
            return
        if not self.isVisible():
            self.toolbar.hide()
            return
        viewport = area.viewport()
        slot = self._toolbar_slot.mapTo(viewport, QPoint(0, 0))
        height = self._toolbar_slot.height()
        bottom = self.mapTo(viewport, QPoint(0, self.height())).y()
        top = min(max(slot.y(), 0), bottom - height)
        stuck = top > slot.y()
        self.toolbar.setGeometry(slot.x(), top, self._toolbar_slot.width(), height)
        if self.toolbar.property("stuck") != stuck:
            self.toolbar.setProperty("stuck", stuck)
            self.toolbar.style().unpolish(self.toolbar)
            self.toolbar.style().polish(self.toolbar)
        self.toolbar.show()
        self.toolbar.raise_()

    def hideEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().hideEvent(event)
        if self._toolbar_floating:
            self.toolbar.hide()  # it lives on the viewport, so the tab does not hide it

    # ----- columns -------------------------------------------------------------------
    def _schedule_arrange(self, opened: Card | None = None) -> None:
        if opened is not None:
            self._opened = opened
        self._arrange_timer.start()

    def _card_toggled(self, card: Card, open_: bool) -> None:
        # Right away rather than on the timer: no frame is drawn with the columns too wide.
        if open_:
            self._opened = card
        self._arrange()
        if open_ and self.isVisible():
            # Opened out of sight (Surprise me opens Panels): bring it into view.
            self._reveal_card = card
            self._reveal_timer.start()

    def showEvent(self, event) -> None:  # noqa: N802 (Qt API)
        super().showEvent(event)
        self._schedule_arrange()
        self._area()
        self._place_toolbar()

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt API)
        area = self._scroll_area
        if area is not None and event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            if watched is area.viewport() and event.type() == QEvent.Type.Resize:
                self._schedule_arrange()
            if watched in (area.viewport(), area.widget(), self._toolbar_slot):
                self._toolbar_timer.start()  # the toolbar follows its place in the page
        return super().eventFilter(watched, event)

    def _area(self) -> QScrollArea | None:
        if self._scroll_area is None:
            area = self.parentWidget()
            while area is not None and not isinstance(area, QScrollArea):
                area = area.parentWidget()
            if area is not None and area.widget() is not None:
                self._scroll_area = area
                area.viewport().installEventFilter(self)
                self._float_toolbar(area)
        return self._scroll_area

    def _room(self, area: QScrollArea) -> int:
        """The width the cards may take without the window scrolling sideways."""
        # As a tab page this panel keeps a stale size while hidden (and a hidden page still
        # counts towards the window's width), so measure the page stack, which is always laid
        # out. The tab widget asks for its frame on top of it, though pages get the full width.
        holder = self
        stack = self.parentWidget()
        if stack is not None and isinstance(stack.parentWidget(), QTabWidget):
            holder = stack
        chrome = area.widget().width() - holder.width()
        if holder is stack:
            chrome += max(0, stack.parentWidget().minimumSizeHint().width() - stack.minimumSizeHint().width())
        return area.viewport().width() - chrome

    @staticmethod
    def _settle() -> None:
        """Finish pending layout work: a card that just opened or closed passes its new size up
        through a few layouts, one event at a time."""
        for _ in range(3):
            QApplication.sendPostedEvents(None, QEvent.Type.LayoutRequest.value)

    def _arrange(self) -> None:
        opened, self._opened = self._opened, None
        area = self._area()
        if area is None:
            return
        self._settle()
        # The layouts' own minimums: the cards' size hints alone come out a few pixels short.
        left, right = (layout.minimumSize().width() for layout in self.column_layouts)
        self._report_width(area, max(left, right, self.toolbar.minimumSizeHint().width()))
        wide = left + self.columns.spacing() + right <= self._room(area)
        direction = QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        if self.columns.direction() == direction:
            return
        self.columns.setDirection(direction)
        # A hidden tab page does not pass its new size on, yet the window still makes room for it.
        self.layout().invalidate()
        widget = self.parentWidget()
        while widget is not None and widget is not area.widget():
            widget.updateGeometry()
            widget = widget.parentWidget()
        self._settle()  # the narrower width reaches the window before anything is drawn
        if opened is not None and self.isVisible():
            # The card just opened has moved (below the other column): bring it back into view.
            self._reveal_card = opened
            self._reveal_timer.start()

    def _report_width(self, area: QScrollArea, column: int) -> None:
        """Tell the window how wide the scroll area must stay for one ``column`` of cards (with
        room for a vertical scroll bar), so the page never scrolls sideways."""
        if not area.isVisible():
            return  # widths are not laid out yet
        bar = area.verticalScrollBar()
        needed = column + area.width() - self._room(area) + (0 if bar.isVisible() else bar.sizeHint().width())
        if needed != self._width_needed:
            self._width_needed = needed
            self.width_needed.emit(needed)

    def _reveal(self) -> None:
        opened, self._reveal_card = self._reveal_card, None
        area = self._scroll_area
        if opened is not None and area is not None and self.isVisible():
            self._settle()
            top = opened.mapTo(area.widget(), QPoint(0, 0)).y()
            shown = area.verticalScrollBar().value()
            first = shown + self.sticky_height()  # the toolbar covers the top of the page
            last = shown + area.viewport().height() - opened.header.height()
            if not first <= top <= last:
                area.verticalScrollBar().setValue(max(0, top - 8 - self.sticky_height()))

    # ----- sections ------------------------------------------------------------------
    def _build_panels(self) -> Card:
        card = Card("Panels", "preferences-desktop", "Bars and docks on any screen edge, with any widgets.")
        self.panels_box = card
        self.panel_editor = PanelEditor()
        self.panel_editor.changed.connect(
            lambda: (self._mark(("layout", "*panels"), self._panels_value), self._sync_enabled())
        )
        card.body.addWidget(self.panel_editor)
        form = QFormLayout()
        self.radius = self._slider_spin("panel_style", "radius", 0, 30, " px", "How round the panel corners are.")
        # What the user asked for; the control may show less while the panels are thin.
        self.radius.valueChanged.connect(lambda v: None if self._clamping else setattr(self, "_radius_wanted", v))
        self.panel_opacity = self._slider_spin(
            "panel_style",
            "opacity",
            20,
            100,
            " %",
            "100 % is solid; lower values let the wallpaper show through.",
            scale=100,
        )
        self.border = self._combo(
            "panel_style",
            "border",
            {"No border": "none", "Subtle border": "subtle", "Accent border": "accent"},
            "A thin outline around the panels.",
        )
        self.panel_colour = QComboBox()
        tip(self.panel_colour, "The panel background. Palette colours follow the theme; a custom one stays fixed.")
        self.panel_colour.activated.connect(self._panel_colour_picked)
        self._getters[("panel_style", "colour")] = self.panel_colour.currentData
        form.addRow(self._label("Corners:", "How round the panel corners are."), self.radius)
        form.addRow(self._label("Opacity:", "How see-through the panels are."), self.panel_opacity)
        form.addRow(self._label("Colour:", "The panel background colour."), self.panel_colour)
        form.addRow(self._label("Border:", "A thin outline around the panels."), self.border)
        card.body.addLayout(form)
        self.panel_look_note = muted(PANEL_LOOK_NOTE)
        card.body.addWidget(self.panel_look_note)
        self.radius_note = muted(PANEL_CORNER_NOTE)
        card.body.addWidget(self.radius_note)
        return card

    def _build_windows(self) -> Card:
        card = Card("Windows", "preferences-desktop-effects", "How windows look, behave and animate.")
        self.windows_box = card
        form = QFormLayout()
        self.tiling = self._check(
            "effects",
            "tiling",
            "Automatic tiling (Polonium)",
            "Windows arrange themselves side by side. Installs Polonium (26 KB) if needed.",
        )
        self.open_close = self._combo(
            "effects",
            "open_close",
            {"Zoom": "scale", "Glide": "glide", "Fade": "fade", "None": "none"},
            "Animation when a window opens or closes.",
        )
        self.minimize = self._combo(
            "effects",
            "minimize",
            {"Magic lamp": "magiclamp", "Squash": "squash", "None": "none"},
            "Animation when a window is minimised.",
        )
        self.desktop_switch = self._combo(
            "effects",
            "desktop_switch",
            {"Slide": "slide", "Fade": "fade", "None": "none"},
            "Animation when you switch virtual desktops.",
        )
        self.wobbly = self._check(
            "effects", "wobbly", "Wobbly windows", "Windows wobble like jelly while you drag them."
        )
        self.wobbly_level = self._slider_spin(
            "effects", "wobbly_level", 0, 4, "", "How much windows wobble: 0 is barely, 4 is very wobbly."
        )
        self.fall_apart = self._check(
            "effects", "fall_apart", "Fall apart on close", "Closing windows break into pieces that fall away."
        )
        self.sheet = self._check(
            "effects",
            "sheet",
            "Dialogs slide in",
            "Dialogs slide down out of the title bar of their window, like a sheet of paper.",
        )
        self.cube = self._check(
            "effects",
            "cube",
            "Desktop cube",
            "Your virtual desktops on the sides of a cube that you spin with Meta+C. Needs a few virtual desktops.",
        )
        self.blur = self._check("effects", "blur", "Blur behind panels and menus", "Frosted glass look.")
        self.blur_strength = self._slider_spin("effects", "blur_strength", 1, 15, "", "How strong the blur is.")
        self.blur_noise = self._slider_spin(
            "effects", "blur_noise", 0, 14, "", "A little grain in the blur, so it looks like frosted glass. 0 is none."
        )
        self.blur_saturation = self._slider_spin(
            "effects",
            "blur_saturation",
            100,
            500,
            " %",
            "How colourful things look through the blur. 100 % keeps their colours as they are.",
        )
        self.moving_opacity = self._slider_spin(
            "effects",
            "moving_opacity",
            30,
            100,
            " %",
            "How solid a window stays while you move or resize it. 100 % is solid; less lets you see behind it.",
            scale=100,
        )
        self.dim_inactive = self._slider_spin(
            "effects",
            "dim_inactive",
            0,
            90,
            " %",
            "Windows you are not using get darker, so the one you work in stands out. Off leaves them as they are.",
            convert=dim_value,
        )
        self.dim_inactive.spin.setSpecialValueText("Off")
        self.dim_inactive.spin.setKeyboardTracking(False)  # typing 50 never stops at 5 on the way
        for control in (self.dim_inactive.slider, self.dim_inactive.spin):
            control.setSingleStep(10)
        self.dim_inactive.slider.setPageStep(10)
        self.dim_inactive.valueChanged.connect(lambda _: self._snap_dim())
        self.dim_inactive.slider.sliderReleased.connect(self._snap_dim)
        self.speed = self._combo(
            "effects",
            "animation_speed",
            ANIMATION_SPEEDS,
            "How fast every animation plays. As before Plasmify: the speed you had before your first theme.",
        )
        self.titlebar = self._combo(
            "effects",
            "titlebar_buttons",
            {
                "As before Plasmify": None,
                "Right side (Windows, KDE)": "right",
                "Left side (macOS)": "left",
                "Close button only": "close",
                "No buttons (keyboard only)": "none",
            },
            "Where the close, minimise and maximise buttons sit on window title bars. "
            "As before Plasmify: where you had them before your first theme.",
        )
        self.borders = self._combo(
            "effects",
            "window_borders",
            {"As before Plasmify": None, "None": "none", "Tiny": "tiny", "Normal": "normal", "Large": "large"},
            "Window frame thickness. Larger borders are easier to grab for resizing. "
            "As before Plasmify: the borders you had before your first theme.",
        )
        self.blur.toggled.connect(lambda _: self._sync_enabled())
        self.wobbly.toggled.connect(lambda _: self._sync_enabled())
        form.addRow(self.tiling)
        form.addRow(subheading("Look"))
        form.addRow(self.blur)
        form.addRow(self._label("Blur strength:", "How strong the blur is."), self.blur_strength)
        form.addRow(self._label("Blur grain:", "A little grain in the blur, like frosted glass."), self.blur_noise)
        form.addRow(
            self._label("Blur saturation:", "How colourful things look through the blur."), self.blur_saturation
        )
        form.addRow(
            self._label("See-through while moving:", "How solid a window stays while you move it."),
            self.moving_opacity,
        )
        form.addRow(self._label("Dim inactive windows:", "Darken the windows you are not using."), self.dim_inactive)
        form.addRow(self._label("Title bar buttons:", "Where window buttons sit."), self.titlebar)
        form.addRow(self._label("Window borders:", "Window frame thickness."), self.borders)
        form.addRow(subheading("Motion"))
        form.addRow(self._label("Open and close:", "Animation when a window opens or closes."), self.open_close)
        form.addRow(self.fall_apart)
        form.addRow(self._label("Minimise:", "Animation when a window is minimised."), self.minimize)
        form.addRow(self._label("Desktop switch:", "Animation when you switch desktops."), self.desktop_switch)
        form.addRow(self.cube)
        form.addRow(self.sheet)
        form.addRow(self.wobbly)
        form.addRow(self._label("Wobbliness:", "How much windows wobble."), self.wobbly_level)
        form.addRow(self._label("Animation speed:", "How fast every animation plays."), self.speed)
        card.body.addLayout(form)
        return card

    def _build_glass(self) -> Card:
        card = Card("Glass", "preferences-desktop-theme-global", GLASS_HINT)
        self.glass_box = card
        tiles = [(glass.LABELS[look], look, hint) for look, hint in GLASS_TILES]
        # One row of four, or two of two (also in the narrowest columns, about 230 px).
        self.glass_tiles = ChoiceTiles(tiles, QSize(96, 60), balanced=True)
        self.glass_tiles.changed.connect(self._glass_picked)
        self.transparency = SliderSpin(*glass.TRANSPARENCY_RANGE, " %")
        self.transparency.setToolTip(TRANSPARENCY_HINT)
        self.transparency.valueChanged.connect(self._transparency_moved)
        self.focused_solid = QCheckBox("Keep the window you use solid")
        self.focused_solid.setToolTip(FOCUSED_SOLID_HINT)
        self.focused_solid.toggled.connect(self._focus_toggled)
        form = QFormLayout()
        form.addRow(self._label("See-through:", TRANSPARENCY_HINT), self.transparency)
        form.addRow(self.focused_solid)
        card.body.addWidget(self.glass_tiles)
        card.body.addLayout(form)
        card.body.addWidget(muted(GLASS_NOTE))
        return card

    def _snap_dim(self) -> None:
        """Show the dimming as it is saved: off, or 10 to 90 %."""
        value = self.dim_inactive.value()
        if self.dim_inactive.slider.isSliderDown() or value == dim_value(value):
            return
        self.dim_inactive.setValue(dim_value(value))  # marks the change through valueChanged

    def _build_wallpapers(self) -> Card:
        card = Card("Wallpapers", "preferences-desktop-wallpaper")
        self.walls_box = card
        self.walls_list = WallpaperList()
        self.walls_list.files_dropped.connect(self.add_wallpapers)
        self.walls_list.reordered.connect(self._walls_reordered)
        self.walls_list.remove_requested.connect(self.remove_wallpapers)
        add = tip(QPushButton(QIcon.fromTheme("list-add"), "Add pictures…"), "Add your own pictures to the slideshow.")
        add.clicked.connect(self._choose_wallpapers)
        remove = tip(QPushButton(QIcon.fromTheme("list-remove"), "Remove"), "Take the selected pictures out.")
        remove.clicked.connect(self.remove_wallpapers)
        back = tip(QPushButton(QIcon.fromTheme("edit-undo"), "Preset's list"), "Back to the preset's own wallpapers.")
        back.clicked.connect(self.reset_wallpapers)
        buttons = QHBoxLayout()
        for button in (add, remove, back):
            buttons.addWidget(button)
        buttons.addStretch(1)
        self.walls_note = muted()
        self.interval = self._combo("wallpaper", "interval", INTERVALS, "How often the slideshow moves on.")
        self.order = self._combo("wallpaper", "order", ORDERS, "Show the pictures in the order above, or mixed up.")
        self.shimmer = self._combo("wallpaper", "shimmer", SHIMMERS, SHIMMER_HINT)
        self.shimmer.currentIndexChanged.connect(lambda _: self._sync_enabled())
        self.shimmer_colour = self._combo("wallpaper", "shimmer_colour", SHIMMER_COLOURS, SHIMMER_COLOUR_HINT)
        self.shimmer_light = self._combo("wallpaper", "shimmer_light", SHIMMER_LIGHTS, SHIMMER_LIGHT_HINT)
        self.shimmer_motion = self._combo("wallpaper", "shimmer_motion", SHIMMER_MOTIONS, SHIMMER_MOTION_HINT)
        self.shimmer_density = self._combo("wallpaper", "shimmer_density", SHIMMER_DENSITIES, SHIMMER_DENSITY_HINT)
        self.shimmer_fps = self._combo("wallpaper", "shimmer_fps", SHIMMER_FPS, SHIMMER_FPS_HINT)
        for box in (self.shimmer_colour, self.shimmer_light, self.shimmer_motion, self.shimmer_density):
            box.currentIndexChanged.connect(lambda _: self._refresh_shimmer_preview())
        self.shimmer_preview = ShimmerPreview()
        self.shimmer_always = self._check(
            "wallpaper",
            "shimmer_always",
            "Keep moving while windows are open",
            "Plasma pauses moving wallpapers while a window fills the screen; this keeps them going "
            "(a little more processor time and battery).",
        )
        form = QFormLayout()
        form.addRow(self._label("Change every:", "How often the slideshow moves on."), self.interval)
        form.addRow(self._label("Order:", "Show the pictures in the order above, or mixed up."), self.order)
        form.addRow(self._label("Shimmer:", SHIMMER_HINT), self.shimmer)
        form.addRow(self._label("Colour:", SHIMMER_COLOUR_HINT), self.shimmer_colour)
        form.addRow(self._label("Light:", SHIMMER_LIGHT_HINT), self.shimmer_light)
        form.addRow(self._label("Motion:", SHIMMER_MOTION_HINT), self.shimmer_motion)
        form.addRow(self._label("Particles:", SHIMMER_DENSITY_HINT), self.shimmer_density)
        form.addRow(self._label("Frame rate:", SHIMMER_FPS_HINT), self.shimmer_fps)
        form.addRow("", self.shimmer_always)
        card.body.addWidget(self.walls_list)
        card.body.addLayout(buttons)
        card.body.addWidget(self.walls_note)
        card.body.addLayout(form)
        card.body.addWidget(self.shimmer_preview)
        return card

    def _build_splash(self) -> Card:
        card = Card("Splash screen", "preferences-system-splash", "What you see while Plasma starts after login.")
        self.splash_box = card
        self.splash_style = ChoiceTiles(SPLASH_TILES, QSize(88, 54))
        self.splash_background = QComboBox()
        for label, value in (("Palette colour", "palette"), ("The wallpaper", "wallpaper"), ("My image", "image")):
            self.splash_background.addItem(label, value)
        tip(self.splash_background, "What fills the screen behind the logo.")
        self.splash_background_drop = DropZone("Drop a splash background here")
        self.splash_logo = QComboBox()
        for label, value in (
            ("Distribution logo", "distro"),
            ("The preset's picture", "preset"),
            ("Plasma logo", "plasma"),
            ("My image", "image"),
            ("No logo", "none"),
        ):
            self.splash_logo.addItem(label, value)
        tip(self.splash_logo, "The picture in the middle of the splash screen.")
        self.splash_logo_drop = DropZone("Drop a logo here (a PNG with transparency looks best)")
        self.splash_text = QLineEdit()
        self.splash_text.setMaxLength(40)
        self.splash_text.setPlaceholderText("Welcome back")
        tip(self.splash_text, "A short line under the logo (up to 40 characters). Leave empty for none.")
        self.splash_progress = QCheckBox("Progress bar")
        tip(self.splash_progress, "A thin bar that fills while Plasma starts.")
        self.splash_preview = tip(
            QPushButton("Preview splash"), "Shows the splash in a window for a few seconds. Nothing is changed."
        )
        self.splash_preview.clicked.connect(self.splash_preview_requested.emit)
        for signal in (
            self.splash_style.changed,
            self.splash_background.currentIndexChanged,
            self.splash_logo.currentIndexChanged,
            self.splash_text.textChanged,
            self.splash_progress.toggled,
        ):
            signal.connect(lambda *_: (self._mark(("splash", "*"), self._splash_value), self._sync_enabled()))
        self.splash_background_drop.picked.connect(lambda paths: self._use_file("splash_background", paths[0]))
        self.splash_background_drop.cleared.connect(lambda: self._forget_file("splash_background"))
        self.splash_logo_drop.picked.connect(lambda paths: self._use_file("splash_logo", paths[0]))
        self.splash_logo_drop.cleared.connect(lambda: self._forget_file("splash_logo"))
        form = QFormLayout()
        form.addRow(self._label("Background:", "What fills the screen behind the logo."), self.splash_background)
        form.addRow(self.splash_background_drop)
        form.addRow(self._label("Logo:", "The picture in the middle."), self.splash_logo)
        form.addRow(self.splash_logo_drop)
        form.addRow(self._label("Text:", "A short line under the logo."), self.splash_text)
        form.addRow(self.splash_progress)
        card.body.addWidget(self.splash_style)
        card.body.addLayout(form)
        card.body.addWidget(self.splash_preview, 0, Qt.AlignmentFlag.AlignLeft)
        return card

    def _build_colours(self) -> Card:
        card = Card("Colours", "preferences-desktop-color", "The palette every part of the theme is coloured from.")
        self.colours_box = card
        self.swatches = SwatchRow()
        self.swatches.setToolTip("Click a colour to make it the accent (buttons, highlights, the dock).")
        self.swatches.chosen.connect(self.set_accent)
        self.accent_button = tip(QPushButton("Any colour…"), "Pick any accent colour.")
        self.accent_button.clicked.connect(self.pick_accent)
        all_colours = tip(QPushButton("All colours…"), "Change backgrounds, text and every other palette colour.")
        all_colours.clicked.connect(self.edit_colours)
        row = QHBoxLayout()
        row.addWidget(self.accent_button)
        row.addWidget(all_colours)
        row.addStretch(1)
        card.body.addWidget(self._label("Accent colour:", "Buttons, highlights, the dock and the prompt use it."))
        card.body.addWidget(self.swatches)
        card.body.addLayout(row)
        return card

    def _build_icons(self) -> Card:
        card = Card("Icons and cursor", "preferences-desktop-icons")
        self.icons_box = card
        self.icon_theme = tip(QComboBox(), "Any icon theme installed on this computer. Papirus is installed if picked.")
        self.icon_theme.currentIndexChanged.connect(
            lambda _: (self._mark(("icons", "*theme"), self._icon_theme_value), self._sync_enabled())
        )
        self.icon_sample = QLabel()
        self.icon_sample.setToolTip("Sample icons from the chosen theme.")
        self.folders = tip(QComboBox(), "Folder colour (Papirus themes only).")
        self.folders.currentIndexChanged.connect(lambda _: self._mark(("icons", "*folders"), self._folders_value))
        self.cursor_theme = tip(QComboBox(), "Any cursor theme installed on this computer.")
        self.cursor_theme.currentIndexChanged.connect(
            lambda _: (self._mark(("cursor", "*theme"), self._cursor_value), self._sync_enabled())
        )
        self.cursor_sample = QLabel()
        self.cursor_sample.setToolTip("The arrow, hand, text, busy and move cursors of the chosen theme.")
        self.cursor_size = self._slider_spin("cursor", "size", 16, 96, " px", "How big the mouse cursor is.")
        form = QFormLayout()
        form.addRow(self._label("Icons:", "Any installed icon theme."), self.icon_theme)
        form.addRow("", self.icon_sample)
        form.addRow(self._label("Folders:", "Folder colour (Papirus themes only)."), self.folders)
        form.addRow(self._label("Cursor:", "Any installed cursor theme."), self.cursor_theme)
        form.addRow("", self.cursor_sample)
        form.addRow(self._label("Cursor size:", "How big the mouse cursor is."), self.cursor_size)
        card.body.addLayout(form)
        return card

    def _build_fonts(self) -> Card:
        card = Card("Fonts", "preferences-desktop-font", "Pick any text font installed on this computer.")
        self.fonts_box = card
        self.ui_font = tip(QFontComboBox(), "The font for menus, buttons and window titles.")
        self.ui_font.setFontFilters(QFontComboBox.FontFilter.ScalableFonts)
        # Only fonts with Latin letters: emoji and symbol fonts (Noto Color Emoji, Dingbats)
        # leave apps without letters and Dolphin with empty boxes.
        self.ui_font.setWritingSystem(QFontDatabase.WritingSystem.Latin)
        self.ui_size = tip(QSpinBox(), "Interface text size.")
        self.ui_size.setRange(6, 24)
        self.ui_size.setSuffix(" pt")
        self.mono_font = tip(QFontComboBox(), "The font for terminals and code. Only fixed-width fonts are listed.")
        self.mono_font.setFontFilters(QFontComboBox.FontFilter.MonospacedFonts)
        self.mono_font.setWritingSystem(QFontDatabase.WritingSystem.Latin)
        self.mono_size = tip(QSpinBox(), "Fixed-width text size (code, terminals that follow the system).")
        self.mono_size.setRange(6, 32)
        self.mono_size.setSuffix(" pt")
        self.font_sample = plain_label("The quick brown fox jumps over the lazy dog", wrap=True)
        self.mono_sample = plain_label("~/dotfiles $ ls -la  {}[]()=>", wrap=True)
        self.font_hint = muted()
        self.ui_font.currentFontChanged.connect(lambda f: self._font_picked("ui", f.family()))
        self.mono_font.currentFontChanged.connect(lambda f: self._font_picked("mono", f.family()))
        for box in (self.ui_size, self.mono_size):
            box.valueChanged.connect(lambda _: (self._mark(("fonts", "*"), self._fonts_value), self._show_fonts()))
        ui_row, mono_row = QHBoxLayout(), QHBoxLayout()
        ui_row.addWidget(self.ui_font, 1)
        ui_row.addWidget(self.ui_size)
        mono_row.addWidget(self.mono_font, 1)
        mono_row.addWidget(self.mono_size)
        form = QFormLayout()
        form.addRow(self._label("Interface:", "Menus, buttons and window titles."), ui_row)
        form.addRow("", self.font_sample)
        form.addRow(self._label("Fixed width:", "Terminals and code."), mono_row)
        form.addRow("", self.mono_sample)
        card.body.addLayout(form)
        card.body.addWidget(self.font_hint)
        return card

    def _build_terminal(self) -> Card:
        card = Card("Terminal and shell", "utilities-terminal")
        self.term_box = card
        self.greeting = self._check(
            "shell", "greeting", "Show system info when a terminal opens", "fastfetch prints a logo and system details."
        )
        self.user_host = self._check(
            "shell", "show_user_host", "Include user@host in it", "Your user and computer name."
        )
        self.greeting.toggled.connect(lambda _: self._sync_enabled())
        self.logo_kind = QComboBox()
        for label, (value, hint) in LOGO_CHOICES.items():
            self.logo_kind.addItem(label, value)
            self.logo_kind.setItemData(self.logo_kind.count() - 1, hint, Qt.ItemDataRole.ToolTipRole)
        tip(self.logo_kind, "What the greeting shows on the left.")
        self._logo_kind_was = self.logo_kind.currentData()
        self.logo_kind.currentIndexChanged.connect(self._logo_kind_changed)
        self.logo_builtin = tip(QComboBox(), "Type to search fastfetch's logos.")
        self.logo_builtin.setEditable(True)
        self.logo_builtin.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.logo_text = tip(QLineEdit(), "Up to 12 letters or digits, drawn in big block letters.")
        self.logo_text.setMaxLength(blockfont.MAX_LENGTH)
        self.logo_text.setPlaceholderText("Up to 12 letters, e.g. HELLO")
        self.ascii_sample = tip(QComboBox(), "Start from a sample, then edit it below.")
        self.ascii_sample.addItem("Start from a sample…", None)
        for name in asciiart.SAMPLES:
            self.ascii_sample.addItem(name, name)
        self.ascii_sample.currentIndexChanged.connect(self._load_ascii_sample)
        self.logo_ascii = QPlainTextEdit()
        self.logo_ascii.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.logo_ascii.setPlaceholderText("Draw your logo with characters, e.g.\n  [0-0]\n  /)  )")
        self.logo_ascii.setFixedHeight(130)
        self.logo_ascii.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        tip(self.logo_ascii, "Up to 24 lines of 80 characters. Any characters except $ and tabs.")
        self.logo_photo = DropZone("Drop a photo for the terminal here")
        self.logo_photo.picked.connect(lambda paths: self._use_file("logo", paths[0]))
        self.logo_photo.cleared.connect(lambda: self._forget_file("logo"))
        self.logo_colour = QComboBox()
        self.logo_colour.setMaxVisibleItems(len(LOGO_COLOUR_CHOICES))
        for label, value in LOGO_COLOUR_CHOICES.items():
            self.logo_colour.addItem(label, value)
        self._logo_icons_for: dict[str, str] = {}
        tip(
            self.logo_colour,
            "Colours of the logo. Original keeps fastfetch's own colours; the schemes blend several colours "
            "in the direction below; the rest paint it in one palette colour.",
        )
        self.logo_direction = QComboBox()
        for label, value in LOGO_DIRECTION_CHOICES.items():
            self.logo_direction.addItem(label, value)
        tip(self.logo_direction, "Which way the colours of a scheme run across the logo.")
        self.logo_size = QComboBox()
        for label, value in (("Small", "small"), ("Medium", "medium"), ("Large", "large")):
            self.logo_size.addItem(label, value)
        tip(self.logo_size, "How big a picture or text logo is drawn.")
        self.logo_preview = QLabel()
        self.logo_preview.setToolTip("Roughly how the logo looks in the terminal.")
        self.logo_hint = muted()
        for signal in (
            self.logo_kind.currentIndexChanged,
            self.logo_builtin.currentTextChanged,
            self.logo_text.textChanged,
            self.logo_ascii.textChanged,
            self.logo_colour.currentIndexChanged,
            self.logo_direction.currentIndexChanged,
            self.logo_size.currentIndexChanged,
        ):
            signal.connect(lambda *_: (self._mark(("shell", "*logo"), self._logo_value), self._sync_enabled()))
        self.prompt = self._combo(
            "shell",
            "prompt",
            {"Powerline": "powerline", "Minimal": "minimal", "Off": "none"},
            "Powerline shows folder, git branch and more in coloured blocks.",
        )
        self.zoxide = self._check(
            "shell",
            "zoxide",
            "Smarter cd (zoxide)",
            "cd learns the folders you use: cd docs jumps to the docs folder you visit most, from anywhere.",
        )
        self.opacity = self._slider_spin(
            "terminal", "opacity", 30, 100, " %", "How see-through the terminal background is.", scale=100
        )
        self.term_blur = self._check("terminal", "blur", "Blur behind the terminal", "Frosted glass behind the text.")
        self.font_size = self._spin("terminal", "font_size", 6, 32, " pt", "Text size inside the terminal.")
        self.modules = QListWidget()
        self.modules.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.modules.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.modules.setFixedHeight(150)
        tip(self.modules, "Tick the lines the greeting shows and drag them into any order.")
        self.modules.itemChanged.connect(lambda _: self._mark(("shell", "fastfetch_modules"), self._modules_value))
        self.modules.model().rowsMoved.connect(
            lambda *_: self._mark(("shell", "fastfetch_modules"), self._modules_value)
        )
        self.logo_rows = QFormLayout()
        form = self.logo_rows
        form.addRow(self.greeting)
        form.addRow(self.user_host)
        form.addRow(self._label("Logo:", "What the greeting shows on the left."), self.logo_kind)
        form.addRow(self._label("Name:", "A fastfetch logo name."), self.logo_builtin)
        form.addRow(self._label("Text:", "Drawn in big block letters."), self.logo_text)
        form.addRow(self._label("Drawing:", "Your own character art."), self.ascii_sample)
        form.addRow(self.logo_ascii)
        form.addRow(self.logo_photo)
        form.addRow(self._label("Colour:", "Colours of the logo."), self.logo_colour)
        form.addRow(self._label("Direction:", "Which way a colour scheme runs."), self.logo_direction)
        form.addRow(self._label("Size:", "How big the logo is drawn."), self.logo_size)
        form.addRow(self.logo_preview)
        form.addRow(self.logo_hint)
        form.addRow(self._label("Shown lines:", "Which system details the greeting lists."), self.modules)
        form.addRow(self._label("Prompt:", "The line where you type commands."), self.prompt)
        form.addRow(self.zoxide)
        form.addRow(self._label("Opacity:", "How see-through the terminal is."), self.opacity)
        form.addRow(self.term_blur)
        form.addRow(self._label("Text size:", "Text size inside the terminal."), self.font_size)
        card.body.addLayout(form)
        return card

    def _build_login(self) -> Card:
        card = Card("Login screen", "system-users", "The screen where you type your password.")
        self.login_box = card
        manager = login_manager()
        names = {"plasmalogin": "Plasma Login", "sddm": "SDDM. The background is set for its Breeze theme"}
        self.login_background = ChoiceTiles(
            [
                ("Keep it", "keep", "Leave the login screen as it is."),
                ("Wallpaper", "wallpaper", "Use this preset's first wallpaper."),
                ("My image", "image", "Use an image you drop below."),
            ],
            QSize(80, 48),
        )
        self.login_drop = DropZone("Drop a login background here")
        self.login_drop.picked.connect(lambda paths: self._use_file("login_background", paths[0]))
        self.login_drop.cleared.connect(lambda: self._forget_file("login_background"))
        self.login_background.changed.connect(
            lambda: (self._mark(("login", "*"), self._login_value), self._sync_enabled())
        )
        status = (
            f"Found {names[manager]}. Changing it asks for your password and affects every user. "
            "Restore puts the old setting back."
            if manager
            else "No supported login manager found (Plasma Login or SDDM), so this is skipped."
        )
        card.body.addWidget(self.login_background)
        card.body.addWidget(self.login_drop)
        card.body.addWidget(muted(status))
        card.setEnabled(manager is not None)
        return card

    # ----- widget factories ------------------------------------------------------------
    def _label(self, text: str, hint: str) -> QLabel:
        label = plain_label(text)
        label.setToolTip(hint)
        return label

    def _mark(self, key: tuple[str, str], getter: Callable[[], Any]) -> None:
        if self._loading:
            return
        self._dirty[key] = getter
        self.changed.emit()

    def _check(self, section: str, key: str, label: str, hint: str) -> QCheckBox:
        box = QCheckBox(label)
        box.setToolTip(hint)
        box.toggled.connect(lambda _: self._mark((section, key), box.isChecked))
        self._getters[(section, key)] = box.isChecked
        return box

    def _combo(self, section: str, key: str, choices: dict[str, Any], hint: str) -> QComboBox:
        box = QComboBox()
        box.setToolTip(hint)
        for label, value in choices.items():
            box.addItem(label, value)
        box.currentIndexChanged.connect(lambda _: self._mark((section, key), box.currentData))
        self._getters[(section, key)] = box.currentData
        return box

    def _spin(self, section: str, key: str, low: int, high: int, suffix: str, hint: str) -> QSpinBox:
        box = QSpinBox()
        box.setToolTip(hint)
        box.setRange(low, high)
        box.setSuffix(suffix)
        box.valueChanged.connect(lambda _: self._mark((section, key), box.value))
        self._getters[(section, key)] = box.value
        return box

    def _slider_spin(
        self,
        section: str,
        key: str,
        low: int,
        high: int,
        suffix: str,
        hint: str,
        scale: int = 1,
        convert: Callable[[int], Any] | None = None,
    ) -> SliderSpin:
        """``scale`` divides the shown number (percent to fraction); ``convert`` maps it instead."""
        widget = SliderSpin(low, high, suffix)
        widget.setToolTip(hint)

        def value() -> Any:
            if convert is not None:
                return convert(widget.value())
            return widget.value() / scale if scale != 1 else widget.value()

        widget.valueChanged.connect(lambda _: self._mark((section, key), value))
        self._getters[(section, key)] = value
        return widget

    # ----- loading a preset ------------------------------------------------------------
    def load(self, preset: Preset | None, overrides: dict | None = None, custom_dir: Path | None = None) -> None:
        """Show ``preset``. ``overrides`` (with their files in ``custom_dir``) are tweaks
        saved earlier, for example the ones applied last time: they are shown as changes."""
        self.preset = preset
        self._dirty.clear()
        self._colours = {}
        self._wall_entries = None
        self._files = {}
        self._custom_dir = None
        if preset is None:
            return
        effective = preset
        if overrides:
            folder = custom_dir if custom_dir is not None and custom_dir.is_dir() else None
            overrides = usable_overrides(preset, overrides, folder)
            if overrides:
                # The user's own values (they validated above). What glass adds on top of them
                # (stronger blur, more see-through panels) is applied, but it is not their setting:
                # shown here it would come back as a tweak of theirs after every apply.
                effective = dataclasses.replace(preset, data=presets.merge(preset.data, overrides))
                self._custom_dir = folder
                # A section can be None ("Keep it" removes the preset's own section).
                self._colours = dict(overrides.get("palette") or {})
                if "wallpaper" in overrides and overrides["wallpaper"] is None:
                    self._wall_entries = []  # every picture removed: the current wallpaper stays
                elif "images" in (overrides.get("wallpaper") or {}):
                    self._wall_entries = [dict(i) for i in overrides["wallpaper"]["images"]]
                for role, (section, key) in FILE_ROLES.items():
                    name = (overrides.get(section) or {}).get(key)
                    if name and folder is not None and (folder / name).is_file():
                        self._files[role] = name
        self._loading = True
        try:
            runner = Runner()
            # A preset without [effects] leaves tiling alone; one that leaves only tiling out
            # gives back what the user had before Plasmify. Tweaks can add [effects] to a preset
            # without one, and then the apply runs the effects step too.
            if effective.section("effects"):
                self._tiling_default = own_tiling(runner)
            else:
                self._tiling_default = runner.kread(*TILING_SWITCH) == "true"
            self._system_cursor = runner.kread("kcminputrc", "Mouse", "cursorTheme")
            self._fill(preset, effective)
        finally:
            self._loading = False
        for section, table in (overrides or {}).items():
            for key in table if isinstance(table, dict) else ("",):  # None: a removed section
                dirty = dirty_key(section, key)
                if dirty in self._getters:
                    self._dirty[dirty] = self._getters[dirty]
        self._sync_enabled()
        self.changed.emit()

    def _sync_panel_look(self) -> None:
        """Say when the panels keep the user's own Plasma style: the theme has no panel style and
        no setting here gave it one."""
        preset = self.preset
        styled = preset is None or bool(preset.section("panel_style") or self.overrides().get("panel_style"))
        self.panel_look_note.setVisible(not styled)

    def tiling_now(self) -> bool:
        """Whether tiling is on after applying the preset when it leaves tiling out."""
        return self._tiling_default

    def surprise(self) -> None:
        """Replace the current tweaks with a random, checked design (own wallpapers stay)."""
        if self.preset is None:
            return
        # A surprise always has [effects] without tiling: the apply gives tiling back as before Plasmify.
        overrides = surprise.suggest(self.preset, random.randrange(1 << 30), own_tiling(Runner()))  # noqa: S311
        if not overrides:
            return
        own_walls = self._wallpaper_value()
        if own_walls is REMOVE:
            overrides["wallpaper"] = None
        elif own_walls:
            overrides["wallpaper"] = own_walls
        self.load(self.preset, overrides, self._custom_dir)
        self.panels_box.set_open(True)

    def _palette(self) -> dict[str, str]:
        base = self.preset.palette.colors if self.preset else {}
        return {**base, **self._colours}

    def _fill(self, base: Preset, preset: Preset) -> None:
        """Fill every control: values from ``preset`` (the preset with any saved tweaks),
        "Preset: …" labels from ``base``."""

        def set_combo(box: QComboBox, value: Any) -> None:
            index = box.findData(value)
            box.setCurrentIndex(index if index >= 0 else 0)

        def keep_value(box: QComboBox, value: Any, label: str, listed: int | None = None) -> None:
            """Select ``value``. One the menu does not list (set with the command line, captured
            from a desktop, a theme uninstalled since) is added as ``label`` and kept: falling back
            to the first choice would replace the user's setting at the next Apply. ``listed``
            drops the items a previous preset added."""
            while listed is not None and box.count() > listed:
                box.removeItem(box.count() - 1)
            if value is not None and box.findData(value) < 0:
                box.addItem(label, value)
            set_combo(box, value)

        colours = self._palette()
        layout = preset.section("layout")
        style = preset.section("panel_style")
        self.panel_editor.set_colours(colours, style)
        self.panel_editor.set_panels(layout_panels(layout) if layout else starter("bottom-panel"))
        self.radius.setRange(0, 30)
        self.radius.setValue(int(style.get("radius", 6)))  # Breeze panels have about 6 px corners
        self.panel_opacity.setValue(int(round(float(style.get("opacity", 0.85)) * 100)))
        set_combo(self.border, style.get("border", "none"))
        self._fill_panel_colours(style.get("colour", "mantle"))

        effects = preset.section("effects")
        self.windows_box.setEnabled(bool(effects))
        tiling = effects.get("tiling")
        if tiling is None:  # left out: as the user had it before Plasmify (see load)
            tiling = self._tiling_default
        self.tiling.setChecked(bool(tiling))
        set_combo(self.open_close, effects.get("open_close", "scale"))
        set_combo(self.minimize, effects.get("minimize", "magiclamp"))
        set_combo(self.desktop_switch, effects.get("desktop_switch", "slide"))
        self.wobbly.setChecked(bool(effects.get("wobbly", False)))
        self.wobbly_level.setValue(int(effects.get("wobbly_level", 2)))
        self.fall_apart.setChecked(bool(effects.get("fall_apart", False)))
        self.sheet.setChecked(bool(effects.get("sheet", False)))
        self.cube.setChecked(bool(effects.get("cube", False)))
        self.blur.setChecked(bool(effects.get("blur", True)))
        self.blur_strength.setValue(int(effects.get("blur_strength", 10)))
        self.blur_noise.setValue(int(effects.get("blur_noise", 4)))
        self.blur_saturation.setValue(int(effects.get("blur_saturation", 100)))
        self.moving_opacity.setValue(int(round(float(effects.get("moving_opacity", 1.0)) * 100)))
        self.dim_inactive.setValue(int(effects.get("dim_inactive", 0)))
        speed = effects.get("animation_speed")
        keep_value(self.speed, speed, speed_text(speed or 0), len(ANIMATION_SPEEDS))
        set_combo(self.titlebar, effects.get("titlebar_buttons"))
        set_combo(self.borders, effects.get("window_borders"))

        # A record or an older preset may still say "subtle": it shows as Frosted, and stays as it
        # is until the user picks another look (see _glass_value).
        looks = preset.section("style")
        self.glass_tiles.paint_with(lambda p, r, v: draw_glass_tile(p, r, v, colours))
        look = glass.canonical(looks.get("glass", "off"))
        self.glass_tiles.set_value(look)
        self._glass_kept = tuple(looks.get(key, MISSING) for key in glass.CHOICE_KEYS)
        self._glass_loaded = self._glass_kept
        self._glass_dark = self._dark()
        self.transparency.setValue(self._shown_transparency(look, looks.get("transparency", MISSING)))
        self.focused_solid.setChecked(self._shown_focus(looks.get("focused_solid", MISSING)))

        wallpaper = preset.section("wallpaper")
        self._show_walls()
        interval = int(wallpaper.get("interval", 1800))
        keep_value(self.interval, interval, interval_text(interval), len(INTERVALS))
        set_combo(self.order, wallpaper.get("order", "listed"))
        set_combo(self.shimmer, wallpaper.get("shimmer", "none"))
        # A colour the menu does not list (another palette name, a #rrggbb) is shown as it is.
        sparkle = wallpaper.get("shimmer_colour", "accent")
        label = sparkle if sparkle.startswith("#") else sparkle.capitalize()
        keep_value(self.shimmer_colour, sparkle, label, len(SHIMMER_COLOURS))
        set_combo(self.shimmer_light, wallpaper.get("shimmer_light", "auto"))
        set_combo(self.shimmer_motion, wallpaper.get("shimmer_motion", "auto"))
        set_combo(self.shimmer_density, wallpaper.get("shimmer_density", "normal"))
        set_combo(self.shimmer_fps, wallpaper.get("shimmer_fps", 12))
        self.shimmer_always.setChecked(bool(wallpaper.get("shimmer_always", False)))

        splash = preset.section("splash")
        self.splash_style.paint_with(lambda p, r, v: draw_splash_tile(p, r, v, colours))
        self.splash_style.set_value(splash.get("style", "plasmify") if splash else "keep")
        set_combo(self.splash_background, splash.get("background", "palette"))
        set_combo(self.splash_logo, splash.get("logo", "distro"))
        self.splash_text.setText(splash.get("text", ""))
        self.splash_progress.setChecked(bool(splash.get("progress", True)))
        self._show_preset_file(self.splash_background_drop, splash.get("background_file"), "Using the palette colour")
        self._show_preset_file(self.splash_logo_drop, splash.get("logo_file"), "")

        self._fill_colours()
        icons = base.section("icons")
        self.icon_theme.clear()
        self.icon_theme.addItem(f"Preset: {icons.get('theme', 'unchanged')}", None)
        themes = installed_icon_themes()
        for theme in themes:
            self.icon_theme.addItem(theme, theme)
        for papirus in ("Papirus", "Papirus-Dark", "Papirus-Light"):
            if papirus not in themes:
                self.icon_theme.addItem(f"{papirus} (installs Papirus)", papirus)
        self.folders.clear()
        self.folders.addItem(f"Preset: {icons.get('folder_color', 'default')}", None)
        for colour in generate.PAPIRUS_COLOURS:
            self.folders.addItem(colour.capitalize(), colour)
        cursor = base.section("cursor")
        self.cursor_theme.clear()
        self.cursor_theme.addItem(f"Preset: {cursor.get('name', 'unchanged')}", None)
        for name in installed_cursor_themes():
            self.cursor_theme.addItem(name, name)
        # Saved tweaks pick a specific theme instead of "Preset: …".
        chosen = preset.section("icons")
        if chosen.get("theme") != icons.get("theme"):
            keep_value(self.icon_theme, chosen.get("theme"), f"{chosen.get('theme')} (not installed)")
        if chosen.get("folder_color") != icons.get("folder_color"):
            keep_value(self.folders, chosen.get("folder_color"), str(chosen.get("folder_color")).capitalize())
        cursor = preset.section("cursor")
        if cursor.get("name") != base.section("cursor").get("name"):
            keep_value(self.cursor_theme, cursor.get("name"), f"{cursor.get('name')} (not installed)")
        self.cursor_size.setValue(int(cursor.get("size", 24)))

        fonts = preset.section("fonts")
        default = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
        fixed = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self._ui_font = fonts.get("ui", default.family())
        self._mono_font = fonts.get("mono", fixed.family())
        self.ui_font.setCurrentFont(QFont(self._ui_font))
        self.mono_font.setCurrentFont(QFont(self._mono_font))
        self.ui_size.setValue(int(fonts.get("ui_size", 10)))
        self.mono_size.setValue(int(fonts.get("mono_size", 10)))
        missing = [f for f in (self._ui_font, self._mono_font) if f not in QFontDatabase.families()]
        self.font_hint.setText(f"Installed when you apply: {', '.join(missing)}." if missing and fonts else "")
        self._show_fonts()

        shell = preset.section("shell")
        self.term_box.setEnabled(bool(shell) or bool(preset.section("terminal")))
        self.greeting.setChecked(bool(shell.get("greeting", True)))
        self.user_host.setChecked(bool(shell.get("show_user_host", False)))
        kind = logo_kind(shell)
        set_combo(self.logo_kind, {"image": "photo" if "logo_file" in shell else "preset-image"}.get(kind, kind))
        has_picture = shell.get("fastfetch_logo", "distro") != "distro"
        self.logo_kind.model().item(self.logo_kind.findData("preset-image")).setEnabled(has_picture)
        if self._logo_names is None:
            self._logo_names = sorted(builtin_logos(), key=str.lower)
            # Once: the panel owns the completer, so a new one per load would pile up.
            self.logo_builtin.setCompleter(QCompleter(self._logo_names, self))
        self.logo_builtin.clear()
        self.logo_builtin.addItems(self._logo_names)
        self.logo_builtin.setCurrentText(shell.get("logo_builtin", "arch"))
        self.logo_text.setText(shell.get("logo_text", ""))
        self.ascii_sample.setCurrentIndex(0)
        self.logo_ascii.setPlainText(shell.get("logo_ascii", ""))
        kind_now = logo_kind(shell)
        default_colour = "original" if kind_now in ("distro", "builtin") else "gradient"
        set_combo(self.logo_colour, shell.get("logo_colour", default_colour))
        set_combo(self.logo_direction, shell.get("logo_direction", "down"))
        set_combo(self.logo_size, shell.get("logo_size", "medium"))
        self._show_preset_file(self.logo_photo, shell.get("logo_file"), "")
        self._fill_modules(shell.get("fastfetch_modules"))
        set_combo(self.prompt, shell.get("prompt", "powerline"))
        self.zoxide.setChecked(bool(shell.get("zoxide", True)))
        terminal = preset.section("terminal")
        self.opacity.setValue(int(round(float(terminal.get("opacity", 0.9)) * 100)))
        self.term_blur.setChecked(bool(terminal.get("blur", True)))
        self.font_size.setValue(int(terminal.get("font_size", 12)))

        login = preset.section("login")
        self.login_background.paint_with(lambda p, r, v: self._draw_login_tile(p, r, v))
        self.login_background.set_value(login.get("background", "keep") if login else "keep")
        self._show_preset_file(self.login_drop, login.get("background_file"), "")

    def _fill_colours(self) -> None:
        colours = self._palette()
        names = ("accent", "red", "orange", "yellow", "green", "cyan", "blue", "magenta")
        options = [(COLOUR_LABELS[k], colours[k]) for k in names if k in colours]
        self.swatches.set_colours(options, colours.get("accent", ""))

    def _draw_login_tile(self, painter: QPainter, rect: QRectF, value: str) -> None:
        colours = self._palette()
        box = rect.adjusted(2, 2, -2, -2)
        shape = QPainterPath()
        shape.addRoundedRect(box, 6, 6)
        fill = {"keep": "#1d1f22", "wallpaper": colours.get("blue", "#5e81ac"), "image": colours.get("mantle", "#222")}
        painter.fillPath(shape, QColor(fill[value]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 200))
        centre = box.center()
        painter.drawEllipse(QRectF(centre.x() - 6, centre.y() - 14, 12, 12))
        painter.setBrush(QColor(255, 255, 255, 120))
        painter.drawRoundedRect(QRectF(centre.x() - 20, centre.y() + 4, 40, 7), 3, 3)

    def _show_preset_file(self, zone: DropZone, name: str | None, fallback: str) -> None:
        if name and self._custom_dir is not None and (self._custom_dir / name).is_file():
            zone.show_images([self._custom_dir / name], "Your image")
            return
        if name and self.preset is not None and self.preset.asset_dir is not None:
            path = safe_child(self.preset.asset_dir, name)
            if path.is_file():
                zone.show_images([path], "From the preset")
                return
        zone.show_caption(fallback)

    def _current_walls(self) -> list[dict]:
        """The slideshow as shown: the user's list, or the preset's own."""
        if self._wall_entries is not None:
            return self._wall_entries
        return [dict(i) for i in self.preset.section("wallpaper").get("images", [])] if self.preset else []

    def _wall_path(self, entry: dict) -> Path | None:
        if "file" in entry:
            for folder in (self._custom_dir, self.preset.asset_dir if self.preset else None):
                if folder is not None and (folder / entry["file"]).is_file():
                    return folder / entry["file"]
            return None
        return cached_wallpaper_file(self.preset, entry) if self.preset else None

    def _show_walls(self) -> None:
        """Fill the thumbnail list, numbered in slideshow order."""
        if self.preset is None:
            return
        entries = self._current_walls()
        self.walls_list.blockSignals(True)
        self.walls_list.clear()
        for number, entry in enumerate(entries, start=1):
            path = self._wall_path(entry)
            item = QListWidgetItem(wallpaper_thumb(path, number), "")
            item.setData(Qt.ItemDataRole.UserRole, entry)
            credit = entry.get("credit", "")
            item.setToolTip(plain_tooltip(f"{number}. {credit}" + ("" if path else "\nDownloaded when you apply")))
            self.walls_list.addItem(item)
        self.walls_list.blockSignals(False)
        if entries:
            note = f"{len(entries)} picture{'s' if len(entries) != 1 else ''}"
            note += ", shown in this order. Drag to reorder." if len(entries) > 1 else "."
        elif self._wall_entries == []:
            note = "No pictures: your current wallpaper stays as it is."
        else:
            note = "This preset keeps your current wallpaper. Add pictures to start a slideshow."
        self.walls_note.setText(note)

    def _walls_changed(self, entries: list[dict]) -> None:
        if any("file" in e for e in entries):
            self.custom_dir()  # the preset's own files are copied next to the user's
        self._wall_entries = entries
        self._show_walls()
        self._mark(("wallpaper", "*own"), self._wallpaper_value)
        self._sync_enabled()

    def _walls_reordered(self) -> None:
        entries = [self.walls_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.walls_list.count())]
        entries = [e for e in entries if e]
        if entries != self._current_walls():  # a drop back on the same place changes nothing
            self._walls_changed(entries)

    def remove_wallpapers(self) -> None:
        selected = {self.walls_list.row(item) for item in self.walls_list.selectedItems()}
        if not selected or self.preset is None:
            return
        self._walls_changed([e for i, e in enumerate(self._current_walls()) if i not in selected])

    def _wallpaper_count(self) -> int:
        return len(self._current_walls())

    def _shimmer_source(self) -> tuple[Path | None, bool]:
        """(the first still wallpaper on this computer, whether any picture can shimmer).
        Animated pictures play as they are, so they are never previewed with sparkles."""
        entries = self._current_walls()[:SHIMMER_MAX]
        for entry in entries:
            path = self._wall_path(entry)
            if path is None:
                return None, True  # downloaded when applied
            try:
                with path.open("rb") as handle:
                    if not downloads.is_animated(handle.read(64)):
                        return path, True
            except OSError:
                continue
        return None, not entries

    def _refresh_shimmer_preview(self) -> None:
        if self.preset is None or self._loading:
            return
        style = self.shimmer.currentData()
        living = style not in (None, "none") and bool(self._current_walls())
        self.shimmer_preview.setVisible(living)
        if not living:
            self.shimmer_preview.stop()
            return
        source, possible = self._shimmer_source()
        if not possible:
            self.shimmer_preview.stop()
            self.shimmer_preview.setVisible(False)
            return
        colours = self._palette()
        ref = self.shimmer_colour.currentData() or "accent"
        colour = colours.get(ref, ref)
        options = shimmer_options(
            {
                "shimmer": style,
                "shimmer_light": self.shimmer_light.currentData() or "auto",
                "shimmer_motion": self.shimmer_motion.currentData() or "auto",
                "shimmer_density": self.shimmer_density.currentData() or "normal",
            }
        )
        options.update(colour=colour if is_hex(colour) else colours["accent"], base=colours["base"], seed=1)
        caption = "" if source else "Shown on a plain background: the wallpapers are downloaded when you apply."
        self.shimmer_preview.show_settings(source, options, caption)

    def _sync_enabled(self) -> None:
        slideshow = self._wallpaper_count() > 1
        self.interval.setEnabled(slideshow)
        self.order.setEnabled(slideshow)
        alone = "Only one wallpaper, so there is nothing to change. Add more pictures for a slideshow."
        self.interval.setToolTip("How often the slideshow moves on." if slideshow else alone)
        self.order.setToolTip("Show the pictures in the order above, or mixed up." if slideshow else alone)
        self.user_host.setEnabled(self.greeting.isChecked())
        self.blur_strength.setEnabled(self.blur.isChecked())
        self.blur_noise.setEnabled(self.blur.isChecked())
        self.blur_saturation.setEnabled(self.blur.isChecked())
        self.wobbly_level.setEnabled(self.wobbly.isChecked())
        # Shimmer brings pictures to life: with none (a preset that keeps the current wallpaper)
        # there is nothing to apply it to, and Plasmify refuses the setting.
        pictures = self._wallpaper_count() > 0
        self.shimmer.setEnabled(pictures)
        self.shimmer.setToolTip(SHIMMER_HINT if pictures else "No wallpapers to bring to life. Add pictures first.")
        living = pictures and self.shimmer.currentData() not in (None, "none")
        for control in (
            self.shimmer_colour,
            self.shimmer_light,
            self.shimmer_motion,
            self.shimmer_density,
            self.shimmer_fps,
            self.shimmer_always,
        ):
            control.setEnabled(living)
        self.shimmer_density.setEnabled(living and self.shimmer.currentData() != "calm")
        self._refresh_shimmer_preview()

        # Corners cannot be rounder than about half the thinnest panel.
        thinnest = min_thickness(self.panel_editor.panels())
        cap = min(max(thinnest // 2 - 2, 1), 30)  # 30 px is also the most a preset may store
        if self.radius.maximum() != cap:
            before = self.radius.value()
            self._clamping = True
            self.radius.blockSignals(True)
            self.radius.setRange(0, cap)
            self.radius.setValue(min(self._radius_wanted, cap))  # thicker panels bring it back
            self.radius.blockSignals(False)
            self._clamping = False
            if self.radius.value() != before and ("panel_style", "radius") in self._dirty and not self._loading:
                self.changed.emit()  # the applied corners changed with the panels
        self.radius.setToolTip(
            f"How round the panel corners are. Up to {cap} px because the thinnest panel is {thinnest} px; "
            "make panels thicker for rounder corners."
        )

        kind = self.logo_kind.currentData()
        rows = self.logo_rows
        for widget, kinds in (
            (self.logo_builtin, ("builtin",)),
            (self.logo_text, ("text",)),
            (self.ascii_sample, ("ascii",)),
            (self.logo_ascii, ("ascii",)),
            (self.logo_photo, ("photo",)),
            (self.logo_colour, TEXT_LOGOS),
            (self.logo_direction, TEXT_LOGOS),
            (self.logo_size, ("text", "photo", "preset-image")),
        ):
            rows.setRowVisible(widget, kind in kinds)
        # "Original colours" only exists for fastfetch's own logos.
        original = self.logo_colour.findData("original")
        self.logo_colour.view().setRowHidden(original, kind in ("text", "ascii"))
        if kind in ("text", "ascii") and self.logo_colour.currentData() == "original":
            self.logo_colour.blockSignals(True)
            self.logo_colour.setCurrentIndex(self.logo_colour.findData("gradient"))
            self.logo_colour.blockSignals(False)
        self.logo_direction.setEnabled(self.logo_colour.currentData() in schema.LOGO_SCHEMES)
        self._paint_logo_colours()
        hint = ""
        if kind == "text" and self.logo_text.text() and not blockfont.supported(self.logo_text.text()):
            hint = "Use letters, digits, spaces and ! ? . - _ + # @ & *"
        elif kind == "ascii" and self.logo_ascii.toPlainText().strip():
            if schema.ascii_art(self._logo_art()) is not None:
                hint = "Keep it to 24 lines of 80 characters, without $ (it starts colour codes)."
        elif kind == "photo" and "logo" not in self._files:
            hint = "Drop a photo above to show it in the terminal."
        elif kind == "builtin" and self.logo_builtin.currentText() not in (self._logo_names or []):
            hint = "Pick a name from the list (fastfetch knows about 600 logos)."
        self.logo_hint.setText(hint)
        self._update_logo_preview(kind)

        splash_style = self.splash_style.value()
        for widget in (self.splash_background, self.splash_logo, self.splash_text, self.splash_progress):
            widget.setEnabled(splash_style == "plasmify")
        self.splash_background_drop.setVisible(
            splash_style == "plasmify" and self.splash_background.currentData() == "image"
        )
        self.splash_logo_drop.setVisible(splash_style == "plasmify" and self.splash_logo.currentData() == "image")
        self.splash_preview.setEnabled(splash_style in ("plasmify", "breeze"))
        self.login_drop.setVisible(self.login_background.value() == "image")

        see_through = (self.glass_tiles.value() or "off") != "off"
        self.transparency.setEnabled(see_through)
        self.transparency.setToolTip(
            TRANSPARENCY_HINT if see_through else "Solid windows are not see-through. Pick a glass look first."
        )
        self.focused_solid.setEnabled(see_through)
        self.focused_solid.setToolTip(
            FOCUSED_SOLID_HINT if see_through else "Solid windows are not see-through. Pick a glass look first."
        )

        theme = self.icon_theme.currentData()
        base = theme if theme is not None else (self.preset.section("icons").get("theme", "") if self.preset else "")
        self.folders.setEnabled(base.startswith("Papirus"))
        self.icon_sample.setPixmap(icon_theme_strip(base or None, 28))
        cursor = self.cursor_theme.currentData()
        if cursor is None and self.preset is not None:
            cursor = self.preset.section("cursor").get("name")
        self.cursor_sample.setPixmap(cursor_strip(cursor, 28))
        self._update_summaries()

    def _logo_art(self) -> str:
        """The drawing as it is saved (and drawn by fastfetch): tabs become four spaces."""
        return self.logo_ascii.toPlainText().replace("\t", "    ").rstrip("\n")

    def _logo_kind_changed(self) -> None:
        """A fastfetch logo picked from the list starts in its own colours."""
        kind, was = self.logo_kind.currentData(), self._logo_kind_was
        self._logo_kind_was = kind
        if not self._loading and kind in ("distro", "builtin") and was not in ("distro", "builtin"):
            self.logo_colour.setCurrentIndex(self.logo_colour.findData("original"))

    def _paint_logo_colours(self) -> None:
        """Colour strips in the logo colour list, redrawn when the palette changes."""
        colours = self._palette()
        if colours == self._logo_icons_for or "accent" not in colours:
            return
        self._logo_icons_for = dict(colours)
        palette = Palette(colours)
        for index in range(self.logo_colour.count()):
            value = self.logo_colour.itemData(index)
            if value == "original":
                self.logo_colour.setItemIcon(index, QIcon.fromTheme("fastfetch", QIcon.fromTheme("edit-undo")))
            elif value in schema.LOGO_SCHEMES:
                self.logo_colour.setItemIcon(index, scheme_icon(logo_stops(palette, value)))
            else:
                self.logo_colour.setItemIcon(index, colour_icon(logo_stops(palette, value)[0]))

    def _update_logo_preview(self, kind: str | None) -> None:
        colours = self._palette()
        size = QSize(360, 150)
        background = colours.get("base", "#1e1e2e")
        choice = self.logo_colour.currentData() or "gradient"
        direction = self.logo_direction.currentData() or "down"
        rows: list[str] = []
        picture: Path | None = None
        original = False
        if kind == "text" and self.logo_text.text() and blockfont.supported(self.logo_text.text()):
            rows = blockfont.render(self.logo_text.text(), 1)
        elif kind == "ascii":
            rows = self._logo_art().split("\n")[: schema.ASCII_MAX_LINES]
        elif kind in ("distro", "builtin") and self.greeting.isChecked():
            name = self.logo_builtin.currentText().strip() if kind == "builtin" else None
            if kind == "distro" or name in (self._logo_names or []):
                rows = fastfetch_logo_rows(name)
                original = choice == "original"
        elif kind == "photo" and "logo" in self._files and self._custom_dir is not None:
            picture = self._custom_dir / self._files["logo"]
        elif kind == "photo" and self.preset and self.preset.asset_dir:
            name = self.preset.section("shell").get("logo_file")
            picture = safe_child(self.preset.asset_dir, name) if name else None
        if picture is not None and picture.is_file():
            pixmap = QPixmap(size)
            pixmap.fill(QColor(background))
            image = QPixmap(str(picture)).scaled(
                size.width() - 16,
                size.height() - 12,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            painter = QPainter(pixmap)
            painter.drawPixmap((size.width() - image.width()) // 2, (size.height() - image.height()) // 2, image)
            painter.end()
            self.logo_preview.setPixmap(pixmap)
            self.logo_preview.setVisible(True)
            return
        self.logo_preview.setVisible(bool(rows))
        if rows:
            palette = Palette(colours)
            # fastfetch's own colours are not known here: shown in the accent colour.
            lines, codes = coloured_lines(rows, palette, "accent" if original else choice, direction)
            self.logo_preview.setPixmap(logo_picture(lines, codes, background, size))

    def _show_fonts(self) -> None:
        ui = QFont(self._ui_font)
        ui.setPointSize(self.ui_size.value() + 1)
        mono = QFont(self._mono_font)
        mono.setPointSize(self.mono_size.value() + 1)
        self.font_sample.setFont(ui)
        self.mono_sample.setFont(mono)

    def _font_picked(self, which: str, family: str) -> None:
        if self._loading:
            return
        if schema.FONT(family) is not None:
            self.font_hint.setText(f"{family} has characters Plasmify cannot store; pick another font.")
            return
        self.font_hint.setText("")
        if which == "ui":
            self._ui_font = family
        else:
            self._mono_font = family
        self._show_fonts()
        self._mark(("fonts", "*"), self._fonts_value)

    def _load_ascii_sample(self) -> None:
        name = self.ascii_sample.currentData()
        if name is not None:
            self.logo_ascii.setPlainText(asciiart.sample(name))

    # ----- panel colour, greeting lines --------------------------------------------------
    def _fill_panel_colours(self, current: str) -> None:
        if current != "custom":
            self._panel_colour_current = current
        colours = self._palette()
        self.panel_colour.blockSignals(True)
        self.panel_colour.clear()
        for key, label in PANEL_COLOURS.items():
            self.panel_colour.addItem(colour_icon(colours.get(key, "#888888")), label, key)
        if current.startswith("#"):
            self.panel_colour.addItem(colour_icon(current), f"Custom {current}", current)
        self.panel_colour.addItem(QIcon.fromTheme("color-picker"), "Custom colour…", "custom")
        index = self.panel_colour.findData(current)
        self.panel_colour.setCurrentIndex(max(index, 0))
        self.panel_colour.blockSignals(False)

    def _panel_colour_picked(self, _index: int) -> None:
        choice = self.panel_colour.currentData()
        if choice == "custom":
            previous = self._panel_colour_current
            start = QColor(previous if previous.startswith("#") else self._palette().get(previous, "#222222"))
            colour = QColorDialog.getColor(start, self, "Panel colour")
            choice = colour.name() if colour.isValid() else previous  # cancel keeps what was there
            self._fill_panel_colours(choice)
        else:
            self._panel_colour_current = choice
        self._mark(("panel_style", "colour"), self.panel_colour.currentData)
        self._sync_enabled()

    def _fill_modules(self, chosen: list[str] | None) -> None:
        picked = [m for m in (chosen or DEFAULT_MODULES + ["colors"]) if m not in ("break", "title", "separator")]
        self.modules.blockSignals(True)
        self.modules.clear()
        rest = [m for m in schema.FASTFETCH_MODULES if m not in picked and m not in ("break", "title", "separator")]
        for name in picked + rest:
            icon, _colour = MODULE_STYLE.get(name, ("", "accent"))
            item = QListWidgetItem(f"{icon.strip() or '•'}  {MODULE_LABELS.get(name, name.capitalize())}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled)
            item.setCheckState(Qt.CheckState.Checked if name in picked else Qt.CheckState.Unchecked)
            if name == "weather":
                item.setToolTip(
                    "Asks wttr.in for the weather every time a terminal opens (plain HTTP, so the site and "
                    "your network see the request and your IP address)."
                )
            self.modules.addItem(item)
        self.modules.blockSignals(False)

    # ----- hints with one-click fixes ---------------------------------------------------
    def set_advice(self, advice: list) -> None:
        while self.advice_layout.count():
            item = self.advice_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                while item.layout().count():
                    inner = item.layout().takeAt(0)
                    if inner.widget():
                        inner.widget().deleteLater()
        for hint in advice:
            row = QHBoxLayout()
            icon = QLabel()
            name = "dialog-warning" if hint.level == "warning" else "dialog-information"
            icon.setPixmap(QIcon.fromTheme(name).pixmap(18, 18))
            row.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
            row.addWidget(plain_label(hint.text, wrap=True), 1)
            if hint.fix:
                button = QPushButton(hint.fix)
                button.setToolTip("Change the settings so this works as expected.")
                button.clicked.connect(lambda _=False, h=hint.id: self.apply_fix(h))
                row.addWidget(button, 0, Qt.AlignmentFlag.AlignTop)
            self.advice_layout.addLayout(row)
        self.advice_box.setVisible(bool(advice))

    def apply_fix(self, advice_id: str) -> None:
        radius = self._radius_wanted  # what the user asked for, not the clamped value

        def fix_panels(panels: dict) -> None:
            ordered = [panels[e] for e in ("top", "bottom", "left", "right") if e in panels]
            if advice_id == "tiling-hidden":
                for panel in ordered:
                    if panel["hiding"] == "dodge":
                        panel["hiding"] = "always"
            elif advice_id == "radius-capped":
                for panel in ordered:
                    panel["thickness"] = max(panel["thickness"], min((radius + 2) * 2, 128))
            elif advice_id == "no-launcher" and ordered:
                ordered[0]["widgets"].insert(0, "launcher")
            elif advice_id == "no-tray" and ordered:
                target = next((p for p in ordered if p["style"] in ("bar", "islands")), ordered[0])
                target["widgets"].append("tray")
            elif advice_id == "labels-vertical":
                for panel in ordered:
                    if panel["edge"] in ("left", "right"):
                        panel["widgets"] = ["tasks" if w == "tasks_labels" else w for w in panel["widgets"]]

        if advice_id in ("tiling-hidden", "radius-capped", "no-launcher", "no-tray", "labels-vertical"):
            self.panel_editor.edit_all(fix_panels)
            if advice_id == "radius-capped":
                self._sync_enabled()
                self.radius.setValue(radius)
        elif advice_id == "clear-without-blur":
            self.blur.setChecked(True)
        elif advice_id == "logo-hidden":
            self.greeting.setChecked(True)

    # ----- one-line summaries on the collapsed cards ---------------------------------------
    def _update_summaries(self) -> None:
        if self.preset is None:
            return
        panels = self.panel_editor.panels()
        names = [f"{p['edge']} {p['style']}" for p in panels]
        self.panels_box.set_summary(f"{', '.join(names)} · {self.radius.value()} px corners")
        tiling = "tiling" if self.tiling.isChecked() else "floating windows"
        self.windows_box.set_summary(f"{tiling} · {self.open_close.currentText().lower()}")
        look = self.glass_tiles.value() or "off"
        glass_summary = f"{glass.LABELS[look]}, {self.transparency.value()} % see-through"
        if self.focused_solid.isChecked():
            glass_summary += ", background windows only"
        self.glass_box.set_summary("Solid windows" if look == "off" else glass_summary)
        count = self._wallpaper_count()
        if count > 1:
            shuffled = ", shuffled" if self.order.currentData() == "shuffle" else ""
            self.walls_box.set_summary(f"{count} images, every {self.interval.currentText()}{shuffled}")
        else:
            self.walls_box.set_summary("1 image" if count else "unchanged")
        splash = self.splash_style.value() or "keep"
        self.splash_box.set_summary({"keep": "unchanged", "none": "off"}.get(splash, splash.capitalize()))
        self.colours_box.set_summary(f"accent {self._palette().get('accent', '')}")
        icons = self.icon_theme.currentText().removeprefix("Preset: ")
        self.icons_box.set_summary(f"{icons} · {self.cursor_theme.currentText().removeprefix('Preset: ')}")
        self.fonts_box.set_summary(
            f"{self._ui_font} {self.ui_size.value()} · {self._mono_font} {self.mono_size.value()}"
        )
        self.term_box.set_summary(
            f"{self.logo_kind.currentText().lower()} · {self.prompt.currentText().lower()} prompt"
        )
        login = self.login_background.value() or "keep"
        self.login_box.set_summary({"keep": "unchanged", "wallpaper": "wallpaper", "image": "your image"}[login])

    # ----- glass -------------------------------------------------------------------------
    def _dark(self) -> bool:
        """Whether the colours shown are dark; glass is less see-through over light ones."""
        base = self._palette().get("base")
        return is_dark(base) if is_hex(base) else True

    def _shown_transparency(self, look: str, stored: Any = MISSING, dark: bool | None = None) -> int:
        """What the See-through slider shows for ``look``: ``stored`` (a preset's or a saved value)
        kept within the slider's range, else the look's default for the colours. Without glass the
        slider waits at frosted's amount."""
        style: dict[str, Any] = {"glass": look if look != "off" else "frosted"}
        if stored is not MISSING:
            style["transparency"] = stored
        return glass.effective_transparency(style, self._dark() if dark is None else dark)

    def _show_transparency_quietly(self, value: int) -> None:
        self.transparency.blockSignals(True)
        self.transparency.setValue(value)
        self.transparency.blockSignals(False)

    @staticmethod
    def _shown_focus(stored: Any = MISSING) -> bool:
        """Whether the box keeps the window in use solid: ``stored`` (a preset's or a saved value)
        when it is true or false, else the default."""
        return stored if isinstance(stored, bool) else glass.FOCUSED_SOLID

    def _glass_picked(self) -> None:
        """A look was picked: the slider moves to that look's own amount, which is no extra change.
        Picking the look the tab was filled with again brings back what it was filled with (a
        record's "subtle" or its own amount), so going back and forth leaves nothing changed. The
        box keeps the window in use solid or not in every look, so it stays as it is."""
        if self._loading:
            return
        picked = self.glass_tiles.value() or "off"
        loaded_look, loaded_amount, _loaded_focus = self._glass_loaded
        focus = self._glass_kept[2]
        if glass.canonical(loaded_look if loaded_look is not MISSING else "off") == picked:
            self._glass_kept = (loaded_look, loaded_amount, focus)
            self._show_transparency_quietly(self._shown_transparency(picked, loaded_amount))
        else:
            self._glass_kept = (CHANGED, CHANGED, focus)
            self._show_transparency_quietly(self._shown_transparency(picked))
        self._mark(("style", "*glass"), self._glass_value)
        self._sync_enabled()

    def _transparency_moved(self, _value: int) -> None:
        if self._loading:
            return
        look, _amount, focus = self._glass_kept
        self._glass_kept = (look, CHANGED, focus)
        self._mark(("style", "*glass"), self._glass_value)
        self._update_summaries()

    def _focus_toggled(self, _checked: bool) -> None:
        if self._loading:
            return
        look, amount, _focus = self._glass_kept
        self._glass_kept = (look, amount, CHANGED)
        self._mark(("style", "*glass"), self._glass_value)
        self._update_summaries()

    def _glass_follow_mode(self) -> None:
        """After new colours: a slider at the look's default moves to the default of the new light
        or dark mode, as a preset without a transparency of its own does."""
        dark, was = self._dark(), self._glass_dark
        self._glass_dark = dark
        if dark == was:
            return
        look = self.glass_tiles.value() or "off"
        amount = self._glass_kept[1]
        if amount is MISSING or (
            amount is CHANGED and self.transparency.value() == self._shown_transparency(look, dark=was)
        ):
            self._show_transparency_quietly(self._shown_transparency(look))

    def _glass_value(self) -> dict[str, Any] | None:
        """The glass look, transparency and focused_solid, as far as they differ from the preset.

        What the tab was filled with stays exactly as it was until the user changes it: a record
        can still say "subtle", or keep a transparency while glass is off, and a value the slider
        cannot show is never rewritten. A slider at the look's default stores nothing, so the
        transparency keeps following the look and the light or dark mode; a box at the default
        stores nothing either.
        """
        if self.preset is None:
            return None
        own = self.preset.section("style")
        own_look, own_amount, own_focus = (own.get(key, MISSING) for key in glass.CHOICE_KEYS)
        picked = self.glass_tiles.value() or "off"
        look, amount, focus = self._glass_kept
        if look is CHANGED:  # the preset's own look under an older name is no change
            look = own_look if glass.canonical(own_look) == picked else picked
        if amount is CHANGED:
            if picked == "off":
                amount = own_amount  # not used without glass, so the preset's own value stays
            elif self.transparency.value() == glass.default_transparency(picked, self._dark()):
                amount = MISSING
            else:
                amount = self.transparency.value()
        if focus is CHANGED:
            if picked == "off":
                focus = own_focus  # not used without glass either
            elif self.focused_solid.isChecked() == glass.FOCUSED_SOLID:
                focus = MISSING
            else:
                focus = self.focused_solid.isChecked()
        value: dict[str, Any] = {}
        for key, wanted, theirs in zip(
            glass.CHOICE_KEYS, (look, amount, focus), (own_look, own_amount, own_focus), strict=True
        ):
            if wanted != theirs:
                value[key] = None if wanted is MISSING else wanted  # None drops the preset's own
        return value or None

    # ----- files picked or dropped by the user -----------------------------------------
    def custom_dir(self) -> Path:
        """Folder for the user's own files, seeded with the preset's own files (if any)."""
        if self._custom_dir is None:
            if self.preset is None:
                raise RuntimeError("No preset selected")
            folder = custom_folder(self.preset.id)
            # Never wiped: remembered tweaks may still point at files in here.
            folder.mkdir(parents=True, exist_ok=True)
            if self.preset.asset_dir is not None and self.preset.asset_dir.is_dir():
                for item in self.preset.asset_dir.iterdir():
                    target = folder / item.name
                    if item.is_file() and not item.is_symlink() and not target.exists():
                        shutil.copyfile(item, target)
            self._custom_dir = folder
        return self._custom_dir

    def asset_dir(self) -> Path | None:
        return self._custom_dir

    def _copy_in(self, source: Path, name: str) -> str | None:
        try:
            if source.is_file() and source.stat().st_size > MAX_PICTURE_BYTES:
                limit = MAX_PICTURE_BYTES // (1024 * 1024)
                message(self, "Picture too large", f"{source.name} is larger than {limit} MB. Pick a smaller picture.")
                return None
            if not looks_like_image(source):
                message(self, "Not an image", f"{source.name} is not a PNG, JPEG, WebP or GIF image.")
                return None
            problem = downloads.image_file_problem(source)  # too many pixels or frames, from the header
            if problem:
                message(self, "Cannot use this picture", f"{source.name} {problem}.")
                return None
            shutil.copyfile(source, safe_child(self.custom_dir(), name))
        except OSError as exc:
            message(self, "Could not use the image", f"{source.name}: {exc.strerror or exc}")
            return None
        return name

    # role -> (file name stem, drop zone, section marked dirty)
    def _roles(self) -> dict[str, tuple[str, DropZone, tuple[str, str], Callable[[], Any]]]:
        return {
            "logo": ("logo", self.logo_photo, ("shell", "*logo"), self._logo_value),
            "splash_background": ("splash-background", self.splash_background_drop, ("splash", "*"),
                                  self._splash_value),
            "splash_logo": ("splash-logo", self.splash_logo_drop, ("splash", "*"), self._splash_value),
            "login_background": ("login-background", self.login_drop, ("login", "*"), self._login_value),
        }  # fmt: skip

    def _use_file(self, role: str, source: Path) -> None:
        if self.preset is None:
            return
        stem, zone, key, getter = self._roles()[role]
        name = self._copy_in(source, stem + _image_suffix(source))
        if name is None:
            return
        self._files[role] = name
        zone.show_images([self.custom_dir() / name], source.name)
        # Dropping an image means "use it": switch the matching choice over.
        if role == "logo":
            self.logo_kind.setCurrentIndex(self.logo_kind.findData("photo"))
        elif role == "splash_background":
            self.splash_style.set_value("plasmify")
            self.splash_background.setCurrentIndex(self.splash_background.findData("image"))
        elif role == "splash_logo":
            self.splash_style.set_value("plasmify")
            self.splash_logo.setCurrentIndex(self.splash_logo.findData("image"))
        elif role == "login_background":
            self.login_background.set_value("image")
        self._mark(key, getter)
        self._sync_enabled()

    def _forget_file(self, role: str) -> None:
        _stem, zone, key, getter = self._roles()[role]
        self._files.pop(role, None)
        zone.show_caption("Back to the default")
        self._mark(key, getter)
        self._sync_enabled()

    def _own_pictures(self, paths: list[Path]) -> list[dict]:
        """Copy pictures into the custom folder under names that never overwrite another one."""
        if self.preset is None:
            return []
        folder = self.custom_dir()
        taken = {p.name for p in folder.iterdir()}
        entries = []
        for path in paths[:30]:
            number = 1
            while True:
                name = own_wallpaper_name(number, path)
                if name not in taken or same_file(folder / name, path):
                    break
                number += 1
            if name in taken or self._copy_in(path, name):  # the same picture again is reused
                taken.add(name)
                entries.append({"file": name, "credit": "My own wallpaper"})
        return entries

    def use_wallpapers(self, paths: list[Path]) -> None:
        """Replace the slideshow with these pictures."""
        entries = self._own_pictures(paths)
        if entries:
            self._walls_changed(entries)

    def add_wallpapers(self, paths: list[Path]) -> None:
        """Add pictures after the current ones (at most 30 in total)."""
        entries = self._own_pictures(paths)
        if entries:
            self._walls_changed((self._current_walls() + entries)[:30])

    def _choose_wallpapers(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, "Add pictures", str(Path.home() / "Pictures"), IMAGE_FILTER)
        if paths:
            self.add_wallpapers([Path(p) for p in paths])

    def reset_wallpapers(self) -> None:
        self._wall_entries = None
        self._dirty.pop(("wallpaper", "*own"), None)
        self._show_walls()
        self._sync_enabled()
        self.changed.emit()

    # ----- colours ---------------------------------------------------------------------
    def set_accent(self, colour: str) -> None:
        if self.preset is None:
            return
        self._colours["accent"] = colour
        if self.preset.palette.colors.get("accent") == colour:
            self._colours.pop("accent")
        self._colours_changed()

    def pick_accent(self) -> None:
        if self.preset is None:
            return
        colour = QColorDialog.getColor(QColor(self._palette()["accent"]), self, "Accent colour")
        if colour.isValid():
            self.set_accent(colour.name())

    def edit_colours(self) -> None:
        if self.preset is None:
            return
        dialog = ColoursDialog(self._palette(), self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            base = self.preset.palette.colors
            self._colours = {k: v for k, v in dialog.colours.items() if base.get(k) != v}
            self._colours_changed()

    def _colours_changed(self) -> None:
        colours = self._palette()
        self._fill_colours()
        self.panel_editor.set_colours(colours, self.preset.section("panel_style") if self.preset else {})
        self._fill_panel_colours(self.panel_colour.currentData() or "mantle")
        self.splash_style.paint_with(lambda p, r, v: draw_splash_tile(p, r, v, colours))
        self.login_background.paint_with(lambda p, r, v: self._draw_login_tile(p, r, v))
        self.glass_tiles.paint_with(lambda p, r, v: draw_glass_tile(p, r, v, colours))
        self._glass_follow_mode()
        self._mark(("palette", "*"), self._palette_value)
        self._sync_enabled()

    # ----- results ---------------------------------------------------------------------
    def _palette_value(self) -> dict[str, Any] | None:
        return dict(self._colours) or None

    def _panels_value(self) -> dict[str, Any] | None:
        return {"panels": compact(self.panel_editor.panels())}

    def _modules_value(self) -> list[str] | None:
        chosen = []
        for index in range(self.modules.count()):
            item = self.modules.item(index)
            if item.checkState() == Qt.CheckState.Checked:
                chosen.append(item.data(Qt.ItemDataRole.UserRole))
        if "colors" in chosen:
            chosen.insert(chosen.index("colors"), "break")
        return ["break", *chosen]

    def _fonts_value(self) -> dict[str, Any] | None:
        fonts = self.preset.section("fonts") if self.preset else {}
        value: dict[str, Any] = {
            "ui": self._ui_font,
            "ui_size": self.ui_size.value(),
            "mono": self._mono_font,
            "mono_size": self.mono_size.value(),
        }
        # Fonts picked from the list are installed already; only keep the preset's
        # packages while one of its fonts is still in use.
        if fonts.get("ui") != self._ui_font and fonts.get("mono") != self._mono_font:
            value["packages"] = None
        return value

    def _icon_theme_value(self) -> dict[str, Any] | None:
        theme = self.icon_theme.currentData()
        if theme is None or (self.preset and theme == self.preset.section("icons").get("theme")):
            return None  # the preset's own theme is not a change
        return {"theme": theme}

    def _folders_value(self) -> dict[str, Any] | None:
        colour = self.folders.currentData()
        icons = self.preset.section("icons") if self.preset else {}
        if colour is None or (colour == icons.get("folder_color") and "folder_source" not in icons):
            return None  # the preset's own folder colour is not a change
        # A Papirus colour replaces any custom folder source the preset had.
        return {"folder_color": colour, "folder_source": None}

    def _cursor_value(self) -> dict[str, Any] | None:
        name = self.cursor_theme.currentData()
        if name is None or (self.preset and name == self.preset.section("cursor").get("name")):
            return None
        return {"name": name, "url": None}  # already installed: nothing to download

    def _wallpaper_value(self) -> Any:
        entries = self._wall_entries
        if entries is None:
            return None
        if not entries:  # every picture removed: leave the current wallpaper alone
            return REMOVE if self.preset and self.preset.section("wallpaper") else None
        return {"images": [dict(e) for e in entries], "mode": "slideshow" if len(entries) > 1 else "single"}

    def _logo_value(self) -> dict[str, Any] | None:
        kind = self.logo_kind.currentData()
        size = self.logo_size.currentData()
        colour = self.logo_colour.currentData()
        # The direction only matters for a scheme; None leaves out an older choice.
        paint: dict[str, Any] = {"logo_colour": colour, "logo_direction": None}
        if colour in schema.LOGO_SCHEMES:
            paint["logo_direction"] = self.logo_direction.currentData()
        if kind in ("text", "ascii") and colour == "original":
            paint["logo_colour"] = "gradient"
        if kind == "distro":
            return {"logo": "distro", **paint}
        if kind == "builtin":
            return {"logo": "builtin", "logo_builtin": self.logo_builtin.currentText().strip(), **paint}
        if kind == "text":
            if not self.logo_text.text().strip():
                return None  # nothing typed yet: keep the preset's logo
            return {"logo": "text", "logo_text": self.logo_text.text(), "logo_size": size, **paint}
        if kind == "ascii":
            art = self._logo_art()
            if not art.strip():
                return None
            return {"logo": "ascii", "logo_ascii": art, **paint}
        if kind == "photo":
            if "logo" in self._files:
                return {"logo": "image", "logo_file": self._files["logo"], "logo_size": size}
            if self.preset is not None and self.preset.section("shell").get("logo_file"):
                return {"logo": "image", "logo_size": size}  # the preset's own photo, resized
            return None
        if self.preset is None or self.preset.section("shell").get("fastfetch_logo", "distro") == "distro":
            return None  # this preset has no picture of its own
        return {"logo": "image", "logo_file": None, "logo_size": size}  # the preset's picture

    def _splash_value(self) -> dict[str, Any] | None:
        style = self.splash_style.value()
        if style == "keep":
            return REMOVE if self.preset and self.preset.section("splash") else None
        if style != "plasmify":
            return {"style": style}
        value: dict[str, Any] = {
            "style": "plasmify",
            "background": self.splash_background.currentData(),
            "logo": self.splash_logo.currentData(),
            "progress": self.splash_progress.isChecked(),
        }
        text = " ".join(self.splash_text.text().split())  # pasted tabs or line breaks become spaces
        if text:  # no text is simply no line under the logo
            value["text"] = text
        elif "text" in (self.preset.section("splash") if self.preset else {}):
            value["text"] = None
        preset_splash = self.preset.section("splash") if self.preset else {}
        for choice, key, role in (("background", "background_file", "splash_background"),
                                  ("logo", "logo_file", "splash_logo")):  # fmt: skip
            if value[choice] == "image":
                name = self._files.get(role) or preset_splash.get(key)
                if name is None:
                    value[choice] = "palette" if choice == "background" else "distro"
                else:
                    value[key] = name
        return value

    def _login_value(self) -> dict[str, Any] | None:
        choice = self.login_background.value()
        if choice == "keep":
            return REMOVE if self.preset and self.preset.section("login") else None
        if choice == "image":
            name = self._files.get("login_background") or (
                self.preset.section("login").get("background_file") if self.preset else None
            )
            return {"background": "image", "background_file": name} if name else None
        return {"background": "wallpaper", "background_file": None}

    def overrides(self) -> dict[str, Any]:
        result: dict[str, dict[str, Any]] = {}
        for (section, key), getter in self._dirty.items():
            value = getter()
            if value is REMOVE:  # "Keep it": the preset's whole section is left out
                result[section] = None
                continue
            if value is None:
                if (section, key) in KEEP_CURRENT and self.preset and key in self.preset.section(section):
                    result.setdefault(section, {})[key] = None  # "As before Plasmify" beats the preset
                continue  # "preset default" choices
            if result.get(section, {}) is None:
                continue
            if key.startswith("*"):
                result.setdefault(section, {}).update(value)
            else:
                result.setdefault(section, {})[key] = value
        return self._complete(result)

    def _complete(self, result: dict[str, Any]) -> dict[str, Any]:
        """Fill in or drop partial tables for sections the preset itself does not have, so a
        lone slider (cursor size, slideshow speed, folder colour) never makes the preset invalid."""
        if self.preset is None:
            return result
        cursor = result.get("cursor")
        if cursor and "name" not in cursor and not self.preset.section("cursor"):
            # Resize the cursor the user has now.
            cursor["name"] = self._system_cursor or "breeze_cursors"
            cursor["url"] = None
        wallpaper = result.get("wallpaper")
        if wallpaper and self._wallpaper_count() < 2:
            wallpaper.pop("interval", None)  # one picture is not a slideshow: nothing to time or order
            wallpaper.pop("order", None)
            if not wallpaper:
                result.pop("wallpaper")
        if wallpaper and "images" not in wallpaper and not self.preset.section("wallpaper"):
            result.pop("wallpaper", None)  # Shimmer and the rest need pictures, and there are none
        icons = result.get("icons")
        if icons and "theme" not in icons and not self.preset.section("icons").get("theme"):
            result.pop("icons")  # folder colours need a Papirus theme to recolour
        return result

    @property
    def change_count(self) -> int:
        """How many settings differ from the preset, by the rule the unsaved bar and the command
        line count with too (see record.changed_settings)."""
        return record.change_count(self.preset, self.overrides()) if self.preset else 0
