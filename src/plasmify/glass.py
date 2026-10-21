"""Glass: see-through Qt app windows through Kvantum.

There are a few ready-made looks: frosted (blurred and a little see-through), glass (clearly
see-through with a light blur) and clear (see-through with no blur behind windows), plus
"off" for solid windows. ``style.transparency`` says how see-through window backgrounds are,
in percent; without it each look uses its own default, lower for light palettes.
``style.focused_solid`` keeps the window in use solid, so only the windows behind it are
see-through; every look does that unless it is set to false.

Breeze cannot make app windows translucent; Kvantum can. Two cases:

* The preset ships a Kvantum theme. A sibling theme "<name>-glass" is written next to it:
  a full copy of its kvconfig with the translucency keys changed, plus a copy of its SVG (with
  the background of the window in use made opaque while it stays solid).
  Kvantum's own "<name>#" copies only find their SVG under /usr/share/Kvantum, and Kvantum
  Manager hides them when no such system theme exists, so they do not work for themes
  installed per user. Keys missing from a kvconfig fall back to Kvantum's defaults, not to
  the original theme, which is why the copy must be complete.
* The preset uses Breeze. The Catppuccin Kvantum theme (MIT) is fetched as a template and
  recoloured from the preset palette into "plasmify-glass".

Plasmify marks the folders it writes, and never replaces a folder of the same name that
it did not make.
"""

from __future__ import annotations

import copy
import json
import math
import re
import shutil
from pathlib import Path
from typing import Any

from plasmify.palette import Palette, is_dark, is_hex, mix
from plasmify.system import UnsafePathError, safe_child

GLASS_LEVELS = ("off", "frosted", "glass", "clear")
# Older names presets and records may still use, and the look they mean now.
LEGACY = {"subtle": "frosted"}
# What the window calls each look.
LABELS = {"off": "Solid", "frosted": "Frosted", "glass": "Glass", "clear": "Clear"}

# How see-through window backgrounds are when style.transparency is left out, in percent
# (Kvantum's reduce_window_opacity): (dark palette, light palette). Light palettes get less,
# because dark text over a bright wallpaper fades fast.
DEFAULT_TRANSPARENCY = {"frosted": (25, 18), "glass": (40, 30), "clear": (50, 38)}
TRANSPARENCY_RANGE = (10, 70)  # what style.transparency may be (schema.TRANSPARENCY)
# Whether the window in use stays solid while the windows behind it are see-through, when
# style.focused_solid is left out (Kvantum's negative reduce_window_opacity): yes, in every look.
# A see-through window in front lets the text of the window behind it show through its own text.
FOCUSED_SOLID = True
# The style keys that make one glass choice together: the look, how see-through it is and whether
# the window in use stays solid. The window and `plasmify set` count them as one change.
CHOICE_KEYS = ("glass", "transparency", "focused_solid")
# Menus are half as see-through as windows, within these bounds, so they stay readable.
MENU_RANGE = (10, 30)
# The looks that have KDE's blur effect blur what is behind app windows; clear does not.
BLURRED = ("frosted", "glass")

# What a look needs from the effects step: blur on, with strength and grain kept within these
# bounds. Frosted wants strong blur; glass keeps it light so the wallpaper stays recognisable.
# Clear leaves the blur settings alone. KWin's strength 1 to 4 blurs a picture scaled down to a
# half or a quarter, 5 to 9 to an eighth (already frosted, the trees of a forest wallpaper melt
# into one tone) and 10 to 15 to a sixteenth.
BLUR_RANGES = {
    "frosted": {"blur_strength": (10, 15), "blur_noise": (5, 14)},
    "glass": {"blur_strength": (2, 4), "blur_noise": (0, 2)},
}
# KWin's own blur values, which hold while kwinrc names none.
KWIN_BLUR_DEFAULTS = {"blur_strength": 15, "blur_noise": 5}
# Panels and terminals turn see-through along with windows, but never below these opacities.
PANEL_MIN_OPACITY = 0.35
TERMINAL_MIN_OPACITY = 0.5
# The values the effects, panel style and terminal steps use when a key is left out.
STEP_DEFAULTS = {"blur_strength": 10, "blur_noise": 4, "panel_opacity": 0.85, "terminal_opacity": 0.9}

