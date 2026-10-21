"""Turning the current desktop into a preset ("Save my desktop as a preset").

Everything is read from the user's KDE config files; nothing is uploaded. Parts
that cannot be reproduced on another computer (a cursor or icon theme Plasmify
does not know how to download, a hand-made panel layout) are left out and
explained in the returned notes, so the result is always a valid, shareable preset.
"""

from __future__ import annotations

import math
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from plasmify import presets, schema
from plasmify.glass import GENERATED_NAME, MAX_THEME_BYTES, get_key, look_of_kvconfig, made_by_plasmify
from plasmify.palette import darken, is_dark, mix, to_hex, to_rgb
from plasmify.panels import EDGES, MAX_WIDGETS, THICKNESS, WIDGETS, compact, normalise
from plasmify.steps import panelstyle
from plasmify.steps.colors import scheme_name
from plasmify.steps.effects import DECORATION, TITLEBAR_BUTTONS, WINDOW_BORDERS
from plasmify.steps.layout import current_panels
from plasmify.steps.terminal import KONSOLE_PROFILE
from plasmify.steps.wallpaper import LIVING, cached_file, url_base, wallpaper_dir
from plasmify.system import CONFIG_HOME, DATA_HOME, CommandError, Runner, which

# Font families Plasmify knows how to install, by canonical package.
FONT_PACKAGES = {
    "Inter": "inter-font",
    "JetBrains Mono": "ttf-jetbrains-mono",
    "Fira Code": "ttf-fira-code",
    "Hack": "ttf-hack",
    "Noto Sans": "noto-fonts",
    "Roboto": "ttf-roboto",
    "Roboto Mono": "ttf-roboto-mono",
    "Nunito": "ttf-nunito",
    "Ubuntu": "ttf-ubuntu-font-family",
    "Ubuntu Mono": "ttf-ubuntu-font-family",
}
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
MAX_WALLPAPER_BYTES = 50 * 1024 * 1024

# Plasma panel locations in plasma-org.kde.plasma.desktop-appletsrc.
TOP, BOTTOM = "3", "4"


@dataclass
class Capture:
    data: dict[str, Any]
    files: dict[str, Path] = field(default_factory=dict)  # wallpaper name in preset -> source file
    notes: list[str] = field(default_factory=list)


def slugify(name: str) -> str:
    """A preset id from a display name: "Café Crème / Lofi" -> "cafe-creme-lofi"."""
    folded = unicodedata.normalize("NFKD", name.replace("ı", "i").replace("İ", "I").replace("ß", "ss"))
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return slug[:48].rstrip("-") or "my-desktop"


def _rgb(value: str, fallback: str) -> str:
    try:
        parts = [int(p) for p in value.split(",")[:3]]
        return to_hex((parts[0], parts[1], parts[2]))
    except (ValueError, IndexError):
        return fallback


def _luminance(hex_colour: str) -> float:
    r, g, b = (c / 255 for c in to_rgb(hex_colour))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def read_palette(run: Runner) -> dict[str, str]:
    def colour(group: str, key: str, fallback: str) -> str:
        return _rgb(run.kread("kdeglobals", f"Colors:{group}", key), fallback)

    base = colour("Window", "BackgroundNormal", "#202326")
    mantle = colour("View", "BackgroundNormal", darken(base, 0.15))
    text = colour("Window", "ForegroundNormal", "#fcfcfc")
    subtext = colour("Window", "ForegroundInactive", mix(text, base, 0.3))
    blue = colour("Window", "ForegroundLink", "#1d99f3")
    green = colour("Window", "ForegroundPositive", "#27ae60")
    accent = colour("Selection", "BackgroundNormal", blue)
    palette = {
        "base": base,
        "mantle": mantle,
        "crust": darken(mantle, 0.25) if _luminance(base) < 0.5 else darken(mantle, 0.08),
        "surface": colour("Button", "BackgroundNormal", mix(base, text, 0.12)),
        "overlay": mix(subtext, base, 0.45),
        "text": text,
        "subtext": subtext,
        "accent": accent,
        "red": colour("Window", "ForegroundNegative", "#da4453"),
        "green": green,
        "yellow": colour("Window", "ForegroundNeutral", "#f67400"),
        "blue": blue,
        "magenta": colour("Window", "ForegroundVisited", "#9b59b6"),
        "cyan": mix(blue, green, 0.5),
        "orange": colour("Window", "ForegroundActive", "#f67400"),
    }
    return palette


