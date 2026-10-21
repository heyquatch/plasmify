"""Glass: translucent Kvantum themes, the palette-coloured template and what glass asks of other steps."""

import copy
import hashlib
import json
import math
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

from plasmify import downloads, engine, generate, glass, presets, schema
from plasmify.palette import Palette
from plasmify.presets import Preset
from plasmify.steps import style
from plasmify.steps.base import Context
from plasmify.system import UnsafePathError

# A Kvantum theme shaped like the ones presets ship: comments, Kvantum's [%General], keys of the
# same name in other sections, and a commented-out key that must stay a comment.
THEME = """[%General]
author=Someone, based on Arc
comment=A dark theme
composite=true
translucent_windows=false
blurring=false
popup_blurring=true
# translucent_windows=false (an old setting, kept for reference)
opaque=kaffeine,VLC,kdenlive
reduce_window_opacity=0
reduce_menu_opacity=0
menu_blur_radius=0
tooltip_blur_radius=5

[GeneralColors]
window.color=#1E1E2E
translucent_windows=false

[Hacks]
transparent_dolphin_view=false
blur_konsole=true

; the end
"""

GLASS_KEYS = {
    "composite",
    "translucent_windows",
    "blurring",
    "popup_blurring",
    "reduce_window_opacity",
    "reduce_menu_opacity",
    "opaque",
    "menu_blur_radius",
    "tooltip_blur_radius",
    "transparent_dolphin_view",
    "no_inactiveness",
}

LIGHT = {
    "base": "#f3f2f2", "mantle": "#e9e7e7", "crust": "#fbfbfb", "surface": "#dfdddd", "overlay": "#8f8a8a",
    "text": "#2e292a", "subtext": "#615758", "red": "#d20415", "green": "#039d10", "yellow": "#ac8303",
    "blue": "#045ad2", "magenta": "#d204d2", "cyan": "#0398a5", "orange": "#d25a04", "accent": "#c61026",
}  # fmt: skip
GREYS = ("#000000", "#bebebe", "#999999")


def template_files(column: int) -> tuple[str, str]:
    """A stand-in for the pinned Catppuccin files (0 = mocha, 1 = latte): every colour the
    real ones use, in upper and lower case, with an alpha suffix, next to neutral greys."""
    colours = [c[column] for c in glass.TEMPLATE_ROLES.values()]
    kvconfig = [
        "[%General]",
        "author=elkrien based on Arc Dark style",
        "comment=Catppuccin-Mocha-Mauve",
        "composite=true",
        "translucent_windows=false",
        "blurring=false",
        "popup_blurring=true",
        "opaque=kaffeine,kdenlive,vlc",
        "reduce_window_opacity=10",
        "",
        "[GeneralColors]",
        *(f"colour{i}.color={c.upper()}" for i, c in enumerate(colours)),
        f"highlight.color={glass.TEMPLATE_ROLES['accent'][column].upper()}4D",
        f"shadow.color={GREYS[0]}",
        "",
        "[Hacks]",
        "transparent_dolphin_view=false",
        "",
    ]
    shapes = "".join(f'<rect id="r{i}" fill="{c}" stroke="{GREYS[i % 3]}"/>' for i, c in enumerate(colours))
    svg = f'<svg xmlns="http://www.w3.org/2000/svg"><defs><stop stop-color="{colours[0]}"/></defs>{shapes}</svg>\n'
    return "\n".join(kvconfig), svg


def colours_in(text: str) -> set[str]:
    return {m.lower() for m in glass._HEX.findall(text)}


