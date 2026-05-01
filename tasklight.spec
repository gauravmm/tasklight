# -*- mode: python ; coding: utf-8 -*-
import os
import PyQt6

# PyInstaller's PyQt6 hook does not reliably collect platform plugins.
# Collect them explicitly so qwindows.dll / libqxcb.so are bundled.
_qt6_plugins = os.path.join(os.path.dirname(PyQt6.__file__), "Qt6", "plugins")
_plugin_datas = [
    (os.path.join(_qt6_plugins, sub), f"PyQt6/Qt6/plugins/{sub}")
    for sub in ("platforms", "styles")
    if os.path.isdir(os.path.join(_qt6_plugins, sub))
]

a = Analysis(
    ['tasklight/__main__.py'],
    pathex=[],
    binaries=[],
    datas=_plugin_datas + [('spec/tasklight.ico', '.')],
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
