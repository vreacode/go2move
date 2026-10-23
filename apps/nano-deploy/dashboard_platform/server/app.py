from __future__ import annotations
import re

from contextlib import asynccontextmanager
from functools import lru_cache
import importlib.util
import json
from pathlib import Path
import time
from typing import AsyncIterator, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .config import ServerSettings
from .hub import AgentBridge, BroadcastHub

settings = ServerSettings()
hub = BroadcastHub()
bridge = AgentBridge(settings.board_base_url, settings.agent_token, hub)


class LoginRequest(BaseModel):
    username: str
    password: str


class ModelStartRequest(BaseModel):
    model_id: str
    source: str | None = None
    vid_stride: int | None = None
    img_size: int | None = None
    conf: float | None = None
    iou: float | None = None
    device: str | None = None
    half: bool | None = None
    view: bool = False
    annotate: bool = True
    event_classes: str = ""
    parking_zone: list[float] | None = Field(default=None, min_length=4, max_length=4)
    parking_dwell_seconds: float = Field(default=5.0, ge=0.0, le=3600.0)
    parking_cooldown_seconds: float = Field(default=30.0, ge=0.0, le=3600.0)
    parking_min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    parking_track_iou: float = Field(default=0.3, gt=0.0, le=1.0)

    @field_validator("parking_zone")
    @classmethod
    def validate_parking_zone(cls, value: list[float] | None) -> list[float] | None:
        if value is None:
            return value
        if any(coordinate < 0.0 or coordinate > 1.0 for coordinate in value):
            raise ValueError("parking_zone coordinates must be normalized to [0, 1]")
        if value[0] >= value[2] or value[1] >= value[3]:
            raise ValueError("parking_zone must have positive area")
        return value


class ControlRequest(BaseModel):
    command: str
    speed: str = "medium"


class CruiseStartRequest(BaseModel):
    speed: Literal["low", "medium", "high"] = "medium"
    auto_arm: bool = False


class CruiseSteeringRequest(BaseModel):
    direction: Literal["center", "left", "right"] = "center"


class GimbalPulseRequest(BaseModel):
    yaw_speed: int = Field(default=0, ge=-100, le=100)
    pitch_speed: int = Field(default=0, ge=-100, le=100)
    duration_ms: int = Field(default=250, ge=50, le=1000)


class MotionArmRequest(BaseModel):
    armed: bool


class MotionOverrideRequest(BaseModel):
    enabled: bool


class MotionModeRequest(BaseModel):
    mode: str


class PresetRequest(BaseModel):
    name: str
    variables: dict[str, str] = {}


class NavigationPointRequest(BaseModel):
    x: float
    y: float
    yaw: float = 0.0


class MediaRecordingStartRequest(BaseModel):
    stream_mode: str = "visible_raw"


class MediaSnapshotRequest(BaseModel):
    stream_mode: str = "visible_raw"


class CaptureStartRequest(BaseModel):
    stream_mode: Literal["visible_raw", "ir_raw"] = "visible_raw"
    scene: str = Field(min_length=1, max_length=80)
    note: str = Field(default="", max_length=240)
    interval_seconds: float = Field(default=1.0, ge=0.2, le=60.0)
    duration_seconds: float = Field(default=0.0, ge=0.0, le=3600.0)
    max_frames: int = Field(default=0, ge=0, le=10000)
    jpeg_quality: int = Field(default=100, ge=50, le=100)


class WebRTCOffer(BaseModel):
    sdp: str
    type: str = "offer"
    stream_mode: str = "visible_raw"


def _extract_token(authorization: str | None) -> str | None:
    if not authorization or not authorization.startswith("Bearer "):
        return None
    prefix = "Bearer "
    return (authorization[len(prefix):] if authorization.startswith(prefix) else authorization).strip()


