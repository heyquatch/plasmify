"""Per-user font installation from upstream, for fonts a distribution does not package, and what
fontconfig knows about the fonts installed here."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlparse

from plasmify import activity, downloads
from plasmify.package_map import FONT_FALLBACKS
from plasmify.system import DATA_HOME, Runner

FONT_ROOT = DATA_HOME / "fonts" / "plasmify"
FONT_SUFFIXES = (".ttf", ".otf")


def font_dir(canonical: str) -> Path:
    return FONT_ROOT / canonical


def families(pattern: str = ":") -> set[str] | None:
    """Font families fontconfig lists for ``pattern`` (None when fontconfig cannot tell)."""
    tool = shutil.which("fc-list")
    if tool is None:
        return None
    try:
        found = subprocess.run([tool, pattern, "family"], capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if found.returncode != 0:
        return None
    return {name.strip() for line in found.stdout.splitlines() for name in line.split(",") if name.strip()}


def no_latin_letters(family: str) -> bool:
    """Whether ``family`` is installed but cannot write English: an emoji, symbol or dingbat font,
    or one made for another script only (an Arabic-only font, say).

    As a desktop font an emoji font leaves letters and digits to fallback fonts, and apps that
    size their items by the font, such as Dolphin, draw empty boxes. A font that is not
    installed (yet) does not count, so packages still install first.
    """
    installed, english = families(), families(":lang=en")
    if not installed or english is None:
        return False
    wanted = family.casefold()
    return wanted in {f.casefold() for f in installed} and wanted not in {f.casefold() for f in english}


def downloaded(canonical: str) -> bool:
    """Whether the font is in the home folder already (an apply then neither plans nor fetches it)."""
    target = font_dir(canonical)
    try:
        return target.is_dir() and any(target.iterdir())
    except OSError:
        return False


def install_user_fonts(names: list[str], workdir: Path, runner: Runner) -> tuple[list[Path], list[str]]:
    """Download ``names`` (canonical package names) into ``~/.local/share/fonts/plasmify``.

    Returns the installed directories and any warnings. Already installed fonts are skipped.
    """
    installed: list[Path] = []
    warnings: list[str] = []
    for name in names:
        target = font_dir(name)
        if downloaded(name):
            installed.append(target)
            continue
        urls = FONT_FALLBACKS.get(name, [])
        staging = workdir / name
        try:
            for url in urls:
                file_name = unquote(Path(urlparse(url).path).name)
                fetched = downloads.fetch(url, staging / "download" / file_name)
                if fetched.suffix.lower() == ".zip":
                    extracted = downloads.extract(fetched, staging / "extracted")
                    found = [p for p in extracted.rglob("*") if p.suffix.lower() in FONT_SUFFIXES]
                else:
                    found = [fetched]
                target.mkdir(parents=True, exist_ok=True)
                for font in found:
                    shutil.copy2(font, target / font.name)
        except downloads.DownloadError as exc:
            warnings.append(f"Could not download font '{name}': {exc}")
            shutil.rmtree(target, ignore_errors=True)
            continue
        installed.append(target)
        activity.record("fonts", name, target)
    if installed:
        runner.run(["fc-cache", "-f", str(FONT_ROOT)], check=False)
    return installed, warnings
