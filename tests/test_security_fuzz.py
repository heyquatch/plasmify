"""Injection fuzzing: every text a preset can carry, against every file and script Plasmify writes.

For each string field of the schema, hostile values are put into an otherwise valid preset.
Whatever the schema refuses is fine. Whatever it accepts must leave every generated file with
exactly the structure the same file has for a harmless value: the same number of lines, INI
sections and keys, TOML/JSON that still parses, Plasma JavaScript that still runs, and QML or
JSON where the value only ever appears as an escaped string. A difference means the value
escaped its place, which for a shared preset is code execution or a config takeover.
"""

import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from plasmify import panels, presets, schema, templates  # noqa: E402
from plasmify.presets import Preset  # noqa: E402
from plasmify.steps.colors import color_scheme, scheme_name  # noqa: E402
from plasmify.steps.icons import index_theme, overlay_name  # noqa: E402
from plasmify.steps.layout import panel_script  # noqa: E402
from plasmify.steps.shell import coloured_lines, fastfetch_modules  # noqa: E402
from plasmify.steps.splash import metadata as splash_metadata  # noqa: E402
from plasmify.steps.splash import splash_qml  # noqa: E402
from plasmify.steps.terminal import terminal_vars  # noqa: E402

HOSTILE = [
    "Inter\nshell /bin/sh -c 'touch /tmp/pwned'",
    "Inter\r\nfont_size 99",
    "Inter extra line",
    "Inter extra",
    "Inter\x85next",
    "Inter\x0bvtab",
    "Inter\x0cff",
    "Inter\x00nul",
    'Inter" ; evil = "1',
    "Inter'; touch /tmp/pwned; '",
    "Inter]]\n[[section",
    "Inter\\\nextra",
    "$(touch /tmp/pwned)",
    "`touch /tmp/pwned`",
    "${HOME}",
    "Inter $e",
    "[Colors:View]",
    "Inter‮evil",
    '"}, "logo": {"type": "command", "source": "touch /tmp/pwned"}, {"x": "',
    "</svg><script>alert(1)</script>",
    '"); evil(); ("',
    "x" * 5000,
]
BENIGN = "Inter"

# (section, key) -> how to reach the value in a preset; only free text fields are fuzzed.
FIELDS = [
    ("meta", "name"),
    ("meta", "description"),
    ("meta", "author"),
    ("fonts", "ui"),
    ("fonts", "mono"),
    ("cursor", "name"),
    ("icons", "theme"),
    ("icons", "folder_color"),
    ("shell", "logo_builtin"),
    ("shell", "logo_text"),
    ("shell", "logo_ascii"),
    ("splash", "text"),
    ("panel_style", "colour"),
    ("wallpaper", "credit"),
]


def base_data() -> dict:
    d = copy.deepcopy(presets.find("catppuccin-anime").data)
    d.setdefault("splash", {"style": "plasmify", "text": "Hello"})
    d.setdefault("panel_style", {"radius": 8, "colour": "mantle"})
    d["meta"]["author"] = "Someone"
    return d


def with_value(field: tuple[str, str], value: str) -> dict:
    section, key = field
    d = base_data()
    if field == ("wallpaper", "credit"):
        d["wallpaper"]["images"][0]["credit"] = value
    elif section == "shell" and key.startswith("logo_"):
        kind = {"logo_builtin": "builtin", "logo_text": "text", "logo_ascii": "ascii"}[key]
        d["shell"].update({"logo": kind, key: value})
    else:
        d.setdefault(section, {})[key] = value
    return d


def ini_shape(text: str) -> tuple[int, int, int]:
    lines = [line for line in text.splitlines() if line.strip()]
    sections = sum(1 for line in lines if line.startswith("["))
    keys = sum(1 for line in lines if "=" in line and not line.startswith("["))
    return len(lines), sections, keys


def kitty_shape(text: str) -> tuple[int, list[str]]:
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return len(lines), sorted(line.split()[0] for line in lines)


def outputs(preset: Preset) -> dict[str, object]:
    """Everything Plasmify writes or runs for ``preset``, reduced to a comparable shape."""
    tvars = terminal_vars(preset)
    result: dict[str, object] = {
        "alacritty": sorted(tomllib.loads(templates.render("alacritty.toml", tvars))),
        "kitty": kitty_shape(templates.render("kitty.conf", tvars)),
        "konsole.profile": ini_shape(templates.render("konsole.profile", tvars)),
        "konsole.colorscheme": ini_shape(templates.render("konsole.colorscheme", tvars)),
        "colorscheme": ini_shape(color_scheme(preset.palette, scheme_name(preset), f"Plasmify {preset.name}")),
        "icon index": ini_shape(index_theme(f"Plasmify {preset.name}", preset.section("icons").get("theme", "x"))),
        "splash metadata": sorted(json.loads(splash_metadata(preset))["KPlugin"]),
    }
    pvars = dict(preset.palette.as_template_vars(), logo='{"type": "auto"}')
    shell = preset.section("shell")
    pvars["modules"] = json.dumps(fastfetch_modules(shell, preset.palette, True))
    if shell.get("logo") == "builtin":
        pvars["logo"] = json.dumps({"type": "builtin", "source": shell["logo_builtin"]})
    fastfetch = templates.render("fastfetch.jsonc", pvars)
    parsed = json.loads("\n".join(line for line in fastfetch.splitlines() if not line.startswith("//")))
    result["fastfetch"] = (sorted(parsed), sorted(parsed["logo"]), len(parsed["modules"]))
    for kind in ("powerline", "minimal"):
        result[f"starship-{kind}"] = sorted(tomllib.loads(templates.render(f"starship-{kind}.toml", pvars)))
    if shell.get("logo") == "ascii":
        # Drawings are multi-line on purpose; what matters is that every line is one colour
        # code followed by plain text (fastfetch only interprets "$<digit>", and "$" is refused).
        rows = shell["logo_ascii"].split("\n")
        lines, _colours = coloured_lines(rows, preset.palette, "gradient")
        result["ascii logo safe"] = all(
            re.fullmatch(r"\$[1-9][^$\n\r]*", line) and line[2:] == row for line, row in zip(lines, rows, strict=True)
        )
    qml = splash_qml(preset, None, None, preset.section("splash").get("text", ""), True)
    result["splash qml"] = (qml.count("\n"), len(re.findall(r"^\s*(\w+):", qml, re.M)))
    script = panel_script(panels.layout_panels(preset.section("layout")), "start-here-kde", [])
    result["panel script"] = script.count("\n")
    return result


