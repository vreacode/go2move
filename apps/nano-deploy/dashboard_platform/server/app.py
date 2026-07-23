from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
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
