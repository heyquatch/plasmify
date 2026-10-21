"""Creating a preset from the user's own wallpaper.

The image is analysed locally (never uploaded): it is shrunk, its dominant
colours are found with a small k-means, and a complete palette is derived from
them. Readability is enforced with WCAG contrast ratios, so even a very bright
or very busy picture produces a usable theme.
"""

from __future__ import annotations

import colorsys
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plasmify import schema
from plasmify.palette import contrast, is_dark, relative_luminance, to_hex, to_rgb

# Pictures a preset can carry (GIF included: Plasma plays animated wallpapers).
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")

# Papirus folder colours (approximate) for matching the accent.
PAPIRUS_COLOURS = {
    "blue": "#5294e2",
    "bluegrey": "#607d8b",
    "brown": "#ae8e6c",
    "cyan": "#00bcd4",
    "deeporange": "#eb6637",
    "green": "#87b158",
    "grey": "#8e8e8e",
    "indigo": "#5c6bc0",
    "magenta": "#ca71df",
    "nordic": "#81a1c1",
    "orange": "#ee923a",
    "palebrown": "#d1bfae",
    "paleorange": "#eeca8f",
    "pink": "#f06292",
    "red": "#e25252",
    "teal": "#16a085",
    "violet": "#7e57c2",
    "yellow": "#f9bd30",
}

STATUS_HUES = {"red": 355, "green": 125, "yellow": 45, "blue": 215, "magenta": 300, "cyan": 185, "orange": 25}

FONT_CHOICES = {
    "Inter + JetBrains Mono": ("Inter", "JetBrains Mono", ["inter-font", "ttf-jetbrains-mono"]),
    "Noto Sans + Hack": ("Noto Sans", "Hack", ["noto-fonts", "ttf-hack"]),
    "Roboto + Fira Code": ("Roboto", "Fira Code", ["ttf-roboto", "ttf-fira-code"]),
    "Nunito + Roboto Mono": ("Nunito", "Roboto Mono", ["ttf-nunito", "ttf-roboto-mono"]),
    "Ubuntu + Ubuntu Mono": ("Ubuntu", "Ubuntu Mono", ["ttf-ubuntu-font-family"]),
}

MOODS = {
    "calm": {"blur": True, "blur_strength": 6, "open_close": "fade", "minimize": "squash", "desktop_switch": "fade"},
    "lively": {
        "blur": True,
        "blur_strength": 12,
        "open_close": "scale",
        "minimize": "magiclamp",
        "desktop_switch": "slide",
    },
    "playful": {
        "blur": True,
        "blur_strength": 12,
        "open_close": "scale",
        "minimize": "magiclamp",
        "desktop_switch": "slide",
        "wobbly": True,
    },
}

CURSORS = {
    True: {
        "name": "Bibata-Modern-Classic",
        "url": "https://github.com/ful1e5/Bibata_Cursor/releases/download/v2.0.7/Bibata-Modern-Classic.tar.xz",
        "size": 24,
    },
    False: {
        "name": "Bibata-Modern-Ice",
        "url": "https://github.com/ful1e5/Bibata_Cursor/releases/download/v2.0.7/Bibata-Modern-Ice.tar.xz",
        "size": 24,
    },
}


# ----- colour maths --------------------------------------------------------


def hsl(h: float, s: float, light: float) -> str:
    r, g, b = colorsys.hls_to_rgb((h % 360) / 360, max(0.0, min(1.0, light)), max(0.0, min(1.0, s)))
    return to_hex((r * 255, g * 255, b * 255))


def to_hsl(hex_colour: str) -> tuple[float, float, float]:
    r, g, b = (c / 255 for c in to_rgb(hex_colour))
    h, light, s = colorsys.rgb_to_hls(r, g, b)
    return h * 360, s, light


def ensure_contrast(fg: str, bg: str, ratio: float) -> str:
    """Move ``fg``'s lightness away from ``bg`` until the WCAG contrast ``ratio`` is met."""
    h, s, light = to_hsl(fg)
    lighter = relative_luminance(bg) < 0.5
    for _ in range(60):
        if contrast(fg, bg) >= ratio:
            return fg
        light = min(1.0, light + 0.015) if lighter else max(0.0, light - 0.015)
        fg = hsl(h, s, light)
    return fg


