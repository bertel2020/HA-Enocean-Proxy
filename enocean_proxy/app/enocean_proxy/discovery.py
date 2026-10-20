"""Tell Home Assistant about the proxied gateways (Supervisor discovery).

Each gateway is announced as service "enocean_eltako" with the app's host name, its port and
protocol; the EnOcean Eltako integration then offers it as a discovered gateway, with the
replay already on. Outside the Supervisor (no token) nothing happens.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.request
from typing import Any

from .proxy import GatewayProxy
from .web import STATUS_PORT

_LOGGER = logging.getLogger(__name__)

SUPERVISOR = "http://supervisor"
SERVICE = "enocean_eltako"


def _request(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{SUPERVISOR}{path}",
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={
            "Authorization": f"Bearer {os.environ['SUPERVISOR_TOKEN']}",
            "Content-Type": "application/json",
        },
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - fixed host
        result: dict[str, Any] = json.loads(response.read())
        return result


def discovery_configs(hostname: str, proxies: list[GatewayProxy]) -> list[dict[str, Any]]:
    return [
        {
            "host": hostname,
            "port": proxy.port,
            "name": proxy.name,
            "protocol": proxy.protocol,
            "replay": True,
            "status_port": STATUS_PORT,
        }
        for proxy in proxies
    ]


async def announce(proxies: list[GatewayProxy]) -> None:
    if "SUPERVISOR_TOKEN" not in os.environ:
        return
    try:
        info = await asyncio.to_thread(_request, "/addons/self/info")
        hostname = info["data"]["hostname"]
        for config in discovery_configs(hostname, proxies):
            await asyncio.to_thread(_request, "/discovery", {"service": SERVICE, "config": config})
            _LOGGER.info(
                "Announced %s to Home Assistant (%s:%s)", config["name"], hostname, config["port"]
            )
    except (OSError, KeyError, ValueError) as err:
        _LOGGER.warning("Could not announce the gateways to Home Assistant: %s", err)
