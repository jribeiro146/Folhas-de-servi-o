"""Loopback-only PWA release-transition fixture. No application, config or data imports."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import mimetypes
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "src" / "web" / "static"

class FixtureHandler(BaseHTTPRequestHandler):
    release = "a" * 64

    def do_POST(self):
        # Only a synthetic switch; no filesystem, provider, queue or document writes.
        if self.path != "/__test/release/b":
            self.send_error(404)
            return
        type(self).release = "b" * 64
        self.reply(b"synthetic release B", "text/plain")

    def do_GET(self):
        url = urlsplit(self.path)
        identity = type(self).release
        version = identity[:16]
        if url.path == "/service-worker.js":
            text = (STATIC / "service-worker.js").read_text(encoding="utf-8")
            self.reply(text.replace("__SENSORPOINT_RELEASE_SHA256__", identity).encode(), "application/javascript")
            return
        if url.path in {"/", "/document"}:
            page = ('<!doctype html><html lang="pt"><meta charset="utf-8">'
                    '<title>Ensaio sintético PWA</title>'
                    f'<link rel="stylesheet" href="/static/css/field-app.css?v={version}">'
                    '<main class="workspace"><h1>Ensaio sintético PWA</h1>'
                    '<label for="notes">Trabalho por guardar</label><textarea id="notes"></textarea>'
                    f'<p>Versão sintética {identity[0].upper()}</p></main>'
                    f'<script src="/static/js/pwa.js?v={version}"></script></html>')
            self.reply(page.encode(), "text/html; charset=utf-8")
            return
        if url.path.startswith("/api/"):
            self.reply(b'{"synthetic":true}', "application/json")
            return
        if url.path == "/manifest.webmanifest":
            target = STATIC / "manifest.webmanifest"
        elif url.path.startswith("/static/"):
            target = (STATIC / url.path.removeprefix("/static/")).resolve()
            if not target.is_relative_to(STATIC.resolve()):
                self.send_error(404)
                return
        else:
            self.send_error(404)
            return
        if "v" in parse_qs(url.query, keep_blank_values=True) and parse_qs(url.query, keep_blank_values=True)["v"] != [version]:
            self.reply(b"synthetic version unavailable", "text/plain", 409)
            return
        if not target.is_file():
            self.send_error(404)
            return
        self.reply(target.read_bytes(), mimetypes.guess_type(str(target))[0] or "application/octet-stream")

    def reply(self, body, mime, status=200):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-App-Version", type(self).release)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    # Prevent accidental outgoing traffic; binding and accepting loopback remain available.
    sys.path.insert(0, str(ROOT))
    from tools.test_runtime import block_outbound_network
    block_outbound_network()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), FixtureHandler)
    print(f"Synthetic PWA fixture: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == "__main__":
    main()