class FakeRunner:
    """Records what the step asks of the desktop; nothing reaches the real one."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.calls: list[tuple] = []
        self.desktop: dict[tuple[str, str, str], str] = {}  # what kread finds

    def kwrite(self, file, group, key, value):
        self.calls.append(("kwrite", file, group, key, value))

    def notify_style_changed(self):
        self.calls.append(("notify",))

    def kread(self, file, group, key, default=""):
        return self.desktop.get((file, group, key), default)

    def kwin_reconfigure(self):
        self.calls.append(("kwin",))

    def kwin_reconfigure_effect(self, name):
        self.calls.append(("kwin", name))

    def run(self, args, check=True, **kwargs):
        self.calls.append(("run", tuple(args)))

    def widget_styles(self) -> list[str]:
        return [c[4] for c in self.calls if c[0] == "kwrite" and c[3] == "widgetStyle"]


def preset_with(preset_id: str = "nord-minimal", **sections) -> Preset:
    data = copy.deepcopy(presets.find(preset_id).data)
    for name, value in sections.items():
        if value is None:
            data.pop(name, None)
        else:
            data[name] = value
    return Preset(data, "user")


class KvconfigTests(unittest.TestCase):
    def test_sets_every_glass_key(self):
        text = glass.glass_kvconfig(THEME, "frosted", dark=True)
        for key, value in {
            "composite": "true",
            "translucent_windows": "true",
            "blurring": "true",
            "popup_blurring": "true",
            "reduce_window_opacity": "-25",  # frosted keeps the window in use solid
            "reduce_menu_opacity": "12",
            "menu_blur_radius": "2",
            "no_inactiveness": "false",
        }.items():
            self.assertEqual(glass.get_key(text, "%General", key), value, key)
        self.assertEqual(glass.get_key(text, "%General", "tooltip_blur_radius"), "5")  # already enough
        self.assertEqual(glass.get_key(text, "Hacks", "transparent_dolphin_view"), "true")

    def test_every_look_on_dark_and_light_palettes(self):
        for level, dark, window, menu, blurring in (  # every look keeps the window in use solid
            ("frosted", True, "-25", "12", "true"),
            ("frosted", False, "-18", "10", "true"),  # 18 // 2 is 9: menus get at least 10
            ("glass", True, "-40", "20", "true"),
            ("glass", False, "-30", "15", "true"),
            ("clear", True, "-50", "25", "false"),
            ("clear", False, "-38", "19", "false"),
        ):
            with self.subTest(level=level, dark=dark):
                text = glass.glass_kvconfig(THEME, level, dark)
                general = {
                    key: glass.get_key(text, "%General", key)
                    for key in ("reduce_window_opacity", "reduce_menu_opacity", "blurring")
                }
                self.assertEqual(general, {"reduce_window_opacity": window, "reduce_menu_opacity": menu,
                                           "blurring": blurring})  # fmt: skip
                for key in ("composite", "translucent_windows", "popup_blurring"):  # menus stay readable
                    self.assertEqual(glass.get_key(text, "%General", key), "true", key)
                self.assertEqual(glass.get_key(text, "Hacks", "transparent_dolphin_view"), "true")

    def test_transparency_replaces_the_default(self):
        for transparency, window, menu in (
            (10, "10", "10"),  # menus never go below 10
            (33, "33", "16"),
            (44, "44", "22"),
            (61, "61", "30"),  # nor above 30
            (70, "70", "30"),
            (5, "10", "10"),  # out of range: kept within 10 to 70
            (95, "70", "30"),
            (40.4, "40", "20"),
        ):
            for dark in (True, False):
                with self.subTest(transparency=transparency, dark=dark):
                    text = glass.glass_kvconfig(THEME, "glass", dark, transparency)
                    self.assertEqual(glass.get_key(text, "%General", "reduce_window_opacity"), f"-{window}")
                    self.assertEqual(glass.get_key(text, "%General", "reduce_menu_opacity"), menu)
        for junk in (None, "40", True, math.nan, math.inf, [40]):
            with self.subTest(junk=junk):
                text = glass.glass_kvconfig(THEME, "clear", True, junk)
                self.assertEqual(glass.get_key(text, "%General", "reduce_window_opacity"), "-50")

    def test_focused_solid_keeps_only_inactive_windows_see_through(self):
        for level in ("frosted", "glass", "clear"):
            for focused in (True, False):
                with self.subTest(level=level, focused=focused):
                    text = glass.glass_kvconfig(THEME, level, True, 44, focused)
                    window = glass.get_key(text, "%General", "reduce_window_opacity")
                    self.assertEqual(window, "-44" if focused else "44")  # Kvantum: negative is inactive only
                    self.assertEqual(glass.get_key(text, "%General", "reduce_menu_opacity"), "22")
                    blurring = glass.get_key(text, "%General", "blurring")
                    self.assertEqual(blurring, "true" if level in glass.BLURRED else "false")
                    # Kvantum must tell inactive windows apart; THEME leaves that to its default.
                    self.assertEqual(glass.get_key(text, "%General", "no_inactiveness"), "false" if focused else None)
        for junk in (None, "yes", 1, 0, [True]):  # anything but a bool is the look's default
            with self.subTest(junk=junk):
                for level in ("frosted", "glass", "clear"):
                    self.assertEqual(
                        glass.glass_kvconfig(THEME, level, True, None, junk), glass.glass_kvconfig(THEME, level, True)
                    )
        self.assertIs(glass.FOCUSED_SOLID, True)  # every look, Clear included

    def test_focused_solid_overrides_a_theme_without_inactive_windows(self):
        theme = THEME.replace("popup_blurring=true\n", "popup_blurring=true\nno_inactiveness=true\n")
        focused = glass.glass_kvconfig(theme, "glass", True)
        self.assertEqual(glass.get_key(focused, "%General", "no_inactiveness"), "false")
        self.assertEqual(focused.count("no_inactiveness="), 1)
        see_through = glass.glass_kvconfig(theme, "glass", True, focused_solid=False)
        self.assertEqual(glass.get_key(see_through, "%General", "no_inactiveness"), "true")  # the theme's own

    def test_menu_transparency(self):
        self.assertEqual(
            [glass.menu_transparency(t) for t in (10, 20, 21, 40, 60, 61, 70)], [10, 10, 10, 20, 30, 30, 30]
        )

    def test_subtle_is_frosted_and_unknown_looks_are_refused(self):
        self.assertEqual(glass.glass_kvconfig(THEME, "subtle", True), glass.glass_kvconfig(THEME, "frosted", True))
        self.assertEqual(
            glass.glass_kvconfig(THEME, "subtle", False, 22), glass.glass_kvconfig(THEME, "frosted", False, 22)
        )
        for bad in ("off", "x\n[Hacks]", "Frosted", "", None, ["frosted"], {"glass": "frosted"}):
            with self.subTest(level=bad), self.assertRaises(ValueError):
                glass.glass_kvconfig(THEME, bad, True)

    def test_patching_is_idempotent(self):
        once = glass.glass_kvconfig(THEME, "frosted", True)
        self.assertEqual(glass.glass_kvconfig(once, "frosted", True), once)
        for level in ("glass", "clear"):  # looks switch back and forth
            other = glass.glass_kvconfig(once, level, True, 60)
            self.assertEqual(glass.glass_kvconfig(other, "frosted", True), once)
        for focused in (False, True):  # and so does the window in use
            other = glass.glass_kvconfig(once, "frosted", True, None, focused)
            self.assertEqual(glass.glass_kvconfig(other, "frosted", True), once)

    def test_keeps_comments_sections_and_other_lines(self):
        patched = glass.glass_kvconfig(THEME, "frosted", True)
        before, after = THEME.splitlines(), patched.splitlines()
        sections = [line for line in before if line.startswith("[")]
        self.assertEqual([line for line in after if line.startswith("[")], sections)
        for line in before:
            key = line.split("=", 1)[0].strip()
            if key not in GLASS_KEYS or line.startswith(("#", ";")):
                self.assertIn(line, after)
        self.assertIn("# translucent_windows=false (an old setting, kept for reference)", after)
        colours = after[after.index("[GeneralColors]") : after.index("[Hacks]")]
        self.assertIn("translucent_windows=false", colours)  # another section's key is not ours
        self.assertEqual(sum(line.startswith("translucent_windows=") for line in after), 2)

    def test_new_keys_stay_inside_their_section(self):
        text = "[%General]\ncomment=x\n\n\n[Other]\na=1\n"
        patched = glass.set_keys(text, "%General", {"blurring": "true"})
        self.assertEqual(patched, "[%General]\ncomment=x\nblurring=true\n\n\n[Other]\na=1\n")
        self.assertEqual(glass.set_keys("", "Hacks", {"a": "1"}), "[Hacks]\na=1\n")
        self.assertEqual(glass.set_keys("[A]\nb=2\n", "Hacks", {"a": "1"}), "[A]\nb=2\n\n[Hacks]\na=1\n")

    def test_a_repeated_section_gets_new_keys_once(self):
        text = "[%General]\na=1\n[Other]\nz=0\n[%General]\nblurring=false\n"
        patched = glass.set_keys(text, "%General", {"blurring": "true", "composite": "true"})
        self.assertEqual(patched.count("composite=true"), 1)
        self.assertNotIn("blurring=false", patched)

    def test_opaque_list_keeps_the_theme_apps_without_duplicates(self):
        opaque = glass.get_key(glass.glass_kvconfig(THEME, "frosted", True), "%General", "opaque").split(",")
        self.assertEqual(opaque[:3], ["kaffeine", "VLC", "kdenlive"])
        self.assertEqual(len({name.lower() for name in opaque}), len(opaque))
        for name in ("krita", "gwenview", "obs", "soffice.bin"):
            self.assertIn(name, opaque)

    def test_odd_values_in_a_downloaded_theme_do_not_break_it(self):
        for value in ("abc", "nan", "1e999", "-inf", ""):
            with self.subTest(value=value):
                text = glass.glass_kvconfig(f"[%General]\nmenu_blur_radius={value}\n", "clear", True)
                self.assertEqual(glass.get_key(text, "%General", "menu_blur_radius"), "2")


# Window backgrounds shaped like Layan's: a little see-through in the theme itself, active and inactive.
LAYAN_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg">\n'
    '<path style="opacity:0.8;fill:#31313a;fill-rule:evenodd" d="m 370,887 h 30 v 30 h -30 z" id="window-normal" />\n'
    '<path style="opacity:0.8;fill:#31313a;fill-rule:evenodd" d="m 402,887 h 30 v 30 h -30 z"'
    ' id="window-normal-inactive" />\n'
    '<rect id="button-normal" style="opacity:0.5;fill:#000000" width="4" height="4"/>\n'
    "</svg>\n"
)


def window_tag(svg: str, element: str) -> str | None:
    found = [e for e in ET.fromstring(svg).iter() if e.get("id") == element]  # noqa: S314 (made by the tests)
    return None if not found else ET.tostring(found[0], encoding="unicode")


class SolidActiveWindowTests(unittest.TestCase):
    """While the window in use stays solid, a theme that draws it see-through itself gets an opaque
    active window; inactive windows keep the theme's own look."""

    def test_layan_turns_solid_and_its_inactive_window_stays(self):
        solid = glass.solid_active_window(LAYAN_SVG)
        self.assertIn('style="opacity:1;fill:#31313a;fill-rule:evenodd" d="m 370,887', solid)
        self.assertEqual(window_tag(solid, "window-normal-inactive"), window_tag(LAYAN_SVG, "window-normal-inactive"))
        self.assertIn('<rect id="button-normal" style="opacity:0.5;fill:#000000"', solid)  # other elements stay
        self.assertEqual(len(solid.splitlines()), len(LAYAN_SVG.splitlines()))
        self.assertEqual(glass.solid_active_window(solid), solid)  # once is enough

    def test_without_an_inactive_element_the_original_becomes_one(self):
        # Kvantum paints inactive windows with the active element when they have none of their own.
        svg = '<svg><rect width="48" x="136" style="opacity:.9;fill:#3d3d3e" id="window-normal"/></svg>'
        solid = glass.solid_active_window(svg)
        self.assertEqual(
            solid,
            '<svg><rect width="48" x="136" style="opacity:1;fill:#3d3d3e" id="window-normal"/>'
            '<rect width="48" x="136" style="opacity:.9;fill:#3d3d3e" id="window-normal-inactive"/></svg>',
        )
        self.assertEqual(glass.solid_active_window(solid), solid)

    def test_every_kind_of_own_opacity_goes(self):
        svg = (
            "<svg><rect id='window-normal' opacity=\"0.7\" fill-opacity='.5' style=\"fill:#000;fill-opacity: 0.6\"/>"
            '<rect id="window-normal-inactive" style="fill:#000"/></svg>'
        )
        self.assertEqual(
            glass.solid_active_window(svg),
            "<svg><rect id='window-normal' opacity=\"1\" fill-opacity='1' style=\"fill:#000;fill-opacity: 1\"/>"
            '<rect id="window-normal-inactive" style="fill:#000"/></svg>',
        )

    def test_opaque_or_unusual_themes_are_left_alone(self):
        for svg in (
            '<svg><rect id="window-normal" style="fill:#1e1f29;fill-opacity:1"/></svg>',  # Dracula, Sweet
            '<svg><rect id="window-normal" style="fill:#EFF1F5" width="46"/></svg>',  # Catppuccin, Rose Pine
            '<svg><rect id="window-normal" style="opacity:1.0;stop-opacity:0.5;fill:url(#g)"/></svg>',
            '<svg><rect id="window-normal" style="opacity:lots" opacity="80%"/></svg>',
            '<svg><rect id="frame-normal" style="opacity:0.5"/></svg>',  # Gruvbox, Tokyo Night: no element
            '<svg><rect id="window-normal-inactive" style="opacity:0.5"/></svg>',
            '<svg><rect id="window-normal" style="opacity:0.5"/><rect id="window-normal" style="opacity:0.5"/></svg>',
            '<svg><g id="window-normal" style="opacity:0.5"><rect/></g></svg>',  # cannot be copied whole
            "",
        ):
            with self.subTest(svg=svg):
                self.assertEqual(glass.solid_active_window(svg), svg)
        group = '<svg><g id="window-normal" style="opacity:0.5"><rect/></g><g id="window-normal-inactive"/></svg>'
        self.assertIn('<g id="window-normal" style="opacity:1">', glass.solid_active_window(group))


