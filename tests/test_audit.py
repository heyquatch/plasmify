"""Regression tests for the defects a multi-agent audit found and reproduced (October 2026).

One test per defect, named after what used to go wrong.
"""

import copy
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from plasmify import backup, bundle, capture, compat, engine, presets, schema  # noqa: E402
from plasmify.presets import Preset  # noqa: E402
from plasmify.steps.base import Context  # noqa: E402
from plasmify.system import Runner  # noqa: E402

try:
    from PyQt6.QtCore import QThread
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def data(preset_id: str = "nord-minimal") -> dict:
    return copy.deepcopy(presets.find(preset_id).data)


def bare_preset(**sections) -> Preset:
    """A valid preset with only a palette (no cursor, wallpaper, icons...)."""
    base = {"schema": 1, "meta": {"id": "bare", "name": "Bare", "description": "x"}}
    base["palette"] = data()["palette"]
    base.update(sections)
    if schema.validate(base):
        raise ValueError(schema.validate(base))
    return Preset(base, "user")


class EngineAndBackupTests(unittest.TestCase):
    def test_symlinked_config_files_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            real = Path(tmp, "dotfiles/kdeglobals")
            real.parent.mkdir()
            real.write_text("old")
            link = Path(tmp, "kdeglobals")
            link.symlink_to(real)
            snap = backup.create([link], "test", root=Path(tmp, "backups"))
            link.unlink()
            link.write_text("new")
            backup.restore_files(snap)
            self.assertTrue(link.is_symlink())
            self.assertEqual(link.read_text(), "old")

    def test_pinned_backup_keeps_originals_after_pruning(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp, "backups")
            a, b = Path(tmp, "a"), Path(tmp, "b")
            a.write_text("a0")
            b.write_text("b0")
            first = backup.create([a], "apply 1", root=root)  # pinned, only holds a
            for index in range(12):  # b is first touched later; then ten more applies prune it
                backup.create([a, b], f"apply {index + 2}", root=root)  # an apply backs up first...
                b.write_text(f"b{index + 1}")  # ...then changes the file
            a.write_text("a-new")
            pinned = next(x for x in backup.list_backups(root) if x.pinned)
            self.assertEqual(pinned.id, first.id)
            for part in engine.restore_points(pinned, backup.list_backups(root)):
                backup.restore_files(part)
            self.assertEqual((a.read_text(), b.read_text()), ("a0", "b0"))

    def test_generated_files_are_backed_up(self):
        from plasmify.steps.colors import ColorsStep
        from plasmify.steps.icons import IconsStep
        from plasmify.steps.panelstyle import PanelStyleStep
        from plasmify.steps.splash import SplashStep
        from plasmify.steps.style import StyleStep

        d = data("tokyo-night-neon")
        d["panel_style"] = {"radius": 8}
        d["splash"] = {"style": "plasmify"}
        preset = Preset(d, "built-in")
        names = {p.name for step in (ColorsStep(), IconsStep(), PanelStyleStep(), SplashStep(), StyleStep())
                 for p in step.touched_paths(preset)}  # fmt: skip
        for expected in ("PlasmifyTokyoNightNeon.colors", "Plasmify-tokyo-night-neon",
                         "plasmify-tokyo-night-neon", "plasmify-splash-tokyo-night-neon"):  # fmt: skip
            self.assertIn(expected, names)
        kvantum = presets.find("tokyo-night-neon").section("style").get("kvantum")
        if kvantum:
            self.assertIn(kvantum["name"], names)  # a user's theme of that name comes back

    def test_splash_and_login_run_after_the_wallpaper(self):
        self.assertLess(engine.LATE_STEPS.index("wallpaper"), engine.LATE_STEPS.index("splash"))
        self.assertLess(engine.LATE_STEPS.index("wallpaper"), engine.LATE_STEPS.index("login"))

    def test_restore_never_strands_on_breeze_dark(self):
        runner = Runner(dry_run=True)
        snap = backup.Backup("x", Path("/nonexistent"), 0, "x", [])
        with (
            mock.patch.object(
                runner, "kread", side_effect=lambda f, g, k, d="": "GoneScheme" if k == "ColorScheme" else ""
            ),
            mock.patch.object(engine, "restore_points", return_value=[]),
        ):
            engine.restore(snap, runner, log=lambda _: None)
        self.assertFalse([c for c in runner.log if "BreezeDark" in c])

    def test_shimmer_plays_living_copies_and_keeps_the_still_for_the_lock_screen(self):
        import sys
        import types

        from plasmify.steps import wallpaper

        made = []

        def render(source, target, colour, style, **kwargs):
            made.append((source.name, colour, style))
            target.write_bytes(b"RIFF....WEBP")
            return target

        fake = types.SimpleNamespace(available=lambda: True, render=render, VERSION=1)
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp, "assets")
            assets.mkdir()
            (assets / "one.png").write_bytes(PNG + b"1")
            (assets / "two.png").write_bytes(PNG + b"2")
            images = [{"file": "one.png", "credit": "me"}, {"file": "two.png", "credit": "me"}]
            d = bare_preset(wallpaper={"images": images, "shimmer": "embers", "shimmer_colour": "red"}).data
            preset = Preset(d, "user", asset_override=assets)
            folder = Path(tmp, "walls")
            scripts, writes = [], []
            runner = Runner()
            runner.plasma_script = lambda script, **k: scripts.append(script) or ""
            runner.kwrite = lambda *a, **k: writes.append(a)
            with (
                mock.patch.object(wallpaper, "wallpaper_dir", return_value=folder),
                mock.patch.dict(sys.modules, {"plasmify.shimmer": fake}),
                mock.patch("plasmify.shimmer", fake, create=True),
            ):
                wallpaper.WallpaperStep().apply(Context(preset, runner, Path(tmp), log=lambda _: None))
                living = sorted(p.name for p in (folder / wallpaper.LIVING).iterdir())
                self.assertEqual(len(living), 2)
                self.assertTrue(all(n.startswith(("01-", "02-")) and n.endswith(".webp") for n in living))
                self.assertEqual({c for _, c, _ in made}, {preset.palette.colors["red"]})
                self.assertIn(str(folder / wallpaper.LIVING), scripts[0])
                self.assertIn('"ForceImageAnimation", false', scripts[0])
                lock = [w for w in writes if w[0] == "kscreenlockerrc"]
                self.assertTrue(all("living" not in str(w[-1]) for w in lock))  # a still for the lock screen
                # Applied again: nothing is made twice. Switched off: the living copies go.
                wallpaper.WallpaperStep().apply(Context(preset, runner, Path(tmp), log=lambda _: None))
                self.assertEqual(len(made), 2)
                # Another light makes other frames: new files, and the old ones go.
                d["wallpaper"]["shimmer_light"] = "rays"
                wallpaper.WallpaperStep().apply(
                    Context(Preset(d, "user", asset_override=assets), runner, Path(tmp), log=lambda _: None)
                )
                self.assertEqual(len(made), 4)
                self.assertEqual(len(list((folder / wallpaper.LIVING).iterdir())), 2)
                d["wallpaper"]["shimmer"] = "none"
                wallpaper.WallpaperStep().apply(
                    Context(Preset(d, "user", asset_override=assets), runner, Path(tmp), log=lambda _: None)
                )
                self.assertFalse((folder / wallpaper.LIVING).exists())

    def test_long_slideshow_keeps_every_picture_with_shimmer(self):
        import sys
        import types

        from plasmify.steps import wallpaper

        made = []

        def render(source, target, colour, style, **kwargs):
            made.append(source.name)
            target.write_bytes(b"RIFF....WEBP")
            return target

        fake = types.SimpleNamespace(available=lambda: True, render=render, VERSION=1)
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp, "assets")
            assets.mkdir()
            names = ["one.png", "two.png", "three.png"]
            for number, name in enumerate(names):
                (assets / name).write_bytes(PNG + bytes([number]))
            images = [{"file": name, "credit": "me"} for name in names]
            d = bare_preset(wallpaper={"images": images, "shimmer": "snow"}).data
            preset = Preset(d, "user", asset_override=assets)
            folder = Path(tmp, "walls")
            runner = Runner()
            runner.plasma_script = lambda script, **k: ""
            runner.kwrite = lambda *a, **k: None
            with (
                mock.patch.object(wallpaper, "wallpaper_dir", return_value=folder),
                mock.patch.object(wallpaper, "SHIMMER_MAX", 1),
                mock.patch.dict(sys.modules, {"plasmify.shimmer": fake}),
                mock.patch("plasmify.shimmer", fake, create=True),
            ):
                wallpaper.WallpaperStep().apply(Context(preset, runner, Path(tmp), log=lambda _: None))
            living = sorted(p.name for p in (folder / wallpaper.LIVING).iterdir())
            self.assertEqual(made, ["01-one.png"])  # only the first is brought to life
            self.assertEqual([n[:3] for n in living], ["01-", "02-", "03-"])  # every picture still plays
            self.assertEqual(living[1:], ["02-two.png", "03-three.png"])
            self.assertEqual((folder / wallpaper.LIVING / "02-two.png").read_bytes(), PNG + bytes([1]))

    def test_shimmer_settings_reach_the_renderer(self):
        from plasmify.steps import wallpaper

        self.assertEqual(
            wallpaper.shimmer_options({"shimmer": "calm"}),
            {"style": "none", "light": None, "motion": None, "density": "normal"},
        )
        chosen = {"shimmer": "magic", "shimmer_light": "flare", "shimmer_motion": "still", "shimmer_density": "high"}
        self.assertEqual(
            wallpaper.shimmer_options(chosen),
            {"style": "magic", "light": "flare", "motion": "still", "density": "high"},
        )

    def test_shimmer_frame_rate_is_12_or_24(self):
        images = [{"url": "https://github.com/a/b/raw/main/one.png", "credit": "me"}]
        for fps in (12, 24):
            data = bare_preset(wallpaper={"images": images, "shimmer": "snow", "shimmer_fps": fps}).data
            self.assertFalse(schema.validate(data), fps)
        for bad in (30, 12.0, True, "24"):
            with self.assertRaisesRegex(ValueError, "shimmer_fps must be 12 or 24"):
                bare_preset(wallpaper={"images": images, "shimmer": "snow", "shimmer_fps": bad})

    def test_shimmer_frame_rate_reaches_the_renderer_and_names_the_file(self):
        import sys
        import types

        from plasmify.steps import wallpaper

        def run(extra: dict) -> tuple[list, list]:
            seen = []

            def render(source, target, colour, style, **kwargs):
                seen.append(kwargs["fps"])
                target.write_bytes(b"RIFF....WEBP")
                return target

            fake = types.SimpleNamespace(available=lambda: True, render=render, VERSION=1)
            with tempfile.TemporaryDirectory() as tmp:
                assets = Path(tmp, "assets")
                assets.mkdir()
                (assets / "one.png").write_bytes(PNG)
                images = [{"file": "one.png", "credit": "me"}]
                d = bare_preset(wallpaper={"images": images, "shimmer": "snow", **extra}).data
                preset = Preset(d, "user", asset_override=assets)
                folder = Path(tmp, "walls")
                runner = Runner()
                runner.plasma_script = lambda script, **k: ""
                runner.kwrite = lambda *a, **k: None
                with (
                    mock.patch.object(wallpaper, "wallpaper_dir", return_value=folder),
                    mock.patch.dict(sys.modules, {"plasmify.shimmer": fake}),
                    mock.patch("plasmify.shimmer", fake, create=True),
                ):
                    wallpaper.WallpaperStep().apply(Context(preset, runner, Path(tmp), log=lambda _: None))
                return seen, sorted(p.name for p in (folder / wallpaper.LIVING).iterdir())

        default_fps, default_names = run({})
        smooth_fps, smooth_names = run({"shimmer_fps": 24})
        self.assertEqual(default_fps, [12])
        self.assertEqual(smooth_fps, [24])
        self.assertNotEqual(default_names, smooth_names)  # another frame rate makes new files

    def test_schema_knows_every_shimmer_choice(self):
        try:
            from plasmify import shimmer
        except ImportError:
            self.skipTest("PyQt6 is not installed")
        named = {"calm" if s == "none" else s for s in shimmer.STYLES}
        self.assertEqual(set(schema.SHIMMER_STYLES) - {"none"}, named)
        self.assertEqual(set(schema.SHIMMER_LIGHTS) - {"auto"}, set(shimmer.LIGHTS))
        self.assertEqual(set(schema.SHIMMER_MOTIONS) - {"auto"}, set(shimmer.MOTIONS))
        self.assertEqual(set(schema.SHIMMER_DENSITIES), set(shimmer.DENSITIES))

    def test_wallpaper_folder_only_holds_current_pictures(self):
        from plasmify.steps import wallpaper

        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp, "assets")
            assets.mkdir()
            (assets / "new.png").write_bytes(PNG + b"new")
            d = bare_preset(wallpaper={"images": [{"file": "new.png", "credit": "me"}]}).data
            preset = Preset(d, "user", asset_override=assets)
            folder = Path(tmp, "walls")
            folder.mkdir()
            (folder / "old.png").write_bytes(PNG)
            (folder / "new.png").write_bytes(PNG + b"stale")
            runner = Runner()
            runner.plasma_script = lambda *a, **k: ""
            runner.kwrite = lambda *a, **k: None
            with mock.patch.object(wallpaper, "wallpaper_dir", return_value=folder):
                wallpaper.WallpaperStep().apply(Context(preset, runner, Path(tmp), log=lambda _: None))
            self.assertFalse((folder / "old.png").exists())
            self.assertFalse((folder / "new.png").exists())  # now named after its place: 01-new.png
            self.assertEqual((folder / "01-new.png").read_bytes(), PNG + b"new")

    def test_style_change_reaches_running_apps(self):
        """A light/dark switch moves Kvantum apps to Breeze: running apps must hear about it,
        or they keep the old style (and its dark colours) until they restart."""
        from unittest import mock

        from plasmify import system
        from plasmify.steps.style import StyleStep

        d = data()
        d.pop("style", None)
        runner = Runner(dry_run=True)
        with mock.patch.object(system, "which", lambda name: f"/usr/bin/{name}" if name == "dbus-send" else None):
            StyleStep().apply(Context(Preset(d, "built-in"), runner, Path(tempfile.gettempdir()), log=lambda _: None))
        sent = [line for line in runner.log if line.startswith("dbus-send")]
        self.assertEqual(len(sent), 1)
        self.assertIn("org.kde.KGlobalSettings.notifyChange int32:2 int32:0", sent[0])

    def test_panel_style_default_radius_matches_the_tab(self):
        from plasmify.steps.panelstyle import PanelStyleStep

        d = data()
        d["panel_style"] = {"colour": "accent"}
        logs = []
        PanelStyleStep().apply(
            Context(Preset(d, "built-in"), Runner(dry_run=True), Path(tempfile.gettempdir()), log=logs.append)
        )
        self.assertIn("radius 6px", " ".join(logs))


