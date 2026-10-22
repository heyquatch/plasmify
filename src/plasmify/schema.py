"""Strict validation of preset files.

Presets are shared between strangers, so they are treated as untrusted input.
Every value is checked against an explicit type, range or character set before
any of it reaches a config file, a file path or the package manager:

* strings may not contain control characters (no new lines that could inject
  extra lines into terminal or KDE config files);
* names that become file or directory names are restricted to a safe
  character set and may never contain ``..`` or ``/``;
* repository paths are relative and cannot climb out of their checkout;
* URLs must be plain ``https://`` without credentials;
* packages must be in the reviewed list in ``package_map.PACKAGES``;
* unknown sections and keys are rejected rather than ignored.

See SECURITY.md for the threat model.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any
from urllib.parse import urlsplit

from plasmify import blockfont, panels
from plasmify.package_map import PACKAGES
from plasmify.palette import REQUIRED_KEYS, is_hex

SCHEMA_VERSION = 1

LAYOUTS = ("topbar-dock", "bottom-panel", "top-panel")
OPEN_CLOSE_EFFECTS = ("scale", "glide", "fade", "none")
MINIMIZE_EFFECTS = ("magiclamp", "squash", "none")
DESKTOP_SWITCH_EFFECTS = ("slide", "fade", "none")
# Glass looks for Qt app windows through Kvantum (plasmify.glass): off (solid), frosted (blurred
# and a little see-through), glass (clearly see-through, lightly blurred) or clear (see-through,
# no blur behind windows).
GLASS_LEVELS = ("off", "frosted", "glass", "clear")
# Older names that stay valid in preset files and records, and what they mean now.
GLASS_ALIASES = {"subtle": "frosted"}
# How see-through window backgrounds are with glass, in percent (optional; each look has a default).
TRANSPARENCY = (10, 70)
PROMPTS = ("powerline", "minimal", "none")
WALLPAPER_MODES = ("slideshow", "single")
WALLPAPER_ORDERS = ("listed", "shuffle")
# Living wallpapers made by plasmify.shimmer ("none" keeps the pictures still; "calm" is only
# the light and the motion, without particles). The rest are plasmify.shimmer.STYLES.
SHIMMER_STYLES = (
    "none",
    "sparkle",
    "embers",
    "petals",
    "snow",
    "fireflies",
    "bokeh",
    "starfall",
    "rain",
    "magic",
    "speedlines",
    "calm",
)
# The light over the picture and how the picture moves ("auto": what suits the style).
SHIMMER_LIGHTS = ("auto", "glint", "rays", "flare", "pulse", "none")
SHIMMER_MOTIONS = ("auto", "breathe", "drift", "still")
SHIMMER_DENSITIES = ("low", "normal", "high")
# Frames per second of the living wallpapers: 12 is lighter (smaller files, half the render time),
# 24 is smoother.
SHIMMER_FPS = (12, 24)
BOTTOM_KINDS = ("none", "panel", "dock")
LOGO_KINDS = ("distro", "builtin", "image", "text", "ascii")
# Several colours blended across the logo (see steps.shell.SCHEME_STOPS).
LOGO_SCHEMES = ("gradient", "rainbow", "sunset", "ocean", "forest", "fire", "candy", "pastel", "neon", "aurora", "mono")
LOGO_SINGLE = ("accent", "text", "red", "green", "yellow", "blue", "magenta", "cyan", "orange")
# "original": a fastfetch logo in its own colours (text and drawn logos use the gradient then).
LOGO_COLOURS = ("original", *LOGO_SCHEMES, *LOGO_SINGLE)
LOGO_DIRECTIONS = ("down", "up", "right", "left", "diagonal")
ASCII_MAX_LINES = 24
ASCII_MAX_WIDTH = 80
LOGO_SIZES = ("small", "medium", "large")
DOCK_HIDING = ("dodge", "autohide", "always")
CLOCK_POSITIONS = ("center", "right")

# Hosts presets may download from without asking. Anything else is allowed only
# after the user confirms, because the host learns the user's IP address.
TRUSTED_HOSTS = (
    "github.com",
    "raw.githubusercontent.com",
    "objects.githubusercontent.com",
    "codeberg.org",
    "gitlab.com",
)

# Control characters, Unicode line/paragraph separators, and bidirectional overrides and
# isolates (which can make a shared preset's name display as something else).
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f\u200b\u2028\u2029\u200e\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff]")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,79}$")
# Words joined by single dashes: "a--b" or "a-" would map to the same colour scheme as "a-b".
_ID = re.compile(r"^(?=.{1,48}$)[a-z0-9]+(?:-[a-z0-9]+)*$")
# Host names in plain ASCII (urlsplit lowercases them). A look-alike letter from another
# alphabet ("gіthub.com" with a Cyrillic і) or a percent-encoded one (urllib decodes it before
# connecting) would pass for a trusted host in the question about untrusted hosts.
# International names still work when written as xn-- punycode, which is shown as such.
_HOST = re.compile(r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$")
_FONT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._+-]{0,63}$")
# Path segments never start with "-", so a path can never be read as a git option.
_RELPATH = re.compile(r"^[A-Za-z0-9._+][A-Za-z0-9._+-]*(/[A-Za-z0-9._+][A-Za-z0-9._+-]*)*$")
_TAG = re.compile(r"^[a-z0-9][a-z0-9-]{0,23}$")
# fastfetch logo names: "KDE Neon", "Adélie", "z/OS"... The step only uses names fastfetch itself
# lists, so the pattern just keeps out control characters, ".." and leading punctuation.
_LOGO_NAME = re.compile(r"^(?!.*\.\.)[^\W_][\w .+/-]{0,47}$")
# re.ASCII: with IGNORECASE alone, [A-Za-z] also matches "ſ" (U+017F) and the Kelvin sign.
_IMAGE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,79}\.(png|jpg|jpeg|webp|gif)$", re.IGNORECASE | re.ASCII)

Check = Callable[[Any], str | None]


# ----- value checks (return an error message or None) --------------------


def text(max_len: int) -> Check:
    def check(value: Any) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return "must be a non-empty string"
        if len(value) > max_len:
            return f"must be at most {max_len} characters"
        if _CONTROL.search(value):
            return "must not contain control characters or line breaks"
        return None

    return check


def pattern(regex: re.Pattern[str], what: str) -> Check:
    def check(value: Any) -> str | None:
        # fullmatch, not match: "$" would also accept a trailing line break.
        if not isinstance(value, str) or not regex.fullmatch(value) or ".." in value or _CONTROL.search(value):
            return f"must be {what}"
        return None

    return check


def integer(low: int, high: int) -> Check:
    def check(value: Any) -> str | None:
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            return f"must be a whole number from {low} to {high}"
        return None

    return check


def glass_level(value: Any) -> str | None:
    """A glass look; the older names in ``GLASS_ALIASES`` are accepted but not offered."""
    if value in (*GLASS_LEVELS, *GLASS_ALIASES):  # a tuple: a list or table as value cannot raise
        return None
    return f"must be one of {', '.join(GLASS_LEVELS)}"


def dim_strength(value: Any) -> str | None:
    """0 turns dimming of inactive windows off; KWin's own settings page offers 10 to 90."""
    if isinstance(value, bool) or not isinstance(value, int) or not (value == 0 or 10 <= value <= 90):
        return "must be 0 (off) or a whole number from 10 to 90"
    return None


