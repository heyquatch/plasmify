"""GIF and animated pictures outside the GUI: image checks, downloads, bundles, capture,
generate and the login screen (which gets a still PNG of the first frame)."""

import copy
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from plasmify import activity, bundle, capture, downloads, generate, presets, rootops, schema, tomlwrite
from plasmify.steps import login, wallpaper
from plasmify.steps.base import Context
from plasmify.system import Runner, UnsafePathError

try:
    from PyQt6.QtCore import QBuffer, QIODevice, Qt
    from PyQt6.QtGui import QImage, QImageWriter
except ImportError:  # the checks still run on header bytes alone
    QImage = None

needs_qt = unittest.skipIf(QImage is None, "needs PyQt6")
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64  # the same stand-in the other test modules use
# Four colours: red, blue, green, white.
PALETTE = bytes([255, 0, 0, 0, 0, 255, 0, 255, 0, 255, 255, 255])


def lzw_fill(count: int, index: int) -> bytes:
    """GIF image data painting ``count`` pixels with colour ``index``. A clear code every two
    pixels keeps every code 3 bits wide, so no real LZW compressor is needed."""
    codes: list[int] = []
    for start in range(0, count, 2):
        codes += [4] + [index] * min(2, count - start)  # 4 = clear
    codes.append(5)  # end of information
    out, bits, used = bytearray(), 0, 0
    for code in codes:
        bits |= code << used
        used += 3
        while used >= 8:
            out.append(bits & 0xFF)
            bits >>= 8
            used -= 8
    if used:
        out.append(bits & 0xFF)
    blocks = bytearray([2])  # minimum code size
    for i in range(0, len(out), 255):
        part = out[i : i + 255]
        blocks += bytes([len(part)]) + part
    return bytes(blocks + b"\0")


def gif(
    width: int = 4, height: int = 3, frames: tuple[int, ...] = (0, 1), screen: tuple[int, int] | None = None
) -> bytes:
    """A real GIF, one solid colour per frame (palette index); ``screen`` overrides the
    logical screen size in the header."""
    screen_w, screen_h = screen or (width, height)
    out = bytearray(b"GIF89a" + struct.pack("<HHBBB", screen_w, screen_h, 0x81, 0, 0) + PALETTE)
    if len(frames) > 1:
        out += b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"  # loop forever
    for index in frames:
        out += b"\x21\xf9\x04\x00\x0a\x00\x00\x00"  # 0.1 s per frame
        out += b"\x2c" + struct.pack("<HHHHB", 0, 0, width, height, 0)
        out += lzw_fill(width * height, index)
    return bytes(out + b"\x3b")


