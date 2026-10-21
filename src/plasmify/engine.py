"""Planning and applying a preset, and restoring backups."""

from __future__ import annotations

import dataclasses
import json
import shutil
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from plasmify import activity, backup, distro, downloads, fonts, glass, package_map, packages, system
from plasmify.distro import Distro
from plasmify.presets import Preset
from plasmify.steps import ALL_STEPS, Context, Step, panelstyle, splash
from plasmify.steps.colors import apply_scheme, scheme_name
from plasmify.steps.effects import TUNED_EFFECTS, plugin_switches
from plasmify.system import CONFIG_HOME, DATA_HOME, PLASMIFY_DATA, PLASMIFY_STATE, Runner, libexec_tool

ASSET_REGISTRY = PLASMIFY_STATE / "assets.json"
# Steps whose result plasmashell only picks up after a restart.
RELOAD_SHELL_STEPS = {"icons", "panelstyle"}
# Steps that change the running shell through scripts. They run last, after any restart:
# a restart right after a script can kill plasmashell before it saved the new panels or
# wallpaper, which loses the dock or leaves a black screen.
SHELL_SCRIPT_STEPS = ("wallpaper", "layout")
# Run after the restart and the shell scripts: the splash and login screens use the wallpaper
# files the wallpaper step downloads.
LATE_STEPS = (*SHELL_SCRIPT_STEPS, "splash", "login")
# Where a restore looks for the downloads the restored settings name (see missing_assets).
WALLPAPER_ROOT = PLASMIFY_DATA / "wallpapers"
CURSOR_FOLDERS = (DATA_HOME / "icons", Path.home() / ".icons", Path("/usr/share/icons"), Path("/usr/local/share/icons"))
# Assets used by several presets (fonts); ``clean`` keeps them.
SHARED_ASSETS = "_shared"

Logger = Callable[[str], None]
Progress = Callable[[int, int, str], None]


@dataclass
class Plan:
    preset: Preset
    steps: list[Step]
    canonical_packages: list[str] = field(default_factory=list)  # Arch names, as presets use them
    distro: Distro | None = None
    packages_needed: list[str] = field(default_factory=list)  # native names for this distro
    packages_missing: list[str] = field(default_factory=list)  # native names to install
    font_downloads: list[str] = field(default_factory=list)  # fonts fetched per-user instead
    unavailable: list[str] = field(default_factory=list)  # cannot be provided here
    download_size: int = 0

    @property
    def install_summary(self) -> str:
        parts = []
        if self.packages_missing:
            count = len(self.packages_missing)
            noun = "package" if count == 1 else "packages"
            parts.append(f"Installs {count} {noun} ({packages.human_size(self.download_size)})")
        if self.font_downloads:
            parts.append(f"downloads {len(self.font_downloads)} font(s) to your home folder")
        if self.unavailable:
            parts.append("skips " + ", ".join(self.unavailable) + " (not packaged for this distribution)")
        if not parts:
            return "Nothing new to install"
        text = "; ".join(parts)
        return text[0].upper() + text[1:]


def make_plan(
    preset: Preset,
    step_ids: Iterable[str] | None = None,
    check_packages: bool = True,
    target: Distro | None = None,
) -> Plan:
    # The glass look and its transparency set blur, panel and terminal values (glass.with_companions).
    preset = dataclasses.replace(preset, data=glass.with_companions(preset.data))
    wanted = set(step_ids) if step_ids is not None else None
    steps = [s for s in ALL_STEPS if s.applies_to(preset) and (wanted is None or s.id in wanted)]
    canonical = list(dict.fromkeys(p for s in steps for p in s.packages(preset)))
    plan = Plan(preset, steps, canonical)
    if not check_packages:
        plan.packages_needed = canonical
        return plan

    plan.distro = target or distro.detect()
    if plan.distro is None:
        plan.unavailable = canonical
        return plan
    backend = packages.backend_for(plan.distro)
    first = package_map.resolve(canonical, plan.distro)
    missing = backend.missing(list(first.native.values()))
    sizes = backend.sizes(missing)
    not_in_repos = {name for name in missing if name not in sizes}
    final = package_map.resolve(canonical, plan.distro, not_in_repos)

    plan.packages_needed = list(final.native.values())
    plan.packages_missing = [name for name in missing if name not in not_in_repos]
    plan.font_downloads = [name for name in final.font_downloads if not fonts.downloaded(name)]
    plan.unavailable = final.unavailable
    plan.download_size = sum(sizes.get(name, 0) for name in plan.packages_missing)
    return plan


