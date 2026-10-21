"""Thin wrappers around the system tools Plasmify relies on.

Everything that touches the running desktop goes through this module so that
the rest of the code base can be exercised in dry-run mode and in tests.
"""

from __future__ import annotations

import ast
import contextlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


def xdg_path(env: str, fallback: str) -> Path:
    value = os.environ.get(env)
    return Path(value) if value else Path.home() / fallback


CONFIG_HOME = xdg_path("XDG_CONFIG_HOME", ".config")
DATA_HOME = xdg_path("XDG_DATA_HOME", ".local/share")
STATE_HOME = xdg_path("XDG_STATE_HOME", ".local/state")
CACHE_HOME = xdg_path("XDG_CACHE_HOME", ".cache")

PLASMIFY_DATA = DATA_HOME / "plasmify"
PLASMIFY_STATE = STATE_HOME / "plasmify"


class CommandError(RuntimeError):
    pass


@dataclass
class Runner:
    """Executes commands, or only records them when ``dry_run`` is set."""

    dry_run: bool = False
    log: list[str] = field(default_factory=list)

    def run(
        self,
        args: list[str],
        *,
        check: bool = True,
        capture: bool = False,
        input_text: str | None = None,
        mutating: bool = True,
        input_bytes: bytes | None = None,
    ) -> str:
        """Run ``args``. Read-only commands (``mutating=False``) run even in dry-run mode.
        ``input_bytes`` feeds binary data on standard input (the login picture for root)."""
        self.log.append(" ".join(args))
        if self.dry_run and mutating:
            return ""
        env = dict(os.environ, LC_ALL="C")
        try:
            if input_bytes is not None:
                raw = subprocess.run(args, input=input_bytes, capture_output=True, env=env, check=False)
                stdout, stderr, code = (
                    raw.stdout.decode(errors="replace"),
                    raw.stderr.decode(errors="replace"),
                    raw.returncode,
                )
            else:
                proc = subprocess.run(args, input=input_text, text=True, capture_output=True, env=env, check=False)
                stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
        except OSError as exc:
            # A missing tool (no KDE Frameworks, a container) fails like any other command.
            stdout, stderr, code = "", f"{exc.strerror or exc}", 127
        if check and code != 0:
            message = (stderr or stdout).strip()
            raise CommandError(f"{args[0]} failed ({code}): {message}")
        return stdout if capture else ""

    def kwrite(self, file: str, group: str | list[str], key: str, value: str | int | float | bool) -> None:
        args = self._kwrite_args(file, group)
        if isinstance(value, bool):
            args += ["--type", "bool"]
            value = "true" if value else "false"
        self.run(args + ["--key", key, str(value)])

    def kdelete(self, file: str, group: str | list[str], key: str) -> None:
        self.run(self._kwrite_args(file, group) + ["--key", key, "--delete"])

    def krevert(self, path: Path, group: str | list[str], key: str) -> None:
        """Take ``key`` out of the user's own config file ``path``, so the default from the
        distribution or Global Theme (kdedefaults, XDG_CONFIG_DIRS) shows again. kdelete cannot:
        for a key with such a default, kwriteconfig6 writes "key[$d]", which hides the default too
        (a stock Fedora got Plasma's light panels and the Breeze splash instead of its own). Opened
        by its full path, the file has no defaults below it, so the key is simply removed; writing it
        first also clears a "key[$d]" left by an earlier version."""
        args = ["kwriteconfig6", "--file", str(path)]
        for name in [group] if isinstance(group, str) else group:
            args += ["--group", name]
        self.run(args + ["--key", key, "-"])
        self.run(args + ["--key", key, "--delete"])

    @staticmethod
    def _kwrite_args(file: str, group: str | list[str]) -> list[str]:
        groups = [group] if isinstance(group, str) else group
        args = ["kwriteconfig6"]
        if (file, groups) == ("kwinrc", ["Plugins"]):
            # KWin loads or unloads an effect when it is told that its switch changed, as System
            # Settings does; its reconfigure does neither (a new cube stayed off until the next login).
            args.append("--notify")
        args += ["--file", file]
        for g in groups:
            args += ["--group", g]
        return args

    def check_config_reader(self) -> None:
        """Raise CommandError unless kreadconfig6 works. ``kread`` reads every setting as empty
        when it does not, which callers that delete or save things must not mistake for real."""
        self.run(["kreadconfig6", "--file", "kdeglobals", "--group", "General", "--key", "ColorScheme"],
                 capture=True, mutating=False)  # fmt: skip

    def kread(self, file: str, group: str, key: str, default: str = "") -> str:
        out = self.run(
            ["kreadconfig6", "--file", file, "--group", group, "--key", key, "--default", default],
            capture=True,
            check=False,
            mutating=False,
        )
        return out.strip()

    def dbus_call(
        self, service: str, path: str, method: str, *args: str, check: bool = True, mutating: bool = True
    ) -> str:
        """Call a session D-Bus method with string arguments, using whichever tool exists.

        Returns the reply as text (a single string or number reply is unwrapped)."""
        qdbus = qdbus_tool()
        if qdbus:
            return self.run([qdbus, service, path, method, *args], check=check, capture=True, mutating=mutating)
        if which("gdbus"):
            # gdbus parses arguments as GVariant text; JSON string escaping is compatible.
            out = self.run(
                ["gdbus", "call", "--session", "--dest", service, "--object-path", path, "--method", method]
                + [json.dumps(arg) for arg in args],
                check=check,
                capture=True,
                mutating=mutating,
            )
            return _unwrap_gvariant(out)
        if check:
            raise CommandError("Neither qdbus (Qt 6) nor gdbus was found")
        return ""

    def plasma_script(self, script: str, *, mutating: bool = True, check: bool = True) -> str:
        """Run a Plasma shell script; returns whatever the script printed.

        A shell that is still starting (its name is on the bus, its script interface not yet) did
        not run the script: wait until it answers and run it once more. Seen right after a reload,
        when the wallpaper step then failed and the last preset's wallpaper stayed."""
        call = (SHELL_SERVICE, "/PlasmaShell", "org.kde.PlasmaShell.evaluateScript", script)
        try:
            return self.dbus_call(*call, check=check, mutating=mutating)
        except CommandError as exc:
            if not SHELL_STARTING.search(str(exc)) or not wait_for_shell(self, 15):
                raise
            return self.dbus_call(*call, check=check, mutating=mutating)

    def notify_style_changed(self) -> None:
        """Tell running KDE apps that the widget style changed (KGlobalSettings StyleChanged, as
        System Settings does), so they switch style now instead of at their next start."""
        self._notify_kde(2)

    def notify_palette_changed(self) -> None:
        """Tell running KDE apps that the colours in kdeglobals changed (KGlobalSettings
        PaletteChanged, as plasma-apply-colorscheme does after writing them)."""
        self._notify_kde(0)

    def _notify_kde(self, kind: int) -> None:
        if which("dbus-send"):
            args = ["dbus-send", "--session", "--type=signal", "/KGlobalSettings",
                    "org.kde.KGlobalSettings.notifyChange", f"int32:{kind}", "int32:0"]  # fmt: skip
        elif which("gdbus"):
            args = ["gdbus", "emit", "--session", "--object-path", "/KGlobalSettings",
                    "--signal", "org.kde.KGlobalSettings.notifyChange", str(kind), "0"]  # fmt: skip
        else:
            return
        self.run(args, check=False)

    def kwin_reconfigure(self) -> None:
        self.dbus_call("org.kde.KWin", "/KWin", "org.kde.KWin.reconfigure", check=False)

    def kwin_reconfigure_effect(self, name: str) -> None:
        """Make KWin read the settings of the running effect ``name`` again. Its reconfigure only
        loads effects that were turned on; one that runs already keeps its old settings (a new
        blur strength showed only after the next login)."""
        self.dbus_call("org.kde.KWin", "/Effects", "org.kde.kwin.Effects.reconfigureEffect", name, check=False)


