# -*- mode: python ; coding: utf-8 -*-
import os
import subprocess
import PyQt6

def _build_version():
    import shutil, sys as _sys
    _GIT_FALLBACK = [
        r"C:\Program Files\Git\cmd\git.exe",
        r"C:\Program Files\Git\bin\git.exe",
    ]
    git = shutil.which("git")
    if not git and _sys.platform == "win32":
        import os
        git = next((p for p in _GIT_FALLBACK if os.path.isfile(p)), None)
    if not git:
        return ""
    _kw = dict(stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if _sys.platform == "win32":
        _kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        short = subprocess.check_output(
            [git, "rev-parse", "--short", "HEAD"], text=True, **_kw
        ).strip()
        dirty = subprocess.call([git, "diff", "--quiet"], **_kw) != 0
        return f"{short}{'-dirty' if dirty else ''}"
    except Exception:
        return ""

_version_file = os.path.join(os.path.dirname(os.path.abspath(SPEC)), "_version.txt")
with open(_version_file, "w") as _f:
    _f.write(_build_version())

# PyInstaller's PyQt6 hook does not reliably collect platform plugins.
# Collect them explicitly so qwindows.dll / libqxcb.so are bundled.
_qt6_plugins = os.path.join(os.path.dirname(PyQt6.__file__), "Qt6", "plugins")
_plugin_datas = [
    (os.path.join(_qt6_plugins, sub), f"PyQt6/Qt6/plugins/{sub}")
    for sub in ("platforms", "styles", "imageformats")
    if os.path.isdir(os.path.join(_qt6_plugins, sub))
]

a = Analysis(
    ['tasklight/__main__.py'],
    pathex=[],
    binaries=[],
    datas=_plugin_datas + [('spec/tasklight.ico', '.'), (_version_file, '.')],
    hiddenimports=['PyQt6.sip', 'pkgutil'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=['rthook_qt.py'],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='tasklight',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='spec/tasklight.ico',
)
