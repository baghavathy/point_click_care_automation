# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the Gateway PCC desktop agent.
# Build with:  uv run pyinstaller --noconfirm GatewayPCC.spec
# Output:      dist/GatewayPCC.exe  (Windows, when built on Windows)
#
# Note: PyInstaller is NOT a cross-compiler — run this ON Windows to get a .exe.
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = collect_submodules("selenium") + ["pyotp", "flask"]

# Bundle the whole frontend (templates + static) alongside the code so the
# packaged app can serve the UI from sys._MEIPASS.
datas = [
    ("frontend", "frontend"),
]

block_cipher = None

a = Analysis(
    ["run_desktop.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="GatewayPCC",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,            # keep a console window for status/logs
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="frontend/static/favicon.ico",
)