def hue_distance(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


# ----- image analysis ------------------------------------------------------


@dataclass
class Swatch:
    colour: str
    weight: float  # share of the image, 0..1

    @property
    def hue(self) -> float:
        return to_hsl(self.colour)[0]

    @property
    def saturation(self) -> float:
        return to_hsl(self.colour)[1]

    @property
    def lightness(self) -> float:
        return to_hsl(self.colour)[2]


def load_pixels(path: Path, size: int = 96) -> list[tuple[int, int, int]]:
    """Downscaled RGB pixels of an image (Qt does the decoding, nothing is uploaded). Animated
    pictures give the colours of their first frame."""
    from PyQt6.QtCore import Qt
    from PyQt6.QtGui import QImage, QImageReader

    from plasmify.downloads import size_problem  # local import: downloads pulls in networking

    reader = QImageReader(str(path))
    header = reader.size()  # read before decoding, so an absurd size is never allocated
    problem = size_problem(header.width(), header.height()) if header.isValid() else None
    if problem:
        raise ValueError(f"{path.name} {problem}")
    image = reader.read()
    if image.isNull():
        raise ValueError(f"{path.name} could not be read as an image")
    image = image.scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    image = image.convertToFormat(QImage.Format.Format_RGB888)
    width, height, stride = image.width(), image.height(), image.bytesPerLine()
    data = bytes(image.constBits().asstring(image.sizeInBytes()))
    pixels = []
    for y in range(height):
        row = y * stride
        for x in range(width):
            i = row + x * 3
            pixels.append((data[i], data[i + 1], data[i + 2]))
    return pixels


def dominant_colours(pixels: list[tuple[int, int, int]], k: int = 8, rounds: int = 10) -> list[Swatch]:
    """A small k-means over RGB, seeded deterministically by brightness."""
    if not pixels:
        return []
    ordered = sorted(pixels, key=lambda p: p[0] * 0.3 + p[1] * 0.59 + p[2] * 0.11)
    centres = [ordered[int((i + 0.5) * len(ordered) / k)] for i in range(k)]
    groups: list[list[tuple[int, int, int]]] = [[] for _ in centres]
    for _ in range(rounds):
        groups = [[] for _ in centres]
        for p in pixels:
            best = min(range(len(centres)), key=lambda c: sum((p[i] - centres[c][i]) ** 2 for i in range(3)))
            groups[best].append(p)
        centres = [
            tuple(sum(p[i] for p in group) / len(group) for i in range(3)) if group else centres[n]
            for n, group in enumerate(groups)
        ]
    total = len(pixels)
    swatches = [Swatch(to_hex(c), len(g) / total) for c, g in zip(centres, groups, strict=True) if g]
    return sorted(swatches, key=lambda s: s.weight, reverse=True)


def analyse(paths: list[Path]) -> list[Swatch]:
    pixels: list[tuple[int, int, int]] = []
    for path in paths[:5]:
        pixels += load_pixels(path, 96 if len(paths) == 1 else 64)
    return dominant_colours(pixels)


# ----- palette building ----------------------------------------------------


def background_hue(swatches: list[Swatch], dark: bool) -> tuple[float, float]:
    """Hue and saturation for backgrounds, from the darkest (or lightest) part of the image."""
    pool = [s for s in swatches if (s.lightness < 0.5) == dark] or swatches
    weight = sum(s.weight for s in pool) or 1.0
    x = sum(math.cos(math.radians(s.hue)) * s.weight * s.saturation for s in pool)
    y = sum(math.sin(math.radians(s.hue)) * s.weight * s.saturation for s in pool)
    hue = math.degrees(math.atan2(y, x)) % 360
    saturation = sum(s.saturation * s.weight for s in pool) / weight
    return hue, min(saturation, 0.45)


def accent_candidates(swatches: list[Swatch], dark: bool, count: int = 5) -> list[str]:
    """Distinct, vivid colours from the image, adjusted to work as an accent."""
    target_light = 0.72 if dark else 0.42
    vivid = sorted(
        (s for s in swatches if s.saturation >= 0.18),
        key=lambda s: s.saturation * (0.3 + s.weight),
        reverse=True,
    )
    picked: list[float] = []
    result: list[str] = []
    for swatch in vivid:
        if all(hue_distance(swatch.hue, h) >= 28 for h in picked):
            picked.append(swatch.hue)
            result.append(hsl(swatch.hue, max(0.5, min(swatch.saturation, 0.85)), target_light))
        if len(result) == count:
            break
    # Too few vivid colours (a muted or greyscale picture): add harmonious hues
    # around the image's dominant colour, then classic accents.
    base_hue = picked[0] if picked else (swatches[0].hue if swatches else 265)
    for offset in (30, -30, 180, 60, -60, 120, -120):
        if len(result) >= count:
            break
        hue = (base_hue + offset) % 360
        if all(hue_distance(hue, h) >= 28 for h in picked):
            picked.append(hue)
            result.append(hsl(hue, 0.55, target_light))
    return result[:count]


def neutrals(hue: float, bg_sat: float, dark: bool) -> dict[str, str]:
    """Backgrounds and text for one hue: dark (crust darkest) or light (crust lightest)."""
    if dark:
        return {
            "base": hsl(hue, bg_sat, 0.11),
            "mantle": hsl(hue, bg_sat, 0.085),
            "crust": hsl(hue, bg_sat, 0.06),
            "surface": hsl(hue, bg_sat * 0.8, 0.19),
            "overlay": hsl(hue, bg_sat * 0.5, 0.45),
            "text": hsl(hue, min(bg_sat, 0.3), 0.89),
            "subtext": hsl(hue, min(bg_sat, 0.25), 0.72),
        }
    return {
        "base": hsl(hue, bg_sat * 0.7, 0.95),
        "mantle": hsl(hue, bg_sat * 0.7, 0.91),
        "crust": hsl(hue, bg_sat * 0.5, 0.985),
        "surface": hsl(hue, bg_sat * 0.6, 0.87),
        "overlay": hsl(hue, bg_sat * 0.4, 0.55),
        "text": hsl(hue, min(bg_sat, 0.3), 0.17),
        "subtext": hsl(hue, min(bg_sat, 0.25), 0.36),
    }


def _readable(palette: dict[str, str], dark: bool) -> dict[str, str]:
    base = palette["base"]
    palette["text"] = ensure_contrast(palette["text"], base, 7.0)
    palette["subtext"] = ensure_contrast(palette["subtext"], base, 4.5)
    for name in ("accent", *STATUS_HUES):
        palette[name] = ensure_contrast(palette[name], base, 3.0)
    if dark:  # crust is the text on selections: it may only get darker, never light
        palette["crust"] = _darken_until(palette["crust"], palette["accent"], 4.5)
        if max(contrast(palette["crust"], palette["accent"]), contrast(palette["text"], palette["accent"])) < 4.5:
            palette["accent"] = _lighten_until(palette["accent"], palette["crust"], 4.5)  # same hue, lighter
    return palette


def _lighten_until(fg: str, bg: str, ratio: float) -> str:
    h, s, light = to_hsl(fg)
    while contrast(fg, bg) < ratio and light < 1:
        light = min(1.0, light + 0.015)
        fg = hsl(h, s, light)
    return fg


def _darken_until(fg: str, bg: str, ratio: float) -> str:
    h, s, light = to_hsl(fg)
    while contrast(fg, bg) < ratio and light > 0 and relative_luminance(fg) < relative_luminance(bg):
        light = max(0.0, light - 0.015)
        fg = hsl(h, s, light)
    return fg


def build_palette(swatches: list[Swatch], accent: str, dark: bool = True, vibrancy: float = 1.0) -> dict[str, str]:
    hue, sat = background_hue(swatches, dark)
    bg_sat = min(sat * 0.9 * vibrancy, 0.4)
    palette = neutrals(hue, bg_sat, dark)
    status_light = 0.72 if dark else 0.42
    _, accent_sat, _ = to_hsl(accent)
    status_sat = max(0.45, min(accent_sat, 0.8)) * min(vibrancy, 1.2)
    for name, status_hue in STATUS_HUES.items():
        palette[name] = hsl(status_hue, status_sat, status_light)
    palette["accent"] = accent
    return _readable(palette, dark)


def mode_variant(colours: dict[str, str], dark: bool) -> dict[str, str]:
    """The same palette for the other mode: backgrounds and text rebuilt from the base hue,
    accent and terminal colours keep their hue and only change lightness as far as needed
    to stay readable. Explicit bright_* colours are dropped (None) and derived again."""
    hue, sat, _ = to_hsl(colours["base"])
    if not is_dark(colours["base"]):
        sat /= 0.7  # light bases carry 70 % of the background saturation (see neutrals)
    palette: dict[str, Any] = neutrals(hue, min(sat, 0.4), dark)
    for name in ("accent", *STATUS_HUES):
        palette[name] = colours[name]
    palette = _readable(palette, dark)
    for name in colours:
        if name.startswith("bright_"):
            palette[name] = None
    return palette


def nearest_folder_colour(accent: str) -> str:
    """The Papirus folder colour that looks closest to ``accent``.

    Hue matters most to the eye (a pastel pink must stay pink, not turn brown), so it is
    weighted far above saturation and lightness; nearly grey accents get grey folders.
    """
    hue, sat, light = to_hsl(accent)
    if sat < 0.15:
        return "grey"

    def distance(name: str) -> float:
        h, s, lt = to_hsl(PAPIRUS_COLOURS[name])
        if s < 0.15:
            return 99.0
        return 3 * hue_distance(hue, h) / 180 + abs(sat - s) + 0.5 * abs(light - lt)

    return min(PAPIRUS_COLOURS, key=distance)


def wallpaper_file_name(index: int, path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._+-]", "_", path.stem).strip("._") or "wallpaper"
    suffix = path.suffix.lower() if path.suffix.lower() in IMAGE_SUFFIXES else ".png"
    return f"{index:02d}-{stem[:60]}{suffix}"


def build_preset(
    *,
    name: str,
    description: str,
    images: list[Path],
    palette: dict[str, str],
    dark: bool,
    layout: str,
    mood: str,
    fonts: str,
    preset_id: str,
    panels: list[dict[str, Any]] | None = None,
    panel_style: dict[str, Any] | None = None,
    splash: bool = False,
) -> tuple[dict[str, Any], dict[str, Path]]:
    """A complete, validated preset plus the wallpaper files it ships with.

    ``panels`` (long form, see ``plasmify.panels``) replaces the named ``layout``;
    ``splash`` adds a Plasmify splash screen on the first wallpaper."""
    ui, mono, font_packages = FONT_CHOICES[fonts]
    files = {wallpaper_file_name(i, p): p for i, p in enumerate(images[:30], start=1)}
    data: dict[str, Any] = {
        "schema": schema.SCHEMA_VERSION,
        "meta": {"id": preset_id, "name": name, "description": description, "tags": ["custom", "wallpaper"]},
        "palette": palette,
        "icons": {
            "theme": "Papirus-Dark" if dark else "Papirus",
            "folder_color": nearest_folder_colour(palette["accent"]),
        },
        "cursor": dict(CURSORS[dark]),
        "fonts": {"ui": ui, "ui_size": 10, "mono": mono, "mono_size": 10, "packages": font_packages},
        "layout": {"name": layout},
        "wallpaper": {
            "mode": "slideshow" if len(files) > 1 else "single",
            "interval": 1800,
            "images": [{"file": f, "credit": "My own wallpaper"} for f in files],
        },
        "effects": dict(MOODS[mood]),
        "terminal": {"opacity": 0.9 if dark else 0.95, "blur": True, "padding": 12, "font_size": 12},
        "shell": {"prompt": "powerline", "zoxide": True, "fastfetch_logo": "distro"},
    }
    if panels:
        from plasmify.panels import compact  # local import: panels imports schema, which imports us lazily

        data["layout"] = {"panels": compact(panels)}
    if panel_style:
        data["panel_style"] = dict(panel_style)
    if splash:
        data["splash"] = {"style": "plasmify", "background": "wallpaper", "logo": "distro", "progress": True}
    errors = schema.validate(data)
    if errors:
        raise ValueError("; ".join(errors))
    return data, files


def current_wallpaper() -> Path | None:
    """The image on the desktop right now (first desktop), if it is a plain file or package."""
    from plasmify.system import CONFIG_HOME  # local import keeps this module free of system state

    config = CONFIG_HOME / "plasma-org.kde.plasma.desktop-appletsrc"
    try:
        lines = config.read_text(errors="replace").splitlines()
    except OSError:
        return None
    for line in lines:
        if not line.startswith("Image="):
            continue
        value = line.split("=", 1)[1].strip().removeprefix("file://")
        path = Path(value)
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
            return path
        images = path / "contents" / "images"
        if images.is_dir():  # a wallpaper package: take its largest picture
            candidates = [p for p in images.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES]
            if candidates:
                return max(candidates, key=lambda p: p.stat().st_size)
    return None
