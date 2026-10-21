"""Light and dark: readable colour schemes in both modes and the one-click switch."""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from plasmify import generate, modes, presets, schema  # noqa: E402
from plasmify.palette import Palette, contrast, is_dark, relative_luminance  # noqa: E402
from plasmify.steps.colors import color_scheme  # noqa: E402

try:
    from PyQt6.QtCore import QThread
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None

LIGHT = {
    "base": "#f3f2f2", "mantle": "#e9e7e7", "crust": "#fbfbfb", "surface": "#dfdddd", "overlay": "#8f8a8a",
    "text": "#2e292a", "subtext": "#615758", "red": "#d20415", "green": "#039d10", "yellow": "#ac8303",
    "blue": "#045ad2", "magenta": "#d204d2", "cyan": "#0398a5", "orange": "#d25a04", "accent": "#c61026",
}  # fmt: skip


def scheme_colour(text: str, group: str, key: str) -> str:
    block = re.search(r"\[Colors:" + re.escape(group) + r"\]\n(.*?)\n\n", text, re.S).group(1)
    rgb = re.search(rf"^{key}=(\d+),(\d+),(\d+)$", block, re.M).groups()
    return "#" + "".join(f"{int(c):02x}" for c in rgb)


def builtins() -> list[presets.Preset]:
    found = [p for p in presets.load_all() if p.builtin]
    if len(found) < 5:  # guards against tests that pass because they checked nothing
        raise AssertionError("built-in presets not found")
    return found


class ColourSchemeTests(unittest.TestCase):
    def test_light_schemes_look_like_light_themes(self):
        text = color_scheme(Palette(LIGHT), "X", "X")
        window = scheme_colour(text, "Window", "BackgroundNormal")
        view = scheme_colour(text, "View", "BackgroundNormal")
        button = scheme_colour(text, "Button", "BackgroundNormal")
        self.assertGreater(relative_luminance(view), relative_luminance(window))  # fields brighter than window
        self.assertGreaterEqual(relative_luminance(button), relative_luminance(window))
        self.assertTrue(is_dark(scheme_colour(text, "Complementary", "BackgroundNormal")))
        complementary_text = scheme_colour(text, "Complementary", "ForegroundNormal")
        self.assertGreaterEqual(
            contrast(complementary_text, scheme_colour(text, "Complementary", "BackgroundNormal")), 7
        )
        selected = scheme_colour(text, "Selection", "ForegroundNormal")
        self.assertGreaterEqual(contrast(selected, LIGHT["accent"]), 4.5)

    def test_dark_schemes_are_unchanged(self):
        dark = [p for p in builtins() if p.palette.dark]
        self.assertGreaterEqual(len(dark), 5)
        for preset in dark:
            text = color_scheme(preset.palette, "X", "X")
            self.assertEqual(scheme_colour(text, "View", "BackgroundNormal"), preset.palette.mantle)
            self.assertEqual(scheme_colour(text, "Button", "BackgroundNormal"), preset.palette.surface)


class ModeVariantTests(unittest.TestCase):
    def check_readable(self, palette: dict, label: str) -> None:
        base = palette["base"]
        self.assertGreaterEqual(contrast(palette["text"], base), 7.0, label)
        self.assertGreaterEqual(contrast(palette["subtext"], base), 4.5, label)
        for name in ("accent", "red", "green", "yellow", "blue", "magenta", "cyan", "orange"):
            self.assertGreaterEqual(contrast(palette[name], base), 2.95, f"{label}: {name}")

    def test_every_preset_flips_both_ways_and_stays_readable(self):
        for preset in [*builtins(), presets.Preset({**builtins()[0].data, "palette": LIGHT}, "user")]:
            colours = preset.palette.colors
            for dark in (True, False):
                variant = {k: v for k, v in generate.mode_variant(colours, dark).items() if v is not None}
                with self.subTest(preset=preset.id, dark=dark):
                    self.assertEqual(is_dark(variant["base"]), dark)
                    self.check_readable(variant, f"{preset.id} dark={dark}")
                    self.assertEqual(schema.validate(presets.merge(preset.data, {"palette": variant})), [])
                    # The accent keeps its hue.
                    self.assertLess(
                        generate.hue_distance(generate.to_hsl(variant["accent"])[0],
                                              generate.to_hsl(colours["accent"])[0]), 6)  # fmt: skip


