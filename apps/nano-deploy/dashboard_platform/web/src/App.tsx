import { useEffect, useMemo, useRef, useState } from "react";
import type { MouseEvent, ReactNode } from "react";
import {
  ArrowsPointingOutIcon,
  ArrowPathIcon,
  BoltIcon,
  ExclamationTriangleIcon,
  PauseCircleIcon,
  PlayCircleIcon,
  StopCircleIcon,
  ChevronUpIcon,
  ChevronDownIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  StopIcon,
  SunIcon,
  MoonIcon,
  SwatchIcon,
} from "@heroicons/react/24/outline";
import { Panel } from "./components/Panel";
import { SidebarMenu, type SidebarTab } from "./components/SidebarMenu";
import { VideoStream } from "./components/VideoStream";
import { API_BASE, apiFetch, classNames, clearToken, formatNumber, loadToken, saveToken, wsUrl } from "./lib";
import type { ConnectionState, LogItem, MapEntry, MapMetadata, MapPoint, MediaItem, MediaStatus, NavigationState, SiyiCameraStatus, StreamStats, SystemStatus } from "./types";

type LoginResponse = { token: string; username: string };

const workspaceMeta: Record<SidebarTab, { eyebrow: string; title: string; description: string }> = {
  live: { eyebrow: "LIVE OPERATIONS", title: "实时监控", description: "查看双光视频、模型状态与车辆即时操控。" },
  navigation: { eyebrow: "MAP & NAVIGATION", title: "建图导航", description: "管理地图、建图流程与自主导航任务。" },
  perception: { eyebrow: "MEDIA & INTELLIGENCE", title: "媒体与感知", description: "管理录像、拍照、检测事件与系统日志。" },
  diagnostics: { eyebrow: "SYSTEM HEALTH", title: "系统诊断", description: "核对底盘链路、设备资源和控制模式。" },
};

const workspaceTabs: SidebarTab[] = ["live", "navigation", "perception", "diagnostics"];

function tabFromHash(hash: string): SidebarTab {
  const candidate = hash.replace(/^#/, "") as SidebarTab;
  return workspaceTabs.includes(candidate) ? candidate : "live";
}

const speedLevels = ["low", "medium", "high"] as const;
const logFilters = ["all", "system", "model", "control"] as const;

function videoModeLabel(mode: "visible_raw" | "ir_raw" | "annotated") {
  if (mode === "visible_raw") return "可见光";
  if (mode === "ir_raw") return "红外";
  return "检测结果";
}

function describeError(error: unknown) {
  const raw = String(error ?? "").replace(/^Error:\s*/i, "").trim();
  if (!raw) return "操作失败，请查看日志。";
  if (raw.includes("invalid credentials")) return "登录失败：用户名或密码不正确。";
  if (raw.includes("invalid token") || raw.includes("missing bearer token")) return "鉴权失败：请重新登录。";
  if (raw.includes("manual control is locked")) return "手动控制已锁定：请先解锁控制。";
  if (raw.includes("navigation is running")) return "导航正在运行：如需人工接管，请先启用人工接管。";
  if (raw.includes("base is not ready")) return "底盘未就绪：请先启动底盘，并确认 wheeltec_robot、/cmd_vel_safe、/odom 都在线。";
  if (raw.includes("annotated stream is not ready")) return "检测结果视频未就绪：请先启动模型。";
  if (raw.includes("recording is already running")) return "录像已经在进行中：请先停止当前录像。";
  if (raw.includes("recording is disabled")) return "录像功能当前未启用。";
  if (raw.includes("legacy web recorder is not integrated")) return "旧的 localhost:8000 录像页面未接入当前 Dashboard，请使用当前媒体面板。";
  if (raw.includes("snapshot capture failed")) return "拍照失败：未能抓取当前视频源。";
  if (raw.includes("snapshot capture timed out")) return "拍照超时：请检查 RTSP 源状态。";
  if (raw.includes("map image") && raw.includes("not found")) return "地图图像不存在：请确认该地图已正确保存。";
  if (raw.includes("Map not found:")) return "启动导航失败：所选地图不存在，请先确认地图名称或重新保存地图。";
  if (raw.includes("Navigation params file not found:")) return "启动导航失败：导航参数文件缺失，请检查部署路径。";
  if (raw.includes("wheeltec_robot not ready")) return "启动导航失败：底盘节点未就绪，请先启动底盘。";
  if (raw.includes("/odom not ready")) return "启动导航失败：里程计未就绪，请先确认底盘状态。";
  if (raw.includes("/c16/lslidar_point_cloud not ready")) return "启动导航失败：雷达点云未就绪，请检查雷达驱动。";
  if (raw.includes("/map is not ready")) return "保存地图失败：当前没有可用的 /map，请先启动建图。";
  if (raw.includes("navigation is not running")) return "导航尚未运行：请先启动导航，再发布初始位姿或目标点。";
  if (raw.includes("a navigation goal is already active")) return "已有导航目标执行中：请先取消当前目标。";
  if (raw.includes("timed out")) return "操作超时：请稍后重试，并检查后端日志。";
  if (raw.includes("stream offer failed")) return "视频协商失败：请检查 Agent、RTSP 和网络状态。";
  return raw;
}

function App() {
  const [token, setToken] = useState(loadToken());
  const [activeTab, setActiveTab] = useState<SidebarTab>(() => tabFromHash(window.location.hash));
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [cameraStatus, setCameraStatus] = useState<SiyiCameraStatus | null>(null);
  const [gimbalPending, setGimbalPending] = useState(false);
  const [logs, setLogs] = useState<LogItem[]>([]);
  const [selectedModel, setSelectedModel] = useState("helmet");
  const [source, setSource] = useState("");
  const [parkingEnabled, setParkingEnabled] = useState(false);
  const [parkingZoneInput, setParkingZoneInput] = useState("0.25, 0.25, 0.75, 0.90");
  const [parkingDwellInput, setParkingDwellInput] = useState("5");
  const [videoSourceMode, setVideoSourceMode] = useState<"visible_raw" | "ir_raw" | "annotated">("visible_raw");
  const [speed, setSpeed] = useState<(typeof speedLevels)[number]>("medium");
  const [commandState, setCommandState] = useState("idle");
  const [logFilter, setLogFilter] = useState<(typeof logFilters)[number]>("all");
  const [videoState, setVideoState] = useState<ConnectionState>("idle");
  const [maps, setMaps] = useState<MapEntry[]>([]);
  const [selectedMapName, setSelectedMapName] = useState("");
  const [mapMetadata, setMapMetadata] = useState<MapMetadata | null>(null);
  const [mapImageUrl, setMapImageUrl] = useState("");
  const [mapNameInput, setMapNameInput] = useState("");
  const [mapLoading, setMapLoading] = useState(false);
  const [navigationState, setNavigationState] = useState<NavigationState | null>(null);
  const [mapPointMode, setMapPointMode] = useState<"none" | "initial" | "goal">("none");
  const [initialPoint, setInitialPoint] = useState<MapPoint | null>(null);
  const [goalPoint, setGoalPoint] = useState<MapPoint | null>(null);
  const [initialYawInput, setInitialYawInput] = useState("0");
  const [goalYawInput, setGoalYawInput] = useState("0");
  const [navigationPending, setNavigationPending] = useState<"" | "initial" | "goal" | "cancel">("");
  const [mediaStatus, setMediaStatus] = useState<MediaStatus | null>(null);
  const [mediaItems, setMediaItems] = useState<MediaItem[]>([]);
  const [mediaPending, setMediaPending] = useState<"" | "record-start" | "record-stop" | "snapshot">("");
  const [galleryLoading, setGalleryLoading] = useState(false);
  const [mediaTab, setMediaTab] = useState<"gallery" | "events">("gallery");
  const [loginForm, setLoginForm] = useState({ username: "admin", password: "" });
  const [error, setError] = useState("");
  const [loginPending, setLoginPending] = useState(false);
  const [modelPending, setModelPending] = useState<"" | "start" | "restart" | "stop">("");
  const [presetPending, setPresetPending] = useState("");
  const [controlPending, setControlPending] = useState(false);
  const [cruisePending, setCruisePending] = useState<"" | "start" | "stop" | "steer">("");
  const [armPending, setArmPending] = useState(false);
  const [overridePending, setOverridePending] = useState(false);
  const [basePending, setBasePending] = useState<"" | "start" | "stop">("");
  const [modePending, setModePending] = useState<"" | "dry_run" | "real">("");
  const [refreshingMaps, setRefreshingMaps] = useState(false);
  const [useFallback, setUseFallback] = useState(false);
  const cruiseSteeringRequestRef = useRef<Promise<void> | null>(null);
  const cruiseSteeringDesiredRef = useRef<"center" | "left" | "right">("center");

  function selectTab(tab: SidebarTab) {
    setActiveTab(tab);
    if (window.location.hash !== `#${tab}`) {
      window.history.pushState({ workspace: tab }, "", `#${tab}`);
    }
  }

  const activeWorkspace = workspaceMeta[activeTab];

  useEffect(() => {
    const syncWorkspaceFromUrl = () => setActiveTab(tabFromHash(window.location.hash));
    window.addEventListener("hashchange", syncWorkspaceFromUrl);
    window.addEventListener("popstate", syncWorkspaceFromUrl);
    if (window.location.hash !== `#${activeTab}`) {
      window.history.replaceState({ workspace: activeTab }, "", `#${activeTab}`);
    }
    return () => {
      window.removeEventListener("hashchange", syncWorkspaceFromUrl);
      window.removeEventListener("popstate", syncWorkspaceFromUrl);
    };
  }, [activeTab]);

  const filteredLogs = useMemo(() => {
    if (logFilter === "all") return logs;
    return logs.filter((item) => item.channel === logFilter);
  }, [logs, logFilter]);

  const manualControlBlocked =
    !!status?.motion_state &&
    ((status.motion_state.require_manual_arm && !status.motion_state.armed) || status.motion_state.nav_lockout);
  const cruiseActive = status?.motion_state?.cruise_active ?? false;
  const cruiseSteering = status?.motion_state?.cruise_steering ?? "center";
  const baseReady =
    !!status?.base_status?.wheeltec_robot_online &&
    !!status?.base_status?.cmd_vel_subscriber_online &&
    !!status?.base_status?.odom_online;
  const controlHint =
    !status?.motion_state
      ? "控制状态加载中。"
      : status.motion_state.last_error
        ? describeError(status.motion_state.last_error)
        : status.motion_state.require_manual_arm && !status.motion_state.armed
          ? "手动控制当前处于锁定状态。点击“解锁控制”后才能发送前进、后退和转向命令。"
          : status.motion_state.nav_lockout && !status.motion_state.manual_override
            ? "导航运行中，手动控制已被锁定。需要人工接管时，请点击“启用人工接管”。"
            : status.motion_state.mode !== "real"
              ? "当前为 dry-run 模式，控制命令不会真正下发到底盘。"
              : "当前可以进行手动控制。";

  useEffect(() => {
    if (!token) return;
    const statusSocket = new WebSocket(wsUrl("/ws/status", token));
    const logSocket = new WebSocket(wsUrl("/ws/logs", token));

    statusSocket.onmessage = (event) => {
      const payload = JSON.parse(event.data) as SystemStatus;
      setStatus(payload);
      if (!source && payload.current_video_source) setSource(payload.current_video_source);
      if (payload.current_model) setSelectedModel(payload.current_model);
      if (payload.current_video_mode === "visible_raw" && videoSourceMode === "annotated" && !payload.model_status?.running) {
        setVideoSourceMode("visible_raw");
      }
    };

    logSocket.onmessage = (event) => {
      const item = JSON.parse(event.data) as LogItem;
      setLogs((prev) => [...prev.slice(-299), item]);
    };

    const onDisconnect = () => setVideoState((prev) => (prev === "connected" ? "reconnecting" : prev));
    statusSocket.onclose = onDisconnect;
    logSocket.onclose = onDisconnect;

    void apiFetch<SystemStatus>("/api/system/status", token)
      .then((payload) => {
        setStatus(payload);
        if (!source && payload.current_video_source) setSource(payload.current_video_source);
        if (payload.current_model) setSelectedModel(payload.current_model);
        if (!selectedMapName && payload.current_map_name) {
          setSelectedMapName(payload.current_map_name);
          setMapNameInput(payload.current_map_name);
        }
        if (payload.current_video_mode === "visible_raw" && videoSourceMode === "annotated" && !payload.model_status?.running) {
          setVideoSourceMode("visible_raw");
        }
      })
      .catch((err) => setError(describeError(err)));

    return () => {
      statusSocket.close();
      logSocket.close();
    };
  }, [token, source, videoSourceMode]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    const refreshCameraStatus = () => {
      void apiFetch<SiyiCameraStatus>("/api/camera/status", token)
        .then((payload) => {
          if (!cancelled) setCameraStatus(payload);
        })
        .catch((err) => {
          if (!cancelled) {
            setCameraStatus({ enabled: true, connected: false, error: describeError(err) });
          }
        });
    };
    refreshCameraStatus();
    const timer = window.setInterval(refreshCameraStatus, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [token]);

  useEffect(() => {
    if (!token || !cruiseActive) return;
    let cancelled = false;
    const heartbeat = () => {
      void apiFetch<SystemStatus["motion_state"]>("/api/control/cruise/heartbeat", token, { method: "POST" })
        .then((payload) => {
          if (!cancelled && payload) {
            setStatus((prev) => (prev ? { ...prev, motion_state: payload } : prev));
          }
        })
        .catch((err) => {
          if (!cancelled) {
            setError(describeError(err));
            setStatus((prev) => (prev && prev.motion_state ? { ...prev, motion_state: { ...prev.motion_state, cruise_active: false, cruise_speed: null, cruise_steering: "center" } } : prev));
          }
        });
    };
    heartbeat();
    const timer = window.setInterval(heartbeat, 200);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [token, cruiseActive]);

  useEffect(() => {
    if (!token) return;
    void apiFetch<{ items: MapEntry[] }>("/api/maps", token)
      .then((payload) => {
        setMaps(payload.items);
        const preferred = selectedMapName || status?.current_map_name || payload.items[0]?.name || "";
        if (preferred) {
          setSelectedMapName(preferred);
          setMapNameInput((prev) => prev || preferred);
        }
      })
      .catch((err) => setError(describeError(err)));
  }, [token]);

  useEffect(() => {
    if (!token) return;
    void refreshMedia();
  }, [token]);

  useEffect(() => {
    if (!token || !selectedMapName) return;
    let cancelled = false;
    setMapLoading(true);
    void loadMap(selectedMapName)
      .catch((err) => {
        if (!cancelled) setError(describeError(err));
      })
      .finally(() => {
        if (!cancelled) setMapLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token, selectedMapName]);

  useEffect(() => {
    if (!token || activeTab !== "navigation") return;
    let cancelled = false;
    const refreshNavigationState = () => {
      void apiFetch<NavigationState>("/api/navigation/status", token)
        .then((payload) => {
          if (!cancelled) setNavigationState(payload);
        })
        .catch(() => {
          if (!cancelled) setNavigationState(null);
        });
    };
    refreshNavigationState();
    const timer = window.setInterval(refreshNavigationState, 1500);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [token, activeTab]);



  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!token) return;
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (event.repeat) return;
      const keyMap: Record<string, string> = {
        w: "forward",
        s: "backward",
        a: "left",
        d: "right",
        " ": "stop",
      };
      const command = keyMap[event.key.toLowerCase()];
      if (!command) return;
      event.preventDefault();
      void sendControl(command);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [token, speed]);

  async function login() {
    try {
      setLoginPending(true);
      setError("");
      const result = await apiFetch<LoginResponse>("/api/auth/login", "", {
        method: "POST",
        body: JSON.stringify(loginForm),
      });
      saveToken(result.token);
      setToken(result.token);
      setError("");
    } catch (err) {
      setError(describeError(err));
    } finally {
      setLoginPending(false);
    }
  }

  async function runModel(action: "start" | "restart") {
    try {
      setModelPending(action);
      setError("");
      let parkingPayload: Record<string, unknown> = {};
      if (parkingEnabled) {
        const parkingZone = parkingZoneInput.split(",").map((value) => Number(value.trim()));
        const dwellSeconds = Number(parkingDwellInput);
        const validZone =
          parkingZone.length === 4 &&
          parkingZone.every((value) => Number.isFinite(value) && value >= 0 && value <= 1) &&
          parkingZone[0] < parkingZone[2] &&
          parkingZone[1] < parkingZone[3];
        if (!validZone) {
          setError("违停区域格式无效：请输入 0 到 1 之间的 x1,y1,x2,y2，且右下角必须大于左上角。");
          return;
        }
        if (!Number.isFinite(dwellSeconds) || dwellSeconds < 0 || dwellSeconds > 3600) {
          setError("违停停留时间必须在 0 到 3600 秒之间。");
          return;
        }
        parkingPayload = {
          parking_zone: parkingZone,
          parking_dwell_seconds: dwellSeconds,
        };
      }
      await apiFetch(`/api/model/${action}`, token, {
        method: "POST",
        body: JSON.stringify({
          model_id: selectedModel,
          source,
          view: false,
          annotate: true,
          ...parkingPayload,
        }),
      });
    } catch (err) {
      setError(describeError(err));
    } finally {
      setModelPending("");
    }
  }

  async function stopModel() {
    try {
      setModelPending("stop");
      setError("");
      await apiFetch("/api/model/stop", token, { method: "POST" });
    } catch (err) {
      setError(describeError(err));
    } finally {
      setModelPending("");
    }
  }

  async function sendControl(command: string) {
    try {
      setControlPending(true);
      setCommandState("sent");
      setError("");
      await apiFetch("/api/control/send", token, {
        method: "POST",
        body: JSON.stringify({ command, speed }),
      });
      setCommandState("acked");
    } catch (err) {
      setCommandState("failed");
      setError(describeError(err));
    } finally {
      setControlPending(false);
    }
  }

  async function startCruise() {
    try {
      cruiseSteeringDesiredRef.current = "center";
      setCruisePending("start");
      setError("");
      const payload = await apiFetch<SystemStatus["motion_state"]>("/api/control/cruise/start", token, {
        method: "POST",
        body: JSON.stringify({ speed }),
      });
      setStatus((prev) => (prev && payload ? { ...prev, motion_state: payload } : prev));
    } catch (err) {
      setError(describeError(err));
    } finally {
      setCruisePending("");
    }
  }

  async function stopCruise() {
    try {
      cruiseSteeringDesiredRef.current = "center";
      setCruisePending("stop");
      setError("");
      const payload = await apiFetch<SystemStatus["motion_state"]>("/api/control/cruise/stop", token, { method: "POST" });
      setStatus((prev) => (prev && payload ? { ...prev, motion_state: payload } : prev));
    } catch (err) {
      setError(describeError(err));
    } finally {
      setCruisePending("");
    }
  }

  async function steerCruise(direction: "center" | "left" | "right") {
    if (!cruiseActive) return;
    cruiseSteeringDesiredRef.current = direction;
    if (cruiseSteeringRequestRef.current) return;
    const request = (async () => {
      while (cruiseActive) {
        const nextDirection = cruiseSteeringDesiredRef.current;
        try {
          setError("");
          const payload = await apiFetch<SystemStatus["motion_state"]>("/api/control/cruise/steer", token, {
            method: "POST",
            body: JSON.stringify({ direction: nextDirection }),
          });
          setStatus((prev) => (prev && payload ? { ...prev, motion_state: payload } : prev));
        } catch (err) {
          setError(describeError(err));
          break;
        }
        if (cruiseSteeringDesiredRef.current === nextDirection) break;
      }
    })();
    cruiseSteeringRequestRef.current = request;
    try {
      await request;
    } finally {
      if (cruiseSteeringRequestRef.current === request) cruiseSteeringRequestRef.current = null;
    }
  }

  async function setControlArm(armed: boolean) {
    try {
      setArmPending(true);
      setError("");
      const payload = await apiFetch<SystemStatus["motion_state"]>("/api/control/arm", token, {
        method: "POST",
        body: JSON.stringify({ armed }),
      });
      setStatus((prev) => (prev ? { ...prev, motion_state: payload } : prev));
    } catch (err) {
      setError(describeError(err));
    } finally {
      setArmPending(false);
    }
  }

  async function setManualOverride(enabled: boolean) {
    try {
      setOverridePending(true);
      setError("");
      const payload = await apiFetch<SystemStatus["motion_state"]>("/api/control/manual-override", token, {
        method: "POST",
        body: JSON.stringify({ enabled }),
      });
      setStatus((prev) => (prev ? { ...prev, motion_state: payload } : prev));
    } catch (err) {
      setError(describeError(err));
    } finally {
      setOverridePending(false);
    }
  }

  async function setMotionMode(mode: "dry_run" | "real") {
    try {
      if (mode === "real" && !window.confirm("确认切换到 real 模式？仅在底盘已启动、架空或场地安全时使用。")) {
        return;
      }
      setModePending(mode);
      setError("");
      const payload = await apiFetch<SystemStatus["motion_state"]>("/api/control/mode", token, {
        method: "POST",
        body: JSON.stringify({ mode }),
      });
      setStatus((prev) => (prev ? { ...prev, motion_state: payload } : prev));
    } catch (err) {
      setError(describeError(err));
    } finally {
      setModePending("");
    }
  }

  async function setBasePower(action: "start" | "stop") {
    try {
      setBasePending(action);
      setError("");
      const payload = await apiFetch<SystemStatus["base_status"]>(`/api/base/${action}`, token, {
        method: "POST",
      });
      setStatus((prev) => (prev ? { ...prev, base_status: payload ?? prev.base_status } : prev));
      const fresh = await apiFetch<SystemStatus>("/api/system/status", token);
      setStatus(fresh);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBasePending("");
    }
  }

  async function emergencyStop() {
    try {
      setControlPending(true);
      setCommandState("sent");
      setError("");
      const payload = await apiFetch<SystemStatus["motion_state"]>("/api/control/emergency-stop", token, {
        method: "POST",
      });
      setStatus((prev) => (prev ? { ...prev, motion_state: payload } : prev));
      setCommandState("acked");
    } catch (err) {
      setCommandState("failed");
      setError(describeError(err));
    } finally {
      setControlPending(false);
    }
  }

  async function pulseGimbal(yawSpeed: number, pitchSpeed: number) {
    try {
      setGimbalPending(true);
      setError("");
      await apiFetch("/api/camera/gimbal/pulse", token, {
        method: "POST",
        body: JSON.stringify({ yaw_speed: yawSpeed, pitch_speed: pitchSpeed, duration_ms: 250 }),
      });
    } catch (err) {
      setError(describeError(err));
    } finally {
      setGimbalPending(false);
    }
  }

  async function stopGimbal() {
    try {
      setGimbalPending(true);
      setError("");
      await apiFetch("/api/camera/gimbal/stop", token, { method: "POST" });
    } catch (err) {
      setError(describeError(err));
    } finally {
      setGimbalPending(false);
    }
  }

  async function centerGimbal() {
    try {
      setGimbalPending(true);
      setError("");
      await apiFetch("/api/camera/gimbal/center", token, { method: "POST" });
    } catch (err) {
      setError(describeError(err));
    } finally {
      setGimbalPending(false);
    }
  }

  async function runPreset(name: string) {
    try {
      setPresetPending(name);
      setError("");
      await apiFetch("/api/preset/run", token, {
        method: "POST",
        body: JSON.stringify({ name, variables: { map_name: mapNameInput || selectedMapName || status?.current_map_name || "gmap_map" } }),
      });
      await new Promise((resolve) => window.setTimeout(resolve, name === "start_navigation" ? 1200 : 400));
      const fresh = await apiFetch<SystemStatus>("/api/system/status", token);
      setStatus(fresh);
      if (name === "save_map") {
        await refreshMaps();
      }
    } catch (err) {
      setError(describeError(err));
    } finally {
      setPresetPending("");
    }
  }

  async function refreshMaps() {
    try {
      setRefreshingMaps(true);
      const payload = await apiFetch<{ items: MapEntry[] }>("/api/maps", token);
      setMaps(payload.items);
      if (!selectedMapName && payload.items[0]?.name) {
        setSelectedMapName(payload.items[0].name);
      }
    } catch (err) {
      setError(describeError(err));
    } finally {
      setRefreshingMaps(false);
    }
  }

  async function refreshMedia() {
    try {
      setGalleryLoading(true);
      const [statusPayload, galleryPayload] = await Promise.all([
        apiFetch<MediaStatus>("/api/media/status", token),
        apiFetch<{ items: MediaItem[] }>("/api/media/gallery?page=1&page_size=20", token),
      ]);
      setMediaStatus(statusPayload);
      setMediaItems(galleryPayload.items);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setGalleryLoading(false);
    }
  }

  async function startRecording() {
    try {
      setMediaPending("record-start");
      setError("");
      const streamMode = videoSourceMode === "ir_raw" ? "ir_raw" : "visible_raw";
      const payload = await apiFetch<MediaStatus>("/api/media/recording/start", token, {
        method: "POST",
        body: JSON.stringify({ stream_mode: streamMode }),
      });
      setMediaStatus(payload);
      await refreshMedia();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setMediaPending("");
    }
  }

  async function stopRecording() {
    try {
      setMediaPending("record-stop");
      setError("");
      const payload = await apiFetch<MediaStatus>("/api/media/recording/stop", token, {
        method: "POST",
      });
      setMediaStatus(payload);
      await refreshMedia();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setMediaPending("");
    }
  }

  async function takeSnapshot() {
    try {
      setMediaPending("snapshot");
      setError("");
      await apiFetch("/api/media/snapshot", token, {
        method: "POST",
        body: JSON.stringify({ stream_mode: videoSourceMode }),
      });
      await refreshMedia();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setMediaPending("");
    }
  }

  async function loadMap(mapName: string) {
    const metadata = await apiFetch<MapMetadata>(`/api/maps/${encodeURIComponent(mapName)}`, token);
    setMapMetadata(metadata);
    const response = await fetch(`${API_BASE}/api/maps/${encodeURIComponent(mapName)}/pgm`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (!response.ok) {
      throw new Error((await response.text()) || "failed to load map image");
    }
    const buffer = await response.arrayBuffer();
    const imageUrl = pgmArrayBufferToDataUrl(buffer);
    setMapImageUrl((prev) => {
      if (prev.startsWith("blob:")) URL.revokeObjectURL(prev);
      return imageUrl;
    });
  }

  function mapPointFromEvent(event: MouseEvent<HTMLDivElement>): MapPoint | null {
    if (!mapMetadata?.width || !mapMetadata.height || !mapMetadata.resolution || mapMetadata.origin.length < 2) return null;
    const rect = event.currentTarget.getBoundingClientRect();
    const pixelX = Math.max(0, Math.min(mapMetadata.width, ((event.clientX - rect.left) / rect.width) * mapMetadata.width));
    const pixelY = Math.max(0, Math.min(mapMetadata.height, ((event.clientY - rect.top) / rect.height) * mapMetadata.height));
    const originX = mapMetadata.origin[0];
    const originY = mapMetadata.origin[1];
    const yaw = Number(mapPointMode === "initial" ? initialYawInput : goalYawInput);
    return {
      x: originX + pixelX * mapMetadata.resolution,
      y: originY + (mapMetadata.height - pixelY) * mapMetadata.resolution,
      yaw: Number.isFinite(yaw) ? (yaw * Math.PI) / 180 : 0,
      frame_id: "map",
    };
  }

  function mapPointPosition(point: MapPoint | null) {
    if (!point || !mapMetadata?.width || !mapMetadata.height || !mapMetadata.resolution || mapMetadata.origin.length < 2) return null;
    const pixelX = (point.x - mapMetadata.origin[0]) / mapMetadata.resolution;
    const pixelY = mapMetadata.height - (point.y - mapMetadata.origin[1]) / mapMetadata.resolution;
    return {
      left: `${Math.max(0, Math.min(100, (pixelX / mapMetadata.width) * 100))}%`,
      top: `${Math.max(0, Math.min(100, (pixelY / mapMetadata.height) * 100))}%`,
    };
  }

  function handleMapClick(event: MouseEvent<HTMLDivElement>) {
    if (mapPointMode === "none") return;
    const point = mapPointFromEvent(event);
    if (!point) return;
    if (mapPointMode === "initial") setInitialPoint(point);
    if (mapPointMode === "goal") setGoalPoint(point);
  }

  async function publishInitialPose() {
    if (!initialPoint) return;
    try {
      setNavigationPending("initial");
      setError("");
      await apiFetch("/api/navigation/initial-pose", token, { method: "POST", body: JSON.stringify(initialPoint) });
    } catch (err) {
      setError(describeError(err));
    } finally {
      setNavigationPending("");
    }
  }

  async function sendNavigationGoal() {
    if (!goalPoint) return;
    try {
      setNavigationPending("goal");
      setError("");
      const payload = await apiFetch<NavigationState>("/api/navigation/goal", token, { method: "POST", body: JSON.stringify(goalPoint) });
      setNavigationState(payload);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setNavigationPending("");
    }
  }

  async function cancelNavigationGoal() {
    try {
      setNavigationPending("cancel");
      setError("");
      const payload = await apiFetch<NavigationState>("/api/navigation/goal/cancel", token, { method: "POST" });
      setNavigationState(payload);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setNavigationPending("");
    }
  }

  function logout() {
    clearToken();
    setToken("");
    setStatus(null);
    setLogs([]);
  }

  if (!token) {
    return (
      <div className="flex min-h-screen items-center justify-center px-6">
        <div className="w-full max-w-md rounded-xl border border-base bg-panel/95 p-8 shadow-panel backdrop-blur">
          <div className="mb-8">
            <p className="text-sm uppercase tracking-[0.35em] text-accent-500">Nano Dashboard</p>
            <h1 className="mt-3 text-3xl font-semibold text-primary">登录控制台</h1>
            <p className="mt-2 text-sm text-muted">输入账号后进入系统。</p>
          </div>
          <div className="space-y-4">
            <input
              className="w-full rounded-xl border border-base bg-card px-4 py-3 text-secondary outline-none ring-accent-300 transition focus:ring-2"
              placeholder="Username"
              value={loginForm.username}
              onChange={(event) => setLoginForm((prev) => ({ ...prev, username: event.target.value }))}
            />
            <input
              type="password"
              className="w-full rounded-xl border border-base bg-card px-4 py-3 text-secondary outline-none ring-accent-300 transition focus:ring-2"
              placeholder="Password"
              value={loginForm.password}
              onChange={(event) => setLoginForm((prev) => ({ ...prev, password: event.target.value }))}
            />
            <button
              onClick={login}
              disabled={loginPending}
              className="w-full rounded-xl bg-gradient-to-r from-accent-400 to-cyan-300 px-4 py-3 font-semibold text-primary transition hover:opacity-90"
            >
              {loginPending ? "登录中..." : "登录"}
            </button>
            {error ? <p className="text-sm text-red-400">{error}</p> : null}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen bg-[radial-gradient(circle_at_top_left,rgba(125,211,252,.24),transparent_30%),linear-gradient(135deg,#edf5ff_0%,#f8fbff_48%,#e9f2fb_100%)] text-primary">
      <SidebarMenu activeTab={activeTab} onChange={selectTab} status={status} />
      <main className="min-w-0 flex-1 px-5 py-5 lg:px-8">
      <header className="mb-6 flex flex-col gap-4 rounded-lg border border-base/70 bg-panel p-6 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <div className="flex items-center gap-3">
            <span className="text-[10px] font-semibold uppercase tracking-[0.24em] text-accent-600">{activeWorkspace.eyebrow}</span>
            <span className="rounded bg-page px-1.5 py-0.5 text-[10px] font-semibold tracking-wider text-muted">NANO</span>
          </div>
          <div className="mt-2 flex items-end gap-3">
            <h1 className="text-2xl font-bold tracking-tight text-primary">{activeWorkspace.title}</h1>
            <span className="hidden pb-0.5 text-xs text-muted lg:inline">Vehicle Intelligence Console</span>
          </div>
          <p className="mt-1 text-[13px] text-muted">{activeWorkspace.description}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill tone={status?.agent_online ? "good" : "danger"}>设备 {status?.agent_online ? "在线" : "离线"}</StatusPill>
          <StatusPill tone={videoState === "connected" ? "good" : videoState === "failed" ? "danger" : "warn"}>视频 {videoState === "connected" ? "已连接" : videoState === "failed" ? "已断开" : "重连中"}</StatusPill>
          <StatusPill tone={status?.mapping_running ? "good" : "neutral"}>建图 {status?.mapping_running ? "运行中" : "空闲"}</StatusPill>
          <StatusPill tone={status?.navigation_running ? "good" : "neutral"}>导航 {status?.navigation_running ? "运行中" : "空闲"}</StatusPill>
          <div className="ml-2 h-4 w-px bg-divider"></div>
          <ThemeToggle />
          <button onClick={logout} className="rounded-md border border-base bg-panel px-3 py-1.5 text-[13px] font-medium text-tertiary hover:bg-card hover:text-primary transition-colors">
            退出登录
          </button>
        </div>
      </header>

      <div className="space-y-5">
        {activeTab === "live" ? <div id="section-live" className="grid gap-5 xl:grid-cols-12">
          <div className="h-full xl:col-span-8">
            <Panel
              title="实时视频 (双光融合)"
              subtitle="左侧为可见光/检测结果，右侧为红外流。"
              className="h-full min-h-[780px] flex flex-col"
              right={
                <div className="flex items-center gap-2">
                  <div className="flex overflow-hidden rounded-md border border-base bg-card text-[13px] text-tertiary font-medium shadow-sm">
                    <button
                      onClick={() => setVideoSourceMode("visible_raw")}
                      className={classNames("px-3 py-1.5 transition-colors", videoSourceMode === "visible_raw" ? "bg-panel text-accent-600 shadow-sm" : "hover:bg-page")}
                    >
                      主屏: 可见光
                    </button>
                    <div className="w-px bg-slate-200"></div>
                    <button
                      onClick={() => setVideoSourceMode("annotated")}
                      disabled={!status?.model_status?.running}
                      className={classNames(
                        "px-3 py-1.5 transition-colors",
                        videoSourceMode === "annotated" ? "bg-panel text-accent-600 shadow-sm" : "hover:bg-page",
                        !status?.model_status?.running && "cursor-not-allowed opacity-50",
                      )}
                    >
                      主屏: 检测结果
                    </button>
                  </div>
                  <button
                    onClick={() => setUseFallback((prev) => !prev)}
                    className="rounded-md border border-base bg-panel px-3 py-1.5 text-[13px] text-tertiary hover:bg-card transition-colors shadow-sm"
                  >
                    {useFallback ? "恢复 WebRTC" : "启用低延时退回"}
                  </button>
                </div>
              }
            >
              <div className="grid grid-cols-2 gap-3 flex-1 min-h-[300px]">
                <VideoStream
                  mode={videoSourceMode}
                  token={token}
                  useFallback={useFallback}
                  onStateChange={setVideoState}
                />
                <VideoStream
                  mode="ir_raw"
                  token={token}
                  useFallback={useFallback}
                />
              </div>
              <div className="mt-4 rounded-xl border border-base bg-card/50 p-4">
                <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-xs uppercase tracking-[0.18em] text-disabled">模型联动</p>
                    <p className="mt-1 text-sm text-muted">切换模型并查看当前推理进程状态。</p>
                  </div>
                  <StatusPill tone={status?.model_status?.running ? "good" : "neutral"}>
                    {status?.model_status?.running ? "running" : "stopped"}
                  </StatusPill>
                </div>
                <div className="grid gap-3 xl:grid-cols-[0.9fr_1.1fr_auto]">
                  <select
                    value={selectedModel}
                    onChange={(event) => setSelectedModel(event.target.value)}
                    className="w-full rounded-xl border border-base bg-panel px-4 py-3 text-secondary shadow-sm"
                  >
                    {(status?.available_models ?? []).map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                  <input
                    value={source}
                    onChange={(event) => setSource(event.target.value)}
                    placeholder="RTSP source"
                    className="w-full rounded-xl border border-base bg-panel px-4 py-3 text-secondary shadow-sm"
                  />
                  <div className="grid grid-cols-3 gap-3 xl:min-w-[300px]">
                    <ActionButton icon={<PlayCircleIcon className="h-5 w-5" />} label={modelPending === "start" ? "启动中..." : "Start"} disabled={!!modelPending} onClick={() => void runModel("start")} />
                    <ActionButton icon={<ArrowPathIcon className="h-5 w-5" />} label={modelPending === "restart" ? "重启中..." : "Restart"} disabled={!!modelPending} onClick={() => void runModel("restart")} />
                    <ActionButton icon={<PauseCircleIcon className="h-5 w-5" />} label={modelPending === "stop" ? "停止中..." : "Stop"} disabled={!!modelPending} onClick={() => void stopModel()} />
                  </div>
                </div>
                <div className="mt-3 grid gap-3 md:grid-cols-4">
                  <MetaCard label="当前模型" value={status?.model_status?.model_id || "--"} />
                  <MetaCard label="进程状态" value={status?.model_status?.running ? "running" : "stopped"} />
                  <MetaCard label="PID" value={status?.model_status?.pid ? String(status.model_status.pid) : "--"} />
                  <MetaCard label="启动时间" value={status?.model_status?.started_at ? new Date(status.model_status.started_at).toLocaleTimeString() : "--"} />
                </div>
                <div className="mt-3 rounded-xl border border-base bg-panel/70 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <label className="flex cursor-pointer items-center gap-2 text-sm font-medium text-secondary">
                      <input
                        type="checkbox"
                        checked={parkingEnabled}
                        onChange={(event) => setParkingEnabled(event.target.checked)}
                        className="h-4 w-4 rounded border-base accent-accent-500"
                      />
                      启用违停判定
                    </label>
                    <span className="text-xs text-muted">车辆进入 ROI 并持续停留后生成事件</span>
                  </div>
                  {parkingEnabled && (
                    <div className="mt-3 grid gap-3 md:grid-cols-[1fr_150px]">
                      <label className="text-xs font-medium uppercase tracking-[0.12em] text-muted">
                        禁停区域 ROI
                        <input
                          value={parkingZoneInput}
                          onChange={(event) => setParkingZoneInput(event.target.value)}
                          placeholder="x1, y1, x2, y2"
                          aria-label="禁停区域归一化坐标"
                          className="mt-1 w-full rounded-lg border border-base bg-card px-3 py-2 text-sm normal-case tracking-normal text-secondary outline-none ring-accent-300 transition focus:ring-2"
                        />
                      </label>
                      <label className="text-xs font-medium uppercase tracking-[0.12em] text-muted">
                        停留秒数
                        <input
                          type="number"
                          min="0"
                          max="3600"
                          step="0.5"
                          value={parkingDwellInput}
                          onChange={(event) => setParkingDwellInput(event.target.value)}
                          aria-label="违停停留秒数"
                          className="mt-1 w-full rounded-lg border border-base bg-card px-3 py-2 text-sm normal-case tracking-normal text-secondary outline-none ring-accent-300 transition focus:ring-2"
                        />
                      </label>
                    </div>
                  )}
                </div>
              </div>
            </Panel>
          </div>

          <div className="h-full xl:col-span-4">
            <Panel
              title="即时操控"
              subtitle="执行车辆即时动作与安全控制。"
              className="h-full min-h-[780px]"
            >
              <div className="mb-4 grid grid-cols-2 gap-3">
                <StatCard label="控制模式" value={status?.motion_state?.mode === "real" ? "real" : "dry-run"} />
                <StatCard label="控制状态" value={status?.motion_state?.armed ? "已解锁" : "已锁定"} />
                <StatCard label="人工接管" value={status?.motion_state?.manual_override ? "enabled" : status?.motion_state?.nav_lockout ? "nav lockout" : "idle"} />
                <StatCard label="速度档位" value={speed} />
              </div>
              <div className="mb-4 flex gap-2">
                {speedLevels.map((item) => (
                  <button
                    key={item}
                    onClick={() => setSpeed(item)}
                    disabled={cruiseActive}
                    className={classNames(
                      "rounded-full px-3 py-1.5 text-sm capitalize transition",
                      speed === item ? "bg-accent-300 text-primary" : "border border-base bg-panel text-tertiary",
                      cruiseActive && "cursor-not-allowed opacity-50",
                    )}
                  >
                    {item}
                  </button>
                ))}
              </div>
              <div className="mb-4 grid grid-cols-2 gap-3">
                <ActionButton
                  icon={<BoltIcon className="h-5 w-5" />}
                  label={armPending ? "处理中..." : status?.motion_state?.armed ? "锁定控制" : "解锁控制"}
                  disabled={armPending || !status?.motion_state}
                  onClick={() => void setControlArm(!status?.motion_state?.armed)}
                />
                <ActionButton
                  icon={<BoltIcon className="h-5 w-5" />}
                  label={overridePending ? "处理中..." : status?.motion_state?.manual_override ? "取消人工接管" : "启用人工接管"}
                  disabled={
                    overridePending ||
                    !status?.motion_state ||
                    (!status?.navigation_running && !status?.motion_state?.manual_override) ||
                    (!!status?.motion_state && status.motion_state.require_manual_arm && !status.motion_state.armed && !status.motion_state.manual_override)
                  }
                  onClick={() => void setManualOverride(!status?.motion_state?.manual_override)}
                />
              </div>
              <div className="mb-4 rounded-xl border border-base bg-card/50 px-4 py-3 text-sm text-tertiary">
                {controlHint}
              </div>
              <div className="rounded-xl border border-base bg-card/50 p-5">
                <div className="mb-5 rounded-2xl border border-accent-200 bg-gradient-to-br from-accent-50 to-card p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-sm font-semibold text-primary">匀速前进</p>
                      <p className="mt-1 text-xs leading-5 text-tertiary">底盘持续保持当前速度档位前进，只需按住左右键修正方向。</p>
                    </div>
                    <StatusPill tone={cruiseActive ? "good" : "neutral"}>{cruiseActive ? (cruiseSteering === "center" ? "直行中" : cruiseSteering === "left" ? "左修正" : "右修正") : "待启动"}</StatusPill>
                  </div>
                  <button
                    onClick={() => void (cruiseActive ? stopCruise() : startCruise())}
                    disabled={cruisePending !== "" || !status?.motion_state || (!cruiseActive && manualControlBlocked)}
                    className={classNames(
                      "mt-3 flex w-full items-center justify-center gap-2 rounded-xl px-4 py-3 text-sm font-semibold transition",
                      cruiseActive ? "bg-panel text-danger ring-1 ring-red-200 hover:bg-red-50" : "bg-accent-300 text-primary hover:bg-accent-400",
                      (cruisePending !== "" || !status?.motion_state || (!cruiseActive && manualControlBlocked)) && "cursor-not-allowed opacity-50",
                    )}
                  >
                    {cruisePending === "start" ? "正在启动..." : cruisePending === "stop" ? "正在停车..." : cruiseActive ? "停止匀速前进" : "开始匀速前进"}
                  </button>
                  {cruiseActive && (
                    <div className="mt-4 grid grid-cols-3 gap-2">
                      <CruiseDirectionButton icon={<ChevronLeftIcon className="h-6 w-6" />} title="按住左修正" onPress={() => void steerCruise("left")} onRelease={() => void steerCruise("center")} disabled={cruisePending === "start" || cruisePending === "stop"} />
                      <CruiseDirectionButton icon={<ChevronUpIcon className="h-6 w-6" />} title="直行" onPress={() => void steerCruise("center")} onRelease={() => void steerCruise("center")} disabled={cruisePending === "start" || cruisePending === "stop"} active={cruiseSteering === "center"} />
                      <CruiseDirectionButton icon={<ChevronRightIcon className="h-6 w-6" />} title="按住右修正" onPress={() => void steerCruise("right")} onRelease={() => void steerCruise("center")} disabled={cruisePending === "start" || cruisePending === "stop"} />
                    </div>
                  )}
                </div>
                <div className="mx-auto w-fit grid grid-cols-3 gap-2">
                  <div />
                  <ControlButton icon={<ChevronUpIcon className="h-7 w-7" />} disabled={controlPending || cruiseActive || manualControlBlocked} onClick={() => void sendControl("forward")} title="前进" />
                  <div />
                  <ControlButton icon={<ChevronLeftIcon className="h-7 w-7" />} disabled={controlPending || cruiseActive || manualControlBlocked} onClick={() => void sendControl("left")} title="左转" />
                  <ControlButton icon={<StopIcon className="h-7 w-7" />} disabled={controlPending || cruisePending !== ""} onClick={() => void (cruiseActive ? stopCruise() : sendControl("stop"))} title="停止" variant="danger" />
                  <ControlButton icon={<ChevronRightIcon className="h-7 w-7" />} disabled={controlPending || cruiseActive || manualControlBlocked} onClick={() => void sendControl("right")} title="右转" />
                  <div />
                  <ControlButton icon={<ChevronDownIcon className="h-7 w-7" />} disabled={controlPending || cruiseActive || manualControlBlocked} onClick={() => void sendControl("backward")} title="后退" />
                  <div />
                </div>
                <button
                  onClick={() => void emergencyStop()}
                  disabled={controlPending}
                  className="mt-4 flex w-full items-center justify-center gap-2 rounded-xl bg-danger px-4 py-3 font-semibold text-white"
                >
                  <ExclamationTriangleIcon className="h-5 w-5" />
                  急停
                </button>
              </div>
              <div className="mt-4 rounded-xl border border-base bg-card/50 p-4 text-sm text-tertiary">
                <InfoRow label="最近命令" value={status?.motion_state?.last_command ? `${status.motion_state.last_command.command}/${status.motion_state.last_command.speed}` : "--"} />
                <InfoRow
                  label="最近 Twist"
                  value={
                    status?.motion_state?.last_command
                      ? `x=${status.motion_state.last_command.linear_x.toFixed(3)} z=${status.motion_state.last_command.angular_z.toFixed(3)}`
                      : "--"
                  }
                />
                <InfoRow label="状态" value={`${commandState}${status?.motion_state?.last_command ? ` / ${status.motion_state.last_command.dry_run ? "dry-run" : "real publish"}` : ""}`} />
              </div>
            </Panel>
          </div>
        </div> : null}

        {activeTab === "navigation" ? <div id="section-navigation" className="grid gap-5 xl:grid-cols-12">
          <div className="h-full xl:col-span-4">
            <Panel title="建图与导航" subtitle="管理建图、地图保存、导航与 RViz。" className="h-full min-h-[560px]">
              <div className="flex h-full flex-col">
                <div className="mb-5 grid grid-cols-2 gap-4">
                  <StatCard label="当前地图" value={status?.current_map_name || selectedMapName || "--"} />
                  <StatCard label="Mapping" value={status?.mapping_running ? "running" : "idle"} />
                  <StatCard label="Navigation" value={status?.navigation_running ? "running" : "idle"} />
                  <StatCard label="RViz" value={status?.rviz_running ? "running" : status?.rviz_available ? "available" : "unavailable"} />
                </div>
                <div className="space-y-4">
                  <select
                    value={selectedMapName}
                    onChange={(event) => {
                      setSelectedMapName(event.target.value);
                      setMapNameInput(event.target.value);
                    }}
                    className="w-full rounded-xl border border-base bg-panel px-4 py-3.5 text-secondary"
                  >
                    {maps.map((item) => (
                      <option key={item.name} value={item.name}>
                        {item.name}
                      </option>
                    ))}
                  </select>
                  <input
                    value={mapNameInput}
                    onChange={(event) => setMapNameInput(event.target.value)}
                    placeholder="地图名，如 gmap_map"
                    className="w-full rounded-xl border border-base bg-panel px-4 py-3.5 text-secondary"
                  />
                </div>
                <div className="mt-5 grid grid-cols-2 gap-4">
                  <ActionButton icon={<BoltIcon className="h-5 w-5" />} label={presetPending === "start_mapping" ? "启动中..." : "启动建图"} disabled={!!presetPending} onClick={() => void runPreset("start_mapping")} />
                  <ActionButton icon={<BoltIcon className="h-5 w-5" />} label={presetPending === "save_map" ? "保存中..." : "保存地图"} disabled={!!presetPending} onClick={() => void runPreset("save_map")} />
                  <ActionButton icon={<BoltIcon className="h-5 w-5" />} label={presetPending === "start_navigation" ? "启动中..." : "启动导航"} disabled={!!presetPending} onClick={() => void runPreset("start_navigation")} />
                  <ActionButton icon={<BoltIcon className="h-5 w-5" />} label={presetPending === "open_rviz" ? "打开中..." : "打开 RViz"} disabled={!!presetPending} onClick={() => void runPreset("open_rviz")} />
                  <ActionButton icon={<StopCircleIcon className="h-5 w-5" />} label={presetPending === "stop_mapping" ? "停止中..." : "停止建图"} disabled={!!presetPending} onClick={() => void runPreset("stop_mapping")} />
                  <ActionButton icon={<StopCircleIcon className="h-5 w-5" />} label={presetPending === "stop_navigation" ? "停止中..." : "停止导航"} disabled={!!presetPending} onClick={() => void runPreset("stop_navigation")} />
                </div>
                <div className="mt-5 rounded-xl border border-base bg-card/50 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="text-xs uppercase tracking-[0.18em] text-disabled">目标点工作区</p>
                      <p className="mt-1 text-sm text-muted">点击地图记录起点或目标点；只有点击“发布/发送”才会下发 ROS 命令。</p>
                    </div>
                    <span className="rounded-full border border-base bg-panel px-2 py-1 text-[11px] text-disabled">{navigationState?.goal_status || "idle"}</span>
                  </div>
                  <div className="mt-3 grid grid-cols-3 gap-2">
                    <button onClick={() => setMapPointMode(mapPointMode === "initial" ? "none" : "initial")} className={classNames("rounded-lg border px-2 py-2 text-xs", mapPointMode === "initial" ? "border-accent-300 bg-accent-100 text-accent-700" : "border-base bg-panel text-tertiary")}>设置起点</button>
                    <button onClick={() => setMapPointMode(mapPointMode === "goal" ? "none" : "goal")} className={classNames("rounded-lg border px-2 py-2 text-xs", mapPointMode === "goal" ? "border-accent-300 bg-accent-100 text-accent-700" : "border-base bg-panel text-tertiary")}>设置目标</button>
                    <button onClick={() => { setMapPointMode("none"); setInitialPoint(null); setGoalPoint(null); }} className="rounded-lg border border-base bg-panel px-2 py-2 text-xs text-tertiary">清除点位</button>
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-3">
                    <label className="text-xs text-muted">起点朝向°
                      <input value={initialYawInput} onChange={(event) => setInitialYawInput(event.target.value)} inputMode="decimal" className="mt-1 w-full rounded-lg border border-base bg-panel px-2 py-2 text-sm text-secondary" />
                    </label>
                    <label className="text-xs text-muted">目标朝向°
                      <input value={goalYawInput} onChange={(event) => setGoalYawInput(event.target.value)} inputMode="decimal" className="mt-1 w-full rounded-lg border border-base bg-panel px-2 py-2 text-sm text-secondary" />
                    </label>
                  </div>
                  <div className="mt-3 grid grid-cols-2 gap-2">
                    <button disabled={!initialPoint || navigationPending !== ""} onClick={() => void publishInitialPose()} className="rounded-lg border border-base bg-panel px-2 py-2 text-xs text-tertiary disabled:cursor-not-allowed disabled:opacity-40">{navigationPending === "initial" ? "发布中..." : "发布起点"}</button>
                    <button disabled={!goalPoint || navigationPending !== "" || !navigationState?.navigation_running} onClick={() => void sendNavigationGoal()} className="rounded-lg bg-accent-500 px-2 py-2 text-xs font-medium text-white disabled:cursor-not-allowed disabled:opacity-40">{navigationPending === "goal" ? "发送中..." : "发送目标"}</button>
                  </div>
                  <button disabled={navigationPending !== "" || !navigationState?.goal} onClick={() => void cancelNavigationGoal()} className="mt-2 w-full rounded-lg border border-red-200 bg-red-50 px-2 py-2 text-xs text-red-700 disabled:cursor-not-allowed disabled:opacity-40">{navigationPending === "cancel" ? "取消中..." : "取消当前目标"}</button>
                </div>
                <div className="mt-auto pt-4">
                  {presetPending ? <p className="text-sm text-accent-600">正在执行：{presetPending}</p> : <div className="h-5" />}
                </div>
              </div>
            </Panel>
          </div>
          <Panel
            title="地图查看"
            subtitle="查看已保存地图、参数与图像结果。"
            className="min-h-[560px] xl:col-span-8"
            right={
              <div className="flex items-center gap-2">
                <button onClick={() => void refreshMaps()} className="rounded-md border border-base bg-panel px-3 py-1.5 text-[13px] font-medium text-tertiary hover:bg-card transition-colors">
                  {refreshingMaps ? "刷新中..." : "刷新地图列表"}
                </button>
              </div>
            }
          >
            <div className="grid gap-4 lg:grid-cols-[0.72fr_1.28fr]">
              <div className="rounded-xl border border-base bg-card/50 p-4">
                <p className="text-xs uppercase tracking-[0.18em] text-disabled">已保存地图</p>
                <div className="mt-3 max-h-[400px] space-y-2 overflow-auto pr-1">
                  {maps.map((item) => (
                    <button
                      key={item.name}
                      onClick={() => {
                        setSelectedMapName(item.name);
                        setMapNameInput(item.name);
                      }}
                      className={classNames(
                        "w-full rounded-xl border px-3 py-3 text-left text-sm transition",
                        selectedMapName === item.name ? "border-accent-300 bg-accent-100 text-primary ring-2 ring-accent-400" : "border-base bg-panel text-tertiary hover:bg-card",
                      )}
                    >
                      <p className="font-medium">{item.name}</p>
                      <p className="mt-1 text-xs text-disabled">{new Date(item.updated_at).toLocaleString()}</p>
                    </button>
                  ))}
                  {maps.length === 0 ? <p className="text-sm text-muted">未找到已保存地图。</p> : null}
                </div>
              </div>

              <div className="rounded-xl border border-base bg-card/50 p-4">
                <div className="mb-4 grid gap-3 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,0.8fr)]">
                  <MetaCard label="地图名称" value={mapMetadata?.name || "--"} />
                  <MetaCard label="分辨率" value={mapMetadata ? `${mapMetadata.resolution} m/px` : "--"} />
                  <MetaCard
                    label="地图原点"
                    value={mapMetadata ? `${mapMetadata.origin[0].toFixed(2)}, ${mapMetadata.origin[1].toFixed(2)}, ${mapMetadata.origin[2].toFixed(2)}` : "--"}
                  />
                  <MetaCard label="地图尺寸" value={mapMetadata?.width && mapMetadata?.height ? `${mapMetadata.width} × ${mapMetadata.height}` : "--"} />
                </div>
                <div
                  onClick={handleMapClick}
                  className={classNames("relative overflow-hidden rounded-xl border border-base bg-page/90", mapPointMode !== "none" && "cursor-crosshair")}
                  style={{ aspectRatio: mapMetadata?.width && mapMetadata?.height ? `${mapMetadata.width} / ${mapMetadata.height}` : "16 / 10" }}
                >
                  {mapLoading ? <div className="flex aspect-[16/10] items-center justify-center text-sm text-disabled">地图加载中...</div> : null}
                  {!mapLoading && mapImageUrl ? <img alt={`map ${mapMetadata?.name ?? ""}`} src={mapImageUrl} className="absolute inset-0 h-full w-full object-fill" /> : null}
                  {!mapLoading && mapImageUrl && initialPoint ? <div className="absolute -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-sky-500 px-2 py-1 text-[10px] font-semibold text-white shadow" style={mapPointPosition(initialPoint) || undefined}>起点</div> : null}
                  {!mapLoading && mapImageUrl && goalPoint ? <div className="absolute -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-white bg-accent-500 px-2 py-1 text-[10px] font-semibold text-white shadow" style={mapPointPosition(goalPoint) || undefined}>目标</div> : null}
                  {!mapLoading && !mapImageUrl ? <div className="flex aspect-[16/10] items-center justify-center text-sm text-muted">请选择地图。</div> : null}
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted">
                  <span>地图点击模式：{mapPointMode === "initial" ? "设置起点" : mapPointMode === "goal" ? "设置目标" : "未选择"}</span>
                  {initialPoint ? <span className="text-sky-700">起点 {initialPoint.x.toFixed(2)}, {initialPoint.y.toFixed(2)}</span> : null}
                  {goalPoint ? <span className="text-accent-700">目标 {goalPoint.x.toFixed(2)}, {goalPoint.y.toFixed(2)}</span> : null}
                </div>
                <p className="mt-2 text-sm text-muted">目标发送前请先启动导航并发布起始位姿；地图点击只做坐标换算，不会自动驱动车辆。</p>
              </div>
            </div>
          </Panel>
        </div> : null}


        {activeTab === "diagnostics" ? <div id="section-diagnostics">
        <Panel title="系统与底盘状态" subtitle="查看系统资源、底盘在线状态与控制模式。">
          <div className="grid gap-4 xl:grid-cols-4">
            <div className="rounded-xl border border-base bg-card/50 p-4">
              <p className="mb-3 text-xs uppercase tracking-[0.18em] text-disabled">系统资源</p>
              <div className="grid grid-cols-2 gap-3">
                <StatCard label="CPU" value={formatNumber(status?.cpu_percent)} />
                <StatCard label="Memory" value={formatNumber(status?.memory_percent)} />
                <StatCard label="GPU" value={formatNumber(status?.gpu_percent)} />
                <StatCard label="Heartbeat" value={status?.last_heartbeat ? new Date(status.last_heartbeat).toLocaleTimeString() : "--"} />
              </div>
            </div>

            <div className="rounded-xl border border-base bg-card/50 p-4">
              <p className="mb-3 text-xs uppercase tracking-[0.18em] text-disabled">底盘在线状态</p>
              <div className="grid grid-cols-2 gap-3">
                <StatCard label="底盘" value={status?.base_status?.running ? "已启动" : "未启动"} />
                <StatCard label="Robot" value={status?.base_status?.wheeltec_robot_online ? "online" : "offline"} />
                <StatCard label="控制订阅" value={status?.base_status?.cmd_vel_subscriber_online ? "online" : "offline"} />
                <StatCard label="里程计" value={status?.base_status?.odom_online ? "online" : "offline"} />
              </div>
            </div>

            <div className="rounded-xl border border-base bg-card/50 p-4">
              <p className="mb-3 text-xs uppercase tracking-[0.18em] text-disabled">运行摘要</p>
              <div className="grid gap-3 md:grid-cols-2">
                <InfoRow label="Board" value={status?.board_name || "--"} />
                <InfoRow label="当前地图" value={status?.current_map_name || selectedMapName || "--"} />
                <InfoRow label="建图状态" value={status?.mapping_running ? "running" : "idle"} />
                <InfoRow label="导航状态" value={status?.navigation_running ? "running" : "idle"} />
                <InfoRow label="视频模式" value={videoModeLabel(videoSourceMode)} />
                <InfoRow label="控制模式" value={status?.motion_state?.mode === "real" ? "real" : "dry-run"} />
              </div>
              <div className="mt-4 grid grid-cols-2 gap-3">
                <ActionButton
                  icon={<PlayCircleIcon className="h-5 w-5" />}
                  label={basePending === "start" ? "启动中..." : "启动底盘"}
                  disabled={!!basePending || !!status?.base_status?.running}
                  onClick={() => void setBasePower("start")}
                />
                <ActionButton
                  icon={<StopCircleIcon className="h-5 w-5" />}
                  label={basePending === "stop" ? "停止中..." : "停止底盘"}
                  disabled={!!basePending || !status?.base_status?.running}
                  onClick={() => void setBasePower("stop")}
                />
                <ActionButton
                  icon={<BoltIcon className="h-5 w-5" />}
                  label={modePending === "dry_run" ? "切换中..." : "切回 dry-run"}
                  disabled={!!modePending || status?.motion_state?.mode === "dry_run"}
                  onClick={() => void setMotionMode("dry_run")}
                />
                <ActionButton
                  icon={<BoltIcon className="h-5 w-5" />}
                  label={modePending === "real" ? "切换中..." : "切换到 real"}
                  disabled={!!modePending || status?.motion_state?.mode === "real" || !baseReady}
                  onClick={() => void setMotionMode("real")}
                />
              </div>
            </div>

            <div className="rounded-xl border border-base bg-card/50 p-4">
              <div className="mb-3 flex items-center justify-between gap-3">
                <p className="text-xs uppercase tracking-[0.18em] text-disabled">ZT6 云台</p>
                <StatusPill tone={cameraStatus?.connected ? "good" : "warn"}>
                  {!cameraStatus?.enabled ? "disabled" : cameraStatus?.connected ? "online" : "offline"}
                </StatusPill>
              </div>
              <div className="grid grid-cols-2 gap-3">
                <StatCard label="模式" value={cameraStatus?.status?.motion_mode_name || "--"} />
                <StatCard label="录像" value={cameraStatus?.status?.record_state_name || "--"} />
                <StatCard label="Yaw" value={formatNumber(cameraStatus?.attitude?.yaw_deg, "°")} />
                <StatCard label="Pitch" value={formatNumber(cameraStatus?.attitude?.pitch_deg, "°")} />
              </div>
              <p className="mt-3 truncate text-xs text-muted" title={cameraStatus?.hardware_id || undefined}>
                ID: {cameraStatus?.hardware_id || cameraStatus?.error || "--"}
              </p>
              <div className="mt-3 border-t border-divider pt-3">
                <p className="mb-2 text-xs text-muted">协议方向控制（默认关闭）</p>
                <div className="grid grid-cols-3 gap-2">
                  <div />
                  <button
                    className="rounded-lg border border-base bg-panel px-2 py-2 text-xs text-tertiary disabled:cursor-not-allowed disabled:opacity-40"
                    disabled={!cameraStatus?.motion_enabled || gimbalPending}
                    onClick={() => void pulseGimbal(0, 10)}
                  >
                    上仰
                  </button>
                  <div />
                  <button
                    className="rounded-lg border border-base bg-panel px-2 py-2 text-xs text-tertiary disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={!cameraStatus?.motion_enabled || gimbalPending}
                  onClick={() => void pulseGimbal(-10, 0)}
                  >
                    左转
                  </button>
                  <button
                    className="rounded-lg border border-red-200 bg-red-50 px-2 py-2 text-xs text-red-700 disabled:cursor-not-allowed disabled:opacity-40"
                    disabled={gimbalPending}
                    onClick={() => void stopGimbal()}
                  >
                    Stop
                  </button>
                  <button
                    className="rounded-lg border border-base bg-panel px-2 py-2 text-xs text-tertiary disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={!cameraStatus?.motion_enabled || gimbalPending}
                  onClick={() => void pulseGimbal(10, 0)}
                  >
                    右转
                  </button>
                  <div />
                  <button
                    className="rounded-lg border border-base bg-panel px-2 py-2 text-xs text-tertiary disabled:cursor-not-allowed disabled:opacity-40"
                    disabled={!cameraStatus?.motion_enabled || gimbalPending}
                    onClick={() => void pulseGimbal(0, -10)}
                  >
                    下俯
                  </button>
                  <div />
                </div>
                <button
                  className="mt-2 w-full rounded-lg border border-accent-200 bg-accent-50 px-2 py-2 text-xs text-accent-700 disabled:cursor-not-allowed disabled:opacity-40"
                  disabled={gimbalPending || !cameraStatus?.connected}
                  onClick={() => void centerGimbal()}
                >
                  回中
                </button>
              </div>
            </div>
          </div>
          {status?.base_status?.last_error ? <p className="mt-3 text-sm text-amber-600">底盘提示：{status.base_status.last_error}</p> : null}
        </Panel>
        </div> : null}

        {activeTab === "perception" ? <div id="section-perception" className="grid gap-5 xl:grid-cols-12">
          <Panel title="媒体与记录" subtitle="管理录像、拍照与 AI 检测历史记录。" className="h-full xl:col-span-5 flex flex-col">
            <div className="mb-4 grid grid-cols-2 gap-3">
              <StatCard label="录像状态" value={mediaStatus?.recording_running ? "recording" : "idle"} />
              <StatCard label="记录总数" value={String(mediaItems.length)} />
            </div>
            
            {/* Tab Header */}
            <div className="mb-4 flex border-b border-divider">
              <button
                onClick={() => setMediaTab("gallery")}
                className={classNames(
                  "px-4 py-2 text-sm font-medium transition-colors border-b-2",
                  mediaTab === "gallery" ? "border-accent-500 text-accent-600" : "border-transparent text-tertiary hover:text-secondary"
                )}
              >
                媒体图库
              </button>
              <button
                onClick={() => setMediaTab("events")}
                className={classNames(
                  "px-4 py-2 text-sm font-medium transition-colors border-b-2",
                  mediaTab === "events" ? "border-accent-500 text-accent-600" : "border-transparent text-tertiary hover:text-secondary"
                )}
              >
                检测记录
              </button>
            </div>

            {mediaTab === "gallery" ? (
              <>
                <div className="mb-4 grid grid-cols-2 gap-3 xl:grid-cols-4">
                  <ActionButton
                    icon={<PlayCircleIcon className="h-5 w-5" />}
                    label={mediaPending === "record-start" ? "..." : "开始录制"}
                    disabled={mediaPending !== "" || !mediaStatus?.enabled || !!mediaStatus?.recording_running}
                    onClick={() => void startRecording()}
                  />
                  <ActionButton
                    icon={<PauseCircleIcon className="h-5 w-5" />}
                    label={mediaPending === "record-stop" ? "..." : "停止录制"}
                    disabled={mediaPending !== "" || !mediaStatus?.recording_running}
                    onClick={() => void stopRecording()}
                  />
                  <ActionButton
                    icon={<BoltIcon className="h-5 w-5" />}
                    label={mediaPending === "snapshot" ? "..." : "拍照"}
                    disabled={mediaPending !== "" || !mediaStatus?.enabled}
                    onClick={() => void takeSnapshot()}
                  />
                  <ActionButton
                    icon={<ArrowPathIcon className="h-5 w-5" />}
                    label={galleryLoading ? "..." : "刷新"}
                    disabled={galleryLoading || mediaPending !== ""}
                    onClick={() => void refreshMedia()}
                  />
                </div>
                <div className="max-h-[300px] space-y-2 overflow-auto pr-1 flex-1">
                  {mediaItems.filter(it => it.storage_kind !== "event").slice(0, 8).map((item) => {
                    const fileUrl = `${API_BASE}/api/media/files/${encodeURIComponent(item.storage_kind)}/${encodeURIComponent(item.filename)}?token=${encodeURIComponent(token)}`;
                    return (
                      <div key={`${item.storage_kind}-${item.filename}`} className="rounded-xl border border-base bg-panel/90 p-3 text-sm text-tertiary shadow-sm">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0">
                            <p className="truncate font-medium text-secondary">{item.filename}</p>
                            <p className="mt-1 text-xs text-disabled">
                              {item.media_type === "video" ? "视频" : "图片"} / {(item.size_bytes / 1024 / 1024).toFixed(2)} MB
                            </p>
                          </div>
                          <a href={fileUrl} target="_blank" rel="noreferrer" className="shrink-0 rounded-md border border-base bg-card px-3 py-1 text-xs text-tertiary hover:bg-panel transition-colors">
                            打开
                          </a>
                        </div>
                      </div>
                    );
                  })}
                  {!galleryLoading && mediaItems.filter(it => it.storage_kind !== "event").length === 0 ? <p className="text-sm text-muted">暂无媒体文件。</p> : null}
                </div>
              </>
            ) : (
              <>
                <div className="mb-4 flex items-center justify-between">
                  <p className="text-xs text-muted">自动保存的检测触发快照。</p>
                  <button onClick={() => void refreshMedia()} disabled={galleryLoading} className="text-xs text-accent-600 hover:underline">刷新记录</button>
                </div>
                <div className="max-h-[360px] space-y-2 overflow-auto pr-1 flex-1">
                  {mediaItems.filter(it => it.storage_kind === "event").slice(0, 15).map((item) => {
                    const fileUrl = `${API_BASE}/api/media/files/${encodeURIComponent(item.storage_kind)}/${encodeURIComponent(item.filename)}?token=${encodeURIComponent(token)}`;
                    // Extract label from filename if possible: 20260419_120000_label.jpg
                    const parts = item.filename.replace(".jpg", "").split("_");
                    const label = parts.length >= 3 ? parts.slice(2).join(" ") : "检测事件";
                    const timeStr = parts.length >= 2 ? `${parts[0].slice(0,4)}-${parts[0].slice(4,6)}-${parts[0].slice(6,8)} ${parts[1].slice(0,2)}:${parts[1].slice(2,4)}:${parts[1].slice(4,6)}` : new Date(item.created_at).toLocaleString();

                    return (
                      <div key={`${item.storage_kind}-${item.filename}`} className="rounded-xl border border-base bg-panel/90 p-3 text-sm text-tertiary shadow-sm hover:border-accent-300 transition-colors">
                        <div className="flex items-center gap-3">
                          <div className="h-12 w-16 shrink-0 overflow-hidden rounded-md border border-base bg-black">
                            <img src={fileUrl} className="h-full w-full object-cover opacity-80 hover:opacity-100 transition-opacity" alt="event" />
                          </div>
                          <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                               <span className="rounded bg-rose-50 px-1.5 py-0.5 text-[10px] font-bold text-rose-600 uppercase tracking-tight border border-rose-100">触发</span>
                               <p className="truncate font-semibold text-secondary">{label}</p>
                            </div>
                            <p className="mt-1 text-[11px] text-disabled font-mono">{timeStr}</p>
                          </div>
                          <a href={fileUrl} target="_blank" rel="noreferrer" className="shrink-0 rounded-md border border-base bg-card px-2 py-1 text-[11px] text-tertiary hover:bg-panel">
                            查看
                          </a>
                        </div>
                      </div>
                    );
                  })}
                  {!galleryLoading && mediaItems.filter(it => it.storage_kind === "event").length === 0 ? <p className="text-sm text-muted">暂无检测记录。</p> : null}
                </div>
              </>
            )}
          </Panel>

          <div className="xl:col-span-7 h-full">
            <Panel
              className="h-full"
              title="日志输出"
              subtitle="查看近期系统日志。"
              right={
                <div className="flex items-center gap-2">
                  <select
                    value={logFilter}
                    onChange={(event) => setLogFilter(event.target.value as (typeof logFilters)[number])}
                    className="rounded-full border border-base bg-panel/80 px-3 py-2 text-sm text-tertiary"
                  >
                    {logFilters.map((item) => (
                      <option key={item} value={item}>
                        {item}
                      </option>
                    ))}
                  </select>
                  <button onClick={() => setLogs([])} className="rounded-full border border-base bg-panel/80 px-3 py-2 text-sm text-tertiary hover:bg-card">
                    清空
                  </button>
                </div>
              }
            >
              <div className="max-h-[320px] overflow-auto rounded-xl border border-base bg-card p-4 font-mono text-xs leading-6 text-tertiary">
                {filteredLogs.length === 0 ? <p className="text-muted">No logs.</p> : null}
                {filteredLogs.map((item, index) => (
                  <div key={`${item.timestamp}-${index}`} className="border-b border-base/60 py-1 last:border-b-0">
                    <span className="text-accent-600">{new Date(item.timestamp).toLocaleTimeString()}</span>{" "}
                    <span className="text-disabled">[{item.channel}]</span> {item.message}
                  </div>
                ))}
              </div>
              {error ? <p className="mt-3 rounded-xl border border-red-500/20 bg-red-500/10 px-4 py-3 text-sm text-red-300">失败提示：{error}</p> : null}
            </Panel>
          </div>
        </div> : null}

      </div>
      </main>
    </div>
  );
}

function ActionButton({ icon, label, onClick, disabled = false }: { icon: ReactNode; label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={classNames(
        "flex items-center justify-center gap-2 rounded-lg border border-base bg-panel px-3 py-2 text-[13px] font-medium text-secondary shadow-sm transition-colors",
        disabled ? "cursor-not-allowed opacity-50 bg-card" : "hover:bg-card hover:text-accent-600 focus:outline-none focus:ring-2 focus:ring-accent-500/40",
      )}
    >
      {icon}
      {label}
    </button>
  );
}

function ControlButton({ icon, title, onClick, disabled = false, variant = "default" }: { icon: ReactNode; title: string; onClick: () => void; disabled?: boolean; variant?: "default" | "danger" }) {
  return (
    <button
      title={title}
      onClick={onClick}
      disabled={disabled}
      className={classNames(
        "flex h-16 w-16 items-center justify-center rounded-xl border shadow-sm transition-all active:scale-90",
        disabled
          ? "cursor-not-allowed border-base bg-card text-disabled opacity-60"
          : variant === "danger"
            ? "border-red-200 bg-panel text-red-500 hover:border-red-400 hover:bg-red-50 focus:outline-none focus:ring-2 focus:ring-red-500/40"
            : "border-base bg-panel text-secondary hover:border-accent-300 hover:bg-accent-50 hover:text-accent-600 focus:outline-none focus:ring-2 focus:ring-accent-500/40",
      )}
    >
      {icon}
    </button>
  );
}

function CruiseDirectionButton({
  icon,
  title,
  onPress,
  onRelease,
  disabled = false,
  active = false,
}: {
  icon: ReactNode;
  title: string;
  onPress: () => void;
  onRelease: () => void;
  disabled?: boolean;
  active?: boolean;
}) {
  return (
    <button
      type="button"
      title={title}
      disabled={disabled}
      style={{ touchAction: "none" }}
      onPointerDown={(event) => {
        event.preventDefault();
        event.currentTarget.setPointerCapture(event.pointerId);
        onPress();
      }}
      onPointerUp={(event) => {
        event.preventDefault();
        onRelease();
      }}
      onPointerCancel={onRelease}
      className={classNames(
        "flex h-14 items-center justify-center rounded-xl border text-secondary transition-all active:scale-95",
        active ? "border-accent-300 bg-accent-100 text-accent-700" : "border-base bg-panel hover:border-accent-300 hover:bg-accent-50 hover:text-accent-600",
        disabled && "cursor-not-allowed opacity-50",
      )}
    >
      {icon}
    </button>
  );
}

function StatusPill({ tone, children }: { tone: "good" | "warn" | "danger" | "neutral"; children: ReactNode }) {
  return (
    <span
      className={classNames(
        "inline-flex items-center rounded-md border px-2 py-0.5 text-[11px] font-medium tracking-wide",
        tone === "good" && "border-emerald-200 bg-emerald-50 text-emerald-700",
        tone === "warn" && "border-amber-200 bg-amber-50 text-amber-700",
        tone === "danger" && "border-rose-200 bg-rose-50 text-rose-700",
        tone === "neutral" && "border-base bg-card text-tertiary",
      )}
    >
      {children}
    </span>
  );
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between border-b border-divider py-2 last:border-0 last:pb-0">
      <span className="text-[13px] text-muted">{label}</span>
      <span className="text-[13px] font-medium text-primary break-words text-right">{value}</span>
    </div>
  );
}

function MetaCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border border-base bg-card/50 p-3">
      <p className="text-[11px] font-medium uppercase tracking-wider text-muted">{label}</p>
      <p className="mt-1 truncate text-[13px] font-semibold text-primary" title={value}>{value}</p>
    </div>
  );
}

function StatCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg border-l-2 border-l-accent-400 border-y border-r border-y-slate-200 border-r-slate-200 bg-panel p-3 shadow-sm">
      <p className="text-[11px] font-medium uppercase tracking-wider text-muted">{label}</p>
      <p className="mt-1 truncate text-base font-semibold text-primary">{value}</p>
    </div>
  );
}

const THEMES = [
  { id: "light", label: "浅色控制台", icon: SunIcon },
  { id: "dark", label: "暗黑工业风", icon: MoonIcon },
  { id: "warm", label: "低反差暖灰", icon: SwatchIcon },
] as const;

function ThemeToggle() {
  const [theme, setTheme] = useState(() => localStorage.getItem("app-theme") || "light");

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("app-theme", theme);
  }, [theme]);

  return (
    <div className="flex items-center gap-1 rounded-md border border-base bg-card p-1 shadow-sm">
      {THEMES.map((t) => {
        const Icon = t.icon;
        const isActive = theme === t.id;
        return (
          <button
            key={t.id}
            onClick={() => setTheme(t.id)}
            title={t.label}
            className={classNames(
              "rounded p-1.5 transition-colors",
              isActive ? "bg-panel text-accent-500 shadow-sm" : "text-tertiary hover:bg-base/50"
            )}
          >
            <Icon className="h-4 w-4" />
          </button>
        );
      })}
    </div>
  );
}

export default App;

function pgmArrayBufferToDataUrl(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  const { width, height, data } = parsePgm(bytes);
  const canvas = document.createElement("canvas");
  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    throw new Error("canvas 2d context is unavailable");
  }
  const image = ctx.createImageData(width, height);
  for (let index = 0; index < data.length; index += 1) {
    const value = data[index];
    const offset = index * 4;
    image.data[offset] = value;
    image.data[offset + 1] = value;
    image.data[offset + 2] = value;
    image.data[offset + 3] = 255;
  }
  ctx.putImageData(image, 0, 0);
  return canvas.toDataURL("image/png");
}

function parsePgm(bytes: Uint8Array): { width: number; height: number; data: Uint8Array } {
  let offset = 0;
  const readToken = (): string => {
    while (offset < bytes.length) {
      const value = bytes[offset];
      if (value === 35) {
        while (offset < bytes.length && bytes[offset] !== 10) offset += 1;
      }
      if (value > 32) break;
      offset += 1;
    }
    const start = offset;
    while (offset < bytes.length && bytes[offset] > 32) offset += 1;
    return new TextDecoder().decode(bytes.slice(start, offset));
  };

  const magic = readToken();
  const width = Number(readToken());
  const height = Number(readToken());
  const maxValue = Number(readToken());
  if (!Number.isFinite(width) || !Number.isFinite(height) || !Number.isFinite(maxValue)) {
    throw new Error("invalid pgm header");
  }
  while (offset < bytes.length && bytes[offset] <= 32) offset += 1;
  if (magic === "P5") {
    const raw = bytes.slice(offset, offset + width * height);
    if (maxValue === 255) return { width, height, data: raw };
    const scaled = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i += 1) {
      scaled[i] = Math.round((raw[i] / maxValue) * 255);
    }
    return { width, height, data: scaled };
  }
  if (magic === "P2") {
    const text = new TextDecoder().decode(bytes.slice(offset));
    const tokens = text.split(/\s+/).filter(Boolean);
    const scaled = new Uint8Array(width * height);
    for (let i = 0; i < scaled.length; i += 1) {
      scaled[i] = Math.round((Number(tokens[i] ?? 0) / maxValue) * 255);
    }
    return { width, height, data: scaled };
  }
  throw new Error(`unsupported pgm format: ${magic}`);
}
