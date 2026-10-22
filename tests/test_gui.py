"""Smoke test: the main window builds and every preset can be selected and previewed.

Skipped when PyQt6 is not installed. Run headless with QT_QPA_PLATFORM=offscreen.
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QSize
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class MainWindowSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_window_lists_and_previews_every_preset(self):
        from plasmify.gui.main_window import MainWindow
        from plasmify.gui.preview import preview_pixmap

        window = MainWindow()
        self.assertGreaterEqual(window.gallery.count(), 5)
        for row in range(window.gallery.count()):
            window.gallery.setCurrentRow(row)
            self.assertIsNotNone(window.current)
            pixmap = preview_pixmap(window.current, QSize(320, 180))
            self.assertFalse(pixmap.isNull())
        window.close()

    def test_restore_list_shows_preset_names_as_plain_text(self):
        # Backup labels carry the preset name ("Before <name>"), and Qt renders HTML in tooltips.
        from pathlib import Path
        from unittest import mock

        from plasmify import backup
        from plasmify.gui.dialogs import RestoreDialog

        label = 'Before <b>Nord</b> <a href="https://example.net">y</a>'
        snap = backup.Backup("1", Path("/nonexistent"), 0.0, label, [])
        with mock.patch.object(backup, "list_backups", return_value=[snap]):
            dialog = RestoreDialog()
        tooltip = dialog.list.item(0).toolTip()
        self.assertTrue(tooltip.startswith("<qt>"), tooltip)
        self.assertNotIn("<b>", tooltip)
        self.assertIn("&lt;b&gt;Nord&lt;/b&gt;", tooltip)
        dialog.close()

    def test_customizing_switches_on_the_needed_steps(self):
        from plasmify.gui.main_window import MainWindow

        window = MainWindow()
        row = next(i for i, p in enumerate(window.presets) if p.id == "nord-minimal")
        window.gallery.setCurrentRow(row)
        self.assertFalse(window.checkboxes["panelstyle"].isEnabled())
        custom = window.customize
        custom.radius.setValue(4)
        custom.splash_style.set_value("none")
        self.assertTrue(window.checkboxes["panelstyle"].isChecked())
        self.assertTrue(window.checkboxes["splash"].isChecked())
        self.assertEqual(custom.overrides()["splash"], {"style": "none"})
        window.close()

    def test_drawn_logo_and_fonts_become_overrides(self):
        from plasmify import asciiart, presets
        from plasmify.gui.main_window import MainWindow

        window = MainWindow()
        custom = window.customize
        custom.logo_kind.setCurrentIndex(custom.logo_kind.findData("ascii"))
        custom.ascii_sample.setCurrentIndex(custom.ascii_sample.findData("Cat"))
        custom.ui_size.setValue(12)
        overrides = custom.overrides()
        self.assertEqual(overrides["shell"]["logo_ascii"], asciiart.sample("Cat"))
        self.assertEqual(overrides["fonts"]["ui_size"], 12)
        presets.customized(window.current, overrides)  # validates
        window.close()

    def test_default_window_never_scrolls_sideways(self):
        """From a small 900 px window to a large screen nothing pushes the details wider than the
        window: long tags wrap, long names end in …, the preview moves above the details, and open
        cards that do not fit beside each other stack."""
        from PyQt6.QtCore import QPoint
        from PyQt6.QtGui import QFontInfo
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QScrollArea

        from plasmify.gui.main_window import MainWindow

        if not QFontInfo(QApplication.font()).family():
            self.skipTest("no fonts installed: text has no real width (a bare container)")
        window = MainWindow()
        window.show()
        area = next(a for a in window.findChildren(QScrollArea) if a.isAncestorOf(window.customize))

        def sideways() -> int:
            for _ in range(20):  # a busy machine may need a moment to finish the layout
                QTest.qWait(50)
                if area.horizontalScrollBar().maximum() == 0:
                    break
            return area.horizontalScrollBar().maximum()

        self.assertLessEqual(window.minimumSizeHint().width(), 900)
        custom = window.customize
        for width in (900, 1180, 1600):
            with self.subTest(width=width):
                window.tabs.setCurrentIndex(0)
                custom.panels_box.set_open(False)
                custom.fonts_box.set_open(False)
                window.resize(width, 760)
                QTest.qWait(50)
                self.assertEqual(window.width(), width)
                for row in range(window.gallery.count()):
                    window.gallery.setCurrentRow(row)
                    self.assertEqual(sideways(), 0, window.current.id)
                window.title.setText("(yours)  a_name_without_any_spaces_that_goes_on_and_on_and_on")
                self.assertEqual(sideways(), 0)
                self.assertTrue(window.title.toolTip())
                window.tabs.setCurrentWidget(custom)
                custom.panels_box.set_open(True)
                custom.fonts_box.set_open(True)
                self.assertEqual(sideways(), 0)
                # The card just opened stays in view (below the toolbar that sticks to the top)
                # even when the columns stacked.
                top = custom.fonts_box.mapTo(area.widget(), QPoint(0, 0)).y() - area.verticalScrollBar().value()
                self.assertTrue(0 <= top < area.viewport().height(), top)
                custom.surprise()
                self.assertEqual(sideways(), 0)
        # Shrinking the window while the Customize tab is hidden must not leave it wide.
        window.resize(1900, 760)
        sideways()
        window.tabs.setCurrentIndex(0)
        for width in (1180, 900):
            window.resize(width, 760)
            self.assertEqual(sideways(), 0, width)
        window.close()


if __name__ == "__main__":
    unittest.main()
