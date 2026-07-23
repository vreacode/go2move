from __future__ import annotations

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from typing import AsyncIterator

import httpx
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse

from .config import AgentSettings
from .battery import BatteryManager
from camera.siyi_zt6 import SiyiZt6Client
from .logs import LogBuffer
from .navigation import NavigationManager
from .motion import MotionControlManager
from .pose import RobotPoseManager
from .capture import CaptureManager
from .runtime import BaseBringupManager, CommandRunner, DevScreenRecordingManager, MapManager, MediaManager, ModelProcessManager, StatusCollector
from .sensors import SensorBridgeManager
from .schemas import CaptureStartRequest, ControlRequest, CruiseStartRequest, CruiseSteeringRequest, GimbalPulseRequest, LoginlessAck, MediaRecordingStartRequest, MediaSnapshotRequest, ModelStartRequest, MotionArmRequest, MotionModeRequest, MotionOverrideRequest, NavigationPointRequest, PresetRequest, WebRTCOffer
from .webrtc import WebRTCManager
from .preview_cache import PreviewCacheManager
from tools.camera_adapter import open_camera

settings = AgentSettings()
log_buffer = LogBuffer(settings.log_buffer_size)
model_manager = ModelProcessManager(settings, log_buffer)
sensor_manager = SensorBridgeManager(settings, log_buffer)
pose_manager = RobotPoseManager(settings, log_buffer)
motion_manager = MotionControlManager(settings, log_buffer)
battery_manager = BatteryManager(settings, log_buffer)
base_manager = BaseBringupManager(settings, log_buffer, motion_manager, battery_manager)
command_runner = CommandRunner(settings, log_buffer, model_manager)
navigation_manager = NavigationManager(settings, log_buffer)
map_manager = MapManager(settings)
media_manager = MediaManager(settings, log_buffer)
capture_manager = CaptureManager(settings, log_buffer)
dev_screen_recording_manager = DevScreenRecordingManager(settings, log_buffer)
preview_cache_manager = PreviewCacheManager()
status_collector = StatusCollector(settings, model_manager, base_manager, sensor_manager, pose_manager, motion_manager, battery_manager)
webrtc_manager = WebRTCManager(settings, log_buffer)
siyi_gimbal_lock = asyncio.Lock()


def verify_token(authorization: str | None = Header(default=None)) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    prefix = "Bearer "
    token = authorization[len(prefix):].strip() if authorization.startswith(prefix) else authorization.strip()
    if token != settings.agent_token:
        raise HTTPException(status_code=403, detail="invalid token")


def reject_go2_data_only(feature: str) -> None:
    """Hard-stop hardware actions for the Go2 first-stage data-only profile."""
    if settings.go2_data_only:
        raise HTTPException(status_code=409, detail=f"{feature} is disabled in Go2 data-only profile")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    log_buffer.add("system", "info", f"{settings.app_name} starting on {settings.agent_host}:{settings.agent_port}")
    await motion_manager.start()
    await base_manager.start()
    await pose_manager.start()
    await sensor_manager.start()
    await battery_manager.start()
    yield
    await battery_manager.stop()
    await navigation_manager.stop()
    await capture_manager.stop()
    await sensor_manager.stop()
    await pose_manager.stop()
    await base_manager.stop()
    await motion_manager.stop()
    await webrtc_manager.close()
    preview_cache_manager.stop()


