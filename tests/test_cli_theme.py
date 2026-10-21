"""Quick theme commands (plasmify theme, set and reset) and the record they share with the window.

Nothing here touches the desktop: commands run with a fake Runner and a fake engine.apply in a
scratch state folder, and the end-to-end dry runs run in a child process whose home, XDG folders
and PATH are scratch folders.
"""

import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from plasmify import cli, engine, generate, glass, modes, presets, record, system  # noqa: E402
from plasmify.palette import is_dark  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
# The built-in presets these tests were written against; newer presets are left out so a
# new "nord-light" cannot make "nord" ambiguous here.
KNOWN = (
    "catppuccin-anime",
    "dracula-night",
    "everforest-woods",
    "gruvbox-retro",
    "kanagawa-wave",
    "nord-minimal",
    "rose-pine-cozy",
    "tokyo-night-neon",
)
ICON_THEMES = ["breeze", "breeze-dark", "Papirus", "Papirus-Dark", "Papirus-Light"]
CURSORS = ["Bibata-Modern-Ice", "breeze_cursors"]
FONTS = {"Noto Sans", "Hack", "Inter", "JetBrains Mono", "Fira Code"}

try:
    from plasmify.gui import main_window
except ImportError:  # pragma: no cover
    main_window = None


def known_presets() -> list[presets.Preset]:
    found = {p.id: p for p in presets.load_all(include_user=False)}
    missing = [i for i in KNOWN if i not in found]
    if missing:
        raise AssertionError(f"built-in presets not found: {missing}")
    return [found[i] for i in KNOWN]


FIXED = known_presets()


def by_id(preset_id: str) -> presets.Preset:
    return next(p for p in FIXED if p.id == preset_id)


def user_preset(base: str, preset_id: str, name: str) -> presets.Preset:
    data = json.loads(json.dumps(by_id(base).data))
    data["meta"]["id"], data["meta"]["name"] = preset_id, name
    return presets.Preset(data, "user")


class FakeTTY(io.StringIO):
    def isatty(self) -> bool:
        return True