def apply(
    plan: Plan,
    runner: Runner,
    log: Logger = print,
    progress: Progress | None = None,
    allowed_hosts: Iterable[str] = (),
) -> tuple[backup.Backup | None, list[str]]:
    """Back up, install missing packages, then run every step. Returns the backup and warnings.

    ``allowed_hosts`` are hosts outside the trusted list that the user agreed to contact
    (see :meth:`Preset.untrusted_hosts`); downloads from any other host are refused.
    """
    downloads.allow_hosts(set(allowed_hosts))
    downloads.reset_budget()
    activity.set_context(plan.preset.id)
    try:
        return _apply(plan, runner, log, progress)
    finally:
        downloads.allow_hosts(set())
        activity.set_context("")


def _apply(
    plan: Plan, runner: Runner, log: Logger, progress: Progress | None
) -> tuple[backup.Backup | None, list[str]]:
    total = len(plan.steps) + 2
    report = progress or (lambda *_: None)

    report(0, total, "Backing up your current setup")
    snapshot = None
    if not runner.dry_run:
        from plasmify import record  # it imports this module

        # The record of applied tweaks goes into every backup too: a restore puts it back with
        # the desktop, so the window and "plasmify theme" show (and build on) what is on screen.
        paths = [p for step in plan.steps for p in step.touched_paths(plan.preset)] + [record.TWEAKS_FILE]
        snapshot = backup.create(paths, f"Before {plan.preset.name}")
        log(f"Backup saved as {snapshot.id}")

    warnings: list[str] = []
    report(1, total, "Installing packages")
    if plan.packages_missing and plan.distro is not None:
        log("Installing " + ", ".join(plan.packages_missing))
        backend = packages.backend_for(plan.distro)
        try:
            backend.install(plan.packages_missing, runner)
        except Exception as exc:
            if not runner.dry_run:
                activity.record("packages", backend.name, ", ".join(plan.packages_missing), ok=False, error=str(exc))
            raise
        if not runner.dry_run:
            activity.record("packages", backend.name, ", ".join(plan.packages_missing), size=plan.download_size)
    for name in plan.unavailable:
        warnings.append(f"'{name}' is not packaged for {plan.distro or 'this distribution'}; that part is skipped")

    with tempfile.TemporaryDirectory(prefix="plasmify-") as tmp:
        if plan.font_downloads:
            log("Downloading fonts that are not packaged here: " + ", ".join(plan.font_downloads))
            if not runner.dry_run:
                installed, font_warnings = fonts.install_user_fonts(plan.font_downloads, Path(tmp) / "fonts", runner)
                warnings += font_warnings
                _register_assets(SHARED_ASSETS, installed)
        first = [s for s in plan.steps if s.id not in LATE_STEPS]
        last = sorted((s for s in plan.steps if s.id in LATE_STEPS), key=lambda s: LATE_STEPS.index(s.id))
        index = 2
        for step in first:
            warnings += _run_step(step, plan, runner, log, Path(tmp), report, index, total)
            index += 1
        if last or {s.id for s in plan.steps} & RELOAD_SHELL_STEPS:
            if not runner.dry_run and {s.id for s in plan.steps} & RELOAD_SHELL_STEPS:
                # Panels keep a stale icon theme and panel style until plasmashell restarts.
                report(index, total, "Reloading Plasma")
                log("Reloading the Plasma shell")
                if not system.restart_shell(runner):
                    warnings.append("Plasma did not come back after the reload; log out and in to finish")
            elif any(s.id in SHELL_SCRIPT_STEPS for s in last) and not runner.dry_run:
                if not system.wait_for_shell(runner, 15):
                    warnings.append("The Plasma shell is not responding; panels and wallpaper were skipped")
                    last = [s for s in last if s.id not in SHELL_SCRIPT_STEPS]
        for step in last:
            warnings += _run_step(step, plan, runner, log, Path(tmp), report, index, total)
            index += 1
    report(total, total, "Done")
    return snapshot, warnings


