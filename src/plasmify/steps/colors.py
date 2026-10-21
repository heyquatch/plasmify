"""Colour scheme, window decoration and Plasma style.

The KDE colour scheme is generated from the preset palette, so every preset
gets a consistent Breeze-based look without downloading anything.
"""

from __future__ import annotations

from pathlib import Path

from plasmify.palette import Palette, contrast, kde_rgb, lighten, mix, relative_luminance
from plasmify.presets import Preset, scheme_key
from plasmify.steps.base import Context, Step
from plasmify.steps.effects import DECORATION, WINDOW_BORDERS, checked
from plasmify.steps.panelstyle import keep_own_style
from plasmify.steps.splash import keep_own_splash
from plasmify.system import CONFIG_HOME, DATA_HOME, CommandError, Runner

# Stands in for the active scheme's name for a moment, so the same scheme can be applied again.
REAPPLY_PLACEHOLDER = "PlasmifyReapplying"
# What Plasma takes for the active scheme when kdeglobals names none.
DEFAULT_SCHEME = "BreezeLight"


def scheme_name(preset: Preset) -> str:
    return scheme_key(preset.id)


def apply_scheme(run: Runner, name: str) -> None:
    """Apply the colour scheme ``name`` to the running desktop, also when it is the active one
    already (its file was rewritten, or a restore put kdeglobals back).

    plasma-apply-colorscheme does nothing for the scheme kdeglobals names, so that name is
    replaced by a placeholder first. Applying another scheme first instead (Breeze Dark, as
    before) flashed its colours across every open window on the way."""
    active = run.kread("kdeglobals", "General", "ColorScheme") or DEFAULT_SCHEME
    again = active == name
    if again:
        run.kwrite("kdeglobals", "General", "ColorScheme", REAPPLY_PLACEHOLDER)
    try:
        run.run(["plasma-apply-colorscheme", name])
    except CommandError:
        if again:  # never leave the placeholder behind
            run.kwrite("kdeglobals", "General", "ColorScheme", name)


def color_scheme(palette: Palette, name: str, display_name: str) -> str:
    """Build a complete KDE ``.colors`` file from ``palette``."""
    p = palette
    common = {
        "ForegroundNormal": p.text,
        "ForegroundInactive": p.subtext,
        "ForegroundActive": p.orange,
        "ForegroundLink": p.blue,
        "ForegroundVisited": p.magenta,
        "ForegroundNegative": p.red,
        "ForegroundNeutral": p.yellow,
        "ForegroundPositive": p.green,
        "DecorationFocus": p.accent,
        "DecorationHover": p.accent,
    }
    sets = _dark_sets(p) if p.dark else _light_sets(p)
    return _scheme_text(p, common, sets, name, display_name)


def _light_sets(p: Palette) -> dict[str, dict[str, str]]:
    """Light palettes the way light themes look: text fields and lists brighter than the
    window, buttons just above it, and dark complementary areas (like Breeze Light)."""
    lightest = max((p.crust, p.mantle, p.base), key=relative_luminance)
    view = lightest if relative_luminance(lightest) > relative_luminance(p.base) + 0.02 else lighten(p.base, 0.6)
    button = mix(p.base, view, 0.6)
    on_accent = max((view, p.text), key=lambda c: contrast(c, p.accent))
    dark_area = mix(p.text, p.base, 0.08)
    return {
        "Window": {"BackgroundNormal": p.base, "BackgroundAlternate": p.mantle},
        "View": {"BackgroundNormal": view, "BackgroundAlternate": mix(view, p.base, 0.5)},
        "Button": {"BackgroundNormal": button, "BackgroundAlternate": mix(button, p.mantle, 0.5)},
        "Header": {"BackgroundNormal": p.mantle, "BackgroundAlternate": p.base},
        "Header][Inactive": {"BackgroundNormal": p.base, "BackgroundAlternate": p.mantle},
        "Tooltip": {"BackgroundNormal": view, "BackgroundAlternate": p.base},
        "Complementary": {
            "BackgroundNormal": dark_area,
            "BackgroundAlternate": mix(dark_area, p.base, 0.12),
            "ForegroundNormal": view,
            "ForegroundInactive": mix(view, dark_area, 0.35),
        },
        "Selection": {
            "BackgroundNormal": p.accent,
            "BackgroundAlternate": mix(p.accent, p.base, 0.35),
            "ForegroundNormal": on_accent,
            "ForegroundInactive": on_accent,
            "ForegroundActive": on_accent,
        },
    }


