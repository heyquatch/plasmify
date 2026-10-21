"""Shimmer: any picture as a living wallpaper.

The result is an animated WebP that Plasma 6's stock Image wallpaper plays (QML AnimatedImage,
which pauses while a maximized window covers the desktop). Every frame is drawn with QPainter and
encoded as a still WebP by Qt; the stills are then joined into one animated file here, in plain
Python, so Shimmer needs nothing beyond PyQt6 (no ffmpeg, img2webp or Pillow).

A frame is made of three independent layers: how the picture moves (``MOTIONS``), a light that
plays over it (``LIGHTS``) and particles or an overlay on top (``STYLES``, with ``DENSITIES`` for
how many particles). The effects favour the edges and corners, so the subject of the picture,
usually in the middle, stays clear.

Everything moves in whole cycles of the loop (and depends only on the seed), so the last frame
flows into the first and the same input always gives the same file.
"""

from __future__ import annotations

import io
import math
import random
import struct
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, NamedTuple

from PyQt6.QtCore import QBuffer, QByteArray, QCoreApplication, QIODevice, QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QColorSpace,
    QConicalGradient,
    QGuiApplication,
    QImage,
    QImageIOHandler,
    QImageReader,
    QImageWriter,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
    QTransform,
)

from plasmify.palette import is_hex, mix

# Particles or an overlay drawn over the picture ("none": only the light and the motion).
STYLES = (
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
)
# A light that plays over the picture, under the particles.
LIGHTS = ("glint", "rays", "flare", "pulse", "none")
# How the picture itself moves.
MOTIONS = ("breathe", "drift", "still")
# How many particles.
DENSITIES = ("low", "normal", "high")
# What render() uses when no light or motion is given: what suits each style best.
DEFAULT_LIGHT = {
    "sparkle": "glint",
    "embers": "pulse",
    "petals": "rays",
    "snow": "rays",
    "fireflies": "none",
    "bokeh": "flare",
    "starfall": "none",
    "rain": "none",
    "magic": "none",
    "speedlines": "none",
    "none": "glint",
}
DEFAULT_MOTION = {
    "sparkle": "breathe",
    "embers": "breathe",
    "petals": "drift",
    "snow": "drift",
    "fireflies": "breathe",
    "bokeh": "drift",
    "starfall": "breathe",
    "rain": "still",
    "magic": "breathe",
    "speedlines": "breathe",
    "none": "drift",
}
# Part of the name of every rendered file (see steps/wallpaper.py): raise it when frames change.
VERSION = 3

# VP8 and VP8L store each side in 14 bits.
MAX_SIDE = 16383
MAX_FPS = 60
MAX_SECONDS = 60.0
REFERENCE_AREA = 1920 * 1080
# The strongest "breathing" zoom, reached halfway through the loop.
ZOOM = 0.035
# "drift" pans around a slightly zoomed picture: the zoom gives the room to pan without an edge.
DRIFT_ZOOM = 0.04
_MOTION_ZOOM = {"breathe": ZOOM, "drift": DRIFT_ZOOM, "still": 0.0}
DENSITY = {"low": 0.55, "normal": 1.0, "high": 1.6}
# Particles at 1080p and normal density; other sizes get as many per area.
PARTICLES = {
    "sparkle": 64,
    "embers": 72,
    "petals": 44,
    "snow": 170,
    "fireflies": 30,
    "bokeh": 18,
    "starfall": 70,  # faint background stars; the shooting stars are counted per second
    "rain": 220,
}
MIN_PARTICLES = 8
MAX_PARTICLES = 1500  # rain and snow at 4K keep their 1080p density
# Lines around the edge, whatever the size.
SPEEDLINES = 110
# The glint's head crosses the picture during this part of the loop and is out of sight otherwise.
GLINT_START, GLINT_END = 0.08, 0.86
PREVIEW_QUALITY = 70

# From the WebP container specification.
_VP8X_ANIMATION = 0x02
_VP8X_ALPHA = 0x10
_ANMF_NO_BLEND = 0x02  # disposal bit 0 stays clear: each whole, opaque frame replaces the last
_RIFF_MAX = 0xFFFFFFF6  # the largest size a WebP file's RIFF header may give

_SCREEN = QPainter.CompositionMode.CompositionMode_Screen
_OVER = QPainter.CompositionMode.CompositionMode_SourceOver
_CLEAR = QColor(0, 0, 0, 0)

# The Qt application made for the command line, kept alive for the rest of the process.
_app: list[QGuiApplication] = []


def available() -> bool:
    """Whether Qt can write WebP pictures here (the image formats plugin is installed)."""
    return b"webp" in (bytes(name) for name in QImageWriter.supportedImageFormats())


class _Job(NamedTuple):
    colour: str
    style: str
    light: str
    motion: str
    density: float
    width: int
    height: int
    frames: int
    fps: int
    quality: int
    seed: object


