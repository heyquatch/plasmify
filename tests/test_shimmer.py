"""Shimmer: living wallpapers as animated WebP files, made with Qt alone."""

from __future__ import annotations

import io
import math
import os
import random
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, Qt
    from PyQt6.QtGui import QColor, QColorSpace, QImage, QImageReader, QLinearGradient, QMovie, QPainter
    from PyQt6.QtWidgets import QApplication

    from plasmify import shimmer
except ImportError:  # pragma: no cover
    shimmer = None

SRC = Path(__file__).resolve().parents[1] / "src"
WIDTH, HEIGHT = 320, 180
SMALL = (160, 90)  # for checks that paint every combination of style, light and motion
COLOUR = "#f5c2e7"


def uint24(data: bytes) -> int:
    return int.from_bytes(data[:3], "little")


def pixels(image: QImage) -> bytes:
    return bytes(image.constBits().asstring(image.sizeInBytes()))


def mean_difference(first: QImage, second: QImage) -> float:
    """How far apart two pictures of the same size are, per colour channel (0 to 255)."""
    one = pixels(first.convertToFormat(QImage.Format.Format_RGB32))
    two = pixels(second.convertToFormat(QImage.Format.Format_RGB32))
    channels = [abs(a - b) for index, (a, b) in enumerate(zip(one, two, strict=True)) if index % 4 != 3]
    return sum(channels) / len(channels)


def landscape(path: Path, width: int = WIDTH, height: int = HEIGHT) -> Path:
    """A small made-up landscape: an evening sky, a sun and a dark hill."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    sky = QLinearGradient(0, 0, 0, height)
    sky.setColorAt(0, QColor("#1e1e2e"))
    sky.setColorAt(1, QColor("#f5a97f"))
    painter.fillRect(image.rect(), sky)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#f9e2af"))
    painter.drawEllipse(QPointF(width * 0.65, height * 0.55), height * 0.15, height * 0.15)
    painter.setBrush(QColor("#181825"))
    painter.drawEllipse(QPointF(width * 0.3, height * 1.1), width * 0.6, height * 0.45)
    painter.end()
    image.save(str(path))
    return path


def bands(path: Path, width: int, height: int, colours: tuple[str, str, str], edge: float) -> Path:
    """Three vertical bands: ``edge`` of the width on each side, the middle in between."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(colours[1]))
    painter = QPainter(image)
    side = round(width * edge)
    painter.fillRect(0, 0, side, height, QColor(colours[0]))
    painter.fillRect(width - side, 0, side, height, QColor(colours[2]))
    painter.end()
    image.save(str(path))
    return path


def turned_jpeg(path: Path, width: int, height: int) -> Path:
    """A JPEG stored on its side (left half red, right half blue) whose EXIF data says to turn it
    90 degrees clockwise to show it: then red is on top."""
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#0000ff"))
    painter = QPainter(image)
    painter.fillRect(0, 0, width // 2, height, QColor("#ff0000"))
    painter.end()
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "JPEG", 90)
    jpeg = data.data()
    # A little-endian TIFF block with a single entry: Orientation (0x0112) = 6.
    tiff = b"II*\0" + struct.pack("<IH", 8, 1) + struct.pack("<HHIHH", 0x0112, 3, 1, 6, 0) + struct.pack("<I", 0)
    exif = b"Exif\0\0" + tiff
    path.write_bytes(jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + jpeg[2:])
    return path


def solid(path: Path, colour: str, width: int = WIDTH, height: int = HEIGHT) -> Path:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(colour))
    image.save(str(path))
    return path


def frame_at(
    base: QImage, size: tuple[int, int], style: str, light: str, motion: str, t: float, seed: int = 3
) -> QImage:
    """One frame painted directly (no encoding), with fresh layers as render() makes them."""
    width, height = size
    scene = shimmer._SCENES[style](random.Random(seed), width, height, COLOUR, 6.0, 1.0)
    glow = shimmer._LIGHTS[light](random.Random(f"light:{seed}"), width, height, COLOUR, 6.0)
    frame = QImage(width, height, QImage.Format.Format_RGB32)
    shimmer._paint(frame, base, motion, glow, scene, t)
    return frame


