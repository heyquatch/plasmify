"""install.sh, backups from older versions and small command-line messages."""

import contextlib
import io
import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from plasmify import backup, cli

ROOT = Path(__file__).resolve().parents[1]
INSTALL = ROOT / "install.sh"


def bash(script: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        env={**os.environ, **(env or {})},
    )


@unittest.skipUnless(shutil.which("bash"), "bash missing")
class InstallerTests(unittest.TestCase):
    def functions(self) -> str:
        return f"source <(sed -n '/^path_forms()/,/^}}/p;/^in_use()/,/^}}/p;/^restore_hint()/,/^}}/p' {INSTALL})"

    def test_purge_sees_everything_still_in_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            config, data = Path(tmp, "config"), Path(tmp, "data")
            config.mkdir()
            (config / "kdeglobals").write_text("[General]\nColorScheme=BreezeDark\n[Icons]\nTheme=Plasmify-nord\n")
            (config / "ksplashrc").write_text("[KSplash]\nTheme=plasmify-splash-nord\n")
            (config / "kscreenlockerrc").write_text(f"Image=file://{data}/plasmify/wallpapers/nord/01-a.png\n")
            proc = bash(f"set -euo pipefail; {self.functions()}; in_use {config} {data}")
            self.assertEqual(proc.stdout.split("\n")[:3], ["its icon theme", "its splash screen", "its wallpapers"])
            clean = bash(f"set -euo pipefail; {self.functions()}; in_use {tmp}/none {tmp}/none; echo done")
            self.assertEqual(clean.stdout, "done\n")

    def test_restore_hint_works_without_the_app(self):
        proc = bash(f"BIN=/nonexistent; SRC_DIR=/src; {self.functions()}; restore_hint")
        self.assertIn("python3 -m plasmify restore original", proc.stdout)

    def check_prefix(self, prefix: str) -> subprocess.CompletedProcess:
        functions = "/^info()/p;/^die()/p;/^check_prefix() {/,/^}/p"
        script = f"source <(sed -n '{functions}' {INSTALL}); check_prefix && echo fine"
        return bash(script, env={"PREFIX": prefix})

    def test_unusual_install_folders_are_refused(self):
        for prefix in ('/x/my "apps"', "/x/pre$HOME", "/x/100%", "/x/a\\b", "/x/a|b", "/x/`id`", "/x/a:b", "/x/a&b"):
            with self.subTest(prefix=prefix):
                proc = self.check_prefix(prefix)
                self.assertNotEqual(proc.returncode, 0)
                self.assertIn("cannot use", proc.stderr)
        for prefix in ("/home/a b/.local", "/home/Женя/.local", "/home/o'brien/.local", "/home/a (old)/~x"):
            with self.subTest(prefix=prefix):
                self.assertEqual(self.check_prefix(prefix).stdout.strip(), "fine")

    def test_uninstall_and_purge_work_whatever_the_folder(self):
        text = INSTALL.read_text()
        self.assertLess(text.index('"--purge" ]] &&'), text.index("\ncheck_prefix\n"))

    def test_purge_sees_every_way_a_path_is_written(self):
        for home in ("john@corp.example", "Мария", "o'brien", "José"):
            with self.subTest(home=home), tempfile.TemporaryDirectory() as tmp:
                config, data = Path(tmp, home, ".config"), Path(tmp, home, ".local", "share")
                config.mkdir(parents=True)
                uri = (data / "plasmify" / "wallpapers" / "nord" / "01.png").as_uri()  # as the app writes it
                (config / "kscreenlockerrc").write_text(f"Image={uri}\n")
                proc = bash(
                    f"set -euo pipefail; {self.functions()}; in_use {shlex.quote(str(config))} {shlex.quote(str(data) + '//')}"
                )
                self.assertEqual(proc.stdout.strip(), "its wallpapers", proc.stderr)

    def test_purge_sees_paths_written_with_dot_dot(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp, ".config")
            config.mkdir()
            data = f"{tmp}/dotfiles/../.local/share"  # pathlib keeps the ".." (so does the app)
            uri = Path(f"{data}/plasmify/wallpapers/nord/01.png").as_uri()
            (config / "kscreenlockerrc").write_text(f"Image={uri}\n")
            proc = bash(f"set -euo pipefail; {self.functions()}; in_use {shlex.quote(str(config))} {shlex.quote(data)}")
            self.assertEqual(proc.stdout.strip(), "its wallpapers")

    def test_path_forms_never_runs_code_from_the_current_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "pathlib.py").write_text("import sys; print('PLANTED'); sys.exit(1)\n")
            proc = subprocess.run(
                ["bash", "-c", f"{self.functions()}; path_forms /home/a"], cwd=tmp, capture_output=True, text=True
            )
            self.assertNotIn("PLANTED", proc.stdout)
            self.assertIn("/home/a", proc.stdout)

    def test_purge_sees_encoded_and_preset_folder_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            config, data = Path(tmp, "my config"), Path(tmp, "John Smith", "share")
            (config / "fastfetch").mkdir(parents=True)
            encoded = str(data).replace(" ", "%20")
            (config / "kscreenlockerrc").write_text(f"Image=file://{encoded}/plasmify/wallpapers/nord/01.png\n")
            (config / "fastfetch" / "config.jsonc").write_text(f'"source": "{config}/plasmify/presets/mine/logo.png"')
            proc = bash(f"set -euo pipefail; {self.functions()}; in_use '{config}/' '{data}/'")
            self.assertEqual(proc.stdout.split("\n")[:2], ["its wallpapers", "its terminal logo"])


