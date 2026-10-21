"""The newer KWin effects: fall apart, sheet, wobbliness, cube, blur saturation and opacity.

Every test runs the effects step in dry-run mode with kreadconfig6 and the backups replaced,
so nothing reads or writes the real kwinrc, and checks the kwriteconfig6 calls it would make.
"""

import copy
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from plasmify import distro, presets, schema  # noqa: E402
from plasmify.steps import effects as fx  # noqa: E402
from plasmify.steps.base import Context  # noqa: E402
from plasmify.system import CONFIG_HOME, Runner  # noqa: E402

# The keys the step understood before, with fixed values, so other presets cannot change the result.
BASE = {
    "blur": True,
    "blur_strength": 8,
    "open_close": "scale",
    "minimize": "squash",
    "desktop_switch": "slide",
    "wobbly": False,
}
NEW_KEYS = ("wobbly_level", "fall_apart", "sheet", "cube", "blur_saturation", "moving_opacity", "dim_inactive")
EVERY_NEW_KEY = {
    "wobbly": True,
    "wobbly_level": 2,
    "fall_apart": True,
    "sheet": True,
    "cube": True,
    "blur_saturation": 200,
    "moving_opacity": 0.8,
    "dim_inactive": 30,
}
RECONFIGURE = "<kwin reconfigure>"
DASHES = ("--", "–", "—")


def bare_data() -> dict:
    """A valid preset with only a palette, independent of the built-in presets' other sections."""
    palette = presets.load_all(include_user=False)[0].data["palette"]
    return {
        "schema": 1,
        "meta": {"id": "effects-test", "name": "Effects test", "description": "x"},
        "palette": copy.deepcopy(palette),
    }


def make_preset(**effects) -> presets.Preset:
    data = bare_data()
    data["effects"] = dict(BASE, **effects)
    errors = schema.validate(data)
    if errors:
        raise ValueError(errors)
    return presets.Preset(data, "built-in")


def writes(log: list[str]) -> dict[tuple[str, str, str], str]:
    """``(file, group, key) -> value`` for every kwriteconfig6 call in a dry-run log."""
    found = {}
    for line in log:
        args = line.split(" ")
        if args[0] != "kwriteconfig6":
            continue
        groups = [args[i + 1] for i, arg in enumerate(args) if arg == "--group"]
        key = args.index("--key")
        file = Path(args[args.index("--file") + 1]).name  # krevert names the file by its full path
        found[(file, "/".join(groups), args[key + 1])] = " ".join(args[key + 2 :])
    return found


class Result:
    def __init__(self, runner: Runner, ctx: Context, messages: list[str], reads: list[tuple]):
        self.log = runner.log
        self.writes = writes(runner.log)
        self.warnings = ctx.warnings
        self.messages = messages
        self.reads = reads


class EffectsTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.workdir = Path(tmp.name)

    def run_step(
        self,
        preset: presets.Preset,
        readings: dict[tuple[str, str, str], str] | None = None,
        cube_installed: bool = True,
        own: dict | None = None,
    ) -> Result:
        """``own`` is what the user had before Plasmify (see effects.own_values); left out, there
        is no backup, as before the first apply."""
        runner = Runner(dry_run=True)
        reads: list[tuple] = []
        readings = readings or {}

        def fake_kread(file: str, group: str, key: str, default: str = "") -> str:
            reads.append((file, group, key))
            return readings.get((file, group, key), default)

        runner.kread = fake_kread
        runner.kwin_reconfigure = lambda: runner.log.append(RECONFIGURE)
        runner.kwin_reconfigure_effect = lambda name: runner.log.append(f"<reconfigure {name}>")
        messages: list[str] = []
        ctx = Context(preset, runner, self.workdir, log=messages.append)
        with (
            mock.patch.object(fx, "cube_installed", return_value=cube_installed),
            mock.patch.object(fx, "own_values", return_value=own or {}),
        ):
            fx.EffectsStep().apply(ctx)
        return Result(runner, ctx, messages, reads)

    def run_effects(self, readings=None, cube_installed=True, own=None, **effects) -> Result:
        return self.run_step(make_preset(**effects), readings, cube_installed, own)

    def added(self, before: dict, after: dict) -> dict:
        self.assertFalse(set(before) - set(after), "a key written before is no longer written")
        return {k: v for k, v in after.items() if before.get(k) != v}


