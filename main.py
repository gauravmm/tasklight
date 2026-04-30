from __future__ import annotations

"""
Tasklight – entry point.

Wires together:
  • HookServer     – receives agent hook events on localhost:{port}
  • AgentStateModel – holds all agent state in memory
  • ConfigWatcher  – hot-reloads tasklight.yaml
  • OverlayWidget  – displays agent state as raw text (prototype)
  • System tray icon

Usage:
    python main.py [--config PATH]   (default: ./tasklight.yaml)
"""

import argparse
import os
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QPoint, QPointF, Qt, QTimer
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QFontMetrics,
    QIcon,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QWidget,
)

from tasklight.config import AppConfig, ConfigWatcher, load_config
from tasklight.model import AgentState, AgentStateModel
from tasklight.server import HookServer


# ---------------------------------------------------------------------------
# Shared context menu
# ---------------------------------------------------------------------------


def _confirm_quit() -> None:
    reply = QMessageBox.question(
        None,
        "Quit Tasklight",
        "Are you sure you want to quit?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if reply == QMessageBox.StandardButton.Yes:
        QApplication.quit()


def build_context_menu(parent: QWidget) -> QMenu:
    menu = QMenu(parent)

    about_action = QAction("About", menu)
    about_action.triggered.connect(lambda: show_about(parent))
    menu.addAction(about_action)

    menu.addSeparator()

    quit_action = QAction("Quit", menu)
    quit_action.triggered.connect(_confirm_quit)
    menu.addAction(quit_action)

    return menu


def show_about(parent: QWidget) -> None:
    QMessageBox.about(
        parent,
        "About Tasklight",
        "<b>Tasklight</b><br>Desktop widget to track your AI agents.",
    )


# ---------------------------------------------------------------------------
# Tray icon
# ---------------------------------------------------------------------------


def _make_tray_icon_pixmap(size: int = 32) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(80, 140, 255))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, size - 4, size - 4, 6, 6)
    painter.setPen(QPen(Qt.GlobalColor.white, 2))
    font = QFont()
    font.setBold(True)
    font.setPixelSize(size // 2)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "TL")
    painter.end()
    return pixmap


# ---------------------------------------------------------------------------
# Overlay widget – raw-text prototype
# ---------------------------------------------------------------------------

_STATE_LABEL = {
    AgentState.THINKING: "Thinking…",
    AgentState.TOOL: "Tool",
    AgentState.APPROVAL: "Waiting for approval",
    AgentState.DONE: "Done",
}

_SPINNER_FRAMES = {
    "claude": ["·", "✻", "✽", "✶", "✱", "✢"],
    "braille": ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
}


@dataclass
class AgentRow:
    record_session_id: str
    source: str
    state: AgentState
    label: str
    elapsed: str


@dataclass
class GroupSummary:
    source: str
    state: AgentState
    label: str
    elapsed: str


@dataclass
class HeaderRow:
    dirname: str
    summary: GroupSummary | None = None


@dataclass
class LayoutRow:
    row: HeaderRow | AgentRow
    top: int
    height: int


def _fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    if s < 3600:
        return f"{s // 60:02d}:{s % 60:02d}"
    return f"{s // 3600:d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


def _hex(color_str: str, alpha: float = 1.0) -> QColor:
    c = QColor(color_str)
    c.setAlphaF(alpha)
    return c


class OverlayWidget(QWidget):
    _CURSOR_POINTS = [
        QPoint(0, 0),
        QPoint(0, 18),
        QPoint(4, 14),
        QPoint(7, 22),
        QPoint(11, 21),
        QPoint(8, 13),
        QPoint(14, 13),
    ]

    def __init__(
        self, model: AgentStateModel, cfg: AppConfig, context_menu: QMenu
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint,
        )
        self._model = model
        self._context_menu = context_menu
        self._cfg = cfg
        self._frame = 0
        self._cursor_pos: QPointF | None = None
        self._collapsed_groups: set[str] = set()
        self._update_colors()

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setMouseTracking(True)
        self.setFixedWidth(cfg.dock.width)

        model.dataChanged.connect(self._refresh)
        model.rowsInserted.connect(self._refresh)
        model.rowsRemoved.connect(self._refresh)

        # 1 Hz clock refresh for elapsed timers.
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self.update)
        self._clock.start()

        # Spinner animation runs only while active rows exist.
        self._anim = QTimer(self)
        self._anim.setInterval(125)
        self._anim.timeout.connect(self._tick_spinner)

        self._refresh()
        self._move_to_dock()

    def apply_config(self, cfg: AppConfig) -> None:
        self._cfg = cfg
        self._update_colors()
        self.setFixedWidth(cfg.dock.width)
        self._refresh()
        self._move_to_dock()

    def _update_colors(self) -> None:
        t = self._cfg.theme
        self._c_bg = _hex(t.background, t.background_alpha)
        self._c_fg = _hex(t.foreground)
        self._c_dim = _hex(t.dimmed)
        self._c_approval_bg = _hex(t.approval_row_bg)

    # ------------------------------------------------------------------
    # Layout helpers
    # ------------------------------------------------------------------

    def _spinner_frames(self, source: str) -> list[str]:
        spinner_name = {
            "claude-code": "claude",
        }.get(source, "braille")
        return _SPINNER_FRAMES[spinner_name]

    def _static_spinner_glyph(self, source: str) -> str:
        return {
            "claude-code": "✻",
        }.get(source, "⠿")

    def _summary_for_group(
        self, records: list, now: float
    ) -> GroupSummary:
        elapsed = _fmt_elapsed(min(now - r.state_entered_at for r in records))

        for state, label in (
            (AgentState.APPROVAL, _STATE_LABEL[AgentState.APPROVAL]),
            (AgentState.DONE, _STATE_LABEL[AgentState.DONE]),
        ):
            matches = [r for r in records if r.state == state]
            if matches:
                return GroupSummary(matches[0].source, state, label, elapsed)

        active = [r for r in records if r.state in (AgentState.THINKING, AgentState.TOOL)]
        source = active[0].source if active else ""
        return GroupSummary(source, AgentState.THINKING, "Working…", elapsed)

    def _build_lines(self) -> list[HeaderRow | AgentRow]:
        records = [r for r in self._model.records() if not r.dismissed]
        if not records:
            return [AgentRow("", "", AgentState.DONE, "No agents", "")]

        groups: dict[str, list] = {}
        for r in records:
            groups.setdefault(r.dirname, []).append(r)

        lines: list[HeaderRow | AgentRow] = []
        now = time.monotonic()
        for dirname, recs in groups.items():
            if dirname in self._collapsed_groups:
                lines.append(HeaderRow(dirname, self._summary_for_group(recs, now)))
                continue

            lines.append(HeaderRow(dirname))
            for r in recs:
                elapsed = _fmt_elapsed(now - r.state_entered_at)
                if r.state == AgentState.TOOL:
                    label = f"Tool: {r.tool_name or '?'}"
                else:
                    label = _STATE_LABEL[r.state]
                lines.append(AgentRow(r.session_id, r.source, r.state, label, elapsed))

        return lines

    def _has_active_rows(self) -> bool:
        if not self._cfg.theme.animate_spinners:
            return False
        return any(
            (
                isinstance(row, AgentRow)
                and row.state in (AgentState.THINKING, AgentState.TOOL)
            )
            or (
                isinstance(row, HeaderRow)
                and row.summary is not None
                and row.summary.state in (AgentState.THINKING, AgentState.TOOL)
            )
            for row in self._build_lines()
        )

    def _glyph_for_state(self, source: str, state: AgentState) -> tuple[str, QColor]:
        t = self._cfg.theme
        if state == AgentState.THINKING or state == AgentState.TOOL:
            if self._cfg.theme.animate_spinners:
                frames = self._spinner_frames(source)
                glyph = frames[self._frame % len(frames)]
            else:
                glyph = self._static_spinner_glyph(source)
            color = _hex("#d97757") if source == "claude-code" else _hex("#88ddff")
            return (glyph, color)
        if state == AgentState.APPROVAL:
            return ("●", _hex(t.accent_approval))
        return ("●", _hex(t.accent_done))

    def _layout_rows(self, fm: QFontMetrics) -> list[LayoutRow]:
        lines = self._build_lines()
        pad = self._padding(fm)
        lh = fm.height() + 4
        y = pad
        layout: list[LayoutRow] = []
        for row in lines:
            layout.append(LayoutRow(row, y, lh))
            y += lh
        return layout

    def _hit_row(self, pos: QPointF) -> HeaderRow | AgentRow | None:
        font = self.font()
        fm = QFontMetrics(font)
        for layout_row in self._layout_rows(fm):
            if layout_row.top <= pos.y() < layout_row.top + layout_row.height:
                return layout_row.row
        return None

    def _tick_spinner(self) -> None:
        self._frame += 1
        self.update()

    def _elide(self, fm: QFontMetrics, text: str, width: int) -> str:
        if width <= 0:
            return ""
        return fm.elidedText(text, Qt.TextElideMode.ElideRight, width)

    def _padding(self, fm: QFontMetrics) -> int:
        return max(8, fm.horizontalAdvance("M"))

    def _em(self, fm: QFontMetrics) -> int:
        return max(1, fm.horizontalAdvance("M"))

    def _draw_cursor(self, painter: QPainter) -> None:
        if self._cursor_pos is None:
            return

        painter.save()
        painter.translate(self._cursor_pos)
        painter.setBrush(QColor(255, 255, 255))
        painter.setPen(QPen(QColor(0, 0, 0), 1))
        painter.drawPolygon(self._CURSOR_POINTS)
        painter.restore()

    def _refresh(self) -> None:
        t = self._cfg.theme
        font = QFont(t.font_family)
        font.setPixelSize(t.font_size_px)
        fm = QFontMetrics(font)
        self.setFont(font)
        layout_rows = self._layout_rows(fm)
        pad = self._padding(fm)
        total_height = pad * 2
        if layout_rows:
            last_row = layout_rows[-1]
            total_height = last_row.top + last_row.height + pad
        self.setFixedHeight(total_height)
        if self._has_active_rows():
            if not self._anim.isActive():
                self._anim.start()
        else:
            self._anim.stop()
        self.show()
        self.update()

    def closeEvent(self, a0) -> None:  # noqa: N802
        if a0 is not None:
            a0.ignore()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802
        t = self._cfg.theme
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        font = QFont(t.font_family)
        font.setPixelSize(t.font_size_px)
        painter.setFont(font)

        fm = painter.fontMetrics()
        layout_rows = self._layout_rows(fm)
        if not layout_rows:
            painter.end()
            return

        # Window background.
        painter.setBrush(self._c_bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), t.corner_radius, t.corner_radius)
        pad = self._padding(fm)
        em = self._em(fm)
        glyph_width = max(
            fm.horizontalAdvance("⠿"),
            fm.horizontalAdvance("✻"),
        )
        elapsed_width = max(fm.horizontalAdvance("00:00"), fm.horizontalAdvance("00:00:00"))
        text_gap = max(1, em // 2)
        content_right = self.width() - pad
        glyph_x = pad + em
        label_x = glyph_x + glyph_width + text_gap
        label_width = content_right - label_x - elapsed_width - text_gap

        for layout_row in layout_rows:
            row = layout_row.row
            is_header = isinstance(row, HeaderRow)
            summary = row.summary if is_header else None
            status = summary if summary is not None else row if isinstance(row, AgentRow) else None
            is_approval = status is not None and status.state == AgentState.APPROVAL
            if is_approval:
                row_rect = self.rect().adjusted(
                    0,
                    layout_row.top,
                    0,
                    -(self.height() - layout_row.top - layout_row.height),
                )
                painter.fillRect(row_rect, self._c_approval_bg)

            baseline = layout_row.top + fm.ascent()
            if is_header and summary is None:
                painter.setPen(QPen(self._c_dim))
                painter.drawText(
                    pad,
                    baseline,
                    self._elide(fm, f"/{row.dirname}", content_right - pad),
                )
            elif is_header and summary is not None:
                elapsed_x = self.width() - pad - elapsed_width
                dirname_text = f"/{row.dirname}"
                dirname_max = max(em * 8, (elapsed_x - pad) // 3)
                dirname_text = self._elide(fm, dirname_text, dirname_max)
                dirname_width = fm.horizontalAdvance(dirname_text)
                summary_glyph_x = pad + dirname_width + text_gap
                summary_label_x = summary_glyph_x + glyph_width + text_gap
                summary_label_width = elapsed_x - summary_label_x - text_gap

                painter.setPen(QPen(self._c_dim))
                painter.drawText(pad, baseline, dirname_text)
                glyph, glyph_color = self._glyph_for_state(summary.source, summary.state)
                painter.setPen(QPen(glyph_color))
                painter.drawText(summary_glyph_x, baseline, glyph)
                painter.setPen(QPen(self._c_fg))
                painter.drawText(
                    summary_label_x,
                    baseline,
                    self._elide(fm, summary.label, summary_label_width),
                )
                painter.setPen(QPen(self._c_dim))
                painter.drawText(elapsed_x, baseline, summary.elapsed)
            else:
                if row.record_session_id == "":
                    painter.setPen(QPen(self._c_fg))
                    painter.drawText(
                        label_x,
                        baseline,
                        self._elide(fm, row.label, label_width),
                    )
                    continue

                glyph, glyph_color = self._glyph_for_state(row.source, row.state)
                painter.setPen(QPen(glyph_color))
                painter.drawText(glyph_x, baseline, glyph)

                elapsed_x = self.width() - pad - elapsed_width
                painter.setPen(QPen(self._c_fg))
                painter.drawText(
                    label_x,
                    baseline,
                    self._elide(fm, row.label, label_width),
                )
                painter.setPen(QPen(self._c_dim))
                painter.drawText(elapsed_x, baseline, row.elapsed)

        self._draw_cursor(painter)
        painter.end()

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def mousePressEvent(self, a0) -> None:  # noqa: N802
        if a0 is None:
            return

        if a0.button() == Qt.MouseButton.RightButton:
            self._context_menu.popup(a0.globalPosition().toPoint())
            return

        if a0.button() != Qt.MouseButton.LeftButton:
            return

        row = self._hit_row(a0.position())
        if isinstance(row, HeaderRow):
            if row.dirname in self._collapsed_groups:
                self._collapsed_groups.remove(row.dirname)
            else:
                self._collapsed_groups.add(row.dirname)
            self._refresh()
            return

        if isinstance(row, AgentRow) and row.record_session_id and row.state == AgentState.DONE:
            self._model.dismiss(row.record_session_id)

    def mouseMoveEvent(self, a0) -> None:  # noqa: N802
        if a0 is not None:
            self._cursor_pos = a0.position()
            self.update()

    def enterEvent(self, a0) -> None:  # noqa: N802
        if a0 is not None and hasattr(a0, "position"):
            self._cursor_pos = a0.position()
        self.update()

    def leaveEvent(self, _a0) -> None:  # noqa: N802
        self._cursor_pos = None
        self.update()

    def _move_to_dock(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        m = self._cfg.dock.margin
        pos = self._cfg.dock.position
        w, h = self.width(), self.height()

        x = {
            "L": geo.left() + m,
            "C": geo.center().x() - w // 2,
            "R": geo.right() - w - m,
        }[pos[1] if len(pos) == 2 else "R"]

        y = {
            "T": geo.top() + m,
            "M": geo.center().y() - h // 2,
            "B": geo.bottom() - h - m,
        }[pos[0]]

        self.move(x, y)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    def _excepthook(exc_type, exc_value, exc_tb):
        traceback.print_exception(exc_type, exc_value, exc_tb)

    sys.excepthook = _excepthook

    parser = argparse.ArgumentParser(description="Tasklight agent monitor")
    parser.add_argument(
        "--config",
        "-c",
        type=Path,
        default=Path("tasklight.yaml"),
        metavar="PATH",
        help="Config file (default: ./tasklight.yaml)",
    )
    args = parser.parse_args()

    # Force XCB (X11/XWayland) — Wayland lacks global coords and system tray.
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    cfg = load_config(args.config)
    model = AgentStateModel()

    server = HookServer(port=cfg.port)
    server.event_received.connect(model.apply_event)
    server.start()

    overlay = OverlayWidget(model, cfg, QMenu())
    context_menu = build_context_menu(overlay)
    overlay._context_menu = context_menu

    watcher = ConfigWatcher(args.config)
    watcher.config_changed.connect(overlay.apply_config)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("Warning: system tray not available.", file=sys.stderr)
    else:
        tray = QSystemTrayIcon(overlay)
        tray.setIcon(QIcon(_make_tray_icon_pixmap()))
        tray.setToolTip("Tasklight")
        tray.setContextMenu(context_menu)
        tray.activated.connect(
            lambda reason: (
                context_menu.popup(tray.geometry().center())
                if reason == QSystemTrayIcon.ActivationReason.Trigger
                else None
            )
        )
        tray.show()

    app.aboutToQuit.connect(server.stop)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
