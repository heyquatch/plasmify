import tempfile
import unittest
from pathlib import Path

from plasmify import backup, palette, presets, templates
from plasmify.packages import human_size
from plasmify.steps.icons import folder_links, index_theme
from plasmify.steps.shell import init_snippet


class PaletteTests(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(palette.to_hex(palette.to_rgb("#1e1e2e")), "#1e1e2e")

    def test_mix(self):
        self.assertEqual(palette.mix("#000000", "#ffffff", 0.5), "#808080")

    def test_kde_and_sgr_format(self):
        self.assertEqual(palette.kde_rgb("#ff8000"), "255,128,0")
        self.assertEqual(palette.ansi_truecolor("#ff8000"), "38;2;255;128;0")


class TemplateTests(unittest.TestCase):
    def test_unknown_variable_is_an_error(self):
        with self.assertRaises(KeyError):
            templates.render_text("{{ nope }}", {})

    def test_block_is_inserted_once_and_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = Path(tmp) / "rc"
            rc.write_text("alias ll='ls -l'\n")
            templates.upsert_block(rc, "first")
            templates.upsert_block(rc, "second")
            text = rc.read_text()
            self.assertIn("alias ll='ls -l'", text)
            self.assertIn("second", text)
            self.assertNotIn("first", text)
            self.assertEqual(text.count(templates.BLOCK_BEGIN), 1)
            templates.remove_block(rc)
            self.assertEqual(rc.read_text(), "alias ll='ls -l'\n")

    def test_shell_snippets(self):
        self.assertIn("starship init fish", init_snippet("fish", True, False))
        self.assertIn("zoxide init bash", init_snippet("bash", False, True))
        self.assertNotIn("starship", init_snippet("zsh", False, True))


class BackupTests(unittest.TestCase):
    def test_create_and_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            existing = root / "config" / "kdeglobals"
            existing.parent.mkdir()
            existing.write_text("old")
            created_later = root / "config" / "new.conf"

            snap = backup.create([existing, created_later], "test", root=root / "backups")
            existing.write_text("new")
            created_later.write_text("plasmify made this")

            backup.restore_files(snap)
            self.assertEqual(existing.read_text(), "old")
            self.assertFalse(created_later.exists())
            self.assertEqual(backup.list_backups(root / "backups")[0].label, "Your setup before Plasmify (test)")

    def test_first_backup_is_pinned_and_never_pruned(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = backup.create([], "Before X", root=root)
            self.assertTrue(first.pinned)
            self.assertIn("before Plasmify", first.label)
            for _ in range(5):
                backup.create([], "later", root=root)
            backup.prune(root, keep=2)
            remaining = backup.list_backups(root)
            self.assertEqual(len(remaining), 3)  # two newest + the pinned original
            self.assertIn(first.id, [b.id for b in remaining])

    def test_prune_keeps_newest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for _ in range(4):
                backup.create([], "x", root=root)
            backup.prune(root, keep=2)
            # two newest unpinned backups plus the pinned first one
            self.assertEqual(len(backup.list_backups(root)), 3)


class IconTests(unittest.TestCase):
    def test_folder_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            places = Path(tmp)
            for name in ("folder-pink.svg", "folder-pink-documents.svg", "user-pink-home.svg", "folder-blue.svg"):
                (places / name).write_text("<svg/>")
            links = folder_links(places, "pink")
            self.assertEqual(set(links), {"folder.svg", "folder-documents.svg", "user-home.svg"})

    def test_index_theme_inherits(self):
        text = index_theme("Test", "Papirus-Dark")
        self.assertIn("Inherits=Papirus-Dark,breeze-dark,hicolor", text)
        self.assertIn("[48x48@2x/places]", text)


class SizeTests(unittest.TestCase):
    def test_human_size(self):
        self.assertEqual(human_size(512), "512 B")
        self.assertEqual(human_size(5 * 1024 * 1024), "5.0 MB")


if __name__ == "__main__":
    unittest.main()


class CleanTests(unittest.TestCase):
    def test_clean_keeps_files_the_active_or_all_presets_use(self):
        import json
        from unittest import mock

        from plasmify import engine

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shared = root / "Plasmify.profile"
            shared.write_text("profile")
            only_old = root / "old.colors"
            only_old.write_text("x")
            registry = root / "assets.json"
            registry.write_text(
                json.dumps({"active": [str(shared)], "old": [str(shared), str(only_old)], "_shared": []})
            )
            runner = mock.Mock()
            runner.dry_run = False
            runner.kread.return_value = ""
            with (
                mock.patch.object(engine, "ASSET_REGISTRY", registry),
                mock.patch.object(engine, "PLASMIFY_DATA", root / "data"),
            ):
                engine.clean("active", runner, log=lambda _: None)
            self.assertTrue(shared.exists(), "a file the active preset uses was deleted")
            self.assertFalse(only_old.exists())

    def test_clean_and_capture_refuse_without_kreadconfig6(self):
        """Without the config reader nothing counts as in use: deleting would hit the live desktop."""
        import json
        import os
        from unittest import mock

        from plasmify import capture, engine
        from plasmify.system import CommandError, Runner

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cursor = root / "Bibata-Modern-Ice"
            cursor.mkdir()
            registry = root / "assets.json"
            registry.write_text(json.dumps({"old": [str(cursor)], "_shared": []}))
            missing = root / "missing"
            missing.mkdir()
            broken = root / "broken"  # starts, then fails like a half-finished upgrade
            broken.mkdir()
            stub = broken / "kreadconfig6"
            stub.write_text("#!/bin/sh\necho 'error while loading shared libraries' >&2\nexit 127\n")
            stub.chmod(0o755)
            for folder in (missing, broken):
                with (
                    self.subTest(folder=folder.name),
                    mock.patch.object(engine, "ASSET_REGISTRY", registry),
                    mock.patch.dict(os.environ, {"PATH": str(folder)}),
                ):
                    with self.assertRaises(CommandError):
                        engine.clean("active", Runner(), log=lambda _: None)
                    with self.assertRaises(presets.PresetError):
                        capture.capture("Mine", "", Runner(), include_wallpapers=False)
                self.assertTrue(cursor.exists())


class RestoreCoverageTests(unittest.TestCase):
    def test_every_file_a_step_writes_is_backed_up(self):
        """Restore can only bring back what was backed up first."""
        from plasmify.steps.terminal import TerminalStep
        from plasmify.system import DATA_HOME

        paths = TerminalStep().touched_paths(presets.find("nord-minimal"))
        self.assertIn(DATA_HOME / "konsole" / "Plasmify.profile", paths)
        self.assertIn(DATA_HOME / "konsole" / "Plasmify.colorscheme", paths)


class CumulativeRestoreTests(unittest.TestCase):
    def test_restoring_an_older_point_undoes_later_applies(self):
        """Apply 1 changes a; apply 2 changes a and b. Back to "before apply 1" means both."""
        from plasmify import engine

        with tempfile.TemporaryDirectory() as tmp:
            root, a, b = Path(tmp, "backups"), Path(tmp, "a"), Path(tmp, "b")
            a.write_text("a0")
            b.write_text("b0")
            first = backup.create([a], "apply 1", root=root)
            a.write_text("a1")
            second = backup.create([a, b], "apply 2", root=root)
            a.write_text("a2")
            b.write_text("b2")
            parts = engine.restore_points(first, backup.list_backups(root))
            for part in parts:
                backup.restore_files(part)
            self.assertEqual((a.read_text(), b.read_text()), ("a0", "b0"))
            only_second = engine.restore_points(second, backup.list_backups(root))
            self.assertEqual([p.id for p in only_second], [second.id])
