"""The ordered wallpaper list: what you see in Customize is what the slideshow plays."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from plasmify import presets  # noqa: E402
from plasmify.presets import Preset  # noqa: E402
from plasmify.steps import wallpaper  # noqa: E402
from plasmify.steps.base import Context  # noqa: E402
from plasmify.system import Runner  # noqa: E402

try:
    from PyQt6.QtCore import QThread
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtWidgets import QApplication
except ImportError:  # pragma: no cover
    QApplication = None

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


class StepOrderTests(unittest.TestCase):
    def preset(self, images: list[dict], **extra) -> Preset:
        data = dict(presets.find("nord-minimal").data)
        data["wallpaper"] = {"images": images, **extra}
        return Preset(data, "user")

    def run_step(self, preset: Preset, folder: Path) -> list[str]:
        scripts = []
        runner = Runner()
        runner.plasma_script = lambda script, **_: scripts.append(script) or ""
        runner.kwrite = lambda *a, **k: None
        fetched = []

        def fetch(url, target):
            fetched.append(url)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(PNG + url.encode())

        with (
            mock.patch.object(wallpaper, "wallpaper_dir", return_value=folder),
            mock.patch.object(wallpaper.downloads, "fetch_image", side_effect=fetch),
        ):
            wallpaper.WallpaperStep().apply(Context(preset, runner, folder.parent, log=lambda _: None))
        self.scripts = scripts
        return fetched

    def test_files_are_named_after_their_place(self):
        self.assertEqual(wallpaper.image_name(3, {"file": "01-mine.png"}), "03-mine.png")
        self.assertEqual(wallpaper.image_name(1, {"file": "wall-02-x.jpg"}), "01-wall-02-x.jpg")
        name = wallpaper.image_name(2, {"url": "https://example.org/a/b.png"})
        self.assertRegex(name, r"^02-[0-9a-f]{8}-b\.png$")
        other = wallpaper.image_name(2, {"url": "https://example.org/other/b.png"})
        self.assertNotEqual(name, other)  # two artists' b.png never share a file

    def test_the_slideshow_follows_the_list_unless_shuffled(self):
        folder = Path("walls")
        self.assertIn('"SlideshowMode", 1', wallpaper.desktop_script("slideshow", folder, folder / "a", 600))
        self.assertIn('"SlideshowMode", 0', wallpaper.desktop_script("slideshow", folder, folder / "a", 600, "shuffle"))

    def test_reordering_downloads_nothing_again(self):
        urls = [f"https://images.unsplash.com/photo-{n}.jpg" for n in "abc"]
        images = [{"url": u, "credit": "x"} for u in urls]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "walls")
            self.assertEqual(len(self.run_step(self.preset(images), folder)), 3)
            reordered = [images[2], images[0], images[1]]
            self.assertEqual(self.run_step(self.preset(reordered), folder), [])
            self.assertEqual(self.contents(folder), [urls[2], urls[0], urls[1]])
            self.assertIn('"SlideshowMode", 1', self.scripts[0])
            # Removing one keeps the rest and drops its file.
            self.assertEqual(self.run_step(self.preset(reordered[:2]), folder), [])
            self.assertEqual(self.contents(folder), [urls[2], urls[0]])

    def contents(self, folder: Path) -> list[str]:
        """The source of each picture in the folder, in slideshow (file name) order."""
        return [p.read_bytes()[len(PNG) :].decode() for p in sorted(folder.glob("*.jpg"))]

    def test_pictures_with_the_same_file_name_never_mix(self):
        urls = ["https://github.com/alice/w/wallpaper.jpg", "https://codeberg.org/bob/w/wallpaper.jpg"]
        images = [{"url": u, "credit": u} for u in urls]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "walls")
            self.run_step(self.preset(images), folder)
            self.assertEqual(self.run_step(self.preset(images[::-1]), folder), [])  # reorder: no download
            self.assertEqual(self.contents(folder), urls[::-1])
            self.assertEqual(self.run_step(self.preset(images[1:]), folder), [])  # remove Alice's
            self.assertEqual(self.contents(folder), urls[1:])
            self.assertIn(urls[1], (folder / "CREDITS.txt").read_text())

    def test_old_names_shared_by_two_pictures_are_downloaded_again(self):
        urls = ["https://github.com/alice/w/original.jpg", "https://codeberg.org/bob/w/original.jpg"]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "walls")
            folder.mkdir()
            for index, url in enumerate(urls, start=1):
                (folder / f"0{index}-original.jpg").write_bytes(PNG + url.encode())  # the old naming
            fetched = self.run_step(self.preset([{"url": urls[1], "credit": "Bob"}]), folder)  # Alice removed
            self.assertEqual(fetched, [urls[1]])  # which old file is Bob's is unknown: fetched again
            self.assertEqual(self.contents(folder), [urls[1]])

    def test_splash_and_login_find_older_downloads(self):
        from plasmify.steps import login, splash

        url = "https://images.unsplash.com/photo-a.jpg"
        data = presets.merge(
            presets.find("nord-minimal").data,
            {"wallpaper": {"images": [{"url": url, "credit": "x"}]}, "login": {"background": "wallpaper"}},
        )
        preset = Preset(data, "user")
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "01-photo-a.jpg").write_bytes(PNG)
            warnings = []
            with (
                mock.patch.object(wallpaper, "wallpaper_dir", return_value=folder),
                mock.patch.object(login, "wallpaper_dir", return_value=folder),
                mock.patch.object(splash, "wallpaper_dir", return_value=folder),
            ):
                ctx = Context(preset, Runner(dry_run=True), folder, log=lambda _: None)
                ctx.warn = warnings.append
                self.assertEqual(login.LoginStep()._image(ctx, {"background": "wallpaper"}), folder / "01-photo-a.jpg")
                found = splash._background(preset, {"background": "wallpaper"}, warnings.append)
                self.assertEqual(found, folder / "01-photo-a.jpg")
            self.assertEqual(warnings, [])

    def test_downloads_from_before_the_hash_are_reused(self):
        url = "https://images.unsplash.com/photo-a.jpg"
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "walls")
            folder.mkdir()
            (folder / "01-photo-a.jpg").write_bytes(PNG + url.encode())  # the old naming
            self.assertEqual(self.run_step(self.preset([{"url": url, "credit": "x"}]), folder), [])
            self.assertEqual(self.contents(folder), [url])
            self.assertTrue((folder / "01-photo-a.jpg").exists())  # reused, under its old-style name

    def test_an_old_download_is_never_given_another_addresss_name(self):
        urls = ["https://github.com/alice/w/original.jpg", "https://codeberg.org/bob/w/original.jpg"]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "walls")
            folder.mkdir()
            (folder / "01-original.jpg").write_bytes(PNG + urls[0].encode())  # the old version mixed them up
            self.run_step(self.preset([{"url": urls[1], "credit": "Bob"}]), folder)
            self.assertFalse(list(folder.glob(f"*-{wallpaper.url_base(urls[1])}")))  # not vouched for
            fetched = self.run_step(self.preset([{"url": u, "credit": u} for u in urls]), folder)
            self.assertIn(urls[1], fetched)  # with both back, Bob's picture is fetched for real
            self.assertEqual(self.contents(folder)[1], urls[1])


@unittest.skipIf(QApplication is None, "PyQt6 missing")
class WallpaperListTests(unittest.TestCase):
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
        self.window.gallery.setCurrentRow(next(i for i, p in enumerate(self.window.presets) if p.id == "nord-minimal"))
        self.custom = self.window.customize
        self.pictures = []
        for colour in ("red", "green", "blue"):
            image = QImage(32, 18, QImage.Format.Format_RGB32)
            image.fill(QColor(colour))
            path = Path(self.tmp.name, f"{colour}.png")
            image.save(str(path))
            self.pictures.append(path)

    def tearDown(self):
        for thread in self.window.findChildren(QThread):
            thread.wait(20000)
        self.window.close()
        for patch in self.patches:
            patch.stop()
        self.tmp.cleanup()

    def files(self) -> list[str]:
        return [image.get("file", image.get("url")) for image in self.custom.overrides()["wallpaper"]["images"]]

    def drag(self, source: int, target: int) -> None:
        """What a drop does to the model: move one row, then report it."""
        from PyQt6.QtCore import QModelIndex

        walls = self.custom.walls_list
        walls.model().moveRow(QModelIndex(), source, QModelIndex(), target)
        self.app.processEvents()

    def test_the_preset_list_is_shown_in_order(self):
        preset = self.custom.preset
        self.assertEqual(self.custom.walls_list.count(), len(preset.section("wallpaper")["images"]))
        self.assertNotIn("wallpaper", self.custom.overrides())
        self.assertIn("7 pictures", self.custom.walls_note.text())

    def test_reorder_remove_add_and_back(self):
        custom = self.custom
        custom.use_wallpapers(self.pictures[:2])
        custom.add_wallpapers(self.pictures[2:])
        first = self.files()
        self.assertEqual(len(first), 3)
        self.assertEqual(custom.walls_list.count(), 3)

        self.drag(2, 0)  # the last picture to the front
        self.assertEqual(self.files(), [first[2], first[0], first[1]])
        self.assertIn("1.", custom.walls_list.item(0).toolTip())

        custom.walls_list.item(1).setSelected(True)
        custom.remove_wallpapers()
        self.assertEqual(self.files(), [first[2], first[1]])
        presets.customized(custom.preset, custom.overrides(), custom.asset_dir())  # still valid

        custom.walls_list.selectAll()
        custom.remove_wallpapers()
        self.assertIsNone(custom.overrides()["wallpaper"])  # the current wallpaper stays
        self.assertFalse(custom.interval.isEnabled())
        self.assertIn("current wallpaper stays", custom.walls_note.text())
        presets.customized(custom.preset, custom.overrides(), custom.asset_dir())

        custom.reset_wallpapers()
        self.assertNotIn("wallpaper", custom.overrides())
        self.assertEqual(custom.walls_list.count(), 7)

    def test_removing_one_of_the_preset_pictures(self):
        custom = self.custom
        urls = [image["url"] for image in custom.preset.section("wallpaper")["images"]]
        custom.walls_list.item(0).setSelected(True)
        custom.remove_wallpapers()
        self.assertEqual(self.files(), urls[1:])
        self.drag(0, 3)
        self.assertEqual(self.files(), [urls[2], urls[3], urls[1], *urls[4:]])

    def test_the_same_picture_is_not_copied_twice(self):
        custom = self.custom
        custom.use_wallpapers(self.pictures[:1])
        custom.use_wallpapers(self.pictures[:1])
        self.assertEqual(len(list(custom.asset_dir().glob("wall-*"))), 1)
        custom.add_wallpapers(self.pictures[:1])
        self.assertEqual(len(set(self.files())), 1)

    def test_order_choice_only_for_a_slideshow(self):
        custom = self.custom
        custom.use_wallpapers(self.pictures[:1])
        self.assertFalse(custom.order.isEnabled())
        custom.add_wallpapers(self.pictures[1:])
        self.assertTrue(custom.order.isEnabled())
        custom.order.setCurrentIndex(custom.order.findData("shuffle"))
        self.assertEqual(custom.overrides()["wallpaper"]["order"], "shuffle")
        presets.customized(custom.preset, custom.overrides(), custom.asset_dir())

    def test_added_pictures_are_laid_out_and_clickable(self):
        custom = self.custom
        custom.walls_box.set_open(True)
        self.window.show()
        self.app.processEvents()
        custom.add_wallpapers(self.pictures)
        self.app.processEvents()
        walls = custom.walls_list
        self.assertEqual(walls.count(), 10)
        self.assertTrue(all(walls.visualItemRect(walls.item(i)).isValid() for i in range(walls.count())))

    def test_a_drop_that_moves_nothing_changes_nothing(self):
        self.custom._walls_reordered()
        self.assertNotIn("wallpaper", self.custom.overrides())

    def test_preview_shows_the_first_picture_of_the_list(self):
        from plasmify.gui import main_window

        custom = self.custom
        custom.use_wallpapers(self.pictures)  # red, green, blue
        self.drag(2, 0)  # blue first
        effective = self.window.effective_preset()
        pixmap = main_window.cached_wallpaper(effective)
        self.assertEqual(pixmap.toImage().pixelColor(5, 5).name(), "#0000ff")

    def test_replacing_a_preset_refreshes_its_copied_files(self):
        from plasmify import bundle

        data = dict(presets.find("nord-minimal").data)
        data["meta"] = dict(data["meta"], id="mine", name="Mine")
        data["wallpaper"] = {"images": [{"file": "01-w.png", "credit": "me"}, {"file": "02-w.png", "credit": "me"}]}
        with (
            mock.patch.object(bundle, "USER_PRESET_DIR", Path(self.tmp.name, "presets")),
            mock.patch.object(presets, "USER_PRESET_DIR", Path(self.tmp.name, "presets")),
        ):
            bundle.save_user_preset(data, {"01-w.png": self.pictures[0], "02-w.png": self.pictures[1]})
            self.window.reload_presets(select="mine")
            custom = self.window.customize
            custom.custom_dir()  # seeded with red and green
            with mock.patch.object(self.main, "confirm", return_value=True):
                self.window._store(data, {"01-w.png": self.pictures[2], "02-w.png": self.pictures[2]})
            self.window.reload_presets(select="mine")
            custom = self.window.customize
            self.drag(1, 0)
            paths = [custom._wall_path(e) for e in custom._current_walls()]
            colours = {QImage(str(p)).pixelColor(1, 1).name() for p in paths}
            copies = {QImage(str(p)).pixelColor(1, 1).name() for p in custom.asset_dir().glob("0*-w.png")}
        self.assertEqual(colours, {"#0000ff"})  # the new version's pictures, not the old copies
        self.assertEqual(copies, {"#0000ff"})  # and the folder Apply reads from still has them

    def test_saved_lists_come_back(self):
        custom = self.custom
        custom.use_wallpapers(self.pictures)
        self.drag(0, 3)
        saved, folder = custom.overrides(), custom.asset_dir()
        custom.load(custom.preset, saved, folder)
        self.assertEqual(custom.walls_list.count(), 3)
        self.assertEqual(custom.overrides(), saved)
        custom.walls_list.selectAll()
        custom.remove_wallpapers()
        removed = custom.overrides()
        custom.load(custom.preset, removed, folder)
        self.assertIsNone(custom.overrides()["wallpaper"])
        self.assertEqual(custom.walls_list.count(), 0)


if __name__ == "__main__":
    unittest.main()


class SharingAfterReorderTests(unittest.TestCase):
    def test_reordered_artist_wallpapers_are_referenced_not_copied(self):
        from plasmify import capture

        base = presets.find("nord-minimal")
        images = base.section("wallpaper")["images"]
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "walls")
            folder.mkdir()
            for index, image in enumerate(images[::-1], start=1):  # applied in reverse order
                (folder / wallpaper.image_name(index, image)).write_bytes(PNG)
            groups = {
                "[Containments][1]": {"plugin": "org.kde.plasma.folder", "wallpaperplugin": "org.kde.slideshow"},
                "[Containments][1][Wallpaper][org.kde.slideshow][General]": {"SlidePaths": str(folder)},
            }
            notes: list[str] = []
            with mock.patch.object(capture, "wallpaper_dir", lambda p: folder if p.id == base.id else Path(tmp, "x")):
                section, files = capture.read_wallpapers(groups, notes)
        self.assertEqual(files, {})
        self.assertTrue(all("url" in image for image in section["images"]))
        self.assertEqual([i["url"] for i in section["images"]], [i["url"] for i in images[::-1]])
