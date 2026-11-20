#!/usr/bin/env python3
"""Local relay for OpenCode Zen/Go endpoints.

The OpenCode gateway rejects requests that do not identify the client:
it requires a coding-agent User-Agent and a stable x-opencode-session
header for routing and prompt caching. The OpenAI SDK used by QitOS does
not send either, so this relay forwards /v1/* to the upstream base and
injects both headers. Stdlib only; streaming responses are passed through.

  RELAY_PORT=8899 python3 opencode_relay.py
  BASE_URL=http://host.docker.internal:8899/v1
"""

from __future__ import annotations

import http.client
import os
import ssl
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

UPSTREAM = os.environ.get("OPENCODE_UPSTREAM", "https://opencode.ai/zen/go")
SESSION = os.environ.get("OPENCODE_SESSION") or f"qitos-cybergym-{uuid.uuid4().hex}"
PORT = int(os.environ.get("RELAY_PORT", "8899"))
USER_AGENT = os.environ.get("RELAY_USER_AGENT", "qitos-cybergym-agent/1.0")
UPSTREAM_TIMEOUT = int(os.environ.get("RELAY_UPSTREAM_TIMEOUT", "1800"))

_parts = urlsplit(UPSTREAM)
_UPSCHEME = _parts.scheme
_UPHOST = _parts.hostname or ""
_UPPORT = _parts.port or (443 if _UPSCHEME == "https" else 80)
_UPPATH = _parts.path.rstrip("/")

_SKIP_REQUEST_HEADERS = {
    "host",
    "connection",
    "content-length",
    "transfer-encoding",
    "accept-encoding",
}
_SKIP_RESPONSE_HEADERS = {"transfer-encoding", "connection", "content-length"}


class RelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _forward(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in _SKIP_REQUEST_HEADERS
        }
        headers["Host"] = _UPHOST
        headers["User-Agent"] = USER_AGENT
        headers["x-opencode-session"] = SESSION
        headers["Accept-Encoding"] = "identity"
        if body is not None:
            headers["Content-Length"] = str(len(body))

        if _UPSCHEME == "https":
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(
                _UPHOST, _UPPORT, timeout=UPSTREAM_TIMEOUT,
                context=ssl.create_default_context(),
            )
        else:
            conn = http.client.HTTPConnection(_UPHOST, _UPPORT, timeout=UPSTREAM_TIMEOUT)

        try:
            conn.request(self.command, _UPPATH + self.path, body=body, headers=headers)
            upstream = conn.getresponse()
            self.send_response(upstream.status)
            for key, value in upstream.getheaders():
                if key.lower() not in _SKIP_RESPONSE_HEADERS:
                    self.send_header(key, value)
            self.send_header("Connection", "close")
            self.end_headers()
            read = getattr(upstream, "read1", upstream.read)
            while True:
                chunk = read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except Exception as exc:  # surface upstream failures as 502, never crash
            try:
                self.send_error(502, f"relay upstream error: {exc}")
            except Exception:
                pass
        finally:
            conn.close()

    do_GET = _forward
    do_POST = _forward
    do_PUT = _forward
    do_PATCH = _forward
    do_DELETE = _forward

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("[relay] " + (fmt % args) + "\n")
        sys.stderr.flush()


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", PORT), RelayHandler)
    print(
        f"[relay] listening 0.0.0.0:{PORT} -> {UPSTREAM} "
        f"session={SESSION} ua={USER_AGENT}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