def require_token(authorization: str | None = Header(default=None)) -> str:
    token = _extract_token(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    if token != settings.auth_token:
        raise HTTPException(status_code=403, detail="invalid token")
    return token


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await bridge.start_background_tasks(settings.websocket_status_interval)
    await hub.broadcast_log(hub.log("system", "info", f"server started on {settings.server_host}:{settings.server_port}"))
    yield
    await bridge.close()


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(httpx.RequestError)
async def backend_connection_error(request, exc: httpx.RequestError) -> JSONResponse:
    # Agent unavailability is a service outage, not an unhandled application bug.
    # Do not expose upstream addresses, credentials or raw exception text.
    return JSONResponse(status_code=503, content={
        "detail": "设备服务暂时无法连接，请重新运行启动命令；视觉识别服务可独立运行。"
    })


@app.post("/api/auth/login")
async def login(payload: LoginRequest) -> dict:
    if payload.username != settings.auth_username or payload.password != settings.auth_password:
        raise HTTPException(status_code=401, detail="invalid credentials")
    return {"token": settings.auth_token, "username": settings.auth_username}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.get("/api/system/status")
async def system_status(_: str = Depends(require_token)) -> dict:
    try:
        status = await bridge.fetch_json("GET", "/status")
        await hub.broadcast_status(status)
        return status
    except Exception:
        if hub.last_status:
            return hub.last_status
        raise


@app.get("/api/camera/status")
async def api_camera_status(_: str = Depends(require_token)) -> dict:
    return await bridge.fetch_json("GET", "/camera/status")


@app.post("/api/camera/gimbal/pulse")
async def api_camera_gimbal_pulse(payload: GimbalPulseRequest, _: str = Depends(require_token)) -> dict:
    return await bridge.fetch_json("POST", "/camera/gimbal/pulse", json=payload.model_dump())


@app.post("/api/camera/gimbal/stop")
async def api_camera_gimbal_stop(_: str = Depends(require_token)) -> dict:
    return await bridge.fetch_json("POST", "/camera/gimbal/stop")


@app.post("/api/camera/gimbal/center")
async def api_camera_gimbal_center(_: str = Depends(require_token)) -> dict:
    return await bridge.fetch_json("POST", "/camera/gimbal/center")


@app.post("/api/model/start")
async def api_model_start(payload: ModelStartRequest, _: str = Depends(require_token)) -> dict:
    await hub.broadcast_log(hub.log("system", "info", f"model start requested: {payload.model_id}"))
    return await bridge.fetch_json("POST", "/model/start", json=payload.model_dump())


@app.post("/api/model/stop")
async def api_model_stop(_: str = Depends(require_token)) -> dict:
    await hub.broadcast_log(hub.log("system", "info", "model stop requested"))
    return await bridge.fetch_json("POST", "/model/stop")


@app.post("/api/model/restart")
async def api_model_restart(payload: ModelStartRequest, _: str = Depends(require_token)) -> dict:
    await hub.broadcast_log(hub.log("system", "info", f"model restart requested: {payload.model_id}"))
    return await bridge.fetch_json("POST", "/model/restart", json=payload.model_dump())


@app.get("/api/model/status")
async def api_model_status(_: str = Depends(require_token)) -> dict:
    return await bridge.fetch_json("GET", "/model/status")


@app.post("/api/control/send")
async def api_control_send(payload: ControlRequest, _: str = Depends(require_token)) -> dict:
    await hub.broadcast_log(hub.log("control", "info", f"control request: {payload.command}/{payload.speed}"))
    try:
        return await bridge.fetch_json("POST", "/control/send", json=payload.model_dump())
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "control request failed"
        try:
            parsed = exc.response.json()
            detail = parsed.get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc


@app.post("/api/control/cruise/start")
async def api_control_cruise_start(payload: CruiseStartRequest, _: str = Depends(require_token)) -> dict:
    await hub.broadcast_log(hub.log("control", "info", f"cruise start request: {payload.speed}"))
    try:
        return await bridge.fetch_json("POST", "/control/cruise/start", json=payload.model_dump())
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "cruise start request failed"
        try:
            parsed = exc.response.json()
            detail = parsed.get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc


@app.post("/api/control/cruise/steer")
async def api_control_cruise_steer(payload: CruiseSteeringRequest, _: str = Depends(require_token)) -> dict:
    try:
        return await bridge.fetch_json("POST", "/control/cruise/steer", json=payload.model_dump())
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "cruise steering request failed"
        try:
            parsed = exc.response.json()
            detail = parsed.get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc


@app.post("/api/control/cruise/heartbeat")
async def api_control_cruise_heartbeat(_: str = Depends(require_token)) -> dict:
    try:
        return await bridge.fetch_json("POST", "/control/cruise/heartbeat")
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "cruise heartbeat failed"
        try:
            parsed = exc.response.json()
            detail = parsed.get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc


@app.post("/api/control/cruise/stop")
async def api_control_cruise_stop(_: str = Depends(require_token)) -> dict:
    await hub.broadcast_log(hub.log("control", "warning", "cruise stop requested"))
    try:
        return await bridge.fetch_json("POST", "/control/cruise/stop")
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "cruise stop request failed"
        try:
            parsed = exc.response.json()
            detail = parsed.get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc


@app.get("/api/control/state")
async def api_control_state(_: str = Depends(require_token)) -> dict:
    return await bridge.fetch_json("GET", "/control/state")


@app.get("/api/base/status")
async def api_base_status(_: str = Depends(require_token)) -> dict:
    return await bridge.fetch_json("GET", "/base/status")


@app.post("/api/base/start")
async def api_base_start(_: str = Depends(require_token)) -> dict:
    await hub.broadcast_log(hub.log("control", "warning", "base bringup start requested"))
    return await bridge.fetch_json("POST", "/base/start", timeout=45.0)


@app.post("/api/base/stop")
async def api_base_stop(_: str = Depends(require_token)) -> dict:
    await hub.broadcast_log(hub.log("control", "warning", "base bringup stop requested"))
    return await bridge.fetch_json("POST", "/base/stop", timeout=20.0)


@app.post("/api/control/arm")
async def api_control_arm(payload: MotionArmRequest, _: str = Depends(require_token)) -> dict:
    await hub.broadcast_log(hub.log("control", "warning", f"manual control {'armed' if payload.armed else 'locked'}"))
    try:
        return await bridge.fetch_json("POST", "/control/arm", json=payload.model_dump())
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "arm request failed"
        try:
            parsed = exc.response.json()
            detail = parsed.get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc


@app.post("/api/control/manual-override")
async def api_control_manual_override(payload: MotionOverrideRequest, _: str = Depends(require_token)) -> dict:
    await hub.broadcast_log(hub.log("control", "warning", f"manual override {'enabled' if payload.enabled else 'disabled'}"))
    try:
        return await bridge.fetch_json("POST", "/control/manual-override", json=payload.model_dump())
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "manual override request failed"
        try:
            parsed = exc.response.json()
            detail = parsed.get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc


@app.post("/api/control/mode")
async def api_control_mode(payload: MotionModeRequest, _: str = Depends(require_token)) -> dict:
    await hub.broadcast_log(hub.log("control", "warning", f"motion mode switch requested: {payload.mode}"))
    try:
        return await bridge.fetch_json("POST", "/control/mode", json=payload.model_dump())
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "mode switch request failed"
        try:
            parsed = exc.response.json()
            detail = parsed.get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc


@app.post("/api/control/emergency-stop")
async def api_control_emergency_stop(_: str = Depends(require_token)) -> dict:
    await hub.broadcast_log(hub.log("control", "warning", "emergency stop requested"))
    try:
        return await bridge.fetch_json("POST", "/control/emergency-stop")
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "emergency stop request failed"
        try:
            parsed = exc.response.json()
            detail = parsed.get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc


@app.post("/api/preset/run")
async def api_preset_run(payload: PresetRequest, _: str = Depends(require_token)) -> dict:
    await hub.broadcast_log(hub.log("system", "info", f"preset request: {payload.name}"))
    try:
        return await bridge.fetch_json("POST", "/preset/run", json=payload.model_dump(), timeout=45.0)
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "preset request failed"
        try:
            parsed = exc.response.json()
            detail = parsed.get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail=f"preset '{payload.name}' timed out") from exc


