"""Regressions in the window: saved values the menus do not list, the unsaved bar's count and
padding, Shimmer without pictures, the Shimmer preview's queue and temporary folders, a crash
when the window closed, and a window narrower than its page.

Skipped without PyQt6. Run headless with QT_QPA_PLATFORM=offscreen. Nothing here touches the
desktop: saved tweaks go to a temporary folder.
"""

import dataclasses
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

try:
    from PyQt6.QtCore import QThread
    from PyQt6.QtGui import QFontInfo
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QBoxLayout, QCompleter, QWidget
except ImportError:  # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class GuiFixTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def panel(self, preset_id: str = "crimson-moon", overrides: dict | None = None):
        from plasmify import presets
        from plasmify.gui.customize import CustomizePanel

        panel = CustomizePanel()
        self.addCleanup(panel.deleteLater)
        panel.load(presets.find(preset_id), overrides)
        return panel


class UnlistedValueTests(GuiFixTestCase):
    """`plasmify set interval 2h` (or a captured preset) stores values the menus do not list.
    They were shown as the first choice and written back as that at the next Apply."""

    def test_unlisted_interval_is_shown_and_kept(self):
        from plasmify.gui.customize import INTERVALS

        for seconds, label in ((7200, "2 hours"), (90, "90 seconds"), (600, "10 minutes"), (60, "1 minute")):
            with self.subTest(seconds=seconds):
                panel = self.panel(overrides={"wallpaper": {"interval": seconds}})
                self.assertEqual(panel.interval.currentText(), label)
                self.assertEqual(panel.overrides(), {"wallpaper": {"interval": seconds}})
        panel.load(panel.preset)  # another load drops the item the previous one added
        self.assertEqual(panel.interval.count(), len(INTERVALS))
        self.assertEqual(panel.interval.currentData(), 1800)

    def test_unlisted_shimmer_colour_is_kept(self):
        for colour in ("#ff0000", "blue", "subtext"):
            with self.subTest(colour=colour):
                tweaks = {"wallpaper": {"shimmer": "sparkle", "shimmer_colour": colour}}
                panel = self.panel(overrides=tweaks)
                self.assertEqual(panel.overrides(), tweaks)

    def test_a_presets_own_animation_speed_is_shown(self):
        from plasmify.gui.customize import ANIMATION_SPEEDS

        panel = self.panel("rose-pine-dawn")  # the preset itself plays animations at 1.25
        self.assertEqual(panel.speed.currentData(), 1.25)
        self.assertIn("1.25", panel.speed.currentText())
        self.assertEqual(panel.overrides(), {})  # shown as it is, not as a change
        panel = self.panel("crimson-moon", {"effects": {"animation_speed": 0.75}})
        self.assertEqual(panel.overrides(), {"effects": {"animation_speed": 0.75}})
        panel.load(panel.preset)
        self.assertEqual(panel.speed.count(), len(ANIMATION_SPEEDS))
        self.assertIsNone(panel.speed.currentData())

    def test_themes_that_are_not_installed_are_kept(self):
        tweaks = {"icons": {"theme": "Some-Removed-Icons"}, "cursor": {"name": "Some-Removed-Cursor", "url": None}}
        panel = self.panel(overrides=tweaks)
        self.assertEqual(panel.icon_theme.currentText(), "Some-Removed-Icons (not installed)")
        self.assertEqual(panel.cursor_theme.currentText(), "Some-Removed-Cursor (not installed)")
        self.assertEqual(panel.overrides(), tweaks)


class ShimmerWithoutPicturesTests(GuiFixTestCase):
    def test_shimmer_needs_pictures(self):
        """A preset that keeps the current wallpaper (a capture without one) offered Shimmer, and
        choosing it made settings Plasmify refuses."""
        from plasmify import presets
        from plasmify.gui.customize import CustomizePanel

        nord = presets.find("nord-minimal")
        bare = dataclasses.replace(nord, data={k: v for k, v in nord.data.items() if k != "wallpaper"})
        self.assertEqual(presets.validate(bare.data), [])
        panel = CustomizePanel()
        self.addCleanup(panel.deleteLater)
        panel.load(bare)
        self.assertFalse(panel.shimmer.isEnabled())
        self.assertFalse(panel.shimmer_colour.isEnabled())
        panel.shimmer.setCurrentIndex(panel.shimmer.findData("sparkle"))  # set by code all the same
        self.assertEqual(panel.overrides(), {})
        presets.customized(bare, panel.overrides())  # valid
        panel.load(nord)
        self.assertTrue(panel.shimmer.isEnabled())


