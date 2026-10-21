"""Apply real layouts and presets to a real plasmashell inside a throwaway desktop.

Opt in with ``PLASMIFY_SANDBOX=1`` (it takes a few minutes). Nothing touches your own
desktop: see ``tools/sandbox.py``. Set ``PLASMIFY_SANDBOX_SHOTS=<folder>`` to keep a
screenshot of every case for review.

Checked after every apply: plasmashell is alive, the panels are exactly the requested
ones (edges and widgets, in order), and the screen is not black.
"""

import json
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from sandbox import Sandbox, available  # noqa: E402

from plasmify import panels, presets  # noqa: E402
from plasmify.steps.layout import PANELS_SCRIPT, layout_problem  # noqa: E402

ENABLED = os.environ.get("PLASMIFY_SANDBOX") == "1" and available()

APPLY = """
import json
from plasmify import engine, presets
from plasmify.system import Runner
preset = presets.customized(presets.find({preset!r}), json.loads({overrides!r}))
plan = engine.make_plan(preset, {steps!r}, check_packages=False)
_, warnings = engine.apply(plan, Runner(), log=lambda m: None)
print(json.dumps(warnings))
"""


def brightness(path: Path) -> float:
    from PyQt6.QtGui import QImage

    image = QImage(str(path))
    if image.isNull():
        return 0.0
    total = count = 0
    for x in range(0, image.width(), max(image.width() // 40, 1)):
        for y in range(0, image.height(), max(image.height() // 25, 1)):
            colour = image.pixelColor(x, y)
            total += colour.red() + colour.green() + colour.blue()
            count += 3
    return total / max(count, 1)


@unittest.skipUnless(ENABLED, "set PLASMIFY_SANDBOX=1 on a Plasma 6 desktop to run")
class SandboxTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = Sandbox(seed=("icons", "plasmify"))
        cls.box.start()
        cls.shots = Path(os.environ.get("PLASMIFY_SANDBOX_SHOTS") or tempfile.mkdtemp(prefix="plasmify-shots-"))
        cls.shots.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.box.stop()

    def apply(self, preset: str, overrides: dict, steps: list[str] | None) -> list[str]:
        proc = self.box.python(APPLY.format(preset=preset, overrides=json.dumps(overrides), steps=steps))
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def verify(self, name: str, expected: list[dict]) -> None:
        self.assertTrue(self.box.shell_ready(), f"{name}: plasmashell is not running")
        found = json.loads(self.box.script(PANELS_SCRIPT).strip() or "[]")
        self.assertIsNone(layout_problem(expected, found), f"{name}: {found}")
        shot = self.box.screenshot(self.shots / f"{name}.png")
        self.assertGreater(brightness(shot), 12, f"{name}: the screen is black")

    def test_every_starter_with_panel_styles(self):
        styles = [
            {"radius": 0, "opacity": 1.0, "border": "none"},
            {"radius": 13, "opacity": 0.85, "border": "subtle"},
            {"radius": 30, "opacity": 0.2, "border": "accent", "colour": "accent"},
            {"radius": 8, "opacity": 0.6, "border": "none", "colour": "#101010"},
        ]
        for index, name in enumerate(panels.STARTERS):
            style = styles[index % len(styles)]
            layout = {"panels": panels.compact(panels.starter(name))}
            with self.subTest(name):
                warnings = self.apply(
                    "tokyo-night-neon", {"layout": layout, "panel_style": style}, ["layout", "panelstyle"]
                )
                self.assertEqual(warnings, [])
                self.verify(f"starter-{name}", panels.starter(name))

    def test_random_layouts(self):
        rng = random.Random(7)
        from tests.test_layout_script import random_panels

        for index in range(12):
            layout = random_panels(rng)
            expected = [panels.normalise(p) for p in layout]
            with self.subTest(index=index, layout=layout):
                self.apply("nord-minimal", {"layout": {"panels": layout}}, ["layout"])
                self.verify(f"random-{index}", expected)

    def test_quick_repeated_applies_keep_the_desktop(self):
        # The reported bug: several applies within a minute left a black screen.
        for attempt in range(5):
            with self.subTest(attempt=attempt):
                self.apply(
                    "rose-pine-cozy", {"panel_style": {"radius": 10 + attempt}}, ["layout", "panelstyle", "icons"]
                )
                self.verify(f"repeat-{attempt}", panels.layout_panels(presets.find("rose-pine-cozy").section("layout")))

    def test_window_options_reach_kwin(self):
        effects = dict(presets.find("nord-minimal").section("effects"), titlebar_buttons="left", window_borders="tiny")
        self.apply("nord-minimal", {"effects": effects}, ["effects"])
        read = lambda key: self.box.run(  # noqa: E731
            ["kreadconfig6", "--file", "kwinrc", "--group", "org.kde.kdecoration2", "--key", key]
        ).stdout.strip()
        self.assertEqual(read("ButtonsOnLeft"), "XIA")
        self.assertEqual(read("BorderSize"), "Tiny")

    def test_every_preset_fully(self):
        builtins = [p for p in presets.load_all() if p.builtin]
        self.assertGreaterEqual(len(builtins), 5)  # never pass by testing nothing
        for preset in builtins:
            with self.subTest(preset.id):
                self.apply(preset.id, {}, None)
                self.verify(f"preset-{preset.id}", panels.layout_panels(preset.section("layout")))


if __name__ == "__main__":
    unittest.main()
