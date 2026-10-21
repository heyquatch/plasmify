"""Package manager integration for pacman, dnf, zypper and apt.

Each backend answers three questions: which packages are not installed, how
much space installing them takes, and which ones the repositories do not have.
Installing always asks for privileges once, through polkit (``pkexec``) when
available, and never pulls in optional/recommended extras. Every program in that
command is called by its full path.

Output parsing lives in small pure functions so it can be unit-tested without
the package manager present.
"""

from __future__ import annotations

import os
import re
import subprocess

from plasmify import rootops
from plasmify.distro import ARCH, DEBIAN, FEDORA, OPENSUSE, Distro
from plasmify.system import Runner

SUDO = "/usr/bin/sudo"
_C_LOCALE = dict(os.environ, LC_ALL="C", LANG="C")
_UNITS = {
    "b": 1,
    "kib": 1024,
    "k": 1024,
    "kb": 1000,
    "mib": 1024**2,
    "m": 1024**2,
    "mb": 1000**2,
    "gib": 1024**3,
    "g": 1024**3,
    "gb": 1000**3,
}


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False, env=_C_LOCALE)


def parse_size(number: str, unit: str) -> int:
    return int(float(number.replace(",", "")) * _UNITS.get(unit.lower(), 1))


def _elevate() -> list[str]:
    """pkexec, or sudo without it, by full path: a program running as you could put its own
    pkexec first in your PATH and show a fake password dialog."""
    return [rootops.PKEXEC] if os.access(rootops.PKEXEC, os.X_OK) else [SUDO]


# ----- parsers ------------------------------------------------------------


def parse_info_blocks(text: str, name_key: str, size_key: str) -> dict[str, int]:
    """Parse ``Key : value`` blocks (pacman -Si, zypper info, dnf info) into name -> bytes.

    The first block per name wins, so a package listed by several repositories counts once.
    """
    name_re = re.compile(rf"^{re.escape(name_key)}\s*:\s*(\S+)", re.MULTILINE)
    size_re = re.compile(rf"^{re.escape(size_key)}\s*:\s*([\d.,]+)\s*([A-Za-z]+)", re.MULTILINE)
    sizes: dict[str, int] = {}
    for block in re.split(r"\n\s*\n", text):
        name, size = name_re.search(block), size_re.search(block)
        if name and size and name.group(1) not in sizes:
            sizes[name.group(1)] = parse_size(*size.groups())
    return sizes


def parse_apt_show(text: str) -> dict[str, int]:
    """``apt-cache show`` reports ``Installed-Size`` in KiB."""
    sizes: dict[str, int] = {}
    for block in re.split(r"\n\s*\n", text):
        name = re.search(r"^Package:\s*(\S+)", block, re.MULTILINE)
        size = re.search(r"^Installed-Size:\s*(\d+)", block, re.MULTILINE)
        if name and size and name.group(1) not in sizes:
            sizes[name.group(1)] = int(size.group(1)) * 1024
    return sizes


def parse_rpm_query(text: str, packages: list[str]) -> list[str]:
    """``rpm -q a b`` prints ``package b is not installed`` for missing ones."""
    absent = set(re.findall(r"^package (\S+) is not installed", text, re.MULTILINE))
    return [p for p in packages if p in absent]


def parse_dpkg_status(text: str, packages: list[str]) -> list[str]:
    """``dpkg-query -W -f '${Package} ${db:Status-Abbrev}\\n'``: ``ii`` means installed."""
    installed = {line.split()[0] for line in text.splitlines() if len(line.split()) >= 2 and line.split()[1] == "ii"}
    return [p for p in packages if p not in installed]


# ----- backends -----------------------------------------------------------


class Backend:
    name = ""

    def missing(self, packages: list[str]) -> list[str]:
        raise NotImplementedError

    def sizes(self, packages: list[str]) -> dict[str, int]:
        raise NotImplementedError

    def install_command(self, packages: list[str]) -> list[str]:
        """What root runs, every program by full path: sudo may keep your PATH."""
        raise NotImplementedError

    def not_in_repos(self, packages: list[str]) -> list[str]:
        """Packages neither installed nor installable from the configured repositories."""
        absent = self.missing(packages)
        known = self.sizes(absent)
        return [p for p in absent if p not in known]

    def installed_size(self, packages: list[str]) -> int:
        return sum(self.sizes(packages).values()) if packages else 0

    def install(self, packages: list[str], runner: Runner) -> None:
        if packages:
            runner.run([*_elevate(), *self.install_command(packages)])


class Pacman(Backend):
    name = "pacman"

    def missing(self, packages):
        if not packages:
            return []
        absent = set(_run(["pacman", "-T", *packages]).stdout.split())
        return [p for p in dict.fromkeys(packages) if p in absent]

    def sizes(self, packages):
        if not packages:
            return {}
        return parse_info_blocks(_run(["pacman", "-Si", *packages]).stdout, "Name", "Installed Size")

    def install_command(self, packages):
        return ["/usr/bin/pacman", "-S", "--needed", "--noconfirm", *packages]


class Dnf(Backend):
    name = "dnf"

    def missing(self, packages):
        if not packages:
            return []
        return parse_rpm_query(_run(["rpm", "-q", *packages]).stdout, list(dict.fromkeys(packages)))

    def sizes(self, packages):
        if not packages:
            return {}
        # Works with both dnf4 and dnf5 ("Installed size" / "Size" lines).
        text = _run(["dnf", "--quiet", "info", "--available", *packages]).stdout
        sizes = parse_info_blocks(text, "Name", "Installed size")
        return sizes or parse_info_blocks(text, "Name", "Size")

    def install_command(self, packages):
        return ["/usr/bin/dnf", "install", "-y", "--setopt=install_weak_deps=False", *packages]


class Zypper(Backend):
    name = "zypper"

    def missing(self, packages):
        if not packages:
            return []
        return parse_rpm_query(_run(["rpm", "-q", *packages]).stdout, list(dict.fromkeys(packages)))

    def sizes(self, packages):
        if not packages:
            return {}
        text = _run(["zypper", "--non-interactive", "--quiet", "info", *packages]).stdout
        return parse_info_blocks(text, "Name", "Installed Size")

    def install_command(self, packages):
        return ["/usr/bin/zypper", "--non-interactive", "install", "--no-recommends", *packages]


class Apt(Backend):
    name = "apt"

    def missing(self, packages):
        if not packages:
            return []
        text = _run(["dpkg-query", "-W", "-f", "${Package} ${db:Status-Abbrev}\n", *packages]).stdout
        return parse_dpkg_status(text, list(dict.fromkeys(packages)))

    def sizes(self, packages):
        if not packages:
            return {}
        return parse_apt_show(_run(["apt-cache", "show", "--no-all-versions", *packages]).stdout)

    def install_command(self, packages):
        return [
            "/usr/bin/env",
            "DEBIAN_FRONTEND=noninteractive",
            "/usr/bin/apt-get",
            "install",
            "-y",
            "--no-install-recommends",
            *packages,
        ]


_BACKENDS = {ARCH: Pacman, FEDORA: Dnf, OPENSUSE: Zypper, DEBIAN: Apt}


def backend_for(distro: Distro) -> Backend:
    return _BACKENDS[distro.family]()


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