# Media, photo and drawing apps stay solid (matched against the app's name, ignoring case),
# on top of the theme's own list. Kvantum itself always adds kscreenlocker and wine.
EXTRA_OPAQUE = (
    "krita",
    "obs",
    "soffice.bin",
    "gwenview",
    "showfoto",
    "kdenlive",
    "shotcut",
    "digikam",
    "vlc",
    "smplayer",
)

# Catppuccin Kvantum (MIT, https://github.com/catppuccin/kvantum), pinned by commit and SHA-256
# like Polonium: the colour table below only fits these exact files.
TEMPLATE_COMMIT = "71105d224fef95dd023691303477ce3eea487457"
TEMPLATE_DARK = "catppuccin-mocha-mauve"
TEMPLATE_LIGHT = "catppuccin-latte-mauve"
TEMPLATE_SHA256 = {
    "catppuccin-mocha-mauve.kvconfig": "6a7887331cd38c852c60dc0f62341456ccc45a1670d4c0c22b405790fbdebc5f",
    "catppuccin-mocha-mauve.svg": "d9eabcdf2764d114faa5bb74fdaa0a3617184191741a7526d3ebfbd30606c3f8",
    "catppuccin-latte-mauve.kvconfig": "a88a6838942703995faa9d4638151e0eae71bd2a309f1035b06afd2b6687e5a6",
    "catppuccin-latte-mauve.svg": "b2e154d0c8e0490beb6b3d34101ca2cd0f74d4119eb2560e3afcfcc7c1b471f0",
}
GENERATED_NAME = "plasmify-glass"
GENERATED_COMMENT = "Generated by Plasmify from the preset palette"

# Catppuccin colours the template uses, (mocha, latte); both files share the same layout.
# The neutral greys and black shadows are the same in both and stay as they are.
TEMPLATE_ROLES = {
    "base": ("#1e1e2e", "#eff1f5"),
    "mantle": ("#181825", "#e6e9ef"),
    "surface": ("#313244", "#ccd0da"),
    "surface_mid": ("#45475a", "#bcc0cc"),
    "overlay": ("#585b70", "#acb0be"),
    "muted": ("#6c7086", "#9ca0b0"),
    "muted2": ("#9399b2", "#7c7f93"),
    "subtext": ("#a6adc8", "#6c6f85"),
    "text": ("#cdd6f4", "#4c4f69"),
    "accent": ("#cba6f7", "#8839ef"),
    "accent_alt": ("#b998e3", "#924cf0"),
    "accent_visited": ("#cbb0f7", "#7c3ed4"),
    "red": ("#f38ba8", "#d20f39"),
}

# A file in every theme folder Plasmify writes; Kvantum only reads the .kvconfig and .svg.
MARKER = "made-by-plasmify.txt"
MARKER_TEXT = (
    "Plasmify made this Kvantum theme for its glass look and replaces it when you apply a preset.\n"
    "To remove it, pick another theme in Kvantum Manager (or apply a preset without glass),\n"
    "then delete this folder.\n"
)

MAX_THEME_BYTES = 4 * 1024 * 1024  # a kvconfig is about 9 KB and an SVG about 150 KB
MAX_OUTPUTS_BYTES = 1024 * 1024

_HEX = re.compile(r"#[0-9a-fA-F]{6}")
# The SVG element Kvantum paints the background of active windows with, and that of inactive ones.
_ACTIVE_WINDOW = re.compile(r"""<[A-Za-z][\w:.-]*\s[^<>]*?\bid\s*=\s*(["'])window-normal\1[^<>]*>""")
_INACTIVE_WINDOW = re.compile(r"""\bid\s*=\s*(["'])window-normal-inactive\1""")
# An element's own opacity, in its style ("opacity:0.8") or as an attribute (opacity="0.8").
_STYLE_OPACITY = re.compile(r"""(?<![\w-])((?:fill-)?opacity\s*:\s*)([^;"']*)""")
_ATTRIBUTE_OPACITY = re.compile(r"""(\s(?:fill-)?opacity\s*=\s*)(["'])([^"']*)\2""")


