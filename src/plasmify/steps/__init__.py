"""All theme steps, in the order they are applied."""

from plasmify.steps.base import Context, Step
from plasmify.steps.colors import ColorsStep
from plasmify.steps.cursor import CursorStep
from plasmify.steps.effects import EffectsStep
from plasmify.steps.fonts import FontsStep
from plasmify.steps.icons import IconsStep
from plasmify.steps.layout import LayoutStep
from plasmify.steps.login import LoginStep
from plasmify.steps.panelstyle import PanelStyleStep
from plasmify.steps.shell import ShellStep
from plasmify.steps.splash import SplashStep
from plasmify.steps.style import StyleStep
from plasmify.steps.terminal import TerminalStep
from plasmify.steps.wallpaper import WallpaperStep

ALL_STEPS: tuple[Step, ...] = (
    ColorsStep(),
    StyleStep(),
    IconsStep(),
    CursorStep(),
    FontsStep(),
    LayoutStep(),
    PanelStyleStep(),
    WallpaperStep(),
    EffectsStep(),
    TerminalStep(),
    ShellStep(),
    SplashStep(),
    LoginStep(),
)
STEP_IDS = tuple(step.id for step in ALL_STEPS)


def by_id(step_id: str) -> Step:
    for step in ALL_STEPS:
        if step.id == step_id:
            return step
    raise KeyError(step_id)


__all__ = ["ALL_STEPS", "STEP_IDS", "Context", "Step", "by_id"]
