from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from datetime import datetime, timezone
from typing import Any

import httpx


class BroadcastHub:
    def __init__(self) -> None:
        self.status_clients: set[Any] = set()
        self.log_clients: set[Any] = set()
        self.sensor_clients: set[Any] = set()
        self.pose_clients: set[Any] = set()
        self.last_status: dict[str, Any] = {}
        self.last_sensor_frame: dict[str, Any] = {}
        self.last_pose_frame: dict[str, Any] = {}
        self.log_buffer: list[dict[str, Any]] = []

    async def broadcast_status(self, payload: dict[str, Any]) -> None:
        self.last_status = payload
        for ws in list(self.status_clients):
            with suppress(Exception):
                await ws.send_json(payload)

    async def broadcast_log(self, payload: dict[str, Any]) -> None:
        self.log_buffer.append(payload)
        self.log_buffer = self.log_buffer[-500:]
        for ws in list(self.log_clients):
            with suppress(Exception):
                await ws.send_json(payload)

    async def broadcast_sensor(self, payload: dict[str, Any]) -> None:
        self.last_sensor_frame = payload
        for ws in list(self.sensor_clients):
            with suppress(Exception):
                await ws.send_json(payload)

    async def broadcast_pose(self, payload: dict[str, Any]) -> None:
        self.last_pose_frame = payload
        for ws in list(self.pose_clients):
            with suppress(Exception):
                await ws.send_json(payload)

    def log(self, channel: str, level: str, message: str, source: str = "server") -> dict[str, Any]:
        payload = {
            "channel": channel,
            "level": level,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": source,
        }
        self.log_buffer.append(payload)
        self.log_buffer = self.log_buffer[-500:]
        return payload


class AgentBridge:
    def __init__(self, base_url: str, token: str, hub: BroadcastHub, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.hub = hub
        self.timeout = timeout
        self.client = httpx.AsyncClient(timeout=timeout, trust_env=False)
        self._logs_task: asyncio.Task[None] | None = None
        self._status_task: asyncio.Task[None] | None = None
        self._sensor_task: asyncio.Task[None] | None = None
        self._pose_task: asyncio.Task[None] | None = None
        self._running = False

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = kwargs.pop("headers", {})
        headers.update(self._headers())
        return await self.client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)

    async def fetch_json(self, method: str, path: str, **kwargs: Any) -> Any:
        resp = await self.request(method, path, **kwargs)
        resp.raise_for_status()
        return resp.json()

    async def _poll_status(self, interval: float) -> None:
        while self._running:
            try:
                status = await self.fetch_json("GET", "/status")
                await self.hub.broadcast_status(status)
            except Exception as exc:
                payload = {
                    "agent_online": False,
                    "board_online": False,
                    "current_video_source": None,
                    "current_model": None,
                    "error": str(exc),
                    "last_heartbeat": datetime.now(timezone.utc).isoformat(),
                }
                await self.hub.broadcast_status(payload)
                await self.hub.broadcast_log(self.hub.log("system", "error", f"status poll failed: {exc}"))
            await asyncio.sleep(interval)

    async def _consume_logs(self) -> None:
        while self._running:
            try:
                async with self.client.stream(
                    "GET",
                    f"{self.base_url}/logs/stream",
                    headers=self._headers(),
                    timeout=None,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        payload = json.loads(line)
                        await self.hub.broadcast_log(payload)
            except Exception as exc:
                await self.hub.broadcast_log(self.hub.log("system", "error", f"log stream dropped: {exc}"))
                await asyncio.sleep(2.0)

    async def _consume_sensors(self) -> None:
        while self._running:
            try:
                async with self.client.stream(
                    "GET",
                    f"{self.base_url}/sensors/stream",
                    headers=self._headers(),
                    timeout=None,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        payload = json.loads(line)
                        await self.hub.broadcast_sensor(payload)
            except Exception as exc:
                await self.hub.broadcast_log(self.hub.log("system", "error", f"sensor stream dropped: {exc}"))
                await asyncio.sleep(2.0)

    async def _consume_pose(self) -> None:
        while self._running:
            try:
                async with self.client.stream(
                    "GET",
                    f"{self.base_url}/pose/stream",
                    headers=self._headers(),
                    timeout=None,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        payload = json.loads(line)
                        await self.hub.broadcast_pose(payload)
            except Exception as exc:
                await self.hub.broadcast_log(self.hub.log("system", "error", f"pose stream dropped: {exc}"))
                await asyncio.sleep(2.0)

    async def start_background_tasks(self, interval: float) -> None:
        self._running = True
        self._status_task = asyncio.create_task(self._poll_status(interval))
        self._logs_task = asyncio.create_task(self._consume_logs())
        self._sensor_task = asyncio.create_task(self._consume_sensors())
        self._pose_task = asyncio.create_task(self._consume_pose())

    async def close(self) -> None:
        self._running = False
        for task in [self._status_task, self._logs_task, self._sensor_task, self._pose_task]:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await task
        await self.client.aclose()
