// CrapSniffer web: stands in for the desktop app's Python API (window.pywebview.api), so the
// same page (lossy_detect/ui/index.html) runs in a normal browser. The analysis itself happens
// in worker.js; files are read straight from the visitor's disk and never uploaded.
(() => {
  const AUDIO = new Set([".wav", ".flac", ".aif", ".aiff", ".mp3", ".m4a", ".aac", ".ogg", ".oga",
    ".opus", ".wma", ".alac", ".caf", ".w64", ".rf64", ".wv", ".ape", ".mp2"]);
  const ext = (name) => { const i = name.lastIndexOf("."); return i < 0 ? "" : name.slice(i).toLowerCase(); };

  const files = new Map();     // path shown in the page -> File
  const pending = new Map();   // job id -> resolve(result)
  const plots = new Map();     // job id -> blob: URL of the spectrum plot
  let csvWaiter = null;

  const worker = new Worker("worker.js", {type: "module"});
  worker.onmessage = ({data}) => {
    if (data.type === "progress") window.onProgress && window.onProgress(data.id, data.stage, data.done);
    else if (data.type === "result") {
      if (data.plot) plots.set(data.id, URL.createObjectURL(new Blob([data.plot], {type: "image/png"})));
      const resolve = pending.get(data.id);
      pending.delete(data.id);
      resolve && resolve(data.result);
    } else if (data.type === "csv") { csvWaiter && csvWaiter(data.text); csvWaiter = null; }
    else if (data.type === "fatal") { csvWaiter && csvWaiter(null); csvWaiter = null; alert(data.error); }
  };
  let crashed = null;
  worker.onerror = (e) => {
    crashed = "The analysis engine stopped (" + (e.message || "unknown error") + "). Reload the page to try again.";
    for (const [id, resolve] of pending) resolve({ok: false, index: id, error: crashed});
    pending.clear();
  };

  function info(file, path, fromFolder) {
    if (fromFolder && !AUDIO.has(ext(file.name))) return null;
    files.set(path, file);
    return {path, name: file.name, size: file.size, supported: AUDIO.has(ext(file.name))};
  }

  function pick(folder) {
    return new Promise((resolve) => {
      const input = document.createElement("input");
      input.type = "file";
      input.multiple = true;
      if (folder) input.webkitdirectory = true;
      else input.accept = [...AUDIO].join(",") + ",audio/*";
      input.onchange = () => resolve([...input.files].map((f) => info(f, f.webkitRelativePath || f.name, folder)).filter(Boolean));
      input.oncancel = () => resolve(null);
      input.click();
    });
  }

  // Dropped folders are walked recursively; entries must be taken during the drop event itself.
  function readEntries(dirEntry) {
    const reader = dirEntry.createReader();
    return new Promise((resolve, reject) => {
      const all = [];
      const next = () => reader.readEntries((batch) => batch.length ? (all.push(...batch), next()) : resolve(all), reject);
      next();
    });
  }
  async function walk(entry, out, inFolder) {
    if (entry.isFile) {
      const file = await new Promise((res, rej) => entry.file(res, rej));
      const i = info(file, entry.fullPath.replace(/^\//, ""), inFolder);
      if (i) out.push(i);
    } else if (entry.isDirectory) {
      const children = (await readEntries(entry)).sort((a, b) => a.name.localeCompare(b.name));
      for (const c of children) await walk(c, out, true);
    }
  }

  window.webApi = {
    browse: () => pick(false),
    browse_folder: () => pick(true),

    dropped(dataTransfer) {
      const entries = [...dataTransfer.items].map((it) => it.webkitGetAsEntry && it.webkitGetAsEntry()).filter(Boolean);
      const loose = entries.length ? [] : [...dataTransfer.files];
      return (async () => {
        const out = [];
        for (const e of entries) await walk(e, out, false);
        for (const f of loose) out.push(info(f, f.name, false));
        return out;
      })();
    },

    analyze(path, id) {
      const file = files.get(path);
      if (!file) return Promise.resolve({ok: false, index: id, error: "File not found - add it again."});
      if (crashed) return Promise.resolve({ok: false, index: id, error: crashed});
      return new Promise((resolve) => {
        pending.set(id, resolve);
        worker.postMessage({type: "analyze", id, file, path});
      });
    },

    plot: async (id) => plots.get(id) || null,

    async export_csv(paths) {
      const text = await new Promise((resolve) => { csvWaiter = resolve; worker.postMessage({type: "csv", paths}); });
      if (!text) return null;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(new Blob(["﻿" + text], {type: "text/csv"}));  // BOM so Excel reads UTF-8
      a.download = "crapsniffer-results.csv";
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 10000);
      return "crapsniffer-results.csv to your Downloads";
    },
  };

  // Web-only notes under the drop zone and in the footer
  addEventListener("DOMContentLoaded", () => {
    const formats = document.querySelector("#empty .formats");
    if (formats) formats.insertAdjacentHTML("afterend",
      '<p class="formats web-note">Runs entirely in your browser: your files are never uploaded. The first analysis downloads the engine (about 40&nbsp;MB, cached afterwards).</p>');
    document.querySelector("main").insertAdjacentHTML("beforeend",
      '<footer class="web-foot">Faster, with no download each visit: <a href="https://github.com/Dos-kuri/crapsniffer/releases/latest">get the desktop app</a> for Mac or Windows.</footer>');
  });
})();
