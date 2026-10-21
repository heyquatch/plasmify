"""Islands: one panel split into separate floating pieces on an edge."""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from plasmify import capture, panels, presets, schema  # noqa: E402
from plasmify.steps.layout import layout_problem, panel_script  # noqa: E402

try:
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None


def islands(*widgets: str, **extra) -> dict:
    return panels.normalise({"edge": "top", "style": "islands", "widgets": list(widgets), **extra})


class PieceTests(unittest.TestCase):
    def places(self, *widgets: str) -> list[tuple[str, list[str]]]:
        return [(p["align"], p["widgets"]) for p in panels.pieces(islands(*widgets))]

    def test_spaces_mark_start_centre_and_end(self):
        self.assertEqual(
            self.places("launcher", "pager", "spacer", "clock", "spacer", "tray"),
            [("start", ["launcher", "pager"]), ("center", ["clock"]), ("end", ["tray"])],
        )
        self.assertEqual(self.places("clock", "spacer", "tray"), [("start", ["clock"]), ("end", ["tray"])])
        self.assertEqual(self.places("clock", "tray"), [("center", ["clock", "tray"])])
        self.assertEqual(self.places("spacer", "clock", "spacer", "tray"), [("center", ["clock"]), ("end", ["tray"])])

    def test_every_piece_is_a_fitted_bar_with_the_panels_settings(self):
        panel = islands("launcher", "spacer", "tray", thickness=40, floating=False, hiding="dodge")
        for piece in panels.pieces(panel):
            self.assertEqual((piece["style"], piece["length"]), ("bar", "fit"))
            self.assertEqual((piece["thickness"], piece["floating"], piece["hiding"]), (40, False, "dodge"))

    def test_other_styles_stay_one_panel(self):
        for style in ("bar", "dock"):
            panel = panels.normalise({"edge": "top", "style": style, "widgets": ["launcher", "spacer", "tray"]})
            self.assertEqual(panels.pieces(panel), [panel])

    def test_rules(self):
        too_many = {"edge": "top", "style": "islands", "widgets": ["a", "spacer", "b", "spacer", "c", "spacer", "d"]}
        self.assertTrue(panels.problems([too_many]))
        self.assertTrue(panels.problems([{"edge": "top", "style": "islands", "widgets": ["spacer"]}]))
        self.assertEqual(panels.problems([{"edge": "top", "style": "islands", "widgets": ["clock"]}]), [])
        data = presets.merge(
            presets.find("nord-minimal").data, {"layout": {"panels": panels.compact(panels.starter("islands"))}}
        )
        self.assertEqual(schema.validate(data), [])


class BuildTests(unittest.TestCase):
    def test_the_script_makes_one_plasma_panel_per_piece(self):
        layout = panels.starter("islands")
        script = panel_script(layout, "start-here-kde", [])
        self.assertEqual(script.count("new Panel;") - 1, 4)  # three islands and the dock (plus the fallback)
        for side in ('"left"', '"center"', '"right"'):
            self.assertIn(f".alignment = {side};", script)

    def test_problem_report_matches_pieces(self):
        layout = [islands("launcher", "spacer", "clock", "spacer", "tray")]
        found = [
            {"location": "top", "widgets": ["org.kde.plasma.systemtray"]},
            {"location": "top", "widgets": ["org.kde.plasma.kickoff"]},
            {"location": "top", "widgets": ["org.kde.plasma.digitalclock"]},
        ]
        self.assertIsNone(layout_problem(layout, found))
        self.assertIsNotNone(layout_problem(layout, found[:2]))
        wrong = [dict(found[0]), dict(found[1]), {"location": "top", "widgets": ["org.kde.plasma.pager"]}]
        self.assertIsNotNone(layout_problem(layout, wrong))


class CaptureTests(unittest.TestCase):
    def test_fitted_panels_on_one_edge_come_back_as_islands(self):
        found = [
            {"location": "top", "lengthMode": "fit", "alignment": "right", "height": 34, "widgets": ["org.kde.plasma.systemtray"]},
            {"location": "top", "lengthMode": "fit", "alignment": "left", "height": 34, "widgets": ["org.kde.plasma.kickoff"]},
            {"location": "top", "lengthMode": "fit", "alignment": "center", "height": 34, "widgets": ["org.kde.plasma.digitalclock"]},
            {"location": "bottom", "lengthMode": "fit", "alignment": "center", "height": 56, "widgets": ["org.kde.plasma.icontasks"]},
        ]  # fmt: skip
        notes: list[str] = []
        layout = capture.panels_from_plasma(found, notes)
        top = panels.layout_panels(layout)[0]
        self.assertEqual(top["style"], "islands")
        self.assertEqual(top["widgets"], ["launcher", "spacer", "clock", "spacer", "tray"])
        self.assertEqual([p["widgets"] for p in panels.pieces(top)], [["launcher"], ["clock"], ["tray"]])
        self.assertEqual(schema.validate({**presets.find("nord-minimal").data, "layout": layout}), [])

    def test_two_pieces_keep_their_places(self):
        found = [
            {"location": "top", "lengthMode": "fit", "alignment": "left", "widgets": ["org.kde.plasma.kickoff"]},
            {"location": "top", "lengthMode": "fit", "alignment": "right", "widgets": ["org.kde.plasma.systemtray"]},
        ]
        top = panels.layout_panels(capture.panels_from_plasma(found, []))[0]
        self.assertEqual(
            [(p["align"], p["widgets"]) for p in panels.pieces(top)], [("start", ["launcher"]), ("end", ["tray"])]
        )


@unittest.skipIf(QApplication is None, "PyQt6 missing")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_preview_and_editor(self):
        from PyQt6.QtCore import QRectF, QSize
        from PyQt6.QtGui import QPainter, QPixmap

        from plasmify.gui.panel_editor import PanelEditor
        from plasmify.gui.preview import paint_panels

        layout = panels.starter("islands")
        colours = presets.find("nord-minimal").palette.colors
        pixmap = QPixmap(QSize(400, 240))
        painter = QPainter(pixmap)
        free = paint_panels(painter, QRectF(0, 0, 400, 240), layout, colours, {}, 1.0)
        painter.end()
        self.assertLess(free.top(), 34 * 2 + 20)  # the top edge's space is taken once, not per island
        editor = PanelEditor()
        editor.set_panels(layout)
        editor._select_edge("top")
        self.assertEqual(editor.style_box.currentData(), "islands")
        self.assertFalse(editor.islands_hint.isHidden())
        self.assertFalse(editor.length_box.isEnabled())


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class PreviewIslandsTests(unittest.TestCase):
    """Previews draw panels larger than life: pieces on one edge must still not overlap."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_pieces_keep_apart_at_any_size(self):
        from PyQt6.QtCore import QRectF

        from plasmify.gui.preview import _fit_islands

        tall = {
            "edge": "left",
            "style": "islands",
            "thickness": 64,
            "widgets": ["launcher", "pager", "spacer", "tasks", "spacer", "tray", "clock"],
        }
        for height in (120, 270, 540, 1113):
            parts = panels.pieces(panels.normalise(tall))
            _squeeze, offsets, lengths = _fit_islands(parts, QRectF(0, 0, height * 16 / 9, height), height / 560)
            spans = sorted(zip(offsets, lengths, strict=True))
            for (a, la), (b, _lb) in zip(spans, spans[1:], strict=False):
                self.assertLess(a + la, b, f"pieces overlap at {height} px")