class InjectionFuzzTests(unittest.TestCase):
    def test_no_accepted_value_changes_the_shape_of_any_output(self):
        accepted = 0
        for field in FIELDS:
            baseline_data = with_value(field, "Inter" if field != ("panel_style", "colour") else "mantle")
            if field == ("shell", "logo_builtin"):
                baseline_data = with_value(field, "arch_small")
            self.assertEqual(schema.validate(baseline_data), [], field)
            baseline = outputs(Preset(baseline_data, "user"))
            for value in HOSTILE:
                candidate = with_value(field, value)
                if schema.validate(candidate):
                    continue  # refused: that is the defence working
                accepted += 1
                with self.subTest(field=field, value=value[:60]):
                    got = outputs(Preset(candidate, "user"))
                    for name in baseline:
                        self.assertEqual(got[name], baseline[name], f"{field} = {value!r} changed {name}")
        self.assertGreater(accepted, 0)  # the fuzzer must exercise something

    def test_names_used_as_folders_and_keys_stay_plain(self):
        for value in HOSTILE:
            d = base_data()
            d["meta"]["id"] = value
            if schema.validate(d):
                continue
            preset = Preset(d, "user")
            for derived in (scheme_name(preset), overlay_name(preset)):
                self.assertRegex(derived, r"^[A-Za-z0-9_.+-]+$")


class SplashTextTests(unittest.TestCase):
    """The splash text from a shared preset stays plain text. As styled text, an <img> tag in it
    would make the splash fetch a picture from any host at every login, without asking."""

    HOSTILE_TEXT = '<img src="https://example.invalid/p">'

    def splash(self) -> str:
        return splash_qml(presets.find("nord-minimal"), None, None, self.HOSTILE_TEXT, True)

    def test_the_text_element_is_plain_text(self):
        qml = self.splash()
        block = qml[qml.index("text: " + json.dumps(self.HOSTILE_TEXT)) :]
        self.assertIn("textFormat: Text.PlainText", block[: block.index("}")])

    def test_loaded_splash_shows_markup_as_text(self):
        try:
            from PyQt6.QtCore import QUrl
            from PyQt6.QtGui import QGuiApplication
            from PyQt6.QtQml import QQmlComponent, QQmlEngine, QQmlExpression
            from PyQt6.QtQuick import QQuickItem
        except ImportError:
            self.skipTest("PyQt6 QtQuick is not installed")
        app = QGuiApplication.instance() or QGuiApplication([])
        if not isinstance(app, QGuiApplication):
            self.skipTest("a non-GUI Qt application is already running")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "Splash.qml")
            path.write_text(self.splash())
            engine = QQmlEngine()
            component = QQmlComponent(engine, QUrl.fromLocalFile(str(path)))
            root = component.create()
            if root is None:
                self.skipTest("the splash needs Kirigami: " + "; ".join(e.toString() for e in component.errors()))
            texts = [
                item
                for item in root.findChildren(QQuickItem)
                if item.metaObject().className() == "QQuickText" and item.property("text") == self.HOSTILE_TEXT
            ]
            self.assertEqual(len(texts), 1)
            value, undefined = QQmlExpression(engine.rootContext(), texts[0], "textFormat").evaluate()
            self.assertFalse(undefined)
            self.assertEqual(value, 0)  # Text.PlainText; the default AutoText (2) would parse the tag
            root.deleteLater()


@unittest.skipUnless(shutil.which("kwriteconfig6") and shutil.which("kreadconfig6"), "KDE config tools missing")
class KConfigRoundTripTests(unittest.TestCase):
    """Values Plasmify hands to kwriteconfig6 (fonts, theme names) can never add groups or keys."""

    def test_accepted_values_round_trip_as_one_value(self):
        from plasmify.steps.fonts import qfont

        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, HOME=tmp, XDG_CONFIG_HOME=tmp)
            for value in HOSTILE:
                if schema.FONT(value) is not None and schema.NAME(value) is not None:
                    continue
                stored = qfont(value, 10) if schema.FONT(value) is None else value
                path = Path(tmp, "testrc")
                path.unlink(missing_ok=True)
                subprocess.run(
                    ["kwriteconfig6", "--file", "testrc", "--group", "General", "--key", "font", stored],
                    env=env, check=True,
                )  # fmt: skip
                read = subprocess.run(
                    ["kreadconfig6", "--file", "testrc", "--group", "General", "--key", "font"],
                    env=env, capture_output=True, text=True, check=True,
                ).stdout.rstrip("\n")  # fmt: skip
                text = path.read_text()
                with self.subTest(value=value[:40]):
                    self.assertEqual(read, stored)
                    self.assertEqual(ini_shape(text)[1:], (1, 1))


if __name__ == "__main__":
    unittest.main()