def render(
    source: Path,
    target: Path,
    colour: str,
    style: str = "sparkle",
    *,
    light: str | None = None,
    motion: str | None = None,
    density: str = "normal",
    size: tuple[int, int] = (1920, 1080),
    seconds: float = 6.0,
    fps: int = 12,
    quality: int = 72,
    seed: int = 0,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Write a looping, living version of ``source`` to ``target`` (an animated WebP) and return
    ``target``. ``colour`` (``#rrggbb``) tints the particles and the light; ``light`` and
    ``motion`` default to what suits ``style`` (DEFAULT_LIGHT, DEFAULT_MOTION); ``progress`` is
    called with (frames done, frames in total) after every frame.

    Raises ValueError for bad arguments or a picture Qt cannot read, RuntimeError when Qt cannot
    write WebP. Nothing but ``target`` is ever left behind.
    """
    job = _job(colour, style, light, motion, density, size, seconds, fps, quality, seed)
    source, target = Path(source), Path(target)
    base = _start(source, job)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    try:
        with partial.open("wb") as out:
            _animate(out, base, job, progress)
        partial.replace(target)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    return target


def preview(
    source: Path,
    colour: str,
    style: str,
    *,
    light: str | None = None,
    motion: str | None = None,
    density: str = "normal",
    size: tuple[int, int] = (480, 270),
    seconds: float = 3.0,
    fps: int = 10,
    seed: int = 0,
) -> bytes:
    """A small animated WebP of ``source`` with these settings, in memory (QMovie plays it from a
    QBuffer): for a settings page. Same arguments and errors as render(); no file is written."""
    job = _job(colour, style, light, motion, density, size, seconds, fps, PREVIEW_QUALITY, seed)
    base = _start(Path(source), job)
    out = io.BytesIO()
    _animate(out, base, job, None)
    return out.getvalue()


def _job(
    colour: str,
    style: str,
    light: str | None,
    motion: str | None,
    density: str,
    size: tuple[int, int],
    seconds: float,
    fps: int,
    quality: int,
    seed: object,
) -> _Job:
    """Every argument checked, before anything is read or written."""
    if style not in STYLES:
        raise ValueError(f"Unknown Shimmer style {style!r}: choose one of {', '.join(STYLES)}")
    if light is not None and light not in LIGHTS:
        raise ValueError(f"Unknown Shimmer light {light!r}: choose one of {', '.join(LIGHTS)}")
    if motion is not None and motion not in MOTIONS:
        raise ValueError(f"Unknown Shimmer motion {motion!r}: choose one of {', '.join(MOTIONS)}")
    if density not in DENSITIES:
        raise ValueError(f"Unknown Shimmer density {density!r}: choose one of {', '.join(DENSITIES)}")
    if not is_hex(colour):
        raise ValueError(f"{colour!r} is not a #rrggbb colour")
    width, height = _check_size(size)
    if isinstance(fps, bool) or not isinstance(fps, int) or not 1 <= fps <= MAX_FPS:
        raise ValueError(f"fps must be a whole number from 1 to {MAX_FPS}")
    if not isinstance(seconds, int | float) or not 0 < seconds <= MAX_SECONDS:
        raise ValueError(f"seconds must be more than 0 and at most {MAX_SECONDS:g}")
    if isinstance(quality, bool) or not isinstance(quality, int) or not 0 <= quality <= 100:
        raise ValueError("quality must be a whole number from 0 to 100")
    return _Job(
        colour=colour,
        style=style,
        light=DEFAULT_LIGHT[style] if light is None else light,
        motion=DEFAULT_MOTION[style] if motion is None else motion,
        density=DENSITY[density],
        width=width,
        height=height,
        frames=max(1, round(seconds * fps)),
        fps=fps,
        quality=quality,
        seed=seed,
    )


def _check_size(size: tuple[int, int]) -> tuple[int, int]:
    try:
        width, height = size
    except (TypeError, ValueError):
        raise ValueError(f"size must be (width, height), not {size!r}") from None
    for side in (width, height):
        if isinstance(side, bool) or not isinstance(side, int) or side <= 0:
            raise ValueError(f"size must be two positive whole numbers, not {size!r}")
        if side > MAX_SIDE:
            raise ValueError(f"WebP pictures are at most {MAX_SIDE} pixels wide and high")
    return width, height


def _start(source: Path, job: _Job) -> QImage:
    """The picture, ready to animate, once Qt is known to write WebP here."""
    if not available():
        raise RuntimeError("Qt cannot write WebP pictures here (the Qt image formats plugin is missing)")
    _ensure_app()
    return _load(source, job.width, job.height, _MOTION_ZOOM[job.motion])


def _animate(out: BinaryIO, base: QImage, job: _Job, progress: Callable[[int, int], None] | None) -> None:
    """Draw, encode and write every frame to ``out``, one frame in memory at a time."""
    width, height, frames = job.width, job.height, job.frames
    seconds = frames / job.fps
    # Separate streams, so the particles stay where they are when only the light changes.
    scene = _SCENES[job.style](random.Random(job.seed), width, height, job.colour, seconds, job.density)  # noqa: S311
    light = _LIGHTS[job.light](random.Random(f"light:{job.seed}"), width, height, job.colour, seconds)  # noqa: S311
    canvas = QImage(width, height, QImage.Format.Format_RGB32)
    webp = _AnimatedWebP(out, width, height)
    for index in range(frames):
        _paint(canvas, base, job.motion, light, scene, index / frames)
        # Rounded on a running clock, so the loop lasts exactly frames / fps seconds.
        duration = round(1000 * (index + 1) / job.fps) - round(1000 * index / job.fps)
        webp.add(_encode(canvas, job.quality), duration)
        if progress is not None:
            progress(index + 1, frames)
    webp.finish()


def _ensure_app() -> None:
    """Qt's image plugins and painting expect an application object. The GUI has one; for the
    command line a hidden one is made, which never opens a window or touches the display."""
    if QCoreApplication.instance() is None:
        _app.append(QGuiApplication(["plasmify", "-platform", "offscreen"]))


def _load(source: Path, width: int, height: int, zoom: float = ZOOM) -> QImage:
    """The picture scaled to cover the canvas at the strongest ``zoom``, cropped around its
    centre, in sRGB, on black (behind any transparent parts)."""
    wide, high = math.ceil(width * (1 + zoom)), math.ceil(height * (1 + zoom))
    reader = QImageReader(str(source))
    reader.setAutoTransform(True)
    if not source.is_file() or not reader.canRead():
        raise ValueError(f"{source.name} is not a picture Qt can read")
    stored = reader.size()
    if stored.isValid() and not stored.isEmpty():
        # Decode big pictures at about twice the size needed: far less memory (JPEG even decodes
        # faster), still plenty for the smooth scale below. The size is in stored orientation.
        turned = bool(reader.transformation() & QImageIOHandler.Transformation.TransformationRotate90)
        shown_wide, shown_high = (stored.height(), stored.width()) if turned else (stored.width(), stored.height())
        shrink = max(wide / shown_wide, high / shown_high)
        if shrink < 0.5:
            reader.setScaledSize(QSize(math.ceil(stored.width() * 2 * shrink), math.ceil(stored.height() * 2 * shrink)))
    image = reader.read()
    if image.isNull():
        raise ValueError(f"{source.name} could not be read: {reader.errorString()}")
    # Crop first, in the picture's own pixels, and scale only that part: scaling the whole picture
    # to cover the canvas would make a huge image of a thin strip (8192x1 grows to gigabytes).
    scale = max(wide / image.width(), high / image.height())
    part_wide = min(image.width(), max(1, round(wide / scale)))
    part_high = min(image.height(), max(1, round(high / scale)))
    if (part_wide, part_high) != (image.width(), image.height()):  # copy() always makes a new image
        image = image.copy((image.width() - part_wide) // 2, (image.height() - part_high) // 2, part_wide, part_high)
    # Before any change of format: a Gray or CMYK picture turned into RGB pixels keeps its colour
    # space, and converting that to sRGB would only relabel it.
    srgb = QColorSpace(QColorSpace.NamedColorSpace.SRgb)
    if image.colorSpace().isValid() and image.colorSpace() != srgb:
        image = image.convertedToColorSpace(srgb)
    image = image.scaled(wide, high, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    image = image.convertToFormat(QImage.Format.Format_ARGB32)
    # A canvas without a colour space: the frames carry no ICC profile, and they are sRGB now.
    base = QImage(wide, high, QImage.Format.Format_RGB32)
    base.fill(Qt.GlobalColor.black)
    painter = QPainter(base)
    painter.drawImage(0, 0, image)
    painter.end()
    return base


def _shown(motion: str, width: int, height: int, t: float) -> QRectF:
    """Where the whole picture (``base``) is drawn at time ``t``: always over the full canvas."""
    if motion == "breathe":
        zoom = 1 + ZOOM * (1 - math.cos(math.tau * t)) / 2
        dx = dy = 0.0
    elif motion == "drift":
        # A slow circle around the middle, the zoom swelling a little on the way.
        zoom = 1 + DRIFT_ZOOM * (0.75 + 0.25 * (1 - math.cos(math.tau * t)) / 2)
        room = 0.8 * DRIFT_ZOOM * 0.75 / 2  # of each side, inside the smallest zoom
        dx = room * width * math.cos(math.tau * t)
        dy = room * height * math.sin(math.tau * t)
    else:
        zoom, dx, dy = 1.0, 0.0, 0.0
    return QRectF((width - width * zoom) / 2 + dx, (height - height * zoom) / 2 + dy, width * zoom, height * zoom)


def _paint(canvas: QImage, base: QImage, motion: str, light: _Light, scene: _Scene, t: float) -> None:
    """Frame ``t`` (0 to 1 over the loop): the moving picture, the light, the particles."""
    width, height = canvas.width(), canvas.height()
    painter = QPainter(canvas)
    try:
        painter.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(_shown(motion, width, height, t), base, QRectF(base.rect()))
        painter.setPen(Qt.PenStyle.NoPen)
        for layer in (light, scene):
            painter.save()
            layer.draw(painter, t)
            painter.restore()
    finally:
        painter.end()


def _encode(image: QImage, quality: int) -> bytes:
    """One frame as a still WebP file, in memory."""
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QImageWriter(buffer, b"webp")
    writer.setQuality(quality)
    if not writer.write(image):
        raise RuntimeError(f"Qt could not encode a WebP frame: {writer.errorString()}")
    buffer.close()
    return data.data()


# Helpers


def _unit(width: int, height: int) -> float:
    """How much larger than at 1080p things are drawn."""
    return math.sqrt(width * height / REFERENCE_AREA)


def _count(style: str, width: int, height: int, density: float = 1.0) -> int:
    wanted = round(PARTICLES[style] * density * width * height / REFERENCE_AREA)
    return max(MIN_PARTICLES, min(MAX_PARTICLES, wanted))


def _alpha(colour: QColor, alpha: float) -> QColor:
    faded = QColor(colour)
    faded.setAlphaF(max(0.0, min(1.0, alpha)))
    return faded


def _smoothstep(low: float, high: float, value: float) -> float:
    u = max(0.0, min(1.0, (value - low) / (high - low)))
    return u * u * (3 - 2 * u)


def _wave(t: float) -> float:
    """0 at the start of the loop, 1 halfway, 0 again at the end: smoothly."""
    return (1 - math.cos(math.tau * t)) / 2


def _edge(x: float, y: float, width: int, height: int, centre: float) -> float:
    """``centre`` in the middle of the picture, where its subject usually is, rising smoothly
    to 1 towards the edges and corners."""
    reach = math.hypot(2 * x / width - 1, 2 * y / height - 1)
    return centre + (1 - centre) * _smoothstep(0.3, 0.95, reach)


def _spread(rng: random.Random, count: int, width: int, height: int) -> list[tuple[float, float]]:
    """``count`` points spread evenly but irregularly: one in each of some random grid cells."""
    columns = max(1, round(math.sqrt(count * width / height)))
    rows = math.ceil(count / columns)
    cell_wide, cell_high = width / columns, height / rows
    return [
        ((cell % columns + rng.random()) * cell_wide, (cell // columns + rng.random()) * cell_high)
        for cell in rng.sample(range(columns * rows), count)
    ]


def _scatter(rng: random.Random, count: int, width: int, height: int, centre: float) -> list[tuple[float, float]]:
    """Like _spread, but a point in the middle of the picture is kept only ``centre`` times as
    often as one near an edge (a weighted draw from three times as many points)."""
    pool = _spread(rng, count * 3, width, height)
    keys = [rng.random() ** (1 / _edge(x, y, width, height, centre)) for x, y in pool]
    order = sorted(range(len(pool)), key=keys.__getitem__, reverse=True)
    return [pool[index] for index in order[:count]]


def _side(rng: random.Random, low: float, high: float, centre: float) -> float:
    """A fraction from ``low`` to ``high`` across the picture, rarer in the middle third."""
    while True:
        x = rng.uniform(low, high)
        if rng.random() < centre + (1 - centre) * _smoothstep(0.15, 0.7, abs(2 * x - 1)):
            return x


def _disc(painter: QPainter, centre: QPointF, radius: float, stops: list[tuple[float, QColor]]) -> None:
    """A circle filled with a radial gradient: ``stops`` from the centre (0) to the rim (1)."""
    gradient = QRadialGradient(centre, radius)
    gradient.setStops(stops)
    painter.setBrush(QBrush(gradient))
    painter.drawEllipse(centre, radius, radius)


def _star_path() -> QPainterPath:
    """A four-point star with slim, slightly curved points, 2 high and 1.5 wide."""
    path = QPainterPath(QPointF(0, -1))
    for bend, tip in (
        ((0.09, -0.09), (0.75, 0)),
        ((0.09, 0.09), (0, 1)),
        ((-0.09, 0.09), (-0.75, 0)),
        ((-0.09, -0.09), (0, -1)),
    ):
        path.quadTo(QPointF(*bend), QPointF(*tip))
    return path


def _spikes(
    painter: QPainter, centre: QPointF, length: float, thick: float, tilt: float, core: QColor, alpha: float
) -> None:
    """Two thin crossing streaks of light, like a star seen through a lens."""
    for turn in (0, 90):
        painter.save()
        painter.translate(centre)
        painter.rotate(tilt + turn)
        painter.scale(length, thick)
        gradient = QRadialGradient(QPointF(0, 0), 1)
        gradient.setColorAt(0, _alpha(core, alpha))
        gradient.setColorAt(0.3, _alpha(core, 0.35 * alpha))
        gradient.setColorAt(1, _alpha(core, 0))
        painter.setBrush(QBrush(gradient))
        painter.drawEllipse(QPointF(0, 0), 1, 1)
        painter.restore()


def _trail(colour: QColor, centre: QPointF, head: float, span: float, lead: float, strength: float) -> QConicalGradient:
    """A brush for an arc trailing behind a head at angle ``head`` (degrees, counter-clockwise):
    brightest just behind the head, gone ``span`` degrees later, with a short ``lead`` before it.
    The trail lies on the counter-clockwise side, so the head moves clockwise."""
    gradient = QConicalGradient(centre, head)
    behind, ahead = span / 360, lead / 360
    gradient.setColorAt(0, _alpha(colour, 0.7 * strength))
    gradient.setColorAt(0.06 * behind, _alpha(colour, strength))
    gradient.setColorAt(0.35 * behind, _alpha(colour, 0.45 * strength))
    gradient.setColorAt(behind, _alpha(colour, 0))
    gradient.setColorAt(1 - ahead, _alpha(colour, 0))
    gradient.setColorAt(1, _alpha(colour, 0.7 * strength))
    return gradient


def _arc(box: QRectF, start: float, span: float) -> QPainterPath:
    path = QPainterPath()
    path.arcMoveTo(box, start)
    path.arcTo(box, start, span)
    return path


# Lights


class _Light:
    """A light played over the picture (under the particles), the same at the same time of every
    loop. The plain one is "none"."""

    def __init__(self, rng: random.Random, width: int, height: int, colour: str, seconds: float) -> None:
        self.width, self.height = width, height

    def draw(self, painter: QPainter, t: float) -> None:
        pass


class _Glint(_Light):
    """A curved shine: a small star-like head sweeps along a wide arc over the upper part of the
    picture, trailing feathered light that fades behind it. It crosses once per loop."""

    def __init__(self, rng: random.Random, width: int, height: int, colour: str, seconds: float) -> None:
        super().__init__(rng, width, height, colour, seconds)
        self.tint = QColor(mix(colour, "#ffffff", 0.7))
        self.white = QColor(mix(colour, "#ffffff", 0.94))
        self.mirror = rng.random() < 0.5  # right to left instead
        # Part of a large circle whose middle lies far below the picture: the arc rises from one
        # side, passes high over the picture and falls towards the other side.
        radius = height * rng.uniform(1.15, 1.45)
        cx = width * rng.uniform(0.45, 0.62)
        cy = height * rng.uniform(0.1, 0.22) + radius
        self.centre, self.box = QPointF(cx, cy), QRectF(cx - radius, cy - radius, 2 * radius, 2 * radius)
        self.radius = radius
        margin = 0.1 * height

        def angle(value: float, inverse: Callable[[float], float]) -> float:
            return math.degrees(inverse(max(-1.0, min(1.0, value))))

        low = angle((cy - height - margin) / radius, math.asin)  # below this the arc is under the picture
        right = max(angle((width + margin - cx) / radius, math.acos), low)
        left = min(angle((-margin - cx) / radius, math.acos), 180 - low)
        self.span = math.degrees(0.42 * width / radius)
        # The head enters on the left and goes on until its whole trail has left on the right.
        self.begin, self.end = left, right - self.span

    def draw(self, painter: QPainter, t: float) -> None:
        u = (t - GLINT_START) / (GLINT_END - GLINT_START)
        if not 0 < u < 1:
            return
        u = u * u * (3 - 2 * u)  # eases in and out
        head = self.begin + (self.end - self.begin) * u
        painter.setCompositionMode(_SCREEN)
        if self.mirror:
            painter.translate(self.width, 0)
            painter.scale(-1, 1)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        height = self.height
        # Wide and faint along the whole trail, narrow and bright near the head: feathered.
        for length, thick, strength in ((1.0, 0.16, 0.08), (0.7, 0.05, 0.2), (0.4, 0.012, 0.6)):
            span = self.span * length
            lead = math.degrees(0.4 * thick * height / self.radius)
            brush = QBrush(_trail(self.tint, self.centre, head, span, lead, strength))
            painter.setPen(QPen(brush, thick * height, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            painter.drawPath(_arc(self.box, head - lead, span + lead))
        painter.setPen(Qt.PenStyle.NoPen)
        rad = math.radians(head)
        tip = QPointF(self.centre.x() + self.radius * math.cos(rad), self.centre.y() - self.radius * math.sin(rad))
        _disc(painter, tip, 0.075 * height, [(0, _alpha(self.tint, 0.4)), (0.4, _alpha(self.tint, 0.12)), (1, _CLEAR)])
        _spikes(painter, tip, 0.07 * height, 0.0035 * height, 90 - head + 12, self.white, 0.9)
        _disc(painter, tip, 0.012 * height, [(0, _alpha(self.white, 1)), (0.5, _alpha(self.white, 0.6)), (1, _CLEAR)])


class _Beam(NamedTuple):
    angle: float  # degrees, counter-clockwise from pointing right
    spread: float  # degrees
    strength: float
    beats: int  # brightenings per loop
    phase: float
    wander: float  # degrees


class _Rays(_Light):
    """Soft god rays fanning from just outside a top corner, swaying slowly and breathing. Drawn
    small and smoothly enlarged (they are soft anyway), then screened over the picture."""

    SHRINK = 4

    def __init__(self, rng: random.Random, width: int, height: int, colour: str, seconds: float) -> None:
        super().__init__(rng, width, height, colour, seconds)
        shrink = self.SHRINK
        self.layer = QImage(
            max(1, math.ceil(width / shrink)),
            max(1, math.ceil(height / shrink)),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        self.mirror = rng.random() < 0.5  # from the top right instead
        ox, oy = -0.05 * width, -0.12 * height
        self.origin = QPointF(ox / shrink, oy / shrink)
        self.reach = 0.95 * math.hypot(width, height) / shrink
        self.light = QColor(mix(colour, "#fff4e0", 0.6))
        # The fan stays between the far corners of the picture as seen from the light.
        first = math.degrees(math.atan2(oy, width - ox))
        last = math.degrees(math.atan2(-(height - oy), -ox))
        count = rng.randint(6, 9)
        self.beams = [
            _Beam(
                angle=first + (last - first) * (index + rng.uniform(0.25, 0.75)) / count,
                spread=rng.uniform(2.0, 5.5),
                strength=rng.uniform(0.45, 1.0),
                beats=rng.choice((1, 1, 2)),
                phase=rng.random(),
                wander=rng.uniform(0.4, 1.4),
            )
            for index in range(count)
        ]
        self.middle, self.sway = (first + last) / 2, rng.uniform(1.8, 3.0)
        self.angles = [last - 14 + 0.5 * step for step in range(round((first - last + 28) / 0.5) + 1)]

    def draw(self, painter: QPainter, t: float) -> None:
        swing = self.sway * math.sin(math.tau * t)
        breath = 0.85 + 0.15 * math.cos(math.tau * t)
        beams = [
            (
                beam.angle + swing + beam.wander * math.sin(math.tau * (t + beam.phase)),
                beam.spread,
                beam.strength * (0.55 + 0.45 * (0.5 + 0.5 * math.cos(math.tau * (beam.beats * t + beam.phase)))),
            )
            for beam in self.beams
        ]
        # The gradient starts pointing up, away from the picture, so its seam never shows.
        stops = [(0.0, _CLEAR)]
        for angle in self.angles:
            value = 0.14 * math.exp(-(((angle - self.middle - swing) / 38) ** 2))
            for centre, spread, strength in beams:
                value += strength * math.exp(-(((angle - centre) / spread) ** 2))
            stops.append((((angle - 90) % 360) / 360, _alpha(self.light, 0.45 * breath * min(1.0, value))))
        stops.append((1.0, _CLEAR))
        self.layer.fill(Qt.GlobalColor.transparent)
        layer = QPainter(self.layer)
        try:
            layer.setPen(Qt.PenStyle.NoPen)
            area = QRectF(self.layer.rect())
            fan = QConicalGradient(self.origin, 90)
            fan.setStops(stops)
            layer.fillRect(area, QBrush(fan))
            layer.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
            fade = QRadialGradient(self.origin, self.reach)
            for stop, alpha in ((0, 1.0), (0.25, 0.62), (0.6, 0.2), (1, 0.0)):
                fade.setColorAt(stop, QColor(0, 0, 0, round(255 * alpha)))
            layer.fillRect(area, QBrush(fade))
            layer.setCompositionMode(_OVER)
            sun = QRadialGradient(self.origin, 0.5 * self.layer.height())
            sun.setColorAt(0, _alpha(self.light, 0.55 * breath))
            sun.setColorAt(0.4, _alpha(self.light, 0.16 * breath))
            sun.setColorAt(1, _CLEAR)
            layer.fillRect(area, QBrush(sun))
        finally:
            layer.end()
        painter.setCompositionMode(_SCREEN)
        if self.mirror:
            painter.translate(self.width, 0)
            painter.scale(-1, 1)
        painter.drawImage(QRectF(0, 0, self.width, self.height), self.layer, QRectF(self.layer.rect()))


class _Ghost(NamedTuple):
    along: float  # on the line from the light through the middle: 0 at the light, 1 in the middle
    radius: float
    alpha: float
    colour: QColor
    ring: bool


class _Flare(_Light):
    """An anamorphic lens flare: a bright core high in the picture with a long horizontal streak,
    and faint ghosts on the line through the middle, all drifting slowly."""

    def __init__(self, rng: random.Random, width: int, height: int, colour: str, seconds: float) -> None:
        super().__init__(rng, width, height, colour, seconds)
        side = rng.choice((-1, 1))
        self.anchor = (width * (0.5 + side * rng.uniform(0.2, 0.3)), height * rng.uniform(0.15, 0.27))
        self.drift = (0.03 * width, 0.02 * height)
        self.turn, self.phase = side, rng.random()
        self.white = QColor(mix(colour, "#ffffff", 0.9))
        self.tint = QColor(mix(colour, "#ffffff", 0.45))
        self.colour = QColor(colour)
        self.ghosts = [
            _Ghost(
                along=along + rng.uniform(-0.08, 0.08),
                radius=height * rng.uniform(0.015, 0.085),
                alpha=rng.uniform(0.05, 0.11),
                colour=QColor(mix(colour, "#ffffff", rng.uniform(0.1, 0.6))),
                ring=rng.random() < 0.35,
            )
            for along in (0.45, 0.8, 1.3, 1.65, 2.05)
        ]

    def draw(self, painter: QPainter, t: float) -> None:
        width, height = self.width, self.height
        angle = math.tau * (self.turn * t + self.phase)
        x = self.anchor[0] + self.drift[0] * math.cos(angle)
        y = self.anchor[1] + self.drift[1] * math.sin(angle)
        core = QPointF(x, y)
        glow = 0.82 + 0.18 * math.cos(math.tau * (2 * t + self.phase))
        painter.setCompositionMode(_SCREEN)
        _disc(
            painter,
            core,
            0.26 * height,
            [
                (0, _alpha(self.tint, 0.32 * glow)),
                (0.2, _alpha(self.tint, 0.13 * glow)),
                (0.55, _alpha(self.colour, 0.04 * glow)),
                (1, _CLEAR),
            ],
        )
        # The streak: a wide faint band and a thin bright line through the core.
        for long, thick, stops in (
            (0.62, 0.05, [(0, _alpha(self.tint, 0.2 * glow)), (0.5, _alpha(self.colour, 0.05 * glow)), (1, _CLEAR)]),
            (
                0.5,
                0.008,
                [
                    (0, _alpha(self.white, 0.9 * glow)),
                    (0.15, _alpha(self.tint, 0.45 * glow)),
                    (0.6, _alpha(self.colour, 0.12 * glow)),
                    (1, _CLEAR),
                ],
            ),
        ):
            painter.save()
            painter.translate(core)
            painter.scale(long * width, thick * height)
            _disc(painter, QPointF(0, 0), 1, stops)
            painter.restore()
        _disc(
            painter,
            core,
            0.035 * height,
            [(0, _alpha(self.white, 0.95)), (0.35, _alpha(self.white, 0.55)), (1, _CLEAR)],
        )
        middle = QPointF(width / 2, height / 2)
        for ghost in self.ghosts:
            centre = core + (middle - core) * ghost.along
            alpha = ghost.alpha * glow
            if ghost.ring:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(_alpha(ghost.colour, alpha * 1.4), max(1.0, 0.003 * height)))
                painter.drawEllipse(centre, ghost.radius, ghost.radius)
                painter.setPen(Qt.PenStyle.NoPen)
            else:
                _disc(
                    painter,
                    centre,
                    ghost.radius,
                    [(0, _alpha(ghost.colour, 0.5 * alpha)), (0.8, _alpha(ghost.colour, alpha)), (1, _CLEAR)],
                )


class _Pulse(_Light):
    """A soft glow in the colour that breathes in from the edges, like a vignette lighting up and
    fading again, creeping a little further in as it brightens."""

    def __init__(self, rng: random.Random, width: int, height: int, colour: str, seconds: float) -> None:
        super().__init__(rng, width, height, colour, seconds)
        self.colour = QColor(mix(colour, "#ffffff", 0.2))
        self.breaths = max(1, round(seconds / 6))

    def draw(self, painter: QPainter, t: float) -> None:
        level = _wave(self.breaths * t)
        level = level * level * (3 - 2 * level)  # lingers when bright and when dim
        strength = 0.05 + 0.22 * level
        reach = 0.82 - 0.14 * level
        corner = math.sqrt(2)
        glow = QRadialGradient(QPointF(0, 0), corner)
        glow.setColorAt(0, _CLEAR)
        glow.setColorAt(reach / corner, _alpha(self.colour, 0))
        glow.setColorAt((reach + 1) / 2 / corner, _alpha(self.colour, 0.22 * strength))
        glow.setColorAt(1 / corner, _alpha(self.colour, 0.6 * strength))
        glow.setColorAt(1, _alpha(self.colour, strength))
        painter.setCompositionMode(_SCREEN)
        painter.translate(self.width / 2, self.height / 2)
        painter.scale(self.width / 2, self.height / 2)
        painter.setBrush(QBrush(glow))
        painter.drawRect(QRectF(-1, -1, 2, 2))


_LIGHTS: dict[str, Callable[[random.Random, int, int, str, float], _Light]] = {
    "glint": _Glint,
    "rays": _Rays,
    "flare": _Flare,
    "pulse": _Pulse,
    "none": _Light,
}


# Particles


class _Scene:
    """Particles or an overlay drawn over the picture, the same way at the same time of every
    loop. The plain one is "none"."""

    population = 0  # particles (or lines, or sparks) in the scene

    def __init__(
        self, rng: random.Random, width: int, height: int, colour: str, seconds: float, density: float = 1.0
    ) -> None:
        self.width, self.height = width, height

    def draw(self, painter: QPainter, t: float) -> None:
        pass


class _Star(NamedTuple):
    x: float
    y: float
    orbit_x: float
    orbit_y: float
    orbit_phase: float
    turn: int  # +1 or -1: which way it circles, once per loop
    size: float
    twinkles: int  # per loop
    twinkle_phase: float
    tilt: float  # degrees
    spikes: bool  # a larger star: crossing streaks of light too


class _Sparkles(_Scene):
    """Four-point stars with a white core and a soft halo that twinkle on slow orbits: mostly small,
    now and then a larger one with crossing streaks. Fewer in the middle of the picture."""

    def __init__(
        self, rng: random.Random, width: int, height: int, colour: str, seconds: float, density: float = 1.0
    ) -> None:
        super().__init__(rng, width, height, colour, seconds, density)
        unit = _unit(width, height)
        self.colour, self.white, self.path = QColor(colour), QColor("#ffffff"), _star_path()
        self.soft = QColor(mix(colour, "#ffffff", 0.6))
        self.stars = []
        for x, y in _scatter(rng, _count("sparkle", width, height, density), width, height, 0.3):
            big = rng.random() ** 2.6
            self.stars.append(
                _Star(
                    x,
                    y,
                    orbit_x=rng.uniform(5, 18) * unit,
                    orbit_y=rng.uniform(4, 12) * unit,
                    orbit_phase=rng.random(),
                    turn=rng.choice((-1, 1)),
                    size=(3.8 + 12 * big) * unit,
                    twinkles=max(1, round(seconds * rng.uniform(0.15, 0.45))),
                    twinkle_phase=rng.random(),
                    tilt=rng.uniform(-20, 20),
                    spikes=big > 0.35,
                )
            )
        self.population = len(self.stars)

    def draw(self, painter: QPainter, t: float) -> None:
        painter.setCompositionMode(_SCREEN)
        for star in self.stars:
            angle = math.tau * (star.turn * t + star.orbit_phase)
            centre = QPointF(star.x + star.orbit_x * math.cos(angle), star.y + star.orbit_y * math.sin(angle))
            glow = (0.5 + 0.5 * math.cos(math.tau * (star.twinkles * t + star.twinkle_phase))) ** 2
            bright = 0.3 + 0.7 * glow  # short bright peaks, never quite gone
            radius = star.size * (0.55 + 0.45 * glow)
            _disc(painter, centre, radius * 2.8, [(0, _alpha(self.colour, 0.45 * bright)), (1, _alpha(self.colour, 0))])
            if star.spikes:
                _spikes(painter, centre, radius * 3.4, radius * 0.1, star.tilt, self.soft, 0.85 * glow)

            painter.save()
            painter.translate(centre)
            painter.rotate(star.tilt + 12 * glow)  # turns a little as it flares
            painter.scale(radius, radius)
            body = QRadialGradient(QPointF(0, 0), 1)
            body.setColorAt(0, _alpha(self.white, bright))
            body.setColorAt(0.3, _alpha(self.soft, 0.95 * bright))
            body.setColorAt(1, _alpha(self.colour, 0.2 * bright))
            painter.setBrush(QBrush(body))
            painter.drawPath(self.path)
            _disc(painter, QPointF(0, 0), 0.3, [(0, _alpha(self.white, bright)), (1, _alpha(self.white, 0))])
            painter.restore()


# Embers, petals, snow and rain travel along lanes: straight paths from out of sight to out of
# sight (or to where they fade away) with a gentle sideways sway. A lane holds ``count`` evenly
# spaced particles that move ``laps`` spacings per loop, so after a loop each particle stands where
# another one started. How a particle looks depends only on its place in the lane and the time of
# the loop, so the last frame flows into the first although every particle crosses the screen.


class _Lane(NamedTuple):
    x: float  # where the lane starts
    y: float
    dx: float  # unit direction of travel
    dy: float
    length: float
    count: int
    start: float
    sway: float
    wave: float  # sway wavelength, in pixels travelled
    phase: float
    laps: int = 1  # spacings travelled per loop

    def spots(self, t: float) -> list[tuple[float, float, float]]:
        """(distance travelled, x, y) of every particle in the lane at time ``t``."""
        spots = []
        for index in range(self.count):
            along = (self.start + (index + self.laps * t) * self.length / self.count) % self.length
            side = self.sway * math.sin(math.tau * along / self.wave + self.phase)
            spots.append((along, self.x + self.dx * along - self.dy * side, self.y + self.dy * along + self.dx * side))
        return spots


def _lane_count(length: float, speed: float, seconds: float, most: int) -> int:
    """Particles in a lane for each to move at about ``speed`` pixels per second."""
    return max(1, min(most, round(length / (speed * seconds))))


class _Ember(NamedTuple):
    lane: _Lane
    size: float
    flickers: int  # per loop
    flicker_wave: float  # in pixels travelled, so neighbours flicker out of step


class _Embers(_Scene):
    """Small glowing dots that rise, sway and flicker, dimming as they cool on the way up; more of
    them towards the sides."""

    def __init__(
        self, rng: random.Random, width: int, height: int, colour: str, seconds: float, density: float = 1.0
    ) -> None:
        super().__init__(rng, width, height, colour, seconds, density)
        unit = _unit(width, height)
        self.colour, self.hot = QColor(colour), QColor(mix(colour, "#fff4e0", 0.6))
        margin = 30 * unit  # below the bottom edge, where every ember starts out of sight
        self.embers: list[_Ember] = []
        wanted, placed = _count("embers", width, height, density), 0
        while placed < wanted:
            length = rng.uniform(0.4, 1.05) * height + margin
            lean = rng.uniform(-0.18, 0.18)
            lane = _Lane(
                x=_side(rng, -0.03, 1.03, 0.45) * width,
                y=height + margin,
                dx=math.sin(lean),
                dy=-math.cos(lean),
                length=length,
                count=_lane_count(length, rng.uniform(40, 75) * unit, seconds, 5),
                start=rng.uniform(0, length),
                sway=rng.uniform(4, 16) * unit,
                wave=rng.uniform(140, 320) * unit,
                phase=rng.uniform(0, math.tau),
            )
            self.embers.append(
                _Ember(
                    lane,
                    size=(2.2 + 3.0 * rng.random() ** 1.5) * unit,
                    flickers=max(1, round(seconds * rng.uniform(1.0, 2.0))),
                    flicker_wave=rng.uniform(90, 220) * unit,
                )
            )
            placed += lane.count
        self.population = placed

    def draw(self, painter: QPainter, t: float) -> None:
        painter.setCompositionMode(_SCREEN)
        for ember in self.embers:
            for along, x, y in ember.lane.spots(t):
                rise = along / ember.lane.length
                flicker = 0.7 + 0.3 * math.sin(math.tau * (ember.flickers * t + along / ember.flicker_wave))
                strength = (1 - rise**1.6) * flicker  # gone by the time it jumps back to the start
                radius = ember.size * (1 - 0.4 * rise)
                centre = QPointF(x, y)
                painter.save()
                painter.translate(centre)
                painter.rotate(math.degrees(math.atan2(ember.lane.dx, -ember.lane.dy)))
                painter.scale(1, 1.7)  # stretched along the way it rises, like a spark
                _disc(
                    painter,
                    QPointF(0, 0),
                    radius * 5,
                    [
                        (0, _alpha(self.colour, 0.7 * strength)),
                        (0.4, _alpha(self.colour, 0.18 * strength)),
                        (1, _alpha(self.colour, 0)),
                    ],
                )
                painter.restore()
                _disc(
                    painter,
                    centre,
                    radius,
                    [
                        (0, _alpha(self.hot, strength)),
                        (0.6, _alpha(self.colour, 0.7 * strength)),
                        (1, _alpha(self.colour, 0)),
                    ],
                )


def _petal_path() -> QPainterPath:
    """A cherry blossom petal 2 long and about 1.3 wide: narrow at its base (y = 1), widest above
    the middle and with a small notch in its rounded tip (y = -1)."""
    path = QPainterPath(QPointF(0, 1))
    path.cubicTo(QPointF(-0.45, 0.8), QPointF(-0.8, -0.3), QPointF(-0.42, -0.92))
    path.quadTo(QPointF(-0.2, -1.08), QPointF(0, -0.76))
    path.quadTo(QPointF(0.2, -1.08), QPointF(0.42, -0.92))
    path.cubicTo(QPointF(0.8, -0.3), QPointF(0.45, 0.8), QPointF(0, 1))
    return path


class _Petal(NamedTuple):
    lane: _Lane
    size: float
    opacity: float
    spin: float  # degrees per pixel travelled
    turn: float  # starting angle
    flip: float  # wavelength of turning over, in pixels travelled
    roll: float  # wavelength of tipping forward and back
    base: QColor  # the colour at the petal's base
    tip: QColor  # soft pink towards the tip


class _Petals(_Scene):
    """Sakura petals tumbling down on a diagonal breeze: notched, soft pink fading into the colour
    at the base, turning over as they fall. Nearer ones are larger and faster."""

    def __init__(
        self, rng: random.Random, width: int, height: int, colour: str, seconds: float, density: float = 1.0
    ) -> None:
        super().__init__(rng, width, height, colour, seconds, density)
        unit = _unit(width, height)
        margin = 48 * unit  # above the top and below the bottom edge, out of sight
        breeze = rng.uniform(0.35, 0.6) * rng.choice((-1, 1))
        self.path = _petal_path()
        self.petals: list[_Petal] = []
        wanted, placed = _count("petals", width, height, density), 0
        while placed < wanted:
            lean = breeze + rng.uniform(-0.12, 0.12)
            dx, dy = math.sin(lean), math.cos(lean)
            length = (height + 2 * margin) / dy
            drift = dx * length
            near = rng.random()
            lane = _Lane(
                x=rng.uniform(min(0.0, -drift), max(float(width), width - drift)),
                y=-margin,
                dx=dx,
                dy=dy,
                length=length,
                count=_lane_count(length, (50 + 45 * near) * unit, seconds, 6),
                start=rng.uniform(0, length),
                sway=rng.uniform(10, 28) * unit,
                wave=rng.uniform(220, 460) * unit,
                phase=rng.uniform(0, math.tau),
            )
            pink = mix("#ffb3cc", "#ffffff", rng.uniform(0.05, 0.3))
            self.petals.append(
                _Petal(
                    lane,
                    size=(12 + 14 * near) * unit,
                    opacity=0.6 + 0.35 * near,
                    spin=rng.choice((-1, 1)) * 360 / (rng.uniform(260, 520) * unit),
                    turn=rng.uniform(0, 360),
                    flip=rng.uniform(170, 340) * unit,
                    roll=rng.uniform(240, 480) * unit,
                    base=QColor(mix(colour, pink, rng.uniform(0.0, 0.3))),
                    tip=QColor(pink),
                )
            )
            placed += lane.count
        self.population = placed

    def draw(self, painter: QPainter, t: float) -> None:
        for petal in self.petals:
            for along, x, y in petal.lane.spots(t):
                # Turning over shows the petal edge on: it narrows, then widens again (the back,
                # a little darker, shows in between); tipping forward shortens it.
                facing = math.cos(math.tau * along / petal.flip + petal.lane.phase)
                tipping = 0.7 + 0.3 * abs(math.cos(math.tau * along / petal.roll + 2 * petal.lane.phase))
                shade = 1.0 if facing >= 0 else 0.8
                painter.save()
                painter.translate(x, y)
                painter.rotate(petal.turn + petal.spin * along)
                painter.scale(petal.size * (0.25 + 0.75 * abs(facing)) * 0.68, petal.size * tipping)
                body = QLinearGradient(QPointF(0, 1), QPointF(0, -1))
                body.setColorAt(0, _alpha(petal.base, 0.95 * petal.opacity * shade))
                body.setColorAt(0.55, _alpha(petal.tip, 0.9 * petal.opacity * shade))
                body.setColorAt(1, _alpha(petal.tip, 0.75 * petal.opacity * shade))
                painter.setBrush(QBrush(body))
                painter.drawPath(self.path)
                painter.restore()


class _Flake(NamedTuple):
    lane: _Lane
    radius: float
    alpha: float
    near: float  # 0 far away (small, sharp, slow) to 1 close by (large, soft, fast)


class _Snow(_Scene):
    """Soft snowflakes falling with depth: far ones small, sharp and slow, near ones larger,
    blurred and faster, and fainter over the middle of the picture."""

    def __init__(
        self, rng: random.Random, width: int, height: int, colour: str, seconds: float, density: float = 1.0
    ) -> None:
        super().__init__(rng, width, height, colour, seconds, density)
        unit = _unit(width, height)
        margin = 40 * unit
        wind = rng.uniform(0.05, 0.28) * rng.choice((-1, 1))
        self.colour = QColor(mix(colour, "#ffffff", 0.82))
        self.flakes: list[_Flake] = []
        wanted, placed = _count("snow", width, height, density), 0
        while placed < wanted:
            near = rng.random() ** 1.6  # most are far away
            lean = wind * (0.6 + 0.8 * near) + rng.uniform(-0.05, 0.05)
            dx, dy = math.sin(lean), math.cos(lean)
            length = (height + 2 * margin) / dy
            drift = dx * length
            lane = _Lane(
                x=rng.uniform(min(0.0, -drift), max(float(width), width - drift)),
                y=-margin,
                dx=dx,
                dy=dy,
                length=length,
                count=_lane_count(length, (30 + 120 * near) * unit, seconds, 4),
                start=rng.uniform(0, length),
                sway=(4 + 14 * near) * unit,
                wave=(160 + 240 * near) * unit,
                phase=rng.uniform(0, math.tau),
            )
            self.flakes.append(_Flake(lane, radius=(2.3 + 7 * near**1.3) * unit, alpha=0.9 - 0.4 * near, near=near))
            placed += lane.count
        self.flakes.sort(key=lambda flake: flake.near)  # the far ones first, behind
        self.population = placed

    def draw(self, painter: QPainter, t: float) -> None:
        width, height = self.width, self.height
        for flake in self.flakes:
            blur = flake.near
            radius = flake.radius * (1 + 1.2 * blur)
            for _, x, y in flake.lane.spots(t):
                alpha = flake.alpha
                if blur > 0.35:
                    alpha *= _edge(x, y, width, height, 0.45)
                _disc(
                    painter,
                    QPointF(x, y),
                    radius,
                    [
                        (0, _alpha(self.colour, alpha)),
                        (0.5 * (1 - blur) + 0.05, _alpha(self.colour, alpha * (0.9 - 0.45 * blur))),
                        (1, _alpha(self.colour, 0)),
                    ],
                )


class _Firefly(NamedTuple):
    x: float
    y: float
    path_x: tuple[tuple[float, int, float], ...]  # (size, turns per loop, phase) of each wave
    path_y: tuple[tuple[float, int, float], ...]
    glow: float  # radius
    core: float
    blinks: int  # per loop
    blink_phase: float


class _Fireflies(_Scene):
    """Glowing dots wandering on smooth looping curves, slowly lighting up and dimming again."""

    def __init__(
        self, rng: random.Random, width: int, height: int, colour: str, seconds: float, density: float = 1.0
    ) -> None:
        super().__init__(rng, width, height, colour, seconds, density)
        unit = _unit(width, height)
        glow = mix(colour, "#fff1a8", 0.3)  # a touch of firefly yellow
        self.colour, self.hot = QColor(glow), QColor(mix(glow, "#ffffff", 0.8))

        def path() -> tuple[tuple[float, int, float], ...]:
            # Waves of 1, 2 and 3 turns per loop, sized for a lazy 15 to 40 pixels a second.
            return tuple(
                (rng.uniform(low, high) * unit * seconds / (math.tau * turns), turns, rng.random())
                for turns, low, high in ((1, 14, 30), (2, 6, 14), (3, 3, 8))
            )

        self.flies = [
            _Firefly(
                x,
                y,
                path(),
                path(),
                glow=rng.uniform(24, 40) * unit,
                core=rng.uniform(3.5, 5.5) * unit,
                blinks=max(1, round(seconds * rng.uniform(0.12, 0.3))),
                blink_phase=rng.random(),
            )
            for x, y in _scatter(rng, _count("fireflies", width, height, density), width, height, 0.25)
        ]
        self.population = len(self.flies)

    def draw(self, painter: QPainter, t: float) -> None:
        painter.setCompositionMode(_SCREEN)
        for fly in self.flies:
            x = fly.x + sum(size * math.cos(math.tau * (turns * t + phase)) for size, turns, phase in fly.path_x)
            y = fly.y + sum(size * math.sin(math.tau * (turns * t + phase)) for size, turns, phase in fly.path_y)
            centre = QPointF(x, y)
            lit = 0.5 + 0.5 * math.cos(math.tau * (fly.blinks * t + fly.blink_phase))
            level = 0.18 + 0.82 * _smoothstep(0.15, 0.85, lit)
            _disc(
                painter,
                centre,
                fly.glow,
                [
                    (0, _alpha(self.colour, 0.8 * level)),
                    (0.2, _alpha(self.colour, 0.42 * level)),
                    (0.5, _alpha(self.colour, 0.14 * level)),
                    (0.8, _alpha(self.colour, 0.03 * level)),
                    (1, _alpha(self.colour, 0)),
                ],
            )
            _disc(
                painter,
                centre,
                fly.core,
                [(0, _alpha(self.hot, level)), (0.6, _alpha(self.colour, 0.8 * level)), (1, _alpha(self.colour, 0))],
            )


class _Disc(NamedTuple):
    x: float
    y: float
    radius: float
    depth: float  # 0 to 1: which discs are sharp depends on where the focus is
    orbit_x: float
    orbit_y: float
    turn: int
    phase: float
    alpha: float
    colour: QColor
    beats: int
    beat_phase: float


class _Bokeh(_Scene):
    """Large soft translucent circles of out-of-focus light drifting gently around the edges,
    while the focus slowly moves from the near ones to the far ones and back."""

    def __init__(
        self, rng: random.Random, width: int, height: int, colour: str, seconds: float, density: float = 1.0
    ) -> None:
        super().__init__(rng, width, height, colour, seconds, density)
        unit = _unit(width, height)
        warm = mix(colour, "#ffe2b8", 0.35)
        self.discs = []
        for x, y in _scatter(rng, _count("bokeh", width, height, density), width, height, 0.1):
            depth = rng.random()
            travel = (5 + 10 * depth) * unit * seconds / math.tau  # nearer ones drift faster
            self.discs.append(
                _Disc(
                    x,
                    y,
                    radius=(26 + 80 * depth) * unit,
                    depth=depth,
                    orbit_x=travel * rng.uniform(0.7, 1.3),
                    orbit_y=travel * rng.uniform(0.5, 1.0),
                    turn=rng.choice((-1, 1)),
                    phase=rng.random(),
                    alpha=rng.uniform(0.14, 0.26) * (1 - 0.35 * depth),
                    colour=QColor(mix(rng.choice((colour, colour, warm)), "#ffffff", rng.uniform(0.1, 0.55))),
                    beats=rng.choice((1, 2)),
                    beat_phase=rng.random(),
                )
            )
        self.discs.sort(key=lambda disc: disc.depth)
        self.population = len(self.discs)

    def draw(self, painter: QPainter, t: float) -> None:
        painter.setCompositionMode(_SCREEN)
        focus = 0.5 + 0.5 * math.cos(math.tau * t)
        for disc in self.discs:
            angle = math.tau * (disc.turn * t + disc.phase)
            centre = QPointF(disc.x + disc.orbit_x * math.cos(angle), disc.y + disc.orbit_y * math.sin(angle))
            blur = min(1.0, 1.6 * abs(disc.depth - focus))
            radius = disc.radius * (1 + 0.1 * blur)
            alpha = disc.alpha * (0.85 + 0.15 * math.cos(math.tau * (disc.beats * t + disc.beat_phase)))
            alpha *= 1 - 0.3 * blur
            soft = 0.12 + 0.55 * blur
            _disc(
                painter,
                centre,
                radius,
                [
                    (0, _alpha(disc.colour, 0.55 * alpha)),
                    (1 - soft, _alpha(disc.colour, 0.65 * alpha)),
                    (1 - 0.4 * soft, _alpha(disc.colour, alpha * (1 - 0.4 * blur))),
                    (1, _alpha(disc.colour, 0)),
                ],
            )


class _Meteor(NamedTuple):
    start: float  # when in the loop it appears
    life: float  # part of the loop it is seen
    x: float
    y: float
    dx: float
    dy: float
    travel: float
    tail: float
    thick: float


class _Twinkle(NamedTuple):
    x: float
    y: float
    radius: float
    alpha: float
    twinkles: int
    phase: float


class _Starfall(_Scene):
    """A few shooting stars at different times of the loop, each with a long fading tail, over
    faint twinkling stars that gather towards the top of the picture."""

    def __init__(
        self, rng: random.Random, width: int, height: int, colour: str, seconds: float, density: float = 1.0
    ) -> None:
        super().__init__(rng, width, height, colour, seconds, density)
        unit = _unit(width, height)
        self.white, self.tint = QColor(mix(colour, "#ffffff", 0.88)), QColor(mix(colour, "#ffffff", 0.45))
        self.stars = []
        wanted = _count("starfall", width, height, density)
        while len(self.stars) < wanted:
            x, y = rng.uniform(0, width), rng.uniform(0, height)
            if rng.random() < 1 - 0.8 * y / height:
                self.stars.append(
                    _Twinkle(
                        x,
                        y,
                        radius=(0.7 + 1.6 * rng.random() ** 2) * unit,
                        alpha=rng.uniform(0.25, 0.8),
                        twinkles=rng.randint(1, 3),
                        phase=rng.random(),
                    )
                )
        count = max(1, round(seconds / 2.2 * density))
        heading = rng.choice((-1, 1))
        fall = math.radians(rng.uniform(22, 36))
        self.meteors = []
        for index in range(count):
            angle = fall + math.radians(rng.uniform(-4, 4))
            dx, dy = heading * math.cos(angle), math.sin(angle)
            travel = rng.uniform(0.22, 0.38) * width
            # High up in the sky, mostly off to one side, and never out of the picture sideways.
            middle = _side(rng, 0.1, 0.9, 0.35) * width
            x = max(0.02 * width, min(0.98 * width - abs(dx) * travel, middle - abs(dx) * travel / 2))
            self.meteors.append(
                _Meteor(
                    start=(index + rng.uniform(0.1, 0.6)) / count,
                    life=min(0.8 / count, rng.uniform(0.75, 1.15) / seconds),
                    x=x if heading > 0 else width - x,
                    y=rng.uniform(0.03, 0.3) * height,
                    dx=dx,
                    dy=dy,
                    travel=travel,
                    tail=rng.uniform(0.1, 0.17) * width,
                    thick=rng.uniform(1.4, 2.4) * unit,
                )
            )
        self.population = len(self.meteors)

    def draw(self, painter: QPainter, t: float) -> None:
        for star in self.stars:
            level = (0.5 + 0.5 * math.cos(math.tau * (star.twinkles * t + star.phase))) ** 2
            painter.setBrush(_alpha(self.white, star.alpha * (0.3 + 0.7 * level)))
            painter.drawEllipse(QPointF(star.x, star.y), star.radius, star.radius)
        painter.setCompositionMode(_SCREEN)
        for meteor in self.meteors:
            u = ((t - meteor.start) % 1.0) / meteor.life
            if u >= 1:
                continue
            fade = math.sin(math.pi * u) ** 0.8
            head = QPointF(meteor.x + meteor.dx * meteor.travel * u, meteor.y + meteor.dy * meteor.travel * u)
            tail = meteor.tail * min(1.0, 0.25 + 2.5 * u)
            end = QPointF(head.x() - meteor.dx * tail, head.y() - meteor.dy * tail)
            across = QPointF(-meteor.dy, meteor.dx)
            for thick, strength in ((4.5, 0.18), (1.0, 0.95)):
                side = across * meteor.thick * thick
                streak = QLinearGradient(head, end)
                streak.setColorAt(0, _alpha(self.white, strength * fade))
                streak.setColorAt(0.25, _alpha(self.tint, 0.55 * strength * fade))
                streak.setColorAt(1, _alpha(self.tint, 0))
                painter.setBrush(QBrush(streak))
                painter.drawPolygon(QPolygonF([head + side, end, head - side]))
            _disc(
                painter,
                head,
                meteor.thick * 6,
                [(0, _alpha(self.white, 0.95 * fade)), (0.25, _alpha(self.tint, 0.4 * fade)), (1, _CLEAR)],
            )


class _Drop(NamedTuple):
    lane: _Lane
    streak: float
    thick: float
    alpha: float


class _Rain(_Scene):
    """Thin slanted streaks of rain with depth (near drops longer, brighter and faster), light and
    sparse enough never to grey the picture, and fainter over its middle."""

    def __init__(
        self, rng: random.Random, width: int, height: int, colour: str, seconds: float, density: float = 1.0
    ) -> None:
        super().__init__(rng, width, height, colour, seconds, density)
        unit = _unit(width, height)
        self.colour = QColor(mix(colour, "#ffffff", 0.72))
        lean = rng.uniform(0.1, 0.24) * rng.choice((-1, 1))
        margin = 90 * unit
        drops = []
        for _ in range(_count("rain", width, height, density)):
            near = rng.random() ** 1.8
            angle = lean + rng.uniform(-0.015, 0.015)
            dx, dy = math.sin(angle), math.cos(angle)
            length = (height + 2 * margin) / dy
            drift = dx * length
            speed = (650 + 850 * near) * unit
            lane = _Lane(
                x=rng.uniform(min(0.0, -drift), max(float(width), width - drift)),
                y=-margin,
                dx=dx,
                dy=dy,
                length=length,
                count=1,
                start=rng.uniform(0, length),
                sway=0.0,
                wave=1.0,
                phase=0.0,
                laps=max(1, round(speed * seconds / length)),
            )
            drops.append(
                (
                    near,
                    _Drop(
                        lane, streak=(24 + 60 * near) * unit, thick=(0.9 + 1.3 * near) * unit, alpha=0.16 + 0.26 * near
                    ),
                )
            )
        drops.sort(key=lambda pair: pair[0])
        self.drops = [drop for _, drop in drops]
        self.population = len(self.drops)

    def draw(self, painter: QPainter, t: float) -> None:
        width, height = self.width, self.height
        for drop in self.drops:
            lane = drop.lane
            for _, x, y in lane.spots(t):
                alpha = drop.alpha * _edge(x, y, width, height, 0.5)
                head, tail = QPointF(x, y), QPointF(x - lane.dx * drop.streak, y - lane.dy * drop.streak)
                streak = QLinearGradient(head, tail)
                streak.setColorAt(0, _alpha(self.colour, alpha))
                streak.setColorAt(1, _alpha(self.colour, 0))
                painter.setPen(QPen(QBrush(streak), drop.thick, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawLine(head, tail)


def _rune(rng: random.Random) -> QPainterPath:
    """A made-up rune about 2 high: a stem with two or three branches between points of a small
    grid, sometimes with a small ring. Random, so it never spells anything real."""
    path = QPainterPath(QPointF(0, -1))
    path.lineTo(QPointF(0, 1))
    for _ in range(rng.randint(2, 3)):
        start = QPointF(0, rng.choice((-1.0, -0.5, 0.0, 0.5, 1.0)))
        end = QPointF(rng.choice((-0.7, 0.7)), rng.choice((-1.0, -0.4, 0.0, 0.4, 1.0)))
        path.moveTo(start)
        if rng.random() < 0.3:
            path.quadTo(QPointF(end.x(), start.y()), end)
        else:
            path.lineTo(end)
    if rng.random() < 0.35:
        path.addEllipse(QPointF(rng.choice((-0.45, 0.45)), rng.choice((-0.55, 0.55))), 0.22, 0.22)
    return path


def _around(shapes: list[QPainterPath], count: int, turn: float) -> QPainterPath:
    """``count`` copies of ``shapes`` (one after another), drawn at the top and spaced evenly around
    the middle, all turned clockwise by ``turn`` degrees. Every angle is brought into 0 to 360 and
    the copies go in order of angle, so turning by a whole number of copies gives exactly the same
    path: the loop stays seamless to the last pixel."""
    step = 360 / count
    placed = sorted(((index * step + turn) % 360, index % len(shapes)) for index in range(count))
    path = QPainterPath()
    for angle, which in placed:
        path.addPath(QTransform().rotate(angle).map(shapes[which]))
    return path


def _at(angle: float, radius: float) -> QPointF:
    """The point ``radius`` from the middle at ``angle`` degrees clockwise from the top."""
    rad = math.radians(angle)
    return QPointF(radius * math.sin(rad), -radius * math.cos(rad))


class _Spark(NamedTuple):
    radius: float  # of its orbit, in circle radii
    turns: int  # per loop, signed
    phase: float
    size: float


class _Magic(_Scene):
    """An anime-style magic circle in a corner: glowing concentric rings, a band of made-up runes,
    ticks and a twelve-point star turning slowly in opposite directions, breathing, with a wave of
    light running outward and a few sparks circling it."""

    # Degrees turned per loop: each part repeats after that angle, so the loop is seamless.
    RUNES, TICKS, STAR = 30.0, 10.0, 30.0

    def __init__(
        self, rng: random.Random, width: int, height: int, colour: str, seconds: float, density: float = 1.0
    ) -> None:
        super().__init__(rng, width, height, colour, seconds, density)
        radius = 0.27 * min(width, height)
        across, down = rng.choice(((1, 1), (-1, 1), (1, 1), (-1, 1), (1, -1), (-1, -1)))
        self.centre = QPointF(
            width / 2 + across * (width / 2 - 1.12 * radius), height / 2 + down * (height / 2 - 0.76 * radius)
        )
        self.radius = radius
        self.turn = rng.choice((-1, 1))
        self.line = QColor(mix(colour, "#ffffff", 0.55))
        self.colour = QColor(colour)
        self.white = QColor(mix(colour, "#ffffff", 0.9))
        side = max(8, math.ceil(2.5 * radius))
        self.layer = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
        self.glow_side = max(2, side // 6)
        # Each part drawn once at the top of the circle (radius 1), then repeated around it.
        self.runes = []  # three made-up runes, each with a dot after it
        for _ in range(3):
            rune = QTransform().translate(0, -0.875).scale(0.042, 0.042).map(_rune(rng))
            rune.addEllipse(_at(5, 0.875), 0.008, 0.008)
            self.runes.append(rune)
        self.ticks = []  # long and short in turn
        for inner in (0.7, 0.725):
            tick = QPainterPath(QPointF(0, -0.76))
            tick.lineTo(QPointF(0, -inner))
            self.ticks.append(tick)
        edge = QPainterPath(_at(0, 0.68))  # one edge of a twelve-point star: every fifth corner joined
        edge.lineTo(_at(150, 0.68))
        self.edge = [edge]
        node = QPainterPath()
        node.addEllipse(_at(15, 1.0), 0.03, 0.03)
        self.node = [node]
        dot = QPainterPath()
        dot.addEllipse(_at(0, 0.24), 0.014, 0.014)
        self.dot = [dot]
        self.sparks = [
            _Spark(
                radius=rng.choice((rng.uniform(1.04, 1.12), rng.uniform(0.5, 0.62))),
                turns=rng.choice((1, -1, 1, 2)) * self.turn,
                phase=rng.random(),
                size=rng.uniform(0.012, 0.02),
            )
            for _ in range(max(2, round(5 * density)))
        ]
        self.population = len(self.sparks)

    def _ring(self, painter: QPainter, radius: float, thick: float, t: float, breath: float) -> None:
        # A wave of light runs outward through the rings once per loop.
        level = 0.55 + 0.45 * _wave(t - 0.6 * radius) ** 2
        painter.setPen(QPen(_alpha(self.line, 0.8 * level * breath), thick))
        painter.drawEllipse(QPointF(0, 0), radius, radius)

    def draw(self, painter: QPainter, t: float) -> None:
        breath = 0.8 + 0.2 * _wave(t)
        layer = self.layer
        layer.fill(Qt.GlobalColor.transparent)
        p = QPainter(layer)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.translate(layer.width() / 2, layer.height() / 2)
            scale = self.radius * (1 + 0.03 * (_wave(t) - 0.5))
            p.scale(scale, scale)
            # A faint glassy disc behind the lines.
            plate = QRadialGradient(QPointF(0, 0), 1)
            plate.setColorAt(0, _alpha(self.colour, 0.03 * breath))
            plate.setColorAt(0.8, _alpha(self.colour, 0.07 * breath))
            plate.setColorAt(1, _alpha(self.colour, 0.12 * breath))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(plate))
            p.drawEllipse(QPointF(0, 0), 1, 1)
            p.setBrush(Qt.BrushStyle.NoBrush)
            for radius, thick in (
                (1.0, 0.012),
                (0.955, 0.005),
                (0.8, 0.006),
                (0.77, 0.004),
                (0.36, 0.006),
                (0.12, 0.005),
                (0.07, 0.004),
            ):
                self._ring(p, radius, thick, t, breath)
            fine = (Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            level = 0.55 + 0.45 * _wave(t - 0.52) ** 2
            p.setPen(QPen(_alpha(self.white, 0.85 * level * breath), 0.006, *fine))
            p.drawPath(_around(self.runes, 36, self.turn * self.RUNES * t))
            p.setPen(QPen(_alpha(self.line, 0.8 * breath), 0.006))
            p.drawPath(_around(self.node, 12, self.turn * self.RUNES * t))
            p.setPen(QPen(_alpha(self.line, 0.7 * breath), 0.004))
            p.drawPath(_around(self.ticks, 72, -self.turn * self.TICKS * t))
            p.setPen(QPen(_alpha(self.line, (0.45 + 0.4 * _wave(t - 0.35) ** 2) * breath), 0.005, *fine))
            p.drawPath(_around(self.edge, 12, -self.turn * self.STAR * t))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_alpha(self.white, 0.7 * breath))
            p.drawPath(_around(self.dot, 12, self.turn * self.STAR * t))
            # Sparks circling with short fading trails.
            for spark in self.sparks:
                head = (90 - 360 * (spark.turns * t + spark.phase)) % 360  # counter-clockwise degrees
                box = QRectF(-spark.radius, -spark.radius, 2 * spark.radius, 2 * spark.radius)
                span = 28.0
                p.save()
                if spark.turns < 0:
                    p.scale(-1, 1)
                    head = (180 - head) % 360
                p.setBrush(Qt.BrushStyle.NoBrush)
                brush = QBrush(_trail(self.white, QPointF(0, 0), head, span, 1.0, 0.8 * breath))
                p.setPen(QPen(brush, 0.6 * spark.size, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
                p.drawPath(_arc(box, head, span))
                tip = _at(90 - head, spark.radius)
                p.setPen(Qt.PenStyle.NoPen)
                _disc(
                    p,
                    tip,
                    2.5 * spark.size,
                    [(0, _alpha(self.white, 0.95 * breath)), (0.3, _alpha(self.line, 0.5 * breath)), (1, _CLEAR)],
                )
                p.restore()
        finally:
            p.end()
        glow = layer.scaled(
            self.glow_side,
            self.glow_side,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        area = QRectF(
            self.centre.x() - layer.width() / 2, self.centre.y() - layer.height() / 2, layer.width(), layer.height()
        )
        painter.setCompositionMode(_SCREEN)
        painter.drawImage(area, glow, QRectF(glow.rect()))
        painter.drawImage(area, layer, QRectF(layer.rect()))


class _Line(NamedTuple):
    angle: float  # radians
    reach: float  # where it ends inside, as a part of the way from the middle to the edge
    thick: float  # half its width at the edge
    alpha: float
    phase: float


class _Speedlines(_Scene):
    """Anime focus lines: thin tapering streaks from beyond the edges towards the middle, which
    stays clear, gently pulsing in and out."""

    def __init__(
        self, rng: random.Random, width: int, height: int, colour: str, seconds: float, density: float = 1.0
    ) -> None:
        super().__init__(rng, width, height, colour, seconds, density)
        unit = _unit(width, height)
        self.colour = QColor(mix(colour, "#ffffff", 0.8))
        count = max(MIN_PARTICLES, round(SPEEDLINES * density))
        self.lines = [
            _Line(
                angle=math.tau * (index + rng.uniform(-0.4, 0.4)) / count,
                reach=rng.uniform(0.58, 0.9),
                thick=(1.0 + 3.5 * rng.random() ** 2) * unit,
                alpha=rng.uniform(0.14, 0.38),
                phase=rng.uniform(0, 0.2),
            )
            for index in range(count)
        ]
        self.pulses = max(1, round(seconds / 3))
        self.population = count

    def draw(self, painter: QPainter, t: float) -> None:
        half_w, half_h = self.width / 2, self.height / 2
        for line in self.lines:
            beat = _wave(self.pulses * t + line.phase)
            cos, sin = math.cos(line.angle), math.sin(line.angle)
            reach = line.reach - 0.07 * beat
            tip = QPointF(half_w * (1 + reach * cos), half_h * (1 + reach * sin))
            edge = QPointF(half_w * (1 + 1.6 * cos), half_h * (1 + 1.6 * sin))  # beyond the corners
            along = edge - tip
            size = math.hypot(along.x(), along.y())
            side = QPointF(-along.y() / size, along.x() / size) * line.thick
            alpha = line.alpha * (0.7 + 0.3 * beat)
            streak = QLinearGradient(edge, tip)
            streak.setColorAt(0, _alpha(self.colour, alpha))
            streak.setColorAt(0.6, _alpha(self.colour, 0.55 * alpha))
            streak.setColorAt(1, _alpha(self.colour, 0))
            painter.setBrush(QBrush(streak))
            painter.drawPolygon(QPolygonF([edge + side, tip, edge - side]))


_SCENES: dict[str, Callable[[random.Random, int, int, str, float, float], _Scene]] = {
    "sparkle": _Sparkles,
    "embers": _Embers,
    "petals": _Petals,
    "snow": _Snow,
    "fireflies": _Fireflies,
    "bokeh": _Bokeh,
    "starfall": _Starfall,
    "rain": _Rain,
    "magic": _Magic,
    "speedlines": _Speedlines,
    "none": _Scene,
}


# The animated WebP container


def _chunk(tag: bytes, payload: bytes) -> bytes:
    """A RIFF chunk: tag, little-endian size, payload and a zero byte when the size is odd."""
    return tag + struct.pack("<I", len(payload)) + payload + b"\0" * (len(payload) & 1)


def _uint24(value: int) -> bytes:
    return value.to_bytes(3, "little")


def _frame_data(still: bytes, width: int, height: int) -> tuple[bytes, bool]:
    """The image chunks of a still WebP file (ALPH if any, then VP8 or VP8L) ready to go inside
    an ANMF chunk, and whether they use transparency. Metadata chunks are left out."""
    if len(still) < 12 or still[:4] != b"RIFF" or still[8:12] != b"WEBP":
        raise RuntimeError("Qt did not write a WebP picture")
    end = min(len(still), 8 + int.from_bytes(still[4:8], "little"))
    parts: list[bytes] = []
    alpha = False
    shape = None
    pos = 12
    while pos + 8 <= end and shape is None:
        tag, length = still[pos : pos + 4], int.from_bytes(still[pos + 4 : pos + 8], "little")
        payload = still[pos + 8 : pos + 8 + length]
        if len(payload) != length:
            raise RuntimeError("Qt wrote a truncated WebP picture")
        if tag == b"ALPH":
            parts.append(_chunk(tag, payload))
            alpha = True
        elif tag == b"VP8 ":
            # Frame tag (3 bytes), start code 9d 01 2a, then 14-bit width and height.
            if length < 10 or payload[3:6] != b"\x9d\x01\x2a":
                raise RuntimeError("Qt wrote a WebP picture with a broken VP8 header")
            shape = (int.from_bytes(payload[6:8], "little") & 0x3FFF, int.from_bytes(payload[8:10], "little") & 0x3FFF)
            parts.append(_chunk(tag, payload))
        elif tag == b"VP8L":
            # Signature 0x2f, then width - 1 and height - 1 (14 bits each) and the alpha bit.
            if length < 5 or payload[0] != 0x2F:
                raise RuntimeError("Qt wrote a WebP picture with a broken VP8L header")
            bits = int.from_bytes(payload[1:5], "little")
            shape = ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
            alpha = alpha or bool(bits >> 28 & 1)
            parts.append(_chunk(tag, payload))
        pos += 8 + length + (length & 1)
    if shape is None:
        raise RuntimeError("Qt wrote a WebP picture without image data")
    if shape != (width, height):
        raise RuntimeError(f"Qt wrote a {shape[0]}x{shape[1]} frame instead of {width}x{height}")
    return b"".join(parts), alpha


class _AnimatedWebP:
    """An animated WebP written chunk by chunk to ``out``, so only one frame is ever in memory:
    RIFF header, VP8X (canvas size, animation flag), ANIM (background, loops) and one ANMF chunk
    per frame. The sizes that are only known at the end are filled in by finish()."""

    def __init__(self, out: BinaryIO, width: int, height: int) -> None:
        self.out, self.width, self.height = out, width, height
        self.alpha = False
        out.write(b"RIFF\0\0\0\0WEBP")
        self.vp8x = out.tell()
        out.write(_chunk(b"VP8X", bytes([_VP8X_ANIMATION, 0, 0, 0]) + _uint24(width - 1) + _uint24(height - 1)))
        # Opaque black background (stored blue, green, red, alpha), then loop count 0: forever.
        out.write(_chunk(b"ANIM", bytes([0, 0, 0, 255]) + struct.pack("<H", 0)))

    def add(self, still: bytes, duration: int) -> None:
        """Append one frame (a still WebP file of the canvas size) shown for ``duration`` ms."""
        data, alpha = _frame_data(still, self.width, self.height)
        self.alpha = self.alpha or alpha
        # Frame offset (in units of 2 pixels; whole frames sit at 0, 0), size, duration, flags.
        header = _uint24(0) + _uint24(0) + _uint24(self.width - 1) + _uint24(self.height - 1)
        header += _uint24(duration) + bytes([_ANMF_NO_BLEND])
        size = len(header) + len(data)
        self.out.write(b"ANMF" + struct.pack("<I", size) + header)
        self.out.write(data)
        if size & 1:
            self.out.write(b"\0")
        if self.out.tell() - 8 > _RIFF_MAX:
            raise RuntimeError("The animation is too large for a WebP file")

    def finish(self) -> None:
        end = self.out.tell()
        if self.alpha:
            self.out.seek(self.vp8x + 8)
            self.out.write(bytes([_VP8X_ANIMATION | _VP8X_ALPHA]))
        self.out.seek(4)
        self.out.write(struct.pack("<I", end - 8))
        self.out.seek(end)