app = FastAPI(title=settings.app_name, lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/status", dependencies=[Depends(verify_token)])
async def status() -> dict:
    payload = status_collector.collect()
    return payload.model_dump()


@app.get("/camera/status", dependencies=[Depends(verify_token)])
async def camera_status() -> dict:
    """Read the ZT6 state without blocking the Agent event loop."""

    if not settings.siyi_zt6_enabled:
        return {
            "enabled": False,
            "motion_enabled": False,
            "connected": False,
            "target": f"{settings.siyi_zt6_host}:{settings.siyi_zt6_port}",
            "error": "SIYI ZT6 status polling is disabled",
        }

    def query() -> dict:
        client = SiyiZt6Client(
            settings.siyi_zt6_host,
            settings.siyi_zt6_port,
            settings.siyi_zt6_query_timeout_seconds,
        )
        return client.query_snapshot()

    try:
        snapshot = await asyncio.to_thread(query)
    except Exception as exc:
        return {
            "enabled": True,
            "connected": False,
            "target": f"{settings.siyi_zt6_host}:{settings.siyi_zt6_port}",
            "error": str(exc),
        }
    return {
        "enabled": True,
        "motion_enabled": settings.siyi_zt6_motion_enabled,
        "connected": True,
        "error": None,
        **snapshot,
    }


@app.post("/camera/gimbal/pulse", dependencies=[Depends(verify_token)])
async def camera_gimbal_pulse(request: GimbalPulseRequest) -> dict:
    """Run one bounded gimbal pulse and always issue a zero-speed stop."""

    if settings.go2_data_only or not settings.siyi_zt6_motion_enabled:
        raise HTTPException(status_code=409, detail="SIYI ZT6 motion control is disabled")
    if max(abs(request.yaw_speed), abs(request.pitch_speed)) > settings.siyi_zt6_motion_max_speed:
        raise HTTPException(
            status_code=400,
            detail=f"gimbal speed exceeds configured limit {settings.siyi_zt6_motion_max_speed}",
        )
    if request.duration_ms > settings.siyi_zt6_motion_max_duration_ms:
        raise HTTPException(
            status_code=400,
            detail=f"gimbal duration exceeds configured limit {settings.siyi_zt6_motion_max_duration_ms} ms",
        )
    if request.yaw_speed == 0 and request.pitch_speed == 0:
        raise HTTPException(status_code=400, detail="gimbal pulse must include a non-zero speed")

    def pulse() -> dict:
        client = SiyiZt6Client(
            settings.siyi_zt6_host,
            settings.siyi_zt6_port,
            settings.siyi_zt6_query_timeout_seconds,
        )
        turn_ack = False
        stop_ack = False
        try:
            turn_ack = client.turn(request.yaw_speed, request.pitch_speed)
            if turn_ack:
                time.sleep(request.duration_ms / 1000.0)
        finally:
            stop_ack = client.stop()
        return {
            "ok": turn_ack and stop_ack,
            "turn_ack": turn_ack,
            "stop_ack": stop_ack,
            "yaw_speed": request.yaw_speed,
            "pitch_speed": request.pitch_speed,
            "duration_ms": request.duration_ms,
        }

    async with siyi_gimbal_lock:
        try:
            return await asyncio.to_thread(pulse)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"ZT6 gimbal command failed: {exc}") from exc


@app.post("/camera/gimbal/stop", dependencies=[Depends(verify_token)])
async def camera_gimbal_stop() -> dict:
    """Issue a zero-speed stop; allowed even when motion pulses are disabled."""

    reject_go2_data_only("SIYI ZT6 control")
    if not settings.siyi_zt6_enabled:
        raise HTTPException(status_code=409, detail="SIYI ZT6 is disabled")

    def stop() -> bool:
        client = SiyiZt6Client(
            settings.siyi_zt6_host,
            settings.siyi_zt6_port,
            settings.siyi_zt6_query_timeout_seconds,
        )
        return client.stop()

    async with siyi_gimbal_lock:
        try:
            ack = await asyncio.to_thread(stop)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"ZT6 gimbal stop failed: {exc}") from exc
    return {"ok": ack, "stop_ack": ack}


@app.post("/camera/gimbal/center", dependencies=[Depends(verify_token)])
async def camera_gimbal_center() -> dict:
    """Trigger the ZT6 native yaw-and-pitch center command."""

    reject_go2_data_only("SIYI ZT6 control")
    if not settings.siyi_zt6_enabled:
        raise HTTPException(status_code=409, detail="SIYI ZT6 is disabled")

    def center() -> bool:
        client = SiyiZt6Client(
            settings.siyi_zt6_host,
            settings.siyi_zt6_port,
            settings.siyi_zt6_query_timeout_seconds,
        )
        return client.center()

    async with siyi_gimbal_lock:
        try:
            ack = await asyncio.to_thread(center)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"ZT6 gimbal center failed: {exc}") from exc
    return {"ok": ack, "center_ack": ack}