def _unwrap_gvariant(text: str) -> str:
    """``('hello',)`` or ``(uint32 42,)`` from gdbus becomes ``hello`` / ``42``."""
    text = text.strip()
    match = re.fullmatch(r"\((?:uint32|int32|uint64|int64)? ?(-?\d+),\)", text)
    if match:
        return match.group(1)
    if text.startswith("(") and text.endswith(",)"):
        try:
            value = ast.literal_eval(text[1:-2])
        except (SyntaxError, ValueError):
            return text
        return str(value)
    return text


# ----- the Plasma shell process --------------------------------------------------------
SHELL_SERVICE = "org.kde.plasmashell"
SHELL_UNIT = "plasma-plasmashell.service"
# How qdbus and gdbus say the shell is not ready for scripts yet (the script did not run).
SHELL_STARTING = re.compile(
    r"Cannot find '.*' in object|does not exist|ServiceUnknown|UnknownObject|UnknownMethod|No such object path"
)


def shell_pid(runner: Runner) -> int | None:
    """PID of the plasmashell that owns the shell's D-Bus name on *this* session bus."""
    out = runner.dbus_call(
        "org.freedesktop.DBus",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus.GetConnectionUnixProcessID",
        SHELL_SERVICE,
        check=False,
        mutating=False,
    ).strip()
    return int(out) if out.isdigit() else None