class CompleterTests(GuiFixTestCase):
    def test_logo_completer_is_made_once(self):
        panel = self.panel()
        completer = panel.logo_builtin.completer()
        before = len(panel.findChildren(QCompleter))
        for _ in range(5):
            panel.load(panel.preset)
        QApplication.sendPostedEvents(None, 0)  # deferred deletes
        self.assertEqual(len(panel.findChildren(QCompleter)), before)
        self.assertIs(panel.logo_builtin.completer(), completer)
        self.assertGreater(completer.model().rowCount(), 100)


class UnsavedBarTests(GuiFixTestCase):
    def setUp(self):
        from plasmify.gui import customize, main_window

        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(main_window, "TWEAKS_FILE", root / "tweaks.json"),
            mock.patch.object(customize, "CUSTOM_ROOT", root / "custom"),
        ]
        for patch in self.patches:
            patch.start()
        self.main = main_window
        self.window = main_window.MainWindow()
        self.custom = self.window.customize
        self.select("nord-minimal")

    def tearDown(self):
        self.idle()
        self.window.close()
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def idle(self) -> None:
        for thread in self.window.findChildren(QThread):
            thread.wait(20000)
        for _ in range(3):
            self.app.processEvents()

    def select(self, preset_id: str) -> None:
        row = next(i for i, p in enumerate(self.window.presets) if p.id == preset_id)
        self.window.gallery.setCurrentRow(row)
        self.idle()

    def text(self) -> str:
        return self.window.unsaved_bar.message.text()

    def test_count_is_what_is_not_applied(self):
        """One font size counted as 4 (the fonts are saved together), and with tweaks applied
        already one more switch counted all of them."""
        custom, bar = self.custom, self.window.unsaved_bar
        custom.ui_size.setValue(custom.ui_size.value() + 2)
        self.assertEqual(self.text(), "You have 1 change that is not applied yet")
        custom.mono_size.setValue(custom.mono_size.value() + 2)
        self.assertEqual(self.text(), "You have 2 changes that are not applied yet")
        bar.reset_button.click()
        self.idle()
        for box in (custom.cube, custom.sheet, custom.fall_apart):
            box.setChecked(not box.isChecked())
        custom.dim_inactive.setValue(40)
        applied = custom.overrides()
        self.main.remember_tweaks(self.window.current.id, applied, custom.asset_dir())
        self.window.reload_presets(select=self.window.current.id)
        self.idle()
        self.assertFalse(bar.is_shown())
        custom.wobbly.setChecked(not custom.wobbly.isChecked())
        self.assertEqual(self.text(), "You have 1 change that is not applied yet")
        # Glass raises blur and see-through levels too, yet it is one choice; so are new colours.
        custom.glass_tiles.set_value("frosted")
        self.assertEqual(self.text(), "You have 2 changes that are not applied yet")
        custom.set_accent("#ff8800")
        self.assertEqual(self.text(), "You have 3 changes that are not applied yet")
        # Edits kept while looking at another preset are still counted against what is applied.
        self.select("gruvbox-retro")
        self.select("nord-minimal")
        self.assertEqual(self.text(), "You have 3 changes that are not applied yet")

    def test_the_bar_goes_as_soon_as_the_apply_worked(self):
        """The bar about changes not applied yet stayed on screen behind the finished apply
        window until that was closed."""
        from PyQt6.QtCore import QObject, pyqtSignal

        custom, bar = self.custom, self.window.unsaved_bar
        custom.ui_size.setValue(custom.ui_size.value() + 2)
        self.assertTrue(bar.is_shown())
        QTest.qWait(600)  # the plan for these settings
        self.idle()
        seen = []

        class FakeDialog(QObject):
            applied = pyqtSignal()
            succeeded = True

            def __init__(self, *_args, **_kwargs):
                super().__init__()

            def exec(self):
                self.applied.emit()  # what the real one does when the apply is done
                seen.append(bar.is_shown())  # the window is still open here
                return 0

        with mock.patch.object(self.main, "ApplyDialog", FakeDialog):
            self.window.apply_current()
        self.assertEqual(seen, [False])
        self.assertFalse(bar.is_shown())
        self.assertEqual(self.main.applied_tweaks()["nord-minimal"]["overrides"], custom.overrides())

    def test_a_command_line_interval_survives_the_window(self):
        """`plasmify set interval 2h`, then the window: it showed 5 minutes, saw nothing to apply,
        and its next Apply put 5 minutes on the desktop."""
        self.main.remember_tweaks("crimson-moon", {"wallpaper": {"interval": 7200}}, None)
        self.select("crimson-moon")
        self.assertFalse(self.window.unsaved_bar.is_shown())
        self.custom.cube.setChecked(not self.custom.cube.isChecked())
        self.assertEqual(self.window.effective_preset().section("wallpaper")["interval"], 7200)
        self.assertEqual(self.text(), "You have 1 change that is not applied yet")

    def test_changed_settings(self):
        from plasmify.gui.main_window import changed_settings

        before = {"palette": {"accent": "#111111", "base": "#000000"}, "fonts": {"ui": "A", "packages": ["x"]}}
        after = {"palette": {"accent": "#222222", "base": "#ffffff"}, "fonts": {"ui": "B"}, "splash": {"style": "none"}}
        self.assertEqual(changed_settings(before, after), 3)  # colours, the font (not its packages), splash
        self.assertEqual(changed_settings(before, before), 0)
        # A glass look, its see-through amount and whether the window in use stays solid: one choice.
        glassy = {"style": {"glass": "glass"}}
        for style in (
            {"glass": "clear", "transparency": 30, "focused_solid": True},
            {"glass": "glass", "focused_solid": False},
            {"glass": "glass", "transparency": 55},
        ):
            with self.subTest(style=style):
                self.assertEqual(changed_settings(glassy, {"style": style}), 1)
        kvantum = {"style": {"kvantum": {"name": "a"}, "glass": "glass"}}
        self.assertEqual(changed_settings(kvantum, {"style": {"kvantum": {"name": "b"}, "focused_solid": True}}), 2)