def number(low: float, high: float) -> Check:
    def check(value: Any) -> str | None:
        if isinstance(value, bool) or not isinstance(value, int | float) or not low <= value <= high:
            return f"must be a number from {low} to {high}"
        return None

    return check


def boolean(value: Any) -> str | None:
    return None if isinstance(value, bool) else "must be true or false"


def one_of(*choices: str) -> Check:
    def check(value: Any) -> str | None:
        return None if value in choices else f"must be one of {', '.join(choices)}"

    return check


def whole_number_of(*choices: int) -> Check:
    def check(value: Any) -> str | None:
        ok = type(value) is int and value in choices  # not bool, not 12.0
        return None if ok else "must be " + " or ".join(str(choice) for choice in choices)

    return check


def url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 500 or _CONTROL.search(value) or " " in value:
        return "must be an https:// URL"
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.hostname:
        return "must be an https:// URL"
    if not _HOST.fullmatch(parts.hostname):
        return "must name its host in plain letters a-z, digits, dots and dashes (xn-- for international names)"
    if parts.username or parts.password:
        return "must not contain credentials"
    return None


def logo_text(value: Any) -> str | None:
    if not isinstance(value, str) or _CONTROL.search(value) or not blockfont.supported(value):
        return f"must be 1-{blockfont.MAX_LENGTH} letters, digits, spaces or ! ? . - _ + # @ & *"
    return None