class RecolourTests(unittest.TestCase):
    def check_no_template_colours(self, palette: Palette, column: int) -> None:
        kvconfig, svg = template_files(column)
        out = glass.recolour(kvconfig, palette) + glass.recolour(svg, palette)
        template = {c[column] for c in glass.TEMPLATE_ROLES.values()}
        ours = set(glass.palette_roles(palette).values())
        self.assertEqual(colours_in(out) & template - ours, set())
        self.assertTrue(set(GREYS) <= colours_in(out))  # neutral greys and shadows stay

    def test_dark_palettes_leave_no_template_colours(self):
        for preset_id in ("nord-minimal", "everforest-woods", "kanagawa-wave"):
            with self.subTest(preset=preset_id):
                palette = presets.find(preset_id).palette
                self.assertTrue(palette.dark)
                self.check_no_template_colours(palette, 0)

    def test_light_palettes_use_the_latte_colours(self):
        self.check_no_template_colours(Palette(LIGHT), 1)
        light = Palette(generate.mode_variant(presets.find("nord-minimal").palette.colors, False))
        self.check_no_template_colours(light, 1)

    def test_case_and_alpha_are_kept(self):
        palette = presets.find("nord-minimal").palette
        out = glass.recolour("a=#CBA6F74D\nb=#cba6f7\nc=#BEBEBE\n", palette)
        self.assertEqual(out, f"a={palette.accent.upper()}4D\nb={palette.accent.lower()}\nc=#BEBEBE\n")

    def test_one_pass_never_swaps_twice(self):
        # The palette's text is the template's base colour: it must not then become the palette's base.
        colours = dict(presets.find("nord-minimal").palette.colors, text="#1e1e2e")
        out = glass.recolour("x=#cdd6f4\n", Palette(colours))
        self.assertEqual(out, "x=#1e1e2e\n")

    def test_palette_theme_is_complete(self):
        palette = presets.find("kanagawa-wave").palette
        kvconfig, svg = template_files(0)
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp, "template")
            source.mkdir()
            Path(source, f"{glass.TEMPLATE_DARK}.kvconfig").write_text(kvconfig)
            Path(source, f"{glass.TEMPLATE_DARK}.svg").write_text(svg)
            target = glass.write_palette_theme(source, glass.TEMPLATE_DARK, Path(tmp, "Kvantum"), palette, "frosted")
            self.assertEqual(target, Path(tmp, "Kvantum", glass.GENERATED_NAME))
            self.assertEqual(
                sorted(p.name for p in target.iterdir()),
                sorted([glass.MARKER, "plasmify-glass.kvconfig", "plasmify-glass.svg"]),
            )
            text = Path(target, "plasmify-glass.kvconfig").read_text()
            ET.fromstring(Path(target, "plasmify-glass.svg").read_text())  # noqa: S314 (made by this test)
            self.assertEqual(glass.get_key(text, "%General", "comment"), glass.GENERATED_COMMENT)
            self.assertIn("elkrien", glass.get_key(text, "%General", "author"))  # the credit stays
            self.assertEqual(glass.get_key(text, "%General", "translucent_windows"), "true")
            self.assertEqual(glass.get_key(text, "GeneralColors", "colour0.color"), palette.base.upper())
            with self.assertRaises(ValueError):
                glass.write_palette_theme(source, "../../etc", Path(tmp, "Kvantum"), palette, "frosted")

    def test_palette_theme_gets_an_opaque_active_window(self):
        palette = presets.find("kanagawa-wave").palette
        kvconfig, svg = template_files(0)
        svg = svg.replace("</svg>", '<rect id="window-normal" style="opacity:0.9;fill:#1e1e2e"/></svg>')
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp, "template")
            source.mkdir()
            Path(source, f"{glass.TEMPLATE_DARK}.kvconfig").write_text(kvconfig)
            Path(source, f"{glass.TEMPLATE_DARK}.svg").write_text(svg)
            for focused, active, inactive in ((None, "opacity:1", "opacity:0.9"), (False, "opacity:0.9", None)):
                with self.subTest(focused=focused):
                    target = glass.write_palette_theme(
                        source, glass.TEMPLATE_DARK, Path(tmp, "Kvantum"), palette, "glass", None, focused
                    )
                    out = Path(target, "plasmify-glass.svg").read_text()
                    self.assertIn(active, window_tag(out, "window-normal"))
                    if inactive is None:  # the theme as it is: nothing added
                        self.assertIsNone(window_tag(out, "window-normal-inactive"))
                    else:  # inactive windows keep the template's own look
                        self.assertIn(inactive, window_tag(out, "window-normal-inactive"))


class GlassCopyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name, "Kvantum")
        self.theme = self.root / "Mine"
        self.theme.mkdir(parents=True)
        (self.theme / "Mine.kvconfig").write_text(THEME)
        (self.theme / "Mine.svg").write_text("<svg/>")

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_a_full_copy_with_the_svg(self):
        target = glass.write_glass_copy(self.theme, "Mine", "frosted", True)
        self.assertEqual(target, self.root / "Mine-glass")
        self.assertEqual((target / "Mine-glass.svg").read_text(), "<svg/>")
        text = (target / "Mine-glass.kvconfig").read_text()
        self.assertEqual(text, glass.glass_kvconfig(THEME, "frosted", True))
        self.assertTrue(glass.made_by_plasmify(target))
        self.assertEqual((self.theme / "Mine.kvconfig").read_text(), THEME)  # the original stays
        (target / "stale").write_text("x")
        glass.write_glass_copy(self.theme, "Mine", "subtle", True)  # Plasmify's own: replaced
        self.assertFalse((target / "stale").exists())

    def test_the_window_in_use_turns_solid_in_the_copy_only(self):
        (self.theme / "Mine.svg").write_text(LAYAN_SVG)
        target = glass.write_glass_copy(self.theme, "Mine", "glass", True)  # on unless turned off
        copied = (target / "Mine-glass.svg").read_text()
        self.assertEqual(copied, glass.solid_active_window(LAYAN_SVG))
        self.assertNotEqual(copied, LAYAN_SVG)
        self.assertEqual((self.theme / "Mine.svg").read_text(), LAYAN_SVG)  # the original stays
        glass.write_glass_copy(self.theme, "Mine", "glass", True, None, False)
        self.assertEqual((target / "Mine-glass.svg").read_text(), LAYAN_SVG)

    def test_an_svg_that_needs_no_change_is_copied_byte_for_byte(self):
        raw = b'<svg><rect id="window-normal" style="fill:#000"/><desc>\xff\xfe</desc></svg>'
        (self.theme / "Mine.svg").write_bytes(raw)
        for focused in (True, False):
            target = glass.write_glass_copy(self.theme, "Mine", "clear", True, None, focused)
            self.assertEqual((target / "Mine-glass.svg").read_bytes(), raw)

    def test_a_huge_svg_writes_nothing_while_the_window_in_use_stays_solid(self):
        (self.theme / "Mine.svg").write_text("x" * (glass.MAX_THEME_BYTES + 1))
        with self.assertRaises(ValueError):
            glass.write_glass_copy(self.theme, "Mine", "glass", True)
        self.assertFalse((self.root / "Mine-glass").exists())
        target = glass.write_glass_copy(self.theme, "Mine", "glass", True, None, False)  # copied as it is
        self.assertEqual((target / "Mine-glass.svg").stat().st_size, glass.MAX_THEME_BYTES + 1)

    def test_a_folder_the_user_made_is_never_touched(self):
        own = self.root / "Mine-glass"
        own.mkdir()
        (own / "Mine-glass.kvconfig").write_text("mine")
        with self.assertRaises(glass.ForeignThemeError):
            glass.write_glass_copy(self.theme, "Mine", "frosted", True)
        self.assertEqual((own / "Mine-glass.kvconfig").read_text(), "mine")

    def test_links_are_not_followed(self):
        outside = Path(self.tmp.name, "outside")
        outside.mkdir()
        (outside / glass.MARKER).write_text("planted")
        (self.root / "Mine-glass").symlink_to(outside)
        with self.assertRaises((glass.ForeignThemeError, UnsafePathError)):
            glass.write_glass_copy(self.theme, "Mine", "frosted", True)
        self.assertEqual(sorted(p.name for p in outside.iterdir()), [glass.MARKER])
        (self.root / "Mine-glass").unlink()
        (self.theme / "Mine.svg").unlink()
        (self.theme / "Mine.svg").symlink_to(Path(self.tmp.name, "secret"))
        Path(self.tmp.name, "secret").write_text("secret")
        target = glass.write_glass_copy(self.theme, "Mine", "frosted", True)
        self.assertFalse((target / "Mine-glass.svg").exists())

    def test_hostile_names_are_refused(self):
        for name in ("../evil", "a/b", "..", ".", "", "/etc/passwd", "x\x00y", "..\\x"):
            with self.subTest(name=name), self.assertRaises(UnsafePathError):
                glass.write_glass_copy(self.theme, name, "frosted", True)
        self.assertEqual(sorted(p.name for p in Path(self.tmp.name).iterdir()), ["Kvantum"])
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["Mine"])

    def test_a_huge_or_missing_kvconfig_writes_nothing(self):
        (self.theme / "Mine.kvconfig").write_text("x" * (glass.MAX_THEME_BYTES + 1))
        with self.assertRaises(ValueError):
            glass.write_glass_copy(self.theme, "Mine", "frosted", True)
        (self.theme / "Mine.kvconfig").unlink()
        with self.assertRaises(FileNotFoundError):
            glass.write_glass_copy(self.theme, "Mine", "frosted", True)
        self.assertFalse((self.root / "Mine-glass").exists())


class StyleStepTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kvantum = Path(self.tmp.name, "config", "Kvantum")
        self.outputs = Path(self.tmp.name, "config", "kwinoutputconfig.json")
        self.patches = [
            mock.patch.object(style, "KVANTUM_DIR", self.kvantum),
            mock.patch.object(style, "KWIN_OUTPUTS", self.outputs),
        ]
        for patcher in self.patches:
            patcher.start()
        self.files = {}
        for column, template in ((0, glass.TEMPLATE_DARK), (1, glass.TEMPLATE_LIGHT)):
            kvconfig, svg = template_files(column)
            self.files[f"{template}.kvconfig"] = kvconfig.encode()
            self.files[f"{template}.svg"] = svg.encode()
        sums = {name: hashlib.sha256(data).hexdigest() for name, data in self.files.items()}
        self.patches.append(mock.patch.dict(glass.TEMPLATE_SHA256, sums))
        self.patches[-1].start()
        self.fetched: list[str] = []

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.tmp.cleanup()

    def fake_fetch(self, url: str, dest: Path) -> Path:
        downloads.check_url(url)
        self.fetched.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.files[url.rsplit("/", 1)[1]])
        return dest

    def run_step(self, preset: Preset, dry_run: bool = False, fetch=None, checkout=None):
        runner = FakeRunner(dry_run)
        logs: list[str] = []
        work = Path(self.tmp.name, "work")
        work.mkdir(exist_ok=True)
        ctx = Context(preset, runner, work, logs.append)
        with (
            mock.patch.object(downloads, "fetch", fetch or self.fake_fetch),
            mock.patch.object(downloads, "sparse_checkout", checkout or self.fake_checkout),
        ):
            style.StyleStep().apply(ctx)
        # Running apps hear about every change of widget style.
        self.assertEqual(len([c for c in runner.calls if c[0] == "notify"]), len(runner.widget_styles()))
        return ctx, runner, logs

    def fake_checkout(self, repo, path, dest, ref=None):
        name = Path(path).name
        folder = Path(dest, path)
        folder.mkdir(parents=True)
        (folder / f"{name}.kvconfig").write_text(THEME)
        (folder / f"{name}.svg").write_text("<svg/>")
        return folder

    def selected_theme(self) -> str:
        return (self.kvantum / "kvantum.kvconfig").read_text()

    def test_packages(self):
        step = style.StyleStep()
        self.assertEqual(step.packages(preset_with(style=None)), [])
        self.assertEqual(step.packages(preset_with(style={"glass": "off"})), [])
        for level in ("frosted", "glass", "clear", "subtle"):
            self.assertEqual(step.packages(preset_with(style={"glass": level})), ["kvantum"], level)
        self.assertEqual(step.packages(presets.find("catppuccin-anime")), ["kvantum"])

    def test_touched_paths(self):
        step = style.StyleStep()
        breeze = {p.name for p in step.touched_paths(preset_with(style={"glass": "frosted"}))}
        self.assertEqual(breeze, {"kdeglobals", "kvantum.kvconfig", glass.GENERATED_NAME})
        kvantum = presets.find("dracula-night").data["style"]["kvantum"]
        named = {
            p.name
            for p in step.touched_paths(preset_with("dracula-night", style={"kvantum": kvantum, "glass": "subtle"}))
        }
        self.assertEqual(
            named, {"kdeglobals", "kvantum.kvconfig", "Dracula-purple-solid", "Dracula-purple-solid-glass"}
        )
        plain = {p.name for p in step.touched_paths(presets.find("dracula-night"))}
        self.assertNotIn("Dracula-purple-solid-glass", plain)
        for path in step.touched_paths(preset_with(style={"glass": "frosted"})):
            self.assertTrue(path == self.kvantum / path.name or path.name == "kdeglobals")

    def blur_calls(self, runner: FakeRunner) -> dict[str, object]:
        return {c[3]: c[4] for c in runner.calls if c[0] == "kwrite" and c[1] == "kwinrc"}

    def test_glass_without_effects_keeps_the_blur_light(self):
        """Without an effects section the effects step does not run, so the desktop's blur (KWin's
        default 15, the strongest) stayed: the glass look read as frosted."""
        for level, kwinrc, strength, noise in (
            ("glass", {}, 4, 2),  # KWin's defaults (15, 5) brought down
            ("glass", {"BlurStrength": "3", "NoiseStrength": "0"}, 3, 0),  # already light: kept
            ("frosted", {"BlurStrength": "6"}, 10, 5),
        ):
            with self.subTest(level=level, kwinrc=kwinrc):
                preset = preset_with(style={"glass": level}, effects=None)
                runner = FakeRunner()
                runner.desktop = {("kwinrc", "Effect-blur", key): value for key, value in kwinrc.items()}
                logs: list[str] = []
                work = Path(self.tmp.name, "work")
                work.mkdir(exist_ok=True)
                with mock.patch.object(downloads, "fetch", self.fake_fetch):
                    style.StyleStep().apply(Context(preset, runner, work, logs.append))
                self.assertEqual(
                    self.blur_calls(runner), {"blurEnabled": True, "BlurStrength": strength, "NoiseStrength": noise}
                )
                self.assertIn(("kwin", "blur"), runner.calls)  # the running blur takes the new strength
                self.assertIn("kwinrc", {p.name for p in style.StyleStep().touched_paths(preset)})

    def test_blur_is_left_to_the_effects_step_or_alone(self):
        for preset in (
            preset_with(style={"glass": "glass"}),  # has effects: that step sets the blur
            preset_with(style={"glass": "clear"}, effects=None),  # clear leaves blur alone
            preset_with(style={"glass": "off"}, effects=None),
        ):
            with self.subTest(style=preset.section("style")):
                _ctx, runner, _logs = self.run_step(preset)
                self.assertEqual(self.blur_calls(runner), {})
                self.assertNotIn("kwinrc", {p.name for p in style.StyleStep().touched_paths(preset)})

    def test_says_which_open_apps_keep_their_old_look(self):
        """KDE gives open apps a new style only when its name changes, and Kvantum reads its
        theme only at the start: Kvantum to Kvantum leaves open apps as they were."""
        for before, level, expected in (
            ("kvantum", "glass", "keep their old Kvantum look until you restart them"),
            ("Breeze", "glass", "turn to glass after you restart them"),
        ):
            with self.subTest(before=before):
                runner = FakeRunner()
                runner.desktop = {("kdeglobals", "KDE", "widgetStyle"): before}
                logs: list[str] = []
                work = Path(self.tmp.name, "work")
                work.mkdir(exist_ok=True)
                with mock.patch.object(downloads, "fetch", self.fake_fetch):
                    style.StyleStep().apply(Context(preset_with(style={"glass": level}), runner, work, logs.append))
                self.assertTrue(any(expected in line for line in logs), logs)

    def test_breeze_preset_gets_a_palette_theme(self):
        preset = preset_with(style={"glass": "frosted"})
        ctx, runner, logs = self.run_step(preset)
        self.assertEqual(ctx.warnings, [])
        self.assertEqual(len(self.fetched), 2)
        self.assertTrue(all(glass.TEMPLATE_COMMIT in url and glass.TEMPLATE_DARK in url for url in self.fetched))
        target = self.kvantum / glass.GENERATED_NAME
        self.assertTrue((target / "plasmify-glass.svg").is_file())
        text = (target / "plasmify-glass.kvconfig").read_text()
        self.assertEqual(glass.get_key(text, "%General", "reduce_window_opacity"), "-25")  # the window in use solid
        self.assertEqual(colours_in(text) & {c[0] for c in glass.TEMPLATE_ROLES.values()}, set())
        self.assertEqual(self.selected_theme(), "[General]\ntheme=plasmify-glass\n")
        self.assertEqual(runner.widget_styles(), ["kvantum"])
        self.assertEqual(ctx.assets, [target])
        self.assertTrue(any("restart" in line for line in logs))

    def test_light_breeze_preset_uses_the_light_template(self):
        preset = preset_with(palette=dict(LIGHT), style={"glass": "subtle"})  # the older name of frosted
        ctx, _, logs = self.run_step(preset)
        self.assertTrue(all(glass.TEMPLATE_LIGHT in url for url in self.fetched))
        text = (self.kvantum / glass.GENERATED_NAME / "plasmify-glass.kvconfig").read_text()
        self.assertEqual(glass.get_key(text, "%General", "reduce_window_opacity"), "-18")
        self.assertTrue(any("frosted look, 18% see-through, the window in use stays solid" in line for line in logs))

    def test_the_transparency_reaches_the_theme_and_the_log(self):
        for level, dark, transparency, window, blurring in (
            ("clear", True, 60, "-60", "false"),
            ("glass", False, 45, "-45", "true"),  # the window in use stays solid
        ):
            with self.subTest(level=level):
                palette = presets.find("nord-minimal").data["palette"] if dark else dict(LIGHT)
                preset = preset_with(palette=palette, style={"glass": level, "transparency": transparency})
                ctx, _, logs = self.run_step(preset)
                self.assertEqual(ctx.warnings, [])
                text = (self.kvantum / glass.GENERATED_NAME / "plasmify-glass.kvconfig").read_text()
                self.assertEqual(glass.get_key(text, "%General", "reduce_window_opacity"), window)
                self.assertEqual(glass.get_key(text, "%General", "blurring"), blurring)
                self.assertTrue(any(f"{level} look, {transparency}% see-through" in line for line in logs), logs)
        kvantum = presets.find("dracula-night").data["style"]["kvantum"]
        preset = preset_with("dracula-night", style={"kvantum": kvantum, "glass": "glass", "transparency": 55})
        ctx, _, logs = self.run_step(preset)
        copy_dir = self.kvantum / "Dracula-purple-solid-glass"
        text = (copy_dir / "Dracula-purple-solid-glass.kvconfig").read_text()
        self.assertEqual(text, glass.glass_kvconfig(THEME, "glass", True, 55))
        expected = "Dracula-purple-solid (glass look, 55% see-through, the window in use stays solid)"
        self.assertTrue(any(expected in line for line in logs), logs)

    def test_focused_solid_reaches_the_theme_and_the_log(self):
        for looks, window, solid in (
            ({"glass": "clear"}, "-50", True),  # on unless turned off, in every look
            ({"glass": "clear", "focused_solid": False}, "50", False),
            ({"glass": "glass", "focused_solid": False}, "40", False),
            ({"glass": "frosted", "transparency": 30, "focused_solid": False}, "30", False),
        ):
            with self.subTest(looks=looks):
                ctx, _, logs = self.run_step(preset_with(style=looks))
                self.assertEqual(ctx.warnings, [])
                text = (self.kvantum / glass.GENERATED_NAME / "plasmify-glass.kvconfig").read_text()
                self.assertEqual(glass.get_key(text, "%General", "reduce_window_opacity"), window)
                self.assertEqual(any("the window in use stays solid" in line for line in logs), solid, logs)

    def test_a_checksum_mismatch_falls_back_to_breeze(self):
        self.files[f"{glass.TEMPLATE_DARK}.svg"] += b"<desc>changed upstream</desc>"
        ctx, runner, _ = self.run_step(preset_with(style={"glass": "frosted"}))
        self.assertTrue(any("checksum" in w for w in ctx.warnings))
        self.assertEqual(runner.widget_styles(), ["Breeze"])
        self.assertFalse((self.kvantum / glass.GENERATED_NAME).exists())
        self.assertFalse((self.kvantum / "kvantum.kvconfig").exists())
        self.assertEqual(ctx.assets, [])

    def test_a_failed_download_falls_back_to_breeze(self):
        def fail(url, dest):
            raise downloads.DownloadError("no network")

        ctx, runner, _ = self.run_step(preset_with(style={"glass": "frosted"}), fetch=fail)
        self.assertTrue(any("no network" in w and "Breeze" in w for w in ctx.warnings))
        self.assertEqual(runner.widget_styles(), ["Breeze"])

    def test_a_users_own_plasmify_glass_folder_is_left_alone(self):
        own = self.kvantum / glass.GENERATED_NAME
        own.mkdir(parents=True)
        (own / "plasmify-glass.kvconfig").write_text("mine")
        ctx, runner, _ = self.run_step(preset_with(style={"glass": "frosted"}))
        self.assertEqual((own / "plasmify-glass.kvconfig").read_text(), "mine")
        self.assertEqual(sorted(p.name for p in own.iterdir()), ["plasmify-glass.kvconfig"])
        self.assertEqual(runner.widget_styles(), ["Breeze"])
        self.assertTrue(ctx.warnings)
        self.assertEqual(ctx.assets, [])

    def test_kvantum_preset_gets_a_glass_copy(self):
        preset = preset_with("dracula-night", style=dict(presets.find("dracula-night").data["style"], glass="frosted"))
        ctx, runner, _ = self.run_step(preset)
        self.assertEqual(ctx.warnings, [])
        copy_dir = self.kvantum / "Dracula-purple-solid-glass"
        self.assertTrue((copy_dir / "Dracula-purple-solid-glass.svg").is_file())
        text = (copy_dir / "Dracula-purple-solid-glass.kvconfig").read_text()
        self.assertEqual(text, glass.glass_kvconfig(THEME, "frosted", True))
        self.assertEqual(self.selected_theme(), "[General]\ntheme=Dracula-purple-solid-glass\n")
        self.assertEqual(ctx.assets, [self.kvantum / "Dracula-purple-solid", copy_dir])
        self.assertEqual(runner.widget_styles(), ["kvantum"])
        self.assertEqual(self.fetched, [])  # no template needed

    def test_a_users_own_glass_named_theme_keeps_the_plain_theme(self):
        own = self.kvantum / "Dracula-purple-solid-glass"
        own.mkdir(parents=True)
        preset = preset_with("dracula-night", style=dict(presets.find("dracula-night").data["style"], glass="subtle"))
        ctx, _, _ = self.run_step(preset)
        self.assertEqual(list(own.iterdir()), [])
        self.assertEqual(self.selected_theme(), "[General]\ntheme=Dracula-purple-solid\n")
        self.assertTrue(any("left alone" in w for w in ctx.warnings))

    def test_without_glass_nothing_changes(self):
        ctx, runner, _ = self.run_step(presets.find("dracula-night"))
        self.assertEqual(self.selected_theme(), "[General]\ntheme=Dracula-purple-solid\n")
        self.assertFalse((self.kvantum / "Dracula-purple-solid-glass").exists())
        ctx, runner, _ = self.run_step(preset_with(style=None))
        self.assertEqual(runner.widget_styles(), ["Breeze"])
        self.assertEqual(self.fetched, [])

    def test_dry_run_writes_nothing(self):
        for preset in (
            preset_with(style={"glass": "frosted"}),
            preset_with("dracula-night", style=dict(presets.find("dracula-night").data["style"], glass="frosted")),
        ):
            with self.subTest(preset=preset.id):
                ctx, runner, logs = self.run_step(preset, dry_run=True)
                self.assertFalse(self.kvantum.exists())
                self.assertEqual(runner.widget_styles(), ["kvantum"])
                self.assertTrue(any("glass" in line for line in logs))
                self.assertEqual(self.fetched, [])

    def test_fractional_scale_warns(self):
        self.outputs.parent.mkdir(parents=True)
        self.outputs.write_text(json.dumps([{"name": "outputs", "data": [{"scale": 1.25}]}]))
        ctx, _, _ = self.run_step(preset_with(style={"glass": "frosted"}))
        self.assertTrue(any("scale" in w for w in ctx.warnings))

    def test_hostile_theme_names_are_refused(self):
        for name in ("../evil", "a/b", "..", "/etc/x"):
            with self.subTest(name=name):
                data = copy.deepcopy(presets.find("dracula-night").data)
                data["style"] = {
                    "kvantum": {"repo": "https://github.com/dracula/gtk", "path": "x", "name": name},
                    "glass": "frosted",
                }
                self.assertTrue(presets.validate(data))  # the schema refuses it first
                preset = Preset(data, "user")  # and the step still refuses it on its own
                with self.assertRaises(UnsafePathError):
                    style.StyleStep().touched_paths(preset)
                checkout = mock.Mock(side_effect=AssertionError("must not download"))
                with self.assertRaises(UnsafePathError):
                    self.run_step(preset, checkout=checkout)
                self.assertFalse(Path(self.tmp.name, "evil").exists())
                self.assertFalse(self.kvantum.exists())