class NewKeyTests(EffectsTestCase):
    # (keys both runs share, the new key(s), exactly what the new key adds to kwinrc)
    CASES = [
        ({}, {"fall_apart": True}, {("kwinrc", "Plugins", "fallapartEnabled"): "true"}),
        ({}, {"fall_apart": False}, {}),  # the default, written either way
        ({}, {"sheet": True}, {("kwinrc", "Plugins", "sheetEnabled"): "true"}),
        ({}, {"sheet": False}, {}),
        (
            {"wobbly": True},
            {"wobbly_level": 3},
            {("kwinrc", "Effect-wobblywindows", "WobblynessLevel"): "3"},  # Auto and level 2 without it
        ),
        (
            {"wobbly": True},
            {"wobbly_level": 0},
            {("kwinrc", "Effect-wobblywindows", "WobblynessLevel"): "0"},
        ),
        ({"wobbly": True}, {"wobbly_level": 2}, {}),
        ({"wobbly": False}, {"wobbly_level": 4}, {}),
        ({}, {"blur_saturation": 180}, {("kwinrc", "Effect-blur", "Saturation"): "180"}),
        ({"blur": False}, {"blur_saturation": 500}, {}),
        (
            {},
            {"moving_opacity": 0.85},
            {
                ("kwinrc", "Plugins", "translucencyEnabled"): "true",
                ("kwinrc", "Effect-translucency", "MoveResize"): "85",
            },
        ),
        (
            {},
            {"moving_opacity": 0.3},
            {
                ("kwinrc", "Plugins", "translucencyEnabled"): "true",
                ("kwinrc", "Effect-translucency", "MoveResize"): "30",
            },
        ),
        ({}, {"moving_opacity": 1.0}, {}),  # solid, as without the key
        ({}, {"moving_opacity": 1}, {}),
        ({}, {"dim_inactive": 0}, {}),
        ({}, {"blur_saturation": 100}, {}),
        (
            {},
            {"dim_inactive": 40},
            {
                ("kwinrc", "Plugins", "diminactiveEnabled"): "true",
                ("kwinrc", "Effect-diminactive", "Strength"): "40",
            },
        ),
        ({}, {"cube": True}, {("kwinrc", "Plugins", "cubeEnabled"): "true"}),
        ({}, {"cube": False}, {}),
    ]

    def test_each_key_writes_exactly_its_kwinrc_keys(self):
        enough_desktops = {("kwinrc", "Desktops", "Number"): "4"}
        for shared, new, expected in self.CASES:
            with self.subTest(shared=shared, new=new):
                before = self.run_effects(enough_desktops, **shared)
                after = self.run_effects(enough_desktops, **shared, **new)
                self.assertEqual(self.added(before.writes, after.writes), expected)
                self.assertEqual(after.warnings, [])

    def test_left_out_keys_get_the_windows_defaults(self):
        """A left-out key used to keep what the last preset wrote, while the window showed it off:
        after Synthwave Neon, every preset still broke closing windows apart."""
        result = self.run_effects()
        for plugin in ("fallapart", "sheet", "cube", "diminactive"):
            self.assertEqual(result.writes[("kwinrc", "Plugins", f"{plugin}Enabled")], "false", plugin)
        self.assertEqual(result.writes[("kwinrc", "Effect-blur", "Saturation")], "100")
        self.assertEqual(result.writes[("kwinrc", "Effect-translucency", "MoveResize")], "100")
        groups = {group for _file, group, _key in result.writes}
        self.assertFalse(groups & {"Effect-wobblywindows", "Effect-diminactive", "Desktops"})
        # With no backup yet, Plasmify never changed kwinrc: the user's own settings stay as they are.
        for entry in fx.OWN_KEYS:
            self.assertNotIn(entry, result.writes)
        self.assertEqual(result.reads, [], "nothing needs to be read when no new key is set")
        self.assertEqual(result.warnings, [])
        wobbly = self.run_effects(wobbly=True)
        self.assertEqual(wobbly.writes[("kwinrc", "Effect-wobblywindows", "WobblynessLevel")], "2")

    def test_left_out_own_settings_go_back_to_the_users(self):
        own = {entry: None for entry in fx.OWN_KEYS}
        own[("kwinrc", fx.DECORATION, "ButtonsOnLeft")] = "XIA"
        own[("kdeglobals", "KDE", "AnimationDurationFactor")] = "0.5"
        result = self.run_effects(own=own)
        self.assertEqual(result.writes[("kwinrc", fx.DECORATION, "ButtonsOnLeft")], "XIA")
        self.assertEqual(result.writes[("kdeglobals", "KDE", "AnimationDurationFactor")], "0.5")
        for file, group, key in (
            fx.TRANSLUCENCY_SWITCH,
            ("kwinrc", fx.DECORATION, "ButtonsOnRight"),
            ("kwinrc", fx.DECORATION, "BorderSize"),
            ("kwinrc", fx.DECORATION, "BorderSizeAuto"),
        ):
            self.assertEqual(result.writes[(file, group, key)], "--delete", key)  # KDE's default
        self.assertEqual(result.writes[fx.TILING_SWITCH], "false")
        # A preset that sets them wins over what the user had.
        result = self.run_effects(own=own, titlebar_buttons="close", animation_speed=1.5, moving_opacity=0.5)
        self.assertEqual(result.writes[("kwinrc", fx.DECORATION, "ButtonsOnRight")], "X")
        self.assertEqual(result.writes[("kdeglobals", "KDE", "AnimationDurationFactor")], "1.5")
        self.assertEqual(result.writes[fx.TRANSLUCENCY_SWITCH], "true")
        self.assertEqual(result.writes[("kwinrc", fx.DECORATION, "BorderSize")], "--delete")

    def test_every_new_key_is_handled(self):
        """Fails when the schema gains an [effects] key this test file does not know about."""
        known = set(BASE) | set(NEW_KEYS) | {"blur_noise", "tiling", "animation_speed"}
        known |= {"titlebar_buttons", "window_borders"}
        self.assertEqual(set(schema.SCHEMA["effects"][0]), known)

    def test_kwin_is_reconfigured_once_at_the_end(self):
        result = self.run_effects(
            {("kwinrc", "Desktops", "Number"): "2", ("kwinrc", "Plugins", "poloniumEnabled"): "true"},
            tiling=False,
            animation_speed=1.2,
            **EVERY_NEW_KEY,
        )
        self.assertEqual(result.log.count(RECONFIGURE), 1)
        index = result.log.index(RECONFIGURE)
        self.assertFalse([line for line in result.log[index:] if line.startswith("kwriteconfig6")])
        # Then each running effect whose own settings changed reads them again.
        self.assertEqual(
            result.log[index + 1 :],
            [
                "<reconfigure blur>",
                "<reconfigure wobblywindows>",
                "<reconfigure translucency>",
                "<reconfigure diminactive>",
            ],
        )

    def test_a_new_blur_strength_reaches_the_running_blur(self):
        """KWin's reconfigure only loads effects that were turned on: the blur that was running
        kept its old strength, so a light glass blur after a frosted preset still looked frosted."""
        result = self.run_effects(blur_strength=4)
        self.assertEqual(result.writes[("kwinrc", "Effect-blur", "BlurStrength")], "4")
        self.assertIn("<reconfigure blur>", result.log[result.log.index(RECONFIGURE) :])
        self.assertNotIn("<reconfigure blur>", self.run_effects(blur=False).log)

    def test_reconfiguring_one_effect_over_dbus(self):
        from plasmify import system

        runner = Runner(dry_run=True)
        with mock.patch.object(system, "qdbus_tool", return_value="qdbus6"):
            runner.kwin_reconfigure_effect("blur")
        self.assertEqual(runner.log, ["qdbus6 org.kde.KWin /Effects org.kde.kwin.Effects.reconfigureEffect blur"])

    def test_everything_written_is_backed_up(self):
        preset = make_preset(
            tiling=False, animation_speed=1.2, titlebar_buttons="left", window_borders="tiny", **EVERY_NEW_KEY
        )
        result = self.run_step(preset, {("kwinrc", "Desktops", "Number"): "2"})
        files = {file for file, _group, _key in result.writes}
        self.assertEqual(files, {"kwinrc", "kdeglobals"})
        touched = set(fx.EffectsStep().touched_paths(preset))
        self.assertLessEqual({CONFIG_HOME / file for file in files}, touched)

    def test_exclusive_groups_stay_complete(self):
        """KWin does not enforce exclusive groups; fall apart and sheet join them, never replace them."""
        result = self.run_effects(open_close="none", fall_apart=True, sheet=True)
        for plugin in ("scale", "glide", "fade"):
            self.assertEqual(result.writes[("kwinrc", "Plugins", f"{plugin}Enabled")], "false", plugin)
        self.assertEqual(result.writes[("kwinrc", "Plugins", "fallapartEnabled")], "true")
        self.assertEqual(result.writes[("kwinrc", "Plugins", "sheetEnabled")], "true")
        result = self.run_effects(open_close="glide", fall_apart=True)
        self.assertEqual(result.writes[("kwinrc", "Plugins", "glideEnabled")], "true")
        self.assertEqual(result.writes[("kwinrc", "Plugins", "scaleEnabled")], "false")

    def test_builtin_presets_apply_cleanly(self):
        builtins = presets.load_all(include_user=False)
        self.assertGreaterEqual(len(builtins), 5)
        for preset in builtins:
            if not fx.EffectsStep().applies_to(preset):
                continue
            with self.subTest(preset=preset.id):
                result = self.run_step(preset, {("kwinrc", "Desktops", "Number"): "4"})
                self.assertEqual(result.warnings, [])
                self.assertEqual(result.log.count(RECONFIGURE), 1)


