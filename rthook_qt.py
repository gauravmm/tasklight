import os
import sys

if hasattr(sys, "_MEIPASS") and sys.platform == "win32":
    # Windows 8+ no longer uses PATH for DLL resolution (security change).
    # os.add_dll_directory() is the correct way to make qwindows.dll find
    # Qt6Gui, Qt6Core, etc., which PyInstaller places in _MEIPASS.
    os.add_dll_directory(sys._MEIPASS)
