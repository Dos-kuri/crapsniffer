"""Bridge between the web page's background worker (worker.js) and the analysis engine.

Runs inside Pyodide (Python compiled for the browser). The worker mounts each dropped file
read-only at /in/<id>/<name>, calls analyze(), and gets JSON back; the spectrum plot is left
at PLOT for the worker to pick up. Plays the role app.Api plays in the desktop app.
"""

from __future__ import annotations

import json
import os
import traceback

from lossy_detect import audio_io
from lossy_detect.analyze import analyse_file, status_of
from lossy_detect.cli import summary_row, to_dict, write_csv
from lossy_detect.plot import save_plot
from lossy_detect.tags import read_tags

PLOT = "/tmp/plot.png"
NEED_FFMPEG = "crapsniffer:ffmpeg-not-loaded"  # raised by worker.js when a format needs ffmpeg.wasm

_rows: dict[str, dict] = {}


def setup(js_run_ffmpeg) -> None:
    """Route the engine's ffmpeg calls to ffmpeg.wasm in the worker."""

    def run_ffmpeg(args: list[str]) -> tuple[int, str]:
        out = None if len(args) < 2 or args[-2] == "-i" else args[-1]  # probe ends with "-i <file>"
        code, log = js_run_ffmpeg(args, out).to_py()
        return int(code), str(log)

    audio_io.run_ffmpeg = run_ffmpeg


def analyze(path: str, display_path: str, index: int, progress) -> str:
    """Analyse one file; returns the same JSON the desktop app gives its page."""
    try:
        if os.path.exists(PLOT):
            os.remove(PLOT)
        r = analyse_file(path, progress=lambda stage, done: progress(stage, done))
        progress("Drawing the spectrum plot", 0.98)
        save_plot(r, PLOT)
        kind, label = status_of(r)
        row = summary_row(r)
        row["folder"] = os.path.dirname(display_path)
        _rows[display_path] = row
        result = to_dict(r)
        result["file"] = display_path
        result.update(
            ok=True, index=index, name=os.path.basename(display_path), kind=kind, status=label,
            peak_dbfs=round(r.samples.peak_dbfs, 1), summary=row, tags=read_tags(path),
        )
    except Exception as e:
        if NEED_FFMPEG in str(e):
            return json.dumps({"ok": False, "need_ffmpeg": True})
        traceback.print_exc()
        result = {"ok": False, "index": index, "error": f"{type(e).__name__}: {e}"}
    return json.dumps(result)


def export_csv(paths_json: str) -> str | None:
    rows = [_rows[p] for p in json.loads(paths_json) if p in _rows]
    if not rows:
        return None
    out = "/tmp/crapsniffer-results.csv"
    write_csv(rows, out)
    with open(out, encoding="utf-8-sig") as fh:
        return fh.read()
