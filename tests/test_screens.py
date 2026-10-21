"""Drawn terminal logos, the splash screen, the login screen and system-file backups."""

import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from plasmify import asciiart, backup, presets, rootops, schema
from plasmify.steps import login, splash
from plasmify.steps.base import Context
from plasmify.system import Runner

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def base() -> dict:
    return copy.deepcopy(presets.find("nord-minimal").data)


class AsciiArtTests(unittest.TestCase):
    def test_every_sample_is_valid(self):
        for name in asciiart.SAMPLES:
            with self.subTest(name):
                self.assertIsNone(schema.ascii_art(asciiart.sample(name)))
                data = base()
                data["shell"].update({"logo": "ascii", "logo_ascii": asciiart.sample(name)})
                self.assertEqual(schema.validate(data), [])

    def test_hostile_art_is_refused(self):
        cases = {
            "colour code": "  $1 boom",
            "tab": "a\tb",
            "escape sequence": "\x1b[31mred",
            "carriage return": "line\rover",
            "too many lines": "\n".join(["x"] * (schema.ASCII_MAX_LINES + 1)),
            "too wide": "x" * (schema.ASCII_MAX_WIDTH + 1),
            "blank": "   \n  ",
            "not text": 42,
        }
        for label, art in cases.items():
            with self.subTest(label):
                self.assertIsNotNone(schema.ascii_art(art))

    def test_drawings_survive_saving(self):
        import tomllib

        from plasmify import tomlwrite

        data = base()
        for name in asciiart.SAMPLES:
            data["shell"].update({"logo": "ascii", "logo_ascii": asciiart.sample(name)})
            with self.subTest(name):
                self.assertEqual(tomllib.loads(tomlwrite.dumps(data))["shell"]["logo_ascii"], asciiart.sample(name))

    def test_ascii_logo_needs_art(self):
        data = base()
        data["shell"]["logo"] = "ascii"
        self.assertTrue(any("logo_ascii" in e for e in schema.validate(data)))


class MergeTests(unittest.TestCase):
    def test_none_is_dropped_in_new_sections(self):
        merged = presets.merge({}, {"login": {"background": "wallpaper", "background_file": None}})
        self.assertEqual(merged, {"login": {"background": "wallpaper"}})

    def test_none_removes_existing_keys(self):
        merged = presets.merge({"fonts": {"ui": "A", "packages": ["x"]}}, {"fonts": {"packages": None}})
        self.assertEqual(merged, {"fonts": {"ui": "A"}})


class SplashTests(unittest.TestCase):
    def test_values_cannot_break_out_of_qml(self):
        preset = presets.Preset(base(), "builtin")
        hostile = '"}\nimport Evil 1.0\nItem { x: "'
        qml = splash.splash_qml(preset, None, None, hostile, True)
        self.assertIn(json.dumps(hostile), qml)
        self.assertNotIn("\nimport Evil", qml)

    def test_package_is_written(self):
        data = base()
        data["splash"] = {"style": "plasmify", "background": "image", "background_file": "bg.png", "text": "Hi"}
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp, "assets")
            assets.mkdir()
            (assets / "bg.png").write_bytes(PNG)
            preset = presets.Preset(data, "user", asset_override=assets)
            root = Path(tmp, "pkg")
            warnings: list[str] = []
            splash.write_package(root, preset, warnings.append)
            self.assertEqual(warnings, [])
            qml = (root / "contents/splash/Splash.qml").read_text()
            self.assertIn('"images/background.png"', qml)
            self.assertTrue((root / "contents/splash/images/background.png").is_file())
            meta = json.loads((root / "metadata.json").read_text())
            self.assertEqual(meta["KPackageStructure"], "Plasma/LookAndFeel")

    def test_missing_own_image_only_warns(self):
        data = base()
        data["splash"] = {"style": "plasmify", "background": "image", "background_file": "gone.png"}
        with tempfile.TemporaryDirectory() as tmp:
            preset = presets.Preset(data, "user", asset_override=Path(tmp))
            warnings: list[str] = []
            splash.write_package(Path(tmp, "pkg"), preset, warnings.append)
            self.assertTrue(warnings)
            self.assertIn("visible: false", (Path(tmp, "pkg/contents/splash/Splash.qml")).read_text())