@app.get("/models", dependencies=[Depends(verify_token)])
async def models() -> list[dict]:
    return [model.model_dump() for model in settings.models]


@app.post("/model/start", dependencies=[Depends(verify_token)])
async def start_model(request: ModelStartRequest) -> dict:
    runtime = await model_manager.start(request)
    return {"ok": True, "model_status": runtime.model_dump()}


@app.post("/model/stop", dependencies=[Depends(verify_token)])
async def stop_model() -> dict:
    runtime = await model_manager.stop()
    return {"ok": True, "model_status": runtime.model_dump()}


@app.post("/model/restart", dependencies=[Depends(verify_token)])
async def restart_model(request: ModelStartRequest) -> dict:
    runtime = await model_manager.restart(request)
    return {"ok": True, "model_status": runtime.model_dump()}


@app.get("/model/status", dependencies=[Depends(verify_token)])
async def model_status() -> dict:
    return model_manager.runtime.model_dump()


@app.post("/control/send", dependencies=[Depends(verify_token)])
async def control_send(request: ControlRequest) -> LoginlessAck:
    reject_go2_data_only("motion control")
    try:
        navigation_running = status_collector.collect().navigation_running
        await motion_manager.send_control(request.command, request.speed, navigation_running=navigation_running)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        detail = f"control send failed: {type(exc).__name__}: {exc}"
        motion_manager.last_error = detail
        log_buffer.add("control", "error", detail)
        raise HTTPException(status_code=500, detail=detail) from exc
    return LoginlessAck(status="acked", detail=f"{request.command}:{request.speed}")


@app.post("/control/cruise/start", dependencies=[Depends(verify_token)])
async def control_cruise_start(request: CruiseStartRequest) -> dict:
    reject_go2_data_only("cruise control")
    try:
        navigation_running = status_collector.collect().navigation_running
        state = await motion_manager.start_cruise(request.speed, navigation_running=navigation_running, auto_arm=request.auto_arm)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return state.model_dump()


@app.post("/control/cruise/steer", dependencies=[Depends(verify_token)])
async def control_cruise_steer(request: CruiseSteeringRequest) -> dict:
    reject_go2_data_only("cruise control")
    try:
        navigation_running = status_collector.collect().navigation_running
        state = await motion_manager.set_cruise_steering(request.direction, navigation_running=navigation_running)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return state.model_dump()


@app.post("/control/cruise/heartbeat", dependencies=[Depends(verify_token)])
async def control_cruise_heartbeat() -> dict:
    reject_go2_data_only("cruise control")
    try:
        navigation_running = status_collector.collect().navigation_running
        state = await motion_manager.cruise_heartbeat(navigation_running=navigation_running)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return state.model_dump()


@app.post("/control/cruise/stop", dependencies=[Depends(verify_token)])
async def control_cruise_stop() -> dict:
    reject_go2_data_only("cruise control")
    navigation_running = status_collector.collect().navigation_running
    state = await motion_manager.stop_cruise(navigation_running=navigation_running)
    return state.model_dump()


@app.get("/control/state", dependencies=[Depends(verify_token)])
async def control_state() -> dict:
    navigation_running = status_collector.collect().navigation_running
    return motion_manager.snapshot(navigation_running=navigation_running).model_dump()


@app.get("/base/status", dependencies=[Depends(verify_token)])
async def base_status() -> dict:
    return (await base_manager.refresh()).model_dump()


@app.post("/base/start", dependencies=[Depends(verify_token)])
async def base_start() -> dict:
    reject_go2_data_only("base bringup")
    return (await base_manager.start_bringup()).model_dump()


@app.post("/base/stop", dependencies=[Depends(verify_token)])
async def base_stop() -> dict:
    reject_go2_data_only("base bringup")
    await motion_manager.force_safe_lock("base stopped; reverted to dry-run and relocked control")
    return (await base_manager.stop_bringup()).model_dump()


