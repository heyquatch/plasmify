"""Terminal logo colours: schemes, directions, and recoloured fastfetch logos."""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from plasmify import blockfont, presets, schema  # noqa: E402
from plasmify.presets import Preset  # noqa: E402
from plasmify.steps import shell  # noqa: E402
from plasmify.steps.base import Context  # noqa: E402
from plasmify.system import Runner  # noqa: E402

try:
    from PyQt6.QtCore import QThread
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None

ARCH = ["      /\\", "     /  \\", "    / $$ \\", "   /  $1  \\"]  # $ in a logo must stay a $


def decode(line: str) -> tuple[str, list[str]]:
    """What fastfetch prints for a coded line: the text and the slot of every character."""
    text, slots, slot, index = [], [], "", 0
    while index < len(line):
        if line[index] == "$" and index + 1 < len(line) and line[index + 1] in "123456789":
            slot = line[index + 1]
            index += 2
            continue
        if line[index] == "$" and index + 1 < len(line) and line[index + 1] == "$":
            index += 1
        text.append(line[index])
        slots.append(slot)
        index += 1
    return "".join(text), slots


class ColouredLinesTests(unittest.TestCase):
    palette = presets.find("catppuccin-anime").palette

    def test_every_choice_and_direction_keeps_the_drawing(self):
        rows = blockfont.render("RICE", 1) + ARCH
        for colour in schema.LOGO_COLOURS:
            for direction in schema.LOGO_DIRECTIONS:
                lines, colours = shell.coloured_lines(rows, self.palette, colour, direction)
                with self.subTest(colour=colour, direction=direction):
                    self.assertLessEqual(len(colours), 9)
                    self.assertTrue(all(re.fullmatch(r"38;2;\d+;\d+;\d+", v) for v in colours.values()))
                    for line, row in zip(lines, rows, strict=True):
                        text, slots = decode(line)
                        self.assertEqual(text, row)
                        self.assertTrue(set(slots) <= set(colours), (line, colours))

    def test_directions_run_the_right_way(self):
        rows = ["#" * 20] * 6
        lines, _ = shell.coloured_lines(rows, self.palette, "sunset", "down")
        self.assertEqual([decode(line)[1][0] for line in lines], ["1", "2", "3", "4", "5", "6"])
        lines, _ = shell.coloured_lines(rows, self.palette, "sunset", "up")
        self.assertEqual(decode(lines[0])[1][0], "6")
        lines, _ = shell.coloured_lines(rows, self.palette, "sunset", "right")
        slots = decode(lines[0])[1]
        self.assertEqual((slots[0], slots[-1]), ("1", "9"))
        self.assertEqual(slots, sorted(slots))
        self.assertEqual(decode(shell.coloured_lines(rows, self.palette, "sunset", "left")[0][0])[1][0], "9")
        diagonal = [decode(line)[1] for line in shell.coloured_lines(rows, self.palette, "sunset", "diagonal")[0]]
        self.assertEqual((diagonal[0][0], diagonal[-1][-1]), ("1", "9"))

    def test_schemes_follow_the_palette_where_they_should(self):
        rows = ["##"] * 7
        _, colours = shell.coloured_lines(rows, self.palette, "rainbow", "down")
        self.assertEqual(colours["1"], "38;2;" + ";".join(str(int(self.palette.red[i : i + 2], 16)) for i in (1, 3, 5)))
        _, colours = shell.coloured_lines(rows, self.palette, "accent", "diagonal")
        self.assertEqual(list(colours), ["1"])  # one colour needs no direction

    def test_schema_knows_the_new_keys(self):
        data = presets.find("nord-minimal").data
        good = presets.merge(data, {"shell": {"logo": "distro", "logo_colour": "aurora", "logo_direction": "left"}})
        self.assertEqual(schema.validate(good), [])
        bad = presets.merge(data, {"shell": {"logo_direction": "sideways"}})
        self.assertTrue(schema.validate(bad))