class CubeTests(EffectsTestCase):
    def test_missing_cube_warns_and_skips(self):
        for family, package in ((distro.DEBIAN, "kwin-addons"), (distro.OPENSUSE, "kdeplasma6-addons")):
            with self.subTest(family=family):
                found = distro.Distro(id=family, family=family, name=family)
                with mock.patch.object(distro, "detect", return_value=found):
                    result = self.run_effects(cube_installed=False, cube=True)
                self.assertNotIn(("kwinrc", "Plugins", "cubeEnabled"), result.writes)
                self.assertEqual(len(result.warnings), 1)
                self.assertIn(f"the {package} package", result.warnings[0])
                self.assertNotIn(("kwinrc", "Desktops", "Number"), result.reads)
                self.assertEqual(result.log.count(RECONFIGURE), 1)

    def test_missing_cube_on_an_unknown_system_names_every_package(self):
        with mock.patch.object(distro, "detect", return_value=None):
            result = self.run_effects(cube_installed=False, cube=True)
        for package in ("kdeplasma-addons", "kdeplasma6-addons", "kwin-addons"):
            self.assertIn(package, result.warnings[0])
        with mock.patch.object(distro, "detect", side_effect=PermissionError("os-release")):
            self.assertEqual(fx.cube_package(), fx.CUBE_ANY_PACKAGE)

    def test_turning_the_cube_off_works_without_the_package(self):
        result = self.run_effects(cube_installed=False, cube=False)
        self.assertEqual(result.writes[("kwinrc", "Plugins", "cubeEnabled")], "false")
        self.assertEqual(result.warnings, [])

    def test_cube_is_found_in_the_xdg_data_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            home, system = Path(tmp, "home"), Path(tmp, "system")
            home.mkdir()
            system.mkdir()
            env = {"XDG_DATA_DIRS": f"relative/share::{system}"}
            with mock.patch.dict(os.environ, env), mock.patch.object(fx, "DATA_HOME", home):
                self.assertEqual(fx.data_dirs(), [home, system])
                self.assertFalse(fx.cube_installed())
                metadata = system / fx.CUBE_METADATA
                metadata.parent.mkdir(parents=True)
                metadata.write_text("{}")
                self.assertTrue(fx.cube_installed())
                metadata.unlink()
                (home / fx.CUBE_METADATA).parent.mkdir(parents=True)
                (home / fx.CUBE_METADATA).mkdir()  # a folder with that name is not the effect
                self.assertFalse(fx.cube_installed())
            with mock.patch.dict(os.environ, {"XDG_DATA_DIRS": ""}), mock.patch.object(fx, "DATA_HOME", home):
                self.assertEqual(fx.data_dirs(), [home, Path("/usr/local/share"), Path("/usr/share")])

    def test_few_desktops_are_explained_never_added(self):
        for number, shown in (("2", "2"), ("1", "1"), (None, "1"), ("abc", "1"), ("-4", "1"), ("3; rm -rf ~", "1")):
            with self.subTest(number=number):
                readings = {} if number is None else {("kwinrc", "Desktops", "Number"): number}
                result = self.run_effects(readings, cube=True)
                self.assertEqual(result.writes[("kwinrc", "Plugins", "cubeEnabled")], "true")
                hint = [m for m in result.messages if "Meta+C" in m]
                self.assertEqual(len(hint), 1)
                self.assertIn("at least 3 virtual desktops", hint[0])
                self.assertIn(f"there are {shown};", hint[0])
                self.assertFalse([k for k in result.writes if k[1] == "Desktops"])
                self.assertEqual(result.warnings, [])

    def test_enough_desktops_only_mention_the_shortcut(self):
        for number in ("3", "12"):
            with self.subTest(number=number):
                result = self.run_effects({("kwinrc", "Desktops", "Number"): number}, cube=True)
                hint = [m for m in result.messages if "Meta+C" in m]
                self.assertEqual(hint, ["Open the desktop cube with Meta+C"])
                self.assertFalse([k for k in result.writes if k[1] == "Desktops"])


