"""Application style: Kvantum when the preset ships a theme for it, Breeze otherwise.

With ``[style] glass`` app windows turn see-through through Kvantum, in one of a few looks and
as see-through as ``[style] transparency`` says, the window in use solid or not as
``[style] focused_solid`` says (see ``plasmify.glass``): a preset's Kvantum theme gets a glass
copy, and a Breeze preset gets a Kvantum theme made from its palette.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from plasmify import downloads, glass
from plasmify.presets import Preset
from plasmify.steps.base import Context, Step
from plasmify.system import CONFIG_HOME, safe_child

KVANTUM_DIR = CONFIG_HOME / "Kvantum"
KWIN_OUTPUTS = CONFIG_HOME / "kwinoutputconfig.json"


def sets_blur(preset: Preset) -> bool:
    """Whether this step sets KWin's blur: a blurred glass look on a preset without effects,
    where the effects step (which otherwise does it) does not run."""
    return glass.level_of(preset.data) in glass.BLURRED and not preset.section("effects")


def _links(folder: str, names: list[str]) -> list[str]:
    """Leave symlinks out of copied theme folders: a link could point anywhere."""
    return [name for name in names if (Path(folder) / name).is_symlink()]


def _sha256(path: Path) -> str:
    with path.open("rb") as fh:
        return hashlib.file_digest(fh, "sha256").hexdigest()


def _fetch_template(ctx: Context, template: str) -> Path | None:
    """Download the pinned Catppuccin template (about 160 KB) and check its checksums."""
    folder = ctx.workdir / "glass-template"
    folder.mkdir(parents=True, exist_ok=True)
    for extension in ("kvconfig", "svg"):
        name = f"{template}.{extension}"
        try:
            path = downloads.fetch(glass.template_url(template, extension), folder / name)
            matches = _sha256(path) == glass.TEMPLATE_SHA256[name]
        except (downloads.DownloadError, OSError) as exc:
            ctx.warn(f"Could not download the glass theme ({exc}); using Breeze instead")
            return None
        if not matches:
            path.unlink(missing_ok=True)
            ctx.warn("The glass theme download does not match the reviewed checksum; using Breeze instead")
            return None
    return folder


class StyleStep(Step):
    id = "style"
    title = "Application style"
    description = "Widget style for Qt apps: a matching Kvantum theme when available, Breeze otherwise."

    def packages(self, preset: Preset) -> list[str]:
        wanted = "kvantum" in preset.section("style") or glass.level_of(preset.data) != "off"
        return ["kvantum"] if wanted else []

    def touched_paths(self, preset: Preset) -> list[Path]:
        paths = [CONFIG_HOME / "kdeglobals", KVANTUM_DIR / "kvantum.kvconfig"]
        kvantum = preset.section("style").get("kvantum")
        if kvantum:  # a theme folder of the same name (maybe the user's own) is replaced
            paths.append(safe_child(KVANTUM_DIR, kvantum["name"]))
        if glass.level_of(preset.data) != "off":
            name = glass.glass_name(kvantum["name"]) if kvantum else glass.GENERATED_NAME
            paths.append(safe_child(KVANTUM_DIR, name))
        if sets_blur(preset):
            paths.append(CONFIG_HOME / "kwinrc")
        return paths

    def apply(self, ctx: Context) -> None:
        kvantum = ctx.preset.section("style").get("kvantum")
        level = glass.level_of(ctx.preset.data)
        transparency = glass.effective_transparency(ctx.preset.data, ctx.preset.palette.dark)
        focused = glass.effective_focused_solid(ctx.preset.data)
        if kvantum:
            theme = self._install_theme(ctx, kvantum)
            if level != "off":
                theme = self._glass_copy(ctx, kvantum["name"], level, transparency, focused)
            glassy = theme != kvantum["name"]
        elif level != "off":
            theme = self._palette_glass(ctx, level, transparency, focused)
            if theme is None:
                self._breeze(ctx)
                return
            glassy = True
        else:
            self._breeze(ctx)
            return

        was_kvantum = ctx.runner.kread("kdeglobals", "KDE", "widgetStyle").lower() == "kvantum"
        if not ctx.dry_run:
            (KVANTUM_DIR / "kvantum.kvconfig").write_text(f"[General]\ntheme={theme}\n")
        ctx.runner.kwrite("kdeglobals", "KDE", "widgetStyle", "kvantum")
        ctx.runner.notify_style_changed()
        if glassy and sets_blur(ctx.preset):
            self._blur(ctx, level)
        if glassy and glass.fractional_scale(KWIN_OUTPUTS):
            ctx.warn(
                "Your screen scale is not a whole number (like 125% or 150%), "
                "so Kvantum keeps windows solid there; glass shows at 100% or 200%"
            )
        if was_kvantum:
            # KDE gives open apps a new style only when the style's name changes, and Kvantum
            # reads its theme only when an app starts.
            ctx.log(
                "Apps that are open now keep their old Kvantum look until you restart them; Plasmify follows at once"
            )
        elif glassy:
            ctx.log("Apps that are open now turn to glass after you restart them")

    def _blur(self, ctx: Context, level: str) -> None:
        """Blur behind glass windows, in the look's strength. KWin's own default (15, the
        strongest) made the glass look as frosted as frosted."""
        run = ctx.runner
        current = {}
        for key, name in (("blur_strength", "BlurStrength"), ("blur_noise", "NoiseStrength")):
            value = run.kread("kwinrc", "Effect-blur", name)
            if value.isdigit():
                current[key] = int(value)
        values = glass.kwin_blur(level, current)
        ctx.log(f"Setting the blur behind windows to strength {values['blur_strength']}")
        run.kwrite("kwinrc", "Plugins", "blurEnabled", True)
        run.kwrite("kwinrc", "Effect-blur", "BlurStrength", values["blur_strength"])
        run.kwrite("kwinrc", "Effect-blur", "NoiseStrength", values["blur_noise"])
        run.kwin_reconfigure()
        run.kwin_reconfigure_effect("blur")

    def _breeze(self, ctx: Context) -> None:
        ctx.log("Using the Breeze application style")
        ctx.runner.kwrite("kdeglobals", "KDE", "widgetStyle", "Breeze")
        ctx.runner.notify_style_changed()

    def _install_theme(self, ctx: Context, kvantum: dict) -> str:
        """Download the preset's Kvantum theme into ``~/.config/Kvantum`` and return its name."""
        name = kvantum["name"]
        target = safe_child(KVANTUM_DIR, name)
        ctx.log(f"Installing Kvantum theme {name}")
        if ctx.dry_run:
            return name
        if "url" in kvantum:
            # Some themes are published as a release archive instead of a folder.
            archive = downloads.fetch(kvantum["url"], ctx.workdir / Path(kvantum["url"]).name)
            extracted = downloads.extract(archive, ctx.workdir / "kvantum")
            source = next((p.parent for p in extracted.rglob(f"{name}.kvconfig") if not p.is_symlink()), extracted)
        else:
            source = downloads.sparse_checkout(
                kvantum["repo"], kvantum["path"], ctx.workdir / "kvantum", kvantum.get("ref")
            )
        existed = target.exists()
        if existed:
            shutil.rmtree(target)
        shutil.copytree(source, target, ignore=_links)
        if not existed:  # never claim a theme the user installed themselves
            ctx.record(target)
        return name

    def _glass_copy(self, ctx: Context, name: str, level: str, transparency: int, focused: bool) -> str:
        """Write ``<name>-glass`` next to the installed theme; returns the theme to use."""
        copy_dir = safe_child(KVANTUM_DIR, glass.glass_name(name))
        ctx.log(f"Making a glass copy of {name} ({glass.describe(level, transparency, focused)})")
        if ctx.dry_run:
            return copy_dir.name
        try:
            dark = ctx.preset.palette.dark
            glass.write_glass_copy(safe_child(KVANTUM_DIR, name), name, level, dark, transparency, focused)
        except glass.ForeignThemeError:
            ctx.warn(
                f"A Kvantum theme named {copy_dir.name} is already there and is not Plasmify's, "
                f"so it was left alone; using {name} without glass"
            )
            return name
        except (OSError, ValueError) as exc:
            ctx.warn(f"Could not make the glass copy of {name} ({exc}); using it without glass")
            return name
        ctx.record(copy_dir)  # always Plasmify's own: the folder carries its marker
        return copy_dir.name

    def _palette_glass(self, ctx: Context, level: str, transparency: int, focused: bool) -> str | None:
        """Make ``plasmify-glass`` from the palette; None when Breeze has to stay."""
        palette = ctx.preset.palette
        template = glass.TEMPLATE_DARK if palette.dark else glass.TEMPLATE_LIGHT
        ctx.log(
            f"Making a glass Kvantum theme in the preset's colours ({glass.describe(level, transparency, focused)}; "
            "template by Catppuccin)"
        )
        if ctx.dry_run:
            return glass.GENERATED_NAME
        folder = _fetch_template(ctx, template)
        if folder is None:
            return None
        try:
            target = glass.write_palette_theme(folder, template, KVANTUM_DIR, palette, level, transparency, focused)
        except glass.ForeignThemeError:
            ctx.warn(
                f"A Kvantum theme named {glass.GENERATED_NAME} is already there and is not Plasmify's, "
                "so it was left alone; using Breeze instead"
            )
            return None
        except (OSError, ValueError) as exc:
            ctx.warn(f"Could not make the glass theme ({exc}); using Breeze instead")
            return None
        ctx.record(target)
        return glass.GENERATED_NAME
