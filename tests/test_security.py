"""Hostile preset files must be rejected before they can touch the system.

Every test starts from a valid built-in preset and changes one thing the way an
attacker sharing a preset would. When you add a preset field, add its abuse case here.
"""

import copy
import tempfile
import tomllib
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from plasmify import downloads, presets, schema
from plasmify.system import UnsafePathError, safe_child


def base_preset() -> dict:
    with presets.builtin_paths()[0].open("rb") as fh:
        return tomllib.load(fh)


class MaliciousPresetTests(unittest.TestCase):
    def assertRejected(self, mutate, fragment):  # noqa: N802 (unittest style)
        data = copy.deepcopy(base_preset())
        mutate(data)
        errors = schema.validate(data)
        self.assertTrue(errors, "preset was accepted")
        self.assertTrue(any(fragment in e for e in errors), errors)

    # --- config file injection ------------------------------------------
    def test_newline_in_font_name(self):
        # Would add a "shell" line to kitty.conf / alacritty.toml.
        self.assertRejected(lambda d: d["fonts"].__setitem__("mono", 'Mono"\nshell sh -c evil'), "fonts.mono")

    def test_comma_in_font_name(self):
        # Would corrupt the comma-separated QFont string in kdeglobals.
        self.assertRejected(lambda d: d["fonts"].__setitem__("ui", "Inter,99,-1"), "fonts.ui")

    def test_string_where_number_expected(self):
        self.assertRejected(
            lambda d: d["terminal"].__setitem__("opacity", '1\n[terminal.shell]\nprogram = "sh"'), "opacity"
        )

    def test_bool_is_not_a_number(self):
        self.assertRejected(lambda d: d["terminal"].__setitem__("padding", True), "padding")

    def test_control_characters_in_text(self):
        self.assertRejected(lambda d: d["meta"].__setitem__("description", "nice\x1b[2Jtheme"), "meta.description")

    def test_newline_in_wallpaper_credit(self):
        self.assertRejected(lambda d: d["wallpaper"]["images"][0].__setitem__("credit", "a\nb"), "credit")

    # --- path traversal -------------------------------------------------
    def test_kvantum_name_traversal(self):
        self.assertRejected(
            lambda d: d["style"]["kvantum"].__setitem__("name", "../../Documents"), "style.kvantum.name"
        )

    def test_cursor_name_with_slash(self):
        self.assertRejected(lambda d: d["cursor"].__setitem__("name", "icons/../../.ssh"), "cursor.name")

    def test_icon_theme_dotdot(self):
        self.assertRejected(lambda d: d["icons"].__setitem__("theme", ".."), "icons.theme")

    def test_repository_path_escape(self):
        self.assertRejected(
            lambda d: d["icons"]["folder_source"].__setitem__("path", "../../etc"), "folder_source.path"
        )

    def test_absolute_repository_path(self):
        self.assertRejected(lambda d: d["style"]["kvantum"].__setitem__("path", "/etc/passwd"), "kvantum.path")

    def test_preset_id_is_a_safe_name(self):
        self.assertRejected(lambda d: d["meta"].__setitem__("id", "../evil"), "meta.id")

    def test_git_option_injection_in_ref(self):
        self.assertRejected(lambda d: d["style"]["kvantum"].__setitem__("ref", "--upload-pack=evil"), "ref")

    # --- network --------------------------------------------------------
    def test_plain_http(self):
        self.assertRejected(lambda d: d["cursor"].__setitem__("url", "http://example.com/c.zip"), "cursor.url")

    def test_credentials_in_url(self):
        self.assertRejected(lambda d: d["cursor"].__setitem__("url", "https://u:p@github.com/c.zip"), "credentials")

    def test_file_url(self):
        self.assertRejected(lambda d: d["layout"].__setitem__("launcher_icon", "file:///etc/shadow"), "launcher_icon")

    def test_untrusted_hosts_are_reported(self):
        data = copy.deepcopy(base_preset())
        data["wallpaper"]["images"][0]["url"] = "https://tracker.example.net/pixel.png"
        self.assertEqual(schema.validate(data), [])
        self.assertEqual(schema.untrusted_hosts(data), ["tracker.example.net"])

    def test_builtin_presets_use_trusted_hosts_only(self):
        for preset in presets.load_all(include_user=False):
            with self.subTest(preset.id):
                self.assertEqual(preset.untrusted_hosts(), [])

    # --- packages and schema shape --------------------------------------
    def test_package_outside_reviewed_list(self):
        # apt starts services on install; presets must not choose arbitrary packages.
        self.assertRejected(lambda d: d["fonts"]["packages"].append("openssh-server"), "fonts.packages")

    def test_unknown_key(self):
        self.assertRejected(lambda d: d["terminal"].__setitem__("command", "sh"), "terminal.command")

    def test_unknown_section(self):
        self.assertRejected(lambda d: d.__setitem__("hooks", {"post": "rm -rf ~"}), "hooks")

    def test_too_many_wallpapers(self):
        self.assertRejected(lambda d: d["wallpaper"].__setitem__("images", d["wallpaper"]["images"] * 10), "at most")


