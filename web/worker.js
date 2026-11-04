// CrapSniffer web: the analysis runs here, in a background thread of the visitor's browser.
// Python (Pyodide) runs the same engine as the desktop app; ffmpeg.wasm is fetched only when
// a format needs it (M4A/AAC/ALAC, Opus, WMA...). Nothing is uploaded anywhere.
//
// Messages in:  {type: "analyze", id, file, path} · {type: "csv", paths}
// Messages out: {type: "progress", id, stage, done} · {type: "result", id, result, plot}
//               {type: "csv", text} · {type: "fatal", error}

const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v314.0.7/full/";
const FFMPEG = "https://cdn.jsdelivr.net/npm/@ffmpeg/core@0.12.10/dist/esm/";
// This is a module worker (see web.js): libraries load with import(), a CORS request like
// fetch(). Classic importScripts() is refused cross-origin by some browsers and embedded views.
const MUTAGEN = "mutagen==1.48.1";
const NEED_FFMPEG = "crapsniffer:ffmpeg-not-loaded";

let py = null;       // Pyodide
let bridge = null;   // the crapsniffer_web Python module
let ffmpeg = null;   // ffmpeg.wasm core, once loaded
let ffmpegLog = [];
let inputFile = null;  // {file, dir} of the file being analysed, mounted in both file systems

// Starts loading as soon as the page opens; a failure here is reported on the first analysis.
const ready = (async () => {
  const {loadPyodide} = await import(PYODIDE + "pyodide.mjs");
  py = await loadPyodide({indexURL: PYODIDE});
  await py.loadPackage(["numpy", "scipy", "matplotlib", "pillow", "soundfile", "micropip"]);
  await py.pyimport("micropip").install(MUTAGEN);
  const zip = await fetch("crapsniffer.zip", {cache: "no-cache"});
  if (!zip.ok) throw new Error("Couldn't download the analysis code (" + zip.status + ")");
  py.unpackArchive(await zip.arrayBuffer(), "zip", {extractDir: "/home/pyodide"});
  bridge = py.pyimport("crapsniffer_web");
  bridge.setup(runFfmpeg);
})();
ready.catch(() => {});  // handled where it's awaited

async function loadFfmpeg() {
  if (ffmpeg) return;
  const {default: createFFmpegCore} = await import(FFMPEG + "ffmpeg-core.js");
  const urls = btoa(JSON.stringify({wasmURL: FFMPEG + "ffmpeg-core.wasm", workerURL: ""}));
  ffmpeg = await createFFmpegCore({mainScriptUrlOrBlob: FFMPEG + "ffmpeg-core.js#" + urls});
  ffmpeg.setLogger(({message}) => ffmpegLog.push(message));
}

function mount(fs, dir, file) {
  fs.mkdirTree(dir);
  fs.mount(fs.filesystems.WORKERFS, {files: [file]}, dir);
}

function unmount(fs, dir) {
  try { fs.unmount(dir); fs.rmdir(dir); } catch (e) { /* already gone */ }
}

// Called synchronously from Python (audio_io.run_ffmpeg). The output file, if any, is copied
// into Python's file system at the same path.
function runFfmpeg(argsProxy, out) {
  if (!ffmpeg) throw new Error(NEED_FFMPEG);
  const args = argsProxy.toJs();
  if (inputFile && !inputFile.inFfmpeg) { mount(ffmpeg.FS, inputFile.dir, inputFile.file); inputFile.inFfmpeg = true; }
  if (out) ffmpeg.FS.mkdirTree(out.slice(0, out.lastIndexOf("/")));
  ffmpegLog = [];
  const code = ffmpeg.exec(...args);
  ffmpeg.reset();
  if (out && code === 0) {
    py.FS.writeFile(out, ffmpeg.FS.readFile(out));
    ffmpeg.FS.unlink(out);
  }
  return [code, ffmpegLog.join("\n")];
}

const ENGINE_STAGES = ["Loading the analysis engine (first time only)", "Loading ffmpeg for this format (first time only)"];

async function analyze({id, file, path}) {
  const progress = (stage, done) => postMessage({type: "progress", id, stage, done});
  if (!bridge) progress(ENGINE_STAGES[0], 0);
  await ready;
  const dir = "/in/" + id;
  inputFile = {file, dir, inFfmpeg: false};
  mount(py.FS, dir, file);
  try {
    for (let attempt = 0; attempt < 2; attempt++) {
      const result = JSON.parse(bridge.analyze(dir + "/" + file.name, path, id, progress));
      if (result.need_ffmpeg && attempt === 0) {
        progress(ENGINE_STAGES[1], 0);
        await loadFfmpeg();
        continue;
      }
      let plot = null;
      if (result.ok) {
        plot = py.FS.readFile("/tmp/plot.png");
      } else if (result.need_ffmpeg) {
        Object.assign(result, {ok: false, error: "This format needs ffmpeg, which couldn't be loaded."});
      }
      postMessage({type: "result", id, result, plot}, plot ? [plot.buffer] : []);
      return;
    }
  } finally {
    unmount(py.FS, dir);
    if (ffmpeg && inputFile.inFfmpeg) unmount(ffmpeg.FS, dir);
    inputFile = null;
  }
}

// One message at a time: analysis is synchronous, so later messages wait their turn.
let chain = Promise.resolve();
onmessage = ({data}) => {
  chain = chain.then(async () => {
    try {
      if (data.type === "analyze") await analyze(data);
      else if (data.type === "csv") { await ready; postMessage({type: "csv", text: bridge.export_csv(JSON.stringify(data.paths)) ?? null}); }
    } catch (e) {
      console.error(e);
      if (data.type === "analyze") postMessage({type: "result", id: data.id, result: {ok: false, index: data.id, error: friendly(e)}});
      else postMessage({type: "fatal", error: friendly(e)});
    }
  });
};

function friendly(e) {
  const msg = String(e && e.message || e);
  if (/fetch|NetworkError|Failed to load/i.test(msg)) return "Couldn't download the analysis engine. Check your internet connection and reload the page.";
  if (/memory|Out of bounds|RangeError/i.test(msg)) return "Your browser ran out of memory on this file. Try a shorter file, or use the desktop app.";
  return msg.split("\n").filter(Boolean).slice(-1)[0] || "Unknown error";
}