def _runs_in_unit(pid: int) -> bool:
    try:
        return SHELL_UNIT in Path(f"/proc/{pid}/cgroup").read_text()
    except OSError:
        return False


def shell_ready(runner: Runner) -> bool:
    """True when plasmashell answers scripts and has at least one desktop."""
    out = runner.plasma_script("print(desktops().length);", mutating=False, check=False).strip()
    return out.isdigit() and int(out) > 0


def wait_for_shell(runner: Runner, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if shell_ready(runner):
            time.sleep(0.5)  # let it finish loading the panels it just created
            return True
        time.sleep(0.5)
    return False


def stop_shell(runner: Runner) -> str:
    """Stop plasmashell cleanly so it saves its configuration first.

    Returns how it was running: ``systemd`` (the usual Plasma 6 session), ``process``
    (sessions without systemd, nested test sessions) or ``none``.
    """
    if runner.dry_run:
        runner.log.append("stop plasmashell")
        return "none"
    pid = shell_pid(runner)
    if pid is None:
        return "none"
    mode = "systemd" if _runs_in_unit(pid) else "process"
    if mode == "systemd":
        runner.run(["systemctl", "--user", "stop", SHELL_UNIT], check=False)
    elif which("kquitapp6"):
        runner.run(["kquitapp6", "plasmashell"], check=False)
    deadline = time.monotonic() + 15
    while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    if Path(f"/proc/{pid}").exists():
        with contextlib.suppress(OSError):
            os.kill(pid, 15)
        time.sleep(1)
    return mode


def start_shell(runner: Runner, mode: str = "systemd") -> bool:
    """Start plasmashell again and wait until it is ready. Never gives up quietly: if the
    systemd unit refuses (for example after many quick restarts), it starts the shell
    directly so the user is not left with an empty screen."""
    if runner.dry_run:
        runner.log.append("start plasmashell")
        return True
    if mode in ("systemd", "none") and which("systemctl"):
        runner.run(["systemctl", "--user", "reset-failed", SHELL_UNIT], check=False)
        runner.run(["systemctl", "--user", "start", SHELL_UNIT], check=False)
        if wait_for_shell(runner, 30):
            return True
    if shell_pid(runner) is None:
        command = ["kstart", "plasmashell"] if which("kstart") else ["plasmashell"]
        runner.log.append(" ".join(command))
        subprocess.Popen(  # noqa: S603 (fixed command)
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    return wait_for_shell(runner, 30)


def restart_shell(runner: Runner) -> bool:
    return start_shell(runner, stop_shell(runner))


class UnsafePathError(ValueError):
    pass


def safe_child(base: Path, name: str) -> Path:
    """``base / name``, refusing anything that is not a plain direct child of ``base``.

    Every directory Plasmify creates or deletes is built with this, so a crafted
    name (``..``, ``a/b``, an absolute path or a symlink trick) can never reach
    outside its folder even if validation were bypassed.
    """
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise UnsafePathError(f"Refusing unsafe name: {name!r}")
    candidate = base / name
    if candidate.resolve(strict=False).parent != base.resolve(strict=False):
        raise UnsafePathError(f"Refusing path outside {base}: {name!r}")
    return candidate


def which(name: str) -> str | None:
    return shutil.which(name)


# qdbus for Qt 6 is packaged under different names: qdbus6 (Arch, openSUSE, Debian),
# qdbus-qt6 (Fedora), or only inside the Qt libexec directory.
QDBUS_CANDIDATES = (
    "qdbus6",
    "qdbus-qt6",
    "/usr/lib/qt6/bin/qdbus",
    "/usr/lib64/qt6/bin/qdbus",
    "/usr/lib/x86_64-linux-gnu/qt6/bin/qdbus",
    "/usr/lib/aarch64-linux-gnu/qt6/bin/qdbus",
)

LIBEXEC_DIRS = (
    "/usr/lib",
    "/usr/libexec",
    "/usr/lib64/libexec",
    "/usr/lib/x86_64-linux-gnu/libexec",
    "/usr/lib/aarch64-linux-gnu/libexec",
)


def qdbus_tool() -> str | None:
    for candidate in QDBUS_CANDIDATES:
        found = which(candidate) if "/" not in candidate else (candidate if os.access(candidate, os.X_OK) else None)
        if found:
            return found
    return None


def libexec_tool(name: str) -> str | None:
    """Locate a KDE helper that lives outside PATH (e.g. plasma-changeicons)."""
    for directory in LIBEXEC_DIRS:
        candidate = Path(directory) / name
        if os.access(candidate, os.X_OK):
            return str(candidate)
    return which(name)


# plasmashell starts Qt before it reads --version, so it gets a platform that needs no display (SSH,
# a TTY); some builds crash on one of them (Fedora 44's on offscreen).
VERSION_PLATFORMS = ("minimal", "offscreen")
# Asking the package manager instead, when plasmashell cannot be started at all.
PLASMA_PACKAGE_QUERIES = (
    ["pacman", "-Q", "plasma-workspace"],
    ["rpm", "-q", "--qf", "%{VERSION}", "plasma-workspace"],  # Fedora
    ["rpm", "-q", "--qf", "%{VERSION}", "plasma6-workspace"],  # openSUSE
    ["dpkg-query", "-W", "-f", "${Version}", "plasma-workspace"],
)


def version_numbers(text: str) -> tuple[int, ...] | None:
    """The first version in ``text``: "plasmashell 6.6.4", "plasma-workspace 6.8.0-1" or Debian's
    "4:6.3.4-2" all give (6, x, y)."""
    match = re.search(r"(?:^|[\s:])(\d+(?:\.\d+)+)", text)
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def _output(args: list[str], env: dict[str, str] | None = None) -> str:
    try:
        done = subprocess.run(args, capture_output=True, text=True, check=False, env=env, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout if done.returncode == 0 else ""


def plasma_version() -> tuple[int, ...] | None:
    """The installed Plasma version, or None when there is no plasmashell or it cannot be told."""
    if not which("plasmashell"):
        return None
    for platform in VERSION_PLATFORMS:
        version = version_numbers(_output(["plasmashell", "--version"], dict(os.environ, QT_QPA_PLATFORM=platform)))
        if version:
            return version
    for query in PLASMA_PACKAGE_QUERIES:
        if which(query[0]) and (version := version_numbers(_output(query))):
            return version
    return None


def environment_problems() -> list[str]:
    """Return human readable reasons why Plasmify cannot run here (empty when fine)."""
    problems = []
    version = plasma_version()
    if not which("plasmashell"):
        problems.append("KDE Plasma was not found (plasmashell is missing).")
    elif version is None:
        problems.append("The KDE Plasma version could not be read (plasmashell --version failed).")
    elif version[0] < 6:
        problems.append(f"KDE Plasma 6 is required, found {'.'.join(map(str, version))}.")
    from plasmify.distro import detect  # local import: distro has no dependencies on this module

    if detect() is None:
        problems.append(
            "Unsupported distribution: Plasmify supports Arch, Fedora, openSUSE and Debian/Ubuntu families."
        )
    for tool in ("kwriteconfig6", "kreadconfig6", "plasma-apply-colorscheme"):
        if not which(tool):
            problems.append(f"Required tool '{tool}' is missing.")
    if not qdbus_tool() and not which("gdbus"):
        problems.append("No D-Bus command line tool found (install qdbus for Qt 6, or gdbus).")
    if "KDE" not in os.environ.get("XDG_CURRENT_DESKTOP", ""):
        problems.append("Plasmify must be run inside a KDE Plasma session.")
    return problems
