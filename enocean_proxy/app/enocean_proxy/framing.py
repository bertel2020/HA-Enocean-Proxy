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