def _font(run: Runner, key: str) -> tuple[str, int] | None:
    raw = run.kread("kdeglobals", "General", key)
    if not raw:
        return None
    family, _, rest = raw.partition(",")
    try:
        size = int(float(rest.split(",")[0]))
    except (ValueError, IndexError):
        size = 10
    return family.strip(), size


def read_fonts(run: Runner, notes: list[str]) -> dict[str, Any] | None:
    ui, mono = _font(run, "font"), _font(run, "fixed")
    if not ui or not mono:
        return None
    fonts = {"ui": ui[0], "ui_size": min(max(ui[1], 6), 24), "mono": mono[0], "mono_size": min(max(mono[1], 6), 32)}
    packages = []
    for family in (ui[0], mono[0]):
        package = FONT_PACKAGES.get(family)
        if package:
            packages.append(package)
        else:
            notes.append(f"Font '{family}' is kept, but others need to have it installed themselves.")
    if packages:
        fonts["packages"] = list(dict.fromkeys(packages))
    return fonts


def _known_section(builtins: list[presets.Preset], section: str, key: str, value: str) -> dict[str, Any] | None:
    for preset in builtins:
        data = preset.section(section)
        if data.get(key) == value:
            return data
    return None


def read_appletsrc(path: Path) -> dict[str, dict[str, str]]:
    """Parse plasma-org.kde.plasma.desktop-appletsrc into {group: {key: value}}."""
    groups: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    if not path.exists():
        return groups
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("["):
            current = groups.setdefault(line.strip(), {})
        elif current is not None and "=" in line:
            key, _, value = line.partition("=")
            current[key.strip()] = value.strip()
    return groups


HIDING_BACK = {"dodgewindows": "dodge", "autohide": "autohide"}
ALIGN_BACK = {"left": "start", "center": "center", "right": "end"}


def panels_from_plasma(found: list[dict], notes: list[str]) -> dict[str, Any] | None:
    """The long form of the panels plasmashell reports (see ``steps.layout.current_panels``).
    Several fitted panels on one edge come back as islands."""
    plugins = {plugin: widget for widget, (plugin, *_rest) in WIDGETS.items()}
    skipped: set[str] = set()
    by_edge: dict[str, list[tuple[dict, list[str]]]] = {}
    for panel in found:
        edge = panel.get("location")
        if edge not in EDGES:
            continue
        widgets = []
        for plugin in panel.get("widgets", []):
            if plugin in plugins:
                widgets.append(plugins[plugin])
            else:
                skipped.add(plugin)
        if widgets:
            by_edge.setdefault(edge, []).append((panel, widgets))
    result = []
    for edge in by_edge:  # in the order plasmashell reported them
        group = by_edge[edge]
        first = group[0][0]
        entry: dict[str, Any] = {
            "edge": edge,
            "thickness": min(max(int(max(p.get("height") or 36 for p, _ in group)), THICKNESS[0]), THICKNESS[1]),
            "floating": bool(first.get("floating", True)),
            "hiding": HIDING_BACK.get(first.get("hiding"), "always"),
        }
        fitted = [(p, w) for p, w in group if p.get("lengthMode") == "fit"]
        if len(group) > 1 and len(fitted) == len(group):
            entry["style"] = "islands"
            entry["widgets"] = _islands(fitted)[:MAX_WIDGETS]
        else:
            if len(group) > 1:
                notes.append(f"Only the first of the panels at the {edge} edge was kept.")
            panel, widgets = group[0]
            entry["widgets"] = widgets[:MAX_WIDGETS]
            if panel.get("lengthMode") == "fit":
                entry["length"] = "fit"
                entry["align"] = ALIGN_BACK.get(panel.get("alignment"), "center")
        result.append(entry)
    if skipped:
        notes.append("Widgets Plasmify does not know were left out: " + ", ".join(sorted(skipped)))
    return {"panels": compact([normalise(p) for p in result])} if result else None