def qt_encode(fmt: bytes, colour=None, width: int = 64, height: int = 48) -> bytes:
    """A solid picture Qt encodes (Qt cannot read back WebP files of only a few bytes)."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(colour if colour is not None else Qt.GlobalColor.red)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QImageWriter(buffer, fmt)
    writer.setQuality(100)
    if not writer.write(image):
        raise unittest.SkipTest(f"Qt cannot write {fmt.decode()} here")
    return bytes(buffer.data())


def riff_chunk(fourcc: bytes, payload: bytes) -> bytes:
    return fourcc + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) % 2 else b"")


def animated_webp(colours: tuple, width: int = 4, height: int = 3) -> bytes:
    """An animated WebP built by hand from frames Qt encodes as still WebP pictures."""

    def u24(value: int) -> bytes:
        return value.to_bytes(3, "little")

    frames = b""
    for colour in colours:
        still = qt_encode(b"webp", colour, width, height)
        if still[12:16] not in (b"VP8L", b"VP8 "):
            raise unittest.SkipTest("Qt wrote an unexpected WebP layout")
        header = u24(0) + u24(0) + u24(width - 1) + u24(height - 1) + u24(100) + b"\0"
        frames += riff_chunk(b"ANMF", header + still[12:])
    vp8x = riff_chunk(b"VP8X", bytes([0x02, 0, 0, 0]) + u24(width - 1) + u24(height - 1))
    body = b"WEBP" + vp8x + riff_chunk(b"ANIM", b"\0\0\0\0\0\0") + frames
    return b"RIFF" + struct.pack("<I", len(body)) + body


def png_header(width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    )


def own_preset(files: list[str]) -> dict:
    data = copy.deepcopy(presets.find("nord-minimal").data)
    data["meta"]["id"] = "my-loop"
    data["meta"]["name"] = "My Loop"
    data["wallpaper"]["images"] = [{"file": name, "credit": "My own wallpaper"} for name in files]
    return data


class ImageCheckTests(unittest.TestCase):
    def test_gif_is_an_image(self):
        for magic in downloads.GIF_MAGIC:
            data = magic + gif()[6:]
            with self.subTest(magic):
                self.assertEqual(downloads.image_kind(data), "gif")
                self.assertTrue(downloads.is_image_bytes(data))
                self.assertTrue(downloads.is_animated(data))
        self.assertIn(b"GIF89a", downloads.IMAGE_MAGIC)

    def test_gif_file_is_an_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "loop.gif")
            path.write_bytes(gif())
            self.assertTrue(downloads.is_image(path))
            self.assertIsNone(downloads.image_file_problem(path))

    def test_fake_gifs_are_refused(self):
        real = gif()
        cases = {
            "wrong version": b"GIF88a" + real[6:],
            "lower case": b"gif89a" + real[6:],
            "short magic": b"GIF8",
            "shifted": b" " + real,
            "script": b"#!/bin/sh\nrm -rf ~\n",
            "html": b"<html>GIF89a</html>",
            "empty": b"",
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.assertFalse(downloads.is_image_bytes(data))
                self.assertEqual(downloads.image_problem(data), downloads.NOT_AN_IMAGE)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "loop.gif")  # the name never decides
            path.write_bytes(cases["script"])
            self.assertFalse(downloads.is_image(path))
            self.assertFalse(downloads.is_image(Path(tmp, "missing.gif")))
            self.assertFalse(downloads.is_image(Path(tmp)))

    def test_oversized_dimensions_are_refused(self):
        side = downloads.MAX_IMAGE_SIDE
        cases = {
            "wide gif": gif(1, 1, (0,), screen=(side + 1, 10)),
            "tall gif": gif(1, 1, (0,), screen=(10, 65535)),
            "huge png": png_header(100_000, 100_000),
            "wide png": png_header(side + 1, 1),
            "huge webp canvas": b"RIFF\0\0\0\0WEBPVP8X\x0a\0\0\0\x02\0\0\0"
            + (9999).to_bytes(3, "little")
            + (9999).to_bytes(3, "little"),
        }
        for label, data in cases.items():
            with self.subTest(label):
                self.assertIn("too large", downloads.image_problem(data) or "")
                self.assertFalse(downloads.is_image_bytes(data))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "bomb.gif")
            path.write_bytes(cases["wide gif"])
            self.assertIn("too large", downloads.image_file_problem(path) or "")
        self.assertTrue(downloads.is_image_bytes(gif(1, 1, (0,), screen=(side, side))))

    @needs_qt
    def test_too_many_frames_are_refused(self):
        limit = downloads.MAX_IMAGE_FRAMES
        self.assertIn("too many frames", downloads.image_problem(gif(1, 1, (0,) * (limit + 1))) or "")
        self.assertIsNone(downloads.image_problem(gif(1, 1, (0,) * limit)))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "long.gif")
            path.write_bytes(gif(1, 1, (0, 1) * limit))
            self.assertFalse(downloads.is_image(path))

    @needs_qt
    def test_qt_reads_sizes_the_first_bytes_do_not_state(self):
        jpeg = bytearray(qt_encode(b"jpeg"))
        sof = jpeg.find(b"\xff\xc0")
        self.assertGreater(sof, 0)
        self.assertTrue(downloads.is_image_bytes(bytes(jpeg)))
        jpeg[sof + 5 : sof + 9] = struct.pack(">HH", 9000, 9000)  # height, width
        self.assertIn("too large", downloads.image_problem(bytes(jpeg)) or "")

        webp = bytearray(qt_encode(b"webp"))
        self.assertTrue(downloads.is_image_bytes(bytes(webp)))
        if webp[12:16] == b"VP8L":
            bits = int.from_bytes(webp[21:25], "little") & ~((1 << 28) - 1)
            webp[21:25] = (bits | 9998 | (9998 << 14)).to_bytes(4, "little")  # 9999x9999
            self.assertIn("too large", downloads.image_problem(bytes(webp)) or "")

    def test_stand_in_pictures_of_other_tests_still_pass(self):
        """Headers that do not state a size are judged by their magic bytes, as before."""
        self.assertTrue(downloads.is_image_bytes(PNG))
        self.assertTrue(downloads.is_image_bytes(b"GIF89a" + b"\0" * 20))

    def test_animation_detection(self):
        self.assertTrue(downloads.is_animated(gif(frames=(0,))))  # Plasma animates every GIF
        self.assertFalse(downloads.is_animated(PNG))
        still_vp8x = b"RIFF\0\0\0\0WEBPVP8X\x0a\0\0\0\x10\0\0\0" + b"\0" * 6
        self.assertFalse(downloads.is_animated(still_vp8x))
        self.assertTrue(downloads.is_animated(still_vp8x[:20] + b"\x12" + still_vp8x[21:]))
        self.assertFalse(downloads.is_animated(b"RIFF\0\0\0\0WEBPVP8X"))  # cut short

    def test_downloaded_gifs_keep_their_suffix(self):
        self.assertEqual(downloads.image_suffix("https://github.com/a/loop.GIF"), ".gif")
        self.assertEqual(downloads.image_suffix("https://github.com/a/loop.gif.sh"), ".png")
        self.assertEqual(downloads.image_suffix("https://github.com/a/loop"), ".png")

    def fetch(self, body: bytes, dest: Path) -> Path:
        response = mock.MagicMock()
        response.__enter__.return_value.read.side_effect = [body, b""]
        downloads.reset_budget()
        try:
            with (
                mock.patch.object(activity, "LOG_FILE", dest.parent / "activity.jsonl"),
                mock.patch.object(downloads._opener, "open", return_value=response),
            ):
                return downloads.fetch_image("https://github.com/a/loop.gif", dest)
        finally:
            downloads.reset_budget()

    def test_fetch_image_keeps_gifs_and_drops_bombs(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = self.fetch(gif(), Path(tmp, "good.gif"))
            self.assertEqual(good.read_bytes(), gif())
            for label, body in {"bomb": gif(1, 1, (0,), screen=(60000, 60000)), "fake": b"GIF88a" + b"\0" * 40}.items():
                with self.subTest(label), self.assertRaises(downloads.DownloadError):
                    self.fetch(body, Path(tmp, f"{label}.gif"))
                self.assertFalse(Path(tmp, f"{label}.gif").exists())

    @needs_qt
    def test_still_png_is_the_first_frame(self):
        png = downloads.still_png(gif(frames=(0, 1)))
        self.assertEqual(downloads.image_kind(png), "png")
        image = QImage.fromData(png, "PNG")
        self.assertFalse(image.isNull())
        self.assertEqual((image.width(), image.height()), (4, 3))
        self.assertEqual(image.pixelColor(0, 0).name(), "#ff0000")

    @needs_qt
    def test_still_png_of_a_large_picture_fits_a_4k_screen(self):
        png = downloads.still_png(gif(4000, 1, (2,)))
        image = QImage.fromData(png, "PNG")
        self.assertEqual(image.width(), downloads.STILL_SIZE[0])
        self.assertEqual(image.pixelColor(0, 0).name(), "#00ff00")

    def test_still_png_refuses_what_it_cannot_read(self):
        for data in (b"#!/bin/sh", gif(1, 1, (0,), screen=(60000, 1)), b"GIF89a" + b"\0" * 20):
            with self.subTest(data[:12]), self.assertRaises(ValueError):
                downloads.still_png(data)


class BundleGifTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for patcher in (
            mock.patch.object(bundle, "USER_PRESET_DIR", self.root / "presets"),
            mock.patch.object(presets, "USER_PRESET_DIR", self.root / "presets"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.loop = self.root / "loop.gif"
        self.loop.write_bytes(gif())

    def make_zip(self, members: dict[str, bytes], data: dict | None = None) -> Path:
        path = self.root / "shared.plasmify"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("preset.toml", tomlwrite.dumps(data or own_preset(["01-loop.gif"])))
            for name, content in members.items():
                zf.writestr(name, content)
        return path

    def test_round_trip_with_a_gif(self):
        preset = bundle.save_user_preset(own_preset(["01-loop.gif"]), {"01-loop.gif": self.loop})
        archive = bundle.export_bundle(preset, self.root / "share")
        with zipfile.ZipFile(archive) as zf:
            self.assertEqual(sorted(zf.namelist()), ["assets/01-loop.gif", "preset.toml"])
        bundle.delete_user_preset(preset)
        data, assets = bundle.read_bundle(archive)
        self.assertEqual(assets, {"01-loop.gif": gif()})
        imported = bundle.import_file(archive)
        self.assertEqual(imported.data, preset.data)
        self.assertEqual((imported.asset_dir / "01-loop.gif").read_bytes(), gif())

    def test_gif_as_login_background_travels_too(self):
        data = own_preset(["01-loop.gif"])
        data["login"] = {"background": "image", "background_file": "login.gif"}
        files = {"01-loop.gif": self.loop, "login.gif": self.loop}
        preset = bundle.save_user_preset(data, files)
        _data, assets = bundle.read_bundle(bundle.export_bundle(preset, self.root / "share"))
        self.assertEqual(sorted(assets), ["01-loop.gif", "login.gif"])

    def test_bad_gif_contents_are_refused(self):
        cases = {
            "wrong magic": b"GIF88a" + gif()[6:],
            "script named gif": b"#!/bin/sh\nrm -rf ~\n",
            "decompression bomb": gif(1, 1, (0,), screen=(65535, 65535)),
        }
        if QImage is not None:
            cases["endless animation"] = gif(1, 1, (0,) * (downloads.MAX_IMAGE_FRAMES + 1))
        for label, content in cases.items():
            with self.subTest(label), self.assertRaises(presets.PresetError):
                bundle.import_file(self.make_zip({"assets/01-loop.gif": content}))
            with self.subTest(f"{label} on save"), self.assertRaises(presets.PresetError):
                self.loop.write_bytes(content)
                bundle.save_user_preset(own_preset(["01-loop.gif"]), {"01-loop.gif": self.loop})
        self.assertFalse((self.root / "presets" / "my-loop.toml").exists())

    def test_hostile_gif_names_are_refused(self):
        names = [
            "assets/../01-loop.gif",
            "assets/../../evil.gif",
            "assets/01-loop.gif.sh",
            "assets/.01-loop.gif",
            "assets/sub/01-loop.gif",
            "assets/01 loop.gif",
            "assets/01-loop.gif\x00.png",
            "assets/" + "a" * 120 + ".gif",
            "/assets/01-loop.gif",
            "assets\\01-loop.gif",
        ]
        for name in names:
            with self.subTest(name=name), self.assertRaises(presets.PresetError):
                bundle.import_file(self.make_zip({"assets/01-loop.gif": gif(), name: gif()}))
        for bad in ("../loop.gif", "a/loop.gif", ".loop.gif", "loop.gif.sh", "loop gif.gif", "loop.gifx"):
            with self.subTest(file=bad):
                self.assertTrue(schema.validate(own_preset([bad])))


class CaptureAndGenerateTests(unittest.TestCase):
    def test_every_suffix_list_takes_gif(self):
        for suffixes in (capture.IMAGE_SUFFIXES, generate.IMAGE_SUFFIXES, wallpaper.IMAGE_SUFFIXES):
            self.assertIn(".gif", suffixes)

    def test_capture_includes_gif_wallpapers_under_safe_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp, "walls")
            folder.mkdir()
            (folder / "loop.GIF").write_bytes(gif())
            (folder / "evil $(rm -rf ~);.gif").write_bytes(gif())
            (folder / "notes.txt").write_text("not a picture")
            groups = {
                "[Containments][1]": {"plugin": "org.kde.plasma.folder", "wallpaperplugin": "org.kde.slideshow"},
                "[Containments][1][Wallpaper][org.kde.slideshow][General]": {"SlidePaths": str(folder)},
            }
            notes: list[str] = []
            section, files = capture.read_wallpapers(groups, notes)
            self.assertEqual(len(files), 2)
            for name in files:
                self.assertTrue(name.endswith(".gif"), name)
                self.assertIsNone(schema.IMAGE_FILE(name), name)
            data = own_preset(list(files))
            data["wallpaper"] = section
            self.assertEqual(schema.validate(data), [])

    def test_generate_keeps_gif_names_safe(self):
        self.assertEqual(generate.wallpaper_file_name(1, Path("my loop.GIF")), "01-my_loop.gif")
        self.assertEqual(generate.wallpaper_file_name(2, Path("../../etc/passwd.gif")), "02-passwd.gif")
        self.assertEqual(generate.wallpaper_file_name(3, Path("x.gif.exe")), "03-x.gif.png")
        for name in ("01-my_loop.gif", "02-passwd.gif", "03-x.gif.png"):
            self.assertIsNone(schema.IMAGE_FILE(name), name)

    def test_current_wallpaper_can_be_a_gif(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp, "loop.gif")
            image.write_bytes(gif())
            config = Path(tmp, "config")
            config.mkdir()
            (config / "plasma-org.kde.plasma.desktop-appletsrc").write_text(f"[x]\nImage=file://{image}\n")
            with mock.patch("plasmify.system.CONFIG_HOME", config):
                self.assertEqual(generate.current_wallpaper(), image)

    @needs_qt
    def test_palette_comes_from_the_first_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "loop.gif")
            path.write_bytes(gif(frames=(0, 1)))
            pixels = generate.load_pixels(path, 8)
            self.assertTrue(pixels)
            self.assertTrue(all(p == (255, 0, 0) for p in pixels), pixels[:3])

    @needs_qt
    def test_palette_refuses_a_huge_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "bomb.gif")
            path.write_bytes(gif(1, 1, (0,), screen=(60000, 60000)))
            with self.assertRaises(ValueError):
                generate.load_pixels(path)


class LoginStillTests(unittest.TestCase):
    """The login screen gets a still PNG of an animation, never a GIF under a wrong name.
    ``runner.run`` is replaced, so nothing here ever runs pkexec."""

    def apply(self, image: bytes, file_name: str = "me.gif", dry_run: bool = False, link: str = ""):
        data = copy.deepcopy(presets.find("nord-minimal").data)
        data["login"] = {"background": "image", "background_file": file_name}
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        assets = Path(tmp.name, "assets")
        assets.mkdir()
        if link:  # "inside" or "outside" the preset's folder
            real = (assets if link == "inside" else Path(tmp.name)) / "elsewhere.gif"
            real.write_bytes(image)
            (assets / file_name).symlink_to(real)
        else:
            (assets / file_name).write_bytes(image)
        preset = presets.Preset(data, "user", asset_override=assets)
        runner = Runner(dry_run=dry_run)
        runner.fed = []
        runner.run = lambda args, **kw: (runner.log.append(" ".join(args)), runner.fed.append(kw.get("input_bytes")))[0]
        logs: list[str] = []
        ctx = Context(preset, runner, Path(tmp.name), log=logs.append)
        with (
            mock.patch.object(login, "login_manager", return_value="plasmalogin"),
            mock.patch.object(rootops, "check_programs"),  # CI has no KDE tools
        ):
            login.LoginStep().apply(ctx)
        commands = [c for c in runner.log if "plasmify-login" in c]
        return commands, runner.fed, ctx, logs

    @needs_qt
    def test_gif_is_sent_as_a_real_png(self):
        commands, fed, ctx, logs = self.apply(gif(frames=(0, 1)))
        self.assertEqual(len(commands), 1)
        self.assertTrue(commands[0].endswith("plasmify-login background.png plasmalogin"))
        self.assertTrue(fed[-1].startswith(b"\x89PNG\r\n\x1a\n"))
        image = QImage.fromData(fed[-1], "PNG")
        self.assertFalse(image.isNull())
        self.assertEqual((image.width(), image.height()), (4, 3))
        self.assertEqual(image.pixelColor(0, 0).name(), "#ff0000")  # the first frame, not the second
        self.assertTrue(any("first frame" in line for line in logs))
        self.assertFalse(ctx.warnings)

    @needs_qt
    def test_animated_webp_is_sent_as_a_png(self):
        webp = animated_webp((Qt.GlobalColor.blue, Qt.GlobalColor.red))
        self.assertTrue(downloads.is_animated(webp))
        commands, fed, ctx, _logs = self.apply(webp, "me.webp")
        self.assertTrue(commands and commands[0].endswith("background.png plasmalogin"), ctx.warnings)
        image = QImage.fromData(fed[-1], "PNG")
        self.assertEqual(image.pixelColor(0, 0).name(), "#0000ff")

    @needs_qt
    def test_still_pictures_keep_their_bytes_and_get_their_real_name(self):
        jpeg = qt_encode(b"jpeg")
        cases = {
            "me.png": (PNG, "background.png"),
            "me.jpeg": (jpeg, "background.jpeg"),
            "photo.png": (jpeg, "background.jpg"),
        }
        for file_name, (content, expected) in cases.items():
            with self.subTest(file_name):
                commands, fed, _ctx, _logs = self.apply(content, file_name)
                self.assertTrue(commands[0].endswith(f"plasmify-login {expected} plasmalogin"))
                self.assertEqual(fed[-1], content)
                self.assertIn(expected, rootops.LOGIN_NAMES)

    def test_dry_run_names_the_still_without_reading_it_all(self):
        commands, fed, _ctx, _logs = self.apply(gif(), dry_run=True)
        self.assertTrue(commands[0].endswith("background.png plasmalogin"))
        self.assertEqual(fed[-1], b"")

    def test_unusable_gifs_never_reach_root(self):
        cases = {
            "fake gif": b"GIF89a" + b"\0" * 40,  # passes the magic check, but has no frame to show
            "wrong magic": b"GIF88a" + gif()[6:],
            "decompression bomb": gif(1, 1, (0,), screen=(60000, 60000)),
        }
        for label, content in cases.items():
            with self.subTest(label):
                commands, _fed, ctx, _logs = self.apply(content)
                self.assertEqual(commands, [])
                self.assertTrue(ctx.warnings)

    def test_symlinked_gif_never_reaches_root(self):
        commands, _fed, ctx, _logs = self.apply(gif(), link="inside")  # read_image refuses links
        self.assertEqual(commands, [])
        self.assertTrue(ctx.warnings)
        with self.assertRaises(UnsafePathError):  # the engine reports it as a failed step
            self.apply(gif(), link="outside")

    def test_hostile_login_file_names_are_refused(self):
        data = copy.deepcopy(presets.find("nord-minimal").data)
        for bad in ("../../etc/shadow.gif", "/etc/x.gif", ".me.gif", "me.gif.sh", "me.gif/x.png", "me\n.gif"):
            data["login"] = {"background": "image", "background_file": bad}
            with self.subTest(bad):
                self.assertTrue(schema.validate(data))

    def test_login_names_are_only_those_root_accepts(self):
        self.assertEqual(login.login_name("gif", ".gif"), None)
        self.assertEqual(login.login_name(None, ".png"), None)
        self.assertEqual(login.login_name("png", ".gif"), "background.png")
        self.assertEqual(login.login_name("jpeg", ".JPG".lower()), "background.jpg")
        for kind, suffix in (("png", ".png"), ("jpeg", ".jpeg"), ("jpeg", ".gif"), ("webp", ".webp")):
            self.assertIn(login.login_name(kind, suffix), rootops.LOGIN_NAMES)


if __name__ == "__main__":
    unittest.main()
