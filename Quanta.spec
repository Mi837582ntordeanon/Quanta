# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, copy_metadata


ROOT = Path(SPECPATH).resolve()
datas = [
    (str(ROOT / "ui"), "ui"),
    (str(ROOT / "assistant" / "minecraft_bot.js"), "assistant"),
    (str(ROOT / "assistant" / "uia_bridge.ps1"), "assistant"),
    (str(ROOT / "README.md"), "."),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "mcp_servers.example.json"), "."),
]
node_modules = ROOT / "build" / "minecraft-runtime" / "node_modules"
if node_modules.is_dir():
    datas.append((str(node_modules), "node_modules"))

hiddenimports = ["server", "mcp", "mcp_types"]
binaries = []
# Both are optional at runtime, but when present in the build they must be
# collected because their public APIs load implementation modules dynamically.
for package in ("fastembed",):
    try:
        package_datas, package_binaries, package_hidden = collect_all(package)
    except Exception:
        continue
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden
for package in ("mcp", "mcp-types"):
    try:
        datas += copy_metadata(package)
    except Exception:
        pass

a = Analysis(
    [str(ROOT / "launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pytest"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Quanta",
    icon=str(ROOT / "assets" / "quanta.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Quanta",
)
