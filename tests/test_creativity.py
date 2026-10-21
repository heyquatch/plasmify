"""Panel builder, hints with fixes, saved tweaks, sharp terminal photos and apply order."""

import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from plasmify import compat, engine, panels, presets, schema  # noqa: E402
from plasmify.steps.base import Context  # noqa: E402
from plasmify.steps.effects import TITLEBAR_BUTTONS, EffectsStep  # noqa: E402
from plasmify.steps.panelstyle import max_radius, panel_svg  # noqa: E402
from plasmify.steps.shell import init_snippet  # noqa: E402
from plasmify.system import Runner, _unwrap_gvariant  # noqa: E402

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None


def preset_with(**sections) -> presets.Preset:
    data = copy.deepcopy(presets.find("nord-minimal").data)
    for key, value in sections.items():
        data[key] = value
    errors = schema.validate(data)
    if errors:
        raise ValueError(errors)
    return presets.Preset(data, "builtin")


class CompatTests(unittest.TestCase):
    def ids(self, preset, tiling_now=False):
        return {a.id for a in compat.check(preset, tiling_now)}

    def test_tiling_hides_dodging_dock(self):
        layout = {"panels": panels.compact(panels.starter("topbar-dock"))}
        self.assertIn("tiling-hidden", self.ids(preset_with(layout=layout), tiling_now=True))
        self.assertNotIn("tiling-hidden", self.ids(preset_with(layout=layout), tiling_now=False))
        effects = dict(presets.find("nord-minimal").section("effects"), tiling=False)
        self.assertNotIn("tiling-hidden", self.ids(preset_with(layout=layout, effects=effects), tiling_now=True))

    def test_missing_launcher_and_tray(self):
        layout = {"panels": [{"edge": "bottom", "widgets": ["tasks", "clock"]}]}
        found = self.ids(preset_with(layout=layout))
        self.assertTrue({"no-launcher", "no-tray"} <= found)

    def test_radius_cap(self):
        layout = {"panels": [{"edge": "top", "thickness": 24, "widgets": ["launcher", "tray"]}]}
        found = self.ids(preset_with(layout=layout, panel_style={"radius": 20}))
        self.assertIn("radius-capped", found)
        self.assertEqual(max_radius(layout), 10)

    def test_side_panel_titles(self):
        layout = {"panels": [{"edge": "left", "widgets": ["launcher", "tasks_labels", "tray"]}]}
        self.assertIn("labels-vertical", self.ids(preset_with(layout=layout)))

    def test_builtin_presets_have_no_warnings(self):
        builtins = [p for p in presets.load_all() if p.builtin]
        self.assertGreaterEqual(len(builtins), 5)  # never pass by testing nothing
        for preset in builtins:
            warnings = [a for a in compat.check(preset) if a.level == "warning"]
            self.assertEqual(warnings, [], preset.id)


class NewOptionTests(unittest.TestCase):
    def test_titlebar_and_borders_are_written(self):
        effects = dict(presets.find("nord-minimal").section("effects"), titlebar_buttons="left", window_borders="none")
        preset = preset_with(effects=effects)
        runner = Runner(dry_run=True)
        EffectsStep().apply(Context(preset, runner, Path(tempfile.mkdtemp()), log=lambda _: None))
        log = "\n".join(runner.log)
        self.assertIn(f"ButtonsOnLeft {TITLEBAR_BUTTONS['left'][0]}", log)
        self.assertIn("BorderSize None", log)

    def test_panel_colour(self):
        palette = presets.find("nord-minimal").palette
        self.assertIn(palette.accent, panel_svg(palette, 8, 0.9, "none", 4, "accent"))
        self.assertIn("#123456", panel_svg(palette, 8, 0.9, "none", 4, "#123456"))
        for bad in ("red;evil", "url(#x)", "#12345"):
            self.assertIsNotNone(schema.palette_colour(bad), bad)

    def test_every_panel_variant_carries_the_colour(self):
        """Plasma shows the "solid" variant while a window touches the panel (adaptive opacity);
        a missing variant falls back to Breeze and the chosen colour seems to revert."""
        from plasmify.steps import panelstyle

        preset = preset_with(panel_style={"radius": 6, "colour": "#123456"})
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(panelstyle, "THEME_ROOT", Path(tmp)):
            runner = Runner()
            runner.kwrite = lambda *args, **kwargs: None  # never touch the real plasmarc
            panelstyle.PanelStyleStep().apply(Context(preset, runner, Path(tmp), log=lambda _: None))
            root = Path(tmp) / panelstyle.theme_id(preset)
            for variant in ("", "translucent", "opaque", "solid"):
                svg = root / variant / "widgets" / "panel-background.svg"
                with self.subTest(variant=variant or "normal"):
                    self.assertIn("#123456", svg.read_text())

    def test_unknown_fastfetch_module_is_refused(self):
        data = copy.deepcopy(presets.find("nord-minimal").data)
        data["shell"]["fastfetch_modules"] = ["cpu", '"; rm -rf ~ ; "']
        self.assertTrue(schema.validate(data))


