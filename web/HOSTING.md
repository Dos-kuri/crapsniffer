# Hosting CrapSniffer on another website

This guide is for adding **CrapSniffer** to another website by serving a copy of its files from that site. It's written for a developer, or an AI coding assistant, working on the other site's code.

## What CrapSniffer is

It's a tool that checks whether an audio file was ever lossy-compressed (MP3, AAC, Vorbis, Opus), even if it's now a WAV, FLAC or AIFF. It also checks whether a lossy file was re-encoded from a lower-bitrate copy. For each file it gives a quality score (0–100, where 320 kbps MP3 = 100), a verdict, the evidence behind it, and a spectrogram plot.

The page is **fully client-side**. A Python analysis engine runs inside the visitor's browser through Pyodide (Python compiled to WebAssembly), in a Web Worker. Audio files are read from the visitor's disk and **never uploaded**. The host site needs no backend, no database and no server-side code, only static file hosting.

- Live reference copy: https://dos-kuri.github.io/crapsniffer/
- Source code: https://github.com/Dos-kuri/crapsniffer (folders `web/`, `lossy_detect/`, `tools/build_web.py`)

## The files

Five files, served together from **one folder**. They load each other by relative URL, so keep them side by side and don't rename them.

| File | What it is |
|---|---|
| `index.html` | The whole user interface: markup, CSS and the main script. |
| `web.js` | Connects the interface to the worker: file picking, drag-and-drop (including folders), progress and CSV download. |
| `worker.js` | A **module** Web Worker that loads Pyodide and runs the analysis. |
| `crapsniffer.zip` | The Python analysis engine (about 30 KB), unpacked inside Pyodide. |
| `icon.png` | Favicon. Optional. |

### Getting them

**Option A (simplest):** download the published build:

```bash
mkdir crapsniffer && cd crapsniffer
for f in index.html web.js worker.js crapsniffer.zip icon.png; do curl -fsSLO "https://dos-kuri.github.io/crapsniffer/$f"; done
```

**Option B:** build from source. This needs only Python 3.10+, with no packages to install:

```bash
git clone https://github.com/Dos-kuri/crapsniffer.git
python crapsniffer/tools/build_web.py path/to/output-folder
```

The build copies `lossy_detect/ui/index.html` and adds `<script src="web.js">` and the favicon link to it. It then copies `web/web.js` and `web/worker.js`, and zips the Python package with `web/crapsniffer_web.py` into `crapsniffer.zip`.

## Where to put them

Put the folder anywhere on the site, for example `public/tools/crapsniffer/`, so it's served at `https://your-site.example/tools/crapsniffer/`. A subfolder is fine. Then link to that URL from the site's navigation.

### Server requirements

- **HTTPS**, or `localhost` while developing. Opening `index.html` straight from disk (`file://`) won't work, because module workers need a real web server.
- **`.js` files must be served with a JavaScript MIME type** (`text/javascript` or `application/javascript`). Browsers refuse a module worker served as `text/plain` or `application/octet-stream`. Almost every host does this by default.
- **No special headers are needed.** It doesn't use SharedArrayBuffer, so COOP/COEP headers aren't required.
- **Frameworks:** in Next.js, Vite, Astro, SvelteKit and similar, put the folder in the static/public directory so the files are served unchanged. Don't import these files through the bundler: `worker.js` loads its libraries from a CDN at run time and must stay a separate file.

### Content-Security-Policy

Only relevant if the site sets one. At run time the page loads code from these origins:

| Origin | What |
|---|---|
| `https://cdn.jsdelivr.net` | Pyodide **314.0.7** (Python runtime, numpy, scipy, matplotlib, Pillow, soundfile) and `@ffmpeg/core` **0.12.10** (only for M4A/AAC/ALAC, Opus, WMA) |
| `https://pypi.org`, `https://files.pythonhosted.org` | The `mutagen` **1.48.1** tag reader, installed by Pyodide's micropip |

A policy that should work is below. It is **untested**, so check it in the browser console:

```
script-src 'self' 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval' https://cdn.jsdelivr.net;
worker-src 'self' blob:;
connect-src 'self' https://cdn.jsdelivr.net https://pypi.org https://files.pythonhosted.org;
img-src 'self' data: blob:;
style-src 'self' 'unsafe-inline';
```

`'unsafe-inline'` is there because `index.html` has inline `<script>` and `<style>` blocks. Moving them into separate files would remove the need for it. Pyodide needs `'wasm-unsafe-eval'`, and may also need `'unsafe-eval'`.

## Matching the site's look

All changes go in `index.html`. Keep every element `id`, because the script relies on them.

