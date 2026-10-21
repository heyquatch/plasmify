"""Panel layout, built only from widgets that ship with Plasma."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from plasmify import downloads
from plasmify.downloads import image_suffix
from plasmify.panels import WIDGETS, layout_panels, pieces, widget_available
from plasmify.presets import Preset
from plasmify.steps.base import Context, Step
from plasmify.system import CONFIG_HOME, PLASMIFY_DATA, safe_child, which

DEFAULT_LAUNCHER_ICON = "start-here-kde"

# Pinned to the dock / task manager when installed, first match per group wins.
PINNED_CANDIDATES = (
    ("org.kde.dolphin.desktop",),
    ("Alacritty.desktop", "kitty.desktop", "com.mitchellh.ghostty.desktop", "org.kde.konsole.desktop"),
    ("@browser",),
    ("systemsettings.desktop", "org.kde.systemsettings.desktop"),
)
APPLICATION_DIRS = (
    Path.home() / ".local/share/applications",
    Path("/usr/local/share/applications"),
    Path("/usr/share/applications"),
    Path("/var/lib/flatpak/exports/share/applications"),
)


def desktop_file_exists(name: str) -> bool:
    return any((d / name).exists() for d in APPLICATION_DIRS)


def default_browser() -> str | None:
    if not which("xdg-settings"):
        return None
    proc = subprocess.run(["xdg-settings", "get", "default-web-browser"], capture_output=True, text=True, check=False)
    name = proc.stdout.strip()
    return name if name and desktop_file_exists(name) else None


def pinned_launchers() -> list[str]:
    pinned = []
    for group in PINNED_CANDIDATES:
        for name in group:
            if name == "@browser":
                name = default_browser() or ""
            if name and desktop_file_exists(name):
                pinned.append(f"applications:{name}")
                break
    return pinned


class LayoutStep(Step):
    id = "layout"
    title = "Panel layout"
    description = "Top bar, bottom panel or dock, built from stock Plasma widgets."

    def applies_to(self, preset: Preset) -> bool:
        return bool(preset.section("layout"))

    def touched_paths(self, preset: Preset) -> list[Path]:
        return [
            CONFIG_HOME / "plasma-org.kde.plasma.desktop-appletsrc",
            CONFIG_HOME / "plasmashellrc",
        ]

    def launcher_icon(self, ctx: Context) -> str:
        url = ctx.preset.section("layout").get("launcher_icon")
        if not url:
            return DEFAULT_LAUNCHER_ICON
        target = safe_child(PLASMIFY_DATA / "icons", f"{ctx.preset.id}{image_suffix(url)}")
        if not ctx.dry_run and not target.exists():
            try:
                downloads.fetch_image(url, target)
            except downloads.DownloadError as exc:
                ctx.warn(str(exc))
                return DEFAULT_LAUNCHER_ICON
            ctx.record(target)
        return str(target)

    def apply(self, ctx: Context) -> None:
        panels = usable_panels(ctx)
        ctx.log("Building panels: " + describe(panels))
        script = panel_script(panels, self.launcher_icon(ctx), pinned_launchers())
        if ctx.dry_run:
            ctx.runner.plasma_script(script)
            return
        tray = read_tray(ctx.runner) if any("tray" in p["widgets"] for p in panels) else {}
        # Build, then check what plasmashell really made. One retry covers a shell that was
        # still busy loading; after that the user gets a clear warning instead of silence.
        problem = None
        for _attempt in (1, 2):
            output = ctx.runner.plasma_script(script)
            problem = layout_problem(panels, current_panels(ctx.runner))
            if problem is None:
                break
            ctx.log(f"Panels are not as expected ({problem}); output: {output.strip() or 'none'}")
        if problem is not None:
            ctx.warn(f"The panels could not be built as chosen: {problem}")
        # Even panels that came out differently (or Plasma's fallback panel) may hold a new tray.
        if tray:
            restored = ctx.runner.plasma_script(tray_restore_script(tray), check=False)
            if not re.search(r"restored [1-9]", restored or ""):
                ctx.warn("Your system tray settings could not be carried over: check which items it shows")


def usable_panels(ctx: Context) -> list[dict]:
    """The preset's panels, without widgets that are not installed here."""
    result = []
    for panel in layout_panels(ctx.preset.section("layout")):
        missing = [w for w in panel["widgets"] if not widget_available(w)]
        for widget in missing:
            ctx.warn(f"The {WIDGETS[widget][1]} widget needs kdeplasma-addons; it was left out")
        widgets = [w for w in panel["widgets"] if w not in missing] or ["spacer"]
        result.append({**panel, "widgets": widgets})
    return result