def _run_step(step, plan: Plan, runner: Runner, log: Logger, tmp: Path, report, index: int, total: int) -> list[str]:
    report(index, total, step.title)
    ctx = Context(plan.preset, runner, tmp / step.id, log)
    ctx.workdir.mkdir(parents=True, exist_ok=True)
    try:
        step.apply(ctx)
    except Exception as exc:  # one broken step must not abort the whole theme
        ctx.warn(f"{step.title} failed: {exc}")
    if not runner.dry_run:
        _register_assets(plan.preset.id, ctx.assets)
        _register_assets(SHARED_ASSETS, ctx.shared_assets)
    return ctx.warnings


def scheme_available(name: str) -> bool:
    folders = [DATA_HOME / "color-schemes", Path("/usr/share/color-schemes"), Path("/usr/local/share/color-schemes")]
    return any((folder / f"{name}.colors").is_file() for folder in folders)


def restore_points(target: backup.Backup, backups: list[backup.Backup] | None = None) -> list[backup.Backup]:
    """What to put back to return to the moment ``target`` was taken.

    ``target`` only holds the files its own apply changed; applies after it may have
    changed others. Every file changed since then is restored from the *oldest* backup
    at or after ``target`` that holds it, which is exactly how it was at that moment.
    """
    newer = sorted(
        (b for b in (backups if backups is not None else backup.list_backups()) if b.created >= target.created),
        key=lambda b: b.created,
    )
    if target.id not in {b.id for b in newer}:
        newer.insert(0, target)
    seen: set[str] = set()
    parts = []
    for snapshot in newer:
        entries = [e for e in snapshot.entries if e["path"] not in seen]
        seen.update(e["path"] for e in entries)
        if entries:
            parts.append(backup.Backup(snapshot.id, snapshot.path, snapshot.created, snapshot.label, entries))
    return parts


def restore(snapshot: backup.Backup, runner: Runner, log: Logger = print, cumulative: bool = True) -> None:
    """Put the desktop back to how it was when ``snapshot`` was taken, and reload the parts of
    Plasma that read the restored files. With ``cumulative`` (the default) later applies are
    undone too, see :func:`restore_points`."""
    from plasmify import record  # it imports this module

    parts = restore_points(snapshot) if cumulative else [snapshot]
    files = {Path(e["path"]).name for part in parts for e in part.entries}
    shell_layout = "plasma-org.kde.plasma.desktop-appletsrc" in files
    # The record of applied tweaks as it was then (None: the backup is older than keeping it).
    tweaks_then = next(
        (text for part in parts if (text := backup.stored_text(part, record.TWEAKS_FILE)) is not None), None
    )

    mode = "none"
    if shell_layout:
        # plasmashell rewrites its config on exit, so it must be stopped first.
        mode = system.stop_shell(runner)
    count = sum(len(part.entries) for part in parts)
    log(f"Restoring {count} file(s) to how they were before {snapshot.id}")
    for part in parts:
        switch_kwin_plugins(part, runner)
        if not runner.dry_run:
            backup.restore_files(part, skip=frozenset({record.TWEAKS_FILE}))
        try:
            root_command = backup.root_restore_command(part)
        except ValueError as exc:
            log(f"warning: login screen settings were not restored: {exc}")
            root_command = None
        if root_command:
            log("Restoring the login screen settings (asks for your password)")
            runner.run(root_command, check=False)
    if shell_layout and not system.start_shell(runner, mode):
        log("warning: Plasma did not start again; log out and in to finish")
    if tweaks_then is not None and not runner.dry_run:
        restore_tweaks(tweaks_then)
    gone = [] if runner.dry_run else missing_assets(runner)
    if gone:
        log(
            "warning: downloads this backup uses were cleaned up since (" + ", ".join(gone) + "); Plasma "
            "shows its own instead until you apply the preset again"
        )

    scheme = runner.kread("kdeglobals", "General", "ColorScheme")
    if scheme and scheme_available(scheme):
        # kdeglobals already names it: apply it again, so open apps take the colours now.
        apply_scheme(runner, scheme)
    else:
        if scheme:
            log(f"warning: the colour scheme {scheme} is gone (cleaned up?); colours were left as they are")
        if "kdeglobals" in files:
            # No scheme to apply (a stock setup names none: Breeze Light), yet the colours came
            # back with kdeglobals. Without being told, open apps kept the theme's colours under
            # Breeze's light title and menu bars.
            runner.notify_palette_changed()
    cursor = runner.kread("kcminputrc", "Mouse", "cursorTheme")
    if cursor:
        runner.run(["plasma-apply-cursortheme", cursor], check=False)
    icons = runner.kread("kdeglobals", "Icons", "Theme")
    changeicons = libexec_tool("plasma-changeicons")
    if icons and changeicons:
        runner.run([changeicons, icons], check=False)
    runner.kwin_reconfigure()
    if "kwinrc" in files:
        for effect in TUNED_EFFECTS:  # a blur strength (and the like) that came back
            runner.kwin_reconfigure_effect(effect)
    if "kdeglobals" in files:
        # The widget style is back to what it was (say Breeze instead of Kvantum glass): open
        # apps switch now, as they did when the preset was applied.
        runner.notify_style_changed()
    if runner.dry_run:
        log("Dry run: nothing was changed.")
    else:
        log("Restored. Open terminals pick up the old prompt in new windows.")