@app.post("/control/arm", dependencies=[Depends(verify_token)])
async def control_arm(request: MotionArmRequest) -> dict:
    reject_go2_data_only("motion control")
    state = await motion_manager.set_armed(request.armed)
    return state.model_dump()


@app.post("/control/mode", dependencies=[Depends(verify_token)])
async def control_mode(request: MotionModeRequest) -> dict:
    reject_go2_data_only("motion control")
    base_state = await base_manager.refresh()
    base_ready = base_state.wheeltec_robot_online and base_state.cmd_vel_subscriber_online and base_state.odom_online
    try:
        state = await motion_manager.set_mode(request.mode, base_ready=base_ready)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        detail = f"motion mode failed: {type(exc).__name__}: {exc}"
        motion_manager.last_error = detail
        log_buffer.add("control", "error", detail)
        raise HTTPException(status_code=500, detail=detail) from exc
    return state.model_dump()


@app.post("/control/manual-override", dependencies=[Depends(verify_token)])
async def control_manual_override(request: MotionOverrideRequest) -> dict:
    reject_go2_data_only("motion control")
    navigation_running = status_collector.collect().navigation_running
    try:
        state = await motion_manager.set_manual_override(request.enabled, navigation_running=navigation_running)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return state.model_dump()


@app.post("/control/emergency-stop", dependencies=[Depends(verify_token)])
async def control_emergency_stop() -> dict:
    reject_go2_data_only("motion control")
    state = await motion_manager.emergency_stop()
    return state.model_dump()


@app.post("/preset/run", dependencies=[Depends(verify_token)])
async def run_preset(request: PresetRequest) -> LoginlessAck:
    reject_go2_data_only("hardware preset")
    variables = {
        "camera_rtsp_url": settings.visible_rtsp_url,
        "model_workdir": str(settings.model_workdir),
        "board_host": settings.board_name,
        "maps_directory": str(settings.maps_directory),
        "default_map_name": settings.default_map_name,
        "map_name": request.variables.get("map_name", settings.default_map_name),
    }
    try:
        if request.name in {"start_mapping", "start_navigation"}:
            await motion_manager.stop_cruise_if_active(navigation_running=False)
        if request.name == "start_navigation":
            try:
                await command_runner.run_command(
                    ["bash", "scripts/start_navigation_integrated.sh", "check", variables["map_name"]],
                    "start_navigation_preflight",
                )
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        result = await command_runner.run_whitelist_command(
            settings.preset_command_map,
            request.name,
            variables,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return LoginlessAck(status=result["status"], detail=request.name)


@app.get("/navigation/status", dependencies=[Depends(verify_token)])
async def navigation_status() -> dict:
    payload = navigation_manager.snapshot()
    payload["navigation_running"] = status_collector.collect().navigation_running
    return payload


@app.post("/navigation/initial-pose", dependencies=[Depends(verify_token)])
async def navigation_initial_pose(request: NavigationPointRequest) -> dict:
    reject_go2_data_only("navigation")
    if not status_collector.collect().navigation_running:
        raise HTTPException(status_code=409, detail="navigation is not running")
    try:
        return await navigation_manager.publish_initial_pose(request.x, request.y, request.yaw)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/navigation/goal", dependencies=[Depends(verify_token)])
async def navigation_goal(request: NavigationPointRequest) -> dict:
    reject_go2_data_only("navigation")
    if not status_collector.collect().navigation_running:
        raise HTTPException(status_code=409, detail="navigation is not running")
    try:
        return await navigation_manager.send_goal(request.x, request.y, request.yaw)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/navigation/goal/cancel", dependencies=[Depends(verify_token)])
async def navigation_goal_cancel() -> dict:
    reject_go2_data_only("navigation")
    return await navigation_manager.cancel_goal()


@app.get("/maps", dependencies=[Depends(verify_token)])
async def maps() -> list[dict]:
    return [item.model_dump() for item in map_manager.list_maps()]


@app.get("/maps/{map_name}", dependencies=[Depends(verify_token)])
async def map_metadata(map_name: str) -> dict:
    try:
        return map_manager.get_map(map_name).model_dump()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"map '{map_name}' not found") from exc