- **Colours:** the CSS variables in `:root` at the top of the `<style>` block (`--bg`, `--surface`, `--accent`, `--good`/`--warn`/`--bad` and so on). The page is designed dark-only, with `color-scheme: dark`.
- **Font:** the `font:` rule on `body`.
- **Header:** the `<header>` block (logo, title, tagline). It can be replaced or removed.
- **Site navigation or footer:** add it inside `<body>`. `web.js` appends a small "get the desktop app" footer to `<main>` and a privacy note under the drop zone. To change or remove those, edit the `DOMContentLoaded` handler at the bottom of `web.js`. `<main>` must stay on the page.

**Recommended:** keep CrapSniffer as its own page on the site. If it must sit inside an existing page layout, load it in a same-origin `<iframe src="/tools/crapsniffer/">`. Pasting its HTML into another page risks clashes with that page's CSS, element IDs and global script names (`$`, `api`, `queue`, `jobs` and so on).

## How it works

### Data flow

```
index.html (UI) ──calls──> window.webApi (web.js) ──postMessage──> worker.js (module worker)
                                                                      │  Pyodide + crapsniffer.zip
                                                                      │  ffmpeg.wasm on demand
   window.onProgress(id, stage, done) <── progress messages ──────────┘
```

- The same `index.html` also runs inside the desktop app, where pywebview provides `window.pywebview.api`. On the web, `web.js` provides `window.webApi` with the same methods, and the page uses whichever exists.
- The worker starts loading the engine when the page opens. The first analysis shows "Loading the analysis engine (first time only)". That download is about 40 MB from jsDelivr, and the browser caches it.
- Files are mounted read-only in the worker (Emscripten WORKERFS) and analysed **one at a time**.

### `window.webApi`

Only needed for deeper integration, such as building a different UI on top. Each method returns a Promise.

| Method | Returns |
|---|---|
| `browse()` / `browse_folder()` | Opens a file or folder picker. Returns `[{path, name, size, supported}]`, or `null` if cancelled. |
| `dropped(dataTransfer)` | The same list, for a drop event, with folders walked recursively. Call it synchronously inside the `drop` handler. |
| `analyze(path, id)` | The result object below. Progress arrives through `window.onProgress(id, stage, fraction)`, which the page must define. |
| `plot(id)` | A `blob:` URL of the spectrogram PNG for that job. |
| `export_csv(paths)` | Downloads `crapsniffer-results.csv` covering the analysed paths. |

Main fields of a result from `analyze`. When `ok` is false, there is an `error` string instead.

- `quality` (0–100), `quality_label` (for example "equivalent to an MP3 at 192 kbps CBR or VBR V2")
- `status` ("Looks good", "Reduced quality", "Inconclusive", "Lossy source" or "Transcoded") and `kind` ("good", "warn" or "bad", for colouring)
- `verdict` (one sentence), `score` (0–100 lossy-evidence points), `source_guess`
- `format` {`container`, `codec`, `sample_rate`, `channels`, `duration_s`, `bitrate_kbps`, `vbr`, `lossy_container`, ...}
- `cutoff` {`found`, `freq_hz`, `drop_db`, ...}, `peak_dbfs`
- `findings` [{`title`, `level` (strong/moderate/weak/info/none), `points`, `text`}], `upconversion` [notes], `grid_scan` [...]
- `tags` {`artist`, `title`, `album`, `label`, `year`, `genre`, `bpm`, `key`, `cover` (a JPEG data URL)}, when the file has tags
- `summary`: one flat row, the same as a CSV line

## Limits worth stating on the site

- **Speed:** roughly 2× slower than the desktop app. A typical 5–8 minute track takes about 30 seconds.
- **Memory:** a long WAV can use around 1 GB of browser memory during analysis. Phones may fail on large files.
- **Browsers:** tested in Chromium-based browsers (Chrome, Edge). It's built on standard features (module workers, WebAssembly) that current Firefox and Safari also support, but they haven't been tested.
- **Formats:** WAV, FLAC, AIFF, MP3, OGG, M4A/AAC, ALAC, Opus and WMA. The first M4A, Opus or WMA file also downloads ffmpeg.wasm (about 30 MB).

## Updating

When CrapSniffer changes, download the five files again (Option A) or rebuild (Option B), and replace the folder. `worker.js` always fetches `crapsniffer.zip` fresh (`cache: "no-cache"`). If the host caches `.js` files for a long time, purge the CDN or cache after replacing them, so visitors don't mix an old `web.js` or `worker.js` with a new zip.

## Checking it works

1. Open the hosted page. The browser console should show no errors, apart from harmless ones the site itself causes.
2. Drop in a WAV or FLAC file. Expect "Loading the analysis engine" for about 10 seconds, then the progress stages, then a report with a gauge and a spectrogram.
3. Drop in an M4A file. Expect "Loading ffmpeg for this format" the first time, then a report.
4. Analyse several files and click **Export CSV**. A CSV file should download.
5. If loading fails, check the Network tab for blocked requests to `cdn.jsdelivr.net` or `pypi.org` (usually a CSP or ad-blocker problem) and the MIME type of `worker.js`.
