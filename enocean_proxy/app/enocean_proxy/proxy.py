"""One proxy per gateway: keeps the gateway connection, serves any number of TCP clients.

Every radio telegram from the gateway goes to every client. Frames from clients are queued
and written one at a time with a short gap, so two programs never interleave on the line.
The gateway's answer to a command goes back to the client that sent it (ESP3 answers every
packet in order; ESP2 answers gateway commands); unsolicited messages go to everyone.
For a client that only knows ser2net, the proxy is just another raw TCP gateway.

Replay (optional, used by the enocean_eltako integration): a client that sends the line
"EOPX REPLAY" first gets the radio telegrams that arrived while no replaying client was
connected, e.g. during a Home Assistant restart, in their original order. Only radio
telegrams are kept (no FAM14 polling), at most replay_size and none older than
replay_max_age.

Protection: clients can be limited to a list of addresses or networks, and a client that
stops reading is disconnected before its unsent data grows without bound.
"""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from .framing import Splitter, expects_response, is_radio, is_response
from .upstream import open_upstream

_LOGGER = logging.getLogger(__name__)

REPLAY_REQUEST = b"EOPX REPLAY\n"
RECONNECT_DELAYS = (1, 2, 5, 10, 30)
RESPONSE_TIMEOUT = 2.0  # seconds a client waits for the gateway's answer
CLIENT_BUFFER_LIMIT = 256 * 1024  # bytes not yet delivered to a client before it is dropped
RECENT_FRAMES = 200  # shown on the status page
Opener = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]
Network = ipaddress.IPv4Network | ipaddress.IPv6Network


@dataclass
class ProxyStats:
    from_gateway: int = 0
    to_gateway: int = 0
    dropped: int = 0  # client frames while the gateway was not connected
    replayed: int = 0
    refused: int = 0  # clients outside the allowed networks
    slow_clients: int = 0  # clients disconnected because they stopped reading


@dataclass(eq=False)
class _Client:
    writer: asyncio.StreamWriter
    splitter: Splitter
    peer: str
    replay: bool = False
    pending: bytearray = field(default_factory=bytearray)
    started: bool = False  # the replay request is only accepted as the very first bytes


