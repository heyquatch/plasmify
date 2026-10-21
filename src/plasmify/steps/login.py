"""Login screen background (Plasma Login Manager, or SDDM with the Breeze theme).

This is the only step that changes system files, so it needs your password once.
It runs one fixed script through polkit (see ``plasmify.rootops``): the picture's bytes
arrive on standard input and are written to /usr/share/wallpapers/plasmify-login under a
name Plasmify chooses, and the login wallpaper key is set. The keys' previous values are
backed up so Restore can put them back.

The login screen gets a still picture: an animated GIF or WebP is sent as a PNG of its first
frame (SDDM's Breeze theme would show only that frame anyway, and root stores no GIFs).
"""

from __future__ import annotations

from pathlib import Path

from plasmify import downloads, rootops
from plasmify.presets import Preset
from plasmify.steps.base import Context, Step
from plasmify.steps.wallpaper import cached_file, image_name, wallpaper_dir
from plasmify.system import safe_child

PLASMALOGIN_CONF = Path(rootops.MANAGED["plasmalogin"][0])
SDDM_BREEZE_CONF = Path(rootops.MANAGED["sddm"][0])
# Suffixes root accepts (rootops.LOGIN_NAMES), by what the picture's bytes really are.
SUFFIXES = {"png": (".png",), "jpeg": (".jpg", ".jpeg"), "webp": (".webp",)}


def login_manager() -> str | None:
    """'plasmalogin' or 'sddm' when one of them is the active login manager."""
    try:
        unit = Path("/etc/systemd/system/display-manager.service").resolve().name
    except OSError:
        return None
    return {"plasmalogin.service": "plasmalogin", "sddm.service": "sddm"}.get(unit)


def config_path(manager: str | None) -> Path | None:
    return {"plasmalogin": PLASMALOGIN_CONF, "sddm": SDDM_BREEZE_CONF}.get(manager or "")


def login_name(kind: str | None, suffix: str) -> str | None:
    """The name root stores the picture under: the file's own suffix when it matches what the
    bytes are, else the usual one for that type. None for types the login screen cannot take."""
    choices = SUFFIXES.get(kind or "")
    if not choices:
        return None
    return "background" + (suffix if suffix in choices else choices[0])


def login_picture(image: Path, dry_run: bool = False) -> tuple[bytes, str | None, bool]:
    """The bytes root gets for ``image``, their type, and whether they are a still made from
    an animation. A dry run only peeks at the header and returns no bytes."""
    if dry_run:
        with image.open("rb") as handle:
            head = handle.read(64)
        animated = downloads.is_animated(head)
        return b"", "png" if animated else downloads.image_kind(head), animated
    # Read here, as you and without following links; root only ever gets these bytes. This also
    # checks the type and the header's size (downloads.is_image_bytes).
    data = rootops.read_image(image)
    if not downloads.is_animated(data):
        return data, downloads.image_kind(data[:16]), False
    still = downloads.still_png(data)
    if len(still) > rootops.MAX_LOGIN_IMAGE:  # root would cut it short
        raise ValueError(f"the still picture made from {image.name} is too large")
    return still, "png", True


class LoginStep(Step):
    id = "login"
    title = "Login screen"
    description = "Background of the login screen (asks for your password once; affects all users)."

    def applies_to(self, preset: Preset) -> bool:
        return bool(preset.section("login"))

    def touched_paths(self, preset: Preset) -> list[Path]:
        path = config_path(login_manager())
        return [path] if path else []

    def _image(self, ctx: Context, login: dict) -> Path | None:
        if login.get("background") == "image" and "background_file" in login:
            if ctx.preset.asset_dir is None:
                ctx.warn("A login image needs a saved or imported preset")
                return None
            path = safe_child(ctx.preset.asset_dir, login["background_file"])
            problem = downloads.image_file_problem(path)
            if problem:
                ctx.warn(f"The login picture {login['background_file']} {problem}")
                return None
            return path
        images = ctx.preset.section("wallpaper").get("images", [])
        if images:
            candidate = wallpaper_dir(ctx.preset) / image_name(1, images[0])
            if not candidate.is_file():
                candidate = cached_file(ctx.preset, images[0])  # e.g. downloaded under an older name
            if candidate is not None and candidate.is_file():
                return candidate
        ctx.warn("No wallpaper is downloaded yet; apply the Wallpapers step too")
        return None

    def apply(self, ctx: Context) -> None:
        manager = login_manager()
        if manager is None:
            ctx.warn("No supported login manager found (Plasma Login or SDDM); login screen skipped")
            return
        image = self._image(ctx, ctx.preset.section("login"))
        if image is None:
            return
        try:
            data, kind, still = login_picture(image, ctx.dry_run)
        except (OSError, ValueError) as exc:
            ctx.warn(f"The login picture cannot be used: {exc}")
            return
        name = login_name(kind, image.suffix.lower())
        if name is None:
            ctx.warn(f"The login picture cannot be used: {image.name} {downloads.NOT_AN_IMAGE}")
            return
        try:
            command = rootops.login_command(name, manager)
        except ValueError as exc:  # a program root needs is missing: say so before any password prompt
            ctx.warn(f"Login screen skipped: {exc}")
            return
        if still:
            ctx.log("The login screen shows still pictures only, so it gets the first frame of the animation")
        ctx.log(f"Setting the login screen background ({manager}); your password is needed once")
        ctx.runner.run(command, input_bytes=data)
