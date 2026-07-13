"""A tiny local proxy that forwards to an authenticated upstream proxy.

Why this exists
---------------
When the US proxy requires a username/password, Firefox pops up a native
"proxy authentication required" dialog that Selenium cannot fill — so the login
page never loads. Instead of fighting that dialog, we run an **unauthenticated**
proxy on 127.0.0.1 and point Firefox at it. This relay opens a connection to the
real upstream proxy and injects the ``Proxy-Authorization`` header on the way,
so Firefox is never challenged and the page loads straight through the US proxy.

It supports both:
  * HTTPS sites  -> the browser sends ``CONNECT host:port`` (the common path;
    PointClickCare is HTTPS), which we forward to the upstream with auth, then
    tunnel raw bytes both ways.
  * Plain HTTP   -> absolute-URI requests are forwarded with the auth header
    added, then piped.

One relay instance is started per (host, port, user) and reused across launches.
"""
from __future__ import annotations

import base64
import socket
import threading
from socketserver import StreamRequestHandler, ThreadingTCPServer

# Reuse running relays keyed by upstream identity -> local port.
_relays: dict[tuple, int] = {}
_lock = threading.Lock()


def _pipe(a: socket.socket, b: socket.socket) -> None:
    """Copy bytes from a -> b until the source closes."""
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def _make_handler(up_host: str, up_port: int, auth_header: bytes):
    class RelayHandler(StreamRequestHandler):
        # Keep latency low for interactive browsing.
        disable_nagle_algorithm = True

        def handle(self) -> None:  # noqa: D401
            client = self.connection
            try:
                head = self._read_headers()
            except OSError:
                return
            if not head:
                return

            line_end = head.find(b"\r\n")
            request_line = head[:line_end].decode("latin-1", "replace")
            parts = request_line.split(" ")
            if len(parts) < 3:
                return
            method = parts[0].upper()

            try:
                upstream = socket.create_connection((up_host, up_port), timeout=30)
            except OSError:
                client.sendall(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
                return

            # Inject Proxy-Authorization just after the request line.
            injected = (
                head[:line_end]
                + b"\r\n"
                + b"Proxy-Authorization: " + auth_header + b"\r\n"
                + head[line_end + 2 :]
            )
            try:
                upstream.sendall(injected)
            except OSError:
                upstream.close()
                return

            # For CONNECT, relay the upstream's response then tunnel raw bytes.
            # For HTTP, the response also just needs piping back.
            t = threading.Thread(target=_pipe, args=(upstream, client), daemon=True)
            t.start()
            _pipe(client, upstream)
            t.join(timeout=1)
            if method == "CONNECT":
                pass  # tunnel finished

        def _read_headers(self) -> bytes:
            """Read until the end of the HTTP header block (\\r\\n\\r\\n)."""
            buf = b""
            while b"\r\n\r\n" not in buf:
                chunk = self.connection.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > 65536:  # guard against abuse
                    break
            return buf

    return RelayHandler


class _Server(ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def ensure_relay(up_host: str, up_port: int, username: str, password: str) -> int:
    """Start (or reuse) a local relay and return its 127.0.0.1 port."""
    key = (up_host, up_port, username)
    with _lock:
        if key in _relays:
            return _relays[key]
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        auth_header = ("Basic " + token).encode("latin-1")
        handler = _make_handler(up_host, up_port, auth_header)
        server = _Server(("127.0.0.1", 0), handler)
        local_port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        _relays[key] = local_port
        return local_port