def ascii_art(value: Any) -> str | None:
    """A user-drawn logo: several lines of plain characters (box drawing and emoji are fine).

    New lines are the only control characters allowed, and ``$`` is refused because
    fastfetch reads ``$1``..``$9`` in logo files as colour codes.
    """
    problem = (
        f"must be up to {ASCII_MAX_LINES} lines of up to {ASCII_MAX_WIDTH} characters, "
        "without tabs, $ or control characters"
    )
    if not isinstance(value, str) or not value.strip():
        return problem
    lines = value.split("\n")
    if len(lines) > ASCII_MAX_LINES or any(len(line) > ASCII_MAX_WIDTH for line in lines):
        return problem
    if "$" in value or _CONTROL.search(value.replace("\n", "")):
        return problem
    return None


def hex_colour(value: Any) -> str | None:
    return None if isinstance(value, str) and is_hex(value) else "must be a #rrggbb colour"


def package(value: Any) -> str | None:
    if value not in PACKAGES:
        return "is not in the reviewed package list (src/plasmify/package_map.py)"
    return None


NAME = pattern(_NAME, "a simple name (letters, digits, . _ + -)")
FONT = pattern(_FONT, "a font family name (letters, digits, spaces, . _ + -)")
RELPATH = pattern(_RELPATH, "a relative path inside the repository")
IMAGE_FILE = pattern(_IMAGE_FILE, "an image file name (.png, .jpg, .jpeg, .webp or .gif) without folders")


def palette_colour(value: Any) -> str | None:
    """A palette colour name (follows the theme) or a fixed #rrggbb colour."""
    if value in REQUIRED_KEYS or (isinstance(value, str) and is_hex(value)):
        return None
    return "must be a palette colour name (accent, base, mantle…) or a #rrggbb colour"


PANEL = {
    "edge": (one_of(*panels.EDGES), True),
    "style": (one_of(*panels.STYLES), False),
    "length": (one_of(*panels.LENGTHS), False),
    "align": (one_of(*panels.ALIGNS), False),
    "thickness": (integer(*panels.THICKNESS), False),
    "floating": (boolean, False),
    "hiding": (one_of(*panels.HIDINGS), False),
    "widgets": (("list", one_of(*panels.WIDGETS), panels.MAX_WIDGETS), True),
}

# fastfetch modules the greeting may show, in any order.
FASTFETCH_MODULES = (
    "title", "separator", "os", "host", "kernel", "uptime", "packages", "shell", "display", "de", "wm",
    "theme", "icons", "font", "cursor", "terminal", "terminalfont", "cpu", "gpu", "memory", "swap", "disk",
    "localip", "battery", "locale", "media", "weather", "datetime", "break", "colors",
)  # fmt: skip

# A wallpaper is either downloaded (url) or shipped inside a shared preset bundle (file).
WALLPAPER_IMAGE = {
    "url": (url, False),
    "file": (IMAGE_FILE, False),
    "credit": (text(200), True),
    "license": (text(60), False),
}


# ----- schema -------------------------------------------------------------

# Each table maps key -> (check, required). Nested tables are dicts; lists are
# ("list", item_schema, max_items).
SOURCE = {"repo": (url, True), "path": (RELPATH, True), "ref": (NAME, False)}

