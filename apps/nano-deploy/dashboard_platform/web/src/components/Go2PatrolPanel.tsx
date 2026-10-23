import { useEffect, useRef, useState } from "react";
import { ChevronDownIcon, MapPinIcon } from "@heroicons/react/24/outline";
import { apiFetch, classNames } from "../lib";
import { AutoPatrolTools } from "./AutoPatrolTools";

type Point = { name: string; x: number; y: number; yaw: number };
type Route = { id: string; name: string; frame_id: "map"; points: Point[] };
type ReportPoint = string | { name?: string };
type PatrolReport = { mode: "planning" | "simulation"; ok: boolean; route_id?: string; error?: string | null;
  scope?: string; ordered_route_validated?: boolean; motion_enabled?: boolean; found?: boolean;
  segments?: { point?: ReportPoint; name?: string; ok?: boolean; success?: boolean; error?: string | object | null }[];
  steps?: { point?: ReportPoint; state: string }[] };
type PatrolStatus = { routes: Route[]; motion_enabled: false; blocked_reason?: string; last_report?: PatrolReport | null };
type EditablePoint = { name: string; x: string; y: string; yaw: string };
const button = "rounded-xl border border-base bg-card px-4 py-2.5 text-sm font-medium text-secondary transition hover:bg-panel disabled:cursor-not-allowed disabled:opacity-50";
const input = "w-full rounded-lg border border-base bg-panel px-3 py-2 text-sm text-primary outline-none focus:ring-2 focus:ring-accent-300";

function friendlyError(error: unknown) {
  const text = String(error).replace(/^Error:\s*/, "");
  try {
    const value = JSON.parse(text) as { detail?: unknown };
    if (typeof value.detail === "string" && /[\u3400-\u9fff]/.test(value.detail)) return value.detail;
  } catch { if (/[\u3400-\u9fff]/.test(text)) return text; }
  return /abort|timeout/i.test(text) ? "操作超时，请稍后重试。" : "准备服务暂时不可用，请稍后重试。";
}
function rehearsalState(state: string) {
  const labels: Record<string, string> = { navigating: "模拟前往", arrived: "模拟到达", observing: "模拟观察", not_found: "结束观察", found: "模拟发现", failed: "未完成", cancelled: "已取消" };
  return labels[state] || "模拟步骤已完成";
}
function pointName(point: ReportPoint | undefined, index: number) {
  return typeof point === "string" ? point : point?.name || `观察点 ${index + 1}`;
}

