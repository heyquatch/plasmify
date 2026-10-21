"""Sharing presets: TOML writing, bundles, capturing the desktop, generating from wallpapers, history."""

import copy
import random
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from plasmify import activity, bundle, capture, generate, presets, schema, tomlwrite

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


def user_dir(tmp: str):
    return mock.patch.multiple("plasmify.bundle", USER_PRESET_DIR=Path(tmp) / "presets"), mock.patch.object(
        presets, "USER_PRESET_DIR", Path(tmp) / "presets"
    )


def own_preset() -> dict:
    data = copy.deepcopy(presets.find("nord-minimal").data)
    data["meta"]["id"] = "my-look"
    data["meta"]["name"] = "My Look"
    data["wallpaper"]["images"] = [{"file": "01-sunset.png", "credit": "My own wallpaper"}]
    return data


class TomlWriterTests(unittest.TestCase):
    def test_round_trip_every_builtin(self):
        for preset in presets.load_all(include_user=False):
            with self.subTest(preset.id):
                self.assertEqual(tomllib.loads(tomlwrite.dumps(preset.data, "header")), preset.data)

    def test_escapes_strings(self):
        data = {"meta": {"name": 'quote " and \\ backslash'}}
        self.assertEqual(tomllib.loads(tomlwrite.dumps(data)), data)


class BundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for patcher in user_dir(self.tmp.name):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.image = self.root / "sunset.png"
        self.image.write_bytes(PNG)

    def tearDown(self):
        self.tmp.cleanup()

    def test_save_export_import_round_trip(self):
        preset = bundle.save_user_preset(own_preset(), {"01-sunset.png": self.image})
        self.assertTrue((preset.asset_dir / "01-sunset.png").exists())
        archive = bundle.export_bundle(preset, self.root / "share")
        self.assertEqual(archive.suffix, ".plasmify")
        with zipfile.ZipFile(archive) as zf:
            self.assertEqual(sorted(zf.namelist()), ["assets/01-sunset.png", "preset.toml"])
        bundle.delete_user_preset(preset)
        imported = bundle.import_file(archive)
        self.assertEqual(imported.data, preset.data)
        self.assertEqual(imported.source, "user")

    def test_bundle_renamed_to_zip_imports(self):
        # GitHub Discussions only takes .zip uploads, so shared presets arrive as .plasmify.zip.
        preset = bundle.save_user_preset(own_preset(), {"01-sunset.png": self.image})
        archive = bundle.export_bundle(preset, self.root / "share")
        renamed = archive.rename(archive.with_name(archive.name + ".zip"))
        bundle.delete_user_preset(preset)
        self.assertEqual(bundle.import_file(renamed).data, preset.data)

    def test_existing_preset_needs_replace(self):
        bundle.save_user_preset(own_preset(), {"01-sunset.png": self.image})
        with self.assertRaises(FileExistsError):
            bundle.save_user_preset(own_preset(), {"01-sunset.png": self.image})
        bundle.save_user_preset(own_preset(), {"01-sunset.png": self.image}, replace=True)

    def test_builtin_id_is_refused(self):
        data = own_preset()
        data["meta"]["id"] = "nord-minimal"
        with self.assertRaises(presets.PresetError):
            bundle.save_user_preset(data, {"01-sunset.png": self.image})

    def make_zip(self, members: dict[str, bytes], toml: str | None = None) -> Path:
        path = self.root / "evil.plasmify"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("preset.toml", toml if toml is not None else tomlwrite.dumps(own_preset()))
            for name, content in members.items():
                zf.writestr(name, content)
        return path

    def test_hostile_bundles_are_rejected(self):
        cases = {
            "traversal": {"assets/../../evil.png": PNG},
            "unexpected file": {"assets/01-sunset.png": PNG, "install.sh": b"rm -rf ~"},
            "nested folder": {"assets/01-sunset.png": PNG, "assets/sub/x.png": PNG},
            "missing asset": {},
            "not an image": {"assets/01-sunset.png": b"#!/bin/sh\necho hi"},
            "script name": {"assets/01-sunset.png": PNG, "assets/run.sh": b"x"},
        }
        for label, members in cases.items():
            with self.subTest(label), self.assertRaises(presets.PresetError):
                bundle.import_file(self.make_zip(members))
        self.assertFalse((self.root / "presets" / "my-look.toml").exists())

    def test_invalid_toml_in_bundle(self):
        with self.assertRaises(presets.PresetError):
            bundle.import_file(self.make_zip({"assets/01-sunset.png": PNG}, toml="[meta]\nid = 'x'\nevil = 1\n"))

    def test_plain_toml_with_local_files_needs_the_bundle(self):
        path = self.root / "my-look.toml"
        path.write_text(tomlwrite.dumps(own_preset()))
        with self.assertRaises(presets.PresetError):
            bundle.import_file(path)


class SchemaFileTests(unittest.TestCase):
    def test_file_names_cannot_escape(self):
        data = own_preset()
        for bad in ("../x.png", "a/b.png", "x.sh", ".hidden.png"):
            data["wallpaper"]["images"][0]["file"] = bad
            with self.subTest(bad):
                self.assertTrue(schema.validate(data))

    def test_url_and_file_are_exclusive(self):
        data = own_preset()
        data["wallpaper"]["images"][0]["url"] = "https://github.com/x.png"
        self.assertTrue(any("exactly one" in e for e in schema.validate(data)))

    def test_builtins_have_no_local_files(self):
        for preset in presets.load_all(include_user=False):
            self.assertEqual(schema.local_files(preset.data), [], preset.id)


