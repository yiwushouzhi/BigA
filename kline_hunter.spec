# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（在目标平台执行，Windows 才能打包出 exe）：

    pip install ".[dev]"
    python -m PyInstaller --noconfirm --clean kline_hunter.spec

产物：dist/KLineHunter.exe（单文件、无控制台窗口）。
首次运行会以 exe 所在目录下的 stock_data 作为数据目录。
"""

from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ["pytdx", "openai", "openpyxl", "pyarrow", "pandas"]
hiddenimports += collect_submodules("pytdx")

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "scipy",
        "IPython",
        "notebook",
        "PyQt6.QtWebEngineWidgets",
        "PyQt6.QtWebEngineCore",
        "PyQt6.QtMultimedia",
        "PyQt6.QtQml",
        "PyQt6.QtQuick",
    ],
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
    name="KLineHunter",
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
)