function ManualPatrolTools({ token }: { token: string }) {
  const [open, setOpen] = useState(false);
  const [status, setStatus] = useState<PatrolStatus | null>(null);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [points, setPoints] = useState<EditablePoint[]>(() => [1, 2, 3].map((index) => ({ name: `观察点 ${index}`, x: "", y: "", yaw: "0" })));
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");
  const [report, setReport] = useState<PatrolReport | null>(null);
  const busy = useRef(false);

  async function refresh() {
    if (busy.current) return;
    busy.current = true; setPending("status"); setError("");
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 15000);
    try {
      const next = await apiFetch<PatrolStatus>("/api/go2/person/patrol/status", token, { signal: controller.signal });
      const chosen = next.routes.some((route) => route.id === selected) ? selected
        : next.routes.some((route) => route.id === next.last_report?.route_id) ? next.last_report!.route_id! : next.routes[0]?.id ?? "";
      setStatus(next); setSelected(chosen);
      setReport(next.last_report && (!next.last_report.route_id || next.last_report.route_id === chosen) ? next.last_report : null);
    } catch (failure) { setError(friendlyError(failure)); }
    finally { window.clearTimeout(timer); busy.current = false; setPending(""); }
  }

  function toggle() {
    setOpen((previous) => !previous);
    if (!open) void refresh();
  }

  async function run(action: "routes" | "preview" | "rehearse") {
    if (busy.current) return;
    setError("");
    let body: object;
    if (action === "routes") {
      if (!name.trim()) { setError("请填写区域名称。"); return; }
      if (points.some((point) => !point.name.trim() || [point.x, point.y, point.yaw].some((value) => !value.trim() || !Number.isFinite(Number(value))))) {
        setError("请填写三个观察点的名称、位置和朝向。"); return;
      }
      const numeric = points.map((point) => ({ name: point.name.trim(), x: Number(point.x), y: Number(point.y), yaw: Number(point.yaw) }));
      if (numeric.some((point) => Math.abs(point.x) > 1000 || Math.abs(point.y) > 1000 || Math.abs(point.yaw) > Math.PI)) {
        setError("请检查位置数值；朝向应在 -3.14 到 3.14 之间。"); return;
      }
      if (new Set(numeric.map((point) => point.name)).size !== numeric.length
        || new Set(numeric.map((point) => `${point.x}:${point.y}:${point.yaw}`)).size !== numeric.length) {
        setError("每个观察点须有不同名称，位置与朝向也不能完全重复。"); return;
      }
      body = { name: name.trim(), frame_id: "map", points: numeric };
    } else {
      if (!selected) { setError("请先保存或选择一个区域。"); return; }
      body = { route_id: selected };
    }
    busy.current = true; setPending(action);
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 45000);
    try {
      if (action === "routes") {
        const route = await apiFetch<Route>("/api/go2/person/patrol/routes", token, { method: "POST", body: JSON.stringify(body), signal: controller.signal });
        setStatus((previous) => ({ routes: [...(previous?.routes ?? []).filter((item) => item.id !== route.id), route], motion_enabled: false,
          blocked_reason: previous?.blocked_reason, last_report: null }));
        setSelected(route.id); setReport(null);
      } else {
        const next = await apiFetch<PatrolReport>(`/api/go2/person/patrol/${action}`, token, { method: "POST", body: JSON.stringify(body), signal: controller.signal });
        setReport(next);
      }
    } catch (failure) { setError(friendlyError(failure)); }
    finally { window.clearTimeout(timer); busy.current = false; setPending(""); }
  }

  const selectedRoute = status?.routes.find((route) => route.id === selected);
  return <section className="overflow-hidden rounded-xl border border-base">
    <button type="button" onClick={toggle} aria-expanded={open} aria-controls="go2-patrol-content" className="flex w-full items-center justify-between gap-3 bg-card px-4 py-4 text-left">
      <span className="flex items-center gap-2 text-sm font-semibold text-primary"><MapPinIcon className="h-4 w-4" />高级：手动巡航与演练<span className="rounded-full border border-base px-2 py-0.5 text-[10px] font-normal text-muted">准备与演练</span></span>
      <ChevronDownIcon className={classNames("h-4 w-4 shrink-0 text-muted", open && "rotate-180")} aria-hidden="true" />
    </button>
    {open ? <div id="go2-patrol-content" className="space-y-4 border-t border-base p-4">
      <p className="text-xs leading-relaxed text-muted">演练不驱动机器狗，也不产生真实发现。实机测试已暂停。</p>
      <div className="flex flex-wrap gap-2">
        <select aria-label="选择巡航区域" value={selected} disabled={!!pending} onChange={(event) => { setSelected(event.target.value); setReport(null); }} className={classNames(input, "min-w-0 flex-1 sm:min-w-52")}>
          <option value="">{status?.routes.length ? "选择已保存的区域" : "尚未保存区域"}</option>
          {(status?.routes ?? []).map((route) => <option key={route.id} value={route.id}>{route.name}</option>)}
        </select>
        <button type="button" onClick={() => void refresh()} disabled={!!pending} className={button}>{pending === "status" ? "读取中…" : "刷新"}</button>
      </div>
      {selectedRoute ? <p className="text-xs text-muted">{selectedRoute.name} · {selectedRoute.points.length} 个观察点</p> : null}
      <details className="rounded-xl border border-base bg-card p-3" open={!status?.routes.length}>
        <summary className="cursor-pointer text-sm font-medium text-secondary">新建区域与观察点</summary>
        <div className="mt-3 space-y-3">
          <label className="block text-xs text-muted">区域名称<input value={name} maxLength={32} onChange={(event) => setName(event.target.value)} disabled={!!pending} placeholder="填写现场区域名称" className={classNames(input, "mt-1")} /></label>
          <p className="text-xs text-muted">填写当前地图中实际确认的位置。位置单位为米，朝向单位为弧度；空白位置尚未验证。</p>
          {points.map((point, index) => <div key={index} className="grid grid-cols-3 gap-2 rounded-lg border border-base bg-panel p-3 sm:grid-cols-[minmax(0,1.2fr)_1fr_1fr_1fr]">
            <label className="col-span-3 text-xs text-muted sm:col-span-1">观察点名称<input value={point.name} maxLength={24} disabled={!!pending} onChange={(event) => setPoints((old) => old.map((value, item) => item === index ? { ...value, name: event.target.value } : value))} className={classNames(input, "mt-1")} /></label>
            {([{ key: "x", label: "X 位置" }, { key: "y", label: "Y 位置" }, { key: "yaw", label: "朝向" }] as const).map(({ key, label }) => <label key={key} className="min-w-0 text-xs text-muted">{label}<input type="number" inputMode="decimal" step="any" value={point[key]} disabled={!!pending} placeholder={key === "yaw" ? "0" : "待填写"} aria-label={`${point.name} ${label}`} onChange={(event) => setPoints((old) => old.map((value, item) => item === index ? { ...value, [key]: event.target.value } : value))} className={classNames(input, "mt-1 !px-2")} /></label>)}
          </div>)}
          <button type="button" disabled={!!pending} onClick={() => void run("routes")} className={button}>{pending === "routes" ? "保存中…" : "保存区域"}</button>
        </div>
      </details>
      <div className="flex flex-wrap gap-2">
        <button type="button" disabled={!!pending || !selected} onClick={() => void run("preview")} className={button}>{pending === "preview" ? "检查中…" : "检查观察点"}</button>
        <button type="button" disabled={!!pending || !selected} onClick={() => void run("rehearse")} className={button}>{pending === "rehearse" ? "演练中…" : "流程演练"}</button>
        <button type="button" disabled className={button}>实机测试暂停</button>
      </div>
      {error ? <p className="rounded-lg bg-danger/5 px-3 py-2 text-sm text-danger" role="alert">{error}</p> : null}
      {report ? <div className="rounded-xl border border-base bg-card p-3" aria-live="polite">
        <p className={classNames("text-sm font-medium", report.ok ? "text-accent-600" : "text-danger")}>{report.mode === "simulation" ? report.ok ? "流程演练完成" : "流程演练未完成" : report.ok ? "观察点检查完成" : "观察点检查未通过"}</p>
        <p className="mt-1 text-xs text-muted">{report.mode === "simulation" ? "这是流程模拟；机器狗没有移动，不计入发现记录。" : "检查各点可达性，尚未验证整段巡航。"}</p>
        {report.error ? <p className="mt-2 text-sm text-danger">{friendlyError(report.error)}</p> : null}
        {report.mode === "planning" && report.segments?.length ? <ul className="mt-3 space-y-1 text-xs text-secondary">{report.segments.map((segment, index) => <li key={index} className="flex justify-between gap-2"><span>{segment.name || pointName(segment.point, index)}</span><span>{segment.error || (segment.ok ?? segment.success) === false ? "未通过" : (segment.ok ?? segment.success) === true ? "可达" : "已检查"}</span></li>)}</ul> : null}
        {report.mode === "simulation" && report.steps?.length ? <ol className="mt-3 space-y-1 text-xs text-secondary">{report.steps.map((step, index) => <li key={index}>{index + 1}. {pointName(step.point, index)} · {rehearsalState(step.state)}</li>)}</ol> : null}
      </div> : null}
    </div> : null}
  </section>;
}