HIDING = {"dodge": "dodgewindows", "autohide": "autohide", "always": "none"}
TASK_PLUGINS = [WIDGETS["tasks"][0], WIDGETS["tasks_labels"][0]]
ALIGNMENT = {"start": "left", "center": "center", "end": "right"}

# The system tray keeps which items are on, hidden or always shown in its [General] settings,
# and a new tray starts from Plasma's defaults (every item on, such as Lock Keys): these keys are
# carried from the old tray to the new ones, so the user's choices stay. Up to Plasma 6.7 the
# tray applet points to an inner containment (SystrayContainmentId) that holds them; from 6.8 the
# applet is the tray and holds them itself.
TRAY_PLUGIN = WIDGETS["tray"][0]
TRAY_LISTS = ("extraItems", "hiddenItems", "shownItems", "knownItems", "disabledStatusNotifiers")
TRAY_SWITCHES = ("showAllItems", "scaleIconsToFit", "reverseIconOrder", "pin")
TRAY_NUMBERS = ("iconSpacing",)
TRAY_KEYS = (*TRAY_LISTS, *TRAY_SWITCHES, *TRAY_NUMBERS)
# KConfig reads these words as "off"; any other text is "on".
KCONFIG_FALSE = ("false", "no", "off", "0")
_TRAY_FIND = (
    "function trayContainments() {\n"
    "    var found = [];\n"
    "    panels().forEach(function (p) { (p.widgets ? p.widgets() : []).forEach(function (w) {\n"
    f"        if (w.type !== {json.dumps(TRAY_PLUGIN)} || !w.readConfig) return;\n"
    "        w.currentConfigGroup = [];\n"
    '        var id = Number(w.readConfig("SystrayContainmentId", 0));\n'
    '        var tray = id && typeof desktopById === "function" ? desktopById(id) : null;\n'
    "        if (!id) tray = w;  // Plasma 6.8 and later: the applet is the tray\n"
    "        if (tray && tray.readConfig) found.push(tray);\n"
    "    }); });\n"
    "    return found;\n"
    "}\n"
)
# Plasma's readConfig(key, default) turns the stored text into the type of the default, and a
# null default has no type KConfig knows, so every stored key would read back as undefined. With
# a string default the text comes back as it is ("a,b", "true", "2"); _tray_value gives it its
# type again before it is written back.
TRAY_READ_SCRIPT = _TRAY_FIND + (
    "var saved = {};\n"
    "var trays = trayContainments();\n"
    "if (trays.length) {\n"
    '    trays[0].currentConfigGroup = ["General"];\n'
    f"    {json.dumps(list(TRAY_KEYS))}.forEach(function (k) {{\n"
    '        var v = trays[0].readConfig(k, "");\n'
    '        if (typeof v === "string" && v !== "") saved[k] = v;\n'
    "    });\n"
    "}\n"
    "print(JSON.stringify(saved));\n"
)


def kconfig_list(text: str) -> list[str]:
    """A list as KConfig stores it: "a,b\\,c" is ["a", "b,c"], empty text is no items."""
    if not text:
        return []
    if text == "\\0":  # KConfig's way of writing a list that holds one empty item
        return [""]
    items, item, escaped = [], [], False
    for char in text:
        if escaped:
            item.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ",":
            items.append("".join(item))
            item = []
        else:
            item.append(char)
    items.append("".join(item))
    return items


