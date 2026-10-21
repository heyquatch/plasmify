"""Fetching upstream assets (files, release archives and parts of git repos).

Assets are never vendored into this repository: they are fetched at apply time
from where they are published. Downloads land in a temporary work directory;
only the final artefacts (wallpapers, cursors, …) are kept.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from plasmify import activity
from plasmify.schema import TRUSTED_HOSTS

MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024
MAX_EXTRACTED_BYTES = 500 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 20_000  # icon and cursor themes have thousands of small files, not millions
MAX_SESSION_BYTES = 1536 * 1024 * 1024  # all downloads of one apply together (30 wallpapers fit easily)
_budget = {"used": 0}


def reset_budget() -> None:
    """Start counting a new apply's downloads."""
    _budget["used"] = 0


TIMEOUT = 60

# Release downloads on GitHub redirect to these asset hosts.
REDIRECT_HOSTS = ("objects.githubusercontent.com", "release-assets.githubusercontent.com")

GIF_MAGIC = (b"GIF87a", b"GIF89a")
IMAGE_MAGIC = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"RIFF",  # webp (RIFF....WEBP)
    *GIF_MAGIC,
)
NOT_AN_IMAGE = "is not a PNG, JPEG, WebP or GIF image"
# Decoders allocate whole frames, so a small file claiming to be huge (a decompression bomb) is
# refused from its header. 8192x8192 is also Qt's own 256 MB per-frame limit.
MAX_IMAGE_SIDE = 8192
MAX_IMAGE_FRAMES = 1500  # a minute of animation at 25 frames per second
# Largest still made for screens that cannot play animations (the login screen).
STILL_SIZE = (3840, 2160)

# Extra hosts the user agreed to contact for the preset being applied.
_allowed_hosts: set[str] = set()


class DownloadError(RuntimeError):
    pass


def allow_hosts(hosts: list[str] | set[str]) -> None:
    """Allow downloads from ``hosts`` (besides the trusted ones) until reset."""
    _allowed_hosts.clear()
    _allowed_hosts.update(hosts)


def check_url(url: str) -> None:
    """Refuse anything but HTTPS to a trusted or explicitly allowed host."""
    parts = urlparse(url)
    if parts.scheme != "https" or not parts.hostname:
        raise DownloadError(f"Refusing non-HTTPS URL: {url}")
    host = parts.hostname
    if host not in TRUSTED_HOSTS and host not in REDIRECT_HOSTS and host not in _allowed_hosts:
        raise DownloadError(f"Refusing to contact {host}: it is not a trusted host and was not approved")


