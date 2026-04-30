import os
import sys

if hasattr(sys, "_MEIPASS") and sys.platform == "win32":
    meipass = sys._MEIPASS

    # Qt DLLs land in PyQt6/Qt6/bin/ (confirmed from bundle layout).
    # os.add_dll_directory() is required on Windows 8+ because PATH is no
    # longer used for DLL resolution; qwindows.dll needs Qt6Gui, Qt6Core, etc.
    os.add_dll_directory(meipass)
    qt_bin = os.path.join(meipass, "PyQt6", "Qt6", "bin")
    if os.path.isdir(qt_bin):
        os.add_dll_directory(qt_bin)

    # Pin platform plugin path so Qt doesn't search relative to the exe.
    platforms = os.path.join(meipass, "PyQt6", "Qt6", "plugins", "platforms")
    if os.path.isdir(platforms):
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = platforms