def missing_assets(runner: Runner) -> list[str]:
    """What the restored settings name but is no longer on disk (``plasmify clean`` deleted it, as
    downloads are not backed up): the colour scheme has its own warning, these are the rest."""
    gone: list[str] = []
    icons = runner.kread("kdeglobals", "Icons", "Theme")
    if icons.startswith("Plasmify-") and not (DATA_HOME / "icons" / icons).is_dir():
        gone.append(f"the icon theme {icons}")
    cursor = runner.kread("kcminputrc", "Mouse", "cursorTheme")
    if cursor and "/" not in cursor and not any((folder / cursor).is_dir() for folder in CURSOR_FOLDERS):
        gone.append(f"the cursor {cursor}")
    style = runner.kread(*panelstyle.THEME_KEY)
    if panelstyle.generated(style) and not (panelstyle.THEME_ROOT / style).is_dir():
        gone.append(f"the panel style {style}")
    splash_theme = runner.kread("ksplashrc", "KSplash", "Theme")
    if splash.generated(splash_theme) and not (splash.PACKAGE_ROOT / splash_theme).is_dir():
        gone.append(f"the splash screen {splash_theme}")
    folders: set[Path] = set()
    for name in ("plasma-org.kde.plasma.desktop-appletsrc", "kscreenlockerrc"):
        try:
            text = (CONFIG_HOME / name).read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            key, _, value = line.partition("=")
            if key.strip() not in ("SlidePaths", "Image", "PreviewImage"):
                continue
            for raw in value.split(","):
                path = Path(raw.strip().removeprefix("file://"))
                if WALLPAPER_ROOT in path.parents and not path.exists():
                    folders.add(path.relative_to(WALLPAPER_ROOT).parts[0])  # the preset's folder
    gone += [f"the wallpapers of {folder}" for folder in sorted(folders)]
    return gone


def restore_tweaks(then: str) -> None:
    """After a restore: the theme on screen again gets its tweak record entry back as it was when
    the backup was taken (``then`` is the record file's text then, "" when there was none), so the
    window and "plasmify theme" show, and the next change builds on, what is on screen. The
    entries of other presets stay: their tweaks are remembered for the next time they are applied."""
    from plasmify import presets, record  # record imports this module

    active = active_preset(presets.load_all())
    if active is None:
        return  # back to a desktop without a Plasmify theme: nothing on screen to describe
    try:
        old = json.loads(then) if then else {}
    except (ValueError, RecursionError):
        return
    entry = old.get(active.id) if isinstance(old, dict) else None
    record.put_entry(active.id, entry if isinstance(entry, dict) else None)


def switch_kwin_plugins(part: backup.Backup, runner: Runner) -> None:
    """Before ``part`` puts kwinrc back: turn the effects it switches on or off through KConfig,
    so KWin loads and unloads them now; copied back as a file, they changed only at the next
    login. A switch the old file leaves out goes back to KWin's own default. Copying the file
    afterwards then changes no switch."""
    kwinrc = CONFIG_HOME / "kwinrc"
    old = backup.stored_text(part, kwinrc)
    if old is None:
        return
    try:
        now = plugin_switches(kwinrc.read_text(errors="replace"))
    except OSError:
        now = {}
    then = plugin_switches(old)
    for key in sorted(now.keys() | then.keys()):
        if now.get(key) == then.get(key):
            continue
        if key in then:
            runner.kwrite("kwinrc", "Plugins", key, then[key])
        else:
            runner.kdelete("kwinrc", "Plugins", key)


