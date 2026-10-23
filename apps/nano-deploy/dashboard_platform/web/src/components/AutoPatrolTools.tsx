import { useEffect, useRef, useState } from "react";
import { API_BASE, apiFetch, classNames } from "../lib";

type SavedMap = { id: string; name: string; width?: number; height?: number; available?: boolean; map_sha256?: string };
type Point = { name: string; x: number; y: number; yaw: number };
type GeneratedRoute = { id: string; name: string; frame_id: "map"; points: Point[];
  generation?: { map_id: string; map_sha256?: string; live_localization: false; width?: number; height?: number;
    point_pixels?: { name: string; x: number; y: number }[];
    preview?: { width: number; height: number; points?: { name: string; x_px: number; y_px: number }[] } } };
type PointLabel = string | { name?: string };
type Report = { mode: "planning" | "simulation"; ok: boolean; error?: string | null; route_id?: string;
  ordered_route_validated?: boolean; segments?: { point?: PointLabel; name?: string; ok?: boolean; success?: boolean; error?: unknown }[];
  steps?: { point?: PointLabel; state: string }[] };
type Execution = { state: string; active: boolean; simulation: true; physical_execution: false; motion_enabled: false;
  route_id?: string; index?: number; total?: number; point?: PointLabel; reason?: string | null; error?: string | null;
  steps?: { point?: PointLabel; state: string }[] };
type Scenario = "not_found" | "found" | "navigation_failed";
const button = "rounded-xl border border-base bg-card px-3 py-2 text-xs font-medium text-secondary transition hover:bg-panel disabled:cursor-not-allowed disabled:opacity-45";
const input = "min-w-0 rounded-lg border border-base bg-panel px-3 py-2 text-xs text-primary";
const executionLabels: Record<string, string> = { idle: "待演练", preparing: "准备模拟", navigating: "模拟前往", settling: "模拟到达", observing: "模拟观察", arrived: "模拟到达", not_found_at_point: "本点未出现模拟候选", cancelling: "正在取消演练", found: "模拟出现候选", not_found: "演练结束，未出现候选", failed: "演练未完成", cancelled: "演练已取消", interrupted: "演练已中断", cancel_failed: "取消未确认，演练已中断" };
function pointName(point: PointLabel | undefined, index = 0) { return typeof point === "string" ? point : point?.name || `观察点 ${index + 1}`; }
function readableError(error: unknown) {
  const text = String(error).replace(/^Error:\s*/, "");
  try { const data = JSON.parse(text) as { detail?: unknown }; if (typeof data.detail === "string" && /[\u3400-\u9fff]/.test(data.detail)) return data.detail.slice(0, 180); }
  catch { if (/[\u3400-\u9fff]/.test(text)) return text.slice(0, 180); }
  return /abort|timeout/i.test(text) ? "请求超时，请查看当前演练状态。" : "地图准备服务暂时不可用。";
}
export function PatrolMapPreview({ token, map, route }: { token: string; map: SavedMap; route: GeneratedRoute | null }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let stopped = false; let current = ""; const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 10000);
    setUrl(""); setError("");
    void fetch(`${API_BASE}/api/go2/person/patrol/maps/${encodeURIComponent(map.id)}/preview.png`,
      { headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal: controller.signal })
      .then(async (response) => { if (!response.ok) throw new Error("无法读取地图预览"); return response.blob(); })
      .then((blob) => { if (stopped) return; current = URL.createObjectURL(blob); setUrl(current); })
      .catch(() => { if (!stopped) setError("地图预览暂不可读，请刷新地图。"); })
      .finally(() => window.clearTimeout(timer));
    return () => { stopped = true; controller.abort(); window.clearTimeout(timer); if (current) URL.revokeObjectURL(current); };
  }, [token, map.id]);
  const generation = route?.generation?.map_id === map.id && (!map.map_sha256 || !route.generation.map_sha256 || map.map_sha256 === route.generation.map_sha256) ? route.generation : null;
  const width = generation?.width ?? generation?.preview?.width;
  const height = generation?.height ?? generation?.preview?.height;
  const pixels = generation?.point_pixels ?? generation?.preview?.points?.map((point) => ({ name: point.name, x: point.x_px, y: point.y_px })) ?? [];
  const valid = typeof width === "number" && width > 0 && Number.isFinite(width) && typeof height === "number" && height > 0 && Number.isFinite(height);
  const displayWidth = width ?? map.width; const displayHeight = height ?? map.height;
  const maxWidth = typeof displayWidth === "number" && displayWidth > 0 && typeof displayHeight === "number" && displayHeight > 0 ? displayWidth / displayHeight * 320 : undefined;
  return <div className="relative mx-auto overflow-hidden rounded-xl border border-base bg-panel" style={maxWidth ? { maxWidth } : undefined}>
    {url ? <><img src={url} alt={`${map.name}已保存地图`} className="block w-full" style={valid ? { aspectRatio: `${width}/${height}` } : undefined} />
      {valid ? pixels.filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y) && point.x >= 0 && point.y >= 0 && point.x < width && point.y < height).map((point, index) => <span key={point.name} aria-label={point.name} title={point.name}
        className="absolute flex h-6 w-6 items-center justify-center rounded-full border-2 border-white bg-accent-600 text-[11px] font-semibold text-white shadow-sm"
        style={{ left: `${point.x / width * 100}%`, top: `${point.y / height * 100}%`, transform: "translate(-50%, -50%)" }}>{index + 1}</span>) : null}</> : <p className="px-4 py-8 text-center text-xs text-muted">{error || "载入地图…"}</p>}
  </div>;
}

