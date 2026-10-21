"""Icon theme with recoloured folders, without root access.

Instead of editing a system-wide Papirus install (which needs root and is undone
by every package update), Plasmify creates a tiny per-user overlay theme that
inherits from Papirus and only overrides the folder icons with symlinks.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from plasmify import downloads
from plasmify.presets import Preset
from plasmify.steps.base import Context, Step
from plasmify.system import CONFIG_HOME, DATA_HOME, libexec_tool, safe_child

PAPIRUS_SEARCH = (DATA_HOME / "icons", Path.home() / ".icons", Path("/usr/share/icons"), Path("/usr/local/share/icons"))


def installed_icon_themes() -> list[str]:
    """Icon themes (not cursor-only themes, not Plasmify overlays) installed on this computer."""
    names = set()
    for root in PAPIRUS_SEARCH:
        if not root.is_dir():
            continue
        for theme in root.iterdir():
            index = theme / "index.theme"
            if theme.name.startswith("Plasmify-") or theme.name in ("default", "hicolor") or not index.is_file():
                continue
            try:
                text = index.read_text(errors="replace")
            except OSError:
                continue
            if "Directories=" in text and "Hidden=true" not in text:
                names.add(theme.name)
    return sorted(names, key=str.lower)


FOLDER_SIZES = ("22x22", "24x24", "32x32", "48x48", "64x64")


def overlay_name(preset: Preset) -> str:
    return f"Plasmify-{preset.id}"


def find_theme(name: str) -> Path | None:
    for root in PAPIRUS_SEARCH:
        candidate = root / name
        if (candidate / "index.theme").exists():
            return candidate
    return None


def folder_links(places: Path, color: str) -> dict[str, Path]:
    """Map generic folder icon names to the coloured variants found in ``places``.

    ``folder-blue-documents.svg`` becomes the target of ``folder-documents.svg``,
    ``user-blue-home.svg`` of ``user-home.svg`` and so on.
    """
    pattern = re.compile(rf"^(folder|user)-{re.escape(color)}((?:-[\w-]+)?)\.svg$")
    links = {}
    if not places.is_dir():
        return links
    for icon in places.iterdir():
        match = pattern.match(icon.name)
        if match and not icon.is_symlink():
            links[f"{match.group(1)}{match.group(2)}.svg"] = icon
    return links


def index_theme(display_name: str, inherits: str) -> str:
    directories = []
    sections = []
    for size in FOLDER_SIZES:
        px = size.split("x")[0]
        for scale, suffix in ((1, ""), (2, "@2x")):
            directory = f"{size}{suffix}/places"
            directories.append(directory)
            sections += [
                f"[{directory}]",
                "Context=Places",
                f"Size={px}",
                f"Scale={scale}",
                "Type=Fixed",
                "",
            ]
    header = [
        "[Icon Theme]",
        f"Name={display_name}",
        "Comment=Folder colours by Plasmify",
        f"Inherits={inherits},breeze-dark,hicolor",
        f"Directories={','.join(directories)}",
        "",
    ]
    return "\n".join(header + sections)


class IconsStep(Step):
    id = "icons"
    title = "Icons"
    description = "Papirus icons with folders tinted to match the palette."

    def applies_to(self, preset: Preset) -> bool:
        return bool(preset.section("icons"))

    def packages(self, preset: Preset) -> list[str]:
        base = preset.section("icons").get("theme", "Papirus-Dark")
        if base.startswith("Papirus") and find_theme(base) is None:
            return ["papirus-icon-theme"]
        return []

    def touched_paths(self, preset: Preset) -> list[Path]:
        return [CONFIG_HOME / "kdeglobals", safe_child(DATA_HOME / "icons", overlay_name(preset))]

    def apply(self, ctx: Context) -> None:
        icons = ctx.preset.section("icons")
        base_name = icons["theme"]
        name = overlay_name(ctx.preset)
        color = icons.get("folder_color")
        if color and not base_name.startswith("Papirus"):
            ctx.warn(f"Folder colours only work with Papirus icons; using {base_name} as it is")
            color = None
        if not color:
            if not ctx.dry_run and find_theme(base_name) is None and not base_name.startswith("Papirus"):
                ctx.warn(f"Icon theme '{base_name}' is not installed; keeping the current icons")
                return
            ctx.runner.kwrite("kdeglobals", "Icons", "Theme", base_name)
            changeicons = libexec_tool("plasma-changeicons")
            if changeicons:
                ctx.runner.run([changeicons, base_name], check=False)
            return

        ctx.log(f"Building icon theme {name} ({base_name}, {color} folders)")
        if not ctx.dry_run:
            self._build_overlay(ctx, base_name, name, color, icons.get("folder_source"))

        ctx.runner.kwrite("kdeglobals", "Icons", "Theme", name)
        changeicons = libexec_tool("plasma-changeicons")
        if changeicons:
            ctx.runner.run([changeicons, name], check=False)

    def _build_overlay(self, ctx: Context, base_name: str, name: str, color: str, source: dict | None) -> None:
        base = find_theme(base_name)
        papirus = find_theme("Papirus")
        if base is None or papirus is None:
            ctx.warn(f"{base_name} is not installed; keeping the current folder icons")
            return

        overlay = safe_child(DATA_HOME / "icons", name)
        if overlay.exists():
            shutil.rmtree(overlay)
        overlay.mkdir(parents=True)

        extra = None
        if source:
            fetched = downloads.sparse_checkout(source["repo"], source["path"], ctx.workdir / "folders")
            extra = overlay / "colors"
            for svg in fetched.glob(f"*/places/*-{color}*.svg"):
                if svg.is_symlink() or not svg.is_file():
                    continue  # never follow a link out of the checkout
                target = extra / svg.relative_to(fetched)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(svg, target)

        linked = 0
        for size in FOLDER_SIZES:
            places = overlay / size / "places"
            places.mkdir(parents=True)
            links = folder_links(papirus / size / "places", color)
            if extra is not None:
                links.update(folder_links(extra / size / "places", color))
            for link_name, target in links.items():
                (places / link_name).symlink_to(target)
                linked += 1
            (overlay / f"{size}@2x").symlink_to(size)

        if linked == 0:
            ctx.warn(f"No '{color}' folder icons found; check icons.folder_color")
        ctx.record(overlay)
        (overlay / "index.theme").write_text(index_theme(f"Plasmify {ctx.preset.name}", base_name))
