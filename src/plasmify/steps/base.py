"""Common interface for the individual pieces of a theme."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from plasmify.presets import Preset
from plasmify.system import Runner


@dataclass
class Context:
    preset: Preset
    runner: Runner
    workdir: Path
    log: Callable[[str], None] = print
    warnings: list[str] = field(default_factory=list)
    assets: list[Path] = field(default_factory=list)
    shared_assets: list[Path] = field(default_factory=list)

    @property
    def dry_run(self) -> bool:
        return self.runner.dry_run

    def record(self, path: Path, shared: bool = False) -> None:
        """Remember an asset so ``plasmify clean`` can remove it later.

        ``shared`` marks files every preset writes to the same place (e.g. the Konsole
        profile); those are never removed, because the active preset uses them too.
        """
        (self.shared_assets if shared else self.assets).append(path)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
        self.log(f"warning: {message}")


class Step:
    """One independently selectable part of a theme (colours, icons, wallpaper, …)."""

    id: str = ""
    title: str = ""
    description: str = ""

    def applies_to(self, preset: Preset) -> bool:
        """Whether the preset configures this step at all."""
        return True

    def packages(self, preset: Preset) -> list[str]:
        """pacman packages this step needs."""
        return []

    def touched_paths(self, preset: Preset) -> list[Path]:
        """Config files to snapshot before applying.

        Downloaded assets (themes, cursors, wallpapers) are not backed up: they do
        not change the desktop on their own, and ``plasmify clean`` removes the
        ones no longer in use.
        """
        return []

    def apply(self, ctx: Context) -> None:
        raise NotImplementedError