class ReviewRegressionTests(unittest.TestCase):
    palette = presets.find("nord-minimal").palette

    def test_a_failed_fastfetch_call_is_retried_by_the_apply_only(self):
        shell._FASTFETCH_LOGOS.pop("arch_small", None)
        with mock.patch.object(shell.subprocess, "run", side_effect=OSError("no fastfetch")) as run:
            self.assertEqual(shell.fastfetch_logo_rows("arch_small"), [])
            self.assertEqual(shell.fastfetch_logo_rows("arch_small"), [])
            self.assertEqual(run.call_count, 1)  # the preview does not wait on fastfetch again
        with mock.patch.object(shell.subprocess, "run", return_value=mock.Mock(stdout="  /\\\n /  \\\n")):
            self.assertEqual(shell.fastfetch_logo_rows("arch_small", refresh=True), ["  /\\", " /  \\"])
        shell._FASTFETCH_LOGOS.pop("arch_small", None)

    def test_cell_widths_follow_the_terminal(self):
        widths = {"a": 1, "家": 2, "\u0301": 0, "\u200d": 0, "\ufe0f": 0, "\u00ad": 1, "\u0600": 1, "\u061c": 0}
        widths |= {"\u180e": 0, "\u206a": 0}
        self.assertEqual({c: shell.cell_width(c) for c in widths}, widths)

    def test_wide_and_combining_characters_follow_terminal_columns(self):
        rows = ["家家家家", "########", "e\u0301e\u0301"]
        lines, colours = shell.coloured_lines(rows, self.palette, "rainbow", "right")
        self.assertEqual([decode(line)[0] for line in lines], rows)
        wide, narrow = decode(lines[0])[1], decode(lines[1])[1]
        self.assertEqual(wide[-1], narrow[-2])  # the last wide character sits in columns 7-8
        self.assertIsNone(re.search(r"\$[1-9]\u0301", lines[2]))  # a mark stays with its letter

    def test_fixed_schemes_stay_readable_on_light_terminals(self):
        from plasmify.generate import contrast
        from plasmify.palette import Palette

        light = Palette({**self.palette.colors, "base": "#f3f2f2", "text": "#2e292a"})
        for scheme in ("pastel", "neon", "aurora", "sunset", "fire"):
            with self.subTest(scheme=scheme):
                self.assertTrue(all(contrast(c, "#f3f2f2") >= 2.95 for c in shell.logo_stops(light, scheme)))
        self.assertEqual(shell.logo_stops(self.palette, "pastel")[0], shell.SCHEME_STOPS["pastel"][0])

    def test_user_at_host_needs_the_tick_even_when_listed_twice(self):
        for listed in (["title", "separator", "os", "title"], ["title", "title", "os"]):
            modules = shell.fastfetch_modules({"fastfetch_modules": listed}, self.palette, False)
            self.assertNotIn("title", [m if isinstance(m, str) else m["type"] for m in modules])

    def test_the_weather_line_needs_consent(self):
        data = presets.merge(presets.find("nord-minimal").data, {"shell": {"fastfetch_modules": ["os", "weather"]}})
        self.assertIn(schema.WEATHER_HOST, schema.untrusted_hosts(data))
        self.assertNotIn(schema.WEATHER_HOST, schema.untrusted_hosts(presets.find("nord-minimal").data))

    def test_user_at_host_needs_the_tick(self):
        section = {"fastfetch_modules": ["title", "separator", "os", "kernel"]}
        names = [m if isinstance(m, str) else m["type"] for m in shell.fastfetch_modules(section, self.palette, False)]
        self.assertNotIn("title", names)
        self.assertNotIn("separator", names)
        names = [m if isinstance(m, str) else m["type"] for m in shell.fastfetch_modules(section, self.palette, True)]
        self.assertIn("title", names)

    def test_imported_presets_do_not_go_online_from_the_terminal(self):
        from plasmify import bundle, tomlwrite

        data = presets.merge(presets.find("nord-minimal").data, {"shell": {"fastfetch_modules": ["os", "weather"]}})
        data["meta"] = dict(data["meta"], id="sneaky", name="Sneaky")
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(bundle, "USER_PRESET_DIR", Path(tmp, "p")):
            path = Path(tmp, "sneaky.toml")
            path.write_text(tomlwrite.dumps(data))
            notes: list[str] = []
            preset = bundle.import_file(path, notes=notes)
        self.assertEqual(preset.section("shell")["fastfetch_modules"], ["os"])
        self.assertTrue(notes and "wttr.in" in notes[0])


