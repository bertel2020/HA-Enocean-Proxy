"""Split a byte stream into whole ESP2 or ESP3 frames.

The proxy must never interleave two clients' frames on the serial line, and it keeps whole
frames for the replay buffer, so it cuts streams at frame boundaries. It does not interpret
the frames otherwise.

ESP2: sync 0xA5 0x5A, then 12 bytes (header/length, ORG, 4 data, 4 ID, status, checksum).
ESP3: 0x55, data length (2), optional length (1), packet type (1), CRC8 of those four,
data, optional data, CRC8 of data + optional data.
"""

from __future__ import annotations

ESP2_FRAME = 14
ESP3_HEADER = 6  # sync + 4 header bytes + header CRC


def crc8(data: bytes) -> int:
    """CRC8 of ESP3 (polynomial 0x07, initial value 0)."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


class Splitter:
    """Feed bytes, get complete frames; garbage before a sync byte is dropped."""

    def __init__(self, protocol: str) -> None:
        if protocol not in ("esp2", "esp3"):
            raise ValueError(f"unknown protocol {protocol!r}")
        self.protocol = protocol
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[bytes]:
        self._buffer += chunk
        frames: list[bytes] = []
        while True:
            frame = self._next_esp2() if self.protocol == "esp2" else self._next_esp3()
            if frame is None:
                return frames
            frames.append(frame)

    def _next_esp2(self) -> bytes | None:
        start = self._buffer.find(b"\xa5\x5a")
        if start < 0:
            del self._buffer[: max(0, len(self._buffer) - 1)]  # keep a trailing 0xA5
            return None
        del self._buffer[:start]
        if len(self._buffer) < ESP2_FRAME:
            return None
        frame = bytes(self._buffer[:ESP2_FRAME])
        del self._buffer[:ESP2_FRAME]
        return frame

    def _next_esp3(self) -> bytes | None:
        while True:
            start = self._buffer.find(b"\x55")
            if start < 0:
                self._buffer.clear()
                return None
            del self._buffer[:start]
            if len(self._buffer) < ESP3_HEADER:
                return None
            header = bytes(self._buffer[1:5])
            if crc8(header) != self._buffer[5]:
                del self._buffer[:1]  # a 0x55 inside data: look for the next one
                continue
            data_len = int.from_bytes(header[0:2], "big")
            total = ESP3_HEADER + data_len + header[2] + 1
            if len(self._buffer) < total:
                return None
            frame = bytes(self._buffer[:total])
            del self._buffer[:total]
            return frame


# What a frame is, as far as the proxy needs it -------------------------------------------------

ESP2_RRT, ESP2_TRT, ESP2_RMT, ESP2_TCT = 0b000, 0b011, 0b100, 0b101
ESP2_RADIO_ORGS = {0x05, 0x06, 0x07, 0xD2}  # RPS, 1BS, 4BS, VLD (Eltako)
ESP2_BUS_CONTROL = 0xF0  # ORG from here on: FAM14 bus polling and memory access
ESP3_RADIO_TYPES = {0x01, 0x03, 0x09}  # RADIO_ERP1, RADIO_SUB_TEL, RADIO_MESSAGE
ESP3_RESPONSE = 0x02


def _esp2(frame: bytes) -> tuple[int, int]:
    return frame[2] >> 5, frame[3]  # header sequence, ORG


def is_radio(protocol: str, frame: bytes) -> bool:
    """A telegram worth replaying: a radio telegram or a bus actor's state answer, not
    FAM14 polling, empty answers or gateway messages."""
    if protocol == "esp3":
        return frame[4] in ESP3_RADIO_TYPES
    hseq, org = _esp2(frame)
    return hseq in (ESP2_RRT, ESP2_TRT, ESP2_RMT) and org in ESP2_RADIO_ORGS


def is_response(protocol: str, frame: bytes) -> bool:
    """The gateway's answer to a command (OK, error, base ID ...)."""
    if protocol == "esp3":
        return frame[4] == ESP3_RESPONSE
    hseq, org = _esp2(frame)
    return hseq == ESP2_RMT and org not in ESP2_RADIO_ORGS and org < ESP2_BUS_CONTROL


def expects_response(protocol: str, frame: bytes) -> bool:
    """ESP3 answers every packet; ESP2 answers gateway commands (not radio, not bus)."""
    if protocol == "esp3":
        return True
    hseq, org = _esp2(frame)
    return hseq == ESP2_TCT and org < ESP2_BUS_CONTROL


ESP2_ORG_RORG = {0x05: "RPS", 0x06: "1BS", 0x07: "4BS", 0xD2: "VLD"}
ESP3_RORG = {0xF6: "RPS", 0xD5: "1BS", 0xA5: "4BS", 0xD2: "VLD"}


def radio_info(protocol: str, frame: bytes) -> tuple[str, str] | None:
    """Telegram type (RPS, 1BS, 4BS, VLD) and sender ID ("FF-AA-80-01") of a radio frame,
    for the status page; None for anything else."""
    if not is_radio(protocol, frame):
        return None
    if protocol == "esp3":
        length = int.from_bytes(frame[1:3], "big")
        if frame[4] != 0x01 or length < 6 or len(frame) < 6 + length:
            return None  # only RADIO_ERP1: RORG, data, 4 ID bytes, status
        data = frame[6 : 6 + length]
        return ESP3_RORG.get(data[0], f"{data[0]:02X}"), data[-5:-1].hex("-").upper()
    if len(frame) < 14:
        return None
    return ESP2_ORG_RORG.get(frame[3], f"{frame[3]:02X}"), frame[8:12].hex("-").upper()


# Air time (868 MHz duty cycle) ------------------------------------------------------------

BIT_RATE = 125_000  # bit/s of EnOcean radio
SUBTELEGRAMS = 3  # gateways send every telegram three times
RADIO_OVERHEAD = 6  # bytes around the payload on air: preamble, sync, length, CRC (approx.)
ESP2_DATA_BYTES = {0x05: 1, 0x06: 1, 0x07: 4, 0xD2: 4}  # RPS, 1BS, 4BS, VLD (max)


def airtime_ms(protocol: str, frame: bytes) -> float:
    """Approximate time on air of a radio telegram the gateway sends (0 for other frames).

    Payload = RORG + data + sender ID + status, coded 8 to 12 bits per byte on air, sent as
    three subtelegrams. Accurate enough to watch the 1 % duty cycle, not to bill it.
    """
    if protocol == "esp3":
        if frame[4] != 0x01:  # RADIO_ERP1 only
            return 0.0
        payload = int.from_bytes(frame[1:3], "big")
    else:
        if (frame[2] >> 5) != ESP2_TRT or frame[3] not in ESP2_DATA_BYTES:
            return 0.0
        payload = 1 + ESP2_DATA_BYTES[frame[3]] + 4 + 1
    bits = (payload + RADIO_OVERHEAD) * 12
    return SUBTELEGRAMS * bits * 1000 / BIT_RATE


def base_id_request(protocol: str) -> bytes:
    """A harmless command every gateway answers: read the base ID (used by the gateway test)."""
    if protocol == "esp3":
        header = bytes([0x00, 0x01, 0x00, 0x05])  # 1 data byte, COMMON_COMMAND
        return b"\x55" + header + bytes([crc8(header), 0x08, crc8(b"\x08")])
    body = bytes([0xAB, 0x58]) + bytes(9)  # TCT, RD_IDBASE, 4 data, 4 ID, status
    return b"\xa5\x5a" + body + bytes([sum(body) & 0xFF])
