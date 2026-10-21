"""What was applied for each preset, shared by the window and the command line.

The record lives in ``PLASMIFY_STATE/applied-tweaks.json`` and maps a preset id to::

    {"overrides": {...}, "custom_dir": "/path" or null, "steps": [...], "own_mode": {...}}

``overrides`` are the Customize changes applied on top of the preset, ``custom_dir`` the
folder with the user's own files, ``steps`` every step applied for it so far that no other
preset ran since (so they are all still on the desktop) and ``own_mode``, only while the theme
shows the other light/dark mode, the way back (see :func:`plasmify.modes.switch`). Nothing here
needs Qt.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plasmify import engine, glass, modes, presets
from plasmify.palette import is_dark
from plasmify.presets import Preset
from plasmify.steps import ALL_STEPS
from plasmify.system import PLASMIFY_STATE, Runner

TWEAKS_FILE = PLASMIFY_STATE / "applied-tweaks.json"
# A real record is a few kilobytes per preset; anything this big was not written by Plasmify.
MAX_RECORD_BYTES = 16 * 1024 * 1024
MAX_PATH_LENGTH = 4096

KEEP = object()  # remember_tweaks: leave this part of the record as it is


# ----- reading and writing the record ----------------------------------------------------


def applied_tweaks(path: Path | None = None) -> dict[str, dict]:
    """Customizations that were applied, per preset id (empty without a readable record).
    ``path`` is the record file (TWEAKS_FILE when not given)."""
    path = path or TWEAKS_FILE
    try:
        if not path.is_file():  # a FIFO or a folder would block or fail the read
            return {}
        with path.open("rb") as handle:
            raw = handle.read(MAX_RECORD_BYTES + 1)
    except OSError:
        return {}
    if len(raw) > MAX_RECORD_BYTES:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):  # broken JSON, bad encoding, absurd nesting
        return {}
    return data if isinstance(data, dict) else {}


def remember_tweaks(
    preset_id: str,
    overrides: dict,
    custom_dir: Path | None,
    steps: list[str] | None = None,
    own_mode: Any = KEEP,
    path: Path | None = None,
) -> None:
    """Record what was applied for a preset: the tweaks, their folder, the steps that ran (added
    to those of earlier applies, which are still on the desktop) and, while the theme is in the
    other light/dark mode, how to get back (see ``modes.switch``).

    The steps that ran are taken off the other presets: their part of the desktop is gone. No
    tweaks and no ``steps`` forget the preset (it was deleted); an apply that ran nothing keeps
    the steps from before, as they are still on the desktop.

    The window and the command line both write through here, so they always agree."""
    data = applied_tweaks(path)
    old = data.get(preset_id) if isinstance(data.get(preset_id), dict) else {}
    earlier = old.get("steps") if isinstance(old.get("steps"), list) else []
    ran = {step for step in earlier if isinstance(step, str)}
    if steps:
        for other_id, other in data.items():
            if other_id != preset_id and isinstance(other, dict) and isinstance(other.get("steps"), list):
                other["steps"] = [step for step in other["steps"] if step not in steps]
    if overrides or steps or (steps is not None and ran):
        entry: dict[str, Any] = {"overrides": overrides, "custom_dir": str(custom_dir) if custom_dir else None}
        if steps is not None or ran:
            entry["steps"] = sorted(ran | set(steps or []))
        own = old.get("own_mode") if own_mode is KEEP else own_mode
        if isinstance(own, dict):
            entry["own_mode"] = own
        data[preset_id] = entry
    else:
        data.pop(preset_id, None)
    _write(data, path or TWEAKS_FILE)


def put_entry(preset_id: str, entry: dict | None, path: Path | None = None) -> None:
    """Set a preset's whole entry as a restore brought it back (None: it had none then). The
    steps it lists are on the desktop again, so they are taken off the other presets, as
    :func:`remember_tweaks` does."""
    data = applied_tweaks(path)
    if entry is None:
        data.pop(preset_id, None)
    else:
        listed = entry.get("steps")
        steps = {s for s in listed if isinstance(s, str)} if isinstance(listed, list) else set()
        for other_id, other in data.items():
            if other_id != preset_id and isinstance(other, dict) and isinstance(other.get("steps"), list):
                other["steps"] = [s for s in other["steps"] if s not in steps]
        data[preset_id] = entry
    _write(data, path or TWEAKS_FILE)


def _write(data: dict, path: Path) -> None:
    """Replace the record in one step: a crash never leaves half a file, and a symlink put in
    its place is replaced instead of followed."""
    text = json.dumps(data, indent=2)
    folder = path.parent
    folder.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".applied-tweaks-", suffix=".tmp", dir=folder)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise


def entry_for(preset_id: str) -> dict[str, Any]:
    """The record of one preset (empty when nothing was remembered for it)."""
    entry = applied_tweaks().get(preset_id)
    return entry if isinstance(entry, dict) else {}


def overrides_of(entry: dict[str, Any]) -> dict[str, Any]:
    overrides = entry.get("overrides")
    return overrides if isinstance(overrides, dict) else {}


def folder_of(entry: dict[str, Any]) -> Path | None:
    """The folder with the user's own files for these tweaks, if the record names a sane one."""
    value = entry.get("custom_dir")
    if not isinstance(value, str) or not value or "\x00" in value or len(value) > MAX_PATH_LENGTH:
        return None
    path = Path(value)
    return path if path.is_absolute() else None


