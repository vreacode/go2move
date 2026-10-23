import { useEffect, useRef, useState } from "react";
import { apiFetch, classNames } from "../lib";
import { LocalPersonSpeech, type LocalDiscoveryStatus } from "./localPersonSpeech";

type HealthSummary = {
  connected: boolean; sampled_at: number | null; age_s: number | null;
  max_temperature_c: number | null; rear_left_hip_temperature_c?: number | null;
  body_height_m: number | null; soc: number | null;
  faults: { index: number; joint: string; code_hex: string; messages: string[] }[];
  motion_allowed: false; reason: string;
};

export function Go2HealthHint({ token }: { token: string }) {
  const [sample, setSample] = useState<{ health: HealthSummary; receivedAt: number } | null>(null);
  const [clock, setClock] = useState(() => Date.now());
  useEffect(() => {
    setSample(null);
    if (!token) return;
    let stopped = false; let inFlight = false; let request: AbortController | null = null;
    const refresh = async () => {
      if (stopped || inFlight) return;
      inFlight = true; request = new AbortController();
      const timer = window.setTimeout(() => request?.abort(), 4500);
      try {
        const health = await apiFetch<HealthSummary>("/api/go2/health", token, { signal: request.signal });
        if (!stopped) { const now = Date.now(); setSample({ health, receivedAt: now }); setClock(now); }
      } catch { if (!stopped) setSample(null); }
      finally { window.clearTimeout(timer); inFlight = false; }
    };
    void refresh();
    const poll = window.setInterval(() => void refresh(), 5000);
    const aging = window.setInterval(() => setClock(Date.now()), 1000);
    return () => { stopped = true; request?.abort(); window.clearInterval(poll); window.clearInterval(aging); };
  }, [token]);
  const health = sample?.health;
  const elapsed = sample ? Math.max(0, (clock - sample.receivedAt) / 1000) : Infinity;
  const fresh = !!health?.connected && typeof health.age_s === "number" && Number.isFinite(health.age_s)
    && health.age_s >= 0 && health.age_s + elapsed <= 10 && typeof health.sampled_at === "number" && Number.isFinite(health.sampled_at);
  const faults = fresh ? health?.faults ?? [] : [];
  const temperature = (value: number | null | undefined) => typeof value === "number" && Number.isFinite(value) ? `${value.toFixed(0)}°C` : "--";
  const leftHip = health?.rear_left_hip_temperature_c;
  const faultLabel = faults.map((fault) => `${fault.index === 9 || fault.joint === "RL_0" ? "左后髋" : fault.joint} ${fault.code_hex}`).join(" · ");
  return <div className={classNames("go2-health-hint", !fresh && "is-offline", faults.length > 0 && "has-fault")} role="status">
    <div className="go2-health-line"><strong>{fresh ? faults.length ? "关节故障" : "健康状态在线" : "健康状态离线"}</strong>
      {fresh ? <><span>左后髋 {temperature(leftHip)}</span><span>最高 {temperature(health?.max_temperature_c)}</span>
        <span>电量 {typeof health?.soc === "number" && Number.isFinite(health.soc) ? `${health.soc.toFixed(0)}%` : "--"}</span>
        <span>机身高度 {typeof health?.body_height_m === "number" && Number.isFinite(health.body_height_m) ? `${health.body_height_m.toFixed(2)} m` : "--"}</span></> : <span>未收到新鲜数据</span>}</div>
    <p>{faults.length ? `${faultLabel} · ` : ""}{fresh ? health?.reason || "动作保持暂停" : "动作保持暂停；连接恢复后才显示当前健康。"}</p>
  </div>;
}

export function useLocalPersonSpeech(status: LocalDiscoveryStatus | null, suspended: boolean, token: string) {
  const [enabled, setEnabled] = useState(false);
  const [supported, setSupported] = useState(false);
  const [error, setError] = useState("");
  const manager = useRef<LocalPersonSpeech | null>(null);
  const latest = useRef(status); latest.current = status;
  const cancel = () => { if (typeof window !== "undefined" && "speechSynthesis" in window) window.speechSynthesis.cancel(); };
  if (!manager.current) manager.current = new LocalPersonSpeech(cancel);
  useEffect(() => {
    setSupported("speechSynthesis" in window && typeof window.SpeechSynthesisUtterance === "function");
    setEnabled(false); setError(""); manager.current?.enable(false, latest.current, Date.now() / 1000);
    return () => manager.current?.dispose();
  }, [token]);
  useEffect(() => {
    if (!status) { manager.current?.quiet(null, Date.now() / 1000); return; }
    if (suspended) { manager.current?.quiet(status, Date.now() / 1000); return; }
    const text = manager.current?.next(status, Date.now() / 1000);
    if (!text || !enabled || !supported) return;
    try {
      const utterance = new SpeechSynthesisUtterance(text); utterance.lang = "zh-CN";
      utterance.onerror = (event) => { if (event.error !== "canceled" && event.error !== "interrupted") setError("本机播报未完成，请确认浏览器声音权限。"); };
      setError(""); window.speechSynthesis.speak(utterance);
    } catch { setError("当前浏览器无法播报，请查看发现记录。"); }
  }, [status, suspended, enabled, supported]);
  function toggle(value: boolean) {
    if (!supported) return;
    manager.current?.enable(value, status, Date.now() / 1000); setEnabled(value); setError("");
    if (value) {
      try { window.speechSynthesis.resume(); } catch { /* Some browsers have no paused queue. */ }
    }
  }
  return { enabled, supported, error, toggle, clear: () => manager.current?.quiet(latest.current, Date.now() / 1000) };
}
