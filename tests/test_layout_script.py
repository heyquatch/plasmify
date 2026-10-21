"""Run panel layouts against a mock of Plasma's scripting API.

The real script runs inside plasmashell, where a syntax or runtime error could
leave the user without panels; this catches such mistakes without a desktop.
Every short-form combination, every starter and a few hundred random panel
layouts are built and checked. The same layouts run against a real (nested)
plasmashell in ``tests/integration``. Skipped when PyQt6's QtQml is missing.
"""

import itertools
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from plasmify import panels, presets, schema
from plasmify.steps.base import Context
from plasmify.steps.layout import ALIGNMENT, HIDING, layout_problem, panel_script
from plasmify.system import Runner

try:
    from PyQt6.QtCore import QCoreApplication
    from PyQt6.QtQml import QJSEngine
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QJSEngine = None

MOCK_API = """
var created = [];
var existing = [{ remove: function () { existing.splice(0, 1); } }];
function panels() { return existing.slice(); }
function print(message) { throw new Error(message); }
function Panel() {
    var self = this;
    this.widgetTypes = [];
    this.remove = function () { existing.splice(existing.indexOf(self), 1); };
    this.addWidget = function (plugin) {
        self.widgetTypes.push(plugin);
        return { currentConfigGroup: [], writeConfig: function (key, value) {} };
    };
    this.widgets = function () { return self.widgetTypes.map(function (t) { return {type: t}; }); };
    created.push(this);
    existing.push(this);
}
function report() {
    return JSON.stringify(existing.map(function (p) {
        return {location: p.location, height: p.height, hiding: p.hiding, floating: p.floating,
                lengthMode: p.lengthMode, alignment: p.alignment, widgets: p.widgetTypes};
    }));
}
"""


def random_panels(rng: random.Random) -> list[dict]:
    edges = rng.sample(panels.EDGES, rng.randint(1, panels.MAX_PANELS))
    result = []
    for edge in edges:
        panel = {"edge": edge, "widgets": rng.sample(list(panels.WIDGETS), rng.randint(1, 8))}
        for key, choices in (
            ("style", panels.STYLES),
            ("length", panels.LENGTHS),
            ("align", panels.ALIGNS),
            ("hiding", panels.HIDINGS),
            ("floating", (True, False)),
        ):
            if rng.random() < 0.6:
                panel[key] = rng.choice(choices)
        if rng.random() < 0.6:
            panel["thickness"] = rng.randint(*panels.THICKNESS)
        if panel.get("style") == "islands" and all(w == "spacer" for w in panel["widgets"]):
            panel["widgets"].append("clock")  # islands need something besides the dividing spaces
        result.append(panel)
    return result