def glass_data(level, transparency=None, palette=None, **sections) -> dict:
    """Nord Minimal (dark) with ``level`` glass and the given sections in place of its own."""
    data = copy.deepcopy(presets.find("nord-minimal").data)
    data["style"] = {"glass": level, **({"transparency": transparency} if transparency is not None else {})}
    if palette is not None:
        data["palette"] = dict(palette)
    for name, value in sections.items():
        data[name] = value
    return data


class CompanionTests(unittest.TestCase):
    def test_off_changes_nothing(self):
        data = presets.find("nord-minimal").data
        self.assertIs(glass.with_companions(data), data)
        self.assertIs(glass.with_companions(dict(data, style={"glass": "off"}))["style"]["glass"], "off")
        off = glass_data("off", 60, effects={"blur": False}, terminal={"opacity": 1.0})
        self.assertIs(glass.with_companions(off), off)

    def test_frosted_turns_blur_on_and_lowers_opacity(self):
        data = glass_data(
            "frosted",
            effects={"blur": False, "blur_strength": 3},
            panel_style={"radius": 8},
            terminal={"opacity": 0.95, "blur": False},
        )
        before = copy.deepcopy(data)
        result = glass.with_companions(data)
        self.assertEqual(data, before)  # the input is never changed
        self.assertEqual(result["effects"], {"blur": True, "blur_strength": 10, "blur_noise": 5})
        self.assertEqual(result["panel_style"], {"radius": 8, "opacity": 0.75})  # 1 - 25%
        self.assertEqual(result["terminal"], {"opacity": 0.85, "blur": True})  # 1 - 0.6 * 25%
        self.assertEqual(schema.validate(result), [])

    def test_glass_keeps_the_blur_light(self):
        strong = glass_data(
            "glass",
            effects={"blur": False, "blur_strength": 13, "blur_noise": 9},
            panel_style={"opacity": 0.9},
            terminal={"opacity": 0.95, "blur": False},
        )
        result = glass.with_companions(strong)
        self.assertEqual(result["effects"], {"blur": True, "blur_strength": 4, "blur_noise": 2})
        self.assertEqual(result["panel_style"], {"opacity": 0.6})  # 1 - 40%
        self.assertEqual(result["terminal"], {"opacity": 0.76, "blur": True})  # 1 - 0.6 * 40%
        weak = glass.with_companions(glass_data("glass", effects={"blur": True, "blur_strength": 1, "blur_noise": 0}))
        self.assertEqual(weak["effects"], {"blur": True, "blur_strength": 2, "blur_noise": 0})
        # Left out, the steps would use 10 and 4: too strong for glass, so they are written.
        missing = glass.with_companions(glass_data("glass", effects={"open_close": "fade"}))
        self.assertEqual(missing["effects"], {"open_close": "fade", "blur": True, "blur_strength": 4, "blur_noise": 2})
        # A preset's own strong blur gives way too: Everforest Woods' 8 read as frosted at 70 %.
        everforest = copy.deepcopy(presets.find("everforest-woods").data)
        everforest["style"] = {"glass": "glass", "transparency": 70}
        self.assertEqual(glass.with_companions(everforest)["effects"]["blur_strength"], 4)
        within = glass_data("glass", effects={"blur": True, "blur_strength": 3, "blur_noise": 1})
        self.assertEqual(glass.with_companions(within)["effects"], within["effects"])

    def test_clear_leaves_blur_alone(self):
        data = glass_data(
            "clear",
            effects={"blur": False, "blur_strength": 13, "blur_noise": 9},
            panel_style={"opacity": 0.9},
            terminal={"opacity": 0.95, "blur": False},
        )
        result = glass.with_companions(data)
        self.assertEqual(result["effects"], data["effects"])
        self.assertEqual(result["panel_style"], {"opacity": 0.5})  # 1 - 50%
        self.assertEqual(result["terminal"], {"opacity": 0.7, "blur": False})  # 1 - 0.6 * 50%, blur left as it is

    def test_limits_follow_the_transparency(self):
        for level, transparency, palette, panel, terminal in (
            ("frosted", None, LIGHT, 0.82, 0.89),  # light palettes default to less
            ("glass", None, LIGHT, 0.7, 0.82),
            ("clear", None, LIGHT, 0.62, 0.77),
            ("glass", 70, None, 0.35, 0.58),  # panels never go below 0.35
            ("clear", 66, LIGHT, 0.35, 0.6),  # set on purpose: kept in light mode too
            ("frosted", 10, None, 0.85, 0.9),  # the steps' own defaults already fit
        ):
            with self.subTest(level=level, transparency=transparency, light=palette is not None):
                data = glass_data(level, transparency, palette, panel_style={"radius": 4}, terminal={"padding": 4})
                result = glass.with_companions(data)
                self.assertEqual(result["panel_style"].get("opacity", 0.85), panel)
                self.assertEqual(result["terminal"].get("opacity", 0.9), terminal)
                self.assertEqual(schema.validate(result), [])
        self.assertEqual(glass.companion_limits(100), {"panel_opacity": 0.35, "terminal_opacity": 0.5})

    def test_stronger_or_clearer_choices_are_kept(self):
        for level in ("frosted", "subtle"):
            data = glass_data(
                level,
                effects={"blur": True, "blur_strength": 14, "blur_noise": 9},
                panel_style={"opacity": 0.4},
                terminal={"opacity": 0.5, "blur": True},
            )
            self.assertIs(glass.with_companions(data), data)

    def test_only_existing_sections_change(self):
        data = copy.deepcopy(presets.find("nord-minimal").data)
        for section in ("effects", "panel_style", "terminal"):
            data.pop(section, None)
        for level in ("frosted", "glass", "clear"):
            data["style"] = {"glass": level, "transparency": 70}
            self.assertIs(glass.with_companions(data), data)

    def test_applying_twice_changes_nothing_more(self):
        for preset_id in ("catppuccin-anime", "glass-night", "crimson-moon", "rose-pine-dawn"):
            for level in ("subtle", "frosted", "glass", "clear"):
                for transparency in (None, 10, 37, 70):
                    with self.subTest(preset=preset_id, level=level, transparency=transparency):
                        data = copy.deepcopy(presets.find(preset_id).data)
                        data["style"] = {**data.get("style", {}), "glass": level}
                        if transparency is not None:
                            data["style"]["transparency"] = transparency
                        once = glass.with_companions(data)
                        self.assertIs(glass.with_companions(once), once)
                        self.assertEqual(schema.validate(once), [])

    def test_subtle_is_frosted(self):
        sections = {"effects": {"blur": False}, "panel_style": {"radius": 3}, "terminal": {"blur": False}}
        subtle = glass.with_companions(glass_data("subtle", **copy.deepcopy(sections)))
        frosted = glass.with_companions(glass_data("frosted", **copy.deepcopy(sections)))
        self.assertEqual(subtle["style"], {"glass": "subtle"})  # the file's own word stays
        self.assertEqual(
            {k: v for k, v in subtle.items() if k != "style"}, {k: v for k, v in frosted.items() if k != "style"}
        )

    def test_junk_values_do_not_crash(self):
        for level, strength, noise in (("frosted", 10, 5), ("glass", 4, 2)):
            data = {
                "style": {"glass": level},
                "effects": {"blur_strength": "lots", "blur_noise": math.nan},
                "panel_style": {"opacity": None},
                "terminal": {"opacity": [1]},
            }
            result = glass.with_companions(data)
            self.assertEqual(result["effects"]["blur_strength"], strength)  # junk is replaced
            self.assertEqual(result["effects"]["blur_noise"], noise)
            self.assertEqual(result["panel_style"]["opacity"], 0.75 if level == "frosted" else 0.6)
            self.assertIs(glass.with_companions(result), result)
        for bad in ({"style": "frosted"}, {"style": {"glass": "../x"}}, {"style": {"glass": ["frosted"]}}):
            self.assertIs(glass.with_companions(bad), bad)
        odd = {"style": {"glass": "glass", "transparency": "lots"}, "palette": {"base": 7}, "terminal": {}}
        self.assertEqual(glass.with_companions(odd)["terminal"], {"blur": True, "opacity": 0.76})  # dark default

    def test_plan_and_customized_agree(self):
        """The GUI only applies a plan made for exactly the settings on screen."""
        base = presets.find("nord-minimal")
        for overrides in (
            {},
            {"style": {"glass": "frosted"}},
            {"style": {"glass": "subtle"}, "terminal": {"opacity": 1.0}},
            {"style": {"glass": "glass", "transparency": 55}},
            {"style": {"glass": "clear"}, "effects": {"blur": False}},
        ):
            with self.subTest(overrides=overrides):
                effective = presets.customized(base, overrides)
                plan = engine.make_plan(effective, check_packages=False)
                self.assertEqual(plan.preset.data, effective.data)
        effective = presets.customized(base, {"style": {"glass": "glass"}})
        self.assertEqual(effective.section("terminal")["opacity"], 0.76)
        self.assertEqual(effective.section("effects")["blur_strength"], 4)  # Nord's own 6 is too strong for glass
        self.assertTrue(effective.section("effects")["blur"])
        self.assertNotIn("panel_style", effective.data)
        self.assertIn("kvantum", engine.make_plan(effective, check_packages=False).canonical_packages)
        clear = presets.customized(base, {"style": {"glass": "clear", "transparency": 70}, "effects": {"blur": False}})
        self.assertFalse(clear.section("effects")["blur"])
        self.assertEqual(clear.section("terminal")["opacity"], 0.58)

    def test_plan_fills_in_companions_for_a_preset_file(self):
        data = copy.deepcopy(presets.find("nord-minimal").data)
        data["style"] = {"glass": "glass", "transparency": 60}
        plan = engine.make_plan(Preset(data, "user"), check_packages=False)
        self.assertEqual(plan.preset.section("terminal")["opacity"], 0.64)
        self.assertEqual(data["style"], {"glass": "glass", "transparency": 60})
        self.assertNotIn("opacity", presets.find("nord-minimal").section("panel_style"))

    def test_builtin_presets_already_fit_their_look(self):
        """What a preset file says is what gets applied: glass changes nothing in them."""
        for preset in presets.load_all(include_user=False):
            with self.subTest(preset=preset.id):
                self.assertIs(glass.with_companions(preset.data), preset.data)
        looks = {p.id: glass.level_of(p.data) for p in presets.load_all(include_user=False)}
        self.assertEqual(looks["glass-night"], "glass")
        self.assertEqual(looks["glass-day"], "frosted")
        self.assertEqual(looks["crimson-moon"], "frosted")


