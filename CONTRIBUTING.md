# Contributing to Plasmify

Thanks for wanting to help! Plasmify is small on purpose, so contributions are easy to
review. You can contribute a **new preset**, a **bug fix** or a **feature**.

## Ground rules

1. **No bloat.** No background services, no AUR/COPR/PPA packages, no new runtime dependencies
   beyond Python's standard library and PyQt6. Every package a preset installs must be
   justified in the pull request.
2. **Published sources only.** Assets are downloaded at apply time from where they are published.
   Never commit wallpapers, icon themes or cursors to this repository.
3. **Stock Plasma only.** Layouts use widgets that ship with Plasma 6.
4. **Reversible.** Every configuration file and generated folder Plasmify changes must be covered
   by a step's `touched_paths()` so that *Restore* can undo it. Downloads are left to
   `plasmify clean`.
5. **Same license.** Plasmify is licensed under the GNU GPL, version 3 or later. By sending a
   pull request you agree that your contribution is released under the same terms.

## Security rules

Presets are shared between strangers, so **every value in a preset is untrusted input**.
Read the threat model in [SECURITY.md](SECURITY.md) once before your first code change. In short:

1. **Validate first.** Every new preset field gets an entry in `src/plasmify/schema.py` with the
   narrowest possible check (`integer`, `number`, `boolean`, `one_of`, `NAME`, `FONT`, `url`, ...).
   Plain `text()` is only for values that are *displayed*, never for values written into config files.
2. **Add the abuse case.** For every new field, add a test to `tests/test_security.py` showing
   that a malicious value is rejected.
3. **Paths only through `safe_child()`.** Never build a path from preset data with `/` or f-strings;
   use `safe_child(base, name)` from `plasmify.system` for anything created or deleted.
4. **Commands as lists.** Use `ctx.runner.run([...])`, never `shell=True`, `os.system` or string
   commands. Put `--` before user-influenced arguments where the tool supports it.
5. **Downloads only through `plasmify.downloads`.** It enforces HTTPS, the trusted host list,
   safe redirects and size limits, and records every download in the local history
   (`plasmify.activity`). Do not call `urllib`, `requests` or `curl` directly.
6. **No new packages without review.** Packages are only installable if listed in
   `package_map.py`, with names for every distribution family.
7. **Show preset text as plain text** (`plain_label()`, `message()` in `plasmify.gui.safe`).
8. **No network calls outside "apply"**, no telemetry, no update checks, no analytics.
9. **Panel scripts run inside plasmashell.** Build them only with `panel_script()` (values via
   `json.dumps`, widgets looked up in `panels.WIDGETS`), and extend `tests/test_layout_script.py`
   when you add a layout option; it runs every combination and hundreds of random layouts against
   a mock of Plasma's scripting API. Then run the sandbox tests below against a real plasmashell.
10. **Root actions live in `rootops.py`,** apart from package installs in `packages.py`. Root
   never reads a path a normal process could replace (send data on standard input) and never
   installs files from the user's folders; new values root writes need a strict pattern in
   `valid_value()` and a test in `tests/test_screens.py`.
11. **Bundles are untrusted too.** Anything read from a `.plasmify` file goes through
   `bundle.read_bundle()`; add hostile-bundle cases to `tests/test_sharing.py`.

`ruff` runs the flake8-bandit security rules (`S`) in CI. If you really need an exception, add
`# noqa: Sxxx` with a comment explaining why it is safe; reviewers will ask about it.

Changes to the security-sensitive files (`schema.py`, `downloads.py`, `system.py`,
`packages.py`, `package_map.py`, `templates/`) need a maintainer's review (see `.github/CODEOWNERS`).

## Development setup

```bash
git clone https://github.com/heyquatch/plasmify.git
cd plasmify
# Arch:          sudo pacman -S --needed python python-pyqt6 git
# Fedora:        sudo dnf install python3 python3-pyqt6 git
# openSUSE:      sudo zypper install python3 python3-PyQt6 git
# Debian/Ubuntu: sudo apt install python3 python3-pyqt6 git
PYTHONPATH=src python -m plasmify          # run the GUI from the checkout
PYTHONPATH=src python -m plasmify --help   # or the CLI
```

Before opening a pull request:

