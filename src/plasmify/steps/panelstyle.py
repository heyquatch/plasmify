"""Panel look: corner radius, opacity and border.

Stock Plasma has no per-panel radius setting, so Plasmify writes a minimal Plasma
style that contains only a generated panel background (nine frame pieces plus
blur masks and margin hints) and inherits everything else from Breeze. No widget,
daemon or package is involved.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from plasmify import backup, glass
from plasmify.palette import Palette
from plasmify.panels import layout_panels, min_thickness
from plasmify.presets import Preset
from plasmify.steps.base import Context, Step
from plasmify.system import CONFIG_HOME, DATA_HOME, UnsafePathError, safe_child

THEME_ROOT = DATA_HOME / "plasma" / "desktoptheme"
# The Plasma style in use. The ones Plasmify generates are named theme_id(preset).
THEME_KEY = ("plasmarc", "Theme", "name")
GENERATED_PREFIX = "plasmify-"
# The panel_style values a generated style was made with, kept in its metadata.json, so "Save
# current desktop as preset" can save them with the user's own tweaks (see style_of).
STYLE_KEY = "X-Plasmify-PanelStyle"
MAX_METADATA_BYTES = 64 * 1024
# Piece sizes follow Breeze's panel background: 32 px edges and centre (tiled by
# Plasma, with hint-tile-center), corners as large as the radius. Borders are
# filled 1 px shapes rather than half-pixel strokes, so nothing is antialiased
# along the tile edges and semi-transparent panels show no seams.
TILE = 32


def _corner_fill(name: str, r: int) -> str:
    """The rounded corner area of an r×r piece, in the piece's own coordinates."""
    return {
        "topleft": f"M 0,{r} H {r} V 0 A {r},{r} 0 0 0 0,{r} Z",
        "topright": f"M 0,0 V {r} H {r} A {r},{r} 0 0 0 0,0 Z",
        "bottomleft": f"M {r},0 H 0 A {r},{r} 0 0 0 {r},{r} Z",
        "bottomright": f"M 0,0 H {r} A {r},{r} 0 0 1 0,{r} Z",
    }[name]


def _corner_ring(name: str, r: int) -> str:
    """A 1 px wide arc along the outside of the corner (filled, not stroked)."""
    i = r - 1
    return {
        "topleft": f"M {r},0 A {r},{r} 0 0 0 0,{r} H 1 A {i},{i} 0 0 1 {r},1 Z",
        "topright": f"M 0,0 A {r},{r} 0 0 1 {r},{r} H {i} A {i},{i} 0 0 0 0,1 Z",
        "bottomleft": f"M 0,0 A {r},{r} 0 0 0 {r},{r} V {i} A {i},{i} 0 0 1 1,0 Z",
        "bottomright": f"M {r},0 A {r},{r} 0 0 1 0,{r} V {i} A {i},{i} 0 0 0 {i},0 Z",
    }[name]


