"""KWin desktop effects and optional tiling."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from plasmify import backup, distro, downloads, glass, schema
from plasmify.presets import Preset
from plasmify.steps.base import Context, Step
from plasmify.system import CONFIG_HOME, DATA_HOME, Runner

OPEN_CLOSE = {"scale": "scale", "glide": "glide", "fade": "fade"}
MINIMIZE = {"magiclamp": "magiclamp", "squash": "squash"}
DESKTOP_SWITCH = {"slide": "slide", "fade": "fadedesktop"}
# Not exclusive with open_close: fall apart takes over closing normal windows and sheet
# animates modal dialogs, while the open_close effect keeps animating everything else.
TOGGLES = {"fall_apart": "fallapart", "sheet": "sheet"}

# The desktop cube is a QML effect that KWin itself does not ship (see cube_installed()).
CUBE_METADATA = Path("kwin/effects/cube/metadata.json")
CUBE_PACKAGES = {
    distro.ARCH: "kdeplasma-addons",
    distro.FEDORA: "kdeplasma-addons",
    distro.OPENSUSE: "kdeplasma6-addons",
    distro.DEBIAN: "kwin-addons",
}
CUBE_ANY_PACKAGE = "kdeplasma-addons (Arch, Fedora), kdeplasma6-addons (openSUSE) or kwin-addons (Debian, Ubuntu)"
CUBE_MIN_DESKTOPS = 3

TILING_SCRIPT = "polonium"
SCRIPT_DIRS = (DATA_HOME / "kwin/scripts", Path("/usr/share/kwin/scripts"))

# What a preset that leaves a key out gets. The keys of a look (blur, the animations, fall apart,
# sheet, the cube, wobbly windows, see-through moving windows, dimming) go to the default the
# Customize tab shows, so switching presets never keeps a mix of the earlier ones. Tiling, title
# bar buttons, border size and animation speed are the user's own more than a look's: left out
# (or "As before Plasmify" in the window), they go back to what the user had before Plasmify,
# read from the pinned backup (see own_values), and a key that file did not set goes back to
# KDE's default. KDE's defaults alone would undo the user's own choices; keeping what is there
# kept the last preset's choice (tiling from Tokyo Night Neon stayed on in every later preset).
DECORATION = "org.kde.kdecoration2"
TILING_SWITCH = ("kwinrc", "Plugins", f"{TILING_SCRIPT}Enabled")
TRANSLUCENCY_SWITCH = ("kwinrc", "Plugins", "translucencyEnabled")
OWN_SETTINGS = {
    "titlebar_buttons": (("kwinrc", DECORATION, "ButtonsOnLeft"), ("kwinrc", DECORATION, "ButtonsOnRight")),
    "window_borders": (("kwinrc", DECORATION, "BorderSize"), ("kwinrc", DECORATION, "BorderSizeAuto")),
    "animation_speed": (("kdeglobals", "KDE", "AnimationDurationFactor"),),
}
OWN_KEYS = (TILING_SWITCH, TRANSLUCENCY_SWITCH, *(entry for entries in OWN_SETTINGS.values() for entry in entries))


# Polonium (MIT, https://github.com/zeroxoneafour/polonium) is not in the distribution
# repositories. A reviewed release is pinned here by URL and SHA-256; presets cannot
# change it. It is installed per user with KDE's own package tool, no root needed.
POLONIUM_URL = "https://github.com/zeroxoneafour/polonium/releases/download/v1.2.1/polonium.kwinscript"
POLONIUM_SHA256 = "1f7cd126d4341761fde0e0d82f277118030dc372b734825d6b1edf3d41b6bd85"


def tiling_script_installed() -> bool:
    return any((d / TILING_SCRIPT).is_dir() for d in SCRIPT_DIRS)


def install_polonium(ctx: Context) -> None:
    ctx.log("Installing the Polonium tiling script (26 KB, from its author)")
    package = ctx.workdir / "polonium.kwinscript"
    try:
        downloads.fetch(POLONIUM_URL, package)
    except downloads.DownloadError as exc:
        ctx.warn(f"Could not download Polonium: {exc}")
        return
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    if digest != POLONIUM_SHA256:
        ctx.warn("Polonium download does not match the reviewed checksum; tiling skipped")
        return
    ctx.runner.run(["kpackagetool6", "--type", "KWin/Script", "--install", str(package)], check=False)
    target = DATA_HOME / "kwin/scripts" / TILING_SCRIPT
    if target.is_dir():
        ctx.record(target, shared=True)  # tiling may be used by any preset; never cleaned
    else:
        ctx.warn("Polonium could not be installed; tiling skipped")


# Polonium never disconnects its window handlers, so after KWin unloads it they keep firing
# at a destroyed timer until the session ends. Restarting KWin would clear them, but that is
# far more disruptive than a few journal lines, so the user is only told what to expect.
TILING_OFF_HINT = (
    "Tiling is off. KWin keeps some of Polonium's old handlers until you log out, "
    "so harmless 'eventTimer' errors can show in the journal until then."
)


def data_dirs() -> list[Path]:
    """XDG data folders in lookup order: the user's own, then ``$XDG_DATA_DIRS``."""
    system_dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    # The XDG spec says relative entries are invalid and must be ignored.
    return [DATA_HOME, *(Path(d) for d in system_dirs.split(":") if Path(d).is_absolute())]