class GuardTests(unittest.TestCase):
    def test_safe_child_refuses_escapes(self):
        base = Path(tempfile.gettempdir()) / "plasmify-guard"
        for name in ("..", ".", "a/b", "/etc", "x\x00y", ""):
            with self.subTest(name=name), self.assertRaises(UnsafePathError):
                safe_child(base, name)
        self.assertEqual(safe_child(base, "ok-name").name, "ok-name")

    def test_download_refuses_untrusted_and_http(self):
        for url in ("http://github.com/x", "https://tracker.example.net/x.png"):
            with self.subTest(url=url), self.assertRaises(downloads.DownloadError):
                downloads.check_url(url)
        downloads.allow_hosts({"tracker.example.net"})
        try:
            downloads.check_url("https://tracker.example.net/x.png")
        finally:
            downloads.allow_hosts(set())

    def test_zip_traversal_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "evil.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                zf.writestr("../../escaped.txt", "x")
            with self.assertRaises(downloads.DownloadError):
                downloads.extract(archive, Path(tmp) / "out")
            self.assertFalse((Path(tmp).parent / "escaped.txt").exists())

    def test_zip_bomb_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp) / "bomb.zip"
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("big.bin", b"\0" * 4096)
            with mock.patch.object(downloads, "MAX_EXTRACTED_BYTES", 1024), self.assertRaises(downloads.DownloadError):
                downloads.extract(archive, Path(tmp) / "out")

    def test_user_preset_cannot_replace_builtin(self):
        with tempfile.TemporaryDirectory() as tmp:
            impostor = presets.builtin_paths()[0].read_text().replace("Soothing pastel", "Totally safe")
            (Path(tmp) / "impostor.toml").write_text(impostor)
            problems: list[str] = []
            with mock.patch.object(presets, "USER_PRESET_DIR", Path(tmp)):
                loaded = presets.load_all(problems=problems)
            builtin = next(p for p in loaded if p.id == "catppuccin-anime")
            self.assertTrue(builtin.builtin)
            self.assertTrue(any("built-in" in p for p in problems))

    def test_invalid_user_preset_is_skipped_not_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "broken.toml").write_text("this is = = not toml")
            problems: list[str] = []
            with mock.patch.object(presets, "USER_PRESET_DIR", Path(tmp)):
                loaded = presets.load_all(problems=problems)
            self.assertGreaterEqual(len(loaded), 5)
            self.assertEqual(len(problems), 1)


if __name__ == "__main__":
    unittest.main()


class TrailingNewlineTests(unittest.TestCase):
    def test_no_pattern_accepts_a_line_break(self):
        from plasmify import schema

        checks = {
            "name": schema.NAME,
            "font": schema.FONT,
            "relpath": schema.RELPATH,
            "image": schema.IMAGE_FILE,
            "logo": schema.SCHEMA["shell"][0]["logo_builtin"][0],
        }
        for label, check in checks.items():
            for value in ("Inter\n", "Inter\r", "a.png\n", "Inter\x00"):
                with self.subTest(label=label, value=value):
                    self.assertIsNotNone(check(value))


class SpoofingTests(unittest.TestCase):
    def test_bidirectional_overrides_are_refused(self):
        from plasmify import schema

        for char in ("\u202e", "\u202d", "\u2066", "\u2069", "\u200f", "\ufeff"):
            with self.subTest(char=hex(ord(char))):
                self.assertIsNotNone(schema.text(60)(f"Nord{char}txt.exe"))


