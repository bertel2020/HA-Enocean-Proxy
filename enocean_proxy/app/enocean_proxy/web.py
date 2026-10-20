"""Status page behind Home Assistant's ingress: one HTML page and a small JSON API.

A minimal HTTP/1.1 server on asyncio (no extra dependency). Only the Supervisor's ingress
proxy (172.30.32.2) may connect; the page fetches "api/status" relative to the ingress path
every few seconds. API: GET status, GET captures/<file> (download), POST capture/start,
capture/stop, captures/<file>/delete, send (one raw frame), test (gateway round trips).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Iterable
from importlib import resources
from typing import Any

from .capture import CaptureError, CaptureManager
from .proxy import GatewayProxy

_LOGGER = logging.getLogger(__name__)

INGRESS_PORT = 8099
STATUS_PORT = 9199  # read-only status for the EnOcean Eltako integration (Supervisor network)
INGRESS_PEERS = ("172.30.32.2",)  # the Supervisor's ingress proxy
MAX_REQUEST = 8192
MAX_BODY = 65536


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
        captures: CaptureManager | None = None,
        read_only: bool = False,
    ) -> None:
        self.proxies = proxies
        self.version = version
        self.host = host
        self.port = port
        self.peers = None if peers is None else set(peers)
        self.captures = captures or CaptureManager(proxies)
        self.read_only = read_only  # status only: no page, no frames, no actions
        self.started = time.time()
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._handle, self.host, self.port)
        if self.port == 0:
            self.port = self._server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        self.captures.stop()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def status(self) -> dict[str, Any]:
        if self.read_only:
            gateways = [
                {key: value for key, value in proxy.status().items() if key != "recent"}
                for proxy in self.proxies
            ]
            return {"version": self.version, "now": time.time(), "gateways": gateways}
        return {
            "version": self.version,
            "started": self.started,
            "now": time.time(),
            "gateways": [proxy.status() for proxy in self.proxies],
            "capture": self.captures.status(),
        }

    def _proxy(self, name: Any) -> GatewayProxy:
        for proxy in self.proxies:
            if proxy.name == name:
                return proxy
        raise ApiError(f"unknown gateway {name!r}")

    async def _route(
        self, method: str, path: str, body: bytes, content_type: str = "application/json"
    ) -> Response:
        path = path.strip("/")
        if self.read_only:
            if method == "GET" and path == "api/status":
                return _json(self.status())
            return 404, "text/plain", b"not found", {}
        if path in ("", "index.html"):
            return 200, "text/html; charset=utf-8", page(), {}
        if not path.startswith("api/"):
            return 404, "text/plain", b"not found", {}
        route = path.removeprefix("api/")
        if method == "GET" and route == "status":
            return _json(self.status())
        if method == "GET" and route.startswith("captures/"):
            name = route.removeprefix("captures/")
            file = self.captures.path(name)
            data = await asyncio.to_thread(file.read_bytes)
            disposition = {"Content-Disposition": f'attachment; filename="{name}"'}
            return 200, "application/x-ndjson", data, disposition
        if method != "POST":
            return 405, "text/plain", b"method not allowed", {}
        if not content_type.startswith("application/json"):
            # actions take JSON only: a plain HTML form from another site cannot trigger them
            raise ApiError("actions need Content-Type: application/json")
        try:
            request = json.loads(body or b"{}")
        except ValueError as err:
            raise ApiError("invalid JSON") from err
        if route == "capture/start":
            name = self.captures.start(request.get("gateway"), request.get("exclusive_peer"))
            return _json({"file": name})
        if route == "capture/stop":
            return _json({"file": self.captures.stop()})
        if route.startswith("captures/") and route.endswith("/delete"):
            self.captures.delete(route.removeprefix("captures/").removesuffix("/delete"))
            return _json({"deleted": True})
        if route == "send":
            proxy = self._proxy(request.get("gateway"))
            try:
                frame = bytes.fromhex(str(request.get("hex", "")))
                answer = await proxy.request(frame)
            except (ValueError, ConnectionError) as err:
                raise ApiError(str(err)) from err
            return _json({"sent": True, "answer": answer.hex(" ") if answer else None})
        if route == "test":
            proxy = self._proxy(request.get("gateway"))
            count = max(1, min(100, int(request.get("count", 20))))
            try:
                return _json(await proxy.gateway_test(count))
            except ConnectionError as err:
                raise ApiError(str(err)) from err
        return 404, "text/plain", b"not found", {}

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
            lines = head.decode("latin-1").split("\r\n")
            method, _, rest = lines[0].partition(" ")
            path = rest.partition(" ")[0].partition("?")[0]
            headers = {
                k.strip().lower(): v.strip() for k, _, v in (h.partition(":") for h in lines[1:])
            }
            length = min(int(headers.get("content-length", "0") or 0), MAX_BODY)
            body = await asyncio.wait_for(reader.readexactly(length), 10) if length else b""
            try:
                status, content_type, data, extra = await self._route(
                    method, path, body, headers.get("content-type", "")
                )
            except (ApiError, CaptureError) as err:
                status, content_type, data, extra = (
                    400,
                    "application/json",
                    json.dumps({"error": str(err)}).encode(),
                    {},
                )
            self._respond(writer, status, content_type, data, extra, head_only=method == "HEAD")
            await writer.drain()
        except (OSError, ValueError, asyncio.IncompleteReadError, TimeoutError):
            pass
        finally:
            writer.close()

    @staticmethod
    def _respond(
        writer: asyncio.StreamWriter,
        status: int,
        content_type: str,
        body: bytes,
        extra: dict[str, str],
        head_only: bool = False,
    ) -> None:
        reasons = {200: "OK", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed"}
        headers = (
            f"HTTP/1.1 {status} {reasons[status]}\r\n"
            f"Content-Type: {content_type}\r\n"
            f"Content-Length: {len(body)}\r\n"
            + "".join(f"{key}: {value}\r\n" for key, value in extra.items())
            + "Cache-Control: no-store\r\nConnection: close\r\n\r\n"
        )
        writer.write(headers.encode() + (b"" if head_only else body))


class ApiError(ValueError):
    """A request the API rejects; answered with 400 and the message."""


Response = tuple[int, str, bytes, dict[str, str]]


def _json(data: Any) -> Response:
    return 200, "application/json", json.dumps(data).encode(), {}
