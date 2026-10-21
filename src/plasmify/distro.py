"""Detecting which distribution family Plasmify runs on."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

ARCH, FEDORA, OPENSUSE, DEBIAN = "arch", "fedora", "opensuse", "debian"
FAMILIES = (ARCH, FEDORA, OPENSUSE, DEBIAN)

# Distribution IDs (from os-release ID or ID_LIKE) that belong to each family.
_FAMILY_IDS = {
    ARCH: {"arch", "archlinux", "cachyos", "endeavouros", "manjaro", "garuda", "artix"},
    FEDORA: {"fedora", "nobara", "ultramarine"},
    OPENSUSE: {"opensuse", "suse", "opensuse-tumbleweed", "opensuse-slowroll", "opensuse-leap"},
    DEBIAN: {"debian", "ubuntu", "kubuntu", "neon", "tuxedo", "linuxmint", "pop"},
}


@dataclass(frozen=True)
class Distro:
    id: str
    family: str
    name: str
    version: str = ""

    def __str__(self) -> str:
        return f"{self.name} {self.version}".strip()


def parse_os_release(text: str) -> dict[str, str]:
    values = {}
    for line in text.splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def family_of(os_release: dict[str, str]) -> str | None:
    candidates = [os_release.get("ID", "").lower()]
    candidates += os_release.get("ID_LIKE", "").lower().split()
    for candidate in candidates:
        for family, ids in _FAMILY_IDS.items():
            if candidate in ids or (family == OPENSUSE and candidate.startswith("opensuse")):
                return family
    return None


def detect(path: Path = Path("/etc/os-release")) -> Distro | None:
    """The running distribution, or ``None`` when it is not a supported family."""
    for candidate in (path, Path("/usr/lib/os-release")):
        if candidate.exists():
            info = parse_os_release(candidate.read_text(errors="replace"))
            break
    else:
        return None
    family = family_of(info)
    if family is None:
        return None
    return Distro(
        id=info.get("ID", family).lower(),
        family=family,
        name=info.get("NAME", family),
        version=info.get("VERSION_ID", ""),
    )
