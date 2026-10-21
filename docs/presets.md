# Preset reference

A preset is a TOML file describing a complete desktop look. Every section except
`schema`, `meta` and `palette` is optional: leave a section out and Plasmify leaves
that part of the desktop alone.

Validate a preset with:

```bash
plasmify validate path/to/preset.toml
```

## Rules

Presets are validated strictly because people share them: unknown keys are errors, numbers must
be numbers within range, names may only use letters, digits and `. _ + -`, and text may not contain
line breaks or control characters. See [SECURITY.md](../SECURITY.md) for why.

- **Only `https://` sources, preferably on GitHub, Codeberg or GitLab.** Plasmify refuses plain HTTP.
  Other hosts work, but users are asked before they are contacted (the host learns their IP),
  and built-in presets must not use them.
- **Link to where it is published.** Use the author's repository or release page, or the collection
  that hosts the picture, never a re-upload. Name the artist in `credit` when you know it.
- **Official repositories only.** No AUR, COPR or PPA. Refer to packages by their **Arch Linux name**;
  `src/plasmify/package_map.py` translates them for Fedora, openSUSE and Debian/Ubuntu. A new package
  needs an entry there for every family (and a fallback download if it is a font that some family
  doesn't package). `scripts/check_packages.py` verifies the names in CI.
- **Keep it light.** Prefer regular fonts over Nerd Fonts (Plasmify adds a 2 MB symbols font for prompt
  icons). Every new package needs a reason.
- **Credit every wallpaper**, including its license when known.

## `schema` and `[meta]`

```toml
schema = 1

[meta]
id = "my-preset"            # letters, digits and dashes; also the file name
name = "My Preset"
description = "One sentence that sells the look."
tags = ["dark", "minimal"]
```

## `[palette]`

All values are `#rrggbb`. These keys are required:

| Key | Used for |
| --- | --- |
| `base` | window background, terminal background |
| `mantle` | slightly darker: panels, sidebars, title bars, list views |
| `crust` | darkest: tooltips, inactive title bars, text on accent |
| `surface` | buttons, inputs, terminal "black" |
| `overlay` | muted foreground, inactive title text |
| `text` | primary text |
| `subtext` | secondary text |
| `accent` | selection, focus, highlights, prompt |
| `red` `green` `yellow` `blue` `magenta` `cyan` `orange` | status colours, links, ANSI colours |

Optional: `bright_<colour>` for bright ANSI variants (otherwise derived), `bright_black`, `bright_white`.

The KDE colour scheme, Konsole/Alacritty/kitty colours, Starship prompt and fastfetch
colours are all generated from this palette.

## `[style]`: application style (optional)

Without this section the Breeze style is used, which follows the generated colour scheme.
To use a Kvantum theme from a git repository:

```toml
[style.kvantum]
repo = "https://github.com/catppuccin/kvantum"
path = "themes/catppuccin-mocha-mauve"   # folder containing <name>.kvconfig and <name>.svg
name = "catppuccin-mocha-mauve"
```

Add `ref = "branch-or-tag"` when the theme does not live on the repository's default branch
(Synthwave Neon uses `ref = "nova"` for Sweet).

Use the repository's own address. Plasmify does not follow git redirects, so a repository that
moved needs its new address, and a GitLab address needs its `.git` ending.

or from an archive:

```toml
[style.kvantum]
url = "https://raw.githubusercontent.com/rose-pine/kvantum/master/dist/rose-pine-rose.tar.gz"
name = "rose-pine-rose"
```

### Glass

```toml
[style]
glass = "glass"        # off (default), frosted, glass or clear
transparency = 45      # optional: how see-through windows are, 10 to 70 (percent)
focused_solid = false  # optional: keep the window in use solid (true when left out)
```

Glass makes Dolphin, System Settings and other Qt apps see-through, so the wallpaper and the
windows behind them show through. Breeze cannot do that, so glass always uses Kvantum (installed
from the distribution when it is missing). There are four ready-made looks; the window calls them
Solid, Frosted, Glass and Clear:

| `glass` | In the window | What it does | Default `transparency` (dark / light palette) |
| --- | --- | --- | --- |
| `off` | Solid | Solid windows, as without the key. | |
| `frosted` | Frosted | A little see-through, with a strong blur behind windows. | 25 / 18 |
| `glass` | Glass | Clearly see-through, with a light blur, so the wallpaper stays recognisable. | 40 / 30 |
| `clear` | Clear | See-through, with no blur behind windows. | 50 / 38 |

`transparency` is how see-through window backgrounds are, a whole number from 10 to 70 percent
(the window calls it *See-through*). Leave it out and the look's default is used, which is lower for
light palettes because dark text over a bright wallpaper fades fast; such a preset follows the
defaults when it is switched between light and dark. A value you set stays the same in both
modes. While glass is off, `transparency` does nothing.

`focused_solid` keeps the window you are working in solid, so only the windows behind it are
see-through (the window calls it *Keep the window you use solid*). Click another window and it turns
solid, while the one you left turns see-through. Without it, wherever windows overlap the text of
the window behind shows through the text of the one in front, which is hard to read with little
blur. It is `true` when left out, in every look; set it to `false` to see through the window you
use as well. Like `transparency`, it does nothing while glass is off.

Menus are half as see-through as windows, but never less than 10 % or more than 30 %. Menus and
tooltips are blurred in every look, Clear included, so their text stays readable. In Kvantum's
terms, the glass theme sets `translucent_windows`, `reduce_window_opacity` (the transparency,
negative with `focused_solid`: Kvantum then applies it to inactive windows only),
`reduce_menu_opacity` (the menus), `blurring` (on for frosted and glass, off for clear) and
`popup_blurring` (always on). With `focused_solid` it also sets `no_inactiveness=false`, so Kvantum
tells inactive windows apart.

`subtle` is the older name of `frosted` and is still accepted, so older presets and saved changes
keep working; it is applied as `frosted`. The window and `plasmify set` only offer the four looks
above.

Where the theme comes from:

- **With `[style.kvantum]`**, Plasmify writes a sibling theme `<name>-glass` next to the preset's
  theme in `~/.config/Kvantum`: a full copy of its settings with the translucency keys changed, plus
  a copy of its SVG. While `focused_solid` is on, the SVG's `window-normal` element (the
  background of the window in use) is made fully opaque, because some themes, such as Layan, draw
  it a little see-through themselves. Inactive windows keep the theme's own look: a theme without
  a `window-normal-inactive` element of its own gets a copy of the original under that name.
