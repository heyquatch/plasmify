"""The live Shimmer preview on the Customize tab (offscreen)."""

from __future__ import annotations

import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    from PyQt6.QtWidgets import QApplication

    from plasmify import presets, shimmer
    from plasmify.gui import shimmer_preview
    from plasmify.gui.customize import CustomizePanel
except ImportError:  # pragma: no cover
    QApplication = None


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class ShimmerPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shimmer.available():
            raise unittest.SkipTest("Qt cannot write WebP here")
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, condition, seconds: float = 15.0) -> bool:
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            if condition():
                return True
            time.sleep(0.02)
        return False

    def panel(self) -> CustomizePanel:
        panel = CustomizePanel()
        self.addCleanup(panel.deleteLater)
        self.addCleanup(shimmer_preview.stop_renders)
        panel.resize(1000, 800)
        panel.load(presets.find("crimson-moon"))
        panel.show()
        panel.walls_box.header.setChecked(True)
        return panel

    def test_preview_plays_and_follows_the_settings(self):
        panel = self.panel()
        preview = panel.shimmer_preview
        self.assertTrue(self.wait(lambda: preview.view.movie is not None), preview.view.note)
        self.assertGreater(preview.view.movie.frameCount(), 1)
        first = preview.view.movie
        panel.shimmer_light.setCurrentIndex(panel.shimmer_light.findData("flare"))
        self.assertTrue(self.wait(lambda: preview.view.movie not in (None, first)))
        # Off: the preview goes away with its movie, and the other Shimmer controls rest.
        panel.shimmer.setCurrentIndex(panel.shimmer.findData("none"))
        self.app.processEvents()
        self.assertFalse(preview.isVisible())
        self.assertIsNone(preview.view.movie)
        self.assertFalse(panel.shimmer_light.isEnabled())

    def test_hidden_preview_makes_nothing(self):
        panel = self.panel()
        panel.walls_box.header.setChecked(False)
        started = []
        with mock.patch.object(shimmer_preview._Render, "start", lambda job, *a: started.append(job)):
            panel.shimmer.setCurrentIndex(panel.shimmer.findData("snow"))
            self.wait(lambda: False, 0.6)
        self.assertEqual(started, [])

    def test_plain_background_needs_no_download(self):
        preview = shimmer_preview.ShimmerPreview()
        self.addCleanup(preview.deleteLater)
        scene = preview._plain_scene("#101018", "#d6a84e")
        self.assertIsNotNone(scene)
        self.assertTrue(scene.is_file())
        self.assertEqual(scene.parent, Path(preview._scenes.path()))

    def test_a_broken_picture_shows_a_note(self):
        preview = shimmer_preview.ShimmerPreview()
        self.addCleanup(preview.deleteLater)
        self.addCleanup(shimmer_preview.stop_renders)
        preview.resize(320, 200)
        preview.show()
        bad = Path(preview._plain_scene("#000000", "#ffffff")).with_name("broken.png")
        bad.write_bytes(b"not a picture")
        preview.show_settings(bad, {"colour": "#ffffff", "style": "sparkle", "light": None, "motion": None})
        self.assertTrue(self.wait(lambda: preview.view.note.startswith("No preview")))
        self.assertIsNone(preview.view.movie)


if __name__ == "__main__":
    unittest.main()
