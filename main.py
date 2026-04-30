"""
Tasklight – starter code.

Features:
  • A frameless, always-on-top overlay widget with a translucent background and
    opaque text (WA_TranslucentBackground + custom paintEvent).
  • A system tray icon.
  • Both the overlay and the tray icon share the same context menu, which
    includes an "About" action.

Run:
    python main.py
"""

import sys

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QWidget,
)


# ---------------------------------------------------------------------------
# Shared context menu
# ---------------------------------------------------------------------------

def build_context_menu(parent: QWidget) -> QMenu:
    """Return a context menu shared by the overlay and the tray icon."""
    menu = QMenu(parent)

    about_action = QAction("About", menu)
    about_action.triggered.connect(lambda: show_about(parent))
    menu.addAction(about_action)

    menu.addSeparator()

    quit_action = QAction("Quit", menu)
    quit_action.triggered.connect(QApplication.quit)
    menu.addAction(quit_action)

    return menu


def show_about(parent: QWidget) -> None:
    QMessageBox.about(
        parent,
        "About Tasklight",
        "<b>Tasklight</b><br>Desktop widget to track your AI agents.",
    )


# ---------------------------------------------------------------------------
# Tray icon helper
# ---------------------------------------------------------------------------

def _make_tray_icon_pixmap(size: int = 32) -> QPixmap:
    """Generate a simple coloured square as a placeholder tray icon."""
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
# Overlay widget
# ---------------------------------------------------------------------------

class OverlayWidget(QWidget):
    """
    A frameless, always-on-top widget with:
      • a semi-transparent background (translucent fill)
      • opaque text drawn on top
    Right-clicking (or left-clicking) opens the shared context menu.
    """

    _BG_ALPHA = 160  # 0 = fully transparent, 255 = fully opaque background
    _BG_COLOR = QColor(30, 30, 30, _BG_ALPHA)
    _TEXT_COLOR = QColor(255, 255, 255, 255)  # fully opaque white text
    _LABEL = "Tasklight"
    _CORNER_RADIUS = 12

    def __init__(self, context_menu: QMenu) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self._context_menu = context_menu

        # Enable translucent (ARGB) window background
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.setFixedSize(220, 70)
        self._move_to_bottom_right()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Translucent rounded background
        painter.setBrush(self._BG_COLOR)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(
            self.rect(), self._CORNER_RADIUS, self._CORNER_RADIUS
        )

        # Opaque text
        painter.setPen(QPen(self._TEXT_COLOR))
        font = QFont()
        font.setPointSize(16)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._LABEL)

        painter.end()

    # ------------------------------------------------------------------
    # Context menu on any mouse button press
    # ------------------------------------------------------------------

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self._context_menu.exec(event.globalPosition().toPoint())

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

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
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # keep alive when overlay is hidden

    # Build the context menu; actions are parented to the menu itself.
    context_menu = build_context_menu(None)
    overlay = OverlayWidget(context_menu)
    context_menu.setParent(overlay)
    overlay.show()

    # --- Tray icon ---
    if not QSystemTrayIcon.isSystemTrayAvailable():
        print("Warning: system tray is not available on this platform.")
    else:
        tray = QSystemTrayIcon(overlay)
        tray.setIcon(QIcon(_make_tray_icon_pixmap()))
        tray.setToolTip("Tasklight")
        tray.setContextMenu(context_menu)
        tray.activated.connect(
            lambda reason: (
                context_menu.exec(tray.geometry().center())
                if reason == QSystemTrayIcon.ActivationReason.Trigger
                else None
            )
        )
        tray.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