class TilingHintTests(EffectsTestCase):
    def test_hint_when_tiling_is_turned_off(self):
        result = self.run_effects({("kwinrc", "Plugins", "poloniumEnabled"): "true"}, tiling=False)
        self.assertEqual(result.writes[("kwinrc", "Plugins", "poloniumEnabled")], "false")
        self.assertIn(fx.TILING_OFF_HINT, result.messages)
        self.assertEqual(result.warnings, [])

    def test_no_hint_when_tiling_was_already_off(self):
        for current in ("", "false"):
            with self.subTest(current=current):
                result = self.run_effects({("kwinrc", "Plugins", "poloniumEnabled"): current}, tiling=False)
                self.assertEqual(result.writes[("kwinrc", "Plugins", "poloniumEnabled")], "false")
                self.assertNotIn(fx.TILING_OFF_HINT, result.messages)

    def test_no_hint_when_tiling_is_left_alone(self):
        result = self.run_effects({("kwinrc", "Plugins", "poloniumEnabled"): "true"})
        self.assertNotIn(fx.TILING_OFF_HINT, result.messages)
        self.assertNotIn(("kwinrc", "Plugins", "poloniumEnabled"), result.writes)


class ValidationTests(EffectsTestCase):
    BAD = {
        "wobbly_level": [-1, 5, 2.5, True, "3", "high"],
        "fall_apart": ["yes", 1, 0, "true"],
        "sheet": ["no", 1, [True]],
        "cube": ["on", 1, {"enabled": True}],
        "blur_saturation": [99, 501, 150.5, False, "150", -150],
        "moving_opacity": [0.29, 1.01, 0, -1, "0.8", True, math.nan, math.inf],
        "dim_inactive": [1, 5, 9, 91, 100, -10, True, 50.0, "40", "40\n[Plugins]"],
    }
    GOOD = {
        "wobbly_level": [0, 4],
        "fall_apart": [True, False],
        "sheet": [True, False],
        "cube": [True, False],
        "blur_saturation": [100, 500],
        "moving_opacity": [0.3, 1, 1.0],
        "dim_inactive": [0, 10, 90],
    }

    def setUp(self):
        super().setUp()
        self.preset = make_preset()

    def test_out_of_range_values_are_rejected(self):
        self.assertEqual(set(self.BAD), set(NEW_KEYS))
        for key, values in self.BAD.items():
            for value in values:
                with self.subTest(key=key, value=value), self.assertRaises(presets.PresetError):
                    presets.customized(self.preset, {"effects": {key: value}})

    def test_edge_values_are_accepted(self):
        for key, values in self.GOOD.items():
            for value in values:
                with self.subTest(key=key, value=value):
                    effective = presets.customized(self.preset, {"effects": {key: value}})
                    self.assertEqual(effective.section("effects")[key], value)

    def test_unvalidated_values_are_never_written(self):
        """A preset built in code skips validation; the step checks again and names only the key."""
        hostile = {
            "wobbly": True,
            "wobbly_level": "4\n[Plugins]\nkwin4_effect_evilEnabled=true",
            "fall_apart": "true",
            "sheet": 1,
            "cube": "yes",
            "blur_saturation": 10_000,
            "moving_opacity": "0.5 --key evil",
            "dim_inactive": True,
        }
        data = bare_data()
        data["effects"] = dict(BASE, **hostile)
        result = self.run_step(presets.Preset(data, "user"), {("kwinrc", "Desktops", "Number"): "4"})
        baseline = self.run_effects(wobbly=True)
        # Only what the step writes anyway (with defaults), and nothing for the rejected keys.
        self.assertLessEqual(set(result.writes.items()), set(baseline.writes.items()))
        for key in (
            ("kwinrc", "Effect-wobblywindows", "WobblynessLevel"),
            ("kwinrc", "Plugins", "fallapartEnabled"),
            ("kwinrc", "Plugins", "sheetEnabled"),
            ("kwinrc", "Plugins", "cubeEnabled"),
            ("kwinrc", "Effect-blur", "Saturation"),
            ("kwinrc", "Effect-translucency", "MoveResize"),
            ("kwinrc", "Plugins", "diminactiveEnabled"),
        ):
            self.assertNotIn(key, result.writes)  # left as it is
        self.assertEqual(len(result.warnings), len(hostile) - 1)
        for warning in result.warnings:
            self.assertNotIn("evil", warning)
            self.assertNotIn("\n", warning)
        self.assertEqual(result.log.count(RECONFIGURE), 1)