class LoginTests(unittest.TestCase):
    def setUp(self):
        # CI has no KDE tools; RootProgramTests checks the real rootops.check_programs.
        patcher = mock.patch.object(rootops, "check_programs")
        self.check_programs = patcher.start()
        self.addCleanup(patcher.stop)

    def _apply(self, manager: str | None, login_section: dict, image: bytes = PNG) -> tuple[Runner, Context]:
        data = base()
        data["login"] = login_section
        tmp = tempfile.mkdtemp()
        assets = Path(tmp, "assets")
        assets.mkdir()
        (assets / "me.png").write_bytes(image)
        preset = presets.Preset(data, "user", asset_override=assets)
        runner = Runner()
        runner.fed = []
        runner.run = lambda args, **kw: (runner.log.append(" ".join(args)), runner.fed.append(kw.get("input_bytes")))[0]
        ctx = Context(preset, runner, Path(tmp), log=lambda _: None)
        with mock.patch.object(login, "login_manager", return_value=manager):
            login.LoginStep().apply(ctx)
        return runner, ctx

    def test_one_fixed_root_command_fed_on_stdin(self):
        runner, _ctx = self._apply("plasmalogin", {"background": "image", "background_file": "me.png"})
        commands = [c for c in runner.log if "plasmify-login" in c]
        self.assertEqual(len(commands), 1)
        self.assertTrue(commands[0].startswith(f"{rootops.PKEXEC} {rootops.SH} -c "))
        self.assertTrue(commands[0].endswith("plasmify-login background.png plasmalogin"))
        self.assertEqual(runner.fed[-1], PNG)  # root gets bytes, never a path it would read itself

    def test_symlinked_picture_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp, "me.png")
            link.symlink_to("/etc/passwd")
            with self.assertRaises(OSError):
                rootops.read_image(link)

    def test_non_image_is_refused(self):
        runner, ctx = self._apply("plasmalogin", {"background": "image", "background_file": "me.png"}, b"#!/bin/sh")
        self.assertFalse([c for c in runner.log if "plasmify-login" in c])

    def test_no_login_manager_skips(self):
        runner, ctx = self._apply(None, {"background": "image", "background_file": "me.png"})
        self.assertFalse([c for c in runner.log if "plasmify-login" in c])
        self.assertTrue(ctx.warnings)

    def test_missing_kwriteconfig6_skips_before_the_password(self):
        self.check_programs.side_effect = ValueError(f"{rootops.KWRITECONFIG6} is missing")
        runner, ctx = self._apply("plasmalogin", {"background": "image", "background_file": "me.png"})
        self.assertFalse([c for c in runner.log if "plasmify-login" in c])
        self.assertIn(rootops.KWRITECONFIG6, " ".join(ctx.warnings))

    def test_login_file_cannot_escape(self):
        data = base()
        data["login"] = {"background": "image", "background_file": "../../etc/shadow.png"}
        self.assertTrue(schema.validate(data))

    def test_scripts_are_fixed_text(self):
        for script in (rootops.LOGIN_SCRIPT, rootops.RESTORE_SCRIPT):
            self.assertNotRegex(script, r"\{[A-Za-z_]+\}|%s|%\(")  # no Python formatting into scripts
        with self.assertRaises(ValueError):
            rootops.login_command("../../etc/passwd", "plasmalogin")
        with self.assertRaises(ValueError):
            rootops.login_command("background.png", "evil")


class RootProgramTests(unittest.TestCase):
    def test_every_program_is_called_by_full_path(self):
        for script in (rootops.LOGIN_SCRIPT, rootops.RESTORE_SCRIPT):
            programs = set(re.findall(r"/usr/bin/([\w-]+)", script))
            self.assertIn(rootops.KWRITECONFIG6, script)
            self.assertIn("kwriteconfig6", programs)
            for name in programs:  # never also by bare name, which a PATH would resolve
                self.assertNotRegex(script, rf"(?<![\w/.-]){re.escape(name)}\b", name)

    def test_missing_kwriteconfig6_is_refused_before_the_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            not_executable = Path(tmp, "plain")
            not_executable.write_text("#!/bin/sh\n")
            for path in (Path(tmp, "absent"), not_executable):
                with self.subTest(path=path.name), mock.patch.object(rootops, "KWRITECONFIG6", str(path)):
                    with self.assertRaisesRegex(ValueError, "is missing"):
                        rootops.login_command("background.png", "plasmalogin")
                    with self.assertRaisesRegex(ValueError, "is missing"):
                        rootops.restore_command("plasmalogin", {"WallpaperPluginId": None, "Image": None})
        with mock.patch.object(rootops, "KWRITECONFIG6", rootops.SH):  # any program that exists
            self.assertEqual(rootops.login_command("background.png", "sddm")[-1], "sddm")
            self.assertEqual(rootops.restore_command("sddm", {"background": None})[-2:], ["delete", "-"])


