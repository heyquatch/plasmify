"""Every design pattern against a real plasmashell (in the sandbox, see tools/sandbox.py).

Opt in with ``PLASMIFY_SANDBOX=1``; it takes several minutes. Covered:

* every subset of screen edges, each panel as a bar or a dock (80 layouts);
* all pairs of the per-panel options (edge, style, length, alignment, visibility, floating,
  thickness, widget set): every value meets every other value at least once;
* every widget on a horizontal and on a vertical panel;
* the panel style options: every corner radius class, opacity, border and colour together;
* every starter on every built-in preset.

After each apply: plasmashell answers, the panels are exactly the requested ones (edges,
widget order, thickness, visibility, length mode, alignment, floating) and the screen is
not black.
"""

import itertools
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

from sandbox import Sandbox, available  # noqa: E402

from plasmify import panels, presets  # noqa: E402
from plasmify.steps.layout import ALIGNMENT, HIDING, layout_problem  # noqa: E402
from tests.integration.test_sandbox import brightness  # noqa: E402
from tests.pairwise import all_pairs  # noqa: E402

ENABLED = os.environ.get("PLASMIFY_SANDBOX") == "1" and available()

# Apply many layouts inside one Python process in the sandbox (much faster than one each).
BATCH = """
import json, sys
from plasmify import engine, presets
from plasmify.steps.layout import current_panels
from plasmify.system import Runner, shell_ready
cases = json.loads(sys.stdin.read())
out = []
for case in cases:
    preset = presets.customized(presets.find(case["preset"]), case["overrides"])
    plan = engine.make_plan(preset, case["steps"], check_packages=False)
    _, warnings = engine.apply(plan, Runner(), log=lambda m: None)
    out.append({"warnings": warnings, "alive": shell_ready(Runner()), "found": current_panels(Runner())})
print(json.dumps(out))
"""

WIDGET_SETS = {
    "minimal": ["tasks"],
    "classic": ["launcher", "pager", "tasks", "separator", "tray", "clock"],
    "centred": ["launcher", "spacer", "clock", "spacer", "tray"],
    "monitor": ["menu", "cpu", "memory", "network", "media", "analog_clock"],
}


def property_problem(expected: list[dict], found: list[dict]) -> str | None:
    problem = layout_problem(expected, found)
    if problem:
        return problem
    by_edge = {p["location"]: p for p in found}
    for panel in expected:
        made = by_edge[panel["edge"]]
        dock = panel["style"] == "dock"
        fit = dock or panel["length"] == "fit"
        checks = {
            "thickness": (made.get("height"), panel["thickness"]),
            "visibility": (made.get("hiding"), HIDING[panel["hiding"]]),
            "length": (made.get("lengthMode"), "fit" if fit else "fill"),
            "floating": (bool(made.get("floating")), bool(panel["floating"]) or dock),
        }
        if fit:
            checks["alignment"] = (made.get("alignment"), ALIGNMENT[panel["align"]])
        for name, (have, want) in checks.items():
            if have != want:
                return f"{panel['edge']} panel {name}: plasmashell has {have!r}, expected {want!r}"
    return None


