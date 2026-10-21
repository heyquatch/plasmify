"""Cursor theme downloaded from the author's release page."""

from __future__ import annotations

import shutil
from pathlib import Path

from plasmify import downloads
from plasmify.presets import Preset
from plasmify.steps.base import Context, Step
from plasmify.system import CONFIG_HOME, DATA_HOME, safe_child


def find_cursor_dir(root: Path, name: str) -> Path | None:
    """Locate the extracted cursor theme: a directory with ``cursors/`` inside."""
    candidates = [p.parent for p in root.rglob("cursors") if p.is_dir()]
    for candidate in candidates:
        if candidate.name == name:
            return candidate
    return candidates[0] if len(candidates) == 1 else None


ICON_DIRS = (DATA_HOME / "icons", Path.home() / ".icons", Path("/usr/share/icons"), Path("/usr/local/share/icons"))


def installed_cursor(name: str) -> Path | None:
    for root in ICON_DIRS:
        if (root / name / "cursors").is_dir():
            return root / name
    return None


def installed_cursor_themes() -> list[str]:
    """Cursor themes available on this computer, for the Customize tab."""
    names = set()
    for root in ICON_DIRS:
        if root.is_dir():
            names |= {p.name for p in root.iterdir() if (p / "cursors").is_dir()}
    return sorted(names, key=str.lower)


class CursorStep(Step):
    id = "cursor"
    title = "Cursor"
    description = "A mouse cursor that matches the palette."

    def applies_to(self, preset: Preset) -> bool:
        return bool(preset.section("cursor"))

    def touched_paths(self, preset: Preset) -> list[Path]:
        return [CONFIG_HOME / "kcminputrc"]

    def apply(self, ctx: Context) -> None:
        cursor = ctx.preset.section("cursor")
        name, size = cursor["name"], int(cursor.get("size", 24))
        target = safe_child(DATA_HOME / "icons", name)
        if "url" not in cursor:
            if not ctx.dry_run and installed_cursor(name) is None:
                ctx.warn(f"Cursor theme '{name}' is not installed on this computer; keeping the current cursor")
                return
        elif not ctx.dry_run and installed_cursor(name) is None:
            ctx.log(f"Installing cursor theme {name}")
            archive = downloads.fetch(cursor["url"], ctx.workdir / Path(cursor["url"]).name)
            extracted = downloads.extract(archive, ctx.workdir / "cursor")
            found = find_cursor_dir(extracted, name)
            if found is None:
                ctx.warn(f"Cursor theme '{name}' was not found in {cursor['url']}")
                return
            if target.exists() or target.is_symlink():
                # Not a cursor theme (installed_cursor found none), so it is something else the
                # user keeps here, for example an icon theme with the same name. Never delete it.
                ctx.warn(f"{target} already exists and is not a cursor theme; it was left alone")
                return
            shutil.copytree(found, target, symlinks=True)
            ctx.record(target)

        ctx.runner.kwrite("kcminputrc", "Mouse", "cursorTheme", name)
        ctx.runner.kwrite("kcminputrc", "Mouse", "cursorSize", size)
        ctx.runner.run(["plasma-apply-cursortheme", "--size", str(size), name], check=False)