class ForeignThemeError(RuntimeError):
    """A theme folder of the name Plasmify wants already exists and is not Plasmify's."""


def canonical(level: Any) -> str:
    """``level`` as one of ``GLASS_LEVELS``: an older name becomes the look it means now
    ("subtle" is frosted), and anything unexpected counts as off."""
    if not isinstance(level, str):
        return "off"
    level = LEGACY.get(level, level)
    return level if level in GLASS_LEVELS else "off"


def level_of(data: dict[str, Any]) -> str:
    """The glass look of preset ``data``; anything unexpected counts as off."""
    style = data.get("style") if isinstance(data, dict) else None
    return canonical(style.get("glass", "off") if isinstance(style, dict) else "off")


def default_transparency(level: str, dark: bool) -> int:
    """The transparency ``level`` uses when style.transparency is left out (0 for off)."""
    level = canonical(level)
    if level == "off":
        return 0
    over_dark, over_light = DEFAULT_TRANSPARENCY[level]
    return over_dark if dark else over_light


def _transparency(level: str, dark: bool, value: Any) -> int:
    """``value`` kept within ``TRANSPARENCY_RANGE``; anything but a number means the default."""
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value):
        return default_transparency(level, dark)
    low, high = TRANSPARENCY_RANGE
    return int(min(max(round(value), low), high))


def default_focused_solid(level: str) -> bool:
    """Whether ``level`` keeps the window in use solid when style.focused_solid is left out
    (``FOCUSED_SOLID`` for every look; without glass nothing is see-through)."""
    return FOCUSED_SOLID and canonical(level) != "off"


def _style_table(data_or_style: Any) -> dict[str, Any]:
    """The style table of preset data, or ``data_or_style`` itself when it is one."""
    if not isinstance(data_or_style, dict):
        return {}
    if any(key in data_or_style for key in (*CHOICE_KEYS, "kvantum")):
        return data_or_style
    style = data_or_style.get("style")
    return style if isinstance(style, dict) else {}


def _dark_palette(data: Any) -> bool:
    """Whether preset ``data`` has a dark palette; without a usable one it counts as dark."""
    palette = data.get("palette") if isinstance(data, dict) else None
    base = palette.get("base") if isinstance(palette, dict) else None
    return is_dark(base) if is_hex(base) else True


def effective_transparency(data_or_style: dict[str, Any], dark: bool | None = None) -> int:
    """How see-through window backgrounds are, in percent (0 without glass).

    ``data_or_style`` is preset data or its style table. style.transparency wins when it is set;
    otherwise the look's default for a dark or light palette is used. ``dark`` defaults to the
    mode of the preset's palette (dark when there is none).
    """
    style = _style_table(data_or_style)
    level = canonical(style.get("glass", "off"))
    if level == "off":
        return 0
    if dark is None:
        dark = _dark_palette(data_or_style)
    return _transparency(level, dark, style.get("transparency"))


def effective_focused_solid(data_or_style: dict[str, Any]) -> bool:
    """Whether the window in use stays solid (False without glass).

    ``data_or_style`` is preset data or its style table. style.focused_solid wins when it is set;
    otherwise ``FOCUSED_SOLID`` holds.
    """
    style = _style_table(data_or_style)
    level = canonical(style.get("glass", "off"))
    if level == "off":
        return False
    value = style.get("focused_solid")
    return value if isinstance(value, bool) else default_focused_solid(level)


def describe(level: str, transparency: int, focused_solid: bool = False) -> str:
    """A short description for logs and listings: "glass look, 40% see-through"."""
    level = canonical(level)
    if level == "off":
        return "solid"
    text = f"{LABELS[level].lower()} look, {transparency}% see-through"
    return text + ", the window in use stays solid" if focused_solid else text


def template_url(template: str, extension: str) -> str:
    if template not in (TEMPLATE_DARK, TEMPLATE_LIGHT) or extension not in ("kvconfig", "svg"):
        raise ValueError(f"Unknown glass template file: {template}.{extension}")
    return (
        f"https://raw.githubusercontent.com/catppuccin/kvantum/{TEMPLATE_COMMIT}"
        f"/themes/{template}/{template}.{extension}"
    )


