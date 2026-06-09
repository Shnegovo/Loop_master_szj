# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import copy_metadata

ROOT = Path(SPECPATH).resolve()
VENV = ROOT / '.venv-build'
if not VENV.exists():
    VENV = Path(r'D:\LoopMaster_v1.3\.venv-build')
LIBUSB_DLL = VENV / 'Lib' / 'site-packages' / 'libusb_package' / 'libusb-1.0.dll'

datas = [('config', 'config'), ('assets', 'assets')]
hiddenimports = []
datas += collect_data_files('pyocd')
datas += collect_data_files('cmsis_pack_manager')
datas += copy_metadata('pyocd')
datas += copy_metadata('hidapi')
datas += copy_metadata('pyusb')
datas += copy_metadata('libusb-package')
datas += copy_metadata('cmsis-pack-manager')
hiddenimports += collect_submodules('pyocd')
hiddenimports += collect_submodules('cmsis_pack_manager')


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[(str(LIBUSB_DLL), 'libusb_package')] if LIBUSB_DLL.exists() else [],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
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
    name='LoopMaster_v2.1',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    icon=str(ROOT / 'assets' / 'app_icon.ico'),
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