def panel_svg(palette: Palette, radius: int, opacity: float, border: str, padding: int, colour: str = "mantle") -> str:
    """The generated panel-background.svg."""
    rounded = radius > 1
    r = max(int(radius), 1)
    t = TILE
    fill = panel_colour(palette, colour)
    stroke = {"accent": palette.accent, "subtle": palette.overlay}.get(border)
    layout = {
        "topleft": (0, 0, r, r),
        "top": (r, 0, t, r),
        "topright": (r + t, 0, r, r),
        "left": (0, r, r, t),
        "center": (r, r, t, t),
        "right": (r + t, r, r, t),
        "bottomleft": (0, r + t, r, r),
        "bottom": (r, r + t, t, r),
        "bottomright": (r + t, r + t, r, r),
    }
    edge_line = {
        "top": f"M 0,0 H {t} V 1 H 0 Z",
        "bottom": f"M 0,{r - 1} H {t} V {r} H 0 Z",
        "left": f"M 0,0 H 1 V {t} H 0 Z",
        "right": f"M {r - 1},0 H {r} V {t} H {r - 1} Z",
    }
    parts: list[str] = []

    def group(element_id: str, x: int, y: int, shapes: list[str]) -> None:
        parts.append(f'<g id="{element_id}" transform="translate({x},{y})">' + "".join(shapes) + "</g>")

    for offset_x, prefix, colour, alpha, outline in (
        (0, "", fill, round(min(opacity, 1.0), 3), True),
        (2 * r + t + 20, "mask-", "#000000", 1.0, False),  # blur/clip mask: same shape, opaque
    ):
        for name, (x, y, w, h) in layout.items():
            corner = name in ("topleft", "topright", "bottomleft", "bottomright")
            if corner and rounded:
                shapes = [f'<path d="{_corner_fill(name, r)}" fill="{colour}" fill-opacity="{alpha}"/>']
            else:
                shapes = [f'<rect x="0" y="0" width="{w}" height="{h}" fill="{colour}" fill-opacity="{alpha}"/>']
            if outline and stroke:
                if corner and rounded:
                    shapes.append(f'<path d="{_corner_ring(name, r)}" fill="{stroke}" fill-opacity="0.75"/>')
                elif name in edge_line:
                    shapes.append(f'<path d="{edge_line[name]}" fill="{stroke}" fill-opacity="0.75"/>')
                elif corner:  # square corners
                    shapes.append(f'<rect x="0" y="0" width="{w}" height="{h}" fill="{stroke}" fill-opacity="0.75"/>')
            group(prefix + name, offset_x + x, y, shapes)

    hints_x = 2 * (2 * r + t + 20)
    pad = max(int(padding), 0) or 0.0001
    parts += [
        f'<rect id="hint-tile-center" x="{hints_x}" y="0" width="5" height="5" fill="none"/>',
        f'<rect id="hint-top-margin" x="{hints_x + 10}" y="0" width="4" height="{pad}" fill="none"/>',
        f'<rect id="hint-bottom-margin" x="{hints_x + 20}" y="0" width="4" height="{pad}" fill="none"/>',
        f'<rect id="hint-left-margin" x="{hints_x + 30}" y="0" width="{pad}" height="4" fill="none"/>',
        f'<rect id="hint-right-margin" x="{hints_x + 45}" y="0" width="{pad}" height="4" fill="none"/>',
    ]
    width, height = hints_x + 60, 2 * r + t
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">' + "".join(parts) + "</svg>\n"
    )


def theme_id(preset: Preset) -> str:
    return f"{GENERATED_PREFIX}{preset.id}"


def generated(name: str | None) -> bool:
    """Whether ``name`` is a Plasma style Plasmify generated (see theme_id)."""
    return bool(name) and name.startswith(GENERATED_PREFIX)


# A preset without [panel_style] leaves the panels to the user's own Plasma style, as the preset
# reference promises for every section left out. A style the user picked (any name Plasmify does
# not generate) stays as it is. A style Plasmify generated is the last preset's leftover, not the
# user's choice, so the one from the pinned "Your setup before Plasmify" backup comes back (as
# effects.own_values does for tiling and the title bar), or the default (the distribution's, else
# Breeze, in the generated colours) when that backup has none. Breeze every time would replace a style the user
# chose themselves, and Plasmify's panel style with default values would give every preset
# generated panels, so leaving the section out would mean nothing. The colour step runs this, as
# it runs for every preset: this step, its "What to apply" box and the light/dark switch stay
# tied to [panel_style], with no extra shell restart. The window draws such panels in the palette
# with Breeze's corners and says they keep the user's own style (PANEL_LOOK_NOTE, gui/customize.py).
def own_style() -> str | None:
    """The Plasma style the user had before Plasmify, from the pinned backup, or None for KDE's
    default: they set none, the backup does not hold plasmarc (or cannot be read), or the style
    there is one Plasmify generated as well.

    Unlike effects.own_values this needs no backup.is_original check: Plasmify sets no style but
    the ones it generates and the user's own, so any other name, also in a backup from an earlier
    Plasmify version, is one the user picked."""
    try:
        pinned = next((b for b in backup.list_backups(backup.BACKUP_ROOT) if b.pinned), None)
        text = backup.stored_text(pinned, CONFIG_HOME / THEME_KEY[0]) if pinned else None
    except (OSError, KeyError, TypeError, ValueError):  # a damaged manifest never stops the apply
        return None
    name = glass.get_key(text, *THEME_KEY[1:]) if text else None
    return name if name and not generated(name) else None


