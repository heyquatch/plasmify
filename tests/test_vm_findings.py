"""Smaller problems a VM test of every feature found (2026-10-21), one class each.

Restore, the splash screen and clean up have their own tests (test_restore_record.py and
SplashLeftoverTests in test_preset_switch.py).
"""

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from plasmify import capture, cli, engine, fonts, package_map, panels, presets, record, system  # noqa: E402
from plasmify.distro import FEDORA, Distro  # noqa: E402
from plasmify.steps.layout import panel_script  # noqa: E402

try:
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None

ROOT = Path(__file__).resolve().parent.parent


class InstallerMessageTests(unittest.TestCase):
    def test_the_last_line_names_the_command_that_opens_the_window(self):
        """ "plasmify" alone prints the help now; "plasmify gui" opens the window."""
        text = (ROOT / "install.sh").read_text()
        self.assertIn("or run: $command_name gui", text)


class ExactNameTests(unittest.TestCase):
    """show, apply and export take an exact id, and said only "Unknown preset 'dracula'"."""

    def test_a_close_name_is_suggested(self):
        with self.assertRaisesRegex(presets.PresetError, r"Did you mean dracula-night\?"):
            cli.find_exact("dracula")

    def test_several_close_names_are_all_suggested(self):
        with self.assertRaisesRegex(presets.PresetError, "rose-pine-cozy, rose-pine-dawn"):
            cli.find_exact("rose")

    def test_nothing_close_points_to_the_list(self):
        with self.assertRaisesRegex(presets.PresetError, "plasmify list shows every preset"):
            cli.find_exact("zzzz")

    def test_an_exact_id_is_still_found(self):
        self.assertEqual(cli.find_exact("nord-minimal").id, "nord-minimal")