@unittest.skipIf(QJSEngine is None, "PyQt6.QtQml is not installed")
class PanelScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A full QApplication when possible: a bare QCoreApplication made here would be the one
        # later tests in the same run get, and they cannot open windows with it.
        cls.app = QCoreApplication.instance() or QApplication([])

    def build(self, layout_panels: list[dict]) -> list[dict]:
        engine = QJSEngine()
        engine.evaluate(MOCK_API)
        result = engine.evaluate(panel_script(layout_panels, "start-here-kde", ["applications:a.desktop"]))
        self.assertFalse(result.isError(), result.toString())
        return json.loads(engine.evaluate("report()").toString())

    def check(self, layout_panels: list[dict]) -> None:
        built = self.build(layout_panels)
        self.assertIsNone(layout_problem(layout_panels, built))
        remaining = list(built)
        for entry in layout_panels:
            for panel in panels.pieces(entry):  # islands become one Plasma panel per piece
                plugins = [panels.WIDGETS[w][0] for w in panel["widgets"]]
                made = next(p for p in remaining if p["location"] == panel["edge"] and p["widgets"] == plugins)
                remaining.remove(made)
                self.assertEqual(made["height"], panel["thickness"])
                self.assertEqual(made["hiding"], HIDING[panel["hiding"]])
                if panel["style"] == "dock" or panel["length"] == "fit":
                    self.assertEqual(made["lengthMode"], "fit")
                    self.assertEqual(made["alignment"], ALIGNMENT[panel["align"]])
                if panel["style"] == "dock":
                    self.assertTrue(made["floating"])
        self.assertEqual(remaining, [])

    def test_every_short_form_combination(self):
        options = itertools.product(
            (True, False),
            ("none", "panel", "dock"),
            ("dodge", "autohide", "always"),
            ("center", "right"),
            (True, False),
            (True, False),
            (True, False),
        )
        for top, bottom, hiding, clock, pager, labels, floating in options:
            if not top and bottom == "none":
                continue
            layout = {
                "top_bar": top,
                "bottom": bottom,
                "dock_hiding": hiding,
                "clock": clock,
                "pager": pager,
                "task_labels": labels,
                "floating": floating,
            }
            with self.subTest(layout=layout):
                built = panels.layout_panels(layout)
                self.assertEqual(len(built), int(top) + int(bottom != "none"))
                self.check(built)

    def test_every_starter(self):
        for name in panels.STARTERS:
            with self.subTest(name):
                self.check(panels.starter(name))

    def test_every_single_panel_combination(self):
        """The full product of every per-panel option, for one panel (2 592 layouts)."""
        options = itertools.product(
            panels.EDGES,
            panels.STYLES,
            panels.LENGTHS,
            panels.ALIGNS,
            panels.HIDINGS,
            (True, False),
            (panels.THICKNESS[0], 36, panels.THICKNESS[1]),
            (["tasks"], list(panels.WIDGETS)),
        )
        count = 0
        for edge, style, length, align, hiding, floating, thickness, widgets in options:
            panel = {"edge": edge, "style": style, "length": length, "align": align, "hiding": hiding,
                     "floating": floating, "thickness": thickness, "widgets": widgets}  # fmt: skip
            self.check([panels.normalise(panel)])
            count += 1
        self.assertEqual(count, 4 * len(panels.STYLES) * 2 * 3 * 3 * 2 * 3 * 2)

    def test_every_edge_subset(self):
        for size in range(1, 5):
            for edges in itertools.combinations(panels.EDGES, size):
                for styles in itertools.product(panels.STYLES, repeat=size):
                    layout = [panels.normalise({"edge": e, "style": s, "widgets": ["launcher", "tasks"]})
                              for e, s in zip(edges, styles, strict=True)]  # fmt: skip
                    self.check(layout)

    def test_random_layouts(self):
        rng = random.Random(20261017)
        data = {"schema": 1, "meta": {"id": "x", "name": "x", "description": "x"}, "palette": {}}
        for _ in range(400):
            layout = random_panels(rng)
            with self.subTest(layout=layout):
                data["layout"] = {"panels": layout}
                layout_errors = [e for e in schema.validate(data) if e.startswith("layout")]
                self.assertEqual(layout_errors, [])
                self.check([panels.normalise(p) for p in layout])

    def test_failure_falls_back_to_a_panel(self):
        engine = QJSEngine()
        engine.evaluate(
            MOCK_API.replace("function print(message) { throw new Error(message); }", "function print(m) {}")
        )
        engine.evaluate("var brokenAdd = 0;")
        script = panel_script(panels.starter("topbar-dock"), "x", [])
        # Make the second widget addition explode after the old panels were removed.
        engine.evaluate(
            "var RealPanel = Panel; Panel = function () { RealPanel.call(this); var add = this.addWidget;"
            " this.addWidget = function (p) { if (++brokenAdd === 2) throw new Error('boom'); return add(p); }; };"
        )
        result = engine.evaluate(script)
        self.assertFalse(result.isError(), result.toString())
        self.assertGreaterEqual(engine.evaluate("existing.length").toInt(), 1)

    def test_problem_report(self):
        want = panels.starter("topbar-dock")
        self.assertIn("expected panels at bottom, top", layout_problem(want, []))
        partial = [{"location": "top", "widgets": ["org.kde.plasma.kickoff"]}, {"location": "bottom", "widgets": []}]
        self.assertIn("widgets", layout_problem(want, partial))