def _tray_value(key: str, value: object) -> object | None:
    """A tray setting as read from Plasma (KConfig text), in the type its key has: a list, yes/no
    or a number. None when it is not a plain value we carry. The type matters: Plasma hands a
    written value straight to the running tray, which would take "a,b" as one item."""
    if isinstance(value, str) and len(value) > 20000:
        return None
    if key in TRAY_LISTS:
        items = kconfig_list(value) if isinstance(value, str) else value
        if isinstance(items, list) and len(items) <= 200 and all(isinstance(v, str) and len(v) <= 200 for v in items):
            return items
        return None
    if key in TRAY_SWITCHES:
        if isinstance(value, bool):
            return value
        # Empty text would read as true: it is not carried.
        return value.strip().lower() not in KCONFIG_FALSE if isinstance(value, str) and value.strip() else None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 1000 else None
    return int(value) if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 4 else None


def tray_restore_script(saved: dict) -> str:
    """Plasma script that gives every system tray the settings read from the old one. Only the
    known keys with plain values are written, each through json.dumps."""
    lines = [_TRAY_FIND, "trayContainments().forEach(function (tray) {", '    tray.currentConfigGroup = ["General"];']
    for key in TRAY_KEYS:
        value = _tray_value(key, saved[key]) if key in saved else None
        if value is not None:
            lines.append(f"    tray.writeConfig({json.dumps(key)}, {json.dumps(value)});")
    lines += ["    if (tray.reloadConfig) tray.reloadConfig();", "    restored += 1;", "});"]
    lines += ['print("restored " + restored);']
    return "var restored = 0;\n" + "\n".join(lines) + "\n"


def read_tray(runner) -> dict:
    """The current system tray's settings as Plasma stores them ({} when there is no tray or
    Plasma cannot be asked)."""
    try:
        data = json.loads(runner.plasma_script(TRAY_READ_SCRIPT, mutating=False).strip() or "{}")
    except (ValueError, RuntimeError):
        return {}
    if not isinstance(data, dict):
        return {}
    saved = {k: data[k] for k in TRAY_KEYS if k in data}
    if "knownItems" in saved:
        # Plasma deletes a list once it is emptied (empty is its default), while a new tray turns
        # every default item on: a tray that knows its items but has no list had them all off.
        for key in TRAY_LISTS:
            saved.setdefault(key, "")
    return saved


PANELS_SCRIPT = (
    "print(JSON.stringify(panels().map(function (p) { return {location: p.location, "
    "height: p.height, hiding: p.hiding, lengthMode: p.lengthMode, floating: p.floating, alignment: p.alignment, "
    "widgets: p.widgets().map(function (w) { return w.type; })}; })));"
)


def current_panels(runner) -> list[dict]:
    """The panels plasmashell has right now (empty if it cannot be asked)."""
    try:
        return json.loads(runner.plasma_script(PANELS_SCRIPT, mutating=False).strip() or "[]")
    except (ValueError, RuntimeError):
        return []


def layout_problem(panels: list[dict], found: list[dict]) -> str | None:
    """Why the panels plasmashell reports (``found``) differ from ``panels``, or None."""
    expected = [piece for panel in panels for piece in pieces(panel)]
    want = sorted(p["edge"] for p in expected)
    have = sorted(p.get("location", "?") for p in found)
    if want != have:
        return f"expected panels at {', '.join(want)}, found {', '.join(have) or 'none'}"
    remaining = [list(p.get("widgets", [])) for p in found]
    locations = [p.get("location") for p in found]
    for piece in expected:
        plugins = [WIDGETS[w][0] for w in piece["widgets"]]
        match = next(
            (i for i, widgets in enumerate(remaining) if locations[i] == piece["edge"] and widgets == plugins), None
        )
        if match is None:
            same_edge = [w for i, w in enumerate(remaining) if locations[i] == piece["edge"]]
            closest = max((len(w) for w in same_edge), default=0)
            return f"the {piece['edge']} panel has {closest} of {len(plugins)} widgets"
        locations[match] = None  # each panel plasmashell made matches one piece only
    return None


