"""System tray icon and shared context menu."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QMenu, QSystemTrayIcon, QWidget

from tasklight.dialogs import confirm_quit, show_about


def build_context_menu(parent: QWidget) -> QMenu:
    menu = QMenu(parent)

    about_action = QAction("About", menu)
    about_action.triggered.connect(lambda: show_about(parent))
    menu.addAction(about_action)

    menu.addSeparator()

    quit_action = QAction("Quit", menu)
    quit_action.triggered.connect(confirm_quit)
    menu.addAction(quit_action)

    return menu


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


def create_tray(parent: QWidget, context_menu: QMenu) -> QSystemTrayIcon | None:
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None

    tray = QSystemTrayIcon(parent)
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
    return tray
