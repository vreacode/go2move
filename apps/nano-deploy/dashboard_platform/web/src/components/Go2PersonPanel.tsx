import { useEffect, useRef, useState } from "react";
import { CameraIcon, CheckCircleIcon, ClockIcon, PhotoIcon, PlayIcon, StopIcon, XMarkIcon } from "@heroicons/react/24/outline";
import { Panel } from "./Panel";
import { Go2PatrolPanel } from "./Go2PatrolPanel";
import { Go2HealthHint, useLocalPersonSpeech } from "./Go2PersonFeedback";
import { Go2SearchTaskStatus, Go2RecentTasks, type SearchTask } from "./Go2PersonRecords";
import { Go2TaskCommand } from "./Go2TaskCommand";
import { Go2FollowPanel, type FollowStatus } from "./Go2FollowPanel";
import { Go2TargetLibrary } from "./Go2TargetLibrary";
import { API_BASE, apiFetch, classNames } from "../lib";

type Target = { id: string; name: string; kind?: "photo" | "description"; description?: string; color?: string; color_label?: string };
type ReviewStatus = "confirmed" | "rejected" | "suspected";
type DepthReading = { depth_m: number; measurement: "optical_axis_depth"; frame_id: string };
type FoundEvent = { id: string; target_id: string; target_name: string; found_at: number; last_seen_at: number;
  snapshot_at?: number; similarity: number; observations: number; track_id: number; source: "live_camera"; snapshot_url: string; target_url: string;
  target_kind?: "photo" | "description"; target_description?: string; color_label?: string; score_kind?: string;
  review_status?: ReviewStatus; status?: ReviewStatus; review_note?: string; reviewed_at?: number | null;
  camera_source?: string | null; depth?: DepthReading | null };
type PersonStatus = { models_ready: boolean; target: Target | null; active: boolean; search_task?: SearchTask | null;
  follow?: FollowStatus;
  error?: string | null; recording_error?: string | null; history_count?: number; latest_event?: FoundEvent | null;
  speech?: { state: "sending" | "sent" | "failed"; delivered: boolean; error?: string | null } | null;
  camera?: { connected: boolean; width?: number; height?: number; fps?: number; source?: string; label?: string; depth_aligned?: boolean };
  last?: { captured_at: number; processed_at: number; suspected_target: boolean; candidates: { similarity: number; score_kind?: string; depth?: DepthReading | null }[] } | null };
type CaptureSelection = { selection_required: true; capture_id: string; captured_at: number; expires_in: number;
  width: number; height: number; preview_base64: string; people: { index: number; bbox: [number, number, number, number]; confidence: number }[] };
type CaptureResponse = CaptureSelection | { selection_required?: false; target: Target };

function detailText(error: unknown): string {
  const text = String(error).replace(/^Error:\s*/, "");
  try { const value = JSON.parse(text) as { detail?: unknown }; return typeof value.detail === "string" ? value.detail : JSON.stringify(value.detail ?? value); }
  catch { return /abort/i.test(text) ? "操作超时，请重试。" : text; }
}
function eventTime(value: number) {
  const date = new Date(value * 1000);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}
async function imageBlob(path: string, token: string, signal: AbortSignal) {
  const response = await fetch(`${API_BASE}${path}`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store", signal });
  if (!response.ok) throw new Error(await response.text());
  return response.blob();
}
const buttonBase = "inline-flex items-center justify-center gap-2 rounded-xl border px-4 py-2.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50";
const button = `${buttonBase} border-base bg-card text-secondary hover:bg-panel`;

function AuthImage({ path, token, alt, className }: { path: string; token: string; alt: string; className?: string }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    let stopped = false; let current = "";
    const controller = new AbortController();
    setUrl(""); setError("");
    void imageBlob(path, token, controller.signal).then((blob) => {
      if (stopped) return;
      current = URL.createObjectURL(blob); setUrl(current);
    }).catch((failure) => { if (!stopped) setError(detailText(failure)); });
    return () => { stopped = true; controller.abort(); if (current) URL.revokeObjectURL(current); };
  }, [path, token]);
  return url ? <img src={url} alt={alt} className={className} /> : <div className={classNames("flex items-center justify-center bg-card p-3 text-center text-xs text-muted", className)}>{error || "载入图片…"}</div>;
}

