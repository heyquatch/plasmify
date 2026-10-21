"""Shell prompt (Starship), smarter cd (zoxide) and a themed fastfetch greeting."""

from __future__ import annotations

import json
import re
import subprocess
import unicodedata
from pathlib import Path

from plasmify import blockfont, downloads, templates
from plasmify.downloads import image_suffix
from plasmify.generate import ensure_contrast
from plasmify.palette import ansi_truecolor, mix
from plasmify.presets import Preset
from plasmify.steps.base import Context, Step
from plasmify.system import CONFIG_HOME, PLASMIFY_DATA, safe_child

LOGO_PADDING = {"top": 1, "left": 2, "right": 3}

SHELL_RCS = {
    "fish": (CONFIG_HOME / "fish" / "config.fish", "fish"),
    "bash": (Path.home() / ".bashrc", "bash"),
    "zsh": (Path.home() / ".zshrc", "zsh"),
}


# Terminals that draw real images: the kitty graphics protocol (kitty, Konsole, Ghostty,
# WezTerm) or sixel (foot). Everything else gets the text-block (chafa) fallback.
FISH_FETCH = """function fish_greeting
    type -q fastfetch; or return
    if set -q KITTY_WINDOW_ID; or set -q KONSOLE_VERSION; or set -q GHOSTTY_RESOURCES_DIR; or test "$TERM_PROGRAM" = WezTerm
        fastfetch --logo-type kitty
    else if string match -q 'foot*' -- $TERM
        fastfetch --logo-type sixel
    else
        fastfetch
    end
end"""
POSIX_FETCH = """    if command -v fastfetch >/dev/null; then
        if [ -n "$KITTY_WINDOW_ID$KONSOLE_VERSION$GHOSTTY_RESOURCES_DIR" ] || [ "$TERM_PROGRAM" = WezTerm ]; then
            fastfetch --logo-type kitty
        elif [ "${TERM#foot}" != "$TERM" ]; then
            fastfetch --logo-type sixel
        else
            fastfetch
        fi
    fi"""


def init_snippet(shell: str, prompt: bool, zoxide: bool, greeting: bool = False, image_logo: bool = False) -> str:
    """Shell start-up lines. ``greeting`` shows the fastfetch system info when a terminal opens;
    ``image_logo`` picks a real image protocol when the terminal has one (sharp photos)."""
    lines = []
    if shell == "fish":
        lines.append("if status is-interactive")
        if prompt:
            lines.append("    type -q starship; and starship init fish | source")
        if zoxide:
            lines.append("    type -q zoxide; and zoxide init fish --cmd cd | source")
        lines.append("end")
        # Defined last, so it overrides a distribution's default greeting (CachyOS runs fastfetch).
        if greeting and image_logo:
            lines.append(FISH_FETCH)
        elif greeting:
            lines.append("function fish_greeting; type -q fastfetch; and fastfetch; end")
        else:
            lines.append("function fish_greeting; end")
    else:
        lines.append("case $- in *i*)")
        if prompt:
            lines.append(f'    command -v starship >/dev/null && eval "$(starship init {shell})"')
        if zoxide:
            lines.append(f'    command -v zoxide >/dev/null && eval "$(zoxide init {shell} --cmd cd)"')
        if greeting and image_logo:
            lines.append(POSIX_FETCH)
        elif greeting:
            lines.append("    command -v fastfetch >/dev/null && fastfetch")
        lines.append(";; esac")
    return "\n".join(lines)


