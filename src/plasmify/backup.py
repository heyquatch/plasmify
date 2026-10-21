"""Snapshots of every file Plasmify is about to change, and restoring them."""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from plasmify import rootops
from plasmify.system import PLASMIFY_STATE

BACKUP_ROOT = PLASMIFY_STATE / "backups"
KEEP_BACKUPS = 10

# The only system files Plasmify changes; for them a backup keeps the values of the keys it
# writes (see plasmify.rootops), never a copy that root would later install.
ROOT_MANAGERS = {path: manager for manager, (path, _keys) in rootops.MANAGED.items()}
ROOT_RESTORABLE = tuple(ROOT_MANAGERS)


@dataclass(frozen=True)
class Backup:
    id: str
    path: Path
    created: float
    label: str
    entries: list[dict]
    pinned: bool = False  # never pruned (the user's setup from before Plasmify)

    @property
    def files(self) -> list[Path]:
        return [Path(e["path"]) for e in self.entries]


# The label of the pinned backup when it holds the setup from before Plasmify. A pinned backup from
# an earlier Plasmify version may hold a theme already, and its label says so (see _original_label).
ORIGINAL_LABEL = "Your setup before Plasmify"


def is_original(backup: Backup) -> bool:
    """Whether ``backup`` holds the user's setup from before Plasmify, not a theme of an earlier
    Plasmify version (older versions kept ten backups, so that one may be gone)."""
    return backup.pinned and backup.label.startswith(ORIGINAL_LABEL)


def _store_path(backup_dir: Path, original: Path) -> Path:
    """Where ``original`` is kept in a backup. The path is made absolute but symlinks are not
    followed: a backed-up link is deleted before restoring, and its target name would be lost."""
    absolute = Path(os.path.abspath(original))
    stored = backup_dir / "files" / str(absolute).lstrip("/")
    if not stored.exists() and not stored.is_symlink():
        legacy = backup_dir / "files" / str(original.resolve()).lstrip("/")  # backups before 0.3
        if legacy.exists() or legacy.is_symlink():
            return legacy
    return stored


