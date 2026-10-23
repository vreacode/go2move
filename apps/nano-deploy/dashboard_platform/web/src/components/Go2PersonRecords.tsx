import { useEffect, useState } from "react";
import { apiFetch, classNames } from "../lib";

export type SearchTask = {
  id: string; state?: string; status?: string; started_at: number; deadline_at: number;
  ended_at?: number | null; finished_at?: number | null; reason?: string | null;
  target_id: string; target_name?: string; target_kind?: "photo" | "description"; found_event_id?: string | null;
};
export function taskLabel(task: SearchTask) {
  const state = task.state ?? task.status;
  return ({ running: "正在查找", found_pending_confirmation: "候选待确认", stopped: "已停止", timed_out: "超时未找到", interrupted: "查找已中断", completed: "人工确认完成" } as Record<string, string>)[state ?? ""] ?? "任务已结束";
}
export function Go2SearchTaskStatus({ task }: { task: SearchTask | null | undefined }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  const running = (task?.state ?? task?.status) === "running";
  useEffect(() => {
    setNow(Date.now() / 1000);
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => window.clearInterval(timer);
  }, [task?.id, running]);
  if (!task) return null;
  const duration = Math.max(1, task.deadline_at - task.started_at);
  const remaining = Math.max(0, Math.ceil(task.deadline_at - now));
  const progress = Math.max(0, Math.min(100, (now - task.started_at) / duration * 100));
  const candidate = (task.state ?? task.status) === "found_pending_confirmation";
  return <div className={classNames("go2-search-task", candidate && "has-candidate")} role="status">
    <div className="flex items-center justify-between gap-2"><strong>{taskLabel(task)}</strong>{running ? <span>{remaining > 0 ? `剩余 ${remaining} 秒` : "正在结束任务…"}</span> : null}</div>
    {running ? <div className="go2-task-progress" role="progressbar" aria-label="查找任务进度" aria-valuenow={Math.round(progress)} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${progress}%` }} /></div> : null}
    <p>{candidate ? "发现的是疑似候选，请在记录中确认。" : task.reason || (running ? `最多观察 ${Math.round(duration)} 秒，不驱动机器狗。` : "可重新设置目标后开始查找。")}</p>
  </div>;
}

export function Go2RecentTasks({ token, refreshKey }: { token: string; refreshKey: string }) {
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<SearchTask[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!open || !token) return;
    let stopped = false; const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 10000);
    setLoading(true); setError("");
    void apiFetch<{ items: SearchTask[]; total: number; current?: SearchTask | null }>("/api/go2/person/tasks", token, { signal: controller.signal })
      .then((result) => { if (!stopped) setItems(result.items); })
      .catch(() => { if (!stopped) setError("暂时无法读取任务记录。"); })
      .finally(() => { if (!stopped) setLoading(false); window.clearTimeout(timeout); });
    return () => { stopped = true; controller.abort(); window.clearTimeout(timeout); };
  }, [open, token, refreshKey]);
  return <details className="mt-4 border-t border-base pt-3" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary className="cursor-pointer text-xs text-muted">查找任务记录</summary>
    <div className="mt-3 space-y-2 text-xs">{error ? <p className="text-danger">{error}</p> : loading && !items.length ? <p className="text-muted">正在读取…</p> : !items.length ? <p className="text-muted">还没有任务记录。</p> : items.map((task) => <div key={task.id} className="flex flex-wrap justify-between gap-1 rounded-lg bg-panel p-2">
      <span className="text-secondary">{task.target_name || "查找任务"} · {taskLabel(task)}</span><time className="text-muted">{new Date(task.started_at * 1000).toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })}</time>
    </div>)}</div>
  </details>;
}