TEXT_LOGO = CONFIG_HOME / "fastfetch" / "plasmify-logo.txt"
IMAGE_SIZES = {"small": (20, 10), "medium": (30, 15), "large": (44, 22)}
# How each fastfetch line looks: (icon, palette colour of its key).
MODULE_STYLE = {
    "os": ("󰣇 ", "accent"), "host": ("󰌢 ", "blue"), "kernel": (" ", "blue"), "uptime": ("󰅐 ", "magenta"),
    "packages": ("󰏖 ", "green"), "shell": (" ", "green"), "display": ("󰍹 ", "cyan"), "de": (" ", "blue"),
    "wm": (" ", "cyan"), "theme": ("󰉼 ", "magenta"), "icons": ("󰀻 ", "yellow"), "font": (" ", "orange"),
    "cursor": ("󰇀 ", "subtext"), "terminal": (" ", "green"), "terminalfont": (" ", "orange"),
    "cpu": (" ", "yellow"), "gpu": ("󰢮 ", "orange"), "memory": (" ", "red"), "swap": ("󰓡 ", "red"),
    "disk": ("󰋊 ", "cyan"), "localip": ("󰩟 ", "blue"), "battery": ("󰁹 ", "green"), "locale": (" ", "subtext"),
    "media": ("󰝚 ", "magenta"), "weather": ("󰖐 ", "cyan"), "datetime": ("󰃭 ", "accent"),
}  # fmt: skip
DEFAULT_MODULES = ["os", "kernel", "wm", "shell", "cpu", "gpu", "memory", "uptime"]


def fastfetch_modules(shell: dict, palette, show_user_host: bool) -> list:
    """The greeting's lines: the user's own order, or Plasmify's default set."""
    chosen = list(shell.get("fastfetch_modules") or [])
    if not chosen:
        chosen = ["break", *DEFAULT_MODULES, "break", "colors"]
    if show_user_host and "title" not in chosen:
        start = 1 if chosen[0] == "break" else 0
        chosen[start:start] = ["title", "separator"]
    elif not show_user_host and "title" in chosen:
        # user@host only ever shows when asked for, even if a preset lists the line (or twice).
        kept, previous = [], ""
        for name in chosen:
            if not (name == "title" or (name == "separator" and previous == "title")):
                kept.append(name)
            previous = name
        chosen = kept
    modules: list = []
    for name in chosen:
        if name in ("break",):
            modules.append("break")
        elif name == "title":
            modules.append(
                {
                    "type": "title",
                    "color": {
                        "user": ansi_truecolor(palette.accent),
                        "at": ansi_truecolor(palette.subtext),
                        "host": ansi_truecolor(palette.blue),
                    },
                }
            )
        elif name == "separator":
            modules.append({"type": "separator", "string": "─", "outputColor": ansi_truecolor(palette.overlay)})
        elif name == "colors":
            modules.append({"type": "colors", "symbol": "circle"})
        else:
            icon, colour = MODULE_STYLE.get(name, ("• ", "accent"))
            modules.append(
                {"type": name, "key": icon, "keyColor": ansi_truecolor(palette.colors.get(colour, palette.accent))}
            )
    return modules


def logo_kind(shell: dict) -> str:
    """Which logo the greeting shows; older presets only have ``fastfetch_logo``."""
    if "logo" in shell:
        return shell["logo"]
    return "image" if shell.get("fastfetch_logo", "distro") != "distro" else "distro"