class CaptureTests(unittest.TestCase):
    def test_panels_are_captured_exactly(self):
        from plasmify.capture import panels_from_plasma

        found = [
            {"location": "left", "height": 300, "hiding": "dodgewindows", "lengthMode": "fit", "alignment": "right",
             "floating": True, "widgets": ["org.kde.plasma.kickoff", "org.example.unknown", "org.kde.plasma.icontasks"]},
            {"location": "top", "height": 30, "hiding": "none", "lengthMode": "fill", "floating": False,
             "widgets": ["org.kde.plasma.digitalclock"]},
        ]  # fmt: skip
        notes: list[str] = []
        layout = panels_from_plasma(found, notes)
        self.assertEqual(schema.validate({**presets.find("nord-minimal").data, "layout": layout}), [])
        left, top = panels.layout_panels(layout)
        self.assertEqual((left["edge"], left["thickness"], left["hiding"]), ("left", 128, "dodge"))
        self.assertEqual((left["length"], left["align"], left["widgets"]), ("fit", "end", ["launcher", "tasks"]))
        self.assertEqual((top["floating"], top["widgets"]), (False, ["clock"]))
        self.assertIn("org.example.unknown", notes[0])


class SurpriseTests(unittest.TestCase):
    def test_surprises_are_valid_and_warning_free(self):
        from plasmify import surprise

        builtins = [p for p in presets.load_all() if p.builtin]
        self.assertGreaterEqual(len(builtins), 5)  # never pass by testing nothing
        for preset in builtins:
            for seed in range(40):
                overrides = surprise.suggest(preset, seed, tiling_now=True)
                with self.subTest(preset=preset.id, seed=seed):
                    self.assertTrue(overrides)
                    candidate = presets.customized(preset, overrides)
                    self.assertEqual([a.id for a in compat.check(candidate, True) if a.level == "warning"], [])

    def test_same_seed_same_surprise(self):
        from plasmify import surprise

        preset = presets.find("rose-pine-cozy")
        self.assertEqual(surprise.suggest(preset, 11), surprise.suggest(preset, 11))
        self.assertNotEqual(surprise.suggest(preset, 11), surprise.suggest(preset, 12))

    def test_builtin_logo_names_exist(self):
        import shutil

        from plasmify.steps.shell import builtin_logos
        from plasmify.surprise import BUILTIN_LOGOS

        if not shutil.which("fastfetch"):
            self.skipTest("fastfetch is not installed")
        self.assertEqual(set(BUILTIN_LOGOS) - builtin_logos(), set())


class TerminalPhotoTests(unittest.TestCase):
    def test_image_logo_uses_a_real_image_protocol(self):
        for shell in ("fish", "bash", "zsh"):
            snippet = init_snippet(shell, True, True, greeting=True, image_logo=True)
            self.assertIn("--logo-type kitty", snippet)
            self.assertIn("--logo-type sixel", snippet)
            self.assertIn("KONSOLE_VERSION", snippet)

    def test_text_logo_keeps_the_plain_call(self):
        self.assertNotIn("--logo-type", init_snippet("fish", True, True, greeting=True))