@app.get("/api/navigation/status")
async def api_navigation_status(_: str = Depends(require_token)) -> dict:
    return await bridge.fetch_json("GET", "/navigation/status")


@app.post("/api/navigation/initial-pose")
async def api_navigation_initial_pose(payload: NavigationPointRequest, _: str = Depends(require_token)) -> dict:
    try:
        return await bridge.fetch_json("POST", "/navigation/initial-pose", json=payload.model_dump())
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "initial pose request failed"
        try:
            detail = exc.response.json().get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc


@app.post("/api/navigation/goal")
async def api_navigation_goal(payload: NavigationPointRequest, _: str = Depends(require_token)) -> dict:
    try:
        return await bridge.fetch_json("POST", "/navigation/goal", json=payload.model_dump())
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "navigation goal request failed"
        try:
            detail = exc.response.json().get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc


@app.post("/api/navigation/goal/cancel")
async def api_navigation_goal_cancel(_: str = Depends(require_token)) -> dict:
    return await bridge.fetch_json("POST", "/navigation/goal/cancel")


@app.get("/api/maps")
async def api_maps(_: str = Depends(require_token)) -> dict:
    items = await bridge.fetch_json("GET", "/maps")
    return {"items": items}


@app.get("/api/maps/{map_name}")
async def api_map_metadata(map_name: str, _: str = Depends(require_token)) -> dict:
    return await bridge.fetch_json("GET", f"/maps/{map_name}")