def _dark_sets(p: Palette) -> dict[str, dict[str, str]]:
    on_accent = max((p.crust, p.text), key=lambda c: contrast(c, p.accent))
    return {
        "Window": {"BackgroundNormal": p.base, "BackgroundAlternate": p.mantle},
        "View": {"BackgroundNormal": p.mantle, "BackgroundAlternate": mix(p.mantle, p.base, 0.5)},
        "Button": {"BackgroundNormal": p.surface, "BackgroundAlternate": mix(p.surface, p.base, 0.4)},
        "Header": {"BackgroundNormal": p.mantle, "BackgroundAlternate": p.base},
        "Header][Inactive": {"BackgroundNormal": p.crust, "BackgroundAlternate": p.mantle},
        "Tooltip": {"BackgroundNormal": p.crust, "BackgroundAlternate": p.mantle},
        "Complementary": {"BackgroundNormal": p.crust, "BackgroundAlternate": p.mantle},
        "Selection": {
            "BackgroundNormal": p.accent,
            "BackgroundAlternate": mix(p.accent, p.base, 0.35),
            "ForegroundNormal": on_accent,
            "ForegroundInactive": p.mantle if on_accent == p.crust else on_accent,
            "ForegroundActive": on_accent,
        },
    }


def _scheme_text(p: Palette, common: dict, sets: dict, name: str, display_name: str) -> str:
    lines = [
        "[ColorEffects:Disabled]",
        "Color=56,56,56",
        "ColorAmount=0",
        "ColorEffect=0",
        "ContrastAmount=0.65",
        "ContrastEffect=1",
        "IntensityAmount=0.1",
        "IntensityEffect=2",
        "",
        "[ColorEffects:Inactive]",
        "ChangeSelectionColor=true",
        f"Color={kde_rgb(p.crust)}",
        "ColorAmount=0.025",
        "ColorEffect=2",
        "ContrastAmount=0.1",
        "ContrastEffect=2",
        "Enable=false",
        "IntensityAmount=0",
        "IntensityEffect=0",
        "",
    ]
    for group, values in sets.items():
        lines.append(f"[Colors:{group}]")
        merged = {**common, **values}
        lines += [f"{key}={kde_rgb(value)}" for key, value in sorted(merged.items())]
        lines.append("")
    lines += [
        "[General]",
        f"ColorScheme={name}",
        f"Name={display_name}",
        "shadeSortColumn=true",
        "",
        "[KDE]",
        "contrast=4",
        "",
        "[WM]",
        f"activeBackground={kde_rgb(p.mantle)}",  # matches Header, which Breeze title bars use
        f"activeBlend={kde_rgb(p.text)}",
        f"activeForeground={kde_rgb(p.text)}",
        f"inactiveBackground={kde_rgb(p.crust)}",
        f"inactiveBlend={kde_rgb(p.overlay)}",
        f"inactiveForeground={kde_rgb(p.overlay)}",
        "",
    ]
    return "\n".join(lines)


def border_size(ctx: Context) -> None:
    """The preset's own ``window_borders``; a preset that leaves it out leaves the border size as
    it is here.

    The light/dark switch and colour changes run this step without the effects step. The size
    then on screen is the user's: the one a full apply put back from before Plasmify (see
    effects.OWN_SETTINGS), or one they picked in System Settings since, which a colour change
    must not undo. This step used to write None (no borders) for every preset that leaves the
    size out (all built-in ones do), and then the size from before Plasmify."""
    borders = checked(ctx, ctx.preset.section("effects"), "window_borders")
    if borders is not None:
        ctx.runner.kwrite("kwinrc", DECORATION, "BorderSizeAuto", False)
        ctx.runner.kwrite("kwinrc", DECORATION, "BorderSize", WINDOW_BORDERS[borders])


class ColorsStep(Step):
    id = "colors"
    title = "Colours"
    description = "Colour scheme generated from the palette, Breeze window frames and Plasma style."

    def scheme_path(self, preset: Preset) -> Path:
        return DATA_HOME / "color-schemes" / f"{scheme_name(preset)}.colors"

    def touched_paths(self, preset: Preset) -> list[Path]:
        return [
            CONFIG_HOME / "kdeglobals",
            CONFIG_HOME / "kwinrc",
            CONFIG_HOME / "plasmarc",
            CONFIG_HOME / "ksplashrc",  # keep_own_splash for presets without a splash
            self.scheme_path(preset),  # re-applying with other colours rewrites the same file
        ]

    def apply(self, ctx: Context) -> None:
        preset, run = ctx.preset, ctx.runner
        name = scheme_name(preset)
        path = self.scheme_path(preset)
        ctx.log(f"Writing colour scheme {name}")
        if not ctx.dry_run:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(color_scheme(preset.palette, name, f"Plasmify {preset.name}"))
            ctx.record(path)

        run.kwrite("kdeglobals", "General", "AccentColorFromWallpaper", False)
        apply_scheme(run, name)  # also when re-applying the same preset after its file changed

        # Breeze decorations follow the colour scheme.
        run.kwrite("kwinrc", "org.kde.kdecoration2", "library", "org.kde.breeze")
        run.kwrite("kwinrc", "org.kde.kdecoration2", "theme", "Breeze")
        border_size(ctx)
        if not preset.section("panel_style"):  # otherwise the panel style step sets its own style
            keep_own_style(ctx)  # the user's own Plasma style, never the last preset's
        if not preset.section("splash"):  # otherwise the splash step writes its own
            keep_own_splash(ctx)  # the user's own splash screen, never the last preset's
        run.kwin_reconfigure()
