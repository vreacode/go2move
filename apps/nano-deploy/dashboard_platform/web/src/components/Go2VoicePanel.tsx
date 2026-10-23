import { useEffect, useState } from "react";
import { MicrophoneIcon, SpeakerWaveIcon } from "@heroicons/react/24/outline";
import { Panel } from "./Panel";
import { apiFetch, classNames } from "../lib";

type VoiceTtsStatus = {
  voice_id: string;
  voice_label: string;
  engine: string;
  model_available: boolean;
  voices: { id: string; label: string; available: boolean }[];
};
type VoiceStatus = {
  voice_policy: { enabled: boolean; reason: string };
  camera: { connected: boolean; width?: number; height?: number; fps?: number };
  audio: { connected: boolean; mic_frames?: number; last_error?: string };
  asr: { model_available: boolean; offline: boolean };
  tts?: VoiceTtsStatus;
  capture?: { phase: string; started_at?: number; seconds?: number };
  motion_enabled: boolean;
};
type VoiceResult = Partial<VoiceTtsStatus> & {
  delivered?: boolean;
  error?: string;
  text?: string;
  intent?: string;
  executed?: boolean;
  reply?: string;
  recognition?: { text: string; confidence: number };
  imported_count?: number;
  fallback_reason?: string;
  listen_hint?: string;
  speech?: { error?: string; ok?: boolean; success?: boolean; fallback_reason?: string };
};

function errorText(error: unknown) {
  let text = String(error).replace(/^Error:\s*/, "");
  try {
    const data = JSON.parse(text) as { detail?: unknown; error?: unknown };
    text = String(data.detail ?? data.error ?? text);
  } catch { /* The API may return a plain error string. */ }
  if (/token|credentials|401|403/i.test(text)) return "登录已过期，请重新登录。";
  if (/abort|timeout|timed out/i.test(text)) return "操作超时，请检查设备连接后重试。";
  if (/mic|audio|microphone|pcm|samples|packets|frames/i.test(text)) return "未收到机身麦克风声音，请重试收听。";
  return /[\u3400-\u9fff]/.test(text) ? text.slice(0, 180) : "操作失败，请检查设备和服务连接后重试。";
}

const buttonStyle = "rounded-lg border border-base bg-card px-4 py-2 text-sm text-secondary transition hover:bg-panel disabled:cursor-not-allowed disabled:opacity-50";
const inputStyle = "w-full rounded-lg border border-base bg-card px-3 py-2.5 text-sm text-secondary outline-none focus:ring-2 focus:ring-accent-300";

