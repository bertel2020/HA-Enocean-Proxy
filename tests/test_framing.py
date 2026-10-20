"""Frames are cut at ESP2/ESP3 boundaries, whatever the chunks look like."""

import pytest
from enocean_proxy.framing import Splitter, crc8

ESP2 = bytes.fromhex("A55A0B0570000000002163433000")


def esp3(data: bytes, optional: bytes = b"", packet_type: int = 1) -> bytes:
    header = len(data).to_bytes(2, "big") + bytes([len(optional), packet_type])
    return (
        b"\x55" + header + bytes([crc8(header)]) + data + optional + bytes([crc8(data + optional)])
    )


def test_esp2_frames_from_junk_and_pieces() -> None:
    splitter = Splitter("esp2")
    assert splitter.feed(b"\x00\x13" + ESP2[:5]) == []
    assert splitter.feed(ESP2[5:] + ESP2 + ESP2[:1]) == [ESP2, ESP2]
    assert splitter.feed(ESP2[1:]) == [ESP2]


def test_esp3_frames_and_a_sync_byte_inside_data() -> None:
    first = esp3(bytes.fromhex("F6500021634330"), bytes.fromhex("01FFFFFFFF3700"))
    second = esp3(b"\x55\x55\x55")  # sync bytes inside the data
    splitter = Splitter("esp3")
    assert splitter.feed(b"\x55\x00" + first[:4]) == []  # a false sync before the frame
    assert splitter.feed(first[4:] + second) == [first, second]


def test_crc8_and_unknown_protocol() -> None:
    assert crc8(bytes.fromhex("00070701")) == 0x7A  # ESP3 header of a radio telegram
    with pytest.raises(ValueError):
        Splitter("esp4")
