"""The Customize tab overhaul: the mouse wheel never changes a setting by accident, the bar for
changes not applied yet, the toolbar that stays in view, a window that works from 900 px up, the
new window effect controls and GIF pictures.

Skipped without PyQt6. Run headless with QT_QPA_PLATFORM=offscreen. Nothing here touches the
desktop: saved tweaks and copied pictures go to a temporary folder.
"""

import os
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QMimeData, QPoint, QPointF, Qt, QThread, QUrl
    from PyQt6.QtGui import QFontDatabase, QWheelEvent
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import (
        QAbstractSlider,
        QAbstractSpinBox,
        QApplication,
        QComboBox,
        QFontComboBox,
        QPushButton,
        QScrollBar,
        QSlider,
        QSpinBox,
        QWidget,
    )
except ImportError:  # pragma: no cover
    QApplication = None

# The smallest valid GIF: one transparent pixel.
GIF = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00"
    b",\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)


def wheel_event(widget, steps: int = -1) -> "QWheelEvent":
    """A turn of the mouse wheel over the middle of ``widget`` (negative: scroll down)."""
    centre = QPointF(widget.width() / 2, widget.height() / 2)
    return QWheelEvent(
        centre,
        widget.mapToGlobal(centre),
        QPoint(0, 0),
        QPoint(0, 120 * steps),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def wheel(widget, steps: int = -1) -> None:
    QApplication.sendEvent(widget, wheel_event(widget, steps))


def value_of(widget):
    if isinstance(widget, QFontComboBox):
        return widget.currentFont().family()
    if isinstance(widget, QComboBox):
        return widget.currentIndex()
    return widget.value()


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class OverhaulTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from plasmify.gui import customize, main_window

        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(main_window, "TWEAKS_FILE", self.root / "tweaks.json"),
            mock.patch.object(customize, "CUSTOM_ROOT", self.root / "custom"),
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

    def idle(self, rounds: int = 3) -> None:
        for thread in self.window.findChildren(QThread):
            thread.wait(20000)
        for _ in range(rounds):
            self.app.processEvents()

    def settle(self, ms: int = 120) -> None:
        QTest.qWait(ms)
        self.idle()

    def select(self, preset_id: str) -> None:
        row = next(i for i, p in enumerate(self.window.presets) if p.id == preset_id)
        self.window.gallery.setCurrentRow(row)
        self.idle()

    def show_customize(self, width: int = 1180, height: int = 600) -> None:
        self.window.resize(width, height)
        self.window.show()
        self.window.tabs.setCurrentWidget(self.custom)
        self.settle()


class WheelTests(OverhaulTestCase):
    def test_wheel_over_an_unfocused_control_scrolls_the_page(self):
        """The Fonts card once changed the font while the user scrolled past it."""
        custom = self.custom
        self.select("glass-night")  # glass is on, so the See-through slider takes part
        self.show_customize()
        custom.windows_box.set_open(True)
        custom.glass_box.set_open(True)
        custom.fonts_box.set_open(True)
        self.settle()
        area = self.window.scroll
        bar = area.verticalScrollBar()
        controls = [
            custom.open_close,
            custom.transparency.slider,
            custom.transparency.spin,
            custom.ui_font,
            custom.ui_size,
            custom.mono_size,
            custom.blur_strength.slider,
            custom.blur_strength.spin,
            custom.dim_inactive.slider,
        ]
        for control in controls:
            with self.subTest(control=type(control).__name__, name=control.toolTip()[:30]):
                area.ensureWidgetVisible(control, 0, 120)
                self.settle(30)
                self.assertFalse(control.hasFocus())
                before, scrolled = value_of(control), bar.value()
                self.assertLess(scrolled, bar.maximum(), "nothing left to scroll")
                wheel(control, -1)
                self.app.processEvents()
                self.assertEqual(value_of(control), before, "the wheel changed a setting")
                self.assertGreater(bar.value(), scrolled, "the page did not scroll")
        self.assertEqual(custom.change_count, 0)

    def test_wheel_over_the_text_of_a_spin_box_scrolls_too(self):
        from PyQt6.QtWidgets import QLineEdit

        from plasmify.gui.widgets import pass_wheel_on

        custom = self.custom
        self.show_customize()
        for card in (custom.fonts_box, custom.term_box, custom.login_box):
            card.set_open(True)
        self.settle()
        bar = self.window.scroll.verticalScrollBar()
        self.window.scroll.ensureWidgetVisible(custom.ui_size, 0, 120)
        self.settle(30)
        before, scrolled = custom.ui_size.value(), bar.value()
        self.assertLess(scrolled + 200, bar.maximum(), "nothing left to scroll")
        text = custom.ui_size.findChild(QLineEdit)
        # What Qt does with a real wheel turn the text field ignores: offer it to its parents,
        # the guarded spin box first.
        self.assertTrue(pass_wheel_on(text, wheel_event(text)))
        self.assertEqual(custom.ui_size.value(), before)
        self.assertGreater(bar.value(), scrolled)
        step = bar.value() - scrolled
        wheel(custom.ui_size)  # sent to the spin box itself: scrolls once, not twice
        self.assertEqual(bar.value() - scrolled, 2 * step)

    def test_a_focused_control_still_follows_the_wheel(self):
        custom = self.custom
        self.show_customize()
        custom.windows_box.set_open(True)
        self.settle()
        self.window.activateWindow()
        if not QTest.qWaitForWindowActive(self.window, 1000):
            self.skipTest("this platform cannot activate windows")
        box = custom.open_close
        self.window.scroll.ensureWidgetVisible(box, 0, 120)
        box.setFocus(Qt.FocusReason.MouseFocusReason)
        self.settle(30)
        if not box.hasFocus():
            self.skipTest("this platform does not hand out keyboard focus")
        before = box.currentIndex()
        wheel(box, -1)  # down: the next entry
        self.assertNotEqual(box.currentIndex(), before)
        self.assertGreater(custom.change_count, 0)

    def test_every_value_control_in_the_window_is_guarded(self):
        guarded = (QComboBox, QAbstractSpinBox, QAbstractSlider)
        controls = [w for w in self.window.findChildren(guarded) if not isinstance(w, QScrollBar)]
        self.assertGreater(len(controls), 30)
        for control in controls:
            self.assertTrue(control.property("wheel-guarded"), control)
            self.assertEqual(control.focusPolicy(), Qt.FocusPolicy.StrongFocus, control)
        for scroll_bar in self.window.findChildren(QScrollBar):
            self.assertFalse(scroll_bar.property("wheel-guarded"))  # scroll bars keep the wheel

    def test_guard_wheel_is_safe_to_call_again(self):
        from plasmify.gui.widgets import WheelGuard, guard_wheel

        holder = QWidget()
        QComboBox(holder)
        QSpinBox(holder)
        QSlider(holder)
        QScrollBar(holder)
        self.assertEqual(guard_wheel(holder), 3)
        self.assertEqual(guard_wheel(holder), 0)
        self.assertEqual(len(holder.findChildren(WheelGuard)), 1)
        QSpinBox(holder)  # added later
        self.assertEqual(guard_wheel(holder), 1)
        holder.deleteLater()


class FontPickerTests(OverhaulTestCase):
    def test_only_fonts_that_can_write_text_are_offered(self):
        """Noto Color Emoji as the interface font left Dolphin with empty boxes for files."""
        for box in (self.custom.ui_font, self.custom.mono_font):
            with self.subTest(name=box.toolTip()[:30]):
                self.assertEqual(box.writingSystem(), QFontDatabase.WritingSystem.Latin)
                self.assertNotIn("Noto Color Emoji", {box.itemText(i) for i in range(box.count())})


class UnsavedBarTests(OverhaulTestCase):
    def test_bar_follows_changes_that_are_not_applied(self):
        w, custom, bar = self.window, self.custom, self.window.unsaved_bar
        self.show_customize()
        self.assertFalse(bar.is_shown())
        self.assertFalse(w.apply_button.isHidden())
        original = custom.blur_strength.value()  # a value the preset sets
        custom.blur_strength.setValue(original + 3)
        self.assertTrue(bar.is_shown())
        self.assertTrue(w.apply_button.isHidden(), "one Apply at a time")
        self.assertGreater(w.body_layout.contentsMargins().bottom(), 0, "the page keeps room for the bar")
        self.assertEqual(bar.message.text(), "You have 1 change that is not applied yet")
        custom.blur_strength.setValue(original)  # nothing differs any more
        self.assertFalse(bar.is_shown())
        self.assertFalse(w.apply_button.isHidden())
        self.assertEqual(w.body_layout.contentsMargins().bottom(), 0)

    def test_the_tab_the_bar_and_the_command_line_count_alike(self):
        """A cursor's download and the font row's other values are no changes of their own: the
        tab counted a cursor as 2 and a font as 4, the bar 1 each."""
        from plasmify import cli

        custom = self.custom
        cursor = {"cursor": {"name": "Some-Cursor", "url": None}}
        for tweaks, count in ((cursor, 1), ({**cursor, "fonts": {"ui": "Inter"}}, 2), ({"fonts": {"ui": "Inter"}}, 1)):
            with self.subTest(tweaks=tweaks):
                custom.load(custom.preset, tweaks)
                self.idle()
                self.assertEqual(custom.change_count, count)
                self.assertEqual(self.window.unapplied_count(), count)
                self.assertEqual(cli.change_count(custom.preset, tweaks), count)
                self.assertEqual(cli.change_count(custom.preset, custom.overrides()), count)

    def test_reset_in_the_bar_forgets_the_changes(self):
        custom, bar = self.custom, self.window.unsaved_bar
        self.show_customize()
        custom.wobbly.setChecked(not custom.wobbly.isChecked())
        custom.glass_tiles.set_value("frosted")
        custom.transparency.setValue(33)
        self.assertTrue(bar.is_shown())
        bar.reset_button.click()
        self.idle()
        self.assertFalse(bar.is_shown())
        self.assertEqual(custom.change_count, 0)

    def test_bar_hides_after_a_successful_apply_and_reset_returns_to_it(self):
        w, custom, bar = self.window, self.custom, self.window.unsaved_bar
        self.show_customize()
        custom.cube.setChecked(True)
        custom.dim_inactive.setValue(40)
        self.assertTrue(bar.is_shown())
        applied = custom.overrides()
        # What a successful Apply does to the window, without touching the desktop.
        self.main.remember_tweaks(w.current.id, applied, custom.asset_dir())
        w.reload_presets(select=w.current.id)
        self.idle()
        self.assertFalse(bar.is_shown())
        self.assertEqual(custom.overrides(), applied)  # the applied tweaks are still shown
        custom.reset_button.click()  # back to the preset: differs from what is applied
        self.assertTrue(bar.is_shown())
        bar.reset_button.click()  # back to what is applied
        self.idle()
        self.assertFalse(bar.is_shown())
        self.assertEqual(custom.overrides(), applied)

    def test_bar_apply_follows_the_footer_apply(self):
        w, bar = self.window, self.window.unsaved_bar
        for enabled in (True, False, True):
            w.apply_button.setEnabled(enabled)
            self.assertEqual(bar.apply_button.isEnabled(), enabled)

    def test_bar_apply_and_reset_reach_the_window(self):
        with (
            mock.patch.object(self.main.MainWindow, "apply_current") as apply,
            mock.patch.object(self.main.MainWindow, "discard_changes") as discard,
        ):
            window = self.main.MainWindow()
            window.unsaved_bar.apply_button.setEnabled(True)
            window.unsaved_bar.apply_button.click()
            window.unsaved_bar.reset_button.click()
            window.close()
        apply.assert_called_once()
        discard.assert_called_once()

    def test_invalid_settings_show_in_the_footer_and_keep_the_bar(self):
        w, custom = self.window, self.custom
        self.show_customize()
        custom.logo_kind.setCurrentIndex(custom.logo_kind.findData("text"))
        custom.logo_text.setText("$$$")  # cannot be drawn in block letters
        self.idle()
        self.assertIn("not valid", w.summary.text())
        self.assertIn("#da4453", w.summary.styleSheet())
        self.assertTrue(w.unsaved_bar.is_shown())
        self.assertFalse(w.unsaved_bar.apply_button.isEnabled())

    def test_switching_presets_keeps_the_edits_and_the_bar(self):
        custom, bar = self.custom, self.window.unsaved_bar
        self.show_customize()
        custom.sheet.setChecked(True)
        self.assertTrue(bar.is_shown())
        self.select("gruvbox-retro")
        self.assertFalse(bar.is_shown())
        self.select("nord-minimal")
        self.assertTrue(bar.is_shown())
        self.assertTrue(custom.sheet.isChecked())

    def test_broken_saved_tweaks_never_break_the_window(self):
        import json

        for record in (["x"], "text", {"overrides": "zzz", "custom_dir": 5}, {"overrides": {"effects": 7}}):
            with self.subTest(record=record):
                (self.root / "tweaks.json").write_text(json.dumps({"nord-minimal": record}))
                self.select("gruvbox-retro")
                self.select("nord-minimal")
                self.assertFalse(self.window.unsaved_bar.is_shown())
                self.custom.cube.setChecked(not self.custom.cube.isChecked())
                self.assertTrue(self.window.unsaved_bar.is_shown())
                self.window.discard_changes()
                self.assertFalse(self.window.unsaved_bar.is_shown())

    def test_footer_keeps_its_parts(self):
        w = self.window
        for name in ("apply_button", "summary", "delete_button", "save_button", "share_button",
                     "credits_button", "restore_button"):  # fmt: skip
            self.assertTrue(hasattr(w, name), name)
        self.assertTrue(w.delete_button.isHidden())  # built-in presets cannot be deleted
        self.assertEqual(w.apply_button.text(), "Apply theme")
        for button in (w.save_button, w.share_button, w.credits_button, w.restore_button, w.delete_button):
            self.assertTrue(button.text())
            self.assertTrue(button.toolTip())


class StickyToolbarTests(OverhaulTestCase):
    def test_surprise_and_reset_stay_in_view(self):
        custom = self.custom
        self.show_customize(1180, 600)
        for card in custom.cards:
            card.set_open(True)
        self.settle()
        area = self.window.scroll
        viewport = area.viewport()
        area.verticalScrollBar().setValue(area.verticalScrollBar().maximum())
        self.settle()
        self.assertIs(custom.toolbar.parentWidget(), viewport)
        for button in (custom.surprise_button, custom.reset_button):
            self.assertTrue(button.isVisible())
            top = button.mapTo(viewport, QPoint(0, 0)).y()
            self.assertTrue(0 <= top < viewport.height(), top)
        self.assertEqual(custom.toolbar.geometry().top(), 0)
        self.assertTrue(custom.toolbar.property("stuck"))
        area.verticalScrollBar().setValue(0)
        self.settle()
        self.assertFalse(custom.toolbar.property("stuck"))
        self.window.tabs.setCurrentIndex(0)
        self.settle()
        self.assertFalse(custom.toolbar.isVisible())  # only on the Customize tab

    def test_an_opened_card_is_not_hidden_under_the_toolbar(self):
        custom = self.custom
        self.show_customize(900, 600)
        custom.panels_box.set_open(True)
        custom.login_box.set_open(True)
        self.settle()
        area = self.window.scroll
        top = custom.login_box.mapTo(area.widget(), QPoint(0, 0)).y() - area.verticalScrollBar().value()
        self.assertGreaterEqual(top, custom.sticky_height())
        self.assertLess(top, area.viewport().height())

    def test_toolbar_stays_put_outside_a_scroll_area(self):
        from plasmify.gui.customize import CustomizePanel

        panel = CustomizePanel()
        panel.show()
        self.idle()
        self.assertIs(panel.toolbar.parentWidget(), panel._toolbar_slot)
        self.assertEqual(panel.sticky_height(), 0)
        panel.close()
        panel.deleteLater()


class ResponsiveTests(OverhaulTestCase):
    def test_window_fits_from_900_pixels_up(self):
        w = self.window
        self.assertLessEqual(w.minimumSizeHint().width(), 900)
        w.show()
        for width, height in ((900, 600), (1180, 760), (1600, 900)):
            for tab in (0, 1):
                with self.subTest(width=width, tab=tab):
                    w.resize(width, height)
                    w.tabs.setCurrentIndex(tab)
                    self.settle()
                    self.assertEqual(w.width(), width)
                    self.assertEqual(w.scroll.horizontalScrollBar().maximum(), 0)

    def test_preview_stacks_above_the_details_when_narrow(self):
        w = self.window
        w.show()
        w.resize(900, 600)
        self.settle()
        self.assertLessEqual(w.preview.geometry().bottom(), w.title.mapTo(w.preview.parentWidget(), QPoint(0, 0)).y())
        w.resize(1600, 900)
        self.settle()
        self.assertLessEqual(w.preview.geometry().right(), w.title.mapTo(w.preview.parentWidget(), QPoint(0, 0)).x())
        self.assertGreater(w.preview.width(), 512)  # a big screen gets a bigger preview

    def test_starting_layouts_wrap_to_fewer_columns(self):
        grid = self.custom.panel_editor.starters.grid
        self.assertEqual(grid.columns_for(4000), 4)
        self.assertLess(grid.columns_for(250), grid.columns_for(500))
        self.assertEqual(grid.columns_for(0), 1)
        self.assertGreater(grid.heightForWidth(200), grid.heightForWidth(800))

    def test_layouts_survive_odd_sizes(self):
        from plasmify.gui.widgets import AdaptiveRow, FlowLayout, TileGrid

        holder = QWidget()
        for layout in (TileGrid(0, 6), FlowLayout(4), AdaptiveRow(100, 200, 80)):
            self.assertGreaterEqual(layout.heightForWidth(0), 0)  # empty
            self.assertIsNone(layout.itemAt(5))
            self.assertIsNone(layout.takeAt(-1))
        flow = FlowLayout(4, holder)
        for _ in range(6):
            button = QPushButton("A button")
            button.setFixedWidth(100)
            flow.addWidget(button)
        self.assertGreater(flow.heightForWidth(250), flow.heightForWidth(1000))
        self.assertGreater(flow.heightForWidth(-50), 0)
        row = AdaptiveRow(100, 200, 80, spacing=10, parent=QWidget(holder))
        row.addWidget(QPushButton("lead"))
        row.addWidget(QPushButton("rest"))
        self.assertTrue(row.is_wide(190))
        self.assertFalse(row.is_wide(189))
        for width in (1, 50, 189, 190, 400, 10**6):
            self.assertLessEqual(row.lead_width(width), max(width, 1) if not row.is_wide(width) else width - 90)
            self.assertGreater(row.heightForWidth(width), 0)
        holder.deleteLater()


class PanelEditorTests(OverhaulTestCase):
    def test_islands_never_get_a_fourth_piece_or_lose_every_widget(self):
        from plasmify import presets
        from plasmify.gui.panel_editor import islands_widgets

        editor = self.custom.panel_editor
        editor.set_panels(
            [{"edge": "top", "style": "islands", "widgets": ["launcher", "spacer", "clock", "spacer", "tray"]}]
        )
        editor._select_edge("top")
        editor._add_widget("spacer")
        self.assertEqual(editor.panels()[0]["widgets"].count("spacer"), 2)
        for _ in range(8):  # remove whatever is first, again and again
            editor.widget_list.setCurrentRow(0)
            editor._remove_widget()
        widgets = editor.panels()[0]["widgets"]
        self.assertTrue(any(w != "spacer" for w in widgets), widgets)
        presets.customized(self.custom.preset, self.custom.overrides())  # still valid
        self.assertEqual(islands_widgets(["spacer"] * 5 + ["clock"]), ["spacer", "spacer", "clock"])
        self.assertEqual(islands_widgets(["spacer"]), ["spacer", "clock"])
        self.assertEqual(islands_widgets(["launcher", "spacer", "tray"]), ["launcher", "spacer", "tray"])

    def test_switching_a_crowded_bar_to_islands_stays_valid(self):
        from plasmify import presets

        editor = self.custom.panel_editor
        crowded = ["launcher", "spacer", "pager", "spacer", "clock", "spacer", "tray", "spacer"]
        editor.set_panels([{"edge": "bottom", "style": "bar", "widgets": crowded}])
        editor._select_edge("bottom")
        editor.style_box.setCurrentIndex(editor.style_box.findData("islands"))
        self.idle()
        self.assertEqual(editor.panels()[0]["widgets"].count("spacer"), 2)
        self.assertEqual(editor.widget_list.count(), len(editor.panels()[0]["widgets"]))  # the list shows it
        presets.customized(self.custom.preset, self.custom.overrides())

    def test_delete_key_goes_through_the_same_rules(self):
        from PyQt6.QtGui import QKeyEvent

        editor = self.custom.panel_editor
        editor.set_panels([{"edge": "top", "style": "islands", "widgets": ["spacer", "clock", "spacer"]}])
        editor._select_edge("top")
        editor.widget_list.setCurrentRow(1)  # the only real widget
        press = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier)
        editor.widget_list.keyPressEvent(press)
        self.assertIn("clock", editor.panels()[0]["widgets"])


