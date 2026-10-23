"""CrapSniffer desktop app: drag in audio files (or folders), click Analyze, get quality scores.

The window is an HTML page (ui/index.html) hosted by pywebview in a native window
(Edge WebView2 on Windows). The page calls the Api methods below through
window.pywebview.api; dropped files are resolved to full paths on the Python side.
The page runs a queue, calling analyze() once per file.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import tempfile
import traceback

import webview
from webview.dom import DOMEventHandler

from . import audio_io
from .analyze import analyse_file, status_of
from .cli import summary_row, to_dict, write_csv
from .tags import read_tags

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui", "index.html")


def _file_info(path: str) -> dict:
    ext = os.path.splitext(path)[1].lower()
    try:
        size = os.path.getsize(audio_io.long_path(path))
    except OSError:
        size = None  # unreadable: still listed, and analysis reports the actual error
    return {
        "path": path,
        "name": os.path.basename(path),
        "size": size,
        "supported": ext in audio_io.AUDIO_EXTENSIONS,
    }


def _expand(paths: list[str]) -> list[dict]:
    """File infos for the given paths; folders contribute every audio file inside them."""
    out = []
    for p in paths:
        lp = audio_io.long_path(p)
        if os.path.isdir(lp):
            for root, dirs, names in os.walk(p):
                dirs.sort()
                out += [_file_info(os.path.join(root, n)) for n in sorted(names)
                        if os.path.splitext(n)[1].lower() in audio_io.AUDIO_EXTENSIONS]
        elif os.path.isfile(lp):
            out.append(_file_info(p))
    return out


class Api:
    def __init__(self):
        self._window: webview.Window | None = None  # private: pywebview exposes public attributes to JS
        # Plots are ~1 MB each, so they wait on disk until a file's details are opened
        self._plots = tempfile.TemporaryDirectory(prefix="crapsniffer-")
        self._rows: dict[str, dict] = {}

    def browse(self):
        types = ("Audio files (" + ";".join("*" + e for e in sorted(audio_io.AUDIO_EXTENSIONS)) + ")", "All files (*.*)")
        picked = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True, file_types=types)
        return _expand(list(picked or []))

    def browse_folder(self):
        picked = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        return _expand(list(picked or []))

    def analyze(self, path: str, index: int = 0):
        def progress(stage: str, done: float):
            self._window.evaluate_js(f"window.onProgress({index}, {json.dumps(stage)}, {done})")

        try:
            r = analyse_file(path, progress=progress)
            progress("Drawing the spectrum plot", 0.98)
            from .plot import save_plot
            save_plot(r, os.path.join(self._plots.name, f"{index}.png"))
            kind, label = status_of(r)
            row = summary_row(r)
            self._rows[path] = row
            result = to_dict(r)
            result.update(
                ok=True, index=index, name=os.path.basename(path), kind=kind, status=label,
                peak_dbfs=round(r.samples.peak_dbfs, 1), summary=row, tags=read_tags(path),
            )
            return result
        except Exception as e:
            traceback.print_exc()
            return {"ok": False, "index": index, "error": f"{type(e).__name__}: {e}"}

    def plot(self, index: int):
        png = os.path.join(self._plots.name, f"{index}.png")
        if not os.path.exists(png):
            return None
        with open(png, "rb") as fh:
            return base64.b64encode(fh.read()).decode("ascii")

    def export_csv(self, paths: list[str]):
        rows = [self._rows[p] for p in paths if p in self._rows]
        if not rows:
            return None
        picked = self._window.create_file_dialog(
            webview.FileDialog.SAVE, save_filename="crapsniffer-results.csv", file_types=("CSV files (*.csv)",))
        if not picked:
            return None
        out = picked if isinstance(picked, str) else picked[0]
        write_csv(rows, out)
        return out

    def reveal(self, path: str):
        """Show a file in Explorer / Finder."""
        import subprocess
        if os.name == "nt" and os.path.exists(path):
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        elif sys.platform == "darwin" and os.path.exists(path):
            subprocess.Popen(["open", "-R", path])


def _self_test(out_path: str, files: list[str]) -> None:
    """Hidden `--self-test OUT FILE...`: analyse, plot and tag-read each file, write a JSON summary.

    Used to check that a packaged build carries everything (ffmpeg, libsndfile, fonts...);
    the windowed exe has no console, so results go to a file.
    """
    report = {"python": sys.version.split()[0], "frozen": getattr(sys, "frozen", False), "ui_found": os.path.exists(UI),
              "files": []}
    for path in files:
        entry = {"file": path}
        try:
            from .plot import save_plot
            r = analyse_file(path)
            with tempfile.TemporaryDirectory() as tmp:
                entry["plot_bytes"] = os.path.getsize(save_plot(r, os.path.join(tmp, "p.png")))
            entry.update(quality=r.quality, status=status_of(r)[1], grid=r.grids[0].label if r.grids else None,
                         tags={k: v for k, v in read_tags(path).items() if k != "cover"},
                         has_cover="cover" in read_tags(path))
        except Exception as e:
            entry["error"] = f"{type(e).__name__}: {e}"
        report["files"].append(entry)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)


def _fatal(message: str) -> None:
    print(message, file=sys.stderr)
    if os.name == "nt":
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, "CrapSniffer", 0x10)  # MB_ICONERROR
    elif sys.platform == "darwin":
        import subprocess
        script = 'on run argv\ndisplay alert "CrapSniffer" message (item 1 of argv) as critical\nend run'
        subprocess.run(["osascript", "-e", script, message], check=False)


def _close_splash() -> None:
    """Close the packaged exe's loading splash (no-op when not running from the exe)."""
    try:
        import pyi_splash
        pyi_splash.close()
    except Exception:
        pass


def main() -> None:
    _close_splash() if len(sys.argv) >= 2 and sys.argv[1] == "--self-test" else None
    if len(sys.argv) >= 3 and sys.argv[1] == "--self-test":
        _self_test(sys.argv[2], sys.argv[3:])
        return
    api = Api()
    window = webview.create_window(
        "CrapSniffer", UI, js_api=api,
        width=1040, height=880, min_size=(640, 640), background_color="#0f1115",
    )
    api._window = window

    def on_drop(event):
        files = event.get("dataTransfer", {}).get("files", [])
        paths = [f["pywebviewFullPath"] for f in files if f.get("pywebviewFullPath")]
        if paths:
            window.evaluate_js(f"window.onFilesAdded({json.dumps(_expand(paths))})")

    def on_loaded():
        _close_splash()
        # Python-side drop handler: only it receives the dropped files' full paths
        window.dom.document.events.drop += DOMEventHandler(on_drop, prevent_default=True, stop_propagation=True)

    window.events.loaded += on_loaded
    try:
        webview.start()
    except Exception as e:
        traceback.print_exc()
        if os.name == "nt":
            hint = ("It needs the Microsoft Edge WebView2 Runtime, which comes with Windows 11 and most "
                    "Windows 10 PCs. Install it from:\n"
                    "https://developer.microsoft.com/microsoft-edge/webview2/\n"
                    "(\"Evergreen Bootstrapper\"), then start CrapSniffer again.")
        else:
            hint = "Make sure macOS is up to date, then start CrapSniffer again."
        _fatal(f"CrapSniffer couldn't open its window.\n\n{hint}\n\nDetails: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
