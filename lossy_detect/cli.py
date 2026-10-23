"""Command line: crapsniffer FILE_OR_FOLDER... [--plot] [--json] [--csv FILE]"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import textwrap

from . import audio_io
from .analyze import Report, analyse_file, status_of
from .tags import read_tags


def _collect(paths: list[str]) -> list[str]:
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                files += [os.path.join(root, n) for n in sorted(names)
                          if os.path.splitext(n)[1].lower() in audio_io.AUDIO_EXTENSIONS]
        else:
            files.append(p)
    return files


def _fmt_time(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}:{s:02d}"


def _track_lines(path: str) -> list[str]:
    t = read_tags(path)
    head = " - ".join(x for x in (t.get("artist"), t.get("title")) if x)
    extra = ", ".join(x for x in (t.get("album"), t.get("label"), t.get("year")) if x)
    dj = " / ".join(x for x in (t.get("genre"), t.get("bpm") and t["bpm"] + " BPM", t.get("key")) if x)
    line = head + (f" ({extra})" if extra else "") + (f" [{dj}]" if dj else "")
    return [f"Track:    {line}"] if line.strip() else []


def format_report(r: Report) -> str:
    a, st = r.audio, r.samples
    if st.effective_bits and st.container_bits:
        depth = f"{st.effective_bits}-bit used of {st.container_bits}"
    elif st.is_float:
        depth = f"float ({st.effective_bits}-bit values)" if st.effective_bits else "float"
    else:
        depth = "decoded"
    rate = f", {a.bitrate_kbps:.0f} kbps" if a.is_lossy_container and a.bitrate_kbps else ""
    if a.vbr is not None:
        rate += " VBR" if a.vbr else " CBR"
    if a.encoder:
        rate += f" ({a.encoder})"
    lines = [
        "=" * 78,
        os.path.basename(a.path),
        "=" * 78,
        *_track_lines(a.path),
        f"Format:   {a.container} / {a.codec}{rate}, {a.sample_rate / 1000:g} kHz, {a.channels} ch, "
        f"{_fmt_time(a.duration)}, {depth}, peak {st.peak_dbfs:.1f} dBFS",
        f"Verdict:  {r.verdict}" + ("" if a.is_lossy_container else f"  (score {r.score}/100)"),
    ]
    lines.append(f"Quality:  {r.quality}/100 - {r.quality_label}  (320 kbps MP3 = 100)")
    if r.source_guess and not a.is_lossy_container and r.score >= 15:
        lines.append(f"Likely source: {r.source_guess}")
    lines += ["", "Evidence:"]
    for f in r.findings:
        tag = f"[{f.level.upper()}]"
        pts = f" (+{f.points})" if f.points else ""
        lines.append(f"  {tag:<11}{f.title}{pts}")
        lines += textwrap.wrap(f.text, 76, initial_indent=" " * 13, subsequent_indent=" " * 13)
    if r.upconversion:
        lines += ["", "Upconversion / processing:"]
        for u in r.upconversion:
            lines += textwrap.wrap(u, 76, initial_indent="  - ", subsequent_indent="    ")
    return "\n".join(lines)


def to_dict(r: Report) -> dict:
    a, c = r.audio, r.cutoff
    return {
        "file": a.path,
        "format": {"container": a.container, "codec": a.codec, "sample_rate": a.sample_rate,
                   "channels": a.channels, "duration_s": round(a.duration, 2),
                   "container_bits": a.container_bits, "effective_bits": r.samples.effective_bits,
                   "lossy_container": a.is_lossy_container,
                   "bitrate_kbps": round(a.bitrate_kbps, 1) if a.bitrate_kbps else None,
                   "vbr": a.vbr, "encoder": a.encoder},
        "verdict": r.verdict,
        "score": r.score,
        "quality": r.quality,
        "quality_label": r.quality_label,
        "source_guess": r.source_guess,
        "cutoff": {"found": c.found, "freq_hz": c.freq_hz, "drop_db": round(c.drop_db, 1),
                   "transition_hz": c.transition_hz, "frames_exceeding": c.frames_exceeding},
        "findings": [{"title": f.title, "level": f.level, "points": f.points, "text": f.text} for f in r.findings],
        "grid_scan": [{"codec": g.label, "offset": g.offset, "z": round(g.zscore, 1),
                       "agreeing_stats": g.agreeing_stats, "level": g.level} for g in r.grids],
        "upconversion": r.upconversion,
    }


SUMMARY_FIELDS = ["file", "artist", "title", "album", "label", "quality", "status", "equivalent_to", "format", "bitrate_kbps", "cutoff_khz",
                  "lossy_score", "verdict", "folder"]


def summary_row(r: Report) -> dict:
    """One flat row per file, for the batch table, CSV export and the CLI summary."""
    a, c = r.audio, r.cutoff
    t = read_tags(a.path, cover_px=0)
    return {
        "file": os.path.basename(a.path),
        "artist": t.get("artist", ""),
        "title": t.get("title", ""),
        "album": t.get("album", ""),
        "label": t.get("label", ""),
        "quality": r.quality,
        "status": status_of(r)[1],
        "equivalent_to": r.quality_label,
        "format": a.codec.replace("MPEG_LAYER_III", "MP3") + (" VBR" if a.vbr else " CBR" if a.vbr is False else ""),
        "bitrate_kbps": round(a.bitrate_kbps) if a.is_lossy_container and a.bitrate_kbps else "",
        "cutoff_khz": round(c.freq_hz / 1000, 2) if c.found and c.drop_db >= 15 else "",
        "lossy_score": r.score,
        "verdict": r.verdict,
        "folder": os.path.dirname(os.path.abspath(a.path)),
    }


def write_csv(rows: list[dict], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:  # BOM so Excel reads UTF-8 names
        w = csv.DictWriter(fh, SUMMARY_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def format_summary(rows: list[dict]) -> str:
    width = min(60, max(len(r["file"]) for r in rows))
    lines = ["=" * 78, f"SUMMARY - {len(rows)} files", "=" * 78,
             f"{'Quality':>7}  {'Status':<16}{'File':<{width}}"]
    for r in sorted(rows, key=lambda r: r["quality"]):
        name = r["file"] if len(r["file"]) <= width else r["file"][: width - 3] + "..."
        lines.append(f"{r['quality']:>7}  {r['status']:<16}{name}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="crapsniffer",
        description='CrapSniffer - The ultimate lie detector for your crates. Because fake "320kbps" sound like absolute shit on a real sound system.')
    ap.add_argument("paths", nargs="+", help="audio files or folders")
    ap.add_argument("--plot", action="store_true", help="save a diagnostic PNG for each file")
    ap.add_argument("--out-dir", help="where to put PNGs (default: next to each file)")
    ap.add_argument("--json", action="store_true", help="print machine-readable JSON instead of text")
    ap.add_argument("--fast", action="store_true", help="skip the codec frame-grid scan (the slowest test)")
    ap.add_argument("--csv", metavar="FILE", help="also write a one-row-per-file summary CSV")
    args = ap.parse_args(argv)

    files = _collect(args.paths)
    if not files:
        print("No audio files found.", file=sys.stderr)
        return 2

    results, rows, failed = [], [], 0
    for i, path in enumerate(files, 1):
        if len(files) > 1 and not args.json:
            print(f"[{i}/{len(files)}] {os.path.basename(path)}", file=sys.stderr, flush=True)
        try:
            r = analyse_file(path, grid_scan=not args.fast)
        except Exception as e:  # keep going through a batch
            failed += 1
            print(f"{path}: could not analyse ({e})", file=sys.stderr)
            continue
        rows.append(summary_row(r))
        if args.plot:
            from .plot import save_plot
            out_dir = args.out_dir or os.path.dirname(os.path.abspath(path))
            os.makedirs(out_dir, exist_ok=True)
            png = save_plot(r, os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + ".lossy.png"))
        if args.json:
            d = to_dict(r)
            if args.plot:
                d["plot"] = png
            results.append(d)
        else:
            print(format_report(r))
            if args.plot:
                print(f"\nPlot:     {png}")
            print()
    if args.json:
        print(json.dumps(results if len(results) != 1 else results[0], indent=2))
    elif len(rows) > 1:
        print(format_summary(rows))
    if args.csv and rows:
        write_csv(rows, args.csv)
        print(f"Summary CSV: {args.csv}", file=sys.stderr)
    return 1 if failed else 0