- **Without it** (a Breeze preset), Plasmify downloads Catppuccin's Kvantum theme (MIT) as a
  template, Mocha for dark palettes and Latte for light ones, and recolours it from the palette into
  `plasmify-glass`. The download is pinned to one commit and checked against its SHA-256
  (`TEMPLATE_COMMIT` and `TEMPLATE_SHA256` in `src/plasmify/glass.py`); if it does not match,
  Breeze stays.

Every folder Plasmify writes for glass holds a `made-by-plasmify.txt` file. A theme folder of the
same name without it (one you installed yourself) is never replaced.

Glass also sets what the look needs in the other sections, but only in sections the preset
already has (it never switches another step on). First the blur:

| | `frosted` | `glass` | `clear` |
| --- | --- | --- | --- |
| `effects.blur` | `true` | `true` | left as it is |
| `effects.blur_strength` | at least 10 | 2 to 4 | left as it is |
| `effects.blur_noise` | at least 5 | 0 to 2 | left as it is |
| `terminal.blur` | `true` | `true` | left as it is |

Frosted wants a strong blur; glass keeps it light so you can still tell what is behind a window.
Panels and the terminal turn see-through along with the windows. With `t` as the transparency in
use (`transparency`, or the look's default):

- `panel_style.opacity` is at most `1 - t/100`, but never below 0.35;
- `terminal.opacity` is at most `1 - 0.6 × t/100`, but never below 0.5.

With the defaults on a dark palette that gives:

| | `frosted` (25 %) | `glass` (40 %) | `clear` (50 %) |
| --- | --- | --- | --- |
| Menus | 12 % | 20 % | 25 % |
| `panel_style.opacity`, at most | 0.75 | 0.6 | 0.5 |
| `terminal.opacity`, at most | 0.85 | 0.76 | 0.7 |

Values that already fit stay as they are, and one outside the range moves to its nearest end, so
applying a preset twice changes nothing more. A key the preset leaves out counts as the step's own
default (`blur_strength` 10, `blur_noise` 4, panel opacity 0.85, terminal opacity 0.9) and is only
written when that does not fit.

Glass makes window backgrounds see-through. Many apps paint their main area themselves (the text
of an editor, a web page, a document), and that area stays solid. Dolphin is set up so its file
view turns see-through as well, so it shows the effect everywhere. Photo, video and drawing apps
(Krita, OBS, LibreOffice, Gwenview, showFoto, Kdenlive, Shotcut, digiKam, VLC, SMPlayer) stay solid
on top of the theme's own list. Kvantum keeps windows solid at a screen scale between whole numbers
(such as 125 % or 150 %), and apps that are already open turn to glass after a restart. When the
Customize tab or the light/dark switch moves a preset to the other mode, its own Kvantum theme is
dropped (it paints one mode only) but glass, `transparency` and `focused_solid` stay, using
`plasmify-glass` coloured from the new palette.

## `[icons]`

```toml
[icons]
theme = "Papirus-Dark"
folder_color = "nordic"   # any papirus-folders colour, e.g. blue, pink, nordic, paleorange
```

Plasmify builds a small per-user overlay theme that inherits from `theme` and only swaps
the folder icons, so no root access is needed and package updates don't undo it.

For folder colours that are not part of Papirus, add a source with the coloured SVGs in
`<size>/places/` layout:

```toml
folder_color = "cat-mocha-mauve"
folder_source = { repo = "https://github.com/catppuccin/papirus-folders", path = "src" }
```

## `[cursor]`

Without `url`, the cursor theme must already be installed on the computer (that is what the
Customize tab does when you pick one of your installed cursors).

```toml
[cursor]
name = "Bibata-Modern-Ice"   # directory name inside the archive
url = "https://github.com/ful1e5/Bibata_Cursor/releases/download/v2.0.7/Bibata-Modern-Ice.tar.xz"
size = 24
```

## `[fonts]`

```toml
[fonts]
ui = "Inter"
ui_size = 10
mono = "JetBrains Mono"
mono_size = 10
packages = ["inter-font", "ttf-jetbrains-mono"]   # optional
```

`packages` is optional. Without it the fonts must already be installed (the Customize tab only
offers installed fonts, so it leaves `packages` out when you pick your own).

## `[layout]`

| Key | Default | Meaning |
| --- | --- | --- |
| `name` | | a starting point: `topbar-dock`, `bottom-panel` or `top-panel` (optional when parts are given) |
| `top_bar` | from `name` | top bar with launcher, tray and clock |
| `bottom` | from `name` | `none`, `panel` (classic task bar) or `dock` (centred, floating) |
| `dock_hiding` | `dodge` | `dodge` (hides when a window touches it), `autohide` or `always` |
| `clock` | `center` | clock position in the top bar: `center` or `right` |
| `pager` | `true` | virtual desktop switcher |
| `task_labels` | `false` | window titles in the task bar instead of icons only |
| `floating` | `true` | floating panels |
| `bar_height` | `32` | panel height in pixels |
| `dock_height` | `56` | dock height (`topbar-dock`) |
| `launcher_icon` | KDE logo (`start-here-kde`) | `https://` URL of an image for the application launcher |

At least one of `top_bar` and `bottom` must be shown. Pinned launchers are picked automatically from what is installed: file manager,
terminal, default browser and System Settings.

### Any panels: `panels`

Instead of the keys above, `panels` lists up to four panels, one per screen edge. This is what
the Customize tab's panel builder writes:

```toml
[[layout.panels]]
edge = "top"                 # top, bottom, left or right (one panel per edge)
thickness = 32               # 20 to 128 pixels
widgets = ["launcher", "pager", "spacer", "clock", "spacer", "tray"]

[[layout.panels]]
edge = "left"
style = "dock"               # bar (default), dock (a floating island sized to its widgets) or islands
align = "center"             # start, center or end (for docks and length = "fit")
hiding = "always"            # always (keeps its space), dodge (hides when windows touch it) or autohide
widgets = ["launcher", "tasks"]
```

With `style = "islands"` the panel is split into separate pieces at each `spacer`: what comes before
the first one sits at the start of the edge, between two at the centre, after the last at the end (at
most two spacers). `["launcher", "pager", "spacer", "clock", "spacer", "tray"]` gives a menu on the
left, the clock in the middle and the tray on the right; `length` and `align` do not apply.

Other keys: `length` (`fill` or `fit`), `floating` (`true` or `false`). Widgets, in order:
`launcher`, `menu`, `dashboard`*, `tasks`, `tasks_labels`, `windowlist`, `pager`, `spacer`,
`separator`, `clock`, `analog_clock`, `tray`, `media`, `cpu`, `memory`, `network`, `showdesktop`,
`trash`, `logout`, `weather`*, `colorpicker`*. All ship with Plasma; the ones marked * come from
kdeplasma-addons and are left out (with a warning) when it is not installed.

## `[panel_style]`

```toml
[panel_style]
radius = 14      # corner radius in pixels, 0 to 30
opacity = 0.8    # 0.2 to 1.0
border = "subtle"  # none, subtle or accent
padding = 4      # space between the panel edge and its widgets, 0 to 12
colour = "mantle"  # panel background: a palette colour name (follows the theme) or "#rrggbb"
```

The radius is capped just below half the thinnest panel (a 32 px bar allows 14 px), because
Plasma squeezes larger corners. Corners are only visible while a panel floats.

Plasmify generates a minimal Plasma style containing only the panel background; everything else
comes from Breeze, and the colours follow the palette.

Without this section the panels keep the user's own Plasma style. A style Plasmify generated for
an earlier preset is not kept: the style the user had before Plasmify comes back (from the backup
that `plasmify restore original` puts back), or the default one (your distribution's, else Breeze,
in the theme's colours) if they had none. A style the user picked themselves is left as it is.

## `[wallpaper]`

```toml
[wallpaper]
mode = "slideshow"   # or "single"
interval = 1800      # seconds between slides, 60 to 86400
order = "listed"     # listed (in this order) or shuffle
lock_screen = true   # also use the first image on the lock screen

[[wallpaper.images]]
url = "https://raw.githubusercontent.com/owner/repo/main/image.png"
credit = "Image title by Artist, via owner/repo"
license = "CC BY-SA 4.0"   # optional
```

Use images of at least 1920×1080, ideally 2560×1440 or larger. Up to 30 images.

An image can also be a file shipped with the preset instead of a URL:

```toml
[[wallpaper.images]]
file = "01-sunset.png"   # a plain file name: letters, digits, . _ + - ; .png .jpg .jpeg .webp .gif
credit = "My own wallpaper"
```

Such presets are shared as `.plasmify` bundles (a zip with `preset.toml` and `assets/<file>`);
*File → Share* and `plasmify export` create them. Built-in presets must only use URLs.

### Animated wallpapers

Animated GIF and animated WebP pictures work as wallpapers (downloaded or bundled): Plasma plays
them. A picture may be at most 8192 pixels on a side and have at most 1500 frames (a minute at 25
frames per second); larger ones are refused before they are decoded. The splash and login screens
show still pictures only, so they get a PNG of the first frame. Plasma pauses moving wallpapers
while a window fills the screen; `shimmer_always` (below) keeps them playing.

### Shimmer: living wallpapers

```toml
[wallpaper]
shimmer = "petals"         # none (default) or a style below
shimmer_colour = "magenta" # a palette colour name or #rrggbb; default accent
shimmer_light = "auto"     # auto (default), glint, rays, flare, pulse or none
shimmer_motion = "auto"    # auto (default), breathe, drift or still
shimmer_density = "normal" # low, normal (default) or high
shimmer_fps = 12           # frames per second: 12 (default) or 24
shimmer_always = false     # keep playing while a window fills the screen
```

With a `shimmer` style, Plasmify turns the still wallpapers into seamlessly looping animated WebP
files when the preset is applied: a six-second loop at 1920×1080, made on the computer with Qt
(a few seconds per picture). Only the first six pictures are brought to life; the rest of a
longer slideshow still plays, as still pictures. They are kept in `~/.local/share/plasmify/wallpapers/<preset>/living/`,
so applying again with the same settings makes nothing new. Pictures that already move (GIF,
animated WebP) are used as they are. The lock screen keeps the first still picture. Shimmer needs
only PyQt6 with Qt's image formats plugin (WebP); without it the wallpapers stay still, with a note.

| `shimmer` | What appears over the picture |
| --- | --- |
| `none` | Nothing: the pictures stay still (the default). |
| `sparkle` | Four-point stars that twinkle on slow orbits, fewer in the middle. |
| `embers` | Small glowing dots that rise, sway and flicker. |
| `petals` | Sakura petals tumbling down on a diagonal breeze. |
| `snow` | Soft snowflakes falling with depth. |
| `fireflies` | Glowing dots wandering on looping curves, lighting up and dimming. |
| `bokeh` | Large soft circles of out-of-focus light drifting around the edges. |
| `starfall` | A few shooting stars with long tails over faint twinkling stars. |
| `rain` | Thin slanted streaks of rain, light enough never to grey the picture. |
| `magic` | A glowing magic circle in a corner, with runes and circling sparks. |
| `speedlines` | Anime focus lines from the edges, the middle stays clear. |
| `calm` | No particles: only the light and the motion. |

The effects favour the edges and corners, so the subject in the middle stays clear.

- `shimmer_colour` tints the particles and the light: a palette colour name (`accent`, `yellow`,
  `magenta`...), which follows the theme, or a fixed `#rrggbb`.
- `shimmer_light` is a light that plays over the picture, under the particles: `glint` (a shine
  that sweeps along an arc once per loop), `rays` (soft rays from a top corner), `flare` (a lens
  flare with a long horizontal streak), `pulse` (a glow that breathes in from the edges) or `none`.
- `shimmer_motion` is how the picture itself moves: `breathe` (a slow zoom in and out), `drift`
  (a slow pan) or `still`.
- `shimmer_density` is how many particles there are; `calm` has none, so it ignores this key.
- `shimmer_fps` is how many frames per second the loop plays: `12` (the default) is lighter, with
  files half the size and half the render time; `24` is smoother.
- `shimmer_always = true` keeps the wallpapers moving while a window fills the screen (Plasma
  pauses them otherwise). It costs a little processor time and battery.

`auto` picks what suits the style:

| Style | Light | Motion |
| --- | --- | --- |
| `sparkle` | `glint` | `breathe` |
| `embers` | `pulse` | `breathe` |
| `petals` | `rays` | `drift` |
| `snow` | `rays` | `drift` |
| `fireflies` | `none` | `breathe` |
| `bokeh` | `flare` | `drift` |
| `starfall` | `none` | `breathe` |
| `rain` | `none` | `still` |
| `magic` | `none` | `breathe` |
| `speedlines` | `none` | `breathe` |
| `calm` | `glint` | `drift` |

## `[effects]`

| Key | Values | Default |
| --- | --- | --- |
| `blur` | `true` / `false` | `true` |
| `blur_strength` | 1 to 15 | `10` |
| `blur_noise` | 0 to 14: a little grain in the blur, so it looks like frosted glass | `4` |
| `blur_saturation` | 100 to 500 (percent): how colourful things look through the blur; 100 keeps their colours | `100` |
| `open_close` | `scale`, `glide`, `fade`, `none` | `scale` |
| `minimize` | `magiclamp`, `squash`, `none` | `magiclamp` |
| `desktop_switch` | `slide`, `fade`, `none` | `slide` |
| `wobbly` | `true` / `false` | `false` |
| `wobbly_level` | 0 to 4: how much windows wobble, from barely (0) to very wobbly (4); used while `wobbly = true` | `2` |
| `fall_apart` | `true` / `false`: closing windows break into pieces that fall away | `false` |
| `sheet` | `true` / `false`: dialogs slide down out of the title bar of their window | `false` |
| `cube` | `true` / `false`: the desktop cube, see below | `false` |
| `moving_opacity` | 0.3 to 1.0: how solid a window stays while you move or resize it (1.0 is solid) | `1.0` |
| `dim_inactive` | `0` (off) or 10 to 90 (percent): darkens the windows you are not using | `0` |
| `tiling` | `true` / `false`; enables or disables Polonium tiling (installed per user from a pinned, checksummed release if missing) | as before Plasmify |
| `animation_speed` | KDE animation duration factor (1.0 = normal) | as before Plasmify |

A key left out gets its default, so a preset never keeps part of the one applied before it.
"As before Plasmify" is the user's own setting: what they had before their first theme (kept in
the backup that `plasmify restore original` puts back), or KDE's default if they had none. A
see-through `moving_opacity` turns KWin's translucency effect on; at `1.0` the effect goes back to
how the user had it. A preset without an `[effects]` table leaves all of this alone. The blur keys only
apply while `blur` is on. `fall_apart` and `sheet` work next to `open_close`: fall apart takes over
closing normal windows and sheet animates dialogs, while the `open_close` effect keeps animating
everything else.

The desktop cube shows your virtual desktops on the sides of a cube that you spin with Meta+C.
KWin itself does not ship it, and Plasmify does not install it: it needs `kdeplasma-addons` (Arch,
Fedora), `kdeplasma6-addons` (openSUSE) or `kwin-addons` (Debian, Ubuntu). Without it, `cube = true` is left out with a note naming the package. It needs at least 3
virtual desktops; Plasmify never adds desktops itself and says so when there are fewer (add them in
System Settings, Virtual Desktops).

Window decorations (optional; left out means "as before Plasmify", see above):

| Key | Values | Meaning |
| --- | --- | --- |
| `titlebar_buttons` | `right`, `left`, `close`, `none` | where window buttons sit (`left` is macOS style) |
| `window_borders` | `none`, `tiny`, `normal`, `large` | window frame thickness |

## `[terminal]`

```toml
[terminal]
opacity = 0.9
blur = true
padding = 12
font_size = 12
```

## `[shell]`

```toml
[shell]
prompt = "powerline"   # powerline, minimal or none
zoxide = true
fastfetch_logo = "distro"   # or an https:// image URL, drawn with chafa
greeting = true             # show the fastfetch system info when a terminal opens (fish, bash, zsh)
logo = "text"               # distro, builtin, image, text or ascii
logo_builtin = "arch_small" # for logo = "builtin": any name from `fastfetch --list-logos`
logo_text = "HELLO"         # for logo = "text": up to 12 letters, digits, spaces and ! ? . - _ + # @ & *
logo_ascii = """
   ,___,
   [0-0]
"""                         # for logo = "ascii": up to 24 lines of 80 characters, no $ or tabs
logo_colour = "gradient"    # a scheme: gradient, rainbow, sunset, ocean, forest, fire, candy,
                            # pastel, neon, aurora or mono; one colour: accent, text, red, green,
                            # yellow, blue, magenta, cyan or orange; or original (fastfetch's own
                            # colours, for distro and builtin logos)
logo_direction = "down"     # which way a scheme runs: down, up, right, left or diagonal
logo_file = "logo.png"      # for logo = "image": a picture shipped in the .plasmify bundle
fastfetch_modules = ["os", "kernel", "cpu", "memory", "break", "colors"]
                            # the greeting's lines in order; see FASTFETCH_MODULES in schema.py
logo_size = "medium"        # small, medium or large
show_user_host = false      # print user@host in the greeting (off by default for screenshot privacy)
```

## `[splash]`

The screen shown while Plasma starts.

```toml
[splash]
style = "plasmify"        # plasmify (generated from the palette), breeze or none
background = "palette"    # palette (the darkest palette colour), wallpaper or image
background_file = "splash.png"   # for background = "image": a file in the bundle
logo = "distro"           # distro, preset (the preset's picture), plasma, image or none
logo_file = "logo.png"    # for logo = "image": a file in the bundle
text = "Welcome back"     # up to 40 characters, shown under the logo
progress = true           # thin progress bar
```

Values are written into the generated QML as JSON strings, so no text can change the QML itself.

Without this section the user keeps their own splash screen. A splash Plasmify generated for an
earlier preset is not kept: the one the user had before Plasmify comes back (from the backup that
`plasmify restore original` puts back), or the default one (your distribution's, else Breeze). A
splash the user picked themselves is left as it is.

## `[login]`

The login screen background, for Plasma Login or SDDM with the Breeze theme.

```toml
[login]
background = "wallpaper"   # wallpaper (the first wallpaper of the preset) or image
background_file = "login.png"   # for background = "image": a file in the bundle
```

This is the only step that writes system files, so it asks for your password once. It runs one
fixed script through polkit: the image is copied to `/usr/share/wallpapers/plasmify-login/` under
a name Plasmify chooses, and the login background setting is changed (two keys for Plasma Login,
one for SDDM). The backup keeps the previous values of those keys, not the whole file, and
*Restore* writes them back (again with your password).
