## What does this change?

<!-- A short description. Link the issue it fixes, e.g. "Fixes #12". -->

## Type of change

- [ ] New preset
- [ ] Bug fix
- [ ] New feature
- [ ] Documentation / maintenance

## Checklist

- [ ] Tests pass: `PYTHONPATH=src python -m unittest discover -s tests -t .`
- [ ] Presets validate: `PYTHONPATH=src python -m plasmify validate`
- [ ] `ruff check .` and `ruff format --check .` are clean
- [ ] I tested this on a real KDE Plasma 6 session (distribution: ______)
- [ ] New packages are added to `package_map.py` for every distribution family
- [ ] *Restore* puts back every setting this change modifies
- [ ] No new background services, AUR/COPR/PPA packages or runtime dependencies

### Security (see SECURITY.md and "Security rules" in CONTRIBUTING.md)

- [ ] New preset fields are validated in `schema.py` with the narrowest check, and have an abuse test in `tests/test_security.py`
- [ ] Paths built from preset data use `safe_child()`; commands are argument lists, never `shell=True`
- [ ] Downloads go through `plasmify.downloads` (no direct `urllib`/`curl`)
- [ ] No new network access outside applying a preset, no telemetry
- [ ] Any `# noqa: S...` has a comment explaining why it is safe

### For new presets

- [ ] Palette taken from the theme's official source (linked in the file)
- [ ] All assets are downloaded from where they are published; nothing is committed to this repo
- [ ] All URLs are on trusted hosts (`plasmify show <preset>` lists no untrusted host)
- [ ] Every wallpaper has a `credit` (and `license` when known) and is at least 1920×1080
- [ ] Screenshot attached below
- [ ] Install size reported by the app: `Installs … packages (… MB)`

## Screenshots

<!-- Drag and drop images here. -->
