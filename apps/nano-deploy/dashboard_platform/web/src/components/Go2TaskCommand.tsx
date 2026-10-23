import { useEffect, useRef, useState } from "react";
import { MicrophoneIcon, PaperAirplaneIcon } from "@heroicons/react/24/outline";
import { apiFetch } from "../lib";

type CommandReply = { intent: string; executed: boolean; reply: string };

export function encodeMonoWav(samples: Float32Array): string {
  const bytes = new Uint8Array(44 + samples.length * 2);
  const view = new DataView(bytes.buffer);
  const text = (offset: number, value: string) => [...value].forEach((letter, i) => view.setUint8(offset + i, letter.charCodeAt(0)));
  text(0, "RIFF"); view.setUint32(4, 36 + samples.length * 2, true); text(8, "WAVE");
  text(12, "fmt "); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, 16000, true); view.setUint32(28, 32000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
  text(36, "data"); view.setUint32(40, samples.length * 2, true);
  samples.forEach((sample, i) => { const bounded = Math.max(-1, Math.min(1, sample)); view.setInt16(44 + i * 2, bounded < 0 ? bounded * 32768 : bounded * 32767, true); });
  let binary = "";
  for (let offset = 0; offset < bytes.length; offset += 4096) binary += String.fromCharCode(...bytes.subarray(offset, offset + 4096));
  return btoa(binary);
}

function readableError(error: unknown) {
  const text = String(error).replace(/^Error:\s*/, "");
  try { const data = JSON.parse(text); return typeof data.detail === "string" ? data.detail : "请求未完成，请重试。"; }
  catch { return /permission|notallowed/i.test(text) ? "麦克风权限未开启，可以使用输入法语音。" : /abort/i.test(text) ? "请求已取消或超时。" : text; }
}