class BackupMigrationTests(unittest.TestCase):
    def test_backups_from_before_pinning_get_an_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, label in enumerate(("Before Nord", "Before Gruvbox")):
                folder = root / f"2026010{index}"
                folder.mkdir()
                (folder / "manifest.json").write_text(json.dumps({"created": index, "label": label, "entries": []}))
            backups = backup.list_backups(root)
            pinned = [b for b in backups if b.pinned]
            self.assertEqual([b.id for b in pinned], ["20260100"])
            self.assertIn("before Plasmify", pinned[0].label)
            # Listing (also a dry run) changes nothing on disk; the next apply writes it down.
            self.assertNotIn("pinned", json.loads((root / "20260100" / "manifest.json").read_text()))
            backup._pin_oldest(root)
            self.assertTrue(json.loads((root / "20260100" / "manifest.json").read_text())["pinned"])
            backup.prune(root, keep=0)
            self.assertTrue((root / "20260100").is_dir())  # never pruned


class ReplaceRefreshTests(unittest.TestCase):
    def test_replacing_a_preset_refreshes_seeded_copies_only(self):
        from plasmify import bundle, presets

        png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
        data = dict(presets.find("nord-minimal").data)
        data["meta"] = dict(data["meta"], id="mine", name="Mine")
        data["wallpaper"] = {"images": [{"file": "01-w.png", "credit": "x"}, {"file": "02-w.png", "credit": "x"}]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sources = {}
            for name, tag in (("01-w.png", b"old1"), ("02-w.png", b"old2")):
                sources[name] = root / f"src-{name}"
                sources[name].write_bytes(png + tag)
            with mock.patch.object(bundle, "USER_PRESET_DIR", root / "presets"):
                bundle.save_user_preset(data, sources)
                copies = root / "custom" / "mine"
                copies.mkdir(parents=True)
                (copies / "01-w.png").write_bytes(png + b"old1")  # seeded copy, untouched
                (copies / "02-w.png").write_bytes(png + b"user")  # the user's own picture there
                for name in sources:
                    sources[name].write_bytes(png + b"new")
                bundle.save_user_preset(data, sources, replace=True, custom_root=root / "custom")
            self.assertEqual((copies / "01-w.png").read_bytes(), png + b"new")
            self.assertEqual((copies / "02-w.png").read_bytes(), png + b"user")
            # A file the new version adds reaches the copies too (Apply reads from there).
            data["wallpaper"]["images"].append({"file": "03-w.png", "credit": "x"})
            sources["03-w.png"] = root / "src-03"
            sources["03-w.png"].write_bytes(png + b"third")
            with mock.patch.object(bundle, "USER_PRESET_DIR", root / "presets"):
                bundle.save_user_preset(data, sources, replace=True, custom_root=root / "custom")
            self.assertEqual((copies / "03-w.png").read_bytes(), png + b"third")


class CliMessageTests(unittest.TestCase):
    def test_clean_dry_run_says_would(self):
        out = io.StringIO()
        with (
            mock.patch.object(cli.engine, "active_preset", return_value=None),
            mock.patch.object(cli.engine, "clean", return_value=3 * 1024 * 1024),
            contextlib.redirect_stdout(out),
        ):
            cli.main(["clean", "--dry-run"])
        self.assertIn("Would free", out.getvalue())

    def test_restore_help_names_original(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            cli.main(["restore", "--help"])
        self.assertIn("original", out.getvalue())


if __name__ == "__main__":
    unittest.main()


class BackupFoldingTests(unittest.TestCase):
    def make(self, root: Path, name: str, created: int, files: dict[str, str]) -> None:
        folder = root / name
        entries = []
        for path, text in files.items():
            stored = backup._store_path(folder, Path(path))
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_text(text)
            entries.append({"path": path, "existed": True, "kind": "file"})
        (folder / "manifest.json").write_text(json.dumps({"created": created, "label": name, "entries": entries}))

    def test_the_migrated_original_takes_older_files_from_newer_backups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make(root, "b1", 1, {"/home/x/.config/kdeglobals": "[General]\nColorScheme=BreezeDark\n"})
            self.make(root, "b2", 2, {"/home/x/.config/appletsrc": "mine", "/home/x/.config/kdeglobals": "themed"})
            self.make(root, "b3", 3, {"/home/x/.config/appletsrc": "plasmify"})
            backup._pin_oldest(root)
            pinned = next(b for b in backup.list_backups(root) if b.pinned)
            self.assertEqual(pinned.id, "b1")
            self.assertTrue(pinned.label.startswith("Your setup before Plasmify"))
            paths = {e["path"] for e in pinned.entries}
            self.assertIn("/home/x/.config/appletsrc", paths)
            self.assertEqual(backup._store_path(pinned.path, Path("/home/x/.config/appletsrc")).read_text(), "mine")
            kdeglobals = backup._store_path(pinned.path, Path("/home/x/.config/kdeglobals")).read_text()
            self.assertIn("BreezeDark", kdeglobals)  # its own copy wins over newer ones

    def test_a_themed_oldest_backup_is_not_called_the_original(self):
        for name, text in (
            ("kdeglobals", "[General]\nColorScheme=PlasmifyNordMinimal\n"),
            ("kdeglobals", "[General]\nColorScheme=BreezeDark\n[Icons]\nTheme=Plasmify-nord\n"),
            ("kscreenlockerrc", "Image=file:///home/x/.local/share/plasmify/wallpapers/nord/01.png\n"),
        ):
            with self.subTest(name=name, text=text), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.make(root, "b1", 1, {f"/home/x/.config/{name}": text})
                pinned = next(b for b in backup.list_backups(root) if b.pinned)
                self.assertTrue(pinned.label.startswith("Oldest backup"))

    def test_a_terminal_logo_marks_a_backup_as_themed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            logo = '"source": "/home/x/.local/share/plasmify/logos/catppuccin-anime.png"'
            self.make(root, "b1", 1, {"/home/x/.config/fastfetch/config.jsonc": logo})
            self.assertTrue(next(b for b in backup.list_backups(root) if b.pinned).label.startswith("Oldest backup"))

    def test_the_label_looks_at_what_restoring_would_bring_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make(root, "b3", 3, {"/home/x/.config/starship.toml": "mine"})  # terminal only
            self.make(root, "b4", 4, {"/home/x/.config/kdeglobals": "[General]\nColorScheme=PlasmifyX\n"})
            listed = next(b for b in backup.list_backups(root) if b.pinned)
            self.assertTrue(listed.label.startswith("Oldest backup"))
            backup._pin_oldest(root)
            written = next(b for b in backup.list_backups(root) if b.pinned)
            self.assertEqual(listed.label, written.label)  # the same before and after the next apply

    def test_a_pin_that_cannot_be_written_never_stops_an_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make(root, "b1", 1, {"/home/x/.config/kdeglobals": "[General]\nColorScheme=BreezeDark\n"})
            os.chmod(root / "b1" / "manifest.json", 0o400)
            os.chmod(root / "b1", 0o500)
            try:
                with tempfile.TemporaryDirectory() as home:
                    target = Path(home, "plasmarc")
                    target.write_text("[Theme]\nname=default\n")
                    created = backup.create([target], "Before Nord", root=root)
                self.assertTrue(created.path.is_dir())
            finally:
                os.chmod(root / "b1", 0o700)
                os.chmod(root / "b1" / "manifest.json", 0o600)

    def test_one_unreadable_copy_does_not_stop_the_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.make(root, "b1", 1, {"/home/x/.config/kdeglobals": "[General]\nColorScheme=BreezeDark\n"})
            self.make(root, "b2", 2, {"/home/x/.bashrc": "mine"})
            backup._store_path(root / "b2", Path("/home/x/.bashrc")).unlink()  # the copy went missing
            backup._pin_oldest(root)
            self.assertTrue(json.loads((root / "b1" / "manifest.json").read_text())["pinned"])


FAKE_PLASMASHELL = """#!/bin/sh
# Like Fedora 44's plasmashell: --version crashes on the offscreen platform, works on minimal.
case "$QT_QPA_PLATFORM" in
    offscreen) kill -SEGV $$ ;;
    minimal) [ "$WORKS_ON_MINIMAL" = 1 ] && echo "plasmashell 6.6.4" && exit 0; kill -ABRT $$ ;;
esac
exit 1
"""
FAKE_RPM = """#!/bin/sh
# rpm -q --qf %{VERSION} NAME
[ "$4" = "plasma-workspace" ] && printf '6.6.4' && exit 0
echo "package $4 is not installed"
exit 1
"""


@unittest.skipUnless(shutil.which("bash"), "bash missing")
class PlasmaVersionTests(unittest.TestCase):
    """The Plasma version is read even where plasmashell crashes on one Qt platform (Fedora 44)."""

    def fake_bin(self, works_on_minimal: bool) -> dict[str, str]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        folder = Path(tmp.name)
        for name, text in (("plasmashell", FAKE_PLASMASHELL), ("rpm", FAKE_RPM)):
            (folder / name).write_text(text)
            (folder / name).chmod(0o755)
        # Only the fakes and the basic tools: no pacman or dpkg-query to answer first.
        tools = folder / "tools"
        tools.mkdir()
        for tool in ("sh", "bash", "kill", "printf", "sed", "cat"):
            if shutil.which(tool):
                (tools / tool).symlink_to(shutil.which(tool))
        return {"PATH": f"{folder}:{tools}", "WORKS_ON_MINIMAL": "1" if works_on_minimal else "0"}

    def run_installer_check(self, env: dict[str, str]) -> str:
        functions = "/^plasma_version() {/,/^}/p;/^plasma_major() {/,/^}/p"
        script = f'source <(sed -n \'{functions}\' {INSTALL}); found="$(plasma_version)"; echo "$found|$(plasma_major "$found")"'
        return bash(script, env=env).stdout.strip()

    def test_installer_falls_back_to_minimal_then_the_package(self):
        self.assertEqual(self.run_installer_check(self.fake_bin(works_on_minimal=True)), "plasmashell 6.6.4|6")
        self.assertEqual(self.run_installer_check(self.fake_bin(works_on_minimal=False)), "6.6.4|6")

    def test_installer_reads_every_version_form(self):
        for text, major in (("plasmashell 6.8.0", "6"), ("plasma-workspace 6.8.0-1", "6"), ("4:6.3.4-2", "6"),
                            ("plasmashell 5.27.11", "5"), ("", ""), ("plasmashell", "")):  # fmt: skip
            with self.subTest(text=text):
                functions = "/^plasma_major() {/,/^}/p"
                out = bash(f"source <(sed -n '{functions}' {INSTALL}); plasma_major {shlex.quote(text)}").stdout
                self.assertEqual(out.strip(), major)

    def test_app_falls_back_to_minimal_then_the_package(self):
        from plasmify import system

        for works, expected in ((True, (6, 6, 4)), (False, (6, 6, 4))):
            with self.subTest(works_on_minimal=works), mock.patch.dict(os.environ, self.fake_bin(works)):
                self.assertEqual(system.plasma_version(), expected)

    def test_app_reads_every_version_form(self):
        from plasmify.system import version_numbers

        self.assertEqual(version_numbers("plasmashell 6.8.0\n"), (6, 8, 0))
        self.assertEqual(version_numbers("plasma-workspace 6.8.0-1"), (6, 8, 0))
        self.assertEqual(version_numbers("4:6.3.4-2"), (6, 3, 4))
        self.assertIsNone(version_numbers("package plasma-workspace is not installed"))
        self.assertIsNone(version_numbers(""))
