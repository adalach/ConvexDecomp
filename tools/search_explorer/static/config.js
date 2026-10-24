/* Where the page runs its searches. Here: the Python server (app.py). The static build
(build_static.py) replaces this file to run them in the browser with Pyodide instead. */
window.EXPLORER_CONFIG = { backend: "server" };