@mock.patch.object(rootops, "check_programs", mock.Mock())  # CI has no KDE tools
class RootRestoreTests(unittest.TestCase):
    def test_restore_rewrites_only_managed_keys(self):
        values = {"WallpaperPluginId": "org.kde.color", "Image": None}
        command = rootops.restore_command("plasmalogin", values)
        self.assertEqual(command[:3], [rootops.PKEXEC, rootops.SH, "-c"])
        self.assertEqual(command[5:], ["plasmalogin", "set", "org.kde.color", "delete", "-"])

    def test_tampered_values_are_refused(self):
        for value in ("x\n[X11]\nDisplayCommand=/tmp/evil", "-delete", "file:///etc/../tmp/x", "a" * 400, "$(id)"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                rootops.restore_command("plasmalogin", {"WallpaperPluginId": "org.kde.image", "Image": value})

    @unittest.skipUnless(shutil.which("kreadconfig6"), "KDE config tools missing")
    def test_backups_keep_values_not_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = Path(tmp, "plasmalogin.conf")
            conf.write_text("[Greeter]\nWallpaperPluginId=org.kde.color\n")
            fake = {"plasmalogin": (str(conf), rootops.MANAGED["plasmalogin"][1])}
            with (
                mock.patch.dict(rootops.MANAGED, fake),
                mock.patch.object(backup, "ROOT_RESTORABLE", (str(conf),)),
                mock.patch.object(backup, "ROOT_MANAGERS", {str(conf): "plasmalogin"}),
            ):
                snap = backup.create([conf], "test", root=Path(tmp, "backups"))
                entry = snap.entries[0]
                self.assertEqual(entry["keys"]["WallpaperPluginId"], "org.kde.color")
                self.assertIsNone(entry["keys"]["Image"])
                self.assertFalse(any(Path(tmp, "backups").rglob("plasmalogin.conf")))  # no copy to tamper with
                command = backup.root_restore_command(snap)
            self.assertIn("org.kde.color", command)

    def test_old_backups_are_not_restored_as_root(self):
        old = backup.Backup("x", Path("/x"), 0, "x", [{"path": "/etc/plasmalogin.conf", "existed": True, "root": True}])
        with self.assertRaises(ValueError):
            backup.root_restore_command(old)


@unittest.skipUnless(shutil.which(rootops.KWRITECONFIG6) and shutil.which("kreadconfig6"), "KDE config tools missing")
class RootScriptExecutionTests(unittest.TestCase):
    """The root scripts really work: run them as you, with the system paths moved to a temp folder."""

    def run_script(self, script: str, tmp: Path, args: list[str], stdin: bytes = b"") -> subprocess.CompletedProcess:
        for real, fake in (
            ("/etc/plasmalogin.conf", f"{tmp}/plasmalogin.conf"),
            ("/usr/share/sddm/themes/breeze/theme.conf.user", f"{tmp}/theme.conf.user"),
            ("/usr/share/wallpapers/plasmify-login", f"{tmp}/plasmify-login"),
        ):
            script = script.replace(real, fake)
        env = dict(os.environ, HOME=str(tmp), XDG_CONFIG_HOME=str(tmp))
        return subprocess.run(  # noqa: S603 (the scripts under test)
            ["/bin/sh", "-c", script, "plasmify-test", *args], input=stdin, capture_output=True, env=env, check=False
        )

    def read(self, tmp: Path, *groups_and_key: str) -> str:
        *groups, key = groups_and_key
        args = ["kreadconfig6", "--file", f"{tmp}/plasmalogin.conf"]
        for group in groups:
            args += ["--group", group]
        return subprocess.run(args + ["--key", key], capture_output=True, text=True, check=False).stdout.strip()

    def test_login_then_restore(self):
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            (tmp / "plasmalogin.conf").write_text("[Autologin]\nSession=plasma\n")
            done = self.run_script(rootops.LOGIN_SCRIPT, tmp, ["background.png", "plasmalogin"], PNG)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual((tmp / "plasmify-login" / "background.png").read_bytes(), PNG)
            self.assertEqual(self.read(tmp, "Greeter", "WallpaperPluginId"), "org.kde.image")
            self.assertTrue(self.read(tmp, "Greeter", "Wallpaper", "org.kde.image", "General", "Image").endswith(
                "plasmify-login/background.png"))  # fmt: skip
            command = rootops.restore_command("plasmalogin", {"WallpaperPluginId": None, "Image": None})
            script, args = command[3], command[5:]
            undone = self.run_script(script, tmp, args)
            self.assertEqual(undone.returncode, 0, undone.stderr)
            self.assertEqual(self.read(tmp, "Greeter", "WallpaperPluginId"), "")
            self.assertIn("Session=plasma", (tmp / "plasmalogin.conf").read_text())
            self.assertFalse((tmp / "plasmify-login").exists())

    def test_login_script_refuses_other_names(self):
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            for args in (["../../evil", "plasmalogin"], ["background.png", "gdm"]):
                with self.subTest(args=args):
                    self.assertNotEqual(self.run_script(rootops.LOGIN_SCRIPT, tmp, args, PNG).returncode, 0)


if __name__ == "__main__":
    unittest.main()