class PanelRuleTests(unittest.TestCase):
    def base(self) -> dict:
        return {"schema": 1, "meta": {"id": "x", "name": "x", "description": "x"}, "palette": {}}

    def layout_errors(self, layout) -> list[str]:
        data = self.base()
        data["layout"] = layout
        return [e for e in schema.validate(data) if e.startswith("layout")]

    def test_rules(self):
        cases = {
            "two panels on one edge": [{"edge": "top", "widgets": ["clock"]}, {"edge": "top", "widgets": ["tray"]}],
            "no widgets": [{"edge": "top", "widgets": []}],
            "unknown widget": [{"edge": "top", "widgets": ["org.evil.applet"]}],
            "unknown edge": [{"edge": "middle", "widgets": ["clock"]}],
            "too thick": [{"edge": "top", "thickness": 500, "widgets": ["clock"]}],
            "too many": [{"edge": e, "widgets": ["clock"]} for e in panels.EDGES] + [{"edge": "top", "widgets": ["x"]}],
            "script injection": [{"edge": "top", "widgets": ['clock"); evil("']}],
        }
        for label, layout in cases.items():
            with self.subTest(label):
                self.assertTrue(self.layout_errors({"panels": layout}))

    def test_empty_list(self):
        self.assertTrue(self.layout_errors({"panels": []}))

    def test_compact_round_trip(self):
        for name in panels.STARTERS:
            full = panels.starter(name)
            self.assertEqual([panels.normalise(p) for p in panels.compact(full)], full)


if __name__ == "__main__":
    unittest.main()


class PairwiseDesignTests(unittest.TestCase):
    def test_all_pairs_are_covered(self):
        from tests.pairwise import all_pairs, covers_all_pairs

        options = {"a": [1, 2, 3], "b": ["x", "y"], "c": [True, False], "d": list("pqrs")}
        cases = all_pairs(options, seed=5)
        self.assertTrue(covers_all_pairs(options, cases))
        self.assertLess(len(cases), 3 * 2 * 2 * 4)


PINNED_MOCK = """
var written = [];
function widget(type, launchers) {
    return {type: type, currentConfigGroup: [],
            readConfig: function (key, fallback) { return key === "launchers" ? launchers : fallback; }};
}
var existing = [
    {widgets: function () { return [widget("org.kde.plasma.kickoff", ""),
                                    widget("org.kde.plasma.icontasks", %s)]; },
     remove: function () { existing.splice(existing.indexOf(this), 1); }},
];
function panels() { return existing.slice(); }
function print(message) { throw new Error(message); }
function Panel() {
    this.addWidget = function (plugin) {
        return {currentConfigGroup: [], writeConfig: function (key, value) {
            if (key === "launchers") written.push(value);
        }};
    };
    existing.push(this);
    this.widgets = function () { return []; };
    this.remove = function () {};
}
"""


