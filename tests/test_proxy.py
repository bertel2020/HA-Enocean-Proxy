"""The proxy against a fake gateway reached over TCP (like ser2net)."""

import asyncio
import json
from pathlib import Path

import pytest
from enocean_proxy.proxy import REPLAY_REQUEST, GatewayProxy

from enocean_proxy import __main__ as app

FRAME_A = bytes.fromhex("A55A0B0570000000002163433000")
FRAME_B = bytes.fromhex("A55A0B0550000000002163433000")
COMMAND = bytes.fromhex("A55A6B0501000000FFB744813000")


class FakeGateway:
    def __init__(self) -> None:
        self.received = bytearray()
        self.writers: list[asyncio.StreamWriter] = []
        self.connected = asyncio.Event()
        self.server: asyncio.Server | None = None
        self.port = 0

    async def start(self, port: int = 0) -> "FakeGateway":
        self.server = await asyncio.start_server(self._client, "127.0.0.1", port)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def _client(self, reader, writer) -> None:
        self.writers.append(writer)
        self.connected.set()
        while data := await reader.read(1024):
            self.received += data

    def send(self, frame: bytes) -> None:
        for writer in self.writers:
            writer.write(frame)

    async def stop(self) -> None:
        for writer in self.writers:
            writer.close()
        self.server.close()
        await self.server.wait_closed()
        self.writers.clear()
        self.connected.clear()


async def _until(predicate, timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest.fixture
async def setup():
    gateway = await FakeGateway().start()
    proxy = GatewayProxy(
        "test", f"tcp://127.0.0.1:{gateway.port}", "esp2", 0, host="127.0.0.1", send_gap=0
    )
    await proxy.start()
    await asyncio.wait_for(proxy.connected.wait(), 2)
    yield gateway, proxy
    await proxy.close()
    await gateway.stop()


async def _connect(proxy: GatewayProxy, replay: bool = False):
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    if replay:
        writer.write(REPLAY_REQUEST)
    return reader, writer


async def test_frames_reach_every_client_and_commands_stay_whole(setup) -> None:
    gateway, proxy = setup
    (r1, w1), (r2, w2) = await _connect(proxy), await _connect(proxy)
    await _until(lambda: proxy.clients == 2)
    gateway.send(FRAME_A)
    assert await r1.readexactly(14) == FRAME_A
    assert await r2.readexactly(14) == FRAME_A

    # two clients send halves of their commands interleaved: the gateway gets whole frames
    w1.write(COMMAND[:6])
    w2.write(FRAME_B[:6])
    await asyncio.sleep(0.05)
    w1.write(COMMAND[6:])
    w2.write(FRAME_B[6:])
    await _until(lambda: len(gateway.received) == 28)
    assert {bytes(gateway.received[:14]), bytes(gateway.received[14:])} == {COMMAND, FRAME_B}
    assert proxy.stats.to_gateway == 2
    w1.close()
    w2.close()


async def test_replay_of_telegrams_missed_while_away(setup) -> None:
    gateway, proxy = setup
    gateway.send(FRAME_A)  # nobody listens: kept
    gateway.send(FRAME_B)
    await _until(lambda: proxy.stats.from_gateway == 2)

    plain, plain_writer = await _connect(proxy)  # a plain client does not get the replay
    reader, writer = await _connect(proxy, replay=True)
    assert await reader.readexactly(28) == FRAME_A + FRAME_B
    assert proxy.stats.replayed == 2
    gateway.send(FRAME_A)  # live, also to the plain client
    assert await reader.readexactly(14) == FRAME_A
    assert await plain.readexactly(14) == FRAME_A

    writer.close()  # e.g. Home Assistant restarts
    await _until(lambda: proxy.clients == 1)
    gateway.send(FRAME_B)
    await _until(lambda: proxy.stats.from_gateway == 4)
    reader, writer = await _connect(proxy, replay=True)
    assert await reader.readexactly(14) == FRAME_B
    writer.close()
    plain_writer.close()


async def test_reconnects_to_the_gateway(setup) -> None:
    gateway, proxy = setup
    port = gateway.port
    await gateway.stop()
    await _until(lambda: not proxy.connected.is_set())
    _reader, writer = await _connect(proxy)
    writer.write(COMMAND)  # gateway away: dropped, not queued forever
    await _until(lambda: proxy.stats.dropped == 1)
    restarted = await FakeGateway().start(port)
    await asyncio.wait_for(proxy.connected.wait(), 5)
    writer.write(COMMAND)
    await _until(lambda: bytes(restarted.received) == COMMAND)
    writer.close()
    await restarted.stop()


async def test_main_without_gateways(tmp_path: Path) -> None:
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"gateways": []}))
    assert await app.main(options) == 1
    proxies = app.proxies_from(
        {
            "gateways": [
                {"name": "FAM", "device": "/dev/ttyUSB0", "protocol": "esp2", "port": 9100}
            ],
            "send_gap_ms": 20,
        }
    )
    assert (proxies[0].port, proxies[0].send_gap, proxies[0].baudrate) == (9100, 0.02, 57600)
