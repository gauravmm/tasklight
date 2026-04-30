"""Overlay row and layout datatypes."""

from __future__ import annotations

from dataclasses import dataclass

from tasklight.model import AgentState


@dataclass(frozen=True)
class AgentRow:
    record_session_id: str
    source: str
    state: AgentState
    label: str
    elapsed: str


@dataclass(frozen=True)
class GroupSummary:
    source: str
    state: AgentState
    label: str
    elapsed: str


@dataclass(frozen=True)
class HeaderRow:
    dirname: str
    summary: GroupSummary | None = None
    hostname: str = ""                      # display hostname; empty when all agents are local
    group_key: tuple[str, str] = ("", "")   # (raw_hostname, dirname) for collapse tracking


@dataclass(frozen=True)
class LayoutRow:
    row: HeaderRow | AgentRow
    top: int
    height: int


@dataclass(frozen=True)
class LayoutMetrics:
    pad: int
    em: int
    row_height: int
    glyph_width: int
    elapsed_width: int
    text_gap: int
    content_right: int
    glyph_x: int
    label_x: int
    label_width: int


@dataclass(frozen=True)
class OverlayLayout:
    rows: list[LayoutRow]
    metrics: LayoutMetrics
    total_height: int