type ScanState = "idle" | "checking" | "scanning" | "observing" | "found" | "completed" | "stopped" | "blocked" | "failed";
type ScanStatus = { state?: ScanState; status?: ScanState; reason?: string | null; available: boolean;
  physical_validated?: boolean; angle_deg?: number; max_angle_deg?: number };
const scanLabels: Record<ScanState, string> = { idle: "待开始", checking: "检查现场条件", scanning: "正在慢速扫描", observing: "停下观察", found: "发现疑似目标，已停止", completed: "本次扫描完成", stopped: "扫描已停止", blocked: "扫描暂停", failed: "扫描未完成" };

function scanFailure(error: unknown) {
  const text = String(error).replace(/^Error:\s*/, "");
  try {
    const body = JSON.parse(text) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail.slice(0, 240);
  } catch { if (/[\u3400-\u9fff]/.test(text)) return text.slice(0, 240); }
  return /abort|timeout/i.test(text) ? "请求超时，请查看当前扫描状态。" : "暂时无法读取扫描服务。";
}

function Go2ScanPanel({ token }: { token: string }) {
  const [scan, setScan] = useState<ScanStatus | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [pending, setPending] = useState<"" | "start" | "stop">("");
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const actionRef = useRef<"" | "start" | "stop">("");
  const versionRef = useRef(0);
  const requestRef = useRef<AbortController[]>([]);

  useEffect(() => {
    if (!token) return;
    let stopped = false; let inFlight = false; let current: AbortController | null = null;
    const refresh = async () => {
      if (stopped || inFlight || actionRef.current) return;
      inFlight = true; const version = versionRef.current;
      const controller = new AbortController(); current = controller;
      const timer = window.setTimeout(() => controller.abort(), 10000);
      try {
        const next = await apiFetch<ScanStatus>("/api/go2/person/scan/status", token, { signal: controller.signal });
        if (!stopped && version === versionRef.current) {
          setScan(next); setPollError("");
          if (!next.available || next.physical_validated === false) setConfirmed(false);
        }
      } catch (failure) { if (!stopped && version === versionRef.current) setPollError(scanFailure(failure)); }
      finally { window.clearTimeout(timer); inFlight = false; }
    };
    void refresh(); const timer = window.setInterval(() => void refresh(), 2000);
    return () => { stopped = true; window.clearInterval(timer); current?.abort(); requestRef.current.forEach((controller) => controller.abort()); versionRef.current += 1; };
  }, [token]);

  async function runScan(action: "start" | "stop") {
    if (action === "start" && (actionRef.current || !confirmed || scan?.available !== true || scan.physical_validated === false)) return;
    if (action === "stop" && actionRef.current === "stop") return;
    const version = ++versionRef.current;
    actionRef.current = action; setPending(action); setError(""); setConfirmed(false);
    const controller = new AbortController(); requestRef.current.push(controller);
    const timer = window.setTimeout(() => controller.abort(), 45000);
    try {
      const next = await apiFetch<ScanStatus>(`/api/go2/person/scan/${action}`, token,
        { method: "POST", body: JSON.stringify(action === "start" ? { confirmed: true } : {}), signal: controller.signal });
      if (version === versionRef.current) setScan(next);
    } catch (failure) { if (version === versionRef.current) setError(scanFailure(failure)); }
    finally {
      window.clearTimeout(timer); requestRef.current = requestRef.current.filter((item) => item !== controller);
      if (version === versionRef.current) { actionRef.current = ""; setPending(""); }
    }
  }

  const state = scan?.state ?? scan?.status ?? "idle";
  const active = ["checking", "scanning", "observing"].includes(state) || pending === "start";
  const available = scan?.available === true && scan.physical_validated !== false;
  const angle = typeof scan?.angle_deg === "number" && Number.isFinite(scan.angle_deg) ? Math.abs(scan.angle_deg) : 0;
  const maxAngle = typeof scan?.max_angle_deg === "number" && Number.isFinite(scan.max_angle_deg) ? Math.min(90, Math.max(0, scan.max_angle_deg)) : 90;
  return <section className="rounded-xl border border-base bg-card p-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="text-sm font-semibold text-primary">原地扫描找人</h3>
      <span className={classNames("rounded-full px-2.5 py-1 text-xs", state === "found" ? "bg-accent-500/10 text-accent-600" : "bg-panel text-muted")}>{scan ? scanLabels[state] : "读取状态…"}</span>
    </div>
    <p className="mt-2 text-xs leading-relaxed text-muted">无需设置坐标。首版最多慢转 90°，发现疑似目标立即停下；全程静音。</p>
    {scan?.reason ? <p className={classNames("mt-3 text-sm", state === "blocked" || state === "failed" ? "text-danger" : "text-secondary")}>{scan.reason}</p> : null}
    {!available && scan ? <p className="mt-1 text-xs text-muted">需完成后续现场验证后开放，冷却不会自动解除暂停。</p> : null}
    {active || angle > 0 ? <p className="mt-2 text-xs text-muted">已扫描 {angle.toFixed(0)}° / {maxAngle.toFixed(0)}°</p> : null}
    {available && !active ? <label className="mt-4 flex items-start gap-2 text-xs leading-relaxed text-secondary">
      <input type="checkbox" checked={confirmed} disabled={!!pending} onChange={(event) => setConfirmed(event.target.checked)} className="mt-0.5 h-4 w-4 shrink-0 accent-accent-500" />
      现场已确认：周围 1 米清空、机器狗站稳、有人可随时接管。
    </label> : null}
    <div className="mt-3 flex flex-wrap gap-2">
      <button type="button" disabled={!available || !confirmed || active || !!pending} onClick={() => void runScan("start")} className={classNames(button, "border-accent-600 bg-accent-600 text-page hover:bg-accent-600 hover:brightness-95")}>{pending === "start" ? "准备扫描…" : "原地扫描找人"}</button>
      <button type="button" disabled={!active || pending === "stop"} onClick={() => void runScan("stop")} className={classNames(button, "border-danger/30 bg-danger/5 text-danger hover:bg-danger/10")}>{pending === "stop" ? "正在停止…" : "停止扫描"}</button>
    </div>
    {error || pollError ? <p className="mt-3 rounded-lg border border-danger/20 bg-danger/5 px-3 py-2 text-sm text-danger" role="alert">{error || pollError}</p> : null}
  </section>;
}

export function Go2PatrolPanel({ token }: { token: string }) {
  return <div className="mt-6 space-y-3"><Go2ScanPanel token={token} /><AutoPatrolTools token={token} /><ManualPatrolTools token={token} /></div>;
}
