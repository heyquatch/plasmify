"""Combinations that work, but not the way people expect, and how to fix them.

Plasmify never refuses a valid preset. Instead the Customize tab shows these hints,
each with a one-click fix where one exists. The rules are pure functions of the
(customized) preset, so they are easy to test.
"""

from __future__ import annotations

from dataclasses import dataclass

from plasmify.panels import layout_panels, min_thickness
from plasmify.presets import Preset
from plasmify.steps.shell import logo_kind

LAUNCHERS = {"launcher", "menu", "dashboard"}


@dataclass(frozen=True)
class Advice:
    id: str
    text: str
    fix: str = ""  # button label; empty when there is nothing to click
    level: str = "info"  # info or warning


def check(preset: Preset, tiling_now: bool = False) -> list[Advice]:
    """Hints for ``preset``. ``tiling_now`` is whether tiling is on after the apply when the
    preset itself leaves tiling out (see ``CustomizePanel.tiling_now``)."""
    advice: list[Advice] = []
    layout = preset.section("layout")
    panels = layout_panels(layout) if layout else []
    effects = preset.section("effects")
    tiling = effects.get("tiling", tiling_now)
    style = preset.section("panel_style")
    widgets = {w for p in panels for w in p["widgets"]}

    hidden = [p["edge"] for p in panels if p["hiding"] == "dodge"]
    if tiling and hidden:
        advice.append(
            Advice(
                "tiling-hidden",
                f"With tiling, windows always touch the {' and '.join(hidden)} panel, so it stays hidden "
                "until you push the mouse to that screen edge.",
                "Keep it visible",
                "warning",
            )
        )
    radius = int(style.get("radius", 6)) if style else 0  # every panel_style key is optional
    if panels and style and radius > 1:
        cap = max(min_thickness(panels) // 2 - 2, 1)
        if radius > cap:
            advice.append(
                Advice(
                    "radius-capped",
                    f"Corners are limited to {cap} px because the thinnest panel is {min_thickness(panels)} px.",
                    "Make panels thicker",
                )
            )
        if tiling and any(p["floating"] and p["hiding"] == "always" for p in panels):
            advice.append(
                Advice(
                    "tiling-corners",
                    "Plasma attaches panels to the screen edge while windows touch them, and with tiling they "
                    "always do: rounded corners then show only on an empty desktop.",
                )
            )
    if panels and not widgets & LAUNCHERS:
        advice.append(
            Advice(
                "no-launcher",
                "No app launcher in any panel. Apps still open with Alt+Space or the Meta key.",
                "Add a launcher",
            )
        )
    if panels and "tray" not in widgets:
        advice.append(
            Advice(
                "no-tray",
                "No system tray: network, sound, battery and notification icons will not be visible.",
                "Add the tray",
                "warning",
            )
        )
    if any(p["edge"] in ("left", "right") and "tasks_labels" in p["widgets"] for p in panels):
        advice.append(
            Advice("labels-vertical", "Window titles need a lot of room on a side panel.", "Use icons instead")
        )
    if style and float(style.get("opacity", 1.0)) < 0.5 and not effects.get("blur", True):
        advice.append(
            Advice(
                "clear-without-blur",
                "Very see-through panels without blur can make their text hard to read.",
                "Turn on blur",
            )
        )
    shell = preset.section("shell")
    if shell and not shell.get("greeting", True) and logo_kind(shell) != "distro":
        advice.append(
            Advice(
                "logo-hidden",
                "The terminal logo only appears with the system info greeting, which is off.",
                "Show the greeting",
            )
        )
    splash = preset.section("splash")
    if splash.get("background") == "wallpaper" and not preset.section("wallpaper").get("images"):
        advice.append(
            Advice("splash-no-wallpaper", "This preset keeps your wallpaper, so the splash uses the palette colour.")
        )
    return advice