def builtin_logos() -> set[str]:
    """Logo names this fastfetch knows (empty if fastfetch is missing)."""
    try:
        proc = subprocess.run(["fastfetch", "--list-logos"], capture_output=True, text=True, check=False, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return set(re.findall(r'"([^"]+)"', proc.stdout))


# Colour stops of the multi-colour logo schemes: palette colour names or fixed colours, all
# bright enough to read on dark terminals (pastel and neon are meant to glow).
SCHEME_STOPS = {
    "gradient": ("accent", "magenta", "blue", "cyan", "green"),
    "rainbow": ("red", "orange", "yellow", "green", "cyan", "blue", "magenta"),
    "mono": ("text", "subtext", "overlay"),
    "sunset": ("#ff4e50", "#fc6c45", "#f9913a", "#f9d423"),
    "ocean": ("#00c6ff", "#0099e5", "#0072ff", "#4a47d5"),
    "forest": ("#a8e063", "#56ab2f", "#2e8b57", "#1d976c"),
    "fire": ("#f9d423", "#ff9a00", "#ff4e00", "#e52d27"),
    "candy": ("#ff6fd8", "#ff8ba7", "#c86dd7", "#7f7fd5"),
    "pastel": ("#ffb3ba", "#ffdfba", "#ffffba", "#baffc9", "#bae1ff"),
    "neon": ("#ff00e6", "#b300ff", "#00e5ff", "#00ff9d"),
    "aurora": ("#00ffa3", "#00d4ff", "#7b61ff", "#d65bff"),
}
MAX_LOGO_COLOURS = 9  # fastfetch reads $1..$9


def logo_stops(palette, colour: str) -> list[str]:
    """The colours a logo choice blends (one colour for the single-colour choices). The fixed
    schemes are made for dark terminals; on a light palette they are darkened until readable."""
    colours = palette.colors
    stops = SCHEME_STOPS.get(colour)
    if stops is None:
        return [colours.get(colour, colours["accent"])]
    result = []
    for stop in stops:
        if not stop.startswith("#"):
            result.append(colours.get(stop, colours["accent"]))
        else:
            result.append(stop if palette.dark else ensure_contrast(stop, colours["base"], 3.0))
    return result


def cell_width(char: str) -> int:
    """Terminal columns a character takes: 0 for combining marks and joiners, 2 for wide ones."""
    code = ord(char)
    category = unicodedata.category(char)
    if category == "Cf":  # format characters are invisible, except the few terminals draw (wcwidth)
        visible = code == 0xAD or 0x600 <= code <= 0x605 or code in (0x6DD, 0x70F, 0x890, 0x891, 0x8E2)
        visible = visible or code in (0x110BD, 0x110CD) or 0xFFF9 <= code <= 0xFFFB or 0x13430 <= code <= 0x1343F
        return 1 if visible else 0
    if unicodedata.combining(char) or category in ("Mn", "Me") or 0xFE00 <= code <= 0xFE0F:
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def ramp(stops: list[str], count: int) -> list[str]:
    """``count`` colours spread evenly along the stops."""
    if count <= 1 or len(stops) == 1:
        return stops[:1] * max(count, 1)
    result = []
    for index in range(count):
        position = index / (count - 1) * (len(stops) - 1)
        low = min(int(position), len(stops) - 2)
        result.append(mix(stops[low], stops[low + 1], position - low))
    return result


def coloured_lines(
    rows: list[str], palette, colour: str = "gradient", direction: str = "down"
) -> tuple[list[str], dict[str, str]]:
    """Logo rows with fastfetch colour codes ($1..$9): one colour, or a scheme blended in
    ``direction`` (down, up, right, left or diagonal). A literal $ is written as $$."""
    if colour == "original":
        colour = "gradient"
    stops = logo_stops(palette, colour)
    if len(stops) == 1:
        return [f"$1{row.replace('$', '$$')}" for row in rows], {"1": ansi_truecolor(stops[0])}
    height = max(len(rows), 1)
    width = max((sum(cell_width(c) for c in row) for row in rows), default=1) or 1
    vertical = direction in ("down", "up")
    span = height if vertical else width if direction in ("right", "left") else height + width - 1
    count = max(2, min(MAX_LOGO_COLOURS, span))
    shades = ramp(stops, count)

    def slot(row: int, column: int) -> int:
        down, across = row / max(height - 1, 1), column / max(width - 1, 1)
        share = {"down": down, "up": 1 - down, "right": across, "left": 1 - across}.get(direction)
        return round((share if share is not None else (down + across) / 2) * (count - 1)) + 1

    used: set[int] = set()
    lines = []
    for index, row in enumerate(rows):
        if vertical:
            number = slot(index, 0)
            used.add(number)
            lines.append(f"${number}{row.replace('$', '$$')}")
            continue
        parts: list[str] = []
        current = column = 0
        for char in row:
            cells = cell_width(char)
            number = slot(index, column)
            # A combining mark or joiner stays with the character before it: no code between.
            if cells and number != current and (char != " " or not current):
                current = number
                used.add(number)
                parts.append(f"${number}")
            parts.append("$$" if char == "$" else char)
            column += cells
        lines.append("".join(parts))
    return lines, {str(n): ansi_truecolor(shades[n - 1]) for n in sorted(used)}


def text_logo_lines(
    text: str, palette, size: str, colour: str = "gradient", direction: str = "down"
) -> tuple[list[str], dict[str, str]]:
    """The user's text in big block letters."""
    return coloured_lines(blockfont.render(text, 2 if size == "large" else 1), palette, colour, direction)


_FASTFETCH_LOGOS: dict[str, list[str]] = {}


def fastfetch_logo_rows(name: str | None, refresh: bool = False) -> list[str]:
    """A fastfetch logo as plain text (None: the distribution's). Cached, failures too, so the
    preview never waits on fastfetch twice; ``refresh`` (the apply) asks again, since fastfetch
    may have just been installed. ``--config none``: the user's config can hide ``-l``."""
    key = name or ""
    if refresh and not _FASTFETCH_LOGOS.get(key):
        _FASTFETCH_LOGOS.pop(key, None)
    if key not in _FASTFETCH_LOGOS:
        command = ["fastfetch", "--config", "none", "--pipe", "-s", "none"] + (["-l", name] if name else [])
        try:
            proc = subprocess.run(command, capture_output=True, text=True, timeout=3, check=False)  # noqa: S603
            rows = [r.rstrip() for r in proc.stdout.splitlines()]
        except (OSError, subprocess.TimeoutExpired):
            rows = []
        while rows and not rows[-1].strip():
            rows.pop()
        while rows and not rows[0].strip():
            rows.pop(0)
        _FASTFETCH_LOGOS[key] = rows[:30]
    return _FASTFETCH_LOGOS[key]


class ShellStep(Step):
    id = "shell"
    title = "Shell prompt"
    description = "Starship prompt, zoxide and an optional system-info greeting (fish, bash, zsh)."

    def applies_to(self, preset: Preset) -> bool:
        return bool(preset.section("shell"))

    def _options(self, preset: Preset) -> tuple[str, bool, str]:
        shell = preset.section("shell")
        return shell.get("prompt", "powerline"), bool(shell.get("zoxide", True)), logo_kind(shell)

    def packages(self, preset: Preset) -> list[str]:
        prompt, zoxide, logo = self._options(preset)
        pkgs = ["fastfetch"]
        if prompt != "none":
            # A small symbols-only Nerd Font so prompt icons render with any font.
            pkgs += ["starship", "ttf-nerd-fonts-symbols"]
        if zoxide:
            pkgs.append("zoxide")
        if logo == "image":
            pkgs.append("chafa")
        return pkgs

    def touched_paths(self, preset: Preset) -> list[Path]:
        paths = [CONFIG_HOME / "starship.toml", CONFIG_HOME / "fastfetch" / "config.jsonc", TEXT_LOGO]
        return paths + [rc for rc, _ in SHELL_RCS.values()]

    def _logo(self, ctx: Context) -> dict:
        shell = ctx.preset.section("shell")
        kind = logo_kind(shell)
        size = shell.get("logo_size", "medium")
        padding = LOGO_PADDING

        direction = shell.get("logo_direction", "down")
        name = None
        if kind == "builtin":
            name = shell["logo_builtin"]
            if not (ctx.dry_run or name in builtin_logos()):
                ctx.warn(f"fastfetch has no logo called '{name}'; showing the distribution logo")
                kind, name = "distro", None

        if kind in ("distro", "builtin"):
            colour = shell.get("logo_colour", "original")
            # Recoloured: fastfetch's own drawing, written out with the chosen colours.
            rows = fastfetch_logo_rows(name, refresh=True) if colour != "original" and not ctx.dry_run else []
            if rows:
                return self._text_logo(ctx, *coloured_lines(rows, ctx.preset.palette, colour, direction))
            if colour != "original" and not ctx.dry_run:
                ctx.warn("fastfetch did not hand out its logo, so it keeps its own colours")
            if kind == "builtin":
                return {"type": "builtin", "source": name, "padding": padding}
            return {"type": "auto", "padding": padding}

        if kind in ("text", "ascii"):
            colour = shell.get("logo_colour", "gradient")
            if kind == "text":
                lines, colours = text_logo_lines(shell["logo_text"], ctx.preset.palette, size, colour, direction)
            else:
                rows = shell["logo_ascii"].split("\n")
                lines, colours = coloured_lines(rows, ctx.preset.palette, colour, direction)
            return self._text_logo(ctx, lines, colours)

        if kind == "image":
            image = self._logo_image(ctx, shell)
            if image is not None:
                width, height = IMAGE_SIZES[size]
                # chafa is the fallback for terminals without images; the greeting asks
                # for the kitty or sixel protocol where the terminal supports one.
                return {
                    "type": "chafa",
                    "source": str(image),
                    "width": width,
                    "height": height,
                    "padding": padding,
                    "preserveAspectRatio": True,
                    "chafa": {"symbols": "sextant+block+border+space"},
                }
        return {"type": "auto", "padding": padding}

    def _text_logo(self, ctx: Context, lines: list[str], colours: dict[str, str]) -> dict:
        if not ctx.dry_run:
            TEXT_LOGO.parent.mkdir(parents=True, exist_ok=True)
            TEXT_LOGO.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {"type": "file", "source": str(TEXT_LOGO), "color": colours, "padding": LOGO_PADDING}

    def _logo_image(self, ctx: Context, shell: dict) -> Path | None:
        if "logo_file" in shell:
            if ctx.preset.asset_dir is None:
                ctx.warn("A logo file needs a saved or imported preset; showing the distribution logo")
                return None
            source = safe_child(ctx.preset.asset_dir, shell["logo_file"])
            if not ctx.dry_run and (not source.is_file() or not downloads.is_image(source)):
                ctx.warn(f"{shell['logo_file']} is missing or not an image; showing the distribution logo")
                return None
            return source
        url = shell.get("fastfetch_logo", "distro")
        if url == "distro":
            return None
        image = safe_child(PLASMIFY_DATA / "logos", f"{ctx.preset.id}{image_suffix(url)}")
        if not ctx.dry_run and not image.exists():
            try:
                downloads.fetch_image(url, image)
                ctx.record(image)
            except downloads.DownloadError as exc:
                ctx.warn(str(exc))
                return None
        return image

    def apply(self, ctx: Context) -> None:
        prompt, zoxide, logo = self._options(ctx.preset)
        greeting = bool(ctx.preset.section("shell").get("greeting", True))
        variables = dict(ctx.preset.palette.as_template_vars())

        if prompt != "none":
            ctx.log(f"Writing the {prompt} Starship prompt")
            if not ctx.dry_run:
                (CONFIG_HOME / "starship.toml").write_text(templates.render(f"starship-{prompt}.toml", variables))

        ctx.log("Writing the fastfetch greeting")
        logo = self._logo(ctx)
        image_logo = logo.get("type") == "chafa"
        variables["logo"] = json.dumps(logo)
        # user@host stays hidden unless the preset opts in, so shared screenshots
        # of a themed terminal don't leak the user name and machine name.
        show_user_host = bool(ctx.preset.section("shell").get("show_user_host", False))
        modules = fastfetch_modules(ctx.preset.section("shell"), ctx.preset.palette, show_user_host)
        variables["modules"] = json.dumps(modules, indent=4, ensure_ascii=False)
        if not ctx.dry_run:
            path = CONFIG_HOME / "fastfetch" / "config.jsonc"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(templates.render("fastfetch.jsonc", variables))

        for shell, (rc, _) in SHELL_RCS.items():
            # Only touch shells the user actually has a config for (fish: or uses).
            if not rc.exists() and not (shell == "fish" and rc.parent.exists()):
                continue
            ctx.log(f"Hooking the prompt into {shell}")
            if not ctx.dry_run:
                templates.upsert_block(rc, init_snippet(shell, prompt != "none", zoxide, greeting, image_logo))
