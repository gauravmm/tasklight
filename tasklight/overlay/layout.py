"""Shared layout and hit-testing for the overlay widget."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QFontMetrics

from tasklight.overlay.types import AgentRow, HeaderRow, LayoutMetrics, LayoutRow, OverlayLayout


def padding(fm: QFontMetrics) -> int:
    return max(8, fm.horizontalAdvance("M"))


def em(fm: QFontMetrics) -> int:
    return max(1, fm.horizontalAdvance("M"))


def elide(fm: QFontMetrics, text: str, width: int) -> str:
    if width <= 0:
        return ""
    return fm.elidedText(text, Qt.TextElideMode.ElideRight, width)


def build_layout(
    rows: list[HeaderRow | AgentRow],
    fm: QFontMetrics,
    width: int,
) -> OverlayLayout:
    pad = padding(fm)
    em_width = em(fm)
    row_height = fm.height() + 4
    glyph_width = max(fm.horizontalAdvance("⠿"), fm.horizontalAdvance("✻"))
    elapsed_width = max(
        fm.horizontalAdvance("00:00"),
        fm.horizontalAdvance("00:00:00"),
    )
    text_gap = max(1, em_width // 2)
    content_right = width - pad
    glyph_x = pad + em_width
    label_x = glyph_x + glyph_width + text_gap
    label_width = content_right - label_x - elapsed_width - text_gap

    layout_rows: list[LayoutRow] = []
    top = pad
    for row in rows:
        layout_rows.append(LayoutRow(row, top, row_height))
        top += row_height

    total_height = pad * 2
    if layout_rows:
        last_row = layout_rows[-1]
        total_height = last_row.top + last_row.height + pad

    metrics = LayoutMetrics(
        pad=pad,
        em=em_width,
        row_height=row_height,
        glyph_width=glyph_width,
        elapsed_width=elapsed_width,
        text_gap=text_gap,
        content_right=content_right,
        glyph_x=glyph_x,
        label_x=label_x,
        label_width=label_width,
    )
    return OverlayLayout(layout_rows, metrics, total_height)


def hit_test(layout: OverlayLayout, pos: QPointF) -> HeaderRow | AgentRow | None:
    for layout_row in layout.rows:
        if layout_row.top <= pos.y() < layout_row.top + layout_row.height:
            return layout_row.row
    return None
