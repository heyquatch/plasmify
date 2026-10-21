"""Sharing presets: saving user presets and ``.plasmify`` bundles.

A bundle is a zip archive with exactly this layout::

    preset.toml
    assets/<PNG, JPEG, WebP or GIF files the preset names (wallpapers, logos, backgrounds)>

It lets people share a look, including their own wallpapers, directly with each
other without uploading anything anywhere. Wallpapers that are referenced by
URL are *not* copied into bundles, so other artists' work is never
redistributed: the recipient downloads it from the original source.

Bundles are untrusted input and are checked as strictly as preset files.
"""

from __future__ import annotations

import hashlib
import shutil
import stat
import tomllib
import zipfile
import zlib
from pathlib import Path
from typing import Any

from plasmify import HOMEPAGE, downloads, presets, schema, tomlwrite
from plasmify.presets import USER_PRESET_DIR, Preset, PresetError
from plasmify.system import safe_child

BUNDLE_SUFFIX = ".plasmify"
MAX_FILES = 32  # preset.toml, the assets/ folder and up to 30 wallpapers
MAX_ASSET_BYTES = 50 * 1024 * 1024
MAX_BUNDLE_BYTES = 300 * 1024 * 1024
HEADER = f"Plasmify preset. Apply it with Plasmify: {HOMEPAGE}"


def save_user_preset(
    data: dict[str, Any], files: dict[str, Path] | None = None, replace: bool = False, custom_root: Path | None = None
) -> Preset:
    """Validate ``data`` and store it (plus its wallpaper ``files``) as a user preset.

    ``custom_root`` is where the Customize tab keeps copies of presets' files (one folder per
    id). When a preset is replaced, copies of its old files there are swapped for the new
    version, so the old pictures are not applied instead; files the user put there stay."""
    errors = schema.validate(data)
    if errors:
        raise PresetError("; ".join(errors))
    preset_id = data["meta"]["id"]
    if any(p.id == preset_id for p in presets.load_all(include_user=False)):
        raise PresetError(f"'{preset_id}' is the id of a built-in preset")
    clash = next(
        (
            p
            for p in presets.load_all()
            if p.id != preset_id and presets.scheme_key(p.id) == presets.scheme_key(preset_id)
        ),
        None,
    )
    if clash is not None:
        raise PresetError(f"'{preset_id}' would overwrite the colours of '{clash.id}'; choose another name")
    toml_path = safe_child(USER_PRESET_DIR, f"{preset_id}.toml")
    asset_dir = safe_child(USER_PRESET_DIR, preset_id)
    if toml_path.exists() and not replace:
        raise FileExistsError(preset_id)

    files = files or {}
    missing = [name for name in schema.local_files(data) if name not in files]
    if missing:
        raise PresetError("Missing wallpaper files: " + ", ".join(missing))

    # Read and check every file first: the sources may live in the very folder replaced below
    # (saving a user preset again under its own name).
    blobs: dict[str, bytes] = {}
    for name, source in files.items():
        problem = downloads.image_file_problem(Path(source))
        if problem:
            raise PresetError(f"{name} {problem}")
        blobs[name] = Path(source).read_bytes()
    old = _digests(asset_dir) if replace and custom_root is not None else None
    USER_PRESET_DIR.mkdir(parents=True, exist_ok=True)
    if asset_dir.exists():
        shutil.rmtree(asset_dir)
    if blobs:
        asset_dir.mkdir()
        for name, blob in blobs.items():
            safe_child(asset_dir, name).write_bytes(blob)
    toml_path.write_text(tomlwrite.dumps(data, HEADER), encoding="utf-8")
    if old is not None and custom_root is not None:
        _refresh_copies(safe_child(custom_root, preset_id), old, blobs)
    return presets.load(toml_path, "user")


def _digests(folder: Path) -> dict[str, str]:
    if not folder.is_dir():
        return {}
    return {
        item.name: hashlib.sha256(item.read_bytes()).hexdigest()
        for item in folder.iterdir()
        if item.is_file() and not item.is_symlink()
    }


def _refresh_copies(folder: Path, old: dict[str, str], blobs: dict[str, bytes]) -> None:
    """A copy that is still byte for byte the old version of a preset file gets the new
    version, and files the new version adds are copied in; anything else there (the user's own
    pictures) is left alone."""
    if not folder.is_dir():
        return
    for name, blob in blobs.items():
        copy = folder / name
        if copy.is_symlink():
            continue
        try:
            if not copy.exists():
                copy.write_bytes(blob)  # new in this version: nobody's own file yet
            elif name in old and copy.is_file() and hashlib.sha256(copy.read_bytes()).hexdigest() == old[name]:
                copy.write_bytes(blob)
        except OSError:
            continue


def delete_user_preset(preset: Preset) -> None:
    if preset.builtin or preset.path is None:
        raise PresetError("Built-in presets cannot be deleted")
    Path(preset.path).unlink(missing_ok=True)
    if preset.asset_dir and preset.asset_dir.is_dir():
        shutil.rmtree(preset.asset_dir)