@unittest.skipIf(QJSEngine is None, "PyQt6.QtQml is not installed")
class PinnedAppTests(unittest.TestCase):
    """Apps pinned to the dock by hand survive a new apply (a browser that is not the default)."""

    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QApplication([])

    def written(self, existing_launchers: str) -> list[str]:
        engine = QJSEngine()
        engine.evaluate(PINNED_MOCK % existing_launchers)
        layout = [panels.normalise({"edge": "bottom", "style": "dock", "widgets": ["tasks"]})]
        script = panel_script(layout, "start-here-kde", ["applications:org.kde.dolphin.desktop"])
        result = engine.evaluate(script)
        self.assertFalse(result.isError(), result.toString())
        return json.loads(engine.evaluate("JSON.stringify(written)").toString())

    def test_pinned_apps_are_kept(self):
        mine = "applications:librewolf.desktop,applications:Alacritty.desktop"
        self.assertEqual(self.written(json.dumps(mine)), [mine.split(",")])

    def test_a_list_from_plasma_is_kept_too(self):
        mine = ["applications:librewolf.desktop", "preferred://filemanager"]
        self.assertEqual(self.written(json.dumps(mine)), [mine])

    def test_an_empty_dock_gets_the_suggestions(self):
        self.assertEqual(self.written('""'), [["applications:org.kde.dolphin.desktop"]])

    def test_launchers_are_written_as_a_list(self):
        """Plasma hands a written value straight to the running task manager, which took the
        text "a,b,c" as one launcher: a "?" icon in the dock until Plasma restarted."""
        layout = [panels.normalise({"edge": "left", "style": "islands", "widgets": ["launcher", "spacer", "tasks"]})]
        pinned = ["applications:org.kde.dolphin.desktop", "applications:org.kde.konsole.desktop"]
        for existing in ('""', json.dumps(",".join(pinned))):
            with self.subTest(existing=existing):
                engine = QJSEngine()
                engine.evaluate(PINNED_MOCK % existing)
                result = engine.evaluate(panel_script(layout, "start-here-kde", pinned))
                self.assertFalse(result.isError(), result.toString())
                self.assertEqual(json.loads(engine.evaluate("JSON.stringify(written)").toString()), [pinned])


# Plasma's scripting readConfig is KConfigGroup::readEntry(key, default.toVariant()) and writeConfig
# is writeEntry(key, value.toVariant()). KConfig keeps every value as text and turns it into the
# type of the default: text for a string, a number, a yes/no or a list. A null or undefined default
# has no type KConfig handles, so a stored key reads back as undefined while a missing one gives
# the default (checked against KF6ConfigCore 6.31). The mock does the same, so a read that only
# works in the mock cannot pass.
TRAY_MOCK = r"""
var printed = [];
var writes = {};
var live = {};
var reloaded = 0;
// Plasma hands a written value to the running tray as it is: a list setting takes text as one item.
var LIVE_LISTS = ["extraItems", "hiddenItems", "shownItems", "knownItems", "disabledStatusNotifiers"];
function print(message) { printed.push(String(message)); }
function kconfigList(text) {
    var items = [], item = "";
    if (text === "") return items;
    for (var i = 0; i < text.length; i++) {
        if (text[i] === "\\" && i + 1 < text.length) item += text[++i];
        else if (text[i] === ",") { items.push(item); item = ""; }
        else item += text[i];
    }
    items.push(item);
    return items;
}
function kconfigRead(text, fallback) {
    if (text === undefined) return fallback;
    if (typeof fallback === "string") return text;
    if (typeof fallback === "number") return text === "" || isNaN(Number(text)) ? fallback : Number(text);
    if (typeof fallback === "boolean") return ["false", "no", "off", "0"].indexOf(text.toLowerCase()) < 0;
    if (Array.isArray(fallback)) return kconfigList(text);
    return undefined;
}
function kconfigText(value) {
    if (!Array.isArray(value)) return String(value);
    return value.map(function (v) { return String(v).replace(/\\/g, "\\\\").replace(/,/g, "\\,"); }).join(",");
}
function configured(groups) {
    return {currentConfigGroup: [], groups: groups,
            readConfig: function (key, fallback) {
                return kconfigRead((groups[this.currentConfigGroup.join("/")] || {})[key], fallback);
            },
            writeConfig: function (key, value) {
                var name = this.currentConfigGroup.join("/");
                (groups[name] = groups[name] || {})[key] = kconfigText(value);
                writes[key] = value;
                live[key] = LIVE_LISTS.indexOf(key) < 0 ? value
                    : (Array.isArray(value) ? value.map(String) : [String(value)]);
            },
            reloadConfig: function () { reloaded += 1; }};
}
var containments = {42: configured({General: %s}), 77: configured({})};
function desktopById(id) { return containments[id]; }
function trayWidget(id) {
    var widget = configured({"": {SystrayContainmentId: String(id)}});
    widget.type = "org.kde.plasma.systemtray";
    return widget;
}
var trayId = 42;
// Plasma 6.8 and later: no inner containment, the applet holds the tray's settings itself.
var embedded = null;
function embeddedTray(general) {
    var widget = configured({General: general});
    widget.type = "org.kde.plasma.systemtray";
    return widget;
}
function panels() {
    return [{widgets: function () { return [embedded ? embedded : trayWidget(trayId)]; }}];
}
"""


