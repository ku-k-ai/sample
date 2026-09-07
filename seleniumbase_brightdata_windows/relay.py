"""Loopback-only HTTPS CONNECT relay. Standard library; no TLS interception.

Chrome connects without proxy credentials. Only this relay sends HTTP Basic
Proxy-Authorization to the configured upstream proxy. Upstream rejections are
mapped to 502 and never forwarded as a browser authentication challenge.

Scope: HTTPS / CONNECT. Plain HTTP requests are intentionally rejected.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import json
import re
import ssl
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ProxyConfig:
    host: str
    port: int
    username: str = field(repr=False)
    password: str = field(repr=False)
    scheme: str = "http"
    extra_ca: str | None = None
    connect_timeout: float = 25.0
    idle_timeout: float = 90.0

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[A-Za-z0-9.-]+", self.host):
            raise ValueError("BRD_PROXY_HOST must be a hostname, without scheme/path")
        if not 1 <= self.port <= 65535:
            raise ValueError("BRD_PROXY_PORT must be between 1 and 65535")
        if self.scheme not in ("http", "https"):
            raise ValueError("BRD_PROXY_SCHEME must be http or https")
        if not self.username or not self.password:
            raise ValueError("Proxy username and password are required")
        if ":" in self.username or any(c in self.username + self.password for c in "\r\n"):
            raise ValueError("Invalid character in proxy credentials")


def parse_authority(authority: str) -> tuple[str, int]:
    """Parse CONNECT host:port without accepting header/URI injection."""
    if authority.startswith("["):
        match = re.fullmatch(r"\[([0-9a-fA-F:]+)\]:(\d{1,5})", authority)
        if not match:
            raise ValueError("Invalid CONNECT authority")
        host = str(ipaddress.IPv6Address(match[1]))
    else:
        match = re.fullmatch(r"([A-Za-z0-9.-]+):(\d{1,5})", authority)
        if not match:
            raise ValueError("Invalid CONNECT authority")
        host = match[1].lower()
    port = int(match[2])
    if not 1 <= port <= 65535:
        raise ValueError("Invalid destination port")
    return host, port


class ConnectRelay:
    def __init__(self, config: ProxyConfig, log_path: Path):
        self.config = config
        self.log_path = Path(log_path)
        self.port = 0
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop: asyncio.Event | None = None
        self._error: BaseException | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    def __enter__(self) -> "ConnectRelay":
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._thread_main, daemon=True)
        self._thread.start()
        if not self._ready.wait(10):
            raise RuntimeError("Local relay startup timed out")
        if self._error is not None:
            raise RuntimeError("Local relay startup failed") from self._error
        return self

    def __exit__(self, *_: Any) -> None:
        if self._loop and self._stop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread:
            self._thread.join(timeout=10)

    def _thread_main(self) -> None:
        try:
            asyncio.run(self._serve())
        except BaseException as exc:
            self._error = exc
            self._ready.set()

    async def _serve(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        server = await asyncio.start_server(self._handle, "127.0.0.1", 0, limit=65536)
        assert server.sockets
        self.port = server.sockets[0].getsockname()[1]
        self._ready.set()
        try:
            await self._stop.wait()
        finally:
            # Stop active handlers before wait_closed (important on Python 3.13).
            server.close()
            tasks = list(self._tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await server.wait_closed()

    def _record(self, **event: Any) -> None:
        # Never log credentials, raw headers, bodies, or target hostnames/URLs.
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    @staticmethod
    async def _reply(writer: asyncio.StreamWriter, status: int, text: str) -> None:
        body = text.encode("ascii")
        writer.write(
            f"HTTP/1.1 {status} Relay Response\r\n"
            f"Content-Type: text/plain\r\nContent-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n".encode("ascii") + body
        )
        await writer.drain()

    async def _pump(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            block = await asyncio.wait_for(reader.read(65536), self.config.idle_timeout)
            if not block:
                break
            writer.write(block)
            await asyncio.wait_for(writer.drain(), self.config.idle_timeout)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._tasks.add(task)
        upstream_writer: asyncio.StreamWriter | None = None
        established = False
        pumps: list[asyncio.Task[Any]] = []
        try:
            request = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
            line = request.split(b"\r\n", 1)[0].decode("ascii")
            method, authority, version = line.split(" ")
            if method != "CONNECT":
                await self._reply(writer, 405, "This relay supports HTTPS CONNECT only.")
                return
            if version not in ("HTTP/1.0", "HTTP/1.1"):
                raise ValueError("Invalid HTTP version")
            parse_authority(authority)
            cfg = self.config
            tls = None
            if cfg.scheme == "https":
                tls = ssl.create_default_context()
                if cfg.extra_ca:
                    tls.load_verify_locations(cafile=cfg.extra_ca)
            upstream_reader, upstream_writer = await asyncio.wait_for(
                asyncio.open_connection(
                    cfg.host, cfg.port, ssl=tls,
                    server_hostname=cfg.host if tls else None, limit=65536,
                ), cfg.connect_timeout,
            )
            credential = base64.b64encode(
                (cfg.username + ":" + cfg.password).encode("utf-8")
            ).decode("ascii")
            # Build a new CONNECT request, rather than forwarding client headers.
            upstream_writer.write(
                f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n"
                f"Proxy-Authorization: Basic {credential}\r\n\r\n".encode("ascii")
            )
            await asyncio.wait_for(upstream_writer.drain(), cfg.connect_timeout)
            for _ in range(5):
                response = await asyncio.wait_for(
                    upstream_reader.readuntil(b"\r\n\r\n"), cfg.connect_timeout
                )
                first = response.split(b"\r\n", 1)[0]
                match = re.fullmatch(rb"HTTP/1\.[01] (\d{3})(?: .*)?", first)
                if not match:
                    raise ValueError("Invalid upstream response")
                status = int(match[1])
                if status >= 200:
                    break
            else:
                raise ValueError("Too many interim responses")
            # Only extract non-sensitive numeric Bright Data error identifiers.
            error_codes = sorted(set(
                part.decode("ascii") for part in re.findall(
                    rb"\b(?:client|server|policy)_[0-9]+\b", response.lower()
                )
            ))
            self._record(event="upstream_connect", status=status, error_codes=error_codes)
            if status != 200:
                # Critical: do not relay 407 / Proxy-Authenticate to Chrome.
                await self._reply(writer, 502, f"Upstream CONNECT rejected: HTTP {status}.")
                return
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            established = True
            pumps = [
                asyncio.create_task(self._pump(reader, upstream_writer)),
                asyncio.create_task(self._pump(upstream_reader, writer)),
            ]
            done, pending = await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            # Browser HTTPS sessions are full-duplex while open; close both ends
            # when either peer closes or reaches the idle deadline.
            for finished in done:
                finished.result()
            for pending_task in pending:
                pending_task.cancel()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._record(event="relay_error", error_type=type(exc).__name__)
            if not established:
                with contextlib.suppress(Exception):
                    await self._reply(writer, 502, "Upstream connection failed; see relay.jsonl.")
        finally:
            for pump in pumps:
                if not pump.done():
                    pump.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            for peer in (upstream_writer, writer):
                if peer is not None:
                    peer.close()
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(peer.wait_closed(), 2)
            self._tasks.discard(task)