class CaptureTests(unittest.TestCase):
    APPLETSRC = """[Containments][1]
plugin=org.kde.plasma.folder
wallpaperplugin=org.kde.image

[Containments][1][Wallpaper][org.kde.image][General]
Image=file://{image}

[Containments][2]
location=3
plugin=org.kde.panel

[Containments][3]
location=4
plugin=org.kde.panel

[Containments][3][Applets][4]
plugin=org.kde.plasma.icontasks
"""

    def test_layout_and_wallpaper_from_appletsrc(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = Path(tmp) / "My Sunset!.png"
            image.write_bytes(PNG)
            path = Path(tmp) / "appletsrc"
            path.write_text(self.APPLETSRC.format(image=image))
            groups = capture.read_appletsrc(path)
            notes: list[str] = []
            self.assertEqual(capture.read_layout(groups, notes), {"name": "topbar-dock"})
            section, files = capture.read_wallpapers(groups, notes)
            self.assertEqual(list(files), ["01-My_Sunset.png"])
            self.assertEqual(section["images"][0]["file"], "01-My_Sunset.png")

    def test_palette_from_kde_colours(self):
        values = {
            ("Colors:Window", "BackgroundNormal"): "30,30,46",
            ("Colors:Window", "ForegroundNormal"): "205,214,244",
            ("Colors:Selection", "BackgroundNormal"): "203,166,247",
        }
        runner = mock.Mock()
        runner.kread.side_effect = lambda file, group, key, default="": values.get((group, key), "")
        palette = capture.read_palette(runner)
        self.assertEqual(palette["base"], "#1e1e2e")
        self.assertEqual(palette["accent"], "#cba6f7")
        self.assertFalse(schema.validate({**own_preset(), "palette": palette}))

    def test_slugify(self):
        self.assertEqual(capture.slugify("  Café Crème / Lofi!! "), "cafe-creme-lofi")
        self.assertEqual(capture.slugify("ı İ ß"), "i-i-ss")
        self.assertEqual(capture.slugify("!!!"), "my-desktop")


class GenerateTests(unittest.TestCase):
    def test_dominant_colours(self):
        pixels = [(200, 40, 40)] * 300 + [(20, 20, 30)] * 700
        swatches = generate.dominant_colours(pixels, k=4)
        self.assertGreater(swatches[0].weight, 0.6)

    def test_palettes_are_always_readable(self):
        rng = random.Random(7)  # noqa: S311 (deterministic test data, not security related)
        for trial in range(40):
            pixels = [(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(400)]
            swatches = generate.dominant_colours(pixels, k=6, rounds=4)
            for dark in (True, False):
                with self.subTest(trial=trial, dark=dark):
                    accent = generate.accent_candidates(swatches, dark)[0]
                    p = generate.build_palette(swatches, accent, dark, rng.uniform(0.4, 1.3))
                    self.assertGreaterEqual(generate.contrast(p["text"], p["base"]), 7.0)
                    self.assertGreaterEqual(generate.contrast(p["subtext"], p["base"]), 4.5)
                    self.assertGreaterEqual(generate.contrast(p["accent"], p["base"]), 3.0)

    def test_muted_image_offers_enough_accents(self):
        swatches = generate.dominant_colours([(200, 160, 120)] * 50 + [(40, 40, 50)] * 50, k=3)
        self.assertGreaterEqual(len(generate.accent_candidates(swatches, True)), 4)

    def test_folder_colour_follows_hue(self):
        self.assertEqual(generate.nearest_folder_colour("#db94b2"), "pink")  # pastel pink, not brown
        self.assertEqual(generate.nearest_folder_colour("#89b4fa"), "blue")
        self.assertEqual(generate.nearest_folder_colour("#a6e3a1"), "green")
        self.assertEqual(generate.nearest_folder_colour("#fab387"), "orange")
        self.assertEqual(generate.nearest_folder_colour("#9a9a9a"), "grey")

    def test_greyscale_image_still_has_accents(self):
        swatches = generate.dominant_colours([(v, v, v) for v in range(0, 256, 2)], k=4)
        self.assertEqual(len(generate.accent_candidates(swatches, True)), 5)

    def test_build_preset_is_valid(self):
        swatches = generate.dominant_colours([(30, 30, 60)] * 50 + [(240, 120, 60)] * 50, k=3)
        palette = generate.build_palette(swatches, generate.accent_candidates(swatches, True)[0], True)
        data, files = generate.build_preset(
            name="Sunset",
            description="x",
            images=[Path("a b.jpg")],
            palette=palette,
            dark=True,
            layout="bottom-panel",
            mood="calm",
            fonts="Noto Sans + Hack",
            preset_id="sunset",
        )
        self.assertEqual(schema.validate(data), [])
        self.assertEqual(list(files), ["01-a_b.jpg"])


class ActivityTests(unittest.TestCase):
    def test_record_and_read(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(activity, "LOG_FILE", Path(tmp) / "log.jsonl"):
            activity.set_context("nord-minimal")
            activity.record("download", "https://github.com/x.png", Path.home() / "a.png", size=10, sha256="ab")
            activity.record("download", "https://evil.example/x", "", ok=False, error="refused")
            activity.set_context("")
            newest, oldest = activity.entries()
            self.assertFalse(newest.ok)
            self.assertEqual(oldest.preset, "nord-minimal")
            self.assertEqual(oldest.detail, "~/a.png")
            self.assertEqual(oldest.host, "github.com")
            activity.clear()
            self.assertEqual(activity.entries(), [])


if __name__ == "__main__":
    unittest.main()
