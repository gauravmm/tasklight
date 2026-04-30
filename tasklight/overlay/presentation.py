"""Presentation policy for overlay rows."""

from PyQt6.QtGui import QColor

from tasklight.config import ThemeConfig
from tasklight.model import AgentState

STATE_LABELS = {
    AgentState.THINKING: "Thinking…",
    AgentState.TOOL: "Tool",
    AgentState.APPROVAL: "Waiting for approval",
    AgentState.DONE: "Done",
}

_SPINNER_FRAMES = {
    "claude": ["·", "✻", "✽", "✶", "✱", "✢"],
    "braille": ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
}


def fmt_elapsed(seconds: float) -> str:
    total_seconds = int(seconds)
    if total_seconds < 3600:
        return f"{total_seconds // 60:02d}:{total_seconds % 60:02d}"
    return (
        f"{total_seconds // 3600:d}:"
        f"{(total_seconds % 3600) // 60:02d}:"
        f"{total_seconds % 60:02d}"
    )


def hex_color(color_str: str, alpha: float = 1.0) -> QColor:
    color = QColor(color_str)
    color.setAlphaF(alpha)
    return color


def spinner_frames(source: str) -> list[str]:
    spinner_name = {"claude-code": "claude"}.get(source, "braille")
    return _SPINNER_FRAMES[spinner_name]


def static_spinner_glyph(source: str) -> str:
    return {"claude-code": "✻"}.get(source, "⠿")


def glyph_for_state(
    source: str,
    state: AgentState,
    *,
    frame: int,
    animate: bool,
    theme: ThemeConfig,
) -> tuple[str, QColor]:
    if state in (AgentState.THINKING, AgentState.TOOL):
        glyph = (
            spinner_frames(source)[frame % len(spinner_frames(source))]
            if animate
            else static_spinner_glyph(source)
        )
        color = hex_color("#d97757") if source == "claude-code" else hex_color("#88ddff")
        return glyph, color
    if state == AgentState.APPROVAL:
        return "●", hex_color(theme.approval_fg)
    return "●", hex_color(theme.done_fg)
