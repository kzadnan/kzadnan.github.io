"""Loopback-only status page and reconnect endpoint.

The page is for a person sitting at the machine. Agents use the MCP server.
Command execution is intentionally not exposed over HTTP.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from hpc_login.config import ALLOWED_HTTP_HOST, ConfigError

_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>hpc-login</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 16px/1.45 ui-sans-serif, system-ui, sans-serif; margin: 2rem auto; max-width: 40rem; padding: 0 1rem; }
  h1 { font-size: 1.4rem; margin-bottom: 0.25rem; }
  p { margin: 0.4rem 0; }
  .pill { display: inline-block; padding: 0.15rem 0.6rem; border-radius: 999px; font-weight: 600; }
  .connected { background: #d8f3dc; color: #1b4332; }
  .connecting, .reconnecting { background: #fff3bf; color: #7c4a03; }
  .stopped { background: #f8d7da; color: #842029; }
  button { font: inherit; padding: 0.4rem 0.8rem; margin-top: 0.8rem; }
  dl { display: grid; grid-template-columns: 12rem 1fr; gap: 0.25rem 0.75rem; }
  dt { color: #555; }
  pre { white-space: pre-wrap; word-break: break-word; }
</style>
</head>
<body>
  <h1>hpc-login</h1>
  <p>SSH control master: <span id="state" class="pill">…</span></p>
  <dl>
    <dt>Destination</dt><dd id="destination"></dd>
    <dt>ProxyJump</dt><dd id="proxy"></dd>
    <dt>Keepalive</dt><dd id="keepalive"></dd>
    <dt>Failures</dt><dd id="failures"></dd>
    <dt>Next attempt</dt><dd id="next"></dd>
    <dt>Connected since</dt><dd id="since"></dd>
  </dl>
  <p id="error"></p>
  <button id="reconnect" type="button">Reconnect</button>
  <p>Agents reach this session through the MCP tools, not this page.</p>
<script>
async function refresh() {
  const response = await fetch("/api/status");
  const data = await response.json();
  const state = document.getElementById("state");
  state.textContent = data.state;
  state.className = "pill " + data.state;
  document.getElementById("destination").textContent = data.destination || "";
  document.getElementById("proxy").textContent = data.proxy_jump || "(none)";
  document.getElementById("keepalive").textContent =
    data.server_alive_interval + "s × " + data.server_alive_count_max;
  document.getElementById("failures").textContent = String(data.consecutive_failures);
  document.getElementById("next").textContent =
    data.state === "connected" ? "—" : (data.next_attempt_in_seconds + "s");
  document.getElementById("since").textContent = data.connected_since || "—";
  document.getElementById("error").textContent = data.last_error || "";
}
document.getElementById("reconnect").addEventListener("click", async () => {
  await fetch("/api/reconnect", {
    method: "POST",
    headers: {"Content-Type": "application/json", "X-HPC-Login": "1"},
    body: "{}"
  });
  refresh();
});
refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""


def make_server(host: str, port: int, status_fn, reconnect_fn) -> ThreadingHTTPServer:
    """Bind an HTTP server. ``host`` must be 127.0.0.1."""
    if host != ALLOWED_HTTP_HOST:
        raise ConfigError(
            f"refusing to bind {host!r}; hpc-login only listens on {ALLOWED_HTTP_HOST}"
        )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if not self._host_allowed():
                return
            if self.path.split("?", 1)[0] == "/api/status":
                self._json(200, status_fn())
                return
            if self.path.split("?", 1)[0] in {"/", "/index.html"}:
                body = _PAGE.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            self._json(404, {"error": "not found"})

        def do_POST(self):  # noqa: N802
            if not self._host_allowed():
                return
            if self.path.split("?", 1)[0] != "/api/reconnect":
                self._json(404, {"error": "not found"})
                return
            if self.headers.get("X-HPC-Login") != "1":
                self._json(400, {"error": "missing X-HPC-Login header"})
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length:
                self.rfile.read(min(length, 4096))
            self._json(200, reconnect_fn())

        def log_message(self, fmt, *args):
            return

        def _host_allowed(self) -> bool:
            host_header = (self.headers.get("Host") or "").split(":")[0].strip("[]")
            if host_header not in {ALLOWED_HTTP_HOST, "localhost"}:
                self._json(403, {"error": "host is not loopback"})
                return False
            return True

        def _json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