class NewControlTests(OverhaulTestCase):
    def test_new_window_controls_become_overrides(self):
        from plasmify import presets

        custom = self.custom
        custom.glass_tiles.set_value("frosted")
        custom.fall_apart.setChecked(True)
        custom.sheet.setChecked(True)
        custom.cube.setChecked(True)
        custom.wobbly.setChecked(True)
        custom.wobbly_level.setValue(4)
        custom.blur.setChecked(True)
        custom.blur_saturation.setValue(250)
        custom.moving_opacity.setValue(70)
        custom.dim_inactive.setValue(40)
        overrides = custom.overrides()
        self.assertEqual(overrides["style"]["glass"], "frosted")
        effects = overrides["effects"]
        expected = {"fall_apart": True, "sheet": True, "cube": True, "wobbly_level": 4, "blur_saturation": 250,
                    "moving_opacity": 0.7, "dim_inactive": 40}  # fmt: skip
        for key, value in expected.items():
            self.assertEqual(effects[key], value, key)
        self.assertGreaterEqual(custom.change_count, len(expected) + 1)
        result = presets.customized(custom.preset, overrides)
        self.assertEqual(result.section("style")["glass"], "frosted")
        # Saved tweaks come back as they were (and count as changes again).
        custom.load(custom.preset, overrides)
        self.assertEqual(custom.overrides(), overrides)
        self.assertEqual(custom.glass_tiles.value(), "frosted")
        self.assertEqual(custom.moving_opacity.value(), 70)

    def test_dependent_controls_follow_their_switch(self):
        custom = self.custom
        custom.wobbly.setChecked(False)
        self.assertFalse(custom.wobbly_level.isEnabled())
        custom.wobbly.setChecked(True)
        self.assertTrue(custom.wobbly_level.isEnabled())
        custom.blur.setChecked(False)
        self.assertFalse(custom.blur_saturation.isEnabled())
        self.assertFalse(custom.blur_noise.isEnabled())
        custom.blur.setChecked(True)
        self.assertTrue(custom.blur_saturation.isEnabled())
        self.assertTrue(custom.blur_noise.isEnabled())

    def test_blur_grain_set_from_the_command_line_stays(self):
        """The tab had no control for blur_noise, so the next Apply dropped it."""
        from plasmify import cli

        custom = self.custom
        custom.load(custom.preset, {"effects": {"blur_noise": 6}})
        self.assertEqual(custom.blur_noise.value(), 6)
        self.assertEqual(custom.overrides(), {"effects": {"blur_noise": 6}})
        self.assertEqual(custom.change_count, cli.change_count(custom.preset, custom.overrides()))
        self.assertEqual(custom.change_count, 1)
        custom.blur_noise.setValue(9)
        self.assertEqual(custom.overrides(), {"effects": {"blur_noise": 9}})

    def test_every_command_line_setting_has_a_control(self):
        """A setting `plasmify set` writes and the tab has no control for is dropped at the next Apply."""
        from plasmify import cli
        from plasmify.gui.customize import dirty_key

        for name, setting in cli.SETTINGS.items():
            for key in setting.keys:
                with self.subTest(setting=name, key=key):
                    self.assertIn(dirty_key(setting.section, key), self.custom._getters)

    def test_dimming_is_never_saved_between_off_and_ten(self):
        from plasmify import schema

        custom = self.custom
        for typed, saved in ((7, 10), (5, 10), (3, 0), (0, 0), (10, 10), (55, 55), (90, 90)):
            custom.dim_inactive.setValue(typed)
            value = custom.overrides()["effects"]["dim_inactive"]
            self.assertEqual(value, saved, typed)
            self.assertIsNone(schema.dim_strength(value))
            self.assertEqual(custom.dim_inactive.value(), saved)  # the control shows what is saved
        custom.dim_inactive.setValue(0)
        self.assertEqual(custom.dim_inactive.spin.text(), "Off")

    def test_out_of_range_and_random_values_stay_valid(self):
        from plasmify import glass, presets

        custom = self.custom
        custom.moving_opacity.setValue(5)
        custom.blur_saturation.setValue(10**6)
        custom.wobbly_level.setValue(-3)
        effects = custom.overrides()["effects"]
        self.assertEqual(
            (effects["moving_opacity"], effects["blur_saturation"], effects["wobbly_level"]), (0.3, 500, 0)
        )
        rng = random.Random(1021)  # noqa: S311 (test data)
        sliders = (custom.wobbly_level, custom.blur_saturation, custom.moving_opacity, custom.dim_inactive,
                   custom.transparency)  # fmt: skip
        checks = (custom.fall_apart, custom.sheet, custom.cube, custom.wobbly, custom.blur)
        for _ in range(150):
            slider = rng.choice(sliders)
            slider.setValue(rng.randint(slider.slider.minimum() - 20, slider.slider.maximum() + 20))
            rng.choice(checks).toggle()
            custom.glass_tiles.set_value(rng.choice(glass.GLASS_LEVELS))
            presets.customized(custom.preset, custom.overrides())  # raises if invalid

    def test_glass_extras_are_not_saved_as_the_users_own(self):
        """Glass raises blur and see-through levels when it is applied; after an apply the tab
        must still show (and keep) the user's own values."""
        custom = self.custom
        custom.blur_strength.setValue(2)
        custom.glass_tiles.set_value("frosted")
        custom.transparency.setValue(44)
        before = custom.overrides()
        self.assertEqual(before["style"], {"glass": "frosted", "transparency": 44})
        custom.load(custom.preset, before)
        self.assertEqual(custom.blur_strength.value(), 2)
        self.assertEqual(custom.overrides(), before)

    def test_preset_values_show_without_becoming_changes(self):
        from plasmify import presets

        base = self.custom.preset
        data = presets.merge(base.data, {"effects": {"dim_inactive": 35, "moving_opacity": 0.55, "cube": True},
                                         "style": {"glass": "subtle"}})  # fmt: skip
        preset = presets.Preset(data, base.id)
        self.custom.load(preset)
        self.assertEqual(self.custom.dim_inactive.value(), 35)
        self.assertEqual(self.custom.moving_opacity.value(), 55)
        self.assertTrue(self.custom.cube.isChecked())
        self.assertEqual(self.custom.glass_tiles.value(), "frosted")  # the older name of the look
        self.assertEqual(self.custom.change_count, 0)