def cube_installed() -> bool:
    return any((d / CUBE_METADATA).is_file() for d in data_dirs())


def cube_package() -> str:
    """The package that brings the desktop cube, named for this distribution when known."""
    try:
        found = distro.detect()
    except OSError:
        found = None
    if found is not None and found.family in CUBE_PACKAGES:
        return f"the {CUBE_PACKAGES[found.family]} package"
    return CUBE_ANY_PACKAGE


def desktop_count(ctx: Context) -> int:
    """How many virtual desktops there are; KWin starts with one when kwinrc does not say."""
    value = ctx.runner.kread("kwinrc", "Desktops", "Number", "1")
    try:
        return max(int(value), 1)
    except ValueError:
        return 1


def own_values() -> dict[tuple[str, str, str], str | None]:
    """The settings in OWN_KEYS as the user had them before Plasmify, from the pinned "Your setup
    before Plasmify" backup: each value, or None where the file did not set it (KDE's default).

    Settings of a file that backup does not hold are not in the result: Plasmify never changed
    that file, so it is still the user's own and is left as it is. So is everything when there
    is no backup yet, or it cannot be read. A pinned backup from an earlier Plasmify version holds
    a theme, not the user's own setup (see backup.is_original): tiling on from Tokyo Night Neon
    would look like theirs, so every setting of a file it holds gets KDE's default instead."""
    values: dict[tuple[str, str, str], str | None] = {}
    try:
        pinned = next((b for b in backup.list_backups(backup.BACKUP_ROOT) if b.pinned), None)
        if pinned is None:
            return values
        texts = {file: backup.stored_text(pinned, CONFIG_HOME / file) for file in {f for f, _g, _k in OWN_KEYS}}
    except (OSError, KeyError, TypeError, ValueError):  # a damaged manifest never stops the step
        return values
    original = backup.is_original(pinned)
    for file, group, key in OWN_KEYS:
        text = texts[file]
        if text is not None:
            values[(file, group, key)] = glass.get_key(text, group, key) if original else None
    return values


def own_tiling(runner: Runner) -> bool:
    """Whether tiling is on after a preset that leaves it out: as the user had it before Plasmify,
    or as it is now when Plasmify never changed kwinrc."""
    own = own_values()
    value = own[TILING_SWITCH] if TILING_SWITCH in own else runner.kread(*TILING_SWITCH)
    return value == "true"


def put_back(ctx: Context, own: dict[tuple[str, str, str], str | None], entry: tuple[str, str, str]) -> None:
    """Give ``entry`` (file, group, key) the user's own value from ``own_values``, deleting it
    where they had none; a setting ``own`` does not know is left as it is."""
    if entry not in own:
        return
    value = own[entry]
    if value is None and entry[1] == "Plugins":
        ctx.runner.kdelete(*entry)  # with --notify, so KWin loads or unloads the effect now
    elif value is None:
        file, group, key = entry
        ctx.runner.krevert(CONFIG_HOME / file, group, key)  # KDE's or the distribution's default
    else:
        ctx.runner.kwrite(*entry, value)