def parse_networks(entries: Iterable[str]) -> list[Network]:
    """ "192.168.1.20", "192.168.1.0/24" ...; raises ValueError for anything else."""
    return [ipaddress.ip_network(entry.strip(), strict=False) for entry in entries if entry.strip()]


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
        replay_max_age: float = 900.0,
        allowed: Iterable[Network] = (),
        opener: Opener = open_upstream,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.name = name
        self.device = device
        self.protocol = protocol
        self.port = port
        self.baudrate = baudrate
        self.host = host
        self.send_gap = send_gap
        self.replay_max_age = replay_max_age
        self.allowed = list(allowed)
        self.stats = ProxyStats()
        self._opener = opener
        self._clock = clock
        self._clients: list[_Client] = []
        self._outgoing: asyncio.Queue[tuple[_Client, bytes]] = asyncio.Queue()
        self._replay: deque[tuple[float, bytes]] = deque(maxlen=replay_size)
        self._awaiting: deque[tuple[_Client, float]] = deque()  # who gets the next answer
        self._upstream: asyncio.StreamWriter | None = None
        self._server: asyncio.Server | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self.connected = asyncio.Event()
        self.connected_since: float | None = None  # time.time()
        self.last_error: str | None = None
        self.recent: deque[dict[str, Any]] = deque(maxlen=RECENT_FRAMES)

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
        for client in list(self._clients):  # before waiting: wait_closed waits for clients
            client.writer.close()
        if self._server is not None:
            await self._server.wait_closed()
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
                self.last_error = str(err)
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
            self.connected_since = time.time()
            self.last_error = None
            splitter = Splitter(self.protocol)
            try:
                while chunk := await reader.read(1024):
                    for frame in splitter.feed(chunk):
                        self._from_gateway(frame)
            except OSError as err:
                _LOGGER.warning("%s: connection to %s lost: %s", self.name, self.device, err)
                self.last_error = str(err)
                warned = True
            self.connected.clear()
            self.connected_since = None
            self._upstream = None
            self._awaiting.clear()
            writer.close()

    def _remember(self, direction: str, frame: bytes, peer: str | None = None) -> None:
        kind = (
            "radio"
            if is_radio(self.protocol, frame)
            else "answer"
            if is_response(self.protocol, frame)
            else "other"
        )
        self.recent.append(
            {
                "time": time.time(),
                "dir": direction,
                "kind": kind,
                "hex": frame.hex(" "),
                "peer": peer,
            }
        )

    def _from_gateway(self, frame: bytes) -> None:
        self.stats.from_gateway += 1
        self._remember("rx", frame)
        if is_response(self.protocol, frame) and (client := self._next_waiting()) is not None:
            self._send(client, frame)  # the answer to this client's command
            return
        if is_radio(self.protocol, frame) and not any(c.replay for c in self._clients):
            self._replay.append((self._clock(), frame))  # nobody to replay to yet: keep it
        for client in list(self._clients):
            self._send(client, frame)

    def _next_waiting(self) -> _Client | None:
        now = self._clock()
        while self._awaiting:
            client, deadline = self._awaiting.popleft()
            if deadline >= now and client in self._clients:
                return client
        return None

    async def _writer_loop(self) -> None:
        while True:
            client, frame = await self._outgoing.get()
            if self._upstream is None:
                self.stats.dropped += 1
                continue
            if expects_response(self.protocol, frame):
                self._awaiting.append((client, self._clock() + RESPONSE_TIMEOUT))
            self._upstream.write(frame)
            self._remember("tx", frame, client.peer)
            with contextlib.suppress(OSError):
                await self._upstream.drain()
            self.stats.to_gateway += 1
            await asyncio.sleep(self.send_gap)

    # client side -----------------------------------------------------------------------------

    def _permitted(self, peer: str) -> bool:
        if not self.allowed:
            return True
        try:
            address = ipaddress.ip_address(peer)
        except ValueError:
            return False
        return any(address in network for network in self.allowed)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peername = writer.get_extra_info("peername")
        peer = str(peername[0]) if peername else "?"
        if not self._permitted(peer):
            self.stats.refused += 1
            _LOGGER.warning("%s: refused client %s (not in allowed_clients)", self.name, peer)
            writer.close()
            return
        client = _Client(writer, Splitter(self.protocol), peer)
        self._clients.append(client)
        _LOGGER.info("%s: client %s connected (%d)", self.name, peer, len(self._clients))
        try:
            while chunk := await reader.read(1024):
                self._from_client(client, chunk)
        except OSError:
            pass
        finally:
            if client in self._clients:
                self._clients.remove(client)
            writer.close()
            _LOGGER.info("%s: client %s left (%d)", self.name, peer, len(self._clients))

    def _send(self, client: _Client, frame: bytes) -> None:
        """Write to a client; one that has stopped reading is dropped, not buffered forever."""
        if client.writer.is_closing():
            return
        client.writer.write(frame)
        transport = client.writer.transport
        if transport.get_write_buffer_size() > CLIENT_BUFFER_LIMIT:
            self.stats.slow_clients += 1
            _LOGGER.warning("%s: client %s stopped reading, disconnected", self.name, client.peer)
            if client in self._clients:
                self._clients.remove(client)
            transport.abort()

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
            self._outgoing.put_nowait((client, frame))

    def _start_replay(self, client: _Client) -> None:
        oldest = self._clock() - self.replay_max_age
        frames = [frame for received, frame in self._replay if received >= oldest]
        self._replay.clear()
        client.replay = True
        for frame in frames:
            self._send(client, frame)
        self.stats.replayed += len(frames)
        _LOGGER.info("%s: replayed %d telegrams", self.name, len(frames))

    def status(self) -> dict[str, Any]:
        """Everything the status page shows about this gateway."""
        now = self._clock()
        replay_ages = [now - received for received, _frame in self._replay]
        return {
            "name": self.name,
            "device": self.device,
            "protocol": self.protocol,
            "baudrate": None if self.device.startswith("tcp://") else self.baudrate,
            "port": self.port,
            "connected": self.connected.is_set(),
            "connected_since": self.connected_since,
            "last_error": self.last_error,
            "clients": [{"peer": c.peer, "replay": c.replay} for c in self._clients],
            "stats": asdict(self.stats),
            "queue": self._outgoing.qsize(),
            "replay": {
                "kept": len(self._replay),
                "size": self._replay.maxlen,
                "oldest_s": round(max(replay_ages), 1) if replay_ages else None,
                "max_age_s": self.replay_max_age,
            },
            "allowed_clients": [str(network) for network in self.allowed],
            "recent": list(self.recent)[-50:],
        }
