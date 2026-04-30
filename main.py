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
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap
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
    AgentState.TOOL:     "Tool",
    AgentState.APPROVAL: "Waiting for approval",
    AgentState.DONE:     "Done",
}


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
    _PAD = 10

    def __init__(self, model: AgentStateModel, cfg: AppConfig, context_menu: QMenu) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint,
        )
        self._model = model
        self._context_menu = context_menu
        self._cfg = cfg
        self._update_colors()

        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.setFixedWidth(cfg.dock.width)

        model.dataChanged.connect(self._refresh)
        model.rowsInserted.connect(self._refresh)
        model.rowsRemoved.connect(self._refresh)

        # 1 Hz clock refresh for elapsed timers.
        self._clock = QTimer(self)
        self._clock.setInterval(1000)
        self._clock.timeout.connect(self.update)
        self._clock.start()

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
        self._c_bg          = _hex(t.background, t.background_alpha)
        self._c_fg          = _hex(t.foreground)
        self._c_dim         = _hex(t.dimmed)
        self._c_approval_bg = _hex(t.approval_row_bg)

    # ------------------------------------------------------------------
    # Layout helpers
    # ------------------------------------------------------------------

    def _build_lines(self) -> list[tuple[str, bool]]:
        """Return (text, is_approval) pairs for the current state."""
        records = [r for r in self._model.records() if not r.dismissed]
        if not records:
            return [("  No agents", False)]

        groups: dict[str, list] = {}
        for r in records:
            groups.setdefault(r.dirname, []).append(r)

        lines: list[tuple[str, bool]] = []
        now = time.monotonic()
        for dirname, recs in groups.items():
            lines.append((f"/{dirname}", False))
            for r in recs:
                elapsed = _fmt_elapsed(now - r.started_at)
                if r.state == AgentState.TOOL:
                    label = f"Tool: {r.tool_name or '?'}"
                else:
                    label = _STATE_LABEL[r.state]
                lines.append((f"  {label:<32} {elapsed:>8}", r.state == AgentState.APPROVAL))

        return lines

    def _refresh(self) -> None:
        lines = self._build_lines()
        t = self._cfg.theme
        font = QFont(t.font_family)
        font.setPixelSize(t.font_size_px)
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

        t = self._cfg.theme
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Window background.
        painter.setBrush(self._c_bg)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), t.corner_radius, t.corner_radius)

        font = QFont(t.font_family)
        font.setPixelSize(t.font_size_px)
        painter.setFont(font)

        fm = painter.fontMetrics()
        lh = fm.height() + 4
        y = self._PAD

        for text, is_approval in lines:
            if is_approval:
                row_rect = self.rect().adjusted(0, y, 0, -(self.height() - y - lh))
                painter.fillRect(row_rect, self._c_approval_bg)

            is_header = text.startswith("/")
            painter.setPen(QPen(self._c_dim if is_header else self._c_fg))
            painter.drawText(self._PAD, y + fm.ascent(), text)
            y += lh

        painter.end()

    # ------------------------------------------------------------------
    # Interaction
    # ------------------------------------------------------------------

    def mousePressEvent(self, a0) -> None:  # noqa: N802
        if a0 is not None and a0.button() == Qt.MouseButton.RightButton:
            self._context_menu.popup(a0.globalPosition().toPoint())

    def _move_to_dock(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        m = self._cfg.dock.margin
        pos = self._cfg.dock.position
        w, h = self.width(), self.height()

        x = {"L": geo.left() + m,
             "C": geo.center().x() - w // 2,
             "R": geo.right() - w - m}[pos[1] if len(pos) == 2 else "R"]

        y = {"T": geo.top() + m,
             "M": geo.center().y() - h // 2,
             "B": geo.bottom() - h - m}[pos[0]]

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
        "--config", "-c",
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
