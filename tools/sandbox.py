"""A throwaway Plasma desktop for testing Plasmify without touching yours.

It starts a nested KWin on a virtual (invisible) screen with its own D-Bus session,
its own HOME and its own runtime directory, then a plasmashell inside it. Everything
Plasmify does in there (panels, wallpapers, restarts) stays in there, and screenshots
are taken with Spectacle inside the sandbox. Apps inside draw in software (only the nested
KWin uses the graphics card), and no services that talk to devices, the network or your
keyring are started.

    python3 tools/sandbox.py shot out.png          # start, screenshot, stop
    python3 tools/sandbox.py apply rose-pine-cozy  # apply a preset and screenshot it

Used by ``tests/integration`` (run with ``PLASMIFY_SANDBOX=1``). Needs kwin_wayland,
plasmashell, dbus-run-session, spectacle and qdbus6, which a full Plasma 6 desktop usually has
(spectacle and qdbus6 can be separate packages).
"""

from __future__ import annotations

import contextlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
SOCKET = "plasmify-sb"
REQUIRED = ("kwin_wayland", "plasmashell", "dbus-run-session", "spectacle", "qdbus6")

# The sandbox stays away from your hardware and session as far as it can. Everything inside
# draws in software. Only the nested KWin composites on your graphics card, like any nested
# compositor: without it KWin turns compositing off and cannot take screenshots. Services that
# talk to devices, the network or your keyring are never started inside.
SOFTWARE_RENDERING = {
    "QT_QUICK_BACKEND": "software",  # Qt Quick apps (plasmashell, spectacle) draw on the CPU
    "LIBGL_ALWAYS_SOFTWARE": "1",  # other OpenGL users inside get Mesa's CPU renderer
    "GALLIUM_DRIVER": "llvmpipe",
}
BLOCKED_SERVICES = (
    "org.bluez.obex",
    "org.freedesktop.portal.Desktop",
    "org.freedesktop.impl.portal.desktop.kde",
    "org.freedesktop.impl.portal.desktop.gtk",
    "org.freedesktop.impl.portal.desktop.kwallet",
    "org.kde.kdeconnect",
    "org.kde.kscreen.osdService",
    "org.kde.kwalletd6",
    "org.kde.secretservicecompat",
    "org.kde.runners.baloo",
)
QUIET_KDED_MODULES = (
    "kameleon",  # keyboard lights, through a system helper
    "bluedevil",
    "networkmanagement",
    "kded_bolt",
    "device_automounter",
    "devicenotifications",
    "soliduiserver",
    "audioshortcutsservice",
    "freespacenotifier",
    "smbwatcher",
    "remotenotifier",
    "wpad_detector",
    "geotimezoned",
    "oom_notifier",
    "kded_accounts",
    "gtkconfig",
    "baloosearchmodule",
    "filenamesearchmodule",
    "donationmessage",
    "browserintegrationreminder",
)


def available() -> bool:
    return all(shutil.which(tool) for tool in REQUIRED)


