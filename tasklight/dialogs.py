"""Small UI dialogs used by the app shell."""

from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget


def confirm_quit() -> None:
    reply = QMessageBox.question(
        None,
        "Quit Tasklight",
        "Are you sure you want to quit?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    )
    if reply == QMessageBox.StandardButton.Yes:
        QApplication.quit()


def show_about(parent: QWidget) -> None:
    QMessageBox.about(
        parent,
        "About Tasklight",
        "<b>Tasklight</b><br>Desktop widget to track your AI agents.",
    )
