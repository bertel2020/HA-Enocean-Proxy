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


BASE_ID_READ = bytes.fromhex("A55AAB5800000000000000000003")  # TCT, ORG 0x58
BASE_ID_ANSWER = bytes.fromhex("A55A8B98FFB7448000000000000A")  # RMT, ORG 0x98
BUS_POLL_ANSWER = bytes.fromhex("A55A8BF800000000000000000000")  # RMT, ORG 0xF8: FAM14 bus


async def test_answers_go_to_the_asking_client(setup) -> None:
    gateway, proxy = setup
    (r1, w1), (r2, w2) = await _connect(proxy), await _connect(proxy)
    await _until(lambda: proxy.clients == 2)
    w1.write(BASE_ID_READ)
    await _until(lambda: bytes(gateway.received) == BASE_ID_READ)
    gateway.send(BASE_ID_ANSWER)
    gateway.send(FRAME_A)
    assert await r1.readexactly(28) == BASE_ID_ANSWER + FRAME_A
    assert await r2.readexactly(14) == FRAME_A  # the other client only sees the telegram
    gateway.send(BASE_ID_ANSWER)  # nobody asked: everyone gets it
    assert await r2.readexactly(14) == BASE_ID_ANSWER
    w1.close()
    w2.close()


async def test_replay_keeps_radio_only_and_drops_old(setup) -> None:
    gateway, proxy = setup
    now = [1000.0]
    proxy._clock = lambda: now[0]
    proxy.replay_max_age = 60
    gateway.send(FRAME_A)  # will be too old
    await _until(lambda: proxy.stats.from_gateway == 1)
    now[0] += 120
    gateway.send(BUS_POLL_ANSWER)  # FAM14 polling: never kept
    gateway.send(FRAME_B)
    await _until(lambda: proxy.stats.from_gateway == 3)
    reader, writer = await _connect(proxy, replay=True)
    assert await reader.readexactly(14) == FRAME_B
    assert proxy.stats.replayed == 1
    writer.close()


async def test_allowed_clients_and_slow_clients(setup, monkeypatch) -> None:
    from enocean_proxy.proxy import parse_networks

    from enocean_proxy import proxy as module

    gateway, proxy = setup
    proxy.allowed = parse_networks(["192.168.1.0/24"])
    reader, writer = await _connect(proxy)
    assert await reader.read() == b""  # 127.0.0.1 is refused
    assert proxy.stats.refused == 1
    writer.close()

    proxy.allowed = parse_networks(["127.0.0.1"])
    monkeypatch.setattr(module, "CLIENT_BUFFER_LIMIT", -1)  # every write counts as too much
    _reader, writer = await _connect(proxy)
    await _until(lambda: proxy.clients == 1)
    gateway.send(FRAME_A)
    await _until(lambda: proxy.clients == 0)
    assert proxy.stats.slow_clients == 1
    writer.close()


def test_configuration_errors() -> None:
    gateway = {"name": "FAM", "device": "/dev/ttyUSB0", "protocol": "esp2", "port": 9100}
    with pytest.raises(app.ConfigError, match="9200"):
        app.proxies_from({"gateways": [{**gateway, "port": 9200}]})
    with pytest.raises(app.ConfigError, match="same port"):
        app.proxies_from({"gateways": [gateway, {**gateway, "name": "B"}]})
    with pytest.raises(app.ConfigError, match="allowed_clients"):
        app.proxies_from({"gateways": [gateway], "allowed_clients": ["not an address"]})


def test_discovery_config_lists_all_gateways() -> None:
    from enocean_proxy.discovery import discovery_config

    proxies = app.proxies_from(
        {
            "gateways": [
                {"name": "FAM", "device": "/dev/x", "protocol": "esp2", "port": 9101},
                {"name": "MGW", "device": "tcp://h:5100", "protocol": "esp3", "port": 9102},
            ]
        }
    )
    assert discovery_config("abc-enocean-proxy", proxies) == {
        "host": "abc-enocean-proxy",
        "status_port": 9199,
        "replay": True,
        "gateways": [
            {"name": "FAM", "port": 9101, "protocol": "esp2"},
            {"name": "MGW", "port": 9102, "protocol": "esp3"},
        ],
    }


