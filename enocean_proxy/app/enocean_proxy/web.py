"""Status page behind Home Assistant's ingress: one HTML page and a JSON endpoint.

A minimal HTTP/1.1 server on asyncio (no extra dependency). Only the Supervisor's ingress
proxy (172.30.32.2) may connect; the page itself fetches "api/status" relative to the
ingress path every few seconds.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Iterable
from importlib import resources
from typing import Any

from .proxy import GatewayProxy

_LOGGER = logging.getLogger(__name__)

INGRESS_PORT = 8099
INGRESS_PEERS = ("172.30.32.2",)  # the Supervisor's ingress proxy
MAX_REQUEST = 8192


def page() -> bytes:
    return resources.files(__package__).joinpath("status.html").read_bytes()


class StatusServer:
    def __init__(
        self,
        proxies: list[GatewayProxy],
        *,
        version: str = "",
        host: str = "0.0.0.0",  # noqa: S104 - reachable for the ingress proxy only (checked)
        port: int = INGRESS_PORT,
        peers: Iterable[str] | None = INGRESS_PEERS,
    ) -> None:
        self.proxies = proxies
        self.version = version
        self.host = host
        self.port = port
        self.peers = None if peers is None else set(peers)
        self.started = time.time()
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        if self.port == 0:
            self.port = self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def status(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "started": self.started,
            "now": time.time(),
            "gateways": [proxy.status() for proxy in self.proxies],
        }

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = (writer.get_extra_info("peername") or ("?",))[0]
        try:
            if self.peers is not None and peer not in self.peers:
                return  # not the ingress proxy: close without an answer
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError):
                return
            if len(head) > MAX_REQUEST:
                return
            method, _, rest = head.decode("latin-1").partition(" ")
            path = rest.partition(" ")[0].partition("?")[0]
            if method not in ("GET", "HEAD"):
                self._respond(writer, 405, "text/plain", b"method not allowed")
            elif path.endswith("/api/status") or path == "api/status":
                body = json.dumps(self.status()).encode()
                self._respond(writer, 200, "application/json", body, head_only=method == "HEAD")
            elif path in ("", "/") or path.endswith("/index.html"):
                self._respond(writer, 200, "text/html; charset=utf-8", page(), method == "HEAD")
            else:
                self._respond(writer, 404, "text/plain", b"not found")
            await writer.drain()
        except OSError:
            pass
        finally:
            writer.close()

    @staticmethod
    def _respond(
        writer: asyncio.StreamWriter,
        status: int,
        content_type: str,
        body: bytes,
        head_only: bool = False,
    ) -> None:
        reason = {200: "OK", 404: "Not Found", 405: "Method Not Allowed"}[status]
        headers = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n"
        )
        writer.write(headers.encode() + (b"" if head_only else body))