def _islands(fitted: list[tuple[dict, list[str]]]) -> list[str]:
    """The widget list of an islands panel: the pieces at the start, centre and end of the edge,
    with a flexible space between them (spaces inside a piece are dropped)."""
    places: dict[str, list[str]] = {"start": [], "center": [], "end": []}
    for panel, widgets in fitted:
        place = ALIGN_BACK.get(panel.get("alignment"), "center")
        places[place] += [w for w in widgets if w != "spacer"]
    filled = [name for name, widgets in places.items() if widgets]
    if filled == ["start", "end"]:
        return places["start"] + ["spacer"] + places["end"]  # two pieces: start and end
    return places["start"] + ["spacer"] + places["center"] + ["spacer"] + places["end"]


def read_layout(groups: dict[str, dict[str, str]], notes: list[str]) -> dict[str, Any] | None:
    panels = []
    for name, values in groups.items():
        match = re.fullmatch(r"\[Containments\]\[(\d+)\]", name)
        if match and values.get("plugin") == "org.kde.panel":
            applets = {
                v.get("plugin")
                for g, v in groups.items()
                if g.startswith(f"[Containments][{match.group(1)}][Applets]") and "plugin" in v
            }
            panels.append((values.get("location"), applets))
    locations = sorted(loc or "" for loc, _ in panels)
    if locations == [TOP, BOTTOM] or locations == [BOTTOM, TOP]:
        return {"name": "topbar-dock"}
    if locations == [BOTTOM]:
        return {"name": "bottom-panel"}
    if locations == [TOP]:
        return {"name": "top-panel"}
    notes.append("Your panel layout is custom, so it is not included; others keep their own panels.")
    return None


def _still_behind(path: Path, folders: set[Path]) -> Path | None:
    """The still picture behind a Shimmer copy in the living/ folder of one of Plasmify's
    wallpaper ``folders``: rendered from it (``NN-name-<key>.webp``) or the still placed there as
    it is. None when the still is gone, so a copy of unknown origin is never bundled. Any other
    path is returned as it is."""
    folder = path.parent.parent
    if path.parent.name != LIVING or folder not in folders:
        return path
    if (folder / path.name).is_file():
        return folder / path.name
    rendered = re.fullmatch(r"(.+)-[0-9a-f]{10}\.webp", path.name)
    if rendered is None:
        return None
    found = sorted(p for p in folder.iterdir() if p.stem == rendered[1] and p.suffix.lower() in IMAGE_SUFFIXES)
    return found[0] if found else None