class HardeningRegressionTests(unittest.TestCase):
    """The October 2026 security audit, one test per fix."""

    def setUp(self):
        # The download tests record to the activity log: keep the user's real one clean.
        from plasmify import activity

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        patcher = mock.patch.object(activity, "LOG_FILE", Path(folder.name) / "activity.jsonl")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_ids_cannot_share_a_colour_scheme(self):
        from plasmify import schema

        for bad in ("a--b", "a-", "-a", "a-b-"):
            self.assertIsNotNone(schema.pattern(schema._ID, "id")(bad), bad)
        self.assertIsNone(schema.pattern(schema._ID, "id")("rose-pine-cozy"))

    def test_scheme_clash_is_refused_on_import(self):
        from plasmify import bundle

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(bundle, "USER_PRESET_DIR", Path(tmp)),
            mock.patch.object(presets, "USER_PRESET_DIR", Path(tmp)),
        ):
            data = copy.deepcopy(presets.find("nord-minimal").data)
            data["meta"].update(id="a1b", name="One")
            bundle.save_user_preset(data)
            self.assertEqual(presets.scheme_key("a1b"), presets.scheme_key("a-1b"))  # digits have no case
            data["meta"].update(id="a-1b", name="Two")
            with self.assertRaises(presets.PresetError):
                bundle.save_user_preset(data)

    def test_padded_names_cannot_hide_their_origin(self):
        data = copy.deepcopy(presets.find("nord-minimal").data)
        data["meta"]["name"] = "Nord Minimal" + "　" * 40 + "x"
        self.assertEqual(presets.Preset(data, "user").name, "Nord Minimal x")
        from plasmify import schema

        self.assertIsNotNone(schema.text(60)("Nord​Minimal"))

    def test_terminal_output_has_no_escape_sequences(self):
        import io

        from plasmify.cli import _SafeStream

        out = io.StringIO()
        _SafeStream(out).write("ok\x1b]52;c;ZXZpbA==\x07 \x1b[2Jdone\n")
        self.assertNotIn("\x1b", out.getvalue())
        self.assertTrue(out.getvalue().endswith("done\n"))

    def test_colour_and_file_patterns_are_exact(self):
        from plasmify import palette, schema

        self.assertFalse(palette.is_hex("#ffffff\n"))
        self.assertIsNotNone(schema.IMAGE_FILE("ſecret.png"))  # long s, matched by [a-z] with IGNORECASE
        self.assertIsNotNone(schema.RELPATH("-c/core.hooksPath=x"))

    def test_git_never_follows_links_or_lfs_and_takes_the_path_on_stdin(self):
        from plasmify import downloads

        calls = []

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            return mock.Mock(returncode=0, stdout="abc\n", stderr="")

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(downloads.subprocess, "run", fake_run):
            target = Path(tmp, "checkout")
            (target / "themes").mkdir(parents=True)
            with mock.patch.object(downloads.shutil, "rmtree"):
                downloads.sparse_checkout("https://github.com/example/repo", "themes", target)
        clone = calls[0][0]
        self.assertIn("core.symlinks=false", clone)
        self.assertIn("filter.lfs.smudge=", clone)
        sparse_args, sparse_kwargs = calls[1]
        self.assertNotIn("themes", sparse_args)
        self.assertEqual(sparse_kwargs["input"], "themes\n")
        self.assertEqual(sparse_kwargs["env"]["GIT_LFS_SKIP_SMUDGE"], "1")

    def test_tar_entry_cap_is_checked_while_reading(self):
        import io
        import tarfile

        from plasmify import downloads

        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp, "many.tar.gz")
            with tarfile.open(archive, "w:gz") as tf:
                for index in range(60):
                    info = tarfile.TarInfo(f"f{index}")
                    tf.addfile(info, io.BytesIO(b""))
            with mock.patch.object(downloads, "MAX_ARCHIVE_ENTRIES", 50), self.assertRaises(downloads.DownloadError):
                downloads.extract(archive, Path(tmp, "out"))

    def test_download_budget_per_apply(self):
        from plasmify import downloads

        downloads.reset_budget()
        downloads._budget["used"] = downloads.MAX_SESSION_BYTES
        response = mock.MagicMock()
        response.__enter__.return_value.read.side_effect = [b"x" * 10, b""]
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(downloads, "check_url"),
            mock.patch.object(downloads._opener, "open", return_value=response),
            self.assertRaises(downloads.DownloadError),
        ):
            downloads.fetch("https://github.com/x.png", Path(tmp, "x.png"))
        downloads.reset_budget()

    def test_cursor_step_never_deletes_a_foreign_folder(self):
        from plasmify.steps import cursor
        from plasmify.steps.base import Context
        from plasmify.system import Runner

        with tempfile.TemporaryDirectory() as tmp:
            icons = Path(tmp, "icons")
            mine = icons / "Papirus-Dark"
            mine.mkdir(parents=True)
            (mine / "index.theme").write_text("[Icon Theme]\nName=mine\n")
            extracted = Path(tmp, "x", "Papirus-Dark", "cursors")
            extracted.mkdir(parents=True)
            data = copy.deepcopy(presets.find("nord-minimal").data)
            data["cursor"] = {"name": "Papirus-Dark", "url": "https://github.com/x/c.tar.gz"}
            runner = Runner()
            runner.run = lambda *a, **k: ""
            runner.kwrite = lambda *a, **k: None
            ctx = Context(presets.Preset(data, "user"), runner, Path(tmp, "work"), log=lambda _: None)
            with (
                mock.patch.object(cursor, "DATA_HOME", Path(tmp)),
                mock.patch.object(cursor, "installed_cursor", return_value=None),
                mock.patch.object(cursor.downloads, "fetch", return_value=Path(tmp, "c.tar.gz")),
                mock.patch.object(cursor.downloads, "extract", return_value=Path(tmp, "x")),
            ):
                cursor.CursorStep().apply(ctx)
            self.assertTrue((mine / "index.theme").exists())
            self.assertTrue(ctx.warnings)

    def test_kvantum_copies_skip_links(self):
        from plasmify.steps.style import _links

        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "theme.kvconfig").write_text("x")
            Path(tmp, "secret").symlink_to(Path.home())
            self.assertEqual(_links(tmp, ["theme.kvconfig", "secret"]), ["secret"])

    def test_untrusted_tooltips_are_escaped(self):
        from plasmify.plaintext import plain_tooltip

        self.assertEqual(plain_tooltip('<a href="x">y</a>'), "<qt>&lt;a href=&quot;x&quot;&gt;y&lt;/a&gt;</qt>")

    def test_encrypted_bundle_is_a_clear_error(self):
        from plasmify import bundle

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "locked.plasmify")
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("preset.toml", "schema = 1\n")
            raw = bytearray(path.read_bytes())
            raw[6] |= 0x01  # local header: "encrypted"
            central = raw.rfind(b"PK\x01\x02")
            raw[central + 8] |= 0x01  # central directory: "encrypted"
            path.write_bytes(bytes(raw))
            with self.assertRaises(presets.PresetError):
                bundle.read_bundle(path)

    def test_tar_hard_links_are_refused(self):
        import io
        import tarfile

        from plasmify import downloads

        with tempfile.TemporaryDirectory() as tmp:
            archive = Path(tmp, "links.tar.gz")
            with tarfile.open(archive, "w:gz") as tf:
                data = tarfile.TarInfo("big")
                data.size = 1024
                tf.addfile(data, io.BytesIO(b"\0" * 1024))
                for index in range(5):
                    link = tarfile.TarInfo(f"copy{index}")
                    link.type = tarfile.LNKTYPE
                    link.linkname = "big"
                    tf.addfile(link)
            with self.assertRaises(downloads.DownloadError):
                downloads.extract(archive, Path(tmp, "out"))

    def test_both_git_commands_get_the_safety_settings(self):
        from plasmify import downloads

        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "checkout")
            (target / "themes").mkdir(parents=True)

            def fake_run(args, **kwargs):
                calls.append(args)
                return mock.Mock(returncode=0, stdout="abc\n", stderr="")

            with (
                mock.patch.object(downloads.subprocess, "run", fake_run),
                mock.patch.object(downloads.shutil, "rmtree"),
            ):
                downloads.sparse_checkout("https://github.com/example/repo", "themes", target)
        for command in calls[:2]:
            self.assertIn("core.symlinks=false", command)
            self.assertIn("filter.lfs.process=", command)

    def test_git_never_follows_a_redirect(self):
        # urllib downloads check every redirect against the approved hosts; git cannot, so it follows none.
        from plasmify import downloads

        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, "checkout")
            (target / "themes").mkdir(parents=True)

            def fake_run(args, **kwargs):
                calls.append(args)
                return mock.Mock(returncode=0, stdout="abc\n", stderr="")

            with (
                mock.patch.object(downloads.subprocess, "run", fake_run),
                mock.patch.object(downloads.shutil, "rmtree"),
            ):
                downloads.sparse_checkout("https://github.com/example/repo", "themes", target)
        for command, subcommand in zip(calls[:2], ("clone", "sparse-checkout"), strict=True):
            setting = command.index("http.followRedirects=false")
            self.assertEqual(command[setting - 1], "-c")
            self.assertLess(setting, command.index(subcommand))  # a -c after the subcommand would not count

        moved = mock.Mock(returncode=128, stdout="", stderr="fatal: The requested URL returned error: 301\n")
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(downloads.subprocess, "run", return_value=moved),
            self.assertRaisesRegex(downloads.DownloadError, r"redirects are not followed.*\.git"),
        ):
            downloads.sparse_checkout("https://gitlab.com/example/repo", "themes", Path(tmp, "checkout"))
