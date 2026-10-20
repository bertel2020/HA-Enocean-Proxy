"""The connection to the real gateway: a serial port or a TCP host (ser2net, MGW-ETH)."""

from __future__ import annotations

import asyncio


async def open_upstream(
    device: str, baudrate: int
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """ "tcp://host:port" opens TCP, anything else is a serial port path."""
    if device.startswith("tcp://"):
        host, _, port = device.removeprefix("tcp://").rpartition(":")
        return await asyncio.open_connection(host, int(port))
    import serial_asyncio_fast  # only needed for serial ports (installed in the app image)

    streams: tuple[
        asyncio.StreamReader, asyncio.StreamWriter
    ] = await serial_asyncio_fast.open_serial_connection(url=device, baudrate=baudrate)
    return streams
