"""Tell Home Assistant about the proxied gateways (Supervisor discovery).

One message (service "enocean_eltako") lists every gateway with port and protocol, plus the
app's host name and status port; the EnOcean Eltako integration offers each gateway as a
discovered one, with the replay already on. Outside the Supervisor (no token) nothing happens.
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


def discovery_config(hostname: str, proxies: list[GatewayProxy]) -> dict[str, Any]:
    """One message for all gateways: the Supervisor keeps one per app and service, so a
    message per gateway would replace the previous one."""
    return {
        "host": hostname,
        "status_port": STATUS_PORT,
        "replay": True,
        "gateways": [
            {"name": proxy.name, "port": proxy.port, "protocol": proxy.protocol}
            for proxy in proxies
        ],
    }


async def announce(proxies: list[GatewayProxy]) -> None:
    if "SUPERVISOR_TOKEN" not in os.environ:
        return
    try:
        info = await asyncio.to_thread(_request, "/addons/self/info")
        hostname = info["data"]["hostname"]
        config = discovery_config(hostname, proxies)
        await asyncio.to_thread(_request, "/discovery", {"service": SERVICE, "config": config})
        _LOGGER.info("Announced %d gateway(s) to Home Assistant (%s)", len(proxies), hostname)
    except (OSError, KeyError, ValueError) as err:
        _LOGGER.warning("Could not announce the gateways to Home Assistant: %s", err)