async def test_health_check(setup, tmp_path: Path) -> None:
    from enocean_proxy.health import healthy

    _gateway, proxy = setup
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"gateways": [{"port": proxy.port}]}))
    assert await asyncio.to_thread(healthy, options)
    options.write_text(json.dumps({"gateways": [{"port": 1}]}))
    assert not await asyncio.to_thread(healthy, options)
    assert not await asyncio.to_thread(healthy, tmp_path / "missing.json")


async def _get(port: int, path: str) -> tuple[str, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    try:
        response = await reader.read()
    except ConnectionResetError:  # refused peers are cut off at once
        response = b""
    writer.close()
    head, _, body = response.partition(b"\r\n\r\n")
    return head.decode().split("\r\n")[0], body


async def test_status_page(setup) -> None:
    from enocean_proxy.web import StatusServer

    gateway, proxy = setup
    _reader, writer = await _connect(proxy, replay=True)
    await _until(lambda: proxy.clients == 1)
    writer.write(COMMAND)
    gateway.send(FRAME_A)
    await _until(lambda: proxy.stats.to_gateway == 1 and proxy.stats.from_gateway == 1)
    server = StatusServer([proxy], version="0.3.0", host="127.0.0.1", port=0, peers=None)
    await server.start()
    line, body = await _get(server.port, "/api/status")
    assert line == "HTTP/1.1 200 OK"
    status = json.loads(body)
    (gw,) = status["gateways"]
    assert status["version"] == "0.3.0"
    assert gw["connected"] and gw["protocol"] == "esp2"
    assert gw["clients"] == [{"peer": "127.0.0.1", "replay": True}]
    recent = {f["dir"]: f for f in gw["recent"]}  # order of rx and tx is not fixed
    assert (recent["tx"]["kind"], recent["rx"]["kind"]) == ("radio", "radio")
    assert recent["tx"]["peer"] == "127.0.0.1" and recent["rx"]["peer"] is None
    line, body = await _get(server.port, "/")
    assert line == "HTTP/1.1 200 OK" and b"EnOcean Proxy" in body
    assert (await _get(server.port, "/nothing"))[0] == "HTTP/1.1 404 Not Found"
    await server.close()

    only_ingress = StatusServer([proxy], host="127.0.0.1", port=0)  # 172.30.32.2 only
    await only_ingress.start()
    assert await _get(only_ingress.port, "/api/status") == ("", b"")
    await only_ingress.close()
    writer.close()


async def test_replay_with_reception_times(setup) -> None:
    from enocean_proxy.proxy import REPLAY_REQUEST_TS

    gateway, proxy = setup
    gateway.send(FRAME_A)
    await _until(lambda: proxy.stats.from_gateway == 1)
    reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
    writer.write(REPLAY_REQUEST_TS[:6])  # the request may come in pieces
    await asyncio.sleep(0.05)
    writer.write(REPLAY_REQUEST_TS[6:])
    assert await reader.readline() == b"EOPX TS 1\n"
    stamp, frame = (await reader.readline()).decode().split()
    assert bytes.fromhex(frame) == FRAME_A and abs(float(stamp) - __import__("time").time()) < 5
    assert await reader.readline() == b"EOPX END\n"
    gateway.send(FRAME_B)  # then the live binary stream
    assert await reader.readexactly(14) == FRAME_B
    writer.close()


async def test_capture_lock_send_and_gateway_test(setup, tmp_path: Path) -> None:
    from enocean_proxy.capture import CaptureManager
    from enocean_proxy.web import StatusServer

    gateway, proxy = setup
    server = StatusServer(
        [proxy],
        host="127.0.0.1",
        port=0,
        peers=None,
        captures=CaptureManager([proxy], tmp_path),
    )
    await server.start()
    pct14_reader, pct14 = await _connect(proxy)
    _r, home_assistant = await _connect(proxy)
    await _until(lambda: proxy.clients == 2)

    async def post(path: str, body: dict) -> tuple[str, dict]:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        data = json.dumps(body).encode()
        writer.write(
            f"POST {path} HTTP/1.1\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(data)}\r\n\r\n".encode()
            + data
        )
        head, _, payload = (await reader.read()).partition(b"\r\n\r\n")
        writer.close()
        return head.decode().split("\r\n")[0], json.loads(payload)

    line, started = await post("/api/capture/start", {"gateway": "test", "exclusive_peer": "x"})
    assert line == "HTTP/1.1 200 OK"
    assert proxy.exclusive_peer == "x"
    pct14.write(COMMAND)  # not program "x": held back
    await _until(lambda: proxy.stats.locked == 1)
    line, refused = await post("/api/test", {"gateway": "test"})  # no tests meanwhile
    assert line.endswith("400 Bad Request") and "reserved" in refused["error"]
    await post("/api/capture/stop", {})
    assert proxy.exclusive_peer is None

    line, started = await post("/api/capture/start", {"gateway": "test"})
    gateway.send(FRAME_A)
    home_assistant.write(FRAME_B)
    await _until(lambda: bytes(gateway.received).endswith(FRAME_B))
    await asyncio.sleep(0.05)
    line, stopped = await post("/api/capture/stop", {})
    assert stopped["file"] == started["file"]
    lines = (tmp_path / started["file"]).read_text().splitlines()
    assert [(json.loads(x)["dir"], json.loads(x)["hex"]) for x in lines] == [
        ("rx", FRAME_A.hex()),
        ("tx", FRAME_B.hex()),
    ]
    status_line, body = await _get(server.port, f"/api/captures/{started['file']}")
    assert status_line == "HTTP/1.1 200 OK" and body.count(b"\n") == 2
    assert (await _get(server.port, "/api/captures/../../etc/passwd"))[0].endswith(
        "400 Bad Request"
    )
    line, _ = await post(f"/api/captures/{started['file']}/delete", {})
    assert not (tmp_path / started["file"]).exists()

    # gateway test: the fake gateway answers every base ID request
    async def answer_base_id() -> None:
        while True:
            await asyncio.sleep(0.01)
            while BASE_ID_READ in gateway.received:
                index = gateway.received.index(BASE_ID_READ)
                del gateway.received[index : index + 14]
                gateway.send(BASE_ID_ANSWER)

    task = asyncio.create_task(answer_base_id())
    line, result = await post("/api/test", {"gateway": "test", "count": 3})
    assert (result["sent"], result["answered"], result["lost"]) == (3, 3, 0)
    line, sent = await post("/api/send", {"gateway": "test", "hex": BASE_ID_READ.hex()})
    assert sent["answer"] == BASE_ID_ANSWER.hex(" ")
    line, error = await post("/api/send", {"gateway": "test", "hex": "A55A00"})
    assert line.endswith("400 Bad Request") and "frame" in error["error"]
    line, error = await post("/api/test", {"gateway": "test", "count": "many"})
    assert line.endswith("400 Bad Request") and "count" in error["error"]
    line, error = await post("/api/send", [])  # type: ignore[arg-type]
    assert line.endswith("400 Bad Request") and "object" in error["error"]
    task.cancel()
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    form = b"gateway=test&hex=00"
    writer.write(
        b"POST /api/send HTTP/1.1\r\nContent-Type: application/x-www-form-urlencoded\r\n"
        + f"Content-Length: {len(form)}\r\n\r\n".encode()
        + form
    )
    assert (await reader.read()).startswith(b"HTTP/1.1 400")  # no form posts
    writer.close()
    assert pct14_reader is not None
    pct14.close()
    home_assistant.close()
    await server.close()


async def test_duty_cycle_counts_sent_radio_telegrams(setup) -> None:
    _gateway, proxy = setup
    _reader, writer = await _connect(proxy)
    await _until(lambda: proxy.clients == 1)
    for _ in range(3):
        writer.write(COMMAND)  # TRT 1BS... radio telegrams over the air
    writer.write(BASE_ID_READ)  # a gateway command: no air time
    await _until(lambda: proxy.stats.to_gateway == 4)
    duty = proxy.duty_cycle()
    assert duty["telegrams"] == 3
    assert 0 < duty["ms"] < 20 and duty["of_limit"] < 1
    writer.close()


async def test_read_only_status_for_the_integration(setup) -> None:
    from enocean_proxy.web import StatusServer

    _gateway, proxy = setup
    server = StatusServer([proxy], host="127.0.0.1", port=0, peers=None, read_only=True)
    await server.start()
    line, body = await _get(server.port, "/api/status")
    status = json.loads(body)
    assert line == "HTTP/1.1 200 OK"
    assert "recent" not in status["gateways"][0] and "capture" not in status
    assert "duty_cycle" in status["gateways"][0]
    assert (await _get(server.port, "/"))[0] == "HTTP/1.1 404 Not Found"  # no page
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    writer.write(b"POST /api/capture/start HTTP/1.1\r\nContent-Length: 2\r\n\r\n{}")
    assert (await reader.read()).startswith(b"HTTP/1.1 404")  # no actions
    writer.close()
    await server.close()