def still_vp8l(width: int, height: int, alpha: bool = False, extra: bytes = b"\0\0") -> bytes:
    """A still WebP file holding just a VP8L header (enough for the muxer, which never decodes)."""
    bits = (width - 1) | (height - 1) << 14 | int(alpha) << 28
    payload = b"\x2f" + struct.pack("<I", bits) + extra
    chunk = b"VP8L" + struct.pack("<I", len(payload)) + payload + b"\0" * (len(payload) & 1)
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


@unittest.skipIf(shimmer is None, "PyQt6 is not installed")
@unittest.skipIf(shimmer is not None and not shimmer.available(), "Qt cannot write WebP here")
class ShimmerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Like the GUI tests; render() then finds an application and does not make its own.
        cls.app = QApplication.instance() or QApplication([])
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.source = landscape(cls.root / "landscape.png")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def render(self, style: str = "sparkle", source: Path | None = None, **options) -> Path:
        target = Path(tempfile.mkdtemp(dir=self.root)) / "living.webp"
        colour = options.pop("colour", COLOUR)
        settings = {"size": (WIDTH, HEIGHT), "seconds": 1.0, "fps": 6} | options
        result = shimmer.render(source or self.source, target, colour, style, **settings)
        self.assertEqual(result, target)
        self.assertEqual([p.name for p in target.parent.iterdir()], ["living.webp"])  # nothing else left
        return target

    def chunks(self, data: bytes, pos: int = 12) -> list[tuple[bytes, bytes]]:
        """(tag, payload) of the RIFF chunks from ``pos`` to the end; odd sizes must be padded."""
        found = []
        while pos < len(data):
            tag, size = data[pos : pos + 4], struct.unpack("<I", data[pos + 4 : pos + 8])[0]
            found.append((tag, data[pos + 8 : pos + 8 + size]))
            if size & 1:
                self.assertEqual(data[pos + 8 + size], 0, f"{tag!r} is not padded")
            pos += 8 + size + (size & 1)
        self.assertEqual(pos, len(data))
        return found

    def first_frame(self, target: Path) -> QImage:
        frame = QImageReader(str(target)).read()
        self.assertFalse(frame.isNull())
        return frame

    def test_every_style_plays_as_an_endless_loop(self):
        for style in shimmer.STYLES:
            with self.subTest(style=style):
                reader = QImageReader(str(self.render(style)))
                self.assertTrue(reader.supportsAnimation())
                self.assertEqual(reader.imageCount(), 6)
                self.assertEqual(reader.loopCount(), -1)  # Qt's word for forever
                frames = []
                for _ in range(6):
                    frame = reader.read()
                    self.assertFalse(frame.isNull(), reader.errorString())
                    self.assertEqual((frame.width(), frame.height()), (WIDTH, HEIGHT))
                    frames.append(frame)
                self.assertNotEqual(frames[0], frames[3])  # it moves

    def test_container_layout(self):
        data = self.render().read_bytes()
        self.assertEqual((data[:4], data[8:12]), (b"RIFF", b"WEBP"))
        self.assertEqual(struct.unpack("<I", data[4:8])[0], len(data) - 8)
        top = self.chunks(data)
        self.assertEqual([tag for tag, _ in top], [b"VP8X", b"ANIM"] + [b"ANMF"] * 6)

        vp8x = top[0][1]
        self.assertEqual(len(vp8x), 10)
        self.assertEqual(vp8x[0], 0x02)  # animated; no alpha, ICC profile or metadata
        self.assertEqual((uint24(vp8x[4:]) + 1, uint24(vp8x[7:]) + 1), (WIDTH, HEIGHT))
        anim = top[1][1]
        self.assertEqual(anim, bytes([0, 0, 0, 255, 0, 0]))  # opaque black, loop forever

        durations = []
        for _, frame in top[2:]:
            self.assertEqual((uint24(frame[0:]), uint24(frame[3:])), (0, 0))
            self.assertEqual((uint24(frame[6:]) + 1, uint24(frame[9:]) + 1), (WIDTH, HEIGHT))
            durations.append(uint24(frame[12:]))
            self.assertEqual(frame[15], 0x02)  # replaces the previous frame, no blending
            self.assertEqual([tag for tag, _ in self.chunks(frame, 16)], [b"VP8 "])
        self.assertEqual(sum(durations), 1000)  # 6 frames at 6 fps
        self.assertTrue(all(d in (166, 167) for d in durations), durations)

    def test_durations_follow_fps(self):
        data = self.render(seconds=0.5, fps=12).read_bytes()
        frames = [payload for tag, payload in self.chunks(data) if tag == b"ANMF"]
        self.assertEqual(len(frames), 6)
        self.assertEqual([uint24(f[12:]) for f in frames], [83, 84, 83, 83, 84, 83])

    def test_lossless_frames(self):
        target = self.render(quality=100)
        frames = [payload for tag, payload in self.chunks(target.read_bytes()) if tag == b"ANMF"]
        self.assertEqual({tag for frame in frames for tag, _ in self.chunks(frame, 16)}, {b"VP8L"})
        reader = QImageReader(str(target))
        self.assertEqual(reader.imageCount(), 6)
        self.assertFalse(reader.read().isNull())

    def test_muxer_pads_odd_chunks_and_flags_alpha(self):
        out = io.BytesIO()
        webp = shimmer._AnimatedWebP(out, 2, 2)
        webp.add(still_vp8l(2, 2, extra=b"\0\0"), 100)  # a 7 byte VP8L chunk
        webp.finish()
        data = out.getvalue()
        top = self.chunks(data)
        self.assertEqual(top[0][1][0], 0x02)
        anmf = top[2][1]
        self.assertEqual(len(anmf), 16 + 8 + 7 + 1)
        self.assertEqual(self.chunks(anmf, 16), [(b"VP8L", b"\x2f" + struct.pack("<I", 1 | 1 << 14) + b"\0\0")])

        out = io.BytesIO()
        webp = shimmer._AnimatedWebP(out, 2, 2)
        webp.add(still_vp8l(2, 2, alpha=True), 100)
        webp.finish()
        self.assertEqual(self.chunks(out.getvalue())[0][1][0], 0x12)  # animated, with alpha

        webp = shimmer._AnimatedWebP(io.BytesIO(), 4, 4)
        with self.assertRaises(RuntimeError):
            webp.add(still_vp8l(2, 2), 100)  # not the canvas size
        with self.assertRaises(RuntimeError):
            webp.add(b"RIFF\0\0\0\0WAVE", 100)

    def test_same_seed_same_file(self):
        for style in shimmer.STYLES:
            with self.subTest(style=style):
                first = self.render(style, seed=7).read_bytes()
                self.assertEqual(first, self.render(style, seed=7).read_bytes())
                self.assertNotEqual(first, self.render(style, seed=8).read_bytes())
        for light in ("glint", "rays", "flare"):  # the lights that are placed by the seed
            with self.subTest(light=light):
                first = self.render("none", light=light, motion="still", seed=7).read_bytes()
                self.assertEqual(first, self.render("none", light=light, motion="still", seed=7).read_bytes())
                self.assertNotEqual(first, self.render("none", light=light, motion="still", seed=8).read_bytes())

    def test_last_frame_flows_into_the_first(self):
        """Every style with every light and every motion: the frame at the end of the loop is the
        first frame again, to the last pixel, and something moves in between."""
        for motion in shimmer.MOTIONS:
            base = shimmer._load(self.source, *SMALL, shimmer._MOTION_ZOOM[motion])
            for style in shimmer.STYLES:
                for light in shimmer.LIGHTS:
                    with self.subTest(style=style, light=light, motion=motion):
                        # Not halfway: some layers pulse twice per loop and look the same there.
                        first, middle, last = (frame_at(base, SMALL, style, light, motion, t) for t in (0.0, 0.37, 1.0))
                        self.assertEqual(pixels(first), pixels(last))
                        if (style, light, motion) != ("none", "none", "still"):
                            self.assertNotEqual(pixels(first), pixels(middle))

    def test_every_light_shows_on_its_own(self):
        base = shimmer._load(self.source, WIDTH, HEIGHT, 0.0)
        plain = pixels(frame_at(base, (WIDTH, HEIGHT), "none", "none", "still", 0.4))
        for light in shimmer.LIGHTS:
            with self.subTest(light=light):
                lit = [pixels(frame_at(base, (WIDTH, HEIGHT), "none", light, "still", t)) for t in (0.0, 0.4, 0.7)]
                if light == "none":
                    self.assertEqual(lit, [plain] * 3)
                else:
                    self.assertNotEqual(lit[1], plain)
                    self.assertNotEqual(lit[1], lit[2])  # and it moves

    def test_motions(self):
        # A plain green picture: any black showing at the edges would be the canvas behind it.
        green = solid(self.root / "green.png", "#00ff00")
        edges = [(x, y) for x in (0, WIDTH // 2, WIDTH - 1) for y in (0, HEIGHT - 1)]
        edges += [(0, HEIGHT // 2), (WIDTH - 1, HEIGHT // 2)]
        for motion in shimmer.MOTIONS:
            base = shimmer._load(green, WIDTH, HEIGHT, shimmer._MOTION_ZOOM[motion])
            for step in range(24):
                with self.subTest(motion=motion, t=step / 24):
                    frame = frame_at(base, (WIDTH, HEIGHT), "none", "none", motion, step / 24)
                    for x, y in edges:
                        colour = frame.pixelColor(x, y)
                        self.assertGreater(colour.green(), 240, (x, y, colour.name()))
                        self.assertLess(colour.red() + colour.blue(), 20, (x, y, colour.name()))
        frames = {}
        for motion in shimmer.MOTIONS:
            base = shimmer._load(self.source, WIDTH, HEIGHT, shimmer._MOTION_ZOOM[motion])
            frames[motion] = [
                pixels(frame_at(base, (WIDTH, HEIGHT), "none", "none", motion, t)) for t in (0, 0.25, 0.5)
            ]
        self.assertEqual(len(set(frames["still"])), 1)
        self.assertEqual(len(set(frames["breathe"])), 3)
        self.assertEqual(len(set(frames["drift"])), 3)
        # A still picture is drawn at its plain size: the canvas is the picture, cropped to fit.
        self.assertEqual(
            shimmer._load(self.source, WIDTH, HEIGHT, 0.0).size(),
            QImage(WIDTH, HEIGHT, QImage.Format.Format_RGB32).size(),
        )

    def test_defaults_per_style(self):
        self.assertEqual(set(shimmer.DEFAULT_LIGHT), set(shimmer.STYLES))
        self.assertEqual(set(shimmer.DEFAULT_MOTION), set(shimmer.STYLES))
        self.assertTrue(set(shimmer.DEFAULT_LIGHT.values()) <= set(shimmer.LIGHTS))
        self.assertTrue(set(shimmer.DEFAULT_MOTION.values()) <= set(shimmer.MOTIONS))
        self.assertNotEqual(shimmer.DEFAULT_LIGHT["none"], "none")  # "none" must still show something
        options = {"size": SMALL, "seconds": 0.5, "fps": 6}
        for style in shimmer.STYLES:
            with self.subTest(style=style):
                light, motion = shimmer.DEFAULT_LIGHT[style], shimmer.DEFAULT_MOTION[style]
                implied = self.render(style, seed=2, **options).read_bytes()
                spelled = self.render(style, light=light, motion=motion, density="normal", seed=2, **options)
                self.assertEqual(implied, spelled.read_bytes())
                job = shimmer._job(COLOUR, style, None, None, "normal", SMALL, 1.0, 6, 72, 0)
                self.assertEqual((job.light, job.motion), (light, motion))
                job = shimmer._job(COLOUR, style, "flare", "still", "high", SMALL, 1.0, 6, 72, 0)
                self.assertEqual((job.light, job.motion, job.density), ("flare", "still", shimmer.DENSITY["high"]))

    def test_choices(self):
        self.assertEqual(shimmer.VERSION, 3)
        self.assertEqual(
            set(shimmer.STYLES),
            {
                "sparkle",
                "embers",
                "petals",
                "snow",
                "fireflies",
                "bokeh",
                "starfall",
                "rain",
                "magic",
                "speedlines",
                "none",
            },
        )
        self.assertEqual(shimmer.LIGHTS, ("glint", "rays", "flare", "pulse", "none"))
        self.assertEqual(shimmer.MOTIONS, ("breathe", "drift", "still"))
        self.assertEqual(shimmer.DENSITIES, ("low", "normal", "high"))
        self.assertEqual(set(shimmer._SCENES), set(shimmer.STYLES))
        self.assertEqual(set(shimmer._LIGHTS), set(shimmer.LIGHTS))
        self.assertEqual(set(shimmer._MOTION_ZOOM), set(shimmer.MOTIONS))
        self.assertEqual(set(shimmer.DENSITY), set(shimmer.DENSITIES))

    def test_particles_at_1080p(self):
        expected = {  # at normal density
            "sparkle": (60, 70),
            "embers": (70, 80),
            "petals": (44, 52),
            "snow": (170, 178),
            "fireflies": (28, 32),
            "bokeh": (16, 20),
            "starfall": (3, 3),  # shooting stars in a 6 second loop
            "rain": (220, 220),
            "magic": (5, 5),  # sparks circling it
            "speedlines": (shimmer.SPEEDLINES, shimmer.SPEEDLINES),
            "none": (0, 0),
        }
        self.assertEqual(set(expected), set(shimmer.STYLES))
        for style, (low, high) in expected.items():
            with self.subTest(style=style):
                count = shimmer._SCENES[style](random.Random(0), 1920, 1080, COLOUR, 6.0).population
                self.assertGreaterEqual(count, low)
                self.assertLessEqual(count, high)
        self.assertEqual(shimmer._count("sparkle", 3840, 2160), 4 * shimmer._count("sparkle", 1920, 1080))

    def test_density_scales_the_particles(self):
        for style in shimmer.STYLES:
            with self.subTest(style=style):
                counts = [
                    shimmer._SCENES[style](random.Random(5), 1920, 1080, COLOUR, 6.0, shimmer.DENSITY[name]).population
                    for name in shimmer.DENSITIES
                ]
                if style == "none":
                    self.assertEqual(counts, [0, 0, 0])
                else:
                    self.assertLess(counts[0], counts[1])
                    self.assertLess(counts[1], counts[2])
                    self.assertLessEqual(counts[2], 2 * counts[1])  # high stays tasteful
        # Large enough for more than the fewest particles there can be.
        options = {"size": (640, 360), "seconds": 0.5, "seed": 4}
        quiet = self.render("snow", density="low", **options).read_bytes()
        self.assertNotEqual(quiet, self.render("snow", density="high", **options).read_bytes())

    def test_effects_keep_clear_of_the_middle(self):
        """Particles are placed more sparsely in the middle of the picture, where its subject is."""
        width, height = 1920, 1080
        for style, where in (
            ("sparkle", lambda scene: [(star.x, star.y) for star in scene.stars]),
            ("fireflies", lambda scene: [(fly.x, fly.y) for fly in scene.flies]),
            ("bokeh", lambda scene: [(disc.x, disc.y) for disc in scene.discs]),
        ):
            with self.subTest(style=style):
                inside = total = 0
                for seed in range(6):
                    points = where(shimmer._SCENES[style](random.Random(seed), width, height, COLOUR, 6.0))
                    total += len(points)
                    inside += sum(abs(x / width - 0.5) < 0.2 and abs(y / height - 0.5) < 0.2 for x, y in points)
                self.assertLess(inside / total, 0.16 * 0.75)  # the middle is 16% of the picture
        magic = shimmer._SCENES["magic"](random.Random(1), width, height, COLOUR, 6.0)
        self.assertGreater(abs(magic.centre.x() - width / 2), width / 4)  # in a corner
        self.assertGreater(abs(magic.centre.y() - height / 2), height / 5)

    def test_picture_covers_the_canvas_and_keeps_its_centre(self):
        # 640x180 drawn at 320x180: the red and blue fifths at the sides are cropped away.
        source = bands(self.root / "wide.png", 640, 180, ("#ff0000", "#00ff00", "#0000ff"), 0.2)
        frame = self.first_frame(self.render("embers", source=source, colour="#ffffff", light="none"))
        spots = [frame.pixelColor(x, y) for x in (4, 160, 315) for y in (20, 90, 160)]
        green = [c for c in spots if c.green() > 150 and c.red() < 90 and c.blue() < 90]
        self.assertGreaterEqual(len(green), 8, [c.name() for c in spots])  # a particle may cover one

    def test_turned_photos_stand_upright(self):
        # The large one is decoded at a reduced size, which Qt measures before turning it.
        for width, height in ((180, 320), (900, 1600)):
            with self.subTest(size=(width, height)):
                source = turned_jpeg(self.root / f"turned-{width}.jpg", width, height)
                frame = self.first_frame(self.render(source=source, colour="#ffffff", light="none"))
                top, bottom = frame.pixelColor(WIDTH // 2, 12), frame.pixelColor(WIDTH // 2, HEIGHT - 12)
                self.assertGreater(top.red(), 200, top.name())
                self.assertLess(top.blue(), 60, top.name())
                self.assertGreater(bottom.blue(), 200, bottom.name())
                self.assertLess(bottom.red(), 60, bottom.name())

    def test_thin_strips_are_cropped_before_they_are_scaled(self):
        """Scaled whole to cover the canvas, a strip of a few pixels would grow to gigabytes (an
        8192x1 picture at 1080p to 9158656x1118). Only its middle is scaled, and that is what shows."""
        original = QImage.scaled

        def scaled(image: QImage, *args) -> QImage:
            # Measured before Qt makes it, so a regression fails here and never fills the memory.
            size = image.size().scaled(*args[:-1])
            self.assertLessEqual(size.width() * size.height(), 4 * WIDTH * HEIGHT, size)
            return original(image, *args)

        for width, height in ((8192, 1), (1, 8192), (8192, 16), (16, 8192)):
            # Green in the middle; the red and blue quarters at the ends are cropped away.
            strip = QImage(width, height, QImage.Format.Format_RGB32)
            strip.fill(QColor("#00ff00"))
            painter = QPainter(strip)
            if width > height:
                painter.fillRect(0, 0, width // 4, height, QColor("#ff0000"))
                painter.fillRect(width - width // 4, 0, width // 4, height, QColor("#0000ff"))
            else:
                painter.fillRect(0, 0, width, height // 4, QColor("#ff0000"))
                painter.fillRect(0, height - height // 4, width, height // 4, QColor("#0000ff"))
            painter.end()
            source = self.root / f"strip-{width}x{height}.png"
            strip.save(str(source))
            for motion, zoom in shimmer._MOTION_ZOOM.items():
                with self.subTest(size=(width, height), motion=motion):
                    with mock.patch.object(QImage, "scaled", scaled):
                        base = shimmer._load(source, WIDTH, HEIGHT, zoom)
                    wide, high = base.width(), base.height()
                    self.assertEqual((wide, high), (math.ceil(WIDTH * (1 + zoom)), math.ceil(HEIGHT * (1 + zoom))))
                    for x, y in ((0, 0), (wide - 1, 0), (wide // 2, high // 2), (0, high - 1), (wide - 1, high - 1)):
                        colour = base.pixelColor(x, y)
                        self.assertGreater(colour.green(), 240, (x, y, colour.name()))
                        self.assertLess(colour.red() + colour.blue(), 20, (x, y, colour.name()))

    def test_cropping_first_looks_the_same(self):
        """The picture is cropped in its own pixels and then scaled: next to scaling it whole and
        cropping that, the difference is a fraction of a pixel's shift, and none when the shape fits."""
        expand = Qt.AspectRatioMode.KeepAspectRatioByExpanding
        for width, height in ((480, 270), (400, 300), (270, 480), (1000, 560)):
            source = landscape(self.root / f"looks-{width}x{height}.png", width, height)
            for motion, zoom in shimmer._MOTION_ZOOM.items():
                with self.subTest(size=(width, height), motion=motion):
                    base = shimmer._load(source, WIDTH, HEIGHT, zoom)
                    wide, high = base.width(), base.height()
                    whole = QImageReader(str(source)).read()
                    whole = whole.scaled(wide, high, expand, Qt.TransformationMode.SmoothTransformation)
                    before = whole.copy((whole.width() - wide) // 2, (whole.height() - high) // 2, wide, high)
                    if (width, height, zoom) == (480, 270, 0.0):
                        self.assertEqual(pixels(base), pixels(before.convertToFormat(QImage.Format.Format_RGB32)))
                    else:
                        self.assertLess(mean_difference(base, before), 1.0)

    def test_gray_and_cmyk_pictures_end_up_in_srgb(self):
        """A picture with a Gray or CMYK profile is converted to sRGB before Qt turns it into RGB
        pixels: afterwards the conversion could only relabel it, and the raw values would show."""
        if not hasattr(QColorSpace, "ColorModel"):
            self.skipTest("colour models came with Qt 6.8")
        srgb = QColorSpace(QColorSpace.NamedColorSpace.SRgb)
        cases = {}
        try:
            linear_gray = QColorSpace(QPointF(0.3127, 0.3290), QColorSpace.TransferFunction.Linear)
        except TypeError:  # Gray colour spaces came with Qt 6.8
            linear_gray = None
        if linear_gray is not None:
            gray = QImage(WIDTH, HEIGHT, QImage.Format.Format_Grayscale8)
            gray.fill(QColor(112, 112, 112))
            gray.setColorSpace(linear_gray)
            gray.save(str(self.root / "gray-linear.png"))
            cases["gray-linear.png"] = QColorSpace.ColorModel.Gray
        profile = Path("/usr/share/ghostscript/iccprofiles/default_cmyk.icc")
        if profile.is_file() and hasattr(QImage.Format, "Format_CMYK8888"):
            cmyk = QImage(WIDTH, HEIGHT, QImage.Format.Format_CMYK8888)
            cmyk.fill(int.from_bytes(bytes([200, 100, 0, 50]), sys.byteorder))  # C, M, Y, K in memory
            cmyk.setColorSpace(QColorSpace.fromIccProfile(profile.read_bytes()))
            cmyk.save(str(self.root / "cmyk.jpg"), "JPEG", 100)
            cases["cmyk.jpg"] = QColorSpace.ColorModel.Cmyk
        p3 = QImage(WIDTH, HEIGHT, QImage.Format.Format_RGB32)
        p3.fill(QColor(200, 60, 40))
        p3.setColorSpace(QColorSpace(QColorSpace.NamedColorSpace.DisplayP3))
        p3.save(str(self.root / "p3.png"))
        cases["p3.png"] = QColorSpace.ColorModel.Rgb
        for name, model in cases.items():
            with self.subTest(picture=name):
                stored = QImageReader(str(self.root / name)).read()
                if stored.colorSpace().colorModel() != model:
                    self.skipTest(f"Qt here does not keep the colour space of {name}")
                expected = stored.convertedToColorSpace(srgb).pixelColor(WIDTH // 2, HEIGHT // 2)
                raw = stored.convertToFormat(QImage.Format.Format_RGB32).pixelColor(WIDTH // 2, HEIGHT // 2)
                self.assertNotEqual(expected.name(), raw.name())  # the profile matters
                for zoom in (0.0, shimmer.ZOOM):
                    base = shimmer._load(self.root / name, WIDTH, HEIGHT, zoom)
                    self.assertEqual(base.pixelColor(base.width() // 2, base.height() // 2).name(), expected.name())

    def test_bad_input(self):
        text = self.root / "notes.png"
        text.write_text("not a picture")
        empty = self.root / "empty.jpg"
        empty.write_bytes(b"")
        target = self.root / "never" / "made.webp"
        cases = [
            {"style": "glitter"},
            {"style": "None"},
            {"style": ""},
            {"style": None},
            {"light": "laser"},
            {"light": "Glint"},
            {"light": ""},
            {"motion": "spin"},
            {"motion": "none"},
            {"density": "extreme"},
            {"density": 2},
            {"density": None},
            {"colour": "pink"},
            {"colour": "#12345"},
            {"colour": "#gggggg"},
            {"colour": "accent"},
            {"size": (0, 180)},
            {"size": (320, -1)},
            {"size": (320,)},
            {"size": (320.0, 180)},
            {"size": (20000, 180)},
            {"fps": 0},
            {"fps": -3},
            {"fps": 2.5},
            {"seconds": 0},
            {"seconds": -1.0},
            {"seconds": float("nan")},
            {"quality": 101},
            {"source": self.root / "missing.png"},
            {"source": text},
            {"source": empty},
            {"source": self.root},
        ]
        for case in cases:
            with self.subTest(case=case):
                options = {"size": (WIDTH, HEIGHT), "seconds": 1.0, "fps": 6}
                options.update({k: v for k, v in case.items() if k not in ("source", "colour", "style")})
                with self.assertRaises(ValueError):
                    shimmer.render(
                        case.get("source", self.source),
                        target,
                        case.get("colour", COLOUR),
                        case.get("style", "sparkle"),
                        **options,
                    )
                self.assertFalse(target.parent.exists())

    def test_no_webp_writer(self):
        target = self.root / "nowebp" / "made.webp"
        with mock.patch.object(shimmer, "available", return_value=False), self.assertRaises(RuntimeError):
            shimmer.render(self.source, target, COLOUR, size=(WIDTH, HEIGHT), seconds=1.0, fps=6)
        self.assertFalse(target.parent.exists())
        with mock.patch.object(shimmer, "available", return_value=False), self.assertRaises(RuntimeError):
            shimmer.preview(self.source, COLOUR, "sparkle")

    def test_preview(self):
        before = sorted(self.root.rglob("*"))
        data = shimmer.preview(self.source, COLOUR, "magic")
        self.assertEqual(sorted(self.root.rglob("*")), before)  # nothing written
        self.assertEqual((data[:4], data[8:12]), (b"RIFF", b"WEBP"))
        self.assertEqual(struct.unpack("<I", data[4:8])[0], len(data) - 8)
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer, b"webp")
        self.assertTrue(reader.supportsAnimation())
        self.assertEqual(reader.imageCount(), 30)  # 3 seconds at 10 frames a second
        self.assertEqual(reader.loopCount(), -1)
        frame = reader.read()
        self.assertEqual((frame.width(), frame.height()), (480, 270))
        buffer.close()

        # What a settings page does with it.
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        movie = QMovie(buffer, b"webp")
        self.assertTrue(movie.isValid())
        self.assertEqual(movie.frameCount(), 30)
        for number in range(30):  # played through, frame by frame
            self.assertTrue(movie.jumpToNextFrame())
            self.assertEqual(movie.currentFrameNumber(), number)
            self.assertEqual(movie.currentImage().size(), frame.size())

        small = shimmer.preview(
            self.source,
            COLOUR,
            "petals",
            light="flare",
            motion="still",
            density="low",
            size=(96, 54),
            seconds=1.0,
            fps=4,
            seed=3,
        )
        self.assertEqual(
            small,
            shimmer.preview(
                self.source,
                COLOUR,
                "petals",
                light="flare",
                motion="still",
                density="low",
                size=(96, 54),
                seconds=1.0,
                fps=4,
                seed=3,
            ),
        )
        buffer = QBuffer()
        buffer.setData(QByteArray(small))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer, b"webp")
        self.assertEqual(reader.imageCount(), 4)
        self.assertEqual(reader.size().width(), 96)

    def test_preview_checks_its_arguments(self):
        text = self.root / "preview-notes.png"
        text.write_text("not a picture")
        cases = [
            {"style": "glitter"},
            {"light": "laser"},
            {"motion": "spin"},
            {"density": "dense"},
            {"colour": "pink"},
            {"size": (0, 270)},
            {"size": (20000, 270)},
            {"seconds": 0},
            {"fps": 0},
            {"source": self.root / "missing.png"},
            {"source": text},
        ]
        for case in cases:
            with self.subTest(case=case):
                options = {k: v for k, v in case.items() if k not in ("source", "colour", "style")}
                with self.assertRaises(ValueError):
                    shimmer.preview(
                        case.get("source", self.source),
                        case.get("colour", COLOUR),
                        case.get("style", "sparkle"),
                        **options,
                    )

    def test_progress_and_a_failure_halfway(self):
        calls = []
        self.render(progress=lambda done, total: calls.append((done, total)))
        self.assertEqual(calls, [(n, 6) for n in range(1, 7)])

        folder = Path(tempfile.mkdtemp(dir=self.root))
        target = folder / "living.webp"
        target.write_bytes(b"the earlier version")

        def stop(done: int, total: int) -> None:
            if done == 3:
                raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            shimmer.render(self.source, target, COLOUR, size=(WIDTH, HEIGHT), seconds=1.0, fps=6, progress=stop)
        self.assertEqual([p.name for p in folder.iterdir()], ["living.webp"])
        self.assertEqual(target.read_bytes(), b"the earlier version")

    def test_command_line_gets_a_hidden_application(self):
        """Without a Qt application (as on the command line) render() makes an offscreen one."""
        target = Path(tempfile.mkdtemp(dir=self.root)) / "living.webp"
        code = (
            "import sys\n"
            "from pathlib import Path\n"
            "from PyQt6.QtCore import QCoreApplication\n"
            "from plasmify import shimmer\n"
            "if QCoreApplication.instance() is not None:\n"
            "    sys.exit('an application already exists')\n"
            "shimmer.render(Path(sys.argv[1]), Path(sys.argv[2]), '#89b4fa', size=(64, 36), seconds=0.5, fps=4)\n"
            "app = QCoreApplication.instance()\n"
            "print(type(app).__name__, app.platformName())\n"
        )
        # No way to reach a real display, whatever happens.
        env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE")}
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(SRC), env.get("PYTHONPATH"))))
        proc = subprocess.run(
            [sys.executable, "-c", code, str(self.source), str(target)],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stdout.split(), ["QGuiApplication", "offscreen"])
        self.assertEqual(QImageReader(str(target)).imageCount(), 2)


if __name__ == "__main__":
    unittest.main()
