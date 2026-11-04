"""Assemble the CrapSniffer website into one folder (default: build/web).

    python tools/build_web.py [out_dir]

The site is static: the shared page (lossy_detect/ui/index.html) with web/web.js injected,
the background worker, and the analysis engine zipped for Pyodide. Python, numpy, scipy etc.
and ffmpeg.wasm come from the jsDelivr CDN at run time. Standard library only, so GitHub's
Pages workflow can run it without installing anything.
"""

from __future__ import annotations

import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The desktop window (app.py) and CLI entry point aren't needed in the browser
SKIP = {"app.py", "__main__.py"}


def build(out: str) -> str:
    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)

    with open(os.path.join(ROOT, "lossy_detect", "ui", "index.html"), encoding="utf-8") as fh:
        page = fh.read()
    marker = "<script>\n  const $ ="
    assert marker in page.replace("\r\n", "\n"), "index.html changed: update the web.js injection point"
    page = page.replace("\r\n", "\n").replace(marker, '<script src="web.js"></script>\n' + marker, 1)
    page = page.replace("</title>", '</title>\n<link rel="icon" href="icon.png">\n'
                        '<meta name="description" content="Check whether an audio file was ever lossy-compressed. '
                        'Runs in your browser; nothing is uploaded.">', 1)
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)

    for name in ("web.js", "worker.js"):
        shutil.copy(os.path.join(ROOT, "web", name), out)
    shutil.copy(os.path.join(ROOT, "packaging", "crapsniffer.png"), os.path.join(out, "icon.png"))
    open(os.path.join(out, ".nojekyll"), "w").close()  # serve files as-is on GitHub Pages

    with zipfile.ZipFile(os.path.join(out, "crapsniffer.zip"), "w", zipfile.ZIP_DEFLATED) as z:
        for name in sorted(os.listdir(os.path.join(ROOT, "lossy_detect"))):
            if name.endswith(".py") and name not in SKIP:
                z.write(os.path.join(ROOT, "lossy_detect", name), f"lossy_detect/{name}")
        z.write(os.path.join(ROOT, "web", "crapsniffer_web.py"), "crapsniffer_web.py")
    return out


if __name__ == "__main__":
    print("Website written to", build(os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "build", "web"))))
