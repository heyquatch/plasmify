"""Background threads so the window stays responsive while the package manager and git work."""

from __future__ import annotations

from PyQt6.QtCore import QObject, QThread, pyqtSignal

from plasmify import backup, engine
from plasmify.engine import Plan
from plasmify.presets import Preset
from plasmify.system import Runner


class PlanWorker(QThread):
    """Works out which packages are missing (package manager queries take a moment)."""

    planned = pyqtSignal(object)

    def __init__(self, preset: Preset, step_ids: list[str], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.preset = preset
        self.step_ids = step_ids

    def run(self) -> None:
        self.planned.emit(engine.make_plan(self.preset, self.step_ids))


class ApplyWorker(QThread):
    progress = pyqtSignal(int, int, str)
    message = pyqtSignal(str)
    finished_with = pyqtSignal(object, list, str)  # backup, warnings, error

    def __init__(self, plan: Plan, parent: QObject | None = None, allowed_hosts: list[str] | None = None) -> None:
        super().__init__(parent)
        self.plan = plan
        self.allowed_hosts = allowed_hosts or []

    def run(self) -> None:
        try:
            snapshot, warnings = engine.apply(
                self.plan,
                Runner(),
                log=self.message.emit,
                progress=self.progress.emit,
                allowed_hosts=self.allowed_hosts,
            )
        except Exception as exc:  # surfaced in the dialog instead of crashing the app
            self.finished_with.emit(None, [], str(exc))
            return
        self.finished_with.emit(snapshot, warnings, "")


class RestoreWorker(QThread):
    message = pyqtSignal(str)
    finished_with = pyqtSignal(str)  # error, empty on success

    def __init__(self, snapshot: backup.Backup, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.snapshot = snapshot

    def run(self) -> None:
        try:
            engine.restore(self.snapshot, Runner(), log=self.message.emit)
        except Exception as exc:
            self.finished_with.emit(str(exc))
            return
        self.finished_with.emit("")
