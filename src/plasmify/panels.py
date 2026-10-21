"""Panels as data: any number of bars and docks on any screen edge, with any widgets.

A preset can describe its panels in two ways:

* the short form (``name``, ``top_bar``, ``bottom`` and friends), which every built-in
  preset uses and which :func:`layout_panels` expands, or
* ``panels``: a list of panel tables, which the Customize tab writes.

A panel is a full bar, a dock, or islands: separate floating pieces on one edge. For islands
the flexible spaces in the widget list mark where one piece ends and the next begins (at most
three pieces: start, centre and end of the edge); see :func:`pieces`.

Only widgets that ship with Plasma are offered. The ones from kdeplasma-addons are
used when installed and skipped otherwise, so nothing extra is ever installed.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

EDGES = ("top", "bottom", "left", "right")
STYLES = ("bar", "dock", "islands")
MAX_ISLANDS = 3  # one at the start, the centre and the end of an edge
LENGTHS = ("fill", "fit")
ALIGNS = ("start", "center", "end")
HIDINGS = ("always", "dodge", "autohide")
MAX_PANELS = 4
MAX_WIDGETS = 24
THICKNESS = (20, 128)

# id -> (plugin, label, description, from kdeplasma-addons)
WIDGETS: dict[str, tuple[str, str, str, bool]] = {
    "launcher": ("org.kde.plasma.kickoff", "App launcher", "The start menu with search and favourites.", False),
    "menu": ("org.kde.plasma.kicker", "Classic menu", "A compact cascading application menu.", False),
    "dashboard": ("org.kde.plasma.kickerdash", "App grid", "A full-screen grid of all applications.", True),
    "tasks": ("org.kde.plasma.icontasks", "Apps (icons)", "Pinned and open apps as icons.", False),
    "tasks_labels": ("org.kde.plasma.taskmanager", "Apps with titles", "Open apps with their window titles.", False),
    "windowlist": ("org.kde.plasma.windowlist", "Window list", "A menu of every open window.", False),
    "pager": ("org.kde.plasma.pager", "Desktops", "Switch between virtual desktops.", False),
    "spacer": ("org.kde.plasma.panelspacer", "Flexible space", "Pushes the widgets around it apart.", False),
    "separator": ("org.kde.plasma.marginsseparator", "Small gap", "A small fixed gap.", False),
    "clock": ("org.kde.plasma.digitalclock", "Clock", "Time and date, with a calendar on click.", False),
    "analog_clock": ("org.kde.plasma.analogclock", "Analog clock", "A small clock face.", False),
    "tray": ("org.kde.plasma.systemtray", "System tray", "Network, sound, battery and notifications.", False),
    "media": ("org.kde.plasma.mediacontroller", "Now playing", "Shows and controls the music or video.", False),
    "cpu": ("org.kde.plasma.systemmonitor.cpu", "CPU graph", "A live CPU usage graph.", False),
    "memory": ("org.kde.plasma.systemmonitor.memory", "Memory graph", "A live memory usage graph.", False),
    "network": ("org.kde.plasma.systemmonitor.net", "Network graph", "Live download and upload speed.", False),
    "showdesktop": ("org.kde.plasma.showdesktop", "Show desktop", "Moves every window out of the way.", False),
    "trash": ("org.kde.plasma.trash", "Trash", "Drop files on it to delete them.", False),
    "logout": ("org.kde.plasma.lock_logout", "Lock and log out", "Lock, log out or shut down.", False),
    "weather": ("org.kde.plasma.weather", "Weather", "The forecast for a place you choose.", True),
    "colorpicker": ("org.kde.plasma.colorpicker", "Colour picker", "Pick a colour from anywhere.", True),
}

APPLET_DIRS = (
    "/usr/lib/qt6/plugins/plasma/applets",
    "/usr/lib64/qt6/plugins/plasma/applets",
    "/usr/lib/x86_64-linux-gnu/qt6/plugins/plasma/applets",
    "/usr/lib/aarch64-linux-gnu/qt6/plugins/plasma/applets",
)
PLASMOID_DIRS = (Path.home() / ".local/share/plasma/plasmoids", Path("/usr/share/plasma/plasmoids"))

PANEL_DEFAULTS: dict[str, Any] = {
    "style": "bar",
    "length": "fill",
    "align": "center",
    "thickness": 36,
    "floating": True,
    "hiding": "always",
}
DOCK_DEFAULTS: dict[str, Any] = {"length": "fit", "thickness": 56, "hiding": "dodge"}


def widget_available(widget: str) -> bool:
    """True when the widget's plugin is installed (or the plugin folders are unknown here)."""
    plugin, _label, _desc, addon = WIDGETS[widget]
    if not addon:
        return True
    applet_dirs = [Path(d) for d in APPLET_DIRS] + [
        Path(p) / "plasma/applets" for p in os.environ.get("QT_PLUGIN_PATH", "").split(":") if p
    ]
    known = [d for d in applet_dirs if d.is_dir()]
    if not known:
        return True
    return any((d / f"{plugin}.so").exists() for d in known) or any((d / plugin).is_dir() for d in PLASMOID_DIRS)


