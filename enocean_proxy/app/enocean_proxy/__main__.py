"""Entry point of the app: one proxy per configured gateway (options from the Supervisor)."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
from pathlib import Path
from typing import Any

from .discovery import announce
from .proxy import GatewayProxy, parse_networks

OPTIONS = Path("/data/options.json")
PORTS = range(9100, 9104)  # the ports the app declares (config.yaml)


class ConfigError(ValueError):
    """The app configuration cannot work; the message says why."""


def proxies_from(options: dict[str, Any]) -> list[GatewayProxy]:
    gateways = options.get("gateways", [])
    ports = [gateway["port"] for gateway in gateways]
    if bad := [port for port in ports if port not in PORTS]:
        raise ConfigError(f"port {bad[0]} is not one of the app's ports 9100 to 9103")
    if len(set(ports)) != len(ports):
        raise ConfigError("two gateways use the same port")
    try:
        allowed = parse_networks(options.get("allowed_clients", []))
    except ValueError as err:
        raise ConfigError(f"allowed_clients: {err}") from err
    return [
        GatewayProxy(
            gateway["name"],
            gateway["device"],
            gateway["protocol"],
            gateway["port"],
            baudrate=gateway.get("baudrate", 57600),
            send_gap=options.get("send_gap_ms", 10) / 1000,
            replay_size=options.get("replay_size", 1000),
            replay_max_age=options.get("replay_max_age_s", 900),
            allowed=allowed,
        )
        for gateway in gateways
    ]


async def main(path: Path = OPTIONS) -> int:
    options = json.loads(path.read_text(encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, str(options.get("log_level", "info")).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        proxies = proxies_from(options)
    except ConfigError as err:
        logging.error("Configuration: %s", err)
        return 1
    if not proxies:
        logging.error("No gateway configured: add one under gateways in the app configuration")
        return 1
    for proxy in proxies:
        await proxy.start()
    await announce(proxies)
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
