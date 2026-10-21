"""The main window: preset gallery on the left, details and components on the right."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from PyQt6.QtCore import QEvent, QProcess, QSize, Qt, QThread, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QFont, QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from plasmify import (
    APP_NAME,
    DOCS_URL,
    HOMEPAGE,
    __version__,
    bundle,
    capture,
    compat,
    engine,
    modes,
    packages,
    presets,
    schema,
)
from plasmify import record as tweak_record
from plasmify.gui import shimmer_preview
from plasmify.gui.customize import CustomizePanel, custom_folder, custom_root
from plasmify.gui.dialogs import ApplyDialog, CaptureDialog, CreditsDialog, HistoryDialog, RestoreDialog
from plasmify.gui.preview import PreviewWidget, preview_pixmap
from plasmify.gui.safe import ElidedLabel, confirm, message, plain_label
from plasmify.gui.widgets import AdaptiveRow, FlowLayout, UnsavedBar, guard_wheel, primary_button
from plasmify.gui.wizard import WallpaperWizard
from plasmify.gui.workers import PlanWorker
from plasmify.presets import Preset
from plasmify.steps import ALL_STEPS, splash
from plasmify.steps.wallpaper import cached_file
from plasmify.system import CACHE_HOME, PLASMIFY_STATE, CommandError, Runner, environment_problems

THUMB = QSize(256, 144)
# The preview beside the preset's details: between these widths (16:9), stacked above them
# when the window is too narrow for both.
PREVIEW_MIN, PREVIEW_MAX, DETAILS_MIN = 420, 600, 330  # the details need room for a title
SUMMARY_STYLE = "color: palette(placeholder-text);"
WARNING_STYLE = "color: #da4453;"
PLASMIFY_CACHE = CACHE_HOME / "plasmify"
STEP_ICONS = {
    "colors": "preferences-desktop-color",
    "style": "preferences-desktop-theme-applications",
    "icons": "preferences-desktop-icons",
    "cursor": "preferences-cursors",
    "fonts": "preferences-desktop-font",
    "wallpaper": "preferences-desktop-wallpaper",
    "layout": "preferences-desktop",
    "panelstyle": "preferences-desktop-theme-global",
    "effects": "preferences-desktop-effects",
    "terminal": "utilities-terminal",
    "shell": "utilities-terminal",
    "splash": "preferences-system-splash",
    "login": "system-users",
}


TWEAKS_FILE = PLASMIFY_STATE / "applied-tweaks.json"
ERROR_LOG = PLASMIFY_STATE / "errors.log"


def log_error(context: str) -> None:
    """Append the current exception to Plasmify's error log (never raises)."""
    import traceback

    try:
        ERROR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with ERROR_LOG.open("a") as handle:
            handle.write(f"--- {context}\n{traceback.format_exc()}\n")
    except OSError:
        pass


def applied_tweaks() -> dict[str, dict]:
    """Customizations that were applied, per preset id (see plasmify.record)."""
    return tweak_record.applied_tweaks(TWEAKS_FILE)


KEEP = tweak_record.KEEP  # remember_tweaks: leave this part of the record as it is
changed_settings = tweak_record.changed_settings  # the unsaved bar counts as the tab does


def remember_tweaks(
    preset_id: str, overrides: dict, custom_dir: Path | None, steps: list[str] | None = None, own_mode: Any = KEEP
) -> None:
    """Record what was applied for a preset, shared with the command line (see plasmify.record)."""
    tweak_record.remember_tweaks(preset_id, overrides, custom_dir, steps, own_mode, path=TWEAKS_FILE)


def live_mode() -> bool | None:
    """Whether the desktop's window colour is dark right now (None if unknown)."""
    return tweak_record.live_mode(Runner())


def cached_wallpaper(preset: Preset) -> QPixmap | None:
    """The first wallpaper of the slideshow that is on this computer (own file or download)."""
    for image in preset.section("wallpaper").get("images", []):
        path = cached_file(preset, image)
        pixmap = QPixmap(str(path)) if path is not None else QPixmap()
        if not pixmap.isNull():
            return pixmap.scaled(
                1280, 720, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
            )
    return None