class KvantumRuleTests(unittest.TestCase):
    def test_a_dark_kvantum_theme_never_paints_a_light_palette(self):
        preset = presets.find("catppuccin-anime")
        self.assertIn("kvantum", preset.section("style"))
        light = generate.mode_variant(preset.palette.colors, False)
        self.assertNotIn("kvantum", presets.customized(preset, {"palette": light}).section("style"))
        self.assertIn("kvantum", presets.customized(preset, {"palette": {"accent": "#ff00ff"}}).section("style"))
        own = {"palette": light, "style": preset.data["style"]}  # chosen on purpose: kept
        self.assertIn("kvantum", presets.customized(preset, own).section("style"))


def round_trip(preset, tweaks):
    """Switch out of the preset's mode and back; returns (switched, back)."""
    applied = presets.customized(preset, tweaks)
    switched, own = modes.switch(preset, tweaks, applied, not preset.palette.dark)
    back, left = modes.switch(preset, switched, presets.customized(preset, switched), preset.palette.dark, own)
    if left is not None:
        raise AssertionError("coming back must not leave a way back behind")
    return switched, back


class SwitchTests(unittest.TestCase):
    def test_round_trip_returns_to_the_preset(self):
        for preset in builtins():
            other, back = round_trip(preset, {})  # light for dark presets, dark for light ones
            applied = presets.customized(preset, other)
            self.assertEqual(applied.palette.dark, not preset.palette.dark, preset.id)
            theme = preset.section("icons").get("theme")
            if theme:
                wanted = modes.icons_for(theme, not preset.palette.dark)
                if wanted is not None:
                    self.assertEqual(applied.section("icons")["theme"], wanted, preset.id)
            self.assertEqual(back, {}, preset.id)
            # Without the remembered way back (an older record), still the preset's own palette.
            fallback, _ = modes.switch(preset, other, applied, preset.palette.dark)
            self.assertEqual(fallback, {}, preset.id)

    def test_other_tweaks_stay(self):
        preset = presets.find("tokyo-night-neon")
        tweaks = {"panel_style": {"radius": 12}, "palette": {"accent": "#ff79c6"}}
        switched, back = round_trip(preset, tweaks)
        self.assertEqual(switched["panel_style"], {"radius": 12})
        self.assertEqual(back, tweaks)  # exactly as before

    def test_round_trip_keeps_every_palette_tweak(self):
        preset = presets.find("catppuccin-anime")
        for tweaks in (
            {"palette": {"accent": "#ff0000"}},
            {"palette": {"accent": "#8839ef"}},  # the preset's hue, but the user's colour
            {"palette": {"base": "#000000", "mantle": "#000000", "crust": "#000000"}},  # OLED black
        ):
            with self.subTest(tweaks=tweaks):
                self.assertEqual(round_trip(preset, tweaks)[1], tweaks)

    def test_without_the_way_back_a_picked_accent_stays(self):
        preset = presets.find("catppuccin-anime")
        tweaks = {"palette": {"accent": "#8839ef"}}
        switched, _ = modes.switch(preset, tweaks, presets.customized(preset, tweaks), False)
        back, _ = modes.switch(preset, switched, presets.customized(preset, switched), True)  # no record
        self.assertEqual(generate.to_hsl(back["palette"]["accent"])[0], generate.to_hsl("#8839ef")[0])

    def test_papirus_light_comes_back_as_papirus_light(self):
        data = presets.merge(builtins()[0].data, {"palette": LIGHT, "icons": {"theme": "Papirus-Light"}})
        data["style"] = {}
        preset = presets.Preset(data, "user")
        switched, own = modes.switch(preset, {}, preset, True)
        self.assertEqual(presets.customized(preset, switched).section("icons")["theme"], "Papirus-Dark")
        self.assertEqual(round_trip(preset, {})[1], {})
        fallback, _ = modes.switch(preset, switched, presets.customized(preset, switched), False)  # no record
        self.assertEqual(fallback, {})

    def test_dark_versions_of_light_themes_stay_readable(self):
        swatches = [generate.Swatch(c, 0.3) for c in ("#f0e6d2", "#88c0d0", "#d08770", "#a3be8c")]
        for accent in generate.accent_candidates(swatches, False, 5) + ["#c61026", "#045ad2", "#7a5c00"]:
            light = generate.build_palette(swatches, accent, dark=False)
            dark = {k: v for k, v in generate.mode_variant(light, True).items() if v}
            with self.subTest(accent=accent):
                # crust is the background of tooltips and inactive title bars in dark schemes
                self.assertGreaterEqual(contrast(dark["text"], dark["crust"]), 7.0)
                text = color_scheme(Palette(dark), "X", "X")
                tip_bg = scheme_colour(text, "Tooltip", "BackgroundNormal")
                self.assertGreaterEqual(contrast(scheme_colour(text, "Tooltip", "ForegroundNormal"), tip_bg), 7.0)
                selected = scheme_colour(text, "Selection", "ForegroundNormal")
                self.assertGreaterEqual(contrast(selected, dark["accent"]), 4.5)

    def test_colours_edited_in_the_other_mode_keep_the_accent_untouched(self):
        preset = presets.find("catppuccin-anime")
        tweaks = {"palette": {"base": "#000000", "mantle": "#000000", "crust": "#000000"}}
        switched, own = modes.switch(preset, tweaks, presets.customized(preset, tweaks), False)
        edited = {**switched, "palette": {**switched["palette"], "base": "#fafafa"}}  # changed while light
        back, _ = modes.switch(preset, edited, presets.customized(preset, edited), True, own)
        self.assertNotIn("palette", back)  # no invented accent

    def test_the_presets_accent_chosen_in_the_other_mode_stays(self):
        preset = presets.find("dracula-night")
        tweaks = {"palette": {"accent": "#ff5555"}}
        switched, own = modes.switch(preset, tweaks, presets.customized(preset, tweaks), False)
        palette = {k: v for k, v in switched["palette"].items() if k != "accent"}  # accent back to default
        edited = {**switched, "palette": palette}
        back, _ = modes.switch(preset, edited, presets.customized(preset, edited), True, own)
        self.assertNotIn("palette", back)  # the preset's accent, not the one from before
        # Other colours edited while light still bring the user's own accent back.
        edited = {**switched, "palette": {**switched["palette"], "base": "#fafafa"}}
        back, _ = modes.switch(preset, edited, presets.customized(preset, edited), True, own)
        self.assertEqual(back["palette"], {"accent": "#ff5555"})

    def test_an_icon_theme_picked_in_the_other_mode_gets_its_flavour(self):
        preset = presets.find("dracula-night")  # Papirus-Dark
        switched, own = modes.switch(preset, {}, presets.customized(preset, {}), False)
        self.assertEqual(switched["icons"], {"theme": "Papirus"})
        edited = {**switched, "icons": {"theme": "breeze"}}  # picked while light
        back, _ = modes.switch(preset, edited, presets.customized(preset, edited), True, own)
        self.assertEqual(back, {"icons": {"theme": "breeze-dark"}})
        edited = {**switched, "icons": {"theme": "Papirus-Light"}}  # the preset's family: no tweak
        back, _ = modes.switch(preset, edited, presets.customized(preset, edited), True, own)
        self.assertEqual(back, {})
        # Untouched icons come back exactly; an older way back without made_icons does as before.
        self.assertEqual(round_trip(preset, {"icons": {"theme": "breeze"}})[1], {"icons": {"theme": "breeze"}})
        older = {k: v for k, v in own.items() if k != "made_icons"}
        edited = {**switched, "icons": {"theme": "breeze"}}
        back, _ = modes.switch(preset, edited, presets.customized(preset, edited), True, older)
        self.assertEqual(back, {})

    def test_a_broken_base_is_a_preset_error(self):
        preset = presets.find("catppuccin-anime")  # has a Kvantum theme, so the mode is checked
        with self.assertRaises(presets.PresetError):
            presets.customized(preset, {"palette": {"base": "#12345g"}})

    def test_carry_moves_colours_into_unapplied_edits(self):
        old = {"layout": {"panels": []}, "palette": {"accent": "#123456"}, "icons": {"folder_color": "red"}}
        new = {"palette": {"base": "#ffffff"}, "icons": {"theme": "Papirus"}}
        carried = modes.carry(old, new)
        self.assertEqual(carried["palette"], {"base": "#ffffff"})
        self.assertEqual(carried["icons"], {"folder_color": "red", "theme": "Papirus"})
        self.assertEqual(carried["layout"], {"panels": []})
        self.assertNotIn("palette", modes.carry(old, {}))
        self.assertIsNone(modes.carry({"icons": None}, new)["icons"])  # a removed section stays removed