class CliCase(unittest.TestCase):
    """Commands against a scratch record, with every desktop-changing part replaced."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tweaks = self.root / "state" / "applied-tweaks.json"
        self.active: str | None = None  # id of the preset "on the desktop"
        self.live: bool | None = None  # whether the desktop is dark (None: unknown)
        self.applied: list[dict] = []  # every engine.apply call
        self.commands: list[str] = []  # every command a Runner was asked to run
        make_plan = engine.make_plan

        def fake_apply(plan, runner, log=print, progress=None, allowed_hosts=()):
            self.applied.append({"plan": plan, "runner": runner, "hosts": list(allowed_hosts)})
            return None, []

        def fake_run(runner, args, **_kwargs):
            runner.log.append(" ".join(args))
            self.commands.append(" ".join(args))
            return ""

        self.patches = [
            mock.patch.object(record, "TWEAKS_FILE", self.tweaks),
            mock.patch.object(presets, "load_all", lambda include_user=True, problems=None: list(FIXED)),
            mock.patch.object(
                engine, "active_preset", lambda found: next((p for p in found if p.id == self.active), None)
            ),
            mock.patch.object(record, "live_mode", lambda runner=None: self.live),
            mock.patch.object(engine, "make_plan", lambda preset, steps=None, **_k: make_plan(preset, steps, False)),
            mock.patch.object(engine, "apply", fake_apply),
            mock.patch.object(system.Runner, "run", fake_run),
            mock.patch.object(cli, "_check_environment", lambda: True),
            mock.patch.object(cli, "installed_icon_themes", lambda: list(ICON_THEMES)),
            mock.patch.object(cli, "installed_cursor_themes", lambda: list(CURSORS)),
            mock.patch.object(cli, "installed_fonts", lambda: set(FONTS)),
            mock.patch.object(cli, "desktop_font", lambda which: {"font": "Noto Sans", "fixed": "Hack"}[which]),
        ]
        for patch in self.patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self.patches):
            patch.stop()
        self.tmp.cleanup()

    def run_cli(self, *argv: str, stdin=None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(err))
            if stdin is not None:
                stack.enter_context(mock.patch("sys.stdin", stdin))
            try:
                code = cli.main(list(argv))
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 2
        return code, out.getvalue(), err.getvalue()

    def activate(self, preset_id: str, overrides: dict, steps=None, own_mode=record.KEEP) -> presets.Preset:
        self.active = preset_id
        record.remember_tweaks(preset_id, overrides, None, steps=steps, own_mode=own_mode)
        return by_id(preset_id)

    def entry(self, preset_id: str) -> dict:
        return record.applied_tweaks().get(preset_id, {})

    def record_bytes(self) -> bytes | None:
        return self.tweaks.read_bytes() if self.tweaks.exists() else None

    def steps_of(self, index: int = -1) -> list[str]:
        return [s.id for s in self.applied[index]["plan"].steps]


# ----- names -----------------------------------------------------------------------------


class NameMatchTests(unittest.TestCase):
    def match(self, query: str, candidates=None) -> cli.Match:
        return cli.match_preset(query, candidates if candidates is not None else FIXED)

    def test_forgiving_names_find_nord_minimal(self):
        for query in (
            "nord-minimal",
            "nord",
            "nordic",
            "Nord Minimal",
            "NORD_MINIMAL",
            "  nord   minimal ",
            "nord min",
            "minimal",
            "\uff4e\uff4f\uff52\uff44",  # fullwidth letters
        ):
            with self.subTest(query=query):
                found = self.match(query)
                self.assertIsNotNone(found.preset)
                self.assertEqual(found.preset.id, "nord-minimal")

    def test_other_presets_by_part_accent_or_typo(self):
        for query, expected in (
            ("Rosé Pine Cozy", "rose-pine-cozy"),
            ("rose pine", "rose-pine-cozy"),
            ("ROSE-PINE-COZY", "rose-pine-cozy"),
            ("tokyo", "tokyo-night-neon"),
            ("night neon", "tokyo-night-neon"),
            ("kanagawa", "kanagawa-wave"),
            ("gruvbox retro", "gruvbox-retro"),
            ("catpuccin", "catppuccin-anime"),
            ("drakula", "dracula-night"),
            ("everforrest", "everforest-woods"),
            ("anime", "catppuccin-anime"),
        ):
            with self.subTest(query=query):
                self.assertEqual(getattr(self.match(query).preset, "id", None), expected)

    def test_ambiguous_names_suggest_and_pick_nothing(self):
        for query, expected in (
            ("pastel", {"catppuccin-anime", "rose-pine-cozy"}),
            ("calm", {"nord-minimal", "everforest-woods"}),
            ("night", {"dracula-night", "tokyo-night-neon"}),
            ("dark", set(KNOWN)),
        ):
            with self.subTest(query=query):
                found = self.match(query)
                self.assertIsNone(found.preset)
                self.assertTrue(found.ambiguous)
                self.assertEqual({p.id for p in found.suggestions}, expected)

    def test_unknown_and_hostile_names_find_nothing(self):
        for query in ("zzzzzz", "../../etc/passwd", "/etc/shadow", "", "   ", "t", "$(reboot)", "nord;rm -rf ~x"):
            with self.subTest(query=query):
                found = self.match(query)
                if query == "nord;rm -rf ~x":  # folded to words: still only ever a loaded preset
                    self.assertIn(getattr(found.preset, "id", None), (None, "nord-minimal"))
                    continue
                self.assertIsNone(found.preset)
                self.assertFalse(found.ambiguous)

    def test_a_path_only_ever_selects_a_loaded_preset(self):
        found = self.match("../nord-minimal")
        self.assertIs(found.preset, by_id("nord-minimal"))  # the loaded object, never a file

    def test_the_exact_id_wins_over_lookalike_user_presets(self):
        copycat = user_preset("dracula-night", "nord-minimal-x", "Nord Minimal")
        own = user_preset("dracula-night", "nord", "My Nord")
        candidates = [*FIXED, copycat, own]
        self.assertEqual(self.match("Nord Minimal", candidates).preset.id, "nord-minimal")
        self.assertEqual(self.match("nord", candidates).preset.id, "nord")
        found = self.match("nord min", candidates)  # a prefix of both: nothing is guessed
        self.assertIsNone(found.preset)
        self.assertEqual({p.id for p in found.suggestions}, {"nord-minimal", "nord-minimal-x"})

    def test_a_word_that_names_other_presets_is_no_typo(self):
        dawn = user_preset("rose-pine-cozy", "pine-dawn", "Pine Dawn")
        dawn.data["meta"]["tags"] = ["cozy", "light"]
        candidates = [*FIXED, dawn]
        for query in ("everforest light", "kanagawa light", "tokyo light", "dracula light"):
            with self.subTest(query=query):
                found = self.match(query, candidates)
                self.assertIsNone(getattr(found.preset, "id", None))  # never the dark preset of that name
                self.assertFalse(found.ambiguous)
                self.assertTrue(found.suggestions)
        self.assertIsNone(getattr(self.match("everforrest light", candidates).preset, "id", None))
        self.assertEqual(self.match("everforrest", candidates).preset.id, "everforest-woods")  # still a typo
        self.assertEqual(self.match("tokyo nite", candidates).preset.id, "tokyo-night-neon")
        self.assertEqual(self.match("dawn light", candidates).preset.id, "pine-dawn")

    def test_name_key_folds_case_accents_and_separators(self):
        self.assertEqual(cli.name_key("Rosé Pine_Cozy"), "rose-pine-cozy")
        self.assertEqual(cli.name_key("--x--y--"), "x-y")


class HostileInputTests(CliCase):
    def assert_refused(self, code, out, err, expected=2):
        self.assertEqual(code, expected, out + err)
        self.assertEqual(self.applied, [])
        self.assertNotIn("\x1b", out + err)
        self.assertNotIn("\x07", out + err)

    def test_hostile_theme_names_change_nothing(self):
        self.activate("nord-minimal", {"panel_style": {"radius": 11}}, ["colors"])
        before = self.record_bytes()
        for name in ("\x1b[2Jnord", "nord\nminimal", "\u202enord", "nord\udcff", "n" * 5000, "", "   ", "nord\x00"):
            with self.subTest(name=repr(name[:20])):
                self.assert_refused(*self.run_cli("theme", name, "--yes"))
        self.assertEqual(self.record_bytes(), before)

    def test_hostile_settings_change_nothing(self):
        self.activate("nord-minimal", {}, ["colors"])
        before = self.record_bytes()
        for argv in (
            ("set", "accent", "\x1b]52;c;ZXZpbA==\x07"),
            ("set", "accent", "#" + "f" * 500),
            ("set", "\x1b[31maccent", "red"),
            ("set", "a" * 100, "red"),
            ("set", "accent", "red\nblur"),
            ("set", "ui-font", "Noto\u2028Sans"),
        ):
            with self.subTest(argv=repr(argv)[:40]):
                self.assert_refused(*self.run_cli(*argv, "--yes"))
        for argv in (
            ("set", "icons", "../../../etc"),
            ("set", "cursor", "/usr/share/icons/x"),
            ("set", "ui-font", "Noto; rm -rf ~"),
            ("set", "shimmer-colour", "url(evil)"),
        ):
            with self.subTest(argv=argv):
                self.assert_refused(*self.run_cli(*argv, "--yes"), expected=1)
        self.assertEqual(self.record_bytes(), before)

    def test_record_tweaks_of_the_wrong_type_are_dropped_not_fatal(self):
        commands = (
            ("theme",),
            ("set", "radius", "10", "--yes"),
            ("reset", "--yes"),
            ("theme", "--light", "--yes"),
            ("theme", "{id}", "--yes"),
        )
        for preset_id, overrides in (("nord-minimal", {"style": "frosted"}), ("tokyo-night-neon", {"palette": ["x"]})):
            self.active = preset_id
            for argv in commands:
                with self.subTest(preset=preset_id, argv=argv):
                    self.tweaks.parent.mkdir(parents=True, exist_ok=True)
                    self.tweaks.write_text(json.dumps({preset_id: {"overrides": overrides, "steps": ["colors"]}}))
                    code, _, err = self.run_cli(*(arg.format(id=preset_id) for arg in argv))
                    self.assertEqual(code, 0, err)
                    self.assertNotIn("Traceback", err)
            with self.assertRaises(presets.PresetError):
                presets.customized(by_id(preset_id), overrides)

    def test_hostile_step_lists_are_refused(self):
        self.assert_refused(*self.run_cli("theme", "nord", "--only", "colors\x1b[2J", "--yes"))
        self.assert_refused(*self.run_cli("theme", "nord", "--skip", "../etc", "--yes"))

    def test_safe_stream_hides_lone_surrogates(self):
        out = io.StringIO()
        cli._SafeStream(out).write("a\udcffb\n")
        self.assertEqual(out.getvalue(), "a?b\n")


# ----- help -----------------------------------------------------------------------------


class HelpTests(unittest.TestCase):
    def help_text(self, *argv: str) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            cli.main([*argv, "--help"])
        return out.getvalue()

    def test_main_help_lists_every_command_quick_ones_first(self):
        text = self.help_text()
        commands = text.split("commands:", 1)[1]
        for name in (
            "theme",
            "set",
            "reset",
            "gui",
            "list",
            "show",
            "apply",
            "restore",
            "clean",
            "validate",
            "import",
            "export",
            "save-desktop",
            "log",
            "steps",
        ):
            self.assertRegex(commands, rf"\n\s+{name}\s", name)
        position = {name: commands.index(f" {name} ") for name in ("theme", "set", "reset", "gui", "list")}
        self.assertLess(max(position["theme"], position["set"], position["reset"]), position["gui"])
        self.assertIn("plasmify theme nord", text)

    def test_plasmify_alone_shows_help_and_gui_opens_the_window(self):
        out = io.StringIO()
        with mock.patch.object(cli, "cmd_gui", return_value=0) as gui, contextlib.redirect_stdout(out):
            self.assertEqual(cli.main([]), 0)
            gui.assert_not_called()
            self.assertIn("plasmify gui", out.getvalue())
            self.assertEqual(cli.main(["gui"]), 0)
        gui.assert_called_once()

    def test_help_has_no_escape_codes(self):
        # Python 3.14 colours argparse help; _SafeStream would print the codes as "?[1;34m".
        with mock.patch.dict(os.environ, {"FORCE_COLOR": "1"}):
            for argv in ((), ("theme",), ("set",), ("gui",)):
                text = self.help_text(*argv)
                self.assertNotIn("\x1b", text, argv)
                self.assertNotIn("?[", text, argv)

    def test_menu_entry_opens_the_window(self):
        entry = Path(__file__).resolve().parent.parent / "data" / "io.github.heyquatch.plasmify.desktop"
        self.assertIn("Exec=plasmify gui\n", entry.read_text())

    def test_command_help_has_options_and_examples(self):
        theme = self.help_text("theme")
        for option in ("--light", "--dark", "--toggle", "--only", "--skip", "--yes", "--dry-run", "--plain"):
            self.assertIn(option, theme)
        self.assertIn("examples:", theme)
        self.assertIn("plasmify theme nord", theme)
        setting = self.help_text("set")
        self.assertIn("examples:", setting)
        self.assertIn("--list", setting)
        self.assertIn("plasmify set accent", setting)
        reset = self.help_text("reset")
        self.assertIn("examples:", reset)
        self.assertIn("plasmify reset --dry-run", reset)

    def test_set_list_names_every_setting(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["set", "--list"]), 0)
        required = [
            "accent",
            "glass",
            "shimmer",
            "shimmer-colour",
            "shimmer-light",
            "shimmer-motion",
            "shimmer-density",
            "shimmer-fps",
            "blur",
            "blur-strength",
            "wobbly",
            "cube",
            "fall-apart",
            "dim-inactive",
            "panel-opacity",
            "radius",
            "ui-font",
            "mono-font",
            "font-size",
            "icons",
            "cursor",
            "interval",
        ]
        for name in (*required, *cli.SETTINGS):
            self.assertRegex(out.getvalue(), rf"\n  {name}\s", name)

    def test_texts_use_no_dashes_as_punctuation(self):
        texts = [cli.MAIN_EPILOG, cli.THEME_EPILOG, cli.SET_EPILOG, cli.RESET_EPILOG]
        texts += [s.allowed for s in cli.SETTINGS.values()]
        texts += [self.help_text(), self.help_text("theme"), self.help_text("set"), self.help_text("reset")]
        for text in texts:
            self.assertNotRegex(text, "[\u2013\u2014]")
            self.assertNotRegex(text, r"--(?![a-z])")  # only option names start with --


# ----- the record -----------------------------------------------------------------------


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name, "state", "applied-tweaks.json")
        self.patch = mock.patch.object(record, "TWEAKS_FILE", self.file)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def operations(self, keep) -> list[tuple]:
        made = {"base": "#f3f2f2", "accent": "#c61026"}
        return [
            ("nord-minimal", {"panel_style": {"radius": 11}}, None, ["colors", "icons"], keep),
            (
                "nord-minimal",
                {"palette": made},
                Path("/x/custom/nord-minimal"),
                ["style"],
                {"palette": None, "made": made},
            ),
            ("rose-pine-cozy", {"effects": {"wobbly": True}, "wallpaper": None}, None, None, keep),
            ("nord-minimal", {"palette": made, "splash": {"text": "Welcome ✨"}}, Path("/x/ü"), ["colors"], keep),
            ("nord-minimal", {}, None, ["fonts"], None),
            ("rose-pine-cozy", {}, None, None, keep),
            ("tokyo-night-neon", {"cursor": {"name": "breeze_cursors", "url": None}}, None, [], keep),
        ]

    def test_format_is_the_windows_format(self):
        record.remember_tweaks(
            "nord-minimal", {"panel_style": {"radius": 11}}, Path("/c/nord"), steps=["icons", "colors"]
        )
        expected = {
            "nord-minimal": {
                "overrides": {"panel_style": {"radius": 11}},
                "custom_dir": "/c/nord",
                "steps": ["colors", "icons"],
            }
        }
        self.assertEqual(self.file.read_bytes(), json.dumps(expected, indent=2).encode())
        self.assertEqual(record.applied_tweaks(), expected)
        self.assertEqual(sorted(p.name for p in self.file.parent.iterdir()), ["applied-tweaks.json"])

    def test_steps_add_up_and_the_way_back_is_kept_until_dropped(self):
        record.remember_tweaks("x", {"a": {"b": 1}}, None, steps=["colors"], own_mode={"made": {}})
        record.remember_tweaks("x", {"a": {"b": 2}}, None, steps=["fonts"])
        entry = record.applied_tweaks()["x"]
        self.assertEqual(entry["steps"], ["colors", "fonts"])
        self.assertEqual(entry["own_mode"], {"made": {}})
        record.remember_tweaks("x", {"a": {"b": 2}}, None, steps=["fonts"], own_mode=None)
        self.assertNotIn("own_mode", record.applied_tweaks()["x"])
        record.remember_tweaks("x", {}, None)
        self.assertEqual(record.applied_tweaks(), {})

    def test_steps_another_preset_ran_are_taken_off(self):
        every = ["colors", "icons", "shell", "style", "terminal", "wallpaper"]
        record.remember_tweaks("nord-minimal", {"panel_style": {"radius": 9}}, None, steps=every)
        record.remember_tweaks("dracula-night", {}, None, steps=every)
        self.assertEqual(record.applied_tweaks()["nord-minimal"]["steps"], [])  # nothing of Nord is left
        self.assertEqual(record.applied_tweaks()["nord-minimal"]["overrides"], {"panel_style": {"radius": 9}})
        record.remember_tweaks("nord-minimal", {}, None, steps=["colors"])
        saved = record.applied_tweaks()
        self.assertEqual(saved["nord-minimal"]["steps"], ["colors"])
        self.assertEqual(saved["dracula-night"]["steps"], ["icons", "shell", "style", "terminal", "wallpaper"])

    def test_an_apply_that_ran_nothing_keeps_the_steps(self):
        record.remember_tweaks("x", {"a": {"b": 1}}, None, steps=["colors", "terminal"])
        record.remember_tweaks("x", {}, None, steps=[], own_mode=None)  # a reset with nothing to run
        self.assertEqual(record.applied_tweaks()["x"]["steps"], ["colors", "terminal"])
        self.assertEqual(record.applied_tweaks()["x"]["overrides"], {})
        record.remember_tweaks("y", {}, None, steps=[])  # nothing ran and nothing was there
        self.assertNotIn("y", record.applied_tweaks())
        record.remember_tweaks("x", {}, None)  # a deleted preset is forgotten
        self.assertEqual(record.applied_tweaks(), {})

    @unittest.skipIf(main_window is None, "PyQt6 missing")
    def test_byte_compatible_with_the_window(self):
        window_file = Path(self.tmp.name, "window", "applied-tweaks.json")
        with mock.patch.object(main_window, "TWEAKS_FILE", window_file):
            for (pid, overrides, folder, steps, own), (_, _, _, _, gui_own) in zip(
                self.operations(record.KEEP), self.operations(main_window.KEEP), strict=True
            ):
                record.remember_tweaks(pid, overrides, folder, steps=steps, own_mode=own)
                main_window.remember_tweaks(pid, overrides, folder, steps=steps, own_mode=gui_own)
                self.assertEqual(self.file.read_bytes(), window_file.read_bytes(), pid)
                self.assertEqual(record.applied_tweaks(), main_window.applied_tweaks())

    @unittest.skipIf(main_window is None, "PyQt6 missing")
    def test_window_and_command_line_take_turns_on_one_file(self):
        with mock.patch.object(main_window, "TWEAKS_FILE", self.file):
            main_window.remember_tweaks("nord-minimal", {"panel_style": {"radius": 5}}, None, steps=["panelstyle"])
            record.remember_tweaks("nord-minimal", {"panel_style": {"radius": 9}}, None, steps=["colors"])
            self.assertEqual(main_window.applied_tweaks()["nord-minimal"]["steps"], ["colors", "panelstyle"])
            main_window.remember_tweaks("dracula-night", {}, None, steps=["colors"])
            self.assertEqual(list(record.applied_tweaks()), ["nord-minimal", "dracula-night"])

    def test_broken_records_read_as_empty_and_can_be_replaced(self):
        for content in (b"{", b"[1, 2]", b"null", b"\xff\xfe\x00garbage", b"[" * 200000, b'{"a": ' * 100000):
            with self.subTest(content=content[:12]):
                self.file.parent.mkdir(parents=True, exist_ok=True)
                self.file.write_bytes(content)
                self.assertEqual(record.applied_tweaks(), {})
                record.remember_tweaks("x", {"a": {"b": 1}}, None)
                self.assertEqual(record.applied_tweaks(), {"x": {"overrides": {"a": {"b": 1}}, "custom_dir": None}})

    def test_an_oversized_record_is_not_read(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text(json.dumps({"x": {"overrides": {"a": "b" * 200}}}))
        with mock.patch.object(record, "MAX_RECORD_BYTES", 64):
            self.assertEqual(record.applied_tweaks(), {})

    def test_odd_entries_do_not_crash(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text(json.dumps({"x": {"overrides": [], "steps": [1, {"a": 1}, "colors"], "own_mode": "?"}}))
        record.remember_tweaks("x", {"a": {"b": 1}}, None, steps=["fonts"])
        entry = record.applied_tweaks()["x"]
        self.assertEqual(entry["steps"], ["colors", "fonts"])
        self.assertNotIn("own_mode", entry)
        self.assertEqual(record.overrides_of({"overrides": [1]}), {})

    def test_a_folder_in_place_of_the_record_reads_as_empty(self):
        self.file.mkdir(parents=True)
        self.assertEqual(record.applied_tweaks(), {})

    def test_a_symlink_is_replaced_not_followed(self):
        target = Path(self.tmp.name, "elsewhere.txt")
        target.write_text("keep me")
        self.file.parent.mkdir(parents=True)
        self.file.symlink_to(target)
        record.remember_tweaks("x", {"a": {"b": 1}}, None)
        self.assertEqual(target.read_text(), "keep me")
        self.assertFalse(self.file.is_symlink())

    def test_folders_from_the_record_must_be_sane(self):
        for value in ("relative/x", "", "/a\x00b", "/" + "a" * 5000, 5, None, ["/x"]):
            with self.subTest(value=repr(value)[:20]):
                self.assertIsNone(record.folder_of({"custom_dir": value}))
        self.assertEqual(record.folder_of({"custom_dir": "/srv/x"}), Path("/srv/x"))
        self.assertIsNone(record.usable(Path(self.tmp.name, "missing")))

    def test_usable_overrides_drop_only_broken_sections(self):
        preset = by_id("nord-minimal")
        kept = record.usable_overrides(
            preset, {"panel_style": {"radius": 11}, "effects": {"window_borders": "huge"}}, None
        )
        self.assertEqual(kept, {"panel_style": {"radius": 11}})


# ----- light and dark -------------------------------------------------------------------


class ModeSwitchTests(CliCase):
    def test_light_and_back_like_the_window(self):
        steps = ["colors", "style", "icons", "terminal", "shell", "wallpaper", "layout"]
        self.activate("rose-pine-cozy", {"panel_style": {"radius": 11}}, steps)
        self.live = True
        code, out, err = self.run_cli("theme", "--light", "--yes")
        self.assertEqual(code, 0, err)
        plan = self.applied[-1]["plan"]
        self.assertFalse(plan.preset.palette.dark)
        self.assertNotIn("kvantum", plan.preset.section("style"))
        self.assertLessEqual(set(self.steps_of()), set(modes.MODE_STEPS))
        self.assertNotIn("wallpaper", self.steps_of())
        entry = self.entry("rose-pine-cozy")
        self.assertEqual(entry["overrides"]["panel_style"], {"radius": 11})
        self.assertFalse(is_dark(entry["overrides"]["palette"]["base"]))
        self.assertIn("own_mode", entry)
        # And back: exactly the tweaks from before.
        self.live = False
        self.assertEqual(self.run_cli("theme", "--dark", "--yes")[0], 0)
        entry = self.entry("rose-pine-cozy")
        self.assertEqual(entry["overrides"], {"panel_style": {"radius": 11}})
        self.assertNotIn("own_mode", entry)

    def test_glass_and_transparency_follow_the_switch(self):
        self.activate("nord-minimal", {"style": {"glass": "glass"}}, ["colors", "style"])
        self.live = True
        self.assertEqual(self.run_cli("theme", "--light", "--yes")[0], 0)
        plan = self.applied[-1]["plan"]
        self.assertIn("style", self.steps_of())  # the glass theme is made for light colours
        self.assertEqual(plan.preset.section("style"), {"glass": "glass"})
        self.assertEqual(glass.effective_transparency(plan.preset.data), 30)  # the light default
        self.live = False
        self.assertEqual(self.run_cli("set", "transparency", "45", "--yes")[0], 0)
        self.assertEqual(self.run_cli("theme", "--dark", "--yes")[0], 0)
        plan = self.applied[-1]["plan"]
        self.assertEqual(plan.preset.section("style"), {"glass": "glass", "transparency": 45})
        self.assertEqual(glass.effective_transparency(plan.preset.data), 45)  # chosen: kept in both modes

    def test_toggle_follows_what_the_screen_shows(self):
        self.activate("nord-minimal", {}, ["colors"])
        self.live = False  # a restore brought the light version back
        self.assertEqual(self.run_cli("theme", "--toggle", "--yes")[0], 0)
        self.assertTrue(self.applied[-1]["plan"].preset.palette.dark)

    def test_the_mode_it_is_already_in_changes_nothing(self):
        self.activate("nord-minimal", {}, ["colors"])
        before = self.record_bytes()
        code, out, _ = self.run_cli("theme", "--dark", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("already dark", out)
        self.assertEqual(self.applied, [])
        self.assertEqual(self.record_bytes(), before)

    def test_only_steps_that_were_applied_run(self):
        self.activate("catppuccin-anime", {}, ["colors", "wallpaper"])
        self.run_cli("theme", "--light", "--yes")
        self.assertEqual(self.steps_of(), ["colors"])

    def test_the_port_switches_exactly_like_modes_switch(self):
        for preset in FIXED:
            with self.subTest(preset=preset.id):
                self.activate(preset.id, {"panel_style": {"radius": 9}}, ["colors", "icons"])
                state = record.applied(FIXED, live=None)
                switch = record.switch_mode(state, not preset.palette.dark)
                expected, keep = modes.switch(preset, state.overrides, state.result, not preset.palette.dark)
                self.assertEqual(switch.overrides, expected)
                self.assertEqual(switch.keep, keep)
                self.assertEqual(switch.preset.data, presets.customized(preset, expected).data)

    def test_the_desktop_wins_over_an_old_record(self):
        preset = by_id("nord-minimal")
        light = {k: v for k, v in generate.mode_variant(preset.palette.colors, False).items() if v}
        self.activate("nord-minimal", {"palette": light, "panel_style": {"radius": 7}}, ["colors"])
        self.live = True  # the record says light, the screen is dark (a restore)
        state = record.applied(FIXED)
        self.assertTrue(state.stale)
        self.assertNotIn("palette", state.overrides)
        self.assertEqual(state.overrides["panel_style"], {"radius": 7})
        self.run_cli("theme", "--light", "--yes")
        self.assertFalse(self.applied[-1]["plan"].preset.palette.dark)

    def test_only_steps_still_on_the_desktop_switch(self):
        self.assertEqual(self.run_cli("theme", "nord", "--yes")[0], 0)
        self.assertEqual(self.run_cli("theme", "dracula", "--yes")[0], 0)
        self.assertEqual(self.run_cli("theme", "nord", "--only", "colors", "--yes")[0], 0)  # on Dracula's desktop
        self.active = "nord-minimal"
        self.assertEqual(self.entry("nord-minimal")["steps"], ["colors"])
        self.run_cli("theme", "--light", "--yes")
        self.assertEqual(self.steps_of(), ["colors"])  # Dracula's style, icons, terminal and prompt stay
        self.run_cli("set", "accent", "red", "--yes")
        self.assertEqual(self.steps_of(), ["colors"])

    def test_after_a_restore_the_way_back_keeps_the_users_accent(self):
        self.activate("dracula-night", {"palette": {"accent": "#ff5555"}}, ["colors"])
        self.live = False  # a restore undid a switch back to dark: light on screen
        state = record.applied(FIXED)
        self.assertTrue(state.stale)
        self.assertIsInstance(state.record["own_mode"], dict)
        self.assertEqual(self.run_cli("theme", "--dark", "--yes")[0], 0)
        entry = self.entry("dracula-night")
        self.assertEqual(entry["overrides"], {"palette": {"accent": "#ff5555"}})
        self.assertNotIn("own_mode", entry)

    def test_set_and_apply_after_a_restore_keep_the_way_back(self):
        for argv in (("set", "radius", "9", "--yes"), ("theme", "dracula", "--yes")):
            with self.subTest(argv=argv):
                self.activate("dracula-night", {"palette": {"accent": "#ff5555"}}, ["colors"], own_mode=None)
                self.live = False  # light on screen, the record still has the dark colours
                self.assertEqual(self.run_cli(*argv)[0], 0)
                self.assertIn("own_mode", self.entry("dracula-night"))
                self.assertEqual(self.run_cli("theme", "--dark", "--yes")[0], 0)
                self.assertEqual(self.entry("dracula-night")["overrides"]["palette"], {"accent": "#ff5555"})
                record.remember_tweaks("dracula-night", {}, None)

    def test_the_default_accent_chosen_while_light_stays(self):
        self.activate("dracula-night", {}, ["colors", "icons"])
        self.live = True
        self.run_cli("set", "accent", "ff5555", "--yes")
        self.run_cli("theme", "--light", "--yes")
        self.live = False
        self.run_cli("set", "accent", "default", "--yes")
        self.assertEqual(self.run_cli("theme", "--dark", "--yes")[0], 0)
        self.assertEqual(self.entry("dracula-night")["overrides"], {})
        self.assertEqual(self.applied[-1]["plan"].preset.palette.accent, by_id("dracula-night").palette.accent)

    def test_icons_picked_while_light_switch_to_their_dark_flavour(self):
        self.activate("dracula-night", {}, ["colors", "icons"])
        self.live = True
        self.run_cli("theme", "--light", "--yes")
        self.live = False
        self.run_cli("set", "icons", "breeze", "--yes")
        self.assertEqual(self.run_cli("theme", "--dark", "--yes")[0], 0)
        self.assertEqual(self.entry("dracula-night")["overrides"], {"icons": {"theme": "breeze-dark"}})
        self.assertEqual(self.applied[-1]["plan"].preset.section("icons")["theme"], "breeze-dark")

    def test_without_a_theme_or_with_a_name_nothing_happens(self):
        self.assertEqual(self.run_cli("theme", "--light", "--yes")[0], 1)
        self.activate("nord-minimal", {}, ["colors"])
        self.assertEqual(self.run_cli("theme", "nord", "--light", "--yes")[0], 2)
        self.assertEqual(self.applied, [])


# ----- set ------------------------------------------------------------------------------


class SetTableTests(unittest.TestCase):
    def test_the_wiki_lists_every_setting(self):
        """docs/wiki/cli.html had no row for four settings plasmify set takes."""
        page = (Path(__file__).resolve().parent.parent / "docs" / "wiki" / "cli.html").read_text()
        table = page.split("<th>Setting</th><th>Values</th>", 1)[1].split("</table>", 1)[0]
        self.assertEqual(re.findall(r"<tr><td><code>([a-z-]+)</code></td>", table), list(cli.SETTINGS))


class SetTests(CliCase):
    def setUp(self):
        super().setUp()
        self.preset = self.activate("nord-minimal", {}, ["colors", "style", "icons", "terminal", "wallpaper"])

    def overrides(self) -> dict:
        return self.entry("nord-minimal").get("overrides", {})

    def test_accent_runs_the_colour_steps_and_is_remembered(self):
        code, out, err = self.run_cli("set", "accent", "FF79C6", "--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.overrides(), {"palette": {"accent": "#ff79c6"}})
        steps = self.steps_of()
        self.assertIn("colors", steps)
        self.assertLessEqual(set(steps), set(modes.MODE_STEPS))
        self.assertNotIn("wallpaper", steps)  # no shimmer to recolour
        self.assertIn("#88c0d0 -> #ff79c6", out)
        self.assertEqual(self.applied[-1]["plan"].preset.palette.accent, "#ff79c6")

    def test_accent_from_the_palette(self):
        for value in ("magenta", "pink", "#B48EAD", "b48ead"):
            with self.subTest(value=value):
                self.run_cli("set", "accent", value, "--yes")
                expected = self.preset.palette.colors["magenta"]
                self.assertEqual(self.overrides().get("palette", {}).get("accent", "?"), expected)
                self.run_cli("set", "accent", "default", "--yes")
                self.assertNotIn("palette", self.overrides())

    def test_effects_in_one_go_run_only_effects(self):
        code, _, err = self.run_cli(
            "set", "wobbly", "3", "cube", "on", "dim-inactive", "50%", "fall-apart", "yes", "-y"
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(
            self.overrides(),
            {"effects": {"wobbly": True, "wobbly_level": 3, "cube": True, "dim_inactive": 50, "fall_apart": True}},
        )
        self.assertEqual(self.steps_of(), ["effects"])
        self.run_cli("set", "wobbly", "off", "dim-inactive", "off", "-y")
        self.assertEqual(self.overrides()["effects"]["wobbly"], False)
        self.assertEqual(self.overrides()["effects"]["dim_inactive"], 0)

    def test_the_presets_own_value_or_default_is_no_change(self):
        self.run_cli("set", "blur-strength", "12", "-y")
        self.assertEqual(self.overrides(), {"effects": {"blur_strength": 12}})
        self.run_cli("set", "blur-strength", "default", "-y")
        self.assertEqual(self.overrides(), {})
        self.run_cli("set", "blur-strength", "12", "-y")
        self.run_cli("set", "blur-strength", "6", "-y")  # Nord's own strength
        self.assertEqual(self.overrides(), {})

    def test_bad_values_change_nothing(self):
        before = self.record_bytes()
        for key, value in (
            ("blur-strength", "99"),
            ("blur-strength", "ten"),
            ("blur", "maybe"),
            ("wobbly", "9"),
            ("dim-inactive", "5"),
            ("panel-opacity", "5%"),
            ("panel-opacity", "150%"),
            ("radius", "31"),
            ("radius", "-1"),
            ("glass", "shiny"),
            ("glass", "subtly"),
            ("transparency", "5%"),
            ("transparency", "90"),
            ("transparency", "0.8"),
            ("transparency", "lots"),
            ("transparency", "40.5%"),
            ("shimmer", "lasers"),
            ("shimmer-colour", "beige"),
            ("shimmer-light", "disco"),
            ("shimmer-motion", "spin"),
            ("shimmer-density", "huge"),
            ("shimmer-fps", "30"),
            ("blur-saturation", "50%"),
            ("blur-noise", "20"),
            ("moving-opacity", "10%"),
            ("sheet", "maybe"),
            ("interval", "10s"),
            ("interval", "2d"),
            ("accent", "#12345g"),
            ("accent", "banana"),
            ("font-size", "100"),
            ("mono-size", "1"),
            ("icons", "Nope-Icons"),
            ("cursor", "nope"),
            ("ui-font", "Wingdings Pro"),
        ):
            with self.subTest(key=key, value=value):
                code, _, err = self.run_cli("set", key, value, "--yes")
                self.assertEqual(code, 1, err)
                self.assertIn(key, err)
                self.assertIn("Nothing was changed", err)
        self.assertEqual(self.applied, [])
        self.assertEqual(self.record_bytes(), before)

    def test_mistakes_in_the_command_itself(self):
        code, _, err = self.run_cli("set", "blurr", "on", "-y")
        self.assertEqual(code, 1)
        self.assertIn("Did you mean blur?", err)
        self.assertEqual(self.run_cli("set", "blur", "-y")[0], 2)
        self.assertEqual(self.run_cli("set", "-y")[0], 2)
        code, _, err = self.run_cli("set", "blur", "on", "blur", "off", "-y")
        self.assertEqual(code, 1)
        self.assertIn("twice", err)
        self.assertEqual(self.run_cli("set", *(["blur", "on"] * 40), "-y")[0], 2)
        self.assertEqual(self.applied, [])

    def test_panel_style_values_add_the_panel_style_step(self):
        code, _, err = self.run_cli("set", "panel-opacity", "80%", "radius", "14px", "-y")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.overrides(), {"panel_style": {"opacity": 0.8, "radius": 14}})
        self.assertEqual(self.steps_of(), ["panelstyle"])
        self.run_cli("set", "panel-opacity", "0.5", "-y")
        self.assertEqual(self.overrides()["panel_style"]["opacity"], 0.5)

    def test_fonts_keep_their_packages_until_both_change(self):
        self.run_cli("set", "ui-font", "fira code", "-y")
        self.assertEqual(self.overrides(), {"fonts": {"ui": "Fira Code"}})
        self.assertEqual(self.steps_of(), ["fonts"])
        self.run_cli("set", "mono-font", "JetBrains Mono", "font-size", "11pt", "-y")
        self.assertEqual(
            self.overrides(), {"fonts": {"ui": "Fira Code", "mono": "JetBrains Mono", "ui_size": 11, "packages": None}}
        )
        self.assertEqual(self.steps_of(), ["fonts", "terminal"])  # terminals use the monospace font
        self.run_cli("set", "ui-font", "default", "-y")
        self.assertEqual(self.overrides(), {"fonts": {"mono": "JetBrains Mono", "ui_size": 11}})

    def test_icons_and_cursor_must_be_installed(self):
        self.run_cli("set", "icons", "papirus", "cursor", "breeze_cursors", "-y")
        self.assertEqual(
            self.overrides(),
            {"icons": {"theme": "Papirus"}, "cursor": {"name": "breeze_cursors", "url": None}},
        )
        self.assertEqual(self.steps_of(), ["icons", "cursor"])
        self.run_cli("set", "icons", "Papirus-Dark", "cursor", "default", "-y")  # the preset's own
        self.assertEqual(self.overrides(), {})
        code, _, err = self.run_cli("set", "icons", "Paprus", "-y")
        self.assertEqual(code, 1)
        self.assertIn("Did you mean Papirus", err)

    def test_wallpaper_settings(self):
        code, _, err = self.run_cli("set", "shimmer", "sparkles", "interval", "15m", "shimmer-color", "pink", "-y")
        self.assertEqual(code, 0, err)
        self.assertEqual(
            self.overrides(), {"wallpaper": {"shimmer": "sparkle", "interval": 900, "shimmer_colour": "magenta"}}
        )
        self.assertEqual(self.steps_of(), ["wallpaper"])
        # With shimmer on, a new accent recolours the sparkles too... unless they have their own colour.
        self.run_cli("set", "shimmer-colour", "accent", "-y")
        self.run_cli("set", "accent", "ff0000", "-y")
        self.assertIn("wallpaper", self.steps_of())

    def test_new_colours_count_as_one_change(self):
        colours = {name: "#123456" for name in ("base", "text", "accent", "red", "green", "blue")}
        tweaks = {"palette": colours, "effects": {"cube": True, "sheet": True}}
        self.assertEqual(cli.change_count(self.preset, tweaks), 3)
        self.assertEqual(cli.change_count(self.preset, {"wallpaper": None}), 1)

    def test_only_real_changes_count(self):
        """As in the window: a tweak equal to the preset's own value, a section the preset does not
        have left out, and what only follows another setting (the fonts' packages) are no change."""
        fonts = self.preset.section("fonts")
        picked = {**fonts, "ui": "Inter", "packages": None}  # the window stores the whole font row
        self.assertEqual(cli.change_count(self.preset, {"fonts": picked}), 1)
        self.assertEqual(cli.change_count(self.preset, {"fonts": {"ui": "Inter"}}), 1)
        own_blur = self.preset.section("effects")["blur_strength"]
        self.assertEqual(cli.change_count(self.preset, {"effects": {"blur_strength": own_blur}}), 0)
        self.assertEqual(cli.change_count(self.preset, {"splash": None}), 0)

    def test_a_glass_choice_counts_as_one_change(self):
        for style in (
            {"glass": "glass"},
            {"transparency": 40},
            {"focused_solid": False},
            {"glass": "clear", "focused_solid": True},
            {"glass": "glass", "transparency": 55, "focused_solid": False},
        ):
            with self.subTest(style=style):
                self.assertEqual(cli.change_count(self.preset, {"style": style}), 1)
        kvantum = by_id("dracula-night")
        self.assertTrue(kvantum.section("style").get("kvantum"))
        self.assertEqual(cli.change_count(kvantum, {"style": {"glass": "glass", "kvantum": None}}), 2)

    def test_more_window_effects(self):
        code, _, err = self.run_cli(
            "set", "sheet", "on", "blur-saturation", "180%", "blur-noise", "6", "moving-opacity", "85%", "-y"
        )
        self.assertEqual(code, 0, err)
        self.assertEqual(
            self.overrides()["effects"],
            {"sheet": True, "blur_saturation": 180, "blur_noise": 6, "moving_opacity": 0.85},
        )
        self.assertEqual(self.steps_of(), ["effects"])

    def test_shimmer_models(self):
        code, _, err = self.run_cli(
            "set",
            "shimmer",
            "sakura",
            "shimmer-light",
            "rays",
            "shimmer-motion",
            "still",
            "shimmer-density",
            "high",
            "shimmer-fps",
            "24",
            "-y",
        )
        self.assertEqual(code, 0, err)
        wallpaper = self.overrides()["wallpaper"]
        self.assertEqual(
            wallpaper,
            {
                "shimmer": "petals",
                "shimmer_light": "rays",
                "shimmer_motion": "still",
                "shimmer_density": "high",
                "shimmer_fps": 24,
            },
        )
        self.assertEqual(self.steps_of(), ["wallpaper"])
        self.run_cli(
            "set",
            "shimmer-light",
            "default",
            "shimmer-motion",
            "default",
            "shimmer-density",
            "default",
            "shimmer-fps",
            "default",
            "-y",
        )
        self.assertEqual(self.overrides()["wallpaper"], {"shimmer": "petals"})

    def test_glass_runs_the_style_step(self):
        code, _, err = self.run_cli("set", "glass", "frosted", "-y")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.overrides(), {"style": {"glass": "frosted"}})
        self.assertIn("style", self.steps_of())

    def test_glass_looks_and_their_other_names(self):
        for raw, look in (
            ("frosted", "frosted"),
            ("Glass", "glass"),
            ("clear", "clear"),
            ("subtle", "frosted"),
            ("on", "glass"),
            ("yes", "glass"),
        ):
            with self.subTest(raw=raw):
                code, out, err = self.run_cli("set", "glass", raw, "-y")
                self.assertEqual(code, 0, err)
                self.assertEqual(self.overrides(), {"style": {"glass": look}})
                self.assertIn("style", self.steps_of())
                self.run_cli("set", "glass", "default", "-y")
        for raw in ("off", "solid", "none", "no"):  # Nord has no glass: off is no change
            with self.subTest(raw=raw):
                self.run_cli("set", "glass", "clear", "-y")
                code, _, err = self.run_cli("set", "glass", raw, "-y")
                self.assertEqual(code, 0, err)
                self.assertEqual(self.overrides(), {})

    def test_the_presets_own_look_under_its_older_name_is_no_change(self):
        data = json.loads(json.dumps(by_id("nord-minimal").data))
        data["meta"]["id"], data["style"] = "old-glass", {"glass": "subtle"}
        old = presets.Preset(data, "user")
        with mock.patch.object(presets, "load_all", lambda include_user=True, problems=None: [*FIXED, old]):
            self.active = "old-glass"
            record.remember_tweaks("old-glass", {}, None, steps=["colors", "style", "effects", "terminal"])
            code, out, err = self.run_cli("set", "glass", "frosted", "-y")
            self.assertEqual(code, 0, err)
            self.assertIn("Nothing to change", out)
            self.assertEqual(self.entry("old-glass").get("overrides", {}), {})
            code, out, _ = self.run_cli("theme")
            self.assertRegex(out, r"  glass\s+frosted")
            self.assertRegex(out, r"  transparency\s+25%")

    def test_transparency_values(self):
        for raw, value in (("40", 40), ("45%", 45), ("0.3", 30), ("70 %", 70), ("10", 10), ("0,55", 55)):
            with self.subTest(raw=raw):
                code, out, err = self.run_cli("set", "glass", "glass", "transparency", raw, "-y")
                self.assertEqual(code, 0, err)
                self.assertEqual(self.overrides(), {"style": {"glass": "glass", "transparency": value}})
                self.assertIn("style", self.steps_of())
                self.assertEqual(glass.effective_transparency(self.applied[-1]["plan"].preset.data), value)
        code, _, err = self.run_cli("set", "see-through", "100%", "-y")
        self.assertEqual(code, 1)
        self.assertIn("transparency must be from 10% to 70%", err)  # named by its own name
        code, out, err = self.run_cli("set", "see-through", "33%", "-y")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.overrides(), {"style": {"glass": "glass", "transparency": 33}})
        self.assertRegex(out, r"transparency\s+55% -> 33%")
        self.run_cli("set", "transparency", "default", "-y")
        self.assertEqual(self.overrides(), {"style": {"glass": "glass"}})
        self.assertEqual(glass.effective_transparency(self.applied[-1]["plan"].preset.data), 40)

    def test_transparency_shows_before_and_after(self):
        self.run_cli("set", "glass", "clear", "-y")
        code, out, err = self.run_cli("set", "transparency", "60%", "-y")
        self.assertEqual(code, 0, err)
        self.assertRegex(out, r"transparency\s+50% -> 60%")
        code, out, _ = self.run_cli("theme")
        self.assertRegex(out, r"\* glass\s+clear")
        self.assertRegex(out, r"\* transparency\s+60%")

    def test_focused_solid(self):
        code, out, err = self.run_cli("set", "glass", "glass", "focused-solid", "off", "-y")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.overrides(), {"style": {"glass": "glass", "focused_solid": False}})
        self.assertIn("style", self.steps_of())
        self.assertRegex(out, r"focused-solid\s+\(glass is off\) -> off")
        self.assertFalse(glass.effective_focused_solid(self.applied[-1]["plan"].preset.data))
        for raw in ("on", "Yes", "true"):
            with self.subTest(raw=raw):
                self.run_cli("set", "focused-solid", "off", "-y")
                code, _, err = self.run_cli("set", "solid-focus", raw, "-y")  # another name for it
                self.assertEqual(code, 0, err)
                self.assertEqual(self.overrides(), {"style": {"glass": "glass"}})  # on is the default
                self.assertTrue(glass.effective_focused_solid(self.applied[-1]["plan"].preset.data))
        code, _, err = self.run_cli("set", "focused-solid", "maybe", "-y")
        self.assertEqual(code, 1)
        self.assertIn("focused-solid must be on or off", err)
        self.run_cli("set", "focused-solid", "default", "-y")
        self.assertEqual(self.overrides(), {"style": {"glass": "glass"}})
        code, out, _ = self.run_cli("theme")
        self.assertRegex(out, r"focused-solid\s+on")  # the window in use stays solid unless turned off
        self.run_cli("set", "glass", "clear", "-y")
        code, out, _ = self.run_cli("theme")
        self.assertRegex(out, r"focused-solid\s+on")  # in every look, Clear included
        code, out, err = self.run_cli("set", "focused-solid", "off", "-y")
        self.assertEqual(code, 0, err)
        self.assertRegex(out, r"focused-solid\s+on -> off")
        self.assertEqual(self.overrides(), {"style": {"glass": "clear", "focused_solid": False}})
        self.assertEqual(cli.change_count(self.preset, self.overrides()), 1)  # one glass choice
        self.assertFalse(glass.effective_focused_solid(self.applied[-1]["plan"].preset.data))

    def test_focused_solid_on_is_no_change_where_it_is_on(self):
        """It is on in every look: storing it again counted a change that changed nothing."""
        self.activate("nord-minimal", {"style": {"glass": "glass"}}, ["colors", "style"])
        before = self.record_bytes()
        code, out, err = self.run_cli("set", "focused-solid", "on", "-y")
        self.assertEqual(code, 0, err)
        self.assertIn("Nothing to change", out)
        self.assertEqual(self.applied, [])
        self.assertEqual(self.record_bytes(), before)
        self.assertEqual(cli.change_count(self.preset, self.overrides()), 1)  # the glass look alone

    def test_focused_solid_needs_glass(self):
        before = self.record_bytes()
        code, _, err = self.run_cli("set", "focused-solid", "off", "-y")
        self.assertEqual(code, 1)
        self.assertIn("focused-solid cannot be set while glass is off", err)
        self.assertIn("plasmify set glass frosted focused-solid off", err)
        self.assertEqual(self.applied, [])
        self.assertEqual(self.record_bytes(), before)
        self.assertEqual(self.run_cli("set", "focused-solid", "off", "glass", "frosted", "-y")[0], 0)
        self.assertEqual(self.run_cli("set", "glass", "off", "-y")[0], 0)
        self.assertEqual(self.overrides(), {"style": {"focused_solid": False}})  # kept for next time
        code, out, _ = self.run_cli("theme")
        self.assertRegex(out, r"focused-solid\s+\(glass is off\)")

    def test_transparency_needs_glass(self):
        before = self.record_bytes()
        code, out, err = self.run_cli("set", "transparency", "40", "-y")
        self.assertEqual(code, 1)
        self.assertIn("transparency cannot be set while glass is off", err)
        self.assertIn("plasmify set glass frosted transparency 30%", err)
        self.assertIn("Nothing was changed", err)
        self.assertEqual(self.applied, [])
        self.assertEqual(self.record_bytes(), before)
        code, _, err = self.run_cli("set", "glass", "off", "transparency", "40", "-y")
        self.assertEqual(code, 1, err)
        # Together with a look it works, and turning glass off later keeps the choice for next time.
        self.assertEqual(self.run_cli("set", "transparency", "40", "glass", "clear", "-y")[0], 0)
        self.assertEqual(self.run_cli("set", "glass", "off", "-y")[0], 0)
        self.assertEqual(self.overrides(), {"style": {"transparency": 40}})
        code, out, _ = self.run_cli("theme")
        self.assertRegex(out, r"transparency\s+\(glass is off\)")
        self.assertEqual(self.run_cli("set", "transparency", "default", "-y")[0], 0)
        self.assertEqual(self.overrides(), {})

    def test_glass_leaves_a_skipped_terminal_alone(self):
        self.run_cli("theme", "catppuccin", "--skip", "terminal", "--yes")
        self.active = "catppuccin-anime"
        code, out, err = self.run_cli("set", "glass", "glass", "-y")
        self.assertEqual(code, 0, err)
        self.assertIn("style", self.steps_of())
        self.assertIn("effects", self.steps_of())  # blur, which glass needs
        self.assertNotIn("terminal", self.steps_of())  # its opacity follows glass, but it was skipped
        self.assertNotIn("terminal", self.entry("catppuccin-anime")["steps"])
        self.run_cli("set", "accent", "red", "-y")
        self.assertNotIn("terminal", self.steps_of())
        self.run_cli("reset", "-y")
        self.assertNotIn("terminal", self.steps_of())

    def test_glass_without_recorded_steps_runs_every_part_it_changes(self):
        self.activate("catppuccin-anime", {}, None)  # an older record: which steps ran is unknown
        self.run_cli("set", "glass", "glass", "-y")
        self.assertIn("terminal", self.steps_of())

    def test_transparency_runs_the_style_step_and_recorded_companions(self):
        self.activate("catppuccin-anime", {"style": {"glass": "clear"}}, ["colors", "style", "effects"])
        code, _, err = self.run_cli("set", "transparency", "70%", "-y")
        self.assertEqual(code, 0, err)
        self.assertIn("style", self.steps_of())
        self.assertNotIn("terminal", self.steps_of())  # its opacity follows, but it was not applied
        self.assertNotIn("effects", self.steps_of())  # clear leaves blur alone
        self.assertEqual(self.applied[-1]["plan"].preset.section("terminal")["opacity"], 0.58)

    def test_every_change_is_validated_as_a_preset(self):
        with mock.patch.object(cli.presets, "customized", wraps=presets.customized) as customized:
            self.run_cli("set", "radius", "9", "-y")
        self.assertTrue(any(call.args[1] == {"panel_style": {"radius": 9}} for call in customized.call_args_list))

    @unittest.skipIf(main_window is None, "PyQt6 missing")
    def test_the_window_shows_the_changes(self):
        from plasmify.gui import customize

        self.run_cli("set", "radius", "9", "wobbly", "on", "accent", "red", "-y")
        overrides = self.overrides()
        self.assertEqual(customize.usable_overrides(self.preset, overrides, None), overrides)

    def test_asks_first_unless_yes(self):
        before = self.record_bytes()
        with mock.patch("builtins.input", return_value="n"):
            code, _, _ = self.run_cli("set", "radius", "9", stdin=FakeTTY())
        self.assertEqual(code, 1)
        self.assertEqual(self.applied, [])
        self.assertEqual(self.record_bytes(), before)
        code, _, err = self.run_cli("set", "radius", "9", stdin=io.StringIO())  # not a terminal
        self.assertEqual(code, 1)
        self.assertIn("--yes", err)
        with mock.patch("builtins.input", return_value=""):
            self.assertEqual(self.run_cli("set", "radius", "9", stdin=FakeTTY())[0], 0)
        self.assertEqual(len(self.applied), 1)

    def test_dry_run_prints_and_records_nothing(self):
        before = self.record_bytes()
        code, out, _ = self.run_cli("set", "accent", "ff79c6", "--dry-run")
        self.assertEqual(code, 0)
        self.assertTrue(self.applied[-1]["runner"].dry_run)
        self.assertIn("Dry run: nothing was changed.", out)
        self.assertEqual(self.record_bytes(), before)

    def test_nothing_to_change(self):
        code, out, _ = self.run_cli("set", "blur", "on", "-y")
        self.assertEqual(code, 0)
        self.assertIn("Nothing to change", out)
        self.assertEqual(self.applied, [])

    def test_without_a_theme(self):
        self.active = None
        code, _, err = self.run_cli("set", "radius", "9", "-y")
        self.assertEqual(code, 1)
        self.assertIn("No Plasmify theme", err)

    def test_changes_in_the_other_mode_keep_the_way_back(self):
        self.activate("rose-pine-cozy", {}, ["colors", "icons"])
        self.live = True
        self.run_cli("theme", "--light", "-y")
        self.live = False
        self.run_cli("set", "radius", "9", "-y")
        self.assertIn("own_mode", self.entry("rose-pine-cozy"))
        self.run_cli("theme", "--dark", "-y")
        self.assertEqual(self.entry("rose-pine-cozy")["overrides"], {"panel_style": {"radius": 9}})

    def test_a_theme_without_fonts_keeps_the_desktops_other_font(self):
        data = json.loads(json.dumps(by_id("nord-minimal").data))
        del data["fonts"]
        data["meta"]["id"] = "no-fonts"
        bare = presets.Preset(data, "user")
        with mock.patch.object(presets, "load_all", lambda include_user=True, problems=None: [*FIXED, bare]):
            self.active = "no-fonts"
            code, _, err = self.run_cli("set", "font-size", "11", "-y")
            self.assertEqual(code, 0, err)
            self.assertEqual(
                self.entry("no-fonts")["overrides"], {"fonts": {"ui_size": 11, "ui": "Noto Sans", "mono": "Hack"}}
            )
            record.remember_tweaks("no-fonts", {}, None)
            with mock.patch.object(cli, "desktop_font", lambda which: None):
                code, _, err = self.run_cli("set", "mono-size", "12", "-y")
            self.assertEqual(code, 1)
            self.assertIn("set ui-font and mono-font", err)

    def test_a_record_that_cannot_be_written_only_warns(self):
        with mock.patch.object(record, "remember_tweaks", side_effect=PermissionError(13, "Permission denied")):
            code, _, err = self.run_cli("set", "radius", "9", "-y")
        self.assertEqual(code, 0)
        self.assertIn("could not remember what was applied (Permission denied)", err)
        self.assertEqual(len(self.applied), 1)