def export_bundle(preset: Preset, destination: Path) -> Path:
    """Write ``preset`` (and its own wallpaper files) to a ``.plasmify`` archive."""
    if destination.suffix != BUNDLE_SUFFIX:
        destination = destination.with_name(destination.name + BUNDLE_SUFFIX)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("preset.toml", tomlwrite.dumps(preset.data, HEADER))
        for name in schema.local_files(preset.data):
            if preset.asset_dir is None:
                raise PresetError(f"{preset.name} has no folder for {name}")
            zf.write(safe_child(preset.asset_dir, name), f"assets/{name}")
    return destination


def read_bundle(path: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    """Read and fully validate a bundle without writing anything to disk."""
    try:
        return _read_bundle(path)
    except (
        zipfile.BadZipFile,
        zlib.error,
        EOFError,
        NotImplementedError,
        UnicodeDecodeError,
        OSError,
        RuntimeError,
    ) as exc:
        raise PresetError(f"{path.name} is damaged: {exc}") from exc
    except RecursionError as exc:  # absurdly nested TOML
        raise PresetError(f"{path.name} has an invalid preset.toml (nested too deeply)") from exc


def _read_bundle(path: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    if not zipfile.is_zipfile(path):
        raise PresetError(f"{path.name} is not a Plasmify bundle")
    with zipfile.ZipFile(path) as zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILES:
            raise PresetError("The bundle contains too many files")
        if sum(i.file_size for i in infos) > MAX_BUNDLE_BYTES:
            raise PresetError("The bundle is too large")
        listed = [i.filename for i in infos if not i.is_dir()]
        names = set(listed)
        if len(names) != len(listed):
            raise PresetError("The bundle lists a file twice")
        if "preset.toml" not in names:
            raise PresetError("The bundle has no preset.toml")
        for info in infos:
            kind = stat.S_IFMT(info.external_attr >> 16)
            if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise PresetError(f"Links and special files are not allowed in a bundle: {info.filename}")
            if info.is_dir():
                if info.filename != "assets/":
                    raise PresetError(f"Unexpected folder in bundle: {info.filename}")
                continue
            if info.filename == "preset.toml":
                if info.file_size > presets.MAX_PRESET_BYTES:
                    raise PresetError("preset.toml is too large")
                continue
            folder, _, name = info.filename.partition("/")
            if folder != "assets" or schema.IMAGE_FILE(name) is not None or info.file_size > MAX_ASSET_BYTES:
                raise PresetError(f"Unexpected file in bundle: {info.filename}")
        try:
            data = tomllib.loads(zf.read("preset.toml").decode("utf-8"))
        except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            raise PresetError(f"preset.toml is not valid TOML ({exc})") from exc
        errors = schema.validate(data)
        if errors:
            raise PresetError("; ".join(errors))
        assets = {}
        for name in schema.local_files(data):
            member = f"assets/{name}"
            if member not in names:
                raise PresetError(f"The bundle is missing {member}")
            content = zf.read(member)
            problem = downloads.image_problem(content)  # by magic bytes and header, not by name
            if problem:
                raise PresetError(f"{name} {problem}")
            assets[name] = content
    return data, assets


# fastfetch lines that go online every time a terminal opens (weather asks wttr.in, over plain
# HTTP). Fine when you pick them yourself; a shared preset must not switch them on for you.
ONLINE_MODULES = ("weather",)


def _offline(data: dict, notes: list[str] | None) -> dict:
    shell = data.get("shell")
    modules = shell.get("fastfetch_modules") if isinstance(shell, dict) else None
    if not isinstance(modules, list) or not any(m in ONLINE_MODULES for m in modules):
        return data
    data = dict(data, shell=dict(shell))
    kept = [m for m in modules if m not in ONLINE_MODULES]
    if kept:
        data["shell"]["fastfetch_modules"] = kept
    else:
        del data["shell"]["fastfetch_modules"]
    if notes is not None:
        notes.append(
            "The weather line was left out of the terminal greeting: it would contact wttr.in every time a "
            "terminal opens. You can turn it on yourself in Customize."
        )
    return data


def import_file(
    path: Path,
    replace: bool = False,
    workdir: Path | None = None,
    notes: list[str] | None = None,
    custom_root: Path | None = None,
) -> Preset:
    """Import a ``.toml`` preset or a ``.plasmify`` bundle as a user preset. ``notes`` collects
    what was changed on the way in; ``custom_root``: see :func:`save_user_preset`."""
    if path.suffix == ".toml":
        preset = presets.load(path)
        if schema.local_files(preset.data):
            raise PresetError("This preset needs its wallpaper files; import the .plasmify bundle instead")
        return save_user_preset(_offline(preset.data, notes), replace=replace, custom_root=custom_root)

    data, assets = read_bundle(path)
    data = _offline(data, notes)
    staging = Path(workdir) if workdir else USER_PRESET_DIR.parent / "import-tmp"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        files = {}
        for name, content in assets.items():
            target = safe_child(staging, name)
            target.write_bytes(content)
            problem = downloads.image_file_problem(target)
            if problem:
                raise PresetError(f"{name} {problem}")
            files[name] = target
        return save_user_preset(data, files, replace=replace, custom_root=custom_root)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