export function Go2PersonPanel({ token }: { token: string }) {
  const [status, setStatus] = useState<PersonStatus | null>(null);
  const [history, setHistory] = useState<FoundEvent[]>([]);
  const [historyReady, setHistoryReady] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [name, setName] = useState("目标人物");
  const [photo, setPhoto] = useState("");
  const [filename, setFilename] = useState("");
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");
  const [pollError, setPollError] = useState("");
  const [frameError, setFrameError] = useState("");
  const [preview, setPreview] = useState("");
  const [capture, setCapture] = useState<(CaptureSelection & { deadline: number }) | null>(null);
  const [expired, setExpired] = useState(false);
  const [viewer, setViewer] = useState<{ path: string; title: string } | null>(null);
  const [targetMode, setTargetMode] = useState<"photo" | "description">("photo");
  const [description, setDescription] = useState("找穿红色上衣的人");
  const [deleteEvent, setDeleteEvent] = useState("");
  const previewRef = useRef("");
  const captureRef = useRef<CaptureSelection | null>(null);
  const fileReaderRef = useRef<FileReader | null>(null);
  const refreshHistoryRef = useRef(true);
  const localSpeech = useLocalPersonSpeech(status, ["target", "description", "start", "stop", "capture", "select"].includes(pending) || !!capture, token);

  useEffect(() => {
    if (!status?.target) return;
    setTargetMode(status.target.kind === "description" ? "description" : "photo");
    if (status.target.kind === "description" && status.target.description) setDescription(status.target.description);
  }, [status?.target?.id]);

  useEffect(() => { captureRef.current = capture; setExpired(false); if (!capture) return;
    const timer = window.setTimeout(() => setExpired(true), Math.max(0, capture.deadline - Date.now()));
    return () => window.clearTimeout(timer);
  }, [capture]);

  useEffect(() => {
    if (!token) return;
    let stopped = false; let inFlight = false; let frameStamp = ""; let historyStamp = ""; let historyReadAt = 0;
    const controller = new AbortController();
    const clearPreview = () => { if (previewRef.current) URL.revokeObjectURL(previewRef.current); previewRef.current = ""; if (!stopped) setPreview(""); frameStamp = ""; };
    const refresh = async () => {
      if (stopped || inFlight) return;
      inFlight = true;
      try {
        const next = await apiFetch<PersonStatus>("/api/go2/person/status", token, { signal: controller.signal });
        if (stopped) return;
        setStatus(next); setPollError("");
        const stamp = `${next.history_count}:${next.latest_event?.id}:${next.latest_event?.last_seen_at}:${next.latest_event?.observations}`;
        if ((stamp !== historyStamp && Date.now() - historyReadAt >= 3000) || refreshHistoryRef.current) {
          try {
            const list = await apiFetch<{ items: FoundEvent[]; total: number }>("/api/go2/person/history?limit=20", token, { signal: controller.signal });
            if (stopped) return;
            setHistory(list.items); setHistoryReady(true); setHistoryError(""); historyStamp = stamp; historyReadAt = Date.now(); refreshHistoryRef.current = false;
          } catch (failure) { if (!stopped) setHistoryError(detailText(failure)); }
        }
        if (captureRef.current) return;
        const fresh = next.last && Date.now() / 1000 - next.last.captured_at < 3;
        const observing = next.active || next.follow?.active;
        const path = observing ? (fresh ? "/api/go2/person/preview.jpg" : "") : "/api/go2/person/camera.jpg";
        const frame = observing ? `search:${next.last?.processed_at}` : `camera:${Date.now()}`;
        if (!path) { clearPreview(); setFrameError(""); return; }
        if (frame === frameStamp) return;
        try {
          const blob = await imageBlob(path, token, controller.signal);
          if (stopped || captureRef.current) return;
          const url = URL.createObjectURL(blob); if (previewRef.current) URL.revokeObjectURL(previewRef.current);
          previewRef.current = url; setPreview(url); setFrameError(""); frameStamp = frame;
        } catch (failure) { if (!stopped) { clearPreview(); setFrameError(detailText(failure)); } }
      } catch (failure) { if (!stopped) { setPollError(detailText(failure)); clearPreview(); } }
      finally { inFlight = false; }
    };
    void refresh(); const timer = window.setInterval(() => { if (!document.hidden) void refresh(); }, 250);
    return () => { stopped = true; controller.abort(); window.clearInterval(timer); clearPreview(); fileReaderRef.current?.abort(); };
  }, [token]);

  useEffect(() => {
    if (!viewer) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setViewer(null); };
    window.addEventListener("keydown", close); return () => window.removeEventListener("keydown", close);
  }, [viewer]);

  function selectPhoto(file?: File) {
    fileReaderRef.current?.abort(); setPhoto(""); setFilename(""); setError("");
    if (!file) return;
    if (file.size > 6_000_000) { setError("照片不能超过 6 MB。"); return; }
    if (!file.type.startsWith("image/")) { setError("请选择人物照片。"); return; }
    const reader = new FileReader(); fileReaderRef.current = reader;
    reader.onerror = () => setError("照片读取失败，请重新选择。");
    reader.onload = () => { setPhoto(String(reader.result).split(",", 2)[1] ?? ""); setFilename(file.name); };
    reader.readAsDataURL(file);
  }

  async function run(action: "target" | "description" | "start" | "stop" | "capture" | "select", selectedIndex?: number) {
    if (pending) return;
    localSpeech.clear(); setPending(action); setError("");
    const controller = new AbortController(); const timer = window.setTimeout(() => controller.abort(), 45000);
    try {
      const targetName = name.trim() || "目标人物";
      if (action === "select" && (!capture || expired || selectedIndex === undefined)) throw new Error("照片已过期，请重新拍摄。");
      if (action === "description" && !description.trim()) throw new Error("请描述要找的人，例如：找穿红色上衣的人。");
      const body = action === "target" ? { image_base64: photo, name: targetName }
        : action === "description" ? { text: description.trim(), name: targetName }
        : action === "start" ? { mode: "stationary", similarity_threshold: .75, timeout_seconds: 120, speak: false }
        : action === "capture" ? { name: targetName }
        : action === "select" ? { capture_id: capture!.capture_id, index: selectedIndex, name: targetName } : {};
      const response = await apiFetch<CaptureResponse>(`/api/go2/person/${action === "select" ? "capture/select" : action}`, token,
        { method: "POST", body: JSON.stringify(body), signal: controller.signal });
      if (action === "capture" && response.selection_required) {
        setCapture({ ...response, deadline: Date.now() + response.expires_in * 1000 }); return;
      }
      if (["capture", "select", "target", "description"].includes(action)) setCapture(null);
      const next = await apiFetch<PersonStatus>("/api/go2/person/status", token, { signal: controller.signal });
      setStatus(next); refreshHistoryRef.current = true;
    } catch (failure) { setError(detailText(failure)); }
    finally { window.clearTimeout(timer); setPending(""); }
  }

  async function changed() {
    setCapture(null);
    refreshHistoryRef.current = true;
    try { setStatus(await apiFetch<PersonStatus>("/api/go2/person/status", token)); }
    catch (failure) { setPollError(detailText(failure)); }
  }
  async function review(eventId: string, value: ReviewStatus) {
    if (pending) return;
    setPending(`review:${eventId}`); setHistoryError("");
    const controller = new AbortController(); const timer = window.setTimeout(() => controller.abort(), 10000);
    try {
      await apiFetch(`/api/go2/person/history/${encodeURIComponent(eventId)}/review`, token,
        { method: "POST", body: JSON.stringify({ status: value }), signal: controller.signal });
      refreshHistoryRef.current = true;
      const result = await apiFetch<{ items: FoundEvent[]; total: number }>("/api/go2/person/history?limit=20", token, { signal: controller.signal });
      setHistory(result.items); setHistoryReady(true);
    } catch (failure) { setHistoryError(detailText(failure)); }
    finally { window.clearTimeout(timer); setPending(""); }
  }
  async function exportHistory() {
    if (pending) return;
    setPending("export"); setHistoryError("");
    const controller = new AbortController(); const timer = window.setTimeout(() => controller.abort(), 10000);
    try {
      const blob = await imageBlob("/api/go2/person/history/export.json", token, controller.signal);
      const url = URL.createObjectURL(blob); const anchor = document.createElement("a");
      anchor.href = url; anchor.download = "go2-person-records.json"; document.body.appendChild(anchor); anchor.click(); anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (failure) { setHistoryError(detailText(failure)); }
    finally { window.clearTimeout(timer); setPending(""); }
  }

  async function removeRecord(eventId: string) {
    if (pending) return;
    setPending(`delete:${eventId}`); setHistoryError("");
    try {
      await apiFetch(`/api/go2/person/history/${encodeURIComponent(eventId)}`, token, { method:"DELETE" });
      const result = await apiFetch<{items:FoundEvent[]}>("/api/go2/person/history?limit=20", token);
      setHistory(result.items); setDeleteEvent(""); await changed();
    } catch (failure) { setHistoryError(detailText(failure)); }
    finally { setPending(""); }
  }

  const found = status?.active && status.last?.suspected_target && Date.now() / 1000 - status.last.captured_at >= -1 && Date.now() / 1000 - status.last.captured_at < 3;
  const best = status?.last?.candidates[0]?.similarity;
  const problem = error || pollError || status?.recording_error || status?.error;
  const descriptionTarget = status?.target?.kind === "description";
  const targetPath = status?.target && !descriptionTarget ? `/api/go2/person/target.jpg?target=${encodeURIComponent(status.target.id)}` : "";
  return <Panel title="找人工作台" subtitle="拍照或描述目标，原地观察 120 秒，发现后由你确认。" className="!p-4 sm:!p-6">
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_280px]">
      <div className="min-w-0 overflow-hidden rounded-2xl border border-base bg-card">
        <Go2HealthHint token={token} />
        <div className="flex items-center justify-between gap-2 border-b border-base px-4 py-3 text-xs">
          <span className="flex items-center gap-2 text-secondary"><span className={classNames("h-2 w-2 rounded-full", status?.camera?.connected ? "bg-accent-500" : "bg-neutral-400")} />{capture ? "选择照片中的人物" : status?.camera?.label || "机身摄像头"}</span>
          <span className={classNames("rounded-full px-2.5 py-1", found ? "bg-accent-500/15 text-accent-600" : "bg-panel text-muted")}>{found ? "疑似目标已出现" : status?.active ? "查找中" : "待开始"}</span>
        </div>
        <div className={classNames("relative flex items-center justify-center bg-black/5", !capture && "min-h-56")} style={{ aspectRatio: capture ? `${capture.width}/${capture.height}` : status?.camera?.width && status.camera.height ? `${status.camera.width}/${status.camera.height}` : "16/9" }}>
          {capture ? <><img src={`data:image/jpeg;base64,${capture.preview_base64}`} alt="拍摄的冻结画面" className="absolute inset-0 h-full w-full object-contain" />
            {capture.people.map((person) => <button key={person.index} type="button" disabled={!!pending || expired}
              onClick={() => void run("select", person.index)} aria-label={`选择人物 ${person.index + 1}`}
              className="absolute border-2 border-accent-300 bg-accent-300/10 disabled:opacity-40"
              style={{ left: `${person.bbox[0] / capture.width * 100}%`, top: `${person.bbox[1] / capture.height * 100}%`, width: `${(person.bbox[2] - person.bbox[0]) / capture.width * 100}%`, height: `${(person.bbox[3] - person.bbox[1]) / capture.height * 100}%` }}>
              <span className="absolute left-0 top-0 rounded-br-lg bg-card px-2 py-1 text-xs text-primary">{person.index + 1}</span>
            </button>)}</> : preview ? <img src={preview} alt="当前摄像头画面" className="absolute inset-0 h-full w-full object-contain" />
            : <div className="px-5 text-center text-sm text-muted"><CameraIcon className="mx-auto mb-3 h-9 w-9 opacity-40" />{frameError || (status?.active ? "正在准备查找画面…" : "等待摄像头画面…")}</div>}
        </div>
        {capture ? <div className="border-t border-base p-3"><p className="mb-2 text-xs text-muted">{expired ? "照片已过期，请重新拍摄。" : "点击人物框或编号选择；120 秒内有效。"}</p>
          <div className="flex flex-wrap gap-2">{capture.people.map((person) => <button key={person.index} disabled={!!pending || expired} onClick={() => void run("select", person.index)} className={classNames(button, "!px-3 !py-1.5")}>人物 {person.index + 1}</button>)}</div></div> : null}
      </div>
      <aside className="flex flex-col rounded-2xl border border-base bg-card p-4">
        <div className="mb-4 flex items-center gap-3">
          <div className="h-20 w-16 shrink-0 overflow-hidden rounded-xl bg-panel">{targetPath ? <AuthImage path={targetPath} token={token} alt="目标参考照片" className="h-full w-full object-contain" /> : descriptionTarget ? <div className="flex h-full items-center justify-center p-2 text-center text-xs text-secondary">{status?.target?.color_label || "文字"}<br />上衣</div> : <PhotoIcon className="mx-auto mt-6 h-7 w-7 text-muted/50" />}</div>
          <div className="min-w-0"><p className="text-xs text-muted">目标人物</p><p className="mt-1 truncate font-semibold text-primary">{status?.target?.name || "还未设置"}</p><p className="mt-1 text-xs text-muted">{descriptionTarget ? status?.target?.description || "按上衣颜色筛选候选" : status?.target ? "参考照片已保存" : "拍照或描述一个目标"}</p></div>
        </div>
        <div className="go2-target-tabs" role="tablist" aria-label="选择目标方式">
          <button role="tab" aria-selected={targetMode === "photo"} disabled={!!pending} onClick={() => setTargetMode("photo")}>拍照找人</button>
          <button role="tab" aria-selected={targetMode === "description"} disabled={!!pending} onClick={() => { setCapture(null); setTargetMode("description"); }}>描述找人</button>
        </div>
        {targetMode === "photo" ? <button disabled={!!pending} onClick={() => void run("capture")} className={button}><CameraIcon className="h-4 w-4" />{pending === "capture" ? "拍摄中…" : status?.target ? "重新拍照 / 更换" : "拍当前画面作为目标"}</button>
          : <div className="space-y-2"><label className="block text-xs text-muted">描述上衣颜色<textarea value={description} maxLength={160} rows={2} onChange={(event) => setDescription(event.target.value)} placeholder="例如：找穿红色上衣的人" className="mt-1 w-full resize-none rounded-xl border border-base bg-panel px-3 py-2 text-sm text-primary" /></label>
            <button disabled={!!pending || !description.trim()} onClick={() => void run("description")} className={classNames(button, "w-full")}>{pending === "description" ? "设置中…" : "使用这个描述"}</button>
            <p className="text-xs leading-relaxed text-muted">支持红、橙、黄、绿、蓝、紫、黑、白、灰上衣；颜色只筛选候选，受光照与遮挡影响。</p></div>}
        <button disabled={!!pending || (!status?.active && (!status?.target || !!capture))} onClick={() => void run(status?.active ? "stop" : "start")}
          className={classNames(buttonBase, "mt-2", status?.active ? "border-danger/30 bg-danger/5 text-danger hover:bg-danger/10" : "border-accent-600 bg-accent-600 text-page hover:brightness-95")}>
          {status?.active ? <StopIcon className="h-4 w-4" /> : <PlayIcon className="h-4 w-4" />}{pending === "start" ? "正在开始…" : pending === "stop" ? "正在停止…" : status?.active ? "停止查找" : "开始 120 秒查找"}</button>
        <p className="mt-2 text-xs text-muted">机身音频暂停 · 查找不控制运动</p>
        <Go2SearchTaskStatus task={status?.search_task} />
        {status?.active ? <div className={classNames("mt-4 rounded-xl p-3 text-sm", found ? "bg-accent-500/10 text-accent-600" : "bg-panel text-muted")}>
          {found ? <p className="flex items-center gap-2 font-medium"><CheckCircleIcon className="h-5 w-5" />发现疑似目标</p> : <p>{status?.active ? "正在观察，等待目标出现" : "准备好后开始原地查找"}</p>}
          {best !== undefined && status?.active ? <p className="mt-1 text-xs">{descriptionTarget || status?.last?.candidates[0]?.score_kind === "color_fraction" ? "上衣颜色符合度" : "外观相似度"} {(best * 100).toFixed(1)}%</p> : null}
          {status?.camera?.source === "realsense_d435i" && best !== undefined ? <p className="mt-1 text-xs">{Number.isFinite(status.last?.candidates[0]?.depth?.depth_m) ? `前向距离约 ${status.last!.candidates[0].depth!.depth_m.toFixed(2)} 米` : "等待有效深度测距"} · 相对深度相机</p> : null}
          {found ? <p className="mt-1 text-xs">{status?.recording_error ? "记录保存失败，请查看下方提示" : status?.latest_event?.target_id === status?.target?.id ? "发现记录已保存，可在下方查看" : "正在保存发现记录…"}</p> : null}
        </div> : null}
        <details className="mt-4 border-t border-base pt-3"><summary className="cursor-pointer text-xs text-muted">更多选项</summary>
          <label className="mt-3 block text-xs text-muted">目标名称<input value={name} maxLength={32} onChange={(event) => setName(event.target.value)} className="mt-1 w-full rounded-lg border border-base bg-panel px-3 py-2 text-sm text-primary" /></label>
          {targetMode === "photo" ? <><label className="mt-3 block text-xs text-muted">使用已有单人照片<input type="file" accept="image/*" disabled={!!pending} onChange={(event) => selectPhoto(event.target.files?.[0])} className="mt-2 block w-full text-xs" /></label>
          {filename ? <p className="mt-2 truncate text-xs text-muted">{filename}</p> : null}
          <button disabled={!photo || !!pending} onClick={() => void run("target")} className={classNames(button, "mt-2 w-full !py-2")}>{pending === "target" ? "上传中…" : "使用这张照片"}</button></> : null}
        </details>
        <Go2TaskCommand token={token} onChanged={() => void changed()} onCapture={() => void run("capture")} />
        <p className="mt-auto pt-4 text-xs leading-relaxed text-muted">系统只提供疑似候选，身份由你现场确认。</p>
      </aside>
    </div>
    <Go2FollowPanel token={token} status={status?.follow} onChanged={changed}
      ready={!!status?.target && !descriptionTarget && !!status?.camera?.connected && !!status?.camera?.depth_aligned && !pending && !capture} />
    {problem ? <p className="mt-3 rounded-xl border border-danger/20 bg-danger/5 px-4 py-3 text-sm text-danger" role="alert">{problem}</p> : null}
    <Go2TargetLibrary token={token} currentId={status?.target?.id} onChanged={changed} />
    <div className="mt-6 border-t border-base pt-5">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-3"><h3 className="text-sm font-semibold text-primary">最近发现</h3><span className="text-xs text-muted">{status?.history_count ?? history.length} 条记录</span><button disabled={!!pending} onClick={() => void exportHistory()} className="text-xs text-muted underline underline-offset-4 disabled:opacity-50">{pending === "export" ? "导出中…" : "导出记录"}</button></div>
        <label className="go2-local-speech"><input type="checkbox" checked={localSpeech.enabled} disabled={!localSpeech.supported} onChange={(event) => localSpeech.toggle(event.target.checked)} />在当前手机/电脑播报</label></div>
      {!localSpeech.supported ? <p className="mb-3 text-xs text-muted">当前浏览器不支持本机播报。</p> : null}
      {localSpeech.error ? <p className="mb-3 text-xs text-danger" role="alert">{localSpeech.error}</p> : null}
      <p className="mb-3 text-xs text-muted">“是目标 / 不是目标”是人工标注，不代表系统自动确认身份。</p>
      {historyError ? <p className="mb-3 text-sm text-danger" role="alert">{historyError}</p> : null}
      {!history.length ? <div className="rounded-xl border border-dashed border-base px-4 py-6 text-center text-sm text-muted"><ClockIcon className="mx-auto mb-2 h-6 w-6 opacity-50" />{historyError ? "暂时无法读取发现记录，请稍后重试。" : historyReady ? "还没有发现记录。找到疑似目标后，参考照片与现场画面会保存在这里。" : "正在读取发现记录…"}</div>
        : <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">{history.map((event) => {
          const root = `/api/go2/person/history/${encodeURIComponent(event.id)}`;
          const descriptionEvent = event.target_kind === "description";
          const reviewState = event.review_status || event.status || "suspected";
          return <article key={event.id} className="rounded-xl border border-base bg-card p-3">
            <div className="mb-2 flex justify-between gap-2"><p className="truncate text-sm font-medium text-primary">{event.target_name}</p><span className={classNames("shrink-0 text-xs", reviewState === "rejected" ? "text-muted" : "text-accent-600")}>{reviewState === "confirmed" ? "人工确认是目标" : reviewState === "rejected" ? "人工排除" : "待人工确认"}</span></div>
            <div className={classNames("grid gap-2", descriptionEvent ? "grid-cols-1" : "grid-cols-[72px_minmax(0,1fr)]")}>{(descriptionEvent ? [{ kind: "snapshot", label: "现场画面" }] as const : [{ kind: "target", label: "参考照片" }, { kind: "snapshot", label: "现场画面" }] as const).map(({ kind, label }) => <button key={kind} className="relative h-24 overflow-hidden rounded-lg bg-panel text-left" onClick={() => setViewer({ path: `${root}/${kind}.jpg?v=${kind === "snapshot" ? event.snapshot_at ?? event.last_seen_at : event.target_id}`, title: `${event.target_name} · ${label}` })} aria-label={`放大${label}`}>
              <AuthImage path={`${root}/${kind}.jpg?v=${kind === "snapshot" ? event.snapshot_at ?? event.last_seen_at : event.target_id}`} token={token} alt={label} className={classNames("h-full w-full", kind === "target" ? "object-contain" : "object-cover")} /><span className="absolute bottom-0 left-0 rounded-tr bg-black/60 px-2 py-1 text-xs text-white">{label}</span>
            </button>)}</div>
            {descriptionEvent ? <p className="mt-2 text-xs text-muted">{event.target_description || `按${event.color_label || "指定"}上衣颜色查找`}</p> : null}
            <p className="mt-2 flex flex-wrap justify-between gap-1 text-xs text-muted"><span>{eventTime(event.found_at)}</span><span>{descriptionEvent || event.score_kind === "color_fraction" ? "颜色符合度" : "相似度"} {(event.similarity * 100).toFixed(1)}%</span></p>
            {Number.isFinite(event.depth?.depth_m) ? <p className="mt-1 text-xs text-muted">发现时前向距离约 {event.depth!.depth_m.toFixed(2)} 米 · 相对深度相机</p> : null}
            <div className="mt-3 flex flex-wrap gap-2 border-t border-base pt-2">
              <button disabled={!!pending || reviewState === "confirmed"} onClick={() => void review(event.id, "confirmed")} className="go2-review-button">是目标</button>
              <button disabled={!!pending || reviewState === "rejected"} onClick={() => void review(event.id, "rejected")} className="go2-review-button">不是目标</button>
              {reviewState !== "suspected" ? <button disabled={!!pending} onClick={() => void review(event.id, "suspected")} className="go2-review-button">撤回</button> : null}
              {deleteEvent === event.id ? <><button disabled={!!pending} onClick={() => void removeRecord(event.id)} className="go2-review-button text-danger">确认删除记录</button><button disabled={!!pending} onClick={() => setDeleteEvent("")} className="go2-review-button">取消</button></> : <button disabled={!!pending} onClick={() => setDeleteEvent(event.id)} className="go2-review-button">删除记录</button>}
            </div>
          </article>;
        })}</div>}
    </div>
    <Go2RecentTasks token={token} refreshKey={`${status?.search_task?.id}:${status?.search_task?.state ?? status?.search_task?.status}`} />
    <Go2PatrolPanel token={token} />
    {viewer ? <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-3 sm:p-8" onClick={() => setViewer(null)} role="dialog" aria-modal="true" aria-label={viewer.title}>
      <div className="relative max-h-full w-full max-w-5xl overflow-auto rounded-2xl bg-card p-3 sm:p-5" onClick={(event) => event.stopPropagation()}>
        <div className="mb-3 flex items-center justify-between gap-2"><p className="text-sm font-medium text-primary">{viewer.title}</p><button onClick={() => setViewer(null)} className="rounded-lg p-2 text-muted hover:bg-panel" aria-label="关闭"><XMarkIcon className="h-5 w-5" /></button></div>
        <AuthImage path={viewer.path} token={token} alt={viewer.title} className="max-h-[75vh] w-full object-contain" />
      </div>
    </div> : null}
  </Panel>;
}