class EngineRunner:
    """Runs Plasma scripts in a QJSEngine holding TRAY_MOCK and returns what they printed."""

    def __init__(self, engine):
        self.engine = engine

    def plasma_script(self, script: str, *, mutating: bool = True, check: bool = True) -> str:
        self.engine.evaluate("printed = [];")
        result = self.engine.evaluate(script)
        if result.isError():
            raise RuntimeError(result.toString())
        return "\n".join(json.loads(self.engine.evaluate("JSON.stringify(printed)").toString()))


@unittest.skipIf(QJSEngine is None, "PyQt6.QtQml is not installed")
class TrayKeepTests(unittest.TestCase):
    """A new tray keeps which items the user turned off, hid or always shows (Lock Keys stays off)."""

    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QApplication([])

    def engine(self, old: dict, new: dict | None = None):
        engine = QJSEngine()
        result = engine.evaluate(TRAY_MOCK % json.dumps(old))
        self.assertFalse(result.isError(), result.toString())
        if new is not None:  # what the new tray starts with (Plasma turns on every default item)
            engine.evaluate(f"containments[77] = configured({{General: {json.dumps(new)}}});")
        return engine

    def run_scripts(self, old: dict, new: dict | None = None) -> tuple[dict, dict, object]:
        from plasmify.steps.layout import read_tray, tray_restore_script

        engine = self.engine(old, new)
        runner = EngineRunner(engine)
        saved = read_tray(runner)
        engine.evaluate("trayId = 77;")  # the panels are rebuilt: a new tray with a new containment
        runner.plasma_script(tray_restore_script(saved))
        self.assertEqual(engine.evaluate("reloaded").toInt(), 1)
        stored = json.loads(engine.evaluate("JSON.stringify(containments[77].groups.General || {})").toString())
        return saved, stored, engine

    def read_new(self, engine, key: str, default: str):
        """A setting of the new tray, read the way the tray itself reads it (as a list, yes/no...)."""
        engine.evaluate('containments[77].currentConfigGroup = ["General"];')
        return json.loads(
            engine.evaluate(f"JSON.stringify(containments[77].readConfig({json.dumps(key)}, {default}))").toString()
        )

    def test_plasma_6_8_tray_is_the_applet_itself(self):
        from plasmify.steps.layout import read_tray, tray_restore_script

        known = "org.kde.plasma.battery,org.kde.plasma.keyboardindicator,org.kde.plasma.networkmanagement"
        old = {"extraItems": "org.kde.plasma.battery,org.kde.plasma.networkmanagement", "knownItems": known,
               "showAllItems": "false"}  # fmt: skip
        engine = self.engine({})
        engine.evaluate(f"embedded = embeddedTray({json.dumps(old)});")
        runner = EngineRunner(engine)
        saved = read_tray(runner)
        self.assertEqual(saved["extraItems"], old["extraItems"])
        # The panels are rebuilt: a new tray applet with Plasma's defaults (Lock Keys on).
        engine.evaluate(f"embedded = embeddedTray({json.dumps({'extraItems': known, 'knownItems': known})});")
        self.assertEqual(runner.plasma_script(tray_restore_script(saved)), "restored 1")
        running = json.loads(engine.evaluate("JSON.stringify(live)").toString())
        self.assertEqual(running["extraItems"], ["org.kde.plasma.battery", "org.kde.plasma.networkmanagement"])
        self.assertIs(running["showAllItems"], False)
        stored = json.loads(engine.evaluate("JSON.stringify(embedded.groups.General)").toString())
        self.assertEqual(stored["extraItems"], old["extraItems"])

    def test_mock_reads_like_kconfig(self):
        engine = self.engine({"extraItems": "a,b"})
        engine.evaluate('containments[42].currentConfigGroup = ["General"];')

        def read(key, default):
            return engine.evaluate(f"String(containments[42].readConfig({json.dumps(key)}, {default}))").toString()

        self.assertEqual(read("extraItems", "null"), "undefined")
        self.assertEqual(read("missing", "null"), "null")
        self.assertEqual(read("extraItems", '""'), "a,b")
        # The read that never found anything on a real desktop finds nothing here either.
        from plasmify.steps.layout import TRAY_READ_SCRIPT

        old_read = TRAY_READ_SCRIPT.replace('readConfig(k, "")', "readConfig(k, null)")
        self.assertNotEqual(old_read, TRAY_READ_SCRIPT)
        self.assertEqual(EngineRunner(engine).plasma_script(old_read), "{}")

    def test_items_carry_over(self):
        known = "org.kde.plasma.battery,org.kde.plasma.keyboardindicator,org.kde.plasma.networkmanagement"
        old = {
            "extraItems": "org.kde.plasma.battery,org.kde.plasma.networkmanagement",
            "hiddenItems": "org.kde.plasma.clipboard,odd\\,name",
            "shownItems": "org.kde.plasma.volume",
            "knownItems": known,
            "disabledStatusNotifiers": "chrome_status_icon_1",
            "showAllItems": "true",
            "iconSpacing": "1",
            "unrelated": "kept out",
        }
        fresh = {"extraItems": known, "knownItems": known}  # Lock Keys is on in a new tray
        saved, stored, engine = self.run_scripts(old, fresh)
        self.assertNotIn("unrelated", saved)
        for key in ("extraItems", "hiddenItems", "shownItems", "knownItems", "disabledStatusNotifiers"):
            self.assertEqual(stored[key], old[key])
        self.assertEqual(
            self.read_new(engine, "extraItems", "[]"), ["org.kde.plasma.battery", "org.kde.plasma.networkmanagement"]
        )
        self.assertEqual(self.read_new(engine, "hiddenItems", "[]"), ["org.kde.plasma.clipboard", "odd,name"])
        self.assertIs(self.read_new(engine, "showAllItems", "false"), True)
        self.assertEqual(self.read_new(engine, "iconSpacing", "2"), 1)
        # The running tray gets the same: real lists (Lock Keys stays off right away), a yes/no, a number.
        running = json.loads(engine.evaluate("JSON.stringify(live)").toString())
        self.assertEqual(running["extraItems"], ["org.kde.plasma.battery", "org.kde.plasma.networkmanagement"])
        self.assertEqual(running["hiddenItems"], ["org.kde.plasma.clipboard", "odd,name"])
        self.assertEqual(running["disabledStatusNotifiers"], ["chrome_status_icon_1"])
        self.assertIs(running["showAllItems"], True)
        self.assertEqual(running["iconSpacing"], 1)

    def test_items_turned_all_off_stay_off(self):
        # Plasma deletes extraItems once the last item is turned off; the new tray must not
        # fall back to every default item.
        known = "org.kde.plasma.battery,org.kde.plasma.keyboardindicator"
        saved, stored, engine = self.run_scripts({"knownItems": known}, {"extraItems": known, "knownItems": known})
        self.assertEqual(saved["extraItems"], "")
        self.assertEqual(self.read_new(engine, "extraItems", "[]"), [])
        self.assertEqual(json.loads(engine.evaluate("JSON.stringify(live.extraItems)").toString()), [])
        self.assertNotIn("showAllItems", stored)

    def test_a_tray_without_known_items_carries_only_what_it_has(self):
        saved, stored, _engine = self.run_scripts({"iconSpacing": "3"})
        self.assertEqual(saved, {"iconSpacing": "3"})
        self.assertEqual(stored, {"iconSpacing": "3"})

    def test_empty_text_never_turns_a_setting_on(self):
        from plasmify.steps.layout import tray_restore_script

        script = tray_restore_script({"showAllItems": "", "pin": "", "extraItems": ""})
        self.assertNotIn('"showAllItems"', script)
        self.assertNotIn('"pin"', script)
        self.assertIn('tray.writeConfig("extraItems", []);', script)
        self.assertIn('tray.writeConfig("pin", false);', tray_restore_script({"pin": "False"}))
        self.assertNotIn("iconSpacing", tray_restore_script({"iconSpacing": "2; x"}))

    def test_kconfig_lists(self):
        from plasmify.steps.layout import kconfig_list

        self.assertEqual(kconfig_list(""), [])
        self.assertEqual(kconfig_list("\\0"), [""])
        self.assertEqual(kconfig_list("a,b"), ["a", "b"])
        self.assertEqual(kconfig_list("a\\,b,c\\\\d,"), ["a,b", "c\\d", ""])

    def test_hostile_values_stay_data(self):
        from plasmify.steps.layout import tray_restore_script

        tricky = 'a"); panels().forEach(function (p) { p.remove(); }); ("'
        script = tray_restore_script(
            {"hiddenItems": [tricky, "x\n</script>"], "extraItems": ["y"] * 500, "evil": "1", "iconSpacing": 2}
        )
        self.assertNotIn("evil", script)
        self.assertNotIn('"extraItems"', script)  # an absurd list is not carried
        engine = QJSEngine()
        engine.evaluate(TRAY_MOCK % "{}")
        engine.evaluate("trayId = 77;")
        result = engine.evaluate(script)
        self.assertFalse(result.isError(), result.toString())
        written = engine.evaluate("JSON.stringify(writes)").toString()
        self.assertIn("hiddenItems", written)
        self.assertEqual(engine.evaluate("writes.hiddenItems[0]").toString(), tricky)

    def test_no_tray_means_nothing_to_carry(self):
        from plasmify.steps.layout import read_tray

        engine = self.engine({})
        engine.evaluate("function panels() { return []; }")
        self.assertEqual(read_tray(EngineRunner(engine)), {})