export function Go2TaskCommand({ token, onChanged, onCapture }: { token: string; onChanged: () => void; onCapture?: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState("");
  const [reply, setReply] = useState("");
  const [error, setError] = useState("");
  const [micAvailable, setMicAvailable] = useState(false);
  const busyRef = useRef(false);
  const disposed = useRef(false);
  const epoch = useRef(0);
  const recorder = useRef<MediaRecorder | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const stopTimer = useRef<{ id: number; media: MediaStream } | null>(null);
  const request = useRef<AbortController | null>(null);
  const isCurrent = (operation: number) => !disposed.current && epoch.current === operation;
  const releaseMic = (media: MediaStream | null) => {
    media?.getTracks().forEach((track) => track.stop());
    if (stream.current === media) stream.current = null;
    if (stopTimer.current?.media === media) {
      window.clearTimeout(stopTimer.current.id); stopTimer.current = null;
    }
  };
  const closeRecorder = (active: MediaRecorder | null) => {
    if (!active) return;
    active.onstop = null; active.onerror = null; active.ondataavailable = null;
    if (active.state !== "inactive") { try { active.stop(); } catch { /* Tracks are released separately. */ } }
  };
  useEffect(() => {
    epoch.current++; disposed.current = false; busyRef.current = false;
    setBusy(""); setReply(""); setError("");
    setMicAvailable(!!window.isSecureContext && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined" && typeof OfflineAudioContext !== "undefined");
    return () => {
      disposed.current = true; epoch.current++; busyRef.current = false;
      request.current?.abort(); request.current = null;
      const active = recorder.current; recorder.current = null;
      const media = stream.current;
      closeRecorder(active); releaseMic(media);
    };
  }, [token]);

  async function send() {
    if (busyRef.current || !text.trim()) return;
    const operation = ++epoch.current;
    busyRef.current = true; setBusy("command"); setError(""); setReply("");
    const controller = new AbortController(); request.current = controller;
    const timer = window.setTimeout(() => controller.abort(), 45000);
    try {
      const result = await apiFetch<CommandReply>("/api/go2/task/command", token, { method: "POST", body: JSON.stringify({ text: text.trim() }), signal: controller.signal });
      if (!isCurrent(operation)) return;
      setReply(result.reply);
      if (result.intent === "capture") onCapture?.(); else if (result.executed) onChanged();
    } catch (failure) { if (isCurrent(operation)) setError(readableError(failure)); }
    finally {
      window.clearTimeout(timer);
      if (isCurrent(operation)) { if (request.current === controller) request.current = null; busyRef.current = false; setBusy(""); }
    }
  }

  async function transcribe(blob: Blob, operation: number) {
    if (!isCurrent(operation)) return;
    setBusy("recognizing");
    const controller = new AbortController(); request.current = controller;
    const timer = window.setTimeout(() => controller.abort(), 45000);
    try {
      // Decode and resample entirely in memory. No audio is connected to speakers.
      const raw = await blob.arrayBuffer();
      if (!isCurrent(operation)) return;
      const decoder = new OfflineAudioContext(1, 1, 16000);
      const decoded = await decoder.decodeAudioData(raw);
      if (!isCurrent(operation)) return;
      if (decoded.duration < 0.1 || decoded.duration > 12) throw new Error("请录制0.1至12秒的简短口令。");
      const context = new OfflineAudioContext(1, Math.ceil(decoded.duration * 16000), 16000);
      const source = context.createBufferSource(); source.buffer = decoded; source.connect(context.destination); source.start();
      const rendered = await context.startRendering();
      if (!isCurrent(operation)) return;
      const result = await apiFetch<{ text: string; reply: string }>("/api/go2/task/transcribe", token, {
        method: "POST", body: JSON.stringify({ wav_base64: encodeMonoWav(rendered.getChannelData(0)) }), signal: controller.signal,
      });
      if (isCurrent(operation)) { setText(result.text); setReply(result.reply); }
    } catch (failure) { if (isCurrent(operation)) setError(readableError(failure)); }
    finally {
      window.clearTimeout(timer);
      if (isCurrent(operation)) { if (request.current === controller) request.current = null; busyRef.current = false; setBusy(""); }
    }
  }

  async function record() {
    if (recorder.current?.state === "recording") { recorder.current.stop(); return; }
    if (busyRef.current || !micAvailable) return;
    const operation = ++epoch.current;
    busyRef.current = true; setBusy("permission"); setError(""); setReply("");
    let media: MediaStream | null = null;
    let active: MediaRecorder | null = null;
    try {
      media = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true }, video: false });
      if (!isCurrent(operation)) { releaseMic(media); return; }
      const ownedMedia = media;
      stream.current = ownedMedia;
      const chunks: Blob[] = [];
      const ownedRecorder = new MediaRecorder(ownedMedia); active = ownedRecorder; recorder.current = ownedRecorder;
      ownedRecorder.ondataavailable = (event) => { if (isCurrent(operation) && recorder.current === ownedRecorder && event.data.size) chunks.push(event.data); };
      ownedRecorder.onerror = () => {
        const current = isCurrent(operation) && recorder.current === ownedRecorder;
        if (recorder.current === ownedRecorder) recorder.current = null;
        closeRecorder(ownedRecorder); releaseMic(ownedMedia);
        if (current) { busyRef.current = false; setBusy(""); setError("录音失败，请使用输入法语音。"); }
      };
      ownedRecorder.onstop = () => {
        const current = isCurrent(operation) && recorder.current === ownedRecorder;
        if (recorder.current === ownedRecorder) recorder.current = null;
        closeRecorder(ownedRecorder); releaseMic(ownedMedia);
        if (current) void transcribe(new Blob(chunks, { type: ownedRecorder.mimeType }), operation);
      };
      ownedRecorder.start(); setBusy("recording");
      stopTimer.current = { media: ownedMedia, id: window.setTimeout(() => {
        if (isCurrent(operation) && recorder.current === ownedRecorder && ownedRecorder.state === "recording") ownedRecorder.stop();
      }, 8000) };
    } catch (failure) {
      if (recorder.current === active) recorder.current = null;
      closeRecorder(active); releaseMic(media);
      if (isCurrent(operation)) { busyRef.current = false; setBusy(""); setError(readableError(failure)); }
    }
  }

  return <div className="mt-4 border-t border-base pt-3">
    <form onSubmit={(event) => { event.preventDefault(); void send(); }}>
      <label htmlFor="go2-task-command" className="text-xs font-medium text-secondary">一句话下达任务</label>
      <input id="go2-task-command" value={text} onChange={(event) => setText(event.target.value)} maxLength={160} disabled={!!busy}
        placeholder="开始找人 / 找穿红衣的人" className="mt-2 w-full rounded-lg border border-base bg-panel px-3 py-2 text-sm text-primary" />
      <div className="mt-2 flex gap-2">
        <button type="submit" disabled={!!busy || !text.trim()} className="flex flex-1 items-center justify-center gap-1 rounded-lg bg-accent-600 px-3 py-2 text-xs text-page disabled:opacity-40"><PaperAirplaneIcon className="h-4 w-4" />{busy === "command" ? "处理中…" : "发送"}</button>
        <button type="button" disabled={!micAvailable || (!!busy && busy !== "recording")} onClick={() => void record()} className="flex items-center gap-1 rounded-lg border border-base px-3 py-2 text-xs text-secondary disabled:opacity-40"><MicrophoneIcon className="h-4 w-4" />{busy === "recording" ? "结束录音" : busy === "recognizing" ? "识别中…" : "说一句"}</button>
      </div>
    </form>
    <p className="mt-2 text-[11px] leading-5 text-muted">{micAvailable ? "录音最长8秒，在NX本地识别；检查文字后再发送。" : "当前网页无法使用麦克风，可用手机输入法的语音键输入。"}</p>
    {reply && <p role="status" className="mt-2 text-xs text-secondary">{reply}</p>}
    {error && <p role="alert" className="mt-2 text-xs text-danger">{error}</p>}
  </div>;
}
