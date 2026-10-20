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


def test_frame_kinds() -> None:
    from enocean_proxy.framing import expects_response, is_radio, is_response

    radio = esp3(bytes.fromhex("F6500021634330"))
    response = esp3(b"\x00", packet_type=2)
    command = esp3(b"\x08", packet_type=5)  # CO_RD_IDBASE
    assert is_radio("esp3", radio) and not is_response("esp3", radio)
    assert is_response("esp3", response) and not is_radio("esp3", response)
    assert expects_response("esp3", command) and expects_response("esp3", radio)

    bus_actor = bytes.fromhex("A55A8B0570000000000000013000")  # RMT ORG 05: actor answer
    poll = bytes.fromhex("A55AABFC00000000000000010000")  # TCT ORG FC: FAM14 poll
    assert is_radio("esp2", ESP2) and is_radio("esp2", bus_actor)
    assert not is_radio("esp2", poll) and not expects_response("esp2", poll)
    assert not is_response("esp2", bus_actor)


def test_radio_info() -> None:
    from enocean_proxy.framing import radio_info

    assert radio_info("esp2", ESP2) == ("RPS", "00-21-63-43")
    assert radio_info("esp3", esp3(bytes.fromhex("F6500021634330"))) == ("RPS", "00-21-63-43")
    assert radio_info("esp3", esp3(bytes.fromhex("A50000000AFF8000013F"))) == ("4BS", "FF-80-00-01")
    assert radio_info("esp3", esp3(b"\x00", packet_type=2)) is None
    assert radio_info("esp2", bytes.fromhex("A55AABFC00000000000000010000")) is None
