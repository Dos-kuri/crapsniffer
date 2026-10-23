# PyInstaller build for a single-file, windowed CrapSniffer.exe.
# Build with:  uv run pyinstaller packaging/crapsniffer.spec --noconfirm
# Output:      dist/CrapSniffer.exe

import os

from PyInstaller.utils.hooks import collect_all, collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [(os.path.join(ROOT, "lossy_detect", "ui"), "lossy_detect/ui")]
binaries = []
hiddenimports = ["imageio_ffmpeg.binaries", "clr_loader", "webview.platforms.winforms"]

# The bundled ffmpeg.exe (decodes M4A/AAC/ALAC/Opus/WMA) lives in imageio_ffmpeg/binaries
datas += collect_data_files("imageio_ffmpeg", subdir="binaries", include_py_files=True)
# pywebview's JS bridge files and .NET (pythonnet) assemblies for the WebView2 window
for pkg in ("webview", "pythonnet", "clr_loader"):
    d, b, h = collect_all(pkg)
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
              "numpy.tests", "scipy.tests"],
    noarchive=False,
)
pyz = PYZ(a.pure)

# Shown the moment the exe is double-clicked, while the one-file bundle unpacks (~10 s);
# lossy_detect.app closes it once the main window has loaded.
splash = Splash(
    os.path.join(SPECPATH, "splash.png"),
    binaries=a.binaries,
    datas=a.datas,
    text_pos=None,  # no per-file extraction messages; "Loading..." is part of the image
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    splash,
    splash.binaries,
    a.binaries,
    a.datas,
    [],
    name="CrapSniffer",
    icon=os.path.join(SPECPATH, "crapsniffer.ico"),
    console=False,          # windowed app: no console window
    upx=False,              # UPX-packed exes trigger more antivirus false positives
    runtime_tmpdir=None,
    debug=False,
    strip=False,
)
