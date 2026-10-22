# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Security

- Download addresses in presets must name their host in plain letters a-z, digits, dots and dashes, so a look-alike such as `gіthub.com` (with a Cyrillic і) or a percent-encoded name can no longer pass for a trusted host in the question about untrusted hosts. International names still work written as `xn--` punycode.
- The Restore window's tooltips show preset names as plain text, like the rest of the app.

## [0.1.0] - 2026-10-21

The first public release, under the GNU General Public License, version 3 or later.

### Added

- Fourteen presets, ten dark, two light and two made of glass. Each one sets colours, app style,
  icons, cursor, fonts, panels, wallpapers, window effects, terminal colours and the shell prompt
  in one go, and every part can be left out.
- A Customize tab with a live preview: panels, docks and islands on any screen edge, glass for Qt
  apps, window effects, Shimmer living wallpapers, accent and fonts, the terminal logo and
  greeting, the splash screen and the login screen background.
- A one-click switch between light and dark, generated from the same palette.
- The theme studio: a complete theme from your own pictures, analysed on your computer.
- *Save as my preset*, *Save current desktop* and single `.plasmify` files for sharing.
- A backup before every apply. *Restore* returns your desktop to any of them, including your setup
  from before Plasmify.
- A command line for presets, light and dark, settings, backups and sharing.
- Support for the Arch, Fedora, openSUSE and Debian/Ubuntu families.

### Security

- Shared presets are plain, strictly checked data: they cannot run code, choose where files go or
  contact unknown servers without asking you.
- Packages come only from your distribution's official repositories. Root is used only for
  missing packages and the login screen.
- Downloads use HTTPS and are recorded locally with their size and SHA-256 (`plasmify log`).

Everything in detail: https://plasmify.github.io