def active_preset(presets: Iterable[Preset]) -> Preset | None:
    """The preset whose colour scheme is currently active, if any."""
    kdeglobals = CONFIG_HOME / "kdeglobals"
    if not kdeglobals.exists():
        return None
    active = ""
    for line in kdeglobals.read_text(errors="replace").splitlines():
        if line.startswith("ColorScheme="):
            active = line.split("=", 1)[1].strip()
    return next((p for p in presets if scheme_name(p) == active), None)


def _load_registry() -> dict[str, list[str]]:
    try:
        return json.loads(ASSET_REGISTRY.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _register_assets(preset_id: str, assets: list[Path]) -> None:
    if not assets:
        return
    registry = _load_registry()
    known = registry.setdefault(preset_id, [])
    known += [str(a) for a in assets if str(a) not in known]
    ASSET_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    ASSET_REGISTRY.write_text(json.dumps(registry, indent=2))


def _size(path: Path) -> int:
    try:
        if path.is_dir():
            return sum(f.stat().st_size for f in path.rglob("*") if f.is_file() and not f.is_symlink())
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def asset_usage() -> dict[str, int]:
    """Disk usage in bytes of the assets downloaded for each preset."""
    return {
        preset_id: sum(_size(Path(raw)) for raw in paths)
        for preset_id, paths in _load_registry().items()
        if preset_id != SHARED_ASSETS
    }


def _check_reader(runner: Runner) -> None:
    if isinstance(runner, Runner):
        try:
            runner.check_config_reader()
        except system.CommandError as exc:
            # Nothing would count as in use, and the desktop's own themes could go.
            raise system.CommandError(
                f"Plasmify cannot read your desktop's settings ({exc}), so it cannot tell what is in use. "
                "Nothing was removed."
            ) from exc


def unused_assets(keep_preset_id: str | None, runner: Runner) -> dict[str, list[str]]:
    """What ``clean`` deletes, per preset: the downloads of presets other than ``keep_preset_id``,
    without the ones the desktop uses (cursor, icons, colours, Kvantum theme) or a kept preset
    (or all presets) lists too."""
    _check_reader(runner)
    in_use = {
        runner.kread("kcminputrc", "Mouse", "cursorTheme"),
        runner.kread("kdeglobals", "Icons", "Theme"),
        runner.kread("kdeglobals", "General", "ColorScheme") + ".colors",
    }
    kvconfig = CONFIG_HOME / "Kvantum" / "kvantum.kvconfig"
    if kvconfig.exists():
        in_use |= {
            line.split("=", 1)[1].strip() for line in kvconfig.read_text().splitlines() if line.startswith("theme=")
        }
    registry = _load_registry()
    # Paths still listed by a kept preset (or shared) must survive, even if another preset lists them too.
    protected = {raw for pid, paths in registry.items() if pid in (keep_preset_id, SHARED_ASSETS) for raw in paths}
    return {
        preset_id: [raw for raw in paths if Path(raw).name not in in_use and raw not in protected]
        for preset_id, paths in registry.items()
        if preset_id not in (keep_preset_id, SHARED_ASSETS)
    }


def unused_size(keep_preset_id: str | None, runner: Runner) -> int:
    """The bytes ``clean`` frees: each path once, even when several presets list it."""
    unique = {raw for paths in unused_assets(keep_preset_id, runner).values() for raw in paths}
    return sum(_size(Path(raw)) for raw in unique)


def clean(keep_preset_id: str | None, runner: Runner, log: Logger = print) -> int:
    """Delete assets downloaded for presets other than ``keep_preset_id``. Returns bytes freed."""
    unused = unused_assets(keep_preset_id, runner)
    registry = _load_registry()
    freed = 0
    removed: set[str] = set()
    for preset_id, paths in unused.items():
        for raw in paths:
            if raw in removed:
                continue
            removed.add(raw)
            path = Path(raw)
            freed += _size(path)
            log(f"{'Would remove' if runner.dry_run else 'Removing'} {path}")
            if not runner.dry_run:
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
        remaining = [raw for raw in registry.get(preset_id, []) if raw not in removed]
        if remaining:
            registry[preset_id] = remaining
        else:
            registry.pop(preset_id, None)
    if not runner.dry_run:
        ASSET_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        ASSET_REGISTRY.write_text(json.dumps(registry, indent=2))
        if PLASMIFY_DATA.is_dir():
            for folder in sorted(PLASMIFY_DATA.rglob("*"), key=lambda p: len(p.parts), reverse=True):
                if folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
    return freed