class TextTests(EffectsTestCase):
    def test_user_facing_text_is_plain(self):
        results = [
            self.run_effects({("kwinrc", "Desktops", "Number"): "2"}, cube=True),
            self.run_effects({("kwinrc", "Plugins", "poloniumEnabled"): "true"}, tiling=False),
        ]
        with mock.patch.object(distro, "detect", return_value=None):
            results.append(self.run_effects(cube_installed=False, cube=True))
        texts = [fx.TILING_OFF_HINT, fx.CUBE_ANY_PACKAGE, fx.EffectsStep.description]
        for result in results:
            texts += result.messages + result.warnings
        for text in texts:
            for dash in DASHES:
                self.assertNotIn(dash, text)


if __name__ == "__main__":
    unittest.main()


class PluginSwitchTests(EffectsTestCase):
    """KWin's reconfigure neither loads nor unloads effects: a preset's cube, wobbly windows and
    fall apart stayed off (and its old effects on) until the next login. System Settings writes
    the switches with KConfig's notify flag, and KWin loads or unloads each effect it is told
    about; Plasmify does the same, also when a restore puts kwinrc back."""

    def test_plugin_switches_tell_kwin(self):
        runner = Runner(dry_run=True)
        runner.kwrite("kwinrc", "Plugins", "cubeEnabled", True)
        runner.kwrite("kwinrc", "Effect-blur", "BlurStrength", 4)
        runner.kdelete("kwinrc", "Plugins", "fallapartEnabled")
        self.assertEqual(
            runner.log,
            [
                "kwriteconfig6 --notify --file kwinrc --group Plugins --type bool --key cubeEnabled true",
                "kwriteconfig6 --file kwinrc --group Effect-blur --key BlurStrength 4",
                "kwriteconfig6 --notify --file kwinrc --group Plugins --key fallapartEnabled --delete",
            ],
        )

    def test_every_switch_of_the_step_tells_kwin(self):
        result = self.run_effects({("kwinrc", "Desktops", "Number"): "4"}, open_close="glide", **EVERY_NEW_KEY)
        switches = [line for line in result.log if "--group Plugins" in line]
        self.assertGreaterEqual(len(switches), 12)
        self.assertEqual([line for line in switches if "--notify" not in line], [])
        self.assertNotIn("--notify", " ".join(line for line in result.log if "--group Plugins" not in line))

    def test_a_restore_switches_effects_back_before_the_file(self):
        from plasmify import backup, engine

        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp, "config")
            config.mkdir()
            kwinrc = config / "kwinrc"
            kwinrc.write_text(
                "[Plugins]\ncubeEnabled=true\nscaleEnabled=false\nwobblywindowsEnabled=true\nblurEnabled=true\n"
                "[Effect-blur]\nBlurStrength=4\n"
            )
            snapshot = Path(tmp, "backup")
            entry = {"path": str(kwinrc), "existed": True}
            stored = backup._store_path(snapshot, kwinrc)
            stored.parent.mkdir(parents=True)
            stored.write_text("[Plugins]\nscaleEnabled=true\nblurEnabled=true\n[Effect-blur]\nBlurStrength=12\n")
            part = backup.Backup("x", snapshot, 0, "x", [entry])
            runner = Runner(dry_run=True)
            with mock.patch.object(engine, "CONFIG_HOME", config):
                engine.switch_kwin_plugins(part, runner)
        self.assertEqual(
            runner.log,
            [
                # left out of the old file: back to KWin's default
                "kwriteconfig6 --notify --file kwinrc --group Plugins --key cubeEnabled --delete",
                "kwriteconfig6 --notify --file kwinrc --group Plugins --key scaleEnabled true",
                "kwriteconfig6 --notify --file kwinrc --group Plugins --key wobblywindowsEnabled --delete",
            ],
        )

    def test_restored_effect_settings_reach_running_effects(self):
        from plasmify import backup, engine

        runner = Runner(dry_run=True)
        part = backup.Backup(
            "x", Path("/nonexistent"), 0, "x", [{"path": str(CONFIG_HOME / "kwinrc"), "existed": True}]
        )
        runner.kread = lambda *_args, **_kwargs: ""
        runner.kwin_reconfigure_effect = lambda name: runner.log.append(f"<reconfigure {name}>")
        with mock.patch.object(engine, "restore_points", return_value=[part]):
            engine.restore(part, runner, log=lambda _: None)
        self.assertEqual(
            [line for line in runner.log if line.startswith("<reconfigure")],
            [f"<reconfigure {name}>" for name in fx.TUNED_EFFECTS],
        )

    def test_plugin_switches_are_read_from_the_plugins_group_only(self):
        text = "[Plugins]\nblurEnabled=true\n# note=x\nkwin4_effect_fooEnabled = false\n[Other]\nxEnabled=true\n"
        self.assertEqual(fx.plugin_switches(text), {"blurEnabled": "true", "kwin4_effect_fooEnabled": "false"})
