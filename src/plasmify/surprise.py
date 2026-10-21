"""Surprise me: a random but coherent design on top of any preset.

Every suggestion is a set of overrides that passes the schema and raises no warning-level
hint (see ``plasmify.compat``), so a surprise never breaks the desktop. Users keep or
change anything afterwards, like any other customization.
"""

from __future__ import annotations

import random
from typing import Any

from plasmify import asciiart, compat, panels, presets, schema

ACCENT_KEYS = ("red", "orange", "yellow", "green", "cyan", "blue", "magenta")
PANEL_COLOURS = ("mantle", "base", "crust", "surface")
FETCH_EXTRAS = ("host", "packages", "display", "terminal", "disk", "localip", "datetime", "media")
# Exact fastfetch names (they are case sensitive).
BUILTIN_LOGOS = ("arch_small", "Debian", "Fedora", "opensuse", "ubuntu", "Gentoo", "NixOS", "void")


def _panels(rng: random.Random) -> list[dict[str, Any]]:
    layout = panels.starter(rng.choice(list(panels.STARTERS)))
    for panel in layout:
        if panel["style"] == "bar" and rng.random() < 0.35:
            panel["length"] = "fit"  # an island
        if panel["edge"] in ("top", "bottom"):
            bar_like = panel["style"] in ("bar", "islands")
            panel["thickness"] = rng.choice((28, 32, 36, 40)) if bar_like else rng.choice((48, 56, 64))
        if panel["style"] == "bar" and rng.random() < 0.3 and "media" not in panel["widgets"]:
            panel["widgets"].insert(max(len(panel["widgets"]) - 2, 0), rng.choice(("media", "cpu", "pager")))
    # Docks that hide behave unexpectedly with tiling; surprises always stay visible.
    for panel in layout:
        if panel["hiding"] == "dodge":
            panel["hiding"] = "always"
    return layout


def suggest(preset: presets.Preset, seed: int | None = None, tiling_now: bool = False) -> dict[str, Any]:
    """Overrides for ``preset``. The same seed always gives the same surprise."""
    rng = random.Random(seed)  # noqa: S311 (a design suggestion, not security)
    for _attempt in range(40):
        overrides = _one(preset, rng)
        try:
            candidate = presets.customized(preset, overrides)
        except presets.PresetError:
            continue
        if not [a for a in compat.check(candidate, tiling_now) if a.level == "warning"]:
            return overrides
    return {}


def _one(preset: presets.Preset, rng: random.Random) -> dict[str, Any]:
    layout = _panels(rng)
    cap = max(panels.min_thickness(layout) // 2 - 2, 1)
    colours = preset.palette.colors
    accent_key = rng.choice(ACCENT_KEYS)
    overrides: dict[str, Any] = {
        "layout": {"panels": panels.compact(layout)},
        "panel_style": {
            "radius": rng.randint(0, cap),
            "opacity": round(rng.uniform(0.7, 1.0), 2),
            "border": rng.choice(("none", "none", "subtle", "accent")),
            "colour": rng.choice(PANEL_COLOURS),
        },
        "effects": {
            "open_close": rng.choice(schema.OPEN_CLOSE_EFFECTS[:3]),
            "minimize": rng.choice(schema.MINIMIZE_EFFECTS[:2]),
            "desktop_switch": rng.choice(schema.DESKTOP_SWITCH_EFFECTS[:2]),
            "wobbly": rng.random() < 0.15,
            "blur": True,
            "blur_strength": rng.randint(6, 14),
            "titlebar_buttons": rng.choice(("right", "right", "left", "close")),
        },
        "shell": {"greeting": True, **_logo(rng), "fastfetch_modules": _modules(rng)},
        "splash": {
            "style": "plasmify",
            "background": rng.choice(("palette", "wallpaper")),
            "progress": True,
            "text": rng.choice(("Welcome back", "Hello again", "Let's go", "Ready when you are")),
        },  # fmt: skip
    }
    if colours.get(accent_key) and colours[accent_key] != colours.get("accent"):
        overrides["palette"] = {"accent": colours[accent_key]}
    if not preset.section("wallpaper").get("images"):
        overrides["splash"]["background"] = "palette"
    return overrides


def _logo(rng: random.Random) -> dict[str, Any]:
    kind = rng.choice(("distro", "ascii", "ascii", "builtin", "text"))
    # Mostly the multi-colour schemes: they are the most fun to discover.
    colour = rng.choice(schema.LOGO_SCHEMES * 2 + schema.LOGO_SINGLE)
    paint: dict[str, Any] = {"logo_colour": colour}
    if colour in schema.LOGO_SCHEMES:
        paint["logo_direction"] = rng.choice(schema.LOGO_DIRECTIONS)
    if kind == "ascii":
        return {"logo": "ascii", "logo_ascii": asciiart.sample(rng.choice(list(asciiart.SAMPLES))), **paint}
    if kind == "builtin":
        return {"logo": "builtin", "logo_builtin": rng.choice(BUILTIN_LOGOS), **paint}
    if kind == "text":
        return {"logo": "text", "logo_text": rng.choice(("HELLO", "RICE", "KDE", "HACK", "CHILL")), **paint}
    return {"logo": "distro", **(paint if rng.random() < 0.5 else {"logo_colour": "original"})}


def _modules(rng: random.Random) -> list[str]:
    core = ["os", "kernel", "shell", "cpu", "gpu", "memory", "uptime"]
    chosen = [m for m in core if rng.random() < 0.85] or ["os"]
    chosen += rng.sample(FETCH_EXTRAS, rng.randint(0, 3))
    return ["break", *chosen, "break", "colors"]