class _SafeRedirects(urllib.request.HTTPRedirectHandler):
    """Follow redirects only to URLs that pass :func:`check_url` (no HTTPS downgrade)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_SafeRedirects)


def fetch(url: str, dest: Path) -> Path:
    """Download ``url`` to ``dest`` (atomically) and return ``dest``.

    No identifying User-Agent is sent: the request looks like any Python client.
    """
    try:
        check_url(url)
    except DownloadError as exc:
        activity.record("download", url, dest, ok=False, error=str(exc))
        raise
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    total = 0
    try:
        # check_url() above restricts this to https and approved hosts.
        with _opener.open(url, timeout=TIMEOUT) as response, tmp.open("wb") as out:  # noqa: S310
            while chunk := response.read(1 << 16):
                total += len(chunk)
                if total > MAX_DOWNLOAD_BYTES:
                    raise DownloadError(f"{url} is larger than {MAX_DOWNLOAD_BYTES} bytes")
                _budget["used"] += len(chunk)
                if _budget["used"] > MAX_SESSION_BYTES:
                    raise DownloadError(f"This preset downloads more than {MAX_SESSION_BYTES // 2**20} MB in total")
                digest.update(chunk)
                out.write(chunk)
    except (OSError, DownloadError) as exc:
        tmp.unlink(missing_ok=True)
        error = exc if isinstance(exc, DownloadError) else DownloadError(f"Could not download {url}: {exc}")
        activity.record("download", url, dest, size=total, ok=False, error=str(error))
        raise error from exc
    tmp.replace(dest)
    activity.record("download", url, dest, size=total, sha256=digest.hexdigest())
    return dest


def image_suffix(url: str) -> str:
    """The file extension to store a downloaded image under (never anything exotic)."""
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix if suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif") else ".png"


def image_kind(head: bytes) -> str | None:
    """'png', 'jpeg', 'webp' or 'gif', judged by the magic bytes alone (never by the name)."""
    if head.startswith(IMAGE_MAGIC[0]):
        return "png"
    if head.startswith(IMAGE_MAGIC[1]):
        return "jpeg"
    if head.startswith(b"RIFF"):
        return "webp" if head[8:12] == b"WEBP" else None
    return "gif" if head[:6] in GIF_MAGIC else None


def is_animated(head: bytes) -> bool:
    """True for pictures Plasma plays as animations: every GIF (even a single frame) and WebP
    files whose VP8X header has the animation flag."""
    kind = image_kind(head)
    if kind == "webp":
        return head[12:16] == b"VP8X" and len(head) > 20 and bool(head[20] & 0x02)
    return kind == "gif"


def _header_size(kind: str, head: bytes) -> tuple[int, int]:
    """Width and height as the file's own header states them, (0, 0) where it does not say.
    Plain byte reads, so these limits hold without Qt too."""
    if kind == "png" and head[12:16] == b"IHDR" and len(head) >= 24:
        width, height = struct.unpack(">II", head[16:24])
        return width, height
    if kind == "gif" and len(head) >= 10:
        width, height = struct.unpack("<HH", head[6:10])
        return width, height
    if kind == "webp" and head[12:16] == b"VP8X" and len(head) >= 30:
        return 1 + int.from_bytes(head[24:27], "little"), 1 + int.from_bytes(head[27:30], "little")
    return 0, 0


def _qt_header(source: bytes | Path, kind: str) -> tuple[int, int, int]:
    """Width, height and frame count as Qt's decoder reads them from the header, without
    decoding pixels; zeros where Qt cannot tell or PyQt6 is missing.

    PNG is left out: its size is always in the first bytes, and Plasma plays no PNG animations."""
    if kind == "png":
        return 0, 0, 0
    try:
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
        from PyQt6.QtGui import QImageReader
    except ImportError:
        return 0, 0, 0
    buffer = QBuffer()  # outlives the reader, which only borrows it
    if isinstance(source, Path):
        reader = QImageReader(str(source), kind.encode())
    else:
        buffer.setData(QByteArray(source))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer, kind.encode())
    reader.setAutoDetectImageFormat(False)  # the magic bytes chose the decoder, not the name
    size = reader.size()
    return max(size.width(), 0), max(size.height(), 0), max(reader.imageCount(), 0)


def size_problem(width: int, height: int, frames: int = 1) -> str | None:
    """Why a picture of this size cannot be used, or None when it can."""
    if width > MAX_IMAGE_SIDE or height > MAX_IMAGE_SIDE:
        return f"is too large ({width}x{height} pixels; at most {MAX_IMAGE_SIDE}x{MAX_IMAGE_SIDE})"
    if frames > MAX_IMAGE_FRAMES:
        return f"has too many frames ({frames}; at most {MAX_IMAGE_FRAMES})"
    return None


def image_problem(data: bytes) -> str | None:
    """Why ``data`` cannot be used as a picture, or None when it can (see image_file_problem)."""
    kind = image_kind(data[:16])
    if kind is None:
        return NOT_AN_IMAGE
    if len(data) > MAX_DOWNLOAD_BYTES:
        return f"is larger than {MAX_DOWNLOAD_BYTES // 2**20} MB"
    return size_problem(*_header_size(kind, data[:64])) or size_problem(*_qt_header(data, kind))


def image_file_problem(path: Path) -> str | None:
    """Why the file at ``path`` cannot be used as a picture, or None when it can.

    The type comes from the magic bytes, never the name. Width, height and frame count come
    from the header (checked by Qt too when PyQt6 is installed); no pixels are decoded."""
    try:
        if not path.is_file():
            return "is missing or is not a file"
        with path.open("rb") as handle:
            head = handle.read(64)
            size = os.fstat(handle.fileno()).st_size
    except OSError as exc:
        return f"cannot be read ({exc.strerror or exc})"
    kind = image_kind(head)
    if kind is None:
        return NOT_AN_IMAGE
    if size > MAX_DOWNLOAD_BYTES:
        return f"is larger than {MAX_DOWNLOAD_BYTES // 2**20} MB"
    return size_problem(*_header_size(kind, head)) or size_problem(*_qt_header(path, kind))


def is_image(path: Path) -> bool:
    """True for a PNG, JPEG, WebP or GIF file of a sane size (see image_file_problem)."""
    return image_file_problem(path) is None


def is_image_bytes(data: bytes) -> bool:
    """True for PNG, JPEG, WebP and GIF data (checked by magic bytes, never by name) whose
    header claims no absurd size or frame count."""
    return image_problem(data) is None


def still_png(data: bytes) -> bytes:
    """The first frame of ``data`` as PNG bytes, at most STILL_SIZE, for screens that show only
    still pictures. Raises ValueError when it cannot be made."""
    problem = image_problem(data)
    if problem:
        raise ValueError(f"the picture {problem}")
    try:
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, Qt
        from PyQt6.QtGui import QImageReader
    except ImportError as exc:
        raise ValueError("turning an animated picture into a still one needs PyQt6") from exc
    source = QBuffer()
    source.setData(QByteArray(data))
    source.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(source, (image_kind(data[:16]) or "").encode())
    reader.setAutoDetectImageFormat(False)
    frame = reader.read()
    if frame.isNull():
        raise ValueError(f"its first frame cannot be read ({reader.errorString()})")
    width, height = STILL_SIZE
    if frame.width() > width or frame.height() > height:
        frame = frame.scaled(
            width, height, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
    out = QBuffer()
    out.open(QIODevice.OpenModeFlag.WriteOnly)
    if not frame.save(out, "PNG"):
        raise ValueError("the still picture could not be written")
    return bytes(out.data())


def fetch_image(url: str, dest: Path) -> Path:
    fetch(url, dest)
    problem = image_file_problem(dest)
    if problem:
        dest.unlink(missing_ok=True)
        if problem == NOT_AN_IMAGE:
            raise DownloadError(f"{url} did not return a PNG, JPEG, WebP or GIF image")
        raise DownloadError(f"The image at {url} {problem}")
    return dest


def extract(archive: Path, dest: Path) -> Path:
    """Extract a zip or tar archive safely (no absolute paths, links outside, devices)."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as zf:
            if len(zf.infolist()) > MAX_ARCHIVE_ENTRIES:
                raise DownloadError(f"{archive.name} has more than {MAX_ARCHIVE_ENTRIES} files")
            if sum(info.file_size for info in zf.infolist()) > MAX_EXTRACTED_BYTES:
                raise DownloadError(f"{archive.name} expands to more than {MAX_EXTRACTED_BYTES} bytes")
            for member in zf.namelist():
                target = (dest / member).resolve()
                if not target.is_relative_to(root):
                    raise DownloadError(f"Unsafe path in archive: {member}")
            # Every member was checked above to stay inside dest; zipfile creates no symlinks.
            zf.extractall(dest)  # noqa: S202
    elif tarfile.is_tarfile(archive):
        with tarfile.open(archive) as tf:
            # Count while reading the index: getmembers() would first load all of it, which a
            # small compressed archive with millions of empty entries turns into a memory bomb.
            members, total = [], 0
            for member in tf:
                if member.islnk():
                    # A hard link counts as 0 bytes here but becomes a full copy when the theme
                    # is copied into place: refuse them so the size cap stays true.
                    raise DownloadError(f"{archive.name} contains hard links, which themes do not need")
                members.append(member)
                total += member.size
                if len(members) > MAX_ARCHIVE_ENTRIES:
                    raise DownloadError(f"{archive.name} has more than {MAX_ARCHIVE_ENTRIES} files")
                if total > MAX_EXTRACTED_BYTES:
                    raise DownloadError(f"{archive.name} expands to more than {MAX_EXTRACTED_BYTES} bytes")
            # filter="data" rejects absolute paths, links outside dest and device files.
            tf.extractall(dest, members=members, filter="data")  # noqa: S202
    else:
        raise DownloadError(f"{archive.name} is not a zip or tar archive")
    return dest


