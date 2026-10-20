"""Captures: every frame in both directions, written to /share as JSON lines.

Made for recording what PCT14 and FAM14 say to each other: PCT14 reaches the proxy through
a virtual COM port that forwards to TCP; while capturing, the proxy can let only that
program send, so Home Assistant does not disturb the bus memory access. A capture stops by
itself at MAX_BYTES. One line per frame: time, gateway, direction, kind, sending program,
frame as hex.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import IO, Any

from .framing import is_radio, is_response
from .proxy import GatewayProxy

_LOGGER = logging.getLogger(__name__)

CAPTURE_DIR = Path("/share/enocean_proxy")
MAX_BYTES = 50 * 1024 * 1024
NAME = re.compile(r"^capture-[\w.-]+\.jsonl$")


class CaptureError(ValueError):
    """A capture request that cannot be done; the message says why."""


class CaptureManager:
    def __init__(self, proxies: list[GatewayProxy], directory: Path = CAPTURE_DIR) -> None:
        self.proxies = proxies
        self.directory = directory
        self._file: IO[str] | None = None
        self._name: str | None = None
        self._gateway: GatewayProxy | None = None
        self._watched: list[GatewayProxy] = []
        self._started = 0.0
        self._frames = 0
        self._bytes = 0

    @property
    def active(self) -> bool:
        return self._file is not None

    def start(self, gateway: str | None = None, exclusive_peer: str | None = None) -> str:
        """Start recording one gateway (or all); exclusive_peer: only it may send meanwhile."""
        if self.active:
            raise CaptureError("a capture is already running")
        watched = [p for p in self.proxies if gateway in (None, "", p.name)]
        if not watched:
            raise CaptureError(f"unknown gateway {gateway!r}")
        if exclusive_peer and (gateway in (None, "") or len(watched) != 1):
            raise CaptureError("pick one gateway to let a single program send")
        self.directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        label = re.sub(r"[^\w.-]", "_", gateway or "all")
        self._name = f"capture-{stamp}-{label}.jsonl"
        self._file = (self.directory / self._name).open("w", encoding="utf-8")
        self._watched = watched
        self._started = time.time()
        self._frames = self._bytes = 0
        for proxy in watched:
            proxy.observers.append(self._record)
        if exclusive_peer:
            self._gateway = watched[0]
            self._gateway.exclusive_peer = exclusive_peer
        _LOGGER.info(
            "Capture %s started%s",
            self._name,
            f" ({exclusive_peer} sends alone)" if exclusive_peer else "",
        )
        return self._name

    def stop(self) -> str | None:
        if self._file is None:
            return None
        for proxy in self._watched:
            if self._record in proxy.observers:
                proxy.observers.remove(self._record)
        if self._gateway is not None:
            self._gateway.exclusive_peer = None
            self._gateway = None
        self._file.close()
        self._file = None
        _LOGGER.info("Capture %s stopped: %d frames", self._name, self._frames)
        return self._name

    def _record(self, proxy: GatewayProxy, direction: str, frame: bytes, peer: str | None) -> None:
        if self._file is None:
            return
        kind = (
            "radio"
            if is_radio(proxy.protocol, frame)
            else "answer"
            if is_response(proxy.protocol, frame)
            else "other"
        )
        line = json.dumps(
            {
                "time": round(time.time(), 3),
                "gateway": proxy.name,
                "dir": direction,
                "kind": kind,
                "peer": peer,
                "hex": frame.hex(),
            }
        )
        self._file.write(line + "\n")
        self._file.flush()  # size and download stay current while recording
        self._frames += 1
        self._bytes += len(line) + 1
        if self._bytes >= MAX_BYTES:
            _LOGGER.warning("Capture %s reached %d MB, stopped", self._name, MAX_BYTES >> 20)
            self.stop()

    def status(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "file": self._name if self.active else None,
            "gateway": None
            if not self.active
            else (self._watched[0].name if len(self._watched) == 1 else None),
            "exclusive_peer": self._gateway.exclusive_peer if self._gateway else None,
            "started": self._started if self.active else None,
            "frames": self._frames if self.active else 0,
            "files": self.files(),
        }

    def files(self) -> list[dict[str, Any]]:
        if not self.directory.is_dir():
            return []
        found = [p for p in self.directory.iterdir() if NAME.match(p.name)]
        return [
            {"name": p.name, "size": p.stat().st_size, "modified": p.stat().st_mtime}
            for p in sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)
        ]

    def path(self, name: str) -> Path:
        """A capture file by name; nothing outside the capture directory."""
        if not NAME.match(name) or not (self.directory / name).is_file():
            raise CaptureError(f"no capture {name!r}")
        return self.directory / name

    def delete(self, name: str) -> None:
        if self.active and name == self._name:
            raise CaptureError("the running capture cannot be deleted")
        self.path(name).unlink()
