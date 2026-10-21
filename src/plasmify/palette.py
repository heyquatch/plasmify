"""Colour helpers used to derive every themed file from a preset palette."""

from __future__ import annotations

import re
from dataclasses import dataclass

HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# Keys every preset palette must define.
REQUIRED_KEYS = (
    "base",  # main window background
    "mantle",  # slightly darker background (panels, sidebars)
    "crust",  # darkest background (borders, inactive titlebars)
    "surface",  # raised surfaces (buttons, inputs)
    "overlay",  # muted foreground (separators, disabled text)
    "text",  # primary foreground
    "subtext",  # secondary foreground
    "accent",  # selection / focus colour
    "red",
    "green",
    "yellow",
    "blue",
    "magenta",
    "cyan",
    "orange",
)


def is_hex(value: str) -> bool:
    return isinstance(value, str) and bool(HEX_RE.fullmatch(value))


def to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def to_hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, round(c))) for c in rgb))


def mix(a: str, b: str, amount: float) -> str:
    """Blend ``amount`` (0..1) of colour ``b`` into colour ``a``."""
    ra, rb = to_rgb(a), to_rgb(b)
    return to_hex(tuple(x + (y - x) * amount for x, y in zip(ra, rb, strict=True)))  # type: ignore[arg-type]


def lighten(color: str, amount: float) -> str:
    return mix(color, "#ffffff", amount)


def darken(color: str, amount: float) -> str:
    return mix(color, "#000000", amount)


def relative_luminance(hex_color: str) -> float:
    def channel(c: int) -> float:
        v = c / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = to_rgb(hex_color)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast(a: str, b: str) -> float:
    """WCAG contrast ratio between two colours (1 to 21)."""
    la, lb = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def is_dark(hex_color: str) -> bool:
    """Darker than perceptual middle grey (CIE L* 50)."""
    return relative_luminance(hex_color) < 0.184


def kde_rgb(hex_color: str) -> str:
    """Format a colour the way KDE .colors files expect it: ``r,g,b``."""
    return ",".join(str(c) for c in to_rgb(hex_color))


def ansi_truecolor(hex_color: str) -> str:
    """SGR parameters for a 24-bit foreground colour (``38;2;r;g;b``)."""
    return "38;2;" + ";".join(str(c) for c in to_rgb(hex_color))


@dataclass(frozen=True)
class Palette:
    colors: dict[str, str]

    def __getattr__(self, name: str) -> str:
        try:
            return self.colors[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    @property
    def dark(self) -> bool:
        return is_dark(self.colors["base"])

    def bright(self, name: str) -> str:
        """Bright ANSI variant: an explicit ``bright_<name>`` or a lightened colour."""
        return self.colors.get(f"bright_{name}") or lighten(self.colors[name], 0.18)

    def as_template_vars(self) -> dict[str, str]:
        """Variables for :class:`string.Template` (``$base``, ``$base_rgb`` …)."""
        out: dict[str, str] = {}
        for key, value in self.colors.items():
            out[key] = value
            out[f"{key}_nohash"] = value.lstrip("#")
            out[f"{key}_rgb"] = kde_rgb(value)
            out[f"{key}_sgr"] = ansi_truecolor(value)
        for key in ("red", "green", "yellow", "blue", "magenta", "cyan"):
            out[f"bright_{key}"] = self.bright(key)
        out["bright_black"] = self.colors.get("bright_black", self.colors["overlay"])
        out["bright_white"] = self.colors.get("bright_white", lighten(self.colors["text"], 0.3))
        return out