SCHEMA: dict[str, Any] = {
    "schema": (integer(SCHEMA_VERSION, SCHEMA_VERSION), True),
    "meta": (
        {
            "id": (pattern(_ID, "lowercase letters, digits and dashes"), True),
            "name": (text(60), True),
            "description": (text(300), True),
            "author": (text(80), False),
            "tags": (("list", pattern(_TAG, "a short lowercase tag"), 8), False),
            "preview": (NAME, False),  # no longer used (previews are painted); kept for older presets
        },
        True,
    ),
    "palette": ("palette", True),
    "style": (
        {
            "kvantum": ("kvantum", False),
            "glass": (glass_level, False),
            "transparency": (integer(*TRANSPARENCY), False),
            # Only windows in the background see-through (optional; each look has a default).
            "focused_solid": (boolean, False),
        },
        False,
    ),
    "icons": (
        {
            "theme": (NAME, True),
            "folder_color": (NAME, False),
            "folder_source": (SOURCE, False),
        },
        False,
    ),
    # Without a url the cursor theme must already be installed (picked from the user's system).
    "cursor": ({"name": (NAME, True), "url": (url, False), "size": (integer(16, 96), False)}, False),
    "fonts": (
        {
            "ui": (FONT, True),
            "ui_size": (integer(6, 24), False),
            "mono": (FONT, True),
            "mono_size": (integer(6, 32), False),
            "packages": (("list", package, 10), False),
        },
        False,
    ),
    "layout": (
        {
            # Either a named layout, or the parts below (parts override the name's defaults).
            "name": (one_of(*LAYOUTS), False),
            "top_bar": (boolean, False),
            "bottom": (one_of(*BOTTOM_KINDS), False),
            "dock_hiding": (one_of(*DOCK_HIDING), False),
            "clock": (one_of(*CLOCK_POSITIONS), False),
            "pager": (boolean, False),
            "task_labels": (boolean, False),
            "floating": (boolean, False),
            "bar_height": (integer(20, 80), False),
            "dock_height": (integer(32, 128), False),
            "launcher_icon": (url, False),
            # The long form: any panels on any edges (written by the Customize tab).
            "panels": (("list", "panel", panels.MAX_PANELS), False),
        },
        False,
    ),
    "wallpaper": (
        {
            "mode": (one_of(*WALLPAPER_MODES), False),
            "interval": (integer(60, 86400), False),
            "order": (one_of(*WALLPAPER_ORDERS), False),
            "lock_screen": (boolean, False),
            "shimmer": (one_of(*SHIMMER_STYLES), False),
            "shimmer_colour": (palette_colour, False),
            "shimmer_light": (one_of(*SHIMMER_LIGHTS), False),
            "shimmer_motion": (one_of(*SHIMMER_MOTIONS), False),
            "shimmer_density": (one_of(*SHIMMER_DENSITIES), False),
            "shimmer_fps": (whole_number_of(*SHIMMER_FPS), False),
            "shimmer_always": (boolean, False),
            "images": (("list", "wallpaper_image", 30), True),
        },
        False,
    ),
    "effects": (
        {
            "blur": (boolean, False),
            "blur_strength": (integer(1, 15), False),
            "blur_noise": (integer(0, 14), False),
            "open_close": (one_of(*OPEN_CLOSE_EFFECTS), False),
            "minimize": (one_of(*MINIMIZE_EFFECTS), False),
            "desktop_switch": (one_of(*DESKTOP_SWITCH_EFFECTS), False),
            "wobbly": (boolean, False),
            "wobbly_level": (integer(0, 4), False),
            "fall_apart": (boolean, False),
            "sheet": (boolean, False),
            "cube": (boolean, False),
            "blur_saturation": (integer(100, 500), False),
            "moving_opacity": (number(0.3, 1.0), False),
            "dim_inactive": (dim_strength, False),
            "tiling": (boolean, False),
            "animation_speed": (number(0, 4), False),
            "titlebar_buttons": (one_of("right", "left", "close", "none"), False),
            "window_borders": (one_of("none", "tiny", "normal", "large"), False),
        },
        False,
    ),
    "terminal": (
        {
            "opacity": (number(0.3, 1.0), False),
            "blur": (boolean, False),
            "padding": (integer(0, 64), False),
            "font_size": (number(6, 32), False),
        },
        False,
    ),
    "splash": (
        {
            "style": (one_of("plasmify", "breeze", "none"), False),
            "background": (one_of("palette", "wallpaper", "image"), False),
            "background_file": (IMAGE_FILE, False),
            "logo": (one_of("distro", "preset", "plasma", "image", "none"), False),
            "logo_file": (IMAGE_FILE, False),
            "text": (text(40), False),
            "progress": (boolean, False),
        },
        False,
    ),
    "login": (
        {
            "background": (one_of("wallpaper", "image"), False),
            "background_file": (IMAGE_FILE, False),
        },
        False,
    ),
    "panel_style": (
        {
            "radius": (integer(0, 30), False),
            "opacity": (number(0.2, 1.0), False),
            "border": (one_of("none", "subtle", "accent"), False),
            "padding": (integer(0, 12), False),
            "colour": (palette_colour, False),
        },
        False,
    ),
    "shell": (
        {
            "prompt": (one_of(*PROMPTS), False),
            "zoxide": (boolean, False),
            "fastfetch_logo": (lambda v: None if v == "distro" else url(v), False),
            "show_user_host": (boolean, False),
            "greeting": (boolean, False),
            # Terminal logo: the distribution's, one of fastfetch's built-in logos,
            # an image (preset URL or a bundled file) or the user's own text.
            "logo": (one_of(*LOGO_KINDS), False),
            "logo_builtin": (pattern(_LOGO_NAME, "a fastfetch logo name"), False),
            "logo_text": (logo_text, False),
            "logo_ascii": (ascii_art, False),
            "logo_colour": (one_of(*LOGO_COLOURS), False),
            "logo_direction": (one_of(*LOGO_DIRECTIONS), False),
            "logo_file": (IMAGE_FILE, False),
            "logo_size": (one_of(*LOGO_SIZES), False),
            "fastfetch_modules": (("list", one_of(*FASTFETCH_MODULES), 30), False),
        },
        False,
    ),
}