class ModeSwitchTests(unittest.TestCase):
    def test_glass_survives_a_switch_to_light(self):
        preset = presets.find("catppuccin-anime")
        light = generate.mode_variant(preset.palette.colors, False)
        switched = presets.customized(preset, {"palette": light, "style": {"glass": "frosted"}})
        self.assertEqual(switched.section("style"), {"glass": "frosted"})  # the dark theme goes, glass stays
        names = {p.name for p in style.StyleStep().touched_paths(switched)}
        self.assertIn(glass.GENERATED_NAME, names)
        self.assertNotIn("catppuccin-mocha-mauve-glass", names)
        self.assertEqual(style.StyleStep().packages(switched), ["kvantum"])

    def test_a_preset_with_glass_keeps_it_in_light_mode(self):
        data = copy.deepcopy(presets.find("catppuccin-anime").data)
        data["style"]["glass"] = "subtle"
        preset = Preset(data, "user")
        light = presets.customized(preset, {"palette": generate.mode_variant(preset.palette.colors, False)})
        self.assertEqual(light.section("style"), {"glass": "subtle"})
        back = presets.customized(preset, {"palette": {"accent": "#ff00ff"}})
        self.assertEqual(back.section("style")["kvantum"]["name"], "catppuccin-mocha-mauve")

    def test_a_theme_picked_on_purpose_stays(self):
        preset = presets.find("catppuccin-anime")
        light = generate.mode_variant(preset.palette.colors, False)
        own = {"palette": light, "style": dict(preset.data["style"], glass="frosted")}
        self.assertIn("kvantum", presets.customized(preset, own).section("style"))
        self.assertNotIn("kvantum", presets.customized(preset, {"palette": light}).section("style"))

    def test_a_breeze_preset_keeps_glass(self):
        preset = presets.customized(presets.find("nord-minimal"), {"style": {"glass": "frosted"}})
        light = presets.customized(preset, {"palette": generate.mode_variant(preset.palette.colors, False)})
        self.assertEqual(light.section("style"), {"glass": "frosted"})

    def test_transparency_follows_a_switch_to_light(self):
        preset = presets.find("catppuccin-anime")
        light = generate.mode_variant(preset.palette.colors, False)
        chosen = presets.customized(preset, {"palette": light, "style": {"glass": "glass", "transparency": 45}})
        self.assertEqual(chosen.section("style"), {"glass": "glass", "transparency": 45})  # only the theme goes
        self.assertEqual(glass.effective_transparency(chosen.data), 45)  # set on purpose: kept as it is
        default = presets.customized(preset, {"palette": light, "style": {"glass": "glass"}})
        self.assertEqual(glass.effective_transparency(default.data), 30)  # left out: the light default
        dark = presets.customized(preset, {"style": {"glass": "glass"}})
        self.assertEqual(glass.effective_transparency(dark.data), 40)
        self.assertEqual(default.section("terminal")["opacity"], 0.82)
        self.assertEqual(dark.section("terminal")["opacity"], 0.76)

    def test_a_preset_with_transparency_keeps_it_in_light_mode(self):
        data = copy.deepcopy(presets.find("nord-minimal").data)
        data["style"] = {"glass": "clear", "transparency": 62}
        preset = Preset(data, "user")
        light = presets.customized(preset, {"palette": generate.mode_variant(preset.palette.colors, False)})
        self.assertEqual(light.section("style"), {"glass": "clear", "transparency": 62})
        self.assertEqual(glass.effective_transparency(light.data), 62)