class StepTests(unittest.TestCase):
    def logo(self, shell_section: dict, dry_run: bool = False) -> dict:
        data = presets.merge(presets.find("nord-minimal").data, {"shell": shell_section})
        self.assertEqual(schema.validate(data), [])
        ctx = Context(Preset(data, "user"), Runner(dry_run=dry_run), Path(self.tmp.name), log=lambda _: None)
        return shell.ShellStep()._logo(ctx)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.text_logo = Path(self.tmp.name, "logo.txt")
        self.patches = [
            mock.patch.object(shell, "TEXT_LOGO", self.text_logo),
            mock.patch.object(shell, "builtin_logos", return_value={"arch"}),
            mock.patch.object(
                shell,
                "fastfetch_logo_rows",
                side_effect=lambda name, refresh=False: ARCH if name in (None, "arch") else [],
            ),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def test_original_colours_use_fastfetch_itself(self):
        self.assertEqual(self.logo({"logo": "builtin", "logo_builtin": "arch"})["type"], "builtin")
        self.assertEqual(self.logo({"logo": "distro", "logo_colour": "original"})["type"], "auto")
        self.assertFalse(self.text_logo.exists())

    def test_recoloured_builtin_and_distro_logos(self):
        for section in (
            {"logo": "builtin", "logo_builtin": "arch", "logo_colour": "neon", "logo_direction": "right"},
            {"logo": "distro", "logo_colour": "red"},
        ):
            with self.subTest(section=section):
                logo = self.logo(section)
                self.assertEqual(logo["type"], "file")
                lines = self.text_logo.read_text().splitlines()
                self.assertEqual([decode(line)[0] for line in lines], ARCH)
                self.assertTrue(set(logo["color"]) <= {str(n) for n in range(1, 10)})
                json.dumps(logo)

    def test_unknown_builtin_falls_back_to_the_distribution_logo(self):
        logo = self.logo({"logo": "builtin", "logo_builtin": "nonexistent", "logo_colour": "fire"})
        self.assertEqual(logo["type"], "file")  # the distribution logo, still in the chosen colours

    def test_dry_run_writes_nothing(self):
        self.assertEqual(
            self.logo({"logo": "builtin", "logo_builtin": "x", "logo_colour": "fire"}, True)["type"], "builtin"
        )
        self.assertFalse(self.text_logo.exists())


@unittest.skipIf(QApplication is None, "PyQt6 missing")
class CustomizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        from plasmify.gui import customize, main_window

        self.customize_module = customize
        self.patches = [
            mock.patch.object(main_window, "TWEAKS_FILE", Path(self.tmp.name, "tweaks.json")),
            mock.patch.object(customize, "CUSTOM_ROOT", Path(self.tmp.name, "custom")),
            mock.patch.object(customize, "fastfetch_logo_rows", lambda name: ARCH),
        ]
        for patch in self.patches:
            patch.start()
        self.window = main_window.MainWindow()
        self.custom = self.window.customize
        self.custom.greeting.setChecked(True)

    def tearDown(self):
        for thread in self.window.findChildren(QThread):
            thread.wait(20000)
        self.window.close()
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def pick(self, box, value) -> None:
        box.setCurrentIndex(box.findData(value))
        self.app.processEvents()

    def test_distro_logo_can_be_coloured_with_a_direction(self):
        custom = self.custom
        self.pick(custom.logo_kind, "distro")
        self.assertEqual(custom.logo_colour.currentData(), "original")
        self.assertFalse(custom.logo_direction.isEnabled())
        self.pick(custom.logo_colour, "ocean")
        self.assertTrue(custom.logo_direction.isEnabled())
        self.pick(custom.logo_direction, "diagonal")
        shell_override = custom.overrides()["shell"]
        self.assertEqual((shell_override["logo_colour"], shell_override["logo_direction"]), ("ocean", "diagonal"))
        presets.customized(custom.preset, custom.overrides(), custom.asset_dir())
        self.assertTrue(custom.logo_preview.isVisible() or not custom.isVisible())
        self.pick(custom.logo_colour, "red")
        self.assertFalse(custom.logo_direction.isEnabled())
        self.assertNotIn("logo_direction", presets.customized(custom.preset, custom.overrides()).section("shell"))

    def test_text_logos_have_no_original_colours(self):
        custom = self.custom
        self.pick(custom.logo_kind, "distro")
        self.pick(custom.logo_colour, "original")
        self.pick(custom.logo_kind, "text")
        custom.logo_text.setText("HI")
        self.assertEqual(custom.logo_colour.currentData(), "gradient")
        self.assertTrue(custom.logo_colour.view().isRowHidden(custom.logo_colour.findData("original")))

    def test_preview_draws_tabs_as_they_are_saved(self):
        custom = self.custom
        self.pick(custom.logo_kind, "ascii")
        custom.logo_ascii.setPlainText("\t/\\\n/____\\")
        self.assertEqual(custom._logo_art().split("\n")[0], "    /\\")
        self.assertEqual(custom._logo_value()["logo_ascii"], custom._logo_art())

    def test_saved_choice_comes_back(self):
        custom = self.custom
        self.pick(custom.logo_kind, "ascii")
        custom.logo_ascii.setPlainText("[0-0]\n/)  )")
        self.pick(custom.logo_colour, "candy")
        self.pick(custom.logo_direction, "up")
        saved = custom.overrides()
        custom.load(custom.preset, saved, custom.asset_dir())
        self.assertEqual(custom.logo_colour.currentData(), "candy")
        self.assertEqual(custom.logo_direction.currentData(), "up")

    def test_preview_matches_the_codes(self):
        row = "ab$1 c$"  # a literal "$1" in fastfetch's text must not turn into a colour
        lines, codes = shell.coloured_lines([row], presets.find("nord-minimal").palette, "fire", "right")
        cells = self.customize_module.logo_cells(lines[0], codes)
        self.assertEqual("".join(text for text, _, _ in cells), row)
        self.assertEqual(len({colour for text, colour, _ in cells if text != " "}), len(codes))


if __name__ == "__main__":
    unittest.main()
