"""Distribution detection, package name mapping and package manager output parsing.

The sample outputs below mirror the format of each tool; the CI "packages" job
checks the same code against the real package managers in containers.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from plasmify import distro, package_map, packages
from plasmify.distro import ARCH, DEBIAN, FEDORA, OPENSUSE, Distro

OS_RELEASES = {
    "cachyos": ('NAME="CachyOS Linux"\nID=cachyos\nID_LIKE=arch\n', ARCH),
    "endeavouros": ('NAME="EndeavourOS"\nID="endeavouros"\nID_LIKE="arch"\n', ARCH),
    "fedora": ('NAME="Fedora Linux"\nVERSION_ID=43\nID=fedora\nVARIANT_ID=kde\n', FEDORA),
    "tumbleweed": ('NAME="openSUSE Tumbleweed"\nID="opensuse-tumbleweed"\nID_LIKE="opensuse suse"\n', OPENSUSE),
    "kubuntu": ('NAME="Ubuntu"\nVERSION_ID="25.10"\nID=ubuntu\nID_LIKE=debian\n', DEBIAN),
    "neon": ('NAME="KDE neon"\nID=neon\nID_LIKE="ubuntu debian"\n', DEBIAN),
    "debian": (
        'PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\nNAME="Debian GNU/Linux"\nVERSION_ID="13"\nID=debian\n',
        DEBIAN,
    ),
}


class DistroDetectionTests(unittest.TestCase):
    def test_known_distributions(self):
        for label, (text, family) in OS_RELEASES.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "os-release"
                path.write_text(text)
                detected = distro.detect(path)
                self.assertIsNotNone(detected)
                self.assertEqual(detected.family, family)

    def test_unknown_distribution(self):
        self.assertIsNone(distro.family_of({"ID": "gentoo"}))


class PackageMapTests(unittest.TestCase):
    def test_arch_uses_canonical_names(self):
        arch = Distro("cachyos", ARCH, "CachyOS")
        result = package_map.resolve(["kvantum", "ttf-nerd-fonts-symbols"], arch)
        self.assertEqual(result.native, {"kvantum": "kvantum", "ttf-nerd-fonts-symbols": "ttf-nerd-fonts-symbols"})

    def test_translation_and_font_fallback(self):
        debian = Distro("debian", DEBIAN, "Debian", "13")
        result = package_map.resolve(["kvantum", "ttf-roboto-mono", "zoxide"], debian)
        self.assertEqual(result.native["kvantum"], "qt6-style-kvantum")
        self.assertEqual(result.native["zoxide"], "zoxide")
        self.assertEqual(result.font_downloads, ["ttf-roboto-mono"])

    def test_distro_id_overrides_family(self):
        ubuntu = Distro("ubuntu", DEBIAN, "Ubuntu", "25.10")
        self.assertEqual(package_map.native_name("ttf-nunito", ubuntu), "fonts-nunito")
        self.assertIsNone(package_map.native_name("ttf-nunito", Distro("debian", DEBIAN, "Debian")))

    def test_non_font_without_package_is_unavailable(self):
        fedora = Distro("fedora", FEDORA, "Fedora", "43")
        self.assertEqual(package_map.resolve(["starship"], fedora).unavailable, ["starship"])

    def test_missing_from_repos_falls_back(self):
        suse = Distro("opensuse-tumbleweed", OPENSUSE, "openSUSE")
        result = package_map.resolve(["inter-font"], suse, missing_from_repos={"inter-fonts"})
        self.assertEqual(result.font_downloads, ["inter-font"])

    def test_every_mapped_font_has_a_fallback(self):
        for name in package_map.PACKAGES:
            if any(name.startswith(p) for p in ("ttf-", "inter-", "noto-")):
                self.assertIn(name, package_map.FONT_FALLBACKS, name)


class ParserTests(unittest.TestCase):
    def test_pacman_si_counts_each_package_once(self):
        text = (
            "Repository      : cachyos-extra-v3\nName            : starship\nInstalled Size  : 11.58 MiB\n\n"
            "Repository      : extra\nName            : starship\nInstalled Size  : 11.50 MiB\n\n"
            "Repository      : extra\nName            : zoxide\nInstalled Size  : 1141.07 KiB\n"
        )
        sizes = packages.parse_info_blocks(text, "Name", "Installed Size")
        self.assertEqual(sizes["starship"], int(11.58 * 1024**2))
        self.assertEqual(sizes["zoxide"], int(1141.07 * 1024))

    def test_zypper_info(self):
        text = (
            "Information for package hack-fonts:\n------------------------------------\n"
            "Repository     : Main Repository (OSS)\nName           : hack-fonts\nVersion        : 3.003-1.1\n"
            "Installed Size : 1.2 MiB\nInstalled      : No\n"
        )
        self.assertEqual(packages.parse_info_blocks(text, "Name", "Installed Size"), {"hack-fonts": int(1.2 * 1024**2)})

    def test_dnf5_info(self):
        text = (
            "Available packages\nName            : zoxide\nEpoch           : 0\nVersion         : 0.9.8\n"
            "Download size   : 400.1 KiB\nInstalled size  : 1.1 MiB\n"
        )
        self.assertEqual(packages.parse_info_blocks(text, "Name", "Installed size"), {"zoxide": int(1.1 * 1024**2)})

    def test_dnf4_info_falls_back_to_size(self):
        text = "Available Packages\nName         : zoxide\nSize         : 400 k\n"
        self.assertEqual(packages.parse_info_blocks(text, "Name", "Size"), {"zoxide": 400 * 1024})

    def test_apt_show(self):
        text = "Package: fonts-hack\nVersion: 3.003-4\nInstalled-Size: 1316\n\nPackage: zoxide\nInstalled-Size: 1003\n"
        self.assertEqual(packages.parse_apt_show(text), {"fonts-hack": 1316 * 1024, "zoxide": 1003 * 1024})

    def test_rpm_query(self):
        text = "git-2.47.0-1.fc43.x86_64\npackage zoxide is not installed\n"
        self.assertEqual(packages.parse_rpm_query(text, ["git", "zoxide"]), ["zoxide"])

    def test_dpkg_status(self):
        text = "git ii \nzoxide un \n"
        self.assertEqual(packages.parse_dpkg_status(text, ["git", "zoxide", "chafa"]), ["zoxide", "chafa"])

    def test_install_commands_skip_extras(self):
        self.assertIn("--setopt=install_weak_deps=False", packages.Dnf().install_command(["x"]))
        self.assertIn("--no-recommends", packages.Zypper().install_command(["x"]))
        self.assertIn("--no-install-recommends", packages.Apt().install_command(["x"]))
        self.assertIn("--needed", packages.Pacman().install_command(["x"]))

    def test_root_programs_are_called_by_full_path(self):
        # A program running as you could put its own pkexec, sudo or pacman first in your PATH.
        for backend in (packages.Pacman(), packages.Dnf(), packages.Zypper(), packages.Apt()):
            for has_pkexec, elevate in ((True, "/usr/bin/pkexec"), (False, "/usr/bin/sudo")):
                with self.subTest(backend=backend.name, elevate=elevate):
                    runner = mock.Mock()
                    with mock.patch.object(packages.os, "access", return_value=has_pkexec):
                        backend.install(["x"], runner)
                    command = runner.run.call_args.args[0]
                    self.assertEqual(command[0], elevate)
                    program = [arg for arg in command[1:] if "=" not in arg]  # past env's VAR=value
                    if program[0] == "/usr/bin/env":
                        program = program[1:]
                    self.assertTrue(program[0].startswith("/usr/bin/"), command)


if __name__ == "__main__":
    unittest.main()
