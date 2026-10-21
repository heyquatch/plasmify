"""Loading and validating preset files.

A preset is a TOML document describing a complete look: palette, icons,
cursor, fonts, wallpapers, panel layout, effects, terminal and prompt. See
``docs/presets.md`` for the full reference.
"""

from __future__ import annotations

import copy
import dataclasses
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

from plasmify import glass, schema
from plasmify.palette import Palette, is_dark, is_hex
from plasmify.system import CONFIG_HOME

USER_PRESET_DIR = CONFIG_HOME / "plasmify" / "presets"
MAX_PRESET_BYTES = 256 * 1024


class PresetError(ValueError):
    pass


@dataclass(frozen=True)
class Preset:
    data: dict[str, Any]
    source: str
    path: Any = None  # the TOML file it was loaded from
    asset_override: Any = None  # folder with files chosen in the Customize tab

    @property
    def asset_dir(self) -> Path | None:
        """Folder with the preset's own files (wallpapers, logo); user presets and customizations."""
        if self.asset_override is not None:
            return Path(self.asset_override)
        if self.source != "user" or self.path is None:
            return None
        return Path(self.path).parent / self.id

    @property
    def id(self) -> str:
        return self.data["meta"]["id"]

    @property
    def name(self) -> str:
        # Runs of (Unicode) spaces collapse to one, so padding cannot hide "(yours)".
        return " ".join(self.data["meta"]["name"].split())

    @property
    def description(self) -> str:
        return self.data["meta"].get("description", "")

    @property
    def tags(self) -> list[str]:
        return list(self.data["meta"].get("tags", []))

    @property
    def builtin(self) -> bool:
        return self.source == "built-in"

    def untrusted_hosts(self) -> list[str]:
        """Download hosts outside the trusted list; the user must agree before they are contacted."""
        return schema.untrusted_hosts(self.data)

    @property
    def palette(self) -> Palette:
        return Palette(dict(self.data["palette"]))

    def section(self, name: str) -> dict[str, Any]:
        return dict(self.data.get(name, {}))


def validate(data: Any) -> list[str]:
    """Return every problem with ``data``; an empty list means it is safe to use."""
    return schema.validate(data)


def load(path: Path | Any, source: str = "") -> Preset:
    with path.open("rb") as fh:
        if hasattr(fh, "seek") and fh.seek(0, 2) > MAX_PRESET_BYTES:
            raise PresetError(f"{path}: file is too large to be a preset")
        fh.seek(0)
        try:
            data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise PresetError(f"{path}: not valid TOML ({exc})") from exc
        except RecursionError as exc:  # absurdly nested arrays or tables
            raise PresetError(f"{path}: not valid TOML (nested too deeply)") from exc
    errors = validate(data)
    if errors:
        raise PresetError(f"{path}: " + "; ".join(errors))
    return Preset(data, source or str(path), path)


def builtin_paths() -> list[Any]:
    folder = resources.files("plasmify") / "presets"
    return sorted((p for p in folder.iterdir() if p.name.endswith(".toml")), key=lambda p: p.name)


def load_all(include_user: bool = True, problems: list[str] | None = None) -> list[Preset]:
    """Load built-in presets followed by user presets from ``~/.config/plasmify/presets``.

    Invalid user presets are skipped (and reported in ``problems``). A user preset can
    never replace a built-in one, so a shared file cannot impersonate a reviewed preset.
    """
    presets: dict[str, Preset] = {}
    for path in builtin_paths():
        preset = load(path, "built-in")
        presets[preset.id] = preset
    if include_user and USER_PRESET_DIR.is_dir():
        for path in sorted(USER_PRESET_DIR.glob("*.toml")):
            try:
                preset = load(path, "user")
            except (PresetError, OSError, ValueError) as exc:
                if problems is not None:
                    problems.append(str(exc))
                continue
            if preset.id in presets:
                if problems is not None:
                    problems.append(f"{path}: id '{preset.id}' belongs to a built-in preset; skipped")
                continue
            clash = next((p for p in presets.values() if scheme_key(p.id) == scheme_key(preset.id)), None)
            if clash is not None:  # e.g. "a1-b" and "a1b" would share one colour scheme file
                if problems is not None:
                    problems.append(f"{path}: id '{preset.id}' would overwrite the colours of '{clash.id}'; skipped")
                continue
            presets[preset.id] = preset
    return list(presets.values())


def scheme_key(preset_id: str) -> str:
    """The colour scheme name Plasmify uses for ``preset_id`` (see ``steps.colors``)."""
    return "Plasmify" + "".join(part.capitalize() for part in preset_id.split("-"))


def find(preset_id: str) -> Preset:
    for preset in load_all():
        if preset.id == preset_id:
            return preset
    raise PresetError(f"Unknown preset '{preset_id}'")


def merge(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge ``overrides`` into a copy of ``base``; a ``None`` value removes the key."""
    result = copy.deepcopy(base)
    for key, value in overrides.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict):
            # Merge into an empty table when the base has none, so None values are dropped too.
            result[key] = merge(result[key] if isinstance(result.get(key), dict) else {}, value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _mode(data: dict) -> bool | None:
    palette = data.get("palette")
    base = palette.get("base") if isinstance(palette, dict) else None
    return is_dark(base) if is_hex(base) else None


def customized(preset: Preset, overrides: dict[str, Any], asset_dir: Path | None = None) -> Preset:
    """``preset`` with the user's customizations applied (validated like any preset).

    What glass needs from blur, panels and the terminal is filled in too (see
    :func:`plasmify.glass.with_companions`), so the result is exactly what gets applied.
    ``asset_dir`` holds files the user picked (own wallpapers or logo photo); it must
    already contain the preset's own files too (see ``CUSTOM_ROOT``).
    """
    if not overrides:
        data = glass.with_companions(preset.data)
        return preset if data is preset.data else dataclasses.replace(preset, data=data)
    data = merge(preset.data, overrides)
    style = data.get("style")  # anything but a table fails validation below
    kvantum = style.get("kvantum") if isinstance(style, dict) else None
    chosen = overrides.get("style")
    picked = isinstance(chosen, dict) and "kvantum" in chosen
    if kvantum and not picked and _mode(data) != _mode(preset.data):
        # A Kvantum theme paints its own light or dark widgets: over the other mode it clashes,
        # so Breeze (which follows the colour scheme) takes over. Only the theme goes: glass, its
        # transparency and focused_solid stay and then use a Kvantum theme coloured from the palette
        # (a left out transparency follows the new mode's default, see glass.effective_transparency).
        data["style"] = {k: v for k, v in data["style"].items() if k != "kvantum"}
        if not data["style"]:
            del data["style"]
    data = glass.with_companions(data)
    errors = validate(data)
    if errors:
        raise PresetError("; ".join(errors))
    return dataclasses.replace(preset, data=data, asset_override=asset_dir or preset.asset_override)