def usable(folder: Path | None) -> Path | None:
    """``folder`` when it is still there (its files are needed to build the theme)."""
    return folder if folder is not None and folder.is_dir() else None


def usable_overrides(preset: Preset, overrides: dict, folder: Path | None) -> dict:
    """Saved tweaks that still form a valid preset. A section that no longer validates (say,
    after a preset update) is dropped on its own; the rest of the user's work is kept."""
    try:
        presets.customized(preset, overrides, folder)
        return overrides
    except presets.PresetError:
        pass
    kept: dict = {}
    for section, value in overrides.items():
        try:
            presets.customized(preset, {**kept, section: value}, folder)
        except presets.PresetError:
            continue
        kept[section] = value
    return kept


# ----- the theme on the desktop ----------------------------------------------------------


def live_mode(runner: Runner | None = None) -> bool | None:
    """Whether the desktop's window colour is dark right now (None if unknown)."""
    value = (runner or Runner()).kread("kdeglobals", "Colors:Window", "BackgroundNormal")
    try:
        red, green, blue = (int(part) for part in value.split(","))
    except ValueError:
        return None
    if min(red, green, blue) < 0 or max(red, green, blue) > 255:
        return None
    return is_dark(f"#{red:02x}{green:02x}{blue:02x}")


def desktop_tweaks(
    preset: Preset, overrides: dict, folder: Path | None, own: dict | None = None, live: bool | None = None
) -> tuple[dict, bool, dict | None]:
    """(tweaks that still validate, in the light/dark mode the desktop shows; whether they
    changed; the way back that goes with them, ``own_mode`` in the record).

    ``live`` is whether the desktop is dark now (None: unknown, or ``preset`` is not the theme
    on the desktop). After a restore or a command-line apply the colours in the record can be
    older than the screen; then the mode change goes through ``modes.switch``, so its exact way
    back (``own``) and its icon flavours apply here too, and the way back it gives replaces
    ``own``."""
    own = own if isinstance(own, dict) else None
    folder = usable(folder)
    overrides = usable_overrides(preset, overrides, folder)
    if live is None:
        return overrides, False, own
    try:
        result = presets.customized(preset, overrides, folder)
        if result.palette.dark == live:
            return overrides, False, own
        matched, keep = modes.switch(preset, overrides, result, live, own)
        presets.customized(preset, matched, folder)
    except (presets.PresetError, KeyError, ValueError):
        return overrides, False, own
    return matched, True, keep


# ----- counting changes ------------------------------------------------------------------

# Keys that only follow another setting (the fonts' packages, a cursor's download, a slideshow's
# mode, a custom folder source): they are never counted as changes of their own.
FOLLOWERS = {("fonts", "packages"), ("cursor", "url"), ("wallpaper", "mode"), ("icons", "folder_source")}


