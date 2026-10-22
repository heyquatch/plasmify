"""Progress, restore and credits dialogs."""

from __future__ import annotations

import time

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from plasmify import activity, backup, packages, schema
from plasmify.engine import Plan
from plasmify.gui import restyle
from plasmify.gui.safe import confirm, plain_label, plain_tooltip
from plasmify.gui.workers import ApplyWorker, RestoreWorker
from plasmify.presets import Preset

# The steps that change the colours or the widget style of open windows (they run first).
LOOK_STEPS = ("colors", "style")


class ApplyDialog(QDialog):
    applied = pyqtSignal()  # the apply succeeded (the dialog stays open until it is closed)

    def __init__(self, plan: Plan, parent: QWidget | None = None, allowed_hosts: list[str] | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Applying {plan.preset.name}")
        self.setMinimumSize(560, 420)
        self.succeeded = False

        self.status = plain_label("Starting…")
        self.status.setStyleSheet("font-weight: 600;")
        self.bar = QProgressBar()
        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumBlockCount(2000)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.reject)
        self.buttons.setEnabled(False)

        hint = QLabel(
            "You may be asked for your password to install packages, and again if the login screen changes. "
            "The settings this apply changes are backed up first, so Restore… can put them back."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: palette(placeholder-text);")

        layout = QVBoxLayout(self)
        layout.addWidget(self.status)
        layout.addWidget(self.bar)
        layout.addWidget(hint)
        layout.addWidget(self.log, 1)
        layout.addWidget(self.buttons)

        self.worker = ApplyWorker(plan, self, allowed_hosts or [])
        self.worker.progress.connect(self._on_progress)
        self.worker.message.connect(self.log.appendPlainText)
        self.worker.finished_with.connect(self._on_finished)
        # The steps change the look one part at a time: Plasmify restyles itself once the colours
        # and the style are both in place (see _on_progress), and at the end if needed.
        self._look_steps = {step.title for step in plan.steps if step.id in LOOK_STEPS}
        self._look_seen = False
        restyle.hold()
        self.worker.finished.connect(self._worker_done)
        self.worker.start()

    def _worker_done(self) -> None:
        restyle.release()

    def _on_progress(self, done: int, total: int, label: str) -> None:
        self.bar.setMaximum(total)
        self.bar.setValue(done)
        self.status.setText(label)
        if label in self._look_steps:
            self._look_seen = True
        elif self._look_seen:  # the step after the colours and the style has begun
            self._look_seen = False
            restyle.catch_up()

    def _on_finished(self, snapshot, warnings: list[str], error: str) -> None:
        self.buttons.setEnabled(True)
        if error:
            self.status.setText("Something went wrong")
            self.log.appendPlainText(f"\nError: {error}")
            return
        self.succeeded = True
        self.status.setText("Done! Enjoy your new desktop ✨")
        self.applied.emit()
        if warnings:
            self.log.appendPlainText("\nNotes:")
            for warning in warnings:
                self.log.appendPlainText(f"  • {warning}")
        if snapshot:
            self.log.appendPlainText(f"\nBackup: {snapshot.id} (Restore… puts these settings back)")

    def reject(self) -> None:
        if self.worker.isRunning():
            return  # never interrupt half-way through
        super().reject()


class RestoreDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Restore a backup")
        self.setMinimumSize(560, 360)
        self.worker: RestoreWorker | None = None
        self.restored = False  # a restore finished: the window reloads what is applied

        intro = QLabel("Each time a preset is applied, the previous setup is saved. Pick one to go back to it.")
        intro.setWordWrap(True)
        self.list = QListWidget()
        # Long labels ("Your setup before Plasmify (before ...)") wrap instead of scrolling sideways.
        self.list.setWordWrap(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for snap in backup.list_backups():
            created = time.strftime("%d %b %Y, %H:%M", time.localtime(snap.created))
            item = QListWidgetItem(f"{created}   {snap.label}" + ("   (always kept)" if snap.pinned else ""))
            item.setToolTip(plain_tooltip(item.text()))  # labels carry preset names
            item.setData(Qt.ItemDataRole.UserRole, snap)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)

        self.log = QPlainTextEdit(readOnly=True)
        self.log.setMaximumHeight(90)
        self.restore_button = QPushButton("Restore")
        self.restore_button.setEnabled(self.list.count() > 0)
        self.restore_button.clicked.connect(self._restore)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.addButton(self.restore_button, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.log)
        layout.addWidget(buttons)
        if not self.list.count():
            self.log.setPlainText("No backups yet. One is made automatically before every apply.")

    def _restore(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        answer = QMessageBox.question(
            self,
            "Restore backup",
            "Your panels may briefly disappear while Plasma reloads. Continue?",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.restore_button.setEnabled(False)
        self.worker = RestoreWorker(item.data(Qt.ItemDataRole.UserRole), self)
        self.worker.message.connect(self.log.appendPlainText)
        self.worker.finished_with.connect(self._on_finished)
        restyle.hold()
        self.worker.finished.connect(self._worker_done)
        self.worker.start()

    def _worker_done(self) -> None:
        restyle.release()

    def _on_finished(self, error: str) -> None:
        self.restore_button.setEnabled(True)
        if error:
            self.log.appendPlainText(f"Error: {error}")
        else:
            self.restored = True  # the restore itself logged "Restored."

    def _busy(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def reject(self) -> None:  # Escape and the Close button
        if self._busy():
            self.log.appendPlainText("Please wait until the restore has finished.")
            return
        super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if self._busy():
            event.ignore()
            self.log.appendPlainText("Please wait until the restore has finished.")
            return
        super().closeEvent(event)


class CreditsDialog(QDialog):
    def __init__(self, preset: Preset, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Credits: {preset.name}")
        self.setMinimumSize(520, 380)
        lines = [
            "When you apply the preset, everything below is downloaded from the address shown, "
            "except pictures included with the preset.",
            "",
        ]
        for section, key, label in (
            ("style", "kvantum", "Application style"),
            ("cursor", None, "Cursor"),
            ("icons", "folder_source", "Folder icons"),
        ):
            data = preset.section(section)
            source = data.get(key) if key else data
            if isinstance(source, dict) and (source.get("repo") or source.get("url")):
                lines.append(f"{label}: {source.get('repo') or source.get('url')}")
        lines += ["", "Wallpapers:"]
        for image in preset.section("wallpaper").get("images", []):
            extra = f" ({image['license']})" if image.get("license") else ""
            lines.append(f"  • {image['credit']}{extra}")
            lines.append(f"    {image.get('url') or 'included with the preset'}")
        text = QPlainTextEdit("\n".join(lines), readOnly=True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(buttons)


class HistoryDialog(QDialog):
    """Everything Plasmify downloaded or installed, from the local activity log."""

    COLUMNS = ("When", "Preset", "Type", "From", "Saved to", "Size", "SHA-256 / commit")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Download history")
        self.resize(1100, 520)

        intro = plain_label(
            "What Plasmify downloaded or installed, newest first, with the SHA-256 of each file "
            "and the commit of each git source. "
            "This history stays on your computer and is never sent anywhere.",
            wrap=True,
        )
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)

        open_folder = QPushButton("Open log folder")
        open_folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(activity.LOG_FILE.parent))))
        clear = QPushButton("Clear history")
        clear.clicked.connect(self._clear)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.addButton(open_folder, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.addButton(clear, QDialogButtonBox.ButtonRole.DestructiveRole)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addWidget(self.table, 1)
        layout.addWidget(buttons)
        self._fill()

    def _fill(self) -> None:
        rows = activity.entries(1000)
        self.table.setRowCount(len(rows))
        for row, e in enumerate(rows):
            values = (
                time.strftime("%Y-%m-%d %H:%M", time.localtime(e.time)),
                e.preset or "-",
                e.kind if e.ok else f"{e.kind} (failed)",
                e.source,
                e.error or e.detail,
                packages.human_size(e.size) if e.size else "",
                e.sha256,
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(plain_tooltip(value))  # sources come from presets
                self.table.setItem(row, column, item)
        self.table.resizeColumnsToContents()

    def _clear(self) -> None:
        if confirm(self, "Clear history", "Delete the whole download history?"):
            activity.clear()
            self._fill()


class CaptureDialog(QDialog):
    """Asks for a name before saving the current desktop as a preset."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Save current desktop as preset")
        self.name = QLineEdit()
        self.name.setMaxLength(60)
        self.name.setPlaceholderText("My Desktop")
        self.description = QLineEdit()
        self.description.setMaxLength(300)
        self.description.setPlaceholderText("What makes this look special?")
        self.wallpapers = QCheckBox("Include my wallpapers (only share images you are allowed to share)")
        self.wallpapers.setChecked(True)
        intro = plain_label(
            "Colours, fonts, panels, effects, terminal and wallpapers are read from your current setup. "
            "Parts that others could not install automatically are left out, and you will see which.",
            wrap=True,
        )
        form = QFormLayout()
        form.addRow("Name:", self.name)
        form.addRow("Description:", self.description)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.name.textChanged.connect(self._validate)
        layout = QVBoxLayout(self)
        layout.addWidget(intro)
        layout.addLayout(form)
        layout.addWidget(self.wallpapers)
        layout.addWidget(self.buttons)
        self._validate()

    def _validate(self) -> None:
        ok = bool(self.name.text().strip()) and schema.text(60)(self.name.text().strip()) is None
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(ok)
