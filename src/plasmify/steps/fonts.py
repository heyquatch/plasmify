"""Interface and monospace fonts."""

from __future__ import annotations

from pathlib import Path

from plasmify.presets import Preset
from plasmify.steps.base import Context, Step
from plasmify.system import CONFIG_HOME


def qfont(family: str, size: int, weight: int = 400) -> str:
    """Serialise a font the way Qt 6 stores it in kdeglobals."""
    return f"{family},{size},-1,5,{weight},0,0,0,0,0,0,0,0,0,0,1"


class FontsStep(Step):
    id = "fonts"
    title = "Fonts"
    description = "Interface and terminal fonts, from your distribution or downloaded if it does not package them."

    def applies_to(self, preset: Preset) -> bool:
        return bool(preset.section("fonts"))

    def packages(self, preset: Preset) -> list[str]:
        return list(preset.section("fonts").get("packages", []))

    def touched_paths(self, preset: Preset) -> list[Path]:
        return [CONFIG_HOME / "kdeglobals"]

    def apply(self, ctx: Context) -> None:
        fonts = ctx.preset.section("fonts")
        ui, mono = fonts["ui"], fonts["mono"]
        ui_size, mono_size = int(fonts.get("ui_size", 10)), int(fonts.get("mono_size", 10))
        ctx.log(f"Setting fonts: {ui} {ui_size} / {mono} {mono_size}")
        if not ctx.dry_run:
            ctx.runner.run(["fc-cache"], check=False)
        w = ctx.runner.kwrite
        w("kdeglobals", "General", "font", qfont(ui, ui_size))
        w("kdeglobals", "General", "menuFont", qfont(ui, ui_size))
        w("kdeglobals", "General", "toolBarFont", qfont(ui, max(ui_size - 1, 8)))
        w("kdeglobals", "General", "smallestReadableFont", qfont(ui, max(ui_size - 2, 7)))
        w("kdeglobals", "WM", "activeFont", qfont(ui, ui_size, 600))
        w("kdeglobals", "General", "fixed", qfont(mono, mono_size))
