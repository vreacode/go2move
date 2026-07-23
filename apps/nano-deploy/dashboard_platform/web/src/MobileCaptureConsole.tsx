import { useEffect, useRef, useState, type FormEvent, type ReactNode, type TouchEvent } from "react";
import {
  ArchiveBoxIcon,
  ArrowLeftIcon,
  ArrowPathIcon,
  BoltIcon,
  CameraIcon,
  ChartBarIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  ChevronUpIcon,
  CheckCircleIcon,
  ClockIcon,
  ExclamationTriangleIcon,
  MapPinIcon,
  PhotoIcon,
  PlayCircleIcon,
  StopCircleIcon,
  WifiIcon,
} from "@heroicons/react/24/outline";
import { VideoStream } from "./components/VideoStream";
import { API_BASE, apiFetch, clearToken, loadToken, saveToken, wsUrl } from "./lib";
import type { CaptureFrameItem, CaptureGalleryItem, CaptureStatus, ConnectionState, SiyiCameraStatus, SystemStatus } from "./types";

type LoginResponse = { token: string; username: string };
type Tab = "capture" | "monitor" | "archive";
type CaptureMode = "visible_raw" | "ir_raw";
const cruiseSpeeds = ["low", "medium", "high"] as const;

function explainMobileError(error: unknown) {
  const message = String(error ?? "").replace(/^Error:\s*/i, "").trim();
  if (message.includes("invalid credentials")) return "用户名或密码不正确。";
  if (message.includes("invalid token") || message.includes("missing bearer token")) return "登录已过期，请重新登录。";
  if (message.includes("capture is already running")) return "已有采集任务在运行。";
  if (message.includes("capture script not found")) return "采集脚本未部署到板端。";
  if (message.includes("source is not configured")) return "当前视频源未配置。";
  if (message.includes("manual control is locked")) return "手动控制已锁定，请先在控制台解锁。";
  if (message.includes("navigation is running")) return "导航正在运行，请先停止导航或启用人工接管。";
  if (message.includes("base is not ready") || message.includes("chassis topics are not ready")) return "底盘基础链路未就绪：请检查底盘电源、串口和 ROS 节点。";
  return message || "操作失败，请稍后重试。";
}

function formatAge(timestamp?: string | null) {
  if (!timestamp) return "等待数据";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(timestamp).getTime()) / 1000));
  if (seconds < 5) return "刚刚更新";
  if (seconds < 60) return `${seconds} 秒前`;
  return `${Math.round(seconds / 60)} 分钟前`;
}

function captureModeLabel(mode: CaptureMode) {
  return mode === "ir_raw" ? "红外" : "可见光";
}

function galleryModeLabel(mode: CaptureGalleryItem["stream_mode"]) {
  if (mode === "ir_raw") return "红外";
  if (mode === "visible_raw") return "可见光";
  return "来源未标记";
}

function formatCaptureTime(timestamp?: string | null) {
  if (!timestamp) return "时间未知";
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return timestamp;
  return date.toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function isTransientCaptureWarning(message?: string | null) {
  return !!message && /(?:hevc|h264).*Could not find ref with POC|Could not find ref with POC/i.test(message);
}

function galleryFrameUrl(token: string, group: string, frameName?: string) {
  const endpoint = frameName
    ? `/api/capture/gallery/${encodeURIComponent(group)}/frames/${encodeURIComponent(frameName)}`
    : `/api/capture/gallery/${encodeURIComponent(group)}/latest`;
  return `${API_BASE}${endpoint}?token=${encodeURIComponent(token)}`;
}

function batteryLabel(status: SystemStatus | null) {
  const battery = status?.battery_status;
  if (!battery?.available) return "未知";
  if (battery.percentage != null) return `${battery.percentage.toFixed(0)}%`;
  if (battery.voltage != null) return `${battery.voltage.toFixed(1)}V`;
  return "已连接";
}

function batteryDetailLabel(status: SystemStatus | null) {
  const battery = status?.battery_status;
  if (!battery?.available) return "未知";
  const details = [
    battery.percentage != null ? `${battery.percentage.toFixed(0)}%` : null,
    battery.voltage != null ? `${battery.voltage.toFixed(1)}V` : null,
  ].filter(Boolean);
  return details.join(" · ") || "已连接";
}

function baseReady(status: SystemStatus | null) {
  const base = status?.base_status;
  return !!base?.wheeltec_robot_online && !!base?.cmd_vel_subscriber_online && !!base?.odom_online;
}

function captureProgress(capture: CaptureStatus | null) {
  if (!capture?.running || !capture.max_frames) return 0;
  return Math.min(100, Math.round((capture.frames_saved / capture.max_frames) * 100));
}

function Metric({ label, value, tone = "neutral" }: { label: string; value: string; tone?: "neutral" | "good" | "warn" }) {
  return (
    <div className={`mobile-metric mobile-metric-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function BaseHealth({ status }: { status: SystemStatus | null }) {
  const base = status?.base_status;
  const checks = [
    { label: "底盘节点", ready: !!base?.wheeltec_robot_online },
    { label: "速度接口", ready: !!base?.cmd_vel_subscriber_online },
    { label: "里程计", ready: !!base?.odom_online },
  ];
  const ready = baseReady(status);
  const percentage = status?.battery_status?.percentage;
  return (
    <section className={`mobile-base-health ${ready ? "is-ready" : "is-checking"}`}>
      <div className="mobile-base-health-heading">
        <div>
          <span className="mobile-eyebrow">CHASSIS LINK</span>
          <h3>{ready ? "底盘可控制" : base?.running ? "底盘正在启动" : "底盘待启动"}</h3>
        </div>
        <span className="mobile-base-health-state">{ready ? "READY" : "CHECK"}</span>
      </div>
      <div className="mobile-base-checks">
        {checks.map((item) => (
          <div key={item.label} className={item.ready ? "is-ready" : ""}>
            <span className="mobile-check-dot" />
            <span>{item.label}</span>
          </div>
        ))}
      </div>
      <div className="mobile-battery-line">
        <div className="mobile-battery-label"><BoltIcon /><span>电池</span><strong>{batteryDetailLabel(status)}</strong></div>
        <div className="mobile-battery-track" aria-label={`电量 ${batteryLabel(status)}`}><span style={{ width: `${percentage == null ? 0 : Math.max(4, Math.min(100, percentage))}%` }} /></div>
      </div>
      {!ready && <p className="mobile-base-health-note">启动匀速前进时会自动检查并启动底盘，确认三项链路全部在线后才允许控制。</p>}
    </section>
  );
}

function MobileCruiseButton({
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
      className={`mobile-cruise-direction ${active ? "is-active" : ""}`}
    >
      {icon}
    </button>
  );
}

function Login({ onLogin }: { onLogin: (token: string) => void }) {
  const [form, setForm] = useState({ username: "admin", password: "" });
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      const response = await apiFetch<LoginResponse>("/api/auth/login", "", {
        method: "POST",
        body: JSON.stringify(form),
      });
      saveToken(response.token);
      onLogin(response.token);
    } catch (err) {
      setError(explainMobileError(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <main className="mobile-auth">
      <div className="mobile-auth-mark"><CameraIcon /></div>
      <p className="mobile-kicker">CARMOVE / FIELD CONSOLE</p>
      <h1>让每一段现场<br /><em>马上可用。</em></h1>
      <p className="mobile-auth-copy">通过 Tailscale 连接小车，实时查看视频，记录带场景和备注的训练素材。</p>
      <form className="mobile-auth-form" onSubmit={submit}>
        <label>账号<input value={form.username} onChange={(event) => setForm({ ...form, username: event.target.value })} autoComplete="username" /></label>
        <label>密码<input type="password" value={form.password} onChange={(event) => setForm({ ...form, password: event.target.value })} autoComplete="current-password" /></label>
        {error ? <div className="mobile-inline-error"><ExclamationTriangleIcon />{error}</div> : null}
        <button className="mobile-primary-button" disabled={pending}>{pending ? "正在连接…" : "进入现场控制台"}</button>
      </form>
      <p className="mobile-auth-foot"><WifiIcon /> Tailscale 私有网络 · 不暴露公网</p>
    </main>
  );
}

export default function MobileCaptureConsole() {
  const [token, setToken] = useState(loadToken());
  const [tab, setTab] = useState<Tab>("capture");
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [cameraStatus, setCameraStatus] = useState<SiyiCameraStatus | null>(null);
  const [capture, setCapture] = useState<CaptureStatus | null>(null);
  const [gallery, setGallery] = useState<CaptureGalleryItem[]>([]);
  const [selectedGallery, setSelectedGallery] = useState<CaptureGalleryItem | null>(null);
  const [galleryFrames, setGalleryFrames] = useState<CaptureFrameItem[]>([]);
  const [selectedFrameIndex, setSelectedFrameIndex] = useState(0);
  const [galleryViewerLoading, setGalleryViewerLoading] = useState(false);
  const viewerTouchStartRef = useRef<number | null>(null);
  const [videoState, setVideoState] = useState<ConnectionState>("idle");
  const [useFallback, setUseFallback] = useState(false);
  const [streamMode, setStreamMode] = useState<CaptureMode>("visible_raw");
  const [scene, setScene] = useState("gate_a_day");
  const [note, setNote] = useState("园区入口，白天，垃圾和车辆");
  const [interval, setIntervalSeconds] = useState(1);
  const [durationMinutes, setDurationMinutes] = useState(3);
  const [pending, setPending] = useState<"start" | "stop" | "snapshot" | "refresh" | "">("");
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [cruiseSpeed, setCruiseSpeed] = useState<(typeof cruiseSpeeds)[number]>("low");
  const [cruisePending, setCruisePending] = useState<"prepare" | "start" | "stop" | "">("");
  const cruiseSteeringRequestRef = useRef<Promise<void> | null>(null);
  const cruiseSteeringDesiredRef = useRef<"center" | "left" | "right">("center");

  const online = !!status?.agent_online && !!status?.board_online;
  const cameraOnline = !!cameraStatus?.connected;
  const chassisReady = baseReady(status);
  const cruiseActive = status?.motion_state?.cruise_active ?? false;
  const cruiseSteering = status?.motion_state?.cruise_steering ?? "center";
  const cruiseBlocked = !status?.motion_state || status.motion_state.nav_lockout;
  const latestFrameUrl = capture?.latest_frame
    ? `${API_BASE}/api/capture/latest?token=${encodeURIComponent(token)}&v=${capture.frames_saved}`
    : "";

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    const socket = new WebSocket(wsUrl("/ws/status", token));
    socket.onmessage = (event) => {
      if (!cancelled) setStatus(JSON.parse(event.data) as SystemStatus);
    };
    socket.onclose = () => {
      if (!cancelled) setStatus((previous) => previous ? { ...previous, agent_online: false } : previous);
    };
    void apiFetch<SystemStatus>("/api/system/status", token).then((payload) => {
      if (!cancelled) setStatus(payload);
    }).catch((err) => {
      if (!cancelled) setError(explainMobileError(err));
    });
    return () => {
      cancelled = true;
      socket.close();
    };
  }, [token]);

  useEffect(() => {
    if (!token || !cruiseActive) return;
    let cancelled = false;
    const heartbeat = () => {
      void apiFetch<SystemStatus["motion_state"]>("/api/control/cruise/heartbeat", token, { method: "POST" })
        .then((payload) => {
          if (!cancelled && payload) setStatus((previous) => (previous ? { ...previous, motion_state: payload } : previous));
        })
        .catch((err) => {
          if (!cancelled) {
            setError(explainMobileError(err));
            setStatus((previous) => (previous && previous.motion_state ? { ...previous, motion_state: { ...previous.motion_state, cruise_active: false, cruise_speed: null, cruise_steering: "center" } } : previous));
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
    if (!token || tab !== "archive") return;
    let cancelled = false;
    const refresh = () => {
      void apiFetch<{ items: CaptureGalleryItem[] }>("/api/capture/gallery?page=1&page_size=20", token)
        .then((payload) => {
          if (!cancelled) setGallery(payload.items);
        })
        .catch((err) => {
          if (!cancelled) setError(explainMobileError(err));
        });
    };
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [tab, token]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    const refresh = () => {
      void apiFetch<CaptureStatus>("/api/capture/status", token).then((payload) => {
        if (!cancelled) {
          setCapture(payload);
          if (payload.running && payload.stream_mode) setStreamMode(payload.stream_mode);
        }
      }).catch((err) => {
        if (!cancelled) setError(explainMobileError(err));
      });
    };
    refresh();
    const timer = window.setInterval(refresh, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [token]);

  useEffect(() => {
    if (!token) return;
    let cancelled = false;
    const refresh = () => void apiFetch<SiyiCameraStatus>("/api/camera/status", token).then((payload) => {
      if (!cancelled) setCameraStatus(payload);
    }).catch(() => {
      if (!cancelled) setCameraStatus({ enabled: true, connected: false, error: "状态查询失败" });
    });
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [token]);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(""), 2600);
    return () => window.clearTimeout(timer);
  }, [toast]);

  async function refreshCapture() {
    setPending("refresh");
    try {
      const payload = await apiFetch<CaptureStatus>("/api/capture/status", token);
      setCapture(payload);
      if (payload.running && payload.stream_mode) setStreamMode(payload.stream_mode);
      setToast("状态已刷新");
    } catch (err) {
      setError(explainMobileError(err));
    } finally {
      setPending("");
    }
  }

  async function refreshGallery() {
    try {
      const payload = await apiFetch<{ items: CaptureGalleryItem[] }>("/api/capture/gallery?page=1&page_size=20", token);
      setGallery(payload.items);
    } catch (err) {
      setError(explainMobileError(err));
    }
  }

  async function openGallery(item: CaptureGalleryItem) {
    setSelectedGallery(item);
    setGalleryFrames([]);
    setSelectedFrameIndex(0);
    setGalleryViewerLoading(true);
    setError("");
    try {
      const payload = await apiFetch<{ items: CaptureFrameItem[] }>(
        `/api/capture/gallery/${encodeURIComponent(item.group)}/frames`,
        token,
      );
      setGalleryFrames(payload.items);
    } catch (err) {
      setError(explainMobileError(err));
    } finally {
      setGalleryViewerLoading(false);
    }
  }

  function closeGallery() {
    setSelectedGallery(null);
    setGalleryFrames([]);
    setSelectedFrameIndex(0);
  }

  function moveGalleryFrame(step: number) {
    setSelectedFrameIndex((current) => Math.min(Math.max(current + step, 0), Math.max(galleryFrames.length - 1, 0)));
  }

  function handleViewerTouchStart(event: TouchEvent<HTMLDivElement>) {
    viewerTouchStartRef.current = event.touches[0]?.clientX ?? null;
  }

  function handleViewerTouchEnd(event: TouchEvent<HTMLDivElement>) {
    const startX = viewerTouchStartRef.current;
    const endX = event.changedTouches[0]?.clientX;
    viewerTouchStartRef.current = null;
    if (startX == null || endX == null || Math.abs(endX - startX) < 44) return;
    moveGalleryFrame(endX < startX ? 1 : -1);
  }

  useEffect(() => {
    if (!selectedGallery) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "ArrowLeft") moveGalleryFrame(-1);
      if (event.key === "ArrowRight") moveGalleryFrame(1);
      if (event.key === "Escape") closeGallery();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selectedGallery, galleryFrames.length]);

  async function startCruise() {
    cruiseSteeringDesiredRef.current = "center";
    setCruisePending("prepare");
    setError("");
    try {
      const current = await apiFetch<SystemStatus>("/api/system/status", token);
      setStatus(current);
      if (current.navigation_running) throw new Error("navigation is running");
      const baseReady = !!current.base_status?.wheeltec_robot_online && !!current.base_status?.cmd_vel_subscriber_online && !!current.base_status?.odom_online;
      if (!baseReady) {
        await apiFetch("/api/base/start", token, { method: "POST" });
      }
      const readyStatus = await apiFetch<SystemStatus>("/api/system/status", token);
      setStatus(readyStatus);
      const readyBase = !!readyStatus.base_status?.wheeltec_robot_online && !!readyStatus.base_status?.cmd_vel_subscriber_online && !!readyStatus.base_status?.odom_online;
      if (!readyBase) throw new Error("base is not ready");
      if (readyStatus.motion_state?.mode !== "real") {
        const realState = await apiFetch<SystemStatus["motion_state"]>("/api/control/mode", token, {
          method: "POST",
          body: JSON.stringify({ mode: "real" }),
        });
        setStatus((previous) => (previous && realState ? { ...previous, motion_state: realState } : previous));
      }
      setCruisePending("start");
      const payload = await apiFetch<SystemStatus["motion_state"]>("/api/control/cruise/start", token, {
        method: "POST",
        body: JSON.stringify({ speed: cruiseSpeed, auto_arm: true }),
      });
      setStatus((previous) => (previous && payload ? { ...previous, motion_state: payload } : previous));
      setToast("匀速前进已启动");
    } catch (err) {
      setError(explainMobileError(err));
    } finally {
      setCruisePending("");
    }
  }

  async function stopCruise() {
    cruiseSteeringDesiredRef.current = "center";
    setCruisePending("stop");
    try {
      const payload = await apiFetch<SystemStatus["motion_state"]>("/api/control/cruise/stop", token, { method: "POST" });
      setStatus((previous) => (previous && payload ? { ...previous, motion_state: payload } : previous));
      setToast("车辆已停止");
    } catch (err) {
      setError(explainMobileError(err));
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
          const payload = await apiFetch<SystemStatus["motion_state"]>("/api/control/cruise/steer", token, {
            method: "POST",
            body: JSON.stringify({ direction: nextDirection }),
          });
          setStatus((previous) => (previous && payload ? { ...previous, motion_state: payload } : previous));
        } catch (err) {
          setError(explainMobileError(err));
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

  async function startCapture() {
    setPending("start");
    setError("");
    try {
      const payload = await apiFetch<CaptureStatus>("/api/capture/start", token, {
        method: "POST",
        body: JSON.stringify({
          stream_mode: streamMode,
          scene,
          note,
          interval_seconds: interval,
          duration_seconds: durationMinutes * 60,
          max_frames: 0,
          jpeg_quality: 95,
        }),
      });
      setCapture(payload);
      if (payload.stream_mode) setStreamMode(payload.stream_mode);
      setToast("采集已开始");
    } catch (err) {
      setError(explainMobileError(err));
    } finally {
      setPending("");
    }
  }

  async function stopCapture() {
    setPending("stop");
    try {
      const payload = await apiFetch<CaptureStatus>("/api/capture/stop", token, { method: "POST" });
      setCapture(payload);
      if (payload.stream_mode) setStreamMode(payload.stream_mode);
      setToast("采集已停止，manifest 已保存");
    } catch (err) {
      setError(explainMobileError(err));
    } finally {
      setPending("");
    }
  }

  async function takeSnapshot() {
    setPending("snapshot");
    try {
      await apiFetch("/api/media/snapshot", token, {
        method: "POST",
        body: JSON.stringify({ stream_mode: streamMode }),
      });
      setToast("单帧已保存");
    } catch (err) {
      setError(explainMobileError(err));
    } finally {
      setPending("");
    }
  }

  function logout() {
    clearToken();
    setToken("");
  }

  if (!token) return <Login onLogin={setToken} />;

  return (
    <main className="mobile-app">
      <header className="mobile-topbar">
        <div>
          <p className="mobile-kicker">CARMOVE / FIELD CONSOLE</p>
          <h1>{tab === "capture" ? "现场采集" : tab === "monitor" ? "链路监控" : "采集记录"}</h1>
        </div>
        <div className="mobile-topbar-actions">
          <div className={`mobile-topbar-battery ${status?.battery_status?.low ? "is-warn" : ""}`}><BoltIcon /><strong>{batteryLabel(status)}</strong></div>
          <button className="mobile-icon-button" aria-label="刷新状态" onClick={() => { void refreshCapture(); if (tab === "archive") void refreshGallery(); }} disabled={pending === "refresh"}>
            <ArrowPathIcon className={pending === "refresh" ? "mobile-spin" : ""} />
          </button>
        </div>
      </header>

      <section className="mobile-status-strip">
        <span className={`mobile-status-dot ${online ? "is-good" : "is-bad"}`} />
        <strong>{online ? "小车在线" : "等待小车"}</strong>
        <span className="mobile-status-divider" />
        <span>{cameraOnline ? "视频已连接" : "视频未连接"}</span>
        <span className="mobile-status-divider" />
        <span className={chassisReady ? "mobile-status-ready" : ""}>{chassisReady ? "底盘就绪" : "底盘待检查"}</span>
        <button onClick={logout}>退出</button>
      </section>

      {error ? <button className="mobile-error-banner" onClick={() => setError("")}><ExclamationTriangleIcon />{error}<span>×</span></button> : null}
      {toast ? <div className="mobile-toast"><CheckCircleIcon />{toast}</div> : null}

      {tab === "capture" ? (
        <div className="mobile-page mobile-capture-page">
          <section className="mobile-video-card">
            <VideoStream mode={streamMode} token={token} useFallback={useFallback} onStateChange={setVideoState} />
            <div className="mobile-video-footer">
              <span className={`mobile-video-state ${videoState === "connected" ? "is-good" : ""}`}><span />{videoState === "connected" ? "实时画面" : "正在连接"}</span>
              <button onClick={() => setUseFallback((value) => !value)}>{useFallback ? "切回低延迟" : "备用画面"}</button>
            </div>
          </section>

          <section className={`mobile-capture-card ${capture?.running ? "is-running" : ""}`}>
            <div className="mobile-section-heading">
              <div><span className="mobile-eyebrow">{capture?.running ? "CAPTURE IN PROGRESS" : "READY FOR FIELD"}</span><h2>{capture?.running ? `正在采集 ${capture.scene}` : "准备采集一段现场"}</h2></div>
              <div className={`mobile-capture-orb ${capture?.running ? "is-running" : ""}`}><CameraIcon /></div>
            </div>
            <div className="mobile-capture-counter"><strong>{capture?.frames_saved ?? 0}</strong><span>张已保存</span>{capture?.running ? <small>每 {capture.interval_seconds ?? interval}s 一张</small> : <small>保存为带 manifest 的原始帧</small>}</div>
            {capture?.running ? <div className="mobile-capture-progress-wrap"><div className="mobile-capture-progress"><span style={{ width: `${captureProgress(capture)}%` }} /></div><div><span>任务进度</span><strong>{capture.max_frames ? `${capture.frames_saved} / ${capture.max_frames} 帧` : "持续采集中"}</strong></div></div> : null}
            <div className="mobile-mode-switch" aria-label="选择采集视频源">
              <button className={streamMode === "visible_raw" ? "is-active" : ""} onClick={() => setStreamMode("visible_raw")} disabled={!!capture?.running}>
                <strong>可见光</strong><small>video1 · 1920×1080</small>
              </button>
              <button className={streamMode === "ir_raw" ? "is-active" : ""} onClick={() => setStreamMode("ir_raw")} disabled={!!capture?.running}>
                <strong>红外</strong><small>video2 · 640×512</small>
              </button>
            </div>
            <div className="mobile-form-grid">
              <label className="mobile-field mobile-field-wide"><span><MapPinIcon />场景名称</span><input value={scene} onChange={(event) => setScene(event.target.value)} disabled={!!capture?.running} placeholder="例如 gate_a_day" /></label>
              <label className="mobile-field mobile-field-wide"><span><ArchiveBoxIcon />现场备注</span><input value={note} onChange={(event) => setNote(event.target.value)} disabled={!!capture?.running} placeholder="光照、路线、目标说明" /></label>
              <label className="mobile-field"><span><ClockIcon />间隔</span><select value={interval} onChange={(event) => setIntervalSeconds(Number(event.target.value))} disabled={!!capture?.running}><option value={0.5}>0.5 秒</option><option value={1}>1 秒</option><option value={2}>2 秒</option><option value={5}>5 秒</option></select></label>
              <label className="mobile-field"><span><ChartBarIcon />时长</span><select value={durationMinutes} onChange={(event) => setDurationMinutes(Number(event.target.value))} disabled={!!capture?.running}><option value={1}>1 分钟</option><option value={3}>3 分钟</option><option value={5}>5 分钟</option><option value={10}>10 分钟</option></select></label>
            </div>
            {capture?.running ? (
              <button className="mobile-stop-button" onClick={() => void stopCapture()} disabled={pending !== ""}><StopCircleIcon />{pending === "stop" ? "正在停止…" : "停止采集"}</button>
            ) : (
              <button className="mobile-primary-button mobile-start-button" onClick={() => void startCapture()} disabled={pending !== "" || !online}><PlayCircleIcon />{pending === "start" ? "正在启动…" : `开始采集 ${captureModeLabel(streamMode)}`}</button>
            )}
            <button className="mobile-secondary-action" onClick={() => void takeSnapshot()} disabled={pending !== "" || !online}><PhotoIcon />只保存当前这一帧 · {captureModeLabel(streamMode)}</button>
          </section>
        </div>
      ) : null}

      {tab === "monitor" ? (
        <div className="mobile-page mobile-monitor-page">
          <section className="mobile-monitor-hero"><div className="mobile-monitor-signal"><WifiIcon /><span>{online ? "CONNECTED" : "OFFLINE"}</span></div><h2>{online ? "设备链路正常" : "等待设备重新上线"}</h2><p>最后心跳：{formatAge(status?.last_heartbeat)}</p></section>
          <div className="mobile-metric-grid">
            <Metric label="CPU" value={status?.cpu_percent != null ? `${status.cpu_percent.toFixed(0)}%` : "--"} />
            <Metric label="内存" value={status?.memory_percent != null ? `${status.memory_percent.toFixed(0)}%` : "--"} />
            <Metric label="GPU" value={status?.gpu_percent != null ? `${status.gpu_percent.toFixed(0)}%` : "--"} />
            <Metric label="电量" value={batteryLabel(status)} tone={status?.battery_status?.low ? "warn" : status?.battery_status?.available ? "good" : "neutral"} />
            <Metric label="相机" value={cameraOnline ? "在线" : "离线"} tone={cameraOnline ? "good" : "warn"} />
            <Metric label="雷达" value={status?.lidar_online ? "在线" : "停止"} tone={status?.lidar_online ? "good" : "neutral"} />
            <Metric label="IMU" value={status?.imu_online ? "在线" : "停止"} tone={status?.imu_online ? "good" : "neutral"} />
          </div>
          <BaseHealth status={status} />
          <section className="mobile-detail-card"><div className="mobile-detail-row"><span>视频源</span><strong>{status?.current_video_source || "--"}</strong></div><div className="mobile-detail-row"><span>分辨率目标</span><strong>video1 / 可见光</strong></div><div className="mobile-detail-row"><span>云台状态</span><strong>{cameraStatus?.status?.motion_mode_name || "--"} · {cameraStatus?.attitude?.yaw_deg?.toFixed(1) ?? "--"}° yaw</strong></div><div className="mobile-detail-row"><span>电池</span><strong>{batteryDetailLabel(status)}{status?.battery_status?.charging ? " · 充电中" : ""}</strong></div><div className="mobile-detail-row"><span>控制模式</span><strong>{status?.motion_state?.mode || "dry_run"}</strong></div></section>
          <section className="mobile-cruise-card">
            <div className="mobile-cruise-heading"><div><span className="mobile-eyebrow">MOTION ASSIST</span><h2>匀速前进</h2></div><span className={cruiseActive ? "mobile-cruise-status is-active" : "mobile-cruise-status"}>{cruiseActive ? (cruiseSteering === "center" ? "直行中" : cruiseSteering === "left" ? "左修正" : "右修正") : "待启动"}</span></div>
            <p className="mobile-cruise-copy">底盘持续保持低速前进，按住左右键修正方向，松开后自动回到直行。</p>
            <div className="mobile-cruise-speed"><span>速度档位</span>{cruiseSpeeds.map((item) => <button key={item} type="button" onClick={() => setCruiseSpeed(item)} disabled={cruiseActive} className={cruiseSpeed === item ? "is-active" : ""}>{item}</button>)}</div>
            <button type="button" className={`mobile-cruise-toggle ${cruiseActive ? "is-stop" : ""}`} onClick={() => void (cruiseActive ? stopCruise() : startCruise())} disabled={cruisePending !== "" || (!cruiseActive && cruiseBlocked)}>{cruisePending === "prepare" ? "正在启动底盘…" : cruisePending === "start" ? "正在启动…" : cruisePending === "stop" ? "正在停车…" : cruiseActive ? "停止匀速前进" : "开始匀速前进"}</button>
            {cruiseActive ? <div className="mobile-cruise-directions"><MobileCruiseButton icon={<ChevronLeftIcon />} title="按住左修正" onPress={() => void steerCruise("left")} onRelease={() => void steerCruise("center")} disabled={cruisePending !== ""} /><MobileCruiseButton icon={<ChevronUpIcon />} title="直行" onPress={() => void steerCruise("center")} onRelease={() => void steerCruise("center")} active={cruiseSteering === "center"} disabled={cruisePending !== ""} /><MobileCruiseButton icon={<ChevronRightIcon />} title="按住右修正" onPress={() => void steerCruise("right")} onRelease={() => void steerCruise("center")} disabled={cruisePending !== ""} /></div> : null}
            <small className="mobile-cruise-note">点击开始会自动启动底盘并切换 real；停止或断线后自动重新锁定。</small>
          </section>
          <section className="mobile-safety-note"><CheckCircleIcon /><div><strong>安全闸门保持开启</strong><p>网页断开或心跳超时约 0.75 秒，Agent 会自动发送停车指令。</p></div></section>
        </div>
      ) : null}

      {tab === "archive" ? (
        <div className="mobile-page mobile-archive-page">
          {selectedGallery ? (
            <section className="mobile-gallery-viewer">
              <button className="mobile-viewer-back" type="button" onClick={closeGallery}><ArrowLeftIcon />返回数据集列表</button>
              <div className="mobile-viewer-heading"><div><span className="mobile-eyebrow">DATASET VIEWER</span><h2>{selectedGallery.scene}</h2></div><span>{galleryModeLabel(selectedGallery.stream_mode)}</span></div>
              <div className="mobile-viewer-image-card" onTouchStart={handleViewerTouchStart} onTouchEnd={handleViewerTouchEnd}>
                {galleryViewerLoading ? <div className="mobile-viewer-loading"><ArrowPathIcon className="mobile-spin" />正在读取数据集…</div> : galleryFrames[selectedFrameIndex] ? <img src={galleryFrameUrl(token, selectedGallery.group, galleryFrames[selectedFrameIndex].name)} alt={`${selectedGallery.scene} 第 ${selectedFrameIndex + 1} 张`} /> : <div className="mobile-viewer-loading"><ArchiveBoxIcon />这个数据集没有可读取的图片</div>}
              </div>
              <div className="mobile-viewer-controls">
                <button type="button" onClick={() => moveGalleryFrame(-1)} disabled={selectedFrameIndex <= 0 || galleryViewerLoading}><ChevronLeftIcon /><span>上一张</span></button>
                <div><strong>{galleryFrames.length ? selectedFrameIndex + 1 : 0}</strong><span> / {galleryFrames.length}</span><small>{galleryFrames[selectedFrameIndex] ? formatCaptureTime(galleryFrames[selectedFrameIndex].captured_at) : "等待图片"}</small></div>
                <button type="button" onClick={() => moveGalleryFrame(1)} disabled={selectedFrameIndex >= galleryFrames.length - 1 || galleryViewerLoading}><span>下一张</span><ChevronRightIcon /></button>
              </div>
              <div className="mobile-viewer-note"><span>{selectedGallery.frames_saved} 张 · {selectedGallery.manifest_name}</span><p>{selectedGallery.note || "没有备注"}</p><small>左右滑动或使用下方按钮切换图片</small></div>
            </section>
          ) : <>
            <section className="mobile-archive-hero"><span className="mobile-eyebrow">LATEST CAPTURE</span><h2>{capture?.scene || "还没有采集任务"}</h2><p>{capture?.manifest_path || "开始一段采集后，这里会显示 manifest 和最近一帧。"}</p></section>
            {latestFrameUrl ? <img className="mobile-latest-frame" src={latestFrameUrl} alt="最近保存的采集帧" /> : <div className="mobile-empty-state"><ArchiveBoxIcon /><strong>等待第一段现场数据</strong><span>采集完成的原始帧会保存在小车上，并按场景和日期分组。</span></div>}
            <div className="mobile-archive-stats"><Metric label="已保存" value={`${capture?.frames_saved ?? 0} 帧`} /><Metric label="采样间隔" value={`${capture?.interval_seconds ?? "--"} 秒`} /><Metric label="状态" value={capture?.running ? "进行中" : "已停止"} tone={capture?.running ? "good" : "neutral"} /></div>
            <section className="mobile-history-card">
              <div className="mobile-section-heading"><div><span className="mobile-eyebrow">CAPTURE HISTORY</span><h2>历史采集段</h2></div><strong>{gallery.length}</strong></div>
              {gallery.length ? <div className="mobile-history-list">{gallery.map((item) => <button className="mobile-history-item" type="button" key={item.group} onClick={() => void openGallery(item)}>{item.latest_frame_name ? <img src={galleryFrameUrl(token, item.group)} alt={`${item.scene} 最近一帧`} loading="lazy" /> : null}<div className="mobile-history-item-body"><div className="mobile-history-item-top"><strong>{item.scene}</strong><span>{galleryModeLabel(item.stream_mode)} <ChevronRightIcon /></span></div><div className="mobile-history-meta"><span>{item.frames_saved} 帧</span><span>{formatCaptureTime(item.last_captured_at)}</span></div><p>{item.note || item.manifest_name}</p><small className="mobile-history-open-hint">点击查看全部图片</small></div></button>)}</div> : <div className="mobile-history-empty">还没有找到已完成的 manifest。</div>}
            </section>
          </>}
          {capture?.last_error && !isTransientCaptureWarning(capture.last_error) ? <div className="mobile-inline-error"><ExclamationTriangleIcon />{capture.last_error}</div> : null}
        </div>
      ) : null}

      <nav className="mobile-bottom-nav" aria-label="主导航">
        <button className={tab === "capture" ? "is-active" : ""} onClick={() => setTab("capture")}><CameraIcon /><span>采集</span></button>
        <button className={tab === "monitor" ? "is-active" : ""} onClick={() => setTab("monitor")}><ChartBarIcon /><span>监控</span></button>
        <button className={tab === "archive" ? "is-active" : ""} onClick={() => setTab("archive")}><ArchiveBoxIcon /><span>记录</span></button>
      </nav>
    </main>
  );
}