@app.get("/api/maps/{map_name}/pgm")
async def api_map_image(map_name: str, authorization: str | None = Header(default=None), token: str | None = Query(default=None)) -> StreamingResponse:
    header_token = _extract_token(authorization)
    if header_token != settings.auth_token and token != settings.auth_token:
        raise HTTPException(status_code=403, detail="invalid token")

    async def generate():
        async with bridge.client.stream(
            "GET",
            f"{bridge.base_url}/maps/{map_name}/pgm",
            headers={"Authorization": f"Bearer {bridge.token}"},
            timeout=None,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                yield chunk

    return StreamingResponse(generate(), media_type="image/x-portable-graymap")


@app.get("/api/media/status")
async def api_media_status(_: str = Depends(require_token)) -> dict:
    return await bridge.fetch_json("GET", "/media/status")


@app.get("/api/capture/status")
async def api_capture_status(_: str = Depends(require_token)) -> dict:
    return await bridge.fetch_json("GET", "/capture/status")


@app.get("/api/capture/gallery")
async def api_capture_gallery(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _: str = Depends(require_token),
) -> dict:
    items = await bridge.fetch_json("GET", f"/capture/gallery?page={page}&page_size={page_size}")
    return {"items": items}


@app.get("/api/capture/gallery/{group}/latest")
async def api_capture_gallery_latest(
    group: str,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> StreamingResponse:
    header_token = _extract_token(authorization)
    if header_token != settings.auth_token and token != settings.auth_token:
        raise HTTPException(status_code=403, detail="invalid token")

    async def generate():
        async with bridge.client.stream(
            "GET",
            f"{bridge.base_url}/capture/gallery/{group}/latest",
            headers={"Authorization": f"Bearer {bridge.token}"},
            timeout=None,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                yield chunk

    return StreamingResponse(generate(), media_type="image/jpeg")


@app.get("/api/capture/gallery/{group}/frames")
async def api_capture_gallery_frames(group: str, _: str = Depends(require_token)) -> dict:
    return {"items": await bridge.fetch_json("GET", f"/capture/gallery/{group}/frames")}


@app.get("/api/capture/gallery/{group}/frames/{frame_name}")
async def api_capture_gallery_frame(
    group: str,
    frame_name: str,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> StreamingResponse:
    header_token = _extract_token(authorization)
    if header_token != settings.auth_token and token != settings.auth_token:
        raise HTTPException(status_code=403, detail="invalid token")

    async def generate():
        async with bridge.client.stream(
            "GET",
            f"{bridge.base_url}/capture/gallery/{group}/frames/{frame_name}",
            headers={"Authorization": f"Bearer {bridge.token}"},
            timeout=None,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                yield chunk

    return StreamingResponse(generate(), media_type="image/jpeg")


@app.post("/api/capture/start")
async def api_capture_start(payload: CaptureStartRequest, _: str = Depends(require_token)) -> dict:
    try:
        return await bridge.fetch_json("POST", "/capture/start", json=payload.model_dump())
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "capture start failed"
        try:
            parsed = exc.response.json()
            detail = parsed.get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc


@app.post("/api/capture/stop")
async def api_capture_stop(_: str = Depends(require_token)) -> dict:
    return await bridge.fetch_json("POST", "/capture/stop")


@app.get("/api/capture/latest")
async def api_capture_latest(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> StreamingResponse:
    header_token = _extract_token(authorization)
    if header_token != settings.auth_token and token != settings.auth_token:
        raise HTTPException(status_code=403, detail="invalid token")

    async def generate():
        async with bridge.client.stream(
            "GET",
            f"{bridge.base_url}/capture/latest",
            headers={"Authorization": f"Bearer {bridge.token}"},
            timeout=None,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                yield chunk

    return StreamingResponse(generate(), media_type="image/jpeg")


@app.get("/api/dev-recording/status")
async def api_dev_recording_status(_: str = Depends(require_token)) -> dict:
    return await bridge.fetch_json("GET", "/dev-recording/status")


@app.post("/api/dev-recording/start")
async def api_dev_recording_start(_: str = Depends(require_token)) -> dict:
    try:
        return await bridge.fetch_json("POST", "/dev-recording/start", timeout=20.0)
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "desktop recording start failed"
        try:
            parsed = exc.response.json()
            detail = parsed.get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc


@app.post("/api/dev-recording/stop")
async def api_dev_recording_stop(_: str = Depends(require_token)) -> dict:
    return await bridge.fetch_json("POST", "/dev-recording/stop", timeout=20.0)


@app.post("/api/media/recording/start")
async def api_media_recording_start(payload: MediaRecordingStartRequest, _: str = Depends(require_token)) -> dict:
    try:
        return await bridge.fetch_json("POST", "/media/recording/start", json=payload.model_dump())
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "recording start failed"
        try:
            parsed = exc.response.json()
            detail = parsed.get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc


@app.post("/api/media/recording/stop")
async def api_media_recording_stop(_: str = Depends(require_token)) -> dict:
    return await bridge.fetch_json("POST", "/media/recording/stop")


@app.post("/api/media/snapshot")
async def api_media_snapshot(payload: MediaSnapshotRequest, _: str = Depends(require_token)) -> dict:
    try:
        return await bridge.fetch_json("POST", "/media/snapshot", json=payload.model_dump())
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "snapshot failed"
        try:
            parsed = exc.response.json()
            detail = parsed.get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc


@app.get("/api/media/gallery")
async def api_media_gallery(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    _: str = Depends(require_token),
) -> dict:
    items = await bridge.fetch_json("GET", f"/media/gallery?page={page}&page_size={page_size}")
    return {"items": items}


@app.get("/api/media/files/{storage_kind}/{filename}")
async def api_media_file(
    storage_kind: str,
    filename: str,
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
) -> StreamingResponse:
    header_token = _extract_token(authorization)
    if header_token != settings.auth_token and token != settings.auth_token:
        raise HTTPException(status_code=403, detail="invalid token")

    async def generate():
        async with bridge.client.stream(
            "GET",
            f"{bridge.base_url}/media/files/{storage_kind}/{filename}",
            headers={"Authorization": f"Bearer {bridge.token}"},
            timeout=None,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                yield chunk

    media_type = "video/mp4" if filename.lower().endswith(".mp4") else "image/jpeg"
    return StreamingResponse(generate(), media_type=media_type)


@app.get("/api/stream/webrtc-offer")
async def webrtc_offer_get() -> JSONResponse:
    raise HTTPException(status_code=405, detail="use POST /api/stream/webrtc-offer")


@app.post("/api/stream/webrtc-offer")
async def api_webrtc_offer(payload: WebRTCOffer, _: str = Depends(require_token)) -> dict:
    try:
        return await bridge.fetch_json("POST", "/stream/webrtc-offer", json=payload.model_dump())
    except httpx.HTTPStatusError as exc:
        detail = exc.response.text or "stream offer failed"
        try:
            parsed = exc.response.json()
            detail = parsed.get("detail", detail)
        except Exception:
            pass
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc


@app.get("/api/stream/fallback")
async def api_stream_fallback(
    authorization: str | None = Header(default=None),
    token: str | None = Query(default=None),
    mode: str = Query(default="visible_raw"),
) -> StreamingResponse:
    header_token = _extract_token(authorization)
    if header_token != settings.auth_token and token != settings.auth_token:
        raise HTTPException(status_code=403, detail="invalid token")
    selected_mode = mode if mode in {"visible_raw", "ir_raw"} else "visible_raw"

    async def generate():
        async with bridge.client.stream(
            "GET",
            f"{bridge.base_url}/preview.mjpeg",
            headers={"Authorization": f"Bearer {bridge.token}"},
            params={"mode": selected_mode},
            timeout=None,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                yield chunk

    return StreamingResponse(generate(), media_type="multipart/x-mixed-replace; boundary=frame")


@app.get("/api/stream/snapshot")
async def api_stream_snapshot(
    authorization: str | None = Header(default=None),
    mode: str = Query(default="visible_raw"),
) -> Response:
    header_token = _extract_token(authorization)
    if header_token != settings.auth_token:
        raise HTTPException(status_code=403, detail="invalid token")
    selected_mode = mode if mode in {"visible_raw", "ir_raw"} else "visible_raw"
    async with bridge.client.stream(
        "GET",
        f"{bridge.base_url}/preview.jpg",
        headers={"Authorization": f"Bearer {bridge.token}"},
        params={"mode": selected_mode},
        timeout=10.0,
    ) as response:
        response.raise_for_status()
        content = await response.aread()
    return Response(
        content=content,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )


@app.websocket("/ws/status")
async def ws_status(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token")
    if token != settings.auth_token:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    hub.status_clients.add(websocket)
    try:
        if hub.last_status:
            await websocket.send_json(hub.last_status)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.status_clients.discard(websocket)


@app.websocket("/ws/logs")
async def ws_logs(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token")
    if token != settings.auth_token:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    hub.log_clients.add(websocket)
    try:
        for item in hub.log_buffer[-200:]:
            await websocket.send_json(item)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.log_clients.discard(websocket)


@app.websocket("/ws/sensors")
async def ws_sensors(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token")
    if token != settings.auth_token:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    hub.sensor_clients.add(websocket)
    try:
        if hub.last_sensor_frame:
            await websocket.send_json(hub.last_sensor_frame)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.sensor_clients.discard(websocket)


@app.websocket("/ws/pose")
async def ws_pose(websocket: WebSocket) -> None:
    token = websocket.query_params.get("token")
    if token != settings.auth_token:
        await websocket.close(code=4403)
        return
    await websocket.accept()
    hub.pose_clients.add(websocket)
    try:
        if hub.last_pose_frame:
            await websocket.send_json(hub.last_pose_frame)
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        hub.pose_clients.discard(websocket)



_GO2_HEALTH_PATH = Path('/home/unitree/go2_ros2_ws/runtime/health/status.json')


@lru_cache(maxsize=1)
def _go2_health_summarizer():
    # This module is pure data decoding: importing it cannot initialize robot SDKs.
    module_path = Path(__file__).resolve().parents[4] / 'deployment/go2/health_status.py'
    spec = importlib.util.spec_from_file_location('go2_health_status', module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError('Health status decoder unavailable')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.summarize_health


@app.get('/api/go2/health')
def go2_health_status(_: str = Depends(require_token)) -> JSONResponse:
    try:
        raw = json.loads(_GO2_HEALTH_PATH.read_text())
    except (OSError, ValueError):
        raw = None
    summary = _go2_health_summarizer()(raw, time.time())
    return JSONResponse(summary, headers={'Cache-Control': 'no-store'})


class Go2VoiceText(BaseModel):
    text: str = Field(min_length=1, max_length=160)
    speak: bool = True


class Go2VoiceListen(BaseModel):
    seconds: float = Field(default=5.0, ge=2.0, le=10.0)
    execute: bool = True
    speak: bool = True


class Go2VoiceSelection(BaseModel):
    voice_id: str = Field(min_length=1, max_length=64)


async def go2_voice_request(method: str, path: str, payload: dict | None = None) -> dict:
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=125.0 if path == '/pack/import' else 65.0) as client:
            response = await client.request(method, "http://127.0.0.1:8767" + path, json=payload)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code, detail=exc.response.json().get("detail", "语音请求失败")) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Go2语音服务未连接，请启动机身媒体服务。") from exc


@app.get("/api/go2/voice/status")
async def go2_voice_status(_: str = Depends(require_token)) -> dict:
    return await go2_voice_request("GET", "/status")


@app.post("/api/go2/voice/command")
async def go2_voice_command(payload: Go2VoiceText, _: str = Depends(require_token)) -> dict:
    return await go2_voice_request("POST", "/command", payload.model_dump())


@app.post("/api/go2/voice/say")
async def go2_voice_say(payload: Go2VoiceText, _: str = Depends(require_token)) -> dict:
    return await go2_voice_request("POST", "/say", payload.model_dump())


@app.post("/api/go2/voice/listen")
async def go2_voice_listen(payload: Go2VoiceListen, _: str = Depends(require_token)) -> dict:
    return await go2_voice_request("POST", "/listen", payload.model_dump())


@app.post("/api/go2/voice/select")
async def go2_voice_select(payload: Go2VoiceSelection, _: str = Depends(require_token)) -> dict:
    return await go2_voice_request("POST", "/select", payload.model_dump())


@app.post("/api/go2/voice/pack/import")
async def go2_voice_pack_import(_: str = Depends(require_token)) -> dict:
    return await go2_voice_request("POST", "/pack/import")


class Go2PersonTarget(BaseModel):
    image_base64: str = Field(min_length=16, max_length=8_000_000)
    name: str = Field(default="目标人物", min_length=1, max_length=32)


class Go2PersonDescription(BaseModel):
    model_config = {"extra": "forbid"}
    text: str = Field(min_length=1, max_length=160)
    name: str | None = Field(default=None, min_length=1, max_length=32)


class Go2HistoryReview(BaseModel):
    model_config = {"extra": "forbid"}
    status: Literal["confirmed", "rejected", "suspected"]
    note: str = Field(default="", max_length=240)


class Go2TaskText(BaseModel):
    model_config = {"extra": "forbid"}
    text: str = Field(min_length=1, max_length=160)


class Go2TaskAudio(BaseModel):
    model_config = {"extra": "forbid"}
    wav_base64: str = Field(min_length=44, max_length=600000)


class Go2PersonCapture(BaseModel):
    name: str = Field(default="目标人物", min_length=1, max_length=32)


class Go2PersonCaptureSelect(Go2PersonCapture):
    capture_id: str = Field(min_length=1, max_length=64)
    index: int = Field(ge=0, le=99)


class Go2PersonStart(BaseModel):
    mode: Literal["stationary"] = "stationary"
    similarity_threshold: float = Field(default=0.75, ge=0.5, le=0.99)
    speak: bool = False
    timeout_seconds: int = Field(default=120, ge=30, le=600)
    color_threshold: float = Field(default=0.35, ge=0.15, le=0.9)


class Go2FollowStart(BaseModel):
    model_config = {"extra": "forbid", "allow_inf_nan": False}
    mode: Literal["preview"] = "preview"
    distance_m: float = Field(default=1.8, ge=1.5, le=2.5)


@app.get("/api/go2/person/follow/status")
async def go2_follow_status(_: str = Depends(require_token)) -> dict:
    return (await go2_person_request("GET", "/follow/status")).json()


@app.post("/api/go2/person/follow/start")
async def go2_follow_start(payload: Go2FollowStart, _: str = Depends(require_token)) -> dict:
    return (await go2_person_request("POST", "/follow/start", payload.model_dump())).json()


@app.post("/api/go2/person/follow/stop")
async def go2_follow_stop(_: str = Depends(require_token)) -> dict:
    return (await go2_person_request("POST", "/follow/stop")).json()


@app.post("/api/go2/person/follow/execute")
async def go2_follow_execute(_: str = Depends(require_token)) -> dict:
    raise HTTPException(status_code=409, detail="实际跟随尚未验收，当前仅开放无运动预览。")


class Go2PatrolModel(BaseModel):
    model_config = {"extra": "forbid", "allow_inf_nan": False}


class Go2PatrolPoint(Go2PatrolModel):
    name: str = Field(min_length=1, max_length=24)
    x: float = Field(ge=-1000, le=1000)
    y: float = Field(ge=-1000, le=1000)
    yaw: float = Field(ge=-3.141592653589793, le=3.141592653589793)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("观察点名称不能为空。")
        return value.strip()


class Go2PatrolRoute(Go2PatrolModel):
    name: str = Field(min_length=1, max_length=32)
    frame_id: Literal["map"] = "map"
    points: list[Go2PatrolPoint] = Field(min_length=1, max_length=12)

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("区域名称不能为空。")
        return value.strip()

    @field_validator("points")
    @classmethod
    def distinct_points(cls, points: list[Go2PatrolPoint]) -> list[Go2PatrolPoint]:
        if len({point.name for point in points}) != len(points):
            raise ValueError("观察点名称不能重复。")
        if len({(point.x, point.y, point.yaw) for point in points}) != len(points):
            raise ValueError("观察点位置与朝向不能完全重复。")
        return points


class Go2PatrolSelection(Go2PatrolModel):
    route_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class Go2PatrolSuggest(Go2PatrolModel):
    map_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
    count: int = Field(default=3, ge=1, le=6)
    radius_m: float = Field(default=2.0, ge=0.5, le=5.0)


class Go2PatrolRehearsal(Go2PatrolSelection):
    scenario: Literal["not_found", "found", "navigation_failed"] = "not_found"


class Go2ScanStart(BaseModel):
    model_config = {"extra": "forbid"}
    confirmed: Literal[True]


async def go2_person_request(method: str, path: str, payload: dict | None = None) -> httpx.Response:
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=45.0) as client:
            response = await client.request(method, "http://127.0.0.1:8768" + path, json=payload)
            response.raise_for_status()
            return response
    except httpx.HTTPStatusError as exc:
        try:
            detail = exc.response.json().get("detail", exc.response.text)
        except (ValueError, AttributeError):
            detail = exc.response.text
        raise HTTPException(status_code=exc.response.status_code, detail=detail) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="找人服务连接失败：" + str(exc)) from exc


@app.get("/api/go2/person/status")
async def go2_person_status(_: str = Depends(require_token)) -> dict:
    return (await go2_person_request("GET", "/status")).json()


@app.post("/api/go2/person/target")
async def go2_person_target(payload: Go2PersonTarget, _: str = Depends(require_token)) -> dict:
    return (await go2_person_request("POST", "/target", payload.model_dump())).json()


@app.post("/api/go2/person/description")
async def go2_person_description(payload: Go2PersonDescription, _: str = Depends(require_token)) -> dict:
    return (await go2_person_request("POST", "/description", payload.model_dump(exclude_none=True))).json()


@app.get("/api/go2/person/targets")
async def go2_person_targets(_: str = Depends(require_token)) -> dict:
    return (await go2_person_request("GET", "/targets")).json()


@app.get("/api/go2/person/targets/{target_id}/image.jpg")
async def go2_person_saved_target_image(target_id: str, _: str = Depends(require_token)) -> Response:
    import re
    if not re.fullmatch(r"[0-9a-f]{32}", target_id):
        raise HTTPException(404, detail="目标不存在。")
    response = await go2_person_request("GET", f"/targets/{target_id}/image.jpg")
    return Response(response.content, media_type="image/jpeg", headers={"Cache-Control":"no-store"})


@app.post("/api/go2/person/targets/{target_id}/select")
async def go2_person_select_target(target_id: str, _: str = Depends(require_token)) -> dict:
    import re
    if not re.fullmatch(r"[0-9a-f]{32}", target_id):
        raise HTTPException(404, detail="目标不存在。")
    return (await go2_person_request("POST", f"/targets/{target_id}/select")).json()


@app.delete("/api/go2/person/targets/{target_id}")
async def go2_person_delete_target(target_id: str, _: str = Depends(require_token)) -> dict:
    import re
    if not re.fullmatch(r"[0-9a-f]{32}", target_id):
        raise HTTPException(404, detail="目标不存在。")
    return (await go2_person_request("DELETE", f"/targets/{target_id}")).json()


@app.delete("/api/go2/person/history/{event_id}")
async def go2_person_delete_history(event_id: str, _: str = Depends(require_token)) -> dict:
    import re
    if not re.fullmatch(r"[0-9a-f]{32}", event_id):
        raise HTTPException(404, detail="记录不存在。")
    return (await go2_person_request("DELETE", f"/history/{event_id}")).json()


@app.get("/api/go2/person/tasks")
async def go2_person_tasks(limit: int = Query(default=20, ge=1, le=100), _: str = Depends(require_token)) -> dict:
    return (await go2_person_request("GET", f"/tasks?limit={limit}")).json()


@app.post("/api/go2/task/command")
async def go2_task_command(payload: Go2TaskText, _: str = Depends(require_token)) -> dict:
    from .go2_commands import execute_command
    async def person_call(method, path, body):
        return (await go2_person_request(method, path, body)).json()
    return await execute_command(payload.text, person_call)


@app.post("/api/go2/task/transcribe")
async def go2_task_transcribe(payload: Go2TaskAudio, _: str = Depends(require_token)) -> dict:
    from .go2_commands import transcribe_audio
    return await transcribe_audio(payload.wav_base64)


@app.get("/api/go2/person/history/export.json")
async def go2_person_history_export(_: str = Depends(require_token)) -> Response:
    response = await go2_person_request("GET", "/history/export.json")
    return Response(content=response.content, media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="go2-person-history.json"', "Cache-Control": "no-store"})


@app.post("/api/go2/person/history/{event_id}/review")
async def go2_person_history_review(event_id: str, payload: Go2HistoryReview, _: str = Depends(require_token)) -> dict:
    import re
    if not re.fullmatch(r"[0-9a-f]{32}", event_id):
        raise HTTPException(404, detail="记录不存在。")
    return (await go2_person_request("POST", f"/history/{event_id}/review", payload.model_dump())).json()


@app.post("/api/go2/person/capture")
async def go2_person_capture(payload: Go2PersonCapture, _: str = Depends(require_token)) -> dict:
    return (await go2_person_request("POST", "/capture", payload.model_dump())).json()


@app.post("/api/go2/person/capture/select")
async def go2_person_capture_select(payload: Go2PersonCaptureSelect, _: str = Depends(require_token)) -> dict:
    return (await go2_person_request("POST", "/capture/select", payload.model_dump())).json()


@app.post("/api/go2/person/start")
async def go2_person_start(payload: Go2PersonStart, _: str = Depends(require_token)) -> dict:
    return (await go2_person_request("POST", "/start", payload.model_dump())).json()


@app.post("/api/go2/person/stop")
async def go2_person_stop(_: str = Depends(require_token)) -> dict:
    return (await go2_person_request("POST", "/stop")).json()


@app.get("/api/go2/person/preview.jpg")
async def go2_person_preview(_: str = Depends(require_token)) -> Response:
    response = await go2_person_request("GET", "/preview.jpg")
    return Response(content=response.content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/go2/person/history")
async def go2_person_history(limit: int = Query(default=20, ge=1, le=100), _: str = Depends(require_token)) -> dict:
    return (await go2_person_request("GET", f"/history?limit={limit}")).json()


@app.get("/api/go2/person/history/{history_id}/{kind}.jpg")
async def go2_person_history_image(history_id: str, kind: Literal["snapshot", "target"], _: str = Depends(require_token)) -> Response:
    from urllib.parse import quote
    response = await go2_person_request("GET", f"/history/{quote(history_id, safe='')}/{kind}.jpg")
    return Response(content=response.content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/go2/person/target.jpg")
async def go2_person_target_image(_: str = Depends(require_token)) -> Response:
    response = await go2_person_request("GET", "/target.jpg")
    return Response(content=response.content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/go2/person/camera.jpg")
async def go2_person_camera_image(_: str = Depends(require_token)) -> Response:
    response = await go2_person_request("GET", "/camera.jpg")
    return Response(content=response.content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/go2/person/patrol/status")
async def go2_person_patrol_status(_: str = Depends(require_token)) -> dict:
    return (await go2_person_request("GET", "/patrol/status")).json()


@app.get("/api/go2/person/patrol/maps")
async def go2_patrol_maps(_: str = Depends(require_token)) -> dict:
    return (await go2_person_request("GET", "/patrol/maps")).json()


@app.get("/api/go2/person/patrol/maps/{map_id}/preview.png")
async def go2_patrol_map_preview(map_id: str, _: str = Depends(require_token)) -> Response:
    import re
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}", map_id):
        raise HTTPException(400, detail="地图编号无效。")
    response = await go2_person_request("GET", f"/patrol/maps/{map_id}/preview.png")
    return Response(response.content, media_type="image/png", headers={"Cache-Control":"no-store"})


@app.post("/api/go2/person/patrol/suggest")
async def go2_patrol_suggest(payload: Go2PatrolSuggest, _: str = Depends(require_token)) -> dict:
    return (await go2_person_request("POST", "/patrol/suggest", payload.model_dump())).json()


@app.get("/api/go2/person/patrol/execution/status")
async def go2_patrol_execution_status(_: str = Depends(require_token)) -> dict:
    return (await go2_person_request("GET", "/patrol/execution/status")).json()


@app.post("/api/go2/person/patrol/execution/rehearse", status_code=202)
async def go2_patrol_execution_rehearse(payload: Go2PatrolRehearsal, _: str = Depends(require_token)) -> dict:
    return (await go2_person_request("POST", "/patrol/execution/rehearse", payload.model_dump())).json()


@app.post("/api/go2/person/patrol/execution/cancel")
async def go2_patrol_execution_cancel(_: str = Depends(require_token)) -> dict:
    return (await go2_person_request("POST", "/patrol/execution/cancel")).json()


@app.post("/api/go2/person/patrol/routes")
async def go2_person_patrol_route(payload: Go2PatrolRoute, _: str = Depends(require_token)) -> dict:
    return (await go2_person_request("POST", "/patrol/routes", payload.model_dump())).json()


@app.post("/api/go2/person/patrol/preview")
async def go2_person_patrol_preview(payload: Go2PatrolSelection, _: str = Depends(require_token)) -> dict:
    return (await go2_person_request("POST", "/patrol/preview", payload.model_dump())).json()


@app.post("/api/go2/person/patrol/rehearse")
async def go2_person_patrol_rehearse(payload: Go2PatrolSelection, _: str = Depends(require_token)) -> dict:
    return (await go2_person_request("POST", "/patrol/rehearse", payload.model_dump())).json()


@app.post("/api/go2/person/patrol/run")
async def go2_person_patrol_run(payload: Go2PatrolSelection, _: str = Depends(require_token)) -> dict:
    raise HTTPException(status_code=409, detail="实机巡航测试已暂停，仅可检查观察点与演练流程。")


@app.get("/api/go2/person/scan/status")
async def go2_person_scan_status(_: str = Depends(require_token)) -> dict:
    return (await go2_person_request("GET", "/scan/status")).json()


@app.post("/api/go2/person/scan/start")
async def go2_person_scan_start(payload: Go2ScanStart, _: str = Depends(require_token)) -> dict:
    return (await go2_person_request("POST", "/scan/start", payload.model_dump())).json()


@app.post("/api/go2/person/scan/stop")
async def go2_person_scan_stop(_: str = Depends(require_token)) -> dict:
    return (await go2_person_request("POST", "/scan/stop")).json()


@app.get("/api/go2/person/helmet/status")
async def go2_helmet_status(_: str = Depends(require_token)) -> dict:
    return (await go2_person_request("GET", "/helmet/status")).json()


@app.post("/api/go2/person/helmet/start")
async def go2_helmet_start(_: str = Depends(require_token)) -> dict:
    return (await go2_person_request("POST", "/helmet/start")).json()


@app.post("/api/go2/person/helmet/stop")
async def go2_helmet_stop(_: str = Depends(require_token)) -> dict:
    return (await go2_person_request("POST", "/helmet/stop")).json()


@app.get("/api/go2/person/helmet/records")
async def go2_helmet_records(_: str = Depends(require_token)) -> dict:
    return (await go2_person_request("GET", "/helmet/records")).json()


@app.get("/api/go2/person/helmet/export.json")
async def go2_helmet_export(_: str = Depends(require_token)) -> Response:
    response = await go2_person_request("GET", "/helmet/export.json")
    return Response(response.content, media_type="application/json", headers={"Cache-Control":"no-store"})


@app.get("/api/go2/person/helmet/preview.jpg")
async def go2_helmet_preview(_: str = Depends(require_token)) -> Response:
    response = await go2_person_request("GET", "/helmet/preview.jpg")
    return Response(response.content, media_type="image/jpeg", headers={"Cache-Control":"no-store"})


@app.get("/api/go2/person/helmet/records/{event_id}/{kind}.jpg")
async def go2_helmet_image(event_id: str, kind: Literal["snapshot","crop"], _: str = Depends(require_token)) -> Response:
    if not re.fullmatch(r"[0-9a-f]{32}",event_id):raise HTTPException(404,"记录不存在。")
    response = await go2_person_request("GET", f"/helmet/records/{event_id}/{kind}.jpg")
    return Response(response.content, media_type="image/jpeg", headers={"Cache-Control":"no-store"})


frontend_dist = settings.frontend_dist_dir.resolve()
if frontend_dist.exists():
    assets_dir = frontend_dist / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/{full_path:path}")
async def spa(full_path: str):
    index_path = frontend_dist / "index.html"
    requested = frontend_dist / full_path
    if full_path and requested.exists() and requested.is_file():
        return FileResponse(requested)
    if index_path.exists():
        return FileResponse(index_path)
    return JSONResponse(
        {
            "message": "frontend build not found",
            "expected": str(index_path),
            "hint": "run npm install && npm run build in dashboard_platform/web",
        },
        status_code=503,
    )