export function Go2VoicePanel({ token }: { token: string }) {
  const [status, setStatus] = useState<VoiceStatus | null>(null);
  const [statusError, setStatusError] = useState("");
  const [text, setText] = useState("报告状态");
  const [sayText, setSayText] = useState("我已准备好。");
  const [pending, setPending] = useState<"" | "command" | "listen" | "say" | "select" | "pack/import">("");
  const [error, setError] = useState("");
  const [result, setResult] = useState<VoiceResult | null>(null);
  const bodyAudioPaused = status?.voice_policy.enabled !== true;
  const pausedByPolicy = status?.voice_policy.enabled === false;

  useEffect(() => {
    setStatus(null); setStatusError("");
    if (!token) return;
    let stopped = false;
    let inFlight = false;
    let activeController: AbortController | null = null;
    const refresh = async () => {
      if (inFlight || stopped) return;
      inFlight = true;
      const controller = new AbortController(); activeController = controller;
      const timeout = window.setTimeout(() => controller.abort(), 8000);
      try {
        const next = await apiFetch<VoiceStatus>("/api/go2/voice/status", token, { signal: controller.signal });
        if (typeof next.voice_policy?.enabled !== "boolean") throw new Error("Invalid voice status");
        if (!stopped) { setStatus(next); setStatusError(""); }
      } catch {
        if (!stopped) { setStatus(null); setStatusError("语音服务暂不可用，请检查服务连接。"); }
      } finally { window.clearTimeout(timeout); activeController = null; inFlight = false; }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 10000);
    return () => { stopped = true; activeController?.abort(); window.clearInterval(timer); };
  }, [token]);

  useEffect(() => {
    if (pending !== "listen" || !token) return;
    let stopped = false, inFlight = false;
    let controller: AbortController | null = null;
    const refreshCapture = async () => {
      if (stopped || inFlight) return;
      inFlight = true;
      const active = new AbortController(); controller = active;
      const timeout = window.setTimeout(() => active.abort(), 2000);
      try {
        const next = await apiFetch<VoiceStatus>("/api/go2/voice/status", token, { signal: active.signal });
        if (!stopped && typeof next.voice_policy?.enabled === "boolean") setStatus(next);
      } catch { /* The listen request supplies its own actionable error. */ }
      finally { window.clearTimeout(timeout); controller = null; inFlight = false; }
    };
    void refreshCapture();
    const timer = window.setInterval(() => void refreshCapture(), 500);
    return () => { stopped = true; controller?.abort(); window.clearInterval(timer); };
  }, [pending, token]);

  async function run(kind: "command" | "listen" | "say" | "select" | "pack/import", commandText?: string) {
    if (bodyAudioPaused) { setError("机身音频暂停，请使用人物发现区的本机播报。"); return; }
    if (pending) return;
    const value = (commandText ?? (kind === "say" ? sayText : text)).trim();
    if (["command", "say", "select"].includes(kind) && !value) { setError("请先输入文字。"); return; }
    setPending(kind); setError(""); setResult(null);
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), kind === "pack/import" ? 150000 : 65000);
    try {
      const body = kind === "listen" ? { seconds: 5, execute: false, speak: false }
        : kind === "select" ? { voice_id: value } : kind === "pack/import" ? {}
        : kind === "say" ? { text: value } : { text: value, speak: true };
      const response = await apiFetch<VoiceResult>(`/api/go2/voice/${kind}`, token, {
        method: "POST", body: JSON.stringify(body), signal: controller.signal,
      });
      const sayFailed = kind === "say" && (response.delivered !== true || !!response.error);
      const fallbackReply = kind === "say" ? (sayFailed ? "播报失败。" : "播报音频已发送。")
        : response.executed ? "操作已执行。" : "操作未执行。";
      if (kind === "select") {
        if (!response.voice_id || !response.voices) throw new Error("音色状态未返回，请刷新后重试。");
        setStatus(current => current ? { ...current, tts: response as VoiceTtsStatus } : current);
        setResult({ ...response, reply: `已选择${response.voice_label}，下一次播报会使用这个音色。` });
      } else if (kind === "pack/import") {
        setResult({ ...response, reply: `已将 ${response.imported_count ?? 0} 条常用提示音保存到机器狗音频库。` });
      } else if (kind === "listen" && response.intent === "transcription" && response.text) {
        setText(response.text);
        setResult({ ...response, reply: "请确认指令文字，再点击执行命令。" });
      } else setResult({ ...response, reply: response.reply || fallbackReply });
      if (sayFailed) setError(`播报失败：${errorText(response.error || "未确认音频发送，请检查设备连接后重试。")}`);
      else if (response.speech?.error) setError(`播报失败：${errorText(response.speech.error)}`);
      else if (response.fallback_reason || response.speech?.fallback_reason) setError("成熟音色暂不可用，本次使用备用合成童声。");
    } catch (failure) { setError(errorText(failure)); }
    finally { window.clearTimeout(timeout); setPending(""); }
  }

  const camera = status?.camera;
  const audio = status?.audio;
  const recognition = result?.recognition;
  const listenLabel = status?.capture?.phase === "recording" ? "正在收听，请对机器狗说话…"
    : status?.capture?.phase === "recognizing" ? "收听已结束，正在识别…" : "正在准备机身麦克风…";
  const unknownStatusLabel = pausedByPolicy ? "未读取" : statusError ? "读取失败" : "读取中";
  const panelMessage = pausedByPolicy ? status.voice_policy.reason
    : status?.voice_policy.enabled ? "点击收听后，对着机器狗说一句；确认识别文字后执行并播报。"
    : statusError || "正在读取语音状态。";
  return (
    <Panel title={pausedByPolicy ? "机身音频暂停" : "机身语音助手"} subtitle={panelMessage}>
      {pausedByPolicy ? <p className="mb-4 rounded-xl border border-base bg-panel p-3 text-sm text-secondary">发现反馈可在人物发现区开启“在当前手机/电脑播报”。设备冷却后不会自动恢复机身音频。</p> : null}
      <div className="mb-4 grid gap-3 sm:grid-cols-3">
        <DeviceCard label="机身摄像头" online={camera?.connected} unknownLabel={unknownStatusLabel} detail={camera?.connected
          ? `${camera.width ?? "--"} × ${camera.height ?? "--"} · ${(camera.fps ?? 0).toFixed(1)} fps`
          : pausedByPolicy ? "请在上方实时画面查看摄像头" : statusError || "等待视频画面"} />
        <DeviceCard label="机身麦克风" online={pausedByPolicy ? undefined : audio?.connected} unknownLabel={pausedByPolicy ? "已暂停" : unknownStatusLabel} detail={pausedByPolicy
          ? "机身音频暂停" : audio?.connected ? `已接收 ${audio.mic_frames ?? 0} 帧声音` : statusError || audio?.last_error || "等待音频连接"} />
        <DeviceCard label="本地中文识别" online={status?.asr.model_available} unknownLabel={unknownStatusLabel} detail={status?.asr.model_available
          ? "离线模型就绪" : statusError || (status ? "模型暂不可用" : "尚未读取模型状态")} />
      </div>
      {statusError ? <p className="mb-3 text-sm text-danger" role="status">{statusError}</p> : null}
      {audio?.last_error ? <p className="mb-3 text-sm text-muted">{errorText(audio.last_error)}</p> : null}
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-3">
          <form onSubmit={(event) => { event.preventDefault(); void run("command"); }} className="flex gap-2">
            <label className="flex-1"><span className="sr-only">控制命令</span><input value={text} onChange={(event) => setText(event.target.value)} maxLength={120} placeholder="例如：拍照、报告状态" className={inputStyle} /></label>
            <button type="submit" disabled={bodyAudioPaused || !!pending} className={buttonStyle}>{pending === "command" ? "执行中…" : "执行命令"}</button>
          </form>
          <div className="flex flex-wrap gap-2">
            {["拍照", "报告状态", "开始录像", "停止录像"].map((command) => <button key={command} disabled={bodyAudioPaused || !!pending} className={buttonStyle} onClick={() => { setText(command); void run("command", command); }}>{command}</button>)}
          </div>
          <button disabled={bodyAudioPaused || !!pending} className={classNames(buttonStyle, "flex items-center gap-2")} onClick={() => void run("listen")}>
            <MicrophoneIcon className="h-5 w-5" />{pending === "listen" ? listenLabel : "机身麦克风收听 5 秒"}
          </button>
          <p className="text-xs text-muted">声音来自机器狗身上的麦克风。请靠近机身，看到“正在收听”后说一句简短的话。</p>
        </div>
        <div className="space-y-3">
          {status?.tts ? <div className="space-y-2">
            <label className="block text-sm text-muted">机身播报音色<select value={status.tts.voice_id}
              disabled={bodyAudioPaused || !!pending} onChange={event => void run("select", event.target.value)}
              className={classNames(inputStyle, "mt-2")}>
              {status.tts.voices.map(voice => <option key={voice.id} value={voice.id} disabled={!voice.available}>{voice.label}</option>)}
            </select></label>
            <button type="button" disabled={bodyAudioPaused || !!pending || status.tts.engine !== "kokoro"}
              onClick={() => void run("pack/import")} className={buttonStyle}>
              {pending === "pack/import" ? "正在导入提示音…" : "导入常用提示音到机身"}
            </button>
            <p className="text-xs text-muted">包含打招呼、准备就绪、没有听清和发现提示。导入会保存音频，不会立即播放。</p>
          </div> : null}
          <form onSubmit={(event) => { event.preventDefault(); void run("say"); }} className="space-y-3">
            <label className="block text-sm text-muted">让机器狗播报<input value={sayText} onChange={(event) => setSayText(event.target.value)} maxLength={160} placeholder="输入要播报的文字" className={classNames(inputStyle, "mt-2")} /></label>
            <button type="submit" disabled={bodyAudioPaused || !!pending} className={classNames(buttonStyle, "flex items-center gap-2")}><SpeakerWaveIcon className="h-5 w-5" />{pending === "say" ? "播报中…" : "机身扬声器播报"}</button>
          </form>
        </div>
      </div>
      <p className="mt-4 text-xs text-muted">{pausedByPolicy ? "机身音频保持暂停，收听和播报暂不可用。"
        : "每次收听 5 秒，识别文字确认后再执行。运动控制保持暂停。"}</p>
      {error ? <p className="mt-3 text-sm text-danger" role="alert">{error}</p> : null}
      {result ? <div className="mt-4 rounded-lg border border-base bg-card p-4 text-sm text-secondary" aria-live="polite">
        {recognition ? <p className="mb-2">听到：{recognition.text || "未识别到有效指令"} · 置信度 {(Math.max(0, Math.min(1, recognition.confidence ?? 0)) * 100).toFixed(0)}%</p> : null}
        <p>{result.reply}</p>
        {result.listen_hint ? <p className="mt-2 text-muted">{result.listen_hint}</p> : null}
      </div> : null}
    </Panel>
  );
}

function DeviceCard({ label, online, detail, unknownLabel = "读取中" }: { label: string; online?: boolean; detail: string; unknownLabel?: string }) {
  return <div className="rounded-lg border border-base bg-card p-3">
    <div className="flex items-center justify-between gap-2"><span className="text-sm text-secondary">{label}</span><span className={classNames("text-xs", online ? "text-accent-600" : "text-muted")}>{online === undefined ? unknownLabel : online ? "在线" : "离线"}</span></div>
    <p className="mt-1 text-xs text-muted">{detail}</p>
  </div>;
}