class GlassCardTests(OverhaulTestCase):
    """The Glass card: four ready-made looks drawn from the palette, a See-through slider that
    starts at each look's own amount, and overrides that hold only what differs from the preset."""

    def summary(self) -> str:
        return self.custom.glass_box.header.full_text()

    def test_card_sits_between_windows_and_wallpapers(self):
        custom = self.custom
        self.assertEqual(custom.cards.index(custom.glass_box), custom.cards.index(custom.windows_box) + 1)
        self.assertEqual(custom.cards.index(custom.walls_box), custom.cards.index(custom.glass_box) + 1)
        left = custom.column_layouts[0]
        order = [left.itemAt(i).widget() for i in range(left.count()) if left.itemAt(i).widget()]
        self.assertEqual(order.index(custom.glass_box), order.index(custom.windows_box) + 1)
        self.assertFalse(hasattr(custom, "glass"), "the Windows card no longer has a glass menu")

    def test_tiles_name_and_explain_every_look(self):
        from plasmify import glass

        tiles = self.custom.glass_tiles._buttons
        self.assertEqual([value for _button, value in tiles], list(glass.GLASS_LEVELS))
        for button, value in tiles:
            self.assertEqual(button.text(), glass.LABELS[value])
            self.assertTrue(button.toolTip().endswith("."), value)
            self.assertFalse(button.icon().isNull(), value)

    def test_tiles_draw_for_every_palette(self):
        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QPainter, QPixmap

        from plasmify import glass
        from plasmify.gui.customize import draw_glass_tile

        palettes = [preset.palette.colors for preset in self.window.presets]
        palettes += [{}, {"base": "not a colour", "accent": "#zzzzzz"}, {"base": "#ffffff"}]
        for colours in palettes:
            for ratio in (1.0, 2.0):
                pictures = {}
                for look in (*glass.GLASS_LEVELS, "subtle", "nonsense"):
                    pixmap = QPixmap(round(96 * ratio), round(62 * ratio))
                    pixmap.setDevicePixelRatio(ratio)
                    pixmap.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(pixmap)
                    draw_glass_tile(painter, QRectF(0, 0, 96, 62), look, colours)
                    painter.end()
                    pictures[look] = pixmap.toImage()
                looks = [pictures[look] for look in glass.GLASS_LEVELS]
                for index, picture in enumerate(looks):  # every look looks different
                    for other in looks[index + 1 :]:
                        self.assertNotEqual(picture, other, colours.get("base"))
                self.assertEqual(pictures["subtle"], pictures["frosted"])
                self.assertEqual(pictures["nonsense"], pictures["off"])

    def test_each_look_gives_its_override_and_slider_default(self):
        from plasmify import glass, presets

        custom = self.custom  # Nord: dark colours, no glass
        self.assertEqual(custom.glass_tiles.value(), "off")
        self.assertFalse(custom.transparency.isEnabled())
        self.assertIn("Solid windows", self.summary())
        for look in ("frosted", "glass", "clear"):
            with self.subTest(look=look):
                custom.glass_tiles.set_value(look)
                self.assertEqual(custom.overrides(), {"style": {"glass": look}})
                self.assertEqual(custom.transparency.value(), glass.default_transparency(look, True))
                self.assertTrue(custom.transparency.isEnabled())
                self.assertEqual(custom.change_count, 1)
                result = presets.customized(custom.preset, custom.overrides())
                self.assertEqual(glass.effective_transparency(result.data), custom.transparency.value())
                self.assertIn(f"{glass.LABELS[look]}, {custom.transparency.value()} % see-through", self.summary())
        custom.glass_tiles.set_value("off")
        self.assertEqual(custom.overrides(), {})
        self.assertFalse(custom.transparency.isEnabled())
        self.assertIn("Solid windows", self.summary())

    def test_light_colours_start_less_see_through(self):
        from plasmify import glass

        self.select("catppuccin-latte")
        custom = self.custom
        for look in ("frosted", "glass", "clear"):
            custom.glass_tiles.set_value(look)
            self.assertEqual(custom.transparency.value(), glass.default_transparency(look, False))
            self.assertLess(custom.transparency.value(), glass.default_transparency(look, True))
            self.assertEqual(custom.overrides(), {"style": {"glass": look}})

    def test_slider_is_stored_only_when_it_differs_from_the_look(self):
        from plasmify import glass

        custom = self.custom
        custom.glass_tiles.set_value("glass")
        own = glass.default_transparency("glass", True)
        custom.transparency.setValue(own + 15)
        self.assertEqual(custom.overrides(), {"style": {"glass": "glass", "transparency": own + 15}})
        self.assertEqual(custom.change_count, 1)  # the look and its amount are one choice
        self.assertIn(f"Glass, {own + 15} % see-through", self.summary())
        custom.transparency.setValue(own)  # the look's own amount: nothing to store
        self.assertEqual(custom.overrides(), {"style": {"glass": "glass"}})
        custom.transparency.setValue(own + 20)
        custom.glass_tiles.set_value("clear")  # another look starts at its own amount
        self.assertEqual(custom.transparency.value(), glass.default_transparency("clear", True))
        self.assertEqual(custom.overrides(), {"style": {"glass": "clear"}})
        custom.transparency.setValue(500)  # the slider keeps it within what a preset may store
        self.assertEqual(custom.overrides()["style"]["transparency"], glass.TRANSPARENCY_RANGE[1])

    def test_unsaved_bar_counts_a_look_as_one_change(self):
        bar = self.window.unsaved_bar
        self.select("crimson-moon")  # has a style table of its own
        self.custom.glass_tiles.set_value("glass")
        self.assertEqual(bar.message.text(), "You have 1 change that is not applied yet")
        self.custom.transparency.setValue(61)  # the look and its amount are one choice
        self.assertEqual(bar.message.text(), "You have 1 change that is not applied yet")
        self.custom.focused_solid.setChecked(not self.custom.focused_solid.isChecked())  # and the focus too
        self.assertEqual(bar.message.text(), "You have 1 change that is not applied yet")
        self.assertEqual(self.window.tabs.tabText(1), "Customize (1)")
        self.custom.blur.setChecked(not self.custom.blur.isChecked())
        self.assertEqual(bar.message.text(), "You have 2 changes that are not applied yet")

    def test_picking_the_loaded_look_again_changes_nothing(self):
        from plasmify import presets

        custom = self.custom
        # A record with the older name: away and back leaves it exactly as it was.
        custom.load(custom.preset, {"style": {"glass": "subtle"}})
        custom.glass_tiles.set_value("glass")
        custom.glass_tiles.set_value("frosted")
        self.assertEqual(custom.overrides(), {"style": {"glass": "subtle"}})
        # An amount kept while glass is off comes back with Solid.
        custom.load(custom.preset, {"style": {"transparency": 65}})
        custom.glass_tiles.set_value("frosted")
        custom.glass_tiles.set_value("off")
        self.assertEqual(custom.overrides(), {"style": {"transparency": 65}})
        # A preset's own look and amount: one change away, none back.
        base = custom.preset
        own = presets.Preset(presets.merge(base.data, {"style": {"glass": "glass", "transparency": 55}}), base.id)
        custom.load(own)
        custom.glass_tiles.set_value("clear")
        self.assertEqual(custom.change_count, 1)
        custom.glass_tiles.set_value("glass")
        self.assertEqual(custom.transparency.value(), 55)
        self.assertEqual(custom.overrides(), {})

    def test_tile_blur_keeps_its_width_on_scaled_screens(self):
        from unittest import mock

        from PyQt6.QtCore import QRectF
        from PyQt6.QtGui import QPainter, QPixmap

        from plasmify.gui import customize

        factors = []
        real = customize.soften
        for ratio in (1.0, 2.0):
            pixmap = QPixmap(round(96 * ratio), round(60 * ratio))
            pixmap.setDevicePixelRatio(ratio)
            painter = QPainter(pixmap)
            with mock.patch.object(
                customize, "soften", lambda image, factor: factors.append(factor) or real(image, factor)
            ):
                customize.draw_glass_tile(painter, QRectF(0, 0, 96, 60), "glass", {})
            painter.end()
        self.assertEqual(factors[1], 2 * factors[0])

    def test_saved_values_come_back_unchanged(self):
        """Values the command line wrote (or applied earlier) show as they are and are written
        back as they are, even the older name of a look or an amount that equals the default."""
        from plasmify import glass

        custom = self.custom
        frosted = glass.default_transparency("frosted", True)
        for tweaks, look, shown in (
            ({"style": {"glass": "clear", "transparency": 55}}, "clear", 55),
            ({"style": {"glass": "subtle"}}, "frosted", frosted),
            ({"style": {"glass": "off", "transparency": 30}}, "off", 30),
            ({"style": {"glass": "glass", "transparency": glass.default_transparency("glass", True)}}, "glass",
             glass.default_transparency("glass", True)),
        ):  # fmt: skip
            with self.subTest(tweaks=tweaks):
                custom.load(custom.preset, tweaks)
                self.assertEqual((custom.glass_tiles.value(), custom.transparency.value()), (look, shown))
                self.assertEqual(custom.transparency.isEnabled(), look != "off")
                self.assertEqual(custom.overrides(), tweaks)
        # Picking a look is the user's own change: the older name and the kept amount go.
        custom.load(custom.preset, {"style": {"glass": "subtle", "transparency": 33}})
        custom.glass_tiles.set_value("glass")
        self.assertEqual(custom.overrides(), {"style": {"glass": "glass"}})

    def test_a_record_with_the_older_name_needs_no_apply(self):
        self.main.remember_tweaks("nord-minimal", {"style": {"glass": "subtle"}}, None)
        self.select("gruvbox-retro")
        self.select("nord-minimal")
        self.assertEqual(self.custom.glass_tiles.value(), "frosted")
        self.assertEqual(self.custom.overrides(), {"style": {"glass": "subtle"}})
        self.assertFalse(self.window.unsaved_bar.is_shown())

    def test_applied_glass_comes_back_after_a_reload(self):
        w, custom = self.window, self.custom
        custom.glass_tiles.set_value("glass")
        custom.transparency.setValue(52)
        custom.focused_solid.setChecked(False)
        applied = custom.overrides()
        self.assertEqual(applied, {"style": {"glass": "glass", "transparency": 52, "focused_solid": False}})
        self.main.remember_tweaks(w.current.id, applied, custom.asset_dir())
        w.reload_presets(select=w.current.id)
        self.idle()
        self.assertEqual(custom.overrides(), applied)
        self.assertEqual((custom.glass_tiles.value(), custom.transparency.value()), ("glass", 52))
        self.assertFalse(custom.focused_solid.isChecked())
        self.assertFalse(w.unsaved_bar.is_shown())

    def test_focus_box_is_on_in_every_look_and_can_be_turned_off(self):
        from plasmify import glass, presets

        custom = self.custom  # Nord: dark colours, no glass
        self.assertTrue(custom.focused_solid.isChecked())
        self.assertFalse(custom.focused_solid.isEnabled())
        for look in ("frosted", "glass", "clear"):
            with self.subTest(look=look):
                custom.glass_tiles.set_value(look)
                self.assertTrue(custom.focused_solid.isEnabled())
                self.assertTrue(custom.focused_solid.isChecked())  # on by default, Clear included
                self.assertEqual(custom.overrides(), {"style": {"glass": look}})
                custom.focused_solid.setChecked(False)
                self.assertEqual(custom.overrides(), {"style": {"glass": look, "focused_solid": False}})
                self.assertEqual(custom.change_count, 1)  # the look and its focus are one choice
                result = presets.customized(custom.preset, custom.overrides())
                self.assertFalse(glass.effective_focused_solid(result.data))
                custom.focused_solid.setChecked(True)  # the default: nothing to store
                self.assertEqual(custom.overrides(), {"style": {"glass": look}})
        custom.focused_solid.setChecked(False)
        for look in ("frosted", "clear"):  # turned off, it stays off from look to look
            custom.glass_tiles.set_value(look)
            self.assertFalse(custom.focused_solid.isChecked())
            self.assertEqual(custom.overrides(), {"style": {"glass": look, "focused_solid": False}})
        custom.glass_tiles.set_value("off")
        self.assertEqual(custom.overrides(), {})
        self.assertFalse(custom.focused_solid.isEnabled())

    def test_focus_shows_in_the_summary(self):
        custom = self.custom
        custom.glass_tiles.set_value("frosted")
        self.assertIn("see-through, background windows only", self.summary())
        custom.focused_solid.setChecked(False)
        self.assertNotIn("background windows only", self.summary())
        custom.glass_tiles.set_value("off")
        self.assertIn("Solid windows", self.summary())

    def test_saved_focus_comes_back_unchanged(self):
        custom = self.custom
        for tweaks, checked in (
            ({"style": {"glass": "clear", "focused_solid": True}}, True),
            ({"style": {"glass": "glass", "focused_solid": False}}, False),
            ({"style": {"glass": "glass", "focused_solid": True}}, True),  # the default, kept as it was written
            ({"style": {"glass": "subtle", "transparency": 15}}, True),  # the old subtle look: frosted's own
            ({"style": {"focused_solid": False}}, False),  # kept while glass is off
        ):
            with self.subTest(tweaks=tweaks):
                custom.load(custom.preset, tweaks)
                self.assertEqual(custom.focused_solid.isChecked(), checked)
                self.assertEqual(custom.overrides(), tweaks)
                self.assertEqual(custom.change_count, 1)

    def test_a_presets_own_focus(self):
        """A preset's own choice shows and stays from look to look; ticking the box back to the
        default drops it."""
        from plasmify import presets

        custom = self.custom
        base = custom.preset
        own = presets.Preset(presets.merge(base.data, {"style": {"glass": "glass", "focused_solid": False}}), base.id)
        custom.load(own)
        self.assertFalse(custom.focused_solid.isChecked())
        self.assertEqual(custom.change_count, 0)
        custom.glass_tiles.set_value("clear")
        self.assertFalse(custom.focused_solid.isChecked())
        self.assertEqual(custom.overrides(), {"style": {"glass": "clear"}})
        self.assertEqual(custom.change_count, 1)
        result = presets.customized(own, custom.overrides())
        self.assertEqual(result.section("style"), {"glass": "clear", "focused_solid": False})
        custom.glass_tiles.set_value("glass")
        self.assertEqual(custom.overrides(), {})
        custom.focused_solid.setChecked(True)  # the default: the preset's value is dropped
        self.assertEqual(custom.overrides(), {"style": {"focused_solid": None}})
        result = presets.customized(own, custom.overrides())
        self.assertEqual(result.section("style"), {"glass": "glass"})

    def test_a_value_the_slider_cannot_show_is_not_rewritten(self):
        from plasmify import glass, presets

        base = self.custom.preset
        data = presets.merge(base.data, {"style": {"glass": "glass", "transparency": 95}})
        self.custom.load(presets.Preset(data, base.id))
        self.assertEqual(self.custom.transparency.value(), glass.TRANSPARENCY_RANGE[1])  # shown within range
        self.assertEqual(self.custom.overrides(), {})
        self.assertEqual(self.custom.change_count, 0)
        self.custom.transparency.setValue(60)  # until the user moves the slider
        self.assertEqual(self.custom.overrides(), {"style": {"transparency": 60}})

    def test_a_presets_own_transparency(self):
        """A preset with a transparency of its own (one the user saved) shows it; picking another
        look follows that look's default, which drops the preset's value."""
        from plasmify import glass, presets

        base = self.custom.preset
        preset = presets.Preset(presets.merge(base.data, {"style": {"glass": "glass", "transparency": 45}}), base.id)
        custom = self.custom
        custom.load(preset)
        self.assertEqual((custom.glass_tiles.value(), custom.transparency.value()), ("glass", 45))
        self.assertEqual(custom.overrides(), {})
        custom.glass_tiles.set_value("frosted")
        self.assertEqual(custom.overrides(), {"style": {"glass": "frosted", "transparency": None}})
        result = presets.customized(preset, custom.overrides())
        self.assertEqual(glass.effective_transparency(result.data), glass.default_transparency("frosted", True))
        custom.glass_tiles.set_value("glass")
        custom.transparency.setValue(45)
        self.assertEqual(custom.overrides(), {})  # the preset's own values again
        custom.glass_tiles.set_value("off")
        self.assertEqual(custom.overrides(), {"style": {"glass": "off"}})  # its transparency is not touched

    def test_new_colours_move_a_slider_that_follows_the_look(self):
        from plasmify import glass, presets

        custom = self.custom
        custom.glass_tiles.set_value("glass")
        nord = custom.preset.palette.colors
        latte = presets.find("catppuccin-latte").palette.colors
        custom._colours = {key: value for key, value in latte.items() if nord.get(key) != value}
        custom._colours_changed()  # light colours: the look's lighter default
        self.assertEqual(custom.transparency.value(), glass.default_transparency("glass", False))
        self.assertEqual(custom.overrides()["style"], {"glass": "glass"})
        custom.transparency.setValue(50)
        custom._colours = {}
        custom._colours_changed()  # dark again: the user's own amount stays
        self.assertEqual(custom.transparency.value(), 50)
        self.assertEqual(custom.overrides()["style"], {"glass": "glass", "transparency": 50})

    def test_reset_and_surprise(self):
        from plasmify import glass, presets

        custom = self.custom
        custom.glass_tiles.set_value("clear")
        custom.transparency.setValue(66)
        custom.reset_button.click()
        self.assertEqual(custom.glass_tiles.value(), "off")
        self.assertEqual(custom.overrides(), {})
        self.select("glass-night")
        for _ in range(5):
            custom.surprise()
            self.assertEqual(custom.glass_tiles.value(), glass.level_of(custom.preset.data))
            self.assertNotIn("transparency", custom.overrides().get("style", {}))
            presets.customized(custom.preset, custom.overrides(), custom.asset_dir())