class WayBackTests(unittest.TestCase):
    def test_a_damaged_way_back_is_no_way_back(self):
        preset = presets.find("dracula-night")
        light, keep = modes.switch(
            preset,
            {"palette": {"accent": "#ff5555"}},
            presets.customized(preset, {"palette": {"accent": "#ff5555"}}),
            False,
        )
        result = presets.customized(preset, light)
        for junk in ({"palette": "junk", "made": keep["made"]}, {"palette": ["x"]}, {"made": 3},
                     {"icons": ["Papirus"]}, "junk", [keep]):  # fmt: skip
            with self.subTest(junk=junk):
                self.assertIsNone(modes.way_back(junk))
                back, _ = modes.switch(preset, light, result, True, junk)  # never raises
                presets.customized(preset, back)
        self.assertEqual(modes.way_back(keep), keep)


@unittest.skipIf(QApplication is None, "PyQt6 missing")
class SwitchButtonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        from plasmify.gui import customize, main_window

        self.main = main_window
        self.active = None
        self.patches = [
            mock.patch.object(main_window, "TWEAKS_FILE", Path(self.tmp.name, "tweaks.json")),
            mock.patch.object(customize, "CUSTOM_ROOT", Path(self.tmp.name, "custom")),
            mock.patch.object(main_window.engine, "active_preset", lambda found: self.active),
            mock.patch.object(main_window, "live_mode", lambda: self.live),
        ]
        self.live = None  # what the desktop shows; None: unknown
        for patch in self.patches:
            patch.start()
        self.window = main_window.MainWindow()

    def tearDown(self):
        for thread in self.window.findChildren(QThread):
            thread.wait(20000)
        self.window.close()
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def test_nothing_applied_means_nothing_to_switch(self):
        self.assertFalse(self.window.mode_button.isEnabled())

    def run_switch(self) -> list:
        """Click the switch; returns the plans that were applied."""
        applied = []

        class FakeDialog:
            succeeded = True

            def __init__(self, plan, *_a, **_k):
                applied.append(plan)

            def exec(self):
                return 0

        with mock.patch.object(self.main, "ApplyDialog", FakeDialog):
            self.window.switch_mode()
            worker = self.window._mode_worker
            if worker is not None:
                worker.wait(20000)
            self.app.processEvents()
        return applied

    def activate(self, preset_id: str, overrides: dict, steps=None) -> presets.Preset:
        preset = presets.find(preset_id)
        self.active = preset
        self.main.remember_tweaks(preset.id, overrides, None, steps=steps)
        self.window.reload_presets(select=preset.id)
        return preset

    def test_after_a_restore_switching_back_keeps_the_users_accent(self):
        self.activate("dracula-night", {"palette": {"accent": "#ff5555"}}, ["colors"])
        self.live = False  # a restore undid a switch back to dark: light on screen, dark in the record
        self.window._update_mode_button()
        self.assertEqual(self.window.mode_button.text(), "Switch to dark")
        plan = self.run_switch()[0]
        self.assertTrue(plan.preset.palette.dark)
        self.assertEqual(plan.preset.palette.colors["accent"], "#ff5555")  # not a colour made for light

    def test_applying_after_a_restore_keeps_the_way_back(self):
        import time

        self.activate("dracula-night", {"palette": {"accent": "#e06c75"}}, ["colors"])
        self.live = False  # light on screen after a restore, the dark colours in the record
        self.window.refresh_plan()
        end = time.monotonic() + 30
        while time.monotonic() < end and (self.window.plan is None or self.window._plan_timer.isActive()):
            self.app.processEvents()
            time.sleep(0.02)
        for thread in self.window.findChildren(QThread):
            thread.wait(20000)
        self.app.processEvents()
        self.assertIsNotNone(self.window.plan)

        from PyQt6.QtCore import QObject, pyqtSignal

        class FakeDialog(QObject):
            applied = pyqtSignal()
            succeeded = True

            def __init__(self, *_a, **_k):
                super().__init__()

            def exec(self):
                self.applied.emit()  # as the real one does when the apply succeeds
                return 0

        with mock.patch.object(self.main, "ApplyDialog", FakeDialog):
            self.window.apply_current()
        self.assertIsInstance(self.main.applied_tweaks()["dracula-night"].get("own_mode"), dict)
        self.live = False  # the light colours are on screen now; switching back to dark:
        self.window._update_mode_button()
        plan = self.run_switch()[0]
        self.assertTrue(plan.preset.palette.dark)
        self.assertEqual(plan.preset.palette.colors["accent"], "#e06c75")

    def test_switching_the_applied_theme(self):
        steps = ["colors", "style", "icons", "terminal", "shell", "wallpaper", "layout"]
        preset = self.activate("rose-pine-cozy", {"panel_style": {"radius": 11}}, steps)
        self.assertEqual(self.window.mode_button.text(), "Switch to light")
        plan = self.run_switch()[0]
        self.assertFalse(plan.preset.palette.dark)
        self.assertNotIn("kvantum", plan.preset.section("style"))
        self.assertLessEqual({s.id for s in plan.steps}, set(modes.MODE_STEPS))
        self.assertNotIn("wallpaper", {s.id for s in plan.steps})
        record = self.main.applied_tweaks()[preset.id]
        saved = record["overrides"]
        self.assertEqual(saved["panel_style"], {"radius": 11})
        self.assertFalse(is_dark(saved["palette"]["base"]))
        self.assertIn("own_mode", record)
        self.assertEqual(self.window.mode_button.text(), "Switch to dark")
        self.assertFalse(self.window.effective_preset().palette.dark)  # Customize shows it too
        # And back: exactly the tweaks from before.
        self.live = False
        self.run_switch()
        record = self.main.applied_tweaks()[preset.id]
        self.assertEqual(record["overrides"], {"panel_style": {"radius": 11}})
        self.assertNotIn("own_mode", record)

    def test_only_steps_that_were_applied_run(self):
        self.activate("catppuccin-anime", {}, ["colors", "wallpaper"])  # shell, terminal, style unticked
        self.assertEqual({s.id for s in self.run_switch()[0].steps}, {"colors"})

    def test_older_records_switch_colours_style_and_icons(self):
        self.activate("catppuccin-anime", {"panel_style": {"radius": 9}})  # no steps recorded
        self.assertLessEqual({s.id for s in self.run_switch()[0].steps}, {"colors", "style", "icons"})

    def test_a_late_or_superseded_plan_does_nothing(self):
        self.activate("nord-minimal", {}, ["colors"])
        applied = []
        with mock.patch.object(self.main, "ApplyDialog", lambda plan, *a, **k: applied.append(plan)):
            self.window._switch_planned(mock.Mock(steps=[1]), self.window._mode_token - 1, self.active, {}, None, None)
            self.window._closing = True
            self.window._switch_planned(mock.Mock(steps=[1]), self.window._mode_token, self.active, {}, None, None)
            self.window._closing = False
        self.assertEqual(applied, [])

    def test_the_desktop_wins_over_an_old_record(self):
        preset = presets.find("nord-minimal")
        light = {k: v for k, v in generate.mode_variant(preset.palette.colors, False).items() if v}
        self.live = True  # the record says light, the screen is dark (a command-line apply)
        self.activate("nord-minimal", {"palette": light}, ["colors"])
        self.assertEqual(self.window.mode_button.text(), "Switch to light")
        plan = self.run_switch()[0]
        self.assertFalse(plan.preset.palette.dark)

    def test_customize_follows_the_desktop_after_a_restore(self):
        preset = presets.find("nord-minimal")
        light = {k: v for k, v in generate.mode_variant(preset.palette.colors, False).items() if v}
        self.live = True  # restored to dark, while the record still holds the light colours
        self.activate("nord-minimal", {"palette": light, "panel_style": {"radius": 7}}, ["colors"])
        overrides = self.window.customize.overrides()
        self.assertNotIn("palette", overrides)
        self.assertEqual(overrides["panel_style"]["radius"], 7)

    def test_a_switch_waits_for_an_open_dialog(self):
        self.activate("nord-minimal", {}, ["colors"])
        applied = []

        class FakeDialog:
            succeeded = False

            def __init__(self, plan, *_a, **_k):
                applied.append(plan)

            def exec(self):
                return 0

        from PyQt6.QtTest import QTest

        modal = [object()]
        with (
            mock.patch.object(self.main, "ApplyDialog", FakeDialog),
            mock.patch.object(
                self.main.QApplication, "activeModalWidget", side_effect=lambda: modal[0] if modal else None
            ),
        ):
            self.window.switch_mode()
            self.window._mode_worker.wait(20000)
            self.app.processEvents()
            self.assertEqual(applied, [])  # a dialog is open: not now, and not lost
            modal.clear()
            QTest.qWait(900)
        self.assertEqual(len(applied), 1)

    def test_after_a_restore_that_undid_a_switch_back_the_button_follows_the_desktop(self):
        self.activate("catppuccin-anime", {}, ["colors"])  # record: the preset's own (dark) colours
        self.live = False  # but a restore brought the light version back
        self.window._update_mode_button()
        self.assertEqual(self.window.mode_button.text(), "Switch to dark")
        plan = self.run_switch()[0]
        self.assertTrue(plan.preset.palette.dark)

    def test_after_a_restore_the_switch_icon_flavour_goes_too(self):
        preset = presets.find("catppuccin-anime")
        light = {k: v for k, v in generate.mode_variant(preset.palette.colors, False).items() if v}
        self.live = True  # dark on screen; the record still holds the light switch
        self.activate("catppuccin-anime", {"palette": light, "icons": {"theme": "Papirus"}}, ["colors", "icons"])
        overrides = self.window.customize.overrides()
        self.assertNotIn("palette", overrides)
        self.assertNotIn("theme", overrides.get("icons") or {})

    def test_customize_shows_the_other_mode_when_the_desktop_is_in_it(self):
        self.live = False  # a restore undid the switch back: light on screen, record says own colours
        self.activate("catppuccin-anime", {"panel_style": {"radius": 9}}, ["colors"])
        overrides = self.window.customize.overrides()
        self.assertFalse(is_dark(overrides["palette"]["base"]))
        self.assertEqual(overrides["panel_style"]["radius"], 9)

    def test_switch_colours_carried_into_edits_follow_the_desktop(self):
        preset = presets.find("catppuccin-anime")
        self.activate("catppuccin-anime", {}, ["colors", "icons"])
        self.window.customize.load(preset, {"fonts": {"ui_size": 11}})  # an unapplied edit
        self.window.customize.changed.emit()
        self.live = False
        plans = self.run_switch()  # to light; the light colours are carried into the edit
        self.assertTrue(plans)
        self.live = True  # then a restore brings dark back
        self.window.reload_presets(select=preset.id)
        overrides = self.window.customize.overrides()
        self.assertNotIn("palette", overrides)
        self.assertEqual(overrides.get("fonts", {}).get("ui_size"), 11)

    def test_a_forgotten_pending_apply_does_not_block_switches(self):
        self.activate("nord-minimal", {}, ["colors"])
        self.window._apply_when_planned = "some-wizard-theme"
        self.window.gallery.setCurrentRow(
            next(i for i, p in enumerate(self.window.presets) if p.id == "rose-pine-cozy")
        )
        self.assertIsNone(self.window._apply_when_planned)

    def test_unapplied_colour_edits_are_kept_while_browsing(self):
        preset = presets.find("catppuccin-anime")
        light = {k: v for k, v in generate.mode_variant(preset.palette.colors, False).items() if v}
        self.live = True
        self.activate("catppuccin-anime", {}, ["colors"])
        self.window.customize.load(preset, {"palette": light})  # typed in, not applied
        self.window.customize.changed.emit()
        self.window.gallery.setCurrentRow(next(i for i, p in enumerate(self.window.presets) if p.id == "nord-minimal"))
        self.window.gallery.setCurrentRow(next(i for i, p in enumerate(self.window.presets) if p.id == preset.id))
        self.assertEqual(self.window.customize.overrides().get("palette", {}).get("base"), light["base"])

    def test_a_waiting_switch_gives_way_to_a_theme_being_applied(self):
        self.activate("nord-minimal", {}, ["colors"])
        applied = []
        with mock.patch.object(self.main, "ApplyDialog", lambda plan, *a, **k: applied.append(plan)):
            self.window._apply_when_planned = "rose-pine-cozy"  # the theme studio's "apply right away"
            self.window._switch_planned(mock.Mock(steps=[1]), self.window._mode_token, self.active, {}, None, None)
        self.assertEqual(applied, [])
        self.assertIn("cancelled", self.window.statusBar().currentMessage())

    def test_the_presets_own_weather_line_still_asks(self):
        data = presets.merge(presets.find("nord-minimal").data, {"shell": {"fastfetch_modules": ["os", "weather"]}})
        base = presets.Preset(data, "user")
        plan = mock.Mock(preset=base)
        with mock.patch.object(self.main, "confirm", return_value=False) as asked:
            ticked_other = {"shell": {"fastfetch_modules": ["os", "weather", "memory"]}}
            self.assertFalse(self.window._confirm_hosts(plan, ticked_other, base))
            asked.assert_called_once()
        own = presets.find("nord-minimal")
        with mock.patch.object(self.main, "confirm", return_value=False) as asked:
            self.assertTrue(
                self.window._confirm_hosts(
                    mock.Mock(preset=base), {"shell": {"fastfetch_modules": ["os", "weather"]}}, own
                )
            )
            asked.assert_not_called()  # ticked by the user on a preset without it

    def test_tweaks_that_no_longer_validate_do_not_take_the_others_along(self):
        preset = self.activate("nord-minimal", {"panel_style": {"radius": 11}}, ["colors"])
        record = self.main.applied_tweaks()
        record[preset.id]["overrides"]["effects"] = {"window_borders": "huge"}
        self.main.TWEAKS_FILE.write_text(json.dumps(record))
        self.run_switch()
        self.assertEqual(self.main.applied_tweaks()[preset.id]["overrides"]["panel_style"], {"radius": 11})


