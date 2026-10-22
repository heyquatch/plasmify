# Security and privacy

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Use GitHub's
[private vulnerability reporting](https://github.com/heyquatch/plasmify/security/advisories/new)
instead. You can expect a first answer within a week. Only the latest release receives fixes.

## Threat model

Plasmify is designed so that **applying a preset someone shared with you is safe**, even if
that person is hostile. A preset is data, never code.

| Asset | Threat | Defence |
| --- | --- | --- |
| Your shell / terminal | A preset injects extra lines (e.g. `shell` in kitty.conf, `[terminal.shell]` in alacritty.toml) through a font name or number | Strict schema: every value has a type, range and character set; no control characters or line breaks anywhere (`src/plasmify/schema.py`) |
| Your files | A name like `../../Documents` makes Plasmify delete or overwrite something outside its folders | Names are restricted to `[A-Za-z0-9._+-]`, `..` is rejected, and every create/delete goes through `safe_child()` which refuses anything that is not a direct child of the expected folder |
| Your files | A downloaded archive contains `../` paths, symlinks or is a decompression bomb | Paths checked before extraction, tar uses `filter="data"`, total extracted size is capped |
| Your system | A preset installs an arbitrary package (on Debian, installing a server starts it) | Only packages from the reviewed list in `package_map.py` can be requested. `pkexec` (or `sudo` where there is no polkit) and the package manager are called by absolute path, so a program running as you cannot slip its own in through your `PATH` |
| Your privacy | A preset points to the author's own server to log the IP of everyone who applies it | Only trusted hosts (GitHub, Codeberg, GitLab) are contacted silently; any other host needs explicit approval (a question in the GUI or the terminal, or `--allow-host` on the CLI), and downloads from unapproved hosts are refused at download time. Host names must be plain ASCII letters, digits, dots and dashes, so a look-alike (`gіthub.com` with a Cyrillic і) or a percent-encoded name cannot pass for a trusted host in that question; international names are shown as their `xn--` form |
| Your privacy | Redirect to plain HTTP or another host | Download redirects are checked with the same rules (HTTPS only, approved hosts); git follows no redirects at all (`http.followRedirects=false`), so it only talks to the host that was checked |
| Your trust | A shared preset pretends to be a built-in one | User presets can never replace a built-in preset id; the GUI shows where each preset comes from |
| You | Preset text contains HTML (fake links, misleading formatting) | All preset text is rendered as plain text |
| Your files | A shared `.plasmify` bundle contains scripts, extra folders, `../` names or huge files | Only `preset.toml` and image files in `assets/` are accepted; names are validated, every image is checked by its magic bytes, sizes and file counts are capped, and the whole bundle is validated before anything is written |
| Your memory and disk | A small picture (download, bundle file or your own drop) claims a huge size or thousands of frames, so decoding it eats all memory (a decompression bomb) | PNG, JPEG, WebP and GIF only, judged by their magic bytes. Before a picture is used, its width, height and frame count are read from the header without decoding any pixels (by plain byte reads, and by Qt's decoder when PyQt6 is there): more than 8192 pixels on a side or more than 1500 frames is refused (`MAX_IMAGE_SIDE`, `MAX_IMAGE_FRAMES` in `downloads.py`). An animated picture reaches the splash and login screens as a still PNG of its first frame, at most 3840×2160 |
| Your themes | Glass replaces a Kvantum theme you made or installed yourself that happens to have the name Plasmify wants (`<theme>-glass` or `plasmify-glass`) | Every theme folder Plasmify writes for glass carries a `made-by-plasmify.txt` marker; a folder of that name without the marker, or a symlink, is never replaced, and glass is skipped with a note instead. Links inside a theme are never copied |
| Artists | A bundle redistributes someone else's wallpapers | Wallpapers downloaded from a preset are always referenced by their original URL when a preset is saved or shared; only the user's own image files are bundled, with a reminder to share only what they may share |
| Your terminal | A logo text or logo name carries extra JSON or command-line options into the fastfetch config | Logo text is limited to 12 characters from a fixed set; logo names must match a strict pattern and exist in `fastfetch --list-logos`; logo files follow the same rules as bundled wallpapers |
| Your system | The login screen step runs as root, and a preset, a download or another program running as you tries to abuse it | Everything the login screen step does as root is in `rootops.py`. Root never reads a path: the picture is read by Plasmify as you (refusing symlinks, non-images and files over 50 MB) and handed to root on standard input; root writes it under a name from a fixed list into a folder only root can write. Restore never installs a file from your (writable) backup folder: backups keep the values of the keys Plasmify writes (two for Plasma Login, one for SDDM), and root only writes those back after they match strict patterns. The scripts are fixed text with positional arguments, re-checked inside the script; `pkexec`, `sh` and every program the scripts run, `kwriteconfig6` included, are called by absolute path, never looked up in a `PATH`. If `/usr/bin/kwriteconfig6` is missing, Plasmify says so before it asks for your password |
| Your disk | A shared theme archive or repository fills the home partition | Archives: total size, file count and (while the index is read, not after) per-archive limits; hard links refused (each would become a full copy). Git checkouts are measured and refused above the limit, and one apply may download at most 1.5 GB in total |
| Your files | A repository contains symlinks that point outside it, or a Git LFS config naming another server | Both git commands run with `core.symlinks=false` and LFS disabled; theme copies leave links out; repository paths cannot start with `-` and reach git on standard input |
| Your terminal | Text from a server (error messages) or a file drives the terminal with escape sequences | The command line output replaces control characters; preset text never contains them |
| You | A shared preset pretends to be another one: a name padded with spaces, invisible or right-to-left characters, or an id that maps to the same colour scheme as an installed preset ("nord-1" vs "nord1") | Bidirectional and zero-width characters are refused, whitespace runs collapse, "(yours)" is shown before the name, ids are words joined by single dashes, and an id whose colour scheme name clashes with an installed preset is refused |
| Your desktop | A panel layout injects code into the Plasma shell script | Widget ids are looked up in the fixed `panels.WIDGETS` table and every other value is a number, boolean or `json.dumps` string; the schema refuses unknown widgets, edges and sizes |
| Your desktop | Splash text or file names break out of the generated QML | Every value is inserted with `json.dumps`; files are referenced by fixed names inside the package |
| Your terminal | A drawn logo carries fastfetch colour codes or escape sequences | Drawn logos refuse `$`, tabs and every control character except new lines, and are capped at 24 lines of 80 characters |
| Git | A repository source pulls submodules or uses another protocol | `git clone` runs with `protocol.allow=never` (HTTPS only) and `--no-recurse-submodules`; repo paths cannot leave the checkout |

Known limits, on purpose: the trusted hosts (GitHub, Codeberg, GitLab) are trusted for privacy,
not for content; theme files from them are installed like any theme you download yourself. Fonts a
distribution does not package come from the google/fonts repository or the font's own GitHub
release, with no pinned checksum; the download history records their SHA-256. Polonium, the one
piece of downloaded code, is pinned to a release and checked against its SHA-256 before it is
installed. The template for glass (Catppuccin's Kvantum theme, used when a preset has no Kvantum
theme of its own) is pinned the same way: both files come from one fixed commit and must match
their SHA-256 (`TEMPLATE_COMMIT` and `TEMPLATE_SHA256` in `glass.py`) before they are recoloured; a
mismatch keeps Breeze. Shimmer living wallpapers are rendered on your computer from pictures that
are already there; it contacts no server.

How this is tested: `tests/test_security.py` (one test per defence and per past finding),
`tests/test_security_fuzz.py` (hostile text in every free-text field must leave every generated
config file, script and QML file with exactly its normal structure), `tests/test_bundle_fuzz.py`
(hundreds of corrupted and malicious bundles must be refused cleanly and never write outside the
preset folder) and the root scripts are executed as you against a temporary folder in
`tests/test_screens.py`.

Out of scope: the upstream projects themselves (a theme author who ships a malicious Kvantum
SVG or cursor), your distribution's packages, and anything you run outside Plasmify.

## Privacy

- **No telemetry, analytics, accounts or background services.** Plasmify only runs while you use it.
- **Plasmify downloads nothing until you apply a preset**, and then fetches what that preset needs.
  Your package manager talks to your distribution's mirrors to install missing packages, and may
  also do so while Plasmify checks which ones are missing. Nothing is uploaded.
- Downloads use a generic Python User-Agent; Plasmify does not identify itself or its version.
- To route downloads through a proxy or Tor, set `HTTPS_PROXY` (honoured by Python and git).
- fastfetch does not print `user@host` unless `show_user_host = true` (the Customize box), even
  if a preset lists the line itself, so screenshots of a themed terminal don't leak your user and
  machine name.
- The greeting never goes online by itself: the weather line (it asks wttr.in over plain HTTP each
  time a terminal opens) is taken out of imported presets, with a note. A preset that lists it
  anyway (say, one dropped into the presets folder) asks first, like an untrusted host
  (`--allow-host wttr.in` on the command line); only ticking it yourself in Customize skips that.
- Every download (URL, size, SHA-256 and where it was saved; for git sources the repository, the
  path inside it and the commit) and every package installation is recorded in
  `~/.local/state/plasmify/activity.jsonl`, purely locally, so users can audit what a preset
  brought in. `plasmify log --clear` deletes it.
- Images are analysed for "Create from wallpaper" on the computer; nothing is uploaded.
- Shimmer makes living wallpapers on the computer, with Qt alone; it needs no network at all, and
  its preview in the Customize tab uses only pictures already on your disk.
- Backups, downloads and settings stay in your home directory:
  `~/.local/state/plasmify`, `~/.local/share/plasmify`, `~/.config/plasmify`, plus the usual places
  for themes, icons and fonts (`~/.config/Kvantum`, `~/.local/share/icons`,
  `~/.local/share/fonts/plasmify`). The exception is the
  login screen: if you change it, its picture goes to `/usr/share/wallpapers/plasmify-login` and
  its setting to the login manager's configuration.