class LayoutApplyTrayTests(unittest.TestCase):
    """The tray settings are carried over even when the panels come out differently, and a tray
    that could not be restored is reported."""

    def apply(self, restored: str, panels_found: str) -> tuple[list[str], Context]:
        from plasmify.steps import layout

        calls: list[str] = []

        def plasma_script(script, **_kw):
            if script == layout.TRAY_READ_SCRIPT:
                return '{"extraItems": "org.kde.plasma.battery", "knownItems": "org.kde.plasma.battery"}'
            if script == layout.PANELS_SCRIPT:
                return panels_found
            if "restored" in script:
                calls.append("restore")
                return restored
            calls.append("build")
            return ""

        runner = Runner()
        runner.plasma_script = plasma_script
        preset = presets.find("nord-minimal")
        ctx = Context(preset, runner, Path(tempfile.gettempdir()), log=lambda _: None)
        with mock.patch.object(layout, "widget_available", return_value=True):
            layout.LayoutStep().apply(ctx)
        return calls, ctx

    def test_restored_after_panels_that_came_out_differently(self):
        calls, ctx = self.apply("restored 1", "[]")
        self.assertEqual(calls, ["build", "build", "restore"])
        self.assertTrue(any("panels could not be built" in w for w in ctx.warnings))
        self.assertFalse(any("tray" in w for w in ctx.warnings))

    def test_a_tray_that_was_not_restored_is_reported(self):
        _calls, ctx = self.apply("restored 0", "[]")
        self.assertTrue(any("system tray settings could not be carried over" in w for w in ctx.warnings))
