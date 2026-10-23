# PyInstaller build for macOS: CrapSniffer.app (then wrapped in a .dmg by build_macos.sh).
# Must run ON a Mac - PyInstaller cannot cross-build. Use:  bash packaging/build_macos.sh
#
# Unlike the Windows one-file exe, a .app is a normal folder bundle, so it starts quickly
# and needs no splash screen (PyInstaller's splash isn't supported on macOS anyway).

import os

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
VERSION = os.environ.get("CRAPSNIFFER_VERSION", "1.0.0")
MIN_MACOS = os.environ.get("CRAPSNIFFER_MIN_MACOS", "14.0")

datas = [(os.path.join(ROOT, "lossy_detect", "ui"), "lossy_detect/ui")]
binaries = []
hiddenimports = ["imageio_ffmpeg.binaries", "webview.platforms.cocoa"]

# The bundled ffmpeg (decodes M4A/AAC/ALAC/Opus/WMA) lives in imageio_ffmpeg/binaries
datas += collect_data_files("imageio_ffmpeg", subdir="binaries", include_py_files=True)
# pywebview's JS bridge files; on macOS it drives WebKit through PyObjC
d, b, h = collect_all("webview")
datas += d
binaries += b
hiddenimports += h

a = Analysis(
    [os.path.join(SPECPATH, "launch.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6", "IPython", "pytest", "matplotlib.tests",
              "numpy.tests", "scipy.tests", "pythonnet", "clr_loader", "webview.platforms.winforms"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CrapSniffer",
    console=False,
    argv_emulation=False,
    upx=False,
    debug=False,
    strip=False,
    codesign_identity=os.environ.get("CODESIGN_IDENTITY") or None,
    entitlements_file=os.path.join(SPECPATH, "entitlements.plist"),
)
coll = COLLECT(exe, a.binaries, a.datas, name="CrapSniffer", upx=False, strip=False)
app = BUNDLE(
    coll,
    name="CrapSniffer.app",
    icon=os.path.join(SPECPATH, "crapsniffer.icns"),
    bundle_identifier="com.crapsniffer.app",
    version=VERSION,
    info_plist={
        "CFBundleDisplayName": "CrapSniffer",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": MIN_MACOS,
        "LSApplicationCategoryType": "public.app-category.music",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
        "NSHumanReadableCopyright": "CrapSniffer",
    },
)
