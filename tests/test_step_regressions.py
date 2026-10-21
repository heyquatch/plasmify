"""Regression tests for steps, capture and restore: splash text that stays plain text, Shimmer
copies in saved desktops, slideshows when a render fails, glass themes and restored styles.

Every test runs in a temporary folder with mocked Plasma calls; nothing touches the desktop.
"""

import copy
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from plasmify import backup, capture, engine, glass, presets, schema, system  # noqa: E402
from plasmify.presets import Preset  # noqa: E402
from plasmify.steps import panelstyle as ps  # noqa: E402
from plasmify.steps import wallpaper  # noqa: E402
from plasmify.steps.base import Context  # noqa: E402
from plasmify.system import Runner  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def own_wallpapers(tmp: str, names: list[str], **settings) -> Preset:
    """A preset with its own (bundled) wallpapers, so nothing is downloaded."""
    assets = Path(tmp, "assets")
    assets.mkdir(exist_ok=True)
    for number, name in enumerate(names):
        (assets / name).write_bytes(PNG + bytes([number]))
    d = copy.deepcopy(presets.find("nord-minimal").data)
    d["meta"]["id"] = "walls"
    d["wallpaper"] = {"images": [{"file": name, "credit": "me"} for name in names], **settings}
    if schema.validate(d):
        raise ValueError(schema.validate(d))
    return Preset(d, "user", asset_override=assets)


class ShimmerFallbackTests(unittest.TestCase):
    """A picture Shimmer cannot bring to life stays in the slideshow, still, in its place."""

    def apply(self, tmp: str, preset: Preset, render, available=lambda: True) -> tuple[Path, list, list, list]:
        folder = Path(tmp, "walls")
        scripts, writes, warnings = [], [], []
        runner = Runner()
        runner.plasma_script = lambda script, **k: scripts.append(script) or ""
        runner.kwrite = lambda *a, **k: writes.append(a)
        fake = types.SimpleNamespace(available=available, render=render, VERSION=1)
        ctx = Context(preset, runner, Path(tmp), log=lambda _: None)
        ctx.warn = warnings.append
        with (
            mock.patch.object(wallpaper, "wallpaper_dir", return_value=folder),
            mock.patch.dict(sys.modules, {"plasmify.shimmer": fake}),
            mock.patch("plasmify.shimmer", fake, create=True),
        ):
            wallpaper.WallpaperStep().apply(ctx)
        return folder, scripts, warnings, writes

    @staticmethod
    def render_all_but_first(source, target, colour, **kwargs):
        if source.name.startswith("01-"):
            raise ValueError("Unable to read image data")
        target.write_bytes(b"RIFF....WEBP")
        return target

    def test_a_failed_render_keeps_its_place_in_the_slideshow(self):
        with tempfile.TemporaryDirectory() as tmp:
            preset = own_wallpapers(tmp, ["one.png", "two.png", "three.png"], shimmer="snow")
            folder, scripts, warnings, _writes = self.apply(tmp, preset, self.render_all_but_first)
            living = sorted(p.name for p in (folder / wallpaper.LIVING).iterdir())
            self.assertEqual([n[:3] for n in living], ["01-", "02-", "03-"])
            self.assertEqual(living[0], "01-one.png")  # the still itself
            self.assertTrue(living[1].endswith(".webp") and living[2].endswith(".webp"))
            self.assertEqual((folder / wallpaper.LIVING / "01-one.png").read_bytes(), PNG + bytes([0]))
            self.assertIn(str(folder / wallpaper.LIVING), scripts[0])
            self.assertTrue(any("01-one.png stays still" in w for w in warnings))

    def test_single_mode_still_shows_the_first_picture(self):
        with tempfile.TemporaryDirectory() as tmp:
            preset = own_wallpapers(tmp, ["one.png", "two.png"], shimmer="snow", mode="single")
            folder, scripts, _warnings, writes = self.apply(tmp, preset, self.render_all_but_first)
            self.assertIn(json_uri(folder / wallpaper.LIVING / "01-one.png"), scripts[0])
            lock = [w for w in writes if w[0] == "kscreenlockerrc"]
            self.assertTrue(all(w[-1] == (folder / "01-one.png").as_uri() for w in lock))

    def test_every_picture_stays_still_when_even_the_copy_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            preset = own_wallpapers(tmp, ["one.png", "two.png"], shimmer="snow")
            with mock.patch.object(wallpaper, "_place", side_effect=OSError(28, "No space left on device")):
                folder, scripts, _warnings, _writes = self.apply(tmp, preset, self.render_all_but_first)
            self.assertNotIn(wallpaper.LIVING, scripts[0])
            self.assertIn(str(folder), scripts[0])
            self.assertFalse((folder / wallpaper.LIVING).exists())

    def test_old_living_copies_go_when_shimmer_cannot_run(self):
        def render(source, target, colour, **kwargs):
            target.write_bytes(b"RIFF....WEBP")
            return target

        with tempfile.TemporaryDirectory() as tmp:
            preset = own_wallpapers(tmp, ["one.png", "two.png"], shimmer="snow")
            folder, _scripts, _warnings, _writes = self.apply(tmp, preset, render)
            self.assertEqual(len(list((folder / wallpaper.LIVING).iterdir())), 2)
            # Later Qt cannot write WebP any more: the stills play, without the old animations
            # (the slideshow also plays subfolders).
            folder, scripts, warnings, _writes = self.apply(tmp, preset, render, available=lambda: False)
            self.assertTrue(any("stay still" in w for w in warnings))
            self.assertNotIn(wallpaper.LIVING, scripts[0])
            self.assertFalse((folder / wallpaper.LIVING).exists())


