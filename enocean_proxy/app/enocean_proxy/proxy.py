"""One proxy per gateway: keeps the gateway connection, serves any number of TCP clients.

Every frame from the gateway goes to every client. Frames from clients are queued and
written one at a time with a short gap, so two programs never interleave on the line.
For a client that only knows ser2net, the proxy is just another raw TCP gateway.

Replay (optional, used by the enocean_eltako integration): a client that sends the line
"EOPX REPLAY" first gets the frames that arrived while no replaying client was connected,
e.g. during a Home Assistant restart, in their original order (up to replay_size).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .framing import Splitter
from .upstream import open_upstream

_LOGGER = logging.getLogger(__name__)

REPLAY_REQUEST = b"EOPX REPLAY\n"
RECONNECT_DELAYS = (1, 2, 5, 10, 30)
Opener = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


@dataclass
class ProxyStats:
    from_gateway: int = 0
    to_gateway: int = 0
    dropped: int = 0  # client frames while the gateway was not connected
    replayed: int = 0


@dataclass
class _Client:
    writer: asyncio.StreamWriter
    splitter: Splitter
    replay: bool = False
    pending: bytearray = field(default_factory=bytearray)
    started: bool = False  # the replay request is only accepted as the very first bytes


class GatewayProxy:
    def __init__(
        self,
        name: str,
        device: str,
        protocol: str,
        port: int,
        *,
        baudrate: int = 57600,
        host: str = "0.0.0.0",  # noqa: S104 - the app's network, mapped by the Supervisor
        send_gap: float = 0.01,
        replay_size: int = 1000,
        opener: Opener = open_upstream,
    ) -> None:
        self.name = name
        self.device = device
        self.protocol = protocol
        self.port = port
        self.baudrate = baudrate
        self.host = host
        self.send_gap = send_gap
        self.stats = ProxyStats()
        self._opener = opener
        self._clients: list[_Client] = []
        self._outgoing: asyncio.Queue[bytes] = asyncio.Queue()
        self._replay: deque[bytes] = deque(maxlen=replay_size)
        self._upstream: asyncio.StreamWriter | None = None
        self._server: asyncio.Server | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self.connected = asyncio.Event()

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._client, self.host, self.port)
        if self.port == 0:  # tests: the system picked the port
            self.port = self._server.sockets[0].getsockname()[1]
        self._tasks = [
            asyncio.create_task(self._upstream_loop()),
            asyncio.create_task(self._writer_loop()),
        ]
        _LOGGER.info("%s: listening on port %s for %s", self.name, self.port, self.device)

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        for client in list(self._clients):
            client.writer.close()
        if self._upstream is not None:
            self._upstream.close()

    @property
    def clients(self) -> int:
        return len(self._clients)

    # gateway side ----------------------------------------------------------------------------

    async def _upstream_loop(self) -> None:
        attempt = 0
        warned = False
        while True:
            try:
                reader, writer = await self._opener(self.device, self.baudrate)
            except OSError as err:
                delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
                attempt += 1
                (_LOGGER.debug if warned else _LOGGER.warning)(
                    "%s: cannot open %s (%s), retrying in %s s", self.name, self.device, err, delay
                )
                warned = True
                await asyncio.sleep(delay)
                continue
            if warned:
                _LOGGER.info("%s: %s connected again", self.name, self.device)
            attempt, warned = 0, False
            self._upstream = writer
            self.connected.set()
            splitter = Splitter(self.protocol)
            try:
                while chunk := await reader.read(1024):
                    for frame in splitter.feed(chunk):
                        self._from_gateway(frame)
            except OSError as err:
                _LOGGER.warning("%s: connection to %s lost: %s", self.name, self.device, err)
                warned = True
            self.connected.clear()
            self._upstream = None
            writer.close()

    def _from_gateway(self, frame: bytes) -> None:
        self.stats.from_gateway += 1
        if not any(c.replay for c in self._clients):
            self._replay.append(frame)  # nobody to replay to yet: keep it
        for client in list(self._clients):
            client.writer.write(frame)

    async def _writer_loop(self) -> None:
        while True:
            frame = await self._outgoing.get()
            if self._upstream is None:
                self.stats.dropped += 1
                continue
            self._upstream.write(frame)
            with contextlib.suppress(OSError):
                await self._upstream.drain()
            self.stats.to_gateway += 1
            await asyncio.sleep(self.send_gap)

    # client side -----------------------------------------------------------------------------

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        client = _Client(writer, Splitter(self.protocol))
        self._clients.append(client)
        peer = writer.get_extra_info("peername")
        _LOGGER.info("%s: client %s connected (%d)", self.name, peer, len(self._clients))
        try:
            while chunk := await reader.read(1024):
                self._from_client(client, chunk)
        except OSError:
            pass
        finally:
            self._clients.remove(client)
            writer.close()
            _LOGGER.info("%s: client %s left (%d)", self.name, peer, len(self._clients))

    def _from_client(self, client: _Client, chunk: bytes) -> None:
        client.pending += chunk
        if not client.started:
            head = bytes(client.pending[: len(REPLAY_REQUEST)])
            if REPLAY_REQUEST.startswith(head) and len(head) < len(REPLAY_REQUEST):
                return  # the request may still be coming
            client.started = True
            if head == REPLAY_REQUEST:
                del client.pending[: len(REPLAY_REQUEST)]
                self._start_replay(client)
        data, client.pending = bytes(client.pending), bytearray()
        for frame in client.splitter.feed(data):
            self._outgoing.put_nowait(frame)

    def _start_replay(self, client: _Client) -> None:
        frames = list(self._replay)
        self._replay.clear()
        client.replay = True
        for frame in frames:
            client.writer.write(frame)
        self.stats.replayed += len(frames)
        _LOGGER.info("%s: replayed %d telegrams", self.name, len(frames))
