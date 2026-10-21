"""Desktop and lock screen wallpapers, downloaded from where they are published."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse

from plasmify import downloads
from plasmify.presets import Preset
from plasmify.steps.base import Context, Step
from plasmify.system import CONFIG_HOME, PLASMIFY_DATA, safe_child

WALLPAPER_ROOT = PLASMIFY_DATA / "wallpapers"

# Plasma's FillMode for "Scaled and Cropped".
FILL_CROP = 2
# Plasma's SlideshowMode: random, or by file name (which starts with the position).
SLIDESHOW_MODES = {"shuffle": 0, "listed": 1}


IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".gif")
# Living (Shimmer) versions sit in a folder of their own, so the slideshow plays only them.
LIVING = "living"
# Making one takes a few seconds per picture: a long slideshow brings only its first ones to life
# (the rest play in the same slideshow, still).
SHIMMER_MAX = 6


def wallpaper_dir(preset: Preset) -> Path:
    return safe_child(WALLPAPER_ROOT, preset.id)


def plain_name(url: str) -> str:
    """The URL's file name reduced to plain characters."""
    base = re.sub(r"[^A-Za-z0-9._-]", "_", unquote(Path(urlparse(url).path).name)).lstrip(".") or "image"
    return base[:80]


def url_base(url: str) -> str:
    """The part of a download's file name that identifies its source: a short hash of the whole
    address (two artists can both call a picture wallpaper.jpg) and the address's file name."""
    return f"{hashlib.sha256(url.encode()).hexdigest()[:8]}-{plain_name(url)}"


def local_name(index: int, url: str) -> str:
    """A safe local file name: the place in the slideshow, then the source (see url_base)."""
    return f"{index:02d}-{url_base(url)}"


def _first(folder: Path, pattern: str) -> Path | None:
    return next((p for p in sorted(folder.glob(pattern)) if p.is_file() and not p.is_symlink()), None)


def cached_file(preset: Preset, image: dict) -> Path | None:
    """The local copy of a preset wallpaper, wherever it sits in the slideshow order."""
    if "file" in image:
        if preset.asset_dir is not None:
            path = safe_child(preset.asset_dir, image["file"])
            return path if path.is_file() else None
        return None
    folder = wallpaper_dir(preset)
    if not folder.is_dir():
        return None
    found = _first(folder, f"[0-9][0-9]-{url_base(image['url'])}")
    if found is None:
        # A download from before names carried the hash ("01-name.jpg") is only trusted when it
        # cannot be another picture: the only file of that name, and no other picture of the
        # preset has that name. Otherwise it is downloaded again under its new name.
        plain = plain_name(image["url"])
        images = preset.section("wallpaper").get("images", [])
        older = [p for p in folder.glob(f"[0-9][0-9]-{plain}") if p.is_file() and not p.is_symlink()]
        if len(older) == 1 and sum(1 for o in images if "url" in o and plain_name(o["url"]) == plain) == 1:
            found = older[0]
    return found


def image_name(index: int, image: dict) -> str:
    """Local file name for a wallpaper entry, starting with its place in the slideshow."""
    if "file" in image:
        return f"{index:02d}-" + re.sub(r"^[0-9]{2}-", "", image["file"])
    return local_name(index, image["url"])


def desktop_script(
    mode: str, folder: Path, first_image: Path, interval: int, order: str = "listed", always_play: bool = False
) -> str:
    """Plasma script that sets the wallpaper on every desktop. ``always_play`` keeps animated
    pictures moving while a window fills the screen (Plasma pauses them by default)."""
    play = "true" if always_play else "false"
    if mode == "slideshow":
        body = f"""
    d.wallpaperPlugin = "org.kde.slideshow";
    d.currentConfigGroup = ["Wallpaper", "org.kde.slideshow", "General"];
    d.writeConfig("SlidePaths", {json.dumps(str(folder))});
    d.writeConfig("SlideInterval", {int(interval)});
    d.writeConfig("SlideshowMode", {SLIDESHOW_MODES.get(order, 1)});
    d.writeConfig("FillMode", {FILL_CROP});
    d.writeConfig("ForceImageAnimation", {play});"""
    else:
        body = f"""
    d.wallpaperPlugin = "org.kde.image";
    d.currentConfigGroup = ["Wallpaper", "org.kde.image", "General"];
    d.writeConfig("Image", {json.dumps(first_image.as_uri())});
    d.writeConfig("FillMode", {FILL_CROP});
    d.writeConfig("ForceImageAnimation", {play});"""
    return "desktops().forEach(function (d) {" + body + "\n});"


