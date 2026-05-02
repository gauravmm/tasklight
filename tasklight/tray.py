"""System tray icon and shared context menu."""

import sys
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from tasklight.dialogs import show_about
from tasklight.model import AgentStateModel

_ICO_PATH = (
    Path(sys._MEIPASS) / "tasklight.ico"
    if hasattr(sys, "_MEIPASS")
    else Path(__file__).parent.parent / "spec" / "tasklight.ico"
)


def build_context_menu(parent: QWidget, model: AgentStateModel) -> QMenu:
    menu = QMenu(parent)

    about_action = QAction("About", menu)
    about_action.triggered.connect(lambda: show_about(parent))
    menu.addAction(about_action)

    menu.addSeparator()

    reset_action = QAction("Reset state", menu)
    reset_action.triggered.connect(model.reset)
    menu.addAction(reset_action)

    quit_action = QAction("Quit", menu)
    quit_action.triggered.connect(lambda: QApplication.instance().exit(0))
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


def load_app_icon() -> QIcon:
    icon = QIcon(str(_ICO_PATH))
    if icon.isNull():
        icon = QIcon(_make_tray_icon_pixmap())
    return icon


def create_tray(parent: QWidget, context_menu: QMenu) -> QSystemTrayIcon | None:
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None

    tray = QSystemTrayIcon(parent)
    tray.setIcon(load_app_icon())
    tray.setToolTip("Tasklight")
    tray.setContextMenu(context_menu)
    tray.activated.connect(
        lambda reason: (
            context_menu.popup(tray.geometry().center()) if reason == QSystemTrayIcon.ActivationReason.Trigger else None
        )
    )
    tray.show()
    return tray
