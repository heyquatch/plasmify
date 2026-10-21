"""Plasmify's own window follows the desktop's look (bugs seen in a recorded demo).

After Switch to light, Plasmify kept painting the old dark Kvantum theme over the new light
colours (a white log box, a light strip behind the Customize toolbar), and with Breeze its menu
bar kept the colours of the theme it was started with. Restores flashed Breeze Dark first.

Skipped without PyQt6. Run headless with QT_QPA_PLATFORM=offscreen. The KDE session test runs
in a private D-Bus session with temporary folders, so the real desktop never sees it.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = Path(__file__).resolve().parents[1] / "src"
ROOT = SRC.parent
sys.path.insert(0, str(SRC))

from plasmify import APP_ID  # noqa: E402
from plasmify.steps import colors  # noqa: E402
from plasmify.system import CommandError, Runner  # noqa: E402

try:
    from PyQt6 import sip
    from PyQt6.QtCore import QObject, pyqtSignal
    from PyQt6.QtGui import QColor, QPalette
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QFrame, QVBoxLayout, QWidget
except ImportError:  # pragma: no cover
    QApplication = None


class StopError(Exception):
    pass


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class NamingTests(unittest.TestCase):
    def test_the_app_is_named_before_qapplication_exists(self):
        """Named after QApplication started, KDE's platform theme and Breeze read the desktop's
        colours through two copies of the settings, and only one of them was ever reloaded."""
        from plasmify.gui import app as gui_app

        with mock.patch.object(gui_app, "QApplication") as fake:
            fake.side_effect = StopError  # the constructor: stop there
            with self.assertRaises(StopError):
                gui_app.run()
        names = [name for name, _args, _kwargs in fake.mock_calls]
        self.assertEqual(names, ["setApplicationName", "setApplicationVersion", "setDesktopFileName", ""])
        fake.setDesktopFileName.assert_called_once_with(APP_ID)

    def test_app_id_matches_the_menu_entry_and_icon(self):
        """The task manager finds the menu entry by the Wayland app id, and its icon by name."""
        entry = (ROOT / "data" / f"{APP_ID}.desktop").read_text()
        self.assertIn(f"\nIcon={APP_ID}\n", entry)
        installer = (ROOT / "install.sh").read_text()
        self.assertIn(f"applications/{APP_ID}.desktop", installer)
        self.assertIn(f"apps/{APP_ID}.svg", installer)


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class FollowerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from plasmify.gui import restyle

        self.restyle = restyle
        saved = QPalette(self.app.palette())
        self.addCleanup(lambda: QApplication.setPalette(saved))
        # Without KDE's signal (no session bus here) the follower watches palette changes.
        with mock.patch.object(restyle.LookFollower, "_listen_to_kde", return_value=False):
            self.follower = restyle.LookFollower(self.app)
        self.addCleanup(sip.delete, self.follower)
        patch = mock.patch.object(restyle, "desktop_style", return_value=self.app.style().name())
        patch.start()
        self.addCleanup(patch.stop)

    def window_colour(self, colour: str) -> None:
        palette = QPalette(self.app.palette())
        palette.setColor(QPalette.ColorRole.Window, QColor(colour))
        QApplication.setPalette(palette)

    def test_a_new_style_instance_under_the_same_name(self):
        """KDE keeps an app's style while its name stays (kvantum to kvantum), and Kvantum reads
        its theme only when the style is made: only a new instance sees the new theme."""
        before = self.app.style()
        name = before.name()
        self.follower.restyle()
        self.assertIsNot(self.app.style(), before)
        self.assertEqual(self.app.style().name(), name)
        self.assertEqual(self.follower.restyles, 1)

    def test_an_unknown_style_falls_back_to_the_one_in_use(self):
        name = self.app.style().name()
        with mock.patch.object(self.restyle, "desktop_style", return_value="NoSuchStyle"):
            self.follower.restyle()
        self.assertEqual(self.app.style().name(), name)

    def test_style_sheets_take_the_new_colours(self):
        """A style sheet naming palette(window) kept the colour it was set with: the light strip
        behind the Customize toolbar after a switch."""
        page = QWidget()
        self.addCleanup(page.deleteLater)
        bar = QFrame()
        bar.setObjectName("bar")
        bar.setStyleSheet("QFrame#bar { background: palette(window); border: none; }")
        bar.setFixedSize(60, 30)
        QVBoxLayout(page).addWidget(bar)
        page.show()

        def shown() -> str:
            return page.grab().toImage().pixelColor(bar.mapTo(page, bar.rect().center())).name()

        self.window_colour("#123456")
        self.follower.restyle()
        self.assertEqual(shown(), "#123456")
        self.window_colour("#abcdef")
        self.follower.restyle()
        self.assertEqual(shown(), "#abcdef")

    def test_a_burst_of_changes_restyles_once(self):
        for colour in ("#101010", "#202020", "#303030"):
            self.window_colour(colour)
            self.follower.changed()
        QTest.qWait(self.restyle.SETTLE_MS + 300)
        self.assertEqual(self.follower.restyles, 1)
        QTest.qWait(self.restyle.SETTLE_MS + 300)  # its own palette change starts no other
        self.assertEqual(self.follower.restyles, 1)

    def test_held_while_an_apply_runs(self):
        self.follower.hold()
        self.window_colour("#404040")
        QTest.qWait(self.restyle.SETTLE_MS + 300)
        self.assertEqual(self.follower.restyles, 0)
        self.follower.release()
        QTest.qWait(self.restyle.SETTLE_MS + 300)
        self.assertEqual(self.follower.restyles, 1)

    def test_only_colour_and_style_changes_count(self):
        """Icons, fonts and the cursor reach the window without a new style."""
        for kind in (1, 3, 4, 5):  # fonts, settings, icons, cursor
            self.follower._kde_changed(kind, 0)
        self.assertFalse(self.follower._timer.isActive())
        self.follower._kde_changed(2, 0)  # the widget style
        self.assertTrue(self.follower._timer.isActive())

    def test_the_apply_dialog_holds_until_its_worker_is_done(self):
        """The steps change colours, then the style, then icons: restyling in between showed new
        colours over the old Kvantum theme. The dialog also says at once when the apply worked."""
        from plasmify import engine, presets
        from plasmify.gui import dialogs

        class Worker(QObject):
            progress = pyqtSignal(int, int, str)
            message = pyqtSignal(str)
            finished_with = pyqtSignal(object, list, str)
            finished = pyqtSignal()

            def __init__(self, *_args):
                super().__init__()

            def start(self):
                pass

            def isRunning(self):  # noqa: N802 (Qt API)
                return False

        plan = engine.make_plan(presets.find("nord-minimal"), ["colors"], check_packages=False)
        with mock.patch.object(dialogs, "ApplyWorker", Worker), mock.patch.object(self.restyle, "follower",
                                                                                  return_value=self.follower):  # fmt: skip
            dialog = dialogs.ApplyDialog(plan)
            self.addCleanup(dialog.deleteLater)
            applied = []
            dialog.applied.connect(lambda: applied.append(True))
            self.window_colour("#505050")
            QTest.qWait(self.restyle.SETTLE_MS + 300)
            self.assertEqual(self.follower.restyles, 0)  # held
            dialog.worker.finished_with.emit(None, [], "")
            self.assertEqual(applied, [True])
            dialog.worker.finished.emit()
            QTest.qWait(self.restyle.SETTLE_MS + 300)
        self.assertEqual(self.follower.restyles, 1)

    def test_restyled_once_colours_and_style_are_in_place(self):
        """Wallpapers (and Shimmer) can take a minute after the colours and the style: Plasmify
        restyles when the step after them begins, not when the whole apply is done."""
        from plasmify import engine, presets
        from plasmify.gui import dialogs

        class Worker(QObject):
            progress = pyqtSignal(int, int, str)
            message = pyqtSignal(str)
            finished_with = pyqtSignal(object, list, str)
            finished = pyqtSignal()

            def __init__(self, *_args):
                super().__init__()

            def start(self):
                pass

            def isRunning(self):  # noqa: N802 (Qt API)
                return False

        plan = engine.make_plan(presets.find("nord-minimal"), ["colors", "style", "icons"], check_packages=False)
        with mock.patch.object(dialogs, "ApplyWorker", Worker), mock.patch.object(self.restyle, "follower",
                                                                                  return_value=self.follower):  # fmt: skip
            dialog = dialogs.ApplyDialog(plan)
            self.addCleanup(dialog.deleteLater)
            progress = dialog.worker.progress
            progress.emit(2, 5, "Colours")
            self.window_colour("#606060")
            progress.emit(3, 5, "Application style")
            self.assertEqual(self.follower.restyles, 0)
            progress.emit(4, 5, "Icons")
            QTest.qWait(self.restyle.SETTLE_MS + 300)
            self.assertEqual(self.follower.restyles, 1)  # although the apply goes on
            progress.emit(5, 5, "Done")
            dialog.worker.finished_with.emit(None, [], "")
            dialog.worker.finished.emit()
            QTest.qWait(self.restyle.SETTLE_MS + 300)
        self.assertEqual(self.follower.restyles, 1)  # nothing changed since: no second restyle


class ReapplySchemeTests(unittest.TestCase):
    """plasma-apply-colorscheme does nothing for the scheme kdeglobals names. Plasmify applied
    Breeze Dark first to get past that, which flashed dark colours over every open window
    (after a restore Plasmify looked half dark, half light for a moment)."""

    def runner(self, active: str, fail: bool = False) -> Runner:
        runner = Runner(dry_run=True)
        store = {"ColorScheme": active}

        def kread(file, group, key, default=""):
            return store.get(key, default) if (file, group) == ("kdeglobals", "General") else default

        def run(args, **kwargs):
            runner.log.append(" ".join(args))
            if fail and args[0] == "plasma-apply-colorscheme":
                raise CommandError("plasma-apply-colorscheme failed (255): not found")
            return ""

        runner.kread = kread
        runner.run = run
        return runner

    def test_the_active_scheme_is_applied_again_without_another_one(self):
        runner = self.runner("PlasmifyNordMinimal")
        colors.apply_scheme(runner, "PlasmifyNordMinimal")
        self.assertEqual(
            runner.log,
            [
                "kwriteconfig6 --file kdeglobals --group General --key ColorScheme PlasmifyReapplying",
                "plasma-apply-colorscheme PlasmifyNordMinimal",
            ],
        )
        self.assertFalse([line for line in runner.log if "BreezeDark" in line])

    def test_breeze_light_counts_as_active_when_none_is_named(self):
        runner = self.runner("")
        colors.apply_scheme(runner, "BreezeLight")
        self.assertIn("PlasmifyReapplying", runner.log[0])

    def test_another_scheme_is_applied_directly(self):
        runner = self.runner("BreezeLight")
        colors.apply_scheme(runner, "PlasmifyNordMinimal")
        self.assertEqual(runner.log, ["plasma-apply-colorscheme PlasmifyNordMinimal"])

    def test_a_failed_apply_puts_the_name_back(self):
        runner = self.runner("PlasmifyNordMinimal", fail=True)
        colors.apply_scheme(runner, "PlasmifyNordMinimal")
        self.assertEqual(
            runner.log[-1], "kwriteconfig6 --file kdeglobals --group General --key ColorScheme PlasmifyNordMinimal"
        )


KDE_TOOLS = ("dbus-run-session", "dbus-send", "kwriteconfig6", "kreadconfig6", "plasma-apply-colorscheme")
QT_PLUGINS = [
    Path(p) for p in ("/usr/lib/qt6/plugins", "/usr/lib64/qt6/plugins", "/usr/lib/x86_64-linux-gnu/qt6/plugins")
]


def kde_session_available() -> bool:
    themes = [d / "platformthemes" for d in QT_PLUGINS]
    has_theme = any(
        p.is_dir() and any(f.name.startswith("KDEPlasmaPlatformTheme") for f in p.iterdir()) for p in themes
    )
    return QApplication is not None and has_theme and all(shutil.which(tool) for tool in KDE_TOOLS)


SESSION_SCRIPT = textwrap.dedent(
    """
    import json, subprocess, sys
    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QPalette
    from PyQt6.QtWidgets import QApplication, QFrame, QMainWindow, QPlainTextEdit, QVBoxLayout, QWidget
    from plasmify.gui import app as gui_app, restyle

    gui_app.name_the_app()
    app = QApplication(sys.argv)
    restyle.follow_desktop(app)
    window = QMainWindow()
    window.menuBar().addMenu("File")
    page = QWidget()
    bar = QFrame()
    bar.setObjectName("bar")
    bar.setStyleSheet("QFrame#bar { background: palette(window); border: none; }")
    bar.setMinimumHeight(30)
    edit = QPlainTextEdit()
    layout = QVBoxLayout(page)
    layout.addWidget(bar)
    layout.addWidget(edit)
    window.setCentralWidget(page)
    window.resize(300, 200)
    window.show()
    steps = json.loads(sys.argv[1])
    seen = []

    def colour(widget):
        image = window.grab().toImage()
        point = widget.mapTo(window, widget.rect().center())
        return image.pixelColor(point.x() + widget.width() // 3, point.y()).name()

    def step(index=0):
        if index == len(steps):
            print(json.dumps(seen))
            app.quit()
            return
        kind, value = steps[index]
        if kind == "sh":
            subprocess.run(value, shell=True, capture_output=True)  # noqa: S602 (fixed test commands)
        else:
            seen.append({"style": app.style().name().lower(), "menu": colour(window.menuBar()),
                         "bar": colour(bar), "edit": colour(edit),
                         "window": app.palette().color(QPalette.ColorRole.Window).name()})
        QTimer.singleShot(1500 if kind == "sh" else 0, lambda: step(index + 1))

    QTimer.singleShot(300, step)
    app.exec()
    """
)


@unittest.skipUnless(kde_session_available(), "needs KDE's Qt platform theme, Breeze and the Plasma config tools")
class KdeSessionTests(unittest.TestCase):
    """A real KDE platform theme and Breeze, in a private D-Bus session with its own folders."""

    def session(self, setup: str, steps: list) -> list[dict]:
        with tempfile.TemporaryDirectory() as home:
            env = {k: v for k, v in os.environ.items() if not k.startswith(("XDG_", "DBUS_", "WAYLAND_", "KDE_"))}
            env.pop("DISPLAY", None)
            env.update(
                HOME=home,
                XDG_CONFIG_HOME=f"{home}/.config",
                XDG_DATA_HOME=f"{home}/.local/share",
                XDG_STATE_HOME=f"{home}/.local/state",
                XDG_CACHE_HOME=f"{home}/.cache",
                QT_QPA_PLATFORM="offscreen",
                QT_QPA_PLATFORMTHEME="kde",
                XDG_CURRENT_DESKTOP="KDE",
                KDE_FULL_SESSION="true",
                KDE_SESSION_VERSION="6",
                PYTHONPATH=str(SRC),
            )
            Path(home, ".config", "Kvantum").mkdir(parents=True)
            script = Path(home, "session.py")
            script.write_text(SESSION_SCRIPT)
            inner = f'{{ {setup}; }} >/dev/null 2>&1; exec {sys.executable} -P {script} "$0"'
            result = subprocess.run(  # noqa: S603 (fixed commands in a private session)
                ["dbus-run-session", "--", "bash", "-c", inner, json.dumps(steps)],
                env=env, capture_output=True, text=True, timeout=120, cwd=home,
            )  # fmt: skip
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_breeze_menu_bar_and_style_sheets_follow_new_colours(self):
        breeze = "kwriteconfig6 --file kdeglobals --group KDE --key widgetStyle Breeze"
        light, dark = self.session(
            f"plasma-apply-colorscheme BreezeLight; {breeze}",
            [["look", ""], ["sh", "plasma-apply-colorscheme BreezeDark"], ["look", ""]],
        )
        self.assertEqual(dark["style"], "breeze")
        self.assertNotEqual(dark["menu"], light["menu"])  # it stayed light before
        self.assertEqual(dark["bar"], dark["window"])  # so did the Customize toolbar's strip
        self.assertLess(QColor(dark["menu"]).lightness(), 100)

    def test_a_stock_kdeglobals_put_back_turns_the_window_light(self):
        """A restore to a stock setup leaves kdeglobals without any colours or scheme name: told
        that the colours changed, KDE falls back to Breeze Light (it stayed dark before)."""
        stock = 'printf "[KDE]\\nwidgetStyle=Breeze\\n" > "$XDG_CONFIG_HOME/kdeglobals"'
        notify = "dbus-send --session --type=signal /KGlobalSettings org.kde.KGlobalSettings.notifyChange"
        breeze = "kwriteconfig6 --file kdeglobals --group KDE --key widgetStyle Breeze"
        dark, light = self.session(
            f"plasma-apply-colorscheme BreezeDark; {breeze}",
            [["look", ""], ["sh", f"{stock}; {notify} int32:0 int32:0; {notify} int32:2 int32:0"], ["look", ""]],
        )
        self.assertLess(QColor(dark["window"]).lightness(), 100)
        self.assertGreater(QColor(light["window"]).lightness(), 200)
        self.assertEqual(light["bar"], light["window"])

    @unittest.skipUnless(
        Path("/usr/share/Kvantum/KvArc").is_dir() and Path("/usr/share/Kvantum/KvArcDark").is_dir(),
        "needs Kvantum and its KvArc themes",
    )
    def test_a_new_kvantum_theme_under_the_same_style(self):
        """Dark to light with glass: Kvantum stays the style, only its theme changes."""
        theme = 'printf "[General]\\ntheme=%s\\n" > "$XDG_CONFIG_HOME/Kvantum/kvantum.kvconfig"'
        style = (
            "dbus-send --session --type=signal /KGlobalSettings org.kde.KGlobalSettings.notifyChange int32:2 int32:0"
        )
        setup = (
            "plasma-apply-colorscheme BreezeDark; kwriteconfig6 --file kdeglobals --group KDE --key widgetStyle kvantum"
        )
        dark, light = self.session(
            f"{setup}; {theme % 'KvArcDark'}",
            [["look", ""], ["sh", f"plasma-apply-colorscheme BreezeLight; {theme % 'KvArc'}; {style}"], ["look", ""]],
        )
        self.assertEqual(light["style"], "kvantum")
        self.assertGreater(QColor(light["bar"]).lightness(), 200)  # the new, light theme
        self.assertGreater(QColor(light["edit"]).lightness(), 200)
        self.assertLess(QColor(dark["bar"]).lightness(), 100)


if __name__ == "__main__":
    unittest.main()