class Swatches(QWidget):
    def __init__(self, preset: Preset, size: int = 14, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        for key in ("base", "surface", "text", "accent", "red", "green", "yellow", "blue", "magenta"):
            dot = QLabel()
            dot.setFixedSize(size, size)
            dot.setToolTip(f"{key}: {preset.palette.colors[key]}")
            dot.setStyleSheet(
                f"background: {preset.palette.colors[key]}; border-radius: {size // 2}px;"
                "border: 1px solid rgba(127,127,127,0.35);"
            )
            row.addWidget(dot)
        row.addStretch(1)


class PresetCard(QFrame):
    def __init__(self, preset: Preset, active: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(4)
        thumb = QLabel()
        thumb.setPixmap(preview_pixmap(preset, THUMB, cached_wallpaper(preset)))
        layout.addWidget(thumb)
        # The origin goes first, so a long name can never push it out of sight.
        prefix = "" if preset.builtin else "(yours)  "
        title = plain_label(prefix + preset.name + ("   ● Active" if active else ""))
        font = title.font()
        font.setWeight(QFont.Weight.DemiBold)
        title.setFont(font)
        layout.addWidget(title)
        layout.addWidget(Swatches(preset, 10))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1180, 760)
        self.presets: list[Preset] = []
        self.current: Preset | None = None
        self.plan = None
        self.plan_worker: PlanWorker | None = None
        self._mode_worker: PlanWorker | None = None  # planning a light/dark switch
        self._mode_token = 0  # which switch request a finished plan belongs to
        self._apply_when_planned: str | None = None  # apply this preset as soon as its plan is ready
        self._closing = False
        self._swatch_colours: dict[str, str] = {}
        # Customize edits not applied yet, per preset id, kept while browsing other presets.
        self._unsaved: dict[str, tuple[dict, Path | None]] = {}
        # What the selected preset looks like as applied (or as the preset itself): the unsaved
        # bar shows while the Customize tab differs from it.
        self._baseline: dict | None = None
        self._baseline_tweaks: dict = {}  # the tweaks that make it, to count what differs
        self._selecting = False
        self.problems = environment_problems()

        self._build_menu()
        self._build_ui()
        self.reload_presets()

    # ----- construction -------------------------------------------------
    def _build_menu(self) -> None:
        menu = self.menuBar().addMenu("&File")
        create = QAction(QIcon.fromTheme("color-management"), "&Create from wallpaper…", self)
        create.setShortcut("Ctrl+N")
        create.triggered.connect(self.create_from_wallpaper)
        capture_action = QAction(QIcon.fromTheme("document-save-as"), "&Save current desktop as preset…", self)
        capture_action.triggered.connect(self.save_current_desktop)
        share = QAction(QIcon.fromTheme("document-share"), "S&hare selected preset…", self)
        share.setShortcut("Ctrl+S")
        share.triggered.connect(self.share_preset)
        restore = QAction(QIcon.fromTheme("edit-undo"), "&Restore a backup…", self)
        restore.triggered.connect(self.open_restore)
        importing = QAction(QIcon.fromTheme("document-import"), "&Import preset…", self)
        importing.setShortcut("Ctrl+O")
        importing.triggered.connect(self.import_preset)
        folder = QAction(QIcon.fromTheme("folder-open"), "Open &presets folder", self)
        folder.triggered.connect(self.open_presets_folder)
        history = QAction(QIcon.fromTheme("view-history"), "Download &history…", self)
        history.triggered.connect(lambda: HistoryDialog(self).exec())
        self.clean_action = QAction(QIcon.fromTheme("edit-clear-all"), "&Clean up unused downloads", self)
        self.clean_action.triggered.connect(self.clean_up)
        quit_action = QAction(QIcon.fromTheme("application-exit"), "&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.close)
        for action in (create, capture_action, share, importing):
            menu.addAction(action)
        menu.addSeparator()
        for action in (restore, folder, history, self.clean_action):
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("&Help")
        about = QAction(QIcon.fromTheme("help-about"), f"&About {APP_NAME}", self)
        about.triggered.connect(self.about)
        docs = QAction(QIcon.fromTheme("help-contents"), "&Documentation", self)
        docs.setShortcut("F1")
        docs.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(DOCS_URL)))
        site = QAction(QIcon.fromTheme("internet-web-browser"), "Project &website", self)
        site.triggered.connect(lambda: QDesktopServices.openUrl(QUrl(HOMEPAGE)))
        help_menu.addAction(docs)
        help_menu.addAction(site)
        help_menu.addAction(about)

    def _build_ui(self) -> None:
        splitter = QSplitter()
        self.setCentralWidget(splitter)

        self.gallery = QListWidget()
        self.gallery.setMinimumWidth(THUMB.width() + 40)
        self.gallery.setMaximumWidth(THUMB.width() + 60)
        self.gallery.setSpacing(4)
        self.gallery.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.gallery.setStyleSheet(
            "QListWidget::item { border: 2px solid transparent; border-radius: 10px; }"
            "QListWidget::item:selected { background: transparent; border: 2px solid palette(highlight); }"
            "QListWidget::item:hover:!selected { border: 2px solid palette(mid); }"
        )
        self.gallery.currentItemChanged.connect(self._on_select)
        splitter.addWidget(self.gallery)

        details = QWidget()
        outer = QVBoxLayout(details)
        outer.setContentsMargins(18, 14, 18, 14)
        outer.setSpacing(10)

        if self.problems:
            banner = plain_label("⚠ " + "  ".join(self.problems))
            banner.setWordWrap(True)
            banner.setStyleSheet(
                "background: rgba(237, 21, 21, 0.15); border: 1px solid rgba(237, 21, 21, 0.5);"
                "border-radius: 6px; padding: 8px;"
            )
            outer.addWidget(banner)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        self.body_layout = QVBoxLayout(body)
        self.body_layout.setContentsMargins(0, 0, 6, 0)
        self.body_layout.setSpacing(10)

        top = AdaptiveRow(PREVIEW_MIN, PREVIEW_MAX, DETAILS_MIN, spacing=18, share=0.62)
        self.header_row = top
        self.preview = PreviewWidget()
        self.preview.setMinimumHeight(PREVIEW_MIN * 9 // 16)
        top.addWidget(self.preview)
        info = QVBoxLayout()
        info.setSpacing(8)
        top.add_layout(info)
        self.body_layout.addLayout(top)

        self.title = ElidedLabel(shrink=0.7)  # a long name gets smaller before it is cut short
        title_font = self.title.font()
        title_font.setPointSizeF(title_font.pointSizeF() * 1.7)
        title_font.setWeight(QFont.Weight.Bold)
        self.title.setFont(title_font)
        self.description = plain_label(wrap=True)
        self.tags = plain_label(wrap=True)
        # Preset text may hold a word too long to wrap (tags are up to 24 letters): such a word
        # is cut off at the edge instead of widening the window.
        for label in (self.description, self.tags):
            policy = label.sizePolicy()
            policy.setHorizontalPolicy(QSizePolicy.Policy.Ignored)
            label.setSizePolicy(policy)
        self.tags.setStyleSheet("color: palette(placeholder-text);")
        self.swatch_holder = QVBoxLayout()
        self.mode_button = QPushButton()
        self.mode_button.setMinimumHeight(34)
        self.mode_button.clicked.connect(self.switch_mode)
        title_row = QHBoxLayout()
        title_row.addWidget(self.title, 1)
        title_row.addWidget(self.mode_button, 0, Qt.AlignmentFlag.AlignTop)
        info.addLayout(title_row)
        info.addWidget(self.description)
        info.addLayout(self.swatch_holder)
        info.addWidget(self.tags)
        info.addStretch(1)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        steps_page = QWidget()
        steps_layout = QVBoxLayout(steps_page)
        steps_layout.setContentsMargins(0, 8, 0, 0)
        self.customize = CustomizePanel()
        self.customize.changed.connect(self._on_customized)
        self.customize.splash_preview_requested.connect(self.preview_splash)
        self.tabs.addTab(steps_page, QIcon.fromTheme("checkbox"), "What to apply")
        self.tabs.addTab(self.customize, QIcon.fromTheme("configure"), "Customize")
        self.body_layout.addSpacing(6)
        self.body_layout.addWidget(self.tabs)
        self._plan_timer = QTimer(self)
        self._plan_timer.setSingleShot(True)
        self._plan_timer.setInterval(350)
        self._plan_timer.timeout.connect(self.refresh_plan)

        self.step_grid = QGridLayout()
        self.step_grid.setHorizontalSpacing(18)
        self.step_grid.setVerticalSpacing(8)
        self.checkboxes: dict[str, QCheckBox] = {}
        for index, step in enumerate(ALL_STEPS):
            box = QCheckBox(step.title)
            box.setIcon(QIcon.fromTheme(STEP_ICONS.get(step.id, "configure")))
            box.setIconSize(QSize(20, 20))
            box.setChecked(True)
            box.setToolTip(step.description)
            box.toggled.connect(self.refresh_plan)
            hint = QLabel(step.description, wordWrap=True)
            hint.setStyleSheet("color: palette(placeholder-text);")
            cell = QVBoxLayout()
            cell.setSpacing(0)
            cell.addWidget(box)
            cell.addWidget(hint)
            cell.addStretch(1)
            self.step_grid.addLayout(cell, index // 2, index % 2)
            self.checkboxes[step.id] = box
        steps_layout.addLayout(self.step_grid)
        steps_layout.addStretch(1)
        self.body_layout.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        self.scroll = scroll

        outer.addWidget(self._build_footer())
        # Floats over the bottom of the page while the Customize tab holds changes not applied yet.
        self.unsaved_bar = UnsavedBar(scroll.viewport())
        self.unsaved_bar.apply_requested.connect(self.apply_current)
        self.unsaved_bar.reset_requested.connect(self.discard_changes)
        self.unsaved_bar.reserved_changed.connect(lambda _: self._pad_for_bar())
        self.customize.width_needed.connect(self._fit_page)
        self.unsaved_bar.apply_button.setEnabled(False)

        splitter.addWidget(details)
        splitter.setStretchFactor(1, 1)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        # The wheel scrolls the page; settings change only in the control that has focus.
        guard_wheel(self)

    def _build_footer(self) -> QWidget:
        """What applying installs in small print, the secondary actions, and Apply theme."""
        footer = QFrame()
        footer.setObjectName("footer")
        footer.setStyleSheet("QFrame#footer { border: none; border-top: 1px solid rgba(127,127,127,0.25); }")
        grid = QGridLayout(footer)
        grid.setContentsMargins(0, 8, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(2)
        self.summary = plain_label(wrap=True)
        self.summary.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        font = self.summary.font()
        font.setPointSizeF(font.pointSizeF() * 0.9)
        self.summary.setFont(font)
        self.summary.setStyleSheet(SUMMARY_STYLE)
        actions = FlowLayout(spacing=2)
        self.save_button = self._footer_action(
            "document-save-as",
            "Save as my preset…",
            self.save_as_preset,
            "Keep this look, with your changes, as a preset of your own.",
        )
        self.share_button = self._footer_action(
            "document-share", "Share…", self.share_preset, "Save the selected preset as one file to send to friends."
        )
        self.credits_button = self._footer_action(
            "help-about", "Credits", self.show_credits, "The artists and projects behind this preset."
        )
        self.restore_button = self._footer_action(
            "edit-undo", "Restore…", self.open_restore, "Put back your settings as they were before an apply."
        )
        self.delete_button = self._footer_action(
            "edit-delete", "Delete", self.delete_preset, "Remove this preset of yours and its pictures."
        )
        for button in (
            self.save_button,
            self.share_button,
            self.credits_button,
            self.restore_button,
            self.delete_button,
        ):
            actions.addWidget(button)
        self.apply_button = primary_button(QPushButton(QIcon.fromTheme("dialog-ok-apply"), "Apply theme"))
        self.apply_button.setDefault(True)
        self.apply_button.setMinimumHeight(38)
        self.apply_button.setMinimumWidth(150)
        self.apply_button.setToolTip(
            "Apply the selected theme. Your settings are backed up first, and Restore… puts them back."
        )
        self.apply_button.clicked.connect(self.apply_current)
        self.apply_button.installEventFilter(self)  # the unsaved bar's Apply follows its state
        grid.addWidget(self.summary, 0, 0)
        grid.addLayout(actions, 1, 0)
        grid.addWidget(self.apply_button, 0, 1, 2, 1, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.setColumnStretch(0, 1)
        return footer

    def _footer_action(self, icon: str, text: str, slot, hint: str) -> QToolButton:
        button = QToolButton()
        button.setIcon(QIcon.fromTheme(icon))
        button.setText(text)
        button.setToolTip(hint)
        button.setAutoRaise(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def set_summary(self, text: str, *, warning: bool = False) -> None:
        """The small print above the actions; problems stand out in red."""
        self.summary.setStyleSheet(WARNING_STYLE if warning else SUMMARY_STYLE)
        self.summary.setText(text)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt API)
        bar = getattr(self, "unsaved_bar", None)
        if bar is not None and watched is self.apply_button and event.type() == QEvent.Type.EnabledChange:
            bar.apply_button.setEnabled(self.apply_button.isEnabled())
        return super().eventFilter(watched, event)

    # ----- behaviour ----------------------------------------------------
    def reload_presets(self, select: str | None = None) -> None:
        problems: list[str] = []
        self.presets = presets.load_all(problems=problems)
        if problems:
            message(self, "Some presets were skipped", "\n\n".join(problems))
        active = engine.active_preset(self.presets)
        self.gallery.clear()
        for preset in self.presets:
            item = QListWidgetItem()
            is_active = active is not None and active.id == preset.id
            card = PresetCard(preset, is_active)
            item.setSizeHint(card.sizeHint())
            item.setData(Qt.ItemDataRole.UserRole, preset)
            # The card is a widget drawn over an empty item: name the item, or screen readers
            # announce a list of blanks.
            spoken = preset.name if preset.builtin else f"(yours) {preset.name}"
            item.setData(Qt.ItemDataRole.AccessibleTextRole, spoken + (", active" if is_active else ""))
            item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, preset.description)
            self.gallery.addItem(item)
            self.gallery.setItemWidget(item, card)
        target = select or (active.id if active else None)
        row = next((i for i, p in enumerate(self.presets) if p.id == target), 0)
        self.gallery.setCurrentRow(row)
        self._update_clean_action()
        self._update_mode_button()

    def _on_select(self, item: QListWidgetItem | None, _previous=None) -> None:
        if item is None:
            return
        preset: Preset = item.data(Qt.ItemDataRole.UserRole)
        if self._apply_when_planned not in (None, preset.id):
            self._apply_when_planned = None  # the user moved on to another preset
        if self.current is not None:  # keep unapplied tweaks while looking at other presets
            shown = self.customize.overrides()
            record = applied_tweaks().get(self.current.id)
            record = record if isinstance(record, dict) else {}  # the file is not ours alone to write
            folder = tweak_record.folder_of(record)
            applied = self._matching_desktop(self.current, record.get("overrides"), folder, record.get("own_mode"))
            if self.customize.change_count and shown not in (applied, record.get("overrides")):
                self._unsaved[self.current.id] = (shown, self.customize.asset_dir())
            else:
                self._unsaved.pop(self.current.id, None)  # nothing beyond what was applied
        self.current = preset
        self.delete_button.setVisible(not preset.builtin)
        self.preview.set_preset(preset, cached_wallpaper(preset))
        self.title.setText(preset.name if preset.builtin else f"(yours)  {preset.name}")
        self.description.setText(preset.description)
        origin = "Built-in preset" if preset.builtin else "Your preset"
        self.tags.setText("  ·  ".join(f"#{t}" for t in preset.tags) + f"    {origin}")
        self._show_swatches(preset)
        # Steps first, from the preset itself; loading saved tweaks afterwards switches on the
        # steps they need (panel style, splash...). The other way round, a reload after Apply
        # switched those steps off again and the next Apply silently skipped them.
        for step in ALL_STEPS:
            box = self.checkboxes[step.id]
            box.blockSignals(True)
            box.setEnabled(step.applies_to(preset))
            box.setChecked(step.applies_to(preset))
            box.blockSignals(False)
        self._load_customize(preset)
        self.refresh_plan()

    def _load_customize(self, preset: Preset, *, discard: bool = False) -> None:
        """Fill the Customize tab: edits not applied yet (unless ``discard``), else the tweaks
        applied last time, else the preset itself. Also remembers what counts as applied."""
        saved = applied_tweaks().get(preset.id)
        # The record file is plain JSON on disk: anything but a table of tweaks is ignored.
        if not isinstance(saved, dict) or not isinstance(saved.get("overrides"), dict):
            saved = None
        baseline: dict | None = None
        baseline_tweaks: dict = {}
        self._selecting = True  # no unsaved bar for the half-loaded tab
        try:
            if preset.id in self._unsaved and not discard:  # edits made earlier in this session
                overrides, folder = self._unsaved[preset.id]
                record_palette = (saved or {}).get("overrides", {}).get("palette")
                if record_palette is not None and overrides.get("palette") == record_palette:
                    # Colours the switch carried into these edits (not typed by the user): they follow
                    # the desktop like the applied ones do.
                    overrides = self._matching_desktop(preset, overrides, folder, (saved or {}).get("own_mode"))
                self.customize.load(preset, overrides, folder)
                baseline, baseline_tweaks = self._applied_data(preset, saved)
            elif saved:
                folder = tweak_record.folder_of(saved)
                matched = self._matching_desktop(preset, saved.get("overrides"), folder, saved.get("own_mode"))
                self.customize.load(preset, matched, folder)
            else:
                self.customize.load(preset)
        finally:
            self._selecting = False
        if baseline is None:  # what the tab shows right now is what is applied
            shown = self._shown_preset()
            baseline = shown.data if shown is not None else preset.data
            baseline_tweaks = self.customize.overrides() if shown is not None else {}
        self._baseline = baseline
        self._baseline_tweaks = baseline_tweaks
        self._update_unsaved()

    def _applied_data(self, preset: Preset, saved: dict | None) -> tuple[dict, dict]:
        """The preset as applied last time (in the desktop's light/dark mode), or as it is, and
        the tweaks that make it."""
        overrides = (saved or {}).get("overrides")
        if not isinstance(overrides, dict):
            return preset.data, {}
        folder = tweak_record.folder_of(saved)
        usable = folder if folder is not None and folder.is_dir() else None
        try:
            matched = self._matching_desktop(preset, overrides, folder, saved.get("own_mode"))
            return presets.customized(preset, matched, usable).data, matched
        except presets.PresetError:
            return preset.data, {}

    def _shown_preset(self) -> Preset | None:
        """The preset with the Customize tab's changes, quietly None when they are not valid."""
        if self.current is None:
            return None
        try:
            return presets.customized(self.current, self.customize.overrides(), self.customize.asset_dir())
        except presets.PresetError:
            return None

    def has_unapplied_changes(self) -> bool:
        """Whether the Customize tab differs from what is applied (or from the preset)."""
        if self.current is None or self._baseline is None:
            return False
        shown = self._shown_preset()
        if shown is None:
            return self.customize.change_count > 0  # settings being typed that are not valid yet
        return shown.data != self._baseline

    def unapplied_count(self) -> int:
        """How many settings on the Customize tab differ from what is applied. Compared before
        glass fills in what it needs (stronger blur, see-through panels): one choice, one change."""
        if self.current is None or self._baseline is None:
            return 0
        before = presets.merge(self.current.data, self._baseline_tweaks)
        return changed_settings(before, presets.merge(self.current.data, self.customize.overrides()))

    def _update_unsaved(self) -> None:
        if self._selecting:
            return
        pending = self.has_unapplied_changes()
        self.unsaved_bar.set_count(self.unapplied_count() if pending else 0)
        self.unsaved_bar.set_shown(pending)
        # One Apply at a time: the bar's while it shows, the footer's otherwise.
        self.apply_button.setVisible(not pending)
        self._pad_for_bar()

    def _fit_page(self, width: int) -> None:
        """The page never gets narrower than one column of the open cards or the unsaved bar
        needs, so neither does the window: narrower, it scrolled sideways and cut off controls."""
        bar = self.unsaved_bar.host_width_needed() + self.scroll.verticalScrollBar().sizeHint().width()
        self.scroll.setMinimumWidth(max(width, bar))

    def _pad_for_bar(self) -> None:
        """Room under the page, so its last options can be scrolled above the bar."""
        bar = self.unsaved_bar
        self.body_layout.setContentsMargins(0, 0, 6, bar.reserved_height() if bar.is_shown() else 0)

    def discard_changes(self) -> None:
        """The bar's Reset: back to what is applied (or to the preset when nothing is)."""
        if self.current is None:
            return
        self._unsaved.pop(self.current.id, None)
        self._load_customize(self.current, discard=True)

    def _show_swatches(self, preset: Preset) -> None:
        self._swatch_colours = dict(preset.palette.colors)
        while self.swatch_holder.count():
            old = self.swatch_holder.takeAt(0).widget()
            if old:
                old.deleteLater()
        self.swatch_holder.addWidget(Swatches(preset, 18))

    def _matching_desktop(
        self, preset: Preset, overrides: dict | None, folder: Path | None, own: dict | None = None
    ) -> dict | None:
        """Applied tweaks of the theme on the desktop, in the light/dark mode the desktop really
        shows (after a restore or a command-line apply), so the Customize tab and the next Apply
        start from what is on screen."""
        if not isinstance(overrides, dict):
            return overrides
        return self._desktop_tweaks(preset, overrides, folder, own)[0]

    def _desktop_tweaks(
        self, preset: Preset, overrides: dict, folder: Path | None, own: dict | None = None
    ) -> tuple[dict, bool, dict | None]:
        """(tweaks that still validate, in the desktop's light/dark mode; whether they changed;
        the way back that goes with them), as the command line sees it (record.desktop_tweaks)."""
        active = engine.active_preset(self.presets)
        live = live_mode() if active is not None and active.id == preset.id else None
        return tweak_record.desktop_tweaks(preset, overrides, folder, own, live)

    def effective_preset(self) -> Preset | None:
        """The selected preset with the Customize tab's changes applied (None if invalid)."""
        if self.current is None:
            return None
        try:
            return presets.customized(self.current, self.customize.overrides(), self.customize.asset_dir())
        except presets.PresetError as exc:
            self.set_summary(f"These settings are not valid: {exc}", warning=True)
            return None

    def _on_customized(self) -> None:
        count = self.customize.change_count
        self.tabs.setTabText(1, f"Customize ({count})" if count else "Customize")
        self._update_unsaved()
        # Until the plan is rebuilt for these settings, Apply would use the previous ones;
        # a plan still being made for older settings is ignored when it arrives.
        self.apply_button.setEnabled(False)
        self.plan_worker = None
        effective = self.effective_preset()
        if effective is None:
            return
        self.preview.set_preset(effective, cached_wallpaper(effective))
        if effective.palette.colors != self._swatch_colours:
            self._show_swatches(effective)
        self._sync_steps(effective)
        self._plan_timer.start()
        try:
            advice = compat.check(effective, self.customize.tiling_now())
        except Exception:  # hints are a convenience; they must never block applying
            log_error("compat.check failed")
            advice = []
        self.customize.set_advice(advice)

    def _sync_steps(self, effective: Preset) -> None:
        """A customization can make a step relevant (say, panel corners on a preset without
        a panel style). Such steps get switched on; the user's own unticks are kept."""
        for step in ALL_STEPS:
            box = self.checkboxes[step.id]
            applies = step.applies_to(effective)
            if applies == box.isEnabled():
                continue
            box.blockSignals(True)
            box.setEnabled(applies)
            box.setChecked(applies)
            box.blockSignals(False)

    def preview_splash(self) -> None:
        """Show the customized splash screen in a window; nothing on the system changes."""
        effective = self.effective_preset()
        if effective is None:
            return
        if shutil.which("ksplashqml") is None:
            message(self, "No preview", "ksplashqml (part of Plasma) was not found.")
            return
        style = effective.section("splash").get("style", "plasmify")  # the same default as SplashStep
        if style == "breeze":
            target = Path("/usr/share/plasma/look-and-feel") / splash.BREEZE_SPLASH
        else:
            target = PLASMIFY_CACHE / "splash-preview"
            problems: list[str] = []
            try:
                splash.write_package(target, effective, problems.append)
            except OSError as exc:
                message(self, "No preview", str(exc))
                return
            if problems:
                self.set_summary("Splash preview: " + "; ".join(problems), warning=True)
        QProcess.startDetached("ksplashqml", ["--test", "--window", str(target)])

    def save_as_preset(self) -> None:
        effective = self.effective_preset()
        if effective is None:
            return
        dialog = QInputDialog(self)
        dialog.setWindowTitle("Save as my preset")
        dialog.setLabelText("Name of your preset:")
        dialog.setTextValue(f"My {effective.name}"[:60])
        # Opened 200 px wide, cutting the title and the name short. The dialog's layout overrides a
        # minimum size set on the dialog itself, so the text field gets it.
        field = dialog.findChild(QLineEdit)
        if field is not None:
            field.setMinimumWidth(380)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = dialog.textValue()
        if not name.strip():
            return
        if schema.text(60)(name.strip()) is not None:
            message(self, "Invalid name", "Use up to 60 characters without line breaks.")
            return
        data = presets.merge(effective.data, {"meta": {"id": capture.slugify(name), "name": name.strip()}})
        data["meta"].pop("preview", None)
        files = {}
        if effective.asset_dir is not None:
            files = {f: effective.asset_dir / f for f in schema.local_files(data)}
        try:
            preset = self._store(data, files)
        except (presets.PresetError, ValueError, OSError) as exc:
            message(self, "Could not save", str(exc))
            return
        if preset is not None:
            self.reload_presets(select=preset.id)

    def selected_steps(self) -> list[str]:
        return [sid for sid, box in self.checkboxes.items() if box.isChecked() and box.isEnabled()]

    def refresh_plan(self) -> None:
        if self.current is None or self._closing:
            return
        self.apply_button.setEnabled(False)
        self.set_summary("Checking what needs to be installed…")
        effective = self.effective_preset()
        if effective is None:
            return
        worker = PlanWorker(effective, self.selected_steps(), self)
        worker.planned.connect(self._on_planned)
        worker.finished.connect(worker.deleteLater)
        self.plan_worker = worker
        worker.start()

    def _on_planned(self, plan) -> None:
        if self.sender() is not self.plan_worker or self._closing:
            return  # a newer selection superseded this one
        effective = self.effective_preset()
        if effective is None or effective.data != plan.preset.data:
            return  # made for settings that have changed since
        self.plan = plan
        text = plan.install_summary
        if plan.packages_missing:
            text += ": " + ", ".join(plan.packages_missing)
        text += ". No background services, no AUR or added repositories."
        self.set_summary(text)
        self.apply_button.setEnabled(bool(plan.steps) and not self.problems and self._mode_worker is None)
        if self._apply_when_planned == plan.preset.id and self._mode_worker is None:
            self._apply_when_planned = None
            if self.apply_button.isEnabled():
                self.apply_current()

    # ----- light / dark ---------------------------------------------------
    def _applied(self) -> tuple[Preset, dict, Path | None, Preset, dict] | None:
        """The theme on the desktop now: (preset, applied tweaks, their folder, result, record)."""
        target = engine.active_preset(self.presets)
        if target is None:
            return None
        record = applied_tweaks().get(target.id)
        record = record if isinstance(record, dict) else {}
        overrides = record.get("overrides") if isinstance(record.get("overrides"), dict) else {}
        folder = tweak_record.folder_of(record)
        usable = folder if folder is not None and folder.is_dir() else None
        # What still validates, like Customize; colours older than the desktop (a restore, a
        # command-line apply) give way to what is on screen.
        overrides, stale, own = self._desktop_tweaks(target, overrides, folder, record.get("own_mode"))
        if stale:
            record = {**record, "own_mode": own}  # the way back from the mode the screen shows
        try:
            result = presets.customized(target, overrides, usable)
        except presets.PresetError:
            overrides, result = {}, target
        return target, overrides, folder, result, record

    def _dark_now(self, result: Preset) -> bool:
        """Whether the desktop is dark: what it shows when known, else what was applied."""
        live = live_mode()
        return result.palette.dark if live is None else live

    def _update_mode_button(self) -> None:
        applied = self._applied()
        busy = self._mode_worker is not None
        if applied is None:
            self.mode_button.setText("Switch to dark")
            self.mode_button.setIcon(QIcon.fromTheme("weather-clear-night"))
            self.mode_button.setEnabled(False)
            self.mode_button.setToolTip(
                "Apply a theme first. Then this button switches it between light and dark in one click."
            )
            return
        target, _, _, result, _ = applied
        to_dark = not self._dark_now(result)
        self.mode_button.setText("Switch to dark" if to_dark else "Switch to light")
        self.mode_button.setIcon(QIcon.fromTheme("weather-clear-night" if to_dark else "weather-clear"))
        self.mode_button.setEnabled(not busy and not self.problems)
        self.mode_button.setToolTip(
            f"Switch {target.name} to {'dark' if to_dark else 'light'} colours in one click. The colour scheme "
            "changes and, where you applied them, app style, icons, terminal, prompt, panel style and splash "
            "follow. A preset's own Kvantum theme makes way for Breeze or glass until you switch back. "
            "Wallpapers, panel layout and fonts stay. Switch back the same way."
        )

    def _mode_steps(self, record: dict, effective: Preset) -> list[str]:
        """The colour steps to run, as the command line picks them (record.mode_steps)."""
        return tweak_record.mode_steps(record, effective)

    def switch_mode(self) -> None:
        """Apply the theme on the desktop in the other mode (light or dark), colours only."""
        applied = self._applied()
        if applied is None or self._mode_worker is not None:
            return
        target, overrides, folder, result, record = applied
        dark = not self._dark_now(result)
        own = record.get("own_mode") if isinstance(record.get("own_mode"), dict) else None
        usable = folder if folder is not None and folder.is_dir() else None
        try:
            switched, keep = modes.switch(target, overrides, result, dark, own)
            effective = presets.customized(target, switched, usable)
        except (presets.PresetError, KeyError, ValueError) as exc:
            message(self, "Could not switch", str(exc))
            return
        self._mode_token += 1
        token = self._mode_token
        worker = PlanWorker(effective, self._mode_steps(record, effective), self)
        worker.planned.connect(lambda plan: self._switch_planned(plan, token, target, switched, folder, keep))
        worker.finished.connect(worker.deleteLater)
        self._mode_worker = worker
        self.apply_button.setEnabled(False)  # one change at a time
        self.set_summary(f"Preparing the switch to {'dark' if dark else 'light'}…")
        self._update_mode_button()
        worker.start()

    def _switch_planned(self, plan, token: int, target: Preset, switched: dict, folder: Path | None, keep) -> None:
        if token != self._mode_token or self._closing:
            return  # superseded, or the window is closing
        if self._apply_when_planned is not None:
            # Meanwhile another theme was chosen to be applied (the theme studio): that wins.
            self._mode_worker = None
            self.statusBar().showMessage("The light/dark switch was cancelled: another theme is being applied.", 10000)
            self._update_mode_button()
            self.refresh_plan()
            return
        if QApplication.activeModalWidget() is not None:
            # Another window is open (Credits, a colour picker...): switch once it is closed.
            QTimer.singleShot(400, lambda: self._switch_planned(plan, token, target, switched, folder, keep))
            return
        try:
            active = engine.active_preset(self.presets)
            if (
                not plan.steps
                or active is None
                or active.id != target.id
                or not self._confirm_hosts(plan, switched, target)
            ):
                return
            dialog = ApplyDialog(plan, self, allowed_hosts=plan.preset.untrusted_hosts())
            dialog.exec()
            if not dialog.succeeded:
                return
            remember_tweaks(target.id, switched, folder, steps=[s.id for s in plan.steps], own_mode=keep)
            # Edits not applied yet keep their place, in the new colours.
            if self.current is not None and self.current.id == target.id:
                self.customize.load(target, modes.carry(self.customize.overrides(), switched),
                                    self.customize.asset_dir() or folder)  # fmt: skip
            elif target.id in self._unsaved:
                old, old_folder = self._unsaved[target.id]
                self._unsaved[target.id] = (modes.carry(old, switched), old_folder)
            self.reload_presets(select=self.current.id if self.current else target.id)
        finally:
            self._mode_worker = None  # only now: Apply stays blocked while the switch runs
            self._update_mode_button()
            self.refresh_plan()

    def _confirm_hosts(self, plan, overrides: dict | None = None, base: Preset | None = None) -> bool:
        hosts = plan.preset.untrusted_hosts()
        modules = ((overrides if overrides is not None else self.customize.overrides()).get("shell") or {}).get(
            "fastfetch_modules"
        )
        base = base or self.current
        own = (base.section("shell").get("fastfetch_modules") or []) if base is not None else []
        if isinstance(modules, list) and schema.WEATHER_HOST in hosts and "weather" in modules and "weather" not in own:
            hosts.remove(schema.WEATHER_HOST)  # you ticked the weather line yourself; the preset did not list it
        notes = {schema.WEATHER_HOST: " (the weather line of the terminal greeting, every time a terminal opens)"}
        return not hosts or confirm(
            self,
            "Untrusted hosts",
            "This preset contacts servers that are not on Plasmify's trusted list:\n\n"
            + "\n".join(f"  • {host}{notes.get(host, '')}" for host in hosts)
            + "\n\nThese servers will see your IP address. Only continue if you trust "
            "whoever shared this preset.",
        )

    def apply_current(self) -> None:
        if self.plan is None or not self.plan.steps or self._mode_worker is not None:
            return
        effective = self.effective_preset()
        if effective is None:
            return
        if effective.data != self.plan.preset.data or set(self.selected_steps()) != {s.id for s in self.plan.steps}:
            # The settings changed after this plan was made: plan again, then apply.
            self._apply_when_planned = effective.id
            self.refresh_plan()
            return
        if not self._confirm_hosts(self.plan):
            return
        # When the record's colours were older than the screen (after a restore), the tweaks
        # were matched to the screen: keep the way back that goes with them, as the command
        # line does. Worked out before applying, while the screen still shows the old colours.
        own_mode: Any = KEEP
        entry = applied_tweaks().get(self.plan.preset.id)
        if isinstance(entry, dict) and self.current is not None and self.current.id == self.plan.preset.id:
            _, stale, keep = self._desktop_tweaks(
                self.current,  # the preset as it is defined (the plan's has the tweaks in it)
                tweak_record.overrides_of(entry),
                tweak_record.folder_of(entry),
                entry.get("own_mode"),
            )
            if stale:
                own_mode = keep
        plan = self.plan
        dialog = ApplyDialog(plan, self, allowed_hosts=plan.preset.untrusted_hosts())
        dialog.applied.connect(lambda: self._mark_applied(plan, own_mode))
        dialog.exec()
        if dialog.succeeded:
            self.reload_presets(select=plan.preset.id)

    def _mark_applied(self, plan, own_mode: Any) -> None:
        """The apply just succeeded (its dialog is still open): what the tab shows is applied now,
        so the bar about changes not applied yet goes at once instead of when the dialog closes."""
        # Keep the tweaks: they are shown again (and kept on top of the preset) next time,
        # so a second Apply never silently falls back to the preset's own layout.
        remember_tweaks(
            plan.preset.id,
            self.customize.overrides(),
            self.customize.asset_dir(),
            steps=[step.id for step in plan.steps],
            own_mode=own_mode,
        )
        self._unsaved.pop(plan.preset.id, None)
        shown = self._shown_preset()
        if shown is not None and self.current is not None and self.current.id == plan.preset.id:
            self._baseline, self._baseline_tweaks = shown.data, self.customize.overrides()
        self._update_unsaved()

    def open_restore(self) -> None:
        if self._mode_worker is not None:
            return  # a light/dark switch is being prepared
        dialog = RestoreDialog(self)
        dialog.exec()
        if dialog.restored and self.current is not None and not self.has_unapplied_changes():
            # The tab showed what was applied. The restore brought back the record of that moment,
            # so the tab must show it too, not keep the tweaks from before as edits not applied.
            self._unsaved.pop(self.current.id, None)
            self.current = None
        self.reload_presets()

    def show_credits(self) -> None:
        preset = self.effective_preset() or self.current  # with the Customize wallpaper list
        if preset:
            CreditsDialog(preset, self).exec()

    def import_preset(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import preset", str(Path.home()), "Plasmify presets (*.plasmify *.zip *.toml)"
        )
        if path:
            self.import_path(Path(path))

    def import_path(self, path: Path) -> None:
        notes: list[str] = []
        try:
            try:
                preset = bundle.import_file(path, notes=notes)
            except FileExistsError as exists:
                if not confirm(self, "Replace preset", f"You already have a preset called '{exists}'. Replace it?"):
                    return
                notes.clear()
                preset = bundle.import_file(path, replace=True, notes=notes, custom_root=custom_root())
        except (presets.PresetError, ValueError, OSError) as exc:
            message(self, "Could not import", str(exc))
            return
        hosts = sorted({urlsplit(u).hostname or "" for u in schema.urls(preset.data)})
        lines = [f"Imported “{preset.name}”."]
        if schema.local_files(preset.data):
            lines.append(f"It includes {len(schema.local_files(preset.data))} wallpaper(s) of its own.")
        if hosts:
            lines.append("When applied it downloads from: " + ", ".join(hosts) + ".")
        if preset.untrusted_hosts():
            lines.append("You will be asked before untrusted hosts are contacted.")
        lines += notes
        message(self, "Preset imported", "\n\n".join(lines), QMessageBox.Icon.Information)
        self.reload_presets(select=preset.id)

    def share_preset(self) -> None:
        if self.current is None:
            return
        default = str(Path.home() / f"{self.current.id}{bundle.BUNDLE_SUFFIX}")
        path, _ = QFileDialog.getSaveFileName(self, "Share preset", default, "Plasmify preset (*.plasmify)")
        if not path:
            return
        try:
            written = bundle.export_bundle(self.current, Path(path))
        except (presets.PresetError, OSError) as exc:
            message(self, "Could not share", str(exc))
            return
        own = len(schema.local_files(self.current.data))
        extra = f" It contains {own} of your own wallpaper(s)." if own else ""
        message(
            self,
            "Ready to share",
            f"Saved to {written}.{extra}\n\nSend this file to anyone: they open it with File → Import preset. "
            "Wallpapers from other artists are not copied; they are downloaded from where they are published.",
            QMessageBox.Icon.Information,
        )

    def delete_preset(self) -> None:
        if self.current is None or self.current.builtin or self._mode_worker is not None:
            return
        if confirm(self, "Delete preset", f"Delete “{self.current.name}” and its wallpapers from your gallery?"):
            bundle.delete_user_preset(self.current)
            # A new preset with the same id must not inherit these.
            remember_tweaks(self.current.id, {}, None)
            self._unsaved.pop(self.current.id, None)
            shutil.rmtree(custom_folder(self.current.id), ignore_errors=True)
            self.current = None
            self.reload_presets()

    def save_current_desktop(self) -> None:
        dialog = CaptureDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            result = capture.capture(
                dialog.name.text(), dialog.description.text(), include_wallpapers=dialog.wallpapers.isChecked()
            )
            preset = self._store(result.data, result.files)
        except (presets.PresetError, ValueError, OSError) as exc:
            message(self, "Could not save", str(exc))
            return
        if preset is None:
            return
        notes = "\n".join(f"• {note}" for note in result.notes)
        message(
            self,
            "Desktop saved",
            f"Saved as “{preset.name}”. Use Share… to send it to others." + (f"\n\nNotes:\n{notes}" if notes else ""),
            QMessageBox.Icon.Information,
        )
        self.reload_presets(select=preset.id)

    def create_from_wallpaper(self) -> None:
        wizard = WallpaperWizard(self)
        if wizard.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            data, files = wizard.result_preset()
            preset = self._store(data, files)
        except (presets.PresetError, ValueError, OSError) as exc:
            message(self, "Could not create the theme", str(exc))
            return
        if preset is None:
            return
        self.reload_presets(select=preset.id)
        if wizard.apply_now:
            self._apply_when_planned = preset.id

    def _store(self, data: dict, files: dict[str, Path]) -> Preset | None:
        try:
            return bundle.save_user_preset(data, files)
        except FileExistsError:
            if not confirm(
                self, "Replace preset", f"You already have a preset called '{data['meta']['id']}'. Replace it?"
            ):
                return None
            return bundle.save_user_preset(data, files, replace=True, custom_root=custom_root())

    def open_presets_folder(self) -> None:
        presets.USER_PRESET_DIR.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(presets.USER_PRESET_DIR)))

    def _update_clean_action(self) -> None:
        active = engine.active_preset(self.presets)
        try:
            unused = engine.unused_size(active.id if active else None, Runner())
        except CommandError:
            unused = 0  # the settings cannot be read, so clean would refuse anyway
        self.clean_action.setText(f"&Clean up unused downloads ({packages.human_size(unused)})")
        self.clean_action.setEnabled(unused > 0)

    def clean_up(self) -> None:
        active = engine.active_preset(self.presets)
        try:
            freed = engine.clean(active.id if active else None, Runner())
        except CommandError as exc:
            message(self, "Nothing was cleaned up", str(exc))
            return
        message(self, "Cleaned up", f"Freed {packages.human_size(freed)}.", QMessageBox.Icon.Information)
        self.reload_presets(select=self.current.id if self.current else None)

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        # Nothing planned before closing may still apply afterwards.
        self._closing = True
        self._plan_timer.stop()
        self._mode_worker = None
        self._apply_when_planned = None
        # Let pending package queries finish so no thread is destroyed while running (Qt aborts
        # the whole process then). A slow package manager can take a while: hide meanwhile.
        self.hide()
        for worker in self.findChildren(QThread):
            worker.wait()
        shimmer_preview.stop_renders()  # Shimmer previews run without a parent, so wait for them too
        super().closeEvent(event)

    def about(self) -> None:
        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<h3>{APP_NAME} {__version__}</h3>"
            "<p>One-click, bloat-free themes for KDE Plasma 6.</p>"
            "<p>Themes, cursors, wallpapers and extra Papirus folder colours are downloaded from the "
            "projects and collections that publish them. See <i>Credits</i> on each preset.</p>"
            f'<p><a href="{DOCS_URL}">Documentation</a> · <a href="{HOMEPAGE}">{HOMEPAGE}</a>'
            " · GNU GPL v3 or later</p>",
        )