@app.get("/maps/{map_name}/pgm", dependencies=[Depends(verify_token)])
async def map_image(map_name: str) -> FileResponse:
    try:
        image_path = map_manager.get_map_image_path(map_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"map image for '{map_name}' not found") from exc
    return FileResponse(Path(image_path), media_type="image/x-portable-graymap")


@app.get("/media/status", dependencies=[Depends(verify_token)])
async def media_status() -> dict:
    return media_manager.status().model_dump()


@app.get("/capture/status", dependencies=[Depends(verify_token)])
async def capture_status() -> dict:
    return capture_manager.status().model_dump()


@app.get("/capture/gallery", dependencies=[Depends(verify_token)])
async def capture_gallery(page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100)) -> list[dict]:
    try:
        return [item.model_dump() for item in capture_manager.list_gallery(page=page, page_size=page_size)]
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/capture/gallery/{group}/latest", dependencies=[Depends(verify_token)])
async def capture_gallery_latest(group: str) -> FileResponse:
    try:
        path = capture_manager.gallery_latest_frame_path(group)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if path is None:
        raise HTTPException(status_code=404, detail="capture group or frame not found")
    return FileResponse(path, media_type="image/jpeg", filename=path.name)


@app.get("/capture/gallery/{group}/frames", dependencies=[Depends(verify_token)])
async def capture_gallery_frames(group: str) -> list[dict]:
    try:
        return [frame.model_dump() for frame in capture_manager.gallery_frames(group)]
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/capture/gallery/{group}/frames/{frame_name}", dependencies=[Depends(verify_token)])
async def capture_gallery_frame(group: str, frame_name: str) -> FileResponse:
    try:
        path = capture_manager.gallery_frame_path(group, frame_name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if path is None:
        raise HTTPException(status_code=404, detail="capture frame not found")
    return FileResponse(path, media_type="image/jpeg", filename=path.name)


@app.post("/capture/start", dependencies=[Depends(verify_token)])
async def capture_start(request: CaptureStartRequest) -> dict:
    if request.stream_mode == "ir_raw" and not settings.ir_camera_enabled:
        raise HTTPException(status_code=409, detail="IR camera is disabled for this device profile")
    try:
        return (await capture_manager.start(request)).model_dump()
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/capture/stop", dependencies=[Depends(verify_token)])
async def capture_stop() -> dict:
    return (await capture_manager.stop()).model_dump()


@app.get("/capture/latest", dependencies=[Depends(verify_token)])
async def capture_latest() -> FileResponse:
    path = capture_manager.latest_frame_path()
    if path is None:
        raise HTTPException(status_code=404, detail="no captured frame is available")
    return FileResponse(path, media_type="image/jpeg", filename=path.name)


@app.post("/media/recording/start", dependencies=[Depends(verify_token)])
async def media_recording_start(request: MediaRecordingStartRequest) -> dict:
    if request.stream_mode == "ir_raw" and not settings.ir_camera_enabled:
        raise HTTPException(status_code=409, detail="IR camera is disabled for this device profile")
    try:
        return (await media_manager.start_recording(request.stream_mode)).model_dump()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/media/recording/stop", dependencies=[Depends(verify_token)])
async def media_recording_stop() -> dict:
    return (await media_manager.stop_recording()).model_dump()


@app.post("/media/snapshot", dependencies=[Depends(verify_token)])
async def media_snapshot(request: MediaSnapshotRequest) -> dict:
    if request.stream_mode == "ir_raw" and not settings.ir_camera_enabled:
        raise HTTPException(status_code=409, detail="IR camera is disabled for this device profile")
    try:
        return (await media_manager.take_snapshot(request.stream_mode)).model_dump()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/media/gallery", dependencies=[Depends(verify_token)])
async def media_gallery(page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100)) -> list[dict]:
    return [item.model_dump() for item in media_manager.list_gallery(page=page, page_size=page_size)]