export function AutoPatrolTools({ token }: { token: string }) {
  const [maps, setMaps] = useState<SavedMap[]>([]);
  const [mapId, setMapId] = useState("");
  const [route, setRoute] = useState<GeneratedRoute | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [execution, setExecution] = useState<Execution | null>(null);
  const [scenario, setScenario] = useState<Scenario>("not_found");
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const session = useRef(0); const version = useRef(0); const busy = useRef("");
  const controllers = useRef<AbortController[]>([]);
  const selection = useRef(""); const executionRef = useRef(execution); executionRef.current = execution;
  async function loadMaps() {
    if (busy.current) return;
    const epoch = session.current; busy.current = "maps"; setPending("maps"); setError("");
    const controller = new AbortController(); controllers.current.push(controller);
    const timer = window.setTimeout(() => controller.abort(), 10000);
    try {
      const [list, saved] = await Promise.all([
        apiFetch<{ items: SavedMap[]; default_id?: string | null }>("/api/go2/person/patrol/maps", token, { signal: controller.signal }),
        apiFetch<{ routes: GeneratedRoute[] }>("/api/go2/person/patrol/status", token, { signal: controller.signal }),
      ]);
      if (epoch !== session.current) return;
      const chosen = list.items.some((map) => map.id === selection.current && map.available !== false) ? selection.current
        : list.items.find((map) => map.id === list.default_id && map.available !== false)?.id ?? list.items.find((map) => map.available !== false)?.id ?? "";
      setMaps(list.items); setMapId(chosen); selection.current = chosen;
      setRoute([...saved.routes].reverse().find((item) => item.generation?.map_id === chosen && (!item.generation.map_sha256 || !list.items.find((map) => map.id === chosen)?.map_sha256 || item.generation.map_sha256 === list.items.find((map) => map.id === chosen)?.map_sha256)) ?? null); setReport(null);
    } catch (failure) { if (epoch === session.current) { setMaps([]); setMapId(""); selection.current = ""; setRoute(null); setReport(null); setError(readableError(failure)); } }
    finally { window.clearTimeout(timer); controllers.current = controllers.current.filter((item) => item !== controller); if (epoch === session.current) { busy.current = ""; setPending(""); } }
  }
  useEffect(() => {
    session.current += 1; version.current += 1; busy.current = ""; selection.current = "";
    setMaps([]); setMapId(""); setRoute(null); setReport(null); setExecution(null); setPending(""); setError(""); setPollError(""); setScenario("not_found");
    if (token) void loadMaps();
    return () => { session.current += 1; version.current += 1; controllers.current.forEach((controller) => controller.abort()); controllers.current = []; busy.current = ""; };
  }, [token]);
  useEffect(() => {
    if (!token) return;
    const epoch = session.current; let stopped = false; let inFlight = false; let current: AbortController | null = null;
    const refresh = async () => {
      if (stopped || inFlight || busy.current) return;
      inFlight = true; const revision = version.current; const controller = new AbortController(); current = controller;
      const timer = window.setTimeout(() => controller.abort(), 10000);
      try {
        const next = await apiFetch<Execution>("/api/go2/person/patrol/execution/status", token, { signal: controller.signal });
        if (!stopped && epoch === session.current && revision === version.current) {
          if (next.simulation !== true || next.physical_execution !== false || next.motion_enabled !== false) throw new Error("仅支持软件演练状态。");
          setExecution(next); setPollError("");
        }
      } catch (failure) { if (!stopped && epoch === session.current && revision === version.current) setPollError(readableError(failure)); }
      finally { window.clearTimeout(timer); inFlight = false; }
    };
    void refresh(); const timer = window.setInterval(() => void refresh(), 2000);
    return () => { stopped = true; current?.abort(); window.clearInterval(timer); };
  }, [token]);
  async function run(action: "suggest" | "preview" | "rehearse" | "cancel") {
    if (action === "cancel" ? busy.current === "cancel" : !!busy.current || executionRef.current?.active) return;
    if (action === "suggest" && !mapId) return;
    if (["preview", "rehearse"].includes(action) && !route) return;
    const epoch = session.current; const revision = ++version.current;
    busy.current = action; setPending(action); setError("");
    const controller = new AbortController(); controllers.current.push(controller);
    const timeout = window.setTimeout(() => controller.abort(), 45000);
    const path = action === "rehearse" ? "execution/rehearse" : action === "cancel" ? "execution/cancel" : action;
    const body = action === "suggest" ? { map_id: mapId, count: 3, radius_m: 2 }
      : action === "rehearse" ? { route_id: route!.id, scenario } : action === "preview" ? { route_id: route!.id } : {};
    try {
      const next = await apiFetch<GeneratedRoute | Report | Execution>(`/api/go2/person/patrol/${path}`, token, { method: "POST", body: JSON.stringify(body), signal: controller.signal });
      if (epoch !== session.current || revision !== version.current) return;
      if (action === "suggest") { setRoute(next as GeneratedRoute); setReport(null); }
      else if (action === "preview") setReport(next as Report);
      else {
        const value = next as Execution;
        if (value.simulation !== true || value.physical_execution !== false || value.motion_enabled !== false) throw new Error("服务未确认软件演练，请查看状态。");
        setExecution(value); setPollError("");
      }
    } catch (failure) { if (epoch === session.current && revision === version.current) setError(readableError(failure)); }
    finally { window.clearTimeout(timeout); controllers.current = controllers.current.filter((item) => item !== controller); if (epoch === session.current && revision === version.current) { busy.current = ""; setPending(""); } }
  }
  const map = maps.find((item) => item.id === mapId);
  const active = execution?.active === true || pending === "rehearse" || pending === "cancel";
  const validExecution = execution?.simulation === true && execution.physical_execution === false && execution.motion_enabled === false;
  return <section className="rounded-xl border border-base bg-card p-4">
    <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-semibold text-primary">地图自动选点</h3><span className="rounded-full bg-panel px-2 py-1 text-[11px] text-muted">只做准备与演练</span></div>
    <p className="mt-2 text-xs leading-relaxed text-muted">无需填写坐标。从已保存地图选取 3 个观察点；尚未确认机器狗当前所在位置。</p>
    <div className="mt-3 flex gap-2"><select aria-label="选择已保存地图" value={mapId} disabled={!!pending || active} onChange={(event) => { selection.current = event.target.value; setMapId(event.target.value); setRoute(null); setReport(null); }} className={classNames(input, "flex-1")}>
      <option value="">{maps.length ? "选择地图" : "暂无已保存地图"}</option>{maps.map((item) => <option key={item.id} value={item.id} disabled={item.available === false}>{item.name}</option>)}</select>
      <button className={button} disabled={!!pending || active} onClick={() => void loadMaps()}>刷新地图</button></div>
    {map ? <div className="mt-3"><PatrolMapPreview token={token} map={map} route={route} /></div> : null}
    {route ? <p className="mt-2 text-xs text-secondary">{route.name} · {route.points.map((point, index) => `${index + 1}. ${point.name}`).join(" · ")}</p> : null}
    <div className="mt-3 flex flex-wrap gap-2">
      <button className={classNames(button, "border-accent-600 bg-accent-600 text-page hover:bg-accent-600")} disabled={!map || !!pending || active} onClick={() => void run("suggest")}>{pending === "suggest" ? "生成中…" : route ? "重新自动选点" : "一键生成观察点"}</button>
      <button className={button} disabled={!route || !!pending || active} onClick={() => void run("preview")}>{pending === "preview" ? "检查中…" : "检查观察点"}</button>
      <button className={button} disabled>实机巡航暂停</button>
    </div>
    {report ? <div className="mt-3 rounded-lg bg-panel p-3 text-xs"><p className={report.ok ? "text-accent-600" : "text-danger"}>{report.ok ? "观察点检查完成" : "观察点检查未通过"}</p><p className="mt-1 text-muted">检查各点可达性，尚未验证整段巡航。</p>
      {report.error ? <p className="mt-1 text-danger">{readableError(report.error)}</p> : null}
      {report.segments?.map((segment, index) => <p key={index} className="mt-1 flex justify-between text-secondary"><span>{segment.name || pointName(segment.point, index)}</span><span>{segment.error || (segment.ok ?? segment.success) === false ? "未通过" : (segment.ok ?? segment.success) === true ? "可达" : "已检查"}</span></p>)}</div> : null}
    <div className="mt-4 border-t border-base pt-3"><div className="flex flex-wrap items-center gap-2"><select aria-label="选择软件演练情景" value={scenario} disabled={!!pending || active} onChange={(event) => setScenario(event.target.value as Scenario)} className={classNames(input, "flex-1")}>
      <option value="not_found">演练：未找到候选</option><option value="found">演练：出现模拟候选</option><option value="navigation_failed">演练：无法到达</option></select>
      <button className={button} disabled={!route || !!pending || active} onClick={() => void run("rehearse")}>{pending === "rehearse" ? "启动演练…" : "流程演练"}</button>
      {active ? <button className={classNames(button, "text-danger")} disabled={pending === "cancel" || execution?.state === "cancelling"} onClick={() => void run("cancel")}>{pending === "cancel" || execution?.state === "cancelling" ? "正在取消…" : "取消演练"}</button> : null}</div>
      <p className="mt-2 text-[11px] leading-relaxed text-muted">纯软件模拟，不驱动机器狗，不生成真实发现记录。取消只停止演练任务。</p>
      {validExecution && execution && execution.state !== "idle" ? <div className="mt-3 rounded-lg bg-panel p-3 text-xs" role="status"><strong className="font-medium text-secondary">{executionLabels[execution.state] || "软件演练"}</strong>
        {execution.active && execution.total ? <p className="mt-1 text-muted">观察点 {Math.min(execution.total, Math.max(1, execution.index ?? 0))} / {execution.total} · {pointName(execution.point)}</p> : null}
        {execution.error ? <p className="mt-1 text-danger">{readableError(execution.error)}</p> : execution.reason && /[\u3400-\u9fff]/.test(execution.reason) ? <p className="mt-1 text-muted">{execution.reason}</p> : null}
        {execution.steps?.length ? <ol className="mt-2 space-y-1 text-secondary">{execution.steps.map((step, index) => <li key={index}>{pointName(step.point, index)} · {step.state === "not_found" ? "本点未出现模拟候选" : executionLabels[step.state] || "模拟步骤完成"}</li>)}</ol> : null}
      </div> : null}
    </div>
    {error || pollError ? <p className="mt-3 rounded-lg bg-danger/5 px-3 py-2 text-xs text-danger" role="alert">{error || pollError}</p> : null}
  </section>;
}