def shimmer_colour(preset: Preset) -> str:
    """The sparkle colour: a palette colour name (accent by default) or a fixed #rrggbb."""
    ref = preset.section("wallpaper").get("shimmer_colour", "accent")
    return preset.palette.colors.get(ref, ref)


def shimmer_options(wallpaper: dict) -> dict[str, str | None]:
    """The preset's Shimmer settings as plasmify.shimmer takes them: "calm" is its style "none"
    (light and motion only) and "auto" is None (what suits the style)."""
    style = wallpaper.get("shimmer", "none")
    light = wallpaper.get("shimmer_light", "auto")
    motion = wallpaper.get("shimmer_motion", "auto")
    return {
        "style": "none" if style == "calm" else style,
        "light": None if light == "auto" else light,
        "motion": None if motion == "auto" else motion,
        "density": wallpaper.get("shimmer_density", "normal"),
    }


def shimmer_fps(wallpaper: dict) -> int:
    """Frames per second of the living wallpapers (schema.SHIMMER_FPS); 12 unless the preset says."""
    return wallpaper.get("shimmer_fps", 12)


def _place(source: Path, target: Path) -> None:
    """``source`` at ``target`` too: a hard link when they share a file system (no extra space),
    else a copy. A file already at ``target`` is replaced."""
    target.unlink(missing_ok=True)
    try:
        target.hardlink_to(source)
    except OSError:
        shutil.copyfile(source, target)


def _bundled_source(asset_dir: Path | None, name: str) -> Path:
    if asset_dir is None:
        raise downloads.DownloadError(f"{name}: only imported presets can contain their own wallpaper files")
    return safe_child(asset_dir, name)