class ReservedHeightTests(GuiFixTestCase):
    def test_a_taller_bar_asks_for_more_room(self):
        """reserved_changed never fired, so a bar that wrapped onto more lines covered the last
        options of the page."""
        from plasmify.gui.widgets import UnsavedBar

        host = QWidget()
        self.addCleanup(host.deleteLater)
        host.resize(1400, 600)
        host.show()
        bar = UnsavedBar(host)
        bar.set_count(12)
        bar.set_shown(True)
        before = bar.reserved_height()
        heard = []
        bar.reserved_changed.connect(heard.append)
        host.resize(380, 600)
        QTest.qWait(20)
        self.assertGreater(bar.reserved_height(), before, "the note wraps in a narrow window")
        self.assertEqual(heard, [bar.reserved_height()])


class ShimmerPreviewTests(GuiFixTestCase):
    def test_a_failed_render_keeps_the_newer_request(self):
        from plasmify.gui import shimmer_preview

        preview = shimmer_preview.ShimmerPreview()
        self.addCleanup(preview.deleteLater)
        preview.show()
        jobs = []
        self.addCleanup(lambda: shimmer_preview._RUNNING.difference_update(jobs))  # never started
        with mock.patch.object(shimmer_preview._Render, "start", lambda job, *a: jobs.append(job)):
            preview.show_settings(Path("broken.png"), {"colour": "#ffffff"})
            preview._timer.stop()
            preview._start()
            preview.show_settings(Path("good.png"), {"colour": "#ffffff"})  # while the first one runs
            preview._finished(preview._generation, None, "broken.png could not be read")
            self.assertTrue(preview.view.note.startswith("No preview"))
            self.assertTrue(preview._timer.isActive(), "the newer settings are next")
            preview._timer.stop()
            preview._start()
            self.assertEqual([job._source for job in jobs], [Path("broken.png"), Path("good.png")])
            # A failure of the settings still wanted is not tried again.
            preview._finished(preview._generation, None, "good.png could not be read")
            self.assertIsNone(preview._wanted)
            self.assertFalse(preview._timer.isActive())

    def test_plain_backgrounds_are_removed_when_the_program_quits(self):
        from plasmify.gui import shimmer_preview

        preview = shimmer_preview.ShimmerPreview()
        self.addCleanup(preview.deleteLater)
        scene = preview._plain_scene("#101018", "#d6a84e")
        self.assertTrue(scene.is_file())
        shimmer_preview.remove_scenes()
        self.assertFalse(scene.parent.exists())
        fresh = shimmer_preview.ShimmerPreview()  # later previews get a folder of their own
        self.addCleanup(fresh.deleteLater)
        self.assertTrue(fresh._plain_scene("#101018", "#d6a84e").is_file())


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class ExitTests(unittest.TestCase):
    def test_window_goes_before_the_application(self):
        """The application went first when run() returned, and its teardown sometimes crashed
        on the window's widgets (a segmentation fault after a Customize card was opened). The
        Shimmer preview's plain backgrounds (the first preset shimmers, nothing is downloaded)
        were left behind in the temporary folder, one folder per run."""
        script = textwrap.dedent(
            """
            import sys
            from PyQt6.QtCore import QTimer
            from plasmify.gui import app, main_window

            events = []
            show = main_window.MainWindow.show

            def watch(frame, event, _arg):
                if event == "return" and frame.f_code is app.run.__code__:
                    events.append("run returned")

            def open_a_card_and_close(window):
                show(window)
                window.destroyed.connect(lambda: events.append("window destroyed"))

                def steps():
                    window.tabs.setCurrentWidget(window.customize)
                    window.customize.fonts_box.set_open(True)
                    sys.setprofile(watch)
                    QTimer.singleShot(150, window.close)

                QTimer.singleShot(100, steps)

            main_window.MainWindow.show = open_a_card_and_close
            code = app.run()
            sys.setprofile(None)
            print(events, code)
            """
        )
        with tempfile.TemporaryDirectory() as home:
            env = {k: v for k, v in os.environ.items() if not (k.startswith("XDG_") and k.endswith("_HOME"))}
            env.update(HOME=home, QT_QPA_PLATFORM="offscreen", PYTHONPATH=str(SRC), TMPDIR=home)
            result = subprocess.run(  # noqa: S603 (this Python, a fixed script)
                [sys.executable, "-c", script], env=env, capture_output=True, text=True, timeout=180
            )
            left = [path.name for path in Path(home).glob("Plasmify-*")]
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertEqual(result.stdout.strip().splitlines()[-1], "['window destroyed', 'run returned'] 0")
        self.assertEqual(left, [])


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class NarrowWindowTests(GuiFixTestCase):
    def test_window_is_never_narrower_than_its_page(self):
        """The window could be made narrower than one column of open cards: the page scrolled
        sideways and the unsaved bar cut its Apply button short."""
        from plasmify.gui.main_window import MainWindow

        if not QFontInfo(QApplication.font()).family():
            self.skipTest("no fonts installed: text has no real width (a bare container)")
        window = MainWindow()
        self.addCleanup(window.close)
        window.show()
        custom = window.customize
        window.tabs.setCurrentWidget(custom)
        custom.cube.setChecked(not custom.cube.isChecked())  # the unsaved bar shows

        def settle() -> None:
            for _ in range(10):
                QTest.qWait(30)

        for opened in (False, True):
            for card in custom.cards:
                card.set_open(opened)
            window.resize(500, 700)
            settle()
            self.assertLessEqual(window.minimumSizeHint().width(), 900)
            bar = window.unsaved_bar
            self.assertTrue(bar.is_shown())
            self.assertGreaterEqual(bar.apply_button.width(), bar.apply_button.sizeHint().width(), opened)
            for tab in (1, 0):
                window.tabs.setCurrentIndex(tab)
                settle()
                self.assertEqual(window.scroll.horizontalScrollBar().maximum(), 0, (opened, tab))
            window.tabs.setCurrentWidget(custom)
        window.resize(1600, 900)  # a wide window still shows two columns of cards
        settle()
        self.assertEqual(custom.columns.direction(), QBoxLayout.Direction.LeftToRight)


if __name__ == "__main__":
    unittest.main()