def checked(ctx: Context, effects: dict[str, Any], key: str, default: Any = None) -> Any:
    """``effects[key]`` when it is valid, ``default`` when the preset leaves it out, and None
    when it is not valid (the setting is then left as it is, with a warning).

    Loaded presets are validated already; checking again here keeps a preset built in
    code from writing values KWin does not expect. The value itself is never echoed.
    """
    if key not in effects:
        return default
    problem = schema.SCHEMA["effects"][0][key][0](effects[key])
    if problem:
        ctx.warn(f"effects.{key} {problem}; it was left as it is")
        return None
    return effects[key]


# Effects with settings of their own that the step writes; a running one reads them again only
# when told to (see Runner.kwin_reconfigure_effect).
TUNED_EFFECTS = ("blur", "wobblywindows", "translucency", "diminactive")


def plugin_switches(text: str) -> dict[str, str]:
    """The ``*Enabled`` switches in the [Plugins] group of a kwinrc, as written there."""
    switches: dict[str, str] = {}
    group = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            group = line[1:-1]
        elif group == "Plugins" and "=" in line and not line.startswith(("#", ";")):
            key, value = (part.strip() for part in line.split("=", 1))
            if key.endswith("Enabled"):
                switches[key] = value
    return switches


def tuned_effects(effects: dict[str, Any]) -> list[str]:
    """The KWin effects whose own settings the step writes (blur strength, wobbliness, how
    see-through moving windows are, how dim inactive ones are)."""
    tuned = []
    if bool(effects.get("blur", True)):
        tuned.append("blur")
    if effects.get("wobbly"):
        tuned.append("wobblywindows")
    tuned.append("translucency")  # moving windows always get a setting, solid when left out
    if effects.get("dim_inactive"):
        tuned.append("diminactive")
    return tuned


# Title bar buttons (KWin letters: X close, I minimise, A maximise, M menu, F keep above).
TITLEBAR_BUTTONS = {"right": ("M", "IAX"), "left": ("XIA", ""), "close": ("", "X"), "none": ("", "")}
WINDOW_BORDERS = {"none": "None", "tiny": "Tiny", "normal": "Normal", "large": "Large"}