class TransparencyTests(unittest.TestCase):
    def test_defaults_per_look_and_palette(self):
        dark, light = presets.find("nord-minimal").data["palette"], dict(LIGHT)
        for level, over_dark, over_light in (("frosted", 25, 18), ("glass", 40, 30), ("clear", 50, 38),
                                             ("subtle", 25, 18), ("off", 0, 0)):  # fmt: skip
            with self.subTest(level=level):
                self.assertEqual(glass.effective_transparency({"palette": dark, "style": {"glass": level}}), over_dark)
                self.assertEqual(
                    glass.effective_transparency({"palette": light, "style": {"glass": level}}), over_light
                )
                self.assertEqual(glass.default_transparency(level, True), over_dark)
                self.assertEqual(glass.default_transparency(level, False), over_light)

    def test_a_set_transparency_wins_in_both_modes(self):
        for dark in (True, False):
            self.assertEqual(glass.effective_transparency({"glass": "clear", "transparency": 22}, dark), 22)
        self.assertEqual(glass.effective_transparency({"glass": "off", "transparency": 22}, True), 0)
        self.assertEqual(glass.effective_transparency({"transparency": 22}, True), 0)  # no glass

    def test_a_style_table_or_preset_data(self):
        style = {"glass": "glass"}
        self.assertEqual(glass.effective_transparency(style, False), 30)
        self.assertEqual(glass.effective_transparency(style), 40)  # without a palette: dark
        data = {"palette": dict(LIGHT), "style": style}
        self.assertEqual(glass.effective_transparency(data), 30)  # the palette decides
        self.assertEqual(glass.effective_transparency(data, True), 40)  # unless told
        self.assertEqual(glass.effective_transparency(presets.find("glass-day").data), 18)
        self.assertEqual(glass.effective_transparency(presets.find("glass-night").data), 40)
        self.assertEqual(glass.effective_transparency(presets.find("nord-minimal").data), 0)

    def test_odd_values_fall_back_or_are_kept_within_range(self):
        for value, expected in ((5, 10), (99, 70), (33.6, 34), (True, 40), ("50", 40), (None, 40), (math.nan, 40),
                                (-math.inf, 40), ([50], 40)):  # fmt: skip
            with self.subTest(value=value):
                self.assertEqual(
                    glass.effective_transparency({"glass": "glass", "transparency": value}, True), expected
                )
        for odd in (None, [], "glass", {"style": "glass"}, {"style": {"glass": ["glass"]}}, {"palette": None}):
            self.assertEqual(glass.effective_transparency(odd), 0)

    def test_level_of_maps_older_names(self):
        for value, expected in (("subtle", "frosted"), ("frosted", "frosted"), ("glass", "glass"), ("clear", "clear"),
                                ("off", "off"), ("Glass", "off"), ("../x", "off"), (["glass"], "off"), (None, "off")):  # fmt: skip
            with self.subTest(value=value):
                self.assertEqual(glass.level_of({"style": {"glass": value}}), expected)
                self.assertEqual(glass.canonical(value), expected)
        self.assertEqual(glass.level_of({}), "off")
        self.assertEqual(glass.level_of({"style": None}), "off")

    def test_describe(self):
        self.assertEqual(glass.describe("glass", 40), "glass look, 40% see-through")
        self.assertEqual(glass.describe("subtle", 25), "frosted look, 25% see-through")
        self.assertEqual(glass.describe("off", 0), "solid")
        self.assertEqual(
            glass.describe("clear", 50, True), "clear look, 50% see-through, the window in use stays solid"
        )
        self.assertEqual(glass.describe("off", 0, True), "solid")


class FocusedSolidTests(unittest.TestCase):
    """Whether the window in use stays solid: the look's default unless style.focused_solid says."""

    def test_defaults_per_look(self):
        for level, expected in (("frosted", True), ("subtle", True), ("glass", True), ("clear", True), ("off", False),
                                ("nonsense", False)):  # fmt: skip
            with self.subTest(level=level):
                self.assertEqual(glass.default_focused_solid(level), expected)
                self.assertEqual(glass.effective_focused_solid({"glass": level}), expected)
                self.assertEqual(glass.effective_focused_solid({"style": {"glass": level}}), expected)

    def test_a_set_value_wins_while_there_is_glass(self):
        for level in ("frosted", "glass", "clear"):
            for value in (True, False):
                with self.subTest(level=level, value=value):
                    style = {"glass": level, "focused_solid": value}
                    self.assertEqual(glass.effective_focused_solid(style), value)
                    self.assertEqual(glass.effective_focused_solid({"palette": dict(LIGHT), "style": style}), value)
        self.assertFalse(glass.effective_focused_solid({"glass": "off", "focused_solid": True}))
        self.assertFalse(glass.effective_focused_solid({"focused_solid": True}))  # no glass
        for odd in (None, [], {"style": "glass"}, {"style": {"glass": ["glass"], "focused_solid": True}}):
            self.assertFalse(glass.effective_focused_solid(odd))
        for junk in ("yes", 1, 0, None, [True]):  # not a bool: the look's default
            self.assertTrue(glass.effective_focused_solid({"glass": "glass", "focused_solid": junk}), junk)

    def test_presets_with_glass_keep_the_window_in_use_solid(self):
        for preset_id in ("glass-day", "glass-night", "crimson-moon"):
            with self.subTest(preset=preset_id):
                self.assertTrue(glass.effective_focused_solid(presets.find(preset_id).data))
        self.assertFalse(glass.effective_focused_solid(presets.find("nord-minimal").data))

    def test_it_survives_a_switch_to_light(self):
        preset = presets.find("catppuccin-anime")
        light = generate.mode_variant(preset.palette.colors, False)
        chosen = presets.customized(preset, {"palette": light, "style": {"glass": "glass", "focused_solid": False}})
        self.assertEqual(chosen.section("style"), {"glass": "glass", "focused_solid": False})  # only the theme goes
        self.assertFalse(glass.effective_focused_solid(chosen.data))

    def test_it_changes_no_companion(self):
        sections = {"effects": {"blur": False}, "panel_style": {"opacity": 1.0}, "terminal": {"opacity": 1.0}}
        for level in ("frosted", "glass", "clear"):
            with self.subTest(level=level):
                plain = glass.with_companions({"style": {"glass": level}, **copy.deepcopy(sections)})
                for value in (True, False):
                    data = {"style": {"glass": level, "focused_solid": value}, **copy.deepcopy(sections)}
                    focused = glass.with_companions(data)
                    self.assertEqual(
                        {k: v for k, v in focused.items() if k != "style"},
                        {k: v for k, v in plain.items() if k != "style"},
                    )