def create(paths: list[Path], label: str, root: Path = BACKUP_ROOT) -> Backup:
    """Copy ``paths`` into a new backup. Paths that do not exist are recorded so that
    restoring removes whatever Plasmify created there.

    The very first backup is pinned: it holds the setup from before Plasmify was ever
    used, so it is kept no matter how many presets are applied later.
    """
    _pin_oldest(root)  # backups from before pinning existed get their "before Plasmify" one
    pinned = not list_backups(root)
    if pinned:
        label = f"{ORIGINAL_LABEL} ({_lead_lower(label)})"
    backup_id = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = root / backup_id
    suffix = 1
    while backup_dir.exists():
        suffix += 1
        backup_dir = root / f"{backup_id}-{suffix}"
    backup_dir.mkdir(parents=True)

    entries = []
    for path in dict.fromkeys(paths):
        path = Path(path)
        entry = {"path": str(path), "existed": path.exists() or path.is_symlink()}
        if str(path) in ROOT_RESTORABLE:
            manager = ROOT_MANAGERS[str(path)]
            entry.update(root=True, manager=manager, keys=rootops.read_managed(manager))
            entries.append(entry)
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.copytree(path, _store_path(backup_dir, path), symlinks=True)
            entry["kind"] = "dir"
        elif entry["existed"]:
            target = _store_path(backup_dir, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target, follow_symlinks=False)
            entry["kind"] = "file"
        entries.append(entry)

    manifest = {"created": time.time(), "label": label, "entries": entries, "pinned": pinned}
    (backup_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    if not pinned:
        _extend_pinned(root, backup_dir, entries)
    prune(root)
    return Backup(backup_dir.name, backup_dir, manifest["created"], label, entries, pinned)


def _extend_pinned(root: Path, source_dir: Path, entries: list[dict]) -> None:
    """Give the pinned "before Plasmify" backup every file it does not hold yet.

    A file that no earlier apply touched is still the user's own version, so the copy just
    taken is its original. Without this, restoring the pinned backup relied on in-between
    backups, which are pruned after ten applies."""
    pinned = next((b for b in _read_backups(root) if b.pinned), None)  # only a pin written down
    if pinned is None:
        return
    known = {e["path"] for e in pinned.entries}
    added = []
    for entry in entries:
        if entry["path"] in known:
            continue
        path = Path(entry["path"])
        stored, target = _store_path(source_dir, path), _store_path(pinned.path, path)
        try:
            if entry.get("root"):
                pass  # system files keep their key values in the entry itself, no copy
            elif entry.get("kind") == "dir":
                shutil.copytree(stored, target, symlinks=True, dirs_exist_ok=True)
            elif entry["existed"]:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(stored, target, follow_symlinks=False)
        except OSError:
            continue  # a copy the pinned backup cannot take never stops the apply
        added.append(entry)
    if added:
        manifest_path = pinned.path / "manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text())
            manifest["entries"] = manifest.get("entries", []) + added
            manifest_path.write_text(json.dumps(manifest, indent=2))
        except (OSError, json.JSONDecodeError):
            return


def list_backups(root: Path = BACKUP_ROOT) -> list[Backup]:
    backups = _read_backups(root)
    if backups and not any(b.pinned for b in backups):
        # Backups from before pinning existed: the oldest stands in for the setup from before
        # Plasmify. Only shown that way here; create() writes it down (see _pin_oldest).
        oldest = backups[-1]
        backups[-1] = dataclasses.replace(oldest, pinned=True, label=_original_label(_merged(backups), oldest.label))
    return backups


def _read_backups(root: Path) -> list[Backup]:
    """The backups as saved, newest first."""
    backups: list[Backup] = []
    if not root.is_dir():
        return backups
    for manifest_path in root.glob("*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        backups.append(
            Backup(
                manifest_path.parent.name,
                manifest_path.parent,
                manifest.get("created", 0),
                manifest.get("label", ""),
                manifest.get("entries", []),
                bool(manifest.get("pinned", False)),
            )
        )
    return sorted(backups, key=lambda b: b.created, reverse=True)


def _lead_lower(label: str) -> str:
    """Only the first letter in lower case, so "Before Nord Minimal" reads "before Nord Minimal"
    inside another label and preset names keep their capitals."""
    return label[:1].lower() + label[1:]


def _original_label(parts: list[tuple[Path, dict]], label: str) -> str:
    if _themed(parts):
        # Older versions kept ten backups: the one from before Plasmify may be gone already.
        return f"Oldest backup, from an earlier Plasmify version ({_lead_lower(label)})"
    return f"{ORIGINAL_LABEL} ({_lead_lower(label)})"


def _pin_oldest(root: Path) -> None:
    """Write down the pinning of backups from before it existed (during an apply only, so a
    dry run never changes a backup): the oldest one becomes the pinned one ("plasmify restore
    original", never pruned) and takes the files only newer backups hold, each from the oldest
    backup that has it, the versions from before Plasmify touched them. A copy that cannot be
    read is skipped (restoring still finds it in the newer backup)."""
    found = _read_backups(root)
    if not found or any(b.pinned for b in found):
        return
    oldest = found[-1]
    path = oldest.path / "manifest.json"
    try:
        manifest = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    entries = [e for e in manifest.get("entries", []) if isinstance(e, dict) and "path" in e]
    known = {e["path"] for e in entries}
    for older in reversed(found[:-1]):  # oldest first
        for entry in older.entries:
            if not isinstance(entry, dict) or entry.get("path") in known or "path" not in entry:
                continue
            original = Path(entry["path"])
            stored, target = _store_path(older.path, original), _store_path(oldest.path, original)
            try:
                if entry.get("kind") == "dir":
                    shutil.copytree(stored, target, symlinks=True, dirs_exist_ok=True)
                elif entry.get("kind") == "file":
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(stored, target, follow_symlinks=False)
            except OSError:
                continue
            entries.append(entry)
            known.add(entry["path"])
    manifest["entries"] = entries
    manifest["pinned"] = True
    manifest["label"] = _original_label([(oldest.path, e) for e in entries], str(manifest.get("label", "")))
    try:
        path.write_text(json.dumps(manifest, indent=2))
    except OSError:
        return


# Signs that a saved file already belongs to a Plasmify theme (the same ones install.sh checks).
THEMED_MARKERS = {
    "kdeglobals": ("ColorScheme=Plasmify", "Theme=Plasmify-"),
    "plasmarc": ("name=plasmify-",),
    "ksplashrc": ("Theme=plasmify-splash-",),
    "plasma-org.kde.plasma.desktop-appletsrc": ("/plasmify/wallpapers/",),
    "kscreenlockerrc": ("/plasmify/wallpapers/",),
    "config.jsonc": ("/plasmify/logos/", "/plasmify/presets/", "/plasmify/custom/", "plasmify-logo.txt"),
}


def _themed(parts: list[tuple[Path, dict]]) -> bool:
    """Whether saved files (backup folder, entry) already hold a Plasmify theme, so they are
    not the setup from before Plasmify."""
    for backup_dir, entry in parts:
        markers = THEMED_MARKERS.get(Path(entry.get("path", "")).name)
        if not markers or entry.get("kind") != "file":
            continue
        try:
            text = _store_path(backup_dir, Path(entry["path"])).read_text(errors="replace")
        except OSError:
            continue
        if any(marker in text for marker in markers):
            return True
    return False


def _merged(backups: list[Backup]) -> list[tuple[Path, dict]]:
    """Every saved path, from the oldest backup that holds it (what restoring the oldest gives)."""
    parts: dict[str, tuple[Path, dict]] = {}
    for backup in reversed(backups):  # oldest first
        for entry in backup.entries:
            if isinstance(entry, dict) and "path" in entry:
                parts.setdefault(entry["path"], (backup.path, entry))
    return list(parts.values())


def stored_text(backup: Backup, path: Path) -> str | None:
    """The text ``path`` had when ``backup`` was taken: "" when it did not exist, None when the
    backup does not hold it (or it cannot be read)."""
    entry = next((e for e in backup.entries if Path(e["path"]) == path and not e.get("root")), None)
    if entry is None:
        return None
    if not entry["existed"]:
        return ""
    try:
        return _store_path(backup.path, path).read_text(errors="replace")
    except OSError:
        return None


def restore_files(backup: Backup, skip: frozenset[Path] = frozenset()) -> None:
    """Put every recorded path back exactly as it was when the backup was taken, except the paths
    in ``skip`` (the caller puts those back its own way)."""
    for entry in backup.entries:
        if entry.get("root"):
            continue  # system files are restored by root_restore_command()
        path = Path(entry["path"])
        if path in skip:
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
        if not entry["existed"]:
            continue
        stored = _store_path(backup.path, path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if entry.get("kind") == "dir":
            shutil.copytree(stored, path, symlinks=True)
        else:
            shutil.copy2(stored, path, follow_symlinks=False)


def root_restore_command(backup: Backup) -> list[str] | None:
    """One polkit command that puts the managed login keys back, if this backup has any.

    Raises ValueError if the stored values are not ones Plasmify writes (tampering), and
    for backups made by older versions, which only kept file copies."""
    for entry in backup.entries:
        if not entry.get("root") or entry["path"] not in ROOT_RESTORABLE:
            continue
        if "keys" not in entry:
            raise ValueError("this backup is from an older Plasmify; reset the login screen in System Settings")
        return rootops.restore_command(entry["manager"], entry["keys"])
    return None


def prune(root: Path = BACKUP_ROOT, keep: int = KEEP_BACKUPS) -> None:
    """Keep the newest ``keep`` backups plus every pinned one."""
    unpinned = [b for b in list_backups(root) if not b.pinned]
    for old in unpinned[keep:]:
        shutil.rmtree(old.path, ignore_errors=True)