def normalise(panel: dict[str, Any]) -> dict[str, Any]:
    """A panel table with every key filled in."""
    result = dict(PANEL_DEFAULTS)
    if panel.get("style") == "dock":
        result.update(DOCK_DEFAULTS)
    result.update(panel)
    result["widgets"] = list(panel.get("widgets", []))
    return result


def pieces(panel: dict[str, Any]) -> list[dict[str, Any]]:
    """The Plasma panels one panel table becomes: itself, or for islands one fitted panel per
    group of widgets between flexible spaces. One group sits in the centre; two at the start
    and the end; three at the start, the centre and the end. An empty group leaves its place
    free, so ``["spacer", "clock", "spacer", "tray"]`` puts the clock in the centre and the
    tray at the end."""
    if panel.get("style") != "islands":
        return [panel]
    groups: list[list[str]] = [[]]
    for widget in panel["widgets"]:
        if widget == "spacer":
            groups.append([])
        else:
            groups[-1].append(widget)
    groups = groups[:MAX_ISLANDS]
    places = {1: ("center",), 2: ("start", "end"), 3: ("start", "center", "end")}[len(groups)]
    return [
        {**panel, "style": "bar", "length": "fit", "align": place, "widgets": group}
        for group, place in zip(groups, places, strict=True)
        if group
    ]


def vertical(panel: dict[str, Any]) -> bool:
    return panel["edge"] in ("left", "right")


def layout_panels(layout: dict[str, Any]) -> list[dict[str, Any]]:
    """Every panel a layout table describes, in the long form."""
    if layout.get("panels"):
        return [normalise(p) for p in layout["panels"]]
    from plasmify.schema import layout_parts  # local import: schema imports this module

    parts = layout_parts(layout)
    top, bottom = parts["top_bar"], parts["bottom"]
    tasks = "tasks_labels" if parts["task_labels"] else "tasks"
    panels: list[dict[str, Any]] = []
    if top:
        widgets = ["launcher"] + (["pager"] if parts["pager"] else []) + ([tasks] if bottom == "none" else [])
        if parts["clock"] == "center":
            widgets += ["spacer", "clock", "spacer", "tray"]
        else:
            widgets += ["spacer", "tray", "clock"]
        panels.append(
            normalise(
                {
                    "edge": "top",
                    "thickness": parts["bar_height"],
                    "floating": parts["floating"],
                    "widgets": widgets,
                }
            )
        )
    if bottom == "panel":
        widgets = [tasks] if top else ["launcher"] + (["pager"] if parts["pager"] else []) + [tasks]
        if not top:
            widgets += ["separator", "tray", "clock"]
        panels.append(
            normalise(
                {
                    "edge": "bottom",
                    "thickness": parts["bar_height"] + 12,
                    "floating": parts["floating"],
                    "widgets": widgets,
                }
            )
        )
    elif bottom == "dock":
        widgets = [tasks] if top else ["launcher", tasks, "separator", "tray", "clock"]
        panels.append(
            normalise(
                {
                    "edge": "bottom",
                    "style": "dock",
                    "thickness": parts["dock_height"],
                    "floating": True,
                    "hiding": parts["dock_hiding"],
                    "widgets": widgets,
                }
            )
        )
    return panels