class Sandbox:
    def __init__(self, width: int = 1600, height: int = 900, seed: tuple[str, ...] = ()) -> None:
        """``seed``: folders under your ~/.local/share to copy in (e.g. ``icons`` to skip
        cursor downloads, ``plasmify`` to reuse downloaded wallpapers)."""
        self.width, self.height, self.seed = width, height, seed
        self.root: Path | None = None
        self.session: subprocess.Popen | None = None

    # ----- lifecycle --------------------------------------------------------------
    def __enter__(self) -> Sandbox:
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    def start(self) -> None:
        # Wayland socket paths are limited to 108 bytes, so keep the root short.
        self.root = Path(tempfile.mkdtemp(prefix="psb-"))
        self.home = self.root / "h"
        self.runtime = self.root / "r"
        self.home.mkdir()
        self.runtime.mkdir(mode=0o700)
        share = self.home / ".local/share"
        share.mkdir(parents=True)
        self._quiet_services(share)
        for name in self.seed:
            source = Path.home() / ".local/share" / name
            if source.is_dir():
                shutil.copytree(source, share / name, symlinks=True)
        bus_file = self.root / "bus"
        inner = self.root / "inner.sh"
        inner.write_text(
            "#!/bin/sh\n"
            f'echo "$DBUS_SESSION_BUS_ADDRESS" > "{bus_file}"\n'
            f"exec kwin_wayland --virtual --width {self.width} --height {self.height} "
            f"--socket {SOCKET} --no-lockscreen --exit-with-session 'sleep infinity'\n"
        )
        inner.chmod(0o755)
        self.session = subprocess.Popen(  # noqa: S603 (fixed command)
            ["dbus-run-session", "--", str(inner)],
            env=self._base_env(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._wait(lambda: bus_file.exists() and (self.runtime / SOCKET).exists(), 20, "KWin")
        self.bus = bus_file.read_text().strip()
        # plasmashell waits for the activity manager; kded provides the rest of a session.
        for command in (["/usr/lib/kactivitymanagerd"], ["kded6"]):
            if Path(command[0]).exists() or shutil.which(command[0]):
                self.spawn(command)
        self.spawn(["plasmashell"])
        self._wait(self.shell_ready, 60, "plasmashell")

    def _quiet_services(self, share: Path) -> None:
        """Keep device, network and keyring services out of the sandbox (see BLOCKED_SERVICES)."""
        # The session bus prefers service files in XDG_DATA_HOME: these ones start nothing.
        services = share / "dbus-1" / "services"
        services.mkdir(parents=True)
        for name in BLOCKED_SERVICES:
            (services / f"{name}.service").write_text(f"[D-BUS Service]\nName={name}\nExec=/bin/false\n")
        config = self.home / ".config"
        config.mkdir(parents=True, exist_ok=True)
        modules = "".join(f"[Module-{name}]\nautoload=false\n\n" for name in QUIET_KDED_MODULES)
        for rc in ("kded5rc", "kded6rc"):  # KF6's kded still reads kded5rc for these
            (config / rc).write_text(modules)

    def stop(self) -> None:
        if self.root is None:
            return
        runtime = str(self.runtime)
        if self.session is not None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.session.pid, signal.SIGTERM)
        # Processes started inside (plasmashell, kded) may have left the process group.
        for pid in self._inside_pids(runtime):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGTERM)
        time.sleep(1)
        for pid in self._inside_pids(runtime):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGKILL)
        shutil.rmtree(self.root, ignore_errors=True)
        self.root = None

    # ----- running things inside -----------------------------------------------------
    def _base_env(self) -> dict[str, str]:
        return {
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "HOME": str(self.home),
            # Explicit, because some tools (fastfetch) find the home folder through the user
            # database instead of $HOME and would otherwise read or write your real files.
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_DATA_HOME": str(self.home / ".local/share"),
            "XDG_STATE_HOME": str(self.home / ".local/state"),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "XDG_RUNTIME_DIR": str(self.runtime),
            "XDG_CURRENT_DESKTOP": "KDE",
            "XDG_SESSION_TYPE": "wayland",
            "KDE_FULL_SESSION": "true",
            "KDE_SESSION_VERSION": "6",
            "LANG": os.environ.get("LANG", "C.UTF-8"),
            **SOFTWARE_RENDERING,
        }

    def env(self) -> dict[str, str]:
        return {
            **self._base_env(),
            "DBUS_SESSION_BUS_ADDRESS": self.bus,
            "WAYLAND_DISPLAY": SOCKET,
            "QT_QPA_PLATFORM": "wayland",
            "PYTHONPATH": str(SRC),
        }

    def run(
        self, args: list[str], timeout: float = 120, check: bool = False, input_text: str | None = None
    ) -> subprocess.CompletedProcess:
        return subprocess.run(  # noqa: S603 (callers pass fixed commands)
            args, env=self.env(), capture_output=True, text=True, timeout=timeout, check=check, input=input_text
        )

    def spawn(self, args: list[str]) -> None:
        subprocess.Popen(  # noqa: S603 (fixed commands)
            args, env=self.env(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )

    def python(self, code: str, timeout: float = 300) -> subprocess.CompletedProcess:
        """Run Python code inside the sandbox with Plasmify importable."""
        return self.run([sys.executable, "-c", code], timeout=timeout)

    def script(self, js: str) -> str:
        """Evaluate a Plasma shell script and return what it printed."""
        proc = self.run(["qdbus6", "org.kde.plasmashell", "/PlasmaShell", "org.kde.PlasmaShell.evaluateScript", js])
        return proc.stdout

    def shell_ready(self) -> bool:
        out = self.script("print(desktops().length);").strip()
        return out.isdigit() and int(out) > 0

    def screenshot(self, path: Path) -> Path:
        self.run(["spectacle", "-b", "-n", "-f", "-o", str(path)], timeout=30)
        return path

    # ----- helpers -------------------------------------------------------------------
    def _wait(self, condition, timeout: float, what: str) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with contextlib.suppress(Exception):
                if condition():
                    return
            time.sleep(0.3)
        self.stop()
        raise RuntimeError(f"{what} did not start in the sandbox")

    @staticmethod
    def _inside_pids(runtime: str) -> list[int]:
        pids = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                environ = (entry / "environ").read_bytes()
            except OSError:
                continue
            if f"XDG_RUNTIME_DIR={runtime}".encode() in environ.split(b"\0"):
                pids.append(int(entry.name))
        return pids


APPLY = """
import json, sys
from plasmify import engine, presets
from plasmify.system import Runner
preset = presets.customized(presets.find({preset!r}), json.loads({overrides!r}))
plan = engine.make_plan(preset, {steps!r}, check_packages=False)
_, warnings = engine.apply(plan, Runner(), log=lambda m: None)
print(json.dumps(warnings))
"""


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in ("shot", "apply"):
        print(__doc__)
        return 2
    if not available():
        print("Missing: " + ", ".join(t for t in REQUIRED if not shutil.which(t)))
        return 1
    with Sandbox(seed=("icons", "plasmify")) as box:
        if argv[0] == "apply":
            preset = argv[1]
            out = box.python(APPLY.format(preset=preset, overrides=argv[2] if len(argv) > 2 else "{}", steps=None))
            print(out.stdout or out.stderr)
            target = Path(f"{preset}.png")
        else:
            target = Path(argv[1] if len(argv) > 1 else "sandbox.png")
        box.screenshot(target.resolve())
        print(f"Screenshot: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
