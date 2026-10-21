"""Per-user font installation from upstream, for fonts a distribution does not package."""

from __future__ import annotations

import shutil
from pathlib import Path
from urllib.parse import unquote, urlparse

from plasmify import activity, downloads
from plasmify.package_map import FONT_FALLBACKS
from plasmify.system import DATA_HOME, Runner

FONT_ROOT = DATA_HOME / "fonts" / "plasmify"
FONT_SUFFIXES = (".ttf", ".otf")


def font_dir(canonical: str) -> Path:
    return FONT_ROOT / canonical


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
