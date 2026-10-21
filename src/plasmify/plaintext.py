"""Untrusted text for Qt tooltips, kept free of Qt so the check runs without PyQt6 too."""

from __future__ import annotations

import html


def plain_tooltip(text: str) -> str:
    """Tooltip text shown literally. Qt renders anything that looks like HTML in a tooltip,
    so untrusted text is escaped and wrapped in an explicit rich-text block."""
    return "<qt>" + html.escape(text).replace("\n", "<br>") + "</qt>"