def _number(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def _usable(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int | float) and math.isfinite(value)


def _keep_within(table: dict[str, Any], key: str, low: float, high: float, default: float) -> None:
    """Move ``table[key]`` into ``low..high``. A missing key counts as ``default`` (and is only
    added when that is outside), and a value that is no number is replaced."""
    present = key in table
    value = table.get(key)
    current = value if _usable(value) else default
    wanted = min(max(current, low), high)
    if wanted != current or (present and not _usable(value)):
        table[key] = wanted


def companion_limits(transparency: int) -> dict[str, float]:
    """The highest panel and terminal opacity that suit ``transparency`` percent of glass."""
    share = transparency / 100
    return {
        "panel_opacity": round(max(PANEL_MIN_OPACITY, 1 - share), 2),
        "terminal_opacity": round(max(TERMINAL_MIN_OPACITY, 1 - 0.6 * share), 2),
    }


def with_companions(data: dict[str, Any]) -> dict[str, Any]:
    """Move blur, panel and terminal values to what the glass look needs.

    Frosted and glass turn blur on and keep its strength and grain within the look's bounds
    (``BLUR_RANGES``); clear leaves blur alone. Panels and terminals get no more opaque than
    suits the transparency (``companion_limits``). Only sections the preset already has are
    changed, so no extra step starts running: without an effects section, the style step
    brings the desktop's blur into the look's bounds itself (see :func:`kwin_blur`). The result
    is ``data`` itself when nothing changes, and applying it twice changes nothing more.
    """
    level = level_of(data)
    if level == "off":
        return data
    limits = companion_limits(effective_transparency(data))
    result = copy.deepcopy(data)
    effects = result.get("effects")
    if isinstance(effects, dict) and level in BLUR_RANGES:
        effects["blur"] = True
        for key, (low, high) in BLUR_RANGES[level].items():
            _keep_within(effects, key, low, high, STEP_DEFAULTS[key])
    for section, key in (("panel_style", "panel_opacity"), ("terminal", "terminal_opacity")):
        table = result.get(section)
        if not isinstance(table, dict):
            continue
        if section == "terminal" and level in BLURRED:
            table["blur"] = True
        _keep_within(table, "opacity", 0, limits[key], STEP_DEFAULTS[key])
    return data if result == data else result


def kwin_blur(level: str, current: dict[str, Any]) -> dict[str, int]:
    """KWin blur values for ``level`` when the preset leaves blur to the desktop (it has no
    effects section): the desktop's own (``current``, from kwinrc; a missing or broken value
    counts as KWin's default) moved into the look's bounds. Empty for looks without blur."""
    values = {}
    for key, (low, high) in BLUR_RANGES.get(level, {}).items():
        value = current.get(key)
        value = value if _usable(value) else KWIN_BLUR_DEFAULTS[key]
        values[key] = int(min(max(value, low), high))
    return values


def glass_name(name: str) -> str:
    return f"{name}-glass"


def _is_section(stripped: str) -> bool:
    return stripped.startswith("[") and stripped.endswith("]")


def _key_of(stripped: str) -> str | None:
    if "=" not in stripped or stripped.startswith(("#", ";")):
        return None
    return stripped.split("=", 1)[0].strip()


def set_keys(text: str, section: str, values: dict[str, str]) -> str:
    """Set ``values`` in ``[section]`` of a kvconfig, keeping every other line as it is.

    configparser is no use here: it lowercases keys, drops comments and trips over
    Kvantum's ``[%General]``. New keys go right after the section's last key.
    """
    out: list[str] = []
    current: str | None = None
    found = False
    done: set[str] = set()

    def add_missing() -> None:
        missing = [f"{key}={value}" for key, value in values.items() if key not in done]
        done.update(values)
        if not missing:
            return
        end = len(out)
        while end > 0 and not out[end - 1].strip():
            end -= 1
        out[end:end] = missing

    for line in text.splitlines():
        stripped = line.strip()
        if _is_section(stripped):
            if current == section:
                add_missing()
            current = stripped[1:-1]
            found = found or current == section
        elif current == section and (key := _key_of(stripped)) in values:
            line = f"{key}={values[key]}"
            done.add(key)
        out.append(line)
    if current == section:
        add_missing()
    if not found:
        while out and not out[-1].strip():
            out.pop()
        if out:
            out.append("")
        out += [f"[{section}]", *(f"{key}={value}" for key, value in values.items())]
    return "\n".join(out).rstrip("\n") + "\n"


def get_key(text: str, section: str, key: str) -> str | None:
    """The value of ``key`` in ``[section]`` (the first one), or None."""
    current = None
    for line in text.splitlines():
        stripped = line.strip()
        if _is_section(stripped):
            current = stripped[1:-1]
        elif current == section and _key_of(stripped) == key:
            return stripped.split("=", 1)[1].strip()
    return None


def menu_transparency(transparency: int) -> int:
    """How see-through menus are for ``transparency`` percent of window glass."""
    return min(max(transparency // 2, MENU_RANGE[0]), MENU_RANGE[1])


def _focused(focused_solid: Any) -> bool:
    """``focused_solid`` when it is a bool, else the default (``FOCUSED_SOLID``)."""
    return focused_solid if isinstance(focused_solid, bool) else FOCUSED_SOLID


def glass_kvconfig(
    text: str, level: str, dark: bool, transparency: int | None = None, focused_solid: bool | None = None
) -> str:
    """Return ``text`` (a complete kvconfig) with see-through windows and menus for ``level``.

    ``transparency`` (percent) replaces the look's default for a dark or light palette. Windows
    of every look are see-through by that much, and menus by half as much; frosted and glass
    blur what is behind windows, clear does not. Menus and tooltips are blurred in every look
    (popup_blurring), so their text stays readable.

    ``focused_solid`` (anything but a bool means the default, on) keeps the window in use
    solid: Kvantum reads a negative reduce_window_opacity as "inactive windows only". It needs
    Kvantum to tell inactive windows apart, which a theme can switch off (no_inactiveness).
    """
    look = LEGACY.get(level, level) if isinstance(level, str) else None
    if look not in DEFAULT_TRANSPARENCY:
        raise ValueError(f"Unknown glass level: {level!r}")
    window = _transparency(look, dark, transparency)
    focused = _focused(focused_solid)
    opaque = [a.strip() for a in (get_key(text, "%General", "opaque") or "").split(",") if a.strip()]
    known = {name.lower() for name in opaque}
    opaque += [name for name in EXTRA_OPAQUE if name not in known]
    general = {
        "composite": "true",
        "translucent_windows": "true",
        "blurring": "true" if look in BLURRED else "false",
        "popup_blurring": "true",
        "reduce_window_opacity": str(-window if focused else window),
        "reduce_menu_opacity": str(menu_transparency(window)),
        "opaque": ",".join(opaque),
    }
    if focused:
        general["no_inactiveness"] = "false"
    for key in ("menu_blur_radius", "tooltip_blur_radius"):
        if _number(get_key(text, "%General", key), 0) < 2:
            general[key] = "2"  # keeps the blur inside rounded popup corners (Kvantum docs)
    text = set_keys(text, "%General", general)
    return set_keys(text, "Hacks", {"transparent_dolphin_view": "true"})


def look_of_kvconfig(text: str, dark: bool, blur_strength: Any = None) -> dict[str, Any]:
    """The style keys of a glass kvconfig Plasmify wrote (see :func:`glass_kvconfig`): the look,
    plus transparency and focused_solid where they are not the defaults, so a saved preset keeps
    following the defaults.

    blurring=false is clear. Frosted and glass both blur and differ in KWin's blur strength,
    which the effects step keeps at 10 or more for frosted and at 4 or less for glass, so
    ``blur_strength`` (from kwinrc) decides when it says so; otherwise the look whose default
    transparency is closer wins. A negative reduce_window_opacity makes only inactive windows
    see-through, by its size: the window in use stays solid (as in the old subtle look too).
    """
    try:
        reduction: int | None = int(get_key(text, "%General", "reduce_window_opacity") or "")
    except ValueError:
        reduction = None
    low, high = TRANSPARENCY_RANGE
    window = None if reduction is None else min(max(abs(reduction), low), high)
    # Plasmify always writes blurring; without it (an unreadable file) frosted is the safe guess.
    blurring = (get_key(text, "%General", "blurring") or "true").lower() == "true"
    strength = blur_strength if _usable(blur_strength) else None
    glass_most = BLUR_RANGES["glass"]["blur_strength"][1]
    frosted_least = BLUR_RANGES["frosted"]["blur_strength"][0]
    if not blurring:
        level = "clear"
    elif strength is not None and (strength <= glass_most or strength >= frosted_least):
        level = "glass" if strength <= glass_most else "frosted"
    elif window is None:
        level = "frosted"
    else:
        to_glass = abs(window - default_transparency("glass", dark))
        level = "glass" if to_glass < abs(window - default_transparency("frosted", dark)) else "frosted"
    style: dict[str, Any] = {"glass": level}
    if window is not None and window != default_transparency(level, dark):
        style["transparency"] = window
    focused = reduction is not None and reduction < 0
    if reduction is not None and focused != FOCUSED_SOLID:
        style["focused_solid"] = focused
    return style


def _opaque_value(value: str) -> str:
    """An opacity at 1, unless it already is (or is no number)."""
    return "1" if _number(value.strip(), 1.0) < 1 else value


def solid_active_window(svg: str) -> str:
    """``svg`` (a Kvantum theme's SVG) with the background of active windows fully opaque.

    Some themes draw it a little see-through themselves (Layan: opacity 0.8), so the window in use
    would never be solid. Only the window-normal element's own opacity and fill-opacity change.
    Inactive windows keep their look: a theme without an element of their own (Kvantum then uses
    the active one for them) gets a copy of the original as window-normal-inactive. A gradient's
    stop opacity and a group's opacity stay as they are, and anything unexpected (no element, two
    of them, one that is not self-closing while it needs copying) leaves ``svg`` as it is.
    """
    found = list(_ACTIVE_WINDOW.finditer(svg))
    if len(found) != 1:
        return svg
    tag = found[0].group(0)
    solid = _STYLE_OPACITY.sub(lambda m: m.group(1) + _opaque_value(m.group(2)), tag)
    solid = _ATTRIBUTE_OPACITY.sub(lambda m: m.group(1) + m.group(2) + _opaque_value(m.group(3)) + m.group(2), solid)
    if solid == tag:
        return svg
    if not _INACTIVE_WINDOW.search(svg):
        if not tag.endswith("/>"):
            return svg  # the whole element cannot be copied for inactive windows
        inactive = re.sub(r"""(\bid\s*=\s*["'])window-normal(["'])""", r"\1window-normal-inactive\2", tag, count=1)
        solid = f"{solid}{inactive}"
    start, end = found[0].span()
    return svg[:start] + solid + svg[end:]


def made_by_plasmify(folder: Path) -> bool:
    return folder.is_dir() and not folder.is_symlink() and (folder / MARKER).is_file()


def _fresh_folder(target: Path) -> None:
    """Empty ``target`` for a new theme; a folder Plasmify did not make is never touched."""
    if target.is_symlink() or (target.exists() and not made_by_plasmify(target)):
        raise ForeignThemeError(f"A Kvantum theme named {target.name} already exists and is not Plasmify's")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    (target / MARKER).write_text(MARKER_TEXT, encoding="utf-8")


def _read_theme_file(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"{path.name} is missing from the theme")
    if path.stat().st_size > MAX_THEME_BYTES:
        raise ValueError(f"{path.name} is too large for a Kvantum theme")
    return path.read_text(encoding="utf-8", errors="replace")


def write_glass_copy(
    theme_dir: Path,
    name: str,
    level: str,
    dark: bool,
    transparency: int | None = None,
    focused_solid: bool | None = None,
) -> Path:
    """Write the theme ``<name>-glass`` beside ``theme_dir`` and return its folder. While the
    window in use stays solid, its SVG gets an opaque active window (see :func:`solid_active_window`);
    otherwise, and when that changes nothing, the SVG is copied as it is."""
    safe_child(theme_dir.parent, name)  # refuses "", "..", "a/b" before anything else
    target = safe_child(theme_dir.parent, glass_name(name))
    original = _read_theme_file(safe_child(theme_dir, f"{name}.kvconfig"))
    config = glass_kvconfig(original, level, dark, transparency, focused_solid)
    try:
        svg: Path | None = safe_child(theme_dir, f"{name}.svg")
    except UnsafePathError:  # a link out of the theme folder is never copied
        svg = None
    if svg is not None and (svg.is_symlink() or not svg.is_file()):
        svg = None
    solid = None
    if svg is not None and _focused(focused_solid):
        text = _read_theme_file(svg)
        solid = solid_active_window(text)
        solid = None if solid == text else solid
    _fresh_folder(target)
    (target / f"{target.name}.kvconfig").write_text(config, encoding="utf-8")
    if solid is not None:
        (target / f"{target.name}.svg").write_text(solid, encoding="utf-8")
    elif svg is not None:
        shutil.copyfile(svg, target / f"{target.name}.svg")
    return target


def palette_roles(p: Palette) -> dict[str, str]:
    return {
        "base": p.base,
        "mantle": p.mantle,
        "surface": p.surface,
        "surface_mid": mix(p.surface, p.overlay, 0.5),
        "overlay": p.overlay,
        "muted": mix(p.overlay, p.subtext, 0.35),
        "muted2": mix(p.overlay, p.subtext, 0.6),
        "subtext": p.subtext,
        "text": p.text,
        "accent": p.accent,
        "accent_alt": mix(p.accent, p.base, 0.12),
        "accent_visited": mix(p.accent, p.text, 0.2),
        "red": p.red,
    }


def recolour(text: str, palette: Palette) -> str:
    """Swap the template's Catppuccin colours for the palette's, in one pass.

    One pass, so a palette colour that happens to equal another template colour is never
    swapped twice. Case and any alpha suffix (``#CBA6F74D``) are kept.
    """
    column = 0 if palette.dark else 1
    roles = palette_roles(palette)
    table = {colours[column]: roles[role] for role, colours in TEMPLATE_ROLES.items()}

    def swap(match: re.Match[str]) -> str:
        new = table.get(match.group(0).lower())
        if new is None:
            return match.group(0)
        return new.upper() if match.group(0).isupper() else new.lower()

    return _HEX.sub(swap, text)


def write_palette_theme(
    template_dir: Path,
    template: str,
    kvantum_dir: Path,
    palette: Palette,
    level: str,
    transparency: int | None = None,
    focused_solid: bool | None = None,
) -> Path:
    """Recolour the downloaded template into ``kvantum_dir/plasmify-glass`` and return it (with an
    opaque active window while the window in use stays solid, see :func:`solid_active_window`)."""
    if template not in (TEMPLATE_DARK, TEMPLATE_LIGHT):
        raise ValueError(f"Unknown glass template: {template!r}")
    config = recolour(_read_theme_file(template_dir / f"{template}.kvconfig"), palette)
    config = set_keys(config, "%General", {"comment": GENERATED_COMMENT})  # the author credit stays
    config = glass_kvconfig(config, level, palette.dark, transparency, focused_solid)
    svg = recolour(_read_theme_file(template_dir / f"{template}.svg"), palette)
    if _focused(focused_solid):
        svg = solid_active_window(svg)
    target = safe_child(kvantum_dir, GENERATED_NAME)
    _fresh_folder(target)
    (target / f"{GENERATED_NAME}.kvconfig").write_text(config, encoding="utf-8")
    (target / f"{GENERATED_NAME}.svg").write_text(svg, encoding="utf-8")
    return target


def fractional_scale(outputs_config: Path) -> bool:
    """Whether KWin's screen settings use a scale between whole numbers (like 125 %).

    Kvantum keeps windows solid at such scales because of Qt bugs. Any problem reading the
    file counts as no.
    """
    try:
        if outputs_config.stat().st_size > MAX_OUTPUTS_BYTES:
            return False
        sections = json.loads(outputs_config.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return False
    if not isinstance(sections, list):
        return False
    for section in sections:
        if not isinstance(section, dict) or section.get("name") != "outputs":
            continue
        for output in section.get("data") or []:
            scale = output.get("scale") if isinstance(output, dict) else None
            if isinstance(scale, bool) or not isinstance(scale, int | float) or not math.isfinite(scale):
                continue
            if abs(scale - round(scale)) > 0.01:
                return True
    return False