@unittest.skipUnless(ENABLED, "set PLASMIFY_SANDBOX=1 on a Plasma 6 desktop to run")
class PatternTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = Sandbox(seed=("icons", "plasmify"))
        cls.box.start()
        cls.shots = Path(os.environ.get("PLASMIFY_SANDBOX_SHOTS") or tempfile.mkdtemp(prefix="plasmify-patterns-"))
        cls.shots.mkdir(parents=True, exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.box.stop()

    def run_layouts(self, name: str, layouts: list[list[dict]], preset: str = "nord-minimal", steps=None) -> None:
        cases = [
            {"preset": preset, "overrides": {"layout": {"panels": layout}}, "steps": steps or ["layout"]}
            for layout in layouts
        ]
        proc = self.box.run([sys.executable, "-c", BATCH], timeout=1800, input_text=json.dumps(cases))
        self.assertEqual(proc.returncode, 0, proc.stderr[-3000:])
        results = json.loads(proc.stdout.strip().splitlines()[-1])
        failures = []
        for index, (layout, result) in enumerate(zip(layouts, results, strict=True)):
            expected = [panels.normalise(p) for p in layout]
            problem = None if result["alive"] else "plasmashell is not running"
            problem = problem or property_problem(expected, result["found"])
            if result["warnings"]:
                problem = problem or f"warnings: {result['warnings']}"
            if problem:
                failures.append(f"#{index} {json.dumps(layout)}: {problem}")
        shot = self.box.screenshot(self.shots / f"{name}-last.png")
        self.assertGreater(brightness(shot), 12, f"{name}: the screen is black")
        self.assertEqual(
            failures, [], f"{len(failures)} of {len(layouts)} {name} layouts failed:\n" + "\n".join(failures[:15])
        )

    def test_every_edge_subset_as_bars_and_docks(self):
        layouts = []
        for count in range(1, 5):
            for edges in itertools.combinations(panels.EDGES, count):
                for styles in itertools.product(panels.STYLES, repeat=count):
                    layouts.append(
                        [
                            {"edge": e, "style": s, "widgets": ["launcher", "tasks", "clock"]}
                            for e, s in zip(edges, styles, strict=True)
                        ]
                    )
        self.assertEqual(len(layouts), 80)
        self.run_layouts("edges", layouts)

    def test_all_pairs_of_panel_options(self):
        options = {
            "edge": list(panels.EDGES),
            "style": list(panels.STYLES),
            "length": list(panels.LENGTHS),
            "align": list(panels.ALIGNS),
            "hiding": list(panels.HIDINGS),
            "floating": [True, False],
            "thickness": [panels.THICKNESS[0], 36, 64, panels.THICKNESS[1]],
            "widgets": list(WIDGET_SETS),
        }
        cases = all_pairs(options, seed=3)
        layouts = [[{**case, "widgets": WIDGET_SETS[case["widgets"]]}] for case in cases]
        self.run_layouts("pairs", layouts)

    def test_every_widget_horizontal_and_vertical(self):
        everything = list(panels.WIDGETS)
        layouts = [
            [{"edge": "bottom", "widgets": everything, "thickness": 44}],
            [{"edge": "left", "widgets": everything, "thickness": 64}],
            [{"edge": "top", "style": "dock", "widgets": everything}],
            [{"edge": "right", "style": "dock", "widgets": everything}],
        ]
        self.run_layouts("widgets", layouts)

    def test_panel_style_options(self):
        cases = []
        layout = panels.compact(panels.starter("topbar-dock"))
        for radius, opacity, border, colour in itertools.product(
            (0, 1, 6, 13, 14, 30), (0.2, 1.0), ("none", "subtle", "accent"), ("mantle", "accent", "#202020")
        ):
            style = {"radius": radius, "opacity": opacity, "border": border, "colour": colour}
            cases.append({"preset": "rose-pine-cozy", "overrides": {"layout": {"panels": layout}, "panel_style": style},
                          "steps": ["layout", "panelstyle"]})  # fmt: skip
        proc = self.box.run([sys.executable, "-c", BATCH], timeout=3600, input_text=json.dumps(cases))
        self.assertEqual(proc.returncode, 0, proc.stderr[-3000:])
        results = json.loads(proc.stdout.strip().splitlines()[-1])
        expected = panels.starter("topbar-dock")
        failures = [
            f"{c['overrides']['panel_style']}: {r['warnings'] or property_problem(expected, r['found']) or 'dead'}"
            for c, r in zip(cases, results, strict=True)
            if r["warnings"] or not r["alive"] or property_problem(expected, r["found"])
        ]
        self.assertGreater(brightness(self.box.screenshot(self.shots / "styles-last.png")), 12)
        self.assertEqual(failures, [], "\n".join(failures[:15]))

    def test_every_starter_on_every_preset(self):
        builtins = [p for p in presets.load_all() if p.builtin]
        self.assertGreaterEqual(len(builtins), 5)  # never pass by testing nothing
        for preset in builtins:
            with self.subTest(preset.id):
                layouts = [panels.compact(panels.starter(name)) for name in panels.STARTERS]
                self.run_layouts(f"starters-{preset.id}", layouts, preset=preset.id)


if __name__ == "__main__":
    unittest.main()
