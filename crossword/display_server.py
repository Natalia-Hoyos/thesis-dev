"""Local web display for the crossword game."""

from __future__ import annotations

import json
import logging
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_DEFAULT_PORT = 8765
_BLANK_STATE: dict[str, Any] = {
    "active": False,
    "puzzle_id": None,
    "title": "Crossword",
    "grid": None,
    "progress": {"solved": 0, "total": 0},
    "complete": False,
}

_state_lock = threading.Lock()
_display_state: dict[str, Any] = dict(_BLANK_STATE)
_server: ThreadingHTTPServer | None = None
_server_thread: threading.Thread | None = None
_browser_opened = False
_bound_port = _DEFAULT_PORT


def _html_page() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Crossword</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f4ef;
      --card: #ffffff;
      --text: #1f2933;
      --muted: #5f6c7b;
      --accent: #2f6fed;
      --solved: #1f7a4d;
      --blank: #c7d2e0;
      --block: #1f2933;
      --cell-border: #9aa8b8;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", system-ui, sans-serif;
      background: radial-gradient(circle at top, #fff 0%, var(--bg) 55%);
      color: var(--text);
      display: flex;
      justify-content: center;
      padding: 2rem 1.25rem;
    }
    .panel {
      width: min(900px, 100%);
      background: var(--card);
      border-radius: 20px;
      box-shadow: 0 18px 50px rgba(31, 41, 51, 0.12);
      padding: 2rem 2.25rem;
    }
    h1 {
      margin: 0 0 0.35rem;
      font-size: clamp(2rem, 4vw, 2.75rem);
      letter-spacing: -0.02em;
    }
    .subtitle {
      margin: 0 0 1.5rem;
      color: var(--muted);
      font-size: 1.1rem;
    }
    .progress {
      display: inline-block;
      margin-bottom: 1.5rem;
      padding: 0.45rem 0.9rem;
      border-radius: 999px;
      background: #eef3ff;
      color: var(--accent);
      font-weight: 600;
      font-size: 1rem;
    }
    .grid-wrap {
      display: flex;
      justify-content: center;
      overflow-x: auto;
      padding-bottom: 0.5rem;
    }
    .crossword-grid {
      --cell-size: 3rem;
      display: grid;
      gap: 0;
      border: 3px solid var(--block);
      background: var(--block);
      box-shadow: 0 10px 30px rgba(31, 41, 51, 0.12);
    }
    .cell {
      position: relative;
      width: var(--cell-size);
      height: var(--cell-size);
      background: #fff;
      border: 1px solid var(--cell-border);
    }
    .cell.block {
      background: var(--block);
      border-color: var(--block);
    }
    .cell.current {
      background: #eef3ff;
      box-shadow: inset 0 0 0 3px var(--accent);
      z-index: 1;
    }
    .cell.solved {
      background: #f4fbf7;
    }
    .cell-number {
      position: absolute;
      top: 0.12rem;
      left: 0.18rem;
      font-size: calc(var(--cell-size) * 0.24);
      font-weight: 700;
      color: var(--text);
      line-height: 1;
      pointer-events: none;
    }
    .cell-letter {
      width: 100%;
      height: 100%;
      display: flex;
      align-items: center;
      justify-content: center;
      font-family: "Consolas", "Courier New", monospace;
      font-size: calc(var(--cell-size) * 0.55);
      font-weight: 700;
      color: var(--blank);
      text-transform: uppercase;
    }
    .cell.solved .cell-letter {
      color: var(--solved);
    }
    .idle {
      text-align: center;
      padding: 3rem 1rem;
      color: var(--muted);
      font-size: 1.25rem;
    }
    .complete-banner {
      margin-top: 1.5rem;
      padding: 1rem 1.1rem;
      border-radius: 14px;
      background: #e8f8ef;
      color: var(--solved);
      font-size: 1.1rem;
      font-weight: 600;
      text-align: center;
    }
  </style>
