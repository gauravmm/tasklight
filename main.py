"""
Tasklight – entry point.

Wires together:
  • HookServer  – receives agent hook events on localhost:57017
  • AgentStateModel – holds all agent state in memory
  • OverlayWidget – displays raw text summary of current state (prototype)
  • System tray icon
"""

import os
import sys
import time
import traceback

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QWidget,
)

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
    AgentState.TOOL:     "Tool",
    AgentState.APPROVAL: "Waiting for approval",
    AgentState.DONE:     "Done",
}

_BG   = QColor(30, 30, 30, 210)
_FG   = QColor(230, 230, 230)
_DIM  = QColor(130, 130, 130)
_FONT = "monospace"


def _fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    if s < 3600:
        return f"{s // 60:02d}:{s % 60:02d}"
    return f"{s // 3600:d}:{(s % 3600) // 60:02d}:{s % 60:02d}"


class OverlayWidget(QWidget):
    _PAD    = 10
    _RADIUS = 10
    _WIDTH  = 360

    def __init__(self, model: AgentStateModel, context_menu: QMenu) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint,
        )
        self._model = model
        self._context_menu = context_menu

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFixedWidth(self._WIDTH)

        model.dataChanged.connect(self._refresh)
        model.rowsInserted.connect(self._refresh)
        model.rowsRemoved.connect(self._refresh)

        # 1 Hz clock refresh for elapsed timers.
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self.update)
        self._clock.start()

        self._refresh()
        self._move_to_bottom_right()

    # ------------------------------------------------------------------
    # Layout helpers
    # ------------------------------------------------------------------

    def _build_lines(self) -> list[tuple[str, QColor, bool]]:
        """Return (text, colour, is_header) triples for the current state."""
        records = [r for r in self._model.records() if not r.dismissed]
        if not records:
            return [("  No agents", _DIM, False)]

        # Group by dirname, preserving insertion order.
        groups: dict[str, list] = {}
        for r in records:
            groups.setdefault(r.dirname, []).append(r)

        lines: list[tuple[str, QColor, bool]] = []
        now = time.monotonic()
        for dirname, recs in groups.items():
            lines.append((f"/{dirname}", _DIM, True))
            for r in recs:
                elapsed = _fmt_elapsed(now - r.started_at)
                if r.state == AgentState.TOOL:
                    label = f"Tool: {r.tool_name or '?'}"
                else:
                    label = _STATE_LABEL[r.state]
                text = f"  {label:<32} {elapsed:>8}"
                lines.append((text, _FG, False))

        return lines

    def _refresh(self) -> None:
        lines = self._build_lines()
        fm = self.fontMetrics()
        lh = fm.height() + 4
        self.setFixedHeight(self._PAD * 2 + len(lines) * lh)
        self.show()
        self.update()

    def closeEvent(self, a0) -> None:  # noqa: N802
        if a0 is not None:
            a0.ignore()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802
        lines = self._build_lines()
        if not lines:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        painter.setBrush(_BG)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), self._RADIUS, self._RADIUS)

        font = QFont(_FONT)
        font.setPointSize(10)
        painter.setFont(font)

        fm = painter.fontMetrics()
        lh = fm.height() + 4
        y = self._PAD + fm.ascent()

        for text, color, _ in lines:
            painter.setPen(QPen(color))
            painter.drawText(self._PAD, y, text)
            y += lh

        painter.end()

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def mousePressEvent(self, a0) -> None:  # noqa: N802
        if a0 is not None and a0.button() == Qt.MouseButton.RightButton:
            self._context_menu.popup(a0.globalPosition().toPoint())

    def _move_to_bottom_right(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        margin = 20
        self.move(
            geo.right() - self.width() - margin,
            geo.bottom() - self.height() - margin,
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    def _excepthook(exc_type, exc_value, exc_tb):
        traceback.print_exception(exc_type, exc_value, exc_tb)
    sys.excepthook = _excepthook

    # Force XCB (X11/XWayland) — Wayland lacks global coords and system tray.
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    model = AgentStateModel()

    server = HookServer()
    server.event_received.connect(model.apply_event)
    server.start()

    overlay = OverlayWidget(model, QMenu())
    context_menu = build_context_menu(overlay)
    overlay._context_menu = context_menu

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
