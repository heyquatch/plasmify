"""Command line interface. Running ``plasmify`` without arguments opens the GUI."""

from __future__ import annotations

import argparse
import copy
import difflib
import re
import sys
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from plasmify import (
    APP_NAME,
    __version__,
    activity,
    backup,
    bundle,
    capture,
    engine,
    fonts,
    glass,
    packages,
    presets,
    record,
    schema,
)
from plasmify.palette import REQUIRED_KEYS, contrast
from plasmify.presets import Preset
from plasmify.steps import ALL_STEPS, STEP_IDS
from plasmify.steps.cursor import installed_cursor_themes
from plasmify.steps.icons import installed_icon_themes
from plasmify.steps.wallpaper import shimmer_colour
from plasmify.system import PLASMIFY_DATA, CommandError, Runner, environment_problems

CUSTOM_COPIES = PLASMIFY_DATA / "custom"  # the window's Customize copies (gui.customize.CUSTOM_ROOT)

# Hand-typed input is short; anything longer is a mistake or an attack.
MAX_NAME_LENGTH = 100
MAX_KEY_LENGTH = 40
MAX_VALUE_LENGTH = 200
MAX_PAIRS = 32
# Control characters, line and paragraph separators, bidirectional overrides and lone surrogates
# (bytes that were not valid text): none of them belong in a preset name or a setting.
_UNSAFE_INPUT = re.compile(
    "[\x00-\x1f\x7f-\x9f\u200b\u2028\u2029\u200e\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff\ud800-\udfff]"
)


def _host(url: str) -> str:
    return urlsplit(url).hostname or url


def _check_environment() -> bool:
    problems = environment_problems()
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    return not problems


def input_problem(value: str, limit: int) -> str | None:
    """Why hand-typed ``value`` is refused (None when it is fine). Never echoes the value."""
    if not value.strip():
        return "is empty"
    if len(value) > limit:
        return f"is too long (at most {limit} characters)"
    if _UNSAFE_INPUT.search(value):
        return "contains control characters or bytes that are not text"
    return None


# ----- shared pieces of the commands that change the desktop ----------------------------


def _step_selection(args: argparse.Namespace) -> list[str] | None:
    """The steps --only and --skip ask for (None after printing why they are wrong)."""
    for raw in (args.only, args.skip):
        if raw and input_problem(raw, 300):
            print(f"error: the step list {input_problem(raw, 300)}", file=sys.stderr)
            return None
    only = args.only.split(",") if args.only else None
    skip = set(args.skip.split(",")) if args.skip else set()
    for step_id in (only or []) + list(skip):
        if step_id not in STEP_IDS:
            print(f"error: unknown step '{step_id}' (choose from {', '.join(STEP_IDS)})", file=sys.stderr)
            return None
    return [s for s in (only or STEP_IDS) if s not in skip]


def _approve_hosts(hosts: list[str], args: argparse.Namespace) -> bool:
    """Ask before contacting hosts outside the trusted list (never in a dry run)."""
    if not hosts or args.dry_run:
        return True
    print("\nThis preset contacts hosts outside the trusted list. They will see your IP address:")
    for host in hosts:
        note = " (the weather line of the terminal greeting, over plain HTTP, every time a terminal opens)"
        print(f"  - {host}{note if host == schema.WEATHER_HOST else ''}")
    approved = set(args.allow_host or [])
    if set(hosts) <= approved:
        return True
    # --yes never approves unknown hosts; that needs --allow-host or an explicit answer.
    if args.yes or not sys.stdin.isatty():
        print("error: approve them with --allow-host HOST (once per host)", file=sys.stderr)
        return False
    return input("Contact these hosts? [y/N] ").strip().lower() in ("y", "yes")


def _hosts_to_approve(theme: Preset, overrides: dict[str, Any], base: Preset) -> list[str]:
    """Hosts to ask about, like the window: a weather line the user ticked on a preset that
    does not have one was their own choice."""
    hosts = theme.untrusted_hosts()
    modules = (overrides.get("shell") or {}).get("fastfetch_modules") if isinstance(overrides, dict) else None
    own = base.section("shell").get("fastfetch_modules") or []
    if isinstance(modules, list) and schema.WEATHER_HOST in hosts and "weather" in modules and "weather" not in own:
        hosts.remove(schema.WEATHER_HOST)
    return hosts