def sparse_checkout(repo: str, path: str, dest: Path, ref: str | None = None) -> Path:
    """Fetch only ``path`` from git ``repo`` and return the local directory for it."""
    check_url(repo)
    if not shutil.which("git"):
        raise DownloadError("git is required to fetch theme sources")
    if dest.exists():
        shutil.rmtree(dest)
    # protocol.allow=never + https only: a repository cannot pull in submodules or other transports.
    # http.followRedirects=false: git never leaves the host check_url() approved (urllib
    # downloads get the same through _SafeRedirects); a moved repository needs its new URL.
    # core.symlinks=false: links in the repository become plain files, so nothing copied
    # later can follow one out of the checkout. The lfs settings stop Git LFS from fetching
    # from a server named in the repository's .lfsconfig (which nobody approved). They apply
    # to both commands: "sparse-checkout set" is the one that writes the files.
    safety = [
        "-c",
        "protocol.allow=never",
        "-c",
        "protocol.https.allow=always",
        "-c",
        "http.followRedirects=false",
        "-c",
        "core.symlinks=false",
        "-c",
        "filter.lfs.smudge=",
        "-c",
        "filter.lfs.process=",
        "-c",
        "filter.lfs.required=false",
    ]
    clone = [
        "git",
        *safety,
        "clone",
        "--quiet",
        "--depth",
        "1",
        "--filter=blob:none",
        "--sparse",
        "--no-recurse-submodules",
    ]
    if ref:
        clone += ["--branch", ref]
    # The path goes in on stdin, so it can never be parsed as an option.
    steps = [
        (clone + ["--", repo, str(dest)], None),
        (["git", "-C", str(dest), *safety, "sparse-checkout", "set", "--no-cone", "--stdin"], path + "\n"),
    ]
    env = dict(os.environ, GIT_LFS_SKIP_SMUDGE="1", GIT_TERMINAL_PROMPT="0")
    for args, stdin in steps:
        proc = subprocess.run(args, capture_output=True, text=True, check=False, input=stdin, env=env)
        if proc.returncode != 0:
            error = proc.stderr.strip()
            activity.record("git", repo, path, ok=False, error=error[:300])
            if "returned error: 30" in error:  # a 3xx answer: the redirect git was told not to follow
                error += " (git redirects are not followed: use the repository's own URL, on GitLab ending in .git)"
            raise DownloadError(f"git failed for {repo}: {error}")
    commit = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"], capture_output=True, text=True, check=False
    ).stdout.strip()
    result = dest / path
    if not result.resolve().is_relative_to(dest.resolve()):
        raise DownloadError(f"{path} points outside the checkout of {repo}")
    if not result.exists():
        raise DownloadError(f"{path} does not exist in {repo}")
    size = sum(f.stat().st_size for f in dest.rglob("*") if f.is_file() and not f.is_symlink())
    _budget["used"] += size
    if size > MAX_EXTRACTED_BYTES or _budget["used"] > MAX_SESSION_BYTES:
        shutil.rmtree(dest, ignore_errors=True)
        raise DownloadError(f"{repo} is too large ({size // 2**20} MB checked out)")
    # For git sources the "sha256" column holds the exact commit that was used.
    activity.record("git", repo, path, sha256=commit)
    return result