def json_uri(path: Path) -> str:
    return '"' + path.as_uri() + '"'


class CaptureShimmerTests(unittest.TestCase):
    """Saving a desktop that shows Shimmer copies shares the pictures behind them, never the copies."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = presets.find("nord-minimal")
        self.images = self.base.section("wallpaper")["images"]
        self.folder = Path(self.tmp.name, "walls")
        (self.folder / wallpaper.LIVING).mkdir(parents=True)
        self.stills = []
        for index, image in enumerate(self.images, start=1):
            still = self.folder / wallpaper.image_name(index, image)
            still.write_bytes(PNG)
            self.stills.append(still)

    def read(self, shown: str, mode: str = "slideshow") -> tuple[dict | None, dict, list[str]]:
        if mode == "slideshow":
            groups = {
                "[Containments][1]": {"plugin": "org.kde.plasma.folder", "wallpaperplugin": "org.kde.slideshow"},
                "[Containments][1][Wallpaper][org.kde.slideshow][General]": {"SlidePaths": shown},
            }
        else:
            groups = {
                "[Containments][1]": {"plugin": "org.kde.plasma.folder", "wallpaperplugin": "org.kde.image"},
                "[Containments][1][Wallpaper][org.kde.image][General]": {"Image": shown},
            }
        notes: list[str] = []
        other = Path(self.tmp.name, "none")
        with mock.patch.object(capture, "wallpaper_dir", lambda p: self.folder if p.id == self.base.id else other):
            section, files = capture.read_wallpapers(groups, notes)
        return section, files, notes

    def living(self, name: str) -> Path:
        path = self.folder / wallpaper.LIVING / name
        path.write_bytes(b"RIFF....WEBP")
        return path

    def test_living_slideshow_is_shared_by_address(self):
        self.living(f"{self.stills[0].stem}-0123456789.webp")  # brought to life
        for still in self.stills[1:]:
            self.living(still.name)  # past the first ones: the still placed as it is
        section, files, notes = self.read(str(self.folder / wallpaper.LIVING))
        self.assertEqual(files, {})
        self.assertEqual([i.get("url") for i in section["images"]], [i["url"] for i in self.images])
        self.assertFalse([n for n in notes if "your own wallpaper" in n])

    def test_a_single_living_picture_is_shared_by_address(self):
        render = self.living(f"{self.stills[0].stem}-89abcdef01.webp")
        section, files, _notes = self.read(render.as_uri(), mode="single")
        self.assertEqual(files, {})
        self.assertEqual(
            section["images"], [{k: v for k, v in self.images[0].items() if k in ("url", "credit", "license")}]
        )

    def test_an_own_picture_is_bundled_as_the_still(self):
        mine = self.folder / "01-mine.png"
        mine.write_bytes(PNG + b"mine")
        self.living("01-mine-0123456789.webp")
        section, files, _notes = self.read(str(self.folder / wallpaper.LIVING))
        self.assertEqual(list(files.values()), [mine])
        self.assertEqual(section["images"], [{"file": "01-mine.png", "credit": "Shared with this preset"}])

    def test_a_copy_without_its_still_is_left_out(self):
        self.living("07-gone-0123456789.webp")
        section, files, notes = self.read(str(self.folder / wallpaper.LIVING))
        self.assertIsNone(section)
        self.assertEqual(files, {})
        self.assertTrue(any("No wallpaper image file" in n for n in notes))

    def test_a_folder_of_the_users_named_living_is_their_own(self):
        own = Path(self.tmp.name, "Pictures", wallpaper.LIVING)
        own.mkdir(parents=True)
        (own / "beach-0123456789.webp").write_bytes(b"RIFF....WEBP")
        _section, files, _notes = self.read(str(own))
        self.assertEqual(list(files.values()), [own / "beach-0123456789.webp"])


class CaptureGlassTests(unittest.TestCase):
    """Only Plasmify's own glass themes (with its marker) are saved as glass."""

    class FakeRunner:
        dry_run = True

        def __init__(self, blur_strength: str = ""):
            self.blur_strength = blur_strength

        def check_config_reader(self):
            pass

        def kread(self, file, group, key, default=""):
            if (file, group, key) == ("kwinrc", "Effect-blur", "BlurStrength"):
                return self.blur_strength or default
            return "kvantum" if (file, group, key) == ("kdeglobals", "KDE", "widgetStyle") else default

    def capture_theme(self, theme: str, kvconfig: str, marker: bool, blur_strength: str = ""):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)
            folder = config / "Kvantum" / theme
            folder.mkdir(parents=True)
            (config / "Kvantum" / "kvantum.kvconfig").write_text(f"[General]\ntheme={theme}\n")
            (folder / f"{theme}.kvconfig").write_text(kvconfig)
            if marker:
                (folder / "made-by-plasmify.txt").write_text("x")
            with (
                mock.patch.object(capture, "CONFIG_HOME", config),
                mock.patch.object(capture, "current_panels", return_value=[]),
            ):
                return capture.capture("Mine", "", self.FakeRunner(blur_strength), include_wallpapers=False)

    def test_an_own_theme_ending_in_glass_is_not_glass(self):
        result = self.capture_theme("Utterly-Sweet-glass", "[%General]\ntranslucent_windows=false\n", marker=False)
        self.assertNotIn("glass", result.data.get("style", {}))
        self.assertTrue(any("'Utterly-Sweet-glass'" in n for n in result.notes))

    def test_plasmify_glass_is_still_recognised(self):
        # Subtle glass made only inactive windows see-through (a negative value): frosted now, which
        # keeps the window in use solid too.
        result = self.capture_theme("LayanDark-glass", "[%General]\nreduce_window_opacity=-20\n", marker=True)
        self.assertEqual(result.data["style"]["glass"], "frosted")
        self.assertEqual(result.data["style"]["transparency"], 20)
        self.assertNotIn("focused_solid", result.data["style"])
        self.assertEqual(result.data["style"]["kvantum"]["name"], "LayanDark")

    def test_the_look_and_transparency_come_back(self):
        # The captured palette falls back to Breeze's dark colours here, so dark defaults apply.
        for theme, level, transparency, focused, strength, style in (
            ("LayanDark-glass", "glass", None, None, "6", {"glass": "glass"}),
            ("LayanDark-glass", "frosted", 55, None, "12", {"glass": "frosted", "transparency": 55}),
            ("plasmify-glass", "clear", 60, None, "", {"glass": "clear", "transparency": 60}),
            ("plasmify-glass", "glass", 40, None, "", {"glass": "glass"}),  # the default is left out
            ("plasmify-glass", "frosted", 12, None, "11", {"glass": "frosted", "transparency": 12}),
            ("plasmify-glass", "clear", None, False, "", {"glass": "clear", "focused_solid": False}),
            ("LayanDark-glass", "glass", None, False, "3", {"glass": "glass", "focused_solid": False}),
        ):
            with self.subTest(theme=theme, level=level, transparency=transparency, focused=focused):
                kvconfig = glass.glass_kvconfig("[%General]\ncomment=x\n", level, True, transparency, focused)
                result = self.capture_theme(theme, kvconfig, marker=True, blur_strength=strength)
                found = {k: v for k, v in result.data["style"].items() if k != "kvantum"}
                self.assertEqual(found, style)
                self.assertEqual("kvantum" in result.data["style"], theme == "LayanDark-glass")
                self.assertEqual(schema.validate(result.data), [])

    def test_a_light_palette_leaves_out_the_light_default(self):
        light = {"Colors:Window": {"BackgroundNormal": "250,250,250", "ForegroundNormal": "40,40,40"}}

        class LightRunner(self.FakeRunner):
            def kread(self, file, group, key, default=""):
                if file == "kdeglobals" and group in light:
                    return light[group].get(key, default)
                return super().kread(file, group, key, default)

        kvconfig = glass.glass_kvconfig("[%General]\n", "glass", False)
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)
            folder = config / "Kvantum" / "plasmify-glass"
            folder.mkdir(parents=True)
            (config / "Kvantum" / "kvantum.kvconfig").write_text("[General]\ntheme=plasmify-glass\n")
            (folder / "plasmify-glass.kvconfig").write_text(kvconfig)
            (folder / glass.MARKER).write_text("x")
            with (
                mock.patch.object(capture, "CONFIG_HOME", config),
                mock.patch.object(capture, "current_panels", return_value=[]),
            ):
                result = capture.capture("Mine", "", LightRunner("5"), include_wallpapers=False)
        self.assertEqual(result.data["style"], {"glass": "glass"})  # 30% is the light default