class WallpaperStep(Step):
    id = "wallpaper"
    title = "Wallpapers"
    description = "Wallpapers from the projects and collections that publish them, on the desktop and the lock screen."

    def applies_to(self, preset: Preset) -> bool:
        return bool(preset.section("wallpaper"))

    def touched_paths(self, preset: Preset) -> list[Path]:
        return [
            CONFIG_HOME / "plasma-org.kde.plasma.desktop-appletsrc",
            CONFIG_HOME / "kscreenlockerrc",
        ]

    def _copy_bundled(self, ctx: Context, name: str, target: Path) -> None:
        source = _bundled_source(ctx.preset.asset_dir, name)
        if not source.is_file() or source.is_symlink() or not downloads.is_image(source):
            raise downloads.DownloadError(f"{name} is missing from the preset or is not an image")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    def _bring_to_life(self, ctx: Context, stills: list[Path], name: str) -> list[Path]:
        """Living versions of the first wallpapers (animated WebP), made once and kept; [] when
        they cannot be made, so the still pictures are used. ``name`` is the preset's shimmer."""
        chosen = stills[:SHIMMER_MAX]
        if len(stills) > SHIMMER_MAX:
            ctx.log(f"Shimmer brings the first {SHIMMER_MAX} of {len(stills)} wallpapers to life; the rest stay still")
        if ctx.dry_run:
            ctx.log(f"Would bring {len(chosen)} wallpaper(s) to life ({name})")
            return []
        try:
            from plasmify import shimmer  # Qt is only needed here
        except ImportError:
            ctx.warn("Shimmer needs PyQt6; the wallpapers stay still")
            return []
        if not shimmer.available():
            ctx.warn("Qt cannot write WebP pictures on this system; the wallpapers stay still")
            return []
        colour = shimmer_colour(ctx.preset)
        options = shimmer_options(ctx.preset.section("wallpaper"))
        fps = shimmer_fps(ctx.preset.section("wallpaper"))
        version = getattr(shimmer, "VERSION", 1)
        # Every setting that changes the frames is in the file name, so a change makes new ones.
        settings = ":".join(str(options[key]) for key in ("style", "light", "motion", "density"))
        settings += f":{fps}fps"
        living = wallpaper_dir(ctx.preset) / LIVING
        living.mkdir(parents=True, exist_ok=True)
        made: list[Path] = []
        for index, still in enumerate(stills, start=1):
            with still.open("rb") as handle:
                animated = downloads.is_animated(handle.read(64))
            if animated or index > SHIMMER_MAX:
                # Already moving (sparkles on its first frame would stop it), or past the ones
                # brought to life. The slideshow plays the living folder, so it goes there as it is.
                target = living / still.name
                if not target.exists() or target.stat().st_size != still.stat().st_size:
                    _place(still, target)
                made.append(target)
                continue
            key = hashlib.sha256(f"{still.name}:{still.stat().st_size}:{settings}:{colour}:{version}".encode())
            target = living / f"{still.stem}-{key.hexdigest()[:10]}.webp"  # keeps the NN- position
            if not target.exists():
                ctx.log(f"Bringing wallpaper {index} of {len(chosen)} to life ({name})")
                try:
                    shimmer.render(still, target, colour, seed=index, fps=fps, **options)
                except (ValueError, RuntimeError, OSError) as exc:
                    ctx.warn(f"{still.name} stays still: {exc}")
                    # It keeps its place in the slideshow as it is (the slideshow plays only
                    # the living folder); if even that fails, every picture stays still.
                    target = living / still.name
                    try:
                        _place(still, target)
                    except OSError:
                        return []
            made.append(target)
        for old in living.iterdir():
            if old.is_file() and old not in made:
                old.unlink()
        return made

    def apply(self, ctx: Context) -> None:
        wallpaper = ctx.preset.section("wallpaper")
        folder = wallpaper_dir(ctx.preset)
        images = wallpaper["images"]
        ctx.log(f"Preparing {len(images)} wallpaper(s)")

        saved: list[Path] = []
        credits: list[str] = []
        targets = {image_name(i, img) for i, img in enumerate(images, start=1)}
        for index, image in enumerate(images, start=1):
            target = folder / image_name(index, image)
            # Bundled files are local and cheap: always copy, so a changed picture with the same
            # name is not left stale. Downloads are kept to avoid fetching them again.
            if not ctx.dry_run and "url" in image and not target.exists():
                earlier = cached_file(ctx.preset, image)  # same picture, other position: no download
                if earlier is not None and earlier.name[3:] == plain_name(image["url"]):
                    # A download named before names had the hash: it moves to its new place but
                    # keeps an old-style name, so it is never vouched for by this address's hash
                    # (if it was ever the wrong picture, a later apply can still fetch the right one).
                    target = folder / f"{index:02d}-{plain_name(image['url'])}"
                    if earlier != target and not target.exists():
                        earlier.rename(target)
                elif earlier is not None and earlier.name not in targets:
                    earlier.rename(target)
            if not ctx.dry_run and ("file" in image or not target.exists()):
                try:
                    if "file" in image:
                        self._copy_bundled(ctx, image["file"], target)
                    else:
                        downloads.fetch_image(image["url"], target)
                except (downloads.DownloadError, OSError) as exc:
                    ctx.warn(str(exc))
                    continue
            saved.append(target)
            licence = f" ({image['license']})" if image.get("license") else ""
            credits.append(f"{target.name}: {image['credit']}{licence}")
        if not ctx.dry_run:
            ctx.record(folder)
            folder.mkdir(parents=True, exist_ok=True)
            # The slideshow shows the whole folder: drop pictures from earlier versions.
            current = {p.name for p in saved}
            for old in folder.iterdir():
                if old.is_file() and old.suffix.lower() in IMAGE_SUFFIXES and old.name not in current:
                    old.unlink()
            (folder / "CREDITS.txt").write_text("\n".join(credits) + "\n")
        if not saved:
            ctx.warn("No wallpaper could be downloaded; keeping the current one")
            return

        style = wallpaper.get("shimmer", "none")
        shown, shown_folder = saved, folder
        if style != "none":
            living = self._bring_to_life(ctx, saved, style)
            if living:
                shown, shown_folder = living, folder / LIVING
        if shown is saved and not ctx.dry_run and (folder / LIVING).is_dir():
            # Shimmer is off or cannot run: the slideshow also plays subfolders, so living copies
            # left from an earlier apply would play next to the stills.
            shutil.rmtree(folder / LIVING)

        mode = wallpaper.get("mode", "slideshow")
        interval = int(wallpaper.get("interval", 1800))
        order = wallpaper.get("order", "listed")
        always = bool(wallpaper.get("shimmer_always", False)) and shown is not saved
        ctx.runner.plasma_script(desktop_script(mode, shown_folder, shown[0], interval, order, always))

        if wallpaper.get("lock_screen", True):
            group = ["Greeter", "Wallpaper", "org.kde.image", "General"]
            ctx.runner.kwrite("kscreenlockerrc", group, "Image", saved[0].as_uri())
            ctx.runner.kwrite("kscreenlockerrc", group, "PreviewImage", saved[0].as_uri())