class SideEffectTests(unittest.TestCase):
    def test_the_colour_step_keeps_the_chosen_border_size(self):
        from plasmify.steps.base import Context
        from plasmify.steps.colors import ColorsStep
        from plasmify.system import Runner

        data = presets.merge(presets.find("nord-minimal").data, {"effects": {"window_borders": "large"}})
        runner = Runner(dry_run=True)
        ColorsStep().apply(
            Context(presets.Preset(data, "user"), runner, Path(tempfile.gettempdir()), log=lambda _: None)
        )
        border = [c for c in runner.log if "BorderSize" in c and "Auto" not in c]
        self.assertTrue(border and "Large" in border[-1], border)

    def test_saving_the_desktop_keeps_the_colours_on_screen(self):
        from plasmify import capture
        from plasmify.steps.colors import scheme_name
        from plasmify.system import Runner

        preset = presets.find("catppuccin-anime")
        light = {k: v for k, v in generate.mode_variant(preset.palette.colors, False).items() if v}
        scheme = color_scheme(Palette(light), scheme_name(preset), "x")

        def kread(file, group, key, default=""):
            if (group, key) == ("General", "ColorScheme"):
                return scheme_name(preset)
            match = re.search(r"\[" + re.escape(group) + r"\]\n(.*?)(\n\n|$)", scheme, re.S)
            found = re.search(rf"^{key}=(.*)$", match.group(1), re.M) if match else None
            return found.group(1) if found else default

        runner = Runner(dry_run=True)
        with mock.patch.object(runner, "kread", side_effect=kread):
            palette = capture.read_palette(runner)
            self.assertFalse(capture._same_colours(palette, preset.palette.colors))
        self.assertTrue(capture._same_colours(preset.palette.colors, preset.palette.colors))


if __name__ == "__main__":
    unittest.main()