class EffectsStep(Step):
    id = "effects"
    title = "Effects"
    description = "Blur, window animations, desktop switching, the desktop cube and optional tiling."

    def applies_to(self, preset: Preset) -> bool:
        return bool(preset.section("effects"))

    def touched_paths(self, preset: Preset) -> list[Path]:
        return [CONFIG_HOME / "kwinrc", CONFIG_HOME / "kdeglobals"]

    def apply(self, ctx: Context) -> None:
        effects = ctx.preset.section("effects")
        w = ctx.runner.kwrite

        def choose(mapping: dict[str, str], selected: str) -> None:
            for value, plugin in mapping.items():
                w("kwinrc", "Plugins", f"{plugin}Enabled", value == selected)

        blur = bool(effects.get("blur", True))
        w("kwinrc", "Plugins", "blurEnabled", blur)
        if blur:
            w("kwinrc", "Effect-blur", "BlurStrength", int(effects.get("blur_strength", 10)))
            w("kwinrc", "Effect-blur", "NoiseStrength", int(effects.get("blur_noise", 4)))
            saturation = checked(ctx, effects, "blur_saturation", 100)
            if saturation is not None:
                # Blur's own colour boost; it replaced the background contrast effect in Plasma 6.
                w("kwinrc", "Effect-blur", "Saturation", saturation)
        choose(OPEN_CLOSE, effects.get("open_close", "scale"))
        choose(MINIMIZE, effects.get("minimize", "magiclamp"))
        choose(DESKTOP_SWITCH, effects.get("desktop_switch", "slide"))
        wobbly = bool(effects.get("wobbly", False))
        w("kwinrc", "Plugins", "wobblywindowsEnabled", wobbly)
        level = checked(ctx, effects, "wobbly_level", 2)
        if wobbly and level is not None:
            # KWin reads WobblynessLevel only while Settings is Auto; Custom uses raw values.
            w("kwinrc", "Effect-wobblywindows", "Settings", "Auto")
            w("kwinrc", "Effect-wobblywindows", "WobblynessLevel", level)
        for key, plugin in TOGGLES.items():
            enabled = checked(ctx, effects, key, False)
            if enabled is not None:
                w("kwinrc", "Plugins", f"{plugin}Enabled", enabled)
        own = own_values()
        self._window_opacity(ctx, effects, own)
        cube = checked(ctx, effects, "cube", False)
        if cube is not None:
            self._cube(ctx, cube)
        self._tiling(ctx, effects, own)

        for key, entries in OWN_SETTINGS.items():
            if key not in effects:
                for entry in entries:
                    put_back(ctx, own, entry)
        if "animation_speed" in effects:
            w("kdeglobals", "KDE", "AnimationDurationFactor", float(effects["animation_speed"]))
        if "titlebar_buttons" in effects:
            left, right = TITLEBAR_BUTTONS[effects["titlebar_buttons"]]
            w("kwinrc", DECORATION, "ButtonsOnLeft", left)
            w("kwinrc", DECORATION, "ButtonsOnRight", right)
        if "window_borders" in effects:
            w("kwinrc", DECORATION, "BorderSizeAuto", False)
            w("kwinrc", DECORATION, "BorderSize", WINDOW_BORDERS[effects["window_borders"]])
        ctx.log("Reloading KWin effects")
        ctx.runner.kwin_reconfigure()
        for effect in tuned_effects(effects):
            ctx.runner.kwin_reconfigure_effect(effect)

    @staticmethod
    def _tiling(ctx: Context, effects: dict[str, Any], own: dict[tuple[str, str, str], str | None]) -> None:
        """Polonium on or off. The switch is in [Plugins], so KWin loads or unloads it at once."""
        w = ctx.runner.kwrite
        if "tiling" in effects:
            wanted = bool(effects["tiling"])
        elif TILING_SWITCH in own:
            wanted = own[TILING_SWITCH] == "true"
            if wanted:  # the user's own Polonium from before Plasmify: nothing to install
                w(*TILING_SWITCH, True)
                return
        else:
            return  # Plasmify never changed kwinrc: tiling is as the user has it
        if wanted:
            if not tiling_script_installed() and not ctx.dry_run:
                install_polonium(ctx)
            if tiling_script_installed() or ctx.dry_run:
                w(*TILING_SWITCH, True)
            return
        was_on = ctx.runner.kread(*TILING_SWITCH) == "true"
        w(*TILING_SWITCH, False)
        if was_on:
            ctx.log(TILING_OFF_HINT)

    @staticmethod
    def _window_opacity(ctx: Context, effects: dict[str, Any], own: dict[tuple[str, str, str], str | None]) -> None:
        """Translucent windows while moving them, and dimmed inactive windows."""
        w = ctx.runner.kwrite
        opacity = checked(ctx, effects, "moving_opacity", 1.0)
        if opacity is not None:
            percent = round(float(opacity) * 100)
            if percent < 100:
                w(*TRANSLUCENCY_SWITCH, True)
            else:
                # Solid windows need no effect. Plasmify turns it on only for moving windows, but
                # it does more (menus, inactive windows), so the switch goes back to how the user
                # had it before Plasmify: off unless they turned it on themselves.
                put_back(ctx, own, TRANSLUCENCY_SWITCH)
            w("kwinrc", "Effect-translucency", "MoveResize", percent)
        dim = checked(ctx, effects, "dim_inactive", 0)
        if dim is not None:
            w("kwinrc", "Plugins", "diminactiveEnabled", dim > 0)
            if dim > 0:
                w("kwinrc", "Effect-diminactive", "Strength", dim)

    @staticmethod
    def _cube(ctx: Context, enabled: bool) -> None:
        if not enabled:
            ctx.runner.kwrite("kwinrc", "Plugins", "cubeEnabled", False)
            return
        if not cube_installed():
            ctx.warn(f"The desktop cube was left out: it needs {cube_package()}")
            return
        ctx.runner.kwrite("kwinrc", "Plugins", "cubeEnabled", True)
        count = desktop_count(ctx)
        if count < CUBE_MIN_DESKTOPS:
            # Adding desktops would change how the user works, so only say what is missing.
            ctx.log(
                f"Open the desktop cube with Meta+C. It needs at least {CUBE_MIN_DESKTOPS} virtual desktops "
                f"and there are {count}; add more in System Settings, Virtual Desktops"
            )
        else:
            ctx.log("Open the desktop cube with Meta+C")
