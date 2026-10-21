"""Terminal logos, panel style, icon/cursor choice and customizations."""

import copy
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from plasmify import blockfont, presets, schema
from plasmify.engine import make_plan
from plasmify.steps.panelstyle import panel_svg
from plasmify.steps.shell import logo_kind, text_logo_lines


def base() -> dict:
    return copy.deepcopy(presets.find("nord-minimal").data)


def with_shell(**shell) -> dict:
    data = base()
    data["shell"].update(shell)
    return data


class BlockFontTests(unittest.TestCase):
    def test_render_has_five_rows(self):
        rows = blockfont.render("Hi 7!")
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(set(r) <= {"█", " "} for r in rows))

    def test_accented_letters_fold(self):
        self.assertTrue(blockfont.supported("Café Crème"))
        self.assertTrue(blockfont.supported("ÇĞİÖŞÜ ğı"))
        self.assertEqual(blockfont.render("Ş"), blockfont.render("S"))

    def test_large_doubles(self):
        self.assertEqual(len(blockfont.render("A", 2)), 10)

    def test_limits(self):
        self.assertFalse(blockfont.supported(""))
        self.assertFalse(blockfont.supported("x" * 13))
        self.assertFalse(blockfont.supported("emoji 🙂"))


class LogoSchemaTests(unittest.TestCase):
    def test_valid_logos(self):
        for shell in (
            {"logo": "distro"},
            {"logo": "builtin", "logo_builtin": "arch_small"},
            {"logo": "text", "logo_text": "HELLO", "logo_size": "large"},
        ):
            with self.subTest(shell):
                self.assertEqual(schema.validate(with_shell(**shell)), [])

    def test_hostile_logo_values(self):
        cases = {
            "newline in text": {"logo": "text", "logo_text": 'A"\n}'},
            "too long text": {"logo": "text", "logo_text": "A" * 13},
            "path as builtin": {"logo": "builtin", "logo_builtin": "../../etc/passwd"},
            "option as builtin": {"logo": "builtin", "logo_builtin": "--config"},
            "logo file escape": {"logo": "image", "logo_file": "../secret.png"},
            "unknown size": {"logo": "text", "logo_text": "A", "logo_size": "huge"},
            "builtin without name": {"logo": "builtin"},
            "image without source": {"logo": "image"},
        }
        for label, shell in cases.items():
            with self.subTest(label):
                self.assertTrue(schema.validate(with_shell(**shell)))

    def test_old_presets_keep_their_logo(self):
        self.assertEqual(logo_kind({"fastfetch_logo": "https://github.com/x.png"}), "image")
        self.assertEqual(logo_kind({}), "distro")

    def test_logo_file_travels_in_bundles(self):
        data = with_shell(logo="image", logo_file="logo.png")
        self.assertIn("logo.png", schema.local_files(data))

    def test_text_logo_gradient(self):
        lines, colours = text_logo_lines("AB", presets.find("nord-minimal").palette, "medium")
        self.assertEqual(len(lines), 5)
        self.assertTrue(all(line.startswith("$") for line in lines))
        self.assertTrue(set(colours) <= {"1", "2", "3", "4", "5"})


class PanelStyleTests(unittest.TestCase):
    def test_svg_has_every_frame_piece(self):
        palette = presets.find("nord-minimal").palette
        for radius in (0, 1, 12, 30):
            with self.subTest(radius=radius):
                # Parsing our own generated SVG in a test, not untrusted input.
                root = ET.fromstring(panel_svg(palette, radius, 0.7, "accent", 4))  # noqa: S314
                ids = {el.get("id") for el in root.iter() if el.get("id")}
                for piece in (
                    "topleft",
                    "top",
                    "topright",
                    "left",
                    "center",
                    "right",
                    "bottomleft",
                    "bottom",
                    "bottomright",
                ):
                    self.assertIn(piece, ids)
                    self.assertIn(f"mask-{piece}", ids)
                for side in ("top", "bottom", "left", "right"):
                    self.assertIn(f"hint-{side}-margin", ids)

    def test_ranges(self):
        data = base()
        data["panel_style"] = {"radius": 31}
        self.assertTrue(schema.validate(data))
        data["panel_style"] = {"radius": 12, "opacity": 0.1}
        self.assertTrue(schema.validate(data))
        data["panel_style"] = {"radius": 12, "opacity": 0.7, "border": "subtle", "padding": 6}
        self.assertEqual(schema.validate(data), [])

    def test_panel_style_step_is_planned(self):
        data = base()
        data["panel_style"] = {"radius": 16}
        plan = make_plan(presets.Preset(data, "user"), check_packages=False)
        self.assertIn("panelstyle", [s.id for s in plan.steps])


class ChoiceTests(unittest.TestCase):
    def test_installed_cursor_needs_no_url(self):
        data = base()
        data["cursor"] = {"name": "breeze_cursors"}
        self.assertEqual(schema.validate(data), [])

    def test_customized_keeps_asset_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            preset = presets.find("nord-minimal")
            custom = presets.customized(preset, {"palette": {"accent": "#ff8800"}}, Path(tmp))
            self.assertEqual(custom.palette.accent, "#ff8800")
            self.assertEqual(custom.asset_dir, Path(tmp))
            self.assertEqual(preset.palette.accent, "#88c0d0")  # the original is untouched

    def test_customized_rejects_invalid_values(self):
        with self.assertRaises(presets.PresetError):
            presets.customized(presets.find("nord-minimal"), {"panel_style": {"radius": 99}})


if __name__ == "__main__":
    unittest.main()
