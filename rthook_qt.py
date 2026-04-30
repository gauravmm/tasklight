import os
import sys

if hasattr(sys, "_MEIPASS") and sys.platform == "win32":
    meipass = sys._MEIPASS

    # Qt DLLs (Qt6Core, Qt6Gui, …) land directly in _MEIPASS when bundled by
    # PyInstaller, but PyQt6's own __init__.py expects them in Qt6/bin and
    # calls os.add_dll_directory() for that path, which no longer exists in
    # the frozen layout. Add both locations so whichever applies is covered.
    os.add_dll_directory(meipass)
    for candidate in (
        os.path.join(meipass, "PyQt6", "Qt6", "bin"),
        os.path.join(meipass, "PyQt6", "Qt6"),
    ):
        if os.path.isdir(candidate):
            os.add_dll_directory(candidate)

    # Pin the platform plugin path so Qt doesn't search relative to the exe.
    platforms = os.path.join(meipass, "PyQt6", "Qt6", "plugins", "platforms")
    if os.path.isdir(platforms):
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = platforms