def _check_palette(value: Any, where: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{where} must be a table")
        return
    for key in REQUIRED_KEYS:
        if key not in value:
            errors.append(f"{where}.{key} is required")
    allowed = set(REQUIRED_KEYS) | {f"bright_{k}" for k in ("red", "green", "yellow", "blue", "magenta", "cyan")}
    allowed |= {"bright_black", "bright_white"}
    for key, colour in value.items():
        if key not in allowed:
            errors.append(f"{where}.{key} is not a known palette key")
        elif problem := hex_colour(colour):
            errors.append(f"{where}.{key} {problem}")


def _check_kvantum(value: Any, where: str, errors: list[str]) -> None:
    if isinstance(value, dict) and "url" in value:
        _check_table(value, {"url": (url, True), "name": (NAME, True)}, where, errors)
    else:
        _check_table(value, {**SOURCE, "name": (NAME, True)}, where, errors)


def _check_value(spec: Any, value: Any, where: str, errors: list[str]) -> None:
    if spec == "palette":
        _check_palette(value, where, errors)
    elif spec == "kvantum":
        _check_kvantum(value, where, errors)
    elif spec == "wallpaper_image":
        _check_table(value, WALLPAPER_IMAGE, where, errors)
        if isinstance(value, dict) and ("url" in value) == ("file" in value):
            errors.append(f"{where} needs exactly one of url or file")
    elif spec == "panel":
        _check_table(value, PANEL, where, errors)
    elif isinstance(spec, dict):
        _check_table(value, spec, where, errors)
    elif isinstance(spec, tuple) and spec and spec[0] == "list":
        _, item_spec, max_items = spec
        if not isinstance(value, list):
            errors.append(f"{where} must be a list")
            return
        if len(value) > max_items:
            errors.append(f"{where} may have at most {max_items} entries")
        for index, item in enumerate(value):
            _check_value(item_spec, item, f"{where}[{index}]", errors)
    elif problem := spec(value):
        errors.append(f"{where} {problem}")


def _check_table(value: Any, spec: dict[str, Any], where: str, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append(f"{where or 'preset'} must be a table")
        return
    prefix = f"{where}." if where else ""
    for key in value:
        if key not in spec:
            errors.append(f"{prefix}{key} is not a known key")
    for key, (item_spec, required) in spec.items():
        if key not in value:
            if required:
                errors.append(f"{prefix}{key} is required")
            continue
        _check_value(item_spec, value[key], f"{prefix}{key}", errors)


def validate(data: Any) -> list[str]:
    """Return every problem found in ``data``; an empty list means it is safe to use."""
    errors: list[str] = []
    _check_table(data, SCHEMA, "", errors)
    layout = data.get("layout") if isinstance(data, dict) else None
    if isinstance(layout, dict) and "panels" in layout:
        errors += panels.problems(layout["panels"])  # safe on any type; always checked
    elif isinstance(layout, dict) and not errors:
        if "name" not in layout and "top_bar" not in layout and "bottom" not in layout:
            errors.append("layout needs a name or top_bar/bottom")
        parts = layout_parts(layout)
        if not parts["top_bar"] and parts["bottom"] == "none":
            errors.append("layout must keep at least one panel (top bar or bottom)")
    shell = data.get("shell") if isinstance(data, dict) else None
    if isinstance(shell, dict) and not errors:
        kind = shell.get("logo")
        if kind == "builtin" and "logo_builtin" not in shell:
            errors.append("shell.logo = builtin needs shell.logo_builtin")
        if kind == "text" and "logo_text" not in shell:
            errors.append("shell.logo = text needs shell.logo_text")
        if kind == "ascii" and "logo_ascii" not in shell:
            errors.append("shell.logo = ascii needs shell.logo_ascii")
        if kind == "image" and "logo_file" not in shell and shell.get("fastfetch_logo", "distro") == "distro":
            errors.append("shell.logo = image needs shell.logo_file or shell.fastfetch_logo")
    return errors


# What each named layout means in terms of parts.
LAYOUT_DEFAULTS = {
    "topbar-dock": {"top_bar": True, "bottom": "dock"},
    "bottom-panel": {"top_bar": False, "bottom": "panel"},
    "top-panel": {"top_bar": True, "bottom": "none"},
}
PART_DEFAULTS = {
    "dock_hiding": "dodge",
    "clock": "center",
    "pager": True,
    "task_labels": False,
    "floating": True,
    "bar_height": 32,
    "dock_height": 56,
}


def layout_parts(layout: dict[str, Any]) -> dict[str, Any]:
    """Resolve a layout table (named and/or by parts) into the full set of parts."""
    parts = dict(PART_DEFAULTS)
    parts.update(LAYOUT_DEFAULTS.get(layout.get("name", ""), {"top_bar": True, "bottom": "dock"}))
    parts.update({k: v for k, v in layout.items() if k in PART_DEFAULTS or k in ("top_bar", "bottom")})
    return parts


def local_files(data: dict[str, Any]) -> list[str]:
    """Wallpaper files a preset expects in its own folder (bundled presets).

    Safe on unvalidated data too: any section may be missing or the wrong type."""

    def table(name: str) -> dict:
        value = data.get(name) if isinstance(data, dict) else None
        return value if isinstance(value, dict) else {}

    images = table("wallpaper").get("images", [])
    images = images if isinstance(images, list) else []
    files = [image["file"] for image in images if isinstance(image, dict) and "file" in image]
    for section, key in (
        ("shell", "logo_file"),
        ("splash", "background_file"),
        ("splash", "logo_file"),
        ("login", "background_file"),
    ):
        value = table(section).get(key)
        if isinstance(value, str) and value not in files:
            files.append(value)
    return files


def urls(data: dict[str, Any]) -> list[str]:
    """Every URL a (validated) preset may download from."""
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("url", "repo", "launcher_icon", "fastfetch_logo") and isinstance(item, str):
                    if item.startswith("https://"):
                        found.append(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(data)
    return found


WEATHER_HOST = "wttr.in"  # fastfetch's weather line asks it, over plain HTTP, each time a terminal opens


def untrusted_hosts(data: dict[str, Any]) -> list[str]:
    """Hosts outside ``TRUSTED_HOSTS`` that applying this preset would contact (or make the
    terminal contact later: the greeting's weather line)."""
    hosts = {urlsplit(u).hostname or "" for u in urls(data)}
    shell = data.get("shell")
    if isinstance(shell, dict) and "weather" in (shell.get("fastfetch_modules") or []):
        hosts.add(WEATHER_HOST)
    return sorted(h for h in hosts if h not in TRUSTED_HOSTS)
