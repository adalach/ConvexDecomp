/* Search explorer in the browser: Pyodide runs explorer_core.py, the module the server runs.

A module worker (Pyodide 314 no longer runs in classic workers). The page (app.js) sends
{id, kind, payload}; kind "boot" first, with the Pyodide address, the zip of the Python files and
the budget's caps, then "engine", "examples" or "run". Each reply is {id, result} or {id, error};
while booting, {progress} lines go to the status bar. */

let core = null;
let ready = null;
let limits = "null";

function progress(text) {
  self.postMessage({ progress: text });
}

async function boot(cfg) {
  progress("Loading Python (the first visit downloads it, later visits use the browser's cache)…");
  const { loadPyodide } = await import(cfg.pyodide + "pyodide.mjs");
  const pyodide = await loadPyodide({ indexURL: cfg.pyodide });
  progress("Loading numpy and shapely…");
  await pyodide.loadPackage(["numpy", "shapely"], { messageCallback: () => {} });
  progress("Loading the engine…");
  const zip = await (await fetch(cfg.bundle)).arrayBuffer();
  pyodide.unpackArchive(zip, "zip", { extractDir: "/explorer" });
  pyodide.runPython([
    "import sys",
    "sys.path.insert(0, '/explorer/tools/search_explorer')",
    "import explorer_core",
    "explorer_core.use_examples('/explorer/examples.json')",
  ].join("\n"));
  core = pyodide.pyimport("explorer_core");
  limits = JSON.stringify(cfg.limits || {});
}

function lastLine(err) {
  // A Python error arrives with its whole traceback; its last line says what went wrong.
  const lines = String((err && err.message) || err).trim().split("\n");
  return lines[lines.length - 1];
}

self.onmessage = async (ev) => {
  const { id, kind, payload, cfg } = ev.data;
  try {
    if (kind === "boot") {
      ready = boot(cfg);
      await ready;
      self.postMessage({ id, result: true });
      return;
    }
    await ready;
    const reply = JSON.parse(core.call_json(kind, JSON.stringify(payload ?? null), limits));
    if (reply && !Array.isArray(reply) && "error" in reply && "status" in reply) {
      self.postMessage({ id, error: `${reply.status} ${reply.error}` });
    } else {
      self.postMessage({ id, result: reply });
    }
  } catch (err) {
    self.postMessage({ id, error: lastLine(err) });
  }
};
