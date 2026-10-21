"""Restore and clean up, as seen in a VM test of every feature.

Restore put the desktop back but not Plasmify's record of the applied tweaks, so the window and
"plasmify theme" described the newer tweaks, and the next light/dark switch applied them again
(the restored accent was lost). After "plasmify clean" a restore only warned about the colour
scheme, and the clean up menu promised more space than it freed.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from plasmify import backup, engine, presets, record  # noqa: E402
from plasmify.system import Runner  # noqa: E402
from tests.test_preset_switch import ConfigRunner  # noqa: E402

NORD_SCHEME = "[General]\nColorScheme=PlasmifyNordMinimal\n"


class RestoreRecordTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.config = root / "config"
        self.config.mkdir()
        self.tweaks = root / "state" / "applied-tweaks.json"
        self.tweaks.parent.mkdir()
        self.runner = ConfigRunner(self.config)
        for patch in (
            mock.patch.object(record, "TWEAKS_FILE", self.tweaks),
            mock.patch.object(backup, "BACKUP_ROOT", root / "backups"),
            mock.patch.object(engine, "CONFIG_HOME", self.config),
            mock.patch.object(engine, "DATA_HOME", root / "data"),
            mock.patch.object(engine, "PLASMIFY_DATA", root / "data" / "plasmify"),
            mock.patch.object(engine, "WALLPAPER_ROOT", root / "data" / "plasmify" / "wallpapers"),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def write_record(self, data: dict) -> None:
        self.tweaks.write_text(json.dumps(data))

    def snapshot(self, label: str = "Before Nord Minimal") -> backup.Backup:
        return backup.create([self.config / "kdeglobals", self.tweaks], label, backup.BACKUP_ROOT)

    def test_every_apply_backs_up_the_record(self):
        plan = engine.make_plan(presets.find("nord-minimal"), ["colors"], check_packages=False)
        with (
            mock.patch.object(engine, "_run_step", return_value=[]),
            mock.patch.object(engine.system, "restart_shell", return_value=True),
            mock.patch.object(engine.backup, "create", return_value=mock.Mock(id="test")) as create,
        ):
            engine.apply(plan, Runner(dry_run=False), log=lambda _: None)
        self.assertIn(self.tweaks, create.call_args.args[0])

    def test_the_theme_on_screen_gets_its_tweaks_back(self):
        """The VM case: pink accent, then reset, then "restore latest"."""
        (self.config / "kdeglobals").write_text(NORD_SCHEME)
        pink = {"overrides": {"palette": {"accent": "#ff79c6"}}, "custom_dir": None, "steps": ["colors"]}
        dracula = {"overrides": {"palette": {"accent": "#50fa7b"}}, "custom_dir": None}
        self.write_record({"nord-minimal": pink, "dracula-night": dracula})
        before_reset = self.snapshot()
        self.write_record({"nord-minimal": {"overrides": {}, "custom_dir": None, "steps": ["colors"]}})  # the reset
        engine.restore(before_reset, self.runner, log=lambda _: None)
        restored = json.loads(self.tweaks.read_text())
        self.assertEqual(restored["nord-minimal"], pink)
        self.assertNotIn("dracula-night", restored, "only the theme on screen comes back")

    def test_tweaks_of_other_presets_stay_remembered(self):
        """Backups docs: after a restore, applying a theme again brings its tweaks."""
        (self.config / "kdeglobals").write_text("[General]\nColorScheme=BreezeLight\n")
        self.write_record({})
        original = self.snapshot("Before Nord Minimal")
        kept = {"nord-minimal": {"overrides": {"palette": {"accent": "#ff79c6"}}, "custom_dir": None}}
        self.write_record(kept)
        engine.restore(original, self.runner, log=lambda _: None)  # back to no Plasmify theme
        self.assertEqual(json.loads(self.tweaks.read_text()), kept)

    def test_a_theme_without_tweaks_then_loses_the_newer_ones(self):
        (self.config / "kdeglobals").write_text(NORD_SCHEME)
        self.write_record({})
        plain = self.snapshot()
        self.write_record({"nord-minimal": {"overrides": {"fonts": {"ui_size": 11}}, "custom_dir": None}})
        engine.restore(plain, self.runner, log=lambda _: None)
        self.assertEqual(json.loads(self.tweaks.read_text()), {})

    def test_a_backup_from_before_the_record_was_kept_leaves_it_alone(self):
        (self.config / "kdeglobals").write_text(NORD_SCHEME)
        older = backup.create([self.config / "kdeglobals"], "Before Nord Minimal", backup.BACKUP_ROOT)
        now = {"nord-minimal": {"overrides": {"fonts": {"ui_size": 11}}, "custom_dir": None}}
        self.write_record(now)
        engine.restore(older, self.runner, log=lambda _: None)
        self.assertEqual(json.loads(self.tweaks.read_text()), now)

    def test_the_steps_on_screen_are_taken_off_other_presets(self):
        self.write_record({"dracula-night": {"overrides": {}, "steps": ["colors", "icons", "layout"]}})
        record.put_entry("nord-minimal", {"overrides": {}, "steps": ["colors", "icons"]})
        data = json.loads(self.tweaks.read_text())
        self.assertEqual(data["dracula-night"]["steps"], ["layout"])

    def test_cleaned_up_downloads_are_named(self):
        """Restoring a backup of a preset whose downloads "plasmify clean" deleted."""
        data = self.config.parent / "data"
        (self.config / "kdeglobals").write_text("[Icons]\nTheme=Plasmify-nord-minimal\n")
        (self.config / "kcminputrc").write_text("[Mouse]\ncursorTheme=Gone-Cursor\n")
        folder = data / "plasmify" / "wallpapers" / "nord-minimal"
        (self.config / "plasma-org.kde.plasma.desktop-appletsrc").write_text(
            f"[Containments][1][Wallpaper][org.kde.slideshow][General]\nSlidePaths={folder}\n"
        )
        gone = engine.missing_assets(self.runner)
        self.assertIn("the icon theme Plasmify-nord-minimal", gone)
        self.assertIn("the cursor Gone-Cursor", gone)
        self.assertIn("the wallpapers of nord-minimal", gone)
        folder.mkdir(parents=True)
        (data / "icons" / "Plasmify-nord-minimal").mkdir(parents=True)
        self.assertEqual(engine.missing_assets(self.runner), ["the cursor Gone-Cursor"])


class CleanSizeTests(unittest.TestCase):
    """File > Clean up promised 538.4 MB and freed 510.3 MB: the menu counted downloads the desktop
    still used and files two presets share twice."""

    def test_the_estimate_is_what_clean_frees(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = root / "shared-cursor"
            shared.mkdir()
            (shared / "cursor").write_bytes(b"x" * 1000)
            in_use = root / "Bibata-Modern-Ice"  # the cursor on screen
            in_use.mkdir()
            (in_use / "cursor").write_bytes(b"x" * 5000)
            walls = root / "walls"
            walls.mkdir()
            (walls / "a.png").write_bytes(b"x" * 300)
            registry = root / "assets.json"
            registry.write_text(
                json.dumps({"old": [str(shared), str(in_use), str(walls)], "older": [str(shared)], "_shared": []})
            )
            runner = mock.Mock()
            runner.dry_run = False
            runner.kread.side_effect = lambda f, g, k, d="": "Bibata-Modern-Ice" if k == "cursorTheme" else ""
            with (
                mock.patch.object(engine, "ASSET_REGISTRY", registry),
                mock.patch.object(engine, "PLASMIFY_DATA", root / "data"),
            ):
                estimate = engine.unused_size("active", runner)
                freed = engine.clean("active", runner, log=lambda _: None)
                left = json.loads(registry.read_text())
            self.assertEqual(estimate, 1300)
            self.assertEqual(freed, 1300)
            self.assertTrue(in_use.exists())
            self.assertFalse(shared.exists() or walls.exists())
            self.assertEqual(left, {"old": [str(in_use)], "_shared": []})


if __name__ == "__main__":
    unittest.main()