def describe(panels: list[dict]) -> str:
    return ", ".join(f"{p['style']} at the {p['edge']}" for p in panels) or "no panels"


def panel_script(panels: list[dict], launcher_icon: str, launchers: list[str]) -> str:
    """Plasma script that replaces every panel with ``panels`` (see ``plasmify.panels``).

    Every value is a number, a boolean or passed through ``json.dumps``, and widget ids
    are looked up in a fixed table, so nothing from a preset can break out of the script.
    """
    lines = [
        "try {",
        # Apps pinned to the task managers now stay pinned in the new panels (the user's own
        # choice, say a browser that is not the default one); suggestions only fill an empty dock.
        "var keep = [];",
        "panels().forEach(function (p) { (p.widgets ? p.widgets() : []).forEach(function (w) {",
        f"    if ({json.dumps(TASK_PLUGINS)}.indexOf(w.type) < 0 || !w.readConfig) return;",
        '    w.currentConfigGroup = ["General"];',
        '    var found = w.readConfig("launchers", "");',
        '    (typeof found === "string" ? found.split(",") : (found || [])).forEach(function (l) {',
        "        l = String(l).trim();",
        "        if (l && keep.indexOf(l) < 0) keep.push(l);",
        "    });",
        "}); });",
        # A list, not "a,b" text: the running task manager takes written text as one launcher,
        # which matches no app and showed as a "?" icon until Plasma restarted.
        f"var launchers = keep.length ? keep : {json.dumps(launchers)};",
        "panels().forEach(function (p) { p.remove(); });",
    ]
    built = [piece for entry in panels for piece in pieces(entry)]  # islands: one Plasma panel each
    for index, panel in enumerate(built):
        name = f"panel{index}"
        dock = panel["style"] == "dock"
        is_vertical = panel["edge"] in ("left", "right")
        lines += [
            f"var {name} = new Panel;",
            f"{name}.location = {json.dumps(panel['edge'])};",
            f"{name}.height = {int(panel['thickness'])};",
            f"{name}.floating = {json.dumps(bool(panel['floating']) or dock)};",
            f"{name}.hiding = {json.dumps(HIDING[panel['hiding']])};",
        ]
        if dock or panel["length"] == "fit":
            lines.append(f'{name}.lengthMode = "fit";')
            lines.append(f"{name}.alignment = {json.dumps(ALIGNMENT[panel['align']])};")
        for widget in panel["widgets"]:
            plugin = WIDGETS[widget][0]
            lines.append(f"var w = {name}.addWidget({json.dumps(plugin)});")
            if widget == "launcher":
                lines.append('w.currentConfigGroup = ["General"];')
                lines.append(f'w.writeConfig("icon", {json.dumps(launcher_icon)});')
            elif widget in ("tasks", "tasks_labels"):
                lines.append('w.currentConfigGroup = ["General"];')
                lines.append('w.writeConfig("launchers", launchers);')
                if dock:
                    lines.append('w.writeConfig("iconSpacing", 2);')
            elif widget == "clock" and not is_vertical:
                lines.append('w.currentConfigGroup = ["Appearance"];')
                lines.append('w.writeConfig("showDate", true);')
                # 1 is BesideTime. The running clock compares the number: the name worked only after
                # the next Plasma restart, so the clock changed shape on every restart.
                lines.append('w.writeConfig("dateDisplayFormat", 1);')
    # If anything above fails after the old panels were removed, never leave the user
    # without a panel: fall back to a plain bottom panel.
    lines += [
        "} catch (error) {",
        "    print('Plasmify layout failed: ' + error);",
        "    if (panels().length === 0) {",
        "        var fallback = new Panel;",
        '        fallback.location = "bottom";',
        '        ["org.kde.plasma.kickoff", "org.kde.plasma.icontasks", "org.kde.plasma.systemtray",',
        '         "org.kde.plasma.digitalclock"].forEach(function (w) { fallback.addWidget(w); });',
        "    }",
        "}",
    ]
    return "\n".join(lines) + "\n"
