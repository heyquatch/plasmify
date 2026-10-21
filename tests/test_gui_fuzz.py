"""Click around the Customize tab like a curious user, hundreds of times, and check after every
action that nothing broke.

Every control is changed in a seeded random order (panel builder, colours, logos, dropped
files, Surprise me, fix buttons, switching presets, the reload that follows Apply). After each
action: no exception was raised anywhere (a raising slot would close the real app), the
settings form a valid preset, Apply is never enabled with a plan for other settings than the
ones shown, and saved tweaks come back unchanged after a reload.

Set PLASMIFY_FUZZ_STEPS to run longer (default 300) and PLASMIFY_FUZZ_SEED for another path.
Skipped without PyQt6.
"""

import json
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PyQt6.QtCore import QThread
    from PyQt6.QtGui import QColor, QImage
    from PyQt6.QtWidgets import QApplication, QCheckBox, QComboBox
except ImportError:  # pragma: no cover
    QApplication = None

STEPS = int(os.environ.get("PLASMIFY_FUZZ_STEPS", "300"))
SEED = int(os.environ.get("PLASMIFY_FUZZ_SEED", "20261018"))
TEXTS = ["HELLO", "Café Crème", "", "x" * 40, 'q"}\n{', "$1 boom", "ÇĞİÖŞÜ", "a\tb", "🙂"]
ART = ["[0-0]\n/)  )", "$1 colour", "line\twith tab", "\n".join(["x"] * 30), "ok", ""]


