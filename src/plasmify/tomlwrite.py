"""A minimal TOML writer for preset data (the standard library only reads TOML).

Supports exactly what presets use: strings, booleans, integers, floats, lists of
scalars, nested tables and arrays of tables. Output is stable and readable.
"""

from __future__ import annotations

import json
from typing import Any


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, str):
        # JSON string escaping is valid TOML basic-string escaping.
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list) and all(not isinstance(v, dict) for v in value):
        return "[" + ", ".join(_scalar(v) for v in value) + "]"
    raise TypeError(f"Unsupported TOML value: {value!r}")


def _is_table_array(value: Any) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def _emit(table: dict[str, Any], prefix: str, lines: list[str]) -> None:
    scalars = {k: v for k, v in table.items() if not isinstance(v, dict) and not _is_table_array(v)}
    for key, value in scalars.items():
        lines.append(f"{key} = {_scalar(value)}")
    for key, value in table.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            lines += ["", f"[{name}]"]
            _emit(value, name, lines)
        elif _is_table_array(value):
            for item in value:
                lines += ["", f"[[{name}]]"]
                _emit(item, name, lines)


def dumps(data: dict[str, Any], header: str = "") -> str:
    lines: list[str] = [f"# {line}" for line in header.splitlines()]
    if lines:
        lines.append("")
    _emit(data, "", lines)
    return "\n".join(line for i, line in enumerate(lines) if line or (i and lines[i - 1])).strip() + "\n"
