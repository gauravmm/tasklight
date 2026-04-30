import os
import sys

if hasattr(sys, "_MEIPASS") and sys.platform == "win32":
    # qwindows.dll needs Qt6Gui/Qt6Core etc., which PyInstaller puts in
    # _MEIPASS. Windows won't find them there without this PATH entry.
    os.environ["PATH"] = sys._MEIPASS + os.pathsep + os.environ.get("PATH", "")
