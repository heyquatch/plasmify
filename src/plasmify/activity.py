"""A local, human-readable record of everything Plasmify downloads or installs.

Each entry is one JSON line in ``~/.local/state/plasmify/activity.jsonl`` with the
time, the preset being applied, what was fetched, from where, its size and
SHA-256, and where it was saved. The log never leaves the machine; it exists so
users can see (and verify) exactly what a preset brought onto their system.
"""

from __future__ import annotations

import json
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plasmify.system import PLASMIFY_STATE

LOG_FILE = PLASMIFY_STATE / "activity.jsonl"
MAX_ENTRIES = 5000

_context: dict[str, str] = {}


@dataclass(frozen=True)
class Entry:
    time: float
    kind: str  # download, git, packages, fonts
    preset: str
    source: str  # URL, repository or package manager
    detail: str  # saved path, git path or package list
    size: int = 0
    sha256: str = ""
    ok: bool = True
    error: str = ""

    @property
    def host(self) -> str:
        if "://" in self.source:
            return self.source.split("/")[2]
        return self.source


def set_context(preset_id: str) -> None:
    """Attribute the following entries to ``preset_id`` (empty string clears it)."""
    if preset_id:
        _context["preset"] = preset_id
    else:
        _context.clear()


def _display_path(path: Path | str) -> str:
    text = str(path)
    if text.startswith(tempfile.gettempdir() + "/plasmify-"):
        return f"(temporary, removed after applying) {Path(text).name}"
    home = str(Path.home())
    return "~" + text[len(home) :] if text.startswith(home) else text


def record(kind: str, source: str, detail: Path | str = "", **fields: Any) -> None:
    """Append one entry. Logging problems never break applying a theme."""
    entry = {
        "time": round(time.time(), 3),
        "kind": kind,
        "preset": _context.get("preset", ""),
        "source": source,
        "detail": _display_path(detail),
        **fields,
    }
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _trim()
    except OSError:
        pass


def _trim() -> None:
    if LOG_FILE.stat().st_size < 2 * 1024 * 1024:
        return
    lines = LOG_FILE.read_text(encoding="utf-8").splitlines()[-MAX_ENTRIES:]
    LOG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def entries(limit: int | None = None) -> list[Entry]:
    """Logged entries, newest first."""
    if not LOG_FILE.exists():
        return []
    result = []
    for line in LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            data = json.loads(line)
            result.append(
                Entry(
                    time=float(data.get("time", 0)),
                    kind=str(data.get("kind", "")),
                    preset=str(data.get("preset", "")),
                    source=str(data.get("source", "")),
                    detail=str(data.get("detail", "")),
                    size=int(data.get("size", 0)),
                    sha256=str(data.get("sha256", "")),
                    ok=bool(data.get("ok", True)),
                    error=str(data.get("error", "")),
                )
            )
        except (ValueError, TypeError):
            continue
    result.reverse()
    return result[:limit] if limit else result


def clear() -> None:
    LOG_FILE.unlink(missing_ok=True)
