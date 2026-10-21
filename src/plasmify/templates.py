"""Rendering bundled templates and editing user files in managed blocks."""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

BLOCK_BEGIN = "plasmify:begin"
BLOCK_END = "plasmify:end"


def read(name: str) -> str:
    return (resources.files("plasmify") / "templates" / name).read_text(encoding="utf-8")


def render_text(text: str, variables: dict[str, object]) -> str:
    """Replace ``{{ name }}`` placeholders. Unknown names are an error, not silently empty."""

    def substitute(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in variables:
            raise KeyError(f"Template variable '{key}' is not defined")
        return str(variables[key])

    return _VAR_RE.sub(substitute, text)


def render(name: str, variables: dict[str, object]) -> str:
    return render_text(read(name), variables)


def upsert_block(path: Path, body: str, comment: str = "#") -> None:
    """Insert or replace the Plasmify-managed block in ``path``, leaving the rest untouched."""
    begin = f"{comment} >>> {BLOCK_BEGIN} (managed by Plasmify, edits inside will be lost) >>>"
    end = f"{comment} <<< {BLOCK_END} <<<"
    block = f"{begin}\n{body.rstrip()}\n{end}\n"
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    pattern = re.compile(
        rf"^{re.escape(comment)} >>> {BLOCK_BEGIN}.*?^{re.escape(comment)} <<< {BLOCK_END} <<<\n?",
        re.MULTILINE | re.DOTALL,
    )
    if pattern.search(text):
        text = pattern.sub(block.replace("\\", "\\\\"), text, count=1)
    else:
        text = text + ("\n" if text and not text.endswith("\n") else "") + block
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def remove_block(path: Path, comment: str = "#") -> None:
    if not path.exists():
        return
    pattern = re.compile(
        rf"^{re.escape(comment)} >>> {BLOCK_BEGIN}.*?^{re.escape(comment)} <<< {BLOCK_END} <<<\n?",
        re.MULTILINE | re.DOTALL,
    )
    path.write_text(pattern.sub("", path.read_text(encoding="utf-8")), encoding="utf-8")
