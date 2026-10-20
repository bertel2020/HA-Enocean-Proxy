"""Docker HEALTHCHECK: every configured gateway port accepts connections."""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

OPTIONS = Path("/data/options.json")


def healthy(path: Path = OPTIONS) -> bool:
    try:
        gateways = json.loads(path.read_text(encoding="utf-8")).get("gateways", [])
    except (OSError, ValueError):
        return False
    for gateway in gateways:
        try:
            socket.create_connection(("127.0.0.1", int(gateway["port"])), timeout=3).close()
        except OSError:
            return False
    return bool(gateways)


if __name__ == "__main__":
    sys.exit(0 if healthy() else 1)