# Starting points offered as picture tiles. Users change anything afterwards.
STARTERS: dict[str, tuple[str, list[dict[str, Any]]]] = {
    "topbar-dock": (
        "A slim bar at the top and a floating dock, like macOS.",
        [
            {"edge": "top", "thickness": 32, "widgets": ["launcher", "pager", "spacer", "clock", "spacer", "tray"]},
            {"edge": "bottom", "style": "dock", "widgets": ["tasks"]},
        ],
    ),
    "bottom-panel": (
        "One classic panel at the bottom, like Windows.",
        [{"edge": "bottom", "thickness": 44, "widgets": ["launcher", "pager", "tasks", "separator", "tray", "clock"]}],
    ),
    "top-panel": (
        "Everything in one slim top bar. The most room for windows and tiling.",
        [{"edge": "top", "thickness": 32, "widgets": ["launcher", "pager", "tasks", "spacer", "tray", "clock"]}],
    ),
    "top-bottom": (
        "A top bar with the clock and a task bar at the bottom.",
        [
            {"edge": "top", "thickness": 30, "widgets": ["launcher", "pager", "spacer", "clock", "spacer", "tray"]},
            {"edge": "bottom", "thickness": 40, "widgets": ["tasks_labels"]},
        ],
    ),
    "left-dock": (
        "A top bar and a dock on the left, like Ubuntu.",
        [
            {"edge": "top", "thickness": 30, "widgets": ["spacer", "clock", "spacer", "tray"]},
            {"edge": "left", "style": "dock", "hiding": "always", "widgets": ["launcher", "tasks"]},
        ],
    ),
    "side-bar": (
        "One vertical bar on the left. Great on wide screens.",
        [{"edge": "left", "thickness": 52, "widgets": ["launcher", "pager", "tasks", "spacer", "tray", "clock"]}],
    ),
    "islands": (
        "Separate islands at the top: menu on the left, clock in the middle, tray on the right.",
        [
            {
                "edge": "top",
                "style": "islands",
                "thickness": 34,
                "widgets": ["launcher", "pager", "spacer", "clock", "spacer", "tray"],
            },
            {"edge": "bottom", "style": "dock", "widgets": ["tasks"]},
        ],
    ),
    "dock-only": (
        "Just a floating dock with everything in it. Minimal and clean.",
        [{"edge": "bottom", "style": "dock", "widgets": ["launcher", "tasks", "separator", "tray", "clock"]}],
    ),
}


def starter(name: str) -> list[dict[str, Any]]:
    return [normalise(p) for p in STARTERS[name][1]]


def compact(panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Panels as they are saved in a preset: keys equal to the defaults are left out."""
    result = []
    for panel in panels:
        defaults = dict(PANEL_DEFAULTS)
        if panel.get("style") == "dock":
            defaults.update(DOCK_DEFAULTS)
        result.append({k: v for k, v in panel.items() if k in ("edge", "widgets") or defaults.get(k) != v})
    return result


def min_thickness(panels: list[dict[str, Any]]) -> int:
    return min((int(p["thickness"]) for p in panels), default=36)


def problems(panels: Any) -> list[str]:
    """Rules a list of panels must follow beyond the type checks in the schema."""
    if not isinstance(panels, list):
        return []
    errors = []
    edges = [p.get("edge") for p in panels if isinstance(p, dict)]
    if not panels:
        errors.append("layout.panels needs at least one panel")
    if len(set(edges)) != len(edges):
        errors.append("layout.panels can have only one panel per screen edge")
    for index, panel in enumerate(panels):
        if isinstance(panel, dict) and not panel.get("widgets"):
            errors.append(f"layout.panels[{index}] needs at least one widget")
        if isinstance(panel, dict) and panel.get("style") == "islands" and isinstance(panel.get("widgets"), list):
            widgets = panel["widgets"]
            if widgets.count("spacer") > MAX_ISLANDS - 1:
                errors.append(f"layout.panels[{index}]: islands are split by at most two flexible spaces")
            if all(w == "spacer" for w in widgets):
                errors.append(f"layout.panels[{index}]: islands need at least one widget besides the spaces")
    return errors
