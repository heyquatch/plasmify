"""Light or dark: the applied theme in the other mode, for the one-click switch.

Only the colours change: wallpapers, panels, fonts and everything else stay. Going back to the
preset's own mode brings back its own palette (keeping an accent colour the user picked).
"""

from __future__ import annotations

import copy
from typing import Any

from plasmify.generate import ensure_contrast, mode_variant
from plasmify.presets import Preset

# Steps whose output depends on the colours; the rest of the desktop is left alone.
MODE_STEPS = ("colors", "style", "icons", "panelstyle", "terminal", "shell", "splash")

# Icon themes that come in a light and a dark flavour: name -> (light, dark).
ICON_FAMILIES = {
    "Papirus": ("Papirus", "Papirus-Dark"),
    "Papirus-Light": ("Papirus-Light", "Papirus-Dark"),
    "Papirus-Dark": ("Papirus", "Papirus-Dark"),
    "breeze": ("breeze", "breeze-dark"),
    "breeze-dark": ("breeze", "breeze-dark"),
}


def icons_for(theme: str | None, dark: bool) -> str | None:
    """The flavour of ``theme`` for the mode (None when the theme has no other flavour)."""
    family = ICON_FAMILIES.get(theme or "")
    return family[1 if dark else 0] if family else None


def switch(
    preset: Preset, overrides: dict[str, Any], applied: Preset, dark: bool, own: dict[str, Any] | None = None
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """``overrides`` (the tweaks applied on top of ``preset``, giving ``applied``) changed so
    the theme comes out ``dark`` or light.

    Returns the new overrides and what to remember for the way back: leaving the preset's own
    mode saves the palette and icon tweaks it had (``own``), and coming back puts exactly those
    back, unless they were changed in between."""
    own = way_back(own)
    result = copy.deepcopy(overrides)
    colours = applied.palette.colors
    theme = applied.section("icons").get("theme")
    own_theme = preset.section("icons").get("theme")
    icons = result.get("icons")
    icons = dict(icons) if isinstance(icons, dict) else {}

    if preset.palette.dark != dark:  # leaving the preset's own mode
        made = mode_variant(colours, dark)
        keep = {
            "palette": copy.deepcopy(overrides.get("palette")),
            "icons": icons.get("theme"),
            "made": copy.deepcopy(made),
        }
        result["palette"] = made
        wanted = icons_for(theme, dark)
        if wanted is not None:
            icons["theme"] = wanted
        keep["made_icons"] = icons.get("theme")
        _put_icons(result, icons)
        return result, keep

    if own and overrides.get("palette") == own.get("made"):
        # The colours were not changed while in the other mode: the exact tweaks from before.
        if own.get("palette") is None:
            result.pop("palette", None)
        else:
            result["palette"] = copy.deepcopy(own["palette"])
        current = icons.get("theme")
        if current != own.get("made_icons", current):
            _icon_flavour(icons, theme, own_theme, dark)  # an icon theme picked in the other mode
        elif own.get("icons"):
            icons["theme"] = own["icons"]
        else:
            icons.pop("theme", None)
        _put_icons(result, icons)
        return result, None

    # Back to the preset's own palette, keeping an accent the user picked. An accent made by the
    # switch (from the preset's palette or the user's own) counts as untouched; only the one made
    # from the user's own accent brings that back (the preset's accent means they chose it since).
    untouched = {mode_variant(preset.palette.colors, not dark)["accent"], preset.palette.accent}
    made = (own or {}).get("made") if isinstance((own or {}).get("made"), dict) else {}
    if made.get("accent"):
        untouched.add(made["accent"])
    own_accent = ((own or {}).get("palette") or {}).get("accent") if own else None
    if colours["accent"] in untouched:
        if own_accent and colours["accent"] == made.get("accent", colours["accent"]):
            result["palette"] = {"accent": own_accent}
        else:
            result.pop("palette", None)
    else:
        result["palette"] = {"accent": ensure_contrast(colours["accent"], preset.palette.base, 3.0)}
    _icon_flavour(icons, theme, own_theme, dark)
    _put_icons(result, icons)
    return result, None


def way_back(own: Any) -> dict[str, Any] | None:
    """``own`` when it has the shape switch() gives it, else None (no way back): the record is
    a plain file, and a damaged one must never stop a switch."""
    if not isinstance(own, dict):
        return None
    for key in ("palette", "made"):
        value = own.get(key)
        if value is not None and not (
            isinstance(value, dict) and all(isinstance(k, str) and isinstance(v, str | None) for k, v in value.items())
        ):
            return None
    for key in ("icons", "made_icons"):
        if own.get(key) is not None and not isinstance(own[key], str):
            return None
    return own


def _icon_flavour(icons: dict[str, Any], theme: str | None, own_theme: str | None, dark: bool) -> None:
    """Point the icon tweaks at the flavour of ``theme`` for the mode; the preset's own icon
    theme (``own_theme``) needs no tweak."""
    wanted = icons_for(theme, dark)
    if wanted is not None and icons_for(own_theme, dark) == own_theme and icons_for(own_theme, not dark) == theme:
        wanted = own_theme  # its own flavour, e.g. Papirus-Light rather than Papirus
    if wanted is not None:
        if wanted == own_theme:
            icons.pop("theme", None)
        else:
            icons["theme"] = wanted


def _put_icons(result: dict[str, Any], icons: dict[str, Any]) -> None:
    if icons:
        result["icons"] = icons
    elif isinstance(result.get("icons"), dict) or "icons" not in result:
        result.pop("icons", None)


def carry(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Unapplied edits ``old`` with the colours and icon theme of the switched ``new``."""
    result = copy.deepcopy(old)
    if "palette" in new:
        result["palette"] = copy.deepcopy(new["palette"])
    else:
        result.pop("palette", None)
    icons = result.get("icons")
    if isinstance(icons, dict) or icons is None and "icons" not in result:
        icons = dict(icons or {})
        theme = (new.get("icons") or {}).get("theme")
        if theme:
            icons["theme"] = theme
        else:
            icons.pop("theme", None)
        if icons:
            result["icons"] = icons
        else:
            result.pop("icons", None)
    return result