class ApplyOrderTests(unittest.TestCase):
    def test_script_steps_run_after_the_shell_restart(self):
        preset = preset_with(panel_style={"radius": 8})
        plan = engine.make_plan(preset, ["layout", "wallpaper", "panelstyle", "colors"], check_packages=False)
        order = []
        with (
            mock.patch.object(engine, "_run_step", side_effect=lambda step, *a: order.append(step.id) or []),
            mock.patch.object(engine.system, "restart_shell", side_effect=lambda r: order.append("RESTART") or True),
            mock.patch.object(engine.backup, "create", return_value=mock.Mock(id="test")),
        ):
            engine.apply(plan, Runner(dry_run=False), log=lambda _: None)
        self.assertLess(order.index("panelstyle"), order.index("RESTART"))
        self.assertGreater(order.index("wallpaper"), order.index("RESTART"))
        self.assertGreater(order.index("layout"), order.index("wallpaper"))

    def test_gvariant_replies(self):
        self.assertEqual(_unwrap_gvariant("(uint32 4242,)"), "4242")
        self.assertEqual(_unwrap_gvariant("('[{\"a\": 1}]',)"), '[{"a": 1}]')
        self.assertEqual(_unwrap_gvariant("plain"), "plain")


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class SavedTweakTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_tweaks_survive_apply_and_restart(self):
        """The reported bug: after Apply the Customize tab fell back to the preset's layout,
        so the next Apply removed the dock."""
        from plasmify.gui import main_window

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(main_window, "TWEAKS_FILE", Path(tmp) / "tweaks.json"),
        ):
            window = main_window.MainWindow()
            row = next(i for i, p in enumerate(window.presets) if p.id == "tokyo-night-neon")
            window.gallery.setCurrentRow(row)
            custom = window.customize
            custom.panel_editor.starters.set_value("topbar-dock")
            custom.radius.setValue(13)
            wanted = custom.overrides()
            main_window.remember_tweaks("tokyo-night-neon", wanted, custom.asset_dir())
            window.reload_presets(select="tokyo-night-neon")  # what happens after Apply
            restored = window.customize.overrides()
            self.assertEqual(restored["layout"], wanted["layout"])
            self.assertEqual(restored["panel_style"]["radius"], 13)
            edges = [p["edge"] for p in window.customize.panel_editor.panels()]
            self.assertEqual(edges, ["top", "bottom"])
            window.close()
            fresh = main_window.MainWindow()  # and after restarting the app
            fresh.gallery.setCurrentRow(row)
            self.assertEqual(fresh.customize.overrides()["layout"], wanted["layout"])
            fresh.close()

    def test_panel_editor_edits(self):
        from plasmify.gui.panel_editor import PanelEditor

        editor = PanelEditor()
        editor.set_panels(panels.starter("bottom-panel"))
        editor._select_edge("left")
        editor.enabled.setChecked(True)
        self.assertEqual([p["edge"] for p in editor.panels()], ["bottom", "left"])
        editor._add_widget("media")
        self.assertIn("media", editor.panels()[1]["widgets"])
        editor.style_box.setCurrentIndex(editor.style_box.findData("dock"))
        self.assertEqual(editor.panels()[1]["length"], "fit")
        editor.enabled.setChecked(False)
        self.assertEqual([p["edge"] for p in editor.panels()], ["bottom"])
        editor._select_edge("bottom")
        self.assertFalse(editor.enabled.isEnabled())  # the last panel cannot be removed

    def test_surprise_button_keeps_own_wallpapers(self):
        from plasmify.gui.main_window import MainWindow

        window = MainWindow()
        custom = window.customize
        custom._wall_entries = None
        before = custom.overrides()
        custom.surprise()
        after = custom.overrides()
        self.assertNotEqual(before, after)
        self.assertIn("layout", after)
        presets.customized(window.current, after)  # still a valid preset
        window.close()

    def test_fix_buttons(self):
        from plasmify.gui.main_window import MainWindow

        window = MainWindow()
        custom = window.customize
        custom.panel_editor.set_panels([panels.normalise({"edge": "bottom", "widgets": ["tasks"]})])
        custom.apply_fix("no-launcher")
        custom.apply_fix("no-tray")
        widgets = custom.panel_editor.panels()[0]["widgets"]
        self.assertEqual(widgets[0], "launcher")
        self.assertIn("tray", widgets)
        window.close()


if __name__ == "__main__":
    unittest.main()
