"""HTTP endpoint the phone talks to. Run as a user service: `python -m fitbuddy_desktop serve`."""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .store import PROTOCOL_VERSION, Store, normalize_token

log = logging.getLogger("fitbuddy-sync")

MAX_BODY = 64 * 1024 * 1024
TAILSCALE_NETS = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))


def client_allowed(address: str, allow_lan: bool) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    if ip.is_loopback or any(ip in net for net in TAILSCALE_NETS):
        return True
    return allow_lan and (ip.is_private or ip.is_link_local)


class SyncHandler(BaseHTTPRequestHandler):
    server_version = "FitBuddyDesktop/1"
    store: Store

    def log_message(self, fmt, *args):
        log.info("%s %s", self.client_address[0], fmt % args)

    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        cfg = self.store.config()
        if not client_allowed(self.client_address[0], cfg.get("allow_lan", False)):
            self._reply(403, {"error": "address not allowed"})
            return False
        header = self.headers.get("Authorization", "")
        supplied = normalize_token(header[7:]) if header.startswith("Bearer ") else ""
        if not supplied or not hmac.compare_digest(supplied, normalize_token(cfg["token"])):
            self._reply(401, {"error": "bad pairing code"})
            return False
        return True

    def do_GET(self):
        if self.path == "/api/v1/health":
            self._reply(200, {"ok": True, "protocol": PROTOCOL_VERSION})
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/v1/sync":
            self._reply(404, {"error": "not found"})
            return
        if not self._authorized():
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY:
            self._reply(413 if length > MAX_BODY else 400, {"error": "bad body size"})
            return
        try:
            request = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._reply(400, {"error": "invalid json"})
            return
        if request.get("protocol") != PROTOCOL_VERSION:
            self._reply(409, {"error": "protocol mismatch", "protocol": PROTOCOL_VERSION})
            return
        snapshot = request.get("snapshot")
        ops, need_snapshot = self.store.apply_sync(
            acked=[str(i) for i in request.get("ackedOps") or []],
            snapshot=snapshot if isinstance(snapshot, dict) else None,
            snapshot_hash=request.get("snapshotHash"),
            app_version=request.get("appVersion"),
        )
        self._reply(200, {"protocol": PROTOCOL_VERSION, "ops": ops, "needSnapshot": need_snapshot})


class DualStackServer(ThreadingHTTPServer):
    address_family = socket.AF_INET6
    daemon_threads = True

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


def make_server(store: Store, port: int | None = None, host: str = "::") -> ThreadingHTTPServer:
    handler = type("Handler", (SyncHandler,), {"store": store})
    return DualStackServer((host, port if port is not None else store.config()["port"]), handler)


def serve(store: Store | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    store = store or Store()
    server = make_server(store)
    log.info("listening on port %s, data in %s", server.server_address[1], store.dir)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
