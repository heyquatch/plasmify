"""Package names per distribution, and upstream fallbacks for fonts.

Presets and steps refer to packages by their Arch Linux name (the canonical
name). This table translates them for the other families. ``None`` means the
family does not package it; fonts then fall back to a per-user download from
the font's official source (no root needed), anything else is skipped with a
warning. A distribution ID (e.g. ``"ubuntu"``) can override its family.

Names were checked against Repology (Fedora 43+, openSUSE Tumbleweed,
Debian 13, Ubuntu 25.10+). ``scripts/check_packages.py`` verifies them against
the real package managers in CI.
"""

from __future__ import annotations

from dataclasses import dataclass

from plasmify.distro import ARCH, DEBIAN, FEDORA, OPENSUSE, Distro

_GOOGLE_FONTS = "https://raw.githubusercontent.com/google/fonts/main"

# canonical (Arch) name -> {family or distro id: native name or None}
PACKAGES: dict[str, dict[str, str | None]] = {
    # Fonts
    "inter-font": {FEDORA: "rsms-inter-fonts", OPENSUSE: "inter-fonts", DEBIAN: "fonts-inter"},
    "ttf-jetbrains-mono": {
        FEDORA: "jetbrains-mono-fonts",
        OPENSUSE: "jetbrains-mono-fonts",
        DEBIAN: "fonts-jetbrains-mono",
    },
    "ttf-nerd-fonts-symbols": {FEDORA: None, OPENSUSE: None, DEBIAN: None},
    "ttf-roboto": {FEDORA: "google-roboto-fonts", OPENSUSE: "google-roboto-fonts", DEBIAN: "fonts-roboto"},
    "ttf-roboto-mono": {FEDORA: "google-roboto-mono-fonts", OPENSUSE: "google-roboto-mono-fonts", DEBIAN: None},
    "ttf-fira-code": {FEDORA: "fira-code-fonts", OPENSUSE: "fira-code-fonts", DEBIAN: "fonts-firacode"},
    "ttf-hack": {FEDORA: "source-foundry-hack-fonts", OPENSUSE: "hack-fonts", DEBIAN: "fonts-hack"},
    "noto-fonts": {FEDORA: "google-noto-sans-fonts", OPENSUSE: "google-noto-sans-fonts", DEBIAN: "fonts-noto-core"},
    # Debian's main repository has no Ubuntu fonts, so Debian uses the download.
    "ttf-ubuntu-font-family": {FEDORA: None, OPENSUSE: "ubuntu-fonts", DEBIAN: None, "ubuntu": "fonts-ubuntu"},
    "ttf-nunito": {FEDORA: "vernnobile-nunito-fonts", OPENSUSE: None, DEBIAN: None, "ubuntu": "fonts-nunito"},
    # Tools
    "kvantum": {FEDORA: "kvantum", OPENSUSE: "kvantum-qt6", DEBIAN: "qt6-style-kvantum"},
    "papirus-icon-theme": {},
    "fastfetch": {},
    "starship": {FEDORA: None},
    "zoxide": {},
    "chafa": {},
    "git": {},
    "python-pyqt6": {FEDORA: "python3-pyqt6", OPENSUSE: "python3-PyQt6", DEBIAN: "python3-pyqt6"},
}

# Per-user font downloads used when a family has no package (or it is not found).
FONT_FALLBACKS: dict[str, list[str]] = {
    "ttf-nerd-fonts-symbols": [
        "https://github.com/ryanoasis/nerd-fonts/releases/download/v3.5.1/NerdFontsSymbolsOnly.zip",
    ],
    "inter-font": [f"{_GOOGLE_FONTS}/ofl/inter/Inter%5Bopsz,wght%5D.ttf"],
    "ttf-jetbrains-mono": [
        f"{_GOOGLE_FONTS}/ofl/jetbrainsmono/JetBrainsMono%5Bwght%5D.ttf",
        f"{_GOOGLE_FONTS}/ofl/jetbrainsmono/JetBrainsMono-Italic%5Bwght%5D.ttf",
    ],
    "ttf-roboto": [f"{_GOOGLE_FONTS}/ofl/roboto/Roboto%5Bwdth,wght%5D.ttf"],
    "ttf-roboto-mono": [
        f"{_GOOGLE_FONTS}/ofl/robotomono/RobotoMono%5Bwght%5D.ttf",
        f"{_GOOGLE_FONTS}/ofl/robotomono/RobotoMono-Italic%5Bwght%5D.ttf",
    ],
    "ttf-fira-code": [f"{_GOOGLE_FONTS}/ofl/firacode/FiraCode%5Bwght%5D.ttf"],
    "ttf-hack": ["https://github.com/source-foundry/Hack/releases/download/v3.003/Hack-v3.003-ttf.zip"],
    "noto-fonts": [f"{_GOOGLE_FONTS}/ofl/notosans/NotoSans%5Bwdth,wght%5D.ttf"],
    "ttf-ubuntu-font-family": [
        f"{_GOOGLE_FONTS}/ufl/ubuntu/Ubuntu-{style}.ttf" for style in ("Regular", "Italic", "Medium", "Bold")
    ]
    + [f"{_GOOGLE_FONTS}/ufl/ubuntumono/UbuntuMono-{style}.ttf" for style in ("Regular", "Italic", "Bold")],
    "ttf-nunito": [
        f"{_GOOGLE_FONTS}/ofl/nunito/Nunito%5Bwght%5D.ttf",
        f"{_GOOGLE_FONTS}/ofl/nunito/Nunito-Italic%5Bwght%5D.ttf",
    ],
}


@dataclass
class Resolution:
    """How a list of canonical packages is satisfied on one distribution."""

    native: dict[str, str]  # canonical -> native package name
    font_downloads: list[str]  # canonical font names to fetch per-user
    unavailable: list[str]  # canonical names that cannot be provided


def native_name(canonical: str, distro: Distro) -> str | None:
    if distro.family == ARCH:
        return canonical
    entry = PACKAGES.get(canonical, {})
    for key in (distro.id, distro.family):
        if key in entry:
            return entry[key]
    return canonical  # same name everywhere, or unknown: try it as-is


def resolve(canonical: list[str], distro: Distro, missing_from_repos: set[str] | None = None) -> Resolution:
    """Translate ``canonical`` names. ``missing_from_repos`` are native names the
    package manager reported as not available, so they also use a fallback."""
    missing_from_repos = missing_from_repos or set()
    result = Resolution({}, [], [])
    for name in dict.fromkeys(canonical):
        native = native_name(name, distro)
        if native and native not in missing_from_repos:
            result.native[name] = native
        elif name in FONT_FALLBACKS:
            result.font_downloads.append(name)
        else:
            result.unavailable.append(name)
    return result