def keep_own_style(ctx: Context) -> None:
    """The Plasma style for a preset without a panel style: the user's own (see above)."""
    if not generated(ctx.runner.kread(*THEME_KEY)):
        return  # the user's own style, or KDE's default: left as it is
    own = own_style()
    if own is None:
        ctx.log("Putting the default Plasma style back on the panels")
        ctx.runner.krevert(CONFIG_HOME / THEME_KEY[0], *THEME_KEY[1:])  # the distribution's style shows again
    else:
        ctx.log(f"Putting your own Plasma style back on the panels ({own})")
        ctx.runner.kwrite(*THEME_KEY, own)


def metadata(preset: Preset, style: dict[str, Any] | None = None) -> str:
    """The style's metadata.json; ``style`` is the panel_style it was made with."""
    data: dict[str, Any] = {
        "KPlugin": {
            "Id": theme_id(preset),
            "Name": f"Plasmify {preset.name}",
            "Description": "Panel style generated by Plasmify; everything else comes from Breeze.",
            "License": "MIT",
            "Version": "1",
        },
        "X-Plasma-API": "5.0",
    }
    if style:
        data[STYLE_KEY] = style
    return json.dumps(data, indent=2)


def style_of(name: str | None) -> dict[str, Any] | None:
    """The panel_style values the generated Plasma style ``name`` was made with, or None: not one
    Plasmify generated, gone, or made before Plasmify kept them. The caller validates them."""
    if not generated(name):
        return None
    try:
        path = safe_child(THEME_ROOT, name) / "metadata.json"
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_METADATA_BYTES:
            return None
        data = json.loads(path.read_text(errors="replace"))
    except (OSError, UnsafePathError, ValueError):
        return None
    style = data.get(STYLE_KEY) if isinstance(data, dict) else None
    return dict(style) if isinstance(style, dict) else None


def max_radius(layout: dict) -> int:
    """Just under half the thinnest panel. Larger corners would leave no straight middle band,
    and Plasma would squeeze them (visible as seams)."""
    if not layout:
        return 14
    return max(min_thickness(layout_panels(layout)) // 2 - 2, 1)


def panel_colour(palette: Palette, choice: str) -> str:
    """A palette colour name or a fixed #rrggbb colour."""
    return palette.colors.get(choice, choice) if not choice.startswith("#") else choice


class PanelStyleStep(Step):
    id = "panelstyle"
    title = "Panel style"
    description = "Rounded corners, transparency and an optional border for panels and the dock."

    def applies_to(self, preset: Preset) -> bool:
        return bool(preset.section("panel_style"))

    def touched_paths(self, preset: Preset) -> list[Path]:
        # Plasma re-fits panel thickness to the new margins and saves it in plasmashellrc.
        return [CONFIG_HOME / "plasmarc", CONFIG_HOME / "plasmashellrc", safe_child(THEME_ROOT, theme_id(preset))]

    def apply(self, ctx: Context) -> None:
        style = ctx.preset.section("panel_style")
        radius = int(style.get("radius", 6))  # the Customize tab and preview assume 6 px too
        limit = max_radius(ctx.preset.section("layout"))
        if radius > limit:
            ctx.log(f"Corner radius {radius}px is more than half the panel height; using {limit}px")
            radius = limit
        opacity = float(style.get("opacity", 0.85))
        border = style.get("border", "none")
        padding = int(style.get("padding", 4))
        colour = style.get("colour", "mantle")
        name = theme_id(ctx.preset)
        ctx.log(f"Writing panel style (radius {radius}px, opacity {int(opacity * 100)}%, border {border})")
        if not ctx.dry_run:
            root = safe_child(THEME_ROOT, name)
            if root.exists():
                shutil.rmtree(root)
            # "solid" is what Plasma shows while a window touches the panel or is maximised
            # (adaptive opacity; with tiling that is nearly always). Without it Plasma falls
            # back to Breeze's solid panel and the chosen colour seems to disappear.
            variants = (("", opacity), ("translucent", opacity * 0.85), ("opaque", 1.0), ("solid", 1.0))
            for variant, alpha in variants:
                folder = root / variant / "widgets" if variant else root / "widgets"
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "panel-background.svg").write_text(
                    panel_svg(ctx.preset.palette, radius, min(alpha, 1.0), border, padding, colour)
                )
            used = {"radius": radius, "opacity": opacity, "border": border, "padding": padding, "colour": colour}
            (root / "metadata.json").write_text(metadata(ctx.preset, used))
            (root / "plasmarc").write_text("[AdaptiveTransparency]\nenabled=true\n")
            ctx.record(root)
        ctx.runner.kwrite(*THEME_KEY, name)