def changed_settings(before: dict, after: dict) -> int:
    """How many settings differ between two presets' data: one per setting, while new colours
    (a light/dark switch changes them all), a section added or left out, and a glass look with
    its see-through amount and whether the window in use stays solid count once.

    The one rule for every count: the Customize tab's, the unsaved bar's and the command line's."""
    count = 0
    for section in before.keys() | after.keys():
        old, new = before.get(section), after.get(section)
        if old == new:
            continue
        if section == "palette" or not isinstance(old, dict) or not isinstance(new, dict):
            count += 1
            continue
        differ = {
            key for key in old.keys() | new.keys() if old.get(key) != new.get(key) and (section, key) not in FOLLOWERS
        }
        if section == "wallpaper" and new.get("shimmer", "none") == "none":
            differ = {key for key in differ if not key.startswith("shimmer_")}  # details of no shimmer
        if section == "style" and differ & set(glass.CHOICE_KEYS):
            differ = (differ - set(glass.CHOICE_KEYS)) | {"glass"}
        count += len(differ)
    return count


def change_count(preset: Preset, overrides: dict[str, Any]) -> int:
    """How many settings the tweaks ``overrides`` change in ``preset`` (see changed_settings): a
    tweak equal to the preset's own value, or one that only follows another, is no change."""
    return changed_settings(preset.data, presets.merge(preset.data, overrides))


@dataclass(frozen=True)
class Applied:
    """The theme on the desktop now."""

    preset: Preset  # the preset as it is defined
    overrides: dict[str, Any]  # its applied tweaks, in the mode the desktop shows
    folder: Path | None  # the folder of the user's own files (as the record names it)
    result: Preset  # the preset with the tweaks
    record: dict[str, Any]  # the record entry (own_mode: the way back from the mode the screen shows)
    live: bool | None  # whether the desktop is dark right now (None if unknown)
    stale: bool = False  # the record's colours were older than the screen

    @property
    def usable(self) -> Path | None:
        return usable(self.folder)

    @property
    def dark(self) -> bool:
        """Whether the desktop is dark: what it shows when known, else what was applied."""
        return self.result.palette.dark if self.live is None else self.live


def applied(all_presets: Iterable[Preset], live: Any = KEEP) -> Applied | None:
    """The theme on the desktop (None when no Plasmify theme is). ``live`` defaults to asking
    the desktop (see :func:`live_mode`)."""
    found = list(all_presets)
    target = engine.active_preset(found)
    if target is None:
        return None
    entry = entry_for(target.id)
    overrides = overrides_of(entry)
    folder = folder_of(entry)
    mode = live_mode() if live is KEEP else live
    # What still validates, like Customize; colours older than the desktop (a restore, a
    # command-line apply) give way to what is on screen.
    overrides, stale, own = desktop_tweaks(target, overrides, folder, entry.get("own_mode"), mode)
    if stale:
        entry = {**entry, "own_mode": own}
    try:
        result = presets.customized(target, overrides, usable(folder))
    except presets.PresetError:
        overrides, result = {}, target
    return Applied(target, overrides, folder, result, entry, mode, stale)


# ----- light / dark ----------------------------------------------------------------------


def mode_steps(entry: dict[str, Any], effective: Preset) -> list[str]:
    """The colour steps to run: the ones applied for this theme before (the colour scheme
    always; older records without steps get colours, style and icons)."""
    ran = entry.get("steps")
    allowed = {step for step in ran if isinstance(step, str)} if isinstance(ran, list) else {"colors", "style", "icons"}
    allowed.add("colors")
    return [s.id for s in ALL_STEPS if s.id in modes.MODE_STEPS and s.id in allowed and s.applies_to(effective)]


@dataclass(frozen=True)
class ModeSwitch:
    """The applied theme in the other mode, ready to plan and apply."""

    dark: bool
    overrides: dict[str, Any]  # the tweaks to remember after the switch
    keep: dict[str, Any] | None  # the way back (``own_mode`` in the record)
    preset: Preset  # what gets applied
    steps: list[str]


def switch_mode(state: Applied, dark: bool) -> ModeSwitch:
    """The theme on the desktop in light (``dark`` False) or dark colours, colours only.

    Raises PresetError, KeyError or ValueError when the switched theme does not validate.
    After applying it, remember it with
    ``remember_tweaks(state.preset.id, switch.overrides, state.folder, steps, own_mode=switch.keep)``."""
    own = state.record.get("own_mode") if isinstance(state.record.get("own_mode"), dict) else None
    switched, keep = modes.switch(state.preset, state.overrides, state.result, dark, own)
    effective = presets.customized(state.preset, switched, state.usable)
    return ModeSwitch(dark, switched, keep, effective, mode_steps(state.record, effective))