def _confirm(question: str, args: argparse.Namespace) -> bool:
    """Ask before changing the desktop, unless --yes (or a dry run, which changes nothing)."""
    if args.yes or args.dry_run:
        return True
    if not sys.stdin.isatty():
        print("error: nothing was changed; add --yes to go ahead without a question", file=sys.stderr)
        return False
    try:
        answer = input(f"{question} [Y/n] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("", "y", "yes")


def _run(plan: engine.Plan, args: argparse.Namespace, allowed_hosts: list[str]) -> backup.Backup | None:
    """Apply ``plan`` (backed up first) and report like ``plasmify apply``."""
    runner = Runner(dry_run=args.dry_run)
    snapshot, warnings = engine.apply(plan, runner, log=lambda m: print(f"  {m}"), allowed_hosts=allowed_hosts)
    if args.dry_run:
        print("\nCommands that would run:")
        for line in runner.log:
            print(f"  $ {line}")
        print("\nDry run: nothing was changed.")
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if snapshot:
        print(f"\nDone. Undo with: plasmify restore {snapshot.id}")
    return snapshot


def _remember(preset_id: str, overrides: dict[str, Any], folder: Path | None, plan: engine.Plan, own_mode: Any) -> None:
    """Keep what was applied, for the window and the next command. The desktop has changed
    already, so a record that cannot be written only warns."""
    try:
        record.remember_tweaks(preset_id, overrides, folder, steps=[s.id for s in plan.steps], own_mode=own_mode)
    except OSError as exc:
        print(
            f"warning: could not remember what was applied ({exc.strerror or exc}); "
            "the window will not show these changes",
            file=sys.stderr,
        )


def _summary(plan: engine.Plan) -> str:
    text = plan.install_summary
    return text + (": " + ", ".join(plan.packages_missing) if plan.packages_missing else "")


def _sentence(text: str) -> str:
    return text if text.endswith((".", "?", "!")) else text + "."


def _label(preset: Preset) -> str:
    return preset.name if preset.builtin else f"{preset.name}  (yours)"


def _no_theme() -> int:
    print("No Plasmify theme is applied right now.", file=sys.stderr)
    print("Apply one with: plasmify theme <name>   (plasmify list shows them all)", file=sys.stderr)
    return 1


def change_count(preset: Preset, overrides: dict[str, Any]) -> int:
    """How many changes the window's Customize tab counts for the tweaks ``overrides`` of
    ``preset``, by the same rule (see record.changed_settings): one per setting, new colours once
    (a light/dark switch changes them all) and a glass look with its amount and focus once."""
    return record.change_count(preset, overrides)


# ----- finding a preset by a forgiving name ---------------------------------------------


def name_key(text: str) -> str:
    """``text`` folded for forgiving comparisons: "Rosé Pine_Cozy" becomes "rose-pine-cozy"."""
    folded = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(c for c in folded if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", plain).strip("-")


FUZZY_CUTOFF = 0.6  # difflib's own default for get_close_matches
FUZZY_MARGIN = 0.1  # how much better the best guess must be than the next one


@dataclass(frozen=True)
class Match:
    preset: Preset | None  # the one preset meant, when that is clear
    suggestions: list[Preset] = field(default_factory=list)
    ambiguous: bool = False  # several fit equally well (otherwise: nothing fits)


def _terms(preset: Preset) -> set[str]:
    words = set(preset.id.split("-")) | set(name_key(preset.name).split("-"))
    return {w for w in words if w} | {name_key(tag) for tag in preset.tags}


def _similarity(key: str, preset: Preset) -> float:
    candidates = {preset.id, name_key(preset.name), *_terms(preset)}
    return max(difflib.SequenceMatcher(None, key, c).ratio() for c in candidates if c)


def match_preset(query: str, candidates: list[Preset]) -> Match:
    """The preset ``query`` means: its id, display name, the start of either, its words or
    tags, or a close spelling. Earlier rules win; a rule that fits several presets stops the
    search with all of them as suggestions, so a guess is only made when it is clear."""
    key = name_key(query)
    if not key:
        return Match(None)
    words = key.split("-")
    rules: list[Callable[[Preset], bool]] = [
        lambda p: p.id == key,
        lambda p: name_key(p.name) == key,
        lambda p: len(key) >= 2 and (p.id.startswith(key) or name_key(p.name).startswith(key)),
        lambda p: set(words) <= _terms(p),
        lambda p: all(len(w) >= 2 and any(t.startswith(w) for t in _terms(p)) for w in words),
    ]
    for rule in rules:
        found = [p for p in candidates if rule(p)]
        if len(found) == 1:
            return Match(found[0])
        if found:
            return Match(None, found, ambiguous=True)
    scored = sorted(((_similarity(key, p), p) for p in candidates), key=lambda pair: -pair[0])
    close = [(score, p) for score, p in scored if score >= FUZZY_CUTOFF]
    if close and (len(close) == 1 or close[0][0] - close[1][0] >= FUZZY_MARGIN):
        best = close[0][1]
        # A word that is exactly another preset's word or tag ("everforest light") is no typo of
        # the best guess: the user meant something no preset is, so nothing is guessed.
        others = set().union(*(_terms(p) for p in candidates if p is not best))
        if not any(word in others and word not in _terms(best) for word in words):
            return Match(best)
        return Match(None, [p for _, p in close[:5]])
    if close:
        return Match(None, [p for _, p in close[:5]], ambiguous=True)
    return Match(None, [p for score, p in scored[:3] if score >= 0.45])


def find_exact(preset_id: str) -> presets.Preset:
    """The preset with exactly this id (show, apply and export take no guesses); when there is none,
    the error names the presets ``plasmify theme`` would think of."""
    try:
        return presets.find(preset_id)
    except presets.PresetError:
        found = match_preset(preset_id, presets.load_all())
        close = [found.preset] if found.preset else found.suggestions
        hint = f" Did you mean {', '.join(p.id for p in close)}?" if close else " plasmify list shows every preset."
        raise presets.PresetError(f"Unknown preset '{preset_id}'.{hint}") from None


def _explain_no_match(query: str, found: Match) -> None:
    shown = " ".join(query.split())
    if found.ambiguous:
        print(f"error: '{shown}' fits more than one preset:", file=sys.stderr)
        for preset in found.suggestions:
            print(f"  {preset.id:<20} {_label(preset)}", file=sys.stderr)
        print("Nothing was changed. Use one of the ids above.", file=sys.stderr)
        return
    print(f"error: no preset called '{shown}'.", file=sys.stderr)
    if found.suggestions:
        print("Did you mean: " + ", ".join(p.id for p in found.suggestions) + "?", file=sys.stderr)
    print("Nothing was changed. plasmify list shows every preset.", file=sys.stderr)


# ----- settings for "plasmify set" -------------------------------------------------------


class SettingError(ValueError):
    """A value typed for ``plasmify set`` that cannot be used (the message says why)."""


DEFAULT = object()  # "default" as a value: back to the preset's own setting
DEFAULT_WORDS = ("default", "preset", "reset")
LEFT_ALONE = "(left as it is)"
NO_SHIMMER = "(no shimmer)"
COLOUR_ALIASES = {"pink": "magenta", "purple": "magenta", "violet": "magenta", "teal": "cyan", "aqua": "cyan"}
# Other words for the glass looks ("subtle" is the older name of frosted).
GLASS_ALIASES = {"solid": "off", "none": "off", "no": "off", "subtle": "frosted", "on": "glass", "yes": "glass"}
ACCENT_SOURCES = ("red", "orange", "yellow", "green", "cyan", "blue", "magenta")


@dataclass(frozen=True)
class Setting:
    section: str
    keys: tuple[str, ...]  # preset keys this setting writes; the first is the one shown
    allowed: str  # for --list
    parse: Callable[[str, record.Applied], dict[str, Any]]
    show: Callable[[Preset], str]
    limits: str = ""  # replaces the schema's wording when a value is out of range


def _on_off(raw: str) -> bool:
    value = raw.strip().lower()
    if value in ("on", "yes", "true", "1", "enable", "enabled"):
        return True
    if value in ("off", "no", "false", "0", "disable", "disabled"):
        return False
    raise SettingError("must be on or off")


def _whole(raw: str, *units: str) -> int:
    text = raw.strip().lower()
    for unit in units:
        if text.endswith(unit):
            text = text[: -len(unit)].strip()
            break
    if not re.fullmatch(r"[0-9]{1,6}", text):
        raise SettingError("must be a whole number")
    return int(text)


def _fraction(raw: str) -> float:
    """80%, 80 or 0.8 all mean 0.8."""
    text = raw.strip().replace(",", ".")
    percent = text.endswith("%")
    text = text.rstrip("%").strip()
    if not re.fullmatch(r"[0-9]{1,3}(\.[0-9]{1,4})?", text):
        raise SettingError("must be a percentage like 80% or a number like 0.8")
    value = float(text)
    if percent or value > 1:
        value /= 100
    return round(value, 4)


_SECONDS = {"": 1, "s": 1, "sec": 1, "second": 1, "m": 60, "min": 60, "minute": 60, "h": 3600, "hr": 3600, "hour": 3600}


def _seconds(raw: str) -> int:
    found = re.fullmatch(r"([0-9]{1,6})\s*([a-z]*?)s?", raw.strip().lower())
    if not found or found.group(2) not in _SECONDS:
        raise SettingError("must be a time like 300, 90s, 15m or 1h")
    seconds = int(found.group(1)) * _SECONDS[found.group(2)]
    if not 60 <= seconds <= 86400:
        raise SettingError("must be from 1 minute to 24 hours")
    return seconds


def _choice(*choices: str, aliases: dict[str, str] | None = None) -> Callable[[str], str]:
    def parse(raw: str) -> str:
        value = raw.strip().lower()
        value = (aliases or {}).get(value, value)
        if value not in choices:
            raise SettingError("must be " + _either(choices))
        return value

    return parse


def _fps(raw: str) -> int:
    """Frames per second of the living wallpapers: "24", "24fps" or "24 fps"."""
    text = raw.strip().lower().removesuffix("fps").strip()
    if not text.isdigit() or int(text) not in schema.SHIMMER_FPS:
        raise SettingError("must be " + _either([str(fps) for fps in schema.SHIMMER_FPS]))
    return int(text)


def _either(choices: tuple[str, ...] | list[str]) -> str:
    return ", ".join(choices[:-1]) + " or " + choices[-1] if len(choices) > 1 else choices[0]


def _hex(raw: str) -> str | None:
    text = raw.strip().lower().lstrip("#")
    if re.fullmatch(r"[0-9a-f]{6}", text):
        return "#" + text
    if re.fullmatch(r"[0-9a-f]{3}", text):
        return "#" + "".join(c * 2 for c in text)
    return None


def _accent(raw: str, state: record.Applied) -> dict[str, Any]:
    colour = _hex(raw)
    if colour is None:
        name = COLOUR_ALIASES.get(raw.strip().lower(), raw.strip().lower())
        if name not in ACCENT_SOURCES:
            raise SettingError("must be a #rrggbb colour or a palette colour (" + _either(list(ACCENT_SOURCES)) + ")")
        colour = state.result.palette.colors[name]
    return {"accent": colour}


def _palette_ref(raw: str) -> str:
    colour = _hex(raw)
    if colour is not None:
        return colour
    name = COLOUR_ALIASES.get(raw.strip().lower(), raw.strip().lower())
    if name not in REQUIRED_KEYS:
        raise SettingError("must be a palette colour name (accent, yellow, orange, red, magenta, cyan...) or #rrggbb")
    return name


def _wallpapers(state: record.Applied, slideshow: bool = False) -> None:
    images = state.result.section("wallpaper").get("images") or []
    if not images:
        raise SettingError("cannot be set: this theme leaves the wallpaper alone")
    if slideshow and len(images) < 2:
        raise SettingError("cannot be set: this theme shows a single picture, so there is no slideshow to time")


def _wallpaper(key: str, convert: Callable[[str], Any], slideshow: bool = False):
    def parse(raw: str, state: record.Applied) -> dict[str, Any]:
        _wallpapers(state, slideshow)
        return {key: convert(raw)}

    return parse


def _plain(key: str, convert: Callable[[str], Any]):
    return lambda raw, _state: {key: convert(raw)}


def _wobbly(raw: str, _state: record.Applied) -> dict[str, Any]:
    text = raw.strip().lower()
    if re.fullmatch(r"[0-9]{1,3}", text):
        return {"wobbly": True, "wobbly_level": int(text)}
    try:
        return {"wobbly": _on_off(text)}
    except SettingError:
        raise SettingError("must be off, on, or a level from 0 (a little) to 4 (a lot)") from None


def _glass(raw: str, state: record.Applied) -> dict[str, Any]:
    look = _choice(*schema.GLASS_LEVELS, aliases=GLASS_ALIASES)(raw)
    # The preset's own look is no change, even when the preset calls it by an older name.
    return {"glass": DEFAULT if look == glass.level_of(state.preset.data) else look}


def _see_through(raw: str, _state: record.Applied) -> dict[str, Any]:
    """40, 40% and 0.4 all mean 40 percent."""
    try:
        percent = _fraction(raw) * 100
    except SettingError:
        raise SettingError("must be a percentage like 40% (or 40, or 0.4)") from None
    if abs(percent - round(percent)) > 1e-6:
        raise SettingError("must be a whole percentage like 40%")
    return {"transparency": round(percent)}


def _focused_solid(raw: str, state: record.Applied) -> dict[str, Any]:
    """On or off. What the preset gives already is no change, as in the window: on, unless the
    preset itself sets it (then _put drops a value equal to the preset's)."""
    value = _on_off(raw)
    own = state.preset.section("style").get("focused_solid")
    return {"focused_solid": DEFAULT if not isinstance(own, bool) and value == glass.FOCUSED_SOLID else value}


def _transparency_text(result: Preset) -> str:
    if glass.level_of(result.data) == "off":
        return "(glass is off)"
    return f"{glass.effective_transparency(result.data)}%"


def _focused_solid_text(result: Preset) -> str:
    if glass.level_of(result.data) == "off":
        return "(glass is off)"
    return _on_text(glass.effective_focused_solid(result.data))


def _dim(raw: str, _state: record.Applied) -> dict[str, Any]:
    text = raw.strip().lower()
    if text in ("off", "no", "false", "none"):
        return {"dim_inactive": 0}
    return {"dim_inactive": _whole(text, "%")}


def installed_fonts() -> set[str] | None:
    """Font families installed here (None when fontconfig cannot tell)."""
    return fonts.families()


def _installed(name: str, available: list[str] | set[str] | None, what: str) -> str:
    """``name`` as it is installed (any case), or a SettingError with close spellings."""
    if available is None:
        return name
    by_case = {item.casefold(): item for item in available}
    if name.casefold() in by_case:
        return by_case[name.casefold()]
    close = difflib.get_close_matches(name.casefold(), list(by_case), n=3, cutoff=0.6)
    hint = (" Did you mean " + _either([by_case[c] for c in close]) + "?") if close else ""
    raise SettingError(f"is not an installed {what} ({name}).{hint}")


def _font(key: str):
    def parse(raw: str, _state: record.Applied) -> dict[str, Any]:
        family = " ".join(raw.split())
        if schema.FONT(family):
            raise SettingError("must be a font family name (letters, digits, spaces, . _ + -)")
        return {key: _installed(family, installed_fonts(), "font")}

    return parse


def _icons(raw: str, state: record.Applied) -> dict[str, Any]:
    name = raw.strip()
    if schema.NAME(name):
        raise SettingError("must be an icon theme name (letters, digits, . _ + -)")
    name = _installed(name, installed_icon_themes(), "icon theme")
    return {"theme": DEFAULT if name == state.preset.section("icons").get("theme") else name}


def _cursor(raw: str, state: record.Applied) -> dict[str, Any]:
    name = raw.strip()
    if schema.NAME(name):
        raise SettingError("must be a cursor theme name (letters, digits, . _ + -)")
    name = _installed(name, installed_cursor_themes(), "cursor theme")
    if name == state.preset.section("cursor").get("name"):
        return {"name": DEFAULT, "url": DEFAULT}
    return {"name": name, "url": None}  # installed already: nothing to download


def _on_text(value: Any) -> str:
    return "on" if value else "off"


def _shown(section: str, key: str, fmt: Callable[[Any], str] = str, fallback: Any = None) -> Callable[[Preset], str]:
    def show(result: Preset) -> str:
        table = result.data.get(section)
        value = table.get(key, fallback) if isinstance(table, dict) else None
        return LEFT_ALONE if value is None else fmt(value)

    return show


def _shimmer_shown(key: str, fmt: Callable[[Any], str] = str, fallback: Any = None) -> Callable[[Preset], str]:
    """A Shimmer detail, which does nothing (and so is no change) while there is no shimmer."""
    shown = _shown("wallpaper", key, fmt, fallback)

    def show(result: Preset) -> str:
        if result.section("wallpaper").get("shimmer", "none") == "none":
            return NO_SHIMMER
        return shown(result)

    return show


def _interval_text(seconds: Any) -> str:
    seconds = int(seconds)
    if seconds % 3600 == 0:
        return f"{seconds // 3600} hour{'s' if seconds != 3600 else ''}"
    if seconds % 60 == 0:
        return f"{seconds // 60} minute{'s' if seconds != 60 else ''}"
    return f"{seconds} seconds"


def _wobbly_text(result: Preset) -> str:
    effects = result.data.get("effects")
    if not isinstance(effects, dict):
        return LEFT_ALONE
    if not effects.get("wobbly", False):
        return "off"
    return f"on, level {effects.get('wobbly_level', 2)}"


SETTINGS: dict[str, Setting] = {
    "accent": Setting(
        "palette",
        ("accent",),
        "a #rrggbb colour, or a palette colour: " + _either(list(ACCENT_SOURCES)),
        _accent,
        lambda result: result.palette.colors["accent"],
    ),
    "glass": Setting(
        "style",
        ("glass",),
        "see-through app windows: off (solid), frosted (blurred, a little see-through), "
        "glass (clearly see-through, light blur) or clear (see-through, no blur)",
        _glass,
        lambda result: glass.level_of(result.data),
    ),
    "transparency": Setting(
        "style",
        ("transparency",),
        "how see-through glass windows are: 10% to 70% (each glass look has its own default)",
        _see_through,
        _transparency_text,
        "must be from 10% to 70%",
    ),
    "focused-solid": Setting(
        "style",
        ("focused_solid",),
        "on or off: keep the window you use solid, so only the windows behind it are see-through "
        "(on unless you turn it off)",
        _focused_solid,
        _focused_solid_text,
    ),
    "shimmer": Setting(
        "wallpaper",
        ("shimmer",),
        "living wallpapers: " + _either(schema.SHIMMER_STYLES),
        _wallpaper(
            "shimmer",
            _choice(
                *schema.SHIMMER_STYLES,
                aliases={
                    "off": "none",
                    "no": "none",
                    "sparkles": "sparkle",
                    "ember": "embers",
                    "petal": "petals",
                    "sakura": "petals",
                    "firefly": "fireflies",
                    "stars": "starfall",
                    "circle": "magic",
                    "speed": "speedlines",
                },
            ),
        ),
        _shown("wallpaper", "shimmer", fallback="none"),
    ),
    "shimmer-light": Setting(
        "wallpaper",
        ("shimmer_light",),
        "the light over the picture: " + _either(schema.SHIMMER_LIGHTS) + " (auto suits the style)",
        _wallpaper("shimmer_light", _choice(*schema.SHIMMER_LIGHTS, aliases={"off": "none", "ray": "rays"})),
        _shimmer_shown("shimmer_light", fallback="auto"),
    ),
    "shimmer-motion": Setting(
        "wallpaper",
        ("shimmer_motion",),
        "how the picture moves: " + _either(schema.SHIMMER_MOTIONS) + " (auto suits the style)",
        _wallpaper("shimmer_motion", _choice(*schema.SHIMMER_MOTIONS, aliases={"off": "still", "none": "still"})),
        _shimmer_shown("shimmer_motion", fallback="auto"),
    ),
    "shimmer-density": Setting(
        "wallpaper",
        ("shimmer_density",),
        "how many particles: " + _either(schema.SHIMMER_DENSITIES),
        _wallpaper("shimmer_density", _choice(*schema.SHIMMER_DENSITIES, aliases={"medium": "normal"})),
        _shimmer_shown("shimmer_density", fallback="normal"),
    ),
    "shimmer-fps": Setting(
        "wallpaper",
        ("shimmer_fps",),
        "frames per second of living wallpapers: 12 (lighter) or 24 (smoother)",
        _wallpaper("shimmer_fps", _fps),
        _shimmer_shown("shimmer_fps", fallback=12),
    ),
    "shimmer-colour": Setting(
        "wallpaper",
        ("shimmer_colour",),
        "a palette colour name (accent, yellow, orange, magenta...) or #rrggbb",
        _wallpaper("shimmer_colour", _palette_ref),
        _shimmer_shown("shimmer_colour", fallback="accent"),
    ),
    "shimmer-always": Setting(
        "wallpaper",
        ("shimmer_always",),
        "on or off: keep the shimmer moving while windows cover the desktop",
        _wallpaper("shimmer_always", _on_off),
        _shimmer_shown("shimmer_always", _on_text, fallback=False),
    ),
    "interval": Setting(
        "wallpaper",
        ("interval",),
        "time between slideshow pictures: 300, 90s, 15m or 1h (1 minute to 24 hours)",
        _wallpaper("interval", _seconds, slideshow=True),
        _shown("wallpaper", "interval", _interval_text, fallback=1800),
    ),
    "blur": Setting(
        "effects",
        ("blur",),
        "on or off: blur behind panels and menus",
        _plain("blur", _on_off),
        _shown("effects", "blur", _on_text, fallback=True),
    ),
    "blur-strength": Setting(
        "effects",
        ("blur_strength",),
        "1 to 15",
        _plain("blur_strength", _whole),
        _shown("effects", "blur_strength", fallback=10),
    ),
    "wobbly": Setting(
        "effects",
        ("wobbly", "wobbly_level"),
        "off, on, or a level from 0 (a little) to 4 (a lot)",
        _wobbly,
        _wobbly_text,
    ),
    "cube": Setting(
        "effects",
        ("cube",),
        "on or off: the desktop cube (Meta+C)",
        _plain("cube", _on_off),
        _shown("effects", "cube", _on_text, fallback=False),
    ),
    "fall-apart": Setting(
        "effects",
        ("fall_apart",),
        "on or off: closing windows fall apart",
        _plain("fall_apart", _on_off),
        _shown("effects", "fall_apart", _on_text, fallback=False),
    ),
    "sheet": Setting(
        "effects",
        ("sheet",),
        "on or off: dialogs slide down out of their window's title bar",
        _plain("sheet", _on_off),
        _shown("effects", "sheet", _on_text, fallback=False),
    ),
    "blur-saturation": Setting(
        "effects",
        ("blur_saturation",),
        "how vivid colours behind blur look: 100% (as they are) to 500%",
        _plain("blur_saturation", lambda raw: _whole(raw, "%")),
        _shown("effects", "blur_saturation", lambda v: f"{v}%", fallback=100),
        "must be from 100% to 500%",
    ),
    "blur-noise": Setting(
        "effects",
        ("blur_noise",),
        "grain on blurred surfaces, for a frosted look: 0 (none) to 14",
        _plain("blur_noise", _whole),
        _shown("effects", "blur_noise", fallback=4),
        "must be from 0 to 14",
    ),
    "moving-opacity": Setting(
        "effects",
        ("moving_opacity",),
        "how opaque windows are while you move them: 30% to 100% (or 0.3 to 1.0)",
        _plain("moving_opacity", _fraction),
        _shown("effects", "moving_opacity", lambda v: f"{round(float(v) * 100)}%", fallback=1.0),
        "must be from 30% to 100%",
    ),
    "dim-inactive": Setting(
        "effects",
        ("dim_inactive",),
        "off, or how much to dim windows you are not using: 10 to 90",
        _dim,
        _shown("effects", "dim_inactive", lambda v: "off" if v == 0 else str(v), fallback=0),
    ),
    "panel-opacity": Setting(
        "panel_style",
        ("opacity",),
        "20% to 100% (or 0.2 to 1.0)",
        _plain("opacity", _fraction),
        _shown("panel_style", "opacity", lambda v: f"{round(float(v) * 100)}%", fallback=0.85),
        "must be from 20% to 100%",
    ),
    "radius": Setting(
        "panel_style",
        ("radius",),
        "panel corners: 0 to 30 pixels",
        _plain("radius", lambda raw: _whole(raw, "px")),
        _shown("panel_style", "radius", lambda v: f"{v} px", fallback=6),
    ),
    "ui-font": Setting(
        "fonts",
        ("ui",),
        'an installed font, e.g. "Noto Sans" (quote names with spaces)',
        _font("ui"),
        _shown("fonts", "ui"),
    ),
    "mono-font": Setting(
        "fonts",
        ("mono",),
        'an installed monospace font, e.g. "JetBrains Mono"',
        _font("mono"),
        _shown("fonts", "mono"),
    ),
    "font-size": Setting(
        "fonts",
        ("ui_size",),
        "interface font size: 6 to 24",
        _plain("ui_size", lambda raw: _whole(raw, "pt")),
        _shown("fonts", "ui_size", fallback=10),
    ),
    "mono-size": Setting(
        "fonts",
        ("mono_size",),
        "monospace font size: 6 to 32",
        _plain("mono_size", lambda raw: _whole(raw, "pt")),
        _shown("fonts", "mono_size", fallback=10),
    ),
    "icons": Setting(
        "icons",
        ("theme",),
        "an installed icon theme, e.g. Papirus-Dark",
        _icons,
        _shown("icons", "theme"),
    ),
    "cursor": Setting(
        "cursor",
        ("name", "url"),
        "an installed cursor theme, e.g. Bibata-Modern-Ice",
        _cursor,
        _shown("cursor", "name"),
    ),
}
NAME_WIDTH = max(len(name) for name in SETTINGS)  # the column of setting names in listings
SETTING_ALIASES = {
    "shimmer-color": "shimmer-colour",
    "accent-colour": "accent",
    "accent-color": "accent",
    "corners": "radius",
    "panel-radius": "radius",
    "opacity": "panel-opacity",
    "icon-theme": "icons",
    "cursor-theme": "cursor",
    "font": "ui-font",
    "slideshow": "interval",
    "dim": "dim-inactive",
    "fallapart": "fall-apart",
    "see-through": "transparency",
    "solid-focus": "focused-solid",
}
# What each preset section feeds; the palette is handled on its own (see _affected_steps).
SECTION_STEPS = {
    "style": "style",
    "icons": "icons",
    "cursor": "cursor",
    "fonts": "fonts",
    "layout": "layout",
    "panel_style": "panelstyle",
    "wallpaper": "wallpaper",
    "effects": "effects",
    "terminal": "terminal",
    "shell": "shell",
    "splash": "splash",
    "login": "login",
}


def setting_for(name: str) -> tuple[str, Setting] | None:
    key = name.strip().lower().replace("_", "-")
    key = SETTING_ALIASES.get(key, key)
    return (key, SETTINGS[key]) if key in SETTINGS else None


def _same(a: Any, b: Any) -> bool:
    return isinstance(a, bool) == isinstance(b, bool) and a == b


def _put(overrides: dict[str, Any], preset: Preset, section: str, updates: dict[str, Any]) -> None:
    """Write ``updates`` into the tweaks of ``section``; a value equal to the preset's own (or
    DEFAULT) drops the tweak, so only real changes are kept, as in Customize."""
    if section in overrides and not isinstance(overrides[section], dict):
        raise SettingError("cannot be set: this part of the theme is switched off on the Customize tab")
    table = dict(overrides.get(section) or {})
    own = preset.section(section)
    for key, value in updates.items():
        if value is DEFAULT or (value is not None and key in own and _same(own[key], value)):
            table.pop(key, None)
        else:
            table[key] = value
    if table:
        overrides[section] = table
    else:
        overrides.pop(section, None)


def desktop_font(which: str) -> str | None:
    """The family of the desktop's interface ("font") or monospace ("fixed") font."""
    value = Runner().kread("kdeglobals", "General", which)
    family = value.split(",", 1)[0].strip()
    return family if family and not schema.FONT(family) else None


def _complete_fonts(overrides: dict[str, Any], preset: Preset) -> None:
    """Fonts come in pairs: a theme without fonts keeps the desktop's other font. Like Customize,
    the preset's font packages only stay while one of its fonts is still used."""
    table = overrides.get("fonts")
    if not isinstance(table, dict):
        return
    own = preset.section("fonts")
    for key, which in (("ui", "font"), ("mono", "fixed")):
        if not table.get(key, own.get(key)):
            family = desktop_font(which)
            if family is None:
                raise SettingError("cannot be set alone here: this theme sets no fonts, so set ui-font and mono-font")
            table[key] = family
    if own.get("packages"):
        if table.get("ui", own.get("ui")) != own.get("ui") and table.get("mono", own.get("mono")) != own.get("mono"):
            table["packages"] = None
        else:
            table.pop("packages", None)


def _affected_steps(before: Preset, after: Preset, entry: dict[str, Any], edited: set[str]) -> list[str]:
    """Only the steps whose part of the theme changed. A colour change runs the colour steps
    that were applied for this theme before (like the light/dark switch), and so does a part
    that only changed along with the ``edited`` sections (glass lowers the terminal opacity):
    a step the user left out does not start running."""
    known = isinstance(entry.get("steps"), list)
    ran = entry["steps"] if known else []
    wanted = {
        step
        for section, step in SECTION_STEPS.items()
        if before.data.get(section) != after.data.get(section) and (section in edited or step in ran or not known)
    }
    if before.palette.colors != after.palette.colors:
        wanted |= set(record.mode_steps(entry, after))
        shimmering = after.section("wallpaper").get("shimmer", "none") != "none"
        if shimmering and "wallpaper" in ran and shimmer_colour(before) != shimmer_colour(after):
            wanted.add("wallpaper")  # the sparkles follow the accent
    if before.section("fonts").get("mono") != after.section("fonts").get("mono") and "terminal" in ran:
        wanted.add("terminal")  # terminals use the monospace font
    if before.section("panel_style") and not after.section("panel_style"):
        wanted.add("colors")  # without a panel style the colour step puts the user's own one back
    return [s.id for s in ALL_STEPS if s.id in wanted and s.applies_to(after)]


def edit_overrides(state: record.Applied, pairs: list[tuple[str, str]]) -> tuple[dict[str, Any], list[str]]:
    """The applied tweaks with ``pairs`` (setting, value) changed, and the setting names used.
    Raises SettingError with a message naming the setting that is wrong."""
    overrides = copy.deepcopy(state.overrides)
    names: list[str] = []
    for raw_key, raw_value in pairs:
        found = setting_for(raw_key)
        if found is None:
            close = difflib.get_close_matches(raw_key.strip().lower(), [*SETTINGS, *SETTING_ALIASES], n=1)
            hint = f" Did you mean {SETTING_ALIASES.get(close[0], close[0])}?" if close else ""
            raise SettingError(f"unknown setting '{raw_key.strip()}'.{hint} plasmify set --list shows them all")
        name, setting = found
        if name in names:
            raise SettingError(f"{name} is given twice")
        names.append(name)
        try:
            if raw_value.strip().lower() in DEFAULT_WORDS:
                updates: dict[str, Any] = dict.fromkeys(setting.keys, DEFAULT)
            else:
                updates = setting.parse(raw_value, state)
            for key, value in updates.items():
                if value is DEFAULT or value is None:
                    continue
                check = schema.hex_colour if setting.section == "palette" else schema.SCHEMA[setting.section][0][key][0]
                problem = check(value)
                if problem:
                    raise SettingError(setting.limits or problem)
            _put(overrides, state.preset, setting.section, updates)
            if setting.section == "fonts":
                _complete_fonts(overrides, state.preset)
        except SettingError as exc:
            raise SettingError(f"{name} {exc}") from None
    style = overrides.get("style")
    for name, key, example in (("transparency", "transparency", "30%"), ("focused-solid", "focused_solid", "off")):
        chosen = isinstance(style, dict) and style.get(key) is not None
        if name in names and chosen and glass.level_of(presets.merge(state.preset.data, overrides)) == "off":
            raise SettingError(
                f"{name} cannot be set while glass is off. Pick a look along with it, "
                f"for example: plasmify set glass frosted {name} {example}"
            )
    return overrides, names


# ----- commands -------------------------------------------------------------------------


def cmd_list(_: argparse.Namespace) -> int:
    all_presets = presets.load_all()
    active = engine.active_preset(all_presets)
    for preset in all_presets:
        marker = "*" if active and preset.id == active.id else " "
        print(f"{marker} {preset.id:<20} {preset.name:<22} {preset.description}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    preset = find_exact(args.preset)
    plan = engine.make_plan(preset)
    print(f"{preset.name} ({preset.id}): {preset.description}\n")
    for step in plan.steps:
        print(f"  {step.id:<10} {step.description}")
    print(f"\nPackages: {', '.join(plan.packages_needed) or 'none'}")
    hosts = preset.untrusted_hosts()
    print("Downloads from: " + ", ".join(sorted({h for h in map(_host, schema.urls(preset.data))})))
    if hosts:
        print("Not on the trusted list (asks before contacting): " + ", ".join(hosts))
    print(plan.install_summary)
    credits = preset.section("wallpaper").get("images", [])
    if credits:
        print("\nWallpaper credits:")
        for image in credits:
            print(f"  - {image['credit']}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    if not args.dry_run and not _check_environment():
        return 1
    preset = find_exact(args.preset)
    step_ids = _step_selection(args)
    if step_ids is None:
        return 2
    plan = engine.make_plan(preset, step_ids)
    print(f"Applying {preset.name}: {', '.join(s.id for s in plan.steps)}")
    print(plan.install_summary)
    if plan.packages_missing and not args.yes and not args.dry_run:
        answer = input(f"Install {' '.join(plan.packages_missing)}? [Y/n] ").strip().lower()
        if answer not in ("", "y", "yes"):
            return 1
    hosts = preset.untrusted_hosts()
    if not _approve_hosts(hosts, args):
        return 1
    snapshot = _run(plan, args, hosts)
    if snapshot is not None and not args.dry_run:
        _remember(preset.id, {}, None, plan, None)  # the preset as it is: the window shows no tweaks
    return 0


def cmd_theme(args: argparse.Namespace) -> int:
    mode = "light" if args.light else "dark" if args.dark else "toggle" if args.toggle else None
    if mode is not None:
        if args.name is not None or args.only or args.skip or args.plain:
            print(
                "error: --light, --dark and --toggle switch the applied theme, so leave out the name, "
                "--only, --skip and --plain",
                file=sys.stderr,
            )
            return 2
        return _switch_mode(args, mode)
    if args.name is None:
        return _show_theme()
    return _apply_named(args)


def _show_theme() -> int:
    state = record.applied(presets.load_all())
    if state is None:
        return _no_theme()
    preset, result = state.preset, state.result
    mode = "dark" if state.dark else "light"
    own = "dark" if preset.palette.dark else "light"
    note = f" (switched from the preset's {own} colours)" if state.dark != preset.palette.dark else ""
    print(f"{_label(preset)} ({preset.id}), {mode}{note}\n")
    try:
        plain = presets.customized(preset, {})  # the preset as it is applied (glass fills in blur...)
    except presets.PresetError:
        plain = preset
    changed = []
    for name, setting in SETTINGS.items():
        shown = setting.show(result)
        mark = " "
        if shown != setting.show(plain):
            mark = "*"
            changed.append(name)
        print(f"{mark} {name:<{NAME_WIDTH}}  {shown}")
    total = change_count(state.preset, state.overrides)
    print()
    if changed:
        print("* changed from the preset (in the window or with plasmify set).")
    if total:
        noun = "change" if total == 1 else "changes"
        print(f"{total} Customize {noun} in all; plasmify reset puts the preset's own settings back.")
    other = "light" if state.dark else "dark"
    print(f"Switch to {other} with: plasmify theme --{other}")
    print("Change a setting with: plasmify set <key> <value>   (plasmify set --list shows them)")
    return 0


def _apply_named(args: argparse.Namespace) -> int:
    problem = input_problem(args.name, MAX_NAME_LENGTH)
    if problem:
        print(f"error: the theme name {problem}. Nothing was changed.", file=sys.stderr)
        return 2
    step_ids = _step_selection(args)
    if step_ids is None:
        return 2
    all_presets = presets.load_all()
    found = match_preset(args.name, all_presets)
    if found.preset is None:
        _explain_no_match(args.name, found)
        return 1
    preset = found.preset
    if not args.dry_run and not _check_environment():
        return 1

    entry = record.entry_for(preset.id)
    folder = record.folder_of(entry)
    own_mode: Any = record.KEEP
    if args.plain:
        overrides, folder, own_mode = {}, None, None
    else:
        # The changes made to this preset come along, like in the window, in the light/dark
        # mode the desktop shows when it is the theme on screen.
        active = engine.active_preset(all_presets)
        live = record.live_mode() if active is not None and active.id == preset.id else None
        overrides, stale, keep = record.desktop_tweaks(
            preset, record.overrides_of(entry), folder, entry.get("own_mode"), live
        )
        if stale:
            own_mode = keep
    effective = presets.customized(preset, overrides, record.usable(folder))
    plan = engine.make_plan(effective, step_ids)
    if not plan.steps:
        print(f"error: nothing to apply: {preset.name} has none of the chosen steps", file=sys.stderr)
        return 1
    count = change_count(preset, overrides)
    extra = f" with your {count} change{'s' if count != 1 else ''}" if count else ""
    print(f"Applying {_label(preset)} ({preset.id}){extra}: {', '.join(s.id for s in plan.steps)}")
    print(_summary(plan))
    if count:
        print(f"Your changes come along; plasmify theme {preset.id} --plain applies the preset as it is.")
    if not _confirm(f"Apply {preset.name} now?", args):
        return 1
    if not _approve_hosts(_hosts_to_approve(plan.preset, overrides, preset), args):
        return 1
    _run(plan, args, plan.preset.untrusted_hosts())
    if not args.dry_run:
        _remember(preset.id, overrides, folder, plan, own_mode)
    return 0


def _switch_mode(args: argparse.Namespace, mode: str) -> int:
    state = record.applied(presets.load_all())
    if state is None:
        return _no_theme()
    dark = not state.dark if mode == "toggle" else mode == "dark"
    wanted = "dark" if dark else "light"
    if dark == state.dark:
        print(f"{state.preset.name} is already {wanted}. Nothing was changed.")
        return 0
    if not args.dry_run and not _check_environment():
        return 1
    try:
        switch = record.switch_mode(state, dark)
    except (presets.PresetError, KeyError, ValueError) as exc:
        print(f"error: could not switch: {exc}", file=sys.stderr)
        return 1
    plan = engine.make_plan(switch.preset, switch.steps)
    if not plan.steps:
        print("error: nothing to switch: no colour step was applied for this theme", file=sys.stderr)
        return 1
    print(f"Switching {_label(state.preset)} to {wanted} colours: {', '.join(s.id for s in plan.steps)}")
    print(
        "Only these steps run, so wallpapers, panel layout and fonts stay. A preset's own Kvantum theme\n"
        "makes way for Breeze or glass until you switch back. plasmify restore can undo it."
    )
    print(_summary(plan))
    if not _confirm(f"Switch to {wanted} now?", args):
        return 1
    if not _approve_hosts(_hosts_to_approve(plan.preset, switch.overrides, state.preset), args):
        return 1
    _run(plan, args, plan.preset.untrusted_hosts())
    if not args.dry_run:
        _remember(state.preset.id, switch.overrides, state.folder, plan, switch.keep)
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    if args.list:
        return _list_settings()
    items = args.pairs
    if not items:
        print("error: give a setting and a value, for example: plasmify set accent ff79c6", file=sys.stderr)
        print("plasmify set --list shows every setting.", file=sys.stderr)
        return 2
    if len(items) > 2 * MAX_PAIRS:
        print(f"error: at most {MAX_PAIRS} settings at once", file=sys.stderr)
        return 2
    for index, item in enumerate(items):
        what = "a setting name" if index % 2 == 0 else "a value"
        problem = input_problem(item, MAX_KEY_LENGTH if index % 2 == 0 else MAX_VALUE_LENGTH)
        if problem:
            print(f"error: {what} {problem}. Nothing was changed.", file=sys.stderr)
            return 2
    if len(items) % 2:
        print(f"error: {items[-1].strip()} needs a value. Nothing was changed.", file=sys.stderr)
        return 2
    pairs = list(zip(items[0::2], items[1::2], strict=True))

    state = record.applied(presets.load_all())
    if state is None:
        return _no_theme()
    try:
        overrides, names = edit_overrides(state, pairs)
        result = presets.customized(state.preset, overrides, state.usable)
    except (SettingError, presets.PresetError) as exc:
        print(f"error: {_sentence(str(exc))} Nothing was changed.", file=sys.stderr)
        return 1
    if result.data == state.result.data:
        print(f"Nothing to change: {state.preset.name} already looks like that.")
        return 0
    steps = _affected_steps(state.result, result, state.record, {SETTINGS[name].section for name in names})
    if not args.dry_run and not _check_environment():
        return 1
    plan = engine.make_plan(result, steps)
    print(f"Changing {_label(state.preset)}:")
    for name in names:
        setting = SETTINGS[name]
        print(f"  {name:<{NAME_WIDTH}}  {setting.show(state.result)} -> {setting.show(result)}")
    accent = result.palette.colors["accent"]
    if "accent" in names and contrast(accent, result.palette.colors["base"]) < 3:
        ratio = contrast(accent, result.palette.colors["base"])
        print(f"Note: this accent is hard to see on the background (contrast {ratio:.1f}:1; 3:1 or more reads well).")
    print(f"Runs: {', '.join(s.id for s in plan.steps) or 'nothing'}")
    print(_summary(plan))
    if not _confirm("Apply now?", args):
        return 1
    if not _approve_hosts(_hosts_to_approve(plan.preset, overrides, state.preset), args):
        return 1
    if plan.steps:
        _run(plan, args, plan.preset.untrusted_hosts())
    if not args.dry_run:
        _remember(
            state.preset.id, overrides, state.folder, plan, state.record.get("own_mode") if state.stale else record.KEEP
        )
    return 0


def _list_settings() -> int:
    print("Settings of the applied theme (plasmify set <key> <value> [<key> <value> ...]):\n")
    for name, setting in SETTINGS.items():
        print(f"  {name:<{NAME_WIDTH}}  {setting.allowed}")
    print('\nEvery setting also takes "default": back to the preset\'s own value.')
    print("A # starts a comment in the shell, so quote colours ('#ff79c6') or leave the # out (ff79c6).")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    state = record.applied(presets.load_all())
    if state is None:
        return _no_theme()
    preset = state.preset
    saved = record.overrides_of(state.record)
    if not saved and not state.overrides:
        print(f"{preset.name} has no changes to drop: it is applied as the preset defines it.")
        return 0
    if not args.dry_run and not _check_environment():
        return 1
    plain = presets.customized(preset, {})
    # Only the parts the changes touched run again, like plasmify set, and only those that were
    # applied: a change that never reached the desktop (its step was left out) has nothing to undo.
    plan = engine.make_plan(plain, _affected_steps(state.result, plain, state.record, set()))
    print(
        f"Resetting {_label(preset)} to the preset's own settings: {', '.join(s.id for s in plan.steps) or 'nothing'}"
    )
    print(_summary(plan))
    # The colour step puts back the user's own Plasma style for a preset without a panel style,
    # so only other parts the preset does not have are left as they are.
    changed = {*saved, *state.overrides}
    alone = sorted(s.replace("_", " ") for s in changed if s not in preset.data and s != "panel_style")
    if alone:
        print(
            f"Note: the preset leaves {_either(alone)} alone, so your changes there stay on the desktop; "
            "plasmify restore can undo them."
        )
    if not _confirm(f"Reset {preset.name} now?", args):
        return 1
    hosts = plan.preset.untrusted_hosts()
    if not _approve_hosts(hosts, args):
        return 1
    if plan.steps:
        _run(plan, args, hosts)
    if not args.dry_run:
        _remember(preset.id, {}, None, plan, None)
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    backups = backup.list_backups()
    if not backups:
        print("No backups yet.")
        return 1
    if args.backup is None:
        for b in backups:
            created = time.strftime("%Y-%m-%d %H:%M", time.localtime(b.created))
            print(f"  {b.id}  {created}  {b.label}{'  (always kept)' if b.pinned else ''}")
        print("\nRestore one with: plasmify restore <id>   ('latest', or 'original' for before Plasmify)")
        return 0
    if args.backup == "latest":
        chosen = backups[0]
    elif args.backup == "original":
        chosen = next((b for b in backups if b.pinned), None)
    else:
        chosen = next((b for b in backups if b.id == args.backup), None)
    if chosen is None:
        print(f"error: no backup '{args.backup}'", file=sys.stderr)
        return 1
    if args.backup == "original" and not backup.is_original(chosen):
        print("Note: your setup from before Plasmify is no longer kept (older versions kept only ten backups);")
        print(f"restoring the oldest backup there is instead: {chosen.label}")
    engine.restore(chosen, Runner(dry_run=args.dry_run))
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    active = engine.active_preset(presets.load_all())
    freed = engine.clean(active.id if active else None, Runner(dry_run=args.dry_run))
    print(f"Would free {packages.human_size(freed)}" if args.dry_run else f"Freed {packages.human_size(freed)}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    paths = [Path(p) for p in args.files] or [Path(str(p)) for p in presets.builtin_paths()]
    failed = 0
    for path in paths:
        try:
            presets.load(path)
            print(f"ok    {path}")
        except (presets.PresetError, OSError, ValueError) as exc:
            failed += 1
            print(f"FAIL  {exc}")
    return 1 if failed else 0


def cmd_log(args: argparse.Namespace) -> int:
    if args.clear:
        activity.clear()
        print("Download history cleared.")
        return 0
    rows = activity.entries(args.limit)
    if not rows:
        print("Nothing downloaded or installed yet.")
        return 0
    for e in rows:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(e.time))
        status = "ok " if e.ok else "ERR"
        size = packages.human_size(e.size) if e.size else ""
        print(f"{when}  {status}  {e.kind:<9} {e.preset or '-':<18} {e.source}")
        line = f"{'':>24}-> {e.detail}"
        if size:
            line += f"  [{size}]"
        if e.sha256:
            line += f"  {'commit' if e.kind == 'git' else 'sha256'} {e.sha256[:16]}"
        if e.error:
            line += f"  error: {e.error}"
        print(line)
    print(f"\nFull log: {activity.LOG_FILE}")
    return 0


def cmd_import(args: argparse.Namespace) -> int:
    try:
        notes: list[str] = []
        preset = bundle.import_file(Path(args.file), replace=args.replace, notes=notes, custom_root=CUSTOM_COPIES)
    except FileExistsError:
        print("error: a preset with this id exists; use --replace to overwrite it", file=sys.stderr)
        return 1
    print(f"Imported {preset.name} ({preset.id}). Apply it with: plasmify apply {preset.id}")
    for note in notes:
        print(f"Note: {note}")
    hosts = preset.untrusted_hosts()
    if hosts:
        print("Note: it downloads from hosts outside the trusted list: " + ", ".join(hosts))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    preset = find_exact(args.preset)
    written = bundle.export_bundle(preset, Path(args.output or f"{preset.id}{bundle.BUNDLE_SUFFIX}"))
    print(f"Wrote {written}")
    return 0


def cmd_save_desktop(args: argparse.Namespace) -> int:
    result = capture.capture(args.name, args.description or "", include_wallpapers=not args.no_wallpapers)
    try:
        preset = bundle.save_user_preset(result.data, result.files, replace=args.replace, custom_root=CUSTOM_COPIES)
    except FileExistsError:
        print("error: a preset with this id exists; use --replace to overwrite it", file=sys.stderr)
        return 1
    print(f"Saved your desktop as {preset.name} ({preset.id}).")
    for note in result.notes:
        print(f"  note: {note}")
    print(f"Share it with: plasmify export {preset.id}")
    return 0


def cmd_gui(_: argparse.Namespace) -> int:
    try:
        from plasmify.gui.app import run
    except ImportError as exc:
        print(
            f"error: the GUI needs PyQt6 (run ./install.sh, or install your distribution's PyQt6 package): {exc}",
            file=sys.stderr,
        )
        return 1
    return run()


# ----- argument parsing -----------------------------------------------------------------

MAIN_EPILOG = """\
quick start:
  plasmify theme                    show the applied theme and its key settings
  plasmify theme nord               apply Nord Minimal (a part of the name is enough)
  plasmify theme --toggle           switch the applied theme between light and dark
  plasmify set accent ff79c6 blur on
  plasmify reset                    drop your changes to the applied theme

"plasmify gui" opens the window. "plasmify <command> --help" shows the options and
examples of each command. Documentation: https://plasmify.github.io
"""

THEME_EPILOG = """\
examples:
  plasmify theme                       show the applied theme, its mode and key settings
  plasmify theme nord                  apply Nord Minimal ("nordic" or "Nord Minimal" work too)
  plasmify theme tokyo --skip layout,effects
  plasmify theme dracula --dry-run     show the commands without running them
  plasmify theme --toggle              switch the applied theme between light and dark
  plasmify theme --light               switch it to light colours (--dark for dark)

The name can be a preset id, its name, the start of either, one of its words or tags, or
a close spelling. When it fits several presets they are listed and nothing changes.
Changes you made to a preset (in the window or with plasmify set) come along when you
apply it again; --plain applies the preset exactly as it is defined.
"""

SET_EPILOG = """\
examples:
  plasmify set accent ff79c6                  a new accent colour
  plasmify set accent magenta                 or one from the palette
  plasmify set glass frosted blur-strength 12
  plasmify set glass clear transparency 50%   see-through windows without blur
  plasmify set glass clear focused-solid off  see through the window you use too
  plasmify set wobbly 3 cube on
  plasmify set panel-opacity 80% radius 14
  plasmify set ui-font "Noto Sans" font-size 11
  plasmify set shimmer petals shimmer-colour accent
  plasmify set shimmer magic shimmer-light flare shimmer-motion drift
  plasmify set interval 15m
  plasmify set radius default                 back to the preset's own value
  plasmify set --list                         every setting and its values

Only the parts of the desktop a change touches are applied again, after a backup.
The window shows these changes on its Customize tab; plasmify reset drops them.
"""

RESET_EPILOG = """\
examples:
  plasmify reset              drop your changes and apply the preset as it is defined
  plasmify reset --dry-run    show what would run without changing anything

Only the parts of the desktop your changes touched are applied again, after a backup.
plasmify restore puts back all the settings of an earlier backup instead.
"""


def _desktop_options(command: argparse.ArgumentParser) -> None:
    command.add_argument("-y", "--yes", action="store_true", help="do not ask before changing the desktop")
    command.add_argument("--dry-run", action="store_true", help="print what would happen without changing anything")
    command.add_argument(
        "--allow-host",
        action="append",
        metavar="HOST",
        help="approve downloads from a host outside the trusted list (repeatable)",
    )


class _Parser(argparse.ArgumentParser):
    """Help without colours: Python 3.14 colours it with escape codes, which _SafeStream would
    print as "?[1;34m". Sub-commands use this class too."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        if sys.version_info >= (3, 14):
            kwargs["color"] = False
        super().__init__(*args, **kwargs)


def build_parser() -> argparse.ArgumentParser:
    raw = argparse.RawDescriptionHelpFormatter
    parser = _Parser(
        prog="plasmify",
        description=f"{APP_NAME}: one-click, bloat-free themes for KDE Plasma 6.",
        epilog=MAIN_EPILOG,
        formatter_class=raw,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", title="commands", metavar="<command>")

    # Quick theme commands first: they are what most people type.
    theme = sub.add_parser(
        "theme",
        help="show the applied theme, apply one by name, or switch it to light or dark",
        description="Show the applied theme, apply a preset by a forgiving name, or switch the applied\n"
        "theme between light and dark like the window's button.",
        epilog=THEME_EPILOG,
        formatter_class=raw,
    )
    theme.add_argument("name", nargs="?", help="preset id, name, the start of one, or a close spelling")
    mode = theme.add_mutually_exclusive_group()
    mode.add_argument("--light", action="store_true", help="switch the applied theme to light colours")
    mode.add_argument("--dark", action="store_true", help="switch the applied theme to dark colours")
    mode.add_argument("--toggle", action="store_true", help="switch the applied theme to the other mode")
    theme.add_argument("--only", help=f"comma-separated steps to apply ({','.join(STEP_IDS)})")
    theme.add_argument("--skip", help="comma-separated steps to skip")
    theme.add_argument("--plain", action="store_true", help="leave out the changes you made to this preset")
    _desktop_options(theme)
    theme.set_defaults(func=cmd_theme)

    setting = sub.add_parser(
        "set",
        help="change settings of the applied theme right away (accent, glass, blur, fonts...)",
        description="Change settings of the applied theme. They are applied at once (only the parts\n"
        "they touch) and kept, so the window shows them as Customize changes.",
        epilog=SET_EPILOG,
        formatter_class=raw,
    )
    setting.add_argument("pairs", nargs="*", metavar="KEY VALUE", help="settings and their new values")
    setting.add_argument("--list", action="store_true", help="list every setting with its values")
    _desktop_options(setting)
    setting.set_defaults(func=cmd_set)

    reset = sub.add_parser(
        "reset",
        help="drop your changes to the applied theme and apply it as the preset defines it",
        description="Drop the changes made to the applied theme (in the window or with plasmify set)\n"
        "and apply it as the preset defines it.",
        epilog=RESET_EPILOG,
        formatter_class=raw,
    )
    _desktop_options(reset)
    reset.set_defaults(func=cmd_reset)

    sub.add_parser("gui", help="open the window").set_defaults(func=cmd_gui)
    sub.add_parser("list", help="list available presets").set_defaults(func=cmd_list)

    show = sub.add_parser("show", help="show what a preset changes and installs")
    show.add_argument("preset")
    show.set_defaults(func=cmd_show)

    apply = sub.add_parser("apply", help="apply a preset by its exact id, without your changes")
    apply.add_argument("preset")
    apply.add_argument("--only", help=f"comma-separated steps to apply ({','.join(STEP_IDS)})")
    apply.add_argument("--skip", help="comma-separated steps to skip")
    apply.add_argument("-y", "--yes", action="store_true", help="do not ask before installing packages")
    apply.add_argument("--dry-run", action="store_true", help="print what would happen without changing anything")
    apply.add_argument(
        "--allow-host",
        action="append",
        metavar="HOST",
        help="approve downloads from a host outside the trusted list (repeatable)",
    )
    apply.set_defaults(func=cmd_apply)

    restore = sub.add_parser("restore", help="list backups, or restore one")
    restore.add_argument(
        "backup", nargs="?", help="backup id, 'latest', or 'original' (your setup from before Plasmify)"
    )
    restore.add_argument("--dry-run", action="store_true")
    restore.set_defaults(func=cmd_restore)

    clean = sub.add_parser("clean", help="delete downloaded assets of presets that are not active")
    clean.add_argument("--dry-run", action="store_true")
    clean.set_defaults(func=cmd_clean)

    validate = sub.add_parser("validate", help="check preset files for errors")
    validate.add_argument("files", nargs="*", help="preset files (default: all built-in presets)")
    validate.set_defaults(func=cmd_validate)

    importing = sub.add_parser("import", help="import a .plasmify bundle (also renamed to .zip) or .toml preset")
    importing.add_argument("file")
    importing.add_argument("--replace", action="store_true", help="overwrite a preset with the same id")
    importing.set_defaults(func=cmd_import)

    export = sub.add_parser("export", help="write a preset to a shareable .plasmify file")
    export.add_argument("preset")
    export.add_argument("-o", "--output", help="output file (default: <id>.plasmify)")
    export.set_defaults(func=cmd_export)

    save = sub.add_parser("save-desktop", help="save the current desktop as a preset")
    save.add_argument("name")
    save.add_argument("-d", "--description")
    save.add_argument("--no-wallpapers", action="store_true", help="do not include your wallpaper files")
    save.add_argument("--replace", action="store_true", help="overwrite a preset with the same id")
    save.set_defaults(func=cmd_save_desktop)

    log = sub.add_parser("log", help="show what Plasmify downloaded or installed, newest first (kept on this computer)")
    log.add_argument("-n", "--limit", type=int, default=50, help="number of entries (default 50)")
    log.add_argument("--clear", action="store_true", help="delete the history")
    log.set_defaults(func=cmd_log)

    steps = sub.add_parser("steps", help="list the steps a preset is made of")
    steps.set_defaults(func=lambda _: print("\n".join(f"{s.id:<10} {s.description}" for s in ALL_STEPS)) or 0)
    return parser


class _SafeStream:
    """Wraps stdout/stderr: control characters other than new lines and tabs are shown as "?",
    so text from servers or files (git errors, HTTP reasons) cannot drive the terminal. Lone
    surrogates (bytes in arguments that were not valid text) become "?" too instead of failing."""

    def __init__(self, stream) -> None:
        self._stream = stream

    def write(self, text: str) -> int:
        return self._stream.write(_TERMINAL_UNSAFE.sub("?", text))

    def __getattr__(self, name: str):
        return getattr(self._stream, name)


_TERMINAL_UNSAFE = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f\ud800-\udfff]")


def main(argv: list[str] | None = None) -> int:
    if not isinstance(sys.stdout, _SafeStream):
        sys.stdout, sys.stderr = _SafeStream(sys.stdout), _SafeStream(sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    try:
        return args.func(args)
    except (presets.PresetError, CommandError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