</head>
<body>
  <main class="panel">
    <h1 id="title">Crossword</h1>
    <p class="subtitle" id="subtitle">Waiting for a puzzle to start…</p>
    <div class="progress" id="progress" hidden></div>
    <div class="grid-wrap" id="grid-wrap" hidden>
      <div class="crossword-grid" id="crossword-grid"></div>
    </div>
    <div class="idle" id="idle">Start a crossword with Gladys to see your puzzle here.</div>
    <div class="complete-banner" id="complete" hidden>Puzzle complete!</div>
  </main>
  <script>
    const titleEl = document.getElementById("title");
    const subtitleEl = document.getElementById("subtitle");
    const progressEl = document.getElementById("progress");
    const gridWrapEl = document.getElementById("grid-wrap");
    const gridEl = document.getElementById("crossword-grid");
    const idleEl = document.getElementById("idle");
    const completeEl = document.getElementById("complete");

    function render(state) {
      if (!state.active || !state.grid) {
        titleEl.textContent = "Crossword";
        subtitleEl.textContent = "Waiting for a puzzle to start…";
        progressEl.hidden = true;
        gridWrapEl.hidden = true;
        idleEl.hidden = false;
        gridEl.innerHTML = "";
        completeEl.hidden = true;
        return;
      }

      const grid = state.grid;
      titleEl.textContent = state.title || "Crossword";
      subtitleEl.textContent = "Answer by voice — Gladys will read each clue.";
      progressEl.hidden = false;
      progressEl.textContent = `${state.progress.solved} of ${state.progress.total} solved`;
      idleEl.hidden = true;
      gridWrapEl.hidden = false;
      completeEl.hidden = !state.complete;

      const cellSize = Math.max(26, Math.min(54, Math.floor(720 / grid.cols)));
      gridEl.style.setProperty("--cell-size", `${cellSize}px`);
      gridEl.style.gridTemplateColumns = `repeat(${grid.cols}, var(--cell-size))`;
      gridEl.innerHTML = grid.cells.map((cell) => {
        if (cell.type === "block") {
          return '<div class="cell block"></div>';
        }

        const classes = ["cell"];
        if (cell.current) classes.push("current");
        if (cell.solved) classes.push("solved");
        const numberHtml = cell.number
          ? `<span class="cell-number">${cell.number}</span>`
          : "";
        const letter = cell.display || "";
        return `
          <div class="${classes.join(" ")}">
            ${numberHtml}
            <div class="cell-letter">${letter}</div>
          </div>`;
      }).join("");
    }

    async function poll() {
      try {
        const response = await fetch("/api/state");
        if (response.ok) {
          render(await response.json());
        }
      } catch (err) {
        console.warn("Failed to fetch crossword state", err);
      }
    }

    poll();
    setInterval(poll, 1000);
  </script>
</body>
</html>"""


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("Crossword display: " + format, *args)

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            body = _html_page().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path == "/api/state":
            with _state_lock:
                self._send_json(dict(_display_state))
            return

        self.send_error(404)


def get_url() -> str:
    return f"http://127.0.0.1:{_bound_port}/"


def update_state(state: dict[str, Any]) -> None:
    with _state_lock:
        _display_state.clear()
        _display_state.update(state)


def reset_display() -> None:
    update_state(dict(_BLANK_STATE))


def ensure_running(*, open_browser: bool = True) -> str:
    """Start the local display server and optionally open a browser window once."""
    global _server, _server_thread, _browser_opened, _bound_port

    if _server is not None:
        if open_browser and not _browser_opened:
            webbrowser.open(get_url())
            _browser_opened = True
        return get_url()

    last_error: OSError | None = None
    for port in range(_DEFAULT_PORT, _DEFAULT_PORT + 10):
        try:
            _server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
            _bound_port = port
            break
        except OSError as exc:
            last_error = exc
            _server = None
    if _server is None:
        raise OSError(f"Could not bind crossword display server: {last_error}")

    _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _server_thread.start()
    logger.info("Crossword display server listening on %s", get_url())

    if open_browser and not _browser_opened:
        webbrowser.open(get_url())
        _browser_opened = True

    return get_url()