class GeneratorTests(unittest.TestCase):
    def test_capture_wallpaper_names_are_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "walls")
            folder.mkdir()
            (folder / "my..picture.v2.png").write_bytes(PNG)
            (folder / "notes.txt").write_text("not a picture")
            groups = {
                "[Containments][1]": {"plugin": "org.kde.plasma.folder", "wallpaperplugin": "org.kde.slideshow"},
                "[Containments][1][Wallpaper][org.kde.slideshow][General]": {"SlidePaths": str(folder)},
            }
            notes: list[str] = []
            wallpaper, files = capture.read_wallpapers(groups, notes)
            self.assertEqual(len(files), 1)
            for name in files:
                self.assertIsNone(schema.IMAGE_FILE(name), name)

    def test_compat_sees_picture_logos_without_a_logo_key(self):
        d = data("catppuccin-anime")
        d["shell"]["greeting"] = False
        self.assertIn("logo-hidden", {a.id for a in compat.check(Preset(d, "built-in"))})

    def test_missing_fastfetch_is_not_a_crash(self):
        from plasmify.steps import shell

        with mock.patch.object(shell.subprocess, "run", side_effect=FileNotFoundError("fastfetch")):
            self.assertEqual(shell.builtin_logos(), set())


class BundleTests(unittest.TestCase):
    def test_damaged_bundle_is_a_clear_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "broken.plasmify")
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("preset.toml", "x" * 5000)
            raw = bytearray(path.read_bytes())
            raw[60:120] = b"\x00" * 60  # corrupt the compressed data
            path.write_bytes(bytes(raw))
            with self.assertRaises(presets.PresetError):
                bundle.read_bundle(path)

    def test_saving_a_user_preset_under_its_own_name_keeps_its_wallpapers(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(bundle, "USER_PRESET_DIR", Path(tmp)):
            d = bare_preset(wallpaper={"images": [{"file": "w.png", "credit": "me"}]}).data
            d["meta"]["id"] = "mine"
            source = Path(tmp, "w.png")
            source.write_bytes(PNG)
            first = bundle.save_user_preset(d, {"w.png": source})
            again = bundle.save_user_preset(d, {"w.png": first.asset_dir / "w.png"}, replace=True)
            self.assertEqual((again.asset_dir / "w.png").read_bytes(), PNG)


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        from plasmify.gui import customize, main_window

        self.main = main_window
        self.patches = [
            mock.patch.object(main_window, "TWEAKS_FILE", Path(self.tmp.name, "tweaks.json")),
            mock.patch.object(customize, "CUSTOM_ROOT", Path(self.tmp.name, "custom")),
        ]
        for patch in self.patches:
            patch.start()
        self.window = main_window.MainWindow()

    def tearDown(self):
        for thread in self.window.findChildren(QThread):
            thread.wait(20000)
        self.window.close()
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def select(self, preset_id: str):
        self.window.gallery.setCurrentRow(next(i for i, p in enumerate(self.window.presets) if p.id == preset_id))
        return self.window.customize

    def test_saved_panel_style_keeps_its_step_on(self):
        self.main.remember_tweaks("tokyo-night-neon", {"panel_style": {"colour": "accent"}}, None)
        self.window.reload_presets(select="tokyo-night-neon")
        box = self.window.checkboxes["panelstyle"]
        self.assertTrue(box.isEnabled() and box.isChecked())
        self.assertIn("panelstyle", self.window.selected_steps())

    def test_unapplied_edits_survive_browsing(self):
        custom = self.select("tokyo-night-neon")
        custom.radius.setValue(9)
        self.select("nord-minimal")
        self.assertEqual(self.select("tokyo-night-neon").overrides().get("panel_style", {}).get("radius"), 9)

    def test_partial_sections_stay_valid(self):
        custom = self.window.customize
        bare = bare_preset()
        custom.load(bare)
        custom.cursor_size.setValue(40)
        custom.interval.setCurrentIndex(1)
        custom.folders.setCurrentIndex(2)
        overrides = custom.overrides()
        presets.customized(bare, overrides)  # no "cursor.name is required" / "images is required"
        self.assertEqual(overrides["cursor"]["size"], 40)

    def test_keep_it_beats_the_preset(self):
        custom = self.window.customize
        d = data()
        d["splash"] = {"style": "plasmify"}
        d["effects"]["animation_speed"] = 1.5
        preset = Preset(d, "built-in")
        custom.load(preset)
        custom.splash_style.set_value("keep")
        custom.speed.setCurrentIndex(custom.speed.findData(None))
        effective = presets.customized(preset, custom.overrides())
        self.assertEqual(effective.section("splash"), {})
        self.assertNotIn("animation_speed", effective.section("effects"))

    def test_empty_text_logo_is_not_a_change(self):
        custom = self.select("nord-minimal")
        custom.logo_kind.setCurrentIndex(custom.logo_kind.findData("text"))
        custom.logo_text.setText("   ")
        self.assertNotIn("shell", custom.overrides())

    def test_radius_comes_back_on_thicker_panels(self):
        from plasmify import panels

        custom = self.select("nord-minimal")
        custom.radius.setValue(20)
        thin = [panels.normalise({"edge": "bottom", "thickness": 24, "widgets": ["tasks"]})]
        custom.panel_editor.set_panels(thin)
        custom._sync_enabled()
        self.assertEqual(custom.radius.value(), 10)
        custom.apply_fix("radius-capped")
        self.assertEqual(custom.radius.value(), 20)
        self.assertGreaterEqual(custom.panel_editor.panels()[0]["thickness"], 44)

    def test_custom_folder_is_not_wiped(self):
        custom = self.select("nord-minimal")
        folder = custom.custom_dir()
        keep = folder / "applied-logo.png"
        keep.write_bytes(PNG)
        custom.load(custom.preset)
        custom._custom_dir = None
        custom.custom_dir()
        self.assertTrue(keep.exists())

    def test_cancelled_custom_colour_keeps_the_choice(self):
        from plasmify.gui import customize

        custom = self.select("nord-minimal")
        index = custom.panel_colour.findData("accent")
        custom.panel_colour.setCurrentIndex(index)
        custom._panel_colour_picked(index)
        with mock.patch.object(customize.QColorDialog, "getColor", return_value=QColor()):
            custom.panel_colour.setCurrentIndex(custom.panel_colour.findData("custom"))
            custom._panel_colour_picked(0)
        self.assertEqual(custom.panel_colour.currentData(), "accent")

    def test_unreadable_image_drop_shows_a_message(self):
        from plasmify.gui import customize

        custom = self.select("nord-minimal")
        image = QImage(8, 8, QImage.Format.Format_RGB32)
        path = Path(self.tmp.name, "locked.png")
        image.save(str(path))
        with (
            mock.patch.object(customize.shutil, "copyfile", side_effect=PermissionError(13, "Permission denied")),
            mock.patch.object(customize, "message") as shown,
        ):
            custom._use_file("logo", path)
        shown.assert_called_once()

    def test_one_wallpaper_has_no_slideshow_speed(self):
        custom = self.select("nord-minimal")
        pictures = []
        for index in range(3):
            image = QImage(8, 8, QImage.Format.Format_RGB32)
            path = Path(self.tmp.name, f"w{index}.png")
            image.save(str(path))
            pictures.append(path)
        custom.use_wallpapers(pictures[:1])
        self.assertFalse(custom.interval.isEnabled())
        custom.use_wallpapers(pictures)
        self.assertTrue(custom.interval.isEnabled())
        custom.interval.setCurrentIndex(0)
        self.assertIn("interval", custom.overrides()["wallpaper"])
        custom.use_wallpapers(pictures[:1])
        self.assertNotIn("interval", custom.overrides()["wallpaper"])

    def test_credits_for_own_wallpapers(self):
        from plasmify.gui.dialogs import CreditsDialog

        CreditsDialog(bare_preset(wallpaper={"images": [{"file": "mine.png", "credit": "me"}]}))

    def test_restore_dialog_waits_for_the_restore(self):
        from plasmify.gui.dialogs import RestoreDialog

        dialog = RestoreDialog(self.window)
        dialog.worker = mock.Mock(isRunning=lambda: True)
        dialog.reject()
        self.assertTrue(dialog.isVisible() or dialog.result() == 0)
        self.assertIn("Please wait", dialog.log.toPlainText())
        dialog.worker = None
        dialog.reject()


if __name__ == "__main__":
    unittest.main()
