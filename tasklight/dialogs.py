"""Small UI dialogs used by the app shell."""

import subprocess
import sys
from pathlib import Path

from PyQt6.QtWidgets import QMessageBox, QWidget


def _git_version() -> str:
    if hasattr(sys, "_MEIPASS"):
        try:
            return (Path(sys._MEIPASS) / "_version.txt").read_text().strip()
        except Exception:
            return ""
    try:
        root = Path(__file__).parent.parent
        short = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        dirty = subprocess.call(
            ["git", "diff", "--quiet"],
            cwd=root,
            stderr=subprocess.DEVNULL,
        ) != 0
        return f"{short}{'-dirty' if dirty else ''}"
    except Exception:
        return ""



def show_about(parent: QWidget) -> None:
    version = _git_version()
    version_line = f"<br><small>{version}</small>" if version else ""
    QMessageBox.about(
        parent,
        "About Tasklight",
        f'<b>Tasklight</b><br>Desktop widget to track your AI agents.{version_line}'
        f'<br><a href="https://github.com/gauravmm/tasklight/">github.com/gauravmm/tasklight</a>',
    )