def read_wallpapers(
    groups: dict[str, dict[str, str]], notes: list[str]
) -> tuple[dict[str, Any] | None, dict[str, Path]]:
    images: list[Path] = []
    mode = "single"
    interval = 1800
    for name, values in groups.items():
        if not re.fullmatch(r"\[Containments\]\[\d+\]", name) or values.get("plugin") != "org.kde.plasma.folder":
            continue
        plugin = values.get("wallpaperplugin", "org.kde.image")
        general = groups.get(f"{name}[Wallpaper][{plugin}][General]", {})
        if plugin == "org.kde.slideshow":
            mode = "slideshow"
            interval = int(general.get("SlideInterval", "1800") or 1800)
            for folder in general.get("SlidePaths", "").split(","):
                folder_path = Path(unquote(urlparse(folder).path) if folder.startswith("file:") else folder)
                if folder_path.is_dir():
                    images += sorted(p for p in folder_path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
        elif plugin == "org.kde.image" and general.get("Image"):
            image = general["Image"]
            images.append(Path(unquote(urlparse(image).path) if image.startswith("file:") else image))
        break

    # Wallpapers Plasmify downloaded for a preset are referenced by their original URL and
    # credit instead of being copied, so other artists' work is never redistributed. They are
    # found wherever they sit in the slideshow (Customize can reorder and remove pictures).
    known: dict[Path, dict[str, str]] = {}
    folders: set[Path] = set()
    for preset in presets.load_all():
        folder = wallpaper_dir(preset)
        if not folder.is_dir():
            continue
        folders.add(folder)
        for image in preset.section("wallpaper").get("images", []):
            if "url" not in image:
                continue
            for path in folder.glob(f"[0-9][0-9]-{url_base(image['url'])}"):
                known[path] = image
            older = cached_file(preset, image)  # also a download named before names had the hash
            if older is not None:
                known[older] = image
    # With Shimmer the desktop shows the copies in living/; each stands for the still it was made from.
    stills = (_still_behind(p, folders) for p in images)
    images = [
        p
        for p in dict.fromkeys(p for p in stills if p is not None)
        if p.suffix.lower() in IMAGE_SUFFIXES and p.is_file() and p.stat().st_size <= MAX_WALLPAPER_BYTES
    ][:30]
    if not images:
        notes.append("No wallpaper image file was found, so wallpapers are not included.")
        return None, {}

    entries: list[dict[str, str]] = []
    files: dict[str, Path] = {}
    for index, image in enumerate(images, start=1):
        original = known.get(image)
        if original:
            entries.append({k: v for k, v in original.items() if k in ("url", "credit", "license")})
            continue
        stem = re.sub(r"^\d+-", "", image.stem)
        stem = re.sub(r"[^A-Za-z0-9_+-]", "_", stem).strip("_") or "wallpaper"  # no dots: no ".."
        name = f"{index:02d}-{stem[:60]}{image.suffix.lower()}"
        files[name] = image
        entries.append({"file": name, "credit": "Shared with this preset"})
    if files:
        notes.append(
            f"{len(files)} of your own wallpaper(s) will be included in the bundle. "
            "Only share images you are allowed to share."
        )
    section = {
        "mode": mode if len(entries) > 1 else "single",
        "interval": min(max(interval, 60), 86400),
        "images": entries,
    }
    return section, files


# KWin's own defaults for settings its effects read while they are on but kwinrc does not set them.
KWIN_MOVING_OPACITY = 80
KWIN_DIM_STRENGTH = 25


def read_effects(run: Runner) -> dict[str, Any]:
    """Every setting the effects step writes, as the desktop has it, so applying the saved preset
    changes nothing: a setting left out would get the step's default (fall apart, the cube and
    dimming off, for example) or the size from before Plasmify. Title bar buttons and the border
    size are left out only when they are not one of the choices a preset has."""

    def enabled(plugin: str, default: bool) -> bool:
        value = run.kread("kwinrc", "Plugins", f"{plugin}Enabled")
        return default if value == "" else value == "true"

    def whole(group: str, key: str, low: int, high: int, default: int | None = None) -> int | None:
        value = run.kread("kwinrc", group, key)
        if not re.fullmatch(r"[0-9]{1,6}", value):
            return default
        return min(max(int(value), low), high)

    effects: dict[str, Any] = {"blur": enabled("blur", True)}
    for key, setting, low, high in (
        ("blur_strength", "BlurStrength", 1, 15),
        ("blur_noise", "NoiseStrength", 0, 14),
        ("blur_saturation", "Saturation", 100, 500),
    ):
        value = whole("Effect-blur", setting, low, high)
        if value is not None:
            effects[key] = value
    effects["open_close"] = next(
        (name for name, default in (("scale", True), ("glide", False), ("fade", False)) if enabled(name, default)),
        "none",
    )
    effects["minimize"] = next(
        (name for name, default in (("magiclamp", False), ("squash", True)) if enabled(name, default)), "none"
    )
    effects["desktop_switch"] = (
        "slide" if enabled("slide", True) else "fade" if enabled("fadedesktop", False) else "none"
    )
    effects["wobbly"] = enabled("wobblywindows", False)
    # KWin reads the level only while Settings is Auto (its default); Custom has raw values.
    if effects["wobbly"] and run.kread("kwinrc", "Effect-wobblywindows", "Settings", "Auto") == "Auto":
        level = whole("Effect-wobblywindows", "WobblynessLevel", 0, 4)
        if level is not None:
            effects["wobbly_level"] = level
    effects["fall_apart"] = enabled("fallapart", False)
    effects["sheet"] = enabled("sheet", False)
    effects["cube"] = enabled("cube", False)
    moving = whole("Effect-translucency", "MoveResize", 30, 100, KWIN_MOVING_OPACITY)
    effects["moving_opacity"] = moving / 100 if enabled("translucency", False) else 1.0
    dim = whole("Effect-diminactive", "Strength", 10, 90, KWIN_DIM_STRENGTH)
    effects["dim_inactive"] = dim if enabled("diminactive", False) else 0
    if run.kread("kwinrc", "Plugins", "poloniumEnabled"):
        effects["tiling"] = enabled("polonium", False)
    speed = run.kread("kdeglobals", "KDE", "AnimationDurationFactor")
    try:
        if speed:
            effects["animation_speed"] = min(max(float(speed), 0.0), 4.0)
    except ValueError:
        pass
    # KWin's defaults when kwinrc does not set them: its own button layout and automatic borders.
    buttons = (
        run.kread("kwinrc", DECORATION, "ButtonsOnLeft", "MS"),
        run.kread("kwinrc", DECORATION, "ButtonsOnRight", "HIAX"),
    )
    layout = next((name for name, pair in TITLEBAR_BUTTONS.items() if pair == buttons), None)
    if layout:
        effects["titlebar_buttons"] = layout
    if run.kread("kwinrc", DECORATION, "BorderSizeAuto", "true") == "false":
        size = run.kread("kwinrc", DECORATION, "BorderSize", "Normal")
        borders = next((name for name, value in WINDOW_BORDERS.items() if value == size), None)
        if borders:
            effects["window_borders"] = borders
    return effects


def read_panel_style(run: Runner, notes: list[str]) -> dict[str, Any] | None:
    """The panel style in use when it is one Plasmify made: the values it was made with, else the
    preset it was made for. A Plasma style of the user's own is not part of a preset (others keep
    theirs), so it is left out."""
    name = run.kread(*panelstyle.THEME_KEY)
    if not panelstyle.generated(name):
        return None
    style = panelstyle.style_of(name)
    if style is None:
        made_for = next((p for p in presets.load_all() if panelstyle.theme_id(p) == name), None)
        style = dict(made_for.section("panel_style")) if made_for else None
    if not style:
        notes.append("Your panel style is not included; others keep their own.")
        return None
    return style


def read_glass(kvconfig: Path, run: Runner, palette: dict[str, str]) -> dict[str, Any]:
    """The style keys of one of Plasmify's glass themes: the look, and the transparency and
    whether the window in use stays solid when they are not the look's defaults for this palette
    (see ``glass.look_of_kvconfig``). KWin's blur strength tells frosted from glass."""
    text = ""
    try:
        if kvconfig.is_file() and not kvconfig.is_symlink() and kvconfig.stat().st_size <= MAX_THEME_BYTES:
            text = kvconfig.read_text(errors="replace")
    except OSError:
        pass
    strength = run.kread("kwinrc", "Effect-blur", "BlurStrength").strip()
    known = int(strength) if re.fullmatch(r"[0-9]{1,3}", strength) else None
    return look_of_kvconfig(text, is_dark(palette["base"]), known)


def read_terminal() -> dict[str, Any] | None:
    """Terminal translucency, padding and font size: from Alacritty's config, else from the Konsole
    profile Plasmify writes (when Konsole uses it)."""
    return _alacritty_terminal() or _konsole_terminal()


def _alacritty_terminal() -> dict[str, Any] | None:
    path = CONFIG_HOME / "alacritty" / "alacritty.toml"
    if not path.exists():
        return None
    try:
        config = tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return None
    window, font = config.get("window", {}), config.get("font", {})
    terminal: dict[str, Any] = {}
    if isinstance(window.get("opacity"), int | float):
        terminal["opacity"] = min(max(float(window["opacity"]), 0.3), 1.0)
    if isinstance(window.get("blur"), bool):
        terminal["blur"] = window["blur"]
    padding = window.get("padding", {})
    if isinstance(padding, dict) and isinstance(padding.get("x"), int):
        terminal["padding"] = min(max(padding["x"], 0), 64)
    if isinstance(font.get("size"), int | float):
        terminal["font_size"] = min(max(float(font["size"]), 6), 32)
    return terminal or None


def _konsole_terminal() -> dict[str, Any] | None:
    """The same values from Plasmify's Konsole profile and colours (steps.terminal), when
    Konsole's default profile is that one."""
    konsolerc = _read_small(CONFIG_HOME / "konsolerc")
    if get_key(konsolerc, "Desktop Entry", "DefaultProfile") != KONSOLE_PROFILE:
        return None
    profile = _read_small(DATA_HOME / "konsole" / KONSOLE_PROFILE)
    colours = _read_small(DATA_HOME / "konsole" / "Plasmify.colorscheme")
    terminal: dict[str, Any] = {}
    opacity = _number(get_key(colours, "General", "Opacity"))
    if opacity is not None:
        terminal["opacity"] = min(max(opacity, 0.3), 1.0)
    blur = get_key(colours, "General", "Blur")
    if blur in ("true", "false"):
        terminal["blur"] = blur == "true"
    margin = _number(get_key(profile, "General", "TerminalMargin"))
    if margin is not None:
        terminal["padding"] = min(max(int(margin), 0), 64)
    font = (get_key(profile, "Appearance", "Font") or "").split(",")
    size = _number(font[1]) if len(font) > 1 else None
    if size is not None:
        terminal["font_size"] = min(max(size, 6), 32)
    return terminal or None


def _read_small(path: Path) -> str:
    try:
        if path.is_file() and not path.is_symlink() and path.stat().st_size <= MAX_THEME_BYTES:
            return path.read_text(errors="replace")
    except OSError:
        pass
    return ""


def _number(raw: str | None) -> float | None:
    try:
        value = float(raw) if raw is not None else None
    except ValueError:
        return None
    return value if value is not None and math.isfinite(value) else None


def read_prompt() -> str | None:
    """The Starship prompt Plasmify wrote (minimal or powerline, told apart by the template's own
    first lines), "powerline" for a config of the user's own (it is not shared), None without one."""
    path = CONFIG_HOME / "starship.toml"
    if not path.exists():
        return None
    head = "\n".join(_read_small(path).splitlines()[:4])
    if "Generated by Plasmify" in head and "Powerline" not in head:
        return "minimal"
    return "powerline"


def _same_colours(live: dict[str, str], preset: dict[str, str]) -> bool:
    def close(a: str, b: str) -> bool:
        return all(abs(x - y) <= 2 for x, y in zip(to_rgb(a), to_rgb(b), strict=True))

    return all(close(live[key], preset[key]) for key in ("base", "text", "accent") if key in live and key in preset)


def capture(name: str, description: str, run: Runner | None = None, include_wallpapers: bool = True) -> Capture:
    """Build a preset from the running desktop."""
    run = run or Runner()
    try:
        run.check_config_reader()
    except CommandError as exc:
        # Every value would read as empty and the "saved desktop" would be plain Breeze.
        raise presets.PresetError(f"Plasmify cannot read your desktop's settings ({exc}).") from exc
    notes: list[str] = []
    builtins = presets.load_all(include_user=False)
    data: dict[str, Any] = {
        "schema": schema.SCHEMA_VERSION,
        "meta": {
            "id": slugify(name),
            "name": name.strip()[:60],
            "description": (description.strip() or "Saved from my desktop with Plasmify.")[:300],
        },
        "palette": read_palette(run),
    }

    active = run.kread("kdeglobals", "General", "ColorScheme")
    from_preset = next((p for p in builtins if scheme_name(p) == active), None)
    if from_preset and _same_colours(data["palette"], from_preset.palette.colors):
        # The desktop uses a built-in preset's colours: keep its exact palette. Not after a
        # light/dark switch or an accent tweak, which keep the scheme's name: then what is on
        # screen wins.
        data["palette"] = dict(from_preset.data["palette"])

    if run.kread("kdeglobals", "KDE", "widgetStyle").lower() == "kvantum":
        kvconfig = CONFIG_HOME / "Kvantum" / "kvantum.kvconfig"
        theme = ""
        if kvconfig.exists():
            theme = next(
                (
                    line.split("=", 1)[1].strip()
                    for line in kvconfig.read_text().splitlines()
                    if line.startswith("theme=")
                ),
                "",
            )
        glass: dict[str, Any] = {}
        glass_name = theme == GENERATED_NAME or theme.endswith("-glass")
        if glass_name and made_by_plasmify(CONFIG_HOME / "Kvantum" / theme):
            # Plasmify's glass copies: <name>-glass of a preset theme, or plasmify-glass made from
            # the palette (a theme of the user's own with such a name has no marker).
            glass = read_glass(CONFIG_HOME / "Kvantum" / theme / f"{theme}.kvconfig", run, data["palette"])
            theme = "" if theme == GENERATED_NAME else theme.removesuffix("-glass")
        known = next(
            (
                p.section("style")["kvantum"]
                for p in builtins
                if theme and p.section("style").get("kvantum", {}).get("name") == theme
            ),
            None,
        )
        if known or glass:
            data["style"] = {**({"kvantum": known} if known else {}), **glass}
        if not known and theme:
            notes.append(
                f"Kvantum theme '{theme}' can't be downloaded automatically; others get Breeze with your colours."
            )

    icon_theme = run.kread("kdeglobals", "Icons", "Theme")
    overlay_of = next((p for p in builtins if f"Plasmify-{p.id}" == icon_theme), None)
    if overlay_of:
        data["icons"] = overlay_of.section("icons")
    elif icon_theme.startswith(("Papirus", "breeze")):
        data["icons"] = {"theme": icon_theme}
    elif icon_theme:
        notes.append(f"Icon theme '{icon_theme}' is not included; others keep their icons.")

    cursor_name = run.kread("kcminputrc", "Mouse", "cursorTheme")
    cursor = _known_section(builtins, "cursor", "name", cursor_name) if cursor_name else None
    if cursor:
        data["cursor"] = cursor
    elif cursor_name:
        notes.append(f"Cursor '{cursor_name}' can't be downloaded automatically, so it is not included.")

    fonts = read_fonts(run, notes)
    if fonts and not schema.validate({**data, "fonts": fonts}):
        data["fonts"] = fonts
    elif fonts:
        notes.append("Your fonts use characters presets don't allow, so they are not included.")

    groups = read_appletsrc(CONFIG_HOME / "plasma-org.kde.plasma.desktop-appletsrc")
    # Ask the running shell first: it knows every panel exactly (edges, sizes, widget order).
    layout = panels_from_plasma(current_panels(run), notes) or read_layout(groups, notes)
    if layout:
        data["layout"] = layout
    files: dict[str, Path] = {}
    if include_wallpapers:
        wallpaper, files = read_wallpapers(groups, notes)
        if wallpaper:
            data["wallpaper"] = wallpaper
    data["effects"] = read_effects(run)
    panel_style = read_panel_style(run, notes)
    if panel_style and not schema.validate({**data, "panel_style": panel_style}):
        data["panel_style"] = panel_style
    elif panel_style:
        notes.append("Your panel style has values presets don't allow, so it is not included.")
    terminal = read_terminal()
    if terminal:
        data["terminal"] = terminal
    prompt = read_prompt()
    if prompt:
        data["shell"] = {"prompt": prompt, "zoxide": bool(which("zoxide")), "fastfetch_logo": "distro"}

    errors = schema.validate(data)
    if errors:
        raise presets.PresetError("Could not capture this desktop: " + "; ".join(errors))
    return Capture(data, files, notes)