class ReadBackTests(unittest.TestCase):
    """A saved desktop gets the look, transparency and focus back from the theme Plasmify wrote."""

    # A blur strength the effects step leaves for each look (frosted 10 or more, glass 7 or less).
    STRENGTH = {"frosted": 12, "glass": 3, "clear": 9}

    def test_every_look_round_trips(self):
        for level in ("frosted", "glass", "clear"):
            for dark in (True, False):
                for transparency in (None, 10, 33, 52, 70, glass.default_transparency(level, dark)):
                    for focused in (None, True, False):
                        with self.subTest(level=level, dark=dark, transparency=transparency, focused=focused):
                            text = glass.glass_kvconfig(THEME, level, dark, transparency, focused)
                            expected: dict = {"glass": level}
                            if transparency not in (None, glass.default_transparency(level, dark)):
                                expected["transparency"] = transparency
                            if focused not in (None, glass.FOCUSED_SOLID):
                                expected["focused_solid"] = focused
                            found = glass.look_of_kvconfig(text, dark, self.STRENGTH[level])
                            self.assertEqual(found, expected)
                            # What is read back writes the same theme again.
                            again = glass.glass_kvconfig(
                                THEME, found["glass"], dark, found.get("transparency"), found.get("focused_solid")
                            )
                            self.assertEqual(again, text)

    def test_without_the_blur_strength_the_closer_default_decides(self):
        for level in ("frosted", "glass", "clear"):
            for dark in (True, False):
                for focused in (True, False):
                    with self.subTest(level=level, dark=dark, focused=focused):
                        text = glass.glass_kvconfig(THEME, level, dark, None, focused)
                        expected = {"glass": level}
                        if focused != glass.FOCUSED_SOLID:
                            expected["focused_solid"] = focused
                        self.assertEqual(glass.look_of_kvconfig(text, dark), expected)
                        self.assertEqual(glass.look_of_kvconfig(text, dark, "lots"), expected)
        text = glass.glass_kvconfig(THEME, "frosted", True, 30)  # closer to frosted's 25 than glass's 40
        self.assertEqual(glass.look_of_kvconfig(text, True), {"glass": "frosted", "transparency": 30})
        self.assertEqual(glass.look_of_kvconfig(text, True, 8), {"glass": "frosted", "transparency": 30})  # 5 to 9
        self.assertEqual(glass.look_of_kvconfig(text, True, 5), {"glass": "frosted", "transparency": 30})
        # Glass keeps blur at 4 or less.
        self.assertEqual(glass.look_of_kvconfig(text, True, 4), {"glass": "glass", "transparency": 30})
        text = glass.glass_kvconfig(THEME, "frosted", True, 36)
        # A guess without the blur strength.
        self.assertEqual(glass.look_of_kvconfig(text, True), {"glass": "glass", "transparency": 36})
        self.assertEqual(glass.look_of_kvconfig(text, True, 10), {"glass": "frosted", "transparency": 36})

    def test_a_negative_value_is_no_longer_only_frosted(self):
        # Each look with the window in use solid, and how see-through the windows behind it are.
        for text, dark, strength, expected in (
            ("blurring=false\nreduce_window_opacity=-50", True, None, {"glass": "clear"}),
            ("blurring=false\nreduce_window_opacity=-62", False, None, {"glass": "clear", "transparency": 62}),
            ("blurring=false\nreduce_window_opacity=50", True, None, {"glass": "clear", "focused_solid": False}),
            ("blurring=true\nreduce_window_opacity=-40", True, 3, {"glass": "glass"}),
            ("blurring=true\nreduce_window_opacity=-55", True, 3, {"glass": "glass", "transparency": 55}),
            ("blurring=true\nreduce_window_opacity=-55", True, 12, {"glass": "frosted", "transparency": 55}),
            ("blurring=true\nreduce_window_opacity=-30", False, None, {"glass": "glass"}),
        ):  # fmt: skip
            with self.subTest(text=text, dark=dark):
                self.assertEqual(glass.look_of_kvconfig(f"[%General]\n{text}\n", dark, strength), expected)

    def test_older_themes(self):
        # The old subtle look: only inactive windows, 15 % see-through. Frosted keeps that now.
        subtle = "[%General]\nreduce_window_opacity=-15\n"
        self.assertEqual(glass.look_of_kvconfig(subtle, True), {"glass": "frosted", "transparency": 15})
        # Frosted and glass themes from before focused_solid had the window in use see-through.
        old_frosted = "[%General]\nblurring=true\nreduce_window_opacity=18\n"
        self.assertEqual(
            glass.look_of_kvconfig(old_frosted, True, 11),
            {"glass": "frosted", "transparency": 18, "focused_solid": False},
        )
        # 18 is light frosted.
        self.assertEqual(glass.look_of_kvconfig(old_frosted, False, 11), {"glass": "frosted", "focused_solid": False})

    def test_odd_files(self):
        for text in ("", "junk", "[%General]\nreduce_window_opacity=lots\n", "[Other]\nblurring=false\n"):
            with self.subTest(text=text):
                self.assertEqual(glass.look_of_kvconfig(text, True), {"glass": "frosted"})
        out_of_range = "[%General]\nblurring=false\nreduce_window_opacity=-95\n"
        self.assertEqual(glass.look_of_kvconfig(out_of_range, True), {"glass": "clear", "transparency": 70})
        out_of_range = "[%General]\nblurring=false\nreduce_window_opacity=95\n"
        self.assertEqual(
            glass.look_of_kvconfig(out_of_range, True), {"glass": "clear", "transparency": 70, "focused_solid": False}
        )


class SchemaTests(unittest.TestCase):
    def preset(self, **style) -> dict:
        data = copy.deepcopy(presets.find("nord-minimal").data)
        data["style"] = style
        return data

    def test_looks_and_the_older_name_are_valid(self):
        for level in (*schema.GLASS_LEVELS, "subtle"):
            self.assertEqual(schema.validate(self.preset(glass=level)), [], level)

    def test_unknown_looks_are_refused_and_only_the_new_names_offered(self):
        for bad in ("shiny", "Frosted", "", None, ["glass"], {"a": 1}, 1):
            with self.subTest(value=bad):
                errors = schema.validate(self.preset(glass=bad))
                self.assertEqual(len(errors), 1)
                self.assertIn("off, frosted, glass, clear", errors[0])
                self.assertNotIn("subtle", errors[0])

    def test_focused_solid_is_true_or_false(self):
        for value in (True, False):
            self.assertEqual(schema.validate(self.preset(glass="clear", focused_solid=value)), [], value)
        self.assertEqual(schema.validate(self.preset(focused_solid=False)), [])  # kept while glass is off
        for value in (1, 0, "true", "on", None, [True], {"a": 1}):
            with self.subTest(value=value):
                errors = schema.validate(self.preset(glass="glass", focused_solid=value))
                self.assertTrue(any("style.focused_solid" in e and "true or false" in e for e in errors), errors)

    def test_transparency_range(self):
        for value in (10, 40, 70):
            self.assertEqual(schema.validate(self.preset(glass="glass", transparency=value)), [], value)
        for value in (9, 71, 0, -5, 40.0, True, "40", None):
            with self.subTest(value=value):
                errors = schema.validate(self.preset(glass="glass", transparency=value))
                self.assertTrue(any("style.transparency" in e and "10 to 70" in e for e in errors), errors)


class SmallHelperTests(unittest.TestCase):
    def test_template_urls_are_pinned_and_trusted(self):
        for template in (glass.TEMPLATE_DARK, glass.TEMPLATE_LIGHT):
            for extension in ("kvconfig", "svg"):
                url = glass.template_url(template, extension)
                downloads.check_url(url)
                self.assertIn(glass.TEMPLATE_COMMIT, url)
                self.assertIn(f"{template}.{extension}", glass.TEMPLATE_SHA256)
        for bad in (("../x", "svg"), (glass.TEMPLATE_DARK, "sh"), ("catppuccin-mocha-red", "svg")):
            with self.assertRaises(ValueError):
                glass.template_url(*bad)

    def test_levels_match_the_schema(self):
        self.assertEqual(glass.GLASS_LEVELS, schema.GLASS_LEVELS)
        self.assertEqual(glass.LEGACY, schema.GLASS_ALIASES)
        self.assertEqual(glass.TRANSPARENCY_RANGE, schema.TRANSPARENCY)
        self.assertEqual(set(glass.DEFAULT_TRANSPARENCY), set(glass.GLASS_LEVELS) - {"off"})
        self.assertEqual(set(glass.CHOICE_KEYS), set(schema.SCHEMA["style"][0]) - {"kvantum"})
        self.assertEqual(set(glass.LABELS), set(glass.GLASS_LEVELS))
        self.assertEqual(set(glass.BLUR_RANGES), set(glass.BLURRED))
        self.assertLessEqual(set(glass.BLURRED), set(glass.GLASS_LEVELS))
        self.assertTrue(set(glass.LEGACY.values()) <= set(glass.GLASS_LEVELS))
        low, high = glass.TRANSPARENCY_RANGE
        for over_dark, over_light in glass.DEFAULT_TRANSPARENCY.values():
            self.assertTrue(low <= over_light < over_dark <= high)  # light palettes get less

    def test_companion_values_fit_the_schema(self):
        effects = schema.SCHEMA["effects"][0]
        for level, ranges in glass.BLUR_RANGES.items():
            for key, (low, high) in ranges.items():
                with self.subTest(level=level, key=key):
                    check = effects[key][0]
                    self.assertIsNone(check(low))
                    self.assertIsNone(check(high))
        for transparency in range(glass.TRANSPARENCY_RANGE[0], glass.TRANSPARENCY_RANGE[1] + 1):
            limits = glass.companion_limits(transparency)
            data = copy.deepcopy(presets.find("catppuccin-anime").data)
            data["style"] = {"glass": "glass", "transparency": transparency}
            data["terminal"]["opacity"] = limits["terminal_opacity"]
            data.setdefault("panel_style", {})["opacity"] = limits["panel_opacity"]
            self.assertEqual(schema.validate(data), [], transparency)

    def test_fractional_scale(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "kwinoutputconfig.json")
            self.assertFalse(glass.fractional_scale(path))  # missing
            cases = [
                ([{"name": "outputs", "data": [{"scale": 1}, {"scale": 2.0}]}], False),
                ([{"name": "outputs", "data": [{"scale": 1}, {"scale": 1.5}]}], True),
                ([{"name": "setups", "data": [{"scale": 1.5}]}], False),
                ([{"name": "outputs", "data": [{"scale": True}, {"scale": "1.5"}, None, 7]}], False),
                ({"outputs": 1.5}, False),
                ("[[[[", False),
            ]
            for content, expected in cases:
                with self.subTest(content=content):
                    path.write_text(content if isinstance(content, str) else json.dumps(content))
                    self.assertEqual(glass.fractional_scale(path), expected)
            path.write_text("[" * 100_000 + "]" * 100_000)
            self.assertFalse(glass.fractional_scale(path))
            path.write_text(" " * (glass.MAX_OUTPUTS_BYTES + 1))
            self.assertFalse(glass.fractional_scale(path))


if __name__ == "__main__":
    unittest.main()
