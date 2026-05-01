"""Small UI dialogs used by the app shell."""

import shutil
import subprocess
import sys
from pathlib import Path

from PyQt6.QtWidgets import QMessageBox, QWidget

_GIT_FALLBACK_PATHS = [
    r"C:\Program Files\Git\cmd\git.exe",
    r"C:\Program Files\Git\bin\git.exe",
]


def _find_git() -> str | None:
    git = shutil.which("git")
    if git:
        return git
    if sys.platform == "win32":
        for p in _GIT_FALLBACK_PATHS:
            if Path(p).is_file():
                return p
    return None


def _git_version() -> str:
    if hasattr(sys, "_MEIPASS"):
        try:
            return (Path(sys._MEIPASS) / "_version.txt").read_text().strip()
        except Exception:
            return ""
    git = _find_git()
    if not git:
        return ""
    kwargs: dict = dict(
        cwd=Path(__file__).parent.parent,
        stdin=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        short = subprocess.check_output(
            [git, "rev-parse", "--short", "HEAD"], text=True, **kwargs
        ).strip()
        dirty = subprocess.call([git, "diff", "--quiet"], **kwargs) != 0
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