class CapturePanelStyleTests(unittest.TestCase):
    """A panel style Plasmify made is saved with the desktop, with the user's tweaks; without it the
    saved preset would give the panels the user's own Plasma style. A style of their own is not
    part of a preset (others keep theirs)."""

    class StyleRunner(CaptureGlassTests.FakeRunner):
        def __init__(self, style: str):
            super().__init__()
            self.style = style

        def kread(self, file, group, key, default=""):
            return self.style if (file, group, key) == ps.THEME_KEY else default

    class QuietRunner(Runner):
        """Writes nothing to the desktop's config; the step's own files go to THEME_ROOT."""

        def __init__(self):
            super().__init__(dry_run=False)

        def run(self, args, **_kwargs):
            self.log.append(" ".join(args))
            return ""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for patch in (
            mock.patch.object(capture, "CONFIG_HOME", self.root / "config"),
            mock.patch.object(capture, "current_panels", return_value=[]),
            mock.patch.object(ps, "THEME_ROOT", self.root / "desktoptheme"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def capture_with(self, style: str) -> capture.Capture:
        return capture.capture("Mine", "", self.StyleRunner(style), include_wallpapers=False)

    def test_the_values_the_style_was_made_with_are_saved(self):
        synthwave = presets.find("synthwave-neon")
        tweaked = presets.customized(synthwave, {"panel_style": {"radius": 4, "colour": "accent"}})
        ps.PanelStyleStep().apply(Context(tweaked, self.QuietRunner(), self.root, log=lambda _: None))
        result = self.capture_with(ps.theme_id(synthwave))
        self.assertEqual(
            result.data["panel_style"], {**synthwave.section("panel_style"), "radius": 4, "colour": "accent"}
        )
        self.assertEqual(schema.validate(result.data), [])

    def test_a_style_made_before_plasmify_kept_its_values_is_the_presets(self):
        synthwave = presets.find("synthwave-neon")
        result = self.capture_with(ps.theme_id(synthwave))
        self.assertEqual(result.data["panel_style"], synthwave.section("panel_style"))

    def test_the_users_own_style_is_left_out(self):
        for style in ("breeze-dark", ""):
            with self.subTest(style=style):
                result = self.capture_with(style)
                self.assertNotIn("panel_style", result.data)
                self.assertFalse([n for n in result.notes if "panel style" in n])

    def test_a_style_that_cannot_be_read_back_is_left_out_with_a_note(self):
        folder = self.root / "desktoptheme" / "plasmify-gone"
        folder.mkdir(parents=True)
        (folder / "metadata.json").write_text(ps.metadata(presets.find("nord-minimal"), {"radius": 99}))
        for style in ("plasmify-gone", "plasmify-never-made"):
            with self.subTest(style=style):
                result = self.capture_with(style)
                self.assertNotIn("panel_style", result.data)
                self.assertTrue([n for n in result.notes if "panel style" in n], result.notes)


class RestoreStyleTests(unittest.TestCase):
    """Restore tells open apps the widget style changed back, as apply does."""

    def restore(self, names: list[str]) -> list[str]:
        runner = Runner(dry_run=True)
        part = backup.Backup("x", Path("/nonexistent"), 0, "x", [{"path": f"/home/u/.config/{n}"} for n in names])
        with (
            mock.patch.object(runner, "kread", return_value=""),
            mock.patch.object(engine, "restore_points", return_value=[part]),
            mock.patch.object(system, "which", lambda name: f"/usr/bin/{name}" if name == "dbus-send" else None),
        ):
            engine.restore(part, runner, log=lambda _: None)
        return [line for line in runner.log if "KGlobalSettings.notifyChange" in line]

    def test_restored_kdeglobals_reaches_running_apps(self):
        """Back to a stock setup, which names no colour scheme: there is none to apply, so open
        apps are told about the colours directly (Plasmify stayed dark under light menu bars)."""
        sent = self.restore(["kdeglobals", "kvantum.kvconfig"])
        self.assertEqual(len(sent), 2)
        self.assertIn("int32:0 int32:0", sent[0])  # the colours
        self.assertIn("int32:2 int32:0", sent[1])  # the widget style

    def test_an_applied_scheme_tells_apps_itself(self):
        runner = Runner(dry_run=True)
        part = backup.Backup("x", Path("/nonexistent"), 0, "x", [{"path": "/home/u/.config/kdeglobals"}])
        with (
            mock.patch.object(
                runner, "kread", side_effect=lambda f, g, k, d="": "PlasmifyNordMinimal" if k == "ColorScheme" else ""
            ),
            mock.patch.object(engine, "restore_points", return_value=[part]),
            mock.patch.object(engine, "scheme_available", return_value=True),
            mock.patch.object(system, "which", lambda name: f"/usr/bin/{name}" if name == "dbus-send" else None),
        ):
            engine.restore(part, runner, log=lambda _: None)
        self.assertIn("plasma-apply-colorscheme PlasmifyNordMinimal", runner.log)  # it sends PaletteChanged
        self.assertFalse([line for line in runner.log if "notifyChange int32:0" in line])

    def test_nothing_is_sent_without_kdeglobals(self):
        self.assertEqual(self.restore(["kscreenlockerrc"]), [])


if __name__ == "__main__":
    unittest.main()
