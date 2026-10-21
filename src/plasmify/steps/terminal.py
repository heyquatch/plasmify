"""Colours and fonts for the terminals that are already installed.

Plasmify never installs a terminal emulator; it themes Konsole, Alacritty and
kitty when they are present.
"""

from __future__ import annotations

import os
import pwd
import re
from pathlib import Path

from plasmify import templates
from plasmify.palette import kde_rgb, to_rgb
from plasmify.presets import Preset
from plasmify.steps.base import Context, Step
from plasmify.system import CONFIG_HOME, DATA_HOME, which

KONSOLE_PROFILE = "Plasmify.profile"


def login_shell() -> str | None:
    """The user's login shell from the passwd database, if it is a valid, listed shell.

    Terminals normally start ``$SHELL``, but that variable goes stale: after ``chsh`` it keeps
    the old value until the whole user session (including systemd --user) restarts. Starting
    the login shell explicitly makes the terminal follow ``chsh`` immediately.
    """
    try:
        shell = pwd.getpwuid(os.getuid()).pw_shell
        listed = Path("/etc/shells").read_text().split()
    except (KeyError, OSError):
        return None
    if shell in listed and os.access(shell, os.X_OK) and re.fullmatch(r"/[A-Za-z0-9/._+-]+", shell):
        return shell
    return None


def terminal_vars(preset: Preset) -> dict[str, object]:
    terminal = preset.section("terminal")
    fonts = preset.section("fonts")
    variables: dict[str, object] = dict(preset.palette.as_template_vars())
    # On light palettes ANSI "black" must be the dark colour and "white" the light one.
    p = preset.palette
    dark = sum(to_rgb(p.base)) < 3 * 128
    ansi_black, ansi_white = (p.surface, p.subtext) if dark else (p.subtext, p.surface)
    variables.update(
        ansi_black=ansi_black,
        ansi_white=ansi_white,
        ansi_black_rgb=kde_rgb(ansi_black),
        ansi_white_rgb=kde_rgb(ansi_white),
        ansi_bright_black=variables["bright_black"] if dark else p.text,
        ansi_bright_white=p.text if dark else p.base,
    )
    variables.update(
        name=preset.name,
        font=fonts.get("mono", "monospace"),
        font_size=terminal.get("font_size", 12),
        opacity=terminal.get("opacity", 0.9),
        blur="true" if terminal.get("blur", True) else "false",
        padding=terminal.get("padding", 12),
        konsole_opacity=round(float(terminal.get("opacity", 0.9)), 2),
    )
    shell = login_shell()
    variables["konsole_command"] = f"Command={shell}\n" if shell else ""
    variables["alacritty_shell"] = f'\n[terminal]\nshell = {{ program = "{shell}" }}\n' if shell else ""
    return variables


class TerminalStep(Step):
    id = "terminal"
    title = "Terminal"
    description = "Palette, font and translucency for Konsole, Alacritty and kitty (whichever are installed)."

    def applies_to(self, preset: Preset) -> bool:
        return bool(preset.section("terminal"))

    def touched_paths(self, preset: Preset) -> list[Path]:
        return [
            CONFIG_HOME / "konsolerc",
            # The generated Konsole profile and colours too, so Restore really brings back the
            # terminal as it was (it used to keep the last preset's colours).
            DATA_HOME / "konsole" / "Plasmify.colorscheme",
            DATA_HOME / "konsole" / KONSOLE_PROFILE,
            CONFIG_HOME / "alacritty" / "alacritty.toml",
            CONFIG_HOME / "kitty" / "kitty.conf",
            CONFIG_HOME / "kitty" / "plasmify-theme.conf",
        ]

    def apply(self, ctx: Context) -> None:
        variables = terminal_vars(ctx.preset)
        themed = []

        if which("konsole"):
            themed.append("Konsole")
            if not ctx.dry_run:
                konsole = DATA_HOME / "konsole"
                konsole.mkdir(parents=True, exist_ok=True)
                (konsole / "Plasmify.colorscheme").write_text(templates.render("konsole.colorscheme", variables))
                (konsole / KONSOLE_PROFILE).write_text(templates.render("konsole.profile", variables))
                ctx.record(konsole / "Plasmify.colorscheme", shared=True)
                ctx.record(konsole / KONSOLE_PROFILE, shared=True)
            ctx.runner.kwrite("konsolerc", "Desktop Entry", "DefaultProfile", KONSOLE_PROFILE)

        if which("alacritty"):
            themed.append("Alacritty")
            if not ctx.dry_run:
                path = CONFIG_HOME / "alacritty" / "alacritty.toml"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(templates.render("alacritty.toml", variables))

        if which("kitty"):
            themed.append("kitty")
            if not ctx.dry_run:
                kitty = CONFIG_HOME / "kitty"
                kitty.mkdir(parents=True, exist_ok=True)
                (kitty / "plasmify-theme.conf").write_text(templates.render("kitty.conf", variables))
                ctx.record(kitty / "plasmify-theme.conf", shared=True)
                templates.upsert_block(kitty / "kitty.conf", "include plasmify-theme.conf")

        ctx.log("Themed " + (", ".join(themed) if themed else "no terminal (none installed)"))