@unittest.skipIf(QApplication is None, "PyQt6 is not installed")
class CustomizeFuzzTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.maxDiff = None
        self.errors = []
        self.old_hook = sys.excepthook
        sys.excepthook = lambda kind, value, tb: self.errors.append(f"{kind.__name__}: {value}")
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.images = []
        for index, colour in enumerate(("#d08770", "#5e81ac", "#a3be8c")):
            image = QImage(64, 40, QImage.Format.Format_RGB32)
            image.fill(QColor(colour))
            path = root / f"picture-{index}.png"
            image.save(str(path))
            self.images.append(path)
        from plasmify.gui import customize, main_window

        self.patches = [
            mock.patch.object(main_window, "TWEAKS_FILE", root / "tweaks.json"),
            mock.patch.object(customize, "CUSTOM_ROOT", root / "custom"),
        ]
        for patch in self.patches:
            patch.start()
        self.window = main_window.MainWindow()
        self.main = main_window

    def tearDown(self):
        self.idle()
        self.window.close()
        for patch in self.patches:
            patch.stop()
        sys.excepthook = self.old_hook
        self.tmp.cleanup()

    # ----- helpers -----------------------------------------------------------------
    def idle(self) -> None:
        for thread in self.window.findChildren(QThread):
            thread.wait(20000)
        for _ in range(3):
            self.app.processEvents()

    def actions(self, rng: random.Random):
        w, c = self.window, self.window.customize
        editor = c.panel_editor

        def combo(box: QComboBox, activated: bool = False):
            def act():
                if not box.isEnabled() or box.count() == 0:
                    return
                index = rng.randrange(box.count())
                if box.itemData(index) == "custom":  # opens a colour dialog
                    return
                item = getattr(box.model(), "item", lambda _i: None)(index)
                if item is not None and not item.isEnabled():
                    return  # people cannot pick disabled entries
                box.setCurrentIndex(index)
                if activated:
                    box.activated.emit(index)

            return act

        def check(box: QCheckBox):
            return lambda: box.isEnabled() and box.setChecked(not box.isChecked())

        def slider(widget):
            return lambda: (
                widget.isEnabled() and widget.setValue(rng.randint(widget.slider.minimum(), widget.slider.maximum()))
            )

        def spin(box):
            return lambda: box.isEnabled() and box.setValue(rng.randint(box.minimum(), box.maximum()))

        def tiles(widget):
            return lambda: widget.isEnabled() and widget.set_value(rng.choice(widget._buttons)[1])

        def preset():
            w.gallery.setCurrentRow(rng.randrange(w.gallery.count()))

        def edge():
            editor._select_edge(rng.choice(("top", "bottom", "left", "right")))

        def add_widget():
            from plasmify import panels

            editor._add_widget(rng.choice(list(panels.WIDGETS)))

        def remove_widget():
            if editor.widget_list.count():
                editor.widget_list.setCurrentRow(rng.randrange(editor.widget_list.count()))
                editor._remove_widget()

        def move_widget():
            items = editor.widget_list
            if items.count() > 1:
                item = items.takeItem(rng.randrange(items.count()))
                items.insertItem(rng.randrange(items.count() + 1), item)
                editor._edit()

        def move_wallpaper():
            from PyQt6.QtCore import QModelIndex

            walls = c.walls_list
            if walls.count() > 1:
                source, target = rng.randrange(walls.count()), rng.randrange(walls.count() + 1)
                walls.model().moveRow(QModelIndex(), source, QModelIndex(), target)
                QApplication.processEvents()

        def remove_wallpapers():
            walls = c.walls_list
            walls.clearSelection()
            for row in rng.sample(range(walls.count()), min(walls.count(), rng.randint(1, 3))):
                walls.item(row).setSelected(True)
            c.remove_wallpapers()

        def module():
            items = c.modules
            item = items.item(rng.randrange(items.count()))
            item.setCheckState(
                c.modules.item(0).checkState().__class__.Unchecked
                if item.checkState().value
                else c.modules.item(0).checkState().__class__.Checked
            )

        def drop():
            role = rng.choice(("logo", "splash_background", "splash_logo", "login_background"))
            c._use_file(role, rng.choice(self.images))

        def forget():
            c._forget_file(rng.choice(("logo", "splash_background", "splash_logo", "login_background")))

        def fixes():
            from plasmify import compat

            effective = w.effective_preset()
            if effective is not None:
                for hint in compat.check(effective, c.tiling_now()):
                    if hint.fix:
                        c.apply_fix(hint.id)

        def accent():
            if c.preset is not None:
                c.set_accent(rng.choice(list(c.preset.palette.colors.values())))

        def apply_and_reload():
            """What a successful Apply does to the window, without touching the desktop."""
            self.idle()
            if w.effective_preset() is None:
                return  # Apply is disabled for invalid settings, so nothing would be saved
            before = c.overrides()
            self.main.remember_tweaks(w.current.id, before, c.asset_dir())
            w.reload_presets(select=w.current.id)
            self.idle()
            after = w.customize.overrides()
            if json.dumps(before, sort_keys=True) != json.dumps(after, sort_keys=True):
                changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
                detail = "\n".join(f"  {k}: {before.get(k)!r} -> {after.get(k)!r}" for k in changed)
                self.errors.append(f"tweaks changed after reload:\n{detail}")

        named = {
            "preset": preset,
            "surprise": c.surprise,
            "reset": lambda: c.load(c.preset),
            "starter": tiles(editor.starters),
            "edge": edge,
            "toggle panel": check(editor.enabled),
            "panel style": combo(editor.style_box),
            "panel length": combo(editor.length_box),
            "panel align": combo(editor.align_box),
            "panel hiding": combo(editor.hiding_box),
            "panel thickness": slider(editor.thickness),
            "panel floating": check(editor.floating),
            "add widget": add_widget,
            "remove widget": remove_widget,
            "move widget": move_widget,
            "corners": slider(c.radius),
            "opacity": slider(c.panel_opacity),
            "border": combo(c.border),
            "panel colour": combo(c.panel_colour, activated=True),
            "tiling": check(c.tiling),
            "open close": combo(c.open_close),
            "minimise": combo(c.minimize),
            "desktop switch": combo(c.desktop_switch),
            "wobbly": check(c.wobbly),
            "blur": check(c.blur),
            "blur strength": slider(c.blur_strength),
            "speed": combo(c.speed),
            "title bar": combo(c.titlebar),
            "borders": combo(c.borders),
            "glass look": tiles(c.glass_tiles),
            "see-through": slider(c.transparency),
            "focused solid": check(c.focused_solid),
            "fall apart": check(c.fall_apart),
            "sheet": check(c.sheet),
            "cube": check(c.cube),
            "wobbliness": slider(c.wobbly_level),
            "blur saturation": slider(c.blur_saturation),
            "blur grain": slider(c.blur_noise),
            "moving opacity": slider(c.moving_opacity),
            "dim inactive": slider(c.dim_inactive),
            "shimmer": combo(c.shimmer),
            "shimmer colour": combo(c.shimmer_colour),
            "shimmer light": combo(c.shimmer_light),
            "shimmer motion": combo(c.shimmer_motion),
            "shimmer density": combo(c.shimmer_density),
            "shimmer fps": combo(c.shimmer_fps),
            "shimmer always": check(c.shimmer_always),
            "wallpapers": lambda: c.use_wallpapers(rng.sample(self.images, rng.randint(1, 3))),
            "add wallpapers": lambda: c.add_wallpapers(rng.sample(self.images, rng.randint(1, 2))),
            "reorder wallpapers": move_wallpaper,
            "remove wallpapers": remove_wallpapers,
            "reset wallpapers": c.reset_wallpapers,
            "interval": combo(c.interval),
            "slideshow order": combo(c.order),
            "splash style": tiles(c.splash_style),
            "splash background": combo(c.splash_background),
            "splash logo": combo(c.splash_logo),
            "splash text": lambda: c.splash_text.setText(rng.choice(TEXTS)[:40]),
            "splash progress": check(c.splash_progress),
            "accent": accent,
            "icons": combo(c.icon_theme),
            "folders": combo(c.folders),
            "cursor": combo(c.cursor_theme),
            "cursor size": slider(c.cursor_size),
            "ui font": combo(c.ui_font),
            "mono font": combo(c.mono_font),
            "ui size": spin(c.ui_size),
            "mono size": spin(c.mono_size),
            "greeting": check(c.greeting),
            "user host": check(c.user_host),
            "logo kind": combo(c.logo_kind),
            "logo builtin": combo(c.logo_builtin),
            "logo text": lambda: c.logo_text.setText(rng.choice(TEXTS)),
            "ascii sample": combo(c.ascii_sample),
            "ascii art": lambda: c.logo_ascii.setPlainText(rng.choice(ART)),
            "logo colour": combo(c.logo_colour),
            "logo direction": combo(c.logo_direction),
            "logo size": combo(c.logo_size),
            "module": module,
            "prompt": combo(c.prompt),
            "zoxide": check(c.zoxide),
            "terminal opacity": slider(c.opacity),
            "terminal blur": check(c.term_blur),
            "font size": spin(c.font_size),
            "login": tiles(c.login_background),
            "drop file": drop,
            "forget file": forget,
            "fix buttons": fixes,
            "apply and reload": apply_and_reload,
        }
        return named

    def check_invariants(self, step: int, name: str) -> None:
        from plasmify import compat, presets, tomlwrite

        w = self.window
        where = f"step {step} ({name})"
        self.assertEqual(self.errors, [], f"{where}: exception")
        overrides = w.customize.overrides()
        json.dumps(overrides)  # saved tweaks must be serialisable
        effective = w.effective_preset()
        if effective is None:
            self.assertFalse(w.apply_button.isEnabled(), f"{where}: Apply enabled for invalid settings")
            try:
                presets.customized(w.current, overrides, w.customize.asset_dir())
            except presets.PresetError as exc:
                # Only text the user typed may be invalid (the tab shows a hint for it);
                # no click, drop or slider may ever produce settings Plasmify refuses.
                typed = ("shell.logo_text", "shell.logo_ascii")
                problems = [p for p in str(exc).split("; ") if not p.startswith(typed)]
                self.assertEqual(problems, [], f"{where}: the UI produced invalid settings")
            return
        compat.check(effective, w.customize.tiling_now())
        presets.customized(w.current, overrides, w.customize.asset_dir())
        tomlwrite.dumps(effective.data)  # Save as my preset / Share must work
        if step % 7 == 0:
            self.idle()
            if w.apply_button.isEnabled():
                self.assertEqual(w.plan.preset.data, effective.data, f"{where}: Apply would use a stale plan")
            w.preview.grab()

    def test_random_clicking_never_breaks_anything(self):
        rng = random.Random(SEED)  # noqa: S311 (test data)
        actions = self.actions(rng)
        names = sorted(actions)
        self.idle()
        used = set()
        emitted = []
        self.window.customize.changed.connect(lambda: emitted.append(1))
        for step in range(STEPS):
            name = rng.choice(names)
            used.add(name)
            before = json.dumps(self.window.customize.overrides(), sort_keys=True)
            emitted.clear()
            actions[name]()
            self.app.processEvents()
            after = json.dumps(self.window.customize.overrides(), sort_keys=True)
            if before != after and not emitted and name not in ("preset", "apply and reload"):
                self.fail(f"step {step} ({name}) changed the settings without telling the window:\n{before}\n{after}")
            self.check_invariants(step, name)
        self.assertGreaterEqual(len(used), len(names) * 0.9, f"never tried: {set(names) - used}")


if __name__ == "__main__":
    unittest.main()