class GifTests(OverhaulTestCase):
    def setUp(self):
        super().setUp()
        self.gif = self.root / "Dancing cat.gif"
        self.gif.write_bytes(GIF)

    def test_pickers_and_drops_accept_gif(self):
        from plasmify.gui import widgets

        self.assertIn("*.gif", widgets.IMAGE_FILTER)
        self.assertIn("GIF", widgets.DropZone("Drop here").toolTip())
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.gif)), QUrl.fromLocalFile(str(self.root / "missing.gif"))])
        self.assertEqual(widgets.image_paths(mime), [self.gif])

    def test_gif_wallpapers_and_logo_are_copied_as_gif(self):
        from plasmify import presets, schema

        custom = self.custom
        custom.use_wallpapers([self.gif])
        entries = custom.overrides()["wallpaper"]["images"]
        self.assertEqual(len(entries), 1)
        name = entries[0]["file"]
        self.assertTrue(name.endswith(".gif"), name)
        self.assertIsNone(schema.IMAGE_FILE(name))
        self.assertEqual((custom.asset_dir() / name).read_bytes(), GIF)
        custom._use_file("logo", self.gif)
        self.assertEqual(custom.overrides()["shell"]["logo_file"], "logo.gif")
        presets.customized(custom.preset, custom.overrides(), custom.asset_dir())

    def test_hostile_pictures_are_refused(self):
        from plasmify.gui import customize

        fake = self.root / "fake.gif"
        fake.write_text("<script>not a picture</script>")
        pipe = self.root / "pipe.gif"
        os.mkfifo(pipe)  # opening it would wait forever for a writer
        device = self.root / "zero.gif"
        device.symlink_to("/dev/zero")
        shown = []
        with mock.patch.object(customize, "message", lambda _parent, title, _text: shown.append(title)):
            for path in (fake, pipe, device, self.root / "missing.gif"):
                self.assertIsNone(self.custom._copy_in(path, "x.gif"), path.name)
            with mock.patch.object(customize, "MAX_PICTURE_BYTES", 10):
                self.assertIsNone(self.custom._copy_in(self.gif, "x.gif"))
            bomb = self.root / "bomb.gif"  # a tiny file whose header claims 60000x60000 pixels
            bomb.write_bytes(GIF[:6] + (60000).to_bytes(2, "little") * 2 + GIF[10:])
            self.assertIsNone(self.custom._copy_in(bomb, "x.gif"))
        self.assertEqual(shown, ["Not an image"] * 4 + ["Picture too large", "Cannot use this picture"])
        self.assertFalse((self.custom.custom_dir() / "x.gif").exists())
        self.assertTrue(customize.looks_like_image(self.gif))
        self.assertFalse(customize.looks_like_image(pipe))

    def test_own_wallpaper_names_are_safe(self):
        from plasmify import schema
        from plasmify.gui.customize import own_wallpaper_name

        for name in ("../../etc/evil.gif", "a b$`;.GIF", "x" * 300 + ".gif", ".gif", "photo.webp", "noext"):
            result = own_wallpaper_name(3, Path(name))
            self.assertNotIn("/", result)
            self.assertIsNone(schema.IMAGE_FILE(result), result)
        self.assertTrue(own_wallpaper_name(1, Path("Dancing cat.gif")).endswith(".gif"))

    def test_theme_studio_takes_a_gif(self):
        from plasmify.gui.wizard import WallpaperWizard

        # Owned by the window as in the app: the garbage collector must not delete the studio
        # while its page fade is still running.
        studio = WallpaperWizard(self.window)
        studio.add_images([self.gif, self.root / "notes.txt"])
        self.assertEqual(studio.images, [self.gif])
        QTest.qWait(300)  # let the fade finish
        studio.close()


if __name__ == "__main__":
    unittest.main()
