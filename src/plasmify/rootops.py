"""Everything Plasmify does as root for the login screen, in one place.

The only other use of root is installing missing packages (see ``plasmify.packages``). The
rules that keep the login screen step safe even if another program running as you tampers with
Plasmify's files while the password dialog is open:

* root never reads a path a normal process could swap: the login picture is read here (as you,
  without following symlinks) and handed to root on standard input;
* root never copies whole files back from a backup: it only rewrites the few keys Plasmify
  changed, with values checked against strict patterns first;
* the scripts are fixed text; every value is a positional argument, checked again inside the
  script, and every program is called by absolute path (``KWRITECONFIG6`` too, checked before
  the password is asked for).
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

PKEXEC = "/usr/bin/pkexec"
SH = "/bin/sh"
# Written out in both scripts. Never looked up in your PATH, where a program running as you
# could put its own kwriteconfig6 first; every supported distribution installs it here.
KWRITECONFIG6 = "/usr/bin/kwriteconfig6"
LOGIN_DIR = "/usr/share/wallpapers/plasmify-login"
MAX_LOGIN_IMAGE = 50 * 1024 * 1024
LOGIN_NAMES = ("background.png", "background.jpg", "background.jpeg", "background.webp")

# manager -> (config file, [(group path, key)]): the only keys Plasmify ever writes as root.
MANAGED = {
    "plasmalogin": (
        "/etc/plasmalogin.conf",
        [(("Greeter",), "WallpaperPluginId"), (("Greeter", "Wallpaper", "org.kde.image", "General"), "Image")],
    ),
    "sddm": ("/usr/share/sddm/themes/breeze/theme.conf.user", [(("General",), "background")]),
}
_PLUGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_IMAGE = re.compile(r"(file://)?/[A-Za-z0-9._+/ -]{1,300}")
ABSENT = "<plasmify: no value>"  # kreadconfig6 default; arguments cannot carry NUL

LOGIN_SCRIPT = r"""set -eu
umask 022
case "$1" in background.png|background.jpg|background.jpeg|background.webp) ;; *) exit 2 ;; esac
dir=/usr/share/wallpapers/plasmify-login
/usr/bin/install -d -m 755 -- "$dir"
tmp=$(/usr/bin/mktemp -- "$dir/.incoming.XXXXXX")
/usr/bin/head -c 52428800 > "$tmp"
/usr/bin/chmod 644 -- "$tmp"
/usr/bin/mv -f -- "$tmp" "$dir/$1"
case "$2" in
plasmalogin)
    /usr/bin/kwriteconfig6 --file /etc/plasmalogin.conf --group Greeter --key WallpaperPluginId org.kde.image
    /usr/bin/kwriteconfig6 --file /etc/plasmalogin.conf --group Greeter --group Wallpaper --group org.kde.image \
        --group General --key Image "file://$dir/$1"
    ;;
sddm)
    /usr/bin/kwriteconfig6 --file /usr/share/sddm/themes/breeze/theme.conf.user --group General \
        --key background "$dir/$1"
    ;;
*) exit 2 ;;
esac
"""

# $1 = manager, then per managed key: "set" or "delete", then the value (ignored for delete).
RESTORE_SCRIPT = r"""set -eu
manager=$1; shift
case "$manager" in
plasmalogin) file=/etc/plasmalogin.conf ;;
sddm) file=/usr/share/sddm/themes/breeze/theme.conf.user ;;
*) exit 2 ;;
esac
put() {  # put ACTION VALUE GROUP... -- KEY
    action=$1; value=$2; shift 2
    groups=""
    while [ "$1" != "--" ]; do groups="$groups --group $1"; shift; done
    key=$2
    case "$action" in
    set) /usr/bin/kwriteconfig6 --file "$file" $groups --key "$key" "$value" ;;
    delete) /usr/bin/kwriteconfig6 --file "$file" $groups --key "$key" --delete ;;
    *) exit 2 ;;
    esac
}
if [ "$manager" = plasmalogin ]; then
    put "$1" "$2" Greeter -- WallpaperPluginId
    put "$3" "$4" Greeter Wallpaper org.kde.image General -- Image
else
    put "$1" "$2" General -- background
fi
if ! /usr/bin/grep -qs plasmify-login /etc/plasmalogin.conf /usr/share/sddm/themes/breeze/theme.conf.user; then
    /usr/bin/rm -rf -- /usr/share/wallpapers/plasmify-login
fi
"""


def check_programs() -> None:
    """Refuse before the password prompt, rather than fail as root, when kwriteconfig6 is missing.
    The scripts' other programs come with every system."""
    if not os.access(KWRITECONFIG6, os.X_OK):
        raise ValueError(f"{KWRITECONFIG6} is missing (it comes with KDE Frameworks' KConfig)")


def valid_value(key: str, value: str | None) -> bool:
    """Values root may write back. Anything else in a backup is treated as tampering."""
    if value is None:
        return True
    if key == "WallpaperPluginId":
        return bool(_PLUGIN.fullmatch(value))
    return bool(_IMAGE.fullmatch(value)) and ".." not in value


def read_managed(manager: str) -> dict[str, str | None]:
    """The current values of the managed keys (as you; the config files are world-readable)."""
    path, keys = MANAGED[manager]
    values: dict[str, str | None] = {}
    for groups, key in keys:
        args = ["kreadconfig6", "--file", path]
        for group in groups:
            args += ["--group", group]
        try:
            out = subprocess.run(  # noqa: S603 (fixed program, values are arguments)
                args + ["--key", key, "--default", ABSENT], capture_output=True, text=True, timeout=10, check=False
            ).stdout.rstrip("\n")
        except (OSError, subprocess.TimeoutExpired):
            out = ABSENT
        values[key] = None if out == ABSENT else out
    return values


def restore_command(manager: str, values: dict[str, str | None]) -> list[str]:
    """pkexec command that puts the managed keys back. Raises ValueError on bad values, or
    when a program is missing."""
    if manager not in MANAGED:
        raise ValueError(f"unknown login manager {manager!r}")
    args = []
    for _groups, key in MANAGED[manager][1]:
        value = values.get(key)
        if not valid_value(key, value):
            raise ValueError(f"refusing to restore {key} = {value!r}: not a value Plasmify writes")
        args += ["delete", "-"] if value is None else ["set", value]
    check_programs()
    return [PKEXEC, SH, "-c", RESTORE_SCRIPT, "plasmify-restore", manager, *args]


def read_image(path: Path) -> bytes:
    """Read the login picture as you, refusing symlinks, non-images and oversized files."""
    from plasmify.downloads import is_image_bytes  # local import: downloads pulls in networking

    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(MAX_LOGIN_IMAGE + 1)
    if len(data) > MAX_LOGIN_IMAGE:
        raise ValueError(f"{path.name} is larger than {MAX_LOGIN_IMAGE // (1024 * 1024)} MB")
    if not is_image_bytes(data):
        raise ValueError(f"{path.name} is not a PNG, JPEG, WebP or GIF image")
    return data


def login_command(name: str, manager: str) -> list[str]:
    """pkexec command that stores the picture fed on stdin and points the login screen at it.
    Raises ValueError on an unexpected name or manager, or when a program is missing."""
    if name not in LOGIN_NAMES or manager not in MANAGED:
        raise ValueError("unexpected login picture name or manager")
    check_programs()
    return [PKEXEC, SH, "-c", LOGIN_SCRIPT, "plasmify-login", name, manager]
