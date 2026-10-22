<div align="center">

<img src="src/plasmify/assets/plasmify.svg" width="96" alt="Plasmify logo">

# Plasmify

**One-click, bloat-free themes for KDE Plasma 6.**

Pick a look, untick what you don't want, press *Apply*. Colours, app style, icons,
cursor, fonts, panels, wallpapers, effects, terminal and shell prompt, all matched,
in under a minute. Change your mind? Your settings are backed up before every apply, and
*Restore* puts them back.

[![CI](https://github.com/heyquatch/plasmify/actions/workflows/ci.yml/badge.svg)](https://github.com/heyquatch/plasmify/actions/workflows/ci.yml)
[![License: GPL-3.0-or-later](https://img.shields.io/badge/license-GPL--3.0--or--later-blue.svg)](LICENSE)
![Plasma 6](https://img.shields.io/badge/KDE%20Plasma-6-1d99f3)
![Distributions](https://img.shields.io/badge/Arch%20%7C%20Fedora%20%7C%20openSUSE%20%7C%20Debian%2FUbuntu-supported-1793d1)

**[Documentation](https://plasmify.github.io)**: install, customize, back up, uninstall and more.

https://github.com/user-attachments/assets/897a508b-0a13-4691-827c-8c6d915ec19e

</div>

---

## What you get

- **Fourteen ready-made looks**, ten dark, two light and two made of glass, from Catppuccin and
  Tokyo Night to Kanagawa and Synthwave. [See them all](https://plasmify.github.io/#presets).
- **Make it yours.** Put panels and docks on any screen edge, turn apps to glass, add window
  effects and living wallpapers, change the accent, fonts and terminal greeting, all with a live
  preview. Or build a theme from your own picture. [How](https://plasmify.github.io/customize.html)
- **Light or dark in one click**, generated from the same palette.
- **Undo anything.** Every apply is backed up first; *Restore* returns your desktop to an earlier
  moment, including how it looked before Plasmify.
- **Share it.** Any look becomes a single file; post yours in
  [Discussions → Presets](https://github.com/heyquatch/plasmify/discussions/categories/presets).

## The bloat-free promise

- **No background services.** No daemon, autostart entry, tray icon, telemetry or update checks.
- **Official packages only.** Missing packages come from your distribution's own repositories,
  never the AUR, COPR or PPAs, and you see the exact list and size before you apply.
- **No root for theming.** Everything lives in your home folder; your password is only asked for
  missing packages and, if you want it, the login screen background.
- **Stock Plasma only.** Panels use widgets that ship with Plasma, so updates don't break them.
- **Safe to share.** A shared preset is plain data: it can't run code or contact unknown servers
  without asking. [Security and privacy](https://plasmify.github.io/security.html)

## Install

```bash
git clone https://github.com/heyquatch/plasmify.git
cd plasmify
./install.sh
```

Then open **Plasmify** from the application menu. It needs KDE Plasma 6 and Python 3.11 or newer
with PyQt6; the installer adds what is missing. It supports the Arch, Fedora, openSUSE and
Debian/Ubuntu families ([which ones are tested](https://plasmify.github.io/install.html#requirements));
if you run openSUSE or Debian/Ubuntu, a quick report in
[Discussions](https://github.com/heyquatch/plasmify/discussions) helps a lot. Arch users can also
[build a package](https://plasmify.github.io/install.html#arch).

To remove it: `plasmify restore original` (your setup from before Plasmify), then
`./install.sh --uninstall`.

## Command line

Everyday things work from a terminal too:

```bash
plasmify theme nord                    # apply Nord Minimal (a part of the name is enough)
plasmify theme --toggle                # switch between light and dark
plasmify set accent ff79c6 blur on     # change settings of the applied theme
plasmify restore latest                # undo the last apply
```

Every command and option: [Command line](https://plasmify.github.io/cli.html).

## Contributing

Presets are single TOML files, documented in [docs/presets.md](docs/presets.md); good ones are
very welcome as pull requests. See [CONTRIBUTING.md](CONTRIBUTING.md), and
[SECURITY.md](SECURITY.md) for reporting a vulnerability privately.

## Credits

<details>
<summary>Plasmify stands on the shoulders of these projects. Thank you!</summary>

[Catppuccin](https://github.com/catppuccin) · [Tokyo Night](https://github.com/folke/tokyonight.nvim) ·
[Nord](https://www.nordtheme.com) · [Gruvbox](https://github.com/morhetz/gruvbox) ·
[Rosé Pine](https://rosepinetheme.com) · [Dracula](https://draculatheme.com) · [Kanagawa](https://github.com/rebelot/kanagawa.nvim) · [Everforest](https://github.com/sainnhe/everforest) ·
[SynthWave '84](https://github.com/robb0wen/synthwave-vscode) · [One Dark](https://github.com/atom/atom/tree/master/packages/one-dark-syntax) and
[One Light](https://github.com/atom/atom/tree/master/packages/one-light-syntax) · [Papirus](https://github.com/PapirusDevelopmentTeam/papirus-icon-theme) ·
[Bibata](https://github.com/ful1e5/Bibata_Cursor) · [XCursor-pro](https://github.com/ful1e5/XCursor-pro) · [Kvantum](https://github.com/tsujan/Kvantum) ·
[Starship](https://starship.rs) · [fastfetch](https://github.com/fastfetch-cli/fastfetch) ·
[zoxide](https://github.com/ajeetdsouza/zoxide) · [Polonium](https://github.com/zeroxoneafour/polonium) ·
[Nerd Fonts](https://www.nerdfonts.com) · the Kvantum ports of
[Catppuccin](https://github.com/catppuccin/kvantum) (also the template for glass), [Rosé Pine](https://github.com/rose-pine/kvantum),
[Tokyo Night](https://github.com/0xsch1zo/Kvantum-Tokyo-Night) and
[Gruvbox](https://github.com/sachnr/gruvbox-kvantum-themes), the [Dracula](https://github.com/dracula/gtk) Kvantum theme and cursors,
the [Sweet](https://github.com/EliverLara/Sweet) and [Layan](https://github.com/vinceliuice/Layan-kde) Kvantum themes,
the [Catppuccin cursors](https://github.com/catppuccin/cursors), [Rosé Pine cursors](https://github.com/rose-pine/cursors) and [Dracula folder colours](https://github.com/dracula/papirus-folders) ·
the fonts Inter, JetBrains Mono, Roboto, Roboto Mono, Fira Code, Noto Sans, Nunito, Ubuntu and Hack ·
the wallpapers of [KDE Plasma](https://github.com/KDE/plasma-workspace-wallpapers), [elementary OS](https://github.com/elementary/wallpapers)
and [Ubuntu Budgie](https://github.com/UbuntuBudgie/budgie-wallpapers), public domain prints by Katsushika Hokusai and
Utagawa Hiroshige via [floating-world](https://github.com/AntouanK/floating-world), the collections
[catppuccin-wallpapers](https://github.com/zhichaoh/catppuccin-wallpapers), [aesthetic-wallpapers](https://github.com/D3Ext/aesthetic-wallpapers),
[walls](https://github.com/dharmx/walls) and [Rosé Pine wallpapers](https://github.com/rose-pine/wallpapers), and the other
wallpaper collections and artists listed under *Credits* in each preset.

The Powerline prompt is based on the Tokyo Night and Pastel Powerline presets of
[Starship](https://starship.rs) (ISC License, Copyright (c) Starship Contributors).

</details>

Themes and wallpapers are downloaded from where their authors publish them, never re-hosted, and
remain the property of their authors. If you are an author and want an image credited differently
or removed, please [open an issue](https://github.com/heyquatch/plasmify/issues/new/choose).

## License

Copyright (C) 2026 quatch

Plasmify is free software: you can redistribute it and/or modify it under the terms of the
[GNU General Public License](LICENSE) as published by the Free Software Foundation, either
version 3 of the License, or (at your option) any later version. It is distributed in the hope
that it will be useful, but without any warranty; see the license for details.