# ----- reset and theme ------------------------------------------------------------------


class ResetTests(CliCase):
    def test_reset_applies_the_preset_as_it_is_defined(self):
        preset = self.activate(
            "nord-minimal",
            {"panel_style": {"radius": 11}, "palette": {"accent": "#ff0000"}, "splash": {"style": "none"}},
            ["colors", "style", "icons", "layout"],
            own_mode={"made": {}},
        )
        code, out, err = self.run_cli("reset", "--yes")
        self.assertEqual(code, 0, err)
        plan = self.applied[-1]["plan"]
        self.assertEqual(plan.preset.data, presets.customized(preset, {}).data)
        self.assertEqual(self.steps_of(), ["colors", "style", "icons"])  # only what the changes touched
        entry = self.entry("nord-minimal")
        self.assertEqual(entry["overrides"], {})
        self.assertNotIn("own_mode", entry)
        self.assertIn("leaves splash alone", out)  # the preset has none: that change stays, and it says so
        self.assertNotIn("panel style", out)  # the colour step puts the Plasma style back

    def test_changes_the_preset_leaves_alone_are_only_forgotten(self):
        self.activate("nord-minimal", {"splash": {"style": "none"}}, ["colors", "splash"])
        code, out, err = self.run_cli("reset", "--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.applied, [])  # Nord has no splash screen to put back
        self.assertIn("leaves splash alone", out)
        self.assertEqual(self.entry("nord-minimal").get("overrides", {}), {})

    def test_a_reset_that_runs_nothing_keeps_the_steps_for_the_switch(self):
        steps = ["colors", "icons", "terminal", "shell", "splash"]
        self.activate("nord-minimal", {"splash": {"style": "none"}}, steps)
        self.assertEqual(self.run_cli("reset", "--yes")[0], 0)
        self.assertEqual(self.applied, [])
        self.assertEqual(self.entry("nord-minimal")["steps"], sorted(steps))
        self.run_cli("theme", "--light", "--yes")
        self.assertEqual(self.steps_of(), ["colors", "icons", "terminal", "shell"])

    def test_a_reset_leaves_parts_that_were_never_applied_alone(self):
        self.run_cli("theme", "dracula", "--skip", "icons", "--yes")  # the user keeps their own icons
        self.active, self.live = "dracula-night", True
        self.run_cli("theme", "--light", "--yes")
        self.assertNotIn("icons", self.steps_of())
        self.assertEqual(self.entry("dracula-night")["overrides"]["icons"], {"theme": "Papirus"})  # never applied
        self.live = False
        self.assertEqual(self.run_cli("reset", "--yes")[0], 0)
        self.assertIn("colors", self.steps_of())
        self.assertNotIn("icons", self.steps_of())

    def test_nothing_to_reset(self):
        self.activate("nord-minimal", {}, ["colors"])
        code, out, _ = self.run_cli("reset", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("no changes", out)
        self.assertEqual(self.applied, [])

    def test_dry_run_keeps_the_record(self):
        self.activate("nord-minimal", {"panel_style": {"radius": 11}}, ["colors"])
        before = self.record_bytes()
        self.assertEqual(self.run_cli("reset", "--dry-run")[0], 0)
        self.assertTrue(self.applied[-1]["runner"].dry_run)
        self.assertEqual(self.record_bytes(), before)


class ThemeTests(CliCase):
    def test_show_the_applied_theme(self):
        self.activate("nord-minimal", {"palette": {"accent": "#ff79c6"}, "effects": {"cube": True}}, ["colors"])
        code, out, _ = self.run_cli("theme")
        self.assertEqual(code, 0)
        self.assertIn("Nord Minimal (nord-minimal), dark", out)
        self.assertRegex(out, r"\* accent\s+#ff79c6")
        self.assertRegex(out, r"\* cube\s+on")
        self.assertRegex(out, r"  blur\s+on")
        self.assertIn("2 Customize changes", out)
        self.assertIn("plasmify theme --light", out)

    def test_show_a_switched_theme(self):
        self.activate("nord-minimal", {}, ["colors"])
        self.live = False
        _, out, _ = self.run_cli("theme")
        self.assertIn("light (switched from the preset's dark colours)", out)

    def test_show_without_a_theme(self):
        code, _, err = self.run_cli("theme")
        self.assertEqual(code, 1)
        self.assertIn("No Plasmify theme is applied", err)

    def test_apply_by_a_forgiving_name(self):
        code, out, err = self.run_cli("theme", "nordic", "--yes")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.applied[-1]["plan"].preset.id, "nord-minimal")
        self.assertIn("Applying Nord Minimal (nord-minimal)", out)
        self.assertEqual(self.entry("nord-minimal")["steps"], sorted(self.steps_of()))

    def test_saved_changes_come_along_unless_plain(self):
        record.remember_tweaks("dracula-night", {"panel_style": {"radius": 9}}, None, steps=["panelstyle"])
        _, out, _ = self.run_cli("theme", "dracula", "--yes")
        self.assertEqual(self.applied[-1]["plan"].preset.section("panel_style")["radius"], 9)
        self.assertIn("with your 1 change", out)
        self.assertEqual(self.entry("dracula-night")["overrides"], {"panel_style": {"radius": 9}})
        self.run_cli("theme", "dracula", "--plain", "--yes")
        plan = self.applied[-1]["plan"]
        self.assertEqual(plan.preset.data, presets.customized(by_id("dracula-night"), {}).data)
        self.assertEqual(self.entry("dracula-night")["overrides"], {})

    def test_only_and_skip(self):
        self.run_cli("theme", "nord", "--only", "colors,fonts", "--yes")
        self.assertEqual(self.steps_of(), ["colors", "fonts"])
        self.run_cli("theme", "nord", "--skip", "layout,wallpaper,effects", "--yes")
        self.assertFalse({"layout", "wallpaper", "effects"} & set(self.steps_of()))
        code, _, err = self.run_cli("theme", "nord", "--only", "colours", "--yes")
        self.assertEqual(code, 2)
        self.assertIn("unknown step", err)

    def test_ambiguous_or_unknown_names_change_nothing(self):
        code, _, err = self.run_cli("theme", "pastel", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("catppuccin-anime", err)
        self.assertIn("rose-pine-cozy", err)
        self.assertIn("Nothing was changed", err)
        code, _, err = self.run_cli("theme", "zzzzzz", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("plasmify list", err)
        self.assertEqual(self.applied, [])
        self.assertFalse(self.tweaks.exists())

    def test_a_light_name_never_applies_the_dark_preset(self):
        dawn = user_preset("rose-pine-cozy", "pine-dawn", "Pine Dawn")
        dawn.data["meta"]["tags"] = ["cozy", "light"]
        with mock.patch.object(presets, "load_all", lambda include_user=True, problems=None: [*FIXED, dawn]):
            code, _, err = self.run_cli("theme", "everforest light", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("no preset called 'everforest light'", err)
        self.assertIn("Did you mean: everforest-woods?", err)
        self.assertEqual(self.applied, [])

    def test_dry_run_prints_and_records_nothing(self):
        code, out, _ = self.run_cli("theme", "nord", "--dry-run")
        self.assertEqual(code, 0)
        self.assertTrue(self.applied[-1]["runner"].dry_run)
        self.assertIn("Commands that would run", out)
        self.assertFalse(self.tweaks.exists())

    def test_asks_before_applying(self):
        with mock.patch("builtins.input", return_value="no"):
            self.assertEqual(self.run_cli("theme", "nord", stdin=FakeTTY())[0], 1)
        self.assertEqual(self.applied, [])


# ----- end to end, in a sealed child process --------------------------------------------

DRIVER = """
import sys

sys.path.insert(0, sys.argv.pop(1))  # the checkout's src folder (-I leaves PYTHONPATH out)
from plasmify import cli, engine, system

make_plan = engine.make_plan
engine.make_plan = lambda preset, steps=None, **_k: make_plan(preset, steps, check_packages=False)


def run(self, args, **kwargs):
    self.log.append(" ".join(args))
    if kwargs.get("mutating", True) and not self.dry_run:
        raise SystemExit("a command that changes the desktop ran: " + args[0])
    return ""


system.Runner.run = run
sys.exit(cli.main(sys.argv[1:]))
"""


class DryRunEndToEndTests(unittest.TestCase):
    """The real engine in dry-run mode, with home, XDG folders and PATH all scratch."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "home"
        self.env = {
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_DATA_HOME": str(self.home / ".local/share"),
            "XDG_STATE_HOME": str(self.home / ".local/state"),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "TMPDIR": str(root / "tmp"),
            "PATH": str(root / "bin"),  # empty: no real tool can run
            "QT_QPA_PLATFORM": "offscreen",
            "LANG": "C.UTF-8",
        }
        for folder in ("bin", "tmp"):
            (root / folder).mkdir()
        (self.home / ".config").mkdir(parents=True)
        (self.home / ".config" / "kdeglobals").write_text("[General]\nColorScheme=PlasmifyNordMinimal\n")
        state = self.home / ".local/state/plasmify"
        state.mkdir(parents=True)
        tweaks = {
            "nord-minimal": {"overrides": {"panel_style": {"radius": 9}}, "custom_dir": None, "steps": ["colors"]}
        }
        (state / "applied-tweaks.json").write_text(json.dumps(tweaks, indent=2))

    def tearDown(self):
        self.tmp.cleanup()

    def files(self) -> dict[str, bytes]:
        return {str(p.relative_to(self.home)): p.read_bytes() for p in sorted(self.home.rglob("*")) if p.is_file()}

    def plasmify(self, *argv: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-I", "-c", DRIVER, str(ROOT / "src"), *argv],
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
            env=self.env,
            cwd=self.tmp.name,
        )

    def test_dry_runs_print_commands_and_change_no_file(self):
        before = self.files()
        for argv in (
            ("theme", "nord", "--dry-run"),
            ("set", "accent", "ff79c6", "radius", "12", "--dry-run"),
            ("theme", "--light", "--dry-run"),
            ("reset", "--dry-run"),
        ):
            with self.subTest(argv=argv):
                done = self.plasmify(*argv)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
                self.assertIn("Commands that would run", done.stdout)
                self.assertIn("kwriteconfig6", done.stdout)
                self.assertIn("Dry run: nothing was changed.", done.stdout)
        self.assertEqual(self.files(), before)
        self.assertEqual(list(Path(self.tmp.name, "tmp").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