class FontPlanTests(unittest.TestCase):
    """Every apply said "Downloads 1 font(s)" for a font already in the home folder."""

    def plan(self) -> engine.Plan:
        backend = mock.Mock()
        backend.missing.return_value = []
        backend.sizes.return_value = {}
        resolution = package_map.Resolution({}, ["ttf-nerd-fonts-symbols"], [])
        with (
            mock.patch.object(engine.packages, "backend_for", return_value=backend),
            mock.patch.object(engine.package_map, "resolve", return_value=resolution),
        ):
            fedora = Distro("fedora", FEDORA, "Fedora Linux", "44")
            return engine.make_plan(presets.find("nord-minimal"), ["shell"], target=fedora)

    def test_a_font_in_the_home_folder_is_not_planned_again(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(fonts, "FONT_ROOT", Path(tmp)):
            self.assertEqual(self.plan().font_downloads, ["ttf-nerd-fonts-symbols"])
            self.assertIn("1 font(s)", self.plan().install_summary)
            folder = Path(tmp) / "ttf-nerd-fonts-symbols"
            folder.mkdir()
            self.assertEqual(self.plan().font_downloads, ["ttf-nerd-fonts-symbols"], "an empty folder is no font")
            (folder / "Symbols.ttf").write_bytes(b"font")
            self.assertEqual(self.plan().font_downloads, [])
            self.assertNotIn("font", self.plan().install_summary)


class SaveDesktopTests(unittest.TestCase):
    """save-desktop wrote the powerline prompt whatever was on screen, and read the terminal only
    from Alacritty."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.config, self.data = Path(tmp.name, "config"), Path(tmp.name, "data")
        (self.data / "konsole").mkdir(parents=True)
        self.config.mkdir()
        for patch in (
            mock.patch.object(capture, "CONFIG_HOME", self.config),
            mock.patch.object(capture, "DATA_HOME", self.data),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_prompt_on_screen_is_saved(self):
        templates = ROOT / "src" / "plasmify" / "templates"
        self.assertIsNone(capture.read_prompt())
        for prompt in ("minimal", "powerline"):
            with self.subTest(prompt=prompt):
                (self.config / "starship.toml").write_text((templates / f"starship-{prompt}.toml").read_text())
                self.assertEqual(capture.read_prompt(), prompt)
        (self.config / "starship.toml").write_text('format = "$all"\n')  # the user's own
        self.assertEqual(capture.read_prompt(), "powerline")

    def test_konsole_settings_are_saved(self):
        (self.config / "konsolerc").write_text("[Desktop Entry]\nDefaultProfile=Plasmify.profile\n")
        (self.data / "konsole" / "Plasmify.profile").write_text(
            "[Appearance]\nColorScheme=Plasmify\nFont=Hack,11,-1,5,400,0,0,0,0,0,0,0,0,0,0,1\n\n"
            "[General]\nName=Plasmify\nTerminalMargin=10\n"
        )
        (self.data / "konsole" / "Plasmify.colorscheme").write_text("[General]\nOpacity=0.95\nBlur=true\n")
        self.assertEqual(capture.read_terminal(), {"opacity": 0.95, "blur": True, "padding": 10, "font_size": 11.0})

    def test_another_konsole_profile_is_not_read(self):
        (self.config / "konsolerc").write_text("[Desktop Entry]\nDefaultProfile=Mine.profile\n")
        (self.data / "konsole" / "Plasmify.colorscheme").write_text("[General]\nOpacity=0.5\n")
        self.assertIsNone(capture.read_terminal())


class ShimmerCountTests(unittest.TestCase):
    """After "plasmify set shimmer none" the six Shimmer details set before still counted as changes."""

    def test_details_of_no_shimmer_are_no_change(self):
        before = presets.merge(presets.find("nord-minimal").data, {"wallpaper": {"shimmer": "none"}})
        details = {"shimmer_light": "flare", "shimmer_fps": 24, "shimmer_always": True}
        off = presets.merge(before, {"wallpaper": details})
        self.assertEqual(record.changed_settings(before, off), 0)
        on = presets.merge(before, {"wallpaper": {"shimmer": "petals", **details}})
        self.assertEqual(record.changed_settings(before, on), 4)

    def test_theme_shows_them_as_no_shimmer(self):
        nord = presets.find("nord-minimal")
        result = presets.customized(nord, {"wallpaper": {"shimmer": "none", "shimmer_fps": 24}})
        self.assertEqual(cli.SETTINGS["shimmer-fps"].show(result), cli.NO_SHIMMER)
        shimmering = presets.customized(nord, {"wallpaper": {"shimmer": "snow", "shimmer_fps": 24}})
        self.assertEqual(cli.SETTINGS["shimmer-fps"].show(shimmering), "24")


class ClockTests(unittest.TestCase):
    """The clock flipped from "time over date" to one big line at every Plasma restart: the layout
    script wrote the date position by name, which the running clock does not understand."""

    def test_the_date_position_is_written_as_its_number(self):
        bar = panels.layout_panels({"panels": [{"edge": "bottom", "thickness": 44, "widgets": ["tasks", "clock"]}]})
        script = panel_script(bar, "start-here", [])
        self.assertIn('w.writeConfig("dateDisplayFormat", 1);', script)
        self.assertNotIn('BesideTime")', script)


class DistributionDefaultTests(unittest.TestCase):
    """Putting "the default" Plasma style or splash back deleted the key with kwriteconfig6, which
    writes "name[$d]" and so also hid Fedora's own defaults (kdedefaults): light Breeze panels and
    the Breeze splash instead of Fedora's dark panels and splash."""

    def test_revert_names_the_file_by_its_full_path(self):
        runner = system.Runner(dry_run=True)
        runner.krevert(Path("/home/me/.config/plasmarc"), "Theme", "name")
        self.assertEqual(
            runner.log,
            [
                "kwriteconfig6 --file /home/me/.config/plasmarc --group Theme --key name -",
                "kwriteconfig6 --file /home/me/.config/plasmarc --group Theme --key name --delete",
            ],
        )

    def test_leftovers_are_reverted_not_deleted(self):
        from plasmify.steps import panelstyle, splash
        from plasmify.steps.base import Context

        runner = mock.Mock()
        leftovers = {"plasmarc": "plasmify-x", "ksplashrc": "plasmify-splash-x"}
        runner.kread.side_effect = lambda file, group, key, default="": leftovers[file]
        ctx = Context(presets.find("nord-minimal"), runner, Path("/nonexistent"), log=lambda _: None)
        with (
            mock.patch.object(panelstyle, "own_style", return_value=None),
            mock.patch.object(splash, "own_splash", return_value=(None, None)),
        ):
            panelstyle.keep_own_style(ctx)
            splash.keep_own_splash(ctx)
        runner.kdelete.assert_not_called()
        reverted = [(Path(c.args[0]).name, c.args[1], c.args[2]) for c in runner.krevert.call_args_list]
        splash_keys = [("ksplashrc", "KSplash", "Engine"), ("ksplashrc", "KSplash", "Theme")]
        self.assertEqual(reverted, [("plasmarc", "Theme", "name"), *splash_keys])


class ShellScriptRetryTests(unittest.TestCase):
    """Right after a reload the wallpaper script once failed with "Cannot find
    'org.kde.PlasmaShell.evaluateScript' in object /PlasmaShell": the shell was not ready yet."""

    def runner(self, errors: list[str]) -> tuple[system.Runner, list[str]]:
        runner = system.Runner()
        calls: list[str] = []

        def dbus_call(*args, **kwargs):
            calls.append(args[-1])
            if errors:
                raise system.CommandError(errors.pop(0))
            return "done"

        runner.dbus_call = dbus_call
        return runner, calls

    def test_a_shell_still_starting_gets_the_script_again(self):
        starting = "qdbus-qt6 failed (1): Cannot find 'org.kde.PlasmaShell.evaluateScript' in object /PlasmaShell"
        runner, calls = self.runner([starting])
        with mock.patch.object(system, "wait_for_shell", return_value=True) as wait:
            self.assertEqual(runner.plasma_script("print(1)"), "done")
        self.assertEqual(calls, ["print(1)", "print(1)"])
        wait.assert_called_once()

    def test_a_script_error_is_not_run_twice(self):
        runner, calls = self.runner(["qdbus failed (2): Error: TypeError: panels is not a function"])
        with mock.patch.object(system, "wait_for_shell", return_value=True), self.assertRaises(system.CommandError):
            runner.plasma_script("panels(")
        self.assertEqual(len(calls), 1)

    def test_a_shell_that_never_comes_gives_the_first_error(self):
        runner, calls = self.runner(["Service 'org.kde.plasmashell' does not exist."])
        with mock.patch.object(system, "wait_for_shell", return_value=False), self.assertRaises(system.CommandError):
            runner.plasma_script("print(1)")
        self.assertEqual(len(calls), 1)


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class WindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_gallery_cards_have_names(self):
        """A screen reader read the gallery as a list of blanks."""
        from plasmify.gui.main_window import MainWindow

        window = MainWindow()
        try:
            items = [window.gallery.item(i) for i in range(window.gallery.count())]
            names = [item.data(Qt.ItemDataRole.AccessibleTextRole) for item in items]
            self.assertIn("Nord Minimal", [re.sub(r", active$", "", n) for n in names])
            for preset, name in zip(window.presets, names, strict=True):
                self.assertTrue(name.startswith(preset.name if preset.builtin else f"(yours) {preset.name}"))
        finally:
            window.close()

    def test_restore_log_says_restored_once(self):
        from plasmify.gui.dialogs import RestoreDialog

        dialog = RestoreDialog()
        dialog.log.appendPlainText("Restored. Open terminals pick up the old prompt in new windows.")  # the restore's
        dialog._on_finished("")
        self.assertEqual(dialog.log.toPlainText().count("Restored."), 1)
        self.assertTrue(dialog.restored)
        dialog._on_finished("it broke")
        self.assertIn("Error: it broke", dialog.log.toPlainText())


if __name__ == "__main__":
    unittest.main()