`tests/test_gui_fuzz.py` clicks through the Customize tab in a seeded random order and checks
after every action that nothing raised, the settings are valid, Apply never uses a stale plan and
saved tweaks survive a reload. Run it longer and with other seeds when you touch the GUI:
`PLASMIFY_FUZZ_STEPS=3000 PLASMIFY_FUZZ_SEED=7 QT_QPA_PLATFORM=offscreen PYTHONPATH=src:. python -m unittest tests.test_gui_fuzz`.

```bash
PYTHONPATH=src python -m unittest discover -s tests -t .   # tests
PYTHONPATH=src python -m plasmify validate                 # all built-in presets
ruff check . && ruff format --check .                      # lint (pip install ruff)
PYTHONPATH=src python scripts/check_packages.py            # package names exist on your distribution
shellcheck install.sh
```

Try destructive changes with `--dry-run` first, e.g.
`PYTHONPATH=src python -m plasmify apply nord-minimal --dry-run`.

### Testing against a real Plasma, safely

Anything that touches panels, wallpapers, the splash screen or plasmashell restarts must be
tried in the sandbox, a throwaway Plasma desktop that cannot touch yours:

```bash
PLASMIFY_SANDBOX=1 PLASMIFY_SANDBOX_SHOTS=/tmp/shots PYTHONPATH=src:. python -m unittest discover -s tests/integration -t . -v
python3 tools/sandbox.py apply catppuccin-anime     # one preset, one screenshot
```

`test_patterns.py` covers the design patterns (edge subsets, all pairs of panel options, every
widget, 108 panel style combinations); new layout or panel style options belong in its option
tables. The whole run takes about fifteen minutes and keeps screenshots in `PLASMIFY_SANDBOX_SHOTS`;
please look at them before you open the pull request. CI cannot run it (no Plasma there), so
mention in the pull request that you did.

## Adding a preset

1. Copy an existing file from `src/plasmify/presets/` and read [docs/presets.md](docs/presets.md).
2. Use the theme's **official** palette and link to it in a comment.
3. Pick wallpapers of at least 1920×1080 from sources that credit the artist where possible,
   and fill in `credit` (and `license` when known) for every image.
4. Run `plasmify validate`, then apply it on a real Plasma 6 session and check every step.
5. Open a pull request using the template. Include a screenshot and the
   "Installs N packages (X MB)" line shown by the app.

## Testing on other distributions

Plasmify is developed on Arch-based systems. Fedora, openSUSE and Debian/Ubuntu support is
implemented and the package names are checked in CI, but **real-desktop reports are very
welcome**. Run `plasmify apply <preset> --dry-run` and then a real apply, and tell us in
Discussions what worked and what didn't (include `cat /etc/os-release` and `plasmashell --version`).

Most distribution-specific code lives in three places:

- `src/plasmify/distro.py`: detecting the family from `/etc/os-release`
- `src/plasmify/packages.py`: one backend per package manager (output parsers are unit-tested)
- `src/plasmify/package_map.py`: package names and font fallbacks

`install.sh` picks the package manager on its own, and `system.py` (the Plasma version check)
and `steps/effects.py` (the desktop cube hint) name a few packages per distribution.

## Code changes

- Code, comments, commit messages and docs are in English.
- Follow the existing style; `ruff` enforces formatting and imports.
- One logical change per pull request. Add or update tests in `tests/` when behaviour changes.
- New steps live in `src/plasmify/steps/`, subclass `Step` and are registered in `steps/__init__.py`.
- Use `ctx.runner` for every command that changes the system so `--dry-run` keeps working.

## Documentation

The user documentation is plain HTML in `docs/wiki`, published at <https://plasmify.github.io> on every push to `main`.
Update the page that describes your change in the same pull request and run
`python3 scripts/check_wiki.py`. [Editing this documentation](https://plasmify.github.io/contributing.html#docs)
explains the rules.

## Commit messages

Use the imperative mood with an optional scope, for example:

```
presets: add Everforest Forest
icons: fall back to Papirus when Papirus-Dark is missing
```

## Reporting bugs

Open an issue with the bug template. Please include `plasmify --version`, your
distribution, `plasmashell --version`, and the output of the failing command
(`plasmify apply <preset>` prints every step).

## Releases (maintainers)

1. Update `__version__` in `src/plasmify/__init__.py` and `CHANGELOG.md`.
2. Tag `vX.Y.Z` and push the tag. The release workflow creates the GitHub release.