@app.get("/media/files/{storage_kind}/{filename}", dependencies=[Depends(verify_token)])
async def media_file(storage_kind: str, filename: str) -> FileResponse:
    try:
        path = media_manager.get_media_file(storage_kind, filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"media file '{filename}' not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    media_type = "video/mp4" if path.suffix.lower() == ".mp4" else "image/jpeg"
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.get("/dev-recording/status", dependencies=[Depends(verify_token)])
async def dev_recording_status() -> dict:
    return dev_screen_recording_manager.status().model_dump()


@app.post("/dev-recording/start", dependencies=[Depends(verify_token)])
async def dev_recording_start() -> dict:
    try:
        return (await dev_screen_recording_manager.start()).model_dump()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/dev-recording/stop", dependencies=[Depends(verify_token)])
async def dev_recording_stop() -> dict:
    return (await dev_screen_recording_manager.stop()).model_dump()


@app.get("/logs/recent", dependencies=[Depends(verify_token)])
async def logs_recent(channel: str | None = Query(default=None)) -> list[dict]:
    return log_buffer.recent(channel=channel)


@app.get("/logs/stream", dependencies=[Depends(verify_token)])
async def logs_stream(channel: str | None = Query(default=None)) -> StreamingResponse:
    async def generate() -> AsyncIterator[str]:
        queue = log_buffer.subscribe()
        try:
            for item in log_buffer.recent(channel=channel):
                yield json.dumps(item, ensure_ascii=True) + "\n"
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield json.dumps({"channel": "system", "level": "debug", "message": "heartbeat"}) + "\n"
                    continue
                if channel and event.channel != channel:
                    continue
                yield json.dumps(event.as_dict(), ensure_ascii=True) + "\n"
        finally:
            log_buffer.unsubscribe(queue)

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.get("/sensors/stream", dependencies=[Depends(verify_token)])
async def sensors_stream() -> StreamingResponse:
    async def generate() -> AsyncIterator[str]:
        queue = sensor_manager.stream.subscribe()
        try:
            for item in sensor_manager.stream.recent():
                yield json.dumps(item, ensure_ascii=True) + "\n"
            while True:
                payload = await queue.get()
                yield json.dumps(payload, ensure_ascii=True) + "\n"
        finally:
            sensor_manager.stream.unsubscribe(queue)

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.get("/pose/current", dependencies=[Depends(verify_token)])
async def pose_current() -> dict:
    return pose_manager.state.last_frame


@app.get("/pose/stream", dependencies=[Depends(verify_token)])
async def pose_stream() -> StreamingResponse:
    async def generate() -> AsyncIterator[str]:
        queue = pose_manager.stream.subscribe()
        try:
            for item in pose_manager.stream.recent():
                yield json.dumps(item, ensure_ascii=True) + "\n"
            while True:
                payload = await queue.get()
                yield json.dumps(payload, ensure_ascii=True) + "\n"
        finally:
            pose_manager.stream.unsubscribe(queue)

    return StreamingResponse(generate(), media_type="application/x-ndjson")


@app.post("/stream/webrtc-offer", dependencies=[Depends(verify_token)])
async def stream_webrtc_offer(offer: WebRTCOffer) -> dict:
    if not (settings.visible_rtsp_url or settings.model_default_source):
        raise HTTPException(status_code=400, detail="visible camera rtsp url is not configured")
    if offer.stream_mode == "ir_raw" and not settings.ir_camera_enabled:
        raise HTTPException(status_code=409, detail="IR camera is disabled for this device profile")
    if offer.stream_mode == "ir_raw" and not settings.ir_rtsp_url:
        raise HTTPException(status_code=409, detail="ir stream is not configured")
    if offer.stream_mode == "annotated" and not (settings.snapshot_path / "latest.jpg").exists():
        raise HTTPException(status_code=409, detail="annotated stream is not ready; start a model first")
    answer = await webrtc_manager.create_answer(offer.sdp, offer.type, offer.stream_mode)
    return answer


@app.get("/preview.mjpeg", dependencies=[Depends(verify_token)])
async def preview_mjpeg(mode: str = Query(default="visible_raw")) -> StreamingResponse:
    if not settings.allow_mjpeg_fallback:
        raise HTTPException(status_code=404, detail="fallback disabled")

    if mode == "ir_raw" and not settings.ir_camera_enabled:
        raise HTTPException(status_code=409, detail="IR camera is disabled for this device profile")
    if mode == "ir_raw":
        source_url = settings.ir_rtsp_url
    else:
        source_url = settings.visible_rtsp_url
    if not source_url:
        raise HTTPException(status_code=409, detail=f"{mode} is not configured")

    executor: ThreadPoolExecutor | None = None
    try:
        import cv2
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="go2-mjpeg-camera")
        capture = await loop.run_in_executor(executor, open_camera, source_url, settings.camera_source_type)
        opened = await loop.run_in_executor(executor, capture.isOpened)
    except Exception as exc:
        if executor is not None:
            with suppress(Exception):
                executor.shutdown(wait=False)
        raise HTTPException(status_code=502, detail=f"camera adapter unavailable: {exc}") from exc
    if not opened:
        with suppress(Exception):
            await loop.run_in_executor(executor, capture.release)
            executor.shutdown(wait=False)
        raise HTTPException(status_code=502, detail="camera adapter opened without an active video source")

    async def generate() -> AsyncIterator[bytes]:
        nonlocal capture
        failures = 0
        try:
            while True:
                ok, frame = await loop.run_in_executor(executor, capture.read)
                if not ok or frame is None:
                    failures += 1
                    if failures >= 5:
                        with suppress(Exception):
                            await loop.run_in_executor(executor, capture.release)
                        try:
                            capture = await loop.run_in_executor(
                                executor, open_camera, source_url, settings.camera_source_type
                            )
                            opened = await loop.run_in_executor(executor, capture.isOpened)
                        except Exception:
                            opened = False
                        failures = 0 if opened else failures
                    await asyncio.sleep(0.05)
                    continue
                failures = 0
                jpeg_bytes = await loop.run_in_executor(
                    executor,
                    _encode_preview_frame,
                    cv2,
                    frame,
                    settings.webrtc_output_width,
                    settings.webrtc_output_height,
                )
                if not jpeg_bytes:
                    continue
                yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(jpeg_bytes)).encode() + b"\r\n\r\n" + jpeg_bytes + b"\r\n"
                await asyncio.sleep(0.04)
        finally:
            with suppress(Exception):
                await loop.run_in_executor(executor, capture.release)
            executor.shutdown(wait=False)

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )


def _encode_preview_frame(cv2, frame, max_width: int, max_height: int) -> bytes | None:
    """Downscale a preview frame and encode it as one complete JPEG."""

    if frame is None or not getattr(frame, "size", 0):
        return None
    height, width = frame.shape[:2]
    scale = min(max(int(max_width), 2) / width, max(int(max_height), 2) / height, 1.0)
    if scale < 1.0:
        output_width = max(2, int(width * scale) // 2 * 2)
        output_height = max(2, int(height * scale) // 2 * 2)
        frame = cv2.resize(frame, (output_width, output_height), interpolation=cv2.INTER_AREA)
    ok, jpeg = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
    return jpeg.tobytes() if ok else None


@app.get("/preview.jpg", dependencies=[Depends(verify_token)])
async def preview_jpeg(mode: str = Query(default="visible_raw")) -> Response:
    """Return one complete preview JPEG for browsers that reset MJPEG images."""

    if mode == "ir_raw" and not settings.ir_camera_enabled:
        raise HTTPException(status_code=409, detail="IR camera is disabled for this device profile")
    source_url = settings.ir_rtsp_url if mode == "ir_raw" else settings.visible_rtsp_url
    if not source_url:
        raise HTTPException(status_code=409, detail=f"{mode} is not configured")

    cache = preview_cache_manager.get(
        mode,
        source_url,
        settings.camera_source_type,
        settings.webrtc_output_width,
        settings.webrtc_output_height,
    )
    loop = asyncio.get_running_loop()
    jpeg_bytes = await loop.run_in_executor(None, cache.get, 5.0)
    if not jpeg_bytes:
        raise HTTPException(status_code=504, detail="camera preview cache is not ready")
    return Response(
        content=jpeg_bytes,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )
