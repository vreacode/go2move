import { useState } from "react";
import { apiFetch } from "../lib";

export type FollowStatus = {
  active: boolean; mode: "preview"; state: string; reason: string;
  distance_m: number; depth_m: number | null; bearing_rad: number | null;
  track_id: number | null; suggested_velocity: [number, number, number];
  motion_enabled: false; physical_execution: false;
};

export function Go2FollowPanel({ token, status, ready, onChanged }: {
  token: string; status?: FollowStatus; ready: boolean; onChanged: () => Promise<void>;
}) {
  const [pending, setPending] = useState(false);
  const [distance, setDistance] = useState("1.8");
  const [error, setError] = useState("");
  async function command(action: "start" | "stop") {
    setPending(true); setError("");
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 12000);
    try {
      await apiFetch(`/api/go2/person/follow/${action}`, token, { method: "POST",
        body: JSON.stringify(action === "start" ? { mode: "preview", distance_m: Number(distance) } : {}),
        signal: controller.signal });
      await onChanged();
    } catch (failure) {
      let message = String(failure).replace(/^Error:\s*/, "");
      try { message = String(JSON.parse(message).detail || message); } catch { /* plain error */ }
      setError(message);
    } finally { window.clearTimeout(timer); setPending(false); }
  }
  const velocity = status?.suggested_velocity || [0, 0, 0];
  return <section className="mt-4 rounded-xl border border-base bg-card p-4" aria-label="深度相机目标跟随">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-sm font-semibold text-primary">目标跟随</h3>
      <span className="text-xs text-muted">无运动预览 · 不驱动机器狗</span>
    </div>
    <p className="mt-2 text-xs leading-relaxed text-muted">拍照选定人物后，预览锁定、测距和跟随建议。目标丢失、换轨或画面超时即停止；不自动换人。</p>
    <div className="mt-3 flex flex-wrap items-center gap-2">
      <label className="text-xs text-secondary">保持距离 <select aria-label="跟随距离" value={distance} disabled={pending || status?.active}
        onChange={e => setDistance(e.target.value)} className="ml-1 rounded-lg border border-base bg-panel px-2 py-2">
        <option value="1.5">1.5 米</option><option value="1.8">1.8 米</option><option value="2.0">2.0 米</option><option value="2.5">2.5 米</option>
      </select></label>
      <button disabled={pending || !ready || status?.active} onClick={() => void command("start")}
        className="rounded-lg border border-base px-3 py-2 text-sm text-secondary disabled:opacity-50">开始跟随预览</button>
      <button disabled={pending || !status?.active} onClick={() => void command("stop")}
        className="rounded-lg border border-danger/30 px-3 py-2 text-sm text-danger disabled:opacity-50">停止预览</button>
    </div>
    {!ready ? <p className="mt-2 text-xs text-muted">需要照片目标与已连接的 D435i 对齐深度。</p> : null}
    <p className="mt-3 text-sm text-secondary" role="status">{status?.reason || "尚未开始。"}</p>
    {status?.active || status?.state === "stopped" ? <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted">
      <span>目标距离 {status.depth_m === null ? "—" : `${status.depth_m.toFixed(2)} 米`}</span>
      <span>方向 {status.bearing_rad === null ? "—" : `${(status.bearing_rad * 180 / Math.PI).toFixed(0)}°（左正）`}</span>
      <span>建议前进 {velocity[0].toFixed(2)} m/s</span><span>建议转向 {velocity[2].toFixed(2)} rad/s</span>
    </div> : null}
    {error ? <p role="alert" className="mt-2 text-xs text-danger">{error}</p> : null}
  </section>;
}
