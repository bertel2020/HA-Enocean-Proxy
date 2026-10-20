"""Entry point of the app: one proxy per configured gateway (options from the Supervisor)."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from pathlib import Path
from typing import Any

from .proxy import GatewayProxy

OPTIONS = Path("/data/options.json")


def proxies_from(options: dict[str, Any]) -> list[GatewayProxy]:
    return [
        GatewayProxy(
            gateway["name"],
            gateway["device"],
            gateway["protocol"],
            gateway["port"],
            baudrate=gateway.get("baudrate", 57600),
            send_gap=options.get("send_gap_ms", 10) / 1000,
            replay_size=options.get("replay_size", 1000),
        )
        for gateway in options.get("gateways", [])
    ]


async def main(path: Path = OPTIONS) -> int:
    options = json.loads(path.read_text(encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, str(options.get("log_level", "info")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    proxies = proxies_from(options)
    if not proxies:
        logging.error("No gateway configured: add one under gateways in the app configuration")
        return 1
    for proxy in proxies:
        await proxy.start()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    for proxy in proxies:
        await proxy.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
