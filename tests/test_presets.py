import copy
import json
import tomllib
import unittest

from plasmify import presets, templates
from plasmify.engine import make_plan
from plasmify.steps import STEP_IDS
from plasmify.steps.colors import color_scheme, scheme_name
from plasmify.steps.shell import fastfetch_modules
from plasmify.steps.terminal import terminal_vars


class BuiltinPresetTests(unittest.TestCase):
    def setUp(self):
        self.presets = presets.load_all(include_user=False)

    def test_all_builtin_presets_load(self):
        self.assertGreaterEqual(len(self.presets), 5)

    def test_ids_are_unique(self):
        ids = [p.id for p in self.presets]
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_preset_has_a_plan(self):
        for preset in self.presets:
            with self.subTest(preset=preset.id):
                plan = make_plan(preset, check_packages=False)
                self.assertTrue(plan.steps)
                self.assertTrue({s.id for s in plan.steps} <= set(STEP_IDS))

    def test_templates_render_for_every_preset(self):
        for preset in self.presets:
            with self.subTest(preset=preset.id):
                tvars = terminal_vars(preset)
                for name in ("alacritty.toml", "kitty.conf", "konsole.colorscheme", "konsole.profile"):
                    self.assertNotIn("{{", templates.render(name, tvars))
                tomllib.loads(templates.render("alacritty.toml", tvars))

                modules = json.dumps(fastfetch_modules({}, preset.palette, False))
                pvars = dict(preset.palette.as_template_vars(), logo='{"type": "auto"}', modules=modules)
                for prompt in ("powerline", "minimal"):
                    tomllib.loads(templates.render(f"starship-{prompt}.toml", pvars))
                fastfetch = templates.render("fastfetch.jsonc", pvars)
                json.loads("\n".join(line for line in fastfetch.splitlines() if not line.startswith("//")))

    def test_fastfetch_header_is_valid_json_when_enabled(self):
        preset = self.presets[0]
        modules = json.dumps(fastfetch_modules({}, preset.palette, True))
        pvars = dict(preset.palette.as_template_vars(), logo='{"type": "auto"}', modules=modules)
        text = templates.render("fastfetch.jsonc", pvars)
        parsed = json.loads("\n".join(line for line in text.splitlines() if not line.startswith("//")))
        self.assertEqual(parsed["modules"][1]["type"], "title")

    def test_custom_fastfetch_modules_keep_their_order(self):
        preset = self.presets[0]

        def types(modules):
            return [m if isinstance(m, str) else m["type"] for m in modules]

        custom = {"fastfetch_modules": ["cpu", "break", "memory", "colors"]}
        self.assertEqual(types(fastfetch_modules(custom, preset.palette, False)), ["cpu", "break", "memory", "colors"])
        # "Include user@host" works with a custom list too.
        with_host = fastfetch_modules({"fastfetch_modules": ["break", "cpu"]}, preset.palette, True)
        self.assertEqual(types(with_host), ["break", "title", "separator", "cpu"])

    def test_color_scheme_is_complete(self):
        preset = self.presets[0]
        text = color_scheme(preset.palette, scheme_name(preset), preset.name)
        for group in ("Window", "View", "Button", "Selection", "Tooltip", "Header", "Complementary"):
            self.assertIn(f"[Colors:{group}]", text)
        self.assertIn(f"ColorScheme={scheme_name(preset)}", text)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        path = presets.builtin_paths()[0]
        with path.open("rb") as fh:
            self.valid = tomllib.load(fh)

    def broken(self, mutate):
        data = copy.deepcopy(self.valid)
        mutate(data)
        return presets.validate(data)

    def test_valid_preset_has_no_errors(self):
        self.assertEqual(presets.validate(self.valid), [])

    def test_missing_palette_key(self):
        errors = self.broken(lambda d: d["palette"].pop("accent"))
        self.assertIn("palette.accent is required", errors)

    def test_bad_colour(self):
        errors = self.broken(lambda d: d["palette"].__setitem__("base", "blue"))
        self.assertTrue(any("palette.base" in e for e in errors))

    def test_http_sources_are_rejected(self):
        errors = self.broken(lambda d: d["cursor"].__setitem__("url", "http://example.com/c.zip"))
        self.assertTrue(any("https" in e for e in errors))

    def test_unknown_layout(self):
        errors = self.broken(lambda d: d["layout"].__setitem__("name", "spiral"))
        self.assertTrue(any("layout.name" in e for e in errors))

    def test_bad_id(self):
        errors = self.broken(lambda d: d["meta"].__setitem__("id", "../evil"))
        self.assertTrue(any("meta.id" in e for e in errors))


if __name__ == "__main__":
    unittest.main()


class LoginShellTests(unittest.TestCase):
    def test_terminals_start_the_login_shell(self):
        from unittest import mock

        from plasmify.steps import terminal

        preset = presets.load_all(include_user=False)[0]
        with mock.patch.object(terminal, "login_shell", return_value="/usr/bin/fish"):
            tvars = terminal.terminal_vars(preset)
        self.assertIn("Command=/usr/bin/fish", templates.render("konsole.profile", tvars))
        alacritty = tomllib.loads(templates.render("alacritty.toml", tvars))
        self.assertEqual(alacritty["terminal"]["shell"]["program"], "/usr/bin/fish")

    def test_no_login_shell_means_no_override(self):
        from unittest import mock

        from plasmify.steps import terminal

        preset = presets.load_all(include_user=False)[0]
        with mock.patch.object(terminal, "login_shell", return_value=None):
            tvars = terminal.terminal_vars(preset)
        self.assertNotIn("Command=", templates.render("konsole.profile", tvars))
        self.assertNotIn("terminal", tomllib.loads(templates.render("alacritty.toml", tvars)))
