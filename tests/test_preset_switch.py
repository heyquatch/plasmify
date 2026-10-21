"""Switching from one preset to another keeps nothing of the earlier one's window behaviour.

A preset that left a setting out used to keep whatever the last preset wrote: after Synthwave Neon,
Catppuccin Anime still broke closing windows apart, and after Tokyo Night Neon every later preset
kept tiling windows, while the window showed those options off. Now each look setting goes to the
default the window shows, and the user's own settings (tiling, title bar buttons, border size,
animation speed) go back to what they had before Plasmify. Panels follow the same rule: a preset
without a panel style never keeps the last preset's generated Plasma style, and never replaces
the user's own. The light/dark switch runs the colour step on its own, and it leaves the window
border size alone unless the preset sets one.

The steps run as in an apply, right after a real backup, against config files in a temporary
folder: kwriteconfig6 and kreadconfig6 are emulated, nothing else runs, and nothing reads or
writes the real desktop.
"""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from plasmify import backup, capture, cli, engine, presets, record, schema  # noqa: E402
from plasmify.steps import colors  # noqa: E402
from plasmify.steps import effects as fx  # noqa: E402
from plasmify.steps import panelstyle as ps  # noqa: E402
from plasmify.steps import splash  # noqa: E402
from plasmify.steps.base import Context  # noqa: E402
from plasmify.system import Runner  # noqa: E402

# The user's own files from before Plasmify: KDE's stock title bar, no border or speed setting.
OWN_KWINRC = (
    "[Desktops]\nNumber=4\n\n[org.kde.kdecoration2]\nButtonsOnLeft=MS\nButtonsOnRight=HIAX\nlibrary=org.kde.breeze\n"
)
OWN_KDEGLOBALS = "[General]\nColorScheme=BreezeDark\n"


def read_config(path: Path) -> dict[str, dict[str, str]]:
    groups: dict[str, dict[str, str]] = {}
    current = None
    for line in path.read_text().splitlines() if path.exists() else []:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = groups.setdefault(stripped[1:-1], {})
        elif current is not None and "=" in stripped:
            key, value = stripped.split("=", 1)
            current[key] = value
    return groups


def write_config(path: Path, groups: dict[str, dict[str, str]]) -> None:
    blocks = [f"[{group}]\n" + "".join(f"{k}={v}\n" for k, v in keys.items()) for group, keys in groups.items() if keys]
    path.write_text("\n".join(blocks))


def option(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args else None


class ConfigRunner(Runner):
    """kwriteconfig6 and kreadconfig6 on the files in ``config``; every other command is only logged."""

    def __init__(self, config: Path):
        super().__init__(dry_run=False)
        self.config = config

    def run(self, args, *, check=True, capture=False, input_text=None, mutating=True, input_bytes=None):
        self.log.append(" ".join(args))
        if args[0] not in ("kwriteconfig6", "kreadconfig6"):
            return ""
        path = self.config / option(args, "--file")
        group, key = option(args, "--group"), option(args, "--key")
        groups = read_config(path)
        if args[0] == "kreadconfig6":
            return groups.get(group, {}).get(key, option(args, "--default") or "")
        if "--delete" in args:
            groups.get(group, {}).pop(key, None)
        else:
            groups.setdefault(group, {})[key] = args[-1]
        write_config(path, groups)
        return ""

    def kwin_reconfigure(self) -> None:
        self.log.append("<kwin reconfigure>")

    def kwin_reconfigure_effect(self, name: str) -> None:
        self.log.append(f"<reconfigure {name}>")


class PresetSwitchTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.config = root / "config"
        self.config.mkdir()
        (self.config / "kwinrc").write_text(OWN_KWINRC)
        (self.config / "kdeglobals").write_text(OWN_KDEGLOBALS)
        self.workdir = root / "work"
        self.workdir.mkdir()
        self.runner = ConfigRunner(self.config)
        for patch in (
            mock.patch.object(fx, "CONFIG_HOME", self.config),
            mock.patch.object(backup, "BACKUP_ROOT", root / "backups"),
            mock.patch.object(fx, "tiling_script_installed", return_value=True),  # never downloads
            mock.patch.object(fx, "cube_installed", return_value=True),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def apply(
        self, preset: presets.Preset | str, overrides: dict | None = None, steps: tuple[str, ...] = ("effects",)
    ) -> list[str]:
        """Back up, then run ``steps`` as an apply does; returns what they told the user."""
        if isinstance(preset, str):
            preset = presets.find(preset)
        if overrides:
            preset = presets.customized(preset, overrides)
        plan = engine.make_plan(preset, steps, check_packages=False)
        paths = [p for step in plan.steps for p in step.touched_paths(plan.preset)]
        backup.create(paths, f"Before {preset.name}", backup.BACKUP_ROOT)  # the temporary folder
        messages: list[str] = []
        for step in plan.steps:
            ctx = Context(plan.preset, self.runner, self.workdir, log=messages.append)
            step.apply(ctx)
            self.assertEqual(ctx.warnings, [])
        return messages

    def kwinrc(self, group: str, key: str) -> str | None:
        return read_config(self.config / "kwinrc").get(group, {}).get(key)

    def plugin(self, name: str) -> str | None:
        return self.kwinrc("Plugins", f"{name}Enabled")


class LookSettingsTests(PresetSwitchTestCase):
    def test_synthwave_neon_then_catppuccin_anime_leaves_no_effect_on(self):
        """The reported desktop: fall apart and the sheet stayed on, with dimming and see-through
        moving windows from earlier presets, while Customize showed them off."""
        reported = {"effects": {"sheet": True, "dim_inactive": 30, "moving_opacity": 0.8}}
        self.apply("synthwave-neon", reported)  # with fall apart, the cube, wobbly windows, more saturation
        for name in ("fallapart", "sheet", "cube", "diminactive", "translucency", "wobblywindows"):
            self.assertEqual(self.plugin(name), "true", name)
        self.assertEqual(self.kwinrc("Effect-blur", "Saturation"), "240")
        self.apply("catppuccin-anime")
        for name in ("fallapart", "sheet", "cube", "diminactive", "wobblywindows"):
            self.assertEqual(self.plugin(name), "false", name)
        self.assertIsNone(self.plugin("translucency"))  # the user never had it on: KWin's default, off
        self.assertEqual(self.kwinrc("Effect-translucency", "MoveResize"), "100")
        self.assertEqual(self.kwinrc("Effect-blur", "Saturation"), "100")
        self.assertEqual(self.plugin("scale"), "true")

    def test_each_preset_clears_the_one_before(self):
        self.apply("glass-night")  # a sheet, dimming and see-through moving windows
        for name in ("sheet", "diminactive", "translucency"):
            self.assertEqual(self.plugin(name), "true", name)
        self.apply("synthwave-neon")
        self.assertEqual(self.plugin("sheet"), "false")
        self.assertEqual(self.plugin("diminactive"), "false")
        self.assertIsNone(self.plugin("translucency"))
        self.assertEqual(self.plugin("fallapart"), "true")

    def test_open_close_none_closes_without_any_animation(self):
        self.apply("synthwave-neon")
        self.apply("catppuccin-anime", {"effects": {"open_close": "none"}})
        for name in ("scale", "glide", "fade", "fallapart"):
            self.assertEqual(self.plugin(name), "false", name)

    def test_switches_tell_kwin_at_once(self):
        self.apply("synthwave-neon")
        self.runner.log.clear()
        self.apply("catppuccin-anime")
        switches = [line for line in self.runner.log if "--group Plugins" in line and "kwriteconfig6" in line]
        self.assertIn(
            "kwriteconfig6 --notify --file kwinrc --group Plugins --type bool --key fallapartEnabled false", switches
        )
        self.assertEqual([line for line in switches if "--notify" not in line], [])

    def test_a_preset_without_effects_leaves_them_alone(self):
        self.apply("synthwave-neon")
        before = (self.config / "kwinrc").read_text(), (self.config / "kdeglobals").read_text()
        data = copy.deepcopy(presets.find("catppuccin-anime").data)
        del data["effects"]
        bare = presets.Preset(data, "user")
        self.assertFalse(fx.EffectsStep().applies_to(bare))
        self.runner.log.clear()
        self.apply(bare)
        self.assertEqual(self.runner.log, [])
        self.assertEqual(((self.config / "kwinrc").read_text(), (self.config / "kdeglobals").read_text()), before)
        self.assertEqual(self.plugin("fallapart"), "true")


class OwnSettingsTests(PresetSwitchTestCase):
    def test_tokyo_night_neon_then_catppuccin_anime_stops_tiling(self):
        self.apply("tokyo-night-neon")
        self.assertEqual(self.plugin("polonium"), "true")
        self.runner.log.clear()
        messages = self.apply("catppuccin-anime")
        self.assertEqual(self.plugin("polonium"), "false")
        # Through KConfig's notify flag, as System Settings does: KWin unloads Polonium now.
        self.assertIn(
            "kwriteconfig6 --notify --file kwinrc --group Plugins --type bool --key poloniumEnabled false",
            self.runner.log,
        )
        self.assertIn(fx.TILING_OFF_HINT, messages)
        self.assertIsNone(self.plugin("translucency"))
        messages = self.apply("nord-minimal")
        self.assertEqual(self.plugin("polonium"), "false")
        self.assertNotIn(fx.TILING_OFF_HINT, messages)  # it was off already

    def test_title_bar_borders_and_speed_go_back(self):
        changes = {"effects": {"titlebar_buttons": "left", "window_borders": "tiny", "animation_speed": 0.5}}
        self.apply("nord-minimal", changes)
        self.assertEqual(self.kwinrc(fx.DECORATION, "ButtonsOnLeft"), "XIA")
        self.assertEqual(self.kwinrc(fx.DECORATION, "BorderSize"), "Tiny")
        self.apply("catppuccin-anime")
        decoration = read_config(self.config / "kwinrc")[fx.DECORATION]
        self.assertEqual(decoration, {"ButtonsOnLeft": "MS", "ButtonsOnRight": "HIAX", "library": "org.kde.breeze"})
        self.assertNotIn("KDE", read_config(self.config / "kdeglobals"))  # KDE's own speed again

    def test_the_users_own_values_come_back_exactly(self):
        (self.config / "kwinrc").write_text(
            OWN_KWINRC + "BorderSize=Large\nBorderSizeAuto=false\n\n[Plugins]\npoloniumEnabled=true\n"
            "translucencyEnabled=true\n"
        )
        (self.config / "kdeglobals").write_text(OWN_KDEGLOBALS + "\n[KDE]\nAnimationDurationFactor=0.7\n")
        self.apply("glass-night", {"effects": {"tiling": False, "window_borders": "none", "animation_speed": 1.5}})
        self.assertEqual(self.plugin("polonium"), "false")
        self.assertEqual(self.kwinrc(fx.DECORATION, "BorderSize"), "None")
        self.apply("catppuccin-anime")
        self.assertEqual(self.plugin("polonium"), "true")  # their own tiling, nothing installed
        self.assertEqual(self.plugin("translucency"), "true")  # their own translucency effect
        self.assertEqual(self.kwinrc(fx.DECORATION, "BorderSize"), "Large")
        self.assertEqual(self.kwinrc(fx.DECORATION, "BorderSizeAuto"), "false")
        self.assertEqual(read_config(self.config / "kdeglobals")["KDE"]["AnimationDurationFactor"], "0.7")

    def test_a_preset_that_sets_them_still_wins(self):
        self.apply("catppuccin-anime")
        self.apply("tokyo-night-neon", {"effects": {"titlebar_buttons": "close"}})
        self.assertEqual(self.plugin("polonium"), "true")
        self.assertEqual(self.kwinrc(fx.DECORATION, "ButtonsOnLeft"), "")
        self.assertEqual(self.kwinrc(fx.DECORATION, "ButtonsOnRight"), "X")

    def test_without_a_backup_the_users_settings_are_left_alone(self):
        """No pinned backup (a dry run before the first apply): Plasmify never changed these
        files, so they are still the user's own and nothing of theirs is written."""
        self.assertEqual(fx.own_values(), {})
        ctx = Context(presets.find("catppuccin-anime"), self.runner, self.workdir, log=lambda _: None)
        fx.EffectsStep().apply(ctx)
        written = {
            line.split(" --key ")[1].split(" ")[0] for line in self.runner.log if line.startswith("kwriteconfig6")
        }
        self.assertFalse(written & {"poloniumEnabled", "translucencyEnabled", "ButtonsOnLeft", "BorderSize"})
        self.assertNotIn("AnimationDurationFactor", written)

    def test_a_backup_from_an_earlier_plasmify_version_is_not_the_users_own(self):
        """Older versions kept ten backups, so the oldest one left can hold a theme already: its
        tiling is Tokyo Night Neon's, not the user's, and KDE's default (off) comes back."""
        (self.config / "kwinrc").write_text(OWN_KWINRC + "\n[Plugins]\npoloniumEnabled=true\n")
        (self.config / "kdeglobals").write_text("[General]\nColorScheme=PlasmifyTokyoNightNeon\n")
        old = backup.create(
            [self.config / "kwinrc", self.config / "kdeglobals"], "Before Glass Night", backup.BACKUP_ROOT
        )
        manifest = json.loads((old.path / "manifest.json").read_text())
        manifest["pinned"] = False  # written before pinning existed
        (old.path / "manifest.json").write_text(json.dumps(manifest))
        pinned = next(b for b in backup.list_backups(backup.BACKUP_ROOT) if b.pinned)
        self.assertFalse(backup.is_original(pinned))
        own = fx.own_values()
        self.assertIsNone(own[fx.TILING_SWITCH])
        self.assertIsNone(own[("kwinrc", fx.DECORATION, "ButtonsOnLeft")])
        self.apply("catppuccin-anime")
        self.assertEqual(self.plugin("polonium"), "false")

    def test_a_damaged_backup_never_stops_the_step(self):
        self.apply("tokyo-night-neon")
        pinned = next(b for b in backup.list_backups(backup.BACKUP_ROOT) if b.pinned)
        damaged = {"pinned": True, "entries": [{"path": str(self.config / "kwinrc")}]}  # no "existed"
        (pinned.path / "manifest.json").write_text(json.dumps(damaged))
        self.assertEqual(fx.own_values(), {})
        self.apply("catppuccin-anime")
        self.assertEqual(self.plugin("fallapart"), "false")


class ColourStepTestCase(PresetSwitchTestCase):
    """The colour and panel style steps write their files into the temporary folder too."""

    def setUp(self):
        super().setUp()
        data = self.config.parent / "data"
        for patch in (
            mock.patch.object(colors, "CONFIG_HOME", self.config),
            mock.patch.object(colors, "DATA_HOME", data),
            mock.patch.object(ps, "CONFIG_HOME", self.config),
            mock.patch.object(ps, "THEME_ROOT", data / "plasma" / "desktoptheme"),
        ):
            patch.start()
            self.addCleanup(patch.stop)


class PanelStyleTests(ColourStepTestCase):
    """A preset without [panel_style] gives the panels the user's own Plasma style: the one on now
    when the user picked it, the one from before Plasmify instead of a style Plasmify generated."""

    STEPS = ("colors", "panelstyle")

    def own(self, name: str) -> None:
        """The Plasma style the user picked themselves."""
        (self.config / "plasmarc").write_text(f"[Theme]\nname={name}\n")

    def style(self) -> str | None:
        return read_config(self.config / "plasmarc").get("Theme", {}).get("name")

    def plasmarc_writes(self) -> list[str]:
        return [line for line in self.runner.log if line.startswith("kwriteconfig6") and "--file plasmarc" in line]

    def test_synthwave_neon_then_catppuccin_anime_drops_its_panels(self):
        """The user never set a Plasma style (KDE's default): that comes back, not Synthwave's."""
        self.apply("synthwave-neon", steps=self.STEPS)
        self.assertEqual(self.style(), "plasmify-synthwave-neon")
        messages = self.apply("catppuccin-anime", steps=self.STEPS)
        self.assertIsNone(self.style())
        self.assertIn("Putting the default Plasma style back on the panels", messages)
        self.apply("synthwave-neon", steps=self.STEPS)  # a panel style still sets its own
        self.assertEqual(self.style(), "plasmify-synthwave-neon")

    def test_no_built_in_preset_keeps_another_ones_panels(self):
        plain = [p for p in presets.load_all(include_user=False) if not p.section("panel_style")]
        self.assertEqual(
            sorted(p.id for p in plain),
            ["catppuccin-anime", "gruvbox-retro", "nord-minimal", "rose-pine-cozy", "tokyo-night-neon"],
        )
        self.own("breeze-dark")
        for preset in plain:
            with self.subTest(preset=preset.id):
                self.apply("synthwave-neon", steps=self.STEPS)
                self.apply(preset, steps=self.STEPS)
                self.assertEqual(self.style(), "breeze-dark")
                self.assertNotIn("panelstyle", [s.id for s in engine.make_plan(preset, check_packages=False).steps])

    def test_the_users_own_style_comes_back(self):
        self.own("Sweet")
        self.apply("synthwave-neon", steps=self.STEPS)
        self.assertEqual(self.style(), "plasmify-synthwave-neon")
        messages = self.apply("gruvbox-retro", steps=self.STEPS)
        self.assertEqual(self.style(), "Sweet")
        self.assertIn("Putting your own Plasma style back on the panels (Sweet)", messages)

    def test_a_user_who_never_applied_a_panel_style_keeps_their_own(self):
        self.own("Sweet")
        for preset_id in ("catppuccin-anime", "nord-minimal", "tokyo-night-neon"):
            self.apply(preset_id, steps=self.STEPS)
        self.assertEqual(self.style(), "Sweet")
        self.assertEqual(self.plasmarc_writes(), [])

    def test_a_style_picked_after_plasmify_stays(self):
        self.own("Sweet")
        self.apply("synthwave-neon", steps=self.STEPS)
        self.own("breeze-dark")  # picked in System Settings afterwards
        self.apply("catppuccin-anime", steps=self.STEPS)
        self.assertEqual(self.style(), "breeze-dark")

    def test_a_generated_style_from_before_is_not_put_back(self):
        """The pinned backup was taken while a Plasmify style was on (older backups deleted):
        that is a leftover too, so KDE's default comes back."""
        self.own("plasmify-glass-night")
        self.apply("synthwave-neon", steps=self.STEPS)
        self.apply("rose-pine-cozy", steps=self.STEPS)
        self.assertIsNone(self.style())

    def test_without_a_backup_the_default_style_comes_back(self):
        self.own("plasmify-synthwave-neon")  # no pinned backup: what the user had is unknown
        self.assertIsNone(ps.own_style())
        ctx = Context(presets.find("catppuccin-anime"), self.runner, self.workdir, log=lambda _: None)
        colors.ColorsStep().apply(ctx)
        self.assertIsNone(self.style())

    def test_dropping_a_panel_style_tweak_puts_the_users_own_back(self):
        """plasmify reset (and a light/dark switch) run only some steps: the colour step is one."""
        self.own("Sweet")
        tweak = {"panel_style": {"radius": 9}}
        self.apply("nord-minimal", tweak, steps=self.STEPS)
        self.assertEqual(self.style(), "plasmify-nord-minimal")
        before, after = presets.customized(presets.find("nord-minimal"), tweak), presets.find("nord-minimal")
        steps = cli._affected_steps(before, after, {"steps": list(self.STEPS)}, set())
        self.assertEqual(steps, ["colors"])
        self.apply(after, steps=tuple(steps))
        self.assertEqual(self.style(), "Sweet")


class SplashLeftoverTests(ColourStepTestCase):
    """Seen in the VM: after Crimson Moon (the only built-in preset with a splash), every later preset
    kept its "Crimson Moon" splash screen. A preset without [splash] now gives the user their own,
    the way panels get their own Plasma style."""

    STEPS = ("colors", "splash")

    def setUp(self):
        super().setUp()
        for patch in (
            mock.patch.object(splash, "CONFIG_HOME", self.config),
            mock.patch.object(splash, "PACKAGE_ROOT", self.config.parent / "data" / "plasma" / "look-and-feel"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def own(self, **keys: str) -> None:
        """The splash screen the user set themselves."""
        write_config(self.config / "ksplashrc", {"KSplash": keys})

    def keys(self) -> dict[str, str]:
        return read_config(self.config / "ksplashrc").get("KSplash", {})

    def test_crimson_moon_then_nord_minimal_drops_its_splash(self):
        self.apply("crimson-moon", steps=self.STEPS)
        self.assertEqual(self.keys().get("Theme"), "plasmify-splash-crimson-moon")
        messages = self.apply("nord-minimal", steps=self.STEPS)
        self.assertEqual(self.keys(), {})
        self.assertIn("Putting the default splash screen back", messages)

    def test_no_built_in_preset_keeps_crimson_moons_splash(self):
        plain = [p for p in presets.load_all(include_user=False) if not p.section("splash")]
        self.assertEqual(len(plain), 13)
        self.own(Engine="KSplashQML", Theme="org.kde.breeze.desktop")
        for preset in plain:
            with self.subTest(preset=preset.id):
                self.apply("crimson-moon", steps=self.STEPS)
                self.apply(preset, steps=self.STEPS)
                self.assertEqual(self.keys(), {"Engine": "KSplashQML", "Theme": "org.kde.breeze.desktop"})

    def test_a_splash_turned_off_comes_back_off(self):
        self.own(Engine="none", Theme="None")
        self.apply("crimson-moon", steps=self.STEPS)
        messages = self.apply("gruvbox-retro", steps=self.STEPS)
        self.assertEqual(self.keys(), {"Engine": "none", "Theme": "None"})
        self.assertIn("Putting your own splash screen back (None)", messages)

    def test_a_splash_picked_after_plasmify_stays(self):
        self.apply("crimson-moon", steps=self.STEPS)
        self.own(Engine="KSplashQML", Theme="com.example.mine")  # picked in System Settings afterwards
        self.apply("dracula-night", steps=self.STEPS)
        self.assertEqual(self.keys().get("Theme"), "com.example.mine")

    def test_a_generated_splash_in_the_pinned_backup_is_not_put_back(self):
        self.own(Engine="KSplashQML", Theme="plasmify-splash-glass-night")  # an older Plasmify's leftover
        self.apply("crimson-moon", steps=self.STEPS)
        self.apply("kanagawa-wave", steps=self.STEPS)
        self.assertEqual(self.keys(), {})


class BorderSizeTests(ColourStepTestCase):
    """The colour step writes a border size only when the preset sets window_borders. The light/dark
    switch and colour changes run it without the effects step, and it used to write None (no
    borders) for every preset that leaves window_borders out, and then the size from before
    Plasmify, also over one the user picked since."""

    STEPS = ("colors", "effects")

    def setUp(self):
        super().setUp()
        (self.config / "kwinrc").write_text(OWN_KWINRC + "BorderSize=Large\nBorderSizeAuto=false\n")  # their own

    def borders(self) -> tuple[str | None, str | None]:
        return self.kwinrc(fx.DECORATION, "BorderSize"), self.kwinrc(fx.DECORATION, "BorderSizeAuto")

    def switch(self, preset: presets.Preset, overrides: dict | None = None) -> None:
        """The light/dark switch of ``preset`` applied with ``self.STEPS``: only its colour step runs."""
        overrides = overrides or {}
        result = presets.customized(preset, overrides)
        state = record.Applied(preset, overrides, None, result, {"steps": list(self.STEPS)}, None)
        switched = record.switch_mode(state, not result.palette.dark)
        self.assertEqual(switched.steps, ["colors"])
        self.apply(switched.preset, steps=tuple(switched.steps))

    def test_a_light_dark_switch_keeps_the_border_size_from_before_plasmify(self):
        for preset in presets.load_all(include_user=False):
            with self.subTest(preset=preset.id):
                self.assertNotIn("window_borders", preset.section("effects"))  # every one leaves it out
                self.apply(preset, steps=self.STEPS)
                self.assertEqual(self.borders(), ("Large", "false"))
                self.switch(preset)
                self.assertEqual(self.borders(), ("Large", "false"))

    def test_a_light_dark_switch_keeps_a_border_size_picked_after_plasmify(self):
        preset = presets.find("catppuccin-anime")
        self.apply(preset, steps=self.STEPS)
        self.runner.kwrite("kwinrc", fx.DECORATION, "BorderSize", "Huge")  # as System Settings writes it
        self.runner.kwrite("kwinrc", fx.DECORATION, "BorderSizeAuto", False)
        self.switch(preset)
        self.assertEqual(self.borders(), ("Huge", "false"))

    def test_a_border_size_picked_after_plasmify_is_kept_when_there_was_none_before(self):
        """The pinned kwinrc sets no border size (KDE's default): a full apply deletes Plasmify's
        size, and a light/dark switch never deletes the one the user picked afterwards."""
        (self.config / "kwinrc").write_text(OWN_KWINRC)
        preset = presets.find("nord-minimal")
        self.apply(preset, {"effects": {"window_borders": "tiny"}}, steps=self.STEPS)
        self.apply(preset, steps=self.STEPS)
        self.assertEqual(self.borders(), (None, None))
        self.runner.kwrite("kwinrc", fx.DECORATION, "BorderSize", "Large")
        self.runner.kwrite("kwinrc", fx.DECORATION, "BorderSizeAuto", False)
        self.switch(preset)
        self.assertEqual(self.borders(), ("Large", "false"))

    def test_a_preset_with_window_borders_still_gets_them(self):
        tiny = {"effects": {"window_borders": "tiny"}}
        self.apply("nord-minimal", tiny, steps=("colors",))  # the colour step on its own
        self.assertEqual(self.borders(), ("Tiny", "false"))
        self.switch(presets.find("nord-minimal"), tiny)
        self.assertEqual(self.borders(), ("Tiny", "false"))
        self.apply("catppuccin-anime", steps=("colors",))  # leaves it out: the colour step leaves it alone
        self.assertEqual(self.borders(), ("Tiny", "false"))
        self.apply("catppuccin-anime", steps=self.STEPS)  # a full apply: theirs from before Plasmify
        self.assertEqual(self.borders(), ("Large", "false"))

    def test_a_preset_without_effects_leaves_the_border_size_alone(self):
        data = copy.deepcopy(presets.find("catppuccin-anime").data)
        del data["effects"]
        self.apply(presets.Preset(data, "user"), steps=self.STEPS)
        self.assertEqual(self.borders(), ("Large", "false"))
        self.assertFalse([line for line in self.runner.log if "--key BorderSize" in line])


class SavedDesktopTests(PresetSwitchTestCase):
    """A desktop saved as a preset ("Save current desktop as preset") applies as it was: every
    setting the effects step writes is read back, or applying it would turn the cube, sheet and
    dimming off and put the border size from before Plasmify back."""

    def config_files(self) -> dict[str, dict[str, dict[str, str]]]:
        return {name: read_config(self.config / name) for name in ("kwinrc", "kdeglobals")}

    def apply_saved(self) -> dict:
        saved = capture.read_effects(self.runner)
        data = copy.deepcopy(presets.find("nord-minimal").data)
        data["effects"] = saved
        self.apply(presets.Preset(data, "user"))
        return saved

    def test_applying_a_saved_desktop_changes_nothing(self):
        tweaks = {
            "effects": {
                "sheet": True,
                "dim_inactive": 30,
                "moving_opacity": 0.8,
                "blur_noise": 6,
                "window_borders": "large",
                "titlebar_buttons": "left",
                "animation_speed": 0.5,
                "tiling": True,
            }
        }
        self.apply("synthwave-neon", tweaks)
        before = self.config_files()
        saved = self.apply_saved()
        self.assertEqual(self.config_files(), before)
        self.assertEqual(
            {key: saved[key] for key in tweaks["effects"]} | {k: saved[k] for k in ("cube", "fall_apart")},
            tweaks["effects"] | {"cube": True, "fall_apart": True},
        )
        self.assertEqual((saved["blur_saturation"], saved["wobbly_level"]), (240, 3))

    def test_a_plain_desktop_saves_its_effects_off(self):
        self.apply("catppuccin-anime")
        before = self.config_files()
        saved = self.apply_saved()
        self.assertEqual(self.config_files(), before)
        self.assertEqual(
            (saved["sheet"], saved["cube"], saved["dim_inactive"], saved["moving_opacity"]), (False, False, 0, 1.0)
        )
        self.assertNotIn("window_borders", saved)  # KDE's automatic size: no preset choice

    def test_values_presets_do_not_have_are_left_out(self):
        (self.config / "kwinrc").write_text(
            OWN_KWINRC.replace("HIAX", "HIAXF")
            + "BorderSize=Huge\nBorderSizeAuto=false\n\n[Plugins]\ntranslucencyEnabled=true\n"
            "diminactiveEnabled=true\n\n[Effect-blur]\nSaturation=900\n"
        )
        saved = capture.read_effects(self.runner)
        for key in ("titlebar_buttons", "window_borders"):
            self.assertNotIn(key, saved)
        self.assertEqual(saved["blur_saturation"], 500)
        self.assertEqual((saved["moving_opacity"], saved["dim_inactive"]), (0.8, 25))  # KWin's own defaults
        self.assertEqual(schema.validate({**presets.find("nord-minimal").data, "effects": saved}), [])


class ShownSettingsTests(unittest.TestCase):
    """``plasmify theme`` lists what the step writes, not "left as it is"."""

    def test_left_out_effects_show_their_defaults(self):
        anime = presets.find("catppuccin-anime")
        for name, shown in (("cube", "off"), ("fall-apart", "off"), ("sheet", "off"), ("dim-inactive", "off")):
            with self.subTest(name=name):
                self.assertEqual(cli.SETTINGS[name].show(anime), shown)
        self.assertEqual(cli.SETTINGS["wobbly"].show(presets.find("rose-pine-cozy")), "on, level 3")
        wobbly = presets.customized(anime, {"effects": {"wobbly": True}})
        self.assertEqual(cli.SETTINGS["wobbly"].show(wobbly), "on, level 2")

    def test_a_preset_without_effects_shows_them_left_alone(self):
        data = copy.deepcopy(presets.find("catppuccin-anime").data)
        del data["effects"]
        bare = presets.Preset(data, "user")
        for name in ("cube", "fall-apart", "sheet", "dim-inactive", "wobbly"):
            self.assertEqual(cli.SETTINGS[name].show(bare), cli.LEFT_ALONE, name)


try:
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class WindowTests(unittest.TestCase):
    """The Customize tab shows what the step will write."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def panel(self, preset: presets.Preset, own: dict, tiling_now: str, overrides: dict | None = None):
        from plasmify.gui.customize import CustomizePanel

        def kread(_self, file, group, key, default=""):
            return tiling_now if (file, group, key) == fx.TILING_SWITCH else default

        panel = CustomizePanel()
        self.addCleanup(panel.deleteLater)
        with mock.patch.object(fx, "own_values", return_value=own), mock.patch.object(Runner, "kread", kread):
            panel.load(preset, overrides)
        return panel

    def test_tiling_shows_what_the_apply_leaves(self):
        anime = presets.find("catppuccin-anime")
        panel = self.panel(anime, {fx.TILING_SWITCH: None}, tiling_now="true")  # Tokyo Night Neon's
        self.assertFalse(panel.tiling.isChecked())
        self.assertFalse(panel.tiling_now())
        panel = self.panel(anime, {fx.TILING_SWITCH: "true"}, tiling_now="false")  # the user's own
        self.assertTrue(panel.tiling.isChecked())
        panel = self.panel(anime, {}, tiling_now="true")  # never changed by Plasmify
        self.assertTrue(panel.tiling.isChecked())
        self.assertEqual(panel.overrides(), {})
        tokyo = self.panel(presets.find("tokyo-night-neon"), {fx.TILING_SWITCH: None}, tiling_now="false")
        self.assertTrue(tokyo.tiling.isChecked())  # its own tiling
        bare = copy.deepcopy(anime.data)
        del bare["effects"]
        panel = self.panel(presets.Preset(bare, "user"), {fx.TILING_SWITCH: None}, tiling_now="true")
        self.assertTrue(panel.tiling_now())  # no [effects]: tiling stays as it is now

    def test_tweaks_that_add_effects_show_the_tiling_of_the_apply(self):
        """A tweak gives a preset without [effects] one, so the effects step runs and gives tiling
        back as before Plasmify; the card showed the tiling on now."""
        bare = copy.deepcopy(presets.find("catppuccin-anime").data)
        del bare["effects"]
        panel = self.panel(presets.Preset(bare, "user"), {fx.TILING_SWITCH: None}, "true", {"effects": {"cube": True}})
        self.assertTrue(panel.windows_box.isEnabled())
        self.assertFalse(panel.tiling.isChecked())
        self.assertFalse(panel.tiling_now())

    def test_left_out_effects_show_off(self):
        panel = self.panel(presets.find("catppuccin-anime"), {}, tiling_now="false")
        for box in (panel.fall_apart, panel.sheet, panel.cube):
            self.assertFalse(box.isChecked())
        self.assertEqual(panel.dim_inactive.value(), 0)
        self.assertEqual(panel.moving_opacity.value(), 100)
        for combo in (panel.speed, panel.titlebar, panel.borders):
            self.assertEqual(combo.currentText(), "As before Plasmify")
            self.assertIsNone(combo.currentData())

    def test_panels_without_a_style_say_they_keep_your_own(self):
        anime = self.panel(presets.find("catppuccin-anime"), {}, tiling_now="false")
        self.assertFalse(anime.panel_look_note.isHidden())
        anime.radius.setValue(9)  # now Plasmify's panel style applies
        self.assertTrue(anime.panel_look_note.isHidden())
        self.assertEqual(anime.overrides()["panel_style"], {"radius": 9})
        synthwave = self.panel(presets.find("synthwave-neon"), {}, tiling_now="false")
        self.assertTrue(synthwave.panel_look_note.isHidden())


if __name__ == "__main__":
    unittest.main()
